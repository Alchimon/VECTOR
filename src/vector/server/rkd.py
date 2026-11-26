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

import hashlib
import io
import json
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import rkd as rk
from vector.core.errors import Conflict, Rejected

from .db import (FILE_CHUNK, document_comment_responses, document_comments, document_profiles, document_reviews,
                 document_revision_issues, document_revisions, document_transmittals, document_versions,
                 documents, file_blobs, file_chunks, file_uploads, kits, projects, transmittal_items,
                 transmittal_packages, users)
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

    # ------------------------------------------------------------ профили шифров и статусы выдачи (D-62)
    def v_profile(self, o: dict, conn=None) -> dict:
        """Профиль схемы шифров: словарь статусов выдачи распаковывается из JSON; revisions — число ревизий,
        уже проверенных/размеченных этим профилем (нужен, если conn передан — для pull и ответов команд)."""
        statuses = json.loads(o["issue_statuses_json"] or "[]")
        out = {"id": o["id"], "project_id": o["project_id"], "kit_id": o["kit_id"], "name": o["name"],
               "label_pattern": o["label_pattern"], "label_example": o["label_example"],
               "issue_statuses": statuses, "active": bool(o["active"]), "version": o["version"],
               "created_by": o["created_by"], "created_at": iso(o["created_at"]),
               "updated_at": iso(o["updated_at"])}
        if conn is not None:
            out["revisions"] = conn.execute(sa.select(sa.func.count()).select_from(document_revision_issues)
                                            .where(document_revision_issues.c.profile_id == o["id"])).scalar()
        return out

    @staticmethod
    def v_issue(o: dict) -> dict:
        """Действующий статус выдачи ревизии (код и подпись — снимок словаря профиля на момент установки)."""
        return {"revision_id": o["revision_id"], "profile_id": o["profile_id"], "status_code": o["status_code"],
                "status_label": o["status_label"], "version": o["version"],
                "set_by": o["set_by"], "set_at": iso(o["set_at"])}

    def _profile_ctx(self, ctx, profile_id: str) -> tuple[dict, dict]:
        """Профиль и его проект (404, если профиля нет)."""
        prof = self._require(ctx.conn, document_profiles, profile_id, "Профиль шифров")
        return prof, self._require(ctx.conn, projects, prof["project_id"], "Проект")

    def _effective_profile(self, conn, project_id: str, kit_id: Optional[str]) -> Optional[dict]:
        """Действующий профиль для комплекта: активный комплектный переопределяет проектный (kit_id NULL)."""
        rows = [dict(r._mapping) for r in conn.execute(sa.select(document_profiles).where(
            document_profiles.c.project_id == project_id, document_profiles.c.active.is_(True)))]
        kit_level = [p for p in rows if p["kit_id"] == kit_id]
        project_level = [p for p in rows if p["kit_id"] is None]
        return kit_level[0] if kit_level else (project_level[0] if project_level else None)

    def _active_conflict(self, ctx, project_id: str, kit_id: Optional[str], exclude_id: Optional[str] = None) -> bool:
        """Есть ли уже активный профиль на ту же область (проект целиком или тот же комплект), кроме exclude_id."""
        cond = [document_profiles.c.project_id == project_id, document_profiles.c.active.is_(True)]
        cond.append(document_profiles.c.kit_id.is_(None) if kit_id is None
                    else document_profiles.c.kit_id == kit_id)
        if exclude_id:
            cond.append(document_profiles.c.id != exclude_id)
        return ctx.conn.execute(sa.select(document_profiles.c.id).where(*cond)).first() is not None

    def _profile_fields(self, ctx, p: dict, existing: Optional[dict] = None) -> dict:
        """Проверить и нормализовать поля профиля: шаблон компилируется, словарь статусов — правилам ядра.

        Для update (existing задан) берутся только присланные ключи; create требует name и принимает остальное.
        """
        out: dict[str, Any] = {}
        if existing is None or "name" in p:
            out["name"] = req_str(p, "name", 200) if existing is None else req_str(p, "name", 200)
        if existing is None or "label_pattern" in p:
            pattern = opt_str(p, "label_pattern", 200)
            try:
                out["label_pattern"] = rk.validate_label_pattern(pattern)
            except ValueError as e:
                raise Rejected("bad_label_pattern", str(e), {"field": "label_pattern"})
        if existing is None or "label_example" in p:
            out["label_example"] = opt_str(p, "label_example", 40)
        if existing is None or "issue_statuses" in p:
            try:
                statuses = rk.validate_issue_statuses(p.get("issue_statuses") if existing is None
                                                      else p["issue_statuses"])
            except ValueError as e:
                raise Rejected("bad_issue_statuses", str(e), {"field": "issue_statuses"})
            out["issue_statuses_json"] = json.dumps(statuses, ensure_ascii=False)
        # Пример шифра обязан соответствовать шаблону (если оба заданы) — иначе «предпросмотр» врёт.
        pattern = out.get("label_pattern", existing["label_pattern"] if existing else None)
        example = out.get("label_example", existing["label_example"] if existing else None)
        if example is not None and not rk.label_matches(pattern, example):
            raise Rejected("example_mismatch",
                           "Пример шифра не соответствует шаблону — поправьте шаблон или пример.",
                           {"field": "label_example"})
        return out

    def _docprofile_create(self, ctx, p, ev):
        """docprofile.create {id, project_id, kit_id?, name, label_pattern?, label_example?, issue_statuses?} —
        профиль схемы шифров ревизий для проекта (kit_id не задан) или конкретного комплекта.

        Одновременно активен один профиль на область: комплектный переопределяет проектный (иначе двусмысленность
        при проверке шифра). Словарь статусов выдачи задаёт заказчик (IFC/CEF и т.п.) — программа формат не
        навязывает, только проверяет непустоту/уникальность кодов.
        """
        only_keys(p, {"id", "project_id", "kit_id", "name", "label_pattern", "label_example", "issue_statuses"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "docprofile.manage", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        kit_id = p.get("kit_id")
        if kit_id is not None:
            kit_id = req_uuid(p, "kit_id")
            kit = self._get(ctx.conn, kits, kit_id)
            if kit is None or kit["project_id"] != pr["id"]:
                raise Rejected("not_found", "Комплект: объект не найден.", {"id": kit_id})
        prof_id = req_uuid(p, "id")
        self._ensure_new(ctx.conn, document_profiles, prof_id)
        if self._active_conflict(ctx, pr["id"], kit_id):
            raise Rejected("profile_active_exists",
                           "Активный профиль для этой области уже есть — отключите его или задайте другой комплект.",
                           {"field": "kit_id"})
        fields = self._profile_fields(ctx, p)
        ctx.conn.execute(document_profiles.insert().values(
            id=prof_id, project_id=pr["id"], kit_id=kit_id, active=True, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now, **fields))
        ctx.log("document_profile", prof_id, auth, None, 1,
                {"project_id": pr["id"], "kit_id": kit_id, "name": fields["name"],
                 "label_pattern": fields.get("label_pattern"),
                 "issue_statuses": json.loads(fields.get("issue_statuses_json", "[]"))})
        self._project_event(ctx, pr["id"], "docprofile_created",
                            f"Профиль шифров «{fields['name']}» создан", auth,
                            object_type="document_profile", object_id=prof_id)
        return {"profile": self.v_profile(self._get(ctx.conn, document_profiles, prof_id), ctx.conn)}

    def _docprofile_update(self, ctx, p, ev):
        """docprofile.update {id, name?, label_pattern?, label_example?, issue_statuses?} — правка профиля.

        Новые правила действуют на новые ревизии и новые установки статуса; созданные ревизии и снимки статусов
        не перепроверяются (история неизменна).
        """
        only_keys(p, {"id", "name", "label_pattern", "label_example", "issue_statuses"})
        prof, pr = self._profile_ctx(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "docprofile.manage", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        fields = self._profile_fields(ctx, p, existing=prof)
        if not fields:
            raise Rejected("nothing_to_update", "Нет изменений: передайте хотя бы одно поле профиля.")
        self._check_version(prof, ev, lambda o: self.v_profile(o, ctx.conn))
        n = self._update_versioned(ctx, document_profiles, prof,
                                   {**fields, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("document_profile", prof["id"], auth, prof["version"], n["version"], fields)
        self._project_event(ctx, pr["id"], "docprofile_updated",
                            f"Профиль шифров «{n['name']}» обновлён", auth,
                            object_type="document_profile", object_id=prof["id"])
        return {"profile": self.v_profile(n, ctx.conn)}

    def _docprofile_activate(self, ctx, p, ev):
        """docprofile.activate {id, active} — включить/отключить профиль. Включение отклоняется, если на ту же
        область уже есть активный профиль; отключение делает область «без профиля» (шифры не проверяются)."""
        only_keys(p, {"id", "active"})
        prof, pr = self._profile_ctx(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "docprofile.manage", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        active = p.get("active")
        if not isinstance(active, bool):
            raise Rejected("field_type", "Поле «active» должно быть логическим.", {"field": "active"})
        self._check_version(prof, ev, lambda o: self.v_profile(o, ctx.conn))
        if active == bool(prof["active"]):
            raise Rejected("already_in_state",
                           "Профиль уже активен." if active else "Профиль уже отключён.")
        if active and self._active_conflict(ctx, pr["id"], prof["kit_id"], exclude_id=prof["id"]):
            raise Rejected("profile_active_exists",
                           "Активный профиль для этой области уже есть — сначала отключите его.")
        n = self._update_versioned(ctx, document_profiles, prof,
                                   {"active": active, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("document_profile", prof["id"], auth, prof["version"], n["version"], {"active": active})
        self._project_event(ctx, pr["id"], "docprofile_toggled",
                            f"Профиль шифров «{prof['name']}» {'включён' if active else 'отключён'}", auth,
                            object_type="document_profile", object_id=prof["id"])
        return {"profile": self.v_profile(n, ctx.conn)}

    def _docprofile_preview(self, ctx, p, ev):
        """docprofile.preview {project_id, kit_id?, label, label_pattern?} — проверка шифра перед созданием ревизии.

        Без label_pattern сверяет с действующим профилем области (комплектный переопределяет проектный); с
        label_pattern — проверяет переданный шаблон («пример» при настройке профиля, в БД ничего не пишется).
        """
        only_keys(p, {"project_id", "kit_id", "label", "label_pattern"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        self.policy.check(ctx.conn, ctx.user_id, "docprofile.read", ctx.now, project_id=pr["id"])
        label = req_str(p, "label", 40)
        pattern = opt_str(p, "label_pattern", 200)
        profile_view = None
        if pattern is not None:
            try:
                rk.validate_label_pattern(pattern)
            except ValueError as e:
                raise Rejected("bad_label_pattern", str(e), {"field": "label_pattern"})
        else:
            kit_id = p.get("kit_id")
            if kit_id is not None:
                kit_id = req_uuid(p, "kit_id")
                kit = self._get(ctx.conn, kits, kit_id)
                if kit is None or kit["project_id"] != pr["id"]:
                    raise Rejected("not_found", "Комплект: объект не найден.", {"id": kit_id})
            prof = self._effective_profile(ctx.conn, pr["id"], kit_id)
            if prof is None:
                return {"label": label, "pattern": None, "matches": True, "profile": None,
                        "note": "Нет активного профиля — шифр не проверяется."}
            pattern = prof["label_pattern"]
            profile_view = self.v_profile(prof, ctx.conn)
        return {"label": label, "pattern": pattern, "matches": rk.label_matches(pattern, label),
                "profile": profile_view}

    def _docrev_set_issue(self, ctx, p, ev):
        """docrev.set_issue {id, status_code} — статус выдачи ревизии из словаря действующего профиля.

        Одна действующая запись на ревизию: повторная установка заменяет статус (версия строки растёт), код и
        расшифровка сохраняются снимком — смена словаря профиля историю не переписывает.
        """
        only_keys(p, {"id", "status_code"})
        rev, doc, pr = self._revision_of(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "docrev.set_issue", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        code = req_str(p, "status_code", 40)
        prof = self._effective_profile(ctx.conn, pr["id"], doc["kit_id"])
        if prof is None or not json.loads(prof["issue_statuses_json"] or "[]"):
            raise Rejected("no_issue_profile",
                           "Для проекта/комплекта нет активного профиля со словарём статусов выдачи.")
        statuses = json.loads(prof["issue_statuses_json"])
        entry = rk.issue_status_of(statuses, code)
        if entry is None:
            raise Rejected("unknown_issue_code",
                           f"Статус «{code}» отсутствует в словаре профиля «{prof['name']}».",
                           {"field": "status_code", "known": [s["code"] for s in statuses]})
        row = ctx.conn.execute(sa.select(document_revision_issues).where(
            document_revision_issues.c.revision_id == rev["id"])).mappings().first()
        row = dict(row) if row is not None else None
        if row is not None and row["status_code"].lower() == entry["code"].lower() \
                and row["profile_id"] == prof["id"]:
            raise Rejected("already_in_state", f"Статус выдачи уже «{entry['code']}».")
        if row is None:
            ctx.conn.execute(document_revision_issues.insert().values(
                revision_id=rev["id"], profile_id=prof["id"], status_code=entry["code"],
                status_label=entry["label_ru"], version=1,
                set_by=ctx.user_id, set_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
            before, after = None, 1
        else:
            n = ctx.conn.execute(document_revision_issues.update().where(
                document_revision_issues.c.revision_id == rev["id"],
                document_revision_issues.c.version == row["version"]).values(
                    profile_id=prof["id"], status_code=entry["code"], status_label=entry["label_ru"],
                    version=row["version"] + 1, updated_by=ctx.user_id, updated_at=ctx.now)).rowcount
            if n != 1:
                raise Conflict("version_conflict",
                               "Статус выдачи изменён параллельно. Повторите после обновления.", {"current": None})
            before, after = row["version"], row["version"] + 1
        ctx.log("document_revision_issue", rev["id"], auth, before, after,
                {"profile_id": prof["id"], "status_code": entry["code"]})
        self._project_event(ctx, pr["id"], "revision_issue_set",
                            f"Документ {doc['code']}, ревизия «{rev['label']}»: статус выдачи {entry['code']}",
                            auth, object_type="document_revision", object_id=rev["id"])
        row = dict(ctx.conn.execute(sa.select(document_revision_issues).where(
            document_revision_issues.c.revision_id == rev["id"])).mappings().one())
        return {"issue": self.v_issue(row)}

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
        prof = self._effective_profile(ctx.conn, pr["id"], doc["kit_id"])
        if prof is not None and prof["label_pattern"] and not rk.label_matches(prof["label_pattern"], label):
            raise Rejected("label_pattern_mismatch",
                           f"Шифр «{label}» не соответствует схеме профиля «{prof['name']}» "
                           f"({prof['label_pattern']}). Пример: {prof['label_example'] or '—'}.",
                           {"field": "label", "profile_id": prof["id"], "pattern": prof["label_pattern"]})
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
        «Отправлена заказчику». В той же транзакции собирается ZIP-пакет из неизменяемых версий файлов ревизий
        с манифестом (D-63): байты детерминированы, хэш записывается в transmittal_packages и больше не
        пересобирается — он доказывает, что именно ушло заказчику. Ручная отправка регистрируется тем же
        трансмитталом; подтверждение доставки — отдельной командой (transmittal.register_receipt).
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
        pkg = self._build_package(ctx, pr, tid, seq + 1, recipient, channel, opt_str(p, "note", 2000), revs)
        ctx.log("transmittal_package", tid, auth, None, None,
                {"package_sha256": pkg["package_sha256"], "size_bytes": pkg["size_bytes"]})
        self._notify(ctx, [rev["created_by"] for rev, _ in revs], "transmittal_sent",
                     f"Трансмиттал № {seq + 1} отправлен ({rk.TRANSMITTAL_CHANNELS[channel].lower()})",
                     f"Адресат: {recipient[:200]}", object_type="document_transmittal", object_id=tid)
        return {"transmittal": self.v_transmittal(self._get(ctx.conn, document_transmittals, tid))}

    def _blob_bytes(self, conn, sha256: str) -> bytes:
        """Содержимое blob из хранилища блоков (упорядоченные части, склеенные)."""
        return b"".join(bytes(r.data) for r in conn.execute(
            sa.select(file_chunks.c.data).where(file_chunks.c.sha256 == sha256).order_by(file_chunks.c.idx)))

    def _build_package(self, ctx, pr, transmittal_id, seq, recipient, channel, note, revs) -> dict:
        """Собрать и сохранить ZIP-пакет трансмиттала (в транзакции команды). Возвращает строку
        transmittal_packages. Пакет детерминирован (core.rkd.build_package) и неизменяем: повторная сборка не
        предусмотрена — хэш фиксирует, что именно ушло заказчику."""
        sender = self._get(ctx.conn, users, ctx.user_id)
        manifest_items, files = [], []
        for pos, (rev, doc) in enumerate(revs, start=1):
            ver = self._get(ctx.conn, document_versions, rev["doc_version_id"])
            issue = ctx.conn.execute(sa.select(document_revision_issues).where(
                document_revision_issues.c.revision_id == rev["id"])).mappings().first()
            manifest_items.append({
                "position": pos, "document_code": doc["code"], "document_title": doc["title"],
                "revision_label": rev["label"], "revision_seq": rev["seq"],
                "issue_status_code": issue["status_code"] if issue else None,
                "issue_status_label": issue["status_label"] if issue else None,
                "file_name": ver["file_name"], "sha256": ver["sha256"], "size_bytes": ver["size_bytes"]})
            files.append((f"{pos:02d}/{ver['file_name']}", self._blob_bytes(ctx.conn, ver["sha256"])))
        manifest = {"format": rk.PACKAGE_FORMAT,
                    "transmittal": {"id": transmittal_id, "seq": seq, "project_code": pr["code"],
                                    "project_title": pr["title"], "recipient": recipient, "channel": channel,
                                    "note": note, "created_by": sender["display_name"], "created_at": iso(ctx.now)},
                    "items": manifest_items}
        data = rk.build_package(manifest, files)
        sha = hashlib.sha256(data).hexdigest()
        if self._get(ctx.conn, file_blobs, sha, "sha256") is None:
            chunk_count = max(1, -(-len(data) // FILE_CHUNK))
            ctx.conn.execute(file_blobs.insert().values(sha256=sha, size_bytes=len(data), chunk_count=chunk_count,
                                                        uploaded_by=ctx.user_id, uploaded_at=ctx.now))
            for idx in range(chunk_count):
                ctx.conn.execute(file_chunks.insert().values(sha256=sha, idx=idx,
                                                             data=data[idx * FILE_CHUNK:(idx + 1) * FILE_CHUNK]))
        name = f"Трансмиттал № {seq} — {pr['code']}.zip"
        ctx.conn.execute(transmittal_packages.insert().values(
            transmittal_id=transmittal_id, package_sha256=sha, package_name=name, size_bytes=len(data),
            created_by=ctx.user_id, created_at=ctx.now))
        return {"transmittal_id": transmittal_id, "package_sha256": sha, "package_name": name,
                "size_bytes": len(data)}

    @staticmethod
    def v_package(o: dict) -> dict:
        return {"transmittal_id": o["transmittal_id"], "package_sha256": o["package_sha256"],
                "package_name": o["package_name"], "size_bytes": o["size_bytes"],
                "created_at": iso(o["created_at"])}

    def get_transmittal_package_to(self, token: Optional[str], transmittal_id: object, dest,
                                   client_addr: Optional[str] = None) -> dict:
        """Записать ZIP-пакет трансмиттала в dest (как get_file_to: потоком, с пересчётом SHA-256 и размера).

        Права — transmittal.read в проекте трансмиттала (команда проекта скачивает пакет для отправки или сверки).
        При расхождении контрольной суммы ответ file_corrupted, и dest обязан быть выброшен (HTTP-слой так и
        делает). Событие пишется в аудит в обоих случаях.
        """
        try:
            tid = req_uuid({"transmittal_id": transmittal_id}, "transmittal_id")
        except Rejected as e:
            return {"status": "rejected", "code": e.code, "message_ru": e.message_ru}

        def tx(conn):
            now = self.now()
            user, device, _ = self._auth(conn, token, now)
            t = self._get(conn, document_transmittals, tid)
            authority = self._authority_for(conn, user["id"], "transmittal.read", now, t["project_id"]) \
                if t is not None else None
            if authority is None:
                self._audit(conn, now, user["id"], device, client_addr, None, "file.download",
                            "document_transmittal", tid, None, None, None, "denied", {})
                return {"status": "denied", "code": "no_authority",
                        "message_ru": "Трансмиттал не найден или нет доступа."}
            pkg = self._get(conn, transmittal_packages, tid, "transmittal_id")
            if pkg is None:
                return {"status": "rejected", "code": "package_absent",
                        "message_ru": "Пакет трансмиттала отсутствует (трансмиттал создан до версии программы с "
                                      "формированием пакета)."}
            dest.seek(0)
            dest.truncate()
            h, size = hashlib.sha256(), 0
            q = (sa.select(file_chunks.c.data).where(file_chunks.c.sha256 == pkg["package_sha256"])
                 .order_by(file_chunks.c.idx).execution_options(yield_per=4))
            for row in conn.execute(q):
                b = bytes(row.data)
                h.update(b)
                size += len(b)
                dest.write(b)
            dest.flush()
            ok = h.hexdigest() == pkg["package_sha256"] and size == pkg["size_bytes"]
            self._audit(conn, now, user["id"], device, client_addr, None, "file.download", "document_transmittal",
                        tid, authority, None, None, "accepted" if ok else "error",
                        {"package_sha256": pkg["package_sha256"], "intact": ok})
            if not ok:
                return {"status": "error", "code": "file_corrupted",
                        "message_ru": "Пакет на сервере повреждён (контрольная сумма не совпала) и не будет выдан. "
                                      "Сообщите администратору: нужно восстановление из резервной копии."}
            return {"status": "accepted", "file_name": pkg["package_name"],
                    "sha256": pkg["package_sha256"], "size_bytes": pkg["size_bytes"]}
        return self._guarded_tx(tx)

    def get_transmittal_package(self, token: Optional[str], transmittal_id: object,
                                client_addr: Optional[str] = None) -> tuple[dict, Optional[bytes]]:
        """То же, что get_transmittal_package_to, с результатом в памяти: (ответ, байты или None). Для тестов."""
        buf = io.BytesIO()
        resp = self.get_transmittal_package_to(token, transmittal_id, buf, client_addr)
        return resp, (buf.getvalue() if resp.get("status") == "accepted" else None)

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
                 "document_comment_responses": [], "document_transmittals": [], "transmittal_items": [],
                 "document_profiles": [], "document_revision_issues": [], "transmittal_packages": []}
        if not project_ids:
            return empty
        profiles = [self.v_profile(dict(r._mapping), conn) for r in conn.execute(
            sa.select(document_profiles).where(document_profiles.c.project_id.in_(project_ids))
            .order_by(document_profiles.c.project_id, document_profiles.c.name))]
        doc_ids = [r.id for r in conn.execute(
            sa.select(documents.c.id).where(documents.c.project_id.in_(project_ids)))]
        if not doc_ids:
            return {**empty, "document_profiles": profiles}
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
        issues = [self.v_issue(dict(r._mapping)) for r in conn.execute(
            sa.select(document_revision_issues).where(document_revision_issues.c.revision_id.in_(rev_ids))
            .order_by(document_revision_issues.c.revision_id))] if rev_ids else []
        t_ids = [t["id"] for t in transmittals]
        packages = [self.v_package(dict(r._mapping)) for r in conn.execute(
            sa.select(transmittal_packages).where(transmittal_packages.c.transmittal_id.in_(t_ids))
            .order_by(transmittal_packages.c.transmittal_id))] if t_ids else []
        return {"document_revisions": [self.v_revision(r) for r in revs], "document_reviews": reviews,
                "document_comments": comments, "document_comment_responses": responses,
                "document_transmittals": transmittals, "transmittal_items": t_items,
                "document_profiles": profiles, "document_revision_issues": issues,
                "transmittal_packages": packages}
