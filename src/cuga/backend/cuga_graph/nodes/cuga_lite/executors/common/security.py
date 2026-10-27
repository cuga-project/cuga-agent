import ast
import re
from typing import FrozenSet, Optional, Set

from .benchmark_mode import is_relaxed_execution


class CodeSyntaxError(ValueError):
    """Raised when submitted code fails Python syntax validation."""


class _SecurityAstVisitor(ast.NodeVisitor):
    """Reject dangerous imports, calls, and attribute access via AST only."""

    def __init__(
        self,
        *,
        forbidden_modules: FrozenSet[str],
        forbidden_calls: FrozenSet[str],
        forbidden_attrs: FrozenSet[str],
        forbidden_io_attrs: FrozenSet[str] = frozenset(),
        strict: bool,
    ) -> None:
        self.forbidden_modules = forbidden_modules
        self.forbidden_calls = forbidden_calls
        self.forbidden_attrs = forbidden_attrs
        self.forbidden_io_attrs = forbidden_io_attrs
        self.strict = strict

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._reject_module(alias.name.split('.')[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._reject_module(node.module.split('.')[0])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.strict:
            name = self._call_name(node.func)
            if name in self.forbidden_calls:
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"dangerous call '{name}' in wrapped code"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.strict:
            attr = node.attr
            if attr.startswith('__') and attr.endswith('__'):
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"dunder attribute '{attr}' in wrapped code"
                )
            if attr in self.forbidden_attrs:
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"forbidden attribute '{attr}' in wrapped code"
                )
            root = self._root_name(node.value)
            if root in self.forbidden_modules:
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"forbidden module '{root}' in wrapped code"
                )
            # Reach-through: an allowlisted module (e.g. ``typing``, ``asyncio``) holds a
            # forbidden module as a plain attribute, so ``typing.sys.modules['os']`` has an
            # allowed root but reaches ``sys``. ``_root_name`` only inspects the leftmost
            # name, so check every link in the chain, not just the root.
            if attr in self.forbidden_modules:
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"forbidden module '{attr}' reached via attribute access in wrapped code"
                )
            # Allowlisted data libraries (pandas, numpy) expose file/URL/pickle I/O that
            # escapes the sandbox (read_pickle -> __reduce__ RCE, read_csv host read,
            # to_csv host write). Block those methods wherever they appear.
            if attr in self.forbidden_io_attrs:
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - "
                    f"forbidden I/O method '{attr}' in wrapped code"
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.strict and isinstance(node.ctx, ast.Load) and node.id in self.forbidden_modules:
            raise PermissionError(
                f"Security violation: Suspicious pattern detected - "
                f"forbidden module '{node.id}' in wrapped code"
            )
        self.generic_visit(node)

    def _reject_module(self, module_name: str) -> None:
        if module_name in self.forbidden_modules:
            raise PermissionError(
                f"Security violation: Suspicious pattern detected - "
                f"forbidden module '{module_name}' in wrapped code"
            )

    @staticmethod
    def _call_name(func: ast.AST) -> Optional[str]:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    @staticmethod
    def _root_name(node: ast.AST) -> Optional[str]:
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None


