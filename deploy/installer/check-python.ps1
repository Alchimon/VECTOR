# Проверка установщика: найден ли Python 3.11–3.14 (64-бит) — та же логика, что в setup-test-stand.ps1.
# Код возврата 0 — подходящий Python есть (версия выводится в stdout); 1 — нет.
$check = "import sys, struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) and struct.calcsize('P') == 8 else 1)"
if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.13", "3.12", "3.11", "3.14")) {
        & py.exe "-$v" -c $check 2>$null
        if ($LASTEXITCODE -eq 0) { & py.exe "-$v" --version; exit 0 }
    }
}
$cmd = Get-Command python.exe -ErrorAction SilentlyContinue
if ($cmd) {
    & $cmd.Source -c $check 2>$null
    if ($LASTEXITCODE -eq 0) { & $cmd.Source --version; exit 0 }
}
exit 1
