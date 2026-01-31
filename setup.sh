#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="second-brain-bot"
SUDO=""

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "❌ Нужны права root или sudo"
    exit 1
  fi
fi

if [ ! -f "$APP_DIR/.env" ]; then
  echo "❌ Файл .env не найден в $APP_DIR"
  exit 1
fi

if [ -f "$APP_DIR/service_account.json.json" ] && [ ! -f "$APP_DIR/service_account.json" ]; then
  mv "$APP_DIR/service_account.json.json" "$APP_DIR/service_account.json"
fi

if [ ! -f "$APP_DIR/service_account.json" ]; then
  echo "❌ Файл service_account.json не найден в $APP_DIR"
  exit 1
fi

if command -v apt >/dev/null 2>&1; then
  echo "Устанавливаю python3 и venv (если нужно)..."
  $SUDO apt update -y
  $SUDO apt install -y python3 python3-venv python3.12-venv || true
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 не найден"
  exit 1
fi

if [ -d "$APP_DIR/.venv" ]; then
  rm -rf "$APP_DIR/.venv"
fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install aiogram openai gspread python-dotenv

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

$SUDO tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Second Brain Telegram Bot
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE_NAME"

echo "✅ Готово. Статус:"
$SUDO systemctl status --no-pager "$SERVICE_NAME"
echo "Логи: journalctl -u $SERVICE_NAME -f"
