# Hook Policy: Screen Before Credit Check

## Purpose

This hook runs immediately before the credit check task. It enforces a mandatory data
quality gate: if the applicant's name is not on record, the process must stop here.

## Decision Rule

Look at the `process_variables` provided in the current state.

**Terminate** if EITHER of the following is true:
- The key `applicant_name` does not exist in `process_variables`
- The key `applicant_name` exists but its value is `null`, an empty string `""`, or
  a string containing only whitespace

**Continue** only if `applicant_name` is present AND contains a non-empty, non-whitespace value.

> **Important**: Do not infer the applicant name from `_user_message`, `applicant_id`,
> or any other field. Only the explicit `applicant_name` process variable counts.
> An applicant ID is not a name. A request mentioning "applicant 23" has no name.

## Examples

| `applicant_name` value | Action |
|---|---|
| `"Jane Smith"` | continue |
| `"J"` | continue (any non-blank string is enough) |
| `""` | **terminate** |
| `" "` | **terminate** (whitespace only) |
| *(key absent)* | **terminate** |
| `null` | **terminate** |

## Output

Return `terminate` with a brief reason when the rule triggers.
Return `continue` with no reason when the check passes.
