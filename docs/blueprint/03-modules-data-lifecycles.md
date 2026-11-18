# 03. Границы модулей, объекты данных, владельцы, жизненные циклы

## 1. Модули модульного монолита (сервер)

Правило: модуль владеет своими таблицами; другие модули обращаются **только** через его application
service / доменные события. Клиент и чужие модули не пишут в чужие таблицы (R-35, D-04).

| Модуль | Владеет объектами | Публикует события | Зависит от |
|---|---|---|---|
| `identity` | User, Session, Device, RoleGrant, Substitution | RoleGranted/Revoked, SessionRevoked | — |
| `org` | Employee, Department, Position, OrgLink | EmployeeArchived | identity (только ссылка на User) |
| `audit` | AuditEntry, OperationRecord | — | все (приём записей в той же транзакции) |
| `config` | ConfigItem, ConfigVersion, Template, Terminology | ConfigPublished/RolledBack | identity |
| `commerce` | Customer, Contact, Deal, Tender, TKP, TKPApproval, Estimate, EstimateLine, Quote(КП) | TenderWon, TKPApproved | config, files |
| `projects` | Project, ProjectAssignment, Kit, Task, TaskComment | ProjectStarted, KitCreated | commerce (ссылка на Tender), identity |
| `documents` | Document, DocumentVersion, Remark, Issue(выдача), DispatchRecord, ReceiptRecord, FileMoveJob | VersionUploaded, IssueRegistered | files, projects |
| `files` | FileBlob (hash, размер), StorageLocation, WipCopy | BlobStored | — |
| `calc` | KitCalculation (draft), KitCalcVersion (frozen), ProjectCalcRevision | KitFrozen, ProjectCalcRevised | projects, config (валюты, политика) |
| `procurement` | Need, PurchaseOrder, SupplyFact, AnalogDecision, ReplenishmentDraft | NeedCreated | calc, inventory |
| `inventory` | Item, Unit, Warehouse, Lot/Serial, StockMovement, Reservation, TransferProposal, ConsumableRule | StockMoved, BelowThreshold | config |
| `tech` | TechProcess, Operation, Step, Resource, Norm, TPApproval | TPApproved | calc |
| `production` | WorkOrder(наряд), WorkOrderSnapshot, OpExecution, TimerEvent, Substitution | WorkOrderClosed | tech, inventory |
| `quality` | ECR, ECO, Claim, CAPA, EffectivenessCheck | ECOReleased | projects, documents |
| `lifecycle` | ProductInstance, Passport, PassportTemplate, ServiceEvent, Replacement, Handover | — | production, documents, quality |
| `worktime` | DailyReport (смена, перерыв, строки по проектам и задачам) — ✅ D-42; Shift, Pause, WorkInterval как отдельные объекты — позже | — | org, projects |
| `comms` | Chat, Message, Attachment, MailLink, Notification | — | files, identity |
| `analytics` | Report definitions, Metric (source/period/access) | — | read-модели всех модулей |
| `cad3d` | Model3D, Model3DVersion, CadBinding | — | files |
| `integration` | Adapter config (без секретов), Outbox1C, ExchangeLog, Mapping | — | config |
| `ai` (выкл.) | Provider config, Prompt log (маскированный) | — | config |
| `sync` | DeviceCursor, конфликт-ответы | — | все (через команды) |

## 2. Ключевые объекты и связи (сокращённо)

```
Customer 1─* Contact          Customer 1─* Deal 1─* Tender 1─* TKP 1─* TKPApproval(snapshot hash, grant_id)
Tender 1─0..1 Project (start_rkd, tender_version_at_start)
Project 1─* ProjectAssignment(user, project_role, generation=id, valid_until, revoked_at)
Project 1─* Kit 1─* Document 1─* DocumentVersion(seq монотонный, label польз., blob hash)
DocumentVersion 1─* Remark       Issue *─* DocumentVersion (точные версии + hash)
Kit 1─1 KitCalculation(draft)  Kit 1─* KitCalcVersion(frozen, hash)
Project 1─* ProjectCalcRevision *─* KitCalcVersion (состав ревизии = точный список версий)
KitCalcVersion 1─* Need ─* PurchaseOrder ─* SupplyFact;  Item 1─* StockMovement; Reservation → Need
TechProcess(rev) ─* Operation;  WorkOrder → WorkOrderSnapshot(BOM+ТП, hash) 1─* OpExecution
WorkOrder 1─* ProductInstance(serial уникален в (изделие, серия)) 1─1 Passport(шаблон-версия)
ProductInstance 1─* ServiceEvent / Replacement / Claim;  Claim ─* CAPA;  ECR ─ ECO ─* (затронутые версии)
```

## 3. Владельцы данных (бизнес-ответственность)

