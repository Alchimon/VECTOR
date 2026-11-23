"""Уведомления в программе (ответ владельца на A-40: «руководителю уведомление отправлять — да» → D-35).

Уведомление — запись «кому, о чём, по какому объекту». Создаётся сервером в ТОЙ ЖЕ транзакции, что и событие
(отказ команды откатывает и уведомление), поэтому уведомление никогда не сообщает о том, чего не было. Доставка — в
программе: при обновлении (pull) клиент получает свои уведомления, в меню — число непрочитанных; кто был не в сети,
увидит их при следующем входе. Почта и мессенджеры не используются (в компании нет утверждённого почтового сервера —
открытый вопрос A-41).

Кому что приходит (decision log D-35):

* заказ отправлен на согласование — согласующим закупок и руководителям отдела (в т. ч. их замещающим);
* заказ согласован / возвращён — создателю и отправившему заказ и руководителям отдела;
* заказ отозван с согласования — согласующим закупок и руководителям отдела;
* оформлено / отменено замещение — замещающему, носителю роли (если оформлял не он) и руководителям отдела;
* производство (D-36): ТП утверждён — автору; назначена операция наряда — исполнителю; проблема на операции и
  «наряд выполнен» — РП проекта (и его замещающему в проекте);
* качество (D-39): новая рекламация и новый запрос на изменение — РП и ГИП проекта; анализ — ответственному;
  действие CAPA — исполнителю; «нужна проверка эффективности» — ГИП, РП и руководителям отдела, кроме участвовавших;
  итог проверки — РП, ответственному и отправившему; оценённый запрос и извещение на утверждении — ГИП; решение по
  запросу — автору и оценившему; извещение утверждено — РП и составителю; внедрено — ГИП и РП; проверено —
  внедрившему и составителю;
* качество (D-40): новая рекламация, «нужна проверка эффективности» и «извещение внедрено» — ещё и сотрудникам ОТК
  (отдел технического контроля); дефект стал критическим — всем, кто ведёт рекламацию; срок ответа заказчику
  подходит или прошёл — напоминание при обновлении (pull) тем, кто ведёт рекламацию (один раз на срок и вид;
  отдельного планировщика нет);
* рабочее время (D-42): дневной отчёт отправлен на проверку — руководителям отдела (и их замещающим); принят или
  возвращён — автору; принятый отчёт открыт автором для исправления — принявшему руководителю.

Тот, кто сам совершил действие, уведомление о нём не получает. В тексте уведомлений нет денег (суммы видит только
тот, у кого есть право на цены, — в самом заказе).

Команда notification.read {ids?, all?} — отметить свои уведомления прочитанными (только при связи; чужие —
не трогаются). Удаления нет: старше NOTIFY_KEEP_DAYS дней уведомления просто не приходят в pull.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Optional

import sqlalchemy as sa

from vector.core.errors import Rejected

from .authz import active_delegated
from .db import notifications, role_delegation_projects, role_delegations, role_grants, users
from .validation import iso, only_keys

# Сколько дней уведомление приходит в pull и сколько последних уведомлений отдаётся (больше — не нужно на экране).
NOTIFY_KEEP_DAYS = 90
PULL_LIMIT = 200

# Виды уведомлений и их подписи для интерфейса (клиент группирует и выбирает значок по виду).
KIND_LABELS_RU = {
    "po_submitted": "Заказ на согласовании",
    "po_approved": "Заказ согласован",
    "po_returned": "Заказ возвращён",
    "po_withdrawn": "Заказ отозван",
    "delegation_created": "Замещение оформлено",
    "delegation_revoked": "Замещение отменено",
    # Технология и производство (D-36).
    "tp_approved": "ТП утверждён",
    "wo_assigned": "Назначена операция наряда",
    "wo_problem": "Проблема по наряду",
    "wo_completed": "Наряд выполнен",
    "wo_released_risk": "Наряд выпущен по риск-разрешению",
    # Качество (D-39).
    "claim_new": "Новая рекламация",
    "claim_assigned": "Анализ рекламации",
    "capa_assigned": "Назначено действие CAPA",
    "claim_check": "Нужна проверка эффективности",
    "claim_checked": "Проверка эффективности",
    "ecr_new": "Запрос на изменение",
    "ecr_assessed": "Запрос оценён — нужно решение",
    "ecr_decided": "Решение по запросу",
    "eco_approval": "Извещение на утверждении",
    "eco_approved": "Извещение утверждено",
    "eco_implemented": "Извещение внедрено",
    "eco_verified": "Извещение проверено",
    # Качество, D-40: сроки ответа заказчику и критический дефект.
    "claim_due_soon": "Срок ответа заказчику подходит",
    "claim_overdue": "Срок ответа заказчику прошёл",
    "claim_critical": "Критический дефект",
    # Рабочее время (D-42): дневной отчёт отправлен, принят, возвращён, принятый открыт для исправления.
    "report_submitted": "Дневной отчёт на проверке",
    "report_accepted": "Дневной отчёт принят",
    "report_returned": "Дневной отчёт возвращён",
    "report_reopened": "Принятый отчёт исправляется",
    # D-43: программа собрала отчёт за прошедший день — сотруднику пройти самопроверку.
    "report_assembled": "Отчёт собран — проверьте",
    # Оргструктура (D-44): назначен руководителем подразделения; включён в подразделение или переведён.
    "org_head": "Вы — руководитель подразделения",
    "org_member": "Вы в подразделении",
    # Таймер рабочего дня (D-46): фактическое начало дня на согласовании у руководителя; решение — сотруднику.
    "workday_start_request": "Фактическое начало дня — на согласование",
    "workday_start_decided": "Решение по началу дня",
    # D-47: фактическое окончание дня (забыли «Завершить») — руководителю; решение — сотруднику; назначен график.
    "workday_end_request": "Фактическое окончание дня — на согласование",
    "workday_end_decided": "Решение по окончанию дня",
    # Сверхурочная/выходная работа (A-55): руководителю — запрос, сотруднику — исход решения.
    "overtime_request": "Запрос часов вне графика",
    "overtime_decided": "Решение по часам вне графика",
    "work_schedule": "График работы",
    # Сквозные потоки проекта (D-56): риск-разрешения РП, статусы потоков и проекта, конвертация лида в тендер.
    "risk_accepted": "Принято риск-разрешение",
    "risk_resolved": "Риск-разрешение снято",
    "workstream_status": "Статус этапа проекта",
    "project_stage": "Статус проекта",
    "opportunity_converted": "Лид переведён в тендер",
    # Ревизии РКД (этап 3 blueprint 52): решения проверки, подача на проверку, замечания, выдача пакета.
    "docrev_review": "Решение по ревизии документа",
    "docrev_submitted": "Ревизия направлена на проверку",
    "comment_added": "Замечание к ревизии",
    "transmittal_sent": "Трансмиттал отправлен",
    # Консолидация закупок (этап 4 blueprint 52): сформированный пакет и решение по аналогу.
    "batch_consolidated": "Закупочный пакет сформирован",
    "batch_received": "Поставка по закупочному пакету",
    "analog_decided": "Решение по аналогу позиции",
    # ПНР и FAT (этап 6 blueprint 52, D-68): итог испытания — РП проекта (финальный решает шлюз отгрузки).
    "fat_finished": "Итог испытания FAT",
    # Нормы трудоёмкости (этап 7 blueprint 52, D-72): решение по предложению — автору предложения.
    "norm_decided": "Решение по предложению нормы",
}


class NotificationMixin:
    """Создание уведомлений, их часть в pull и команда «отметить прочитанными»."""

    # ------------------------------------------------------------ кому
    def _users_with_role(self, conn, role: str, now) -> list[str]:
        """Активные пользователи, у которых глобальная роль role действует сейчас: своя выдача или замещение (D-34).

        Замещающий получает уведомления наравне с носителем — иначе на время отпуска руководитель «выпадал» бы из
        курса дел, а замещающий о них не знал.
        """
        rg, us, rd = role_grants, users, role_delegations
        out: list[str] = []
        for r in conn.execute(sa.select(rg.c.user_id).join(us, us.c.id == rg.c.user_id)
                              .where(rg.c.role == role, us.c.active == sa.true(), rg.c.revoked_at.is_(None),
                                     rg.c.valid_from <= now, sa.or_(rg.c.valid_until.is_(None), rg.c.valid_until > now))
                              .order_by(rg.c.user_id)):
            if r.user_id not in out:
                out.append(r.user_id)
        # Кандидаты по замещению: проверяем тем же правилом, что и права (active_delegated) — без цепочек и только
        # пока у носителя есть роль.
        today = now.date()
        scoped = sa.exists().where(role_delegation_projects.c.delegation_id == rd.c.id)
        for r in conn.execute(sa.select(rd.c.to_user_id).join(us, us.c.id == rd.c.to_user_id)
                              .where(rd.c.role == role, rd.c.revoked_at.is_(None), rd.c.valid_from <= today,
                                     rd.c.valid_until >= today, us.c.active == sa.true(), ~scoped)
                              .order_by(rd.c.to_user_id)):
            if r.to_user_id not in out and any(x[1] == role for x in active_delegated(conn, r.to_user_id, now)):
                out.append(r.to_user_id)
        return out

    def _user_name(self, conn, user_id: Optional[str]) -> str:
        """Имя сотрудника для текста уведомления («—», если не найден)."""
        if not user_id:
            return "—"
        r = conn.execute(sa.select(users.c.display_name).where(users.c.id == user_id)).first()
        return r.display_name if r else "—"

    # ------------------------------------------------------------ создание
    def _notify(self, ctx, user_ids: Iterable[Optional[str]], kind: str, title: str, body: Optional[str] = None,
                object_type: Optional[str] = None, object_id: Optional[str] = None, include_actor: bool = False) -> int:
        """Создать уведомление каждому из user_ids (без повторов, кроме автора действия и отключённых учёток).

        include_actor — уведомить и самого автора действия: программа сотрудника собрала его отчёт без него
        (D-43, «Отчёт собран — проверьте»), напоминание должно остаться в его уведомлениях.

        Вызывается из обработчика команды — запись идёт в транзакции команды. Возвращает число созданных записей.
        """
        if kind not in KIND_LABELS_RU:
            raise ValueError(f"неизвестный вид уведомления {kind}")
        seen: set[str] = set()
        n = 0
        for uid in user_ids:
            if not uid or (uid == ctx.user_id and not include_actor) or uid in seen:
                continue
            seen.add(uid)
            u = self._get(ctx.conn, users, uid)
            if not u or not u["active"]:
                continue
            ctx.conn.execute(notifications.insert().values(
                user_id=uid, kind=kind, title=title[:200], body=(body or None) and body[:1000],
                object_type=object_type, object_id=str(object_id) if object_id is not None else None,
                actor_id=ctx.user_id, created_at=ctx.now, read_at=None))
            n += 1
        return n

    def _actor_label(self, ctx, auth) -> str:
        """«Зайцева Вера» или «Никитина Ольга (замещает Зайцеву Веру)» — кто совершил действие и за кого."""
        name = self._user_name(ctx.conn, ctx.user_id)
        holder = getattr(auth, "on_behalf_of", None)
        return f"{name} (замещает: {self._user_name(ctx.conn, holder)})" if holder else name

    # ------------------------------------------------------------ команда
    def _notification_read(self, ctx, p, ev):
        """notification.read {ids?, all?} — отметить свои уведомления прочитанными (только при связи).

        ids — список id уведомлений (чужие и несуществующие молча пропускаются: они не ваши); all=true — все свои
        непрочитанные. Нужно одно из двух. Возвращает, сколько отмечено и сколько непрочитанных осталось.
        """
        only_keys(p, {"ids", "all"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "notification.read", ctx.now)
        ids, all_ = p.get("ids"), p.get("all")
        if all_ not in (None, True, False):
            raise Rejected("invalid_type", "all — true или false.", {"field": "all"})
        if not all_:
            if (not isinstance(ids, list) or not ids or len(ids) > 500
                    or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)):
                raise Rejected("field_required", "Укажите id уведомлений (список чисел, не больше 500) или all=true.",
                               {"field": "ids"})
        nt = notifications
        cond = sa.and_(nt.c.user_id == ctx.user_id, nt.c.read_at.is_(None))
        if not all_:
            cond = sa.and_(cond, nt.c.id.in_(ids))
        n = ctx.conn.execute(nt.update().where(cond).values(read_at=ctx.now)).rowcount or 0
        unread = self._unread_count(ctx.conn, ctx.user_id, ctx.now)
        ctx.log("notification", ctx.user_id, auth, None, None, {"read": n, "all": bool(all_)})
        return {"read": n, "unread": unread}

    # ------------------------------------------------------------ pull
    def _unread_count(self, conn, uid: str, now) -> int:
        """Сколько непрочитанных уведомлений за срок хранения в pull."""
        nt = notifications
        return conn.execute(sa.select(sa.func.count()).select_from(nt).where(
            nt.c.user_id == uid, nt.c.read_at.is_(None),
            nt.c.created_at >= now - timedelta(days=NOTIFY_KEEP_DAYS))).scalar_one()

    def _pull_notifications(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: свои уведомления за NOTIFY_KEEP_DAYS дней (новые сверху, не больше PULL_LIMIT) и число
        непрочитанных. Только свои — чужие уведомления не видит никто, включая администратора.
        """
        nt = notifications
        rows = conn.execute(sa.select(nt).where(nt.c.user_id == uid,
                                                nt.c.created_at >= now - timedelta(days=NOTIFY_KEEP_DAYS))
                            .order_by(nt.c.created_at.desc(), nt.c.id.desc()).limit(PULL_LIMIT))
        items = [{"id": r.id, "kind": r.kind, "kind_label": KIND_LABELS_RU.get(r.kind, r.kind), "title": r.title,
                  "body": r.body, "object_type": r.object_type, "object_id": r.object_id, "actor_id": r.actor_id,
                  "created_at": iso(r.created_at), "read_at": iso(r.read_at)} for r in rows]
        return {"notifications": items, "notifications_unread": self._unread_count(conn, uid, now)}
