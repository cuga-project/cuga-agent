# CUGA Setup CLI

Automated cross-platform installation and configuration for CUGA.

## Overview

The `setup_cli.py` script automates the entire CUGA setup process across macOS (Intel/ARM), Linux, and Windows. It detects your environment and installs dependencies appropriately for your platform.

## Quick Start

```bash
python3 scripts/setup_cli.py
```

That's it! The script will:
1. ✅ Detect your OS, CPU architecture, and Python version
2. ✅ Verify required tools (Python 3.10+, uv, Git)
3. ✅ Create a virtual environment
4. ✅ Install all dependencies (5-15 minutes, platform-dependent)
5. ✅ Verify the installation
6. ✅ Print next steps

## What It Checks

### System Requirements
- **Python**: 3.10, 3.11, or 3.12
- **uv**: Package manager (latest recommended)
- **Git**: Version control

### Environment Detection
- **OS**: macOS, Linux, or Windows
- **Architecture**: Intel (x86_64), Apple Silicon (arm64), or other
- **Python Version**: Full version detection
- **CI Environment**: Detects GitHub Actions, GitLab CI, etc.

### Platform-Specific Notes
- **Intel Macs**: PyTorch limited to v2.2.2 (build: 5-10 min)
- **Apple Silicon**: Full PyTorch support (build: 3-7 min)
- **Linux**: Full PyTorch support (build: 3-8 min)
- **Windows**: Full PyTorch support (build: 5-15 min)

## Installation Steps

### Step 1: Clone/Navigate to Project
```bash
cd cuga-agent
```

### Step 2: Run Setup CLI
```bash
python3 scripts/setup_cli.py
```

The script will:
- Display detected platform info
- Check for prerequisites
- Install dependencies if missing
- Create `.venv/` directory
- Run `uv sync` to install packages

### Step 3: Activate Virtual Environment
After setup completes, activate the venv:

**macOS/Linux:**
```bash
source .venv/bin/activate
```

**Windows:**
```cmd
.venv\Scripts\activate
```

### Step 4: Verify Installation
```bash
cuga --help
```

### Step 5: Configure API Keys
Edit `.env` file (create if needed):
```bash
OPENAI_API_KEY=sk-your-api-key-here
```

### Step 6: Run CUGA
```bash
cuga start demo_crm --read-only
```

Open browser at `https://localhost:7860`

## Troubleshooting

### Missing Prerequisites

**If uv is not installed:**
```bash
# macOS
brew install uv

# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**If Python 3.10+ not installed:**
```bash
# macOS
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt-get install python3.12

# Linux (RHEL/Fedora)
sudo dnf install python3.12

# Windows
Download from https://python.org/downloads/
```

**If Git not installed:**
```bash
# macOS
brew install git

# Linux
sudo apt-get install git

# Windows
Download from https://git-scm.com/download/win
```

### Build Times

The setup CLI will warn that dependency compilation takes 5-15 minutes. This is **normal and expected**, especially for:
- `docling-parse` (heavy C++ bindings)
- `cryptography` (Rust compilation)
- `torch` / `torchvision` (PyTorch)

Monitor progress:
```bash
# Check if build is still running
ps aux | grep -i "cargo\|rustc\|clang"
```

### Python Version Mismatch

If `python3` is not Python 3.10+, try:
```bash
python3.12 scripts/setup_cli.py
```

Or set alias:
```bash
alias python3="python3.12"
python3 scripts/setup_cli.py
```

### Virtual Environment Issues

If `.venv/` is corrupted, remove and recreate:
```bash
rm -rf .venv
python3 scripts/setup_cli.py
```

### Port Already in Use

If `https://localhost:7860` is in use, run on different port:
```bash
cuga start demo_crm --port 8000
```

## Advanced Usage

### Manual Setup (If Script Fails)

```bash
# Create venv
python3 -m venv .venv

# Activate
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate     # Windows

# Sync dependencies
uv sync

# Configure
echo "OPENAI_API_KEY=sk-..." > .env

# Verify
cuga --version
```