class SecurityValidator:
    """Handles security validation for code execution."""

    DANGEROUS_IMPORTS: Set[str] = {'os', 'sys', 'subprocess', 'pathlib', 'shutil', 'glob', 'importlib'}

    ALLOWED_IMPORTS: Set[str] = {
        'asyncio',
        'json',
        'pandas',
        'numpy',
        'pydantic',
        'statistics',
        'datetime',
        '_strptime',
        'calendar',
        'time',
        'math',
        'collections',
        'itertools',
        'functools',
        're',
        'typing',
    }

    DANGEROUS_MODULE_NAMES: Set[str] = {
        'os',
        'sys',
        'subprocess',
        'pathlib',
        'shutil',
        'glob',
        'importlib',
        '__import__',
        'eval',
        'exec',
        'compile',
    }

    # Modules blocked by AST import / name / attribute checks.
    FORBIDDEN_MODULES: FrozenSet[str] = frozenset(
        {
            'os',
            'sys',
            'subprocess',
            'pathlib',
            'shutil',
            'glob',
            'importlib',
            'requests',
            'socket',
            'urllib',
            'http',
            'ctypes',
            'pickle',
            'cPickle',
            'marshal',
            'shelve',
            'pdb',
            'builtins',
        }
    )

    # Lighter CodeAgent check: only these module imports.
    DANGEROUS_IMPORT_MODULES: FrozenSet[str] = frozenset({'os', 'sys', 'subprocess', 'pathlib', 'shutil'})

    FORBIDDEN_CALLS: FrozenSet[str] = frozenset(
        {
            'open',
            'eval',
            'exec',
            'compile',
            '__import__',
            'setattr',
            'delattr',
            'hasattr',
            'getattr',
            'breakpoint',
        }
    )

    FORBIDDEN_ATTRS: FrozenSet[str] = frozenset(
        {
            'env',
            'f_locals',
            'f_globals',
            'f_back',
            'f_code',
            # Reach-through to forbidden state via an allowlisted module's attributes
            # (GHSA-4rj2): e.g. ``typing.sys.modules['os'].environ``. ``sys`` is already
            # rejected as a forbidden module in the chain, but block the registry and the
            # environment mapping by name too, so a chain that reaches them another way
            # (``<any>.modules[...]``, ``<any>.environ``) is caught.
            'modules',
            'environ',
        }
    )

    # File / URL / pickle I/O on the allowlisted data libraries (pandas, numpy). These
    # escape the sandbox even though the module import is allowed: read_pickle unpickles
    # attacker data (__reduce__ -> RCE), read_* give arbitrary host file read, to_* give
    # arbitrary file write, numpy load/save carry the pickle primitive. The set is
    # deliberately broad; model code in the executor has no need for file/URL I/O (use
    # the provided tools instead).
    FORBIDDEN_IO_ATTRS: FrozenSet[str] = frozenset(
        {
            # pandas readers
            'read_pickle',
            'read_csv',
            'read_table',
            'read_fwf',
            'read_excel',
            'read_json',
            'read_html',
            'read_xml',
            'read_parquet',
            'read_orc',
            'read_feather',
            'read_hdf',
            'read_sql',
            'read_sql_query',
            'read_sql_table',
            'read_stata',
            'read_sas',
            'read_spss',
            'read_gbq',
            'read_clipboard',
            # pandas writers
            'to_pickle',
            'to_csv',
            'to_excel',
            'to_json',
            'to_html',
            'to_xml',
            'to_parquet',
            'to_orc',
            'to_feather',
            'to_hdf',
            'to_sql',
            'to_stata',
            'to_gbq',
            'to_clipboard',
            # numpy I/O
            'load',
            'loadtxt',
            'genfromtxt',
            'fromfile',
            'fromregex',
            'save',
            'savez',
            'savez_compressed',
            'savetxt',
            'tofile',
            'memmap',
            'DataSource',
        }
    )

    @staticmethod
    def format_syntax_error(code: str, exc: SyntaxError) -> str:
        line_no = exc.lineno or "?"
        msg = f"Python syntax error at line {line_no}: {exc.msg}"
        lines = code.splitlines()
        if isinstance(line_no, int) and 1 <= line_no <= len(lines):
            msg += f"\n  >>> {lines[line_no - 1].rstrip()}"
        lower = (exc.msg or "").lower()
        if "unterminated" in lower and ("string" in lower or "f-string" in lower):
            msg += (
                "\nHint: Do not embed large markdown, JSON, or prior tool/subagent outputs inside "
                "f-strings or triple-quoted strings. Build reports with '\\n'.join([...]), "
                "json.dumps() for dict sections, or await write_file('./output/report.md', content). "
                "For spawn_agent follow-ups: "
                'task = ".... Inputs JSON: " + json.dumps({"a": a_out, "b": b_out})'
            )
        elif "unexpected indent" in lower:
            msg += (
                "\nHint: Top-level statements must start at column 0. "
                "Do not indent code inside triple-quoted string literals."
            )
        return msg

    @staticmethod
    def validate_syntax(code: str, *, filename: str = "<code>") -> None:
        """Reject invalid Python before sandbox exec so the model can retry cleanly.

        Runs unconditionally, including under relaxed/skills/benchmark execution:
        this checks whether `exec()` will even parse the code, not a security policy.
        """
        try:
            compile(code, filename, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError as exc:
            raise CodeSyntaxError(SecurityValidator.format_syntax_error(code, exc)) from exc

    @staticmethod
    def validate_imports(code: str) -> None:
        """Validate that code only imports allowed modules.

        Args:
            code: Python code to validate

        Raises:
            ImportError: If dangerous or disallowed imports are found
            CodeSyntaxError: If code is not valid Python (via validate_syntax)
        """
        SecurityValidator.validate_syntax(code)

        if is_relaxed_execution():
            return

        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name.split('.')[0]
                    SecurityValidator._check_module(module_name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_name = node.module.split('.')[0]
                    SecurityValidator._check_module(module_name)

    @staticmethod
    def _check_module(module_name: str) -> None:
        """Check if a module is allowed.

        Args:
            module_name: Name of the module to check

        Raises:
            ImportError: If module is dangerous or not allowed
        """
        if module_name in SecurityValidator.DANGEROUS_IMPORTS:
            raise ImportError(f"Import of '{module_name}' is not allowed in restricted execution context")
        if module_name not in SecurityValidator.ALLOWED_IMPORTS:
            raise ImportError(f"Import of '{module_name}' is not allowed in restricted execution context")

    @staticmethod
    def _parse_for_security(code: str) -> ast.AST:
        try:
            return ast.parse(code)
        except SyntaxError as exc:
            raise CodeSyntaxError(SecurityValidator.format_syntax_error(code, exc)) from exc

    @staticmethod
    def _run_ast_security(code: str, *, strict: bool) -> None:
        tree = SecurityValidator._parse_for_security(code)
        forbidden_modules = (
            SecurityValidator.FORBIDDEN_MODULES if strict else SecurityValidator.DANGEROUS_IMPORT_MODULES
        )
        visitor = _SecurityAstVisitor(
            forbidden_modules=forbidden_modules,
            forbidden_calls=SecurityValidator.FORBIDDEN_CALLS,
            forbidden_attrs=SecurityValidator.FORBIDDEN_ATTRS,
            forbidden_io_attrs=SecurityValidator.FORBIDDEN_IO_ATTRS if strict else frozenset(),
            strict=strict,
        )
        visitor.visit(tree)

    @staticmethod
    def validate_dangerous_modules(wrapped_code: str) -> None:
        """Validate wrapped code for dangerous module imports only (lighter validation).

        This is less restrictive than validate_wrapped_code() - only checks for dangerous
        modules, not suspicious patterns. Suitable for CodeAgent where LLM-generated code
        may legitimately use dunder methods, etc.

        Args:
            wrapped_code: The wrapped code to validate

        Raises:
            PermissionError: If dangerous modules are detected
        """
        if is_relaxed_execution():
            return

        SecurityValidator._run_ast_security(wrapped_code, strict=False)

    @staticmethod
    def validate_wrapped_code(wrapped_code: str) -> None:
        """Validate wrapped code for dangerous imports and suspicious patterns (strict validation).

        Uses AST inspection so string/bytes literals (e.g. tool arguments) are ignored.
        Suitable for interactive cuga_lite mode where user code needs stricter validation.

        Args:
            wrapped_code: The wrapped code to validate

        Raises:
            PermissionError: If dangerous modules or suspicious patterns are detected
        """
        if is_relaxed_execution():
            return

        SecurityValidator._run_ast_security(wrapped_code, strict=True)

    @staticmethod
    def filter_safe_locals(locals_dict: dict) -> dict:
        """Filter out dangerous modules from locals dictionary.

        Args:
            locals_dict: Dictionary of local variables

        Returns:
            Filtered dictionary with dangerous modules removed, or original dict if execution is relaxed
        """
        if is_relaxed_execution():
            return locals_dict

        return {k: v for k, v in locals_dict.items() if k not in SecurityValidator.DANGEROUS_MODULE_NAMES}

    @staticmethod
    def assert_safe_globals(restricted_globals: dict) -> None:
        """Assert that no dangerous modules leaked into globals.

        Args:
            restricted_globals: Dictionary of global variables

        Raises:
            AssertionError: If dangerous modules are found
        """
        if is_relaxed_execution():
            return

        assert 'os' not in restricted_globals, "Security violation: os module in restricted_globals!"
        assert 'sys' not in restricted_globals, "Security violation: sys module in restricted_globals!"
        assert 'subprocess' not in restricted_globals, "Security violation: subprocess in restricted_globals!"

    @staticmethod
    def validate_context_usage(code: str, context_locals: dict) -> None:
        """Validate that code uses at least one variable from context.

        Args:
            code: Python code to validate
            context_locals: Dictionary of available context variables

        Raises:
            ValueError: If code doesn't use any context variables
        """
        if is_relaxed_execution():
            return
        if not context_locals:
            return

        code_without_comments = '\n'.join(line.split('#')[0] for line in code.split('\n'))

        for var_name in context_locals.keys():
            if re.search(rf'\b{re.escape(var_name)}\b', code_without_comments):
                return

        raise ValueError("Code must use at least one variable or tool from context")
