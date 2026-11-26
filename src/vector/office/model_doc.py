"""Модель текстового документа ВЕКТОР (фаза Ф1.2, 55-office-documents.md § 3): документ → блоки → прогоны.

Чистое ядро без Qt и БД. Блоки первой порции: абзац, заголовок (уровни 1–4), список (маркированный/
нумерованный), таблица (строки × ячейки-текст — для актов и спецификаций). Прогон — текст с начертанием.
Поля-подстановки ({object.field}) — связь с объектами платформы («документ = база данных»): подстановку
делает сервер при формировании акта/спецификации (порция Ф1.3), модель их просто хранит как текст.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Run:
    """Прогон — кусок текста с единым начертанием."""
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size: float = 12.0                        # pt
    font: str = ""                                 # "" — шрифт по умолчанию документа


@dataclass
class Paragraph:
    """Абзац: прогоны + выравнивание. Заголовок — Paragraph со style='heading', level 1–4."""
    runs: list[Run] = field(default_factory=list)
    align: str = "left"                            # left | center | right | justify
    style: str = "normal"                          # normal | heading | list-bullet | list-number
    level: int = 1                                 # для heading — уровень; для list-* — вложенность

    @property
    def text(self) -> str:
        """Весь текст абзаца (прогоны склеены) — для поиска и тестов."""
        return "".join(r.text for r in self.runs)

    def add(self, text: str, **kw) -> Run:
        """Добавить прогон. Пустой текст не добавляется (не плодим мусор)."""
        if text:
            self.runs.append(Run(text, **kw))
        return self.runs[-1] if self.runs else Run("")


@dataclass
class TableBlock:
    """Таблица в тексте: строки → ячейки (в ячейке — абзацы; первая строка может быть шапкой)."""
    rows: list[list[list[Paragraph]]] = field(default_factory=list)   # row → cell → paragraphs
    header: bool = False

    def cell_text(self, r: int, c: int) -> str:
        return "\n".join(p.text for p in self.rows[r][c])


# Блок документа — абзац или таблица.
Block = object                                     # Paragraph | TableBlock (typing.Union в аннотациях мешает dataclass)


@dataclass
class Document:
    """Текстовый документ: блоки по порядку, название (для списка документов), неизвестные части OOXML
    для round-trip (как у таблиц): изображения, SmartArt, колонтитулы из .docx возвращаются при экспорте."""
    blocks: list = field(default_factory=list)
    title: str = ""
    unknown_parts: dict[str, bytes] = field(default_factory=dict)
    format_version: int = 1

    def para(self, text: str = "", style: str = "normal", level: int = 1, align: str = "left") -> Paragraph:
        """Добавить абзац (text — первым прогоном)."""
        p = Paragraph(style=style, level=level, align=align)
        if text:
            p.add(text)
        self.blocks.append(p)
        return p

    def table(self, rows: int, cols: int, header: bool = False) -> TableBlock:
        """Добавить пустую таблицу rows×cols."""
        t = TableBlock(rows=[[[Paragraph()] for _ in range(cols)] for _ in range(rows)], header=header)
        self.blocks.append(t)
        return t

    @property
    def text(self) -> str:
        """Весь текст документа (для поиска/проверок)."""
        parts = []
        for b in self.blocks:
            if isinstance(b, Paragraph):
                parts.append(b.text)
            else:
                parts.extend(b.cell_text(r, c) for r in range(len(b.rows)) for c in range(len(b.rows[r])))
        return "\n".join(p for p in parts if p)
