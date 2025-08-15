#!/usr/bin/env bash
set -euo pipefail
NON_INTERACTIVE=false
if [[ "${1:-}" == "--non-interactive" ]]; then NON_INTERACTIVE=true; fi
export DEBIAN_FRONTEND=noninteractive
MGMT_IP="${MGMT_IP:-}"
PAYOUT_ADDRESS="${PAYOUT_ADDRESS:-}"
WG_PORT="${WG_PORT:-51820}"
apt-get update -y
apt-get install -y ca-certificates curl gnupg jq ufw vnstat
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
cat >/etc/sysctl.d/99-myst-tuning.conf <<'EOF'
net.core.somaxconn = 4096
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.ipv4.ip_local_port_range = 10000 65000
net.ipv4.conf.all.rp_filter = 2
EOF
sysctl --system
if ! ufw status | grep -q "Status: active"; then
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow OpenSSH
  ufw allow ${WG_PORT}/udp
  yes | ufw enable
else
  ufw allow ${WG_PORT}/udp || true
fi
if [[ -n "${MGMT_IP}" ]]; then
  ufw delete allow OpenSSH || true
  ufw allow from ${MGMT_IP} to any port 22 proto tcp
  ufw reload
fi
mkdir -p /opt/myst/{data,logs}
cat >/opt/myst/myst.env <<EOF
LOG_LEVEL=info
MYST_TEQUILA_API_PORT=4050
WIREGUARD_PORT=${WG_PORT}
PAYOUT_ADDRESS=${PAYOUT_ADDRESS}
EOF
cat >/opt/myst/docker-compose.yml <<'EOF'
version: "3.8"
services:
  myst:
    image: mysteriumnetwork/myst:latest
    container_name: myst-node
    network_mode: "host"
    restart: unless-stopped
    env_file:
      - /opt/myst/myst.env
    volumes:
      - /opt/myst/data:/var/lib/mysterium-node
      - /opt/myst/logs:/var/log/mysterium
EOF
cd /opt/myst && docker compose up -d
