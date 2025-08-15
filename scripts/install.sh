#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run as root: sudo bash scripts/install.sh"; exit 1; fi
apt-get update -y
apt-get install -y python3-venv python3-pip git ufw jq

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/myst-manager"
ENV_FILE="${APP_DIR}/.env"
DB_PATH="${APP_DIR}/manager.db"

mkdir -p "${APP_DIR}"
cp -r "${REPO_DIR}/app" "${APP_DIR}/"
cp -r "${REPO_DIR}/scripts/remote_install.sh" "${APP_DIR}/"
cp "${REPO_DIR}/requirements.txt" "${APP_DIR}/"
chmod +x "${APP_DIR}/remote_install.sh"

python3 -m venv "${APP_DIR}/.venv"
source "${APP_DIR}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/requirements.txt"

echo "== MysteriumNET setup =="
read -rp "Admin username [admin]: " ADMIN_USER; ADMIN_USER=${ADMIN_USER:-admin}
read -rsp "Admin password: " ADMIN_PASSWORD; echo
read -rp "Uvicorn port [8080]: " UVICORN_PORT; UVICORN_PORT=${UVICORN_PORT:-8080}

cat > "${ENV_FILE}" <<EOF
UVICORN_HOST=0.0.0.0
UVICORN_PORT=${UVICORN_PORT}
MYST_MANAGER_DB=${DB_PATH}
ADMIN_USER=${ADMIN_USER}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF

cat >/etc/systemd/system/myst-manager.service <<'EOF'
[Unit]
Description=MysteriumNET Management Web Service
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/myst-manager/app
EnvironmentFile=/opt/myst-manager/.env
ExecStart=/opt/myst-manager/.venv/bin/python /opt/myst-manager/app/app.py
Restart=on-failure
User=root
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now myst-manager
ufw allow ${UVICORN_PORT}/tcp || true

echo "== Optional: local Myst node =="
read -rp "Install local Myst node on this server? [y/N]: " LOCAL_NODE; LOCAL_NODE=${LOCAL_NODE:-N}
if [[ "${LOCAL_NODE}" =~ ^[Yy]$ ]]; then
  read -rp "WireGuard UDP port [51820]: " WG_PORT; WG_PORT=${WG_PORT:-51820}
  read -rp "Payout address (Polygon 0x...): " PAYOUT_ADDRESS
  read -rp "Admin public IP (for SSH allowlist): " MGMT_IP
  if [[ -n "${MGMT_IP}" ]]; then
    echo "Installing Myst node locally..."
    pushd /opt/myst-manager >/dev/null
    sudo MGMT_IP="${MGMT_IP}" WG_PORT="${WG_PORT}" PAYOUT_ADDRESS="${PAYOUT_ADDRESS}"       bash /opt/myst-manager/remote_install.sh --non-interactive
    popd >/dev/null
  else
    echo "Skipped local node install (no admin IP provided)."
  fi
fi

echo "Done. Open http://<server_ip>:${UVICORN_PORT} (user: ${ADMIN_USER})"
