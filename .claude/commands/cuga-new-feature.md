# Create a new issue (upstream)

1. Create the issue with the GitHub CLI (`gh issue create`).
2. Open it against the **origin** upstream (use that remote / repository—not a fork-only default).
3. Labels: run `gh label list` for that upstream repository. Only use names that appear in that output. Always pass `--label needs-triage` and add other applicable labels. Do not invent label names.
4. Choose the correct title prefix based on the issue type:

   | Prefix | When to use |
   |---|---|
   | `[Feature]` | New functionality or capability |
   | `[Design]` | Architecture, API design, or UX proposal before implementation |
   | `[Refactor]` | Internal restructuring with no behavior change |
   | `[Performance]` | Speed, memory, or efficiency improvements |
   | `[Security]` | Vulnerability, safety concern, or hardening |
   | `[Docs]` | Documentation additions or corrections |
   | `[Test]` | Missing tests, flaky tests, or test infrastructure |
   | `[Chore]` | Dependency updates, cleanup, or tooling |
   | `[Epic]` | Large body of work grouping multiple issues |
   | `[Question]` | Clarification needed, not a task |

5. **Epic placement (required unless creating an `[Epic]` itself):** Before creating the issue, you **must** search for an existing open epic that this work belongs under. A parent epic is mandatory — do **not** create the issue with no parent. (The only exception elsewhere is a small bug via `/cuga-report-bug`; it does not apply here.) Do not skip this step and do not create the issue until a parent epic is chosen.

   - List open epics from the same upstream repo, e.g. `gh issue list --repo <origin> --label "type: epic" --state open` and also search titles for `[Epic]` / `[EPIC]` so nothing is missed.
   - Score candidates by topic overlap with the proposed issue (title, summary, components). Prefer epics that clearly own the same capability or area.
   - Decision rules:
     - **0 matches:** Tell the user no fitting epic was found. Ask them to either name an existing epic to use, or create a new `[Epic]` first. Do **not** invent a parent and do **not** proceed without one.
     - **1 clear match:** Confirm that single epic with the user (number + title), then proceed with it.
     - **2+ matches:** Stop and ask carefully which epic to use. Present each candidate as `#N — title` with a one-line why it might fit. Do not pick for the user. Wait for an explicit choice.
   - When an epic is chosen: put `Part of #<number>.` at the top of the issue body (before the template sections), create the issue, then **link it as a GitHub sub-issue** under that epic. Do not stop at body text alone — parent/child linking is required.
   - For list/link/verify `gh` recipes, follow sibling [`github_commands.md`](./github_commands.md) (sections **Epics in this repo**, **Add a sub-issue**, **Verify after linking**). Use GraphQL `addSubIssue`; do not rely on unsupported `gh issue edit --set-parent`.
   - If the new issue is itself an `[Epic]`, skip parent-epic search.

6. Write the body using the same sections as `.github/ISSUE_TEMPLATE/feature_request.yml`: What you want and why, How it could work, Links or extra context (if any). Incorporate the user's message and any selected editor/context so the issue is concrete and complete.
7. Do not add "Made with Cursor" or similar promotional footers to the issue.
