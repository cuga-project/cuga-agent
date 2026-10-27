import asyncio
import json
from cuga.config import settings
from .benchmark_mode import is_relaxed_execution


class RestrictedEnvironment:
    """Manages restricted execution environment for local code execution."""

    @staticmethod
    def is_benchmark_mode() -> bool:
        """Check if benchmark mode is enabled (non-default benchmark setting).

        Returns:
            True if benchmark mode is enabled, False otherwise
        """
        return settings.advanced_features.benchmark != "default"

    @staticmethod
    def create_restricted_import(allowed_modules: set):
        """Create a restricted import function.

        Args:
            allowed_modules: Set of allowed module names

        Returns:
            Restricted import function, or original import if execution is relaxed
        """
        if is_relaxed_execution():
            return __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__

        _original_import = (
            __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__
        )

        def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.split('.')[0] not in allowed_modules:
                raise ImportError(f"Import of '{name}' is not allowed in restricted execution context")
            return _original_import(name, globals, locals, fromlist, level)

        return restricted_import

    @staticmethod
    def create_safe_builtins(restricted_import_func) -> dict:
        """Create a dictionary of safe builtin functions.

        Args:
            restricted_import_func: The restricted import function to use

        Returns:
            Dictionary of safe builtins, or full builtins if execution is relaxed
        """
        if is_relaxed_execution():
            # Relaxed execution: return full builtins
            if isinstance(__builtins__, dict):
                return __builtins__.copy()
            else:
                return dict(__builtins__.__dict__)

        return {
            'dict': dict,
            'list': list,
            'tuple': tuple,
            'set': set,
            'frozenset': frozenset,
            'str': str,
            'bytes': bytes,
            'bytearray': bytearray,
            'int': int,
            'float': float,
            'bool': bool,
            'complex': complex,
            'len': len,
            'range': range,
            'enumerate': enumerate,
            'zip': zip,
            'map': map,
            'filter': filter,
            'sorted': sorted,
            'reversed': reversed,
            'sum': sum,
            'min': min,
            'max': max,
            'abs': abs,
            'round': round,
            'any': any,
            'all': all,
            'chr': chr,
            'ord': ord,
            'format': format,
            'repr': repr,
            'isinstance': isinstance,
            'issubclass': issubclass,
            'type': type,
            # getattr/hasattr/setattr/delattr are intentionally NOT exposed (GHSA-4rj2):
            # they fetch an attribute by a runtime string, which defeats the AST name
            # checks in SecurityValidator (e.g. ``g = getattr; g(mod, 'envi'+'ron')``
            # reaches os.environ with no forbidden attribute ever appearing in the source).
            'iter': iter,
            'next': next,
            'slice': slice,
            'BaseException': BaseException,
            'Exception': Exception,
            'ValueError': ValueError,
            'TypeError': TypeError,
            'KeyError': KeyError,
            'IndexError': IndexError,
            'AttributeError': AttributeError,
            'RuntimeError': RuntimeError,
            'StopIteration': StopIteration,
            'AssertionError': AssertionError,
            'ImportError': ImportError,
            'print': print,
            'None': None,
            'True': True,
            'False': False,
            # locals() is required by the code wrapper (it returns locals() to capture
            # new variables); vars() is NOT exposed — vars(module) hands back the
            # module's __dict__, so ``vars(sys)['modules']['os']`` or
            # ``vars(os_mod)['environ']`` reaches forbidden state with no forbidden
            # attribute in the source for the AST visitor to catch (GHSA-4rj2).
            'locals': locals,
            'staticmethod': staticmethod,
            '__name__': '__restricted__',
            '__build_class__': __build_class__,
            '__import__': restricted_import_func,
        }

    @staticmethod
    def create_restricted_globals(safe_builtins: dict, safe_locals: dict) -> dict:
        """Create restricted globals dictionary.

        Args:
            safe_builtins: Dictionary of safe builtin functions
            safe_locals: Dictionary of safe local variables/tools

        Returns:
            Dictionary of restricted globals, or unrestricted globals if execution is relaxed
        """
        if is_relaxed_execution():
            # Relaxed execution: return unrestricted globals
            unrestricted_globals = {
                "__builtins__": safe_builtins,
            }
            # Add common modules
            try:
                import sys

                unrestricted_globals["sys"] = sys
            except ImportError:
                pass
            try:
                import os

                unrestricted_globals["os"] = os
            except ImportError:
                pass
            unrestricted_globals["asyncio"] = asyncio
            unrestricted_globals["json"] = json
            try:
                import pandas as pd

                unrestricted_globals["pd"] = pd
                unrestricted_globals["pandas"] = pd
            except ImportError:
                pass
            unrestricted_globals.update(safe_locals)
            return unrestricted_globals

        restricted_globals = {
            "__builtins__": safe_builtins,
            "asyncio": asyncio,
            "json": json,
        }

        try:
            import pandas as pd

            restricted_globals["pd"] = pd
            restricted_globals["pandas"] = pd
        except ImportError:
            pass

        restricted_globals.update(safe_locals)
        return restricted_globals
