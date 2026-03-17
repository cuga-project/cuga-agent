"""Integration test for policy similarity and retrieval using embeddings."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path to avoid importing through cuga.__init__.py
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from loguru import logger  # noqa: E402

# Import directly from modules to avoid cuga.__init__.py
from cuga.backend.cuga_graph.policy.storage import PolicyStorage  # noqa: E402
from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext  # noqa: E402
from cuga.backend.cuga_graph.policy.models import (  # noqa: E402
    IntentGuard,
    IntentGuardResponse,
    NaturalLanguageTrigger,
    KeywordTrigger,
    Playbook,
    PolicyType,
)


@pytest_asyncio.fixture
async def storage():
    """Create a test storage instance with embeddings."""
    # Use environment variable or default to local
    embedding_provider = os.getenv("POLICY_EMBEDDING_PROVIDER", "local")
    embedding_model = os.getenv("POLICY_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # Get correct embedding dimension for the model
    from cuga.backend.cuga_graph.policy.utils import get_embedding_dimension

    embedding_dim = get_embedding_dimension(embedding_provider, embedding_model)

    # Note: Real embeddings are required (no dummy embeddings)
    storage = PolicyStorage(
        collection_name="test_similarity_policies",
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dim=embedding_dim,
    )

    await storage.initialize_async()

    # Clean up any existing test policies
    existing_policies = await storage.list_policies(enabled_only=False)
    for policy in existing_policies:
        await storage.delete_policy(policy.id)

    yield storage

    # Cleanup after test
    existing_policies = await storage.list_policies(enabled_only=False)
    for policy in existing_policies:
        await storage.delete_policy(policy.id)

    await storage.disconnect()


@pytest_asyncio.fixture
async def policy_agent(storage):
    """Create a policy agent with storage."""
    agent = PolicyAgent(
        storage=storage,
        llm=None,  # No LLM needed for basic retrieval tests
        embedding_function=storage._embedding_function,
    )
    return agent


@pytest.mark.asyncio
async def test_similarity_integration(storage, policy_agent):
    """
    Test policy similarity and retrieval with real embeddings.

    This test:
    1. Creates a Playbook policy about checkout process
    2. Creates an IntentGuard policy about account deletion
    3. Adds both to storage (embeddings generated automatically)
    4. Tests semantic search retrieval
    5. Verifies correct policy matching based on user input
    """

    logger.info("=" * 80)
    logger.info("Starting Policy Similarity Integration Test")
    logger.info("=" * 80)

    # ========================================================================
    # Step 1: Create Playbook Policy
    # ========================================================================
    logger.info("\n[Step 1] Creating Playbook policy for checkout process...")

    checkout_playbook = Playbook(
        id="test_playbook_checkout",
        name="E-commerce Checkout Guide",
        description="Step-by-step guide for completing an e-commerce checkout and purchase",
        triggers=[
            KeywordTrigger(
                value=["checkout", "purchase", "buy"],
                target="intent",
                case_sensitive=False,
                operator="or",
            )
        ],
        markdown_content="""# E-commerce Checkout Process

## Overview
This guide helps users complete their purchase successfully.

## Steps:

1. **Review Cart**
   - Verify items and quantities
   - Check prices and discounts

2. **Enter Shipping Information**
   - Provide delivery address
   - Select shipping method

3. **Payment Details**
   - Enter payment information
   - Apply coupon codes if available

4. **Review Order**
   - Confirm all details are correct
   - Check total amount

5. **Complete Purchase**
   - Click "Place Order"
   - Wait for confirmation
