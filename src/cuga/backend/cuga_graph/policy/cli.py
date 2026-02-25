"""CLI tool for managing CUGA policies."""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext
from cuga.backend.cuga_graph.policy.models import PolicyType
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.utils import (
    backup_policies,
    export_policies_to_json,
    format_policy_summary,
    get_policy_statistics,
    load_policies_from_json,
    restore_policies,
    validate_policy,
)

app = typer.Typer(help="CUGA Policy Management CLI")
console = Console()


async def get_storage(policy_db_path: Optional[str] = None) -> PolicyStorage:
    """Get initialized PolicyStorage instance."""
    backend = None
    if policy_db_path:
        from cuga.backend.storage.policy import LocalPolicyStore

        backend = LocalPolicyStore(policy_db_path, "cuga_policies")
    storage = PolicyStorage(collection_name="cuga_policies", backend=backend)
    await storage.initialize_async()
    return storage


@app.command()
def list_policies(
    policy_type: Optional[str] = typer.Option(None, help="Filter by policy type"),
    enabled_only: bool = typer.Option(True, help="Show only enabled policies"),
    policy_db_path: Optional[str] = typer.Option(
        None, help="Path to policy DB file (default: storage config)"
    ),
):
    """List all policies in storage."""

    async def _list():
        storage = await get_storage(policy_db_path)
        try:
            ptype = PolicyType(policy_type) if policy_type else None
            policies = await storage.list_policies(policy_type=ptype, enabled_only=enabled_only)

            if not policies:
                console.print("[yellow]No policies found[/yellow]")
                return

            table = Table(title="CUGA Policies")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Type", style="blue")
            table.add_column("Priority", justify="right")
            table.add_column("Enabled", justify="center")
            table.add_column("Triggers", justify="right")

            for policy in policies:
                # ToolApproval policies don't have triggers
                trigger_count = len(policy.triggers) if hasattr(policy, 'triggers') and policy.triggers else 0
                table.add_row(
                    policy.id,
                    policy.name,
                    policy.type,
                    str(policy.priority),
                    "✓" if policy.enabled else "✗",
                    str(trigger_count),
                )

            console.print(table)
            console.print(f"\n[bold]Total: {len(policies)} policies[/bold]")

        finally:
            await storage.disconnect()

    asyncio.run(_list())


