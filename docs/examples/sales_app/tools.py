"""LangChain tools the embedded sales agent can call."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from store import STAGES, SalesStore


def build_tools(store: SalesStore) -> list:
    @tool
    def list_accounts(industry: str = "") -> str:
        """List sales accounts. Pass industry to filter, or leave it empty for every account."""
        rows = store.list_accounts(industry=industry or None)
        return json.dumps(rows)

    @tool
    def get_account(name: str) -> str:
        """Get one account by name, including its contacts and opportunities."""
        account = store.get_account(name)
        if account is None:
            return json.dumps({"error": f"No account named {name}"})
        return json.dumps(account)

    @tool
    def list_leads(status: str = "") -> str:
        """List leads, highest score first. status may be new, working, or qualified."""
        return json.dumps(store.list_leads(status=status or None))

    @tool
    def list_contacts() -> str:
        """List people at current accounts."""
        return json.dumps(store.list_contacts())

    @tool
    def list_opportunities(stage: str = "") -> str:
        """List opportunities and their account, value, stage, and close date. Filter with a stage name or leave it empty."""
        return json.dumps(store.list_opportunities(stage=stage or None))

    @tool
    def pipeline_summary() -> str:
        """Return open pipeline value, open deal count, won value, and win rate."""
        return json.dumps(store.summary())

    @tool
    def update_opportunity_stage(opportunity_id: int, stage: str) -> str:
        """Move an opportunity to a new stage. Use list_opportunities first to find the id. stage must be prospecting, qualification, proposal, negotiation, closed-won, or closed-lost."""
        try:
            updated = store.update_opportunity_stage(opportunity_id, stage)
        except ValueError as exc:
            return json.dumps({"error": str(exc), "allowed_stages": list(STAGES)})
        return json.dumps(updated)

    return [
        list_accounts,
        get_account,
        list_leads,
        list_contacts,
        list_opportunities,
        pipeline_summary,
        update_opportunity_stage,
    ]
