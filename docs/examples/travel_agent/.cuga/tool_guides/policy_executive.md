---
id: tool_guide_policy_executive
name: Executive Travel Policy
type: tool_guide
description: Corporate travel policy limits for the executive role
enabled: true
priority: 90
prepend: true
target_tools:
  - analyze_travel_compliance
triggers:
  always: true
---

# Executive Travel Policy

Apply these limits when `role` is `executive`.

| Category | Rule | Limit |
|----------|------|-------|
| Flight | Max price per person | $1,500 |
| Flight | Allowed cabin classes | Economy, Premium Economy, Business |
| Flight | Max layovers | 1 |
| Flight | Direct flights | Preferred |
| Hotel | Max rate per night | $400 |
| Hotel | Min star rating | 4.0 ★ |
| Hotel | Required amenities | Free Wi-Fi, Breakfast included, Gym |
| Budget | Max total trip cost | $8,000 |
| Approval | Auto-approve threshold | Under $5,000 |

Reject any option that exceeds these limits and explain why.
