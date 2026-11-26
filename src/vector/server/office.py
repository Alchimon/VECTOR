"""Офисные документы на сервере (Ф1.3, D-94, 55-office-documents.md § 7): таблицы и текст как объекты платформы.

Офисный документ = карточка documents (код в проекте) + неизменяемые версии файла (.vsheetx/.vdocx) + вид и
признак шаблона (office_documents, схема v44). Файловая механика — общая (дедупликация SHA-256, квитанции
загрузки); этот модуль добавляет: создание пустого документа НАШЕГО формата (сразу с версией 1), пометку
шаблонов и формирование документа из шаблона с подстановкой полей из объектов платформы ({{project.code}} и
т. п. — «документ = база данных»; контекст собирается сервером и содержит только то, что пользователю доступно
по правам проекта).

Инварианты (закреплены тестами tests/server/test_office_docs.py):
* вид — только sheet/doc (проверка и БД, и команды); шаблоном может быть только doc (текстовый акт/спецификация);
* render не лезет в чужие данные: контекст — проект комплекта, его заказчик, дата, исполнитель — по правам
  templates.render в ЭТОМ проекте;
* неподставленные поля возвращаются честно (missing_fields) — документ формируется, маркеры остаются видимыми;
* версия 1 созданного/сформированного документа неизменяема (общие триггеры document_versions);
* лицензионный гейт — по ВИДУ документа в транзакции (kind=sheet → «Таблицы», doc → «Текст»): префиксный
  гейт команд параметра не видит (D-94).
"""
from __future__ import annotations

import hashlib

import sqlalchemy as sa

from vector.core import modules as modules_core
from vector.core.errors import Rejected
from vector.office import template as tpl
from vector.office.model import Workbook
from vector.office.model_doc import Document
from vector.office.vdocx import write_vdocx
from vector.office.vsheetx import write_vsheetx

from .db import (
    FILE_CHUNK,
    customers,
    document_versions,
    documents,
    file_blobs,
    file_chunks,
    file_uploads,
    office_documents,
    tenders,
    users,
)
from .validation import only_keys, req_str, req_uuid

KINDS = ("sheet", "doc")


