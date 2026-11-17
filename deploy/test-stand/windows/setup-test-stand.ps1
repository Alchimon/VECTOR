<#
=============================================================================
 ВЕКТОР — тестовый стенд на ПК с Windows 10/11 (x64): сервер + клиент + демо-данные.

 ТОЛЬКО ДЛЯ ТЕСТА: база SQLite, тестовый сертификат (30 дней), вымышленные данные.
 Для работы в компании — docs\ops\install.md (PostgreSQL / MS SQL, сертификат CA компании).

 Обычно запускается двойным щелчком по setup.cmd (рядом с этим файлом). Параметры:
   -Dir <путь>      каталог стенда (по умолчанию %USERPROFILE%\VECTOR-test); новый или пустой
   -Lan             сервер доступен с других ПК сети (иначе — только с этого ПК)
   -NoDemo          без демо-данных (только администратор)
   -Port <N>        порт сервера (по умолчанию 8443)
   -ClientOnly      только клиент — для других ПК сети при тесте с -Lan:
                    -Server https://ИМЯ-ПК:8443 -CaFile <путь к ca.pem с ПК сервера>
   -NoShortcuts     не создавать ярлыки на рабочем столе

 Пароли спрашиваются без отображения и никуда не записываются. Скрипт не требует прав
 администратора и ничего не меняет вне каталога стенда, кроме ярлыков на рабочем столе
 (и правила межсетевого экрана — только с -Lan и только если запущен от администратора).
=============================================================================
#>
[CmdletBinding()]
param(
    [string]$Dir = (Join-Path $env:USERPROFILE "VECTOR-test"),
    [switch]$Lan,
    [switch]$NoDemo,
    [int]$Port = 8443,
    [switch]$ClientOnly,
    [string]$Server = "",
    [string]$CaFile = "",
    [switch]$NoShortcuts
)

$ErrorActionPreference = "Stop"
# Кириллица в консоли: вывод PowerShell и Python — в UTF-8.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Templates = $PSScriptRoot

# Заголовок очередного шага установки (голубым цветом).
function Say([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
# Сообщить об ошибке понятным текстом (красным) и завершить скрипт с кодом 1. Переменные окружения с паролями
# живут только в этом процессе PowerShell и исчезают вместе с ним.
function Fail([string]$text) {
    Write-Host ""
    Write-Host "ОШИБКА: $text" -ForegroundColor Red
    exit 1
}
# Внешние программы не бросают исключений — проверяем код возврата явно. Внутри Run и Probe режим ошибок
# «Continue»: в PowerShell 5.1 вывод внешней программы в stderr (предупреждения pip и т. п.) при режиме «Stop»
# превращается в фатальную ошибку, хотя программа отработала успешно. Решает только код возврата.
function Run([string]$what, [scriptblock]$block) {
    $ErrorActionPreference = "Continue"
    & $block
    if ($LASTEXITCODE -ne 0) { Fail "$what (код $LASTEXITCODE). Подробности — в сообщениях выше." }
}
# Пробный запуск: $true, если команда завершилась с кодом 0; сообщения об ошибках скрываются.
function Probe([scriptblock]$block) {
    $ErrorActionPreference = "Continue"
    try { & $block 2>&1 | Out-Null } catch { return $false }
    return ($LASTEXITCODE -eq 0)
}
# Запросить пароль без отображения на экране; возвращает обычную строку только для передачи дочернему процессу.
function Read-Secret([string]$prompt) {
    $s = Read-Host -AsSecureString $prompt
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
# Файл с секретом — доступ только текущему пользователю (снимаем унаследованные права).
function Protect-File([string]$path) {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $path /inheritance:r /grant:r "*${sid}:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось ограничить права на файл $path" }
}
# UTF-8 без BOM: путь может содержать кириллицу (имя пользователя Windows), а BOM сломал бы чтение в cmd/Python.
# Шаг с паролем: до трёх попыток. Сервер проверяет пароль по политике ДО создания чего-либо и при отказе
# завершается с кодом 3 — тогда спрашиваем снова. Пароль передаётся дочернему процессу через переменную окружения
# этого процесса (не через командную строку, где его видно в списке процессов) и удаляется в конце скрипта.
function Invoke-PasswordStep([string]$envName, [string]$prompt, [string]$failText, [scriptblock]$block) {
    $fromEnv = [bool][Environment]::GetEnvironmentVariable($envName, "Process")
    for ($i = 1; $i -le 3; $i++) {
        if (-not $fromEnv) {
            $p1 = Read-Secret $prompt
            $p2 = Read-Secret "Повторите пароль"
            if ($p1 -ne $p2) { Write-Host "Пароли не совпадают — ещё раз." -ForegroundColor Yellow; continue }
            [Environment]::SetEnvironmentVariable($envName, $p1, "Process")
            Remove-Variable p1, p2
        }
        $ErrorActionPreference = "Continue"
        & $block
        $rc = $LASTEXITCODE
        if ($rc -eq 0) { return }
        if ($rc -ne 3 -or $fromEnv) { Fail "${failText} (код $rc). Подробности — в сообщениях выше." }
        Write-Host "Придумайте другой пароль (требования — в сообщении выше)." -ForegroundColor Yellow
    }
    Fail "${failText}: три попытки не удались. Запустите setup.cmd снова, предварительно удалив каталог стенда."
}
# Записать текстовый файл в UTF-8 без BOM (см. пояснение выше).
function Write-Utf8([string]$path, [string]$text) {
    [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}
# Ярлык на рабочем столе пользователя: name — подпись, target — запускаемый .cmd, workdir — рабочий каталог.
function New-Shortcut([string]$name, [string]$target, [string]$workdir) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut((Join-Path $desktop "$name.lnk"))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $workdir
    $lnk.Save()
}

# --- 1. Python 3.11–3.14 x64 (PySide6 6.11 не поддерживает 3.15+) ------------------------------------
Say "Поиск Python 3.11–3.14 (64-бит)"
$check = "import sys, struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) and struct.calcsize('P') == 8 else 1)"
$Py = $null
foreach ($v in @("3.13", "3.12", "3.11", "3.14")) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        if (Probe { & py.exe "-$v" -c $check }) { $Py = @("py.exe", "-$v"); break }
    }
}
if (-not $Py) {
    # «python» из Microsoft Store-заглушки не запускается (код 9009) — такой вариант просто пропускаем.
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Probe { & $cmd.Source -c $check })) { $Py = @($cmd.Source) }
}
if (-not $Py) {
    Fail ("Не найден Python 3.11–3.14 (64-бит). Установите Python 3.12 или 3.13 с https://www.python.org/downloads/windows/ " +
          "(«Windows installer (64-bit)», отметьте «Add python.exe to PATH») и запустите setup.cmd снова.")
}
$PyExe = $Py[0]
$PyArgs = @()
if ($Py.Count -gt 1) { $PyArgs = @($Py[1..($Py.Count - 1)]) }   # всегда массив: @PyArgs со строкой ненадёжен
$ver = & $PyExe @PyArgs --version
Write-Host "Python: $ver"

