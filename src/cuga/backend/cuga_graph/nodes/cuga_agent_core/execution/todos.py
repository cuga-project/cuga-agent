"""Task todos schemas, formatting, and tool shared across all agents."""

from __future__ import annotations

import ast
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

TODOS_TOOL_NAME = "create_update_todos"

_AWAITED_CALL_RE = re.compile(r"await\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

# --- Bookkeeping-todo filter (issue #676, defect 3) ---------------------------------
#
# Models routinely append self-referential items ("Confirm operation completed",
# "Provide summary to user") to their plans. These are never part of the user's task,
# and discharging them has produced real damage: 07bb666_1 lost its only failing
# assertion to two file_system_create_file calls made to "persist a summary" of its
# own progress. Items are matched by their LEADING verb phrase only, so ordinary task
# steps that merely mention a summary or a check are kept.
#
# Each entry: (pattern, destination_exempt). When destination_exempt is True, an item
# that names a real destination ("Write summary to Simple Note") is kept — producing
# an artifact somewhere the user asked for is task work, reporting "to the user" is not.
_BOOKKEEPING_PATTERNS: List[tuple] = [
    # "Confirm completion", "Confirm order placement and provide summary", ... —
    # every leading-"Confirm" item observed across three bundles was bookkeeping.
    # "before" exempts pre-action checks ("Confirm cart contents before purchase").
    (re.compile(r"^confirm\b(?!.*\bbefore\b)", re.I), False),
    # "Verify completion", "Verify everything is done" — not "Verify the transfer amount".
    (re.compile(r"^verify\b.*\b(complet\w+|success|everything|all (?:steps|items|todos))\b", re.I), False),
    # "Summarize actions taken" — not "Summarize the article" (real content work).
    (
        re.compile(
            r"^summari[sz]e\s*(?:$|(?:the\s+)?(?:actions?|results?|work|progress|steps|outcome)\b)", re.I
        ),
        False,
    ),
    # "Report completion", "Report back", "Report actions taken", "Report deletion summary".
    (
        re.compile(
            r"^report\s*(?:$|(?:back|completion|success|outcome|status|progress|results?|actions?|(?:\w+\s+)?summary|to (?:the )?user)\b)",
            re.I,
        ),
        False,
    ),
    # "Generate summary report", "Prepare final answer", "Compose final answer for the user"
    # — destination-exempt: "Write summary to Simple Note" is task work.
    (
        re.compile(
            r"^(?:generate|create|prepare|write|produce|compose|draft)\b.*\b(?:summary|report|final answer)\b",
            re.I,
        ),
        True,
    ),
    # "Provide summary to user", "Return final answer", "Present results to the user".
    (
        re.compile(
            r"^(?:provide|present|give|return|deliver)\b.*\b(?:summary|final answer|results? to (?:the )?user)\b",
            re.I,
        ),
        True,
    ),
    # "Finalize and report" — not "Finalize actions (accept or delete)" (real work).
    (re.compile(r"^finali[sz]e\s*(?:$|and\s+(?:report|summari[sz]e|provide)\b)", re.I), False),
    # "Record actions taken", "Persist the results summary", "Document progress".
    (
        re.compile(
            r"^(?:document|record|persist|save|log)\b.*\b(?:actions?|progress|summary|report|completion)\b",
            re.I,
        ),
        True,
    ),
]

# A named destination other than the user themselves marks a user-requested artifact.
_DESTINATION_RE = re.compile(r"\b(?:in|into|to|at|on)\s+(?!(?:the\s+)?(?:user|me)\b)\S", re.I)

_LEADING_NUMBERING_RE = re.compile(r"^[\s\d.)\-*•]+")


def is_bookkeeping_todo(text: str) -> bool:
    """True when a todo item's only purpose is to record the agent's own progress."""
    t = _LEADING_NUMBERING_RE.sub("", str(text or "")).strip()
    if not t:
        return False
    for pattern, destination_exempt in _BOOKKEEPING_PATTERNS:
        if not pattern.search(t):
            continue
        if destination_exempt and _DESTINATION_RE.search(t):
            continue
        return True
    return False


def split_bookkeeping_todos(items: List[Dict[str, Any]]) -> tuple:
    """Split serialized todo dicts into (kept, dropped) by is_bookkeeping_todo."""
    kept, dropped = [], []
    for item in items:
        (dropped if is_bookkeeping_todo(item.get("text", "")) else kept).append(item)
    return kept, dropped


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
    note: Optional[str] = Field(
        default=None,
        description="Set when items were filtered out (e.g. bookkeeping items); explains what was dropped and why",
    )


def _awaited_call_names(script: str) -> Optional[set]:
    """Names of every awaited call in ``script``, or None if it cannot be read.

    AST first (handles multi-line calls and comments); falls back to a regex when the
    block does not parse, which happens for the partial code the model sometimes emits.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError:
        matches = _AWAITED_CALL_RE.findall(script)
        return {name.rsplit(".", 1)[-1] for name in matches} if matches else None

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def is_todo_only_script(script: str) -> bool:
    """True when a code block calls ``create_update_todos`` and no other tool.

    The prompt requires todo updates to be executed in isolation, so such a block
    changes no application state: no API is called, no variable of substance is
    produced, and the plan it writes is re-rendered into the system prompt anyway.
    A reflection pass over it therefore has nothing to reflect on — it can only
    restate "the todo list was updated, nothing else happened" at the cost of a
    full LLM call. Callers use this to skip that pass.

    Non-tool calls (``print``, ``json.dumps``, …) are ignored: only awaited calls
    count, which is what the Isolation Rule in the executor prompt constrains.
    """
    names = _awaited_call_names(script or "")
    return bool(names) and names == {TODOS_TOOL_NAME}


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
    filter_bookkeeping: bool = True,
) -> StructuredTool:
    """Create a create_update_todos StructuredTool for managing task todos.

    Args:
        agent_state: Optional AgentState (reserved for future use)
        todos_store_ref: Mutable list shared with the graph; latest todos are written here for the system prompt.
        write_todos: Optional callback receiving the serialized todos. Lets a caller persist todos into
            run-local state (e.g. the supervisor) instead of a shared list, avoiding cross-run bleed.
        filter_bookkeeping: Drop self-referential items ("Confirm completion", "Provide summary")
            before storing, returning a note naming what was dropped (issue #676).

    Returns:
        StructuredTool configured for creating and updating todos
    """

    async def create_update_todos_func(todos: Any) -> TodosOutput:
        """Create or update a list of todos for complex multi-step tasks.

        Use this tool when you have a complex task that requires multiple steps.
        This helps you track progress and organize your work.

        This list is your own private scratchpad. It is not a connected application, it is
        never shown to the user, and it never needs to be mirrored anywhere. Do not create,
        complete, or sync these items in a real task manager (Todoist, Simple Note, or any
        other app), and never match your own todo titles against records in a connected app.
        Tasks that live in a connected app are only in scope when the user's request refers
        to them.

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

        serialized = _serialize_todos_for_store(todos_list)
        note = None
        if filter_bookkeeping:
            serialized, dropped = split_bookkeeping_todos(serialized)
            if dropped:
                names = "; ".join(f"'{d['text']}'" for d in dropped)
                note = (
                    f"Dropped {len(dropped)} bookkeeping item(s) ({names}): tracking, confirming, or "
                    "summarizing your own progress is not a task step. Completion is communicated in "
                    "your final answer — never by a todo, and never by writing files, creating notes, "
                    "or sending messages. Do not re-add these items."
                )

        if todos_store_ref is not None:
            todos_store_ref.clear()
            todos_store_ref.extend(serialized)
        if write_todos is not None:
            write_todos(serialized)

        normalized = [Todo(**t) for t in serialized]
        return TodosOutput(todos=normalized, note=note)

    return StructuredTool.from_function(
        func=create_update_todos_func,
        name="create_update_todos",
        description=(
            "Create or update a list of todos for complex multi-step tasks. Pass `todos` as a "
            "list of objects with 'text' and 'status' ('pending', 'in_progress', or 'completed'). "
            "Returns a todos payload; the full list is shown in the system prompt under "
            "'Current task todos' (Current Plan). This list is your own private scratchpad: it is "
            "not a connected application, is never shown to the user, and never needs to be "
            "mirrored anywhere. Do not create, complete, or sync these items in a real task "
            "manager (Todoist, Simple Note, or any other app), and never match your own todo "
            "titles against records in a connected app. Tasks that live in a connected app are "
            "only in scope when the user's request refers to them. Todo items are steps of the "
            "user's task ONLY: never add bookkeeping items such as 'Confirm completion', "
            "'Summarize actions', or 'Provide summary to user' (such items are removed "
            "automatically), and never write files, create notes, or send messages solely to "
            "record your own progress — completion is communicated in your final answer."
        ),
        args_schema=TodosInput,
        return_direct=False,
    )


def _staleness_phrase(steps_since_update: Optional[int]) -> str:
    if steps_since_update is None or steps_since_update < 0:
        return ""
    if steps_since_update == 0:
        return " You updated it during the last execution."
    step_word = "step" if steps_since_update == 1 else "steps"
    return f" You last updated it {steps_since_update} {step_word} ago."


# Deliberately NOT "the source of truth" (issue #676). Calling the plan authoritative
# produced failures in both directions: a stale `in_progress` made the agent re-run work
# it had already completed, and an all-`completed` plan let it report success for actions
# that never happened (12 of 17 failing tasks in one bundle ended all-`completed`, versus
# 9 of 21 passing ones). The plan records intent; execution output records fact.
_TODOS_PROVENANCE = (
    "This is your own plan for the task, exactly as you last wrote it.{staleness} Statuses record "
    "what you **intended**, not what the environment confirms: an item marked *completed* is not "
    "evidence the action succeeded, and one marked *pending* may already be done. Before relying "
    "on any status, check the execution output and variables from the turns since it was written — "
    "where they disagree with this list, **the execution output wins**. Execution only prints "
    "**Todos updated** after each change, so this block is the only place the list is shown."
)


def format_task_todos_system_block(
    todos: List[Dict[str, str]], steps_since_update: Optional[int] = None
) -> str:
    if not todos:
        return ""
    lines = [
        "",
        "---",
        "",
        "## Current task todos",
        "",
        _TODOS_PROVENANCE.format(staleness=_staleness_phrase(steps_since_update)),
        "",
    ]
    for i, item in enumerate(todos, start=1):
        status = item.get("status", "pending")
        text = item.get("text", "")
        lines.append(f"{i}. **[{status}]** {text}")
    lines.append("")
    return "\n".join(lines)


def format_current_plan_section(
    task_todos: List[Dict[str, Any]], steps_since_update: Optional[int] = None
) -> str:
    lines = [
        "## Current Plan",
        "",
        _TODOS_PROVENANCE.format(staleness=_staleness_phrase(steps_since_update)),
        "",
    ]
    for item in task_todos:
        text = str(item.get("text", "")).strip()
        status = str(item.get("status", "pending")).strip()
        lines.append(f"- **[{status}]** {text}")
    return "\n".join(lines) + "\n"
