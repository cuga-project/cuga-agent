# Experimental memory retention

Retention applies to the service instance namespace across users. Individual CUGA
agent IDs remain provenance/filter fields inside that namespace.

Deleting a conversation writes an outbox event in the same database transaction
that removes its history and stream events. When Evolve is enabled, the server
retries delivery every 30 seconds and acknowledges the outbox only after Evolve
persists the event. Delivery can repeat after a crash without duplicating effects.
The event contains service, agent, user, conversation ID, and deletion time; it
contains no messages. Evolve downtime does not roll back an already committed
conversation deletion.

New standard policies include `orphaned-conversations`: memories older than seven
days qualify only if Evolve has an explicit, matching source-deletion receipt.
An absent conversation listing is never treated as proof of deletion. Marking
includes held memories, while sweeping preserves them until the hold is removed.

Existing policies are not overwritten. To opt an existing policy into this rule:

```sh
evolve retention policies rules add cuga-standard --namespace SERVICE_INSTANCE_ID \
  --name orphaned-conversations --source-deleted --max-age-days 7 --action delete
```

This path requires PostgreSQL in Evolve. Missing user/agent/conversation provenance
is kept, and historical deletions are not inferred or backfilled. Memories created
after the recorded deletion time are also kept, protecting conversation-ID reuse;
ordinary age rules still apply to them. Receipt delivery alone does not delete a
memory: a policy mark/sweep or scheduled run must execute.

The supported UBI image installs Evolve and its extras from `uv.lock`, using the
source pin in `pyproject.toml`. Both model preloading and runtime dependency
installation use frozen resolution; updating Evolve requires updating that pin and
regenerating the lock. `EVOLVE_REF` build-argument overrides are no longer used.
