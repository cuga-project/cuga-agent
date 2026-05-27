# Loan Processor Policy

## Goal
Finalise and disburse the approved loan to the applicant.

## Required Actions
1. Record the disbursement in the system with the applicant's `applicant_id` and `loan_amount`.
2. Set `loan_granted = true` once disbursement is confirmed.
3. Notify the applicant of the approved amount and expected timeline.

## Constraints
- Do **not** alter `loan_amount` — disburse exactly what was approved.
- If disbursement fails for any technical reason, set `loan_granted = false` and record the error in `rejection_reason` so the process can be retried or escalated.
- Do **not** send a rejection notification — that is handled by the rejection handler task.

## Compliance Notes
- All disbursements above **10 000** require a confirmation reference number from the payment system.
- Retain an audit trail entry with timestamp, applicant ID, and amount.
