# Approval Path Hook Policy — Flow_0ybszcv

## Purpose

This hook intercepts the approval path (the yes-flow from the credit decision gateway)
immediately before the loan grant task. It is the integration point for any future
intervention logic on approved loan applications.

## Rule

Always allow the process to continue. No intervention at this stage.

## Default

Return `continue` with no `state_updates` and no target node override.
