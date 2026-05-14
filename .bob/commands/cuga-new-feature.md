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

5. **Epic association (mandatory for every non-`[Epic]` issue):**
   - Ask the user which Epic this issue belongs to if they have not already specified one.
   - Run `gh issue list --label "type: epic" --state open` to show available open Epics.
   - If no suitable Epic exists, offer to create one first using the `[Epic]` prefix.
   - Add the following line verbatim at the **top** of the issue body (before any other content):
     ```
     Epic: #<epic-issue-number>
     ```
   - Do **not** skip this step. A missing `Epic:` line will cause the GitHub Actions check to fail.

6. Write the body using the same sections as `.github/ISSUE_TEMPLATE/feature_request.yml`: What you want and why, How it could work, Links or extra context (if any). Incorporate the user's message and any selected editor/context so the issue is concrete and complete.
7. Do not add "Made with Cursor" or similar promotional footers to the issue.
