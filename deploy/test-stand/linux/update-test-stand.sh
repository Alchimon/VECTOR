#!/usr/bin/env bash
# =============================================================================
# ВЕКТОР — обновление тестового стенда (Linux) до версии из ЭТОГО распакованного репозитория.
#
#   bash deploy/test-stand/linux/update-test-stand.sh [--dir ~/vector-test]
#
# Что делает (данные стенда сохраняются):
#   1) проверяет, что сервер стенда остановлен (порт из server.toml свободен);
#   2) ставит новую версию программы в venv стенда (те же ограничения версий, constraints.txt);
#   3) vector-server migrate: если схема БД старше — СНАЧАЛА зашифрованная копия текущей базы с полной проверкой
#      (backups/vector-premigrate-v<N>-<время>.vbak), затем обновление схемы одной транзакцией (всё или ничего);
#   4) vector-server check — сервер с этой базой запустится.
# Откат: восстановить копию vector-premigrate-* в пустую базу старой версией программы (docs/ops/backup-restore.md §6).
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DIR="$HOME/vector-test"
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }
[ -x "$DIR/venv/bin/vector-server" ] && [ -f "$DIR/server.toml" ] || die "В $DIR нет тестового стенда (venv/bin/vector-server, server.toml)."
cd "$DIR"
PORT="$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' server.toml | head -1)"
if [ -n "$PORT" ] && (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  die "Сервер стенда работает (порт $PORT занят). Остановите его (Ctrl+C в окне сервера) и запустите обновление снова."
fi
printf '\n== Установка новой версии программы из %s\n' "$REPO"
./venv/bin/python -m pip install --quiet --disable-pip-version-check -c "$REPO/deploy/test-stand/constraints.txt" "$REPO[server,client]" \
  || die "Не удалось установить программу (нужен доступ к pypi.org)."
printf '\n== Обновление схемы базы (с проверенной резервной копией)\n'
./venv/bin/vector-server migrate --config server.toml --key-file backup.key
printf '\n== Проверка\n'
./venv/bin/vector-server check --config server.toml
printf '\nГотово. Запустите сервер: %s/start-server.sh\n' "$DIR"
