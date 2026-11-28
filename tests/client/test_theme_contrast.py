"""Контраст цветов темы (WCAG 2.1 AA) — читаемость в светлой и тёмной темах после смены палитры (D-28).

Токены читаются прямо из qml/Theme.qml (строки вида `readonly property color имя: dark ? "#тёмный" : "#светлый"`),
поэтому тест ломается при любой правке палитры, ухудшающей читаемость. Пороги:
* основной и вторичный текст (text, textMuted) на всех поверхностях — не ниже 4,5:1;
* бледные подписи (textFaint) — не ниже 3:1 (вспомогательный текст, не основной);
* основной цвет на поверхности и текст на кнопке основного цвета — 4,5:1;
* цвет каждого тона (успех, предупреждение, ошибка, информация) на своей подложке и на карточке — 4,5:1.
Фирменные изображения графического пака (assets/vektor) тоже проверяются: файлы на месте, логотип прозрачен по краям.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "src" / "vector" / "client" / "qml" / "Theme.qml"


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
    """Фирменные изображения — графический пак экосистемы (D-99, assets/vektor): логотипы, фон, значок окна."""
    pytest.importorskip("PySide6.QtGui", reason="нет GUI-стека (минимальная машина тестирования)")
    from PySide6.QtGui import QImage
    pack = ROOT / "src" / "vector" / "client" / "assets" / "vektor"
    for name in ("app_icons/logo/vektor_platform_horizontal.png", "app_icons/logo/vektor_emblem_only.png",
                 "app_icons/png_64/vektor_platform.png", "app_icons/ico/vektor_platform.ico",
                 "backgrounds/space.jpg"):
        img = QImage(str(pack / name))
        assert not img.isNull(), name
    for name in ("app_icons/logo/vektor_platform_horizontal.png", "app_icons/logo/vektor_emblem_only.png"):
        img = QImage(str(pack / name))
        assert img.hasAlphaChannel() and img.pixelColor(0, 0).alpha() == 0, f"{name}: фон не прозрачен"


def test_client_asset_pack_matches_repo_pack():
    """Графический пак клиента (src/vector/client/assets/vektor/app_icons) — точная копия исходного пака
    assets/vektor/app_icons (D-99): значки всех модулей и размеров, включая ВЕКТОР Орбита (vektor_orbita).
    Рассинхронизация = в клиенте пропадут новые значки."""
    src = ROOT / "assets" / "vektor" / "app_icons"
    dst = ROOT / "src" / "vector" / "client" / "assets" / "vektor" / "app_icons"
    src_files = {p.relative_to(src): p.read_bytes() for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst): p.read_bytes() for p in dst.rglob("*") if p.is_file()}
    assert src_files.keys() == dst_files.keys(), (
        f"только в исходном паке: {sorted(src_files.keys() - dst_files.keys())}, "
        f"только в клиентском: {sorted(dst_files.keys() - src_files.keys())}")
    diff = [str(k) for k in src_files if src_files[k] != dst_files[k]]
    assert not diff, "расходятся файлы: " + ", ".join(diff)


def test_orbita_icons_exist():
    """ВЕКТОР Орбита (сервер экосистемы, D-131): значок в клиентском паке — все размеры + .ico."""
    pack = ROOT / "src" / "vector" / "client" / "assets" / "vektor" / "app_icons"
    for name in ("ico/vektor_orbita.ico", "png_64/vektor_orbita.png", "png_128/vektor_orbita.png",
                 "png_256/vektor_orbita.png", "png_512/vektor_orbita.png"):
        assert (pack / name).exists() and (pack / name).stat().st_size > 0, name
