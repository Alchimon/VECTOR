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
QML_DIR = HERE / "qml"


class IconProvider(QQuickImageProvider):
    """image://icon/<lucide-name>?c=<#rrggbb> — SVG Lucide, перекрашенный под токен темы."""

    def __init__(self):
        """Провайдер изображений QML с кэшем исходных SVG (файл читается с диска один раз)."""
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache: dict[str, bytes] = {}

    def requestImage(self, id_: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802
        """Отрисовать иконку: имя Lucide и цвет из адреса image://icon/<имя>?c=<цвет>; currentColor в SVG заменяется
        цветом токена темы. Неизвестное имя → иконка «?» (а не пустое место). Размер по умолчанию 24×24.
        """
        name, _, query = id_.partition("?")
        color = "#667085"
        for part in query.split("&"):
            if part.startswith("c="):
                color = part[2:]
        if name not in self._cache:
            p = ASSETS / "icons" / f"{name}.svg"
            self._cache[name] = p.read_bytes() if p.exists() else (ASSETS / "icons" / "circle-help.svg").read_bytes()
        svg = self._cache[name].replace(b"currentColor", QColor(color).name().encode())
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


def load_fonts() -> str:
    """Подключить встроенный шрифт Inter (одинаковый вид на всех ПК); если не загрузился — Segoe UI (системный шрифт
    Windows). Возвращает имя семейства для QML.
    """
    fid = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / "InterVariable.ttf"))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    return fams[0] if fams else "Segoe UI"


def create_engine(backend, family: str) -> QQmlApplicationEngine:
    """Создать движок QML: провайдер иконок, объект backend (вся логика клиента) и имя шрифта в контексте, загрузка
    Main.qml.
    """
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("uiFontFamily", family)
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
    app.setApplicationName("ВЕКТОР")
    app.setOrganizationName("VECTOR")
    # Значок окна и панели задач — знак «V» фирменного логотипа (D-28).
    app.setWindowIcon(QIcon(str(ASSETS / "brand" / "mark-light.png")))
    family = load_fonts()
    from .backend import Backend
    backend = Backend()
    engine = create_engine(backend, family)
    if not engine.rootObjects():
        return 1
    rc = app.exec()
    backend.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
