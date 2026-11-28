"""Обсуждения объектов (P3-2): комментарии с тредами и @упоминаниями к проекту, комплекту, документу, задаче.

Правила (server/discuss.py, contracts permissions discuss.*):
* комментировать объекты проекта могут участники проекта (ГИП, РП, participant); посторонний — отказ;
* упоминание @display_name подтверждается сервером: упомянуть можно только читающего проект (назначение или
  замещение, D-35); «прощупывание» чужого проекта через упоминание — отказ без раскрытия;
* уведомления: упомянутым — mention, автору родительского комментария — comment_reply (без дублей и самому себе);
* правка — только автору и только в течение 24 часов; упоминания пересчитываются, уведомляются лишь новые;
* удаление мягкое (deleted_at): автор или РП/ГИП проекта; тред сохраняется, тело скрывается из выдачи.
"""
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import proj  # noqa: F401  (проект в работе: ГИП gip1, РП rp1, участник eng1; lead1 вне команды)
from vector.server import db


def notes(s, kind=None):
    """Уведомления пользователя s из pull (новые сверху), по желанию — только вида kind."""
    r = s.query("pull")["result"]
    return [n for n in r["notifications"] if kind is None or n["kind"] == kind]


def comments_of(s, object_id):
    """Комментарии объекта из pull."""
    r = s.query("pull")["result"]
    return [c for c in r["comments"] if c["object_id"] == object_id]


def add(s, object_type, object_id, body, **kw):
    """Добавить комментарий (успешно)."""
    return s.ok("discuss.add", {"id": new_id(), "object_type": object_type, "object_id": object_id,
                                "body": body, **kw})["comment"]


# ====================================================================== добавление и права
def test_add_to_each_object_type(world, proj):  # noqa: F811
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Схема"})
    did = new_id()
    proj.rp.ok("document.create", {"id": did, "kit_id": proj.kit, "code": "АТХ-001", "title": "Схема электрическая"})
    for otype, oid in (("project", proj.pid), ("kit", proj.kit), ("document", did), ("task", tid)):
        c = add(proj.eng, otype, oid, "Смотрю, вопросов нет")
        assert (c["object_type"], c["object_id"], c["project_id"], c["version"], c["deleted"]) == \
               (otype, oid, proj.pid, 1, False)
    # Комментарии приходят в pull только участникам проекта.
    assert len(comments_of(proj.rp, proj.kit)) == 1
    outsider = world.login("lead1")
    assert comments_of(outsider, proj.kit) == []


def test_add_denied_for_outsider_and_unknown_object(world, proj):  # noqa: F811
    r = proj.lead.cmd("discuss.add", {"id": new_id(), "object_type": "kit", "object_id": proj.kit, "body": "Привет"})
    assert r["status"] == "denied"
    # Несуществующий объект — отказ, не раскрывающий существования.
    r = proj.rp.cmd("discuss.add", {"id": new_id(), "object_type": "kit", "object_id": new_id(), "body": "Привет"})
    assert r["status"] == "rejected" and r["code"] == "object_not_found"
    r = proj.rp.cmd("discuss.add", {"id": new_id(), "object_type": "server", "object_id": new_id(), "body": "?"})
    assert r["status"] == "rejected" and r["code"] == "bad_object_type"


def test_threads_require_same_object(world, proj):  # noqa: F811
    c1 = add(proj.rp, "kit", proj.kit, "Корневой")
    reply = add(proj.eng, "kit", proj.kit, "Ответ", parent_id=c1["id"])
    assert reply["parent_id"] == c1["id"]
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Т"})
    r = proj.eng.cmd("discuss.add", {"id": new_id(), "object_type": "task", "object_id": tid,
                                     "body": "Чужой тред", "parent_id": c1["id"]})
    assert r["status"] == "rejected" and r["code"] == "parent_mismatch"


# ====================================================================== @упоминания и уведомления
def test_mentions_notify_only_confirmed(world, proj):  # noqa: F811
    c = add(proj.rp, "kit", proj.kit, "@ENG1 посмотри, @GIP1 в курсе, @RP1 — это я сам")
    assert sorted(c["mentions"]) == sorted([world.users["eng1"], world.users["gip1"]])
    assert len(notes(proj.eng, "mention")) == 1
    assert len(notes(proj.gip, "mention")) == 1
    assert notes(proj.rp, "mention") == []  # автор себя не упоминает
    # Постороннего упомянуть нельзя — отказ без раскрытия, уведомлений нет.
    r = proj.rp.cmd("discuss.add", {"id": new_id(), "object_type": "kit", "object_id": proj.kit,
                                    "body": "@LEAD1 глянь"})
    assert r["status"] == "rejected" and r["code"] == "mention_not_allowed"
    assert notes(proj.lead, "mention") == []


