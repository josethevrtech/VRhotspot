#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

declare -A OS_NAMES=(
  ["arch"]="Arch Linux"
  ["cachyos"]="CachyOS"
  ["endeavouros"]="EndeavourOS"
  ["steamos"]="SteamOS"
  ["ubuntu"]="Ubuntu"
  ["fedora"]="Fedora"
  ["bazzite"]="Bazzite"
)

for os_id in "${!OS_NAMES[@]}"; do
  echo "==> Checking OS mapping for ${os_id}"
  output="$(
    VR_HOTSPOT_OS_ID="${os_id}" \
    VR_HOTSPOT_OS_NAME="${OS_NAMES[${os_id}]}" \
      "${ROOT_DIR}/install.sh" --check-os --no-clear
  )"
  printf '%s\n' "$output"

  if [[ "$os_id" == "bazzite" ]]; then
    dependency_line="$(printf '%s\n' "$output" | grep -F "Dependency plan for Bazzite:")"
    [[ "$output" == *"Detected Bazzite (rpm-ostree)."* ]]
    [[ "$dependency_line" == *"Dependency plan for Bazzite: python3 python3-pip iw iproute iptables"* ]]
    [[ "$dependency_line" != *" hostapd"* ]]
    [[ "$dependency_line" != *" dnsmasq"* ]]
    [[ "$output" == *"Bazzite support policy: supported through the rpm-ostree path with bundled hostapd/dnsmasq."* ]]
    [[ "$output" == *"reboot and rerun the installer"* ]]
  fi
done
