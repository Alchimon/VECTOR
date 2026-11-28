"""Офисные документы ВЕКТОР (фаза Ф1, D-83): модель таблицы/текста, свои форматы .vsheetx/.vdocx (ZIP+XML,
надмножество OOXML), импорт/экспорт OOXML без потерь. Чистое ядро без Qt и БД — см. 55-office-documents.md."""
from __future__ import annotations


def sniff_office_kind(data: bytes) -> str | None:
    """Вид офисного документа ВЕКТОР по СОДЕРЖИМОМУ пакета: «doc» (.vdocx — часть document.xml),
    «sheet» (.vsheetx — часть workbook.xml); None — не ZIP или чужой ZIP.

    Определение по байтам, а не по имени файла: имя приходит из клиентского кэша версий, который может
    не знать этот файл (холодный кэш, версия вне снимка) — тогда угадывание по расширению ошибочно
    отдавало текстовый документ парсеру таблиц (SheetFormatError вместо открытия, D-130).
    """
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "document.xml" in names:
        return "doc"
    if "workbook.xml" in names:
        return "sheet"
    return None
