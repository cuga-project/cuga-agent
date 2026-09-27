"""Exercise the image's configured Evolve hooks without network or persistent data."""

from tempfile import TemporaryDirectory


def verify_evolve_hooks() -> None:
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.hooks.manager import MemoryPolicyViolation, dispatch_llm_pre_call, hooks_active
    from altk_evolve.hooks.types import HookType
    from altk_evolve.schema.core import Entity

    with TemporaryDirectory(prefix="cuga-hook-check-") as data_dir:
        client = EvolveClient(
            EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=data_dir))
        )
        try:
            for hook in (
                HookType.MEMORY_PRE_WRITE,
                HookType.MEMORY_POST_READ,
                HookType.MEMORY_PRE_DELETE,
                HookType.LLM_PRE_CALL,
            ):
                assert hooks_active(hook), f"Required bundled hook is inactive: {hook}"
            namespace = "cuga-hook-check"
            client.create_namespace(namespace)
            email = "airgap-check@example.com"
            updates = client.update_entities(
                namespace,
                [Entity(type="guideline", content=f"Contact {email}", metadata={"task_id": "hook-check"})],
                enable_conflict_resolution=False,
            )
            entity_id = updates[0].id
            saved = client.get_entity_by_id(namespace, entity_id)
            assert saved is not None
            assert email not in saved.content and "[REDACTED]" in saved.content
            assert saved.metadata.get("created_at") and saved.metadata.get("trace_id") == "hook-check"
            persisted = client.scan_entities(namespace, filters={"id": entity_id}, limit=1)[0]
            assert persisted.metadata.get("last_accessed"), "Memory retrieval did not record access"

            messages = dispatch_llm_pre_call(
                [{"role": "user", "content": f"Contact {email}"}], "airgap-check"
            )
            assert email not in str(messages) and "[REDACTED]" in str(messages)

            client.patch_entity_metadata(namespace, entity_id, {"legal_hold": True})
            try:
                client.delete_entity_by_id(namespace, entity_id)
            except MemoryPolicyViolation:
                pass
            else:
                raise AssertionError("Deletion of a memory under legal hold was allowed")
            assert client.scan_entities(namespace, filters={"id": entity_id}, limit=1)
        finally:
            client.backend.close()
    print("Evolve PII redaction, metadata, access tracking and legal hold verified offline")


if __name__ == "__main__":
    verify_evolve_hooks()
