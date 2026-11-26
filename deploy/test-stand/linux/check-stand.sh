#!/usr/bin/env bash
# ВЕКТОР — самопроверка тестового стенда (только чтение): сервер отвечает, сертификат принят, демо-учётки входят,
# демо-данные для сценариев на месте (в т. ч. D-35). Сервер стенда должен быть запущен.
#   bash deploy/test-stand/linux/check-stand.sh [--dir ~/vector-test]
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DIR="$HOME/vector-test"
[ "${1:-}" = "--dir" ] && DIR="$2"
[ -x "$DIR/venv/bin/python" ] || { echo "Стенд не найден в $DIR (нет venv). Укажите: --dir <каталог стенда>" >&2; exit 2; }
exec "$DIR/venv/bin/python" "$REPO/deploy/test-stand/stand_check.py" --dir "$DIR"
