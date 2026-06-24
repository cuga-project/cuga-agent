# Save & Reuse (Experimental)

[← Back to README](../README.md)

Capture and reuse successful execution paths (plans, code, and trajectories) for faster and consistent behavior across repeated tasks.

## Setup

- Change `./src/cuga/settings.toml`: `cuga_mode = "save_reuse_fast"`
- Run: `cuga start demo`

## Demo Steps

- **First run**: `get top account by revenue`

  - This is a new flow (first time)
  - Wait for task to finish
  - Approve to save the workflow
  - Provide another example to help generalization of flow e.g. `get top 2 accounts by revenue`

- **Flow now will be saved**:

  - May take some time
  - Flow will be successfully saved

- **Verify reuse**: `get top 4 accounts by revenue`

  - Should run faster using saved workflow
