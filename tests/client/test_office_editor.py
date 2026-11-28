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


class TestDocumentPreview:
    """Мини-предпросмотр офисного документа для инспектора 2.0 (D-103): первые строки из байтов версии."""

    def test_preview_sheet(self):
        """Таблица: заголовок — имя листа, строки — непустые ряды «A · B …», пустые пропускаются."""
        wb = Workbook()
        sh = wb.sheet("Спецификация")
        sh.set("A1", value=__import__("decimal").Decimal("2"))
        sh.set("B1", value="Шкаф управления")
        sh.set("A3", value="Итого")
        recalc(sh)
        pv = office_view.document_preview(write_vsheetx(wb), "sheet")
        assert pv["title"] == "Спецификация"
        assert pv["lines"][0] == "2  ·  Шкаф управления"
        assert pv["lines"][1] == "Итого" and len(pv["lines"]) == 2   # пустая строка 2 не попала

    def test_preview_doc(self):
        """Текст: заголовок документа, непустые абзацы по порядку, пустые пропущены."""
        doc = Document(title="Акт осмотра")
        doc.para("Первый абзац")
        doc.para("")
        doc.para("Второй абзац")
        pv = office_view.document_preview(write_vdocx(doc), "doc")
        assert pv["title"] == "Акт осмотра"
        assert pv["lines"] == ["Первый абзац", "Второй абзац"]

    def test_preview_caps_at_eight_lines(self):
        """Не больше PREVIEW_MAX_LINES строк — предпросмотр компактное."""
        doc = Document(title="Длинный")
        for i in range(20):
            doc.para(f"Строка {i + 1}")
        pv = office_view.document_preview(write_vdocx(doc), "doc")
        assert len(pv["lines"]) == office_view.PREVIEW_MAX_LINES


class TestSniffOfficeKind:
    """D-130: вид офисного документа определяется по СОДЕРЖИМОМУ пакета, а не по имени файла из кэша
    версий (кэш может не знать версию — тогда угадывание по расширению отдавало текст парсеру таблиц)."""

    def test_sniff_vdocx_and_vsheetx(self):
        from vector.office import sniff_office_kind
        doc = Document(title="Акт")
        doc.para("Текст")
        wb = Workbook()
        wb.sheet("Лист 1").set("A1", value=1)
        assert sniff_office_kind(write_vdocx(doc)) == "doc"
        assert sniff_office_kind(write_vsheetx(wb)) == "sheet"

    def test_sniff_rejects_garbage_and_foreign_zip(self):
        import io
        import zipfile

        from vector.office import sniff_office_kind
        assert sniff_office_kind(b"plain text") is None
        assert sniff_office_kind(b"") is None
        buf = io.BytesIO()                                   # чужой ZIP без частей ВЕКТОР
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "hi")
        assert sniff_office_kind(buf.getvalue()) is None

    def test_load_office_document_garbage_is_readable_value_error(self):
        with pytest.raises(ValueError, match="не документ ВЕКТОР"):
            office_view.load_office_document(b"plain text")


class TestOfficeLoadSlot:
    """Слот Backend.officeLoad (D-130): текстовый документ открывается как текст даже при пустом кэше
    версий; битый файл — честный отказ с понятным текстом, а не «Ошибка клиента: SheetFormatError»."""

    def _backend(self, tmp_path, data):
        pytest.importorskip("PySide6.QtCore")
        from PySide6.QtCore import QCoreApplication, QSettings

        from vector.client.backend import Backend

        class _Resp:
            status = "accepted"
            message_ru = ""

        class _FakeClient:                                   # только скачивание версии — байты задаёт тест
            def download_document_version(self, doc_id, ver, dest):
                dest.write_bytes(data)
                return _Resp()

        app = QCoreApplication.instance() or QCoreApplication([])
        be = Backend(client_factory=lambda *a: None,
                     settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                     auto_timers=False)
        be.client = _FakeClient()
        return be, app

    def _load(self, be, app):
        """Вызвать officeLoad и дождаться ответа (queryDone — успех, commandDone — отказ)."""
        import time
        got = {}
        be.queryDone.connect(lambda tag, res: got.setdefault("query", res))
        be.commandDone.connect(lambda tag, res: got.setdefault("cmd", res))
        be.officeLoad("t1", "doc-1", 1)
        deadline = time.time() + 10
        while not got and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        return got

    def test_doc_opens_as_text_with_cold_version_cache(self, tmp_path):
        doc = Document(title="Акт осмотра")
        doc.para("Осмотр выполнен.")
        be, app = self._backend(tmp_path, write_vdocx(doc))
        assert be._doc_versions == []                        # холодный кэш: имени файла нет
        got = self._load(be, app)
        assert "query" in got and "cmd" not in got, got      # успех, а не отказ
        res = got["query"]
        assert res["status"] == "accepted" and res["kind"] == "doc"
        assert res["rows"][0]["text"] == "Осмотр выполнен."

    def test_sheet_opens_as_sheet_with_cold_version_cache(self, tmp_path):
        wb = Workbook()
        wb.sheet("Спецификация").set("A1", value="Шкаф")
        be, app = self._backend(tmp_path, write_vsheetx(wb))
        got = self._load(be, app)
        assert "query" in got and got["query"]["kind"] == "sheet"
        assert got["query"]["view"]["cells"]["A1"]["text"] == "Шкаф"

    def test_broken_file_is_clean_rejection_not_client_error(self, tmp_path):
        be, app = self._backend(tmp_path, b"broken bytes")
        got = self._load(be, app)
        assert "cmd" in got and "query" not in got, got
        res = got["cmd"]
        assert res["status"] == "rejected"
        assert "не читается" in res["message_ru"] and "Ошибка клиента" not in res["message_ru"]


class TestLayoutSettings:
    """Ширина инспектора и «Сбросить раскладку» (D-135): настройка хранится в QSettings, сброс — к умолчанию."""

    def _backend(self, tmp_path):
        pytest.importorskip("PySide6.QtCore")
        from PySide6.QtCore import QCoreApplication, QSettings

        from vector.client.backend import Backend
        QCoreApplication.instance() or QCoreApplication([])
        return Backend(client_factory=lambda *a: None,
                       settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                       auto_timers=False)

    def test_inspector_width_saved_and_reset(self, tmp_path):
        be = self._backend(tmp_path)
        assert be.inspectorWidth == 0                    # по умолчанию — из плотности интерфейса
        be.inspectorWidth = 333
        assert be.inspectorWidth == 333
        be.inspectorWidth = 9000                         # за пределами допустимого — игнорируется
        assert be.inspectorWidth == 333
        # Пересозданный Backend на том же файле настроек видит сохранённую ширину.
        be2 = self._backend(tmp_path)
        assert be2.inspectorWidth == 333
        be2.resetLayout()
        assert be2.inspectorWidth == 0

    def test_copy_text_uses_clipboard(self, tmp_path):
        """copyText не падает без GUI-приложения; с QGuiApplication — пишет в буфер (полная проверка — UI-тест)."""
        pytest.importorskip("PySide6.QtGui")
        from PySide6.QtGui import QGuiApplication
        inst = QGuiApplication.instance()                # в этой сессии мог быть создан QCoreApplication
        be = self._backend(tmp_path)
        be.copyText("АТХ-15.02")
        if isinstance(inst, QGuiApplication):
            assert inst.clipboard().text() == "АТХ-15.02"
