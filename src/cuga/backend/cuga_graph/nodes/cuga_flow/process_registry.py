"""
ProcessRegistry - Catalog of BPMN process definitions.

Loaded at startup; read-only at runtime.  Maps short process keys to their
BPMNProcess and FlowConfig, with a parsed+cached BPMN object per key.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNParser, BPMNProcess
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig


@dataclass
class ProcessDefinition:
    key: str
    name: str
    bpmn_path: str
    config_path: str
    policies_dir: str = ""


class ProcessRegistry:
    """Catalog of BPMN process definitions. Loaded at startup; read-only at runtime."""

    def __init__(self, bridge: Any = None) -> None:
        self._definitions: Dict[str, ProcessDefinition] = {}
        self._bpmn_cache: Dict[str, BPMNProcess] = {}
        self._config_cache: Dict[str, FlowConfig] = {}
        if bridge is not None:
            bridge.register_registry(self)

    def register_flow(self, flow_config_path: str) -> str:
        """
        Parse YAML + BPMN from flow_config_path and register the process.

        Returns the process key derived from flow.id / flow.name in the YAML.
        """
        config = FlowConfig.from_yaml(flow_config_path)
        process_key = config.get_flow_id() or config.get_flow_name() or "process"
        bpmn_file = config.get_bpmn_file()
        config_dir = Path(flow_config_path).parent
        policies_path = config_dir / "policies"
        self._definitions[process_key] = ProcessDefinition(
            key=process_key,
            name=config.get_flow_name() or process_key,
            bpmn_path=bpmn_file,
            config_path=flow_config_path,
            policies_dir=str(policies_path) if policies_path.is_dir() else "",
        )
        self._bpmn_cache[process_key] = BPMNParser().parse_file(bpmn_file)
        self._config_cache[process_key] = config
        logger.info(f"ProcessRegistry: registered flow '{process_key}' from {flow_config_path}")
        return process_key

    def _register_preloaded(
        self,
        key: str,
        bpmn: BPMNProcess,
        definition: ProcessDefinition,
        config: Optional["FlowConfig"] = None,
    ) -> None:
        """Register pre-parsed objects directly. Used by the backward-compat bpmn_file path."""
        self._definitions[key] = definition
        self._bpmn_cache[key] = bpmn
        if config is not None:
            self._config_cache[key] = config
        logger.info(f"ProcessRegistry: registered pre-loaded flow '{key}'")

    def _get_definition(self, key: str) -> ProcessDefinition:
        if key not in self._definitions:
            raise KeyError(f"Process '{key}' not registered. Available: {list(self._definitions)}")
        return self._definitions[key]

    def get_bpmn_process(self, key: str) -> BPMNProcess:
        if key not in self._bpmn_cache:
            defn = self._get_definition(key)
            self._bpmn_cache[key] = BPMNParser().parse_file(defn.bpmn_path)
            logger.debug(f"ProcessRegistry: parsed BPMN for '{key}'")
        return self._bpmn_cache[key]

    def get_flow_config(self, key: str) -> FlowConfig:
        if key not in self._config_cache:
            defn = self._get_definition(key)
            self._config_cache[key] = FlowConfig.from_yaml(defn.config_path)
            logger.debug(f"ProcessRegistry: loaded FlowConfig for '{key}'")
        return self._config_cache[key]

    def list_keys(self) -> List[str]:
        return list(self._definitions.keys())
