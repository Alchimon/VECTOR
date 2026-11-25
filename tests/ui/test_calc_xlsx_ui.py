"""UI-проверки обмена расчётом с Excel (.xlsx, R-31): экспорт текущего черновика в файл,
импорт с предпросмотром (только после подтверждения), отказ «всё или ничего» при замечаниях."""
import pytest
from PySide6.QtCore import QMetaObject, Q_ARG

from vector.core import calc, xlsx

from .test_calc_ui import _calc, _popup, _v  # noqa: F811 — общие помощники стенда

pytestmark = pytest.mark.ui


def _arg(v):
    return Q_ARG("QVariant", v)


def _editor(ui):
    """Открыть редактор расчёта комплекта АТХ поверх вкладки «Расчёт»."""
    kc = _calc(ui)
    ed = _popup(ui, "calcEditor")
    QMetaObject.invokeMethod(ed, "openFor", _arg(_v(kc, "kit")), _arg(_v(kc, "v")))
    ui.wait_idle()
    return ed, _v(kc, "v")


def _wait_dialog(ui, name="calcXlsxPreview", timeout=6.0):
    """Дождаться открытия диалога предпросмотра (импорт идёт в рабочем потоке backend)."""
    dlg = _popup(ui, name)
    for _ in range(60):
        ui.pump(timeout / 60)
        if dlg.property("opened"):
            return dlg
    raise AssertionError(f"диалог {name} не открылся")


def test_export_then_import_preview_and_confirm(ui, tmp_path):
    ed, v = _editor(ui)
    lines = v["lines"]
    dest = tmp_path / "расчёт АТХ"                      # без расширения — backend допишет .xlsx
    ui.backend.exportCalcXlsx("t", str(dest), lines)
    out = tmp_path / "расчёт АТХ.xlsx"
    for _ in range(60):
        ui.pump(0.1)
        if out.exists():
            break
    assert out.exists() and out.stat().st_size > 400

    before = len(_v(ed, "pv")["rows"])
    ui.backend.importCalcXlsx("calc-xlsx-import", str(out))
    dlg = _wait_dialog(ui)
    assert _v(dlg, "canConfirm") is True
    assert len(_v(dlg, "rows")) == len(lines)           # круговой обход без потерь
    assert "Позиций:" in _v(dlg, "subtitle")
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert len(_v(ed, "pv")["rows"]) == before + len(lines)   # позиции добавлены в конец
    assert dlg.property("opened") in (False, None) or not dlg.property("visible")


def test_import_rejected_shows_problems_and_adds_nothing(ui, tmp_path):
    ed, v = _editor(ui)
    before = len(_v(ed, "pv")["rows"])
    bad = tmp_path / "bad.xlsx"
    xlsx.atomic_write(bad, xlsx.write_rows(calc.SHEET_HEADER and [
        calc.SHEET_HEADER,
        ["КБЛ", "Кабель", "м", "120.5", "99.99", ""],
        ["", "Без количества", "шт", "", "10", ""],     # строка листа 3
        ["", "Ноль", "шт", "0", "", ""],                # строка листа 4
    ]))
    ui.backend.importCalcXlsx("calc-xlsx-import", str(bad))
    dlg = _wait_dialog(ui)
    problems = _v(dlg, "problems")
    assert _v(dlg, "canConfirm") is False               # кнопка добавления недоступна
    assert {p["line"] for p in problems} == {3, 4}      # номера строк листа Excel
    assert "не добавлены" in _v(dlg, "subtitle")
    QMetaObject.invokeMethod(dlg, "dismissed")          # закрыть без импорта
    ui.wait_idle()
    assert len(_v(ed, "pv")["rows"]) == before          # в редакторе ничего не изменилось


def test_import_broken_file_shows_error(ui, tmp_path):
    ed, _ = _editor(ui)
    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"definitely not a zip")
    ui.backend.importCalcXlsx("calc-xlsx-import", str(junk))
    ui.pump(2.0)
    for _ in range(40):
        ui.pump(0.1)
        if _v(ed, "error"):
            break
    assert "не является книгой" in _v(ed, "error")
