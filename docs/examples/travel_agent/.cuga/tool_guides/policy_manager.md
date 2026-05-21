---
id: tool_guide_policy_manager
name: Manager Travel Policy
type: tool_guide
description: Corporate travel policy limits for the manager role
enabled: true
priority: 90
prepend: true
target_tools:
  - analyze_travel_compliance
triggers:
  always: true
---

# Manager Travel Policy

Apply these limits when `role` is `manager`.

| Category | Rule | Limit |
|----------|------|-------|
| Flight | Max price per person | $800 |
| Flight | Allowed cabin classes | Economy, Premium Economy |
| Flight | Max layovers | 2 |
| Hotel | Max rate per night | $250 |
| Hotel | Min star rating | 3.5 ★ |
| Hotel | Required amenities | Free Wi-Fi, Breakfast included |
| Budget | Max total trip cost | $4,000 |
| Approval | Auto-approve threshold | Under $2,500 |

Reject any option that exceeds these limits and explain why.
