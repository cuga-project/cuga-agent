import pytest

from cuga.configurations import instructions_manager

pytestmark = pytest.mark.unit


def test_packaged_instruction_path_works_through_symlink(monkeypatch, tmp_path):
    package = tmp_path / "package"
    instructions = package / "configurations/default"
    instructions.mkdir(parents=True)
    (instructions / "answer.md").write_text("Packaged answer instruction")
    alias = tmp_path / "package-link"
    alias.symlink_to(package, target_is_directory=True)
    monkeypatch.setattr(
        instructions_manager, "__file__", str(alias / "configurations/instructions_manager.py")
    )
    monkeypatch.chdir(tmp_path)
    manager = object.__new__(instructions_manager.InstructionsManager)
    assert manager._load_file_content("configurations/default/answer.md") == "Packaged answer instruction"
    outside = tmp_path / "package-other"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")
    assert manager._load_file_content(str(outside / "secret.txt")) == ""
