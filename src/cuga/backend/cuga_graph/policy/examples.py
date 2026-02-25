"""Example usage of the policy system."""

from loguru import logger

from langchain_core.runnables import RunnableConfig

from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import (
    AppTrigger,
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
    NaturalLanguageTrigger,
    Playbook,
    PlaybookStep,
    PolicyActionType,
    CustomPolicy,
)
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


async def create_example_playbook() -> Playbook:
    """Create an example playbook for e-commerce checkout."""
    return Playbook(
        id="playbook_ecommerce_checkout",
        name="E-commerce Checkout Process",
        description="Step-by-step guide for completing an e-commerce checkout",
        triggers=[
            KeywordTrigger(
                value=["checkout", "purchase", "buy", "order"],
                target="intent",
                case_sensitive=False,
            ),
            AppTrigger(value="shopping"),
        ],
        markdown_content="""# E-commerce Checkout Process

## Steps to Complete Purchase

1. **Add items to cart**
   - Navigate to product page
   - Click "Add to Cart" button
   - Verify item appears in cart

2. **Review cart**
   - Click cart icon
   - Verify quantities and prices
   - Apply coupon code if available

3. **Proceed to checkout**
   - Click "Checkout" button
   - Sign in or continue as guest

4. **Enter shipping information**
   - Fill in shipping address
   - Select shipping method

5. **Enter payment information**
   - Choose payment method
   - Enter payment details
   - Review order total

6. **Complete order**
   - Review order summary
   - Click "Place Order" button
   - Save confirmation number
""",
        steps=[
            PlaybookStep(
                step_number=1,
                instruction="Add items to cart",
                expected_outcome="Item appears in shopping cart",
                tools_allowed=["click", "navigate"],
            ),
            PlaybookStep(
                step_number=2,
                instruction="Review cart contents",
                expected_outcome="Cart shows correct items and prices",
                tools_allowed=["click", "read"],
            ),
            PlaybookStep(
                step_number=3,
                instruction="Proceed to checkout",
                expected_outcome="Checkout page loads",
                tools_allowed=["click", "navigate"],
            ),
            PlaybookStep(
                step_number=4,
                instruction="Enter shipping information",
                expected_outcome="Shipping address is saved",
                tools_allowed=["fill", "click"],
            ),
            PlaybookStep(
                step_number=5,
                instruction="Enter payment information",
                expected_outcome="Payment method is accepted",
                tools_allowed=["fill", "click"],
            ),
            PlaybookStep(
                step_number=6,
                instruction="Complete order",
                expected_outcome="Order confirmation received",
                tools_allowed=["click", "read"],
            ),
        ],
        priority=10,
        enabled=True,
        metadata={"category": "e-commerce", "version": "1.0"},
    )


async def create_example_intent_guard() -> IntentGuard:
    """Create an example intent guard for blocking sensitive operations."""
    return IntentGuard(
        id="guard_delete_account",
        name="Account Deletion Guard",
        description="Prevents accidental account deletion",
        triggers=[
            NaturalLanguageTrigger(
                value=["user wants to delete their account or remove their profile"],
                target="intent",
                threshold=0.75,
            ),
            KeywordTrigger(
                value=["delete account", "remove account", "close account", "cancel account"],
                target="intent",
                case_sensitive=False,
            ),
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="""I understand you want to delete your account. This is a sensitive operation that cannot be undone.

Before proceeding, please note:
- All your data will be permanently deleted
- You will lose access to all services
- This action cannot be reversed

If you're sure you want to proceed, please contact customer support at support@example.com or call 1-800-SUPPORT.

Is there anything else I can help you with instead?""",
        ),
        allow_override=False,
        priority=100,
        enabled=True,
        metadata={"category": "security", "severity": "high"},
    )


