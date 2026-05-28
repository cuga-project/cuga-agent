"""
ProcessRegistry - Catalog of BPMN process definitions.

Loaded at startup; read-only at runtime.  Maps short process keys to their
BPMNProcess and FlowConfig, with a parsed+cached BPMN object per key.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml
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

    def __init__(self) -> None:
        self._definitions: Dict[str, ProcessDefinition] = {}
        self._bpmn_cache: Dict[str, BPMNProcess] = {}
        self._config_cache: Dict[str, FlowConfig] = {}

    def register(
        self,
        key: str,
        bpmn_path: str,
        config_path: str,
        name: Optional[str] = None,
        policies_dir: str = "",
    ) -> None:
        self._definitions[key] = ProcessDefinition(
            key=key,
            name=name or key,
            bpmn_path=bpmn_path,
            config_path=config_path,
            policies_dir=policies_dir,
        )
        logger.info(f"ProcessRegistry: registered '{key}'")

    def register_flow(
        self,
        key: str,
        bpmn: BPMNProcess,
        definition: ProcessDefinition,
        config: Optional["FlowConfig"] = None,
    ) -> None:
        """Register a pre-parsed process, bypassing on-demand file loading."""
        self._definitions[key] = definition
        self._bpmn_cache[key] = bpmn
        if config is not None:
            self._config_cache[key] = config
        logger.info(f"ProcessRegistry: registered pre-loaded flow '{key}'")

    def register_from_directory(self, root_dir: str) -> None:
        """
        Auto-discover process subdirs that contain a config/ folder with a
        YAML having a 'flow:' key.  Compatible with the flow_agent_app_inline/
        layout where each subdirectory is one process.
        """
        root = Path(root_dir)
        if not root.is_dir():
            raise FileNotFoundError(f"Directory not found: {root_dir}")

        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            config_dir = subdir / "config"
            if not config_dir.is_dir():
                continue

            flow_yaml: Optional[Path] = None
            for yf in config_dir.glob("*.yaml"):
                try:
                    with open(yf) as f:
                        doc = yaml.safe_load(f)
                    if isinstance(doc, dict) and "flow" in doc:
                        flow_yaml = yf
                        break
                except Exception:
                    pass

            if flow_yaml is None:
                continue

            try:
                with open(flow_yaml) as f:
                    doc = yaml.safe_load(f)
                bpmn_rel = doc.get("flow", {}).get("bpmn_file", "")
                if not bpmn_rel:
                    continue
                bpmn_path = str(config_dir / bpmn_rel)
                if not Path(bpmn_path).exists():
                    logger.warning(f"ProcessRegistry: BPMN file not found: {bpmn_path}")
                    continue

                key = subdir.name
                name = doc.get("flow", {}).get("name", key)
                policies_path = config_dir / "policies"
                policies_dir = str(policies_path) if policies_path.is_dir() else ""

                self._definitions[key] = ProcessDefinition(
                    key=key,
                    name=name,
                    bpmn_path=bpmn_path,
                    config_path=str(flow_yaml),
                    policies_dir=policies_dir,
                )
                logger.info(f"ProcessRegistry: auto-discovered '{key}' from {flow_yaml.name}")
            except Exception as e:
                logger.warning(f"ProcessRegistry: failed to register '{subdir.name}': {e}")

    def get_definition(self, key: str) -> ProcessDefinition:
        if key not in self._definitions:
            raise KeyError(f"Process '{key}' not registered. Available: {list(self._definitions)}")
        return self._definitions[key]

    def get_bpmn_process(self, key: str) -> BPMNProcess:
        if key not in self._bpmn_cache:
            defn = self.get_definition(key)
            self._bpmn_cache[key] = BPMNParser().parse_file(defn.bpmn_path)
            logger.debug(f"ProcessRegistry: parsed BPMN for '{key}'")
        return self._bpmn_cache[key]

    def get_flow_config(self, key: str) -> FlowConfig:
        if key not in self._config_cache:
            defn = self.get_definition(key)
            self._config_cache[key] = FlowConfig.from_yaml(defn.config_path)
            logger.debug(f"ProcessRegistry: loaded FlowConfig for '{key}'")
        return self._config_cache[key]

    def list_keys(self) -> List[str]:
        return list(self._definitions.keys())
