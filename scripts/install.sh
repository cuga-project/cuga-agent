#!/usr/bin/env bash
# CUGA installer v0.4.1. Publish this exact file only after cuga 0.4.1 is on PyPI.
set -euo pipefail
CUGA_RELEASE="0.4.1"
UV_BOOTSTRAP_RELEASE="0.9.8"
fail() { printf 'CUGA installation failed: %s\n' "$*" >&2; exit 1; }
case "$(uname -s)" in
  Darwin)
    [[ "$(uname -m)" == "arm64" ]] || fail 'This release requires Apple Silicon on macOS (CPU PyTorch wheels are unavailable for Intel Macs).'
    mac_major=$(sw_vers -productVersion | cut -d. -f1)
    (( mac_major >= 14 )) || fail 'macOS 14 or newer is required by the approved PyTorch wheel.'
    ;;
  Linux)
    libc_version=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
    [[ "$libc_version" == glibc* ]] || fail 'Linux/WSL with glibc 2.28 or newer is required (musl/Alpine is not supported).'
    IFS=. read -r libc_major libc_minor <<< "${libc_version#glibc }"
    (( libc_major > 2 || (libc_major == 2 && libc_minor >= 28) )) || fail 'glibc 2.28 or newer is required.'
    ;;
  *) fail 'Supported systems: macOS, Linux, and WSL (run inside your Linux terminal).' ;;
esac
case "$(uname -m)" in
  x86_64|aarch64|arm64) ;;
  *) fail 'A 64-bit x86 or ARM machine is required.' ;;
esac
command -v curl >/dev/null || fail 'Install curl, then run this command again.'
task_tmp=$(mktemp -d)
trap 'rm -rf "$task_tmp"' EXIT
if command -v uv >/dev/null; then
  uv_cmd=$(command -v uv)
else
  bootstrap_bin="${UV_TOOL_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
  mkdir -p "$bootstrap_bin"
  curl -fsSL "https://astral.sh/uv/$UV_BOOTSTRAP_RELEASE/install.sh" -o "$task_tmp/uv-install.sh"
  UV_INSTALL_DIR="$bootstrap_bin" UV_NO_MODIFY_PATH=1 sh "$task_tmp/uv-install.sh"
  uv_cmd="$bootstrap_bin/uv"
fi
uv_version=$("$uv_cmd" --version | awk '{print $2}')
IFS=. read -r uv_major uv_minor uv_patch <<< "$uv_version"
if (( uv_major == 0 && (uv_minor < 9 || (uv_minor == 9 && uv_patch < 8)) )); then
  fail 'uv 0.9.8 or newer is required. Run uv self update, then retry.'
fi
printf 'Installing CUGA %s with Python 3.12 in a dedicated uv tool environment…\n' "$CUGA_RELEASE"
"$uv_cmd" python install 3.12
# BEGIN CONSTRAINTS (synchronized with scripts/install/constraints.txt)
cat > "$task_tmp/constraints.txt" <<'CUGA_CONSTRAINTS'
pillow>=12.3.0
urllib3>=2.7.0
h11>=0.16.0
setuptools>=83.0.0
authlib>=1.7.1
idna>=3.15
orjson>=3.11.6
pyasn1>=0.6.4
protobuf>=5.29.6
strawberry-graphql>=0.315.7
arize-phoenix>=17.13.0
starlette>=1.3.1
python-multipart>=0.0.30
langsmith>=0.8.18
langgraph-checkpoint>=4.1.1
langgraph-sdk>=0.3.15
pydantic-settings>=2.14.2
langchain-classic>=1.0.7
docling>=2.94.0
mako>=1.3.12
soupsieve>=2.8.4
joserfc>=1.6.8
json-repair>=0.60.1
datamodel-code-generator>=0.64.0
transformers>=5.10.0
httpx2>=2.12.0
accelerate>=1.15.0
anyio>=4.14.2
torch==2.13.0; sys_platform == "darwin"
torchvision==0.28.0; sys_platform == "darwin"
torch==2.13.0+cpu; sys_platform == "linux"
torchvision==0.28.0+cpu; sys_platform == "linux"
CUGA_CONSTRAINTS
# END CONSTRAINTS
# BEGIN OVERRIDES (synchronized with scripts/install/overrides.txt)
cat > "$task_tmp/overrides.txt" <<'CUGA_OVERRIDES'
python-dotenv>=1.1.0
nltk>=3.10.3
pytz>=2025.1
pandas>=2.3.3,<2.4
safetensors>=0.8.0
CUGA_OVERRIDES
# END OVERRIDES
# Hash-pinned CPU wheels from the approved release lockfile. All other packages
# resolve from PyPI; an extra global index would shadow dependencies such as setuptools.
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    torch_req="torch @ https://download-r2.pytorch.org/whl/cpu/torch-2.13.0-cp312-cp312-macosx_14_0_arm64.whl#sha256=2fe228aba290d14b9f31b049be550dbd469c3fd3013d7a19705b30454da97027"
    vision_req="torchvision @ https://download-r2.pytorch.org/whl/cpu/torchvision-0.28.0-cp312-cp312-macosx_14_0_arm64.whl#sha256=e9f54c30cd52e3ef7fd034cc69b7bb7e0964e1c8f8743e018ab92e95b40f9eee"
    ;;
  Linux-aarch64)
    torch_req="torch @ https://download-r2.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=6f307c2c32d764ffc6ff6893b801fad6d4752f3e67966cb8abf1843427c02604"
    vision_req="torchvision @ https://download-r2.pytorch.org/whl/cpu/torchvision-0.28.0%2Bcpu-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=2f768c4f6d5adf6d5535061fd69ec44827608bac0e96e12114942a6fdfce1107"
    ;;
  Linux-x86_64)
    torch_req="torch @ https://download-r2.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=4ca4a9394b0c771238a4f73590fdbbc4debad85ed0fa63d026ae1b085da7d6e2"
    vision_req="torchvision @ https://download-r2.pytorch.org/whl/cpu/torchvision-0.28.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=b545d46f4d2f9d30381281cf22874bfe1d32a8a7b0ee8396fccde89f30c6a9d9"
    ;;
  *) fail "No approved CPU PyTorch wheels for this platform." ;;
esac
"$uv_cmd" tool install --no-config --python 3.12 --force \
  --with "$torch_req" --with "$vision_req" \
  --constraints "$task_tmp/constraints.txt" --overrides "$task_tmp/overrides.txt" \
  "cuga==$CUGA_RELEASE"
tool_root=$("$uv_cmd" tool dir)
"$tool_root/cuga/bin/python" - <<'CUGA_VERIFY'
from importlib.resources import files
from importlib.metadata import version
root = files('cuga') / 'frontend' / 'dist'
assert (root / 'index.html').is_file(), 'The CUGA wheel is missing the manager frontend'
assert list(root.glob('*.js')), 'The CUGA wheel is missing frontend JavaScript'
print('Verified CUGA', version('cuga'), 'and its packaged frontend.')
CUGA_VERIFY
bin_dir=$("$uv_cmd" tool dir --bin)
"$bin_dir/cuga" --help >/dev/null
"$uv_cmd" tool update-shell
printf '\nCUGA %s is installed. Next: cuga start manager\n' "$CUGA_RELEASE"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf 'Open a new terminal, or run: export PATH="%s:$PATH"\n' "$bin_dir" ;;
esac
printf 'Setup guide: https://github.com/cuga-project/cuga-agent/blob/v%s/docs/getting-started.md\n' "$CUGA_RELEASE"
