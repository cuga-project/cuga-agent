#!/usr/bin/env bash
# =============================================================================
# CUGA Cross-Platform Setup Script
#
# Detects your environment, installs missing system dependencies, creates a
# Python virtual environment, and installs all project dependencies via uv.
#
# Usage:
#   bash scripts/setup.sh          # interactive
#   CI=1 bash scripts/setup.sh     # non-interactive (CI mode)
#
# Supports: macOS (Intel/ARM), Linux (x86_64/aarch64), Windows (Git Bash/WSL)
# Requires: bash 4+, internet access
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
  BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; BLUE=''; BOLD=''; RESET=''
fi

info()    { echo -e "${BLUE}${BOLD}$*${RESET}"; }
success() { echo -e "${GREEN}✅ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${RESET}"; }
error()   { echo -e "${RED}❌ ERROR: $*${RESET}" >&2; }
die()     { error "$*"; exit 1; }
step()    { echo -e "\n${BOLD}${BLUE}──────────────────────────────────────────${RESET}"; info "$*"; }

# ---------------------------------------------------------------------------
# CI / non-interactive detection
# ---------------------------------------------------------------------------
IS_CI=false
for _v in "$CI" "$GITHUB_ACTIONS" "$CIRCLECI" "$TRAVIS"; do
  [[ "$_v" == "true" || "$_v" == "1" ]] && IS_CI=true && break
done
unset _v

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
step "🔍 Detecting environment"

_UNAME_S="$(uname -s)"
OS_TYPE=""
case "$_UNAME_S" in
  Darwin)              OS_TYPE="darwin"  ;;
  Linux)               OS_TYPE="linux"   ;;
  MINGW*|MSYS*|CYGWIN*) OS_TYPE="windows" ;;
  *) die "Unsupported OS: $_UNAME_S" ;;
esac
unset _UNAME_S

case "$(uname -m)" in
  arm64|aarch64) ARCH_NORM="arm64"  ;;
  x86_64|AMD64)  ARCH_NORM="x86_64" ;;
  *) die "Unsupported architecture: $(uname -m)" ;;
esac

# Detect Apple Silicon chip generation (M1–M5+) via sysctl CPU brand string.
# sysctl is macOS-only; on Linux/Windows CHIP_GEN stays empty.
CHIP_GEN=""
if [[ "$OS_TYPE" == "darwin" && "$ARCH_NORM" == "arm64" ]]; then
  _brand="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
  case "$_brand" in
    *"M5"*) CHIP_GEN="M5" ;;
    *"M4"*) CHIP_GEN="M4" ;;
    *"M3"*) CHIP_GEN="M3" ;;
    *"M2"*) CHIP_GEN="M2" ;;
    *"M1"*) CHIP_GEN="M1" ;;
    *)      CHIP_GEN="Apple Silicon" ;;  # future chip or unrecognised brand
  esac
  unset _brand
fi

echo "  OS:           $OS_TYPE"
echo "  Architecture: $ARCH_NORM${CHIP_GEN:+ ($CHIP_GEN)}"
echo "  CI mode:      $IS_CI"

