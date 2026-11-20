"""Бизнес-ревизии документов РКД: проверка РП, замечания/ответы, трансмитталы (этап 3 blueprint 52, D-56 п. 2).

Слой поверх карточек документов (documents) и неизменяемых версий файлов (document_versions): ревизия имеет
внешний шифр («REV A», «01»), проходит внутреннюю проверку, отправляется заказчику только неизменяемым пакетом
(трансмитталом), получает замечания с обязательным основанием и закрывается согласованием заказчика — только по
его документу. Правила переходов — vector.core.rkd (источник — контракт workflows, раздел docrev); права —
contracts/permissions (docrev.*, comment.*, transmittal.*).

Все записи решений проверки, ответов на замечания и состава трансмитталов неизменяемы (триггеры БД), каждая
веха дополнительно попадает в журнал событий проекта (project_events, D-56).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import rkd as rk
from vector.core.errors import Rejected

from .db import (document_comment_responses, document_comments, document_reviews, document_revisions,
                 document_transmittals, document_versions, documents, file_blobs, file_uploads, projects,
                 transmittal_items)
from .validation import iso, only_keys, opt_str, req_str, req_uuid
from .work import _check_sha256


class RkdMixin:
    """Обработчики команд и pull-срез ревизий РКД. Подключается в Service (базы + self.handlers)."""

    # ------------------------------------------------------------ представления для клиента
    def v_revision(self, o: dict) -> dict:
        wf = rk.revision_workflow(self.wf)
        return {"id": o["id"], "document_id": o["document_id"], "seq": o["seq"], "label": o["label"],
                "status": o["status"], "status_label": wf["labels_ru"][o["status"]],
                "doc_version_id": o["doc_version_id"], "note": o["note"], "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_review(o: dict) -> dict:
        return {"id": o["id"], "revision_id": o["revision_id"], "cycle_no": o["cycle_no"],
                "decision": o["decision"], "decision_label": rk.REVIEW_DECISIONS[o["decision"]],
                "reason": o["reason"], "decided_by": o["decided_by"], "authority": o["authority"],
                "at": iso(o["at"])}

    @staticmethod
    def v_comment(o: dict) -> dict:
        return {"id": o["id"], "revision_id": o["revision_id"], "seq": o["seq"], "source": o["source"],
                "source_label": rk.COMMENT_SOURCES[o["source"]], "page": o["page"], "text": o["text"],
                "external_ref": o["external_ref"], "status": o["status"], "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "closed_by": o["closed_by"], "closed_at": iso(o["closed_at"])}

    @staticmethod
    def v_response(o: dict) -> dict:
        return {"id": o["id"], "comment_id": o["comment_id"], "text": o["text"], "sha256": o["sha256"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"])}

    @staticmethod
    def v_transmittal(o: dict) -> dict:
        return {"id": o["id"], "project_id": o["project_id"], "seq": o["seq"], "recipient": o["recipient"],
                "channel": o["channel"], "channel_label": rk.TRANSMITTAL_CHANNELS[o["channel"]],
                "note": o["note"], "status": o["status"], "external_ref": o["external_ref"],
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "delivered_at": iso(o["delivered_at"])}

    # ------------------------------------------------------------ общие выборки
    def _doc_project(self, ctx, document_id: str) -> tuple[dict, dict]:
        """Карточка документа и её проект (404, если документа нет)."""
        doc = self._require(ctx.conn, documents, document_id, "Документ")
        return doc, self._get(ctx.conn, projects, doc["project_id"])

    def _revision_of(self, ctx, revision_id: str) -> tuple[dict, dict, dict]:
        """Ревизия, её документ и проект."""
        rev = self._require(ctx.conn, document_revisions, revision_id, "Ревизия")
        doc, pr = self._doc_project(ctx, rev["document_id"])
        return rev, doc, pr

    def _open_comments(self, ctx, revision_id: str) -> int:
        """Сколько замечаний ревизии ещё не закрыто ответом."""
        return ctx.conn.execute(sa.select(sa.func.count()).select_from(document_comments).where(
            document_comments.c.revision_id == revision_id, document_comments.c.status == "open")).scalar()

    # ------------------------------------------------------------ ревизии
    def _docrev_create(self, ctx, p, ev):
        """docrev.create {id, document_id, label, doc_version_id, note?} — новая бизнес-ревизия документа.

        Шифр ревизии уникален в документе и не повышается при возврате: следующая загрузка = следующая ревизия.
        Одновременно допускается одна «рабочая» ревизия (в работе или на проверке) — иначе путаница в шифрах.
        Можно офлайн (очередь): сервер при применении проверит уникальность шифра и версию файла.
        """
        only_keys(p, {"id", "document_id", "label", "doc_version_id", "note"})
        doc, pr = self._doc_project(ctx, req_uuid(p, "document_id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "docrev.create", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, document_revisions, rid)
        label = req_str(p, "label", 40)
        labels = [r.label for r in ctx.conn.execute(
            sa.select(document_revisions.c.label).where(document_revisions.c.document_id == doc["id"]))]
        if not rk.label_free(labels, label):
            raise Rejected("revision_label_taken", "Ревизия с таким шифром уже есть у этого документа.",
                           {"field": "label"})
        busy = ctx.conn.execute(sa.select(document_revisions.c.id).where(
            document_revisions.c.document_id == doc["id"],
            document_revisions.c.status.in_(("in_work", "ready_for_rp")))).first()
        if busy:
            raise Rejected("revision_in_progress",
                           "Есть ревизия в работе или на проверке — завершите её, прежде чем создавать новую.")
        ver_id = p.get("doc_version_id")
        ver = ctx.conn.execute(sa.select(document_versions).where(
            document_versions.c.id == ver_id, document_versions.c.document_id == doc["id"])).first() \
            if isinstance(ver_id, int) else None
        if ver is None:
            raise Rejected("version_not_found", "Версия файла не найдена в этом документе.",
                           {"field": "doc_version_id"})
        seq = ctx.conn.execute(sa.select(sa.func.max(document_revisions.c.seq)).where(
            document_revisions.c.document_id == doc["id"])).scalar() or 0
        ctx.conn.execute(document_revisions.insert().values(
            id=rid, document_id=doc["id"], seq=seq + 1, label=label, status=rk.revision_workflow(self.wf)["initial"],
            doc_version_id=ver_id, note=opt_str(p, "note", 2000), version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("document_revision", rid, auth, None, 1,
                {"document_id": doc["id"], "seq": seq + 1, "label": label, "doc_version_id": ver_id})
        self._project_event(ctx, pr["id"], "revision_created",
                            f"Документ {doc['code']}: создана ревизия «{label}»", auth,
                            object_type="document_revision", object_id=rid,
                            details={"document": doc["code"], "label": label})
        return {"revision": self.v_revision(self._get(ctx.conn, document_revisions, rid))}

    def _docrev_transition(self, ctx, p, ev):
        """docrev.transition {id, to, reason?} — переход ревизии по контракту docrev.

        Развилка прав: подача на проверку и взятие в работу — авторская сторона (docrev.transition); решения
        проверки (возврат, разрешение/отзыв отправки, регистрация согласования заказчика) — РП проекта
        (docrev.review, D-56 п. 1). Переходы в sent/customer_comments — только системными командами
        (transmittal.create, comment.add): здесь они запрещены.
        """
        only_keys(p, {"id", "to", "reason"})
        rev, doc, pr = self._revision_of(ctx, req_uuid(p, "id"))
        wf = rk.revision_workflow(self.wf)
        to = req_str(p, "to", 24)
        if not rk.can_transition(self.wf, rev["status"], to):
            raise Rejected("transition_not_allowed",
                           f"Переход «{wf['labels_ru'][rev['status']]}» → «{wf['labels_ru'].get(to, to)}» "
                           "не предусмотрен контрактом.", {"from": rev["status"], "to": to})
        if to in rk.SYSTEM_ONLY_TARGETS:
            cmd = "transmittal.create" if to == "sent" else "comment.add"
            raise Rejected("use_system_command",
                           f"Этот статус устанавливается командой {cmd}, а не прямым переходом.", {"to": to})
        reason = opt_str(p, "reason", 2000)
        if rk.needs_reason(self.wf, rev["status"], to) and not reason:
            raise Rejected("reason_required", "Укажите причину (основание).", {"field": "reason"})
        reviewer = to in rk.REVIEW_TARGETS or (rev["status"], to) == ("ready_to_transmit", "in_work")
        action = "docrev.review" if reviewer else "docrev.transition"
        auth = self.policy.check(ctx.conn, ctx.user_id, action, ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        self._check_version(rev, ev, self.v_revision)
        if to in ("revision_in_work", "approved") and self._open_comments(ctx, rev["id"]):
            raise Rejected("comments_open", "Сначала закройте все замечания ревизии ответом или доказательством.")
        n = self._update_versioned(ctx, document_revisions, rev, {"status": to, "updated_by": ctx.user_id,
                                                                  "updated_at": ctx.now})
        decision = rk.review_decision_for(to)
        if decision is not None:
            cycle = ctx.conn.execute(sa.select(sa.func.max(document_reviews.c.cycle_no)).where(
                document_reviews.c.revision_id == rev["id"])).scalar() or 0
            ctx.conn.execute(document_reviews.insert().values(
                revision_id=rev["id"], cycle_no=cycle + 1, decision=decision, reason=reason,
                decided_by=ctx.user_id, authority=auth.describe(), at=ctx.now))
        ctx.log("document_revision", rev["id"], auth, rev["version"], n["version"],
                {"from": rev["status"], "to": to, "reason": reason})
        from_label, to_label = wf["labels_ru"][rev["status"]], wf["labels_ru"][to]
        self._project_event(ctx, pr["id"], "revision_status",
                            f"Документ {doc['code']}, ревизия «{rev['label']}»: «{from_label}» → «{to_label}»", auth,
                            object_type="document_revision", object_id=rev["id"],
                            details={"document": doc["code"], "label": rev["label"], "from": rev["status"],
                                     "to": to, "reason": reason})
        # Уведомления: автор ревизии — о решениях по ней; РП проекта — о подаче на проверку.
        title = f"{doc['code']} рев.{rev['label']}: {to_label}"
        body = f"Причина: {reason[:300]}" if reason else None
        if to in ("returned_to_engineer", "ready_to_transmit", "approved"):
            self._notify(ctx, [rev["created_by"]], "docrev_review", title, body,
                         object_type="document_revision", object_id=rev["id"], include_actor=False)
        elif to == "ready_for_rp":
            self._notify(ctx, self._project_role_users(ctx.conn, pr["id"], "rp", ctx.now), "docrev_submitted",
                         title, None, object_type="document_revision", object_id=rev["id"])
        return {"revision": self.v_revision(n)}

    # ------------------------------------------------------------ замечания и ответы
    def _comment_add(self, ctx, p, ev):
        """comment.add {id, revision_id, source, text, page?, external_ref?} — замечание к точной ревизии.

        internal — в ходе внутренней проверки (ревизия на проверке или возвращена); customer — только к
        отправленной ревизии и с обязательным основанием (external_ref — номер письма заказчика); регистрация
        замечания заказчика переводит ревизию «Отправлена» → «Получены замечания заказчика».
        """
        only_keys(p, {"id", "revision_id", "source", "text", "page", "external_ref"})
        rev, doc, pr = self._revision_of(ctx, req_uuid(p, "revision_id"))
        source = req_str(p, "source", 16)
        if source not in rk.COMMENT_SOURCES:
            raise Rejected("bad_value", "Источник замечания — internal или customer.", {"field": "source"})
        if source == "customer":
            auth = self.policy.check(ctx.conn, ctx.user_id, "comment.register_customer", ctx.now,
                                     project_id=pr["id"])
            if rev["status"] not in ("sent", "customer_comments"):
                raise Rejected("revision_not_sent",
                               "Замечание заказчика регистрируется только на отправленную ревизию.")
            ref = req_str(p, "external_ref", 200)
        else:
            auth = self.policy.check(ctx.conn, ctx.user_id, "comment.add", ctx.now, project_id=pr["id"])
            if rev["status"] not in ("ready_for_rp", "returned_to_engineer"):
                raise Rejected("revision_not_in_review",
                               "Внутреннее замечание добавляется в ходе проверки (ревизия на проверке или "
                               "возвращена на доработку).")
            if p.get("external_ref"):
                raise Rejected("bad_value", "Основание (письмо) указывается только для замечания заказчика.",
                               {"field": "external_ref"})
            ref = None
        self._project_editable(pr)
        cid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, document_comments, cid)
        text = req_str(p, "text", 2000)
        seq = ctx.conn.execute(sa.select(sa.func.max(document_comments.c.seq)).where(
            document_comments.c.revision_id == rev["id"])).scalar() or 0
        ctx.conn.execute(document_comments.insert().values(
            id=cid, revision_id=rev["id"], seq=seq + 1, source=source, page=opt_str(p, "page", 40), text=text,
            external_ref=ref, status="open", version=1, created_by=ctx.user_id, created_at=ctx.now))
        out: dict[str, Any] = {"comment": self.v_comment(self._get(ctx.conn, document_comments, cid))}
        if source == "customer" and rev["status"] == "sent":
            n = self._update_versioned(ctx, document_revisions, rev, {"status": "customer_comments",
                                                                      "updated_by": ctx.user_id,
                                                                      "updated_at": ctx.now})
            out["revision"] = self.v_revision(n)
            self._project_event(ctx, pr["id"], "revision_status",
                                f"Документ {doc['code']}, ревизия «{rev['label']}»: получены замечания заказчика "
                                f"({ref})", auth, object_type="document_revision", object_id=rev["id"],
                                details={"document": doc["code"], "label": rev["label"], "from": "sent",
                                         "to": "customer_comments", "reason": ref})
        ctx.log("document_comment", cid, auth, None, 1,
                {"revision_id": rev["id"], "source": source, "external_ref": ref})
        self._notify(ctx, [rev["created_by"]], "comment_added",
                     f"{doc['code']} рев.{rev['label']}: замечание ({rk.COMMENT_SOURCES[source].lower()})",
                     text[:300], object_type="document_revision", object_id=rev["id"])
        return out

    def _comment_respond(self, ctx, p, ev):
        """comment.respond {id, text, sha256?} — ответ на замечание закрывает его (id — id замечания).

        Доказательство (sha256) — файл, загруженный отвечающим в этот проект (как document.add_version).
        Ответ неизменяем: исправление — новым ответом на переоткрытое… нет, переоткрытия нет: замечание
        закрывается один раз, спор решается новым замечанием к следующей ревизии.
        """
        only_keys(p, {"id", "text", "sha256"})
        c = self._require(ctx.conn, document_comments, req_uuid(p, "id"), "Замечание")
        rev, doc, pr = self._revision_of(ctx, c["revision_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "comment.respond", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        if c["status"] != "open":
            raise Rejected("comment_closed", "Замечание уже закрыто ответом.")
        self._check_version(c, ev, self.v_comment)
        sha = None
        if p.get("sha256") is not None:
            sha = _check_sha256(p["sha256"])
            receipt = ctx.conn.execute(sa.select(file_uploads.c.id).where(
                file_uploads.c.sha256 == sha, file_uploads.c.user_id == ctx.user_id,
                file_uploads.c.project_id == pr["id"])).first()
            if self._get(ctx.conn, file_blobs, sha, "sha256") is None or receipt is None:
                raise Rejected("file_not_uploaded",
                               "Сначала загрузите файл-доказательство (загрузка выполняется онлайн).",
                               {"field": "sha256"})
        ctx.conn.execute(document_comment_responses.insert().values(
            comment_id=c["id"], text=req_str(p, "text", 2000), sha256=sha, created_by=ctx.user_id,
            created_at=ctx.now))
        n = self._update_versioned(ctx, document_comments, c, {"status": "closed", "closed_by": ctx.user_id,
                                                               "closed_at": ctx.now})
        ctx.log("document_comment", c["id"], auth, c["version"], n["version"], {"status": "closed"})
        self._notify(ctx, [c["created_by"]], "comment_added",
                     f"{doc['code']} рев.{rev['label']}: дан ответ на замечание", None,
                     object_type="document_revision", object_id=rev["id"])
        return {"comment": self.v_comment(n)}

    # ------------------------------------------------------------ трансмитталы
    def _transmittal_create(self, ctx, p, ev):
        """transmittal.create {id, project_id, recipient, channel, note?, items: [revision_id, …]} — пакет выдачи.

        Атомарно: каждая ревизия пакета должна быть «Разрешена к отправке»; при создании все они переходят в
        «Отправлена заказчику». Ручная отправка регистрируется тем же трансмитталом; подтверждение доставки —
        отдельной командой (transmittal.register_receipt).
        """
        only_keys(p, {"id", "project_id", "recipient", "channel", "note", "items"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "transmittal.create", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        tid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, document_transmittals, tid)
        recipient = req_str(p, "recipient", 300)
        channel = req_str(p, "channel", 16)
        if channel not in rk.TRANSMITTAL_CHANNELS:
            raise Rejected("bad_value", "Неизвестный канал отправки.", {"field": "channel"})
        items = p.get("items")
        if not isinstance(items, list) or not items:
            raise Rejected("field_required", "Укажите состав пакета (items).", {"field": "items"})
        rev_ids = [req_uuid({"items": x}, "items") for x in items]
        if len(set(rev_ids)) != len(rev_ids):
            raise Rejected("bad_value", "Ревизия не может входить в пакет дважды.", {"field": "items"})
        revs = []
        for rid in rev_ids:
            rev, doc, _pr = self._revision_of(ctx, rid)
            if _pr["id"] != pr["id"]:
                raise Rejected("bad_value", "В пакете — только ревизии одного проекта.", {"field": "items"})
            if rev["status"] != "ready_to_transmit":
                raise Rejected("revision_not_ready",
                               f"Документ {doc['code']} рев.{rev['label']}: ревизия не разрешена к отправке "
                               f"(статус «{rk.revision_workflow(self.wf)['labels_ru'][rev['status']]}»).",
                               {"revision_id": rid})
            revs.append((rev, doc))
        seq = ctx.conn.execute(sa.select(sa.func.max(document_transmittals.c.seq)).where(
            document_transmittals.c.project_id == pr["id"])).scalar() or 0
        ctx.conn.execute(document_transmittals.insert().values(
            id=tid, project_id=pr["id"], seq=seq + 1, recipient=recipient, channel=channel,
            note=opt_str(p, "note", 2000), status="sent", version=1,
            created_by=ctx.user_id, created_at=ctx.now))
        for pos, (rev, doc) in enumerate(revs, start=1):
            ctx.conn.execute(transmittal_items.insert().values(transmittal_id=tid, revision_id=rev["id"],
                                                               position=pos))
            self._update_versioned(ctx, document_revisions, rev, {"status": "sent", "updated_by": ctx.user_id,
                                                                  "updated_at": ctx.now})
            self._project_event(ctx, pr["id"], "revision_status",
                                f"Документ {doc['code']}, ревизия «{rev['label']}»: отправлена заказчику "
                                f"(трансмиттал № {seq + 1})", auth, object_type="document_revision",
                                object_id=rev["id"],
                                details={"document": doc["code"], "label": rev["label"],
                                         "from": "ready_to_transmit", "to": "sent", "transmittal": tid})
        ctx.log("document_transmittal", tid, auth, None, 1,
                {"project_id": pr["id"], "seq": seq + 1, "channel": channel, "items": rev_ids})
        self._notify(ctx, [rev["created_by"] for rev, _ in revs], "transmittal_sent",
                     f"Трансмиттал № {seq + 1} отправлен ({rk.TRANSMITTAL_CHANNELS[channel].lower()})",
                     f"Адресат: {recipient[:200]}", object_type="document_transmittal", object_id=tid)
        return {"transmittal": self.v_transmittal(self._get(ctx.conn, document_transmittals, tid))}

    def _transmittal_register_receipt(self, ctx, p, ev):
        """transmittal.register_receipt {id, external_ref} — подтверждение доставки (номер письма/квитанции).

        Вносится один раз: потеря квитанции не повторяет отправку — сверяется внешний номер.
        """
        only_keys(p, {"id", "external_ref"})
        t = self._require(ctx.conn, document_transmittals, req_uuid(p, "id"), "Трансмиттал")
        auth = self.policy.check(ctx.conn, ctx.user_id, "transmittal.register_receipt", ctx.now,
                                 project_id=t["project_id"])
        self._check_version(t, ev, self.v_transmittal)
        if t["status"] != "sent":
            raise Rejected("already_delivered", "Подтверждение доставки уже зарегистрировано.")
        n = self._update_versioned(ctx, document_transmittals, t,
                                   {"status": "delivered", "external_ref": req_str(p, "external_ref", 200),
                                    "delivered_at": ctx.now})
        ctx.log("document_transmittal", t["id"], auth, t["version"], n["version"],
                {"status": "delivered", "external_ref": n["external_ref"]})
        self._project_event(ctx, t["project_id"], "transmittal_delivered",
                            f"Трансмиттал № {t['seq']}: подтверждена доставка ({n['external_ref']})", auth,
                            object_type="document_transmittal", object_id=t["id"])
        return {"transmittal": self.v_transmittal(n)}

    # ------------------------------------------------------------ pull
    def _pull_rkd(self, conn, project_ids: list[str]) -> dict:
        """Ревизии, проверки, замечания/ответы и трансмитталы проектов, которые пользователь уже вправе читать."""
        empty = {"document_revisions": [], "document_reviews": [], "document_comments": [],
                 "document_comment_responses": [], "document_transmittals": [], "transmittal_items": []}
        if not project_ids:
            return empty
        doc_ids = [r.id for r in conn.execute(
            sa.select(documents.c.id).where(documents.c.project_id.in_(project_ids)))]
        if not doc_ids:
            return empty
        revs = [dict(r._mapping) for r in conn.execute(
            sa.select(document_revisions).where(document_revisions.c.document_id.in_(doc_ids))
            .order_by(document_revisions.c.document_id, document_revisions.c.seq))]
        rev_ids = [r["id"] for r in revs]
        reviews, comments, responses, t_items = [], [], [], []
        if rev_ids:
            reviews = [self.v_review(dict(r._mapping)) for r in conn.execute(
                sa.select(document_reviews).where(document_reviews.c.revision_id.in_(rev_ids))
                .order_by(document_reviews.c.revision_id, document_reviews.c.cycle_no))]
            c_rows = [dict(r._mapping) for r in conn.execute(
                sa.select(document_comments).where(document_comments.c.revision_id.in_(rev_ids))
                .order_by(document_comments.c.revision_id, document_comments.c.seq))]
            comments = [self.v_comment(c) for c in c_rows]
            c_ids = [c["id"] for c in c_rows]
            if c_ids:
                responses = [self.v_response(dict(r._mapping)) for r in conn.execute(
                    sa.select(document_comment_responses)
                    .where(document_comment_responses.c.comment_id.in_(c_ids))
                    .order_by(document_comment_responses.c.id))]
            t_items = [{"transmittal_id": r.transmittal_id, "revision_id": r.revision_id, "position": r.position}
                       for r in conn.execute(sa.select(transmittal_items)
                                             .where(transmittal_items.c.revision_id.in_(rev_ids))
                                             .order_by(transmittal_items.c.transmittal_id,
                                                       transmittal_items.c.position))]
        transmittals = [self.v_transmittal(dict(r._mapping)) for r in conn.execute(
            sa.select(document_transmittals).where(document_transmittals.c.project_id.in_(project_ids))
            .order_by(document_transmittals.c.project_id, document_transmittals.c.seq))]
        return {"document_revisions": [self.v_revision(r) for r in revs], "document_reviews": reviews,
                "document_comments": comments, "document_comment_responses": responses,
                "document_transmittals": transmittals, "transmittal_items": t_items}
