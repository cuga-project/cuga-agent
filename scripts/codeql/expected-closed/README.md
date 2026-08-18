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
