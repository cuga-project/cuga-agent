# Credit Decision Policy — Gateway_09ad5fc

## Purpose
This policy governs the routing decision at the credit evaluation gateway.
After the credit checker agent has assessed the applicant and stored a
`credit_score` (float, 0.0–1.0) in the process variables, this gateway
determines whether the application proceeds to loan processing or rejection.

## Routing Rules

| Credit Score Range | Classification | Route          |
|--------------------|----------------|----------------|
| 0.80 – 1.00        | Excellent      | Approve (yes)  |
| 0.60 – 0.79        | Good           | Approve (yes)  |
| 0.40 – 0.59        | Fair           | Reject (no)    |
| 0.00 – 0.39        | Poor           | Reject (no)    |

**Primary threshold**: `credit_score > 0.6` → approve; `credit_score ≤ 0.6` → reject.

## Override Conditions

- If `credit_score` is missing or could not be determined (None / 0.0 with no
  prior task result from the credit checker), **reject** and flag for manual review.
- If the applicant's `loan_amount` exceeds 50000, reject the loan request anyways.

## Decision Output

Select **exactly one** of the two outgoing flow IDs:
- `Flow_0ybszcv` — approval path (credit sufficient)
- `Flow_1jgea85` — rejection path (credit insufficient)