@app.command()
def show_policy(
    policy_id: str = typer.Argument(..., help="Policy ID to show"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Show detailed information about a policy."""

    async def _show():
        storage = await get_storage(policy_db_path)
        try:
            policy = await storage.get_policy(policy_id)

            if not policy:
                console.print(f"[red]Policy not found: {policy_id}[/red]")
                return

            summary = format_policy_summary(policy)
            console.print(f"\n[bold cyan]{summary}[/bold cyan]")

            # Validate policy
            is_valid, errors = validate_policy(policy)
            if is_valid:
                console.print("\n[green]✓ Policy is valid[/green]")
            else:
                console.print("\n[red]✗ Policy has validation errors:[/red]")
                for error in errors:
                    console.print(f"  - {error}")

        finally:
            await storage.disconnect()

    asyncio.run(_show())


@app.command()
def delete_policy(
    policy_id: str = typer.Argument(..., help="Policy ID to delete"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Delete a policy from storage."""

    async def _delete():
        storage = await get_storage(policy_db_path)
        try:
            policy = await storage.get_policy(policy_id)

            if not policy:
                console.print(f"[red]Policy not found: {policy_id}[/red]")
                return

            if not confirm:
                console.print("\n[yellow]About to delete policy:[/yellow]")
                console.print(f"  ID: {policy.id}")
                console.print(f"  Name: {policy.name}")
                console.print(f"  Type: {policy.type}")

                if not typer.confirm("\nAre you sure you want to delete this policy?"):
                    console.print("[yellow]Deletion cancelled[/yellow]")
                    return

            await storage.delete_policy(policy_id)
            console.print(f"[green]✓ Deleted policy: {policy_id}[/green]")

        finally:
            await storage.disconnect()

    asyncio.run(_delete())


@app.command()
def load_from_file(
    file_path: str = typer.Argument(..., help="Path to JSON file with policies"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Load policies from a JSON file."""

    async def _load():
        storage = await get_storage(policy_db_path)
        try:
            count = await load_policies_from_json(file_path, storage)
            console.print(f"[green]✓ Loaded {count} policies from {file_path}[/green]")
        finally:
            await storage.disconnect()

    asyncio.run(_load())


@app.command()
def export_to_file(
    output_path: str = typer.Argument(..., help="Path to output JSON file"),
    policy_type: Optional[str] = typer.Option(None, help="Filter by policy type"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Export policies to a JSON file."""

    async def _export():
        storage = await get_storage(policy_db_path)
        try:
            ptype = PolicyType(policy_type) if policy_type else None
            success = await export_policies_to_json(storage, output_path, ptype)

            if success:
                console.print(f"[green]✓ Exported policies to {output_path}[/green]")
            else:
                console.print("[red]✗ Failed to export policies[/red]")

        finally:
            await storage.disconnect()

    asyncio.run(_export())


@app.command()
def backup(
    backup_dir: str = typer.Argument(..., help="Directory to store backups"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Backup all policies to a directory."""

    async def _backup():
        storage = await get_storage(policy_db_path)
        try:
            success = await backup_policies(storage, backup_dir)

            if success:
                console.print(f"[green]✓ Backed up all policies to {backup_dir}[/green]")
            else:
                console.print("[red]✗ Failed to backup policies[/red]")

        finally:
            await storage.disconnect()

    asyncio.run(_backup())


@app.command()
def restore(
    backup_dir: str = typer.Argument(..., help="Directory containing backups"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Restore policies from a backup directory."""

    async def _restore():
        storage = await get_storage(policy_db_path)
        try:
            count = await restore_policies(storage, backup_dir)
            console.print(f"[green]✓ Restored {count} policies from {backup_dir}[/green]")
        finally:
            await storage.disconnect()

    asyncio.run(_restore())


@app.command()
def stats(
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Show policy statistics."""

    async def _stats():
        storage = await get_storage(policy_db_path)
        try:
            statistics = await get_policy_statistics(storage)

            console.print("\n[bold cyan]Policy Statistics[/bold cyan]\n")
            console.print(f"Total Policies: {statistics.get('total_policies', 0)}")
            console.print(f"Enabled: {statistics.get('enabled', 0)}")
            console.print(f"Disabled: {statistics.get('disabled', 0)}")
            console.print(f"Average Priority: {statistics.get('average_priority', 0):.2f}")

            console.print("\n[bold]By Type:[/bold]")
            for ptype, count in statistics.get("by_type", {}).items():
                console.print(f"  {ptype}: {count}")

        finally:
            await storage.disconnect()

    asyncio.run(_stats())


@app.command()
def test_match(
    user_input: str = typer.Argument(..., help="User input to test"),
    thread_id: Optional[str] = typer.Option(None, help="Thread ID"),
    apps: Optional[str] = typer.Option(None, help="Comma-separated list of active apps"),
    tools: Optional[str] = typer.Option(None, help="Comma-separated list of available tools"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Test policy matching with given context."""

    async def _test():
        storage = await get_storage(policy_db_path)
        try:
            agent = PolicyAgent(storage=storage)

            context = PolicyContext(
                user_input=user_input,
                thread_id=thread_id,
                active_apps=apps.split(",") if apps else None,
                available_tools=tools.split(",") if tools else None,
            )

            console.print("\n[bold cyan]Testing Policy Match[/bold cyan]\n")
            console.print(f"User Input: {user_input}")
            if thread_id:
                console.print(f"Thread ID: {thread_id}")
            if apps:
                console.print(f"Active Apps: {apps}")
            if tools:
                console.print(f"Available Tools: {tools}")

            match = await agent.match_policy(context)

            console.print("\n[bold]Result:[/bold]")
            if match.matched:
                console.print("[green]✓ Policy Matched[/green]")
                console.print(f"\nPolicy: {match.policy.name} ({match.policy.id})")
                console.print(f"Type: {match.policy.type}")
                console.print(f"Action: {match.action.action_type}")
                console.print(f"Confidence: {match.confidence:.2%}")
                console.print(f"\nReasoning: {match.reasoning}")

                explanation = await agent.explain_match(match)
                console.print(f"\n[bold]Detailed Explanation:[/bold]\n{explanation}")
            else:
                console.print("[yellow]✗ No Policy Matched[/yellow]")
                console.print(f"Reasoning: {match.reasoning}")

        finally:
            await storage.disconnect()

    asyncio.run(_test())


@app.command()
def validate(
    policy_id: str = typer.Argument(..., help="Policy ID to validate"),
    policy_db_path: Optional[str] = typer.Option(None, help="Path to policy DB file"),
):
    """Validate a policy."""

    async def _validate():
        storage = await get_storage(policy_db_path)
        try:
            policy = await storage.get_policy(policy_id)

            if not policy:
                console.print(f"[red]Policy not found: {policy_id}[/red]")
                return

            is_valid, errors = validate_policy(policy)

            console.print(f"\n[bold]Validating Policy: {policy.name}[/bold]\n")

            if is_valid:
                console.print("[green]✓ Policy is valid[/green]")
            else:
                console.print("[red]✗ Policy has validation errors:[/red]")
                for error in errors:
                    console.print(f"  - {error}")

        finally:
            await storage.disconnect()

    asyncio.run(_validate())


if __name__ == "__main__":
    app()
