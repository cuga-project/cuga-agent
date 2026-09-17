"""Protocol-neutral agent interfaces for CUGA.

Both the A2A and ACP adapters consume these types; neither protocol-specific
package is imported here, so this package is safe to import anywhere.
"""

from cuga.backend.server.agent_protocol.events import AgentStreamEvent
from cuga.backend.server.agent_protocol.protocol import AgentRunner

__all__ = ["AgentStreamEvent", "AgentRunner"]
