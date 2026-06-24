"""Verified ``run_command`` uv/python patterns for sandbox shells (local + native).

Empirically tested against LocalSandboxExecutor and NativeSandboxExecutor from
the cuga-agent repo root (workspace cwd inside the project tree).
"""

# --- Install / inspect (always uv; never pip / python -m pip) ----------------

SANDBOX_UV_PIP_INSTALL = "uv pip install <package>"
SANDBOX_UV_PIP_SHOW = "uv pip show <package>"
SANDBOX_UV_PIP_LIST = "uv pip list | grep -i <package>"

# --- Verify import after install (try in this order; stop at first success) ----

SANDBOX_VERIFY_IMPORT = (
    "After `uv pip install <package>`, verify with ONE of these (never `python -m pip`, "
    "`pip show`, `pip list`, or `python -m <package>` — sandbox venvs have no pip CLI and "
    "most packages are not runnable modules): "
    "(1) `python -c \"import <package>; print('ok')\"` — preferred; "
    "(2) `uv pip show <package>`; "
    "(3) `uv pip list | grep -i <package>`."
)

# --- Run scripts / inline code (try direct python first, then uv run) ----------

SANDBOX_RUN_FALLBACK = (
    "Run Python via `run_command`: after a successful install check, prefer "
    "`python -c '...'` or `python ./script.py` (venv is already active). "
    "If that fails (import error, wrong interpreter, or script error), retry once with "
    "`uv run --no-project python -c '...'` or `uv run --no-project ./script.py`. "
    "Do not retry with bare `uv run`, `uv run --active`, `python -m pip`, or `pip`."
)

SANDBOX_UV_RUN_PREFIX = "uv run --no-project"

SANDBOX_UV_FORBIDDEN = (
    "Never use bare `uv run` (syncs/builds the parent Cuga project), "
    "`uv run --active`, `pip` / `pip install` / `pip show` / `pip list`, "
    "`python -m pip ...`, or `python -m <package>` to validate imports."
)

SANDBOX_UV_COMMAND_NORMALIZATION = (
    "Sandbox Python via `run_command`: "
    f"install only with `{SANDBOX_UV_PIP_INSTALL}`. "
    f"{SANDBOX_VERIFY_IMPORT} "
    f"{SANDBOX_RUN_FALLBACK} "
    "If skill docs say `uv run python ...`, rewrite to "
    "`uv run --no-project python ...` or `uv run --no-project ./script.py`. "
    f"{SANDBOX_UV_FORBIDDEN} "
    "Rewrite legacy `pip install` / `python -m pip install` → `uv pip install`. "
    "Node/npm: plain `node ...` / `npm install ...` only — never `uv npm` or `uv run node`."
)
