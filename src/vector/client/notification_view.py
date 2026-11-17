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
              "po_withdrawn": "undo-2", "delegation_created": "user-check", "delegation_revoked": "user-x"}
KIND_TONES = {"po_submitted": "warning", "po_approved": "success", "po_returned": "danger",
              "po_withdrawn": "neutral", "delegation_created": "info", "delegation_revoked": "neutral"}
# Куда ведёт «Открыть»: раздел оболочки по типу объекта.
TARGET_PAGES = {"purchase_order": "procurement", "delegation": "delegations"}


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
                       tz: Optional[tzinfo] = None) -> dict[str, Any]:
    """Всё для раздела «Уведомления»: список с подписями, число непрочитанных и что можно сделать.

    * items — уведомления из pull (новые сверху); у каждого добавляются when (время), actor_name, icon, tone,
      unread, page (куда ведёт «Открыть», пусто — некуда);
    * unread — сколько непрочитанных (бейдж в меню); unread_ids — их id (для «Прочитать все» и тестов);
    * can_mark — отметить прочитанными можно только при связи.
    """
    rows = []
    for n in items or []:
        rows.append({**n, "when": when_label(n.get("created_at"), now, tz),
                     "actor_name": people.get(n.get("actor_id") or "", ""),
                     "icon": KIND_ICONS.get(n.get("kind"), "bell"), "tone": KIND_TONES.get(n.get("kind"), "neutral"),
                     "unread": not n.get("read_at"), "page": TARGET_PAGES.get(n.get("object_type") or "", "")})
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
