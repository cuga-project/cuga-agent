---
id: tool_approval_dam_shell
name: Confirm DAM shell command execution
type: tool_approval
enabled: true
required_tools:
  - run_command
approval_message: Review the CUGA shell command before execution. Commands run inside this DAM agent and can access its workspace and granted connections.
show_code_preview: true
auto_approve_after: null
---
