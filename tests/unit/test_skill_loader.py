from pathlib import Path

from cuga.backend.skills.loader import discover_skills, get_skill_search_roots
from cuga.backend.skills.registry import SkillEntry, SkillRegistry


def _write_skill(root: Path, name: str, description: str, body: str = "Body", requirements: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    requirements_block = f"requirements: {requirements}\n" if requirements else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{requirements_block}---\n{body}\n",
        encoding="utf-8",
    )


def test_skill_search_roots_prioritize_agents_paths_with_legacy_fallbacks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    roots = get_skill_search_roots(
        ".cuga",
        global_skills_root=str(tmp_path / "global_agents"),
        legacy_global_skills_root=str(tmp_path / "global_cuga"),
    )

    assert roots == [
        tmp_path / "global_cuga",
        tmp_path / "global_agents",
        tmp_path / ".cuga" / "skills",
        tmp_path / ".cuga" / ".skills",
        tmp_path / ".agents" / "skills",
    ]


def test_discover_skills_agents_paths_override_legacy_fallbacks_and_preserve_requirements(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_cuga = tmp_path / "global_cuga"
    global_agents = tmp_path / "global_agents"

    _write_skill(global_cuga, "shared", "legacy global")
    _write_skill(global_agents, "shared", "agents global")
    _write_skill(tmp_path / ".cuga" / "skills", "shared", "legacy local")
    _write_skill(
        tmp_path / ".agents" / "skills",
        "shared",
        "agents local",
        requirements="[python-pptx, npm:sharp]",
    )
    _write_skill(global_agents, "global_only", "only global agents")

    entries = discover_skills(
        ".cuga",
        global_skills_root=str(global_agents),
        legacy_global_skills_root=str(global_cuga),
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name["shared"].description == "agents local"
    assert by_name["shared"].requirements == ("python-pptx", "npm:sharp")
    assert by_name["global_only"].description == "only global agents"


def test_load_skill_points_at_source_folder_in_local_mode(
    tmp_path: Path, monkeypatch
) -> None:
    """Local mode (no sandbox, no /tmp/skills mount) must reference the on-disk skill folder."""
    # Unique name to avoid colliding with anything actually mounted at /tmp/skills/
    skill_name = "pytest_local_skill_unique"
    skill_root = tmp_path / skill_name
    skill_root.mkdir()
    skill_md = skill_root / "SKILL.md"
    skill_md.write_text("# body", encoding="utf-8")
    assert not Path(f"/tmp/skills/{skill_name}").exists(), "test fixture polluted"

    from cuga.config import settings as cuga_settings

    monkeypatch.setattr(cuga_settings.advanced_features, "opensandbox_sandbox", False, raising=False)
    monkeypatch.setattr(cuga_settings.advanced_features, "e2b_sandbox", False, raising=False)

    registry = SkillRegistry(
        [
            SkillEntry(
                name=skill_name,
                description="Test",
                body="instructions",
                source=str(skill_md),
            )
        ]
    )

    loaded = registry.load_skill(skill_name)

    assert f"`{skill_root}/`" in loaded
    assert f"/tmp/skills/{skill_name}" not in loaded


def test_load_skill_uses_tmp_skills_when_mount_exists(tmp_path: Path, monkeypatch) -> None:
    """When /tmp/skills/<name>/ exists (marketplace UI populates it), prefer that path
    so the load_skill text matches what the host's run_command can subprocess."""
    import os

    skill_name = "pytest_tmp_mount_unique"
    skill_root = tmp_path / skill_name
    skill_root.mkdir()
    skill_md = skill_root / "SKILL.md"
    skill_md.write_text("# body", encoding="utf-8")

    tmp_mount = Path(f"/tmp/skills/{skill_name}")
    tmp_mount.parent.mkdir(parents=True, exist_ok=True)
    if tmp_mount.exists() or tmp_mount.is_symlink():
        if tmp_mount.is_symlink() or tmp_mount.is_file():
            tmp_mount.unlink()
        else:
            import shutil as _sh
            _sh.rmtree(tmp_mount)
    os.symlink(skill_root, tmp_mount)

    try:
        from cuga.config import settings as cuga_settings

        monkeypatch.setattr(cuga_settings.advanced_features, "opensandbox_sandbox", False, raising=False)
        monkeypatch.setattr(cuga_settings.advanced_features, "e2b_sandbox", False, raising=False)

        registry = SkillRegistry(
            [
                SkillEntry(
                    name=skill_name,
                    description="Test",
                    body="instructions",
                    source=str(skill_md),
                )
            ]
        )

        loaded = registry.load_skill(skill_name)
        assert f"`/tmp/skills/{skill_name}/`" in loaded
    finally:
        if tmp_mount.is_symlink() or tmp_mount.exists():
            tmp_mount.unlink()


def test_load_skill_uses_sandbox_path_when_sandbox_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    """Sandbox modes upload to /tmp/skills/<name>/, so the agent must reference that path."""
    skill_root = tmp_path / "hiking_research"
    skill_root.mkdir()
    skill_md = skill_root / "SKILL.md"
    skill_md.write_text("# body", encoding="utf-8")

    from cuga.config import settings as cuga_settings

    monkeypatch.setattr(cuga_settings.advanced_features, "opensandbox_sandbox", True, raising=False)

    registry = SkillRegistry(
        [
            SkillEntry(
                name="hiking_research",
                description="Hikes",
                body="instructions",
                source=str(skill_md),
            )
        ]
    )

    loaded = registry.load_skill("hiking_research")

    assert "`/tmp/skills/hiking_research/`" in loaded
    assert str(skill_root) not in loaded


def test_load_skill_lists_companion_files_with_full_paths(tmp_path: Path, monkeypatch) -> None:
    """The agent must see actual companion filenames so weak models can't hallucinate them."""
    skill_name = "pytest_listing_unique"
    skill_root = tmp_path / skill_name
    (skill_root / "scripts").mkdir(parents=True)
    skill_md = skill_root / "SKILL.md"
    skill_md.write_text("# body", encoding="utf-8")
    (skill_root / "scripts" / "hike_tools.py").write_text("# code", encoding="utf-8")
    (skill_root / "scripts" / "__pycache__").mkdir()
    (skill_root / "scripts" / "__pycache__" / "ignored.pyc").write_text("x", encoding="utf-8")
    assert not Path(f"/tmp/skills/{skill_name}").exists(), "test fixture polluted"

    from cuga.config import settings as cuga_settings

    monkeypatch.setattr(cuga_settings.advanced_features, "opensandbox_sandbox", False, raising=False)
    monkeypatch.setattr(cuga_settings.advanced_features, "e2b_sandbox", False, raising=False)

    registry = SkillRegistry(
        [
            SkillEntry(
                name=skill_name,
                description="Test",
                body="instructions",
                source=str(skill_md),
            )
        ]
    )

    loaded = registry.load_skill(skill_name)

    assert f"  - {skill_root}/scripts/hike_tools.py" in loaded
    assert "do not invent filenames" in loaded
    assert "ignored.pyc" not in loaded
    assert "__pycache__" not in loaded


def test_skill_registry_load_skill_emits_install_steps_for_requirements() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="deck",
                description="Deck skill",
                body="Make slides.",
                source="/tmp/SKILL.md",
                requirements=("python-pptx", "npm:sharp"),
            )
        ]
    )

    loaded = registry.load_skill("deck")

    assert "await run_command('uv pip install --quiet python-pptx')" in loaded
    assert "await run_command('cd /tmp && npm install sharp')" in loaded
    assert "STEP 2 — SKILL INSTRUCTIONS" in loaded
    assert "`python -m <module> ...` → `uv run python -m <module> ...`" in loaded
    assert "`pip list` / `pip show` / `pip freeze` → `uv pip list`" in loaded
    assert "must never be rewritten as `uv npm`" in loaded
    assert "Do not use `uv npm`, `uv run node`, or `uv run npm`" in loaded
