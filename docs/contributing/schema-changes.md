# Schema changes

This file holds the detail for the rule "a database schema change must state what happens to existing data" in [AGENTS.md](../../AGENTS.md).

Every pull request that changes a database schema must state what happens to data written by older versions. A user who upgrades must not lose data, and the server must still start on an old database.

This repository has no migration framework. Each store creates its tables with `CREATE TABLE IF NOT EXISTS` and upgrades older databases in place at startup. `CREATE TABLE IF NOT EXISTS` does nothing when the table already exists, so a column added only to the `CREATE TABLE` statement is missing in every existing installation.

## What counts as a schema change

A pull request changes a schema when it changes any of these, in SQLite or Postgres:

| Change | Examples |
|---|---|
| Table or index definition | `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`, `DROP`, a renamed table |
| Column definition | name, type, default, `NOT NULL`, primary key, unique constraint |
| Format of a stored value | JSON keys inside a text column, a status or enum value, a timestamp format, the embedding dimension of a vector table |
| Location of the data | database file path or name, Postgres schema, table name prefix |

## What the pull request must contain

1. **Upgrade code in the same pull request.** Follow the existing pattern: check, then add (`self._db.columns(table)` in `src/cuga/backend/events/agent_store.py`, `ADD COLUMN IF NOT EXISTS` in `src/cuga/backend/knowledge/storage/adapter.py`). The upgrade runs at every startup, so it must be safe to run again, and it must work on every backend the store supports.
2. **A chosen value for existing rows, with the reason in a comment.** The default decides how old data behaves after the upgrade (see the `source` column in `agent_store.py`).
3. **No drop, rename, or type change of a column or table that holds user data** unless the same pull request copies the data to its new place first. If the data cannot be carried over, stop and ask the user. Do not decide alone that the loss is acceptable.
4. **A test that upgrades an old database.** Create the tables with the old schema, insert a row, open the store with the new code, and assert that the row is still readable and correct. Mark the test with its type marker.
5. **A `Data migration` section in the pull request description**, with every line filled in.

## `Data migration` section

Copy this section into the pull request description:

```markdown
## Data migration

- Schema change: <tables and columns, old → new>
- Existing rows after upgrade: <what value they get, what the user sees>
- Upgrade path: <automatic at startup | manual step (give the command) | none needed, with the reason>
- Old and new version on the same database: <works | breaks, and how>
- Downgrade: <does the previous release still start on the upgraded database?>
- Data loss: <none | what is lost and who approved it>
- Tested: <test name, and the oldest release or schema the test starts from>
```

## Review

When you review a pull request, check its diff for the changes in the table above. If the `Data migration` section is missing or a line is empty, request changes.
