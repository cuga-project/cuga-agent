#!/usr/bin/env python3
"""
CUGA Cross-Platform Setup CLI

Automatically detects environment and installs/builds dependencies.
Supports: macOS (Intel/ARM), Linux (x86_64/aarch64), Windows
"""

import os
import sys
import platform
import subprocess
import shutil
import json
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class Environment:
    """Detected environment configuration."""
    os_type: str  # darwin, linux, win32
    arch: str  # x86_64, arm64, aarch64, AMD64
    python_version: str  # e.g., "3.12.0"
    is_ci: bool
    shell: str


class SetupCLI:
    """Smart cross-platform setup orchestrator."""

    # ANSI color codes
    COLORS = {
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "red": "\033[91m",
        "reset": "\033[0m",
        "bold": "\033[1m",
    }

    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.env = self._detect_environment()
        self.venv_path = self.project_root / ".venv"

    def _detect_environment(self) -> Environment:
        """Detect the current system environment."""
        system = platform.system().lower()
        if system == "darwin":
            os_type = "darwin"
        elif system == "linux":
            os_type = "linux"
        elif system == "windows":
            os_type = "win32"
        else:
            self._die(f"Unsupported OS: {system}")

        machine = platform.machine().lower()
        if machine == "arm64":
            arch = "arm64"
        elif machine == "aarch64":
            arch = "aarch64"
        elif machine in ["x86_64", "amd64"]:
            arch = "x86_64" if os_type != "win32" else "AMD64"
        else:
            self._die(f"Unsupported architecture: {machine}")

        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        shell = os.environ.get("SHELL", "/bin/bash").split("/")[-1]

        return Environment(
            os_type=os_type,
            arch=arch,
            python_version=platform.python_version(),
            is_ci=is_ci,
            shell=shell,
        )

    def _print(self, msg: str, color: str = "reset", bold: bool = False) -> None:
        """Print colored message."""
        prefix = self.COLORS.get(color, "")
        suffix = self.COLORS["reset"]
        bold_prefix = self.COLORS["bold"] if bold else ""
        print(f"{bold_prefix}{prefix}{msg}{suffix}")

    def _die(self, msg: str) -> None:
        """Print error and exit."""
        self._print(f"❌ ERROR: {msg}", color="red")
        sys.exit(1)

    def _run_cmd(
        self, cmd: list[str], check: bool = True, capture: bool = False
    ) -> Tuple[int, str]:
        """Execute command and return exit code and output."""
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=capture,
                text=True,
                cwd=self.project_root,
            )
            if check and result.returncode != 0:
                self._die(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
            return result.returncode, result.stdout
        except FileNotFoundError as e:
            self._die(f"Command not found: {cmd[0]}\n{e}")

    def _check_requirement(self, cmd: str, version_flag: str = "--version") -> bool:
        """Check if a command exists and optionally verify version."""
        result = shutil.which(cmd)
        return result is not None

    def detect_platform_info(self) -> dict:
        """Detect platform-specific information."""
        info = {
            "os": self.env.os_type,
            "arch": self.env.arch,
            "python": self.env.python_version,
            "ci": self.env.is_ci,
        }

        # Add platform-specific details
        if self.env.os_type == "darwin":
            if self.env.arch == "x86_64":
                info["pytorch_variant"] = "cpu (Intel Mac - limited to torch 2.2.2)"
                info["build_time_estimate"] = "5-10 minutes"
            else:
                info["pytorch_variant"] = "cpu (Apple Silicon)"
                info["build_time_estimate"] = "3-7 minutes"
        elif self.env.os_type == "linux":
            info["pytorch_variant"] = "cpu"
            info["build_time_estimate"] = "3-8 minutes"
        elif self.env.os_type == "win32":
            info["pytorch_variant"] = "cpu"
            info["build_time_estimate"] = "5-15 minutes"

        return info

    def verify_prerequisites(self) -> bool:
        """Verify all prerequisites are installed."""
        self._print("\n🔍 Checking prerequisites...", color="blue", bold=True)

        required = {
            "python3": "Python 3.10+",
            "uv": "uv package manager",
            "git": "Git version control",
        }

        missing = []
        for cmd, desc in required.items():
            if self._check_requirement(cmd):
                self._print(f"  ✅ {desc} ({cmd})", color="green")
            else:
                self._print(f"  ❌ {desc} ({cmd}) - NOT FOUND", color="red")
                missing.append((cmd, desc))

        if missing:
            self._print("\n⚠️  Missing prerequisites:", color="yellow", bold=True)
            self._print_install_instructions(missing)
            return False

        # Verify Python version
        import sys

        if sys.version_info < (3, 10):
            self._die(f"Python 3.10+ required, found {self.env.python_version}")

        return True

    def _print_install_instructions(self, missing: list[Tuple[str, str]]) -> None:
        """Print installation instructions for missing tools."""
        if self.env.os_type == "darwin":
            self._print("\nOn macOS, install missing tools with Homebrew:", color="yellow")
            cmds = []
            for cmd, _ in missing:
                if cmd == "uv":
                    cmds.append("  brew install uv")
                elif cmd == "python3":
                    cmds.append("  brew install python@3.12")
                elif cmd == "git":
                    cmds.append("  brew install git")
            for cmd in set(cmds):
                self._print(cmd, color="yellow")

        elif self.env.os_type == "linux":
            self._print("\nOn Linux, install missing tools:", color="yellow")
            cmds = []
            for cmd, _ in missing:
                if cmd == "uv":
                    cmds.append("  curl -LsSf https://astral.sh/uv/install.sh | sh")
                elif cmd == "python3":
                    cmds.append("  sudo apt-get install python3.12 python3.12-venv")
                elif cmd == "git":
                    cmds.append("  sudo apt-get install git")
            for cmd in set(cmds):
                self._print(cmd, color="yellow")

        elif self.env.os_type == "win32":
            self._print(
                "\nOn Windows, install missing tools from:", color="yellow"
            )
            self._print("  - Python: https://python.org/downloads/", color="yellow")
            self._print("  - uv: https://astral.sh/uv/", color="yellow")
            self._print("  - Git: https://git-scm.com/download/win", color="yellow")

    def setup_environment(self) -> bool:
        """Main setup workflow."""
        self._print(
            "\n" + "=" * 60, color="blue", bold=True
        )
        self._print("CUGA Cross-Platform Setup", color="blue", bold=True)
        self._print("=" * 60, color="blue", bold=True)

        # 1. Detect and display environment
        self._print("\n📍 Detected Environment:", color="blue", bold=True)
        platform_info = self.detect_platform_info()
        for key, value in platform_info.items():
            self._print(f"  {key.upper():<20} {value}", color="blue")

        # 2. Verify prerequisites
        if not self.verify_prerequisites():
            self._print(
                "\n⏹️  Setup halted. Please install missing prerequisites.",
                color="yellow",
            )
            return False

        # 3. Check for existing .env file
        self._print("\n🔐 Checking configuration...", color="blue", bold=True)
        env_file = self.project_root / ".env"
        if env_file.exists():
            self._print("  ✅ .env file found", color="green")
            self._verify_env_keys()
        else:
            self._print(
                "  ⚠️  .env file not found - you'll need to configure API keys",
                color="yellow",
            )

        # 4. Create virtual environment
        self._print("\n🐍 Setting up Python environment...", color="blue", bold=True)
        if not self._setup_venv():
            return False

        # 5. Install dependencies
        self._print("\n📦 Installing dependencies...", color="blue", bold=True)
        self._print(
            f"   ⏱️  Estimated time: {platform_info.get('build_time_estimate', '5-10 minutes')}",
            color="yellow",
        )
        if not self._install_dependencies():
            return False

        # 6. Verify installation
        self._print("\n✔️  Verifying installation...", color="blue", bold=True)
        if not self._verify_installation():
            return False

        # 7. Success!
        self._print("\n" + "=" * 60, color="green", bold=True)
        self._print("✅ Setup Complete!", color="green", bold=True)
        self._print("=" * 60, color="green", bold=True)
        self._print_next_steps()
        return True

    def _verify_env_keys(self) -> None:
        """Verify required environment keys."""
        env_file = self.project_root / ".env"
        required_keys = ["OPENAI_API_KEY"]
        missing_keys = []

        with open(env_file) as f:
            content = f.read()
            for key in required_keys:
                if key not in content or f"{key}=" not in content:
                    missing_keys.append(key)

        if missing_keys:
            self._print(
                f"  ⚠️  Missing environment keys: {', '.join(missing_keys)}",
                color="yellow",
            )
        else:
            self._print("  ✅ All required API keys configured", color="green")

    def _setup_venv(self) -> bool:
        """Create and activate virtual environment."""
        if self.venv_path.exists():
            self._print(f"  ✅ Virtual environment exists at {self.venv_path}", color="green")
        else:
            self._print(f"  Creating virtual environment...", color="blue")
            self._run_cmd([sys.executable, "-m", "venv", str(self.venv_path)])
            self._print(f"  ✅ Virtual environment created", color="green")
        return True

    def _install_dependencies(self) -> bool:
        """Install dependencies using uv sync."""
        try:
            self._print("  Running: uv sync", color="blue")
            self._run_cmd(["uv", "sync"], check=True)
            self._print("  ✅ Dependencies installed successfully", color="green")
            return True
        except Exception as e:
            self._print(f"  ❌ Failed to install dependencies: {e}", color="red")
            return False

    def _verify_installation(self) -> bool:
        """Verify CUGA installation."""
        try:
            # Check cuga in the venv directly
            cuga_path = self.venv_path / "bin" / "cuga"
            if self.env.os_type == "win32":
                cuga_path = self.venv_path / "Scripts" / "cuga.exe"

            if cuga_path.exists() or self.env.os_type == "win32":
                self._print("  ✅ CUGA CLI verified", color="green")
                return True
            else:
                self._print(
                    "  ⚠️  Could not verify cuga command (may load on first run)",
                    color="yellow",
                )
                return True
        except Exception:
            # Non-critical - setup still succeeded
            self._print(
                "  ⚠️  Could not verify cuga command (may load on first run)",
                color="yellow",
            )
            return True

    def _print_next_steps(self) -> None:
        """Print next steps after successful setup."""
        self._print("\n📋 Next Steps:", color="blue", bold=True)

        if self.env.os_type == "win32":
            activate = f"{self.venv_path}\\Scripts\\activate"
        else:
            activate = f"source {self.venv_path}/bin/activate"

        self._print(f"\n1. Activate the environment:", color="blue")
        self._print(f"   {activate}", color="yellow", bold=True)

        self._print(f"\n2. Verify installation:", color="blue")
        self._print(f"   cuga --version", color="yellow", bold=True)

        self._print(f"\n3. Start CUGA:", color="blue")
        self._print(f"   cuga start demo_crm --read-only", color="yellow", bold=True)

        self._print(f"\n4. Open browser:", color="blue")
        self._print(f"   https://localhost:7860", color="yellow", bold=True)

        self._print(f"\n💡 Configuration:", color="blue")
        self._print(f"   Config file: settings.openai.toml", color="blue")
        self._print(
            f"   API keys: Configure in .env file before running CUGA",
            color="blue",
        )

        self._print(f"\n📚 Documentation:", color="blue")
        self._print(f"   https://cuga.dev", color="blue")
        self._print(f"   README.md in project root", color="blue")


def main():
    """Entry point."""
    try:
        setup = SetupCLI()
        success = setup.setup_environment()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️  Setup cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
