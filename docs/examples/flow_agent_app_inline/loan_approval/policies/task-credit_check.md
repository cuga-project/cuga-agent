# Credit Check Policy

## Goal
Analyse the applicant's financial profile and produce a normalised credit score in the range **0.0 – 1.0**.

## Required Output
You **must** respond with a JSON object containing at least `credit_score` (a float 0.0–1.0) and `explanation` (a short string). Example:

```json
{"credit_score": 0.82, "explanation": "Strong payment history and low debt ratio."}
```

Do not omit `credit_score` or wrap the JSON in extra prose.

## Scoring Criteria
| Factor | Weight |
|--------|--------|
| Payment history (on-time vs late) | 35 % |
| Outstanding debt relative to income | 30 % |
| Length of credit history | 15 % |
| Credit mix (loans, cards, etc.) | 10 % |
| Recent credit enquiries | 10 % |

## Edge Cases
- If **no credit history** is found, set `credit_score = 0.3` and include `"no_history": true` in your response.
- If the applicant's identity **cannot be verified**, set `credit_score = 0.0` and set `rejection_reason = "identity_unverified"`.
- If the requested `loan_amount` exceeds **50 000**, apply a stricter threshold: each scoring factor is weighted 10 % higher on the debt-relative-to-income axis.

## Constraints
- Do **not** fabricate data.  Only use information supplied in the process variables and any tools available to you.
- Do **not** approve or reject the loan — that decision belongs to the gateway.
- Return a concise explanation alongside the score so the gateway policy can review it.
