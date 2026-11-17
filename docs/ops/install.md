# Установка сервера и клиента ВЕКТОР

> Для администратора, который разворачивает систему у себя (on-prem).
> Версия документа соответствует коду `0.2.0.dev0` (схема БД v3).
> Статусы: ✅ — пройдено вручную в Linux-песочнице (команды и результаты в разделе «Что проверено»);
> 🟡 — написано по документации, на этой платформе не запускалось; ⛔ — ещё не реализовано.

## 1. Из чего состоит система

```
 Рабочие места (Windows)                Сервер приложения                  СУБД
 ┌──────────────────────┐   HTTPS     ┌──────────────────────┐   сеть   ┌──────────────────┐
 │ vector-client        │ ──────────► │ vector-server serve  │ ───────► │ PostgreSQL       │
 │ локальный зашифр.    │  порт 8443  │ (Starlette+uvicorn)  │          │ или MS SQL Server│
 │ кэш и очередь        │             │ TLS, права, аудит    │          └──────────────────┘
 └──────────────────────┘             └──────────────────────┘
```

* Клиенты **никогда** не подключаются к СУБД — только к серверу по HTTPS. Порт СУБД закрывается
  для рабочих мест на сетевом экране; открыт только порт сервера (по умолчанию 8443).
* Файлы документов хранятся в той же БД (блоками по 1 МБ, D-20) и попадают в ту же резервную копию.
* SQLite поддерживается только для тестов и пилота на одном компьютере — сервер предупреждает об этом при запуске.

## 2. Требования

