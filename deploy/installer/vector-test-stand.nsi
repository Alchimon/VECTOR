; ============================================================================
;  ВЕКТОР — установщик ТЕСТОВОГО стенда (сервер + клиент + демо-данные).
;  Один .exe: распаковывает программу во временный каталог и запускает
;  сценарий deploy/test-stand/windows/setup-test-stand.ps1 (venv, сертификат,
;  SQLite, демо-данные, ярлыки). ТОЛЬКО ДЛЯ ТЕСТА. См. docs/ops/test-pc.md.
;
;  Сборка: deploy/installer/build-installer.sh (Linux) / .ps1 (Windows, NSIS 3).
; ============================================================================
Unicode true
ManifestDPIAware true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.9.0rc1"
!endif
!ifndef PRODUCT_VERSION_NUM
  !define PRODUCT_VERSION_NUM "0.9.0.0"
!endif
!ifndef OUTFILE
  !define OUTFILE "VECTOR-test-stand-setup.exe"
!endif
; Корень репозитория: makensis разрешает пути от каталога скрипта, поэтому
; build-installer.sh передаёт абсолютный -DREPO_ROOT.
!ifndef REPO_ROOT
  !define REPO_ROOT "../.."
!endif

Name "ВЕКТОР — тестовый стенд ${PRODUCT_VERSION}"
OutFile "${OUTFILE}"
InstallDir "$PROFILE\VECTOR-test"
RequestExecutionLevel user            ; администратор не нужен (кроме -Lan firewall, решает ps1)
SetCompressor /SOLID lzma
ShowInstDetails show                  ; прогресс pip виден сразу
BrandingText "ВЕКТОР · тестовый стенд (только для проверки)"
Icon "${REPO_ROOT}/src/vector/client/assets/vektor/app_icons/ico/vektor_platform.ico"
UninstallIcon "${REPO_ROOT}/src/vector/client/assets/vektor/app_icons/ico/vektor_platform.ico"

VIProductVersion "${PRODUCT_VERSION_NUM}"
VIAddVersionKey "FileDescription" "Установщик тестового стенда ВЕКТОР (сервер + клиент + демо-данные)"
VIAddVersionKey "ProductName" "ВЕКТОР — тестовый стенд"
VIAddVersionKey "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "LegalCopyright" "Вымышленные данные, не для работы в компании"

; ---------- страницы ----------
!define MUI_ICON "${REPO_ROOT}/src/vector/client/assets/vektor/app_icons/ico/vektor_platform.ico"
!define MUI_WELCOMEPAGE_TITLE "ВЕКТОР — тестовый стенд"
!define MUI_WELCOMEPAGE_TEXT "Будут развёрнуты сервер и клиент ВЕКТОР с вымышленными демо-данными: отдельное окружение Python, база SQLite, тестовый сертификат (30 дней), ярлыки запуска.$\r$\n$\r$\nНужны: Windows 10/11 (64-бит), Python 3.11–3.14 (64-бит) с python.org, интернет для загрузки библиотек (~150 МБ). Права администратора не требуются.$\r$\n$\r$\nТолько для теста — не для работы в компании."
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirLeave
!insertmacro MUI_PAGE_DIRECTORY
Page custom PasswordsPageCreate PasswordsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Стенд готов"
!define MUI_FINISHPAGE_TEXT "Сервер и клиент установлены в $INSTDIR.$\r$\nЯрлыки «ВЕКТОР — сервер (тест)» и «ВЕКТОР — клиент (тест)» — на рабочем столе. Учётные записи — в demo-accounts.txt (пароли — те, что вы задали).$\r$\n$\r$\nИнструкция и сценарии проверки: docs/ops/test-pc.md в репозитории."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Запустить сервер и клиент сейчас"
!define MUI_FINISHPAGE_RUN_FUNCTION FinishRun
!define MUI_FINISHPAGE_RUN_CHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "Russian"

; ---------- переменные страницы паролей ----------
Var Dialog
Var AdminPwdH
Var AdminPwd2H
Var DemoCheckH
Var DemoPwdLblH
Var DemoPwdH
Var DemoPwd2LblH
Var DemoPwd2H
Var LanCheckH
Var AdminPwd
Var AdminPwd2
Var DemoPwd
Var DemoPwd2
Var WantDemo
Var WantLan

; Скрипт проверки Python нужен до страниц: распаковываем в $PLUGINSDIR заранее.
Function .onInit
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=check-python.ps1 "${REPO_ROOT}/deploy/installer/check-python.ps1"
FunctionEnd

