import tomllib
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def test_pyproject_does_not_export_local_evolve_launcher() -> None:
    pyproject_path = _repo_root() / "pyproject.toml"
    parsed = tomllib.loads(pyproject_path.read_text())

    assert "cuga-evolve-mcp" not in parsed["project"]["scripts"]


def test_evolve_templates_use_upstream_package_entrypoint() -> None:
    repo_root = _repo_root()
    source_template = (repo_root / "src/frontend_workspaces/frontend/src/AddToolModal.tsx").read_text()
    bundled_template = next((repo_root / "src/cuga/frontend/dist").glob("main.*.js")).read_text()
    readme = (repo_root / "README.md").read_text()

    expected_args = "--from\\naltk-evolve\\n--with\\nsetuptools<70\\nevolve-mcp"

    assert 'command: "uvx"' in source_template
    assert f'argsText: "{expected_args}"' in source_template
    assert "cuga-evolve-mcp" not in source_template
    assert "cuga.backend.evolve.mcp_server" not in source_template
    assert 'OPENAI_BASE_URL: "env://OPENAI_BASE_URL"' in source_template

    assert f'argsText: "{expected_args}"' in bundled_template
    assert "cuga.backend.evolve.mcp_server" not in bundled_template
    assert 'OPENAI_BASE_URL: "env://OPENAI_BASE_URL"' in bundled_template

    assert "cuga-evolve-mcp" not in readme
    assert "cuga.backend.evolve.mcp_server" not in readme
    assert "--from altk-evolve" in readme or "--from\naltk-evolve" in readme
    assert "evolve-mcp" in readme
    assert "setuptools<70" in readme
    assert "OPENAI_BASE_URL=env://OPENAI_BASE_URL" in readme