# --- 2. Каталог стенда: новый или пустой (чужие данные не трогаем) -----------------------------------
if ((Test-Path $Dir) -and (Get-ChildItem -Force $Dir | Select-Object -First 1)) {
    Fail "Каталог $Dir не пуст. Укажите другой (-Dir) или удалите старый стенд."
}
New-Item -ItemType Directory -Force -Path $Dir, "$Dir\certs", "$Dir\data", "$Dir\backups" | Out-Null
$Dir = (Resolve-Path $Dir).Path
Say "Каталог стенда: $Dir"

# --- 3. Программа: отдельное окружение; версии зависимостей — те, на которых пройдены тесты ----------
Say "Установка программы (несколько минут: PySide6 ~ 100 МБ)"
Run "Не удалось создать окружение Python" { & $PyExe @PyArgs -m venv "$Dir\venv" }
$VPy = "$Dir\venv\Scripts\python.exe"
Run "Не удалось обновить pip" { & $VPy -m pip install --quiet --disable-pip-version-check --upgrade pip }
$extras = "server,client"
if ($ClientOnly) { $extras = "client" }
Run "Не удалось установить программу (нужен доступ в интернет к pypi.org)" {
    & $VPy -m pip install --quiet --disable-pip-version-check -c "$Repo\deploy\test-stand\constraints.txt" "${Repo}[$extras]"
}
Copy-Item "$Templates\start-client.cmd" "$Dir\start-client.cmd"

# --- Только клиент (другой ПК сети) ----------------------------------------------------------------
if ($ClientOnly) {
    if (-not $Server -or -not $Server.StartsWith("https://")) { Fail "Укажите адрес сервера: -Server https://ИМЯ-ПК:8443" }
    if (-not $CaFile -or -not (Test-Path $CaFile)) { Fail "Укажите файл сертификата CA с ПК сервера: -CaFile <путь>\ca.pem" }
    Copy-Item $CaFile "$Dir\certs\ca.pem"
    Write-Utf8 "$Dir\server-url.txt" $Server
    if (-not $NoShortcuts) { New-Shortcut "ВЕКТОР — клиент (тест)" "$Dir\start-client.cmd" $Dir }
    Say "Готово. Клиент: $Dir\start-client.cmd   Сервер: $Server"
    exit 0
}