; Каталог стенда — только новый или пустой (то же требование, что у setup-test-stand.ps1).
Function DirLeave
  ${If} ${FileExists} "$INSTDIR\*"
    MessageBox MB_ICONEXCLAMATION|MB_OK "Каталог $INSTDIR не пуст.$\r$\nУкажите новый или пустой каталог (старый стенд удалите вручную)."
    Abort
  ${EndIf}
FunctionEnd

; Включить/выключить поля пароля демо-учёток вместе с флажком.
Function OnDemoToggle
  ${NSD_GetState} $DemoCheckH $0
  ${If} $0 == 1
    EnableWindow $DemoPwdLblH 1
    EnableWindow $DemoPwdH 1
    EnableWindow $DemoPwd2LblH 1
    EnableWindow $DemoPwd2H 1
  ${Else}
    EnableWindow $DemoPwdLblH 0
    EnableWindow $DemoPwdH 0
    EnableWindow $DemoPwd2LblH 0
    EnableWindow $DemoPwd2H 0
  ${EndIf}
FunctionEnd

Function PasswordsPageCreate
  !insertmacro MUI_HEADER_TEXT "Пароли и параметры" "Пароли никуда не записываются — только передаются установке стенда."
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0u 100% 10u "Пароль администратора «admin» (не короче 12 символов, не из простых):"
  ${NSD_CreateText} 0 11u 70% 12u ""
  Pop $AdminPwdH
  ${NSD_AddStyle} $AdminPwdH ${ES_PASSWORD}
  ${NSD_CreateLabel} 0 27u 100% 10u "Повторите пароль администратора:"
  ${NSD_CreateText} 0 36u 70% 12u ""
  Pop $AdminPwd2H
  ${NSD_AddStyle} $AdminPwd2H ${ES_PASSWORD}

  ${NSD_CreateCheckbox} 0 54u 100% 12u "Демо-данные (проекты, задачи, документы — вымышленные)"
  Pop $DemoCheckH
  ${NSD_SetState} $DemoCheckH 1
  ${NSD_OnClick} $DemoCheckH OnDemoToggle
  ${NSD_CreateLabel} 0 68u 100% 10u "Пароль для всех демо-учёток (тоже от 12 символов, отдельный):"
  Pop $DemoPwdLblH
  ${NSD_CreateText} 0 79u 70% 12u ""
  Pop $DemoPwdH
  ${NSD_AddStyle} $DemoPwdH ${ES_PASSWORD}
  ${NSD_CreateLabel} 0 95u 100% 10u "Повторите пароль демо-учёток:"
  Pop $DemoPwd2LblH
  ${NSD_CreateText} 0 106u 70% 12u ""
  Pop $DemoPwd2H
  ${NSD_AddStyle} $DemoPwd2H ${ES_PASSWORD}

  ${NSD_CreateCheckbox} 0 124u 100% 12u "Доступ по локальной сети (другие ПК смогут подключаться к серверу)"
  Pop $LanCheckH
  ${NSD_SetState} $LanCheckH 0

  ${NSD_CreateLabel} 0 140u 100% 20u "Установка займёт несколько минут: библиотеки (PySide6 ~ 100 МБ) скачиваются с pypi.org."
  nsDialogs::Show
FunctionEnd

Function PasswordsPageLeave
  ${NSD_GetText} $AdminPwdH $AdminPwd
  ${NSD_GetText} $AdminPwd2H $AdminPwd2
  ${NSD_GetText} $DemoPwdH $DemoPwd
  ${NSD_GetText} $DemoPwd2H $DemoPwd2
  ${NSD_GetState} $DemoCheckH $WantDemo
  ${NSD_GetState} $LanCheckH $WantLan

  StrLen $0 $AdminPwd
  ${If} $0 < 12
    MessageBox MB_ICONEXCLAMATION|MB_OK "Пароль администратора короче 12 символов."
    Abort
  ${EndIf}
  ${If} $AdminPwd != $AdminPwd2
    MessageBox MB_ICONEXCLAMATION|MB_OK "Пароли администратора не совпадают."
    Abort
  ${EndIf}
  ${If} $WantDemo == 1
    StrLen $0 $DemoPwd
    ${If} $0 < 12
      MessageBox MB_ICONEXCLAMATION|MB_OK "Пароль демо-учёток короче 12 символов."
      Abort
    ${EndIf}
    ${If} $DemoPwd != $DemoPwd2
      MessageBox MB_ICONEXCLAMATION|MB_OK "Пароли демо-учёток не совпадают."
      Abort
    ${EndIf}
    ${If} $DemoPwd == $AdminPwd
      MessageBox MB_ICONEXCLAMATION|MB_OK "Придумайте для демо-учёток отдельный пароль (не такой, как у admin)."
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

