# ВЕКТОР Юпитер — технический контракт интеграции

> Документ для внешней C++/Qt-команды, разрабатывающей САПР-модуль **ВЕКТОР Юпитер** (черчение, CAD).
> Всё ниже — факты кодовой базы ВЕКТОР (`src/vector`, `contracts/`, `tests/`); ссылки на файлы и строки
> приведены по состоянию на версию `0.9.0rc1` (схема БД 45). Разделы, помеченные **[ПРЕДЛАГАЕМЫЙ КОНТРАКТ]**,
> в коде ещё не реализованы — это предложение к согласованию, а не существующее поведение.

## Термины (как в коде)

| Имя | Что это | Где в коде |
|---|---|---|
| **ВЕКТОР Орбита** | Сервер экосистемы (этот репозиторий, `src/vector/server`) | `backend.py:orbitaStatus` («Связь с ВЕКТОР Орбита», D-131), `help/user-guide.md` |
| **ВЕКТОР Платформа** | Настольный клиент (Python/PySide6 + QML), точка входа `vector-client` | `src/vector/client/app.py:main` (`app.setApplicationName("ВЕКТОР Платформа")`) |
| **ВЕКТОР Юпитер** | CAD-модуль; в Платформе уже есть пункт переключателя модулей (отключён, «в разработке») и акцентный цвет `#D99B26` | `qml2/Shell2.qml:381`, `qml/Theme.qml:51` (`accJupiter`), `assets/vektor` |

---

## 1. API сервера «ВЕКТОР Орбита»

Источник истины: `src/vector/server/api.py` (Starlette), примеры — `tests/server/test_api.py`.

### 1.1. Базовый URL и транспорт

- API версии **v1**, все маршруты под `/api/v1/…` (`api.py:create_app`).
- Порт сервера по умолчанию **8443**, задаётся в `server.toml` (секция `[server]`: `host`, `port`,
  `tls_cert`, `tls_key`) — `src/vector/server/config.py:40`. Запуск: `vector-server serve --config server.toml`
  (HTTPS через uvicorn, заголовок `Server` не отдаётся, журнал доступа выключен — `server/cli.py:97-112`).
- Клиент принимает **только `https://`** (открытый HTTP разрешён лишь для `127.0.0.1`/`localhost` при разработке);
  редиректы запрещены, свой корневой CA передаётся отдельным файлом — `client/transport.py:HttpTransport`.
- `User-Agent` клиента Платформы: `VECTOR-desktop` (`transport.py`).
- Тело JSON-запроса ≤ **1 МБ** (`api.py:MAX_BODY`); файлы — отдельными маршрутами (см. 1.4), предел размера файла
  по умолчанию **200 МБ** (`security.py:max_file_mb`, настраивается в `[security]`, допустимо 1…2048 МБ).
- Ответы JSON: `Cache-Control: no-store`. Код HTTP дублирует поле `status` тела: `accepted`→200,
  `rejected`→422, `denied`→403, `conflict`→409, `unauthenticated`→401, `error`→500 (`api.py:_STATUS_HTTP`).
- Служебные маршруты без входа: `GET /api/v1/health` → `{"status": "ok"}`; `GET /api/v1/about` →
  `{"product": "ВЕКТОР", "product_en": "VECTOR", "version": "0.9.0rc1", "api": "v1", "schema_version": 45}`
  (`service.py:about`).

### 1.2. Авторизация

Вход и выход — через конверт протокола:

```
POST /api/v1/auth/login
Content-Type: application/json

{"login": "<логин>", "password": "<пароль>", "device_id": "<строка ≤100>"}
```

Ответ 200 (`service.py:_login`):

```json
{
  "status": "accepted",
  "token": "<сессионный токен>",
  "user_id": "7d2e…uuid",
  "display_name": "Смирнова Анна",
  "login": "a.smirnova",
  "must_change_password": false,
  "expires_at": "2026-11-16T21:00:00.000000Z",
  "idle_timeout_minutes": 120,
  "max_file_bytes": 209715200
}
```

- Дальше токен — **только в заголовке** `Authorization: Bearer <token>`; токен в теле/URL игнорируется
  (запрос с `token` в теле получает 401 — `test_api.py:test_token_in_body_is_ignored`).
