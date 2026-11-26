"""Точка входа настольного клиента ВЕКТОР."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QIcon, QImage, QPainter
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
VEKTOR = ASSETS / "vektor"
QML_DIR = HERE / "qml"

# Графический пак экосистемы (D-99, assets/vektor/manifest.json): нейтральные SVG со stroke="currentColor"
# (сетка 24×24, штрих 1,5). Маппинг имён Lucide → файл пака: IconProvider сначала ищет здесь, иначе — старый
# Lucide из assets/icons. Специальные префиксы:
#   image://icon/vmod/<vektor_mars>     — цветная иконка модуля (01_modules_colored, НЕ перекрашивается);
#   image://icon/vui/<05_mars_electro/fuse> — прямой путь в ui_icons с перекраской под цвет запроса.
VEKTOR_ICONS = {
    # 02_navigation — дерево, поиск, палитра, панели, настройки
    "search": "02_navigation/search", "bell": "02_navigation/bell", "settings": "02_navigation/settings",
    "user": "02_navigation/user", "x": "02_navigation/close", "folder-kanban": "02_navigation/project_tree",
    "folder": "02_navigation/folder", "file-text": "02_navigation/file", "menu": "02_navigation/menu",
    "chevron-right": "02_navigation/chevron_right", "chevron-down": "02_navigation/chevron_down",
    "house": "02_navigation/home", "sliders-horizontal": "02_navigation/dashboard",
    "panel-left": "02_navigation/sidebar_left", "panel-right": "02_navigation/sidebar_right",
    "command": "02_navigation/command_palette", "ellipsis-vertical": "02_navigation/more_vertical",
    # 03_actions — действия
    "plus": "03_actions/plus", "minus": "03_actions/minus", "save": "03_actions/save",
    "undo-2": "03_actions/undo", "rotate-ccw": "03_actions/undo", "copy": "03_actions/copy",
    "trash-2": "03_actions/trash", "pencil": "03_actions/edit", "download": "03_actions/download",
    "upload": "03_actions/upload", "printer": "03_actions/print", "refresh-cw": "03_actions/refresh",
    "filter": "03_actions/filter", "lock": "03_actions/lock", "unlock": "03_actions/unlock",
    "eye": "03_actions/eye", "eye-off": "03_actions/eye_off", "check": "03_actions/check",
    "triangle-alert": "03_actions/alert_triangle", "info": "03_actions/info", "link": "03_actions/link",
    # 04_hub_pdm — ядро/PDM
    "database": "04_hub_pdm/database", "package": "04_hub_pdm/component_box", "package-check": "04_hub_pdm/component_box",
    "cabinet": "04_hub_pdm/cabinet", "git-compare": "04_hub_pdm/git_branch", "tag": "04_hub_pdm/tag",
    "barcode": "04_hub_pdm/barcode", "clipboard-check": "04_hub_pdm/checklist", "clipboard-list": "04_hub_pdm/checklist",
    "workflow": "04_hub_pdm/workflow", "archive": "04_hub_pdm/archive", "shield-check": "04_hub_pdm/shield_check",
    "git-branch": "04_hub_pdm/git_branch",
    # 07_mercury_tables — таблицы/спецификации (ВЕКТОР Меркурий)
    "table": "07_mercury_tables/table", "calculator": "07_mercury_tables/formula_fx",
    "sigma": "07_mercury_tables/sum_sigma", "cable": "07_mercury_tables/cable_journal",
    "list-checks": "07_mercury_tables/bom_spec",
    # 08_venus_docs — документы (ВЕКТОР Венера)
    "document": "08_venus_docs/document", "file-check": "08_venus_docs/stamp_approve",
    "align-left": "08_venus_docs/align_left",
    # 06_jupiter_cad — черчение (ВЕКТОР Юпитер)
    "layers": "06_jupiter_cad/layers", "ruler": "06_jupiter_cad/measure_ruler", "move": "06_jupiter_cad/move",
}


class IconProvider(QQuickImageProvider):
    """image://icon/<lucide-name>?c=<#rrggbb> — SVG, перекрашенный под токен темы.

    Приоритет — графический пак экосистемы (assets/vektor/ui_icons, D-99): нейтральные SVG с currentColor.
    Чего в паке нет — прежние иконки Lucide (assets/icons). Неизвестное имя → иконка «?» (а не пустое место).
    """

    def __init__(self):
        """Провайдер изображений QML с кэшем исходных SVG (файл читается с диска один раз)."""
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache: dict[str, bytes] = {}

    def _source_bytes(self, name: str) -> bytes:
        """Сырые байты SVG по имени: vmod:/vui: — пак vektor; обычное имя — маппинг vektor → Lucide → «?»."""
        if name not in self._cache:
            if name.startswith("vmod/"):
                p = VEKTOR / "ui_icons" / "01_modules_colored" / f"{name[5:]}.svg"
            elif name.startswith("vui/"):
                p = VEKTOR / "ui_icons" / f"{name[4:]}.svg"
            elif name in VEKTOR_ICONS:
                p = VEKTOR / "ui_icons" / f"{VEKTOR_ICONS[name]}.svg"
            else:
                p = ASSETS / "icons" / f"{name}.svg"
            self._cache[name] = p.read_bytes() if p.exists() else (ASSETS / "icons" / "circle-help.svg").read_bytes()
        return self._cache[name]

    def requestImage(self, id_: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802
        """Отрисовать иконку: имя и цвет из адреса image://icon/<имя>?c=<цвет>; currentColor в SVG заменяется
        цветом токена темы. Цветные иконки модулей (vmod/) не перекрашиваются. Размер по умолчанию 24×24.
        """
        name, _, query = id_.partition("?")
        color = "#667085"
        for part in query.split("&"):
            if part.startswith("c="):
                color = part[2:]
        svg = self._source_bytes(name)
        if not name.startswith("vmod/"):
            svg = svg.replace(b"currentColor", QColor(color).name().encode())
        w = requested.width() if requested.width() > 0 else 24
        h = requested.height() if requested.height() > 0 else 24
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        r = QSvgRenderer(QByteArray(svg))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(painter, QRectF(0, 0, w, h))
        painter.end()
        if size is not None:
            size.setWidth(w)
            size.setHeight(h)
        return img


def load_fonts() -> tuple[str, str]:
    """Подключить встроенные шрифты (одинаковый вид на всех ПК): Inter — интерфейсный, JetBrains Mono —
    моноширинный для шифров/артикулов/кодов (дизайн-система D-99). Если не загрузились — Segoe UI / Consolas.
    Возвращает (семейство UI, семейство моно).
    """
    fid = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / "InterVariable.ttf"))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    ui_fam = fams[0] if fams else "Segoe UI"
    mono_fam = "Consolas"
    for f in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        mid = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / f))
        mfams = QFontDatabase.applicationFontFamilies(mid) if mid >= 0 else []
        if mfams:
            mono_fam = mfams[0]
    return ui_fam, mono_fam


def create_engine(backend, family: str, mono_family: str = "Consolas") -> QQmlApplicationEngine:
    """Создать движок QML: провайдер иконок, объект backend (вся логика клиента), семейства шрифтов
    (интерфейсный и моноширинный) в контексте, загрузка Main.qml.
    """
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("uiFontFamily", family)
    engine.rootContext().setContextProperty("uiMonoFamily", mono_family)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    return engine


def main(argv=None) -> int:
    """Запуск настольного клиента: стиль Qt Quick Basic (свой дизайн, а не системный), чёткое масштабирование на
    мониторах с нецелым DPI, шрифт, backend, окно. При выходе backend.shutdown() закрывает соединение и локальное
    хранилище. Код возврата 1 — QML не загрузился.
    """
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QGuiApplication(argv or sys.argv)
    app.setApplicationName("ВЕКТОР ПЛАТФОРМА")
    app.setOrganizationName("VECTOR")
    # Значок окна и панели задач — эмблема экосистемы из графического пака (D-99, vektor_platform.ico).
    ico = VEKTOR / "app_icons" / "ico" / "vektor_platform.ico"
    app.setWindowIcon(QIcon(str(ico)) if ico.exists()
                      else QIcon(str(VEKTOR / "app_icons" / "png_128" / "vektor_platform.png")))
    family, mono_family = load_fonts()
    from .backend import Backend
    backend = Backend()
    engine = create_engine(backend, family, mono_family)
    if not engine.rootObjects():
        return 1
    rc = app.exec()
    backend.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