; ---------- установка ----------
Section
  ; 1. Проверить Python 3.11–3.14 (64-бит) до долгой работы.
  DetailPrint "Проверка Python…"
  nsExec::ExecToStack /OEM 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\check-python.ps1"'
  Pop $0                                  ; код возврата
  Pop $1                                  ; вывод (версия)
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "Не найден Python 3.11–3.14 (64-бит).$\r$\nУстановите Python 3.12 или 3.13 с https://www.python.org/downloads/windows/ — «Windows installer (64-bit)», отметьте «Add python.exe to PATH» — и запустите установщик снова."
    ExecShell "open" "https://www.python.org/downloads/windows/"
    Abort
  ${EndIf}
  DetailPrint "Найден: $1"

  ; 2. Полезная нагрузка: исходники программы + сценарии стенда (pip ставит из них).
  DetailPrint "Распаковка файлов программы…"
  SetOutPath "$PLUGINSDIR\payload"
  File "${REPO_ROOT}/pyproject.toml"
  File /r /x "__pycache__" /x "*.pyc" /x "*.egg-info" "${REPO_ROOT}/src"
  SetOutPath "$PLUGINSDIR\payload\deploy"
  File /r "${REPO_ROOT}/deploy/test-stand"

  ; 3. Пароли — через переменные окружения процесса (setup-test-stand.ps1 читает их сам,
  ;    в командной строке и на диске пароли не появляются).
  System::Call 'Kernel32::SetEnvironmentVariableW(t "VECTOR_ADMIN_PASSWORD", t "$AdminPwd")i'
  ${If} $WantDemo == 1
    System::Call 'Kernel32::SetEnvironmentVariableW(t "VECTOR_DEMO_PASSWORD", t "$DemoPwd")i'
  ${EndIf}

  ; 4. Сам сценарий стенда: venv, pip, сертификат, база, демо-данные, ярлыки.
  StrCpy $9 ""
  ${If} $WantDemo != 1
    StrCpy $9 "$9 -NoDemo"
  ${EndIf}
  ${If} $WantLan == 1
    StrCpy $9 "$9 -Lan"
  ${EndIf}
  DetailPrint "Установка программы и данных (несколько минут)…"
  nsExec::ExecToLog /OEM 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\payload\deploy\test-stand\windows\setup-test-stand.ps1" -Dir "$INSTDIR"$9'
  Pop $0

  ; Пароли из окружения — сразу убрать.
  System::Call 'Kernel32::SetEnvironmentVariableW(t "VECTOR_ADMIN_PASSWORD", i 0)i'
  System::Call 'Kernel32::SetEnvironmentVariableW(t "VECTOR_DEMO_PASSWORD", i 0)i'

  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "Установка не завершилась (код $0). Причина — в протоколе выше.$\r$\nПеред повторным запуском удалите каталог $INSTDIR."
    Abort
  ${EndIf}

  WriteUninstaller "$INSTDIR\uninstall.exe"
  DetailPrint "Готово."
SectionEnd

; ---------- «Запустить сейчас» на последней странице ----------
Function FinishRun
  ExecShell "open" "$INSTDIR\start-server.cmd"
  Sleep 4000
  ExecShell "open" "$INSTDIR\start-client.cmd"
FunctionEnd

; ---------- удаление стенда (по желанию пользователя) ----------
Section "Uninstall"
  ${IfNot} ${FileExists} "$INSTDIR\server.toml"
    MessageBox MB_ICONSTOP|MB_OK "Похоже, $INSTDIR — не каталог тестового стенда ВЕКТОР. Удаление отменено."
    Abort
  ${EndIf}
  MessageBox MB_YESNO|MB_ICONQUESTION "Удалить тестовый стенд ВЕКТОР и все его данные из $INSTDIR?" IDYES +2
  Abort
  Delete "$DESKTOP\ВЕКТОР — сервер (тест).lnk"
  Delete "$DESKTOP\ВЕКТОР — клиент (тест).lnk"
  RMDir /r "$INSTDIR"
SectionEnd
