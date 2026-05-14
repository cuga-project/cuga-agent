# Move an issue to a new status on the project board

Moves an issue's **Status** field on the GitHub Projects board to **Todo** or **In Progress**
after validating Epic association rules.

## Steps

1. Identify the issue number and target status (`Todo` or `In Progress`) from the user.

2. Fetch the issue:
   ```bash
   gh issue view <number> --json number,title,body,projectItems
   ```

3. **Skip Epic checks** if the title starts with `[Bug]` or `[Epic]`. Jump to step 7.

4. Parse `Epic: #<epic-number>` from the issue body (first match, case-insensitive).
   - If the line is missing, **abort**:
     > "This issue has no Epic association. Add `Epic: #<number>` to the body before changing its status."

5. Fetch the Epic's project board Status:
   ```bash
   gh issue view <epic-number> --json projectItems \
     --jq '[.projectItems.nodes[].fieldValues.nodes[]
            | select(.field.name == "Status") | .name] | first'
   ```

6. If the result is not `"In Progress"`, **abort**:
   > "Epic #<epic-number> has Status '<value>' on the board.
   > Move the Epic to **In Progress** first, then retry."

7. Find the project and item IDs for the issue:
   ```bash
   gh issue view <number> --json projectItems \
     --jq '.projectItems.nodes[] | {projectId: .project.id, itemId: .id}'
   ```

8. Find the Status field ID and the option ID for the target status:
   ```bash
   gh project field-list <project-number> --owner <org> --format json \
     --jq '.fields[] | select(.name == "Status") | {fieldId: .id, options: .options}'
   ```

9. Apply the new status:
   ```bash
   gh project item-edit \
     --id <item-id> \
     --project-id <project-id> \
     --field-id <status-field-id> \
     --single-select-option-id <option-id>
   ```

10. Print the updated issue URL and new status.
