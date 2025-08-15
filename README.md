# MysteriumNET v3

Полноценная админ‑панель для развёртывания и управления нодами **Mysterium** (WireGuard provider).

## Разделы
- **Nodes** — добавление/деплой/сбор статуса/удаление, сортировка, оценка дохода, NAT, Utilization.
- **Wallets** — хранилище Polygon‑адресов с метками.
- **Server** — UFW ACL (allowlist), TLS (Let's Encrypt), Diagnostics (Prometheus /metrics, Backup DB).
- **Settings** — hostname/email для TLS, USD per GB, Telegram placeholders.

## Возможности
- Deploy нод по SSH (password/SSH key), кастомные WG/API порты, авто‑установка Docker.
- Collect метрик: docker, vnstat (Avg Mbps), TequilAPI (health, sessions, NAT).
- Кнопка Deploy неактивна, если нода уже работает.
- Импорт CSV/JSON, экспорт, Backup DB, Prometheus /metrics.
- Генератор скрипта TLS (nginx + certbot, prod).
- UFW allowlist из панели с автоматическим добавлением текущего IP для SSH и порта панели.

## Установка
```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/evdokimenkoiv/MysteriumNET.git
cd MysteriumNET/scripts
sudo bash install.sh
```
Инсталлятор может развернуть локальную ноду и/или включить TLS (если введён FQDN).

## Развёртывание нод
1) Добавьте ноду в **Nodes → Add node**. 2) Нажмите **Deploy**. 3) Нажмите **Collect**.

## CLI проверки
```bash
docker ps | grep myst-node
docker logs --tail=100 myst-node
ss -lunp | grep -E ':(51820|4050)\b'
curl -s http://127.0.0.1:<API_PORT>/tequilapi/health | jq .
curl -s http://127.0.0.1:<API_PORT>/tequilapi/nat/type | jq .
docker exec -it myst-node myst cli
```

## Troubleshooting
Проверьте NAT/порты/логи; при необходимости используйте альтернативные порты при добавлении ноды.

## Лицензия
MIT.
