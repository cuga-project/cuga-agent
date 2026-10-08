import os
import json
from pathlib import Path
import subprocess
import shutil
import tomllib

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_installer_carries_release_and_uv_constraints():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    script = (ROOT / "scripts/install.sh").read_text()
    assert f'CUGA_RELEASE="{config["project"]["version"]}"' in script
    for kind in ("constraint", "override"):
        for requirement in config["tool"]["uv"][kind + "-dependencies"]:
            assert requirement in script
    for kind in ("constraints", "overrides"):
        assert (ROOT / f"scripts/install/{kind}.txt").read_text() in script
    subprocess.run(["bash", "-n", str(ROOT / "scripts/install.sh")], check=True)


def executable(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize(
    "platform,arch", [("Linux", "x86_64"), ("Linux", "aarch64"), ("Darwin", "arm64"), ("Darwin", "x86_64")]
)
def test_repeat_install_selects_python_cpu_and_preserves_configuration(tmp_path, platform, arch):
    binary = tmp_path / "bin"
    binary.mkdir()
    tool = tmp_path / "tools/cuga/bin"
    tool.mkdir(parents=True)
    config = tmp_path / "settings.toml"
    config.write_text("existing provider configuration")
    executable(binary / "uname", f'if [[ "$1" == "-m" ]]; then echo {arch}; else echo {platform}; fi\n')
    executable(binary / "getconf", 'echo "glibc 2.35"\n')
    executable(binary / "sw_vers", 'echo "14.6"\n')
    executable(binary / "sysctl", 'echo "1"\n')
    executable(tool / "python", 'cat >/dev/null\n')
    executable(binary / "cuga", 'exit 0\n')
    executable(
        binary / "uv",
        '''printf '%s\\n' "$*" >> "$INSTALL_CALLS"
if [[ "$1" == "--version" ]]; then echo 'uv 0.9.8'; fi
if [[ "$1 $2" == 'tool dir' ]]; then
  if [[ "$3" == '--bin' ]]; then echo "$TEST_BIN"; else echo "$TEST_TOOLS"; fi
fi
''',
    )
    env = {
        **os.environ,
        "PATH": str(binary) + ":" + os.environ["PATH"],
        "TEST_BIN": str(binary),
        "TEST_TOOLS": str(tmp_path / "tools"),
        "INSTALL_CALLS": str(tmp_path / "calls"),
    }
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/install.sh")], env=env, capture_output=True, text=True, check=True
        )
        assert "Next: cuga start manager" in result.stdout
    calls = (tmp_path / "calls").read_text()
    python_request = "cpython-3.12-macos-aarch64-none" if platform == "Darwin" else "3.12"
    assert calls.count(f"python install {python_request}") == 2
    assert f"tool install --no-config --python {python_request}" in calls
    wheels = json.loads((ROOT / "scripts/install/torch-wheels.json").read_text())
    cpu_arch = "arm64" if platform == "Darwin" else arch
    assert wheels[f"{platform}-{cpu_arch}"]["torch"] in calls
    assert wheels[f"{platform}-{cpu_arch}"]["torchvision"] in calls
    assert "--index " not in calls
    assert "--constraints" in calls and "--overrides" in calls
    assert "cuga==0.4.1" in calls
    assert config.read_text() == "existing provider configuration"
    if platform == "Darwin" and arch == "x86_64":
        assert "Detected Apple Silicon through Rosetta" in result.stdout


@pytest.mark.parametrize("arm_support", ["0", "unavailable"])
def test_actual_intel_mac_is_rejected_before_installing(tmp_path, arm_support):
    executable(tmp_path / "uname", 'if [[ "$1" == "-m" ]]; then echo x86_64; else echo Darwin; fi\n')
    executable(tmp_path / "sysctl", 'exit 1\n' if arm_support == "unavailable" else 'echo "0"\n')
    executable(tmp_path / "uv", 'printf "installer must not run"; exit 99\n')
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install.sh")],
        env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "This release requires Apple Silicon on macOS" in result.stderr
    assert "installer must not run" not in result.stdout


def test_unsupported_platform_fails_before_installing(tmp_path):
    executable(tmp_path / "uname", "echo MINGW64_NT\n")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install.sh")],
        env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "run inside your Linux terminal" in result.stderr


def test_fresh_install_bootstraps_uv_and_explains_path_reload(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    for command in ("bash", "sh", "mktemp", "rm", "awk", "cat", "cut", "mkdir", "cp"):
        (commands / command).symlink_to(shutil.which(command))
    executable(commands / "uname", 'if [[ "$1" == "-m" ]]; then echo x86_64; else echo Linux; fi\n')
    executable(commands / "getconf", 'echo "glibc 2.35"\n')
    executable(
        commands / "curl",
        '''printf '%s\\n' "$*" >> "$INSTALL_CALLS"
cp "$FAKE_BOOTSTRAP" "${@: -1}"
''',
    )
    bootstrap = tmp_path / "bootstrap.sh"
    executable(bootstrap, 'cp "$FAKE_UV" "$UV_INSTALL_DIR/uv"\n')
    fake_uv = tmp_path / "uv"
    executable(
        fake_uv,
        '''printf '%s\\n' "$*" >> "$INSTALL_CALLS"
if [[ "$1" == "--version" ]]; then echo 'uv 0.9.8'; fi
if [[ "$1 $2" == 'tool dir' ]]; then
  if [[ "$3" == '--bin' ]]; then echo "$UV_TOOL_BIN_DIR"; else echo "$TEST_TOOLS"; fi
fi
''',
    )
    installed_bin = tmp_path / "installed-bin"
    installed_bin.mkdir()
    executable(installed_bin / "cuga", 'exit 0\n')
    tool = tmp_path / "tools/cuga/bin"
    tool.mkdir(parents=True)
    executable(tool / "python", 'cat >/dev/null\n')
    result = subprocess.run(
        [str(commands / "bash"), str(ROOT / "scripts/install.sh")],
        env={
            **os.environ,
            "PATH": str(commands),
            "UV_TOOL_BIN_DIR": str(installed_bin),
            "FAKE_BOOTSTRAP": str(bootstrap),
            "FAKE_UV": str(fake_uv),
            "TEST_TOOLS": str(tmp_path / "tools"),
            "INSTALL_CALLS": str(tmp_path / "calls"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    calls = (tmp_path / "calls").read_text()
    assert "https://astral.sh/uv/0.9.8/install.sh" in calls
    assert "tool update-shell" in calls
    assert (installed_bin / "uv").is_file()
    assert f'Open a new terminal, or run: export PATH="{installed_bin}:$PATH"' in result.stdout
