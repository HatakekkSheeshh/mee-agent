#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# NVIDIA driver install for nhihb-gpu-2080 — NO-REBOOT approach.
#
# ⚠️  DO NOT RUN until the mentor / IT confirms it's OK to install the driver
#     on this company VM. This script is prepared, not executed.
#
# Context (verified 2026-06-15):
#   • KVM VM, Ubuntu 22.04.5, kernel 5.15.0-177-generic
#   • RTX 2080 Ti passed through (PCI 0x10de:0x1e04), but NO NVIDIA driver
#   • nouveau loaded (refcount 0 → unloadable), cirrus drives the console
#   • Secure Boot OFF (UEFI, no SecureBoot efivar) → unsigned modules load fine
#   • Passwordless sudo available; NO physical/console access (SSH only)
#
# Why no-reboot: we only have SSH. A botched reboot could lock us out with no
# console to recover. Installing + modprobe-ing live keeps the VM up the whole
# time; if a step fails, SSH still works (cirrus owns the console, not the 2080).
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

DRIVER_VERSION="${DRIVER_VERSION:-535}"
KERNEL="$(uname -r)"

echo "==> Target driver: nvidia-driver-${DRIVER_VERSION}-server  | kernel: ${KERNEL}"
echo "==> This will: install headers + DKMS driver, blacklist nouveau, modprobe nvidia."
read -r -p "Proceed? (yes/NO) " ans
[ "$ans" = "yes" ] || { echo "Aborted."; exit 1; }

echo "==> [1/5] apt update + system deps"
sudo apt-get update
sudo apt-get install -y \
    "linux-headers-${KERNEL}" build-essential \
    pciutils ffmpeg tmux python3-venv python3-pip

echo "==> [2/5] install NVIDIA driver (server/headless variant, DKMS)"
sudo apt-get install -y "nvidia-driver-${DRIVER_VERSION}-server"

echo "==> [3/5] blacklist nouveau"
echo -e "blacklist nouveau\noptions nouveau modeset=0" | \
    sudo tee /etc/modprobe.d/blacklist-nouveau.conf >/dev/null

echo "==> [4/5] swap nouveau → nvidia live (no reboot)"
# nouveau has refcount 0, safe to remove. If this fails (something grabbed it),
# STOP — do not reboot blindly; debug over SSH first.
sudo modprobe -r nouveau || {
    echo "!! could not unload nouveau — investigate before rebooting (no console!)"; exit 1;
}
sudo modprobe nvidia || {
    echo "!! modprobe nvidia failed — VM is still up via SSH. Check: dmesg | tail -40"; exit 1;
}
sudo modprobe nvidia_uvm nvidia_modeset || true

echo "==> [5/5] verify"
nvidia-smi || { echo "!! nvidia-smi failed despite module load — check dmesg"; exit 1; }

echo
echo "✓ Driver installed and loaded WITHOUT reboot."
echo "  If the VM is ever rebooted by the host, the driver auto-loads (nouveau is"
echo "  blacklisted). SSH still comes up either way (console = cirrus)."