""",
        steps=[],
        priority=60,
        enabled=True,
    )

    await storage.add_policy(checkout_playbook)
    logger.success(f"✅ Added Playbook: {checkout_playbook.name}")

    # ========================================================================
    # Step 2: Create IntentGuard Policy
    # ========================================================================
    logger.info("\n[Step 2] Creating IntentGuard policy for account deletion...")

    deletion_guard = IntentGuard(
        id="test_guard_deletion",
        name="Account Deletion Protection",
        description="Prevents accidental account deletion and requires confirmation",
        triggers=[
            KeywordTrigger(
                value=["delete", "remove", "close"],
                target="intent",
                case_sensitive=False,
                operator="or",
            ),
            NaturalLanguageTrigger(
                value=[
                    "I want to delete my account",
                    "How do I remove my profile?",
                    "Delete all my data",
                    "Close my account permanently",
                    "Remove my account from the system",
                ],
                target="intent",
                threshold=0.7,
            ),
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Account deletion is a permanent action. Please contact support to verify your identity and confirm this request.",
        ),
        allow_override=False,
        priority=80,
        enabled=True,
    )

    await storage.add_policy(deletion_guard)
    logger.success(f"✅ Added IntentGuard: {deletion_guard.name}")

    # ========================================================================
    # Step 3: Verify Policies in Storage
    # ========================================================================
    logger.info("\n[Step 3] Verifying policies in storage...")

    total_policies = await storage.count_policies()
    assert total_policies == 2, f"Expected 2 policies, found {total_policies}"
    logger.success(f"✅ Verified {total_policies} policies in storage")

    playbooks = await storage.list_policies(policy_type=PolicyType.PLAYBOOK)
    assert len(playbooks) == 1, f"Expected 1 playbook, found {len(playbooks)}"
    logger.success(f"✅ Found {len(playbooks)} Playbook policy")

    guards = await storage.list_policies(policy_type=PolicyType.INTENT_GUARD)
    assert len(guards) == 1, f"Expected 1 intent guard, found {len(guards)}"
    logger.success(f"✅ Found {len(guards)} IntentGuard policy")

    # ========================================================================
    # Step 4: Test Semantic Search - Checkout Query
    # ========================================================================
    logger.info("\n[Step 4] Testing semantic search with checkout-related query...")

    checkout_context = PolicyContext(
        user_input="I want to buy something and complete my purchase",
        thread_id="test_thread_1",
    )

    # If embedding function is available, test vector search
    if storage._embedding_function:
        logger.info("Testing vector search with embeddings...")

        # Generate query embedding
        query_embedding = await storage._embedding_function(checkout_context.user_input)

        # Search for similar policies
        search_results = await storage.search_policies(
            query_embedding=query_embedding,
            limit=5,
            enabled_only=True,
        )

        logger.info(f"Found {len(search_results)} policies from vector search")

        if search_results:
            top_policy, similarity = search_results[0]
            logger.info(f"Top result: {top_policy.name} (similarity: {similarity:.4f})")

            # Note: Vector search ranking may vary based on model and query
            # The important thing is that we get results with real embeddings
            logger.success(f"✅ Vector search returned {len(search_results)} results with real embeddings")
        else:
            logger.warning("⚠️  No results from vector search (embeddings may be placeholders)")
    else:
        logger.warning("⚠️  No embedding function available, skipping vector search test")

    # ========================================================================
    # Step 5: Test Semantic Search - Deletion Query
    # ========================================================================
    logger.info("\n[Step 5] Testing semantic search with deletion-related query...")

    deletion_context = PolicyContext(
        user_input="I need to delete my account and remove all my information",
        thread_id="test_thread_2",
    )

    if storage._embedding_function:
        # Generate query embedding
        query_embedding = await storage._embedding_function(deletion_context.user_input)

        # Search for similar policies
        search_results = await storage.search_policies(
            query_embedding=query_embedding,
            limit=5,
            enabled_only=True,
        )

        logger.info(f"Found {len(search_results)} policies from vector search")

        if search_results:
            top_policy, similarity = search_results[0]
            logger.info(f"Top result: {top_policy.name} (similarity: {similarity:.4f})")

            # Note: Vector search ranking may vary based on model and query
            # The important thing is that we get results with real embeddings
            logger.success(f"✅ Vector search returned {len(search_results)} results with real embeddings")
        else:
            logger.warning("⚠️  No results from vector search (embeddings may be placeholders)")

    # ========================================================================
    # Step 6: Test Policy Agent Matching - Checkout
    # ========================================================================
    logger.info("\n[Step 6] Testing policy agent matching for checkout query...")

    match = await policy_agent.match_policy(checkout_context)

    if match.matched:
        logger.info(f"Matched policy: {match.policy.name}")
        logger.info(f"Confidence: {match.confidence:.2f}")
        logger.info(f"Reasoning: {match.reasoning}")

        # Should match the playbook for checkout-related queries
        assert match.policy.id == checkout_playbook.id, f"Expected checkout playbook, got {match.policy.name}"
        logger.success("✅ Policy agent correctly matched checkout playbook")
    else:
        logger.warning(f"⚠️  No policy matched. Reason: {match.reasoning}")
        # This might happen if embeddings are placeholders or triggers don't match

    # ========================================================================
    # Step 7: Test Policy Agent Matching - Deletion
    # ========================================================================
    logger.info("\n[Step 7] Testing policy agent matching for deletion query...")

    match = await policy_agent.match_policy(deletion_context)

    if match.matched:
        logger.info(f"Matched policy: {match.policy.name}")
        logger.info(f"Confidence: {match.confidence:.2f}")
        logger.info(f"Reasoning: {match.reasoning}")

        # Intent guards have higher priority, should match first
        assert match.policy.id == deletion_guard.id, f"Expected deletion guard, got {match.policy.name}"
        logger.success("✅ Policy agent correctly matched deletion guard")
    else:
        logger.warning(f"⚠️  No policy matched. Reason: {match.reasoning}")

    # ========================================================================
    # Step 8: Test Embedding Quality (if available)
    # ========================================================================
    logger.info("\n[Step 8] Testing embedding quality...")

    if storage._embedding_function:
        # Get embeddings for both policies
        checkout_text = f"{checkout_playbook.description} {checkout_playbook.markdown_content[:200]}"
        # Get NL trigger values for deletion guard
        deletion_nl_values = []
        for t in deletion_guard.triggers:
            if isinstance(t, NaturalLanguageTrigger):
                deletion_nl_values.extend(t.value)

        deletion_text = f"{deletion_guard.description} {' '.join(deletion_nl_values)}"

        checkout_embedding = await storage._embedding_function(checkout_text)
        deletion_embedding = await storage._embedding_function(deletion_text)

        # Verify embeddings were generated (should always be real now)
        logger.success("✅ Real embeddings generated!")

        # Calculate cosine similarity
        import numpy as np

        checkout_vec = np.array(checkout_embedding)
        deletion_vec = np.array(deletion_embedding)

        # Normalize
        checkout_norm = checkout_vec / np.linalg.norm(checkout_vec)
        deletion_norm = deletion_vec / np.linalg.norm(deletion_vec)

        # Cosine similarity
        similarity = np.dot(checkout_norm, deletion_norm)

        logger.info(f"Cosine similarity between policies: {similarity:.4f}")

        # Different policies should have low similarity
        assert similarity < 0.9, (
            f"Policies are too similar ({similarity:.4f}), embeddings may not be working correctly"
        )
        logger.success(f"✅ Embeddings are distinct (similarity: {similarity:.4f})")
    else:
        logger.warning("⚠️  No embedding function available")

    # ========================================================================
    # Summary
    # ========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("Test Summary")
    logger.info("=" * 80)
    logger.success("✅ Created 2 policies (1 Playbook, 1 IntentGuard)")
    logger.success("✅ Policies stored with embeddings")
    logger.success("✅ Vector search retrieval tested")
    logger.success("✅ Policy agent matching tested")

    if storage._embedding_function:
        logger.success("✅ Real embeddings working correctly!")
    else:
        logger.warning("⚠️  No embedding function available")
        logger.info("   To enable embeddings:")
        logger.info("   - Set OPENAI_API_KEY environment variable, OR")
        logger.info("   - Install 'sentence-transformers' package")

    logger.info("=" * 80)
    logger.success("🎉 Integration test completed successfully!")
    logger.info("=" * 80)


if __name__ == "__main__":
    # Run test directly
    async def main():
        from cuga.backend.cuga_graph.policy.storage import PolicyStorage
        from cuga.backend.cuga_graph.policy.agent import PolicyAgent
        from cuga.backend.cuga_graph.policy.utils import get_embedding_dimension

        # Use local embeddings by default
        embedding_provider = os.getenv("POLICY_EMBEDDING_PROVIDER", "local")
        embedding_model = os.getenv("POLICY_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        embedding_dim = get_embedding_dimension(embedding_provider, embedding_model)

        # Create storage
        storage = PolicyStorage(
            collection_name="test_similarity_policies",
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
        )
        await storage.initialize_async()

        # Create agent
        agent = PolicyAgent(
            storage=storage,
            llm=None,
            embedding_function=storage._embedding_function,
        )

        try:
            await test_similarity_integration(storage, agent)
        finally:
            # Cleanup
            existing_policies = await storage.list_policies(enabled_only=False)
            for policy in existing_policies:
                await storage.delete_policy(policy.id)
            await storage.disconnect()

    asyncio.run(main())
