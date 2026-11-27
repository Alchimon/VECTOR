#!/usr/bin/env bash
# Сборка установщика тестового стенда ВЕКТОР (deploy/installer/vector-test-stand.nsi) в один .exe.
# Нужен makensis (NSIS 3.x): Linux — пакет nsis дистрибутива или сборка из исходников;
# Windows — официальный дистрибутив NSIS. Путь можно задать: MAKENSIS=/path/to/makensis ./build-installer.sh
# Результат: dist/VECTOR-test-stand-setup-<версия>.exe в корне репозитория.
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION="$(grep -m1 '^version = ' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')"
# Для VIProductVersion нужны только цифры: 0.9.0rc1 -> 0.9.0.0
NUM="$(printf '%s' "$VERSION" | sed -E 's/[^0-9.].*$//' | awk -F. '{printf "%s.%s.%s.%s", $1, $2, ($3==""?0:$3), ($4==""?0:$4)}')"
OUT="dist/VECTOR-test-stand-setup-${VERSION}.exe"
mkdir -p dist

MAKENSIS="${MAKENSIS:-makensis}"
"$MAKENSIS" -DPRODUCT_VERSION="$VERSION" -DPRODUCT_VERSION_NUM="$NUM" -DOUTFILE="$PWD/$OUT" \
    -DREPO_ROOT="$PWD" deploy/installer/vector-test-stand.nsi
echo "== Готово: $OUT"
