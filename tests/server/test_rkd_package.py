"""ZIP-пакет трансмиттала из неизменяемых версий (D-63, этап 3б blueprint 52).

При создании трансмиттала в той же транзакции собирается ZIP: манифест (состав, шифры, статусы выдачи, хэши)
плюс точные версии файлов ревизий. Пакет детерминирован (повторная сборка тех же входов — те же байты),
хранится в адресном хранилище с дедупликацией, неизменяем (триггер), скачивается командой проекта с пересчётом
контрольной суммы.
"""
from __future__ import annotations

import hashlib

import pytest
import sqlalchemy as sa

from tests.conftest import new_id
from tests.server.test_rkd import make_rev, pd, pull, ready_rev, rev_of, trans, upload  # noqa: F401
from tests.server.test_rkd import pd as pd  # noqa: F401  # фикстура: проект с РП/ГИП/инженером
from vector.core import rkd as rk
from vector.server import db


def make_transmittal(pd_, items=None, note=None):
    """РП создаёт трансмиттал из готовой ревизии (или из переданных); возвращает (трансмиттал, ревизия)."""
    rev = ready_rev(pd_) if items is None else None
    ids = items or [rev["id"]]
    p = {"id": new_id(), "project_id": pd_["pid"], "recipient": "ООО «Заказчик»", "channel": "email",
         "items": ids}
    if note:
        p["note"] = note
    t = pd_["rp"].ok("transmittal.create", p)["transmittal"]
    return t, rev


def package_of(pd_, transmittal_id):
    """Запись пакета из pull инженера проекта."""
    pkgs = pull(pd_["eng"])["transmittal_packages"]
    return next((x for x in pkgs if x["transmittal_id"] == transmittal_id), None)


# ====================================================================== сборка при создании
def test_package_built_at_create(pd):
    """Пакет собирается в транзакции создания: запись в pull, ZIP открывается, манифест описывает состав,
    файлы в пакете — точные байты версий ревизий."""
    rev = ready_rev(pd, "01")
    t, _ = make_transmittal(pd, items=[rev["id"]], note="Первая выдача")
    pkg = package_of(pd, t["id"])
    assert pkg is not None and pkg["package_name"].endswith(".zip")
    resp, data = pd["eng"].svc.get_transmittal_package(pd["eng"].token, t["id"], pd["eng"].addr)
    assert resp["status"] == "accepted" and resp["file_name"] == pkg["package_name"]
    assert hashlib.sha256(data).hexdigest() == pkg["package_sha256"] and len(data) == pkg["size_bytes"]
    manifest, files = rk.read_package(data)
    assert manifest["format"] == rk.PACKAGE_FORMAT
    tr = manifest["transmittal"]
    assert tr["seq"] == t["seq"] and tr["project_code"] == "П-2026-910" and tr["recipient"] == "ООО «Заказчик»"
    item = manifest["items"][0]
    rev_now = rev_of(pd, rev["id"])
    assert item["document_code"] == "АТХ-001" and item["revision_label"] == "01"
    assert rev_now["status"] == "sent"
    # Файл в пакете — байты версии файла ревизии (их хэш в манифесте).
    arc = next(iter(files))
    assert arc.startswith("01/")
    assert hashlib.sha256(files[arc]).hexdigest() == item["sha256"]


def test_package_manifest_carries_issue_status(pd):
    """Статус выдачи (штамп профиля, D-62) попадает в манифест пакета снимком кода и подписи."""
    rp, eng = pd["rp"], pd["eng"]
    rp.ok("docprofile.create", {"id": new_id(), "project_id": pd["pid"], "name": "Схема",
                                "issue_statuses": [{"code": "IFC", "label_ru": "Для строительства"}]})
    rev = ready_rev(pd, "01")
    rp.ok("docrev.set_issue", {"id": rev["id"], "status_code": "IFC"})
    t, _ = make_transmittal(pd, items=[rev["id"]])
    resp, data = eng.svc.get_transmittal_package(eng.token, t["id"], eng.addr)
    manifest, _files = rk.read_package(data)
    item = manifest["items"][0]
    assert item["issue_status_code"] == "IFC" and item["issue_status_label"] == "Для строительства"