- Ошибки входа: 401 `bad_credentials` (одинаковый ответ для «нет логина»/«неверный пароль»/«учётка отключена»),
  401 `account_locked` (после 5 неудач подряд — блокировка 15 минут; пороги — `security.py:SecurityPolicy`),
  422 `bad_login_request`, 422 `rate_limited`.
- Сеанс: абсолютный срок 12 ч (`session_absolute_hours`), простой 120 мин (`session_idle_minutes`) — оба поля
  возвращаются при входе; «простой» обновляется каждой активностью.
- Выход: `POST /api/v1/auth/logout` (с заголовком Bearer) → `{"status": "accepted", "revoked": 1}`.

### 1.3. Конверты command/query

Все изменения — единый маршрут команд; чтение — единый маршрут запросов:

```
POST /api/v1/command        {"operation_id": "<uuid>", "command": "<имя>", "payload": {...}, "expected_version": <int?>}
POST /api/v1/query          {"query": "pull" | "history" | "users" | "norm_stats", …}
```

- `operation_id` создаётся клиентом **один раз на операцию**; повтор с тем же id после обрыва связи возвращает
  сохранённый исход (идемпотентность); тот же id с другим содержимым → 422 `operation_id_reused`
  (`tests/server/test_idempotency.py`).
- Ответ команды: `{"status": "accepted", "result": {…}}`; ошибки: `{"status": "rejected"|"denied", "code": "…",
  "message_ru": "…", "details": {…}?}`. Конфликт версий: 409 `version_conflict` с `details.current` —
  актуальным состоянием объекта на сервере (показать пользователю «моё/на сервере», не перезаписывать молча).
- `expected_version` — оптимистическая блокировка версионных объектов (документы, ревизии, комментарии).
- Полный каталог команд: `contracts/command-catalog.slice1.json`; права: `contracts/permissions.slice1.json`
  (синхронизированные копии поставляются в пакете `src/vector/core/contracts_data/`).
- Денежные суммы — только десятичные строки; JSON-число с плавающей точкой отклоняется (`*_float_forbidden`).
- Форматы: время — ISO-8601 UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`; дата — `YYYY-MM-DD`; идентификаторы — UUID
  строчными hex (`^[0-9a-f]{8}-…$`, `validation.py:_UUID_RE`).

### 1.4. Файлы версий документов (сырые байты, не JSON)

Источник: `api.py:file_upload/file_download`, `server/work.py:put_file_stream/get_file_to`,
клиентская эталонная реализация — `client/transport.py:upload/download` (блоки по 1 МБ, таймаут 600 с).

**Загрузка содержимого** (предшествует `document.add_version`):

```
POST /api/v1/files?project_id=<uuid>
Authorization: Bearer <token>
X-Content-SHA256: <sha256-hex содержимого, 64 символа>
Content-Type: application/octet-stream
Content-Length: <размер>