| Компонент | Требование |
|---|---|
| ОС сервера | Linux с systemd ✅ (проверено: Debian 12, Python 3.11); Windows Server 🟡 (см. §7) |
| Python | 3.11 или новее (используется `tomllib`) |
| СУБД | PostgreSQL 13+ ✅ (тесты — PostgreSQL 16 из pgserver); MS SQL Server 2017+ 🟡 (issue #3) |
| Память сервера | от 2 ГБ. Объём памяти не зависит от размера файлов: загрузка и скачивание идут потоком блоками по 1 МБ (замер на файле 150 МБ: пиковая память не выросла), резервное копирование и восстановление — тоже (+4…12 МБ на 150 МБ файлов) |
| Временные файлы | Каждая идущая передача файла занимает на диске сервера до его размера (`security.max_file_mb`, по умолчанию 200 МБ) в системном каталоге временных файлов. Каталог задаётся переменной окружения `TMPDIR` (Linux) / `TEMP` (Windows); держите его на локальном диске с правами только для службы — там на время передачи лежит содержимое документа |
| Диск | БД + место для копий: каждая копия ≈ объём файлов + несколько процентов (файлы почти не сжимаются) × `backup.keep` |
| TLS | Сертификат сервера от CA компании; в нём DNS-имя сервера (SAN), по которому подключаются клиенты |
| Клиент | Windows 10/11 x64 🟡 (интерфейс проверен в offscreen-режиме Linux; Windows DPI — Class C, issue #4) |

## 3. База данных

### 3.1 PostgreSQL ✅

Создайте отдельную роль и базу (выполняет администратор СУБД):

```sql
CREATE ROLE vector LOGIN PASSWORD '<длинный случайный пароль>';
CREATE DATABASE vector OWNER vector ENCODING 'UTF8' TEMPLATE template0;
```

Владелец базы может создавать в ней таблицы — это нужно команде `vector-server init`.
Строка подключения (понадобится в §5):

```
postgresql+psycopg2://vector:<пароль>@db-host:5432/vector
```

Сервер работает с уровнем изоляции SERIALIZABLE и сам повторяет транзакции при конфликте сериализации.
Включите в `pg_hba.conf` шифрование соединения (`hostssl`), если СУБД на другом компьютере.

### 3.2 MS SQL Server 🟡 (не проверено на настоящем сервере — issue #3)

```sql
CREATE LOGIN vector WITH PASSWORD = '<длинный случайный пароль>', CHECK_POLICY = ON;
CREATE DATABASE vector COLLATE Cyrillic_General_CI_AS;
USE vector;
CREATE USER vector FOR LOGIN vector;
ALTER ROLE db_owner ADD MEMBER vector;   -- нужно для init (создание таблиц и триггеров)
```

На сервер приложения дополнительно установите Microsoft ODBC Driver 18 for SQL Server и драйвер Python:

```bash
pip install pyodbc
```

Строка подключения:

```
mssql+pyodbc://vector:<пароль>@db-host/vector?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes
```

Особенность: резервное копирование выполняется в транзакции SERIALIZABLE, и пока оно идёт, запись в уже
прочитанные таблицы ждёт его окончания. Планируйте копирование на нерабочее время (§ «Расписание» в
[backup-restore.md](backup-restore.md)). Режим SNAPSHOT без блокировок — после проверки на MS SQL (issue #3).

## 4. Установка программы сервера (Linux) ✅

```bash
# Учётная запись службы без входа в систему и каталоги
sudo useradd --system --home /var/lib/vector --shell /usr/sbin/nologin vector
sudo install -d -o vector -g vector -m 700 /var/lib/vector /var/lib/vector/backups
sudo install -d -o root   -g vector -m 750 /etc/vector

# Программа — в отдельном виртуальном окружении
sudo python3 -m venv /opt/vector/venv
sudo /opt/vector/venv/bin/pip install "vector[server] @ git+https://github.com/Alchimon/VECTOR@<тег-версии>"
/opt/vector/venv/bin/vector-server --help
```

До первого выпуска устанавливайте из конкретного коммита или тега, а не из ветки: так известно, какая версия работает.

## 5. Конфигурация и секреты ✅

1. Скопируйте пример `config/vector-server.example.toml` в `/etc/vector/vector-server.toml`
   и поправьте пути. **В этом файле нет секретов**; сервер откажется запускаться, если найдёт в нём `database.url`.
2. Строку подключения положите в `/etc/vector/vector.env` (читает только systemd от имени root):

   ```bash
   sudo install -m 600 -o root -g root /dev/null /etc/vector/vector.env
   sudoedit /etc/vector/vector.env      # одна строка: VECTOR_DB_URL=postgresql+psycopg2://vector:...@db-host:5432/vector
   ```

   Альтернатива — `database.url_file` с файлом прав 600, принадлежащим `vector`.
3. TLS: сертификат и закрытый ключ от CA компании.

   ```bash
   sudo install -d -o vector -g vector -m 700 /etc/vector/tls
   sudo install -o vector -g vector -m 600 server.key /etc/vector/tls/server.key
   sudo install -o vector -g vector -m 644 server.pem /etc/vector/tls/server.pem   # цепочка: сертификат сервера + промежуточные
   ```

   Сервер проверяет права ключа и не запустится, если ключ доступен группе или всем.
   Для пилота без CA компании: `vector-server dev-cert --dir ./pilot/certs --host <имя-сервера>` —
   **только для пилота**, такой CA нельзя раздавать на рабочие места надолго.
4. Ключ резервных копий — см. [backup-restore.md](backup-restore.md), раздел 2.

## 6. Первый запуск ✅

```bash
# Создать схему и первого администратора (пароль спрашивается без эха; в командной строке НЕ передаётся)
sudo -u vector env $(sudo cat /etc/vector/vector.env) \
  /opt/vector/venv/bin/vector-server init --config /etc/vector/vector-server.toml

# Проверить конфигурацию, права файлов и совместимость схемы
sudo -u vector env $(sudo cat /etc/vector/vector.env) \
  /opt/vector/venv/bin/vector-server check --config /etc/vector/vector-server.toml
```

`init` работает только с **пустой** базой. Пароль администратора для автоматизации можно передать в
переменной `VECTOR_ADMIN_PASSWORD` (не в аргументах — их видно в списке процессов).

### Служба systemd ✅ (синтаксис проверен `systemd-analyze verify`)

Готовые файлы — в [`deploy/systemd/`](../../deploy/systemd):

```bash
sudo cp deploy/systemd/vector-server.service deploy/systemd/vector-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vector-server.service vector-backup.timer
systemctl status vector-server
journalctl -u vector-server -f
```

`vector-server.service` перед запуском выполняет `check`, работает от `vector` с ограничениями
(`ProtectSystem=strict`, запись только в `/var/lib/vector`) и перезапускается при сбое.

### Проверка снаружи

```bash
curl --cacert ca.pem https://vector.company.local:8443/api/v1/health   # {"status":"ok"}
curl --cacert ca.pem https://vector.company.local:8443/api/v1/about    # версия программы и схемы
```

Без доверенного CA соединение отклоняется — так и должно быть.

## 7. Сервер на Windows 🟡 / ⛔

Программа сервера кроссплатформенная (`vector-server serve` работает так же), но **обёртка службы Windows
ещё не сделана** (вопрос OQ-21 к владельцу). Варианты:

| Вариант | Плюсы | Минусы |
|---|---|---|
| A. Собственная служба Windows в составе установщика (pywin32) — **рекомендуется как цель** | Штатная служба, журнал событий Windows, без сторонних программ | Разработка и проверка на Windows (Class C, вместе с установщиком, issue #4) |
| B. Сторонняя обёртка (WinSW / NSSM) | Быстро | Сторонний исполняемый файл на сервере: нужен проверенный официальный источник и решение ИБ |
| C. Планировщик заданий Windows «При запуске компьютера» — **временно** | Встроен в Windows | Нет перезапуска при сбое «из коробки», хуже наблюдаемость |

Пока решение не принято, для пилота на Windows используйте вариант C (команды — в
[backup-restore.md](backup-restore.md), § «Windows», тот же принцип). Строку подключения храните в
переменной окружения учётной записи службы, а не в файле конфигурации.

## 8. Клиент (рабочие места)

⛔ Установщика (MSI/подпись кода) пока нет — issue #4. Для знакомства и теста на одном ПК есть готовый
стенд со скриптами — [test-pc.md](test-pc.md). Для пилота в сети вручную:

```powershell
py -3.11 -m venv C:\VECTOR\venv
C:\VECTOR\venv\Scripts\pip install "vector[client] @ git+https://github.com/Alchimon/VECTOR@<тег-версии>"
C:\VECTOR\venv\Scripts\vector-client
```

При первом входе пользователь указывает адрес сервера (`https://vector.company.local:8443`) и, если
сертификат выдан CA компании, который ещё не установлен в Windows, — путь к файлу CA (`ca.pem`).

* Локальные данные: `%LOCALAPPDATA%\VECTOR\profiles` — кэш зашифрован; ключ защищён паролем пользователя и
  секретом устройства в диспетчере учётных данных Windows. Без учётной записи Windows и пароля кэш не читается.
* Офлайн-вход разрешён 7 дней после последнего входа с сервером (OQ-20).
* Сброс пароля администратором делает прежний локальный кэш нечитаемым: клиент откладывает его
  (файл `local.db.orphaned-<время>`, не удаляется), а неотправленные изменения из него **не отправляются** —
  пользователь видит об этом предупреждение. Это осознанная мера безопасности: старый пароль больше
  не открывает данные. Перед сбросом пароля попросите пользователя синхронизироваться, если это возможно.

## 9. Обновление ✅ (SQLite, PostgreSQL) / 🟡 (MS SQL)

Обновление схемы БД выполняет команда `vector-server migrate`: она сама делает и проверяет копию текущей БД, затем
в одной транзакции переводит схему на новую версию (сейчас v3 → v4 → v5 → v6 → v7); при сбое изменения откатываются.
Пошагово, включая откат, — [backup-restore.md, § 8](backup-restore.md#8-обновление-версии-программы-миграция-схемы--sqlite-postgresql---ms-sql);
сводный порядок — [руководство администратора, § 9](../manual/admin-guide.md#9-обновление-версии-программы).
Сервер не запустится с несовместимой версией схемы (`check` сообщит об этом).

## 10. Что проверено (Class A, Linux-песочница, 2026-11-16)

Команды выполнены по этому документу; результаты — дословно из вывода программы.

| Шаг | СУБД | Результат |
|---|---|---|
| `dev-cert`, `gen-backup-key` | — | файлы созданы, ключи с правами 600 |
| `init` | SQLite, PostgreSQL 16 | «Схема v3 создана…; администратор «admin» создан», код 0 |
| `check` | SQLite, PostgreSQL | «Схема БД: v3 — совместима», код 0; для SQLite — предупреждение «только для тестов и пилота» |
| `serve` с TLS | PostgreSQL | `/api/v1/health` → `{"status":"ok"}`; без CA — соединение отклонено (curl, код 60); вход admin → `accepted` |
| `database.url` в конфигурации | — | «Строку подключения нельзя хранить в конфигурации (секрет)…», запуск отклонён |
| Журнал сервера | — | пароль администратора в журнале не встречается (0 вхождений) |
| systemd-юниты | — | `systemd-analyze verify`: ошибок синтаксиса нет (только отсутствие `/opt/vector` в песочнице) |
| Передача файла 150 МБ через HTTP-сервер (uvicorn) и клиентский транспорт | SQLite, PostgreSQL 16 | пиковая память процесса: загрузка +0 МБ, скачивание +0 МБ; контроль — чтение того же файла в память +110…145 МБ (issue #8) |

Не проверено: MS SQL (issue #3), Windows Server и клиент на Windows (issue #4), реальный CA компании.
