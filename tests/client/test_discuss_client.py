"""Клиент: обсуждения объектов с @упоминаниями (P3-2) — представление objectDiscussion и ссылки уведомлений.

Проверяются чистые представления backend (без сервера): лента с тредами и глубиной, пометки «изменён»/«в очереди»/
«удалён», права-подсказки (can_add/can_moderate), подстановка vector://-ссылки в уведомление о комментарии.
"""
from datetime import datetime, timezone

from vector.client import notification_view as nv

ME, GIP, ENG = "u-me", "u-gip", "u-eng"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _backend(tmp_path):
    """Backend без транспорта (снимок подставляется напрямую)."""
    from PySide6.QtCore import QCoreApplication, QSettings

    from vector.client.backend import Backend
    QCoreApplication.instance() or QCoreApplication([])
    return Backend(client_factory=lambda *a: None,
                   settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                   auto_timers=False)


def _stock(be):
    """Мини-снимок: проект, комплект, документ, задача, назначения, люди, комментарии."""
    be._me = {"user_id": ME, "roles": ["engineer"]}
    be._people = {ME: "Я Сотрудник", GIP: "Волков Павел", ENG: "Лебедев Максим"}
    be._data["project"] = [{"id": "p-1", "code": "П-100", "title": "Проект"}]
    be._data["kit"] = [{"id": "k-1", "project_id": "p-1", "code": "АТХ", "title": "Автоматизация"}]
    be._data["document"] = [{"id": "d-1", "kit_id": "k-1", "code": "АТХ-001", "title": "Схема"}]
    be._data["task"] = [{"id": "t-1", "project_id": "p-1", "kit_id": "k-1", "title": "Сверить"}]
    be._assignments = [{"project_id": "p-1", "user_id": ME, "project_role": "participant"}]
    be._data["comment"] = [
        {"id": "c-1", "object_type": "kit", "object_id": "k-1", "project_id": "p-1", "parent_id": None,
         "author_id": GIP, "body": "Проверьте марку кабеля, @Я Сотрудник", "mentions": [ME], "version": 2,
         "created_at": "2026-11-16T09:00:00+00:00", "edited_at": "2026-11-16T09:30:00+00:00", "deleted": False},
        {"id": "c-2", "object_type": "kit", "object_id": "k-1", "project_id": "p-1", "parent_id": "c-1",
         "author_id": ME, "body": "Смотрю", "mentions": [], "version": 1,
         "created_at": "2026-11-16T09:05:00+00:00", "edited_at": None, "deleted": False},
        {"id": "c-3", "object_type": "kit", "object_id": "k-1", "project_id": "p-1", "parent_id": None,
         "author_id": ENG, "body": "", "mentions": [], "version": 2,
         "created_at": "2026-11-16T09:10:00+00:00", "edited_at": None, "deleted": True},
        # Локальный черновик из офлайн-очереди: сервер ещё не подтвердил (нет created_at/author_id).
        {"id": "c-4", "object_type": "kit", "object_id": "k-1", "body": "Черновик без связи", "parent_id": ""},
        # Комментарий другого объекта в ленту не попадает.
        {"id": "c-9", "object_type": "task", "object_id": "t-1", "project_id": "p-1", "parent_id": None,
         "author_id": GIP, "body": "По задаче", "mentions": [], "version": 1,
         "created_at": "2026-11-16T10:00:00+00:00", "edited_at": None, "deleted": False},
    ]
    return be


def test_object_discussion_threads_and_flags(tmp_path):
    be = _stock(_backend(tmp_path))
    v = be.objectDiscussion("kit", "k-1")
    assert [i["id"] for i in v["items"]] == ["c-1", "c-2", "c-3", "c-4"]
    root, reply, deleted, pending = v["items"]
    assert (root["depth"], root["author_name"], root["edited"]) == (0, "Волков Павел", True)
    assert root["mention_names"] == ["Я Сотрудник"]
    assert (reply["depth"], reply["mine"], reply["parent_id"]) == (1, True, "c-1")
    assert deleted["deleted"] and deleted["body"] == ""
    assert pending_is_marked(pending)
    assert v["count"] == 3  # удалённый не считается, неотправленный считается
    assert v["can_add"] is True and v["can_moderate"] is False


def pending_is_marked(item):
    return item["pending"] is True and item["created_label"] == "" and item["author_name"] == "Я Сотрудник"


def test_object_discussion_other_object_and_rights(tmp_path):
    be = _stock(_backend(tmp_path))
    v = be.objectDiscussion("task", "t-1")
    assert [i["id"] for i in v["items"]] == ["c-9"]
    # Без назначения в проекте комментировать нельзя (подсказка; решает сервер).
    be._assignments = []
    assert be.objectDiscussion("kit", "k-1")["can_add"] is False
    # Модерация — РП/ГИП проекта.
    be._assignments = [{"project_id": "p-1", "user_id": ME, "project_role": "rp"}]
    assert be.objectDiscussion("kit", "k-1")["can_moderate"] is True
    # Неизвестный объект — честный пустой вид без прав.
    v = be.objectDiscussion("kit", "nope")
    assert v["items"] == [] and v["can_add"] is False


def test_notification_link_to_discussed_object():
    """Уведомление «Вас упомянули» получает vector://-ссылку на объект обсуждения (P3-1 + P3-2)."""
    items = [{"id": 5, "kind": "mention", "title": "Вас упомянули в обсуждении", "actor_id": GIP,
              "object_type": "comment", "object_id": "c-1", "created_at": "2026-11-16T09:00:00+00:00"}]
    v = nv.notifications_view(items, {GIP: "Волков Павел"}, True, NOW,
                              links={"c-1": "vector://kit/k-1"})
    assert v["items"][0]["link"] == "vector://kit/k-1" and v["items"][0]["page"] == ""
    # Без ссылки (комментарий вне кэша) — как раньше: пустая ссылка, оболочка никуда не ведёт.
    v2 = nv.notifications_view(items, {}, True, NOW)
    assert v2["items"][0]["link"] == ""
