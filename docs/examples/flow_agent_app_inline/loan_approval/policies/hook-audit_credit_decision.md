## Rule

Applicant ID `4321` is subject to a regulatory restriction and must never receive a loan.
If the current applicant's `applicant_id` is exactly `4321`, override the gateway decision and redirect to the rejection task.

When overriding for applicant `4321`, set the following process variables via `state_updates`:
- `rejection_reason`: `"regulatory_policy_restriction"`
- `policy_override_active`: `true`

These variables signal to the rejection handler that the rejection is intentional and policy-compliant, not a credit-score outcome.

## Default

For **all other applicant IDs** (i.e. `applicant_id` is anything other than `4321`), return `continue` with no state_updates and no target_node. Do not modify any process variables.
