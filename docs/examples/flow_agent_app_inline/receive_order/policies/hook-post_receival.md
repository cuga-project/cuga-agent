## Rule

Order ID `1212` is a special internal test order for which no invoice should be generated.
If the current order's `order_id` is exactly `1212`, redirect the flow directly to the
Reserve Inventory task (`Activity_1h9ix55`), bypassing the parallel gateway and the
Generate Invoice task entirely.

Use action `skip_to` with `target_node = "Activity_1h9ix55"`.

Set the following process variable via `state_updates` to record the override:
- `invoice_skipped`: `true`

## Default

For **all other order IDs** (i.e. `order_id` is anything other than `1212`), return
`continue` with no `state_updates` and no `target_node`. The flow proceeds normally
through the parallel gateway, running both Reserve Inventory and Generate Invoice
concurrently.
