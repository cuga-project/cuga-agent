# Hook Policy: Screen Before Credit Check

## Purpose

Intercepts the process before the credit check task runs.

## Rule

If the applicant name is missing (empty, null, or not provided), **terminate** the process immediately.

## Action

- If `applicant_name` is absent or blank in the process variables, return `terminate`.
- Otherwise, return `continue`.
