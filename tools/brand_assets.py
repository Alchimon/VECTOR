"""Сборка фирменных изображений клиента ВЕКТОР из исходников владельца (D-28).

Исходники (как прислал владелец, не меняются) — docs/brand/source/:
  * logo-2_blueprint_business.png — логотип на тёплом светлом фоне;
  * background.jpg — фоновая «чертёжная» композиция в пастельных тонах.

Результат — src/vector/client/assets/brand/ (входит в пакет клиента):
  * logo-light.png  — логотип с прозрачным фоном (для светлой темы);
  * logo-dark.png   — то же, но тёмные линии и надпись осветлены (для тёмной темы), пастельные цвета знака
                      сохранены;
  * mark-light.png, mark-dark.png — только знак «V» (без надписи), квадрат 256×256: боковая панель, значок окна;
  * bg-light.jpg    — фон без изменений (копия, 1024×572);
  * bg-dark.jpg     — фон для тёмной темы: светлота инвертирована при сохранённом оттенке, поэтому лаванда
                      остаётся лавандой, а белая «бумага» становится тёмным сланцем.

Почему отдельный скрипт, а не ручная обработка: изображения воспроизводимы (тот же исходник → тот же результат),
а при новом логотипе достаточно заменить исходник и запустить скрипт. Обработка — средствами Qt (QImage), без
сторонних библиотек: PySide6 и так нужен клиенту.

Запуск (из корня репозитория; на Linux без GPU — с заглушками OpenGL, см. tools/qt_offscreen_stubs.py):
    LD_LIBRARY_PATH=.venv/stubgl QT_QPA_PLATFORM=offscreen python tools/brand_assets.py
"""
from __future__ import annotations

import colorsys
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "brand" / "source"
OUT = ROOT / "src" / "vector" / "client" / "assets" / "brand"

# Цвет «бумаги» логотипа (угол исходника) — его делаем прозрачным.
LOGO_PAPER = (245, 246, 241)
# Цвет, которым в тёмном варианте рисуются тёмные нейтральные линии и надпись (= Theme.text тёмной темы).
DARK_INK = (230, 235, 240)
# Непрозрачность чернил надписи после color_to_alpha: (245 − 72) / 245 ≈ 0,71 для #485460 на бумаге #F5F6F1.
INK_ALPHA = 0.71
# Порог «это ещё бумага»: у исходника лёгкий шум и светлая дымка вокруг надписи (отличие до ~6 %). Всё, что
# слабее порога, считается бумагой; непрозрачность остальных пикселей растягивается на 0…1.
PAPER_TOLERANCE = 0.07
# Знак «V» занимает левую часть логотипа; надпись начинается правее этой границы (по исходнику 688×384).
MARK_MAX_X = 325


def _rgba(img: QImage) -> QImage:
    """Изображение в формате RGBA8888 (байты R, G, B, A подряд) — удобно обрабатывать как bytearray."""
    return img.convertToFormat(QImage.Format.Format_RGBA8888)


def _bytes(img: QImage) -> bytearray:
    """Копия пикселей изображения (RGBA8888, строки без выравнивания — ширина×4 байта)."""
    assert img.bytesPerLine() == img.width() * 4, "ожидаются строки без выравнивания"
    return bytearray(bytes(img.constBits()))


