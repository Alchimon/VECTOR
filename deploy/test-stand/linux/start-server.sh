#!/usr/bin/env bash
# ВЕКТОР — запуск сервера тестового стенда. Окно оставить открытым; остановка — Ctrl+C.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
./venv/bin/vector-server check --config server.toml
echo "Сервер запускается: $(cat server-url.txt)  (остановка — Ctrl+C)"
exec ./venv/bin/vector-server serve --config server.toml
