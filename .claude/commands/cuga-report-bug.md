# Report a bug (upstream issue)

1. Create the issue with the GitHub CLI (`gh issue create`).
2. Open it against the **origin** upstream (use that remote / repository—not a fork-only default).
3. Labels: run `gh label list` for that upstream repository (same scope as the issue, e.g. `--repo owner/name` if you are not using the default remote). Only use names that appear in that output. When you run `gh issue create`, pass `--label bug` and repeat `--label <name>` for each other applicable label. Do not invent label names.
4. Write the body using the same sections as `.github/ISSUE_TEMPLATE/bug_report.yml`: What happened, How to reproduce, Environment, Logs, screenshots, or config (if any). Incorporate the user’s message and any selected editor/context so the issue is concrete and reproducible.
5. Use a sensible title consistent with the template’s title prefix.
6. Do not add “Made with Cursor” or similar promotional footers to the issue.
