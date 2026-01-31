#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

declare -A OS_NAMES=(
  ["arch"]="Arch Linux"
  ["cachyos"]="CachyOS"
  ["steamos"]="SteamOS"
  ["ubuntu"]="Ubuntu"
  ["fedora"]="Fedora"
  ["bazzite"]="Bazzite"
)

for os_id in "${!OS_NAMES[@]}"; do
  echo "==> Checking OS mapping for ${os_id}"
  VR_HOTSPOT_OS_ID="${os_id}" \
  VR_HOTSPOT_OS_NAME="${OS_NAMES[${os_id}]}" \
    "${ROOT_DIR}/install.sh" --check-os --no-clear
done