<сырые байты файла>
```

- Право (`document.add_version` в проекте) и вход проверяются **до чтения тела**; завышенный Content-Length
  отклоняется до приёма.
- Сервер пересчитывает SHA-256: несовпадение → 422 `hash_mismatch` (повреждение в пути — повторить).
- Дедупликация по хэшу: повторная загрузка того же содержимого не пишет байты, но выдаёт квитанцию.
- Ответ 200: `{"status": "accepted", "sha256": "…", "size_bytes": 12345, "deduplicated": false}`.
- Ошибки: 401 без токена; 403 `no_authority`; 422 `file_too_large` / `hash_mismatch` / `bad_request`.

**Скачивание**:

```
GET /api/v1/files/{document_id}/{version_no}
Authorization: Bearer <token>
```

- 200: `application/octet-stream`, заголовки `Content-Length`, `X-Content-SHA256`,
  `Content-Disposition: attachment; filename="document.bin"; filename*=UTF-8''<urlencoded имя>` (кириллица —
  по RFC 5987, извлекать из `filename*`).
- Сервер собирает файл из блоков БД во временный файл, проверяет SHA-256 **до начала отдачи** — повреждённый
  файл не отдаётся ни байтом. Клиент обязан пересчитать SHA-256 принятого и сравнить с заголовком.
- Ошибки: 401 без токена; 403 `no_authority` (одинаково для «нет документа» и «нет доступа»);
  422 `bad_version_no` / `version_not_found`.

**Пакет выдачи (трансмиттал)**: `GET /api/v1/transmittals/{transmittal_id}/package` → `application/zip` с теми же
соглашениями (SHA-256 в `X-Content-SHA256`) — D-63.

### 1.5. Карточки проекта/комплекта/документа и штамп ЕСКД

Чтение — через `POST /api/v1/query {"query": "pull"}`: сервер отдаёт снимок всего, что пользователь вправе
видеть (состав по правам; `service.py:_pull`). Ключи, нужные Юпитеру:

| Ключ pull | Содержимое | Представление |
|---|---|---|
| `server_time` | время сервера (UTC) | строка ISO |
| `me` | `user_id`, `display_name`, `login`, `roles`, … | `service.py:_pull` |
| `people` | `[{"id", "display_name"}]` — справочник имён (без логинов) | для штампа: ФИО по `created_by` |
| `projects` | `{"id","code","title","tender_id","status","version","updated_at"}` | `service.py:v_project`; статусы: `rkd_in_progress` «Разработка РКД», `closing` «Завершается», `archived` «Архив», `cancelled` «Отменён» (`contracts/workflows.slice1.json`) |
| `kits` | `{"id","project_id","code","title","version","updated_at"}` | `service.py:v_kit` |
| `documents` | `{"id","project_id","kit_id","code","title","current_version_no","version","updated_at"}` | `work.py:v_document`; `code` уникален в проекте |
| `document_versions` | `{"id","document_id","version_no","file_name","sha256","size_bytes","comment","created_by","created_at"}` | `work.py:v_document_version` |
| `document_revisions` | бизнес-ревизии РКД (см. ниже) | `rkd.py:v_revision` |
| `comments` | обсуждения объектов (P3-2) | `discuss.py:v_discuss_comment` |
| `assignments` | назначения в проектах: `{"id","project_id","user_id","project_role","valid_until"}` | роли: `gip`/`rp`/`participant` |

**Ревизия РКД** (`rkd.py:v_revision`, таблица `document_revisions`):

```json
{"id": "…", "document_id": "…", "seq": 1, "label": "01",
 "status": "sent", "status_label": "Отправлена заказчику",
 "doc_version_id": "…", "note": "", "version": 1,
 "created_by": "<user_id>", "created_at": "…", "updated_at": "…"}
```

Статусы ревизии (`workflows.slice1.json:docrev.labels_ru`): `in_work` «В работе», `ready_for_rp` «На проверке у
руководителя проекта», `returned_to_engineer` «Возвращена на доработку», `ready_to_transmit` «Разрешена к
отправке», `sent` «Отправлена заказчику», `customer_comments` «Получены замечания заказчика»,
`revision_in_work` «Доработка по замечаниям», `approved` «Согласована заказчиком».

**Метаданные для штампа ЕСКД — маппинг на реальные поля:**

| Поле штамма | Источник в API |
|---|---|
| Шифр документа | `documents[].code` (например «АТХ-001») |
| Наименование | `documents[].title` (+ `projects[].title`, `kits[].title`) |
| Номер версии файла | `documents[].current_version_no`, файл — `document_versions[]` |
| Ревизия (обозначение) | `document_revisions[].label` (+ `seq`); статус — `status_label` |
| ФИО разработал/проверил | `people[ document_versions[].created_by ].display_name` / `people[ document_revisions[].created_by ]` |
| Дата | `created_at` версии/ревизии (UTC) |

⚠️ **Чего в модели нет** (не выдумывать, согласовать отдельно): отдельного поля «стадия ЕСКД» (П/Р/РД) и
реквизитов организации для штамма в API нет; ссылки «лист/листов» не хранятся. Стадия проекта — это
`projects[].status` (см. выше), что не равно стадии ЕСКД документа.

### 1.6. Команды, которые нужны Юпитеру

Все — через `POST /api/v1/command`. Обязательные ключи перечислены; лишние ключи отклоняются (`only_keys`).

**Создать карточку документа** (в комплекте; файла пока нет):

```json
{"operation_id": "<uuid>", "command": "document.create",
 "payload": {"id": "<uuid>", "kit_id": "<uuid>", "code": "АТХ-001", "title": "Схема электрическая принципиальная"}}
