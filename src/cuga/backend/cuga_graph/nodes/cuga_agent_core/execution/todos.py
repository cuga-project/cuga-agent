"""Task todos schemas, formatting, and tool shared across all agents."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class Todo(BaseModel):
    """A single todo item with text and status."""

    text: str = Field(..., description="The task description")
    status: str = Field(
        default="pending",
        description="Status of the todo: 'pending', 'in_progress', or 'completed'",
    )


class TodosInput(BaseModel):
    """Input schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos, each with 'text' and 'status' fields")


class TodosOutput(BaseModel):
    """Output schema for create_update_todos function."""

    todos: List[Todo] = Field(..., description="List of todos with their current status")


def _try_parse_todos_payload(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, dict) or "todos" not in value:
        return None
    raw = value["todos"]
    if not isinstance(raw, list):
        return None
    if not raw:
        return []
    if not all(isinstance(x, dict) and "text" in x and "status" in x for x in raw):
        return None
    return raw


def extract_task_todos_from_new_vars(new_vars: dict) -> Optional[List[Dict[str, Any]]]:
    for val in new_vars.values():
        parsed = _try_parse_todos_payload(val)
        if parsed is not None:
            return parsed
    return None


def _serialize_todos_for_store(todos_list: List[Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for t in todos_list:
        if isinstance(t, Todo):
            out.append({"text": t.text, "status": t.status})
        elif hasattr(t, "model_dump"):
            d = t.model_dump()
            out.append({"text": str(d.get("text", "")), "status": str(d.get("status", "pending"))})
        elif isinstance(t, dict):
            out.append({"text": str(t.get("text", "")), "status": str(t.get("status", "pending"))})
        else:
            out.append({"text": str(t), "status": "pending"})
    return out


async def create_update_todos_tool(
    agent_state: Optional[Any] = None,
    todos_store_ref: Optional[List[Dict[str, str]]] = None,
    write_todos: Optional[Callable[[List[Dict[str, str]]], None]] = None,
) -> StructuredTool:
    """Create a create_update_todos StructuredTool for managing task todos.

    Args:
        agent_state: Optional AgentState (reserved for future use)
        todos_store_ref: Mutable list shared with the graph; latest todos are written here for the system prompt.
        write_todos: Optional callback receiving the serialized todos. Lets a caller persist todos into
            run-local state (e.g. the supervisor) instead of a shared list, avoiding cross-run bleed.

    Returns:
        StructuredTool configured for creating and updating todos
    """

    async def create_update_todos_func(todos: Any) -> TodosOutput:
        """Create or update a list of todos for complex multi-step tasks.

        Use this tool when you have a complex task that requires multiple steps.
        This helps you track progress and organize your work.

        Args:
            todos: List of todo dicts/models (matches ``TodosInput.todos`` / tool schema).

        Returns:
            Short confirmation only (full list is shown in the system prompt via todos_store_ref).
        """
        input_data = todos
        # Handle different input types
        if isinstance(input_data, TodosInput):
            todos_list = input_data.todos
        elif isinstance(input_data, dict):
            # If it's a dict, check if it has 'todos' key
            if "todos" in input_data:
                todos_list = input_data["todos"]
            else:
                # If no 'todos' key, treat the whole dict as a single todo or wrap it
                todos_list = [input_data]
            # Convert dict items to Todo models
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in todos_list]
        elif isinstance(input_data, list):
            # If it's a list directly, convert each item to Todo
            todos_list = [Todo(**todo) if isinstance(todo, dict) else todo for todo in input_data]
        else:
            # Fallback: try to create TodosInput
            try:
                if isinstance(input_data, dict):
                    input_data = TodosInput(**input_data)
                else:
                    input_data = TodosInput(todos=input_data)
                todos_list = input_data.todos
            except Exception:
                # Last resort: wrap in a list
                todos_list = [Todo(**input_data) if isinstance(input_data, dict) else input_data]

        if todos_store_ref is not None or write_todos is not None:
            serialized = _serialize_todos_for_store(todos_list)
            if todos_store_ref is not None:
                todos_store_ref.clear()
                todos_store_ref.extend(serialized)
            if write_todos is not None:
                write_todos(serialized)

        normalized = [t if isinstance(t, Todo) else Todo(**t) for t in todos_list]
        return TodosOutput(todos=normalized)

    return StructuredTool.from_function(
        func=create_update_todos_func,
        name="create_update_todos",
        description="Create or update a list of todos for complex multi-step tasks. Pass `todos` as a list of objects with 'text' and 'status' ('pending', 'in_progress', or 'completed'). Returns a todos payload; the full list is shown in the system prompt under 'Current task todos' (Current Plan).",
        args_schema=TodosInput,
        return_direct=False,
    )


def format_task_todos_system_block(todos: List[Dict[str, str]]) -> str:
    if not todos:
        return ""
    lines = [
        "",
        "---",
        "",
        "## Current task todos",
        "",
        "Execution only prints **Todos updated** after each change; use this list as the source of truth.",
        "",
    ]
    for i, item in enumerate(todos, start=1):
        status = item.get("status", "pending")
        text = item.get("text", "")
        lines.append(f"{i}. **[{status}]** {text}")
    lines.append("")
    return "\n".join(lines)


def format_current_plan_section(task_todos: List[Dict[str, Any]]) -> str:
    lines = ["## Current Plan", ""]
    for item in task_todos:
        text = str(item.get("text", "")).strip()
        status = str(item.get("status", "pending")).strip()
        lines.append(f"- **[{status}]** {text}")
    return "\n".join(lines) + "\n"
