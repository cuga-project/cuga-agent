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

SANDBOX_WORKSPACE_PATHS = (
    "Workspace paths: use workspace-relative paths everywhere (e.g. `./uploads/foo.json`, "
    "`./scripts/parse.py`) — they work in `read_file`/`write_file`/`list_files`, `run_command`, "
    "and inside Python scripts (`open('uploads/foo.json')`). Session uploads are listed in "
    "`.manifest.json` with a single `path` field per file. Absolute `/workspace/...` is also "
    "accepted (rewritten to `./` in shell automatically)."
)

SANDBOX_RUN_COMMAND_RETURN = (
    "`run_command` returns stdout as a plain **string**; `\\n[stderr]\\n...` is appended "
    "**only on failure** (non-zero exit) — never a dict. "
    "Parse JSON script output with "
    "`json.loads(out.split('\\n[stderr]\\n', 1)[0].strip())`; do **not** use `out[\"raw_output\"]` "
    "or subscript the return value. "
    "If output contains `[stderr]`, `can't open file`, `head: None`, `exited with status`, or "
    "`[run_command error]`, the command failed — print the full string, fix the cause, and "
    "**stop** before the next step. Do **not** regex-parse, `json.loads`, or infer prefixes/schema "
    "from failed output. After `uv pip install`, verify with "
    "`python -c \"import pkg; print('ok')\"` (install stderr is not included on success)."
)

SANDBOX_WRITE_THEN_RUN = (
    "Before `run_command(\"python ./script.py\")`, ensure the script exists: "
    "`await write_file(\"./script.py\", content)` in a prior step (or earlier in the same block), "
    "confirm the write succeeded (return starts with `File written:` — not `[write_file error]`), "
    "then run the **same path** you wrote (e.g. if you wrote `./scripts/parse_phase1.py`, run "
    "`python ./scripts/parse_phase1.py`, not `./parse_phase1.py`). "
    "Prefer separate code blocks for write vs run when the script is large."
)

SANDBOX_UV_COMMAND_NORMALIZATION = (
    "Sandbox Python via `run_command`: "
    f"install only with `{SANDBOX_UV_PIP_INSTALL}`. "
    f"{SANDBOX_VERIFY_IMPORT} "
    f"{SANDBOX_RUN_FALLBACK} "
    f"{SANDBOX_RUN_COMMAND_RETURN} "
    f"{SANDBOX_WRITE_THEN_RUN} "
    f"{SANDBOX_WORKSPACE_PATHS} "
    "If skill docs say `uv run python ...`, rewrite to "
    "`uv run --no-project python ...` or `uv run --no-project ./script.py`. "
    f"{SANDBOX_UV_FORBIDDEN} "
    "Rewrite legacy `pip install` / `python -m pip install` → `uv pip install`. "
    "Node/npm: plain `node ...` / `npm install ...` only — never `uv npm` or `uv run node`."
)
