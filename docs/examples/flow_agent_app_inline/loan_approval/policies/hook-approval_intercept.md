# Approval Path Hook Policy — Flow_0ybszcv

## Purpose

This hook intercepts the approval path (the yes-flow from the credit decision gateway)
immediately before the loan grant task. It is the integration point for regulatory
overrides that must redirect approved applications to rejection before the loan is issued.

## Rule

Only applicant ID `4321` (the four-digit string "4321", nothing else) is subject to a regulatory restriction.

**Exact match only**: compare `applicant_id == "4321"` as a full string. Do NOT trigger this rule for any other value, including IDs that contain the digits 4, 3, 2, or 1 in any order or subset (e.g. `23`, `321`, `4320`, `43210` must all pass through as `continue`).

If and only if `applicant_id` is exactly `"4321"`, use action `skip_to` with `target_node` set to `Activity_131ar38` (the rejection task node), and set the following process variables via `state_updates`:
- `rejection_reason`: `"regulatory_policy_restriction"`
- `policy_override_active`: `true`

These variables signal to the rejection handler that the rejection is intentional and policy-compliant, not a credit-score outcome.

## Default

For every applicant ID that is not exactly `"4321"` — including `"23"`, `"321"`, `"4320"`, and any other value — return `continue` with no `state_updates` and no `target_node`. Do not modify any process variables.
