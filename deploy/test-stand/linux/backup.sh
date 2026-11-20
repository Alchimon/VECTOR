#!/usr/bin/env bash
# ВЕКТОР — зашифрованная резервная копия базы тестового стенда (с проверкой и ротацией: 14 последних).
# Копия: backups/vector-<время>.vbak; ключ: backup.key (без ключа копия бесполезна — храните отдельно).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./venv/bin/vector-server backup --config server.toml --key-file backup.key
