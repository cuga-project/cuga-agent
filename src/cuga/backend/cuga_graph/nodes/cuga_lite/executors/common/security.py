import ast
import re
from typing import List, Set, Tuple
from loguru import logger

from .benchmark_mode import is_benchmark_mode


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

    SUSPICIOUS_PATTERNS: List[Tuple[str, str]] = [
        (r'__', 'dunder method/attribute access'),
        (r'(?<!\w)os\.', 'os module method call'),
        (r'\.os\.', 'os module access via attribute'),
        (r'\.os\b', 'os attribute access'),
        (r'(?<!\w)sys\.', 'sys module method call'),
        (r'\.sys\.', 'sys module access via attribute'),
        (r'\.sys\b', 'sys attribute access'),
        (r'(?<!\w)subprocess\.', 'subprocess module method call'),
        (r'\.subprocess\.', 'subprocess module access via attribute'),
        (r'\.subprocess\b', 'subprocess attribute access'),
        (r'\.env\b', 'environment variable access'),
        (r'__import__', 'dangerous builtin function'),
        (r'__builtins__', 'builtins access'),
        (r'__globals__', 'globals access'),
        (r'__dict__', 'dictionary access'),
        (r'__subclasses__', 'class hierarchy traversal'),
        (r'__subclass__', 'class hierarchy traversal'),
        (r'__bases__', 'class hierarchy traversal'),
        (r'__mro__', 'class hierarchy traversal'),
        (r'__class__', 'class introspection'),
        (r'__self__', 'method binding introspection'),
        (r'setattr\s*\(', 'attribute modification bypass'),
        (r'delattr\s*\(', 'attribute deletion bypass'),
        (r'hasattr\s*\(', 'attribute check'),
        (r'__traceback__', 'stack trace inspection'),
        (r'\.f_locals', 'stack frame local vars'),
        (r'\.f_globals', 'stack frame global vars'),
        (r'\.f_back', 'stack frame traversal'),
        (r'\.f_code', 'code object inspection'),
        (r'sys\._getframe', 'direct frame access'),
        (r'eval\s*\(', 'eval function call'),
        (r'exec\s*\(', 'exec function call'),
        (r'compile\s*\(', 'compile function call'),
        (r'breakpoint\s*\(', 'debugger invocation'),
        (r'pdb\.set_trace', 'debugger invocation'),
        (r'open\s*\(', 'file access'),
        (r'shutil\.', 'high-level file operations'),
        (r'glob\.', 'file pattern matching'),
        (r'pathlib\.', 'path object manipulation'),
        (r'pickle\.', 'serialization vulnerability'),
        (r'cPickle\.', 'serialization vulnerability'),
        (r'marshal\.', 'serialization vulnerability'),
        (r'shelve\.', 'serialization vulnerability'),
        (r'ctypes', 'foreign function interface (memory access)'),
        (r'socket', 'network access'),
        (r'urllib', 'network access'),
        (r'requests', 'network access'),
        (r'http\.client', 'network access'),
        (r'\\x[0-9a-fA-F]{2}', 'hex encoded string (potential obfuscation)'),
        (r'\\u[0-9a-fA-F]{4}', 'unicode encoded string (potential obfuscation)'),
    ]

    @staticmethod
    def validate_imports(code: str) -> None:
        """Validate that code only imports allowed modules.

        Args:
            code: Python code to validate

        Raises:
            ImportError: If dangerous or disallowed imports are found
        """
        if is_benchmark_mode():
            return

        try:
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
        except SyntaxError as e:
            logger.warning(f"Syntax error in code during pre-validation: {e}. Will attempt execution anyway.")

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
        if is_benchmark_mode():
            return

        for dangerous_module in ['os', 'sys', 'subprocess', 'pathlib', 'shutil']:
            if re.search(rf'\bimport\s+{dangerous_module}\b', wrapped_code) or re.search(
                rf'\bfrom\s+{dangerous_module}\b', wrapped_code
            ):
                raise PermissionError(
                    f"Security violation: Dangerous module '{dangerous_module}' detected in wrapped code"
                )

    @staticmethod
    def validate_wrapped_code(wrapped_code: str) -> None:
        """Validate wrapped code for dangerous imports and suspicious patterns (strict validation).

        This is more restrictive - checks both dangerous modules and suspicious patterns.
        Suitable for interactive cuga_lite mode where user code needs stricter validation.

        Args:
            wrapped_code: The wrapped code to validate

        Raises:
            PermissionError: If dangerous modules or suspicious patterns are detected
        """
        if is_benchmark_mode():
            return

        SecurityValidator.validate_dangerous_modules(wrapped_code)

        code_without_comments = '\n'.join(line.split('#')[0] for line in wrapped_code.split('\n'))

        for pattern, description in SecurityValidator.SUSPICIOUS_PATTERNS:
            target_code = (
                code_without_comments
                if description in ('eval function call', 'exec function call', 'compile function call')
                else wrapped_code
            )
            if re.search(pattern, target_code):
                raise PermissionError(
                    f"Security violation: Suspicious pattern detected - {description} in wrapped code"
                )

    @staticmethod
    def filter_safe_locals(locals_dict: dict) -> dict:
        """Filter out dangerous modules from locals dictionary.

        Args:
            locals_dict: Dictionary of local variables

        Returns:
            Filtered dictionary with dangerous modules removed, or original dict if benchmark mode
        """
        if is_benchmark_mode():
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
        if is_benchmark_mode():
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
        if is_benchmark_mode():
            return
        if not context_locals:
            return

        code_without_comments = '\n'.join(line.split('#')[0] for line in code.split('\n'))

        for var_name in context_locals.keys():
            if re.search(rf'\b{re.escape(var_name)}\b', code_without_comments):
                return

        raise ValueError("Code must use at least one variable or tool from context")
