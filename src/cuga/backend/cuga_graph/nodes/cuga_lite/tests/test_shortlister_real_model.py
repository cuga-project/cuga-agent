"""Real-embedding accuracy checks — no fake embedder.

Marked ``stability``: excluded from the default run because a cold cache
downloads ~90MB. Run with ``uv run pytest -m stability -k shortlister_real``.

The tool names here are the real shapes CUGA produces, not idealized ones:
CRM's ``/contacts/`` and ``/contacts/{contact_id}`` collide on path segment 1,
so ``determine_operation_name_strategy`` falls back to FastAPI operationIds and
the parser's description degrades to the auto-summary. If cosine works on
these, it works on the real catalogue.
"""

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistRequest
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import (
    EmbeddingShortlister,
    prewarm,
    reset_caches,
)

pytestmark = [pytest.mark.stability, pytest.mark.slow]

MODEL = "BAAI/bge-small-en-v1.5"


class _Args(BaseModel):
    email: str = Field(default="", description="filter by email address")
    limit: int = Field(default=10, description="max rows")


def _tool(name: str, description: str) -> StructuredTool:
    def fn(**kwargs):
        return None

    fn.__name__ = "fn"
    return StructuredTool.from_function(func=fn, name=name, description=description, args_schema=_Args)


CRM_TOOLS = [
    _tool("crm_get_contacts_contacts_get", "Get Contacts"),
    _tool("crm_get_contact_contacts_contact_id_get", "Get Contact"),
    _tool("crm_create_contact_contacts_post", "Create Contact"),
    _tool("crm_update_contact_contacts_contact_id_put", "Update Contact"),
    _tool("crm_delete_contact_contacts_contact_id_delete", "Delete Contact"),
    _tool("crm_get_accounts_accounts_get", "Get Accounts"),
    _tool("crm_get_account_accounts_account_id_get", "Get Account"),
    _tool("crm_create_account_accounts_post", "Create Account"),
    _tool("crm_get_opportunities_opportunities_get", "Get Opportunities"),
    _tool("crm_create_opportunity_opportunities_post", "Create Opportunity"),
    _tool("crm_get_leads_leads_get", "Get Leads"),
    _tool("crm_delete_lead_leads_lead_id_delete", "Delete Lead"),
]
NOISE_TOOLS = [_tool(f"other_app_operation_{i}_thing_get", f"Operation {i}") for i in range(200)]
CATALOGUE = CRM_TOOLS + NOISE_TOOLS

CASES = [
    ("list all my contacts", "crm_get_contacts_contacts_get"),
    ("add a new account", "crm_create_account_accounts_post"),
    ("remove a lead from the system", "crm_delete_lead_leads_lead_id_delete"),
    ("what sales opportunities are open", "crm_get_opportunities_opportunities_get"),
]


@pytest.fixture(scope="module")
def strategy():
    reset_caches()
    if not prewarm("local", MODEL):
        pytest.skip(f"embedding model {MODEL} unavailable (offline?)")
    return EmbeddingShortlister(MODEL, provider="local", query_weight=0.7, min_score=0.15)


async def _rank(strategy, query, top_k=5, task_context=None):
    result = await strategy.shortlist(
        ShortlistRequest(query=query, tools=CATALOGUE, apps=[], top_k=top_k, task_context=task_context)
    )
    return [c.name for c in result.candidates]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected", CASES, ids=[c[0][:20] for c in CASES])
async def test_expected_tool_survives_the_cosine_cut(strategy, query, expected):
    """Recall is the property that matters — the hybrid prefilter and the bind
    cap both only need "don't drop the needed tool"."""
    assert expected in await _rank(strategy, query, top_k=5)


@pytest.mark.asyncio
async def test_recall_at_top_k_over_a_realistic_catalogue(strategy):
    """Recall@k across k, so the default top_k is a measured choice."""
    for k in (8, 32, 128):
        hits = 0
        for query, expected in CASES:
            if expected in await _rank(strategy, query, top_k=k):
                hits += 1
        assert hits == len(CASES), f"recall@{k} was {hits}/{len(CASES)}"


@pytest.mark.asyncio
async def test_crud_siblings_are_barely_separated(strategy):
    """Documents the limitation rather than asserting a score.

    ``get_contacts`` (list) and ``get_contact`` (by id) are one character apart
    and embed almost identically. If this margin is ever comfortably wide, the
    assumption behind preferring `hybrid` for discovery has changed and the
    defaults should be re-measured — so this test failing is a prompt to think,
    not simply to bump a number.
    """
    result = await strategy.shortlist(
        ShortlistRequest(query="list all my contacts", tools=CATALOGUE, apps=[], top_k=8)
    )
    scores = {c.name: c.score for c in result.candidates}
    listing, by_id = "crm_get_contacts_contacts_get", "crm_get_contact_contacts_contact_id_get"
    missing = [n for n in (listing, by_id) if n not in scores]
    assert not missing, f"expected both CRUD siblings in the top 8, missing {missing}"
    margin = abs(scores[listing] - scores[by_id])
    assert margin < 0.15, (
        f"list-vs-get-by-id margin is {margin:.3f}; cosine now separates CRUD siblings "
        f"better than assumed — re-measure before trusting it for final selection"
    )


@pytest.mark.asyncio
async def test_steps_of_one_task_retrieve_different_tools(strategy):
    """The vector-blend fix: a long task context must not swamp the step query.

    With string concatenation both steps would embed nearly identically and
    return the same tools, defeating per-step discovery entirely.
    """
    context = "I need a full CRM cleanup for the quarterly review across all our accounts"

    contacts = await _rank(strategy, "list all contacts", top_k=3, task_context=context)
    deletion = await _rank(strategy, "delete the stale lead", top_k=3, task_context=context)

    assert contacts[0] == "crm_get_contacts_contacts_get"
    assert deletion[0] == "crm_delete_lead_leads_lead_id_delete"
    assert contacts[0] != deletion[0]


@pytest.mark.asyncio
async def test_repeated_queries_reuse_cached_vectors(strategy):
    """Second call must not re-embed the catalogue."""
    import time

    await _rank(strategy, "list all my contacts")
    start = time.time()
    await _rank(strategy, "add a new account")
    warm = time.time() - start

    assert warm < 1.0, f"warm query took {warm:.2f}s — document vectors are not being cached"
