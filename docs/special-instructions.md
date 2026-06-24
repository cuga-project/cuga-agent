# Special Instructions Configuration

[← Back to README](../README.md)

## How It Works

Each `.md` file contains specialized instructions that are automatically integrated into the CUGA's internal prompts when that component is active. Simply edit the markdown files to customize behavior for each node type.

**Available instruction sets:** `answer`, `api_planner`, `code_agent`, `plan_controller`, `reflection`, `shortlister`, `task_decomposition`

## Configuration

```
configurations/
└── instructions/
    ├── instructions.toml
    ├── default/
    │   ├── answer.md
    │   ├── api_planner.md
    │   ├── code_agent.md
    │   ├── plan_controller.md
    │   ├── reflection.md
    │   ├── shortlister.md
    │   └── task_decomposition.md
    └── [other instruction sets]/
```

Edit `configurations/instructions/instructions.toml`:

```toml
[instructions]
instruction_set = "default"  # or any instruction set above
```