# Platform-specific build time estimates
case "$OS_TYPE/$ARCH_NORM" in
  darwin/arm64)  BUILD_EST="3–7 minutes ($CHIP_GEN)" ;;
  darwin/x86_64) BUILD_EST="5–10 minutes (Intel Mac — torch pinned to 2.2.2)" ;;
  linux/*)       BUILD_EST="3–8 minutes" ;;
  windows/*)     BUILD_EST="5–15 minutes" ;;
  *)             BUILD_EST="5–15 minutes" ;;
esac
echo "  Build time:   $BUILD_EST"

# Where is this script / project root?
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

echo "  Project root: $PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Helper: check whether a command exists
# ---------------------------------------------------------------------------
has() { command -v "$1" &>/dev/null; }

# ---------------------------------------------------------------------------
# Helper: install system packages
# ---------------------------------------------------------------------------
install_brew_pkg() {
  # Install via Homebrew, installing Homebrew first if needed
  if ! has brew; then
    warn "Homebrew not found. Installing…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      || die "Homebrew installation failed"
    # Add brew to PATH for the rest of this session
    if [[ -x /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi
  brew install "$@"
}

install_apt_pkg() {
  sudo apt-get update -qq
  sudo apt-get install -y "$@"
}

install_dnf_pkg() {
  sudo dnf install -y "$@"
}

install_pacman_pkg() {
  sudo pacman -Sy --noconfirm "$@"
}

linux_install_pkg() {
  if has apt-get; then
    install_apt_pkg "$@"
  elif has dnf; then
    install_dnf_pkg "$@"
  elif has pacman; then
    install_pacman_pkg "$@"
  else
    die "No supported package manager found (apt/dnf/pacman). Install $* manually."
  fi
}

# ---------------------------------------------------------------------------
# 1. Git
# ---------------------------------------------------------------------------
step "1/6 Checking git"
if has git; then
  success "git $(git --version | cut -d' ' -f3)"
else
  warn "git not found — installing…"
  case "$OS_TYPE" in
    darwin)  install_brew_pkg git ;;
    linux)   linux_install_pkg git ;;
    windows) die "Install Git for Windows from https://git-scm.com/download/win then re-run this script." ;;
  esac
  has git || die "git installation failed"
  success "git installed: $(git --version)"
fi

# ---------------------------------------------------------------------------
# 2. Python 3.10–3.12
# ---------------------------------------------------------------------------
step "2/6 Checking Python (requires 3.10–3.12)"

# Find the first candidate that exists, then check its version once.
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
  if has "$candidate"; then
    _ver="$("$candidate" -c 'import sys; v=sys.version_info; print(v.major,v.minor)' 2>/dev/null || true)"
    _major="${_ver%% *}"
    _minor="${_ver##* }"
    if [[ "$_major" -eq 3 && "$_minor" -ge 10 && "$_minor" -le 12 ]]; then
      PYTHON_BIN="$candidate"
      break
    fi
    unset _ver _major _minor
  fi
done
unset candidate

if [[ -z "$PYTHON_BIN" ]]; then
  warn "No compatible Python (3.10–3.12) found — installing Python 3.12…"
  case "$OS_TYPE" in
    darwin)
      install_brew_pkg python@3.12
      PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
      ;;
    linux)
      if has apt-get; then
        # Try deadsnakes PPA on Ubuntu/Debian
        if has add-apt-repository; then
          sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
          sudo apt-get update -qq
        fi
        install_apt_pkg python3.12 python3.12-venv python3.12-dev || \
          install_apt_pkg python3 python3-venv python3-dev
        PYTHON_BIN="$(command -v python3.12 2>/dev/null || command -v python3)"
      elif has dnf; then
        install_dnf_pkg python3.12 || install_dnf_pkg python3
        PYTHON_BIN="$(command -v python3.12 2>/dev/null || command -v python3)"
      else
        die "Cannot auto-install Python on this Linux. Install Python 3.10–3.12 manually."
      fi
      ;;
    windows)
      die "Install Python 3.12 from https://www.python.org/downloads/ then re-run."
      ;;
  esac
  has "$PYTHON_BIN" || die "Python installation failed. Install Python 3.10–3.12 manually."
fi

PYTHON_VER="$("$PYTHON_BIN" -c 'import sys; print(sys.version)')"
success "Python: $PYTHON_VER  ($PYTHON_BIN)"

# ---------------------------------------------------------------------------
# 3. uv
# ---------------------------------------------------------------------------
step "3/6 Checking uv (package manager)"
if has uv; then
  success "uv $(uv --version)"
else
  warn "uv not found — installing…"
  case "$OS_TYPE" in
    darwin|linux)
      curl -LsSf https://astral.sh/uv/install.sh | sh
      # The installer typically puts uv in ~/.local/bin or ~/.cargo/bin
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
      ;;
    windows)
      # Git Bash / MSYS2
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$APPDATA/uv/bin:$HOME/.local/bin:$PATH"
      ;;
  esac
  has uv || die "uv installation failed. Install manually: https://astral.sh/uv"
  success "uv installed: $(uv --version)"
fi

# ---------------------------------------------------------------------------
# 4. Virtual environment
# ---------------------------------------------------------------------------
step "4/6 Setting up virtual environment (.venv)"
if [[ -d "$VENV_DIR" ]]; then
  success "Virtual environment already exists at $VENV_DIR"
else
  info "Creating virtual environment with $PYTHON_BIN…"
  uv venv --python "$PYTHON_BIN" "$VENV_DIR"
  success "Virtual environment created at $VENV_DIR"
fi

# Determine activate script path
if [[ "$OS_TYPE" == "windows" ]]; then
  ACTIVATE_SCRIPT="$VENV_DIR/Scripts/activate"
  ACTIVATE_CMD="source $VENV_DIR/Scripts/activate"
else
  ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
  ACTIVATE_CMD="source $VENV_DIR/bin/activate"
fi

# ---------------------------------------------------------------------------
# 5. Activate the virtual environment
# ---------------------------------------------------------------------------
step "5/6 Activating virtual environment"
# shellcheck source=/dev/null
source "$ACTIVATE_SCRIPT"
success "Virtual environment activated: $VIRTUAL_ENV"

# ---------------------------------------------------------------------------
# 6. Install project dependencies (inside the active venv)
# ---------------------------------------------------------------------------
step "6/6 Installing dependencies via uv sync"
echo ""
warn "This may take $BUILD_EST — fetching and building wheels…"
echo ""

# Special note for Intel Mac
if [[ "$OS_TYPE" == "darwin" && "$ARCH_NORM" == "x86_64" ]]; then
  warn "Intel Mac detected: torch is pinned to 2.2.2 (last wheel published for x86_64)."
  warn "This is expected — see pyproject.toml for details."
fi

# --directory avoids a subshell; uv resolves pyproject.toml from there.
uv --directory "$PROJECT_ROOT" sync
success "All dependencies installed into $VIRTUAL_ENV"

# ---------------------------------------------------------------------------
# .env file check
# ---------------------------------------------------------------------------
step "🔐 Configuration check"
ENV_FILE="$PROJECT_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  success ".env file found"
  if grep -q "OPENAI_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    success "OPENAI_API_KEY is configured"
  else
    warn "OPENAI_API_KEY not found in .env — add it before running CUGA"
  fi
else
  warn ".env file not found at $PROJECT_ROOT/.env"
  warn "Copy the example and add your API keys:"
  echo "    cp .env.example .env    # (if .env.example exists)"
  echo "    # or create .env and add:  OPENAI_API_KEY=sk-..."
fi

# ---------------------------------------------------------------------------
# Verify cuga CLI
# ---------------------------------------------------------------------------
step "✔️  Verifying CUGA installation"
if [[ "$OS_TYPE" == "windows" ]]; then
  CUGA_BIN="$VENV_DIR/Scripts/cuga.exe"
else
  CUGA_BIN="$VENV_DIR/bin/cuga"
fi

if [[ -x "$CUGA_BIN" ]]; then
  success "CUGA CLI found at $CUGA_BIN"
else
  warn "cuga binary not found at expected path (it may still work once the venv is activated)"
fi

# Run cuga --help as a smoke test; capture output so it doesn't flood the terminal.
# Exit code 0 means the CLI loads and Typer responds correctly.
if "$CUGA_BIN" --help &>/dev/null; then
  success "cuga --help passed"
else
  _exit=$?
  warn "cuga --help exited with code $_exit — installation may be incomplete"
  warn "Try: $ACTIVATE_CMD && cuga --help"
  unset _exit
fi

# ---------------------------------------------------------------------------
# Done — print next steps
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}✅  Setup complete!${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════${RESET}"
echo ""
echo -e "${BOLD}Next steps:${RESET}"
echo ""
echo -e "  1. Activate the environment:"
echo -e "     ${YELLOW}${BOLD}$ACTIVATE_CMD${RESET}"
echo ""
echo -e "  2. Verify CUGA:"
echo -e "     ${YELLOW}${BOLD}cuga --version${RESET}"
echo ""
echo -e "  3. Start CUGA:"
echo -e "     ${YELLOW}${BOLD}cuga start demo_crm --read-only${RESET}"
echo ""
echo -e "  4. Open in browser:"
echo -e "     ${YELLOW}${BOLD}https://localhost:7860${RESET}"
echo ""
echo -e "${BOLD}Documentation:${RESET}"
echo -e "  • ${BLUE}https://cuga.dev${RESET}"
echo -e "  • ${BLUE}README.md${RESET} in project root"
echo ""
