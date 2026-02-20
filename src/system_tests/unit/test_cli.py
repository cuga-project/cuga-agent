"""CLI tests for CUGA memory commands against the Kaizen-backed API."""

from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from kaizen.schema.core import Namespace
from kaizen.schema.exceptions import KaizenException, NamespaceNotFoundException

runner = CliRunner()


class FakeMemoryClient:
    def __init__(self):
        self.is_ready = True

    def ready(self) -> bool:
        return self.is_ready

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        return Namespace(id=namespace_id or "ns_auto", created_at=datetime.now(timezone.utc), num_entities=0)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        return Namespace(id=namespace_id, created_at=datetime.now(timezone.utc), num_entities=0)

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        _ = limit
        return [Namespace(id="foobar", created_at=datetime.now(timezone.utc), num_entities=1)]

    def delete_namespace(self, namespace_id: str) -> None:
        _ = namespace_id


@pytest.fixture
def cli_app(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "enable_memory", True)
    monkeypatch.setattr(settings.advanced_features, "enable_fact", True)

    from cuga.cli import app

    return app


@pytest.fixture
def memory_client(monkeypatch):
    import cuga.backend.memory.memory as memory_module

    client = FakeMemoryClient()
    monkeypatch.setattr(memory_module, "get_kaizen_client", lambda: client)
    return client


def test_health_check(cli_app, memory_client):
    memory_client.is_ready = False

    result = runner.invoke(cli_app, ["memory", "namespace", "create", "foobar", "--user-id", "baz"])
    assert result.exit_code == 1
    assert "Memory backend is not healthy." in result.output


def test_create_namespace(cli_app, memory_client):
    created_at = datetime.now(timezone.utc)
    memory_client.create_namespace = lambda namespace_id=None: Namespace(  # type: ignore[method-assign]
        id=namespace_id or "foobar",
        created_at=created_at,
        num_entities=0,
    )

    result = runner.invoke(cli_app, ["memory", "namespace", "create", "foobar", "--user-id", "baz"])
    assert result.exit_code == 0
    assert "Created namespace `foobar`" in result.output


def test_create_namespace_already_exists(cli_app, memory_client):
    def _create_namespace(_namespace_id=None):
        raise KaizenException("Namespace `foobar` already exists.")

    memory_client.create_namespace = _create_namespace  # type: ignore[method-assign]

    result = runner.invoke(cli_app, ["memory", "namespace", "create", "foobar", "--user-id", "baz"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_get_namespace_details(cli_app, memory_client):
    created_at = datetime.now(timezone.utc)
    memory_client.get_namespace_details = lambda namespace_id: Namespace(  # type: ignore[method-assign]
        id=namespace_id,
        created_at=created_at,
        num_entities=3,
    )

    result = runner.invoke(cli_app, ["memory", "namespace", "details", "foobar"])
    assert result.exit_code == 0
    assert "ID" in result.output
    assert "Created At" in result.output
    assert "Entities" in result.output
    assert "foobar" in result.output
    assert "3" in result.output
    assert created_at.isoformat()[:10] in result.output


def test_get_namespace_details_not_found(cli_app, memory_client):
    def _get_namespace_details(_namespace_id):
        raise NamespaceNotFoundException()

    memory_client.get_namespace_details = _get_namespace_details  # type: ignore[method-assign]

    result = runner.invoke(cli_app, ["memory", "namespace", "details", "foobar"])
    assert result.exit_code == 1
    assert "Namespace `foobar` not found." in result.output


def test_search_namespaces(cli_app, memory_client):
    created_at = datetime.now(timezone.utc)
    memory_client.search_namespaces = lambda limit=10: [  # type: ignore[method-assign]
        Namespace(id="foobar", created_at=created_at, num_entities=2)
    ]

    result = runner.invoke(cli_app, ["memory", "namespace", "search"])
    assert result.exit_code == 0
    assert "ID" in result.output
    assert "Created At" in result.output
    assert "Entities" in result.output
    assert "foobar" in result.output
    assert "2" in result.output
    assert created_at.isoformat()[:10] in result.output


def test_delete_namespace(cli_app, memory_client):
    _ = memory_client
    result = runner.invoke(cli_app, ["memory", "namespace", "delete", "foobar"])
    assert result.exit_code == 0
    assert "Deleted namespace `foobar`" in result.output
