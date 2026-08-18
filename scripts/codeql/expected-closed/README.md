# Expected-closed manifests

One file per change that closes CodeQL alerts. Each row is `<rule-id><TAB><path>`.

Rows match on rule id and file, never line number — the line moves the moment the
file is edited, so pinning it would turn every run into a false pass.

Run one against your working tree with:

```bash
scripts/codeql/verify.sh --manifest scripts/codeql/expected-closed/<file>.txt \
                         --baseline origin/main
```

A manifest only passes on a branch that actually contains the corresponding fix;
running `error-responses.txt` on a branch without it will correctly fail.

## Rows are file-level, on purpose

A row asserts the rule produces **no result anywhere in that file**, not just at
the line GitHub happened to flag. That is deliberately stricter than the alert:
several of these files still contain same-shaped constructs that CodeQL does not
currently flag (`main.py` has other `detail=str(e)` handlers where it cannot
trace the value back to a caught exception). If a future CodeQL version starts
flagging one of them, this check goes red — which is the point.

The flip side is that a row will also go red for an unrelated new finding of the
same rule in the same file. That is a fair trade for a gate whose job is to stop
a leak class from creeping back.
