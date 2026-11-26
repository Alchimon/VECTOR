"""Офисный редактор на клиенте (Ф1.4, D-95): рабочая копия .vsheetx/.vdocx в памяти клиента, правка ячеек с
пересчётом формул, правка абзацев текста, сохранение новой версией документа (онлайн, конфликты честные).

UI-часть (окно OfficeEditor.qml) проверяется стендом снимков; здесь — логика моста (office_view) и слоты
Backend (officeLoad/officeEditCell/officeEditPara/officeSave) поверх SyncClient+LoopbackTransport.
"""
from __future__ import annotations

import hashlib

import pytest
from conftest import new_id
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: F401  (фикстура mk и общие константы)

from vector.client import office_view
from vector.office.model import Workbook
from vector.office.model_doc import Document
from vector.office.vdocx import read_vdocx, write_vdocx
from vector.office.vsheetx import read_vsheetx, write_vsheetx
from vector.office.formula import recalc

_ = (BACKENDS, FAST_KDF, SERVER)


@pytest.fixture
def oproj(world):
    """Проект с комплектом и РП; офисный документ-таблица и документ-текст на сервере."""
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-100", "title": "Проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp",
                                      "reason": "Приказ"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    sheet_id = rp.ok("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "СП-1", "title": "Спецификация",
                                           "kind": "sheet"})["document"]["id"]
    doc_id = rp.ok("office_doc.create", {"id": new_id(), "kit_id": kit, "code": "АКТ-1", "title": "Акт",
                                         "kind": "doc"})["document"]["id"]
    return {"pid": pid, "kit": kit, "rp": rp, "sheet": sheet_id, "doc": doc_id}


def _client(world, mk, login="rp1"):
    """Клиент с обновлённым кэшем (pull), чтобы были видны созданные документы."""
    c = mk(login)
    c.pull()
    return c


class TestSheetEditor:
    def test_load_edit_recalc(self, world, mk, oproj):
        """Таблица: загрузка → представление сетки; правка ячейки пересчитывает формулы (мост office_view)."""
        c = _client(world, mk)
        # Кладём наполнение: числа и формулу (как будто документ уже редактировали).
        wb = Workbook()
        sh = wb.sheet("Лист 1")
        sh.set("A1", value=__import__("decimal").Decimal("2"))
        sh.set("A2", value=__import__("decimal").Decimal("3"))
        sh.set("B1", formula="=A1+A2")
        recalc(sh)
        data = write_vsheetx(wb)
        rp = world.login("rp1")
        rp.svc.put_file(rp.token, oproj["pid"], data, hashlib.sha256(data).hexdigest(), rp.addr)
        rp.ok("document.add_version", {"id": oproj["sheet"], "sha256": hashlib.sha256(data).hexdigest(),
                                       "file_name": "spec.vsheetx"}, expected_version=1)
        c.pull()
        # Клиентское чтение версии (те же байты, что и редактируем).
        wb2 = read_vsheetx(data)
        view = office_view.sheet_to_view(wb2)
        assert view["name"] == "Лист 1" and view["cells"]["B1"]["computed"] == "5"
        # Правка: A1 = 10 → B1 пересчитался до 13; ввод «010» не теряет нули (строка).
        cell = office_view.view_set_cell(wb2, "A1", "10")
        assert cell["text"] == "10" and office_view.sheet_to_view(wb2)["cells"]["B1"]["computed"] == "13"
        assert office_view.view_set_cell(wb2, "C1", "010")["text"] == "010"

    def test_save_new_version(self, world, mk, oproj):
        """Сохранение таблицы — новая версия документа с пересчитанными значениями (he confl)."""
        c = _client(world, mk)
        wb = Workbook(); sh = wb.sheet("Лист 1")
        sh.set("A1", value=__import__("decimal").Decimal("5"))
        office_view.view_set_cell(wb, "A2", "7")
        office_view.view_set_cell(wb, "B1", "=A1*A2")
        data = write_vsheetx(wb)
        d0 = c.get_local("document", oproj["sheet"])
        r = c.add_document_version(oproj["sheet"], data, "spec.vsheetx", "Из редактора", d0["version"])
        assert r.status == "accepted", r
        doc = c.get_local("document", oproj["sheet"])
        assert doc["current_version_no"] == 2
        import sqlalchemy as sa
        from vector.server import db as sdb
        with world.svc.engine.connect() as conn:
            sha = conn.execute(sa.select(sdb.document_versions.c.sha256).where(
                sdb.document_versions.c.document_id == oproj["sheet"],
                sdb.document_versions.c.version_no == 2)).scalar_one()
            blob = b"".join(bytes(x.data) for x in conn.execute(
                sa.select(sdb.file_chunks.c.data).where(sdb.file_chunks.c.sha256 == sha).order_by(sdb.file_chunks.c.idx)))
        assert office_view.sheet_to_view(read_vsheetx(blob))["cells"]["B1"]["computed"] == "35"


class TestDocEditor:
    def test_load_edit_save(self, world, mk, oproj):
        """Текст: загрузка абзацев, правка строки, сохранение новой версией .vdocx."""
        c = _client(world, mk)
        doc = Document(title="Акт")
        doc.para("Акт по проекту {{project.code}}", style="heading", level=1)
        doc.para("Итого: 0 шт.")
        data = write_vdocx(doc)
        rp = world.login("rp1")
        rp.svc.put_file(rp.token, oproj["pid"], data, hashlib.sha256(data).hexdigest(), rp.addr)
        rp.ok("document.add_version", {"id": oproj["doc"], "sha256": hashlib.sha256(data).hexdigest(),
                                       "file_name": "akt.vdocx"}, expected_version=1)
        c.pull()
        rows = office_view.doc_to_view(read_vdocx(data))
        assert rows[0]["style"] == "heading" and "проекту" in rows[0]["text"]
        rows[1]["text"] = "Итого: 12 шт."
        out = office_view.doc_from_view("Акт", rows)
        assert "12 шт." in out.text and "{{project.code}}" in out.text   # поля-шаблоны не трогаем при правке
        doc_now = c.get_local("document", oproj["doc"])
        r = c.add_document_version(oproj["doc"], write_vdocx(out), "akt.vdocx", "Из редактора", doc_now["version"])
        assert r.status == "accepted"
        assert c.get_local("document", oproj["doc"])["current_version_no"] == 3

    def test_table_block_placeholder(self):
        """Таблица в документе первой порции редактора — пометка-строка (не ломает разбор/сборку)."""
        doc = Document(title="Т")
        doc.para("Шапка")
        doc.table(2, 3)
        rows = office_view.doc_to_view(doc)
        assert rows[1]["block"] == "table-note" and "2×3" in rows[1]["text"]
        # doc_from_view не роняется на пометке и не создаёт таблицу (абзацы сохраняются).
        out = office_view.doc_from_view("Т", rows)
        assert out.text == "Шапка"
