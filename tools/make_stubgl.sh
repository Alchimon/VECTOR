#!/usr/bin/env bash
# Сборка stubgl — минимальных заглушек GL/EGL/xkbcommon/dbus для запуска клиентских и UI-тестов
# на Linux-машине без системных графических библиотек (песочница, CI-контейнер).
#
# Под offscreen-платформой и программным растеризатором Qt эти библиотеки только линкуются,
# их функции не вызываются — достаточно пустых разделяемых библиотек с нужными символами
# и версиями (libdbus-1.so.3 → LIBDBUS_1_3, libxkbcommon.so.0 → V_0.5.0; без них ld.so
# падает с «check_match: Assertion»).
#
# Запуск из корня репозитория после создания .venv:
#   bash tools/make_stubgl.sh
# Проверка: LD_LIBRARY_PATH=.venv/stubgl QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import PySide6.QtQuick"
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=${VENV:-.venv}
OUT="$VENV/stubgl"
QTLIBS="$VENV/lib/python3.11/site-packages/PySide6/Qt/lib"
[ -d "$QTLIBS" ] || { echo "PySide6 не найден в $VENV — сначала pip install -e .[client]"; exit 1; }
mkdir -p "$OUT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Собирает заглушку из списка неразрешённых символов Qt-библиотек с данным префиксом.
build_stub() {  # $1 = soname, $2 = regex префикса символов, $3 = version-node (или "-")
    local soname=$1 pat=$2 ver=$3
    nm -D --undefined-only "$QTLIBS"/*.so* 2>/dev/null \
        | awk -v p="$pat" '{n=$NF; sub(/@.*/,"",n); if (n ~ ("^" p)) print n}' | sort -u > "$TMP/syms.txt"
    local n; n=$(wc -l < "$TMP/syms.txt")
    [ "$n" -eq 0 ] && { echo "$soname: символов нет, пропуск"; return; }
    awk '{print "void " $1 "(void){}"}' "$TMP/syms.txt" > "$TMP/stub.c"
    local args=(-shared -fPIC "$TMP/stub.c" -Wl,-soname,"$soname")
    if [ "$ver" != "-" ]; then
        { echo "$ver {"; echo "  global:"; sed 's/^/    /; s/$/;/' "$TMP/syms.txt"; echo "  local: *;"; echo "};"; } > "$TMP/ver.map"
        args+=(-Wl,--version-script="$TMP/ver.map")
    fi
    gcc "${args[@]}" -o "$OUT/$soname"
    echo "$soname: $n символов (версия: $ver)"
}

build_stub libGL.so.1 gl -
build_stub libEGL.so.1 egl -
build_stub libxkbcommon.so.0 xkb V_0.5.0
build_stub libdbus-1.so.3 dbus_ LIBDBUS_1_3

LD_LIBRARY_PATH="$OUT" QT_QPA_PLATFORM=offscreen "$VENV/bin/python" -c \
    "import PySide6.QtWidgets, PySide6.QtQuick, PySide6.QtQml, PySide6.QtQuickControls2; print('stubgl: Qt OK')"
