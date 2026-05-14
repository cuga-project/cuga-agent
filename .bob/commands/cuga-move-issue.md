# Move an issue to a new status

Moves an issue to **Todo** (`status: todo`) or **In Progress** (`status: in-progress`) after
validating Epic association rules.

## Steps

1. Identify the issue number and the target status from the user's request.
   - Accepted status values: `todo` → label `status: todo` | `in-progress` → label `status: in-progress`.

2. Fetch the issue:
   ```bash
   gh issue view <number> --json number,title,body,labels
   ```

3. **Skip Epic checks** if the title starts with `[Bug]` or `[Epic]`. Jump directly to step 7.

4. Parse `Epic: #<epic-number>` from the first occurrence of that pattern in the issue body (case-insensitive).
   - If the line is missing, **abort** and tell the user:
     > "This issue has no Epic association. Add `Epic: #<number>` to the issue body before changing its status."

5. Fetch the referenced Epic:
   ```bash
   gh issue view <epic-number> --json number,title,labels,state
   ```

6. Verify the Epic has the label `status: in-progress`.
   - If not, **abort** and tell the user:
     > "Epic #<epic-number> is not in progress. Move the Epic to in-progress first:
     > `gh issue edit <epic-number> --add-label 'status: in-progress'`"

7. Remove any conflicting status labels from the issue, then apply the new one:
   ```bash
   gh issue edit <number> --remove-label "status: todo" --remove-label "status: in-progress"
   gh issue edit <number> --add-label "<target-status-label>"
   ```
   (Ignore errors from removing a label that is not present.)

8. Print the updated issue URL.
