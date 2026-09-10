"""Tool→text conversion for the cosine ranker.

`split_identifier` is the single highest-leverage detail in the cosine path:
real tool names are machine-generated (`crm_get_contacts_contacts_get`) and
embed poorly until split back into words.
"""

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.doc import (
    app_name_for_tool,
    split_identifier,
    tool_document,
    tool_fingerprint,
)

pytestmark = pytest.mark.unit


# --- split_identifier -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The real shape CRM produces: path segments collide, so names fall
        # back to FastAPI operationIds.
        ("crm_get_contacts_contacts_get", "crm get contacts contacts get"),
        ("crm_get_contacts_contacts__get", "crm get contacts contacts get"),
        ("getAccountContacts", "get account contacts"),
        ("crm-get.contacts", "crm get contacts"),
        ("HTTPResponseCode", "http response code"),
        ("tool_v2_lookup", "tool v2 lookup"),
        ("", ""),
        ("___", ""),
    ],
)
def test_split_identifier(raw, expected):
    assert split_identifier(raw) == expected


# --- tool_document ----------------------------------------------------------


class _ContactArgs(BaseModel):
    email: str = Field(..., description="filter by email address")
    limit: int = Field(default=10, description="")


def _tool(name="crm_get_contacts_contacts_get", description="Get Contacts", args=_ContactArgs):
    def fn(**kwargs):
        return None

    fn.__name__ = "fn"
    return StructuredTool.from_function(func=fn, name=name, description=description, args_schema=args)


def test_tool_document_includes_the_signal_bearing_fields():
    document = tool_document(_tool(), app_name="crm", response_doc="- items (array): rows\n- total (int)")

    assert "crm get contacts contacts get" in document
    assert "Get Contacts" in document
    assert "email: filter by email address" in document
    assert "Returns: items, total" in document


def test_tool_document_excludes_raw_schema_noise():
    """args_schema JSON is what makes the LLM prompt expensive; in a fixed-size
    vector it dilutes signal rather than adding to it."""
    document = tool_document(_tool(), app_name="crm")
    assert "properties" not in document
    assert "$defs" not in document
    assert "anyOf" not in document


def test_tool_document_handles_missing_description_and_params():
    def fn():
        return None

    fn.__name__ = "bare"
    tool = StructuredTool.from_function(func=fn, name="bare_tool", description="")
    assert "bare tool" in tool_document(tool, app_name="app")


def test_tool_document_is_deterministic():
    a, b = tool_document(_tool(), "crm"), tool_document(_tool(), "crm")
    assert a == b


# --- fingerprints -----------------------------------------------------------


def test_fingerprint_changes_with_content_and_model():
    base = tool_fingerprint("doc", "model")
    assert tool_fingerprint("doc", "model") == base
    assert tool_fingerprint("doc changed", "model") != base
    assert tool_fingerprint("doc", "other-model") != base


def test_app_name_from_registry_metadata():
    tool = _tool()
    tool.func._app_name = "crm"
    assert app_name_for_tool(tool) == "crm"
    assert app_name_for_tool(_tool(), fallback="fallback_app") == "fallback_app"
