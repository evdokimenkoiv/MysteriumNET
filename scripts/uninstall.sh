#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now myst-manager || true
rm -f /etc/systemd/system/myst-manager.service
systemctl daemon-reload
echo 'Uninstalled.'
