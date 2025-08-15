#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run as root: sudo bash scripts/install.sh"; exit 1; fi
apt-get update -y
apt-get install -y python3-venv python3-pip git ufw jq sqlite3

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
SECRET_KEY=$(python3 - <<'PY'
import secrets; print(secrets.token_hex(32))
PY
)

cat > "${ENV_FILE}" <<EOF
UVICORN_HOST=0.0.0.0
UVICORN_PORT=${UVICORN_PORT}
MYST_MANAGER_DB=${DB_PATH}
ADMIN_USER=${ADMIN_USER}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
SECRET_KEY=${SECRET_KEY}
EOF

cat >/etc/systemd/system/myst-manager.service <<'EOF'
[Unit]
Description=MysteriumNET Management Web Service
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/myst-manager/app
EnvironmentFile=/opt/myst-manager/.env
ExecStart=/opt/myst-manager/.venv/bin/python /opt/myst-manager/app/main.py
Restart=on-failure
User=root
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now myst-manager
ufw allow ${UVICORN_PORT}/tcp || true
echo "Done. Panel: http://<server_ip>:${UVICORN_PORT} (user: ${ADMIN_USER})"
