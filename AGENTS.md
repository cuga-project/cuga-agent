# Agent guidelines

## Mark tests with their type

Every new or changed test must be marked with a pytest marker that declares its type. Use the markers registered in `pyproject.toml` (`[tool.pytest.ini_options].markers`), for example:

- `@pytest.mark.unit` — fast, isolated tests
- `@pytest.mark.e2e` — live services / full agent stack
- `@pytest.mark.stability` — LLM-backed e2e stability suite
- `@pytest.mark.pgvector` — requires pgvector
- `@pytest.mark.load` — concurrent load tests
- `@pytest.mark.slow` — long-running integration tests
- `@pytest.mark.manual` — needs manually started services
- `@pytest.mark.windows_smoke` — Windows CI smoke subset

Do not leave tests unmarked when a type marker applies.

## Lazy imports — rules for new code and tests

**Any additions to these files must preserve the pattern or the startup gains are lost.**

### `cuga/__init__.py` — PEP 562 lazy exports
`import cuga` is near-instant (~4 ms). All public exports are deferred via `__getattr__`.

- **Do not add top-level imports** to `cuga/__init__.py`.
- New public exports must be added to the `_import_map` dict (lazy), not as bare `from cuga.xxx import Yyy` statements.
- The `if TYPE_CHECKING:` block is fine — it is never executed at runtime.

### `cuga/backend/llm/models.py` — scope-local provider imports
All LLM provider libraries (`langchain_openai`, `langchain_ibm`, `langchain_groq`, etc.) and the `ReasoningChatOpenAI` class are imported **inside** the `if platform == "..."` branch that uses them, not at module level.

- **New provider branches must follow the same pattern** — keep `from langchain_xxx import ...` inside the branch, never at the top of the file.
- `ReasoningChatOpenAI` is built inside the `_get_reasoning_chat_openai()` factory function and never exists at module level.

### `cuga/sdk.py` and `cuga/backend/server/main.py` — deferred function-body imports
Heavy imports (graph, policy, provider, observability) were moved from module-top into the function bodies that use them.

- New heavy dependencies added to these files should follow the same pattern: import inside the function, not at the top of the file.

### Patching lazy-loaded classes in tests
Because nothing is at module level, `patch("cuga.backend.llm.models.SomeClass")` will raise `AttributeError`. Use these targets instead:

| Class | Correct patch target |
|---|---|
| `ReasoningChatOpenAI` | `cuga.backend.llm.models._get_reasoning_chat_openai` — patch the factory; `mock_factory.return_value` is the class |
| `AzureChatOpenAI` | `langchain_openai.AzureChatOpenAI` |
| `ChatWatsonx` | `langchain_ibm.ChatWatsonx` |

Example:
```python
with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
    mock_cls = mock_factory.return_value   # the class returned by the factory
    mock_cls.return_value = object()       # the instance returned by calling the class
    mgr.get_model(settings)

assert mock_cls.call_args.kwargs["timeout"] == 120.0
```

## Creating issues and pull requests

When creating a new GitHub issue or pull request, use the AI agent commands documented in [CONTRIBUTING.md](CONTRIBUTING.md#ai-agent-commands) instead of inventing an ad-hoc flow. Prefer:

- `/cuga-report-bug` — open a bug issue from the `bug_report.yml` template
- `/cuga-new-feature` — open a feature request from the `feature_request.yml` template
- `/cuga-create-pr` — validate local state, pick the right PR template, and open the PR via `gh`

These commands live under `.cursor/commands/`, `.claude/commands/`, and `.bob/commands/` and follow repo conventions (templates, Conventional Commits, DCO signoff expectations, no promotional footers).