async def create_example_custom_policy() -> CustomPolicy:
    """Create an example custom policy for tool restriction."""
    return CustomPolicy(
        id="policy_restrict_admin_tools",
        name="Restrict Admin Tools",
        description="Restricts access to admin tools for non-admin users",
        triggers=[
            KeywordTrigger(
                value=["admin", "administrator", "manage users", "system settings"],
                target="intent",
                case_sensitive=False,
            ),
        ],
        action_type=PolicyActionType.MODIFY_TOOLS,
        action_config={
            "remove_tools": ["delete_user", "modify_permissions", "system_config"],
            "add_message": "Admin tools are restricted. Please contact an administrator.",
        },
        priority=50,
        enabled=True,
        metadata={"category": "security", "requires_role": "admin"},
    )


async def setup_example_policies(storage: PolicyStorage, embedding_function=None):
    """
    Set up example policies in storage.

    Embeddings will be generated automatically by the storage layer.

    Args:
        storage: PolicyStorage instance
        embedding_function: Deprecated - embeddings are now generated automatically
    """
    if embedding_function is not None:
        logger.warning(
            "embedding_function parameter is deprecated - embeddings are now generated automatically"
        )

    # Create example policies
    playbook = await create_example_playbook()
    intent_guard = await create_example_intent_guard()
    custom_policy = await create_example_custom_policy()

    # Add policies to storage (embeddings generated automatically)
    for policy in [playbook, intent_guard, custom_policy]:
        await storage.add_policy(policy)

    print(f"Added {await storage.count_policies()} policies to storage")


async def example_usage_in_node(state, config: RunnableConfig):
    """
    Example of how to use policies in a LangGraph node.

    Args:
        state: AgentState
        config: LangGraph RunnableConfig
    """
    # Get policy system from config
    policy_system = PolicyConfigurable.from_config(config)

    # Create context from state
    context = PolicyContext(
        user_input=state.intent,
        thread_id=config.get("configurable", {}).get("thread_id"),
        chat_messages=[msg.content for msg in state.chat_messages] if state.chat_messages else None,
        current_agent=state.current_agent if hasattr(state, "current_agent") else None,
        available_tools=[tool.name for tool in state.tools]
        if hasattr(state, "tools") and state.tools
        else None,
        active_apps=config.get("configurable", {}).get("apps_list"),
        state_data=state.model_dump() if hasattr(state, "model_dump") else {},
    )

    # Match policy
    match = await policy_system.match_policy(context)

    if match.matched:
        print(f"Policy matched: {match.policy.name}")
        print(f"Action: {match.action.action_type}")
        print(f"Confidence: {match.confidence:.2%}")

        # Handle different action types
        if match.action.action_type == PolicyActionType.GUIDE_PROMPT:
            # Inject playbook content into agent prompt
            state.system_message = f"{state.system_message}\n\n{match.action.content}"

        elif match.action.action_type == PolicyActionType.BLOCK_INTENT:
            # Block the intent and return guard response
            return {"blocked": True, "response": match.action.content}

        elif match.action.action_type == PolicyActionType.MODIFY_TOOLS:
            # Modify available tools
            modifications = match.action.modifications
            if "remove_tools" in modifications:
                state.tools = [tool for tool in state.tools if tool.name not in modifications["remove_tools"]]

    return state


async def example_standalone_usage():
    """Example of standalone usage without LangGraph."""
    # Initialize storage
    storage = PolicyStorage(host="localhost", port="19530")
    await storage.initialize_async()

    # Set up example policies
    await setup_example_policies(storage)

    # Create policy agent
    agent = PolicyAgent(storage=storage)

    # Create context
    context = PolicyContext(
        user_input="I want to checkout and buy these items",
        thread_id="user_123",
        active_apps=["shopping"],
        available_tools=["click", "fill", "navigate"],
    )

    # Match policy
    match = await agent.match_policy(context)

    if match.matched:
        explanation = await agent.explain_match(match)
        print(explanation)

    # Clean up
    await storage.disconnect()


if __name__ == "__main__":
    import asyncio

    asyncio.run(example_standalone_usage())
