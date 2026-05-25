"""
BPMN Parser - Converts BPMN 2.0 XML to LangGraph StateGraph.

This module parses BPMN XML files and constructs a LangGraph StateGraph
that can be executed by the FlowAgent.

Supported BPMN Elements:
- Start Event
- End Event
- Task (Service Task, User Task, etc.)
- Exclusive Gateway
- Parallel Gateway
- Sequence Flow
- Sub-Process (as nested FlowAgent)
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from loguru import logger

from langgraph.graph import StateGraph, START, END
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState


# BPMN 2.0 namespace
BPMN_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}


@dataclass
class BPMNElement:
    """Represents a BPMN element."""

    id: str
    name: str
    element_type: str  # task, gateway, event, etc.
    attributes: Dict[str, Any]
    # Task annotations (mutually exclusive)
    tool_binding: Optional[str] = None  # Tool name for direct tool execution
    agent_binding: Optional[str] = None  # Agent name for task agent execution
    # Gateway annotations (mutually exclusive)
    condition_tool: Optional[str] = None  # Tool name for gateway condition evaluation
    decision_agent: Optional[str] = None  # Agent name for decision agent routing


@dataclass
class BPMNFlow:
    """Represents a BPMN sequence flow."""

    id: str
    name: str
    source_ref: str
    target_ref: str
    condition: Optional[str] = None

    def _extract_agent_binding(self, element: ET.Element) -> Optional[str]:
        """
        Extract agent binding from BPMN extension elements.

        Looks for: <extensionElements><agentBinding>agent_name</agentBinding></extensionElements>

        Args:
            element: BPMN task element

        Returns:
            Agent name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            agent_binding = ext_elements.find(".//bpmn:agentBinding", BPMN_NS)
            if agent_binding is not None and agent_binding.text:
                return agent_binding.text.strip()

        # Try without namespace
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            agent_binding = ext_elements.find(".//agentBinding")
            if agent_binding is not None and agent_binding.text:
                return agent_binding.text.strip()

        return None

    def _extract_decision_agent(self, element: ET.Element) -> Optional[str]:
        """
        Extract decision agent from gateway extension elements.

        Looks for: <extensionElements><decisionAgent>agent_name</decisionAgent></extensionElements>

        Args:
            element: BPMN gateway element

        Returns:
            Agent name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            dec_agent = ext_elements.find(".//bpmn:decisionAgent", BPMN_NS)
            if dec_agent is not None and dec_agent.text:
                return dec_agent.text.strip()

        # Try without namespace
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            dec_agent = ext_elements.find(".//decisionAgent")
            if dec_agent is not None and dec_agent.text:
                return dec_agent.text.strip()

        return None


@dataclass
class BPMNProcess:
    """Represents a parsed BPMN process."""

    id: str
    name: str
    elements: Dict[str, BPMNElement]
    flows: List[BPMNFlow]
    start_event: Optional[str] = None
    end_events: List[str] = None


class BPMNParser:
    """
    Parser for BPMN 2.0 XML files.

    Converts BPMN process definitions into LangGraph StateGraph structures
    that can be executed by FlowAgent.
    """

    def __init__(self):
        self.processes: Dict[str, BPMNProcess] = {}

    def parse_file(self, bpmn_file: str) -> BPMNProcess:
        """
        Parse a BPMN XML file and extract process definition.

        Args:
            bpmn_file: Path to BPMN XML file

        Returns:
            BPMNProcess object containing parsed elements and flows
        """
        logger.info(f"Parsing BPMN file: {bpmn_file}")

        tree = ET.parse(bpmn_file)
        root = tree.getroot()

        # Find all process elements
        processes = root.findall(".//bpmn:process", BPMN_NS)

        if not processes:
            raise ValueError(f"No process found in BPMN file: {bpmn_file}")

        # Parse the first process (support for multiple processes can be added later)
        process_elem = processes[0]
        process = self._parse_process(process_elem)

        self.processes[process.id] = process
        logger.info(f"Parsed process: {process.name} (ID: {process.id})")
        logger.info(f"  Elements: {len(process.elements)}")
        logger.info(f"  Flows: {len(process.flows)}")

        return process

    def _parse_process(self, process_elem: ET.Element) -> BPMNProcess:
        """Parse a BPMN process element."""
        process_id = process_elem.get("id", "process_1")
        process_name = process_elem.get("name", process_id)

        elements: Dict[str, BPMNElement] = {}
        flows: List[BPMNFlow] = []
        start_event = None
        end_events = []

        # Parse start events
        for start in process_elem.findall(".//bpmn:startEvent", BPMN_NS):
            elem_id = start.get("id")
            elem_name = start.get("name", elem_id)
            elements[elem_id] = BPMNElement(
                id=elem_id, name=elem_name, element_type="startEvent", attributes={}
            )
            start_event = elem_id
            logger.debug(f"  Found start event: {elem_name} ({elem_id})")

        # Parse end events
        for end in process_elem.findall(".//bpmn:endEvent", BPMN_NS):
            elem_id = end.get("id")
            elem_name = end.get("name", elem_id)
            elements[elem_id] = BPMNElement(
                id=elem_id, name=elem_name, element_type="endEvent", attributes={}
            )
            end_events.append(elem_id)
            logger.debug(f"  Found end event: {elem_name} ({elem_id})")

        # Parse tasks (service tasks, user tasks, etc.)
        for task_type in ["task", "serviceTask", "userTask", "scriptTask", "manualTask"]:
            for task in process_elem.findall(f".//bpmn:{task_type}", BPMN_NS):
                elem_id = task.get("id")
                elem_name = task.get("name", elem_id)

                # Check for tool or agent binding in extension elements
                tool_binding = self._extract_tool_binding(task)
                agent_binding = self._extract_agent_binding(task)

                elements[elem_id] = BPMNElement(
                    id=elem_id,
                    name=elem_name,
                    element_type=task_type,
                    attributes=dict(task.attrib),
                    tool_binding=tool_binding,
                    agent_binding=agent_binding,
                )

                if tool_binding:
                    logger.debug(f"  Found {task_type}: {elem_name} ({elem_id}) [tool: {tool_binding}]")
                elif agent_binding:
                    logger.debug(f"  Found {task_type}: {elem_name} ({elem_id}) [agent: {agent_binding}]")
                else:
                    logger.debug(f"  Found {task_type}: {elem_name} ({elem_id})")

        # Parse gateways
        for gateway_type in ["exclusiveGateway", "parallelGateway", "inclusiveGateway"]:
            for gateway in process_elem.findall(f".//bpmn:{gateway_type}", BPMN_NS):
                elem_id = gateway.get("id")
                elem_name = gateway.get("name", elem_id)

                # Check for condition tool or decision agent in extension elements
                condition_tool = self._extract_condition_tool(gateway)
                decision_agent = self._extract_decision_agent(gateway)

                elements[elem_id] = BPMNElement(
                    id=elem_id,
                    name=elem_name,
                    element_type=gateway_type,
                    attributes=dict(gateway.attrib),
                    condition_tool=condition_tool,
                    decision_agent=decision_agent,
                )

                if condition_tool:
                    logger.debug(
                        f"  Found {gateway_type}: {elem_name} ({elem_id}) [condition tool: {condition_tool}]"
                    )
                elif decision_agent:
                    logger.debug(
                        f"  Found {gateway_type}: {elem_name} ({elem_id}) [decision agent: {decision_agent}]"
                    )
                else:
                    logger.debug(f"  Found {gateway_type}: {elem_name} ({elem_id})")

        # Parse sequence flows
        for flow in process_elem.findall(".//bpmn:sequenceFlow", BPMN_NS):
            flow_id = flow.get("id")
            flow_name = flow.get("name", "")
            source_ref = flow.get("sourceRef")
            target_ref = flow.get("targetRef")

            # Check for condition expression
            condition = None
            cond_expr = flow.find(".//bpmn:conditionExpression", BPMN_NS)
            if cond_expr is not None and cond_expr.text:
                condition = cond_expr.text.strip()

            flows.append(
                BPMNFlow(
                    id=flow_id,
                    name=flow_name,
                    source_ref=source_ref,
                    target_ref=target_ref,
                    condition=condition,
                )
            )
            logger.debug(f"  Found flow: {source_ref} -> {target_ref} ({flow_id})")

        return BPMNProcess(
            id=process_id,
            name=process_name,
            elements=elements,
            flows=flows,
            start_event=start_event,
            end_events=end_events,
        )

    def build_graph(
        self,
        process: BPMNProcess,
        node_implementations: Dict[str, Callable],
        hooks: Optional[Dict[str, Any]] = None,
    ) -> StateGraph:
        """
        Build a LangGraph StateGraph from a parsed BPMN process.

        Args:
            process: Parsed BPMN process
            node_implementations: Map of element_id -> node function
            hooks: Optional map of flow_id -> hook configuration

        Returns:
            LangGraph StateGraph ready for execution
        """
        logger.info(f"Building graph for process: {process.name}")

        # Create StateGraph
        graph = StateGraph(FlowState)

        # Add nodes for each BPMN element (except start/end events)
        for elem_id, element in process.elements.items():
            if element.element_type in ["startEvent", "endEvent"]:
                continue

            # Get node implementation or create a stub
            if elem_id in node_implementations:
                node_func = node_implementations[elem_id]
            else:
                # Create a default pass-through node
                node_func = self._create_default_node(element)

            graph.add_node(elem_id, node_func)
            logger.debug(f"  Added node: {element.name} ({elem_id})")

        # Add edges based on sequence flows
        for flow in process.flows:
            source = flow.source_ref
            target = flow.target_ref

            # Handle start event
            if source == process.start_event:
                source = START

            # Handle end events
            if target in process.end_events:
                target = END

            # Add edge (conditional edges handled separately)
            if flow.condition:
                # This is a conditional flow from a gateway
                # Will be handled by DecisionAgent
                logger.debug(f"  Conditional flow: {flow.source_ref} -> {flow.target_ref}")
            else:
                graph.add_edge(source, target)
                logger.debug(f"  Added edge: {source} -> {target}")

        logger.info(f"Graph built successfully with {len(process.elements)} nodes")
        return graph

    def _create_default_node(self, element: BPMNElement) -> Callable:
        """Create a default pass-through node for elements without implementation."""

        def default_node(state: FlowState) -> FlowState:
            logger.info(f"Executing default node: {element.name} ({element.id})")
            state.execution_path.append(element.id)
            state.current_node_id = element.id
            return state

        return default_node

    def get_outgoing_flows(self, process: BPMNProcess, element_id: str) -> List[BPMNFlow]:
        """Get all outgoing flows from an element."""
        return [flow for flow in process.flows if flow.source_ref == element_id]

    def get_incoming_flows(self, process: BPMNProcess, element_id: str) -> List[BPMNFlow]:
        """Get all incoming flows to an element."""
        return [flow for flow in process.flows if flow.target_ref == element_id]

    def is_gateway(self, process: BPMNProcess, element_id: str) -> bool:
        """Check if an element is a gateway."""
        element = process.elements.get(element_id)
        if not element:
            return False
        return "Gateway" in element.element_type

    def get_gateway_type(self, process: BPMNProcess, element_id: str) -> Optional[str]:
        """Get the type of gateway (exclusive, parallel, inclusive)."""
        element = process.elements.get(element_id)
        if not element or "Gateway" not in element.element_type:
            return None
        return element.element_type

    # Made with Bob

    def _extract_tool_binding(self, element: ET.Element) -> Optional[str]:
        """
        Extract tool binding from BPMN extension elements.

        Looks for: <extensionElements><toolBinding>tool_name</toolBinding></extensionElements>

        Args:
            element: BPMN element (task or gateway)

        Returns:
            Tool name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            tool_binding = ext_elements.find(".//bpmn:toolBinding", BPMN_NS)
            if tool_binding is not None and tool_binding.text:
                return tool_binding.text.strip()

        # Try without namespace (some BPMN editors don't use namespace for extensions)
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            tool_binding = ext_elements.find(".//toolBinding")
            if tool_binding is not None and tool_binding.text:
                return tool_binding.text.strip()

        return None

    def _extract_agent_binding(self, element: ET.Element) -> Optional[str]:
        """
        Extract agent binding from BPMN extension elements.

        Looks for: <extensionElements><agentBinding>agent_name</agentBinding></extensionElements>

        Args:
            element: BPMN element (task or gateway)

        Returns:
            Agent name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            agent_binding = ext_elements.find(".//bpmn:agentBinding", BPMN_NS)
            if agent_binding is not None and agent_binding.text:
                return agent_binding.text.strip()

        # Try without namespace (some BPMN editors don't use namespace for extensions)
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            agent_binding = ext_elements.find(".//agentBinding")
            if agent_binding is not None and agent_binding.text:
                return agent_binding.text.strip()

        return None

    def _extract_condition_tool(self, element: ET.Element) -> Optional[str]:
        """
        Extract condition evaluation tool from gateway extension elements.

        Looks for: <extensionElements><conditionTool>tool_name</conditionTool></extensionElements>

        Args:
            element: BPMN gateway element

        Returns:
            Tool name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            cond_tool = ext_elements.find(".//bpmn:conditionTool", BPMN_NS)
            if cond_tool is not None and cond_tool.text:
                return cond_tool.text.strip()

        # Try without namespace
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            cond_tool = ext_elements.find(".//conditionTool")
            if cond_tool is not None and cond_tool.text:
                return cond_tool.text.strip()

    def _extract_decision_agent(self, element: ET.Element) -> Optional[str]:
        """
        Extract decision agent from gateway extension elements.

        Looks for: <extensionElements><decisionAgent>agent_name</decisionAgent></extensionElements>

        Args:
            element: BPMN gateway element

        Returns:
            Agent name if found, None otherwise
        """
        # Try standard BPMN namespace first
        ext_elements = element.find(".//bpmn:extensionElements", BPMN_NS)
        if ext_elements is not None:
            decision_agent = ext_elements.find(".//bpmn:decisionAgent", BPMN_NS)
            if decision_agent is not None and decision_agent.text:
                return decision_agent.text.strip()

        # Try without namespace
        ext_elements = element.find(".//extensionElements")
        if ext_elements is not None:
            decision_agent = ext_elements.find(".//decisionAgent")
            if decision_agent is not None and decision_agent.text:
                return decision_agent.text.strip()

        return None
        return None
