# -*- coding: utf-8 -*-
"""Обсуждения объектов (P3-2): комментарии к проекту, комплекту, документу или задаче с тредами и @упоминаниями.

Упоминание — маркер @display_name в тексте (допускаются пробелы: @Иван Иванов). Сервер подтверждает каждое
упоминание: упомянутый обязан читать проект (своё действующее назначение или роль по замещению, D-35) — иначе
отказ, чтобы упоминание не стало способом «подключить» постороннего. Упомянутые получают уведомление
«Вас упомянули» (kind mention); автор родительского комментария — «Ответ в обсуждении» (comment_reply).

Правка — только автору и только в течение 24 часов после публикации (маркеров «тихой правки» нет: edited_at
показывается в ленте). Удаление мягкое (deleted_at): треды не разваливаются, в ленте остаётся заглушка
«Комментарий удалён»; удалить может автор либо РП/ГИП проекта. Повторная правка пересчитывает упоминания;
уведомление получают только ВНОВЬ упомянутые (кто уже упоминался в прежней версии — не спамим).
"""

import json
from datetime import timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core.errors import Rejected

from .authz import active_delegated_project, active_project_roles, project_member
from .db import comments, documents, kits, projects, tasks, users
from .validation import iso, only_keys, req_str, req_uuid

# Типы объектов, к которым разрешены обсуждения, и их таблицы (проект объекта всегда сверяется с объектом,
# project_id в команде игнорируется — берём из объекта, как в task.create).
_OBJECT_TABLES = {"project": projects, "kit": kits, "document": documents, "task": tasks}
_OBJECT_LABELS_RU = {"project": "Проект", "kit": "Комплект", "document": "Документ", "task": "Задача"}
# Окно правки автором после публикации (дольше — только удаление).
_EDIT_WINDOW = timedelta(hours=24)


