# Lists of alerts a change is expected to close

One file per change. Each line is a CodeQL rule id, then a tab, then a file path.

Lines are matched on the rule and the file, never on the line number. The line
number moves as soon as the file is edited, so recording it would make every run
pass without really checking anything.

Check one against your working tree with:

```bash
scripts/codeql/verify.sh --manifest scripts/codeql/expected-closed/<file>.txt \
                         --baseline origin/main
```

A list only passes on a branch that actually contains the matching fix. Running
`error-responses.txt` on a branch without it will correctly fail.

## Each line covers the whole file, on purpose

A line says the rule produces no result anywhere in that file, not only at the
line GitHub happened to report. That is stricter than the alert itself. Some of
these files still contain similar-looking code that CodeQL does not currently
report: `main.py`, for example, has other handlers that pass error text into a
response, in places where CodeQL cannot trace the text back to a caught error.
If a later version of CodeQL starts reporting one of those, this check fails,
which is the behaviour we want.

The cost of that choice is that a line will also fail for an unrelated new
finding of the same rule in the same file. That seems a fair trade for a check
whose job is to stop this kind of leak from reappearing.

## Alerts handled by dismissal instead

There is no list here for alert #170 (`py/weak-sensitive-data-hashing` in
`src/cuga/backend/llm/models.py`). That alert was dismissed as a false positive
rather than fixed in code, because every available fix either fails to satisfy
the rule or makes the code worse. A list for it would always fail, which would
make this check untrustworthy. The reasoning is written out in a comment beside
the code it concerns.