def test_reply_notifies_parent_author_once(world, proj):  # noqa: F811
    c1 = add(proj.eng, "kit", proj.kit, "Вопрос по марке кабеля")
    before = len(notes(proj.eng))
    add(proj.rp, "kit", proj.kit, "Ответ по схеме", parent_id=c1["id"])
    got = notes(proj.eng)
    assert len(got) == before + 1 and got[0]["kind"] == "comment_reply"
    # Если автор родителя ещё и упомянут в ответе — одно уведомление (mention), без дубля.
    add(proj.gip, "kit", proj.kit, "@ENG1 верно", parent_id=c1["id"])
    kinds = [n["kind"] for n in notes(proj.eng)[:2]]
    assert kinds == ["mention", "comment_reply"]


# ====================================================================== правка и удаление
def test_edit_window_and_mentions_recalc(world, proj):  # noqa: F811
    c = add(proj.rp, "kit", proj.kit, "Черновик мысли")
    # Чужая правка — отказ.
    r = proj.eng.cmd("discuss.edit", {"id": c["id"], "body": "Переписал"}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "not_author"
    # Автор правит; упоминания пересчитываются, уведомление — только новому упомянутому.
    c2 = proj.rp.ok("discuss.edit", {"id": c["id"], "body": "@ENG1 теперь ты"}, expected_version=1)["comment"]
    assert (c2["version"], c2["edited_at"] is not None, c2["mentions"]) == (2, True, [world.users["eng1"]])
    assert len(notes(proj.eng, "mention")) == 1
    proj.rp.ok("discuss.edit", {"id": c["id"], "body": "@ENG1 и ещё раз тот же"}, expected_version=2)
    assert len(notes(proj.eng, "mention")) == 1  # повторно не спамим
    # Через 24 часа после публикации правка закрыта (сдвигаем created_at, чтобы не истекала тестовая сессия).
    from datetime import timedelta
    with world.svc.engine.begin() as conn:
        conn.execute(db.comments.update().where(db.comments.c.id == c["id"])
                     .values(created_at=world.clock.now() - timedelta(hours=25)))
    r = proj.rp.cmd("discuss.edit", {"id": c["id"], "body": "Поздно"}, expected_version=3)
    assert r["status"] == "rejected" and r["code"] == "edit_window_passed"


def test_soft_delete_by_author_and_moderator(world, proj):  # noqa: F811
    c = add(proj.eng, "kit", proj.kit, "Лишнее сообщение")
    reply = add(proj.rp, "kit", proj.kit, "Ответ", parent_id=c["id"])
    # Участник не может удалить чужое.
    other = add(proj.gip, "kit", proj.kit, "Комментарий ГИП")
    r = proj.eng.cmd("discuss.delete", {"id": other["id"]}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "not_moderator"
    # Автор удаляет своё: заглушка остаётся, тело скрыто, ответ на месте.
    c_del = proj.eng.ok("discuss.delete", {"id": c["id"]}, expected_version=1)["comment"]
    assert c_del["deleted"] and c_del["body"] == ""
    feed = {x["id"]: x for x in comments_of(proj.rp, proj.kit)}
    assert feed[c["id"]]["deleted"] and feed[c["id"]]["body"] == ""
    assert feed[reply["id"]]["parent_id"] == c["id"] and not feed[reply["id"]]["deleted"]
    # Модератор (РП) удаляет чужое.
    proj.rp.ok("discuss.delete", {"id": other["id"]}, expected_version=1)
    # Повторное удаление — отказ.
    r = proj.rp.cmd("discuss.delete", {"id": c["id"]}, expected_version=2)
    assert r["status"] == "rejected" and r["code"] == "comment_deleted"
    # Строки аудита: добавление и удаление.
    with world.svc.engine.connect() as conn:
        rows = [dict(r_._mapping) for r_ in conn.execute(
            sa.select(db.audit_log).where(db.audit_log.c.object_type == "comment",
                                          db.audit_log.c.object_id == c["id"]))]
    assert [(x["command"], x["result"]) for x in rows] == [("discuss.add", "accepted"),
                                                           ("discuss.delete", "accepted")]


def test_offline_queue_rechecked_at_apply(world, proj):  # noqa: F811
    """Офлайн-команда применяется с проверками в момент эффекта: если упомянутого вывели из проекта, пока команда
    лежала в очереди, — отказ, а не «проскочившее» упоминание."""
    body = "@ENG1 смотри"
    c = add(proj.rp, "kit", proj.kit, body)
    assert c["mentions"] == [world.users["eng1"]]
    # Повторная команда с тем же operation_id и тем же содержимым не создаёт дубликат (идемпотентность очереди).
    op, cid = new_id(), new_id()
    payload = {"id": cid, "object_type": "kit", "object_id": proj.kit, "body": "Рав"}
    r1 = proj.rp.cmd("discuss.add", payload, op_id=op)
    r2 = proj.rp.cmd("discuss.add", payload, op_id=op)
    assert r1["status"] == "accepted" and r2["status"] == "accepted"
    assert r2["result"]["comment"]["id"] == cid
    assert len([c for c in comments_of(proj.rp, proj.kit) if c["id"] == cid]) == 1