→ {"status": "accepted", "result": {"document": {…v_document…}}}
```

**Загрузить новую версию файла** (двухшагно: сначала байты по 1.4, затем команда):

```json
{"operation_id": "<uuid>", "command": "document.add_version",
 "payload": {"id": "<document uuid>", "sha256": "<hex из ответа загрузки>", "file_name": "Схема К-1.dwg",
             "comment": "Что изменилось"},
 "expected_version": <documents[].version>}
→ {"status": "accepted", "result": {"document": {…}, "document_version": {…}}}
```

- Имя файла ≤255, без `<>:"/\|?*` и управляющих (`work.py:_check_file_name`).
- Без предшествующей загрузки → 422 `file_not_uploaded`. Чужая загрузка не подтверждает вашу версию.
- Устаревший `expected_version` → 409 `version_conflict` + `details.current` (карточка на сервере).
- Правка карточки (шифр/название): `document.update {id, code?, title?}` + `expected_version`.

**Ревизия РКД** (зафиксировать бизнес-ревизию на версии файла):

```json
{"operation_id": "<uuid>", "command": "docrev.create",
 "payload": {"id": "<uuid>", "document_id": "<uuid>", "label": "01", "doc_version_id": "<uuid>", "note": ""}}
→ {"status": "accepted", "result": {"revision": {…v_revision…}}}
```

- `label` уникален в документе; при действующем профиле шифров проекта проверяется по шаблону
  (ошибка `label_pattern_mismatch` с примером). Одновременно одна «рабочая» ревизия.
- Переходы: `docrev.transition {id, to, reason?}` + `expected_version`; допустимые цели — из
  `workflows.slice1.json:docrev.transitions`; причина обязательна для возвратов/отзыва разрешения.

**Комментарии / @упоминания** (P3-2, `discuss.*`; офлайн-очередь на стороне Платформы, для Юпитера — обычный POST):

```json
{"operation_id": "<uuid>", "command": "discuss.add",
 "payload": {"id": "<uuid>", "object_type": "document", "object_id": "<uuid>",
             "body": "@Волков Павел посмотрите разрез А-А", "parent_id": "<uuid необязательно>"}}
→ {"status": "accepted", "result": {"comment": {"id","object_type","object_id","project_id","parent_id",
   "author_id","body","mentions":["<user_id>"],"version":1,"created_at":"…","edited_at":null,"deleted":false}}}
```

- `object_type`: `project` | `kit` | `document` | `task`. Упоминание — маркер `@<display_name>` в тексте
  (display_name из `people`); сервер подтверждает только участников проекта, иначе 422 `mention_not_allowed`.
- Правка: `discuss.edit {id, body}` + `expected_version` — только автор, в течение 24 ч (`edit_window_passed`).
- Удаление мягкое: `discuss.delete {id}` + `expected_version` — автор или РП/ГИП проекта; в pull комментарий
  остаётся с `"deleted": true` и пустым `body` (треды не разваливаются).
- Чтение — pull-коллекция `comments` (только читаемые пользователем проекты).

**Внутренние ссылки**: `vector://project|kit|document|task/<id>` (`qml2/Shell2.qml:openLink`, P3-1) — не зависят
от адреса сервера; Платформа открывает объект по такой ссылке (палитра Ctrl+K, «Копировать ссылку» в меню).
Рекомендуемый формат ссылок на объекты Орбиты и внутри Юпитера.

---

## 2. Запуск и IPC с клиентом «ВЕКТОР Платформа»

**Факт (по коду):** сегодня Платформа **не имеет** внешнего запуска модулей: в `src/vector/client` нет
`QDesktopServices.openUrl`, `QProcess`/плагинов/сокетов (проверено поиском), точка входа `vector-client`
(`pyproject.toml:[project.scripts]` → `vector.client.app:main`) не разбирает собственные CLI-аргументы модуля.
Переключатель модулей в оболочке 2.0 уже содержит пункт «Юпитер» (`Shell2.qml:381`) с `enabled: false`.

### 2.1. [ПРЕДЛАГАЕМЫЙ КОНТРАКТ] запуск Юпитера из Платформы

```
vektor-jupiter --orbita-url https://<host>:8443 \
               --token <сессионный токен Платформы> \
               --doc-id <uuid документа> \
               --file <путь к скачанному файлу версии> \
               [--link vector://document/<uuid>]
```