class OfficeMixin:
    """Примесь к Service: офисные документы (создание, шаблоны, формирование из шаблона)."""

    # ------------------------------------------------------------ внутренние помощники
    def _store_bytes(self, ctx, project_id: str, data: bytes) -> str:
        """Сохранить байты в файловом хранилище (дедупликация по SHA-256) + квитанция file_uploads —
        чтобы версию документа можно было сослать и скачивать по обычным правилам (как file.upload)."""
        sha = hashlib.sha256(data).hexdigest()
        if self._get(ctx.conn, file_blobs, sha, "sha256") is None:
            chunk_count = max(1, -(-len(data) // FILE_CHUNK))
            ctx.conn.execute(file_blobs.insert().values(sha256=sha, size_bytes=len(data), chunk_count=chunk_count,
                                                        uploaded_by=ctx.user_id, uploaded_at=ctx.now))
            for idx in range(chunk_count):
                ctx.conn.execute(file_chunks.insert().values(sha256=sha, idx=idx,
                                                             data=data[idx * FILE_CHUNK:(idx + 1) * FILE_CHUNK]))
        ctx.conn.execute(file_uploads.insert().values(sha256=sha, user_id=ctx.user_id, project_id=project_id,
                                                      uploaded_at=ctx.now))
        return sha

    def _blob_bytes(self, conn, sha256: str) -> bytes:           # та же механика, что в пакетах РКД (rkd.py)
        return b"".join(bytes(r.data) for r in conn.execute(
            sa.select(file_chunks.c.data).where(file_chunks.c.sha256 == sha256).order_by(file_chunks.c.idx)))

    def _office_row(self, ctx, document_id: str) -> dict:
        row = self._get(ctx.conn, office_documents, document_id, "document_id")
        if row is None:
            raise Rejected("not_office_document", "Это не офисный документ (таблица/текст ВЕКТОР).",
                           {"field": "id"})
        return row

    def _render_context(self, ctx, project: dict) -> dict:
        """Контекст подстановки из объектов платформы: проект, его заказчик (через тендер), дата, исполнитель.
        Ничего лишнего — только то, что участник проекта и так видит."""
        customer = None
        if project.get("tender_id"):
            t = self._get(ctx.conn, tenders, project["tender_id"])
            if t:
                customer = self._get(ctx.conn, customers, t["customer_id"])
        me = self._get(ctx.conn, users, ctx.user_id)
        return {
            "project": {"code": project["code"], "title": project["title"]},
            "customer": {"name": customer["name"]} if customer else {},
            "user": {"name": me["display_name"] if me else ""},
            "today": ctx.now.strftime("%d.%m.%Y"),
        }

    def _license_gate_module(self, mod: str) -> None:
        """Гейт лицензии по модулю, выбранному ПАРАМЕТРОМ команды (kind=sheet → модуль «Таблицы», doc → «Текст»):
        общий гейт работает по префиксу команды и параметра не видит, поэтому проверка здесь, в транзакции."""
        st = self.license_status().modules[mod]
        if st.state in ("active", "grace"):
            return
        m = modules_core.MODULE_BY_ID[mod]
        if st.state == "readonly":
            raise Rejected("module_readonly",
                           f"Подписка на модуль «{m.title_ru}» истекла: документы открываются и выгружаются, "
                           "изменения — после продления.")
        raise Rejected("module_not_licensed",
                       f"Модуль «{m.title_ru}» не входит в лицензию этой установки. Обратитесь к поставщику.")

    # ------------------------------------------------------------ команды
    def _office_doc_create(self, ctx, p, ev):
        """office_doc.create {id, kit_id, code, title, kind} — новый офисный документ сразу с версией 1:
        пустая книга (.vsheetx) или пустой документ (.vdocx) нашего формата. Код уникален в проекте (как у всех
        документов). Можно офлайн (очередь) — файл формирует сервер, клиент ничего не загружает."""
        only_keys(p, {"id", "kit_id", "code", "title", "kind"})
        kit, pr = self._open_project_of_kit(ctx, req_uuid(p, "kit_id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "office_doc.create", ctx.now, project_id=pr["id"])
        self._project_must_be_active(pr)
        kind = req_str(p, "kind", 16)
        if kind not in KINDS:
            raise Rejected("bad_kind", "Вид офисного документа: sheet (таблица) или doc (текст).", {"field": "kind"})
        self._license_gate_module("sheets" if kind == "sheet" else "text")
        did = req_uuid(p, "id")
        self._ensure_new(ctx.conn, documents, did)
        code = req_str(p, "code", 100)
        self._document_code_free(ctx, pr["id"], code)
        title = req_str(p, "title", 300)
        # Пустой документ нашего формата — версия 1 (файл сразу существует: клиент открывает и редактирует).
        if kind == "sheet":
            wb = Workbook()
            wb.sheet("Лист 1")
            data, fname = write_vsheetx(wb), f"{code}.vsheetx"
        else:
            doc = Document(title=title)
            doc.para("")
            data, fname = write_vdocx(doc), f"{code}.vdocx"
        sha = self._store_bytes(ctx, pr["id"], data)
        ctx.conn.execute(documents.insert().values(
            id=did, project_id=pr["id"], kit_id=kit["id"], code=code, title=title, current_version_no=1, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.conn.execute(office_documents.insert().values(
            document_id=did, kind=kind, is_template=False, created_by=ctx.user_id, created_at=ctx.now))
        ctx.conn.execute(document_versions.insert().values(
            document_id=did, version_no=1, file_name=fname, sha256=sha, size_bytes=len(data),
            comment="Создан пустой " + ("табличный документ" if kind == "sheet" else "текстовый документ"),
            created_by=ctx.user_id, created_at=ctx.now))
        ctx.log("document", did, auth, None, 1, {"project_id": pr["id"], "code": code, "office_kind": kind})
        return {"document": self.v_document(self._get(ctx.conn, documents, did)),
                "office": {"kind": kind, "is_template": False}}

    def _office_doc_set_template(self, ctx, p, ev):
        """office_doc.set_template {id, is_template} — пометить документ шаблоном (или снять пометку).
        Шаблоном может быть текстовый документ (акты/спецификации). Право office_doc.manage (dept_head)."""
        only_keys(p, {"id", "is_template"})
        o = self._require(ctx.conn, documents, req_uuid(p, "id"), "Документ")
        row = self._office_row(ctx, o["id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "office_doc.manage", ctx.now, project_id=o["project_id"])
        self._license_gate_module("text")
        flag = bool(p.get("is_template"))
        if flag and row["kind"] != "doc":
            raise Rejected("bad_kind", "Шаблоном может быть только текстовый документ (doc).", {"field": "is_template"})
        if bool(row["is_template"]) == flag:
            raise Rejected("no_changes", "Пометка шаблона уже " + ("стоит." if flag else "снята."))
        ctx.conn.execute(office_documents.update().where(office_documents.c.document_id == o["id"])
                         .values(is_template=flag))
        ctx.log("document", o["id"], auth, None, None, {"office_is_template": flag})
        return {"document_id": o["id"], "is_template": flag}

    def _office_doc_render(self, ctx, p, ev):
        """templates.render {id, template_id, kit_id, code, title} — сформировать документ из шаблона:
        поля {{project.code}}, {{customer.name}}, {{user.name}}, {{today}}, {{doc.code}} подставляются из
        платформы. Результат — новый документ (версия 1, неизменяемая); неподставленные поля — в missing_fields.
        Только онлайн (читает файл шаблона и создаёт файл результата)."""
        only_keys(p, {"id", "template_id", "kit_id", "code", "title"})
        kit, pr = self._open_project_of_kit(ctx, req_uuid(p, "kit_id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "templates.render", ctx.now, project_id=pr["id"])
        self._license_gate_module("text")
        self._project_must_be_active(pr)
        tpl_doc = self._require(ctx.conn, documents, req_uuid(p, "template_id"), "Шаблон")
        trow = self._office_row(ctx, tpl_doc["id"])
        if not trow["is_template"]:
            raise Rejected("not_a_template", "Документ не помечен как шаблон (office_doc.set_template).",
                           {"field": "template_id"})
        ver = ctx.conn.execute(sa.select(document_versions).where(
            document_versions.c.document_id == tpl_doc["id"],
            document_versions.c.version_no == tpl_doc["current_version_no"])).first()
        if ver is None or tpl_doc["current_version_no"] < 1:
            raise Rejected("template_empty", "У шаблона нет ни одной версии файла — загрузите .vdocx.")
        data = self._blob_bytes(ctx.conn, ver._mapping["sha256"])
        context = self._render_context(ctx, pr)
        code = req_str(p, "code", 100)
        context["doc"] = {"code": code, "title": req_str(p, "title", 300)}
        try:
            out, missing = tpl.render_template(data, context)
        except tpl.DocFormatError as e:                 # битый файл шаблона — честный отказ
            raise Rejected("template_broken", f"Файл шаблона не читается: {e}") from None
        did = req_uuid(p, "id")
        self._ensure_new(ctx.conn, documents, did)
        self._document_code_free(ctx, pr["id"], code)
        sha = self._store_bytes(ctx, pr["id"], out)
        fname = f"{code}.vdocx"
        ctx.conn.execute(documents.insert().values(
            id=did, project_id=pr["id"], kit_id=kit["id"], code=code, title=context["doc"]["title"],
            current_version_no=1, version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.conn.execute(office_documents.insert().values(
            document_id=did, kind="doc", is_template=False, created_by=ctx.user_id, created_at=ctx.now))
        ctx.conn.execute(document_versions.insert().values(
            document_id=did, version_no=1, file_name=fname, sha256=sha, size_bytes=len(out),
            comment=f"Сформирован из шаблона {tpl_doc['code']}", created_by=ctx.user_id, created_at=ctx.now))
        ctx.log("document", did, auth, None, 1, {"project_id": pr["id"], "code": code,
                                                 "from_template": tpl_doc["id"], "missing_fields": missing})
        return {"document": self.v_document(self._get(ctx.conn, documents, did)),
                "missing_fields": missing, "sha256": sha, "size_bytes": len(out)}