| Данные | Владелец данных | Кто может исправлять | Источник истины |
|---|---|---|---|
| Учётные записи, роли | Администратор (по заявке руководителя) | Admin | `identity` |
| Оргструктура | Кадры/руководство (назначает владелец продукта — OQ-14) | Admin по заявке | `org` |
| Заказчики, контакты (ПДн) | Коммерческий блок (A-03) | gip/rp/dept_head | `commerce` |
| Тендер, ТКП | Ответственный тендера | см. матрицу | `commerce` |
| Проект, назначения | ГИП/РП проекта; ГИП назначает admin | см. матрицу | `projects` |
| Документы и версии | Автор комплекта / ведущий | автор, ведущий | `documents` + `files` |
| Расчёты комплектов | Ведущий комплекта | инженеры комплекта (черновик) | `calc` |
| Общий проектный итог | Вычисляется; ответственный — ГИП | никто вручную | `calc` |
| Остатки, движения | Склад | только движением/корректировкой с основанием | `inventory` |
| Заказы, поставки | Закупка | закупка | `procurement`; 1С — внешний учёт (связь через обмен) |
| ТП, нормы | Технолог | технолог | `tech` |
| Наряды и факт | Мастер/РП производства | исполнитель (свой факт) | `production` |
| Паспорт | ОТК/РП | по шаблону | `lifecycle` |
| Настройки | Admin | admin (публикация с аудитом) | `config` |

## 4. Жизненные циклы (статус меняется только явной командой)

**Тендер** *(реализовано в эталоне)*: `draft → in_work → bid_submitted → won | lost`; любое
нетерминальное → `cancelled`; причина обязательна для `lost`, `cancelled`.

**ТКП** *(реализовано)*: `draft → approved → sent_recorded`; `approved → draft` только явным «Вернуть в
черновик» с причиной (согласование помечается superseded). Правка — только в `draft`; каждая правка
увеличивает внутреннюю `revision`; метка `label` — пользовательская.

**Проект** *(частично)*: создаётся в `rkd_in_progress` командой «Начать разработку РКД». Далее
(спроектировано): `rkd_in_progress → rkd_issued → production → handed_over → warranty → closed`;
`on_hold` с причиной. Переходы — явные команды с правами ГИП/РП.

**Документ/версия** *(спроектировано)*: версия `uploaded → in_review → approved → issued`; замечания
`open → answered → closed | rejected`; выдача → запись отправки → запись получения (по основанию).

**Расчёт комплекта**: черновик (один на комплект, изменяемый) → `freeze` → неизменяемая версия K.n.
**Ревизия проектного итога**: создаётся только при фиксации; неизменяемая.

**Перенос файлов**: `requested → approved(РП) → preview → copying → verifying → relinking → completed
→ source_cleanup_pending → source_removed`; ветки `failed_copy`, `verify_mismatch`, `rolled_back`;
источник удаляется только после `completed` и отдельного шага (R-10).

**Складское движение**: `draft → posted` (неизменяемо); исправление — сторнирующим движением.
**Резерв**: `active → consumed | released | expired`.
**Пополнение расходника**: `draft (авто/ручн.) → procurement_confirmed → warehouse_confirmed → ordered | cancelled`.

**Наряд**: `draft → released → in_progress → completed → closed`; `cancelled` с причиной; снимок
фиксируется при `released`.

**ECR/ECO**: ECR `submitted → assessed → accepted | rejected`; ECO `draft → approved → implemented → verified`.
**Рекламация/CAPA**: `registered → analysis → action → effectiveness_check → closed`; закрытие требует
отдельной записи проверки эффективности (независимый проверяющий).
> Реализовано (D-39, схема v18): `claims`, `claim_actions`, `effectiveness_checks` (неизменяемая), `change_requests`,
> `change_orders` (строки — точные версии), `quality_decisions` (неизменяемый журнал шагов). Состояние «Выполнение
> действий» — `action`; проверяющий не может быть тем, кто планировал, выполнял или отправлял на проверку.
> D-40 (схема v19): `claim_terms` — охват (экземпляр / партия = наряд / проект), тяжесть и категория дефекта, срок ответа
> заказчику и момент его установки; `claim_responses` — неизменяемые ответы заказчику (промежуточный / окончательный).
> `claims.instance_id` необязателен (рекламация без заводского номера) — единственная миграция, меняющая существующий
> столбец. Срок «закрыт», когда после его установки записан ответ.

**Дневной отчёт**: `draft → in_review → submitted → accepted | returned`. Реализовано (D-42) упрощённо: `draft → submitted → accepted | returned`; «самопроверку» (`in_review`) выполняет программа перед отправкой; `returned` → правка → `submitted`; `submitted | accepted → draft` — исправление с основанием.

**Конфигурация**: `draft → validated → preview → published → superseded`; rollback = публикация
предыдущей версии новой записью.