def _image(buf: bytearray, w: int, h: int) -> QImage:
    """QImage из буфера RGBA8888 (копия: буфер Python может быть освобождён)."""
    return QImage(bytes(buf), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


def color_to_alpha(buf: bytearray, paper: tuple[int, int, int]) -> None:
    """Сделать цвет «бумаги» прозрачным с сохранением сглаженных краёв (алгоритм «color to alpha», как в GIMP).

    Для каждого пикселя берётся наименьшая непрозрачность a, при которой он получается наложением цвета fg на
    бумагу: c = a·fg + (1 − a)·paper. Затем восстанавливается fg. Чистая бумага → полностью прозрачна,
    полупрозрачный край линии → полупрозрачная линия того же цвета (без светлого ореола на тёмном фоне).
    """
    for i in range(0, len(buf), 4):
        a = 0.0
        for k in range(3):
            c, p = buf[i + k], paper[k]
            # Учитываются только каналы ТЕМНЕЕ бумаги: светлее бумаги в исходнике лишь шум (до #F8F9F3), а при
            # «бумаге» #F5F6F1 даже +3 давали бы непрозрачность 0,3 — по краям появились бы пятна.
            if c < p:
                a = max(a, (p - c) / p)
        a = (a - PAPER_TOLERANCE) / (1 - PAPER_TOLERANCE)
        if a <= 0.0:                       # шум и дымка бумаги — полностью прозрачно
            buf[i:i + 4] = b"\x00\x00\x00\x00"
            continue
        for k in range(3):
            fg = paper[k] + (buf[i + k] - paper[k]) / a
            buf[i + k] = max(0, min(255, round(fg)))
        buf[i + 3] = round(min(1.0, a) * 255)


def paper_alpha(buf: bytearray, i: int, paper: tuple[int, int, int]) -> float:
    """«Сколько не-бумаги» в пикселе: 0 — чистая бумага, 1 — чёрный (только каналы темнее бумаги)."""
    return max(((p - c) / p if c < p else 0.0) for c, p in zip(buf[i:i + 3], paper))


def matte_emblem(buf: bytearray, w: int, h: int, paper: tuple[int, int, int], max_x: int) -> None:
    """Жёсткая маска знака «V» (полоса x < max_x) поверх результата color_to_alpha.

    color_to_alpha делает светлые пастельные заливки знака полупрозрачными — на тёмном фоне они тускнеют.
    Поэтому для знака: прозрачна только бумага, СВЯЗАННАЯ С КРАЕМ изображения (заливка от границ по «почти
    бумаге»); всё внутри знака — непрозрачно и исходного цвета (пастель остаётся пастелью, белый центр шестерни —
    белым). Мягкий край — только у пикселей, соседних с внешней бумагой.
    """
    src = bytes(_ORIGINAL[0])
    paperish = [paper_alpha(src, (y * w + x) * 4, paper) < 0.10 for y in range(h) for x in range(w)]
    outside = bytearray(w * h)
    stack = [y * w + x for y in range(h) for x in (0, min(max_x, w) - 1)] + \
            [y * w + x for x in range(min(max_x, w)) for y in (0, h - 1)]
    while stack:
        k = stack.pop()
        if outside[k] or not paperish[k]:
            continue
        outside[k] = 1
        x, y = k % w, k // w
        if x > 0:
            stack.append(k - 1)
        if x < max_x - 1:
            stack.append(k + 1)
        if y > 0:
            stack.append(k - w)
        if y < h - 1:
            stack.append(k + w)
    for y in range(h):
        for x in range(min(max_x, w)):
            k = y * w + x
            i = k * 4
            if outside[k]:
                buf[i:i + 4] = b"\x00\x00\x00\x00"
                continue
            edge = any(outside[(y + dy) * w + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                       if 0 <= y + dy < h and 0 <= x + dx < max_x)
            if edge:
                continue                       # край: оставляем мягкую альфу из color_to_alpha
            buf[i:i + 3] = src[i:i + 3]
            buf[i + 3] = 255


# Исходные пиксели логотипа (до color_to_alpha) — нужны matte_emblem; заполняет main().
_ORIGINAL: list = []


def crop_to_content(img: QImage, pad: int = 8) -> QImage:
    """Обрезать прозрачные поля вокруг логотипа (с полем pad), чтобы в интерфейсе он вписывался по содержимому."""
    w, h = img.width(), img.height()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() > 40:
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    r = QRect(max(0, x0 - pad), max(0, y0 - pad), 0, 0)
    r.setRight(min(w - 1, x1 + pad))
    r.setBottom(min(h - 1, y1 + pad))
    return img.copy(r)


def lighten_ink(buf: bytearray) -> None:
    """Тёмный вариант логотипа: тёмные НЕЙТРАЛЬНЫЕ пиксели (надпись, контуры, линии сетки) → светлые чернила.

    Цветные пастельные заливки знака не трогаем — по ним логотип узнаётся. Критерий «нейтральный тёмный»:
    низкая насыщенность (разброс каналов < 48) и тёмный тон (максимум канала < 150).
    """
    for i in range(0, len(buf), 4):
        if buf[i + 3] == 0:
            continue
        r, g, b = buf[i], buf[i + 1], buf[i + 2]
        if max(r, g, b) < 150 and max(r, g, b) - min(r, g, b) < 48:
            buf[i], buf[i + 1], buf[i + 2] = DARK_INK
            # Сланцевые чернила исходника (≈ #485460) после color_to_alpha — «чёрный с непрозрачностью ~0,71».
            # Светлые чернила с той же прозрачностью выглядели бы серыми: возвращаем полную непрозрачность.
            buf[i + 3] = min(255, round(buf[i + 3] / INK_ALPHA))


def dark_background(buf: bytearray) -> None:
    """Фон для тёмной темы: светлота L → 0.075 + (1 − L)·0.62 при том же оттенке; насыщенность слегка снижена.

    Белая бумага (L≈0.97) → тёмный сланец (≈ Theme.bg тёмной темы), пастельные плашки (L≈0.85) → приглушённые
    тёмные тех же оттенков, тонкие серые линии (L≈0.7) → чуть светлее фона: «чертёж» читается, но не спорит
    с содержимым.
    """
    for i in range(0, len(buf), 4):
        r, g, b = buf[i] / 255, buf[i + 1] / 255, buf[i + 2] / 255
        h, light, s = colorsys.rgb_to_hls(r, g, b)
        nl = 0.075 + (1.0 - light) * 0.62
        ns = s * 0.75 if s > 0.08 else 0.18           # почти серое → лёгкий холодный оттенок фона
        if s <= 0.08:
            h = 0.6                                     # сланцево-синий оттенок, как у Theme.bg
        nr, ng, nb = colorsys.hls_to_rgb(h, nl, ns)
        buf[i], buf[i + 1], buf[i + 2] = round(nr * 255), round(ng * 255), round(nb * 255)


def bbox(buf: bytearray, w: int, h: int, max_x: int) -> QRect:
    """Прямоугольник заметно непрозрачных пикселей (alpha > 64) в полосе x < max_x — границы знака «V»."""
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * w * 4
        for x in range(min(max_x, w)):
            if buf[row + x * 4 + 3] > 64:
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    return QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def square(img: QImage, rect: QRect, size: int = 256, pad: float = 0.06) -> QImage:
    """Вырезать rect и вписать по центру в прозрачный квадрат size×size с полями pad (доля стороны)."""
    out = QImage(size, size, QImage.Format.Format_RGBA8888)
    out.fill(Qt.GlobalColor.transparent)
    part = img.copy(rect)
    inner = round(size * (1 - 2 * pad))
    part = part.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage((size - part.width()) // 2, (size - part.height()) // 2, part)
    p.end()
    return out


def main() -> int:
    """Построить все производные изображения и вывести их список с размерами."""
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 — QImage/QPainter требуют приложение
    OUT.mkdir(parents=True, exist_ok=True)
    logo = _rgba(QImage(str(SRC / "logo-2_blueprint_business.png")))
    if logo.isNull():
        print("Нет исходника логотипа в docs/brand/source/", file=sys.stderr)
        return 1
    w, h = logo.width(), logo.height()
    light = _bytes(logo)
    _ORIGINAL[:] = [bytes(light)]
    color_to_alpha(light, LOGO_PAPER)
    matte_emblem(light, w, h, LOGO_PAPER, MARK_MAX_X)
    dark = bytearray(light)
    lighten_ink(dark)
    li, di = _image(light, w, h), _image(dark, w, h)
    crop_to_content(li).save(str(OUT / "logo-light.png"))
    crop_to_content(di).save(str(OUT / "logo-dark.png"))
    mark = bbox(light, w, h, MARK_MAX_X)
    square(li, mark).save(str(OUT / "mark-light.png"))
    square(di, mark).save(str(OUT / "mark-dark.png"))

    bg = _rgba(QImage(str(SRC / "background.jpg")))
    bg.convertToFormat(QImage.Format.Format_RGB888).save(str(OUT / "bg-light.jpg"), "JPG", 88)
    bw, bh = bg.width(), bg.height()
    bb = _bytes(bg)
    dark_background(bb)
    _image(bb, bw, bh).convertToFormat(QImage.Format.Format_RGB888).save(str(OUT / "bg-dark.jpg"), "JPG", 88)
    for f in sorted(OUT.iterdir()):
        im = QImage(str(f))
        print(f"{f.relative_to(ROOT)}  {im.width()}×{im.height()}  {f.stat().st_size // 1024} КБ")
    print(f"Знак «V» в исходнике: x={mark.x()} y={mark.y()} {mark.width()}×{mark.height()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
