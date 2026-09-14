from __future__ import annotations

import asyncio
import json

import typer

purge_app = typer.Typer(help="Operational data purge commands")


@purge_app.command("service-instance")
def purge_service_instance(
    service_instance_id: str = typer.Option(
        ...,
        "--service-instance-id",
        envvar="DYNACONF_SERVICE__INSTANCE_ID",
        help="Service instance ID whose persisted database rows should be purged.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Count matching rows without deleting them.",
    ),
):
    from cuga.backend.storage.service_instance_cleanup import delete_service_instance_records

    try:
        result = asyncio.run(delete_service_instance_records(service_instance_id, dry_run=dry_run))
    except Exception as exc:
        typer.secho(f"Failed to purge service instance data: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                "service_instance_id": result.service_instance_id,
                "dry_run": result.dry_run,
                "deleted_records": result.deleted_records,
                "tables": result.tables,
            },
            sort_keys=True,
        )
    )
