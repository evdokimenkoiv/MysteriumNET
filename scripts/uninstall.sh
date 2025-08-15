#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run as root: sudo bash scripts/uninstall.sh"; exit 1; fi
systemctl disable --now myst-manager || true
rm -f /etc/systemd/system/myst-manager.service
systemctl daemon-reload
read -rp "Remove /opt/myst-manager (data/.env)? [y/N]: " RM; RM=${RM:-N}
if [[ "${RM}" =~ ^[Yy]$ ]]; then rm -rf /opt/myst-manager; fi
echo "Uninstalled."
