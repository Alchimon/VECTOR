# -*- coding: utf-8 -*-
"""База знаний и справочники компании (P3-3, blueprint 57).

* kb_articles — статьи (стандарты/инструкции/заметки) компании или проекта: scope company правят ведущий инженер
  и руководитель отдела, scope project — участники этого проекта (обе проверки в обработчике, не доверяем
  правилу каталога). code человекочитаемый и уникален в своём scope. Команды kb.* — офлайн-очередь.
* vendors — общая база подрядчиков/поставщиков компании (НЕ suppliers закупки: тот — реквизиты для заказов).
  Ведёт закупка (global_role procurement, как у suppliers, A-34). Только онлайн.
* equipment_catalog — справочник оборудования с паспортными характеристиками (params, JSON-объект); ведут
  ведущий инженер и технолог. Только онлайн.

Архивация везде мягкая (active=false): записи остаются в выдаче с пометкой — ссылки на них не разваливаются.
"""

import json
from typing import Optional

import sqlalchemy as sa

from vector.core.errors import Rejected

from .db import equipment_catalog, kb_articles, projects, users, vendors
from .validation import _INN_RE, iso, only_keys, opt_str, req_str, req_uuid

KB_KIND_LABELS_RU = {"standard": "Стандарт (СТО)", "template_ref": "Шаблон", "note": "Заметка", "howto": "Инструкция"}
VENDOR_KIND_LABELS_RU = {"supplier": "Поставщик", "contractor": "Подрядчик", "both": "Поставщик и подрядчик"}


def _tags(value) -> list[str]:
    """Теги: список непустых строк до 40 символов, до 12 штук, без повторов (регистр не важен)."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 12 or any(not isinstance(t, str) for t in value):
        raise Rejected("bad_tags", "Теги — список до 12 строк.", {"field": "tags"})
    out: list[str] = []
    for t in value:
        t = t.strip()
        if not t or len(t) > 40:
            raise Rejected("bad_tags", "Тег — непустая строка до 40 символов.", {"field": "tags"})
        if t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out


def _json_object(value, field: str) -> dict:
    """JSON-объект из поля команды (словарь); None — пустой объект."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Rejected("bad_json", f"Поле «{field}» — объект (словарь).", {"field": field})
    return value


