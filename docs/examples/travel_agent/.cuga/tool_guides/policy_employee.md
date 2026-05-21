---
id: tool_guide_policy_employee
name: Employee Travel Policy
type: tool_guide
description: Corporate travel policy limits for the employee role
enabled: true
priority: 90
prepend: true
target_tools:
  - analyze_travel_compliance
triggers:
  always: true
---

# Employee Travel Policy

Apply these limits when `role` is `employee` (the default).

| Category | Rule | Limit |
|----------|------|-------|
| Flight | Max price per person | $500 |
| Flight | Allowed cabin classes | Economy only |
| Flight | Max layovers | 2 |
| Hotel | Max rate per night | $150 |
| Hotel | Min star rating | 3.0 ★ |
| Hotel | Required amenities | Free Wi-Fi |
| Budget | Max total trip cost | $2,000 |
| Approval | Auto-approve threshold | Under $1,000 |

Reject any option that exceeds these limits and explain why.
