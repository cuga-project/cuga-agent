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
Cold-start work (#486) made `import cuga` ~4 ms and cut SDK `import_s` ~66%. Re-eagerizing imports undoes that.

### `cuga/__init__.py` — PEP 562 lazy exports
`import cuga` is near-instant (~4 ms). All public exports are deferred via `__getattr__`.

- **Do not add top-level imports** to `cuga/__init__.py`.
- New public exports must be added to the `_import_map` dict (lazy), not as bare `from cuga.xxx import Yyy` statements.
- The `if TYPE_CHECKING:` block is fine — it is never executed at runtime.

### `cuga/backend/llm/models.py` — scope-local provider imports
All LLM provider libraries (`langchain_openai`, `langchain_ibm`, `langchain_groq`, etc.) and the `ReasoningChatOpenAI` class are imported **inside** the `if platform == "..."` branch that uses them, not at module level.

- **New provider branches must follow the same pattern** — keep `from langchain_xxx import ...` inside the branch, never at the top of the file.
- `ReasoningChatOpenAI` is built inside the `_get_reasoning_chat_openai()` factory function and never exists at module level.
- Never re-export provider classes (`ChatWatsonx`, `AzureChatOpenAI`, …) as module attributes of `models.py`.

### `cuga/sdk.py` and `cuga/backend/server/main.py` — deferred function-body imports
Heavy imports (graph, policy, provider, observability) were moved from module-top into the function bodies that use them.

- New heavy dependencies added to these files should follow the same pattern: import inside the function, not at the top of the file.
- `openlit` must stay behind the disabled-by-default observability gate — do not import it at module top.

### Patching lazy-loaded classes in tests
Because provider classes are not attributes of `cuga.backend.llm.models`, patching them there raises `AttributeError` (or never hits the real import). Patch the **source module** (or the factory) instead:

| Class | Correct patch target | Wrong (breaks) |
|---|---|---|
| `ReasoningChatOpenAI` | `cuga.backend.llm.models._get_reasoning_chat_openai` — patch the factory; `mock_factory.return_value` is the class | `cuga.backend.llm.models.ReasoningChatOpenAI` |
| `AzureChatOpenAI` | `langchain_openai.AzureChatOpenAI` | `cuga.backend.llm.models.AzureChatOpenAI` |
| `ChatWatsonx` | `langchain_ibm.ChatWatsonx` — prefer a real fake class (MagicMock breaks `isinstance`) | `cuga.backend.llm.models.ChatWatsonx` |

Example (factory):
```python
with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
    mock_cls = mock_factory.return_value   # the class returned by the factory
    mock_cls.return_value = object()       # the instance returned by calling the class
    mgr.get_model(settings)

assert mock_cls.call_args.kwargs["timeout"] == 120.0
```

Example (source-module provider, same pattern as Azure — use a real class so
`isinstance(model, ChatWatsonx)` in `_update_model_parameters` still works):
```python
class _FakeChatWatsonx:
    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.params = dict(kwargs.get("params") or {})

with patch("langchain_ibm.ChatWatsonx", _FakeChatWatsonx):
    mgr.get_model(settings_for_watsonx)

assert _FakeChatWatsonx.last_kwargs["model_id"] == "configured-model-name"
```

If a test fails with `AttributeError: <module 'cuga.backend.llm.models' ...> does not have the attribute '…'`, update the patch target — do not re-add a module-level import to “make the patch work”.

## Creating issues and pull requests

When creating a new GitHub issue or pull request, use the AI agent skills documented in [CONTRIBUTING.md](CONTRIBUTING.md#ai-agent-skills) instead of inventing an ad-hoc flow. Prefer:

- `cuga-github-issues` — open bugs, features, epics, and related issues (`bug_report.yml` / `feature_request.yml`), with epic → feature → issue hierarchy
- `cuga-contributor-workflows` — commit (Conventional Commits), create PRs via `gh`, and run ruff check/format

These skills are mirrored under `.cursor/skills/`, `.claude/skills/`, and `.bob/skills/` and follow repo conventions (templates, Conventional Commits, DCO signoff expectations, no promotional footers).
