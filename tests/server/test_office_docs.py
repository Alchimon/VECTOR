"""Офисные документы на сервере (Ф1.3, D-94): создание таблицы/текста с версией 1, шаблоны и формирование
документа из шаблона с подстановкой полей платформы ({{project.code}} и т. п.); лицензионный гейт по виду
(kind=sheet → модуль «Таблицы», doc → «Текст»).
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from conftest import World, new_id

from vector.core import modules as M
from vector.core.contracts import Contracts
from vector.office.model_doc import Document
from vector.office.vdocx import read_vdocx, write_vdocx
from vector.office.vsheetx import read_vsheetx
from vector.server import db
from vector.server.service import Service


@pytest.fixture
def oproj(world):
    """Проект с комплектом и командой (паттерн test_tasks_documents.proj, локально — модуль самодостаточен)."""
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-100", "title": "Шкафы для завода"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp",
                                      "reason": "Приказ"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant",
                             "reason": "Работы"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    return {"pid": pid, "kit": kit, "rp": rp, "eng": world.login("eng1"), "tender": tender}


def _current_sha(world, document_id: str) -> str:
    """SHA-256 файла текущей версии документа (из БД — клиент получает его метаданными версии)."""
    with world.svc.engine.connect() as c:
        d = c.execute(sa.select(db.documents.c.current_version_no)
                      .where(db.documents.c.id == document_id)).one()
        return c.execute(sa.select(db.document_versions.c.sha256).where(
            db.document_versions.c.document_id == document_id,
            db.document_versions.c.version_no == d[0])).scalar_one()


def _blob(world, sha: str) -> bytes:
    """Байты файла из хранилища (для проверок: прочитать записанную версию документа)."""
    with world.svc.engine.connect() as c:
        return b"".join(bytes(r.data) for r in c.execute(
            sa.select(db.file_chunks.c.data).where(db.file_chunks.c.sha256 == sha)
            .order_by(db.file_chunks.c.idx)))


def _office_row(world, document_id: str) -> dict:
    with world.svc.engine.connect() as c:
        return dict(c.execute(sa.select(db.office_documents)
                              .where(db.office_documents.c.document_id == document_id)).one()._mapping)


def _bare_doc(world, pid, kit, code, kind="doc", is_template=True) -> str:
    """Офисный документ БЕЗ версий файла (состояние, недостижимое командами create — нужно для отказов render)."""
    did = new_id()
    now = datetime(2026, 11, 16, tzinfo=UTC)
    with world.svc.engine.begin() as c:
        c.execute(db.documents.insert().values(id=did, project_id=pid, kit_id=kit, code=code, title="Пустой",
                                               current_version_no=0, version=1, created_by=world.users["rp1"],
                                               created_at=now, updated_by=world.users["rp1"], updated_at=now))
        c.execute(db.office_documents.insert().values(document_id=did, kind=kind, is_template=is_template,
                                                      created_by=world.users["rp1"], created_at=now))
    return did


class TestCreate:
    def test_create_sheet_and_doc_with_version1(self, world, oproj):
        rp = oproj["rp"]
        d1 = rp.ok("office_doc.create", {"id": new_id(), "kit_id": oproj["kit"], "code": "СП-1",
                                         "title": "Спецификация", "kind": "sheet"})
        assert d1["document"]["current_version_no"] == 1 and d1["office"] == {"kind": "sheet", "is_template": False}
        row = _office_row(world, d1["document"]["id"])
        assert row["kind"] == "sheet" and row["is_template"] is False
        # Файл версии 1 — валидный .vsheetx с пустым «Лист 1».
        wb = read_vsheetx(_blob(world, _current_sha(world, d1["document"]["id"])))
        assert wb.sheets[0].name == "Лист 1"
        d2 = rp.ok("office_doc.create", {"id": new_id(), "kit_id": oproj["kit"], "code": "АКТ-1",
                                         "title": "Акт", "kind": "doc"})
        doc = read_vdocx(_blob(world, _current_sha(world, d2["document"]["id"])))
        assert doc.title == "Акт"

    def test_bad_kind_and_duplicate_code(self, world, oproj):
        rp = oproj["rp"]
        r = rp.cmd("office_doc.create", {"id": new_id(), "kit_id": oproj["kit"], "code": "Х-1",
                                         "title": "Х", "kind": "slides"})
        assert (r["status"], r["code"]) == ("rejected", "bad_kind")
        rp.ok("office_doc.create", {"id": new_id(), "kit_id": oproj["kit"], "code": "Х-2", "title": "Х",
                                    "kind": "sheet"})
        r = rp.cmd("office_doc.create", {"id": new_id(), "kit_id": oproj["kit"], "code": "Х-2",
                                         "title": "Дубль", "kind": "sheet"})
        assert (r["status"], r["code"]) == ("rejected", "document_code_taken")


class TestTemplate:
    def _make_template(self, world, oproj) -> str:
        """Шаблон акта: загрузить .vdocx с полями, пометить шаблоном (право — у руководителя отдела head1).
        Возвращает id документа-шаблона."""
        rp = oproj["rp"]
        head = world.login("head1")
        tid = new_id()
        rp.ok("office_doc.create", {"id": tid, "kit_id": oproj["kit"], "code": "ШАБЛОН-АКТ", "title": "Шаблон акта",
                                    "kind": "doc"})
        doc = Document(title="Акт {{doc.code}}")
        doc.para("Акт по проекту {{project.code}} — {{project.title}}", style="heading", level=1)
        doc.para("Заказчик: {{customer.name}}. Дата: {{today}}. Составил: {{user.name}}.")
        doc.para("Примечание: {{project.budget}}")                    # такого поля нет — останется маркером
        data = write_vdocx(doc)
        sha = hashlib.sha256(data).hexdigest()
        r = rp.svc.put_file(rp.token, oproj["pid"], data, sha, rp.addr)
        assert r["status"] == "accepted"
        rp.ok("document.add_version", {"id": tid, "sha256": sha, "file_name": "shablon.vdocx"}, expected_version=1)
        head.ok("office_doc.set_template", {"id": tid, "is_template": True})
        return tid

    def test_set_template_rules(self, world, oproj):
        rp, head = oproj["rp"], world.login("head1")
        sid = new_id()
        rp.ok("office_doc.create", {"id": sid, "kit_id": oproj["kit"], "code": "Т-1", "title": "Таб", "kind": "sheet"})
        r = head.cmd("office_doc.set_template", {"id": sid, "is_template": True})
        assert (r["status"], r["code"]) == ("rejected", "bad_kind")   # шаблон — только текстовый документ
        did = new_id()
        rp.ok("office_doc.create", {"id": did, "kit_id": oproj["kit"], "code": "Д-1", "title": "Док", "kind": "doc"})
        # Шаблоны ведёт руководитель отдела (dept_head), не участник проекта:
        r = oproj["eng"].cmd("office_doc.set_template", {"id": did, "is_template": True})
        assert r["status"] == "denied"
        r = rp.cmd("office_doc.set_template", {"id": did, "is_template": True})
        assert r["status"] == "denied"                                # rp без dept_head тоже не может
        head.ok("office_doc.set_template", {"id": did, "is_template": True})
        assert _office_row(world, did)["is_template"] is True
        r2 = head.cmd("office_doc.set_template", {"id": did, "is_template": True})
        assert r2["code"] == "no_changes"
        # Снятие пометки — симметрично (тоже head1).
        head.ok("office_doc.set_template", {"id": did, "is_template": False})
        assert _office_row(world, did)["is_template"] is False

    def test_render_from_template(self, world, oproj):
        rp = oproj["rp"]
        tid = self._make_template(world, oproj)
        assert _office_row(world, tid)["is_template"] is True
        out = rp.ok("templates.render", {"id": new_id(), "template_id": tid, "kit_id": oproj["kit"],
                                          "code": "АКТ-17", "title": "Акт по проекту"})
        assert out["missing_fields"] == ["project.budget"]            # неизвестное поле — честно в ответе
        doc = read_vdocx(_blob(world, out["sha256"]))
        assert doc.title == "Акт АКТ-17"
        assert "П-100" in doc.text and "Шкафы для завода" in doc.text
        assert "АО Заказчик" in doc.text                              # заказчик из тендера проекта
        assert "{{project.budget}}" in doc.text                       # маркер остался — не затёрли молча
        # Журнал: видно, из какого шаблона сформировано.
        with world.svc.engine.connect() as c:
            row = c.execute(sa.select(db.audit_log.c.details_json).where(
                db.audit_log.c.object_id == out["document"]["id"])).first()
        assert "from_template" in row[0]

    def test_render_rejections(self, world, oproj):
        rp = oproj["rp"]
        plain = new_id()
        rp.ok("office_doc.create", {"id": plain, "kit_id": oproj["kit"], "code": "Д-9", "title": "Обычный",
                                    "kind": "doc"})
        r = rp.cmd("templates.render", {"id": new_id(), "template_id": plain, "kit_id": oproj["kit"],
                                         "code": "АКТ-18", "title": "Х"})
        assert (r["status"], r["code"]) == ("rejected", "not_a_template")
        # Шаблон без файла (ни одной версии) — честный отказ, а не падение.
        empty = _bare_doc(world, oproj["pid"], oproj["kit"], "Д-10")
        r = rp.cmd("templates.render", {"id": new_id(), "template_id": empty, "kit_id": oproj["kit"],
                                         "code": "АКТ-19", "title": "Х"})
        assert (r["status"], r["code"]) == ("rejected", "template_empty")


_LIC_WORLD: dict = {}
_LIC_WORLD_TEXT: dict = {}


def _licensed_world(request, engine, clock, fast_policy):
    """World с лицензией «только pdm» (вечная). Один экземпляр на движок БД — экземпляры БД внутри прогона
    разделяются между sqlite/pg-параметрами, а bootstrap_admin в той же БД дважды запрещён."""
    key = request.node.callspec.id
    if key not in _LIC_WORLD:
        lic = M.License(license_id="L-1", customer="Тест", issued_at=date(2026, 1, 1), kind="perpetual",
                        valid_until=None, support_until=None, modules=frozenset({"pdm"}), seats=None,
                        grace_days=14, key_id="t")
        svc = Service(engine, contracts=Contracts.load(), clock=clock, sec=fast_policy, vendor_license=lic)
        w = World(svc, clock)
        rp = w.login("rp1")
        _, tender, _ = w.won_tender(rp)
        pid, kit = new_id(), new_id()
        rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-200", "title": "Проект"})
        w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["rp1"], "project_role": "rp",
                                      "reason": "Приказ"})
        rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
        _LIC_WORLD[key] = (w, rp, pid, kit)
    return _LIC_WORLD[key]


class TestLicenseGate:
    def test_module_gate_by_kind(self, request, clock, engine, fast_policy):
        """Лицензия «только pdm»: офисные команды отклоняются по модулю, выбранному ПАРАМЕТРОМ kind."""
        w, rp, _pid, kit = _licensed_world(request, engine, clock, fast_policy)
        r = rp.cmd("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "СП-1", "title": "Спец",
                                         "kind": "sheet"})
        # Префикс office_doc. → «Текст» по умолчанию (см. карту модулей): без text в лицензии отказ идёт
        # ПО ПРЕФИКСУ раньше, чем команда дошла до гейта по kind. «Таблицы» появляются в отказе, когда
        # text разрешён, а sheets — нет (проверено ниже вторым прогоном).
        assert (r["status"], r["code"]) == ("rejected", "module_not_licensed")
        assert "Текст" in r["message_ru"]
        r = rp.cmd("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "Д-1", "title": "Док",
                                         "kind": "doc"})
        assert (r["status"], r["code"]) == ("rejected", "module_not_licensed")
        # set_template — модуль «Текст», тот же отказ (лицензия проверяется после загрузки документа).
        pid2, kit2 = new_id(), new_id()
        rp2 = w.login("rp2")
        _, tender2, _ = w.won_tender(rp2)
        rp2.ok("project.start_rkd", {"id": pid2, "tender_id": tender2, "code": "П-201", "title": "Проект"})
        w.admin.ok("project.assign", {"project_id": pid2, "user_id": w.users["rp2"], "project_role": "rp",
                                      "reason": "Приказ"})
        rp2.ok("kit.create", {"id": kit2, "project_id": pid2, "code": "АТХ", "title": "Автоматизация"})
        did = _bare_doc(w, pid2, kit2, "Д-1", is_template=False)
        r = w.login("head1").cmd("office_doc.set_template", {"id": did, "is_template": True})
        assert (r["status"], r["code"]) == ("rejected", "module_not_licensed")

    def test_kind_gate_tables(self, request, clock, engine, fast_policy):
        """Лицензия «pdm + text»: команда create(kind=sheet) доходит до внутреннего гейта и отклоняется
        по «Таблицам»; create(kind=doc) работает."""
        key = request.node.callspec.id
        if key not in _LIC_WORLD_TEXT:
            lic = M.License(license_id="L-2", customer="Тест", issued_at=date(2026, 1, 1), kind="perpetual",
                            valid_until=None, support_until=None, modules=frozenset({"pdm", "text"}), seats=None,
                            grace_days=14, key_id="t")
            svc = Service(engine, contracts=Contracts.load(), clock=clock, sec=fast_policy, vendor_license=lic)
            w = World(svc, clock)
            rp = w.login("rp1")
            _, tender, _ = w.won_tender(rp)
            pid, kit = new_id(), new_id()
            rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-300", "title": "Проект"})
            w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["rp1"], "project_role": "rp",
                                          "reason": "Приказ"})
            rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
            _LIC_WORLD_TEXT[key] = (w, rp, pid, kit)
        w, rp, _pid, kit = _LIC_WORLD_TEXT[key]
        r = rp.cmd("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "СП-2", "title": "Спец",
                                         "kind": "sheet"})
        assert (r["status"], r["code"]) == ("rejected", "module_not_licensed")
        assert "Таблицы" in r["message_ru"]
        rp.ok("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "Д-2", "title": "Док", "kind": "doc"})
