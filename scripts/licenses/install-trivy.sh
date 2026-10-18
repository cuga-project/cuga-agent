#!/usr/bin/env bash
set -euo pipefail
# Immutable official release; hashes verified against GitHub release asset digests.
version=0.76.0
case "$(uname -m)" in
  x86_64) arch=64bit; checksum=e24b1c51293c1b15e4ba8d45bbc7891ba053f90278ec2698f3562c993e145fe7 ;;
  aarch64|arm64) arch=ARM64; checksum=aab21e677a7b7071fc79f1711480840754c18a1b9d9cefa7ccd06af63a0e8f4c ;;
  *) echo 'Unsupported Trivy runner architecture' >&2; exit 1 ;;
esac
install_dir="${RUNNER_TEMP:?}/cuga-trivy"
mkdir -p "$install_dir"
curl --fail --location --retry 3 "https://github.com/aquasecurity/trivy/releases/download/v${version}/trivy_${version}_Linux-${arch}.tar.gz" -o "$install_dir/trivy.tar.gz"
printf '%s  %s\n' "$checksum" "$install_dir/trivy.tar.gz" | sha256sum --check -
tar -xzf "$install_dir/trivy.tar.gz" -C "$install_dir" trivy
printf '%s\n' "$install_dir" >> "${GITHUB_PATH:?}"
"$install_dir/trivy" --version
