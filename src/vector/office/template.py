"""Поля-подстановки в .vdocx (Ф1.3-ядро, 55-office-documents.md § 3): «документ = база данных» на практике.

Шаблон акта/спецификации содержит {{путь.к.полю}} — сервер подставляет значения из объектов платформы
(проект, заказчик, реквизиты, дата) и выдаёт готовый документ новой версией. Синтаксис {{field}} — тот же,
что у настраиваемых форм доставки (doc_template), чтобы пользователь учил один язык.

Ядро чистое (без сервера): контекст — вложенный словарь; подстановка во всех прогонах абзацев, таблиц
(включая шапки), с сохранением начертания. Неизвестные поля НЕ затираются молча: маркер остаётся в тексте,
а список неподставленных возвращается — пользователь видит, что шаблон отстал от данных.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .model_doc import Document, Paragraph, TableBlock
from .vdocx import read_vdocx, write_vdocx

_FIELD_RE = re.compile(r"\{\{\s*([A-Za-zА-Яа-яЁё_][\w.]*)\s*\}\}")


def lookup(context: dict, path: str) -> Optional[str]:
    """Значение поля по пути «a.b.c» из вложенного контекста; None — поля нет (маркер останется в тексте)."""
    node: Any = context
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None:
        return None
    if isinstance(node, bool):
        return "да" if node else "нет"
    return str(node)


def render(doc: Document, context: dict) -> list[str]:
    """Подставить поля в документ (на месте). Возвращает отсортированный список неподставленных полей."""
    missing: set[str] = set()

    def subst(text: str) -> str:
        def rep(m: re.Match) -> str:
            v = lookup(context, m.group(1))
            if v is None:
                missing.add(m.group(1))
                return m.group(0)                    # маркер остаётся — видно, что не подставилось
            return v
        return _FIELD_RE.sub(rep, text)

    def walk_para(p: Paragraph) -> None:
        for r in p.runs:
            r.text = subst(r.text)

    for b in doc.blocks:
        if isinstance(b, Paragraph):
            walk_para(b)
        elif isinstance(b, TableBlock):
            for row in b.rows:
                for cell in row:
                    for p in cell:
                        walk_para(p)
    if doc.title:
        doc.title = subst(doc.title)
    return sorted(missing)


def render_template(template_bytes: bytes, context: dict) -> tuple[bytes, list[str]]:
    """Шаблон .vdocx (байты) + контекст → (готовый .vdocx байты, неподставленные поля)."""
    doc = read_vdocx(template_bytes)
    missing = render(doc, context)
    return write_vdocx(doc), missing
