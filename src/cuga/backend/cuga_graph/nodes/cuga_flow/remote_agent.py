"""
Binding CUGA FLO's wrapper agents to remote agents over A2A.

Two bindings, one client:

* **Delegation** — a task names a remote agent in ``agent_type:``. ``RemoteTaskExecutor``
  stands in for the ``CugaAgent`` the task would otherwise build, and the remote agent
  becomes the authority for that fulfilment.
* **Consultation** — a gateway or hook names one in ``human_consultation:``. The remote
  agent is bound as a *tool* on the local reasoning agent, which still concludes the
  routing or the hook itself.

Both go over the A2A client that already exists for the supervisor
(``cuga_supervisor/a2a_protocol.py``); nothing here re-implements it.
"""

from typing import Any, Dict, Optional, Set

from loguru import logger

# Discriminator sent in A2A request metadata. Both bindings reach the same remote
# endpoint with free text, so without this the remote agent cannot tell a delegated
# task from a consultation — and the two are answered by different means.
ROLE_FULFILL = "fulfill_task"
ROLE_CONSULT = "elicit_user_preference"


class RemoteAgentRegistry:
    """
    The ``remote_agents:`` block, resolved by name.

    Name lookup is synchronous and strict, so a typo fails at config load. Agent cards
    are fetched lazily on first use and cached for the life of the process — never once
    per invocation.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config: Dict[str, Any] = config or {}
        self._cards: Dict[str, Any] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._config

    def __bool__(self) -> bool:
        return bool(self._config)

    def names(self) -> Set[str]:
        return set(self._config)

    def require(self, name: str, where: str) -> None:
        """Raise unless ``name`` is a declared remote agent. Sync, so it runs at load."""
        if name not in self._config:
            known = ", ".join(sorted(self._config)) or "none declared"
            raise ValueError(
                f"{where} refers to remote agent '{name}', which is not in the "
                f"top-level remote_agents: block (known: {known})"
            )
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import HAS_A2A_SDK

        if not HAS_A2A_SDK:
            raise ImportError(
                f"{where} refers to remote agent '{name}', but a2a-sdk is not installed. "
                "Install with: uv add a2a-sdk"
            )

    def timeout(self, name: str) -> float:
        # The a2a-sdk default is 30s, which sits below the 120s a Kogito script task
        # allows for a whole control point — so an unset timeout would truncate work
        # that would otherwise have finished.
        return float(self._config[name].get("timeout") or 90.0)

    def auth(self, name: str) -> Optional[Dict[str, str]]:
        return self._config[name].get("auth")

    async def card(self, name: str) -> Any:
        """Fetch this agent's card, once, and cache it."""
        if name not in self._cards:
            from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import fetch_agent_card

            cfg = self._config[name]
            self._cards[name] = await fetch_agent_card(
                cfg["url"], auth=cfg.get("auth"), timeout=self.timeout(name)
            )
            logger.info(f"Remote agent '{name}': agent card fetched from {cfg['url']}")
        return self._cards[name]

    async def send(self, name: str, text: str, role: str, owner: str = "") -> str:
        """
        Send one message to a remote agent and return its reply text.

        Every exchange is recorded on the ActivityTracker. That matters most for
        consultation: it is where an external system influences the process's structure,
        and without this the trace would show a conclusion whose basis lived only in the
        remote agent's own logs.
        """
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
            delegate_task_via_a2a_sdk,
        )
        from cuga.backend.activity_tracker.tracker import ActivityTracker, Step

        tracker = ActivityTracker()
        label = f"{owner} → " if owner else ""
        tracker.collect_step(Step(name=f"{label}remote '{name}' ({role})", data=text))

        card = await self.card(name)
        out = await delegate_task_via_a2a_sdk(
            card,
            text,
            auth=self.auth(name),
            timeout=self.timeout(name),
            variables={"role": role},
        )
        answer = out.get("result", "") or ""
        tracker.collect_step(Step(name=f"{label}remote '{name}' replied", data=answer))
        return answer


class RemoteTaskExecutor:
    """
    Stands in for a ``CugaAgent`` on a task delegated via ``agent_type: <remote>``.

    ``TaskAgent`` is duck-typed — it only calls ``invoke()`` — so no base class is
    needed. It reads ``output`` or ``content`` off the result and otherwise falls back
    to ``str(result)``, which would stringify the whole dict; hence the key rename.
    """

    def __init__(self, name: str, registry: RemoteAgentRegistry):
        self.name = name
        self._registry = registry

    async def invoke(self, task_input: Any = None, message: Any = None, **_: Any) -> Dict[str, str]:
        text = str(task_input if task_input is not None else (message or ""))
        logger.info(f"Delegating task to remote agent '{self.name}'")
        answer = await self._registry.send(self.name, text, ROLE_FULFILL, owner="task")
        return {"output": answer}


def make_consultation_tool(name: str, registry: RemoteAgentRegistry, owner: str):
    """
    Build the LangChain tool a DecisionAgent or FlowAgent uses to consult a human.

    Bound on the reasoning agent, so *whether* to ask is part of what that agent works
    out from its policy. ``owner`` is the gateway or hook id, used for logging and the
    trace.
    """
    from langchain_core.tools import tool

    @tool
    async def consult_user(question: str) -> str:
        """Ask a person a question through the remote agent and return their answer.

        Use this when the decision needs information only a human can supply — a
        preference between the available options, or a value the process does not hold.
        Returns what the user said, not a decision: you still decide.
        """
        logger.info(f"{owner}: consulting remote agent '{name}'")
        try:
            answer = await registry.send(name, question, ROLE_CONSULT, owner=owner)
        except Exception as e:
            # Soft degrade, unlike delegation. A consultation is advisory: losing it
            # leaves a decision that is less informed but still sound, whereas a task
            # that cannot execute is a broken app. Returning the failure as tool output
            # lets the agent route on its policy alone rather than failing the process.
            logger.warning(f"{owner}: consultation of '{name}' failed: {e} — deciding without it")
            return (
                f"Consultation unavailable: could not reach remote agent '{name}' ({e}). "
                "Decide using the policy and process state alone."
            )
        logger.info(f"{owner}: remote agent '{name}' replied ({len(answer)} chars)")
        return answer

    return consult_user
