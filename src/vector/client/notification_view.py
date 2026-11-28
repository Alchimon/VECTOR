"""Представление уведомлений для интерфейса (D-35, ответ владельца на A-40) — чистые функции без Qt.

Уведомления создаёт сервер (vector.server.notify) и отдаёт в pull — свои за 90 дней. Клиент показывает их списком
(новые сверху, непрочитанные выделены), число непрочитанных — бейджем в меню, а о новых, пришедших при фоновом
обновлении, сообщает всплывающей подсказкой. «Открыть» ведёт к объекту (заказ — в «Закупку», замещение — в
«Замещения»). Отметить прочитанным можно только при связи (команда notification.read, только свои).
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any, Optional

# Значок и тон по виду уведомления (значки — из набора Lucide в assets/icons).
KIND_ICONS = {"po_submitted": "shield-check", "po_approved": "circle-check", "po_returned": "undo-2",
              "po_withdrawn": "undo-2", "delegation_created": "user-check", "delegation_revoked": "user-x",
              # Технология и производство (D-36).
              "tp_approved": "circle-check", "wo_assigned": "factory", "wo_problem": "triangle-alert",
              "wo_completed": "check-check",
              # Качество (D-39): рекламации и CAPA, запросы (ECR) и извещения (ECO) об изменении.
              "claim_new": "shield-x", "claim_assigned": "user-check", "capa_assigned": "list-checks",
              "claim_check": "clipboard-check", "claim_checked": "circle-check", "ecr_new": "file-pen-line",
              "ecr_assessed": "sliders-horizontal", "ecr_decided": "gavel", "eco_approval": "file-check",
              "eco_approved": "circle-check", "eco_implemented": "check-check", "eco_verified": "clipboard-check",
              # D-40: срок ответа заказчику подходит / прошёл; зарегистрирована критическая рекламация.
              "claim_due_soon": "calendar-clock", "claim_overdue": "triangle-alert", "claim_critical": "octagon-x",
              # Рабочее время (D-42): дневной отчёт на проверке, принят, возвращён, принятый исправляется.
              "report_submitted": "clock", "report_accepted": "circle-check", "report_returned": "undo-2",
              "report_reopened": "rotate-ccw",
              # D-43: отчёт собран программой — пройти самопроверку.
              "report_assembled": "sparkles",
              # Оргструктура (D-44): назначен руководителем подразделения; включён в подразделение или переведён.
              "org_head": "network", "org_member": "users",
              # Таймер рабочего дня (D-46): фактическое начало дня — на согласование руководителю; решение сотруднику.
              "workday_start_request": "sunrise", "workday_start_decided": "timer",
              # D-47: фактическое окончание дня — на согласование; решение; назначен график работы.
              "workday_end_request": "sunset", "workday_end_decided": "timer", "work_schedule": "calendar-clock"}
KIND_TONES = {"po_submitted": "warning", "po_approved": "success", "po_returned": "danger",
              "po_withdrawn": "neutral", "delegation_created": "info", "delegation_revoked": "neutral",
              "tp_approved": "success", "wo_assigned": "info", "wo_problem": "danger", "wo_completed": "success",
              "claim_new": "warning", "claim_assigned": "info", "capa_assigned": "info", "claim_check": "primary",
              "claim_checked": "success", "ecr_new": "info", "ecr_assessed": "info", "ecr_decided": "success",
              "eco_approval": "warning", "eco_approved": "success", "eco_implemented": "primary", "eco_verified": "success",
              "claim_due_soon": "warning", "claim_overdue": "danger", "claim_critical": "danger",
              "report_submitted": "warning", "report_accepted": "success", "report_returned": "danger", "report_reopened": "info",
              "report_assembled": "primary", "org_head": "info", "org_member": "info",
              "workday_start_request": "warning", "workday_start_decided": "info",
              "workday_end_request": "warning", "workday_end_decided": "info", "work_schedule": "info"}
# Куда ведёт «Открыть»: раздел оболочки по типу объекта.
TARGET_PAGES = {"purchase_order": "procurement", "delegation": "delegations", "tech_process": "production",
                "work_order": "production", "claim": "quality", "change_request": "quality", "change_order": "quality",
                "daily_report": "worktime", "org_unit": "org", "workday_start_request": "worktime",
                "workday_end_request": "worktime", "work_schedule": "worktime"}


def when_label(at: Optional[str], now: datetime, tz: Optional[tzinfo] = None) -> str:
    """Время уведомления по-человечески в местном времени: «сегодня, 09:15», «вчера, 18:02», «14.11.2026, 09:15»."""
    if not at:
        return ""
    try:
        dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local, today = dt.astimezone(tz), now.astimezone(tz).date()
    hm = local.strftime("%H:%M")
    days = (today - local.date()).days
    if days == 0:
        return f"сегодня, {hm}"
    if days == 1:
        return f"вчера, {hm}"
    return f"{local:%d.%m.%Y}, {hm}"


def notifications_view(items: list[dict], people: dict[str, str], online: bool, now: datetime,
                       tz: Optional[tzinfo] = None, links: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Всё для раздела «Уведомления»: список с подписями, число непрочитанных и что можно сделать.

    * items — уведомления из pull (новые сверху); у каждого добавляются when (время), actor_name, icon, tone,
      unread, page (куда ведёт «Открыть», пусто — некуда), link (vector://-ссылка на объект — для уведомлений
      об обсуждениях, P3-2: по ней оболочка открывает сам объект, а не раздел);
    * unread — сколько непрочитанных (бейдж в меню); unread_ids — их id (для «Прочитать все» и тестов);
    * can_mark — отметить прочитанными можно только при связи.
    """
    rows = []
    for n in items or []:
        rows.append({**n, "when": when_label(n.get("created_at"), now, tz),
                     "actor_name": people.get(n.get("actor_id") or "", ""),
                     "icon": KIND_ICONS.get(n.get("kind"), "bell"), "tone": KIND_TONES.get(n.get("kind"), "neutral"),
                     "unread": not n.get("read_at"), "page": TARGET_PAGES.get(n.get("object_type") or "", ""),
                     "link": (links or {}).get(str(n.get("object_id") or ""), "")})
    unread = [r["id"] for r in rows if r["unread"]]
    return {"items": rows, "unread": len(unread), "unread_ids": unread, "can_mark": online and bool(unread),
            "online": online, "empty_text": "Уведомлений нет. Здесь появятся заказы на согласовании, решения по ним "
                                            "и замещения, которые вас касаются."}


def new_since(items: list[dict], last_seen_id: int) -> list[dict]:
    """Непрочитанные уведомления новее last_seen_id — для всплывающей подсказки после фонового обновления."""
    return [n for n in items or [] if not n.get("read_at") and int(n.get("id") or 0) > last_seen_id]


def toast_text(new: list[dict]) -> str:
    """Текст подсказки о новых уведомлениях: заголовок единственного или «Новых уведомлений: N»."""
    if not new:
        return ""
    if len(new) == 1:
        return "Уведомление: " + (new[0].get("title") or "")
    return f"Новых уведомлений: {len(new)}. Откройте раздел «Уведомления»."