class DiscussMixin:
    """Обработчики команд discuss.* и представление комментария для pull."""

    @staticmethod
    def v_discuss_comment(o: dict) -> dict:
        """Комментарий для клиента: текст (пустой у удалённого), автор, тред, подтверждённые упоминания, версия,
        метки времени. Удалённый комментарий остаётся в ленте заглушкой — ответы на него не теряются."""
        deleted = o["deleted_at"] is not None
        return {"id": o["id"], "object_type": o["object_type"], "object_id": o["object_id"],
                "project_id": o["project_id"], "parent_id": o["parent_id"], "author_id": o["author_id"],
                "body": "" if deleted else o["body"],
                "mentions": json.loads(o["mentions"]) if o["mentions"] else [],
                "version": o["version"], "created_at": iso(o["created_at"]),
                "edited_at": iso(o["edited_at"]) if o["edited_at"] else None,
                "deleted": deleted}

    # ------------------------------------------------------------ общие проверки

    def _comment_object(self, ctx, object_type: str, object_id: str) -> dict:
        """Объект обсуждения с project_id. Для всех типов в схеме есть project_id (project — сам себе проект);
        об отсутствующем объекте говорим нейтрально, не раскрывая существования."""
        if object_type not in _OBJECT_TABLES:
            raise Rejected("bad_object_type", "Обсуждение возможно для проекта, комплекта, документа или задачи.",
                           {"field": "object_type"})
        t = _OBJECT_TABLES[object_type]
        o = self._get(ctx.conn, t, object_id)
        if o is None:
            raise Rejected("object_not_found", f"{_OBJECT_LABELS_RU[object_type]} не найден.",
                           {"field": "object_id"})
        return o if object_type != "project" else {**o, "project_id": o["id"]}

    def _mentions(self, ctx, project_id: str, body: str) -> list[str]:
        """Подтверждённые упоминания: активные пользователи, чьё @display_name встречается в тексте (регистр
        не важен) и кто читает этот проект (назначение или замещение, D-35). Найденное, но не подтверждённое
        упоминание — отказ: так нельзя «прощупать», есть ли человек в проекте, через чужие обсуждения."""
        if "@" not in body:
            return []
        low = body.lower()
        found: list[tuple[str, str]] = []          # (user_id, display_name)
        rejected_name: Optional[str] = None
        for u in ctx.conn.execute(sa.select(users.c.id, users.c.display_name).where(users.c.active)):
            name = u.display_name
            if name and f"@{name.lower()}" in low:
                if project_member(ctx.conn, u.id, project_id, ctx.now):
                    found.append((u.id, name))
                elif u.id != ctx.user_id:
                    rejected_name = name
        if rejected_name is not None:
            raise Rejected("mention_not_allowed",
                           f"Пользователь «{rejected_name}» не участвует в проекте — упомянуть его здесь нельзя.",
                           {"field": "body"})
        # Свой комментарий сам себя не «упоминает» — уведомление автору не нужно.
        return [uid for uid, _ in found if uid != ctx.user_id]

    def _require_comment(self, ctx, cid: str) -> dict:
        c = self._get(ctx.conn, comments, cid)
        if c is None:
            raise Rejected("comment_not_found", "Комментарий не найден.")
        return c

    def _object_title(self, ctx, object_type: str, object_id: str) -> str:
        """Короткая подпись объекта для текста уведомления (обозначение/название, если есть)."""
        o = self._get(ctx.conn, _OBJECT_TABLES[object_type], object_id)
        if not o:
            return ""
        for col in ("code", "title", "name"):
            if o.get(col):
                return f" «{o[col]}»"
        return ""

    # ------------------------------------------------------------ команды

    def _discuss_add(self, ctx, p, ev):
        """discuss.add {id, object_type, object_id, body, parent_id?} — комментарий к объекту проекта.

        Упоминания (@display_name) подтверждаются сервером; ответ (parent_id) — только на комментарий того же
        объекта. Можно офлайн (очередь): проверки повторяются при отправке. Уведомления: упомянутым — «Вас
        упомянули», автору родителя — «Ответ в обсуждении» (упомянутый автор родителя получает одно)."""
        only_keys(p, {"id", "object_type", "object_id", "body", "parent_id"})
        o = self._comment_object(ctx, req_str(p, "object_type", 16), req_uuid(p, "object_id"))
        pid = o["project_id"]
        # Права проверяются ДО подробностей об объекте (не подсказываем посторонним).
        auth = self.policy.check(ctx.conn, ctx.user_id, "discuss.add", ctx.now, project_id=pid)
        cid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, comments, cid)
        body = req_str(p, "body", 4000)
        parent_id = p.get("parent_id")
        parent: Optional[dict] = None
        if parent_id is not None:
            parent = self._require_comment(ctx, req_uuid(p, "parent_id"))
            if parent["object_type"] != p["object_type"] or parent["object_id"] != p["object_id"]:
                raise Rejected("parent_mismatch", "Ответ возможен только на комментарий того же объекта.")
        mentioned = self._mentions(ctx, pid, body)
        row = {"id": cid, "object_type": p["object_type"], "object_id": p["object_id"], "project_id": pid,
               "parent_id": parent["id"] if parent else None, "author_id": ctx.user_id, "body": body,
               "mentions": json.dumps(mentioned, ensure_ascii=False), "version": 1, "created_at": ctx.now,
               "edited_at": None, "deleted_at": None}
        ctx.conn.execute(comments.insert().values(**row))
        ctx.log("comment", cid, auth, None, 1,
                {"object_type": row["object_type"], "object_id": row["object_id"], "parent_id": row["parent_id"],
                 "mentions": mentioned})
        title = self._object_title(ctx, row["object_type"], row["object_id"])
        where = f"{_OBJECT_LABELS_RU[row['object_type']]}{title}"
        if mentioned:
            self._notify(ctx, mentioned, "mention",
                         f"Вас упомянули в обсуждении: {where}", body[:200], "comment", cid)
        if parent is not None and parent["author_id"] not in mentioned and parent["deleted_at"] is None:
            self._notify(ctx, [parent["author_id"]], "comment_reply",
                         f"Ответ в обсуждении: {where}", body[:200], "comment", cid)
        return {"comment": self.v_discuss_comment(self._get(ctx.conn, comments, cid))}

    def _discuss_edit(self, ctx, p, ev):
        """discuss.edit {id, body} — правка своего комментария в течение 24 часов (version+1). Упоминания
        пересчитываются; уведомление получают только вновь упомянутые."""
        only_keys(p, {"id", "body"})
        c = self._require_comment(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "discuss.edit", ctx.now, project_id=c["project_id"])
        self._check_version(c, ev, self.v_discuss_comment)
        if c["author_id"] != ctx.user_id:
            raise Rejected("not_author", "Изменить комментарий может только его автор.")
        if c["deleted_at"] is not None:
            raise Rejected("comment_deleted", "Комментарий удалён.")
        if ctx.now - c["created_at"] > _EDIT_WINDOW:
            raise Rejected("edit_window_passed", "Правка возможна в течение 24 часов после публикации.")
        body = req_str(p, "body", 4000)
        if body == c["body"]:
            raise Rejected("no_changes", "Нет изменений.")
        mentioned = self._mentions(ctx, c["project_id"], body)
        was = set(json.loads(c["mentions"]) if c["mentions"] else [])
        ctx.conn.execute(comments.update().where(comments.c.id == c["id"]).values(
            body=body, mentions=json.dumps(mentioned, ensure_ascii=False),
            version=c["version"] + 1, edited_at=ctx.now))
        ctx.log("comment", c["id"], auth, c["version"], c["version"] + 1,
                {"mentions_added": [u for u in mentioned if u not in was]})
        fresh = [u for u in mentioned if u not in was]
        if fresh:
            title = self._object_title(ctx, c["object_type"], c["object_id"])
            self._notify(ctx, fresh, "mention",
                         f"Вас упомянули в обсуждении: {_OBJECT_LABELS_RU[c['object_type']]}{title}",
                         body[:200], "comment", c["id"])
        return {"comment": self.v_discuss_comment(self._get(ctx.conn, comments, c["id"]))}

    def _discuss_delete(self, ctx, p, ev):
        """discuss.delete {id} — мягкое удаление: автор либо РП/ГИП проекта (модерация). Текст в ленте заменяется
        заглушкой, ответы остаются."""
        only_keys(p, {"id"})
        c = self._require_comment(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "discuss.delete", ctx.now, project_id=c["project_id"])
        self._check_version(c, ev, self.v_discuss_comment)
        if c["deleted_at"] is not None:
            raise Rejected("comment_deleted", "Комментарий уже удалён.")
        if c["author_id"] != ctx.user_id:
            roles = {role for _, role in active_project_roles(ctx.conn, ctx.user_id, c["project_id"], ctx.now)}
            roles |= {role for _, role, _, _ in active_delegated_project(ctx.conn, ctx.user_id, c["project_id"],
                                                                         ctx.now)}
            if not roles & {"rp", "gip"}:
                raise Rejected("not_moderator", "Удалить чужой комментарий может руководитель проекта или ГИП.")
        ctx.conn.execute(comments.update().where(comments.c.id == c["id"]).values(
            version=c["version"] + 1, deleted_at=ctx.now))
        ctx.log("comment", c["id"], auth, c["version"], c["version"] + 1, {"deleted": True})
        return {"comment": self.v_discuss_comment(self._get(ctx.conn, comments, c["id"]))}
