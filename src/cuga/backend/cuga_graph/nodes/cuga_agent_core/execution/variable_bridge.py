"""VariableBridge — cross-agent variable handoff utility.

Copies variables computed by a sub-agent (carried in ``InvokeResult.variables``)
into the Supervisor's ``supervisor_variables_manager``.

The bridge is driven by a *shared mutable reference* (``_shared_vm_ref: List``)
that ``execute_agent_tool`` refreshes to the current state's VM before each code
execution. ``delegate_to_agent`` reads from that ref after the sub-agent returns,
so variables land in the correct state snapshot even though the delegation closure
is built at graph-prepare time.
"""

from __future__ import annotations

from typing import Any, Dict, List


class VariableBridge:
    """Utility for copying variables between agents."""

    @staticmethod
    def extract_values(variables_storage: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Convert ``variables_storage`` serialised format → ``{name: raw_value}``.

        Entries that lack a ``'value'`` key are silently skipped so callers
        never need to guard against malformed storage.
        """
        return {name: meta["value"] for name, meta in variables_storage.items() if "value" in meta}

    @staticmethod
    def bridge(
        source_values: Dict[str, Any],
        target_manager: Any,
        description_prefix: str = "bridged",
    ) -> List[str]:
        """Copy ``{name: value}`` pairs into *target_manager*.

        Args:
            source_values: Plain ``{name: value}`` dict (e.g. from
                ``extract_values`` or ``InvokeResult.variables``).
            target_manager: Any ``VariablesManager``-compatible object that
                implements ``add_variable(value, name, description)``.
            description_prefix: Prepended to each variable's description so
                the supervisor knows where the value originated.

        Returns:
            List of variable names that were written.
        """
        bridged: List[str] = []
        for name, value in source_values.items():
            if value is None:
                continue
            target_manager.add_variable(
                value,
                name=name,
                description=f"{description_prefix}: {name}",
            )
            bridged.append(name)
        return bridged
