#!/usr/bin/env bash
# =============================================================================
# ВЕКТОР — тестовый стенд на одном ПК (Linux): сервер + клиент + демо-данные.
#
# ТОЛЬКО ДЛЯ ТЕСТА: база SQLite, тестовый сертификат (30 дней), вымышленные данные.
# Для работы в компании — docs/ops/install.md (PostgreSQL / MS SQL, сертификат CA компании).
#
# Запуск из распакованного репозитория:
#   bash deploy/test-stand/linux/setup-test-stand.sh [--dir ~/vector-test] [--lan] [--no-demo]
#     --dir      каталог стенда (по умолчанию ~/vector-test); должен не существовать или быть пустым
#     --lan      сервер доступен с других ПК сети (иначе — только с этого ПК, 127.0.0.1)
#     --no-demo  без демо-данных (только администратор)
#     --port N   порт сервера (по умолчанию 8443)
#
# Пароли спрашиваются без эха (или берутся из VECTOR_ADMIN_PASSWORD / VECTOR_DEMO_PASSWORD) и
# никуда не записываются. Скрипт ничего не меняет вне каталога стенда, кроме кэша pip.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$REPO/deploy/test-stand/linux"
DIR="$HOME/vector-test"
LAN=0
DEMO=1
PORT=8443
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --lan) LAN=1; shift ;;
    --no-demo) DEMO=0; shift ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

# Шаг с паролем: до трёх попыток. Сервер проверяет пароль по политике ДО создания чего-либо и при отказе
# завершается с кодом 3 — тогда спрашиваем снова. Пароль передаётся через переменную окружения (не argv).
password_step() {   # <имя переменной> <приглашение> <текст ошибки> <команда...>
  local name="$1" prompt="$2" fail="$3" from_env=0 rc i P1 P2
  shift 3
  [ -n "${!name:-}" ] && from_env=1
  for i in 1 2 3; do
    if [ "$from_env" = 0 ]; then
      read -r -s -p "$prompt: " P1; echo
      read -r -s -p "Повторите пароль: " P2; echo
      if [ "$P1" != "$P2" ]; then echo "Пароли не совпадают — ещё раз."; continue; fi
      export "$name=$P1"; unset P1 P2
    fi
    set +e; "$@"; rc=$?; set -e
    [ "$rc" = 0 ] && return 0
    if [ "$rc" != 3 ] || [ "$from_env" = 1 ]; then die "$fail (код $rc). Подробности — в сообщениях выше."; fi
    echo "Придумайте другой пароль (требования — в сообщении выше)."
  done
  die "$fail: три попытки не удались. Удалите $DIR и запустите скрипт снова."
}
run_init() { (cd "$DIR" && "$VS" init --config server.toml); }
run_demo() { (cd "$DIR" && "$VS" demo-seed --config server.toml) | tee "$DIR/demo-accounts.txt"; }

# --- 1. Python 3.11–3.14 (PySide6 6.11 не поддерживает 3.15+) ---------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -n "$PY" ] || die "Нужен Python 3.11–3.14 (рекомендуется 3.12 или 3.13). Установите его и повторите."
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "В Python нет модуля venv/ensurepip. Debian/Ubuntu: sudo apt install python3-venv"
say "Python: $("$PY" --version) ($PY)"

# --- 2. Каталог стенда: новый или пустой (не трогаем чужие данные) ---------------------------------
if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  die "Каталог $DIR не пуст. Укажите другой (--dir) или удалите старый стенд."
fi
mkdir -p "$DIR"/{certs,data,backups}
DIR="$(cd "$DIR" && pwd)"
umask 077
say "Каталог стенда: $DIR"

# --- 3. Программа: отдельное окружение, версии зависимостей — те, на которых пройдены тесты ----------
say "Установка программы (несколько минут: PySide6 ~ 100 МБ)…"
"$PY" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/venv/bin/python" -m pip install --quiet -c "$REPO/deploy/test-stand/constraints.txt" "$REPO[server,client]"
VS="$DIR/venv/bin/vector-server"

# --- 4. Сертификат, строка подключения, ключ копий, конфигурация -----------------------------------
HOSTS=(--host localhost)
BIND=127.0.0.1
if [ "$LAN" = 1 ]; then
  BIND=0.0.0.0
  HOSTS+=(--host "$(hostname)")
  for ip in $(hostname -I 2>/dev/null); do case "$ip" in *:*) ;; *) HOSTS+=(--host "$ip") ;; esac; done
fi
(cd "$DIR" && "$VS" dev-cert --dir certs "${HOSTS[@]}")
printf 'sqlite:///%s\n' "$DIR/data/vector.sqlite" > "$DIR/db.url"
chmod 600 "$DIR/db.url"
(cd "$DIR" && "$VS" gen-backup-key backup.key >/dev/null)
sed -e "s|@BIND@|$BIND|" -e "s|@PORT@|$PORT|" "$REPO/deploy/test-stand/server.test.toml" > "$DIR/server.toml"
for f in start-server.sh start-client.sh backup.sh; do
  cp "$HERE/$f" "$DIR/$f"; chmod 700 "$DIR/$f"
done
printf 'https://localhost:%s\n' "$PORT" > "$DIR/server-url.txt"

# --- 5. База: схема + первый администратор; демо-данные ---------------------------------------------
say "Создание базы. Придумайте пароль администратора «admin» (не короче 12 символов, не из простых)."
password_step VECTOR_ADMIN_PASSWORD "Пароль администратора" "База не создана" run_init
if [ "$DEMO" = 1 ]; then
  say "Демо-данные (вымышленные). Пароль для всех демо-учёток — придумайте отдельный (не короче 12 символов)."
  password_step VECTOR_DEMO_PASSWORD "Пароль демо-учёток" "Демо-данные не созданы" run_demo
fi
unset VECTOR_ADMIN_PASSWORD VECTOR_DEMO_PASSWORD

say "Готово. Стенд: $DIR"
cat <<EOF
  1) Сервер:  $DIR/start-server.sh      (окно оставить открытым; остановка — Ctrl+C)
  2) Клиент:  $DIR/start-client.sh      (адрес и сертификат подставятся сами)
     Адрес сервера: https://localhost:$PORT   Сертификат CA: $DIR/certs/ca.pem
  3) Копия:   $DIR/backup.sh
  4) Самопроверка (при запущенном сервере, только чтение): bash $HERE/check-stand.sh --dir $DIR
$( [ "$LAN" = 1 ] && echo "  С других ПК: https://$(hostname):$PORT (откройте порт $PORT в межсетевом экране; на ПК клиента нужен certs/ca.pem)" )
Инструкция и сценарии проверки: docs/ops/test-pc.md
EOF
