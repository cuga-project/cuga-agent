"""Protocol-neutral agent interfaces for CUGA.

Both the A2A and ACP adapters consume these types; neither protocol-specific
package is imported here, so this package is safe to import anywhere.
"""

from cuga.backend.server.agent_protocol.events import AgentStreamEvent
from cuga.backend.server.agent_protocol.protocol import AgentRunner
from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner
from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

__all__ = ["AgentStreamEvent", "AgentRunner", "SimpleAgentRunner", "SupervisorAgentRunner"]
