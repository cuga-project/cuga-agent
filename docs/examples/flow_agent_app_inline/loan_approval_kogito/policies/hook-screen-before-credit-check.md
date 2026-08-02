# Hook Policy: Screen Before Credit Check

## Purpose

Gate before the credit check. The process must know who the applicant is before
running a credit evaluation.

## Decision Rule

Determine whether an applicant name is available from **any** of these sources,
in order of preference:

1. The `applicant_name` process variable — check if it exists and is non-empty/non-whitespace.
2. The `_user_message` — scan for an explicit person name (e.g. "John Doe", "Jane Smith").

**Terminate** if no identifiable person name is found in either source.

**Continue** if a name is found in either source.

## What counts as a name

- A first-name + last-name pair → valid (e.g. "John Doe")
- A single recognisable given name → valid (e.g. "Maria")
- An applicant ID number (e.g. "applicant 23", "ID: A12345") → NOT a name
- Generic references ("the applicant", "my client", "the borrower") → NOT a name

## Examples

| Situation | Action |
|---|---|
| `applicant_name: "Jane Smith"` in process variables | continue |
| `applicant_name` absent, `_user_message` contains "loan for John Doe" | continue |
| `applicant_name: ""`, no name in `_user_message` | **terminate** |
| `applicant_name` absent, `_user_message` says "applicant ID 23" | **terminate** |
| `applicant_name` absent, `_user_message` mentions "my client" | **terminate** |
