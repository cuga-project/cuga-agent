"""Focused tests for the dependency-light modules extracted from the graph pipeline."""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel


_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "cuga" / "backend"


def _load_module(name: str, path: Path):
    """Load a leaf module without importing optional graph/model dependencies."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_verifier_mode_prompts_have_distinct_decision_rules():
    prompts = _load_module(
        "_test_verifier_system_prompts",
        _SOURCE_ROOT / "cuga_graph" / "nodes" / "cuga_agent_core" / "verification" / "system_prompts.py",
    )

    standard = prompts.system_prompt_for_mode(rejection_only=False)
    rejection_only = prompts.system_prompt_for_mode(rejection_only=True)

    assert standard == prompts.STANDARD_SYSTEM_PROMPT
    assert rejection_only == prompts.REJECTION_ONLY_SYSTEM_PROMPT
    assert standard != rejection_only
    assert "Decision task" in standard and "Decision task" in rejection_only
    assert "Missing\n   support" in rejection_only
    assert "Default to approved" in rejection_only


def test_rejection_only_mode_is_explicitly_disabled_by_default():
    settings_path = _SOURCE_ROOT.parent / "settings.toml"
    settings_text = settings_path.read_text(encoding="utf-8")
    assert re.search(
        r"^prompt_verification_rejection_only\s*=\s*false\b",
        settings_text,
        re.MULTILINE,
    )


@pytest.mark.parametrize(
    "source",
    [
        "",
        "short source",
        "# Heading\n\n" + "A sentence. " * 700,
        "```python\n" + "print('x')\n" * 300 + "```\n",
    ],
)
def test_source_chunking_is_lossless_and_ordered(source: str):
    chunking = _load_module(
        "_test_memory_graph_source_chunking",
        _SOURCE_ROOT / "memory_graph" / "source_chunking.py",
    )

    chunks = chunking.split_source_for_decomposition(source, max_chars=300, target_chars=250)
    assert "".join(chunk.text for chunk in chunks) == source
    assert all(chunk.text == source[chunk.start : chunk.end] for chunk in chunks)
    assert all(chunk.end - chunk.start <= 300 for chunk in chunks)
    assert all(left.end == right.start for left, right in zip(chunks, chunks[1:]))
    assert (chunks[-1].end if chunks else 0) == len(source)


def test_candidate_call_inspection_records_without_invoking_tool():
    calls = _load_module(
        "_test_verifier_candidate_calls",
        _SOURCE_ROOT / "cuga_graph" / "nodes" / "cuga_agent_core" / "verification" / "candidate_calls.py",
    )

    candidate = "```python\nresult = await search(query='customer record')\n```"
    extracted = asyncio.run(calls._extract_candidate_calls_dry_run(candidate, tool_names={"search"}))
    assert len(extracted) == 1
    assert extracted[0]["call"] == "search"
    assert extracted[0]["keyword_args"]["query"].static_value == "customer record"
    assert extracted[0]["assigned_to"] == "result"


def test_optional_decomposition_backend_reports_missing_dependency(monkeypatch):
    backends = _load_module(
        "_test_memory_graph_decomposition_backends",
        _SOURCE_ROOT / "memory_graph" / "decomposition_backends.py",
    )
    real_import = builtins.__import__

    def deny_stanza(name, *args, **kwargs):
        if name == "stanza":
            raise ImportError("missing in test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_stanza)
    with pytest.raises(RuntimeError, match="Stanza is required"):
        backends.stanza_module()


def test_verifier_model_client_owns_structured_invocation(monkeypatch):
    verification_dir = _SOURCE_ROOT / "cuga_graph" / "nodes" / "cuga_agent_core" / "verification"

    class Decision(BaseModel):
        verdict: str

    class StructuredModel:
        async def ainvoke(self, messages):
            assert len(messages) == 1
            return {"parsed": {"verdict": "approved"}, "raw": None, "parsing_error": None}

    class FakeModel:
        model_name = "test/structured-model"

        def with_structured_output(self, schema, *, method, include_raw):
            assert schema is Decision
            assert method == "function_calling"
            assert include_raw is True
            return StructuredModel()

    with monkeypatch.context() as patch:
        package = types.ModuleType("_test_verifier_package")
        package.__path__ = [str(verification_dir)]
        patch.setitem(sys.modules, package.__name__, package)
        models = types.ModuleType("cuga.backend.llm.models")
        models.LLMManager = object
        config = types.ModuleType("cuga.config")
        config.settings = object()
        patch.setitem(sys.modules, "cuga.backend.llm.models", models)
        patch.setitem(sys.modules, "cuga.config", config)
        _load_module("_test_verifier_package.errors", verification_dir / "errors.py")
        client = _load_module("_test_verifier_package.model_client", verification_dir / "model_client.py")
        patch.setattr(client, "_get_model", lambda **kwargs: FakeModel())

        decision = asyncio.run(
            client.invoke_verifier_decision(
                messages=[object()],
                schema=Decision,
                verification_id="test",
                candidate_kind="terminal",
                candidate_hash="abc",
            )
        )

        class ClaudeModel:
            model_name = "aws/claude-test"

        async def plain_json(**kwargs):
            assert kwargs["schema"] is Decision
            return object(), {"verdict": "rejected"}

        patch.setattr(client, "_get_model", lambda **kwargs: ClaudeModel())
        patch.setattr(client, "_plain_json_invoke", plain_json)
        claude_decision = asyncio.run(
            client.invoke_verifier_decision(
                messages=[object()],
                schema=Decision,
                verification_id="test-claude",
                candidate_kind="terminal",
                candidate_hash="def",
            )
        )

    assert decision.verdict == "approved"
    assert claude_decision.verdict == "rejected"