- `--orbita-url` — адрес Орбиты (в Платформе хранится в настройках `server/url` / `VECTOR_SERVER_URL`,
  `backend.py:orbitaStatus`); требование `https://` — как в `transport.py`.
- `--token` — действующий токен сеанса Платформы (передаётся в среду запуска, не в командной строке при
  промышленной эксплуатации — вопрос безопасной передачи согласуется отдельно; токен имеет сроки 1.2).
- `--doc-id` + `--file` — документ и уже скачанный/проверенный по SHA-256 файл; дальнейшие обращения — по API
  раздела 1 (загрузка новой версии — двухшагная, с `expected_version` карточки).
- Обратный переход из Юпитера в Платформу — ссылка `vector://…` (1.6): «Открыть в Платформе».

До согласования IPC Юпитер может работать полностью автономно поверх HTTP API раздела 1 (свой вход по
login/password → токен, своя очередь команд по `operation_id`).

---

## 3. Дизайн-токены и ассеты

### 3.1. Графический пак экосистемы: `src/vector/client/assets/vektor/`

`manifest.json` (реальный файл, сокращённо):

```json
{
  "ecosystem": "ВЕКТОР",
  "style": "Neo-Utilitarian / Industrial Modern",
  "grid": "24x24",
  "stroke_width": "1.5px",
  "tokens": { "canvas": "#121417", "surface": "#181B20", "elevated": "#1F242B", "border": "#2A303A",
              "text_primary": "#E6EDF3", "text_secondary": "#8B949E", "text_muted": "#586069",
              "accents": { "vektor_platform": "#E2E8F0", "vektor_hub": "#E2E8F0", "vektor_mars": "#E05A47",
                           "vektor_jupiter": "#D99B26", "vektor_mercury": "#2E9E76", "vektor_venus": "#4A77D4" } },
  "app_icons": { "formats": ["png_512","png_256","png_128","png_64","ico"],
                 "modules": ["vektor_platform","vektor_hub","vektor_mars","vektor_jupiter","vektor_mercury","vektor_venus"],
                 "logos": ["app_icons/logo/vektor_platform_horizontal.png","app_icons/logo/vektor_emblem_only.png"] },
  "ui_icons": { "01_modules": […], "02_navigation": […], "03_actions": […], "04_hub_pdm": […],
                "05_mars_electro": […], "06_jupiter_cad": […17 иконок CAD…],
                "07_mercury_tables": […], "08_venus_docs": […] }
}
```

Структура каталога (реальная):

```
assets/vektor/
  manifest.json
  app_icons/  ico/  png_64/  png_128/  png_256/  png_512/  logo/   # vektor_jupiter.* и др. модули, vektor_orbita.* есть
  backgrounds/space.jpg
  ui_icons/   01_modules/ 01_modules_colored/ 02_navigation/ 03_actions/ 04_hub_pdm/
              05_mars_electro/ 06_jupiter_cad/ 07_mercury_tables/ 08_venus_docs/
```

- Иконки — SVG с `stroke="currentColor"`, сетка 24×24, штрих 1.5, скруглённые концы (пример —
  `ui_icons/06_jupiter_cad/circle.svg`). Цветные версии модулей — в `01_modules_colored` (не перекрашиваются).
- Как Платформа их подаёт в QML (`app.py:IconProvider`, VEKTOR_ICONS): `image://icon/<lucide-имя>?c=#rrggbb` —
  перекраска под токен темы; `image://icon/vmod/vektor_jupiter` — цветная иконка модуля;
  `image://icon/vui/06_jupiter_cad/layers` — прямой путь в ui_icons с перекраской. C++-модулю достаточно
  рендерить SVG и подставлять currentColor.

### 3.2. Палитра тем (единый источник: `src/vector/client/qml/Theme.qml`; контраст проверен `tests/client/test_theme_contrast.py`)

