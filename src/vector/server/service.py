"""Application service сервера ВЕКТОР — единственная граница записи бизнес-данных.

Каждая команда — одна транзакция (SERIALIZABLE / BEGIN IMMEDIATE):
сеанс → права (в момент эффекта) → состояние/версия → эффект → журнал → результат операции.
Частичные эффекты при отказе откатываются SAVEPOINT; отказ и журнал отказа фиксируются.
Конфликт сериализации/дубль первичного ключа -> повтор всей транзакции (ограниченно).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from vector import __version__
from vector.core import modules as modules_core
from vector.core import money
from vector.core.contracts import Contracts
from vector.core.errors import CommandOutcome, Conflict, Denied, Rejected, Unauthenticated
from vector.core.redact import LEGACY_SECRET_COMMANDS, without_secrets

from . import db, security
from .authz import Authority, Policy, grant_is_active
from .calc import CalcMixin
from .config_store import ConfigMixin
from .consolidation import ConsolidationMixin
from .delegation import DelegationMixin
from .delivery import DeliveryMixin
from .office import OfficeMixin
from .rkd import RkdMixin
from .notify import NotificationMixin
from .lifecycle import LifecycleMixin
from .quality import QualityMixin
from .worktime import WorktimeMixin
from .org import OrgMixin
from .workday import WorkdayMixin
from .prodcal import CalendarMixin
from .schedule import ScheduleMixin
from .production import ProductionMixin
from .purchasing import PurchasingMixin
from .stock import StockMixin
from .work import WorkMixin
from .db import audit_log, customers, kits, operations, project_assignments, projects, role_grants, sessions, tenders, tkp, tkp_approvals, users

log = logging.getLogger("vector.server")

# Сколько раз повторять транзакцию при конфликте сериализации/взаимоблокировке СУБД (паузы 10, 20, 40… мс).
MAX_ATTEMPTS = 6


class SystemClock:
    """Источник текущего времени (UTC). В тестах подменяется управляемыми часами — так проверяются сроки сеансов и
    блокировок без ожидания.
    """
    def now(self) -> datetime:
        """Текущее время в UTC (с часовым поясом — «наивное» время в системе не используется)."""
        return datetime.now(timezone.utc)


# Валидаторы и сериализация вынесены в validation.py (используются и модулем задач/документов);
# импортируются сюда под прежними именами, чтобы не менять существующий код и тесты.
from .validation import (_CUR_RE, _INN_RE, _LOGIN_RE, _UUID_RE, canon, iso, only_keys,  # noqa: E402,F401
                         opt_date, opt_int, opt_str, parse_iso_utc, req_str, req_uuid)


class Ctx:
    """Контекст выполнения одной команды: соединение (внутри транзакции), пользователь, устройство, id операции, время,
    адрес клиента.

    Обработчики пишут записи аудита не напрямую, а через ctx.log: записи копятся в ctx.audit и попадают в журнал
    только при успехе команды (при отказе — одна запись об отказе).
    """
    def __init__(self, conn: Connection, user: dict, device_id: str, op_id: str, now: datetime, command: str,
                 session_hash: str, client_addr: Optional[str]):
        """Сохраняет всё, что нужно обработчику и журналу; время now одно на всю команду (все записи получают
        одинаковую метку).
        """
        self.conn, self.user, self.user_id = conn, user, user["id"]
        self.device_id, self.operation_id, self.now, self.command = device_id, op_id, now, command
        self.session_hash, self.client_addr = session_hash, client_addr
        self.audit: list[dict] = []

    def log(self, otype, oid, authority: Optional[Authority], before, after, details=None):
        """Запланировать запись в журнал аудита: тип и id объекта, основание доступа (Authority), версия до/после,
        подробности (что изменилось, причина).
        """
        self.audit.append(dict(object_type=otype, object_id=str(oid) if oid is not None else None,
                               authority=authority.describe() if authority else None,
                               version_before=before, version_after=after, details=details or {}))


class Service(WorkMixin, CalcMixin, StockMixin, PurchasingMixin, ConfigMixin, DelegationMixin, NotificationMixin,
              ProductionMixin, LifecycleMixin, QualityMixin, WorktimeMixin, OrgMixin, WorkdayMixin,
              ScheduleMixin, CalendarMixin, DeliveryMixin, RkdMixin, ConsolidationMixin, OfficeMixin):
    """Сервис команд. Задачи, документы и файлы — в примеси WorkMixin (work.py); расчёты — CalcMixin (calc.py);
    склад и закупочная потребность — StockMixin (stock.py); настройки компании — ConfigMixin (config_store.py);
    замещение и основания решений — DelegationMixin (delegation.py, D-34/D-35); уведомления — NotificationMixin
    (notify.py, D-35); технология и производство (ТП, наряды, снимок, исполнение операций) — ProductionMixin
    (production.py, D-36); экземпляры изделий, паспорт, передача, сервис и замены — LifecycleMixin (lifecycle.py, D-38);
    рекламации, CAPA, запросы и извещения об изменении — QualityMixin (quality.py, D-39); рабочее время и дневные
    отчёты — WorktimeMixin (worktime.py, D-42); оргструктура (филиалы, структуры, отделы, руководители и сотрудники)
    и маршрут проверки отчётов — OrgMixin (org.py, D-44); таймер рабочего дня и фактическое начало дня —
    WorkdayMixin (workday.py, D-46); графики работы — ScheduleMixin (schedule.py, D-47);
    производственный календарь — CalendarMixin (prodcal.py, D-48). Лиды, потоки проекта, риск-разрешения и архив —
    DeliveryMixin (delivery.py, D-56). Ревизии РКД: проверка, замечания, трансмитталы — RkdMixin (rkd.py,
    этап 3 blueprint 52). Консолидация закупок и аналоги — ConsolidationMixin (consolidation.py, этап 4, 52).

    Как устроен обработчик команды (все _xxx_yyy(ctx, p, ev) следуют одной схеме — так их легко проверять):

    1. only_keys(p, {...}) — неизвестные поля запроса → отказ (опечатка не должна молча игнорироваться);
    2. self.policy.check(..., project_id=...) — право в момент эффекта; для проектных объектов project_id
       обязателен, иначе роли в проекте не учитываются;
    3. загрузка объекта (_require), проверка версии (_check_version) и состояния (статус, правила домена);
    4. запись (insert / _update_versioned) — только после всех проверок;
    5. ctx.log(...) — что изменилось и на каком основании (попадёт в журнал, только если команда успешна);
    6. возврат представления объекта (v_xxx) — клиент кладёт его в свой кэш по ключу результата.

    Ожидаемые отказы — исключения Rejected / Denied / Conflict (vector.core.errors) с русским сообщением.
    """

    def __init__(self, engine: Engine, contracts: Optional[Contracts] = None, clock=None,
                 sec: Optional[security.SecurityPolicy] = None, vendor_license=None):
        """Собрать сервис: контракты, матрица прав, часы, политика безопасности, ограничители частоты и таблица
        обработчиков команд.

        При старте сверяется каталог команд (contracts) и таблица handlers: команда без обработчика или обработчик
        без команды в каталоге — ошибка запуска, а не «тихо не работает».
        """
        self.engine = engine
        self.contracts = contracts or Contracts.load()
        self.policy = Policy(self.contracts.permissions)
        self.wf = self.contracts.workflows
        self.clock = clock or SystemClock()
        self.sec = sec or security.SecurityPolicy()
        self.sec.validate()
        self.rate = security.RateLimiter(self.sec.login_rate_per_minute_per_addr)
        # Подбор текущего пароля через украденный сеанс: не чаще lockout_threshold попыток в минуту на пользователя.
        self.pwd_rate = security.RateLimiter(self.sec.lockout_threshold)
        # Неверные пароли подписи актов (D-31) — отдельный счётчик и только неудачи: подписывать много актов подряд
        # можно, перебирать пароль — нет.
        self.sign_rate = security.RateLimiter(self.sec.lockout_threshold)
        # Лицензия установки (D-80/D-81): vector.core.modules.License или None (демо/стенд — всё доступно).
        # Статус считается на дату «сейчас» и кэшируется на день: истёкшая подписка начинает действовать
        # без перезапуска сервера, а суточный кэш не считает состояние на каждую команду.
        self._license = vendor_license
        self._license_cache: Optional[tuple[Any, modules_core.LicenseStatus]] = None
        self._fault_after_effect: Optional[str] = None
        self.handlers: dict[str, Callable[[Ctx, dict, Optional[int]], dict]] = {
            "customer.create": self._customer_create, "customer.update": self._customer_update,
            "tender.create": self._tender_create, "tender.update": self._tender_update, "tender.transition": self._tender_transition,
            "tkp.create": self._tkp_create, "tkp.update": self._tkp_update, "tkp.approve": self._tkp_approve,
            "tkp.reopen": self._tkp_reopen, "tkp.record_sent": self._tkp_record_sent,
            "project.start_rkd": self._project_start_rkd, "project.assign": self._project_assign,
            "project.revoke_assignment": self._project_revoke_assignment,
            # Лиды, потоки проекта, риск-разрешения и архив (delivery.py, D-56).
            "opportunity.create": self._opportunity_create, "opportunity.update": self._opportunity_update,
            "opportunity.transition": self._opportunity_transition,
            "opportunity.convert_to_tender": self._opportunity_convert_to_tender,
            "project.transition": self._project_transition,
            "workstream.update": self._workstream_update, "workstream.set_status": self._workstream_set_status,
            "risk.accept": self._risk_accept, "risk.resolve": self._risk_resolve,
            "checklist.create": self._checklist_create, "checklist.update": self._checklist_update,
            "checklist.archive": self._checklist_archive, "checklist.unarchive": self._checklist_unarchive,
            # ПНР и FAT-сессии с замечаниями; шлюз отгрузки — в workstream.set_status (D-68, этап 6 blueprint 52).
            "pnr.create": self._pnr_create, "pnr.set_status": self._pnr_set_status,
            "fat.create": self._fat_create, "fat.finish": self._fat_finish,
            "fat.add_finding": self._fat_add_finding, "fat.close_finding": self._fat_close_finding,
            # Документы доставки отдела логистики: настраиваемые формы (D-69, этап 6 blueprint 52).
            "office_doc.create": self._office_doc_create, "office_doc.set_template": self._office_doc_set_template,
            "templates.render": self._office_doc_render,
            "doc_template.create": self._doc_template_create, "doc_template.update": self._doc_template_update,
            "doc_template.set_active": self._doc_template_set_active,
            "delivery_doc.create": self._delivery_doc_create, "delivery_doc.update": self._delivery_doc_update,
            # Нормы трудоёмкости: агрегированный диапазон + ручное утверждение (D-72, этап 7 blueprint 52).
            "norm.propose": self._norm_propose, "norm.decide": self._norm_decide,
            # Ревизии РКД: проверка, замечания, трансмитталы (rkd.py, этап 3 blueprint 52).
            "docrev.create": self._docrev_create, "docrev.transition": self._docrev_transition,
            "comment.add": self._comment_add, "comment.respond": self._comment_respond,
            "transmittal.create": self._transmittal_create,
            "transmittal.register_receipt": self._transmittal_register_receipt,
            # Профили шифров и статусы выдачи (D-62, этап 3б blueprint 52).
            "docprofile.create": self._docprofile_create, "docprofile.update": self._docprofile_update,
            "docprofile.activate": self._docprofile_activate, "docprofile.preview": self._docprofile_preview,
            "docrev.set_issue": self._docrev_set_issue,
            # Консолидация закупок и аналоги (consolidation.py, этап 4 blueprint 52).
            "pgroup.create": self._pgroup_create, "pgroup.update": self._pgroup_update,
            "batch.create": self._batch_create, "batch.update": self._batch_update,
            "batch.transition": self._batch_transition,
            "analog.propose": self._analog_propose, "analog.decide": self._analog_decide,
            "kit.create": self._kit_create, "kit.update": self._kit_update,
            "account.change_password": self._account_change_password,
            "admin.create_user": self._admin_create_user, "admin.grant_role": self._admin_grant_role,
            "admin.revoke_role": self._admin_revoke_role, "admin.revoke_sessions": self._admin_revoke_sessions,
            "admin.reset_password": self._admin_reset_password, "admin.set_user_active": self._admin_set_user_active,
            # настройки компании (config_store.py)
            "config.publish": self._config_publish,
            # задачи и документы (work.py)
            "task.create": self._task_create, "task.update": self._task_update, "task.transition": self._task_transition,
            "document.create": self._document_create, "document.update": self._document_update,
            "document.add_version": self._document_add_version,
            # расчёт комплекта → фиксация → ревизия проектного итога (calc.py)
            "calc.save_draft": self._calc_save_draft, "calc.freeze": self._calc_freeze,
            # склад и закупочная потребность (stock.py)
            "warehouse.create": self._warehouse_create, "warehouse.update": self._warehouse_update,
            "item.create": self._item_create, "item.update": self._item_update,
            "item_alias.create": self._item_alias_create, "item_alias.update": self._item_alias_update,
            "unit_conversion.create": self._unit_conversion_create, "unit_conversion.update": self._unit_conversion_update,
            "stock.receive": self._stock_receive, "stock.issue": self._stock_issue, "stock.reverse": self._stock_reverse,
            "stock.transfer": self._stock_transfer, "stock.transfer_accept": self._stock_transfer_accept,
            "stock.transfer_cancel": self._stock_transfer_cancel,
            "stock.transfer_lost": self._stock_transfer_lost,
            "stock.reserve": self._stock_reserve, "stock.release": self._stock_release,
            "need.generate": self._need_generate, "need.match_line": self._need_match_line,
            "stock.adjust": self._stock_adjust, "stock.adjust_sign": self._stock_adjust_sign,
            "stock.adjust_cancel": self._stock_adjust_cancel,
            "proposal.propose": self._proposal_propose, "proposal.withdraw": self._proposal_withdraw,
            "proposal.decide": self._proposal_decide,
            "stock.replen_rule_set": self._replen_rule_set, "stock.replen_rule_update": self._replen_rule_update,
            "stock.replen_confirm": self._replen_confirm, "stock.replen_dismiss": self._replen_dismiss,
            # поставщики, заказы поставщикам и факт поставки (purchasing.py, D-32)
            "supplier.create": self._supplier_create, "supplier.update": self._supplier_update,
            "po.create": self._po_create, "po.update": self._po_update, "po.place": self._po_place,
            "po.cancel": self._po_cancel, "po.close": self._po_close, "po.receive": self._po_receive,
            # Договоры поставщиков и согласование заказов (D-33).
            "contract.create": self._contract_create, "contract.update": self._contract_update,
            "po.submit": self._po_submit, "po.withdraw": self._po_withdraw, "po.approve": self._po_approve,
            "po.return": self._po_return,
            # Замещение — передача роли на время (delegation.py, D-34).
            "delegation.create": self._delegation_create, "delegation.revoke": self._delegation_revoke,
            # Уведомления в программе (notify.py, D-35).
            "notification.read": self._notification_read,
            # Технология и производство (production.py, D-36).
            "tp.create": self._tp_create, "tp.update": self._tp_update, "tp.approve": self._tp_approve,
            "wo.release": self._wo_release, "wo.assign": self._wo_assign, "wo.op_event": self._wo_op_event,
            "wo.substitute": self._wo_substitute, "wo.close": self._wo_close, "wo.cancel": self._wo_cancel,
            # Экземпляры изделий и паспорт (lifecycle.py, D-38).
            "instance.register": self._instance_register, "instance.handover": self._instance_handover,
            "instance.service": self._instance_service, "instance.replace": self._instance_replace,
            "passport.create": self._passport_create, "passport.save": self._passport_save,
            "passport.export_draft": self._passport_export_draft,
            # Качество: рекламации, CAPA, ECR, ECO (quality.py, D-39).
            "claim.register": self._claim_register, "claim.start_analysis": self._claim_start_analysis,
            "claim.plan": self._claim_plan, "claim.action_done": self._claim_action_done,
            "claim.submit_check": self._claim_submit_check, "claim.check": self._claim_check,
            # D-40: классификация дефекта, срок ответа и ответы заказчику.
            "claim.classify": self._claim_classify, "claim.set_due": self._claim_set_due, "claim.respond": self._claim_respond,
            "ecr.submit": self._ecr_submit, "ecr.assess": self._ecr_assess, "ecr.decide": self._ecr_decide,
            "eco.create": self._eco_create, "eco.save": self._eco_save, "eco.approve": self._eco_approve,
            "eco.implement": self._eco_implement, "eco.verify": self._eco_verify,
            # Рабочее время и дневной отчёт (worktime.py, D-42).
            "report.create": self._report_create, "report.save": self._report_save, "report.submit": self._report_submit,
            "report.reopen": self._report_reopen, "report.review": self._report_review,
            "overtime.period_set": self._overtime_period_set, "overtime.period_close": self._overtime_period_close,
            "overtime.request": self._overtime_request_create, "overtime.decide": self._overtime_request_decide,
            # ТК-контур сверхурочных: кадровые категории (worktime.py, D-74).
            "hr.category_set": self._hr_category_set,
            # Оргструктура: подразделения и сотрудники в них (org.py, D-44).
            "org.unit_create": self._org_unit_create, "org.unit_update": self._org_unit_update,
            "org.unit_archive": self._org_unit_archive, "org.unit_restore": self._org_unit_restore,
            "org.member_set": self._org_member_set,
            # Таймер рабочего дня и фактическое начало дня (workday.py, D-46).
            "workday.mark": self._workday_mark, "workday.start_request": self._workday_start_request,
            "workday.start_decide": self._workday_start_decide,
            # Графики работы, забытое «Завершить» и фактическое окончание дня (schedule.py, workday.py; D-47).
            "workday.end_request": self._workday_end_request, "workday.end_decide": self._workday_end_decide,
            "schedule.save": self._schedule_save, "schedule.assign": self._schedule_assign,
            "calendar.import": self._calendar_import, "calendar.day_set": self._calendar_day_set,
        }
        diff = set(self.contracts.catalog["commands"]) ^ set(self.handlers)
        if diff:
            raise RuntimeError(f"Каталог команд и обработчики расходятся: {sorted(diff)}")

    # ------------------------------------------------------------ инфраструктура
    def now(self) -> datetime:
        """Текущее время по часам сервиса (подменяемым в тестах)."""
        return self.clock.now()

    def _tx(self, fn: Callable[[Connection], Any]) -> Any:
        """Выполнить fn(conn) в транзакции с ограниченным повтором (до MAX_ATTEMPTS, с нарастающей паузой).

        Повторяются только безопасные случаи: конфликт сериализации/взаимоблокировка СУБД и нарушение уникальности
        (параллельная вставка того же operation_id — повтор увидит сохранённый ответ). Поэтому fn обязана не иметь
        побочных эффектов вне БД — она может выполниться несколько раз.
        """
        for attempt in range(MAX_ATTEMPTS):
            try:
                with self.engine.begin() as conn:
                    return fn(conn)
            except sa.exc.IntegrityError:
                # Например, параллельная вставка того же operation_id: повтор увидит сохранённый исход.
                if attempt == MAX_ATTEMPTS - 1:
                    raise
            except sa.exc.DBAPIError as e:
                if not db.is_retryable(e) or attempt == MAX_ATTEMPTS - 1:
                    raise
            time.sleep(0.01 * (2 ** attempt))
        raise RuntimeError("unreachable")

    def _audit(self, conn, at, actor, device, addr, op_id, command, otype, oid, authority, vb, va, result, details):
        """Записать одну строку журнала аудита (append-only: правка и удаление строк запрещены триггерами БД).
        Подробности хранятся каноническим JSON.
        """
        conn.execute(audit_log.insert().values(
            at=at, actor_user_id=actor, device_id=device, client_addr=addr, operation_id=op_id, command=command,
            object_type=otype, object_id=oid, authority=authority, version_before=vb, version_after=va,
            result=result, details_json=canon(details)))

    @staticmethod
    def _get(conn, table, key, col="id") -> Optional[dict]:
        """Одна строка таблицы по ключу (по умолчанию id) как словарь, или None."""
        r = conn.execute(sa.select(table).where(table.c[col] == key)).first()
        return dict(r._mapping) if r else None

    def bootstrap_admin(self, login: str, password: str, display_name: str = "Администратор") -> str:
        """Однократно: только если учётных записей нет (F-10-подобная защита от повторного bootstrap)."""
        problems = security.password_problems(password, login, self.sec)
        if problems or not _LOGIN_RE.match(login):
            raise Rejected("weak_password", " ".join(problems) or "Недопустимый логин.")

        def tx(conn):
            """Транзакция создания первого администратора: только если пользователей ещё нет (повторный bootstrap
            запрещён).
            """
            if conn.execute(sa.select(sa.func.count()).select_from(users)).scalar():
                raise Rejected("already_bootstrapped", "Система уже инициализирована; повторный bootstrap запрещён.")
            now, uid = self.now(), str(uuid.uuid4())
            conn.execute(users.insert().values(id=uid, login=login, display_name=display_name,
                                               pwd_hash=security.hash_password(password), must_change_password=False,
                                               password_changed_at=now, failed_attempts=0, active=True, created_at=now))
            gid = conn.execute(role_grants.insert().values(user_id=uid, role="admin", scope_type="company", valid_from=now,
                                                           reason="bootstrap")).inserted_primary_key[0]
            self._audit(conn, now, None, None, None, None, "system.bootstrap", "user", uid, f"bootstrap#grant:{gid}", None, 1,
                        "accepted", {"login": login})
            return uid
        return self._tx(tx)

    # ------------------------------------------------------------ лицензия (D-81)
    def license_status(self) -> modules_core.LicenseStatus:
        """Состояние лицензии на сегодня (кэш на дату)."""
        today = self.now().date()
        if self._license_cache is None or self._license_cache[0] != today:
            self._license_cache = (today, modules_core.evaluate(self._license, today))
        return self._license_cache[1]

    def _license_block(self, conn, now) -> dict:
        """Блок лицензии для pull: состояние модулей + занятые именные места (D-85, мягкий контроль A-64).

        Место = пользователь с хотя бы одним живым сеансом. Превышение не блокирует работу (не выкидываем людей
        посреди смены): администратор видит превышение в «О программе», каждый вход сверх мест — в журнале."""
        block = modules_core.license_block(self.license_status())
        lic = self._license
        if lic is not None and lic.seats is not None:
            in_use = conn.execute(
                sa.select(sa.func.count(sa.distinct(sessions.c.user_id)))
                .where(sessions.c.revoked_at.is_(None), sessions.c.expires_at > now)).scalar() or 0
            block["seats"] = lic.seats
            block["seats_in_use"] = in_use
            block["seats_exceeded"] = in_use > lic.seats
        return block

    def _license_denied(self, command: str) -> Optional[dict]:
        """Отказ команды по лицензии или None. Модуль вне лицензии — изменения запрещены; подписка истекла —
        «только чтение»: чтение/печать/выгрузка (query) доступны всегда, изменения — после продления. Данные
        клиента не становятся заложниками счёта (D-80)."""
        mod = modules_core.command_module(command)
        if mod is None:
            return None                                              # платформа — всегда
        st = self.license_status().modules[mod]
        if st.state in ("active", "grace"):
            return None
        m = modules_core.MODULE_BY_ID[mod]
        if st.state == "readonly":
            return {"status": "rejected", "code": "module_readonly",
                    "message_ru": f"Подписка на модуль «{m.title_ru}» истекла: чтение, печать и выгрузка доступны, "
                                  "изменения — после продления. Данные сохраняются в полном объёме.",
                    "details": {"module": mod, "module_state": st.state}}
        return {"status": "rejected", "code": "module_not_licensed",
                "message_ru": f"Модуль «{m.title_ru}» не входит в лицензию этой установки. Обратитесь к поставщику "
                              "или администратору — раздел можно подключить.",
                "details": {"module": mod, "module_state": st.state}}

    # ------------------------------------------------------------ точка входа
    def handle(self, req: dict, client_addr: Optional[str] = None) -> dict:
        """Единая точка входа сервера: конверт {type: login|logout|command|query, …} → ответ-словарь.

        Любая неожиданная ошибка превращается в ответ status=error без подробностей (стек — только в журнал
        сервера): транзакция откачена, изменение не применено, повтор той же операции безопасен.
        """
        try:
            t = req.get("type") if isinstance(req, dict) else None
            if t == "login":
                return self._login(req, client_addr)
            if t == "logout":
                return self._logout(req)
            if t == "command":
                return self._command(req, client_addr)
            if t == "query":
                return self._query(req)
            return {"status": "rejected", "code": "bad_type", "message_ru": "Неизвестный тип запроса."}
        except Exception:
            log.exception("internal error")  # стек — только в журнале сервера; секретов в запросах журнал не содержит
            return {"status": "error", "code": "internal_error", "operation_id": req.get("operation_id") if isinstance(req, dict) else None,
                    "message_ru": "Внутренняя ошибка сервера. Изменение не применено; повтор безопасен."}

    # ------------------------------------------------------------ вход/выход
    def _login(self, req: dict, addr: Optional[str]) -> dict:
        """Вход по логину и паролю.

        Защиты: ограничение частоты попыток с одного адреса; одинаковый ответ для «нет такого логина», «неверный
        пароль» и «учётка отключена» (нельзя перебором узнать логины); время проверки выровнено фиктивным хэшем;
        после lockout_threshold неудач подряд — блокировка на lockout_minutes. При успехе — новый токен сеанса (в БД
        — только его хэш) и параметры для клиента (простой сеанса, предел размера файла).
        """
        login, password, device = req.get("login"), req.get("password"), req.get("device_id")
        if not all(isinstance(x, str) and x for x in (login, password, device)) or len(device) > 100 or len(login) > 64:
            return {"status": "rejected", "code": "bad_login_request", "message_ru": "Укажите логин, пароль и идентификатор устройства."}
        if not self.rate.allow(addr or "local", self.now().timestamp()):
            return {"status": "rejected", "code": "rate_limited", "message_ru": "Слишком много попыток входа. Подождите минуту."}
        generic = {"status": "unauthenticated", "code": "bad_credentials",
                   "message_ru": "Неверный логин или пароль, либо учётная запись отключена."}

        def tx(conn):
            """Транзакция входа: проверка пароля, счётчик неудач/блокировка, создание сеанса, запись в журнал."""
            now = self.now()
            u = self._get(conn, users, login, "login")
            ok, rehash = security.verify_password(u["pwd_hash"] if u else None, password)
            if u and u["locked_until"] and u["locked_until"] > now:
                self._audit(conn, now, u["id"], device, addr, None, "auth.login", "user", u["id"], None, None, None, "denied", {"reason": "locked"})
                return {"status": "unauthenticated", "code": "account_locked",
                        "message_ru": f"Учётная запись временно заблокирована после неудачных попыток. Повторите после {u['locked_until'].astimezone(timezone.utc):%H:%M} UTC или обратитесь к администратору.",
                        "locked_until": iso(u["locked_until"])}
            if not u or not ok or not u["active"]:
                if u and not ok:
                    fails = u["failed_attempts"] + 1
                    vals: dict[str, Any] = {"failed_attempts": fails}
                    if fails >= self.sec.lockout_threshold:
                        vals = {"failed_attempts": 0, "locked_until": now + timedelta(minutes=self.sec.lockout_minutes)}
                    conn.execute(users.update().where(users.c.id == u["id"]).values(**vals))
                self._audit(conn, now, u["id"] if u else None, device, addr, None, "auth.login", "user", u["id"] if u else None,
                            None, None, None, "denied", {"login": login, "reason": "inactive" if (u and ok) else "bad_credentials"})
                return generic
            vals = {"failed_attempts": 0, "locked_until": None}
            if rehash:
                vals["pwd_hash"] = rehash
            conn.execute(users.update().where(users.c.id == u["id"]).values(**vals))
            token, th = security.new_token()
            exp = now + timedelta(hours=self.sec.session_absolute_hours)
            conn.execute(sessions.insert().values(token_hash=th, user_id=u["id"], device_id=device, client_addr=addr,
                                                  issued_at=now, last_seen_at=now, expires_at=exp))
            self._audit(conn, now, u["id"], device, addr, None, "auth.login", "user", u["id"], None, None, None, "accepted", {})
            lic = self._license
            if lic is not None and lic.seats is not None:
                in_use = conn.execute(
                    sa.select(sa.func.count(sa.distinct(sessions.c.user_id)))
                    .where(sessions.c.revoked_at.is_(None), sessions.c.expires_at > now)).scalar() or 0
                if in_use > lic.seats:
                    # Мягко (A-64): вход разрешён, превышение зафиксировано — администратор видит его в pull
                    # и «О программе»; событие в журнале — основание для разговора о расширении лицензии.
                    self._audit(conn, now, u["id"], device, addr, None, "license.seats_exceeded", "user", u["id"],
                                None, None, None, "accepted", {"seats": lic.seats, "in_use": in_use})
            return {"status": "accepted", "token": token, "user_id": u["id"], "display_name": u["display_name"],
                    "login": u["login"], "must_change_password": bool(u["must_change_password"]), "expires_at": iso(exp),
                    "idle_timeout_minutes": self.sec.session_idle_minutes,
                    # Предел размера файла — чтобы клиент отказал понятно ДО отправки (issue #9). Только подсказка
                    # для интерфейса: сервер проверяет размер сам при каждой загрузке и клиенту не доверяет.
                    "max_file_bytes": self.sec.max_file_mb * 1024 * 1024}
        return self._tx(tx)

    def _logout(self, req: dict) -> dict:
        """Выход: отозвать сеанс по токену. Повторный выход безопасен (revoked = 0)."""
        th = security.token_hash(req.get("token") or "")

        def tx(conn):
            """Транзакция выхода: пометить сеанс отозванным."""
            now = self.now()
            n = conn.execute(sessions.update().where(sessions.c.token_hash == th, sessions.c.revoked_at.is_(None))
                             .values(revoked_at=now, revoked_reason="logout")).rowcount
            return {"status": "accepted", "revoked": n}
        return self._tx(tx)

    def _auth(self, conn, token, now, allow_restricted=False) -> tuple[dict, str, str]:
        """Проверить токен сеанса и вернуть (пользователь, устройство, хэш токена).

        Сеанс недействителен, если отозван, истёк абсолютный срок или превышен простой. Пользователь с временным
        паролем может выполнить только смену пароля (allow_restricted). При успехе обновляется время последней
        активности сеанса.
        """
        if not isinstance(token, str) or not token:
            raise Unauthenticated("no_token", "Требуется вход в систему.")
        th = security.token_hash(token)
        s = self._get(conn, sessions, th, "token_hash")
        if (not s or s["revoked_at"] is not None or s["expires_at"] <= now
                or now - s["last_seen_at"] > timedelta(minutes=self.sec.session_idle_minutes)):
            raise Unauthenticated("session_invalid", "Сеанс истёк или завершён. Войдите снова; локальная очередь изменений сохранится.")
        u = self._get(conn, users, s["user_id"])
        if not u or not u["active"]:
            raise Unauthenticated("session_invalid", "Учётная запись отключена.")
        if u["must_change_password"] and not allow_restricted:
            raise Unauthenticated("password_change_required", "Перед началом работы смените временный пароль.")
        conn.execute(sessions.update().where(sessions.c.token_hash == th).values(last_seen_at=now))
        return u, s["device_id"], th

    # ------------------------------------------------------------ команды
    def _command(self, req: dict, addr: Optional[str]) -> dict:
        """Выполнить команду изменения с защитой от повторов и полным журналом.

        Порядок: проверка формы конверта → хэш запроса → транзакция: сеанс → если operation_id уже был — вернуть
        сохранённый ответ (или отказ, если под тем же id пришли другие данные) → обработчик внутри SAVEPOINT →
        журнал → запись исхода в operations. При отказе (CommandOutcome) эффекты обработчика откатываются до
        SAVEPOINT, но сам отказ и его исход фиксируются — повтор вернёт тот же ответ.
        """
        op_id, command, payload, ev = req.get("operation_id"), req.get("command"), req.get("payload"), req.get("expected_version")
        if not isinstance(op_id, str) or not _UUID_RE.match(op_id):
            return {"status": "rejected", "code": "bad_operation_id", "message_ru": "operation_id должен быть UUID."}
        if command not in self.handlers:
            return {"status": "rejected", "code": "unknown_command", "message_ru": f"Неизвестная команда «{command}»."}
        denied = self._license_denied(command)
        if denied is not None:                                       # как unknown_command: до транзакции, повтор
            denied["operation_id"] = op_id                           # даёт тот же отказ без записи в operations
            return denied
        if not isinstance(payload, dict):
            return {"status": "rejected", "code": "bad_payload", "message_ru": "payload должен быть объектом."}
        if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
            return {"status": "rejected", "code": "bad_expected_version", "message_ru": "expected_version должен быть целым числом."}
        # Секреты (пароли) в хэш НЕ входят вовсе: иначе по хэшу без соли пароль подбирается в обход argon2
        # (vector.core.redact). Повтор того же operation_id с другим паролем и теми же остальными данными считается тем
        # же запросом — он вернёт сохранённый исход и ничего не выполнит повторно.
        request_hash = hashlib.sha256(canon({"c": command, "p": without_secrets(payload), "v": ev}).encode()).hexdigest()

        def tx(conn):
            """Транзакция одной команды (см. описание _command). Может выполниться повторно при конфликте сериализации
            — поэтому без внешних эффектов.
            """
            now = self.now()
            prev = self._get(conn, operations, op_id, "operation_id")
            try:
                user, device, th = self._auth(conn, req.get("token"), now, allow_restricted=(command == "account.change_password"))
            except Unauthenticated as e:
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            if prev:
                if prev["request_hash"] != request_hash or prev["user_id"] != user["id"]:
                    return {"status": "rejected", "code": "operation_id_reused",
                            "message_ru": "Этот operation_id уже использован для другой операции. Повтор с изменёнными данными запрещён."}
                stored = json.loads(prev["response_json"])
                stored["replayed"] = True
                return stored
            ctx = Ctx(conn, user, device, op_id, now, command, th, addr)
            sp = conn.begin_nested()
            try:
                if self.contracts.catalog["commands"][command].get("requires_expected_version") and ev is None:
                    raise Rejected("expected_version_required", "Для изменения нужна версия, на которой основана правка (expected_version).")
                result = self.handlers[command](ctx, payload, ev)
                response = {"status": "accepted", "operation_id": op_id, "result": result}
                for a in ctx.audit:
                    self._audit(conn, now, user["id"], device, addr, op_id, command, a["object_type"], a["object_id"],
                                a["authority"], a["version_before"], a["version_after"], "accepted", a["details"])
                if self._fault_after_effect == command:
                    raise RuntimeError("injected fault after effect, before commit")
                sp.commit()
            except CommandOutcome as e:
                sp.rollback()
                response = {"status": e.status, "operation_id": op_id, "code": e.code, "message_ru": e.message_ru, "details": e.details}
                self._audit(conn, now, user["id"], device, addr, op_id, command, None, None, None, None, None, e.status,
                            {"code": e.code, **({"details": e.details} if e.details else {})})
            conn.execute(operations.insert().values(operation_id=op_id, user_id=user["id"], device_id=device, command=command,
                                                    request_hash=request_hash, status=response["status"],
                                                    response_json=canon(response), recorded_at=now))
            return response
        return self._tx(tx)

    def redact_legacy_secret_hashes(self, apply: bool = False) -> int:
        """Старые записи operations команд с паролями (до исправления хэш считался вместе с паролем) — сколько их и,
        если apply, заменить их хэш меткой «redacted». Ответ и исход операции не меняются; повтор такого старого
        operation_id получит «operation_id уже использован» — это безопасно (операции давно завершены).
        """
        cond = sa.and_(operations.c.command.in_(LEGACY_SECRET_COMMANDS), operations.c.request_hash != "redacted")
        with self.engine.begin() as conn:
            n = conn.execute(sa.select(sa.func.count()).select_from(operations).where(cond)).scalar_one()
            if apply and n:
                conn.execute(operations.update().where(cond).values(request_hash="redacted"))
        return n

    # ------------------------------------------------------------ helpers
    def _require(self, conn, table, oid, what_ru) -> dict:
        """Объект по id или отказ not_found с русским названием («Тендер: объект не найден»)."""
        o = self._get(conn, table, oid)
        if o is None:
            raise Rejected("not_found", f"{what_ru}: объект не найден.", {"id": oid})
        return o

    @staticmethod
    def _check_version(o: dict, expected: Optional[int], view):
        """Оптимистическая блокировка: если клиент правил версию expected, а на сервере уже другая — Conflict с текущим
        состоянием объекта (клиент покажет «моё / на сервере» и не сольёт молча).
        """
        if expected is not None and o["version"] != expected:
            raise Conflict("version_conflict",
                           f"Объект изменён другим пользователем: ваша версия {expected}, на сервере {o['version']}. "
                           "Изменение не применено; сравните и решите, повторить ли его.",
                           {"expected_version": expected, "current": view(o)})

    def _ensure_new(self, conn, table, oid):
        """Убедиться, что объекта с этим id ещё нет (id создаёт клиент; повтор уже выполненной операции сюда не доходит
        — его ловит operations).
        """
        if self._get(conn, table, oid) is not None:
            raise Rejected("id_exists", "Объект с таким идентификатором уже существует.", {"id": oid})

    def _update_versioned(self, ctx, table, o, values: dict):
        """Обновить строку с увеличением версии; условие WHERE version = текущей — вторая защита от параллельной правки
        поверх уровня изоляции.
        """
        n = ctx.conn.execute(table.update().where(table.c.id == o["id"], table.c.version == o["version"])
                             .values(version=o["version"] + 1, **values)).rowcount
        if n != 1:  # дополнительная защита поверх SERIALIZABLE
            raise Conflict("version_conflict", "Объект изменён параллельно. Повторите после обновления.", {"current": None})
        return self._get(ctx.conn, table, o["id"])

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_customer(o):
        """Заказчик для клиента (поля, версия, время изменения)."""
        return {"id": o["id"], "name": o["name"], "inn": o["inn"], "note": o["note"], "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_tender(o):
        """Тендер для клиента: статус, причина статуса, срок подачи, ответственный, версия."""
        return {"id": o["id"], "customer_id": o["customer_id"], "title": o["title"],
                "submission_deadline": o["submission_deadline"].isoformat() if o["submission_deadline"] else None,
                "owner_user_id": o["owner_user_id"], "status": o["status"], "status_reason": o["status_reason"],
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_tkp(o):
        """ТКП для клиента: сумма строкой (Decimal), валюта, ревизия содержимого, статус, факт отправки, автор
        последней правки.
        """
        return {"id": o["id"], "tender_id": o["tender_id"], "label": o["label"], "revision": o["revision"],
                "amount": money.to_json(o["amount"]), "currency": o["currency"], "note": o["note"], "status": o["status"],
                "sent_on": o["sent_on"].isoformat() if o["sent_on"] else None, "sent_channel": o["sent_channel"],
                "version": o["version"], "last_edited_by": o["last_edited_by"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_project(o):
        """Проект для клиента: код, название, тендер-основание, статус, версия."""
        return {"id": o["id"], "code": o["code"], "title": o["title"], "tender_id": o["tender_id"], "status": o["status"],
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_kit(o):
        """Комплект проекта для клиента: код, название, версия."""
        return {"id": o["id"], "project_id": o["project_id"], "code": o["code"], "title": o["title"], "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ заказчики
    @staticmethod
    def _inn(p):
        """Необязательный ИНН: 10 или 12 цифр (контрольная сумма пока не проверяется — отмечено в сообщении)."""
        inn = opt_str(p, "inn", 12)
        if inn is not None and not _INN_RE.match(inn):
            raise Rejected("invalid_inn", "ИНН должен содержать 10 или 12 цифр (контрольная сумма пока не проверяется).")
        return inn

    def _customer_create(self, ctx, p, ev):
        """customer.create {id, name, inn?, note?} — создать заказчика. Права: customer.create (A-03). Можно офлайн
        (очередь).
        """
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.create", ctx.now)
        cid = req_uuid(p, "id")
        name = req_str(p, "name")
        inn = self._inn(p)
        self._ensure_new(ctx.conn, customers, cid)
        ctx.conn.execute(customers.insert().values(id=cid, name=name, inn=inn, note=opt_str(p, "note"), version=1,
                                                   created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("customer", cid, auth, None, 1, {"name": name})
        return {"customer": self.v_customer(self._get(ctx.conn, customers, cid))}

    def _customer_update(self, ctx, p, ev):
        """customer.update {id, name?, inn?, note?} + expected_version — изменить заказчика. Передаются только меняемые
        поля; пустой набор — отказ no_changes.
        """
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.update", ctx.now)
        o = self._require(ctx.conn, customers, req_uuid(p, "id"), "Заказчик")
        self._check_version(o, ev, self.v_customer)
        ch: dict[str, Any] = {}
        if "name" in p:
            ch["name"] = req_str(p, "name")
        if "inn" in p:
            ch["inn"] = self._inn(p)
        if "note" in p:
            ch["note"] = opt_str(p, "note")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, customers, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("customer", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"customer": self.v_customer(n)}

    # ------------------------------------------------------------ тендеры
    def _tender_create(self, ctx, p, ev):
        """tender.create {id, customer_id, title, submission_deadline?, owner_user_id?} — создать тендер в начальном
        статусе (draft). Ответственный по умолчанию — создатель (A-11).
        """
        only_keys(p, {"id", "customer_id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.create", ctx.now)
        tid = req_uuid(p, "id")
        self._require(ctx.conn, customers, req_uuid(p, "customer_id"), "Заказчик")
        owner = p.get("owner_user_id") or ctx.user_id  # A-11
        if not isinstance(owner, str) or not self._get(ctx.conn, users, owner):
            raise Rejected("owner_not_found", "Ответственный не найден.")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, tenders, tid)
        ctx.conn.execute(tenders.insert().values(id=tid, customer_id=p["customer_id"], title=title,
                                                 submission_deadline=opt_date(p, "submission_deadline"), owner_user_id=owner,
                                                 status=self.wf["tender"]["initial"], version=1, created_by=ctx.user_id,
                                                 created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("tender", tid, auth, None, 1, {"title": title, "customer_id": p["customer_id"]})
        return {"tender": self.v_tender(self._get(ctx.conn, tenders, tid))}

    def _tender_update(self, ctx, p, ev):
        """tender.update {id, title?, submission_deadline?, owner_user_id?} + версия — изменить тендер; в конечном
        статусе (выигран/проигран/отменён) — запрещено.
        """
        only_keys(p, {"id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.update", ctx.now)
        o = self._require(ctx.conn, tenders, req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        if o["status"] in self.wf["tender"]["terminal"]:
            raise Rejected("tender_closed", "Тендер в конечном статусе — изменение запрещено.")
        ch: dict[str, Any] = {}
        if "title" in p:
            ch["title"] = req_str(p, "title")
        if "submission_deadline" in p:
            ch["submission_deadline"] = opt_date(p, "submission_deadline")
        if "owner_user_id" in p:
            if not isinstance(p["owner_user_id"], str) or not self._get(ctx.conn, users, p["owner_user_id"]):
                raise Rejected("owner_not_found", "Ответственный не найден.")
            ch["owner_user_id"] = p["owner_user_id"]
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, tenders, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tender", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"tender": self.v_tender(n)}

    def _tender_transition(self, ctx, p, ev):
        """tender.transition {id, to, reason?} + версия — сменить статус по workflows (A-02); для «проигран»/«отменён»
        причина обязательна. Победа НЕ создаёт проект — это отдельное явное действие project.start_rkd.
        """
        only_keys(p, {"id", "to", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.transition", ctx.now)
        o = self._require(ctx.conn, tenders, req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        to = p.get("to")
        allowed = self.wf["tender"]["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed", f"Переход «{o['status']}» → «{to}» не разрешён. Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        if to in self.wf["tender"]["reason_required_for"] and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину.")
        n = self._update_versioned(ctx, tenders, o, {"status": to, "status_reason": reason, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tender", o["id"], auth, o["version"], n["version"], {"from": o["status"], "to": to, "reason": reason})
        # Победа в тендере НЕ создаёт проект: проект — только командой project.start_rkd.
        return {"tender": self.v_tender(n)}

    # ------------------------------------------------------------ ТКП
    @staticmethod
    def _amount(p: dict) -> tuple[Optional[Decimal], Optional[str]]:
        """Сумма и валюта ТКП: сумма — строгий Decimal или None («не указана»); если сумма есть, валюта ISO обязательна
        (по умолчанию не подставляется).
        """
        try:
            amount = money.parse_optional(p.get("amount"))
        except money.MoneyError as e:
            raise Rejected(f"amount_{e.code}", e.message_ru, {"field": "amount"})
        cur = p.get("currency")
        if cur is not None and (not isinstance(cur, str) or not _CUR_RE.match(cur)):
            raise Rejected("invalid_currency", "Валюта — трёхбуквенный код ISO 4217. Значение по умолчанию не подставляется.")
        if amount is not None and cur is None:
            raise Rejected("currency_required", "Для суммы укажите валюту — значение по умолчанию не подставляется.")
        return amount, cur

    @staticmethod
    def _tkp_hash(o: dict) -> str:
        """SHA-256 содержимого ТКП (сумма, валюта, метка, ревизия, примечание). Согласование привязано к этому хэшу:
        изменённое после согласования ТКП отправить нельзя.
        """
        return hashlib.sha256(canon({"id": o["id"], "tender_id": o["tender_id"], "label": o["label"], "revision": o["revision"],
                                     "amount": money.to_json(o["amount"]), "currency": o["currency"], "note": o["note"]}).encode()).hexdigest()

    def _tkp_create(self, ctx, p, ev):
        """tkp.create {id, tender_id, label, amount?, currency?, note?} — новое ТКП-черновик к тендеру (не к
        закрытому).
        """
        only_keys(p, {"id", "tender_id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.create", ctx.now)
        kid = req_uuid(p, "id")
        t = self._require(ctx.conn, tenders, req_uuid(p, "tender_id"), "Тендер")
        if t["status"] in ("lost", "cancelled"):
            raise Rejected("tender_closed", "Тендер закрыт (проигран или отменён) — новое ТКП не создаётся.")
        label = req_str(p, "label", 64)
        amount, cur = self._amount(p)
        self._ensure_new(ctx.conn, tkp, kid)
        ctx.conn.execute(tkp.insert().values(id=kid, tender_id=t["id"], label=label, revision=1, amount=amount, currency=cur,
                                             note=opt_str(p, "note"), status="draft", version=1, created_by=ctx.user_id,
                                             created_at=ctx.now, last_edited_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("tkp", kid, auth, None, 1, {"label": label, "amount": money.to_json(amount), "currency": cur})
        return {"tkp": self.v_tkp(self._get(ctx.conn, tkp, kid))}

    def _tkp_update(self, ctx, p, ev):
        """tkp.update {id, label?, amount?, currency?, note?} + версия — изменить черновик; каждая правка увеличивает
        ревизию содержимого. Согласованное ТКП сначала явно возвращается в черновик (tkp.reopen).
        """
        only_keys(p, {"id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.update", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Изменять можно только черновик ТКП. Чтобы изменить согласованное — сначала явно верните его в черновик.")
        ch: dict[str, Any] = {}
        if "label" in p:
            ch["label"] = req_str(p, "label", 64)
        if "amount" in p or "currency" in p:
            ch["amount"], ch["currency"] = self._amount({"amount": p.get("amount", money.to_json(o["amount"])),
                                                         "currency": p.get("currency", o["currency"])})
        if "note" in p:
            ch["note"] = opt_str(p, "note")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (money.to_json(o[k]) if k == "amount" else o[k]) for k in ch}
        n = self._update_versioned(ctx, tkp, o, {**ch, "revision": o["revision"] + 1, "last_edited_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"],
                {"before": before, "after": {k: (money.to_json(v) if k == "amount" else v) for k, v in ch.items()}, "revision": n["revision"]})
        return {"tkp": self.v_tkp(n)}

    def _tkp_approve(self, ctx, p, ev):
        """tkp.approve {id, comment?} + версия — согласовать черновик ТКП (A-04).

        Автор последней правки согласовать сам не может (разделение обязанностей). Запись согласования хранит
        ревизию, хэш содержимого и id выдачи роли согласующего — по ним при отправке проверяется, что согласование
        всё ещё действительно.
        """
        only_keys(p, {"id", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.approve", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Согласовать можно только черновик.")
        if o["last_edited_by"] == ctx.user_id:
            raise Denied("self_approval", "Автор последней правки не может согласовать ТКП сам (разделение обязанностей).")
        h = self._tkp_hash(o)
        # grant_id — выдача роли, давшей право; при замещении (D-34) — выдача НОСИТЕЛЯ роли: согласование остаётся
        # действительным, пока действует его роль. Основание (кто за кого) — в approval_bases.
        aid = ctx.conn.execute(tkp_approvals.insert().values(tkp_id=o["id"], tkp_revision=o["revision"], content_hash=h,
                                                             approver_id=ctx.user_id, grant_id=auth.ref_id,
                                                             approved_at=ctx.now)).inserted_primary_key[0]
        self._record_basis(ctx, "tkp_approval", str(aid), 1, auth)
        n = self._update_versioned(ctx, tkp, o, {"status": "approved", "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"], {"approved_revision": o["revision"], "content_hash": h,
                                                                  "comment": opt_str(p, "comment", 2000)})
        return {"tkp": self.v_tkp(n)}

    def _tkp_reopen(self, ctx, p, ev):
        """tkp.reopen {id, reason} + версия — вернуть согласованное ТКП в черновик; действующее согласование помечается
        заменённым (не удаляется — история сохраняется).
        """
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.reopen", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Вернуть в черновик можно только согласованное ТКП.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(tkp_approvals.update().where(tkp_approvals.c.tkp_id == o["id"], tkp_approvals.c.superseded_at.is_(None))
                         .values(superseded_at=ctx.now, superseded_reason="reopen: " + reason))
        n = self._update_versioned(ctx, tkp, o, {"status": "draft", "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"], {"reason": reason, "approval": "superseded"})
        return {"tkp": self.v_tkp(n)}

    def _tkp_record_sent(self, ctx, p, ev):
        """tkp.record_sent {id, sent_on, channel?, note?} + версия — зарегистрировать факт отправки ТКП заказчику.

        Проверяется, что согласование относится к текущей ревизии, содержимое не менялось (хэш) и роль согласующего
        всё ещё действует (отзыв и повторная выдача не «оживляют» старое согласование). Дата не в будущем. Это
        запись со слов пользователя — доставка не доказана (так и пишется в журнал).
        """
        only_keys(p, {"id", "sent_on", "channel", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.record_sent", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Зарегистрировать отправку можно только для согласованного ТКП.")
        ap = ctx.conn.execute(sa.select(tkp_approvals).where(tkp_approvals.c.tkp_id == o["id"], tkp_approvals.c.superseded_at.is_(None))
                              .order_by(tkp_approvals.c.id.desc()).limit(1)).first()
        roles = self.policy.roles_for("tkp.approve", "global_role")
        # Чья выдача роли дала право: согласующего или — если он действовал по замещению (D-34) — носителя роли.
        basis = self._basis(ctx.conn, "tkp_approval", str(ap.id)) if ap else None
        grant_owner = (basis["on_behalf_of"] if basis and basis["on_behalf_of"] else ap.approver_id) if ap else None
        if (not ap or ap.tkp_revision != o["revision"] or ap.content_hash != self._tkp_hash(o)
                or not grant_is_active(ctx.conn, ap.grant_id, grant_owner, roles, ctx.now)):
            raise Rejected("approval_invalid",
                           "Согласование недействительно: изменилось содержимое, или полномочие согласующего отозвано/истекло. "
                           "Повторная выдача роли не восстанавливает прежнее согласование — нужно новое.",
                           {"approval_id": ap.id if ap else None})
        sent_on = opt_date(p, "sent_on")
        if not sent_on:
            raise Rejected("field_required", "Укажите дату фактической отправки.")
        if sent_on > ctx.now.date():
            raise Rejected("date_in_future", "Дата отправки не может быть в будущем.")
        channel = opt_str(p, "channel", 100)
        n = self._update_versioned(ctx, tkp, o, {"status": "sent_recorded", "sent_on": sent_on, "sent_channel": channel, "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"],
                {"sent_on": sent_on.isoformat(), "channel": channel, "approval_id": ap.id,
                 "meaning": "Зарегистрирован факт отправки со слов пользователя; доставка и получение не доказаны."})
        return {"tkp": self.v_tkp(n)}

    # ------------------------------------------------------------ проекты
    def _project_start_rkd(self, ctx, p, ev):
        """project.start_rkd {id, tender_id, code, title} — «Начать РКД»: создать проект по выигранному тендеру (A-05).
        Только онлайн.

        Один проект на тендер; код проекта уникален в компании. ГИП не назначается автоматически — его назначает
        администратор.
        """
        only_keys(p, {"id", "tender_id", "code", "title"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "project.start_rkd", ctx.now)
        pid = req_uuid(p, "id")
        t = self._require(ctx.conn, tenders, req_uuid(p, "tender_id"), "Тендер")
        allowed = self.wf["project"]["start_rkd_allowed_from_tender_status"]
        if t["status"] not in allowed:
            raise Rejected("tender_status_not_allowed", f"Начать РКД можно из статуса тендера: {', '.join(allowed)}. Сейчас: {t['status']}.",
                           {"tender_status": t["status"]})
        if self.wf["project"]["one_project_per_tender"]:
            ex = ctx.conn.execute(sa.select(projects.c.id, projects.c.code).where(projects.c.tender_id == t["id"])).first()
            if ex:
                raise Rejected("project_exists_for_tender", f"По этому тендеру уже начат проект {ex.code}.", {"project_id": ex.id})
        code = req_str(p, "code", 64)
        if ctx.conn.execute(sa.select(projects.c.id).where(projects.c.code == code)).first():
            raise Rejected("project_code_taken", "Код проекта уже используется (уникальность в пределах компании).")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, projects, pid)
        ctx.conn.execute(projects.insert().values(id=pid, code=code, title=title, tender_id=t["id"], tender_version_at_start=t["version"],
                                                  status=self.wf["project"]["initial"], version=1, created_by=ctx.user_id,
                                                  created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        # Потоки проекта (D-56): договор, РКД, закупка, производство, отгрузка, площадка — с первого события в журнале.
        self._streams_init(ctx, pid)
        ctx.log("project", pid, auth, None, 1, {"tender_id": t["id"], "tender_version": t["version"], "code": code,
                                               "note": "ГИП не назначен — назначает администратор."})
        return {"project": self.v_project(self._get(ctx.conn, projects, pid)), "gip_assigned": False}

    def _valid_until(self, ctx, p) -> Optional[datetime]:
        """Необязательный срок действия назначения/роли: время ISO-8601 в будущем или None (бессрочно)."""
        if p.get("valid_until") is None:
            return None
        vu = parse_iso_utc(p["valid_until"], "valid_until")
        if vu <= ctx.now:
            raise Rejected("bad_valid_until", "Срок должен быть в будущем.")
        return vu

    def _project_assign(self, ctx, p, ev):
        """project.assign {project_id, user_id, project_role, valid_until?, reason?} — назначить пользователя в проект
        (gip, rp, participant).

        Право зависит от назначаемой роли (project.assign_gip — только admin; РП и участников — по матрице A-07).
        ГИП в проекте один: нового — только после явного отзыва текущего.
        """
        only_keys(p, {"project_id", "user_id", "project_role", "valid_until", "reason"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        role = p.get("project_role")
        if role not in self.contracts.permissions["project_roles"]:
            raise Rejected("bad_project_role", "Роль в проекте: gip, rp или participant.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{role}", ctx.now, project_id=pr["id"])
        target = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not target or not target["active"]:
            raise Rejected("user_not_found", "Пользователь не найден или отключён.")
        vu = self._valid_until(ctx, p)
        act = sa.and_(project_assignments.c.project_id == pr["id"], project_assignments.c.revoked_at.is_(None),
                      sa.or_(project_assignments.c.valid_until.is_(None), project_assignments.c.valid_until > ctx.now))
        if role == "gip" and ctx.conn.execute(sa.select(project_assignments.c.id).where(act, project_assignments.c.project_role == "gip")).first():
            raise Rejected("gip_already_assigned", "ГИП уже назначен. Сначала явно отзовите текущее назначение.")
        if ctx.conn.execute(sa.select(project_assignments.c.id).where(act, project_assignments.c.user_id == target["id"],
                                                                      project_assignments.c.project_role == role)).first():
            raise Rejected("already_assigned", "Такое действующее назначение уже есть.")
        aid = ctx.conn.execute(project_assignments.insert().values(project_id=pr["id"], user_id=target["id"], project_role=role,
                                                                   valid_from=ctx.now, valid_until=vu, assigned_by=ctx.user_id)).inserted_primary_key[0]
        ctx.log("assignment", aid, auth, None, 1, {"project_id": pr["id"], "user_id": target["id"], "project_role": role,
                                                  "valid_until": iso(vu), "reason": opt_str(p, "reason", 500)})
        return {"assignment_id": aid}

    def _project_revoke_assignment(self, ctx, p, ev):
        """project.revoke_assignment {assignment_id, reason} — отозвать назначение (с той же проверкой права, что и при
        назначении). Запись остаётся в истории.
        """
        only_keys(p, {"assignment_id", "reason"})
        aid = opt_int(p, "assignment_id")
        row = self._get(ctx.conn, project_assignments, aid) if aid is not None else None
        if not row:
            raise Rejected("not_found", "Назначение не найдено.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{row['project_role']}", ctx.now, project_id=row["project_id"])
        if row["revoked_at"]:
            raise Rejected("already_revoked", "Назначение уже отозвано.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(project_assignments.update().where(project_assignments.c.id == aid).values(revoked_at=ctx.now, revoked_reason=reason))
        ctx.log("assignment", aid, auth, 1, 2, {"revoked": True, "reason": reason, "user_id": row["user_id"], "project_role": row["project_role"]})
        return {"assignment_id": aid, "revoked": True}

    # ------------------------------------------------------------ комплекты
    def _kit_create(self, ctx, p, ev):
        """kit.create {id, project_id, code, title} — комплект в проекте (только в статусе «Разработка РКД»; код
        уникален в проекте). Права A-08.
        """
        only_keys(p, {"id", "project_id", "code", "title"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.create", ctx.now, project_id=pr["id"])
        if pr["status"] != "rkd_in_progress":
            raise Rejected("project_state", "Комплекты добавляются только в проект в статусе «Разработка РКД».")
        kid = req_uuid(p, "id")
        code = req_str(p, "code", 64)
        if ctx.conn.execute(sa.select(kits.c.id).where(kits.c.project_id == pr["id"], kits.c.code == code)).first():
            raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, kits, kid)
        ctx.conn.execute(kits.insert().values(id=kid, project_id=pr["id"], code=code, title=title, version=1, created_by=ctx.user_id,
                                              created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("kit", kid, auth, None, 1, {"project_id": pr["id"], "code": code})
        return {"kit": self.v_kit(self._get(ctx.conn, kits, kid))}

    def _kit_update(self, ctx, p, ev):
        """kit.update {id, code?, title?} + версия — изменить комплект."""
        only_keys(p, {"id", "code", "title"})
        o = self._require(ctx.conn, kits, req_uuid(p, "id"), "Комплект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_kit)
        ch: dict[str, Any] = {}
        if "code" in p:
            ch["code"] = req_str(p, "code", 64)
            if ch["code"] != o["code"] and ctx.conn.execute(sa.select(kits.c.id).where(kits.c.project_id == o["project_id"], kits.c.code == ch["code"])).first():
                raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        if "title" in p:
            ch["title"] = req_str(p, "title")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, kits, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("kit", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"kit": self.v_kit(n)}

    # ------------------------------------------------------------ учётные записи
    def _revoke_sessions(self, conn, user_id, now, reason, except_hash=None) -> int:
        """Отозвать все сеансы пользователя (кроме текущего, если передан except_hash). Возвращает число отозванных.
        """
        q = sessions.update().where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
        if except_hash:
            q = q.where(sessions.c.token_hash != except_hash)
        return conn.execute(q.values(revoked_at=now, revoked_reason=reason)).rowcount

    def _check_password(self, pwd, login):
        """Проверить пароль по политике; при нарушении — отказ weak_password со списком причин."""
        problems = security.password_problems(pwd, login, self.sec)
        if problems:
            raise Rejected("weak_password", " ".join(problems))

    def _account_change_password(self, ctx, p, ev):
        """account.change_password {current_password, new_password} — смена своего пароля.

        Нужен текущий пароль (защита от украденного сеанса) и ограничение частоты попыток. После смены остальные
        сеансы пользователя отзываются, текущий остаётся.
        """
        only_keys(p, {"current_password", "new_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "account.change_password", ctx.now)
        if not self.pwd_rate.allow(ctx.user_id, ctx.now.timestamp()):
            raise Rejected("rate_limited", "Слишком много попыток смены пароля. Подождите минуту.")
        ok, _ = security.verify_password(ctx.user["pwd_hash"], p.get("current_password") or "")
        if not ok:
            raise Denied("bad_current_password", "Текущий пароль указан неверно.")
        new = p.get("new_password")
        self._check_password(new, ctx.user["login"])
        if security.verify_password(ctx.user["pwd_hash"], new)[0]:
            raise Rejected("same_password", "Новый пароль совпадает с текущим.")
        ctx.conn.execute(users.update().where(users.c.id == ctx.user_id).values(
            pwd_hash=security.hash_password(new), must_change_password=False, password_changed_at=ctx.now))
        n = self._revoke_sessions(ctx.conn, ctx.user_id, ctx.now, "password_changed", except_hash=ctx.session_hash)
        ctx.log("user", ctx.user_id, auth, None, None, {"password_changed": True, "other_sessions_revoked": n})
        return {"password_changed": True, "other_sessions_revoked": n}

    def _admin_create_user(self, ctx, p, ev):
        """admin.create_user {login, display_name, initial_password} — создать учётную запись с временным паролем (при
        первом входе обязательна смена). Роли выдаются отдельно.
        """
        only_keys(p, {"login", "display_name", "initial_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.create_user", ctx.now)
        login = req_str(p, "login", 64)
        if not _LOGIN_RE.match(login):
            raise Rejected("bad_login", "Логин: 3–64 символа, латиница, цифры, точка, дефис, подчёркивание.")
        self._check_password(p.get("initial_password"), login)
        if self._get(ctx.conn, users, login, "login"):
            raise Rejected("login_taken", "Логин занят.")
        uid = str(uuid.uuid4())
        ctx.conn.execute(users.insert().values(id=uid, login=login, display_name=req_str(p, "display_name"),
                                               pwd_hash=security.hash_password(p["initial_password"]), must_change_password=True,
                                               password_changed_at=ctx.now, failed_attempts=0, active=True, created_at=ctx.now))
        ctx.log("user", uid, auth, None, 1, {"login": login, "must_change_password": True})
        return {"user_id": uid}

    def _admin_reset_password(self, ctx, p, ev):
        """admin.reset_password {user_id, temporary_password, reason} — задать временный пароль: снимает блокировку,
        отзывает все сеансы, требует смены при входе.
        """
        only_keys(p, {"user_id", "temporary_password", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.reset_password", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        self._check_password(p.get("temporary_password"), u["login"])
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(users.update().where(users.c.id == u["id"]).values(
            pwd_hash=security.hash_password(p["temporary_password"]), must_change_password=True, password_changed_at=ctx.now,
            failed_attempts=0, locked_until=None))
        n = self._revoke_sessions(ctx.conn, u["id"], ctx.now, "password_reset")
        ctx.log("user", u["id"], auth, None, None, {"password_reset": True, "sessions_revoked": n, "reason": reason})
        return {"user_id": u["id"], "sessions_revoked": n}

    def _active_admins(self, conn, now) -> set[str]:
        """Множество id действующих администраторов (роль действует и учётка включена) — для защиты «нельзя остаться
        без администратора».
        """
        rows = conn.execute(sa.select(role_grants.c.user_id).join(users, users.c.id == role_grants.c.user_id).where(
            role_grants.c.role == "admin", role_grants.c.revoked_at.is_(None), role_grants.c.valid_from <= now,
            sa.or_(role_grants.c.valid_until.is_(None), role_grants.c.valid_until > now), users.c.active == sa.true()))
        return {r.user_id for r in rows}

    def _admin_set_user_active(self, ctx, p, ev):
        """admin.set_user_active {user_id, active, reason} — включить/отключить учётку. Нельзя отключить себя и
        последнего администратора; при отключении сеансы отзываются.
        """
        only_keys(p, {"user_id", "active", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.set_user_active", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        active = p.get("active")
        if not isinstance(active, bool):
            raise Rejected("field_type", "Поле «active» — true или false.")
        reason = req_str(p, "reason", 500)
        if not active:
            if u["id"] == ctx.user_id:
                raise Rejected("cannot_disable_self", "Нельзя отключить собственную учётную запись.")
            if self._active_admins(ctx.conn, ctx.now) == {u["id"]}:
                raise Rejected("last_admin", "Нельзя отключить последнего действующего администратора.")
        ctx.conn.execute(users.update().where(users.c.id == u["id"]).values(active=active, failed_attempts=0, locked_until=None))
        n = 0 if active else self._revoke_sessions(ctx.conn, u["id"], ctx.now, "user_disabled")
        ctx.log("user", u["id"], auth, None, None, {"active": active, "sessions_revoked": n, "reason": reason})
        return {"user_id": u["id"], "active": active}

    def _admin_grant_role(self, ctx, p, ev):
        """admin.grant_role {user_id, role, valid_until?, reason} — выдать глобальную роль. Каждая выдача — новая
        запись с новым id (основание для журнала).
        """
        only_keys(p, {"user_id", "role", "valid_until", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.grant_role", ctx.now)
        if p.get("role") not in self.contracts.permissions["roles"]:
            raise Rejected("bad_role", "Неизвестная роль доступа.")
        if not isinstance(p.get("user_id"), str) or not self._get(ctx.conn, users, p["user_id"]):
            raise Rejected("user_not_found", "Пользователь не найден.")
        vu = self._valid_until(ctx, p)
        reason = req_str(p, "reason", 500)
        gid = ctx.conn.execute(role_grants.insert().values(user_id=p["user_id"], role=p["role"], scope_type="company", valid_from=ctx.now,
                                                           valid_until=vu, granted_by=ctx.user_id, reason=reason)).inserted_primary_key[0]
        ctx.log("role_grant", gid, auth, None, 1, {"user_id": p["user_id"], "role": p["role"], "valid_until": iso(vu), "reason": reason})
        return {"grant_id": gid}

    def _admin_revoke_role(self, ctx, p, ev):
        """admin.revoke_role {grant_id, reason} — отозвать выдачу роли. Нельзя отозвать роль admin у последнего
        действующего администратора.
        """
        only_keys(p, {"grant_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_role", ctx.now)
        gid = opt_int(p, "grant_id")
        row = self._get(ctx.conn, role_grants, gid) if gid is not None else None
        if not row:
            raise Rejected("not_found", "Выдача роли не найдена.")
        if row["revoked_at"]:
            raise Rejected("already_revoked", "Роль уже отозвана.")
        if row["role"] == "admin":
            others = ctx.conn.execute(sa.select(sa.func.count()).select_from(role_grants).join(users, users.c.id == role_grants.c.user_id).where(
                role_grants.c.role == "admin", role_grants.c.id != gid, role_grants.c.revoked_at.is_(None),
                sa.or_(role_grants.c.valid_until.is_(None), role_grants.c.valid_until > ctx.now), users.c.active == sa.true())).scalar()
            if not others:
                raise Rejected("last_admin", "Нельзя отозвать роль у последнего действующего администратора.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(role_grants.update().where(role_grants.c.id == gid).values(revoked_at=ctx.now))
        ctx.log("role_grant", gid, auth, 1, 2, {"revoked": True, "user_id": row["user_id"], "role": row["role"], "reason": reason})
        return {"grant_id": gid, "revoked": True}

    def _admin_revoke_sessions(self, ctx, p, ev):
        """admin.revoke_sessions {user_id, reason} — принудительно завершить все сеансы пользователя (например, при
        утере ноутбука).
        """
        only_keys(p, {"user_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_sessions", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        reason = req_str(p, "reason", 500)
        n = self._revoke_sessions(ctx.conn, u["id"], ctx.now, "admin: " + reason)
        ctx.log("session", u["id"], auth, None, None, {"revoked_sessions": n, "reason": reason})
        return {"revoked_sessions": n}

    # ------------------------------------------------------------ запросы
    def _query(self, req: dict) -> dict:
        """Запросы чтения: pull (всё, что пользователю можно видеть), history (история объекта), users (список учёток —
        только администратору).
        """
        def tx(conn):
            """Транзакция запроса: проверка сеанса и выбор запроса."""
            now = self.now()
            try:
                user, _, _ = self._auth(conn, req.get("token"), now)
            except Unauthenticated as e:
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            q = req.get("query")
            if q == "pull":
                return {"status": "accepted", "result": self._pull(conn, user, now)}
            if q == "history":
                return self._history(conn, user["id"], now, req.get("object_type"), req.get("object_id"))
            if q == "norm_stats":
                if not self.policy.can(conn, user["id"], "norm.read", now):
                    return {"status": "denied", "code": "no_authority",
                            "message_ru": "Статистика норм — производственному руководству (РП, ГИП, руководитель отдела, ведущий инженер)."}
                return self._norm_stats(conn, user["id"], now)
            if q == "users":
                if not self.policy.can(conn, user["id"], "admin.list_users", now):
                    return {"status": "denied", "code": "no_authority", "message_ru": "Список учётных записей доступен администратору."}
                return {"status": "accepted", "result": self._users(conn, now)}
            return {"status": "rejected", "code": "unknown_query", "message_ru": "Неизвестный запрос."}
        return self._tx(tx)

    def _rows(self, conn, stmt) -> list[dict]:
        """Все строки запроса как список словарей."""
        return [dict(r._mapping) for r in conn.execute(stmt)]

    def _pull(self, conn, user, now) -> dict:
        """Снимок данных для клиента: каждая часть — только если у пользователя есть право её читать.

        Проекты, комплекты, назначения, задачи, документы и расчёты — только по читаемым проектам; склад — по
        stock.read; потребности — по need.read в их проекте. Справочник людей — только имена (без логинов). me.roles
        — подсказка для интерфейса, а не решение о правах.
        """
        uid = user["id"]
        can = lambda action, pid=None: self.policy.can(conn, uid, action, now, pid)  # noqa: E731
        out: dict[str, Any] = {"server_time": iso(now), "customers": [], "tenders": [], "tkp": [], "projects": [], "kits": [], "assignments": [], "people": [],
                               # Лицензия установки — всем (D-81): клиент прячет разделы вне лицензии и показывает
                               # блок «О программе»; ворота — на сервере (_license_denied), это лишь подсказка.
                               "license": self._license_block(conn, now)}
        if can("customer.read"):
            out["customers"] = [self.v_customer(o) for o in self._rows(conn, sa.select(customers).order_by(customers.c.name))]
        if can("tender.read"):
            out["tenders"] = [self.v_tender(o) for o in self._rows(conn, sa.select(tenders).order_by(tenders.c.created_at))]
        if can("tkp.read"):
            out["tkp"] = [self.v_tkp(o) for o in self._rows(conn, sa.select(tkp).order_by(tkp.c.created_at))]
        readable: list[str] = []
        for pr in self._rows(conn, sa.select(projects).order_by(projects.c.code)):
            if can("project.read", pr["id"]):
                readable.append(pr["id"])
                out["projects"].append(self.v_project(pr))
                out["kits"].extend(self.v_kit(k) for k in self._rows(conn, sa.select(kits).where(kits.c.project_id == pr["id"]).order_by(kits.c.code)))
                out["assignments"].extend(
                    {"id": a["id"], "project_id": a["project_id"], "user_id": a["user_id"], "project_role": a["project_role"],
                     "valid_until": iso(a["valid_until"])}
                    for a in self._rows(conn, sa.select(project_assignments).where(
                        project_assignments.c.project_id == pr["id"], project_assignments.c.revoked_at.is_(None),
                        sa.or_(project_assignments.c.valid_until.is_(None), project_assignments.c.valid_until > now))
                        .order_by(project_assignments.c.id)))
        # Задачи, документы и метаданные версий (без содержимого файлов) — только по читаемым проектам.
        out.update(self._pull_work(conn, readable))
        # Лиды, потоки проекта, риск-разрешения, события и снимок архива (D-56).
        out.update(self._pull_delivery(conn, uid, now, readable))
        # Ревизии РКД: проверка, замечания/ответы, трансмитталы (этап 3 blueprint 52).
        out.update(self._pull_rkd(conn, readable))
        # Консолидированные закупочные пакеты, распределения и аналоги (этап 4 blueprint 52).
        out.update(self._pull_consolidation(conn, uid, now, readable))
        # Расчёты: черновики и версии — по читаемым проектам; ревизии итога — по отдельному праву.
        out.update(self._pull_calc(conn, uid, now, readable))
        # Склад (остатки, резервы, журнал) — по праву stock.read; потребности — по праву need.read в их проекте.
        out.update(self._pull_stock(conn, uid, now))
        # Поставщики (supplier.read) и заказы поставщикам (po.read; цены — только с po.read_prices).
        out.update(self._pull_purchasing(conn, uid, now))
        # Настройки компании — всем (правила работы, по ним клиент показывает подсказки).
        out["config"] = self._pull_config(conn)
        # Замещения (свои; все — руководителю отдела и администратору), покрытие ролей (D-34) и замещающие в
        # читаемых проектах (D-35).
        out.update(self._pull_delegations(conn, uid, now, readable))
        # Напоминания о сроке ответа заказчику по рекламациям (D-40) — до выдачи уведомлений, чтобы новые пришли сразу.
        self._quality_reminders(conn, uid, now)
        # Свои уведомления и число непрочитанных (D-35).
        out.update(self._pull_notifications(conn, uid, now))
        # ТП и состав изделий (без цен), наряды и операции (D-36).
        out.update(self._pull_production(conn, uid, now))
        # Экземпляры изделий, паспорта, наряды для регистрации, документы для паспорта (D-38).
        out.update(self._pull_lifecycle(conn, uid, now))
        # Рекламации, CAPA, запросы и извещения об изменении; справочник версий для извещений (D-39).
        out.update(self._pull_quality(conn, uid, now))
        # Дневные отчёты: свои; руководителю отдела — отправленные чужие; проекты для строк отчёта (D-42).
        out.update(self._pull_worktime(conn, uid, now))
        # Оргструктура — всем вошедшим (справочник компании), своё подразделение и кто проверяет мои отчёты (D-44).
        out.update(self._pull_org(conn, uid, now))
        # Таймер рабочего дня: свои дни и запросы; руководителю — запросы на фактическое начало (D-46).
        out.update(self._pull_workday(conn, uid, now))
        # Графики работы (справочник), назначения и мой действующий график (D-47).
        out.update(self._pull_schedule(conn, uid, now))
        # Производственный календарь: праздники, сокращённые дни, переносы (D-48).
        out.update(self._pull_calendar(conn, uid, now))
        me_roles = self._me_roles(conn, uid, now)
        # Справочник людей нужен и для выбора замещающего — поэтому он приходит всем, у кого есть хоть одна роль.
        if (out["tenders"] or out["projects"] or out["calc_revisions"] or out["warehouses"] or out["needs"]
                or out["purchase_orders"] or me_roles["roles"] or out["delegations"] or out["work_orders"]
                or out["product_instances"] or out["claims"] or out["change_requests"] or out["daily_reports"]
                or out["org_units"] or out["opportunities"]):
            # Минимальный справочник людей (имя для отображения ответственного) — без логинов и контактов.
            out["people"] = [{"id": r["id"], "display_name": r["display_name"]} for r in
                             self._rows(conn, sa.select(users.c.id, users.c.display_name).where(users.c.active == sa.true()))]
        out["me"] = {"user_id": uid, "display_name": user["display_name"], "login": user["login"],
                     **me_roles,
                     "note_ru": "Роли в кэше клиента служат только подсказкой интерфейса; решение принимает сервер в момент эффекта."}
        return out

    def _users(self, conn, now) -> list[dict]:
        """Список учёток для администратора: состояние, блокировка, требование смены пароля, выдачи ролей с признаком
        «действует».
        """
        from vector.core import overtime as ot
        out = []
        cat_rows = {r["user_id"]: r for r in self._rows(conn, sa.select(db.hr_categories))}
        for u in self._rows(conn, sa.select(users).order_by(users.c.login)):
            grants = self._rows(conn, sa.select(role_grants).where(role_grants.c.user_id == u["id"]).order_by(role_grants.c.id))
            cat_row = cat_rows.get(u["id"])
            cats = json.loads(cat_row["categories_json"]) if cat_row else []
            out.append({"id": u["id"], "login": u["login"], "display_name": u["display_name"], "active": bool(u["active"]),
                        "hr_categories": cats, "hr_category_labels": ot.category_labels(cats),
                        "hr_categories_version": cat_row["version"] if cat_row else 0,
                        "must_change_password": bool(u["must_change_password"]),
                        "locked_until": iso(u["locked_until"]) if u["locked_until"] and u["locked_until"] > now else None,
                        "grants": [{"id": g["id"], "role": g["role"], "valid_until": iso(g["valid_until"]), "revoked_at": iso(g["revoked_at"]),
                                    "active": g["revoked_at"] is None and (g["valid_until"] is None or g["valid_until"] > now)} for g in grants]})
        return out

    def _history(self, conn, uid, now, otype, oid) -> dict:
        """История объекта из журнала аудита — если пользователь может читать сам объект.

        Для объектов внутри проекта (комплект, задача, документ, черновик расчёта, потребность) право проверяется в
        их проекте. Возвращает кто, когда, какой командой, на каком основании и что изменилось.
        """
        action = {"customer": "customer.read", "tender": "tender.read", "tkp": "tkp.read", "project": "project.read",
                  "kit": "kit.read", "task": "task.read", "document": "document.read",
                  "calc_draft": "calc.read", "project_calc": "calc.read_revisions",
                  "warehouse": "stock.read", "stock_item": "stock.read", "stock_movement": "stock.read",
                  "stock_transfer": "stock.read", "stock_reservation": "stock.read", "procurement_need": "need.read",
                  "stock_adjustment": "stock.read", "stock_transfer_proposal": "need.read",
            "stock_replenishment_rule": "stock.read", "stock_replenishment_draft": "stock.read",
                  "item_alias": "stock.read", "unit_conversion": "stock.read", "config": "config.publish",
                  "supplier": "supplier.read", "supplier_contract": "supplier.read", "purchase_order": "po.read",
                  "tech_process": "tp.read", "work_order": "wo.read", "wo_operation": "wo.read",
                  "product_instance": "instance.read", "passport": "passport.read",
                  "claim": "quality.read", "change_request": "quality.read", "change_order": "quality.read",
                  "daily_report": "worktime.read_all", "sales_opportunity": "opportunity.read",
                  "project_workstream": "project.read", "project_risk_acceptance": "project.read",
                  "project_closeout": "project.read", "document_revision": "project.read",
                  "document_comment": "project.read", "document_transmittal": "project.read",
                  "procurement_group": "pgroup.read", "procurement_batch": "batch.read",
                  "item_analog": "analog.read"}.get(otype)
        if not action or not isinstance(oid, str):
            return {"status": "rejected", "code": "bad_history_request", "message_ru": "Укажите тип и id объекта."}
        pid = oid if otype in ("project", "project_calc") else None
        if otype == "procurement_need":
            n = self._get(conn, db.procurement_needs, oid)
            pid = n["project_id"] if n else None
        if otype in ("project_workstream", "project_risk_acceptance", "project_closeout"):
            # Объекты потоков проекта (D-56): право чтения определяется ролью в их проекте.
            table = {"project_workstream": db.project_workstreams,
                     "project_risk_acceptance": db.project_risk_acceptances,
                     "project_closeout": db.project_closeouts}[otype]
            w = self._get(conn, table, oid)
            pid = w["project_id"] if w else None
        if otype == "document_transmittal":
            w = self._get(conn, db.document_transmittals, oid)
            pid = w["project_id"] if w else None
        if otype in ("document_revision", "document_comment"):
            # Ревизия/замечание живёт в документе проекта: право чтения — по проекту документа.
            if otype == "document_revision":
                rev = self._get(conn, db.document_revisions, oid)
            else:
                cm = self._get(conn, db.document_comments, oid)
                rev = self._get(conn, db.document_revisions, cm["revision_id"]) if cm else None
            doc = self._get(conn, db.documents, rev["document_id"]) if rev else None
            pid = doc["project_id"] if doc else None
        if otype in ("tech_process", "work_order"):
            t = self._get(conn, db.tech_processes if otype == "tech_process" else db.work_orders, oid)
            pid = t["project_id"] if t else None
        if otype in ("claim", "change_request", "change_order"):
            pid = self._quality_project(conn, otype, oid)
        if otype in ("product_instance", "passport"):
            t = self._get(conn, db.product_instances if otype == "product_instance" else db.passports, oid)
            pid = t["project_id"] if t else None
        if otype == "wo_operation":
            op = self._get(conn, db.wo_operations, oid)
            wo = self._get(conn, db.work_orders, op["work_order_id"]) if op else None
            pid = wo["project_id"] if wo else None
            # Исполнитель видит историю своей операции, даже без права читать наряды проекта.
            if op and op["assignee_id"] == uid:
                action = "wo.op_event"
        if otype in ("kit", "task", "document", "calc_draft"):
            # Объекты внутри проекта: право чтения определяется ролью в их проекте.
            # Черновик расчёта идентифицируется id комплекта (один черновик на комплект).
            table = {"kit": kits, "task": db.tasks, "document": db.documents, "calc_draft": kits}[otype]
            k = self._get(conn, table, oid)
            pid = k["project_id"] if k else None
        if otype == "daily_report":
            # Дневной отчёт: автор — всегда; руководитель отдела — отправленный чужой (не черновик и не на
            # самопроверке — D-42, D-43).
            r = self._get(conn, db.daily_reports, oid)
            if r is not None and r["user_id"] == uid:
                action = "report.create"
            elif r is not None and r["status"] in ("draft", "self_check"):
                action = None
        if not action or not self.policy.can(conn, uid, action, now, pid):
            return {"status": "denied", "code": "no_authority", "message_ru": "Нет права на просмотр истории этого объекта."}
        names = {r["id"]: r["display_name"] for r in self._rows(conn, sa.select(users.c.id, users.c.display_name))}
        rows = self._rows(conn, sa.select(audit_log).where(audit_log.c.object_type == otype, audit_log.c.object_id == oid).order_by(audit_log.c.id))
        return {"status": "accepted", "result": [
            {"at": iso(r["at"]), "actor_user_id": r["actor_user_id"], "actor": names.get(r["actor_user_id"]), "operation_id": r["operation_id"],
             "command": r["command"], "authority": r["authority"], "version_before": r["version_before"], "version_after": r["version_after"],
             "result": r["result"], "details": json.loads(r["details_json"])} for r in rows]}

    def about(self) -> dict:
        """Сведения о программе для «О программе» и диагностики: название, версия, версия API и схемы БД."""
        return {"product": "ВЕКТОР", "product_en": "VECTOR", "version": __version__, "api": "v1", "schema_version": db.SCHEMA_VERSION}