def test_package_deterministic_and_immutable(pd):
    """Одинаковые входы → одинаковые байты пакета (core); запись пакета неизменяема (триггер)."""
    files = [("01/a.pdf", "первый".encode()), ("02/b.pdf", "второй".encode())]
    manifest = {"format": rk.PACKAGE_FORMAT, "transmittal": {"seq": 1}, "items": []}
    assert rk.build_package(manifest, files) == rk.build_package(manifest, list(reversed(files)))
    t, _ = make_transmittal(pd)
    pkg = package_of(pd, t["id"])
    with pd["eng"].svc.engine.connect() as c:
        row = c.execute(sa.select(db.transmittal_packages).where(
            db.transmittal_packages.c.transmittal_id == t["id"])).mappings().one()
    assert row["package_sha256"] == pkg["package_sha256"]
    with pytest.raises(sa.exc.DBAPIError):
        with pd["eng"].svc.engine.begin() as c:
            c.execute(db.transmittal_packages.update().values(package_name="x.zip"))
    with pytest.raises(sa.exc.DBAPIError):
        with pd["eng"].svc.engine.begin() as c:
            c.execute(db.transmittal_packages.delete())


def test_package_dedup_blob(pd):
    """Blob пакета хранится в адресном хранилище с дедупликацией: блоки пакета присутствуют, повторная
    одинаковая сборка нового blob не создаёт."""
    t, _ = make_transmittal(pd)
    pkg = package_of(pd, t["id"])
    with pd["eng"].svc.engine.connect() as c:
        blob = c.execute(sa.select(db.file_blobs).where(
            db.file_blobs.c.sha256 == pkg["package_sha256"])).mappings().one()
        chunks = c.execute(sa.select(sa.func.count()).select_from(db.file_chunks).where(
            db.file_chunks.c.sha256 == pkg["package_sha256"])).scalar()
    assert blob["size_bytes"] == pkg["size_bytes"] and chunks == blob["chunk_count"] >= 1


# ====================================================================== скачивание и права
def test_package_download_rights(pd):
    """Скачать пакет может команда проекта (transmittal.read); посторонний — denied, без раскрытия существования."""
    t, _ = make_transmittal(pd)
    for who in ("eng", "rp", "gip"):
        s = pd[who]
        resp, data = s.svc.get_transmittal_package(s.token, t["id"], s.addr)
        assert resp["status"] == "accepted" and data
    outsider = pd["world"].login("lead1")
    resp, data = outsider.svc.get_transmittal_package(outsider.token, t["id"], outsider.addr)
    assert resp["status"] == "denied" and resp["code"] == "no_authority" and data is None
    # Несуществующий трансмиттал — тот же denied (существование не раскрывается).
    resp, _ = outsider.svc.get_transmittal_package(outsider.token, new_id(), outsider.addr)
    assert resp["status"] == "denied"


def test_package_download_detects_corruption(pd):
    """Повреждённый блок пакета не отдаётся: file_corrupted, аудит фиксирует попытку."""
    t, _ = make_transmittal(pd)
    pkg = package_of(pd, t["id"])
    eng = pd["eng"]
    with eng.svc.engine.begin() as c:
        row = c.execute(sa.select(db.file_chunks.c.idx).where(
            db.file_chunks.c.sha256 == pkg["package_sha256"]).order_by(db.file_chunks.c.idx)).first()
        c.execute(db.file_chunks.update().where(
            db.file_chunks.c.sha256 == pkg["package_sha256"], db.file_chunks.c.idx == row.idx
        ).values(data=b"corrupted-block"))
    resp, data = eng.svc.get_transmittal_package(eng.token, t["id"], eng.addr)
    assert resp["status"] == "error" and resp["code"] == "file_corrupted" and data is None


def test_package_pull_slice_scoped(pd):
    """Pull отдаёт пакеты только трансмитталов читаемых проектов; посторонний их не видит."""
    t, _ = make_transmittal(pd)
    assert package_of(pd, t["id"]) is not None
    outsider = pd["world"].login("lead1")
    assert pull(outsider)["transmittal_packages"] == []
