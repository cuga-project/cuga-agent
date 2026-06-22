"""
BPMN Parser - Converts BPMN 2.0 XML to BPMNProcess.

Parses BPMN 2.0 XML files into BPMNProcess, BPMNElement, and BPMNFlow
dataclasses consumed by the FlowAgent and WorkflowEngine.

Supported BPMN elements:
- Start Event, End Event
- Task (Service Task, User Task, etc.)
- Exclusive Gateway, Parallel Gateway
- Sequence Flow
- Sub-Process
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from loguru import logger


# BPMN 2.0 namespace
BPMN_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}


@dataclass
class BPMNElement:
    """Represents a BPMN element."""

    id: str
    name: str
    element_type: str  # task, gateway, event, etc.
    attributes: Dict[str, Any]


@dataclass
class BPMNFlow:
    """Represents a BPMN sequence flow."""

    id: str
    name: str
    source_ref: str
    target_ref: str
    condition: Optional[str] = None


@dataclass
class BPMNProcess:
    """Represents a parsed BPMN process."""

    id: str
    name: str
    elements: Dict[str, BPMNElement]
    flows: List[BPMNFlow]
    start_event: Optional[str] = None
    end_events: List[str] = None

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BPMNProcess":
        elements = {
            k: BPMNElement(
                id=e["id"],
                name=e["name"],
                element_type=e["element_type"],
                attributes=e.get("attributes", {}),
            )
            for k, e in data.get("elements", {}).items()
        }
        flows = [
            BPMNFlow(
                id=f["id"],
                name=f["name"],
                source_ref=f["source_ref"],
                target_ref=f["target_ref"],
                condition=f.get("condition"),
            )
            for f in data.get("flows", [])
        ]
        return cls(
            id=data["id"],
            name=data["name"],
            elements=elements,
            flows=flows,
            start_event=data.get("start_event"),
            end_events=data.get("end_events") or [],
        )


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
                elements[elem_id] = BPMNElement(
                    id=elem_id,
                    name=elem_name,
                    element_type=task_type,
                    attributes=dict(task.attrib),
                )
                logger.debug(f"  Found {task_type}: {elem_name} ({elem_id})")

        # Parse gateways
        for gateway_type in ["exclusiveGateway", "parallelGateway", "inclusiveGateway"]:
            for gateway in process_elem.findall(f".//bpmn:{gateway_type}", BPMN_NS):
                elem_id = gateway.get("id")
                elem_name = gateway.get("name", elem_id)
                elements[elem_id] = BPMNElement(
                    id=elem_id,
                    name=elem_name,
                    element_type=gateway_type,
                    attributes=dict(gateway.attrib),
                )
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

    # Made with Bob