### CI/CD Integration

The setup CLI automatically detects CI environments and skips interactive prompts:

```yaml
# GitHub Actions example
- name: Setup CUGA
  run: python3 scripts/setup_cli.py

- name: Run tests
  run: |
    source .venv/bin/activate
    pytest tests/
```

### Editable/Development Install

For development with live code changes:

```bash
# Activate venv
source .venv/bin/activate

# Install in editable mode
uv sync --editable

# Changes to src/ now reflect immediately
```

## Script Architecture

The setup CLI is organized into these methods:

- `_detect_environment()` - Identify OS, arch, Python version, CI mode
- `detect_platform_info()` - Get platform-specific details (PyTorch variant, build time)
- `verify_prerequisites()` - Check for required tools
- `setup_environment()` - Main workflow orchestrator
- `_setup_venv()` - Create virtual environment
- `_install_dependencies()` - Run `uv sync`
- `_verify_installation()` - Test CUGA command
- `_print_next_steps()` - Display completion message

## Environment Variables

The script respects these environment variables:

- `CI` or `GITHUB_ACTIONS` - Activates CI mode (no interactive prompts)
- `SHELL` - Used to detect shell type

## Output Examples

### Successful Setup (Apple Silicon)

```
============================================================
CUGA Cross-Platform Setup
============================================================

📍 Detected Environment:
  OS                   darwin
  ARCH                 arm64
  PYTHON               3.12.1
  CI                   false
  PYTORCH_VARIANT      cpu (Apple Silicon)
  BUILD_TIME_ESTIMATE  3-7 minutes

🔍 Checking prerequisites...
  ✅ Python 3.10+ (python3)
  ✅ uv package manager (uv)
  ✅ Git version control (git)

🔐 Checking configuration...
  ✅ .env file found
  ✅ All required API keys configured

🐍 Setting up Python environment...
  ✅ Virtual environment created

📦 Installing dependencies...
  ⏱️  Estimated time: 3-7 minutes
  Running: uv sync
  ✅ Dependencies installed successfully

✔️  Verifying installation...
  ✅ CUGA CLI verified

============================================================
✅ Setup Complete!
============================================================

📋 Next Steps:

1. Activate the environment:
   source .venv/bin/activate

2. Verify installation:
   cuga --version

3. Start CUGA:
   cuga start demo_crm --read-only

4. Open browser:
   https://localhost:7860
```

### Missing Prerequisites (macOS)

```
🔍 Checking prerequisites...
  ✅ Python 3.10+ (python3)
  ❌ uv package manager (uv) - NOT FOUND
  ✅ Git version control (git)

⚠️  Missing prerequisites:

On macOS, install missing tools with Homebrew:
  brew install uv

⏹️  Setup halted. Please install missing prerequisites.
```

## Performance

### Build Times by Platform

| Platform | Architecture | Time |
|----------|--------------|------|
| macOS | Intel (x86_64) | 5-10 min |
| macOS | Apple Silicon (arm64) | 3-7 min |
| Linux | x86_64 | 3-8 min |
| Linux | aarch64 | 3-8 min |
| Windows | AMD64 | 5-15 min |

**Note**: Times vary based on disk speed, CPU cores, and network.

## Supported Platforms

✅ Tested and supported:
- macOS 12+ (Intel and Apple Silicon)
- Ubuntu 20.04+ (x86_64 and aarch64)
- CentOS/RHEL 8+ (x86_64)
- Fedora 36+ (x86_64)
- Windows 10/11 (AMD64)

## Support

For issues:
1. Check [main SETUP_GUIDE.md](../SETUP_GUIDE.md) for troubleshooting
2. Open issue at: https://github.com/cuga-project/cuga-agent/issues
3. Check GitHub Discussions: https://github.com/cuga-project/cuga-agent/discussions

## Development

To modify the setup script:

```bash
# Edit the script
vim scripts/setup_cli.py

# Test on your platform
python3 scripts/setup_cli.py

# The script has no external dependencies (uses only stdlib)
```

## License

Same as CUGA project (Apache 2.0)