# --- 4. Сертификат, строка подключения, ключ копий, конфигурация -----------------------------------
$VS = "$Dir\venv\Scripts\vector-server.exe"
$hosts = @("--host", "localhost")
$bind = "127.0.0.1"
if ($Lan) {
    $bind = "0.0.0.0"
    $hosts += @("--host", $env:COMPUTERNAME)
    try {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
            ForEach-Object { $script:hosts += @("--host", $_.IPAddress) }
    } catch { Write-Host "IP-адреса не определены — сертификат будет только для имени $env:COMPUTERNAME." }
}
Push-Location $Dir
try {
    Run "Не удалось создать тестовый сертификат" { & $VS dev-cert --dir certs @hosts }
    Protect-File "$Dir\certs\server.key"
    Write-Utf8 "$Dir\db.url" "sqlite:///$($Dir.Replace('\', '/'))/data/vector.sqlite"
    Protect-File "$Dir\db.url"
    Run "Не удалось создать ключ резервных копий" { & $VS gen-backup-key backup.key | Out-Null }
    Protect-File "$Dir\backup.key"
    $toml = (Get-Content -Raw -Encoding UTF8 "$Repo\deploy\test-stand\server.test.toml").Replace("@BIND@", $bind).Replace("@PORT@", "$Port")
    [IO.File]::WriteAllText("$Dir\server.toml", $toml, (New-Object System.Text.UTF8Encoding $false))
    Copy-Item "$Templates\start-server.cmd", "$Templates\backup.cmd" $Dir
    Write-Utf8 "$Dir\server-url.txt" "https://localhost:$Port"

    # --- 5. База: схема + первый администратор; демо-данные -----------------------------------------
    Say "Создание базы. Придумайте пароль администратора «admin» (не короче 12 символов, не из простых)"
    Invoke-PasswordStep "VECTOR_ADMIN_PASSWORD" "Пароль администратора" "База не создана" {
        & $VS init --config server.toml
    }
    if (-not $NoDemo) {
        Say "Демо-данные (вымышленные). Придумайте отдельный пароль для всех демо-учёток (не короче 12 символов)"
        Invoke-PasswordStep "VECTOR_DEMO_PASSWORD" "Пароль демо-учёток" "Демо-данные не созданы" {
            $script:demoOut = & $VS demo-seed --config server.toml
            $script:demoOut | ForEach-Object { Write-Host $_ }
        }
        [IO.File]::WriteAllLines("$Dir\demo-accounts.txt", [string[]]$script:demoOut, (New-Object System.Text.UTF8Encoding $true))
    }
} finally {
    Remove-Item Env:VECTOR_ADMIN_PASSWORD, Env:VECTOR_DEMO_PASSWORD -ErrorAction SilentlyContinue
    Pop-Location
}

# --- 6. Сеть (только -Lan) и ярлыки --------------------------------------------------------------------
if ($Lan) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($admin) {
        New-NetFirewallRule -DisplayName "VECTOR test server $Port" -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Action Allow -Profile Private, Domain | Out-Null
        Write-Host "Открыт входящий порт $Port (частная и доменная сеть)."
    } else {
        Write-Host ("Чтобы другие ПК подключались, откройте порт ${Port}: PowerShell от администратора —`n" +
                    "  New-NetFirewallRule -DisplayName 'VECTOR test server $Port' -Direction Inbound -Protocol TCP " +
                    "-LocalPort $Port -Action Allow -Profile Private,Domain") -ForegroundColor Yellow
    }
}
if (-not $NoShortcuts) {
    New-Shortcut "ВЕКТОР — сервер (тест)" "$Dir\start-server.cmd" $Dir
    New-Shortcut "ВЕКТОР — клиент (тест)" "$Dir\start-client.cmd" $Dir
}

Say "Готово. Стенд: $Dir"
Write-Host "  1) Сервер: ярлык «ВЕКТОР — сервер (тест)» или $Dir\start-server.cmd (окно не закрывать)"
Write-Host "  2) Клиент: ярлык «ВЕКТОР — клиент (тест)» или $Dir\start-client.cmd (адрес и сертификат подставятся сами)"
Write-Host "     Адрес сервера: https://localhost:$Port    Сертификат CA: $Dir\certs\ca.pem"
Write-Host "  3) Резервная копия: $Dir\backup.cmd"
if ($Lan) {
    Write-Host "  С других ПК: https://$($env:COMPUTERNAME):$Port — на них запустите setup.cmd -ClientOnly (см. инструкцию)"
}
Write-Host "Инструкция и сценарии проверки: docs\ops\test-pc.md"
