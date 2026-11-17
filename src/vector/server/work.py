"""Задачи и документы проекта (срез 1, завершение цепочки «комплект → задача → документ с версиями»).

Модуль — примесь (mixin) к `Service`: обработчики команд пользуются общей инфраструктурой сервиса
(транзакция, идемпотентность по operation_id, журнал аудита, проверка версий) и регистрируются в
`Service.handlers`. Здесь только бизнес-правила задач, документов и файлов.

Главные инварианты (каждый закреплён тестом в tests/server/test_tasks_documents.py):

* Права — только по ролям В ПРОЕКТЕ (A-13, A-14) и проверяются в момент эффекта, в той же транзакции.
* Задача: статусы open → in_progress → done; отмена и повторное открытие — только ГИП/РП и с причиной.
  Исполнитель (участник проекта) может двигать СВОЮ задачу по «своим» переходам (workflows: own_transitions).
* Исполнитель задачи — только пользователь с действующим назначением в этом проекте.
* Документ: обозначение уникально в проекте; файл хранится в НЕИЗМЕНЯЕМЫХ версиях с SHA-256 и размером.
  Новая версия — только онлайн (файлы не ставятся в офлайн-очередь и не сливаются при конфликте).
* Файл можно привязать к версии, только если ЭТОТ пользователь сам загрузил его байты (квитанция file_uploads).
* При скачивании содержимое пересчитывается и сверяется с SHA-256: повреждённый файл не отдаётся.
* Каждое изменение, загрузка и скачивание файла записываются в журнал аудита.
"""
from __future__ import annotations

import hashlib
import io
from typing import Any, Optional

import sqlalchemy as sa

from vector.core.errors import Denied, Rejected

from .authz import Authority, active_delegated_project, project_member
from .db import FILE_CHUNK, document_versions, documents, file_blobs, file_chunks, file_uploads, kits, projects, tasks
from .validation import iso, only_keys, opt_date, opt_str, req_str, req_uuid

_SHA_RE_LEN = 64
# Символы, недопустимые в имени файла на Windows, плюс разделители путей: имя файла хранится как
# метка версии и при скачивании предлагается пользователю — путь из него не должен «выпрыгнуть» из папки.
_BAD_NAME_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


class _SourceChanged(Exception):
    """Источник загрузки изменился между проходом хэширования и записью — транзакция отменяется."""


def _check_sha256(v: object) -> str:
    """SHA-256 в виде 64 строчных шестнадцатеричных символов (иначе — отказ с понятным текстом)."""
    if not isinstance(v, str) or len(v) != _SHA_RE_LEN or any(ch not in "0123456789abcdef" for ch in v):
        raise Rejected("invalid_sha256", "Контрольная сумма файла должна быть SHA-256 (64 шестнадцатеричных символа).",
                       {"field": "sha256"})
    return v


def _check_file_name(p: dict) -> str:
    """Имя файла версии документа: до 255 символов, без символов, запрещённых в Windows (\\ / : * ? " < > |),
    управляющих символов, «.»/«..» и точки/пробела в конце. Так файл безопасно сохраняется на любом ПК при
    скачивании.
    """
    name = req_str(p, "file_name", 255)
    if any(ch in _BAD_NAME_CHARS for ch in name) or name in (".", "..") or name.endswith((" ", ".")):
        raise Rejected("invalid_file_name", "Имя файла содержит недопустимые символы (\\ / : * ? \" < > | или управляющие).",
                       {"field": "file_name"})
    return name


