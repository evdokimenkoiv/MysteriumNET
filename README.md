# MysteriumNET — Manager & Node Deployer

FastAPI-панель для управления узлами Mysterium: деплой, мониторинг, ACL для SSH/панели, хранилище кошельков, базовый дашборд и базовая авторизация.

## Возможности
- Добавление узлов и удалённый деплой (`mysteriumnetwork/myst`), сбор метрик, удаление из списка.
- **Wallet Vault**: хранение нескольких `Polygon (POL)`-адресов; выбор payout для каждой ноды (или указание кастомного адреса).
- **ACL Management**: белые списки IP/CIDR для портов 22/8080/80/443 на машине управления.
- **Dashboard**: быстрые показатели (Total/Myst running), кнопка **Collect All**.
- **Auth**: HTTP Basic (логин/пароль задаются при установке).
- **Скрипты**: `scripts/install.sh` (установка) и `scripts/uninstall.sh` (удаление). Опция: развернуть **локальную** Myst-ноду.

## Быстрый старт
```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/evdokimenkoiv/MysteriumNET.git
cd MysteriumNET/scripts
sudo bash install.sh
# Панель: http://<IP>:8080 (логин/пароль из установки)
```

## Структура
```
MysteriumNET/
 ├─ app/
 │   ├─ app.py
 │   ├─ templates/
 │   │   └─ index.html
 │   └─ static/
 │       └─ styles.css
 ├─ scripts/
 │   ├─ install.sh
 │   ├─ uninstall.sh
 │   └─ remote_install.sh
 ├─ requirements.txt
 ├─ .env.example
 ├─ LICENSE
 └─ README.md
```

## Требования к нодам
Ubuntu 22.04/24.04, 1–2 vCPU, 1–2 GB RAM, 10+ GB SSD, публичный IPv4, открыт UDP-порт WG.

## Безопасность
Панель защищена Basic Auth, но **обязательно ограничивай доступ** через ACL (и/или VPN). Никогда не публикуй `.env`.