class KbMixin:
    """Обработчики kb.*, vendor.*, equipment.* и представления для pull."""

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_kb_article(o: dict) -> dict:
        return {"id": o["id"], "scope": o["scope"], "project_id": o["project_id"], "kind": o["kind"],
                "kind_label": KB_KIND_LABELS_RU[o["kind"]], "code": o["code"], "title": o["title"],
                "body_md": o["body_md"], "tags": json.loads(o["tags"]) if o["tags"] else [],
                "active": bool(o["active"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_vendor(o: dict) -> dict:
        return {"id": o["id"], "inn": o["inn"], "name": o["name"],
                "contacts": json.loads(o["contacts"]) if o["contacts"] else {},
                "kind": o["kind"], "kind_label": VENDOR_KIND_LABELS_RU[o["kind"]],
                "rating_note": o["rating_note"], "active": bool(o["active"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_equipment(o: dict) -> dict:
        return {"id": o["id"], "code": o["code"], "name": o["name"], "category": o["category"],
                "manufacturer": o["manufacturer"],
                "params": json.loads(o["params"]) if o["params"] else {},
                "source_doc_id": o["source_doc_id"], "active": bool(o["active"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ база знаний
    def _kb_scope_check(self, ctx, scope: str, project_id: Optional[str]) -> None:
        """Scope-проверка поверх каталожного права: статью проекта пишет его участник (назначение в ЭТОМ проекте
        или замещение, D-35); стандарт компании — ведущий инженер / руководитель отдела (глобальная роль)."""
        from .authz import active_delegated_project, active_project_roles, effective_roles
        if scope == "project":
            roles = {r for _, r in active_project_roles(ctx.conn, ctx.user_id, project_id, ctx.now)}
            roles |= {r for _, r, _, _ in active_delegated_project(ctx.conn, ctx.user_id, project_id, ctx.now)}
            if not roles:
                raise Rejected("kb_scope_denied", "Статью проекта пишут его участники.")
        elif not (set(effective_roles(ctx.conn, ctx.user_id, ctx.now)) & {"lead_engineer", "dept_head"}):
            raise Rejected("kb_scope_denied", "Стандарты компании ведут ведущий инженер и руководитель отдела.")

    def _kb_code_free(self, ctx, scope: str, project_id: Optional[str], code: str, exclude_id: Optional[str] = None):
        q = sa.select(kb_articles.c.id).where(kb_articles.c.scope == scope, kb_articles.c.code == code)
        q = q.where(kb_articles.c.project_id == project_id) if scope == "project" else q
        if exclude_id:
            q = q.where(kb_articles.c.id != exclude_id)
        if ctx.conn.execute(q).first():
            raise Rejected("code_taken", f"Код «{code}» уже занят в этом разделе базы знаний.", {"field": "code"})

    def _kb_create(self, ctx, p, ev):
        """kb.create {id, scope, project_id?, kind, code, title, body_md, tags?} — статья базы знаний (P3-3).
        Можно офлайн (очередь): scope и уникальность кода проверяются повторно при применении."""
        only_keys(p, {"id", "scope", "project_id", "kind", "code", "title", "body_md", "tags"})
        scope = req_str(p, "scope", 16)
        if scope not in ("company", "project"):
            raise Rejected("bad_scope", "Раздел базы знаний — «company» (компания) или «project» (проект).",
                           {"field": "scope"})
        pid = req_uuid(p, "project_id") if scope == "project" else None
        if scope == "project" and self._get(ctx.conn, projects, pid) is None:
            raise Rejected("project_not_found", "Проект не найден.", {"field": "project_id"})
        kind = req_str(p, "kind", 16)
        if kind not in KB_KIND_LABELS_RU:
            raise Rejected("bad_kind", "Вид статьи: standard / template_ref / note / howto.", {"field": "kind"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "kb.create", ctx.now, project_id=pid)
        self._kb_scope_check(ctx, scope, pid)
        aid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, kb_articles, aid)
        code = req_str(p, "code", 64)
        self._kb_code_free(ctx, scope, pid, code)
        tags = _tags(p.get("tags"))
        ctx.conn.execute(kb_articles.insert().values(
            id=aid, scope=scope, project_id=pid, kind=kind, code=code, title=req_str(p, "title", 300),
            body_md=req_str(p, "body_md", 60000), tags=json.dumps(tags, ensure_ascii=False), active=True,
            version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("kb_article", aid, auth, None, 1, {"scope": scope, "project_id": pid, "code": code})
        return {"kb_article": self.v_kb_article(self._get(ctx.conn, kb_articles, aid))}

    def _kb_update(self, ctx, p, ev):
        """kb.update {id, kind?, code?, title?, body_md?, tags?} — правка статьи (version+1)."""
        only_keys(p, {"id", "kind", "code", "title", "body_md", "tags"})
        o = self._require(ctx.conn, kb_articles, req_uuid(p, "id"), "Статья базы знаний")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kb.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_kb_article)
        self._kb_scope_check(ctx, o["scope"], o["project_id"])
        ch: dict = {}
        if "kind" in p:
            kind = req_str(p, "kind", 16)
            if kind not in KB_KIND_LABELS_RU:
                raise Rejected("bad_kind", "Вид статьи: standard / template_ref / note / howto.", {"field": "kind"})
            ch["kind"] = kind
        if "code" in p:
            code = req_str(p, "code", 64)
            if code != o["code"]:
                self._kb_code_free(ctx, o["scope"], o["project_id"], code, exclude_id=o["id"])
            ch["code"] = code
        if "title" in p:
            ch["title"] = req_str(p, "title", 300)
        if "body_md" in p:
            ch["body_md"] = req_str(p, "body_md", 60000)
        if "tags" in p:
            ch["tags"] = json.dumps(_tags(p.get("tags")), ensure_ascii=False)
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, kb_articles, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("kb_article", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"kb_article": self.v_kb_article(n)}

    def _kb_archive(self, ctx, p, ev):
        """kb.archive {id, active} — в архив (active=false) или обратно; статья остаётся в выдаче с пометкой."""
        only_keys(p, {"id", "active"})
        o = self._require(ctx.conn, kb_articles, req_uuid(p, "id"), "Статья базы знаний")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kb.archive", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_kb_article)
        self._kb_scope_check(ctx, o["scope"], o["project_id"])
        target = p.get("active")
        if not isinstance(target, bool):
            raise Rejected("bad_active", "Поле active — true (вернуть) или false (в архив).", {"field": "active"})
        if target == bool(o["active"]):
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, kb_articles, o, {"active": target, "updated_by": ctx.user_id,
                                                         "updated_at": ctx.now})
        ctx.log("kb_article", o["id"], auth, o["version"], n["version"], {"active": target})
        return {"kb_article": self.v_kb_article(n)}

    # ------------------------------------------------------------ подрядчики (общая база компании)
    def _vendor_inn(self, p) -> Optional[str]:
        inn = opt_str(p, "inn", 12)
        if inn is not None and not _INN_RE.match(inn):
            raise Rejected("invalid_inn", "ИНН должен содержать 10 или 12 цифр.", {"field": "inn"})
        return inn

    def _vendor_create(self, ctx, p, ev):
        """vendor.create {id, name, kind, inn?, contacts?, rating_note?} — карточка в общей базе компании (онлайн)."""
        only_keys(p, {"id", "name", "kind", "inn", "contacts", "rating_note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "vendor.create", ctx.now)
        kind = req_str(p, "kind", 16)
        if kind not in VENDOR_KIND_LABELS_RU:
            raise Rejected("bad_kind", "Вид: supplier / contractor / both.", {"field": "kind"})
        vid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, vendors, vid)
        contacts = _json_object(p.get("contacts"), "contacts")
        ctx.conn.execute(vendors.insert().values(
            id=vid, inn=self._vendor_inn(p), name=req_str(p, "name", 300),
            contacts=json.dumps(contacts, ensure_ascii=False), kind=kind,
            rating_note=opt_str(p, "rating_note", 1000), active=True, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("vendor", vid, auth, None, 1, {"name": p["name"], "kind": kind})
        return {"vendor": self.v_vendor(self._get(ctx.conn, vendors, vid))}

    def _vendor_update(self, ctx, p, ev):
        """vendor.update {id, name?, kind?, inn?, contacts?, rating_note?} — правка карточки (version+1)."""
        only_keys(p, {"id", "name", "kind", "inn", "contacts", "rating_note"})
        o = self._require(ctx.conn, vendors, req_uuid(p, "id"), "Карточка подрядчика")
        auth = self.policy.check(ctx.conn, ctx.user_id, "vendor.update", ctx.now)
        self._check_version(o, ev, self.v_vendor)
        ch: dict = {}
        if "name" in p:
            ch["name"] = req_str(p, "name", 300)
        if "kind" in p:
            kind = req_str(p, "kind", 16)
            if kind not in VENDOR_KIND_LABELS_RU:
                raise Rejected("bad_kind", "Вид: supplier / contractor / both.", {"field": "kind"})
            ch["kind"] = kind
        if "inn" in p:
            ch["inn"] = self._vendor_inn(p)
        if "contacts" in p:
            ch["contacts"] = json.dumps(_json_object(p.get("contacts"), "contacts"), ensure_ascii=False)
        if "rating_note" in p:
            ch["rating_note"] = opt_str(p, "rating_note", 1000)
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, vendors, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("vendor", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"vendor": self.v_vendor(n)}

    def _vendor_archive(self, ctx, p, ev):
        """vendor.archive {id, active} — архивировать / вернуть (мягко: записи и ссылки не теряются)."""
        only_keys(p, {"id", "active"})
        o = self._require(ctx.conn, vendors, req_uuid(p, "id"), "Карточка подрядчика")
        auth = self.policy.check(ctx.conn, ctx.user_id, "vendor.archive", ctx.now)
        self._check_version(o, ev, self.v_vendor)
        target = p.get("active")
        if not isinstance(target, bool):
            raise Rejected("bad_active", "Поле active — true (вернуть) или false (в архив).", {"field": "active"})
        if target == bool(o["active"]):
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, vendors, o, {"active": target, "updated_by": ctx.user_id,
                                                     "updated_at": ctx.now})
        ctx.log("vendor", o["id"], auth, o["version"], n["version"], {"active": target})
        return {"vendor": self.v_vendor(n)}

    # ------------------------------------------------------------ каталог оборудования
    def _equipment_create(self, ctx, p, ev):
        """equipment.create {id, code, name, category, manufacturer?, params?, source_doc_id?} — запись каталога."""
        only_keys(p, {"id", "code", "name", "category", "manufacturer", "params", "source_doc_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "equipment.create", ctx.now)
        eid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, equipment_catalog, eid)
        code = req_str(p, "code", 64)
        if ctx.conn.execute(sa.select(equipment_catalog.c.id).where(equipment_catalog.c.code == code)).first():
            raise Rejected("code_taken", f"Код «{code}» уже есть в каталоге оборудования.", {"field": "code"})
        params = _json_object(p.get("params"), "params")
        ctx.conn.execute(equipment_catalog.insert().values(
            id=eid, code=code, name=req_str(p, "name", 300), category=req_str(p, "category", 120),
            manufacturer=opt_str(p, "manufacturer", 300), params=json.dumps(params, ensure_ascii=False),
            source_doc_id=p.get("source_doc_id"), active=True, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("equipment", eid, auth, None, 1, {"code": code, "category": p["category"]})
        return {"equipment": self.v_equipment(self._get(ctx.conn, equipment_catalog, eid))}

    def _equipment_update(self, ctx, p, ev):
        """equipment.update {id, code?, name?, category?, manufacturer?, params?, source_doc_id?} (version+1)."""
        only_keys(p, {"id", "code", "name", "category", "manufacturer", "params", "source_doc_id"})
        o = self._require(ctx.conn, equipment_catalog, req_uuid(p, "id"), "Запись каталога оборудования")
        auth = self.policy.check(ctx.conn, ctx.user_id, "equipment.update", ctx.now)
        self._check_version(o, ev, self.v_equipment)
        ch: dict = {}
        if "code" in p:
            code = req_str(p, "code", 64)
            if code != o["code"]:
                if ctx.conn.execute(sa.select(equipment_catalog.c.id).where(
                        equipment_catalog.c.code == code, equipment_catalog.c.id != o["id"])).first():
                    raise Rejected("code_taken", f"Код «{code}» уже есть в каталоге оборудования.", {"field": "code"})
            ch["code"] = code
        if "name" in p:
            ch["name"] = req_str(p, "name", 300)
        if "category" in p:
            ch["category"] = req_str(p, "category", 120)
        if "manufacturer" in p:
            ch["manufacturer"] = opt_str(p, "manufacturer", 300)
        if "params" in p:
            ch["params"] = json.dumps(_json_object(p.get("params"), "params"), ensure_ascii=False)
        if "source_doc_id" in p:
            ch["source_doc_id"] = p["source_doc_id"]
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, equipment_catalog, o, {**ch, "updated_by": ctx.user_id,
                                                               "updated_at": ctx.now})
        ctx.log("equipment", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"equipment": self.v_equipment(n)}

    def _equipment_archive(self, ctx, p, ev):
        """equipment.archive {id, active} — архивировать / вернуть запись каталога."""
        only_keys(p, {"id", "active"})
        o = self._require(ctx.conn, equipment_catalog, req_uuid(p, "id"), "Запись каталога оборудования")
        auth = self.policy.check(ctx.conn, ctx.user_id, "equipment.archive", ctx.now)
        self._check_version(o, ev, self.v_equipment)
        target = p.get("active")
        if not isinstance(target, bool):
            raise Rejected("bad_active", "Поле active — true (вернуть) или false (в архив).", {"field": "active"})
        if target == bool(o["active"]):
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, equipment_catalog, o, {"active": target, "updated_by": ctx.user_id,
                                                               "updated_at": ctx.now})
        ctx.log("equipment", o["id"], auth, o["version"], n["version"], {"active": target})
        return {"equipment": self.v_equipment(n)}

    # ------------------------------------------------------------ pull
    def _pull_kb(self, conn, user_id: str, now, readable_projects: list[str]) -> dict:
        """База знаний и справочники компании в снимок (P3-3): статьи компании — всем вошедшим, статьи проекта —
        читающим проект; подрядчики и оборудование — справочники компании, читают все вошедшие (как оргструктуру,
        D-44). Включая архивные (active=false) — с пометкой."""
        q = sa.select(kb_articles).where(
            sa.or_(kb_articles.c.scope == "company", kb_articles.c.project_id.in_(readable_projects)))
        return {"kb_articles": [self.v_kb_article(dict(r._mapping)) for r in
                                conn.execute(q.order_by(kb_articles.c.code, kb_articles.c.id))],
                "vendors": [self.v_vendor(dict(r._mapping)) for r in
                            conn.execute(sa.select(vendors).order_by(vendors.c.name, vendors.c.id))],
                "equipment": [self.v_equipment(dict(r._mapping)) for r in
                              conn.execute(sa.select(equipment_catalog).order_by(equipment_catalog.c.code,
                                                                                 equipment_catalog.c.id))]}
