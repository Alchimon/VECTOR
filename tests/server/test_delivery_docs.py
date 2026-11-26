# -*- coding: utf-8 -*-
"""Настраиваемые формы документов доставки (этап 6 blueprint 52, D-69, ответ владельца п. 6).

Покрыто:
- форму ведёт руководитель отдела (начальник логистики, A-59); у РП права на формы нет;
- плейсхолдеры {{field}}: автозаполнение данными проекта/комплекта + ручные значения логиста;
  неизвестное поле остаётся в тексте как есть (видно, не молчит);
- правка формы версионируется и НЕ переписывает выписанные документы (снимок);
- отключённая форма не даёт новых документов; правка документа версионируется (конфликт версий);
- документы и формы приезжают в pull.
"""
from __future__ import annotations

import pytest

from conftest import new_id
from tests.server.test_delivery import make_project, pull


@pytest.fixture
def project(world):
    rp = world.login("rp1")
    return make_project(world, rp)


FORM = {"code": "TOR12", "title": "Товарная накладная ТОРГ-12",
        "body": "Проект {{project.code}} «{{project.title}}»\nЗаказчик: {{customer.name}}\n"
                "Комплект: {{kit.code}} — {{kit.title}}\nТранспорт: {{truck}}\n{{unknown_field}}"}


def test_template_rights_and_lifecycle(world, project):
    rp, head = world.login("rp1"), world.login("head1")
    # Форму ведёт начальник логистики (временное правило A-59: руководитель отдела); у РП права нет.
    assert rp.cmd("doc_template.create", {"id": new_id(), **FORM})["status"] == "denied"
    tid = new_id()
    r = head.ok("doc_template.create", {"id": tid, **FORM})["template"]
    assert r["active"] is True and r["version"] == 1
    assert set(r["fields"]) == {"project.code", "project.title", "customer.name", "kit.code", "kit.title",
                                "truck", "unknown_field"}
    # Правка версионируется; конфликт версий — отказ.
    r2 = head.ok("doc_template.update", {"id": tid, "title": "ТОРГ-12 (новая форма)"}, expected_version=1)
    assert r2["template"]["version"] == 2
    assert head.cmd("doc_template.update", {"id": tid, "title": "x"},
                    expected_version=1)["code"] == "version_conflict"
    # Отключение: новых документов по форме нет.
    assert head.cmd("doc_template.set_active", {"id": tid, "active": True},
                    expected_version=2)["code"] == "no_changes"
    r3 = head.ok("doc_template.set_active", {"id": tid, "active": False}, expected_version=2)["template"]
    assert r3["active"] is False
    assert rp.cmd("delivery_doc.create", {"id": new_id(), "project_id": project,
                                          "template_id": tid})["code"] == "template_unusable"
    head.ok("doc_template.set_active", {"id": tid, "active": True}, expected_version=3)


def test_doc_autofill_snapshot_and_versions(world, project):
    rp, head = world.login("rp1"), world.login("head1")
    tid = new_id()
    head.ok("doc_template.create", {"id": tid, **FORM})
    kid = new_id()
    rp.ok("kit.create", {"id": kid, "project_id": project, "code": "ШАВР", "title": "Шкаф АВР"})
    # Логист выписывает документ: проект и комплект подставлены, своё поле — вручную, неизвестное — как есть.
    r = rp.ok("delivery_doc.create", {"id": new_id(), "project_id": project, "kit_id": kid,
                                      "template_id": tid, "values": {"truck": "МАЗ, В001АА159"}})
    doc = r["doc"]
    assert "Проект П-2026-901 «Шкафы АСУ ТП»" in doc["rendered"]
    assert "Заказчик: АО Заказчик" in doc["rendered"]
    assert "Комплект: ШАВР — Шкаф АВР" in doc["rendered"]
    assert "Транспорт: МАЗ, В001АА159" in doc["rendered"]
    assert "{{unknown_field}}" in doc["rendered"]            # неизвестное поле видно, а не молча пусто
    assert doc["template_code"] == "TOR12"
    # Правка формы выписанный документ не переписывает.
    head.ok("doc_template.update", {"id": tid, "body": "ПУСТО"}, expected_version=1)
    again = next(d for d in pull(rp)["delivery_docs"] if d["id"] == doc["id"])
    assert "Проект П-2026-901" in again["rendered"]
    # Правка документа версионируется; конфликт — отказ.
    r2 = rp.ok("delivery_doc.update", {"id": doc["id"], "rendered": doc["rendered"] + "\nПодпись: ___"},
               expected_version=1)["doc"]
    assert r2["version"] == 2 and "Подпись" in r2["rendered"]
    assert rp.cmd("delivery_doc.update", {"id": doc["id"], "rendered": "x"},
                  expected_version=1)["code"] == "version_conflict"
    # Формы и документы приехали в pull; событие в журнале проекта.
    data = pull(rp)
    assert any(t["id"] == tid for t in data["delivery_doc_templates"])
    assert any(e["kind"] == "delivery_doc_issued" for e in data["project_events"]
               if e["project_id"] == project)
