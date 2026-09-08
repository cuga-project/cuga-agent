import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_STREAM_PATHS = (
    REPO_ROOT / "src/frontend_workspaces/agentic_chat/public/fake_data.json",
    REPO_ROOT / "src/frontend_workspaces/frontend/static/fake_data.json",
)


def test_fake_stream_fixtures_match_and_end_with_answer():
    fixtures = [json.loads(path.read_text()) for path in FAKE_STREAM_PATHS]

    assert fixtures[0] == fixtures[1]
    assert any(step["name"] == "CodeAgent" for step in fixtures[0]["steps"])
    assert fixtures[0]["steps"][-1] == {
        "name": "Answer",
        "data": "There are 3 accounts in the available data.",
    }
