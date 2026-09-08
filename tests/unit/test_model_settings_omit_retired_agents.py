from pathlib import Path

import pytest

RETIRED_AGENT_SECTIONS = (
    "agent.task_decomposition.model",
    "agent.plan_controller.model",
    "agent.code_planner.model",
    "agent.shortlister.model",
)


@pytest.mark.unit
def test_model_settings_omit_retired_full_graph_agents():
    models_dir = Path(__file__).resolve().parents[2] / "src/cuga/configurations/models"
    leftovers = []
    for path in sorted(models_dir.glob("settings*.toml")):
        text = path.read_text()
        for section in RETIRED_AGENT_SECTIONS:
            if f"[{section}]" in text:
                leftovers.append(f"{path.name}: [{section}]")
    assert leftovers == [], (
        "Retired full-graph agent model sections remain in provider settings: " + ", ".join(leftovers)
    )
