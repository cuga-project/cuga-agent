import typer
from rich.console import Console
from rich.table import Table
from typing import Annotated, TYPE_CHECKING

from kaizen.schema.exceptions import KaizenException, NamespaceNotFoundException

if TYPE_CHECKING:
    from cuga.backend.memory.memory import Memory

memory_app = typer.Typer(help="Tools used with in-process memory")
memory_namespace = typer.Typer(help="Manage namespaces")
memory_app.add_typer(memory_namespace, name="namespace")


def create_memory_client() -> "Memory":
    from cuga.backend.memory.memory import Memory

    memory = Memory()
    if not memory.health_check():
        err_console = Console(stderr=True)
        err_console.print("[bold red]Memory backend is not healthy.[/bold red]")
        raise typer.Exit(1)
    return memory


@memory_namespace.command(help="Create a new namespace")
def create(
    namespace_id: Annotated[
        str | None,
        typer.Argument(help="ID to create the namespace with. Automatically generated if none provided."),
    ] = None,
    user_id: Annotated[str | None, typer.Option(help="The user associated with the namespace.")] = None,
    agent_id: Annotated[str | None, typer.Option(help="The agent associated with the namespace.")] = None,
    app_id: Annotated[str | None, typer.Option(help="The application associated with the namespace.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress output.")] = False,
):
    memory = create_memory_client()
    try:
        namespace = memory.create_namespace(namespace_id, user_id, agent_id, app_id)
    except KaizenException as e:
        err_console = Console(stderr=True)
        err_console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(1)

    if not quiet:
        console = Console()
        console.print(f"Created namespace `{namespace.id}`")


@memory_namespace.command(help="Get namespace details")
def details(namespace_id: Annotated[str, typer.Argument(help="ID of the namespace to retrieve.")]):
    memory = create_memory_client()
    try:
        namespace = memory.get_namespace_details(namespace_id)
    except NamespaceNotFoundException:
        err_console = Console(stderr=True)
        err_console.print(f"[bold red]Namespace `{namespace_id}` not found.[/bold red]")
        raise typer.Exit(1)

    console = Console()
    table = Table("ID", "Created At", "Entities")
    table.add_row(namespace.id, str(namespace.created_at), str(namespace.num_entities or 0))
    console.print(table)


@memory_namespace.command(help="Deletes a namespace")
def delete(
    namespace_id: Annotated[str, typer.Argument(help="ID of the namespace to delete.")],
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Suppress output.")] = False,
):
    memory = create_memory_client()
    memory.delete_namespace(namespace_id)
    if not quiet:
        console = Console()
        console.print(f"Deleted namespace `{namespace_id}`")


@memory_namespace.command(help="Search for namespaces. Lists all namespaces if no filters provided.")
def search(
    user_id: Annotated[str | None, typer.Option(help="Unused in Kaizen mode.")] = None,
    agent_id: Annotated[str | None, typer.Option(help="Unused in Kaizen mode.")] = None,
    app_id: Annotated[str | None, typer.Option(help="Unused in Kaizen mode.")] = None,
    limit: int = 10,
):
    _ = (user_id, agent_id, app_id)
    memory = create_memory_client()
    namespaces = memory.search_namespaces(limit=limit)
    console = Console()
    table = Table("ID", "Created At", "Entities")
    for namespace in namespaces:
        table.add_row(namespace.id, str(namespace.created_at), str(namespace.num_entities or 0))
    console.print(table)