| Токен | Dark | Light |
|---|---|---|
| `bg` | `#121417` | `#F6F8FA` |
| `sidebar` / `surface` | `#181B20` | `#FFFFFF` |
| `surfaceAlt` | `#1F242B` | `#EFF1F4` |
| `hover` | `#21262D` | `#EAEEF2` |
| `border` / `borderStrong` | `#2A303A` / `#39424E` | `#D0D7DE` / `#B8C0C8` |
| `text` / `textMuted` / `textFaint` | `#E6EDF3` / `#8B949E` / `#6E7681` | `#1F2328` / `#57606A` / `#6E7781` |
| `primary` / `primaryHover` | `#E2E8F0` / `#EDF2F8` | `#31648C` / `#2A5678` |
| `primarySoft` / `textOnPrimary` | `#262D36` / `#121417` | `#DDE8F3` / `#FFFFFF` |
| `accent` | `#9AA7B8` | `#6E5CA8` |
| `success` / `successSoft` | `#2E9E76` / `#1A2728` | `#177233` / `#DAF0E0` |
| `warning` / `warningSoft` | `#D99B26` / `#2F2A21` | `#8A5700` / `#FFF1D6` |
| `danger` / `dangerSoft` | `#E56B55` / `#2C2325` | `#B3261E` / `#FBE5E3` |
| `info` / `infoSoft` | `#6B93E0` / `#202733` | `#0A6E7C` / `#DCF0F3` |
| `neutralSoft` | `#232830` | `#EAEEF2` |
| `scrim` (подложка диалогов) | `#99000000` | `#5C2E3846` |
| Бренд-блоки (`brandBg/Text/Muted`) | `#121417` / `#E6EDF3` / `#8B949E` | те же (тёмная бренд-зона в обеих темах) |

Акценты модулей (`Theme.qml:49-53`, Dark/Light): Платформа/Хаб `#E2E8F0`/`#31648C`, МАРС `#E05A47`/`#B3261E`,
**Юпитер `#D99B26`/`#8A5700`**, Меркурий `#2E9E76`/`#177233`, Венера `#4A77D4`/`#3A5FCD`.

### 3.3. Шрифты и метрики

- Интерфейсный шрифт **Inter** (`assets/fonts/InterVariable.ttf`, SIL OFL — `Inter-LICENSE.txt`),
  моноширинный **JetBrains Mono** Regular/Bold (для шифров/артикулов/кодов — уместно для полей штампа);
  запасные — Segoe UI / Consolas (`app.py:load_fonts`).
- Метрики UI 2.0 (`qml2/Theme2.qml`): шаг сетки 8px (все размеры кратны 4); радиусы 4/6/8;
  две плотности — «Уютно» (базовый текст 15, строка ≥52) и «Компактно» (13, ≥38); кегли
  `fsXs/fsS/fs/fsM/fsL/fsXl/fsXxl` = 12/13/15/17/21/27/32 («Уютно»).

---

## 4. Контрольные примеры (из `tests/server/test_api.py`)

```python
# Вход и pull
h = {"Authorization": "Bearer " + post("/api/v1/auth/login",
     json={"login": "…", "password": "…", "device_id": "d1"}).json()["token"]}
post("/api/v1/query", headers=h, json={"query": "pull"}).json()["result"]["projects"]

# Загрузка и привязка версии
up = post("/api/v1/files?project_id=<pid>", headers={**h, "X-Content-SHA256": sha}, content=data)
post("/api/v1/command", headers=h, json={"operation_id": new_id(), "command": "document.add_version",
     "payload": {"id": did, "sha256": sha, "file_name": "Схема К-1.dwg"}, "expected_version": 1})

# Скачивание с проверкой
r = get(f"/api/v1/files/{did}/1", headers=h)
assert r.headers["x-content-sha256"] == sha256(r.content).hexdigest()
```

Коды ошибок, на которые стоит опираться Юпитеру: `bad_request`, `bad_credentials`, `account_locked`,
`rate_limited`, `no_token`, `no_authority`, `file_too_large`, `hash_mismatch`, `file_not_uploaded`,
`bad_version_no`, `version_not_found`, `version_conflict` (409, с `details.current`), `operation_id_reused`,
`label_pattern_mismatch`, `mention_not_allowed`, `edit_window_passed`, `project_state` (изменения — только
в проекте «Разработка РКД»), `unknown_query`, `unknown_command`.

---

*Вопросы и изменения контракта — через владельца продукта ВЕКТОР (репозиторий Alchimon/VECTOR). Любые поля,
не описанные здесь, считать отсутствующими: сервер лишние ключи отклоняет, а недостающие — добавляются только
новой версией контракта.*