class WorkMixin:
    """Обработчики команд task.* и document.*, загрузка/скачивание файлов, представления для pull."""

    # ------------------------------------------------------------ представления (то, что видит клиент)
    @staticmethod
    def v_task(o: dict) -> dict:
        """Задача для клиента: комплект, исполнитель, срок, ссылка на документ (замечание), статус с причиной, версия.
        """
        return {"id": o["id"], "project_id": o["project_id"], "kit_id": o["kit_id"], "title": o["title"],
                "description": o["description"], "assignee_user_id": o["assignee_user_id"],
                "due_date": o["due_date"].isoformat() if o["due_date"] else None, "document_id": o["document_id"],
                "status": o["status"], "status_reason": o["status_reason"], "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_document(o: dict) -> dict:
        """Карточка документа для клиента: обозначение, название, номер текущей версии файла."""
        return {"id": o["id"], "project_id": o["project_id"], "kit_id": o["kit_id"], "code": o["code"], "title": o["title"],
                "current_version_no": o["current_version_no"], "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_document_version(o: dict) -> dict:
        """Метаданные версии файла (без содержимого): имя, SHA-256, размер, комментарий, кто и когда загрузил."""
        return {"id": o["id"], "document_id": o["document_id"], "version_no": o["version_no"], "file_name": o["file_name"],
                "sha256": o["sha256"], "size_bytes": o["size_bytes"], "comment": o["comment"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"])}

    # ------------------------------------------------------------ общие проверки
    def _open_project_of_kit(self, ctx, kit_id: str) -> tuple[dict, dict]:
        """Комплект и его проект; проект должен быть в работе (как и для комплектов, см. kit.create)."""
        kit = self._require(ctx.conn, kits, kit_id, "Комплект")
        pr = self._get(ctx.conn, projects, kit["project_id"])
        return kit, pr

    @staticmethod
    def _project_must_be_active(pr: dict) -> None:
        """Изменения задач и документов возможны только в проекте в работе («Разработка РКД»)."""
        if pr["status"] != "rkd_in_progress":
            raise Rejected("project_state", "Изменения возможны только в проекте в статусе «Разработка РКД».")

    def _assignee(self, ctx, project_id: str, value: Any) -> Optional[str]:
        """Исполнитель: None — «не назначен»; иначе пользователь с ДЕЙСТВУЮЩИМ назначением в проекте или
        замещающий участника этого проекта (D-35: задачи отсутствующего можно передать его замещающему).

        Проверяется на сервере в момент эффекта: если назначение отозвали, пока правка лежала в офлайн-очереди,
        команда будет отклонена, а не «проскочит» со старыми данными.
        """
        if value is None:
            return None
        uid = req_uuid({"assignee_user_id": value}, "assignee_user_id")
        if not project_member(ctx.conn, uid, project_id, ctx.now):
            raise Rejected("assignee_not_in_project", "Исполнителем может быть только участник команды этого проекта "
                           "или его замещающий.",
                           {"field": "assignee_user_id"})
        return uid

    def _task_document(self, ctx, project_id: str, value: Any) -> Optional[str]:
        """Необязательная ссылка задачи на документ того же проекта (замечание к документу)."""
        if value is None:
            return None
        did = req_uuid({"document_id": value}, "document_id")
        d = self._get(ctx.conn, documents, did)
        if d is None or d["project_id"] != project_id:
            raise Rejected("document_not_in_project", "Документ не найден в этом проекте.", {"field": "document_id"})
        return did

    # ------------------------------------------------------------ задачи
    def _task_create(self, ctx, p, ev):
        """task.create {id, kit_id, title, description?, assignee_user_id?, due_date?, document_id?} — задача в
        комплекте (A-13).

        Исполнитель — только участник проекта с действующим назначением; ссылка на документ — только того же
        проекта. Можно офлайн (очередь): при отправке всё проверяется заново.
        """
        only_keys(p, {"id", "kit_id", "title", "description", "assignee_user_id", "due_date", "document_id"})
        kit, pr = self._open_project_of_kit(ctx, req_uuid(p, "kit_id"))
        # Права проверяются ДО раскрытия подробностей о состоянии проекта (не подсказываем посторонним).
        auth = self.policy.check(ctx.conn, ctx.user_id, "task.create", ctx.now, project_id=pr["id"])
        self._project_must_be_active(pr)
        tid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, tasks, tid)
        row = {"id": tid, "project_id": pr["id"], "kit_id": kit["id"], "title": req_str(p, "title", 200),
               "description": opt_str(p, "description", 8000),
               "assignee_user_id": self._assignee(ctx, pr["id"], p.get("assignee_user_id")),
               "due_date": opt_date(p, "due_date"),
               "document_id": self._task_document(ctx, pr["id"], p.get("document_id")),
               "status": self.wf["task"]["initial"], "status_reason": None, "version": 1,
               "created_by": ctx.user_id, "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(tasks.insert().values(**row))
        ctx.log("task", tid, auth, None, 1, {"project_id": pr["id"], "kit_id": kit["id"], "title": row["title"],
                                             "assignee_user_id": row["assignee_user_id"]})
        return {"task": self.v_task(self._get(ctx.conn, tasks, tid))}

    def _task_update(self, ctx, p, ev):
        """Правка полей задачи. Отсутствующий ключ — «не менять», null — «очистить» (для необязательных)."""
        only_keys(p, {"id", "title", "description", "assignee_user_id", "due_date", "document_id"})
        o = self._require(ctx.conn, tasks, req_uuid(p, "id"), "Задача")
        auth = self.policy.check(ctx.conn, ctx.user_id, "task.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_task)
        if o["status"] in ("done", "cancelled"):
            raise Rejected("task_closed", "Задача закрыта. Чтобы изменить её, сначала откройте задачу повторно.")
        ch: dict[str, Any] = {}
        if "title" in p:
            ch["title"] = req_str(p, "title", 200)
        if "description" in p:
            ch["description"] = opt_str(p, "description", 8000)
        if "assignee_user_id" in p:
            ch["assignee_user_id"] = self._assignee(ctx, o["project_id"], p["assignee_user_id"])
        if "due_date" in p:
            ch["due_date"] = opt_date(p, "due_date")
        if "document_id" in p:
            ch["document_id"] = self._task_document(ctx, o["project_id"], p["document_id"])
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, tasks, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("task", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"task": self.v_task(n)}

    def _task_transition(self, ctx, p, ev):
        """Смена статуса задачи.

        Два пути полномочий:
        1) ГИП/РП проекта (task.transition) — любые переходы из workflows.task.transitions;
        2) исполнитель задачи (task.transition_own) — только «свои» переходы own_transitions
           (взять в работу, выполнить, вернуть в открытые). Отмена и повторное открытие — не его решение;
        3) замещающий исполнителя в этом проекте (D-35) — те же «свои» переходы за исполнителя; в журнале основание
           «роль в проекте по замещению» (видно, за кого).
        """
        only_keys(p, {"id", "to", "reason"})
        o = self._require(ctx.conn, tasks, req_uuid(p, "id"), "Задача")
        wf = self.wf["task"]
        try:
            auth = self.policy.check(ctx.conn, ctx.user_id, "task.transition", ctx.now, project_id=o["project_id"])
            allowed = wf["transitions"].get(o["status"], [])
        except Denied:
            if o["assignee_user_id"] != ctx.user_id:
                # Замещающий исполнителя (D-35): замещение именно в этом проекте и именно за исполнителя задачи.
                sub = [d for d in active_delegated_project(ctx.conn, ctx.user_id, o["project_id"], ctx.now)
                       if d[3] == o["assignee_user_id"]
                       and d[1] in self.policy.roles_for("task.transition_own", "project_role")]
                if not sub or o["assignee_user_id"] is None:
                    raise
                aid, role, did, holder = sub[0]
                auth = Authority("delegated_project_role", role, aid, did, holder)
            else:
                # Исполнитель должен по-прежнему состоять в проекте (назначение могли отозвать).
                auth = self.policy.check(ctx.conn, ctx.user_id, "task.transition_own", ctx.now,
                                         project_id=o["project_id"])
            allowed = wf["own_transitions"].get(o["status"], [])
            to = p.get("to")
            if to not in allowed and to in wf["transitions"].get(o["status"], []):
                raise Denied("own_transition_not_allowed",
                             "Этот переход выполняет ГИП или РП проекта (исполнитель может взять задачу в работу, "
                             "выполнить её или вернуть в открытые).", {"from": o["status"], "to": to})
        self._check_version(o, ev, self.v_task)
        to = p.get("to")
        if to not in allowed:
            raise Rejected("transition_not_allowed",
                           f"Переход «{o['status']}» → «{to}» не разрешён. Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        is_reopen = o["status"] == "done" and to == "open"
        if (to in wf["reason_required_for"] or (is_reopen and "reopen" in wf["reason_required_for"])) and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину.", {"field": "reason"})
        n = self._update_versioned(ctx, tasks, o, {"status": to, "status_reason": reason,
                                                    "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("task", o["id"], auth, o["version"], n["version"], {"from": o["status"], "to": to, "reason": reason})
        return {"task": self.v_task(n)}

    # ------------------------------------------------------------ документы
    def _document_code_free(self, ctx, project_id: str, code: str) -> None:
        """Обозначение документа уникально в пределах проекта — иначе отказ document_code_taken."""
        if ctx.conn.execute(sa.select(documents.c.id).where(documents.c.project_id == project_id,
                                                            documents.c.code == code)).first():
            raise Rejected("document_code_taken", "Документ с таким обозначением уже есть в этом проекте.", {"field": "code"})

    def _document_create(self, ctx, p, ev):
        """document.create {id, kit_id, code, title} — карточка документа в комплекте (пока без файла: версии
        добавляются document.add_version). Обозначение уникально в проекте. Можно офлайн (очередь).
        """
        only_keys(p, {"id", "kit_id", "code", "title"})
        kit, pr = self._open_project_of_kit(ctx, req_uuid(p, "kit_id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "document.create", ctx.now, project_id=pr["id"])
        self._project_must_be_active(pr)
        did = req_uuid(p, "id")
        self._ensure_new(ctx.conn, documents, did)
        code = req_str(p, "code", 100)
        self._document_code_free(ctx, pr["id"], code)
        title = req_str(p, "title", 300)
        ctx.conn.execute(documents.insert().values(
            id=did, project_id=pr["id"], kit_id=kit["id"], code=code, title=title, current_version_no=0, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("document", did, auth, None, 1, {"project_id": pr["id"], "kit_id": kit["id"], "code": code})
        return {"document": self.v_document(self._get(ctx.conn, documents, did))}

    def _document_update(self, ctx, p, ev):
        """Правка карточки (обозначение, название). Файлы этой командой не меняются — только add_version."""
        only_keys(p, {"id", "code", "title"})
        o = self._require(ctx.conn, documents, req_uuid(p, "id"), "Документ")
        auth = self.policy.check(ctx.conn, ctx.user_id, "document.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_document)
        ch: dict[str, Any] = {}
        if "code" in p:
            ch["code"] = req_str(p, "code", 100)
            if ch["code"] != o["code"]:
                self._document_code_free(ctx, o["project_id"], ch["code"])
        if "title" in p:
            ch["title"] = req_str(p, "title", 300)
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, documents, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("document", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"document": self.v_document(n)}

    def _document_add_version(self, ctx, p, ev):
        """Новая неизменяемая версия документа с ранее загруженным файлом.

        expected_version — версия КАРТОЧКИ документа: если коллега успел добавить свою версию, пользователь
        получит конфликт и сам решит, добавлять ли свой файл поверх (никакого молчаливого «последний победил»).
        """
        only_keys(p, {"id", "sha256", "file_name", "comment"})
        o = self._require(ctx.conn, documents, req_uuid(p, "id"), "Документ")
        auth = self.policy.check(ctx.conn, ctx.user_id, "document.add_version", ctx.now, project_id=o["project_id"])
        self._project_must_be_active(self._get(ctx.conn, projects, o["project_id"]))
        self._check_version(o, ev, self.v_document)
        sha = _check_sha256(p.get("sha256"))
        name = _check_file_name(p)
        blob = self._get(ctx.conn, file_blobs, sha, "sha256")
        receipt = ctx.conn.execute(sa.select(file_uploads.c.id).where(
            file_uploads.c.sha256 == sha, file_uploads.c.user_id == ctx.user_id,
            file_uploads.c.project_id == o["project_id"])).first()
        if blob is None or receipt is None:
            # Одинаковый ответ для «файла нет» и «файл есть, но загружен не вами» — не раскрываем,
            # существует ли в системе файл с таким хэшем.
            raise Rejected("file_not_uploaded", "Сначала загрузите файл (загрузка выполняется онлайн), затем добавьте версию.",
                           {"field": "sha256"})
        no = o["current_version_no"] + 1
        ctx.conn.execute(document_versions.insert().values(
            document_id=o["id"], version_no=no, file_name=name, sha256=sha, size_bytes=blob["size_bytes"],
            comment=opt_str(p, "comment", 2000), created_by=ctx.user_id, created_at=ctx.now))
        n = self._update_versioned(ctx, documents, o, {"current_version_no": no, "updated_by": ctx.user_id,
                                                        "updated_at": ctx.now})
        ver = dict(ctx.conn.execute(sa.select(document_versions).where(
            document_versions.c.document_id == o["id"], document_versions.c.version_no == no)).one()._mapping)
        ctx.log("document", o["id"], auth, o["version"], n["version"],
                {"version_no": no, "file_name": name, "sha256": sha, "size_bytes": blob["size_bytes"]})
        return {"document": self.v_document(n), "document_version": self.v_document_version(ver)}

    # ------------------------------------------------------------ файлы (вне конвейера команд)
    def _authority_for(self, conn, user_id: str, action: str, now, project_id: str) -> Optional[str]:
        """Основание доступа строкой для аудита («project_role:participant#assignment:7») или None, если права нет.

        Файловые маршруты идут мимо общего конвейера команд, поэтому основание вычисляем здесь сами:
        в журнале должно быть видно не только КТО загрузил/скачал файл, но и ПО КАКОМУ праву.
        """
        try:
            return self.policy.check(conn, user_id, action, now, project_id=project_id).describe()
        except Denied:
            return None

    # ------------------------------------------------------------ файлы: потоковая передача
    # Память не зависит от размера файла (issue #8): содержимое всегда идёт через файловые объекты
    # блоками по FILE_CHUNK. HTTP-слой сначала сохраняет входящий поток во временный файл (с контролем
    # предела размера), а при скачивании — собирает проверенный файл во временный файл и только потом
    # отдаёт его. Источник и приёмник должны поддерживать seek: транзакцию при конфликте сериализации
    # повторяют целиком (Service._tx), и каждая попытка начинает чтение/запись с начала.

    def _file_limit_reply(self) -> dict:
        """Стандартный отказ «файл больше допустимого размера» с текущим пределом из политики сервера."""
        return {"status": "rejected", "code": "file_too_large",
                "message_ru": f"Файл больше допустимого размера ({self.sec.max_file_mb} МБ)."}

    def precheck_upload(self, token: Optional[str], project_id: object, declared_size: Optional[int],
                        client_addr: Optional[str] = None) -> Optional[dict]:
        """Быстрая проверка ДО приёма тела загрузки: вход, право в проекте, заявленный размер.

        Зачем: не принимать гигабайты от постороннего или сверх предела. Возвращает ответ-отказ или None,
        если можно принимать тело. Это лишь предварительная проверка — окончательная (в той же транзакции,
        что и запись) выполняется в put_file_stream. Отказ по правам записывается в аудит здесь.
        """
        if declared_size is not None and declared_size > self.sec.max_file_mb * 1024 * 1024:
            return self._file_limit_reply()
        try:
            pid = req_uuid({"project_id": project_id}, "project_id")
        except Rejected as e:
            return {"status": "rejected", "code": e.code, "message_ru": e.message_ru}

        def tx(conn):
            """Транзакция предварительной проверки: сеанс и право добавлять версии в документы проекта; отказ — в
            журнал.
            """
            now = self.now()
            user, device, _ = self._auth(conn, token, now)
            authority = self._authority_for(conn, user["id"], "document.add_version", now, pid) \
                if self._get(conn, projects, pid) is not None else None
            if authority is None:
                self._audit(conn, now, user["id"], device, client_addr, None, "file.upload", "project", pid, None, None, None,
                            "denied", {"stage": "precheck", "declared_size": declared_size})
                return {"status": "denied", "code": "no_authority",
                        "message_ru": "Нет права добавлять файлы в документы этого проекта."}
            return None
        return self._guarded_tx(tx)

    def put_file_stream(self, token: Optional[str], project_id: object, source, claimed_sha256: object,
                        client_addr: Optional[str] = None) -> dict:
        """Загрузка содержимого файла из файлового объекта source (двоичный, с seek). Создаёт квитанцию.

        * Нужно право document.add_version в указанном проекте — иначе посторонний мог бы заполнять БД.
        * claimed_sha256 клиент считает ДО отправки; сервер пересчитывает. Несовпадение = повреждение в пути
          (или файл изменился у пользователя во время загрузки).
        * Если такое содержимое уже есть (дедупликация по SHA-256), байты повторно не записываются, но
          квитанция выдаётся: пользователь доказал, что у него есть файл, — он прислал его целиком.
        * Операция идемпотентна по содержимому: её безопасно повторять после обрыва связи.
        """
        limit = self.sec.max_file_mb * 1024 * 1024
        try:
            claimed = _check_sha256(claimed_sha256)
            pid = req_uuid({"project_id": project_id}, "project_id")
        except Rejected as e:
            return {"status": "rejected", "code": e.code, "message_ru": e.message_ru}
        # Проход 1 — хэш и размер (вне транзакции: чтение диска не держит блокировки БД).
        h, size = hashlib.sha256(), 0
        source.seek(0)
        while True:
            block = source.read(FILE_CHUNK)
            if not block:
                break
            size += len(block)
            if size > limit:
                return self._file_limit_reply()
            h.update(block)
        actual = h.hexdigest()
        if actual != claimed:
            return {"status": "rejected", "code": "hash_mismatch",
                    "message_ru": "Файл повреждён при передаче или изменился во время загрузки (контрольная сумма "
                                  "не совпала). Повторите загрузку."}
        chunk_count = max(1, -(-size // FILE_CHUNK))   # пустой файл хранится одним пустым блоком

        def tx(conn):
            """Транзакция записи файла: повторная проверка права, запись блоков (с дедупликацией по SHA-256), квитанция
            загрузки, журнал.
            """
            now = self.now()
            user, device, _ = self._auth(conn, token, now)
            authority = self._authority_for(conn, user["id"], "document.add_version", now, pid) \
                if self._get(conn, projects, pid) is not None else None
            if authority is None:
                self._audit(conn, now, user["id"], device, client_addr, None, "file.upload", "project", pid, None, None, None,
                            "denied", {"sha256": actual, "size_bytes": size})
                return {"status": "denied", "code": "no_authority",
                        "message_ru": "Нет права добавлять файлы в документы этого проекта."}
            dedup = self._get(conn, file_blobs, actual, "sha256") is not None
            if not dedup:
                conn.execute(file_blobs.insert().values(sha256=actual, size_bytes=size, chunk_count=chunk_count,
                                                        uploaded_by=user["id"], uploaded_at=now))
                # Проход 2 — запись блоков по одному: в памяти не больше одного блока.
                # Каждый блок перепроверяется общим хэшем: если файл подменили на диске между проходами,
                # итоговый хэш не совпадёт и транзакция будет отменена (ничего не запишется).
                source.seek(0)
                h2 = hashlib.sha256()
                for idx in range(chunk_count):
                    block = source.read(FILE_CHUNK)
                    h2.update(block)
                    conn.execute(file_chunks.insert().values(sha256=actual, idx=idx, data=block))
                if h2.hexdigest() != actual or source.read(1):
                    raise _SourceChanged()
            conn.execute(file_uploads.insert().values(sha256=actual, user_id=user["id"], project_id=pid, uploaded_at=now))
            self._audit(conn, now, user["id"], device, client_addr, None, "file.upload", "project", pid, authority, None, None,
                        "accepted", {"sha256": actual, "size_bytes": size, "deduplicated": dedup})
            return {"status": "accepted", "sha256": actual, "size_bytes": size, "deduplicated": dedup}
        try:
            return self._guarded_tx(tx)
        except _SourceChanged:
            return {"status": "rejected", "code": "hash_mismatch",
                    "message_ru": "Файл изменился во время загрузки. Повторите загрузку."}

    def put_file(self, token: Optional[str], project_id: object, data: bytes, claimed_sha256: object,
                 client_addr: Optional[str] = None) -> dict:
        """То же, что put_file_stream, для содержимого в памяти (тесты, небольшие файлы)."""
        if not isinstance(data, (bytes, bytearray)):
            return {"status": "rejected", "code": "bad_file", "message_ru": "Нет содержимого файла."}
        if len(data) > self.sec.max_file_mb * 1024 * 1024:
            return self._file_limit_reply()
        return self.put_file_stream(token, project_id, io.BytesIO(bytes(data)), claimed_sha256, client_addr)

    def get_file_to(self, token: Optional[str], document_id: object, version_no: object, dest,
                    client_addr: Optional[str] = None) -> dict:
        """Записать файл версии в dest (двоичный файловый объект с seek/truncate) и проверить его.

        Права — document.read в проекте документа. Блоки читаются из БД потоком и пишутся в dest с
        пересчётом SHA-256; при расхождении ответ — file_corrupted, и вызывающий ОБЯЗАН выбросить dest
        (HTTP-слой так и делает: клиенту ничего не отдаётся). Событие пишется в аудит в обоих случаях —
        администратор восстанавливает повреждённый файл из резервной копии.
        """
        try:
            did = req_uuid({"document_id": document_id}, "document_id")
            if isinstance(version_no, bool) or not isinstance(version_no, int) or version_no < 1:
                raise Rejected("bad_version_no", "Номер версии — целое число от 1.")
        except Rejected as e:
            return {"status": "rejected", "code": e.code, "message_ru": e.message_ru}

        def tx(conn):
            """Транзакция скачивания: право чтения документа, сборка файла из блоков с проверкой SHA-256 и размера,
            журнал.
            """
            now = self.now()
            user, device, _ = self._auth(conn, token, now)
            d = self._get(conn, documents, did)
            authority = self._authority_for(conn, user["id"], "document.read", now, d["project_id"]) if d is not None else None
            if authority is None:
                # Не различаем «нет документа» и «нет доступа» — не раскрываем существование.
                self._audit(conn, now, user["id"], device, client_addr, None, "file.download", "document", did, None,
                            None, None, "denied", {"version_no": version_no})
                return {"status": "denied", "code": "no_authority", "message_ru": "Документ не найден или нет доступа."}
            v = conn.execute(sa.select(document_versions).where(document_versions.c.document_id == did,
                                                               document_versions.c.version_no == version_no)).first()
            if v is None:
                return {"status": "rejected", "code": "version_not_found", "message_ru": "Такой версии документа нет."}
            v = dict(v._mapping)
            dest.seek(0)
            dest.truncate()
            h, size = hashlib.sha256(), 0
            q = (sa.select(file_chunks.c.data).where(file_chunks.c.sha256 == v["sha256"])
                 .order_by(file_chunks.c.idx).execution_options(yield_per=4))
            for row in conn.execute(q):
                b = bytes(row.data)
                h.update(b)
                size += len(b)
                dest.write(b)
            dest.flush()
            ok = h.hexdigest() == v["sha256"] and size == v["size_bytes"]
            self._audit(conn, now, user["id"], device, client_addr, None, "file.download", "document", did, authority, None, None,
                        "accepted" if ok else "error", {"version_no": version_no, "sha256": v["sha256"], "intact": ok})
            if not ok:
                return {"status": "error", "code": "file_corrupted",
                        "message_ru": "Файл на сервере повреждён (контрольная сумма не совпала) и не будет выдан. "
                                      "Сообщите администратору: нужно восстановление из резервной копии."}
            return {"status": "accepted", "file_name": v["file_name"], "sha256": v["sha256"], "size_bytes": v["size_bytes"]}
        return self._guarded_tx(tx)

    def get_file(self, token: Optional[str], document_id: object, version_no: object,
                 client_addr: Optional[str] = None) -> tuple[dict, Optional[bytes]]:
        """То же, что get_file_to, с результатом в памяти: (ответ, байты или None). Для тестов и малых файлов."""
        buf = io.BytesIO()
        resp = self.get_file_to(token, document_id, version_no, buf, client_addr)
        return resp, (buf.getvalue() if resp.get("status") == "accepted" else None)

    def _guarded_tx(self, fn, wrap=lambda resp: resp):
        """Транзакция для операций вне конвейера команд (файлы).

        Ошибка входа (сеанс истёк, отозван, нужна смена пароля) превращается в обычный ответ
        с кодом, а не в исключение; `wrap` приводит его к форме ответа операции
        (по умолчанию — сам ответ).
        """
        from vector.core.errors import Unauthenticated

        def wrapped(conn):
            """Выполнить fn; ошибку входа вернуть обычным ответом, а не исключением."""
            try:
                return fn(conn)
            except Unauthenticated as e:
                return wrap({"status": e.status, "code": e.code, "message_ru": e.message_ru})
        return self._tx(wrapped)

    # ------------------------------------------------------------ данные для pull
    def _pull_work(self, conn, project_ids: list[str]) -> dict:
        """Задачи, документы и метаданные версий для проектов, которые пользователь уже вправе читать."""
        if not project_ids:
            return {"tasks": [], "documents": [], "document_versions": []}
        t = [self.v_task(dict(r._mapping)) for r in conn.execute(
            sa.select(tasks).where(tasks.c.project_id.in_(project_ids)).order_by(tasks.c.created_at, tasks.c.id))]
        d_rows = [dict(r._mapping) for r in conn.execute(
            sa.select(documents).where(documents.c.project_id.in_(project_ids)).order_by(documents.c.code))]
        ids = [d["id"] for d in d_rows]
        v = [self.v_document_version(dict(r._mapping)) for r in conn.execute(
            sa.select(document_versions).where(document_versions.c.document_id.in_(ids))
            .order_by(document_versions.c.document_id, document_versions.c.version_no))] if ids else []
        return {"tasks": t, "documents": [self.v_document(d) for d in d_rows], "document_versions": v}
