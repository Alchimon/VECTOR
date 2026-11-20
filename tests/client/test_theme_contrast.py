"""Контраст цветов темы (WCAG 2.1 AA) — читаемость в светлой и тёмной темах после смены палитры (D-28).

Токены читаются прямо из qml/Theme.qml (строки вида `readonly property color имя: dark ? "#тёмный" : "#светлый"`),
поэтому тест ломается при любой правке палитры, ухудшающей читаемость. Пороги:
* основной и вторичный текст (text, textMuted) на всех поверхностях — не ниже 4,5:1;
* бледные подписи (textFaint) — не ниже 3:1 (вспомогательный текст, не основной);
* основной цвет на поверхности и текст на кнопке основного цвета — 4,5:1;
* цвет каждого тона (успех, предупреждение, ошибка, информация) на своей подложке и на карточке — 4,5:1.
Фирменные изображения тоже проверяются: файлы на месте, логотип прозрачен по краям.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "src" / "vector" / "client" / "qml" / "Theme.qml"
BRAND = ROOT / "src" / "vector" / "client" / "assets" / "brand"


def _tokens() -> dict:
    """{"light": {имя: "#RRGGBB"}, "dark": {...}} из Theme.qml."""
    out = {"light": {}, "dark": {}}
    for name, dark, light in re.findall(r'property color (\w+):\s*dark \? "(#[0-9A-Fa-f]{6,8})" : "(#[0-9A-Fa-f]{6,8})"',
                                        THEME.read_text(encoding="utf-8")):
        out["dark"][name], out["light"][name] = dark, light
    return out


def _lum(h: str) -> float:
    """Относительная яркость цвета по WCAG 2.1 (альфа-канал #AARRGGBB игнорируется — берутся последние 6 цифр)."""
    h = h.lstrip("#")[-6:]
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    """Коэффициент контраста двух цветов (1…21)."""
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


PAIRS = ([(f, b, 4.5) for f in ("text", "textMuted") for b in ("bg", "sidebar", "surface", "surfaceAlt", "hover")]
         + [("textFaint", b, 3.0) for b in ("bg", "sidebar", "surface")]
         + [("primary", "surface", 4.5), ("primary", "bg", 4.5), ("primary", "primarySoft", 4.5),
            ("textOnPrimary", "primary", 4.5), ("textMuted", "neutralSoft", 4.5), ("accent", "surface", 4.5)]
         + [(t, s, 4.5) for t in ("success", "warning", "danger", "info") for s in (t + "Soft", "surface")])


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_theme_tokens_meet_wcag_aa(theme):
    t = _tokens()[theme]
    assert len(t) >= 25, "Theme.qml не разобран — изменился формат строк с цветами?"
    bad = [f"{f} на {b}: {contrast(t[f], t[b]):.2f} < {m}" for f, b, m in PAIRS if contrast(t[f], t[b]) < m]
    assert not bad, f"{theme}: недостаточный контраст — " + "; ".join(bad)


def test_screens_have_no_hardcoded_colors():
    """Цвета задаются только токенами Theme.qml — иначе тёмная тема и смена палитры «забывают» часть экранов."""
    offenders = [f"{p.name}:{n}" for p in THEME.parent.glob("*.qml") if p.name != "Theme.qml"
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if re.search(r'"#[0-9A-Fa-f]{6,8}"', line)]
    assert not offenders, "Жёстко заданные цвета: " + ", ".join(offenders)


def test_brand_images_exist_and_logo_is_transparent():
    from PySide6.QtGui import QImage
    for name in ("logo-light.png", "logo-dark.png", "mark-light.png", "mark-dark.png", "bg-light.jpg", "bg-dark.jpg"):
        img = QImage(str(BRAND / name))
        assert not img.isNull(), name
    for name in ("logo-light.png", "logo-dark.png", "mark-light.png"):
        img = QImage(str(BRAND / name))
        assert img.hasAlphaChannel() and img.pixelColor(0, 0).alpha() == 0, f"{name}: фон не прозрачен"
