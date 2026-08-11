# Ruff check and format

1. Run `uv run ruff check --fix` on the project (or the files in scope).
2. Fix any issues Ruff still reports after `--fix` (manual fixes or refactors — do not suppress without a good reason).
3. Run `uv run ruff format` so formatting matches project style.
