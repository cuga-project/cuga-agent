# Report a bug (upstream issue)

1. Create the issue with the GitHub CLI (`gh issue create`).
2. Open it against the **origin** upstream (use that remote / repository—not a fork-only default).
3. Labels: run `gh label list` for that upstream repository (same scope as the issue, e.g. `--repo owner/name` if you are not using the default remote). Only use names that appear in that output. When you run `gh issue create`, pass `--label bug` and repeat `--label <name>` for each other applicable label. Do not invent label names.
4. **Epic placement:** Before creating the issue, search for an existing open epic this bug belongs under (`gh issue list --repo <origin> --label "type: epic" --state open`, plus title search for `[Epic]` / `[EPIC]`).

   - **Small bug exception:** A small, self-contained bug (narrow repro, no design change, not part of a larger workstream) may proceed **without** a parent epic. Say so explicitly when filing.
   - Otherwise a parent epic is required — do **not** file with no parent.
   - Decision rules when a parent is required:
     - **0 matches:** Tell the user none fit. Ask them to name an existing epic or create a new `[Epic]` first. Do not invent a parent and do not proceed without one.
     - **1 clear match:** Confirm that epic (number + title), then proceed.
     - **2+ matches:** Stop and ask carefully which epic to use. Present each as `#N — title` with a one-line why it might fit. Do not pick for the user.
   - When an epic is chosen: put `Part of #<number>.` at the top of the issue body (before the template sections), create the issue, then **link it as a GitHub sub-issue** under that epic. Do not stop at body text alone — parent/child linking is required.
   - For list/link/verify `gh` recipes, follow sibling [`github_commands.md`](./github_commands.md) (sections **Epics in this repo**, **Add a sub-issue**, **Verify after linking**). Use GraphQL `addSubIssue`; do not rely on unsupported `gh issue edit --set-parent`.

5. Write the body using the same sections as `.github/ISSUE_TEMPLATE/bug_report.yml`: What happened, How to reproduce, Environment, Logs, screenshots, or config (if any). Incorporate the user’s message and any selected editor/context so the issue is concrete and reproducible.
6. Use a sensible title consistent with the template’s title prefix.
7. Do not add “Made with Cursor” or similar promotional footers to the issue.
