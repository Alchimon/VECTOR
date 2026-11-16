#!/usr/bin/env bash
# ВЕКТОР — запуск клиента тестового стенда. Адрес сервера и сертификат CA подставляются в форму входа.
# Клиенту нужен системный менеджер паролей (GNOME Keyring / KWallet): в нём хранится секрет устройства,
# которым защищён локальный кэш. Без него клиент откажется хранить данные офлайн (это намеренно).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export VECTOR_SERVER_URL="${VECTOR_SERVER_URL:-$(cat server-url.txt)}"
export VECTOR_CA_FILE="${VECTOR_CA_FILE:-$PWD/certs/ca.pem}"
exec ./venv/bin/vector-client
