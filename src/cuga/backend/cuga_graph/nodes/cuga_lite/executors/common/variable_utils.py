import types
from typing import Any, Optional, Set

from loguru import logger

_TODO_CONFIRMATION_VALUES = frozenset({"Todos updated", "Todos have been updated"})


class VariableUtils:
    """Utilities for managing variables during code execution."""

    @staticmethod
    def strip_todo_confirmation_only_vars(new_vars: dict[str, Any]) -> dict[str, Any]:
        """Drop variables that only hold the short create_update_todos confirmation string."""
        if not new_vars:
            return new_vars
        return {
            k: v
            for k, v in new_vars.items()
            if not (isinstance(v, str) and v.strip() in _TODO_CONFIRMATION_VALUES)
        }

    @staticmethod
    def strip_tools_output_var(new_vars: dict[str, Any], code: str) -> dict[str, Any]:
        """Drop `tools_output` after find_tools — same idea as not persisting noisy todo confirmation; discovery markdown is for the turn only."""
        if not new_vars or "tools_output" not in new_vars:
            return new_vars
        if "find_tools" not in code:
            return new_vars
        return {k: v for k, v in new_vars.items() if k != "tools_output"}

    @staticmethod
    def _sanitize_recursive(obj: Any) -> Any:
        """Recursively convert non-JSON-serializable scalars and containers.

        Handles:
        - numpy: integer, floating, complexfloating, bool_, ndarray
        - pandas scalars: NaT → None, Timestamp → ISO string, Timedelta → seconds
        - Python datetime: datetime/date/time → ISO string, timedelta → seconds
        - bytes: UTF-8 string if decodable, else base64-encoded ASCII string
        - complex: {"real": float, "imag": float}
        - dict / list: recursive traversal

        DataFrame and Series are intentionally excluded; sanitize_value wraps
        those with metadata before delegating cell content here.
        """
        import base64
        import datetime as dt

        if obj is None:
            return obj

        # --- numpy ---
        try:
            import numpy as np  # type: ignore[import-untyped]

            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                # nan and inf are not valid JSON — map to None
                v = float(obj)
                return None if (v != v or v == float("inf") or v == float("-inf")) else v
            if isinstance(obj, np.complexfloating):
                return {"real": float(obj.real), "imag": float(obj.imag)}
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                # tolist() converts simple dtypes to Python natives; recurse to
                # catch object-dtype arrays that may still contain exotic types.
                return VariableUtils._sanitize_recursive(obj.tolist())
        except ImportError:
            pass

        # --- pandas scalars ---
        try:
            import pandas as pd  # type: ignore[import-untyped]

            if obj is pd.NaT or obj is pd.NA:
                return None
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            if isinstance(obj, pd.Timedelta):
                return obj.total_seconds()
        except ImportError:
            pass

        # --- Python stdlib datetime (datetime before date: it's a subclass) ---
        if isinstance(obj, dt.datetime):
            return obj.isoformat()
        if isinstance(obj, dt.date):
            return obj.isoformat()
        if isinstance(obj, dt.time):
            return obj.isoformat()
        if isinstance(obj, dt.timedelta):
            return obj.total_seconds()

        # --- bytes ---
        if isinstance(obj, bytes):
            try:
                return obj.decode("utf-8")
            except UnicodeDecodeError:
                return base64.b64encode(obj).decode("ascii")

        # --- complex ---
        if isinstance(obj, complex):
            return {"real": obj.real, "imag": obj.imag}

        # --- containers ---
        if isinstance(obj, dict):
            return {k: VariableUtils._sanitize_recursive(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [VariableUtils._sanitize_recursive(v) for v in obj]

        return obj

    @staticmethod
    def sanitize_value(value: Any) -> Any:
        """Convert non-JSON-serializable types to serializable equivalents.

        - pd.DataFrame → dict with records (cells recursively sanitized)
        - pd.Series    → dict with data list (values recursively sanitized)
        - All other types → delegated to _sanitize_recursive

        All other values are returned unchanged.
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]

            if isinstance(value, pd.DataFrame):
                return {
                    "__pandas_type__": "DataFrame",
                    "records": VariableUtils._sanitize_recursive(value.to_dict("records")),
                    "columns": list(value.columns),
                    "reconstruct": "pd.DataFrame(value['records'])",
                }
            if isinstance(value, pd.Series):
                return {
                    "__pandas_type__": "Series",
                    "data": VariableUtils._sanitize_recursive(value.tolist()),
                    "name": value.name,
                    "reconstruct": "pd.Series(value['data'], name=value['name'])",
                }
        except ImportError:
            pass

        return VariableUtils._sanitize_recursive(value)

    @staticmethod
    def is_serializable(value: Any) -> bool:
        """Check if a value is serializable.

        Args:
            value: Value to check

        Returns:
            True if value is serializable, False otherwise
        """
        if isinstance(value, (str, int, float, bool, type(None))):
            return True

        if isinstance(value, (list, tuple)):
            return all(VariableUtils.is_serializable(item) for item in value)

        if isinstance(value, dict):
            return all(
                VariableUtils.is_serializable(k) and VariableUtils.is_serializable(v)
                for k, v in value.items()
            )

        if isinstance(
            value, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType, type)
        ):
            return False

        try:
            import pandas as pd

            if isinstance(value, pd.DataFrame):
                return False
            if isinstance(value, pd.Series):
                return False

        except ImportError:
            pass

        try:
            from pydantic import BaseModel

            if isinstance(value, BaseModel):
                return True
        except ImportError:
            pass

        return False

    @staticmethod
    def filter_new_variables(
        all_locals: dict[str, Any], original_keys: Set[str], always_include_keys: Set[str] | None = None
    ) -> dict[str, Any]:
        """Filter and return only new, serializable variables.

        Args:
            all_locals: Dictionary of all local variables
            original_keys: Set of keys that existed before execution
            always_include_keys: Set of variable names to always include even if they existed before
                                (useful for variables that should be updated when reassigned)

        Returns:
            Dictionary of new serializable variables (preserves insertion order)
        """
        if always_include_keys is None:
            always_include_keys = set()

        new_keys = set(all_locals.keys()) - original_keys
        new_vars = {}

        for key in all_locals.keys():
            # Include if it's a new key OR if it's in always_include_keys
            if key not in new_keys and key not in always_include_keys:
                continue
            if key.startswith('_'):
                continue

            value = VariableUtils.sanitize_value(all_locals[key])
            if VariableUtils.is_serializable(value):
                new_vars[key] = value
            else:
                logger.debug(f"Skipping non-serializable variable '{key}': {type(value).__name__}")

        return new_vars

    @staticmethod
    def reorder_variables_by_print(new_vars: dict[str, Any], code: str) -> dict[str, Any]:
        """Reorder variables to move printed ones to the end.

        Args:
            new_vars: Dictionary of new variables
            code: Original code to analyze

        Returns:
            Reordered dictionary with printed variables at the end
        """
        if not new_vars:
            return new_vars

        lines = code.strip().split('\n')
        last_print_line = None

        for line in reversed(lines):
            stripped = line.strip()
            if 'print(' in stripped:
                last_print_line = stripped
                break

        if not last_print_line:
            return new_vars

        reordered_vars = {}
        print_vars = {}

        for var_name, var_value in new_vars.items():
            if var_name in last_print_line and len(var_name) > 3:
                print_vars[var_name] = var_value
            else:
                reordered_vars[var_name] = var_value

        reordered_vars.update(print_vars)
        return reordered_vars

    @staticmethod
    def filter_single_letter_variables(new_vars: dict[str, Any]) -> dict[str, Any]:
        """Filter out variables with single-letter names.

        Args:
            new_vars: Dictionary of new variables

        Returns:
            Dictionary with single-letter variable names removed
        """
        if not new_vars:
            return new_vars

        filtered_vars = {name: value for name, value in new_vars.items() if len(name) > 1}
        return filtered_vars

    @staticmethod
    def limit_variables_to_keep(new_vars: dict[str, Any], keep_last_n: int) -> dict[str, Any]:
        """Limit the number of variables to keep, keeping only the last N variables.

        Args:
            new_vars: Dictionary of variables (preserves insertion order)
            keep_last_n: Number of variables to keep:
                        -1 or 0 = keep all variables
                        N > 0 = keep only the last N variables

        Returns:
            Dictionary with limited variables (last N if keep_last_n > 0, all if keep_last_n <= 0)
        """
        if not new_vars:
            return new_vars

        # Keep all if keep_last_n is -1 or 0
        if keep_last_n <= 0:
            return new_vars

        # Convert to list to preserve order, then take last N
        var_items = list(new_vars.items())
        if len(var_items) <= keep_last_n:
            return new_vars

        # Keep only the last N variables
        limited_items = var_items[-keep_last_n:]
        return dict(limited_items)

    @staticmethod
    def add_variables_to_manager(
        new_vars: dict[str, Any],
        var_manager,
        result: str,
        skip_summary_keys: Optional[Set[str]] = None,
    ) -> str:
        """Add new variables to VariablesManager and append summary to result.

        Args:
            new_vars: Dictionary of new variables
            var_manager: VariablesManager instance
            result: Current execution result string
            skip_summary_keys: Variable names to still store but omit from the execution-output
                summary (e.g. ``todos`` from ``create_update_todos``, shown via Current Plan instead).

        Returns:
            Updated result string with variables summary
        """
        if not new_vars:
            return result

        if skip_summary_keys is None:
            skip_summary_keys = set()

        existing_names = (
            set(var_manager.get_variable_names()) if hasattr(var_manager, 'get_variable_names') else set()
        )
        created_names = []
        updated_names = []

        for var_name, var_value in new_vars.items():
            if var_name in existing_names:
                if var_name not in skip_summary_keys:
                    updated_names.append(var_name)
            else:
                if var_name not in skip_summary_keys:
                    created_names.append(var_name)
            var_manager.add_variable(var_value, name=var_name, description="Created during code execution")

        try:
            summary_names = [k for k in new_vars.keys() if k not in skip_summary_keys]
            if not summary_names:
                return result
            variables_summary = var_manager.get_variables_summary(variable_names=summary_names)
            if variables_summary and variables_summary != "# No variables stored":
                if created_names and updated_names:
                    header = "## New Variables Created / Updated:"
                elif updated_names:
                    header = "## Variables Updated:"
                else:
                    header = "## New Variables Created:"
                result += f"\n\n{header}\n{variables_summary}"
        except Exception as e:
            logger.debug(f"Could not generate variables summary: {e}")

        return result
