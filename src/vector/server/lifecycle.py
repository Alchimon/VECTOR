"""Экземпляры изделий, паспорт, передача заказчику, сервис и замены (этап 6, сценарий С5 шаги 7–9; decision log D-38).

Модуль — примесь (mixin) к `Service`, как production.py: транзакция, идемпотентность по operation_id, аудит и
проверка версий — в service.py; здесь только правила домена (сами правила — в vector.core.lifecycle, общие с
клиентом).

Поток работы:

    РП проекта (резерв — руководитель отдела): instance.register — по ЗАКРЫТОМУ наряду, серия + заводские номера
        ──▶ экземпляры «Изготовлен» + НЕИЗМЕНЯЕМЫЙ состав каждого (снимок наряда + замены по наряду, SHA-256)
    РП проекта или ведущий инженер — участник проекта: passport.create / passport.save — черновик паспорта по
        шаблону-мастеру (можно офлайн через очередь): текстовые разделы, испытания, документы с точными версиями
    РП проекта: passport.export_draft — черновой экспорт (HTML «Черновик, не сертификат»), журналируется
    РП проекта (резерв — руководитель отдела): instance.handover — «Передан заказчику»;
        instance.service — сервисное событие; instance.replace — замена в эксплуатации (история, не перезапись)

Главные инварианты (каждый закреплён тестом в tests/server/test_lifecycle.py):

* экземпляр создаётся только по закрытому наряду, не больше количества наряда; заводской номер уникален в области
  «изделие + серия» (без учёта регистра), не глобально;
* состав экземпляра и история эксплуатации не меняются (триггеры БД); замена — новая запись истории;
* паспорт хранит код, версию и SHA-256 шаблона; документы — с точной версией и SHA-256 файла;
* черновой экспорт — только РП проекта, всегда с пометкой «Черновик, не сертификат», каждый — в журнале с SHA-256
  содержимого; экспорт ≠ сертификат.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import lifecycle as lc
from vector.core import production as prod
from vector.core.errors import Conflict, Rejected

from .db import (customers, document_versions, documents, instance_compositions, instance_events, kits,
                 passport_exports, passports, product_instances, projects, tenders, wo_operations, work_orders)
from .validation import canon, iso, only_keys, req_uuid


def _lc_error(e: lc.LifecycleError, current: Optional[dict] = None):
    """LifecycleError → Conflict (состояние уже другое) или Rejected (ошибка ввода, с полем и замечаниями)."""
    if e.conflict:
        return Conflict(e.code, e.message_ru, {**e.details, "current": current})
    return Rejected(e.code, e.message_ru, e.details)


class LifecycleMixin:
    """Обработчики instance.* / passport.* и данные экземпляров и паспортов для pull."""

    # ------------------------------------------------------------ представления
    def _instance_events(self, conn, instance_id: str) -> list[dict]:
        """История экземпляра по порядку: вид, дата, подробности, строка-сводка, кто, когда, на каком основании."""
        out = []
        for r in conn.execute(sa.select(instance_events).where(instance_events.c.instance_id == instance_id)
                              .order_by(instance_events.c.seq)):
            d = json.loads(r.details_json)
            b = self._basis(conn, "instance_event", f"{instance_id}#{r.seq}")
            out.append({"seq": r.seq, "kind": r.kind, "kind_label": lc.EVENT_KIND_LABELS_RU[r.kind],
                        "date": r.event_date.isoformat(), "details": d, "summary": lc.event_summary(r.kind, d),
                        "authority": r.authority, "created_by": r.created_by, "created_at": iso(r.created_at),
                        "on_behalf_of": b["on_behalf_of"] if b else None})
        return out

    def v_instance(self, conn, o: dict) -> dict:
        """Экземпляр для клиента: точные связи (проект, наряд, снимок, ТП, версия расчёта), состав одного изделия с
        проверкой SHA-256, операции наряда (что и кем сделано), история эксплуатации, id паспорта.
        """
        c = self._get(conn, instance_compositions, o["id"], "instance_id")
        comp = json.loads(c["composition_json"])
        wo = self._get(conn, work_orders, o["work_order_id"])
        ops = [{"op_no": r.op_no, "name": r.name, "work_center": r.work_center, "state": r.state,
                "state_label": prod.OP_STATE_LABELS_RU[r.state], "assignee_id": r.assignee_id,
                "fact_seconds": r.fact_seconds, "finished_at": iso(r.finished_at)}
               for r in conn.execute(sa.select(wo_operations).where(wo_operations.c.work_order_id == wo["id"])
                                     .order_by(wo_operations.c.op_no))]
        pp = conn.execute(sa.select(passports.c.id).where(passports.c.instance_id == o["id"])).first()
        return {"id": o["id"], "project_id": o["project_id"], "project_code": comp["project"]["code"],
                "kit_id": o["kit_id"], "kit_code": comp["kit"]["code"], "kit_title": comp["kit"].get("title"),
                "work_order_id": o["work_order_id"], "wo_doc_no": wo["doc_no"], "series": o["series"],
                "serial_no": o["serial_no"], "status": o["status"], "status_label": lc.INSTANCE_STATUS_LABELS_RU[o["status"]],
                "composition": comp, "composition_hash": c["content_hash"],
                # Целостность: сохранённый состав совпадает со своим SHA-256.
                "composition_ok": lc.canon_hash(comp) == c["content_hash"],
                "operations": ops, "events": self._instance_events(conn, o["id"]),
                "passport_id": pp.id if pp else None, "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    def v_passport(self, conn, o: dict) -> dict:
        """Паспорт для клиента: шаблон (код, версия, SHA-256 и совпадает ли он с шаблоном программы), разделы,
        испытания, документы с версиями, SHA-256 содержимого, журнал черновых экспортов.
        """
        exports = [{"export_no": r.export_no, "passport_version": r.passport_version, "content_hash": r.content_hash,
                    "exported_by": r.exported_by, "exported_at": iso(r.exported_at), "authority": r.authority}
                   for r in conn.execute(sa.select(passport_exports).where(passport_exports.c.passport_id == o["id"])
                                         .order_by(passport_exports.c.export_no))]
        t = lc.PASSPORT_TEMPLATES.get((o["template_code"], o["template_version"]))
        return {"id": o["id"], "instance_id": o["instance_id"], "project_id": o["project_id"],
                "template_code": o["template_code"], "template_version": o["template_version"],
                "template_hash": o["template_hash"],
                "template_ok": t is not None and lc.template_hash(t) == o["template_hash"],
                "status": o["status"], "status_label": lc.PASSPORT_STATUS_LABELS_RU[o["status"]],
                "fields": json.loads(o["fields_json"]), "tests": json.loads(o["tests_json"]),
                "documents": json.loads(o["documents_json"]), "content_hash": o["content_hash"],
                "exports": exports, "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ регистрация экземпляров
    def _instance_register(self, ctx, p, ev):
        """instance.register {work_order_id, series, items:[{id, serial_no}]} — экземпляры по закрытому наряду.

        В одной транзакции: экземпляры «Изготовлен» и неизменяемый состав каждого (снимок наряда + замены по наряду,
        SHA-256). Всё или ничего: занятый номер (в области «изделие + серия») или превышение количества наряда —
        отказ со списком, ни один экземпляр не создаётся.
        """
        only_keys(p, {"work_order_id", "series", "items"})
        wo = self._require(ctx.conn, work_orders, req_uuid(p, "work_order_id"), "Наряд")
        auth = self.policy.check(ctx.conn, ctx.user_id, "instance.register", ctx.now, project_id=wo["project_id"])
        if wo["status"] != "closed":
            raise Rejected("wo_not_closed", f"Экземпляры регистрируются по закрытому наряду (сейчас — «{prod.WO_STATUS_LABELS_RU[wo['status']]}»).")
        items = p.get("items")
        if not isinstance(items, list) or not items:
            raise Rejected("required", "Укажите хотя бы один заводской номер.", {"field": "items"})
        if not all(isinstance(x, dict) and set(x) <= {"id", "serial_no"} for x in items):
            raise Rejected("invalid_type", "items — список {id, serial_no}.", {"field": "items"})
        try:
            series, serials = lc.parse_serials(p.get("series"), [x.get("serial_no") for x in items])
        except lc.LifecycleError as e:
            raise _lc_error(e)
        if len(serials) != len(items):
            raise Rejected("required", "У каждой строки должен быть заводской номер.", {"field": "items"})
        ids = [req_uuid(x, "id") for x in items]
        if len(set(ids)) != len(ids):
            raise Rejected("id_duplicate", "Идентификаторы экземпляров повторяются.", {"field": "items"})
        for i in ids:
            self._ensure_new(ctx.conn, product_instances, i)
        have = ctx.conn.execute(sa.select(sa.func.count()).select_from(product_instances)
                                .where(product_instances.c.work_order_id == wo["id"])).scalar()
        if have + len(serials) > wo["qty"]:
            raise Rejected("qty_exceeded", f"По наряду № {wo['doc_no']} изготовлено {wo['qty']} шт., уже зарегистрировано "
                                           f"{have}; можно добавить не больше {wo['qty'] - have}.",
                           {"field": "items", "qty": wo["qty"], "registered": have})
        taken = [r.serial_no for r in ctx.conn.execute(
            sa.select(product_instances.c.serial_no).where(product_instances.c.kit_id == wo["kit_id"],
                                                           product_instances.c.series == series,
                                                           product_instances.c.serial_key.in_([s.casefold() for s in serials])))]
        if taken:
            raise Rejected("serial_taken", f"В серии «{series}» этого изделия номера уже заняты: {', '.join(sorted(taken))}.",
                           {"field": "items", "serials": sorted(taken)})
        comp = self._composition_of(ctx.conn, wo)
        h = lc.canon_hash(comp)
        out = []
        for iid, sn in zip(ids, serials, strict=True):
            ctx.conn.execute(product_instances.insert().values(
                id=iid, project_id=wo["project_id"], kit_id=wo["kit_id"], work_order_id=wo["id"], series=series,
                serial_no=sn, serial_key=sn.casefold(), status="registered", version=1, created_by=ctx.user_id,
                created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
            ctx.conn.execute(instance_compositions.insert().values(instance_id=iid, content_hash=h,
                                                                   composition_json=canon(comp), created_at=ctx.now))
            ctx.log("product_instance", iid, auth, None, 1,
                    {"event": "InstanceRegistered", "work_order_id": wo["id"], "wo_doc_no": wo["doc_no"], "series": series,
                     "serial_no": sn, "composition_hash": h, "snapshot_hash": wo["snapshot_hash"]})
            out.append(self.v_instance(ctx.conn, self._get(ctx.conn, product_instances, iid)))
        return {"product_instances": out}

    def _composition_of(self, conn, wo: dict) -> dict:
        """Состав экземпляра из наряда: связи (проект, изделие, наряд и SHA-256 его снимка, ТП, версия расчёта) и
        состав одного изделия — строки снимка с заменами по наряду (количество на единицу — из снимка).
        """
        snap, _ = self._snapshot(conn, wo["id"])
        subs = self._substitutions(conn, wo["id"])
        eff = prod.effective_spec(snap["spec"]["lines"], subs)
        pr = self._get(conn, projects, wo["project_id"])
        return {"format": 1, "project": {"id": pr["id"], "code": pr["code"], "title": pr["title"]},
                "kit": snap["kit"],
                "work_order": {"id": wo["id"], "doc_no": wo["doc_no"], "qty": wo["qty"], "snapshot_hash": wo["snapshot_hash"],
                               "spec_hash": prod.spec_hash(eff), "closed_at": iso(wo["closed_at"])},
                "tp": snap["tp"],
                "spec": {k: snap["spec"][k] for k in ("calc_version_id", "version_no", "content_hash")},
                "lines": lc.composition_lines(snap["spec"]["lines"], eff),
                "substitutions": [{"seq": s["seq"], "line_id": s["line_id"], "old": s["old"], "new": s["new"],
                                   "reason": s["reason"]} for s in subs]}

    # ------------------------------------------------------------ история эксплуатации
    def _add_event(self, ctx, inst: dict, kind: str, d: dict, auth) -> int:
        """Добавить неизменяемую запись истории экземпляра (следующий номер) и основание решения."""
        seq = (ctx.conn.execute(sa.select(sa.func.max(instance_events.c.seq))
                                .where(instance_events.c.instance_id == inst["id"])).scalar() or 0) + 1
        ctx.conn.execute(instance_events.insert().values(
            instance_id=inst["id"], seq=seq, kind=kind, event_date=date.fromisoformat(d["date"]), details_json=canon(d),
            authority=auth.describe(), created_by=ctx.user_id, created_at=ctx.now))
        self._record_basis(ctx, "instance_event", f"{inst['id']}#{seq}", 1, auth)
        return seq

    def _event_date_ok(self, ctx, d: dict) -> None:
        """Дата события не в будущем (по часам сервера; запас — сутки на часовые пояса)."""
        if date.fromisoformat(d["date"]) > (ctx.now + timedelta(days=1)).date():
            raise Rejected("date_in_future", "Дата события ещё не наступила.", {"field": "date"})

    def _instance_handover(self, ctx, p, ev):
        """instance.handover {id, date, recipient, document, note?} — передать заказчику («Изготовлен» → «Передан»)."""
        only_keys(p, {"id", "date", "recipient", "document", "note"})
        inst = self._require(ctx.conn, product_instances, req_uuid(p, "id"), "Экземпляр")
        auth = self.policy.check(ctx.conn, ctx.user_id, "instance.handover", ctx.now, project_id=inst["project_id"])
        view = lambda x: self.v_instance(ctx.conn, x)  # noqa: E731
        self._check_version(inst, ev, view)
        if inst["status"] != "registered":
            raise Conflict("instance_state", "Экземпляр уже передан заказчику.", {"current": view(inst)})
        try:
            d = lc.parse_handover(p)
        except lc.LifecycleError as e:
            raise _lc_error(e)
        self._event_date_ok(ctx, d)
        pr = self._get(ctx.conn, projects, inst["project_id"])
        cust = None
        if pr["tender_id"]:
            t = self._get(ctx.conn, tenders, pr["tender_id"])
            c = self._get(ctx.conn, customers, t["customer_id"]) if t else None
            cust = {"id": c["id"], "name": c["name"]} if c else None
        d["customer"] = cust
        seq = self._add_event(ctx, inst, "handover", d, auth)
        n = self._update_versioned(ctx, product_instances, inst, {"status": "handed_over", "updated_by": ctx.user_id,
                                                                  "updated_at": ctx.now})
        ctx.log("product_instance", inst["id"], auth, inst["version"], n["version"],
                {"event": "InstanceHandedOver", "seq": seq, **d})
        return {"product_instance": view(n)}

    def _instance_service(self, ctx, p, ev):
        """instance.service {id, kind, date, description, document?} — сервисное событие (после передачи заказчику)."""
        only_keys(p, {"id", "kind", "date", "description", "document"})
        inst = self._require(ctx.conn, product_instances, req_uuid(p, "id"), "Экземпляр")
        auth = self.policy.check(ctx.conn, ctx.user_id, "instance.service", ctx.now, project_id=inst["project_id"])
        if inst["status"] != "handed_over":
            raise Rejected("instance_state", "Сервисные события записываются после передачи заказчику.")
        try:
            d = lc.parse_service(p)
        except lc.LifecycleError as e:
            raise _lc_error(e)
        self._event_date_ok(ctx, d)
        seq = self._add_event(ctx, inst, "service", d, auth)
        n = self._update_versioned(ctx, product_instances, inst, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("product_instance", inst["id"], auth, inst["version"], n["version"], {"event": "ServiceEvent", "seq": seq, **d})
        return {"product_instance": self.v_instance(ctx.conn, n)}

    def _instance_replace(self, ctx, p, ev):
        """instance.replace {id, date, removed, removed_serial?, installed, installed_serial?, qty?, reason, line_id?} —
        замена в эксплуатации или до передачи. Состав при регистрации не переписывается; line_id (необязательно) —
        строка состава, к которой относится замена (должна быть в составе экземпляра).
        """
        only_keys(p, {"id", "date", "removed", "removed_serial", "installed", "installed_serial", "qty", "reason", "line_id"})
        inst = self._require(ctx.conn, product_instances, req_uuid(p, "id"), "Экземпляр")
        auth = self.policy.check(ctx.conn, ctx.user_id, "instance.replace", ctx.now, project_id=inst["project_id"])
        try:
            d = lc.parse_replacement(p)
        except lc.LifecycleError as e:
            raise _lc_error(e)
        self._event_date_ok(ctx, d)
        if d["line_id"]:
            comp = json.loads(self._get(ctx.conn, instance_compositions, inst["id"], "instance_id")["composition_json"])
            if not any(ln["line_id"] == d["line_id"] for ln in comp["lines"]):
                raise Rejected("line_not_found", "В составе экземпляра нет такой строки.", {"field": "line_id"})
        seq = self._add_event(ctx, inst, "replacement", d, auth)
        n = self._update_versioned(ctx, product_instances, inst, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("product_instance", inst["id"], auth, inst["version"], n["version"], {"event": "Replacement", "seq": seq, **d})
        return {"product_instance": self.v_instance(ctx.conn, n)}

    # ------------------------------------------------------------ паспорт
    def _passport_docs(self, conn, project_id: str, refs: list[dict]) -> list[dict]:
        """Документы паспорта с точными версиями: документ того же проекта, версия существует; к ссылке
        добавляются обозначение, наименование, имя файла и SHA-256 этой версии.
        """
        out = []
        for ref in refs:
            d = self._get(conn, documents, ref["document_id"])
            if d is None or d["project_id"] != project_id:
                raise Rejected("document_invalid", "Документ паспорта — документ этого проекта.", {"field": "documents"})
            v = conn.execute(sa.select(document_versions).where(document_versions.c.document_id == d["id"],
                                                                document_versions.c.version_no == ref["version_no"])).first()
            if v is None:
                raise Rejected("document_version_invalid", f"У документа {d['code']} нет версии {ref['version_no']}.",
                               {"field": "documents"})
            out.append({"document_id": d["id"], "code": d["code"], "title": d["title"], "version_no": v.version_no,
                        "file_name": v.file_name, "sha256": v.sha256})
        return out

    def _passport_content(self, conn, inst: dict, t: dict, p: dict, cur: Optional[dict] = None) -> dict:
        """Проверить и собрать содержимое паспорта из запроса (не переданные части — из текущего паспорта)."""
        try:
            fields = lc.parse_fields(t, p["fields"]) if "fields" in p else (cur["fields"] if cur else lc.parse_fields(t, {}))
            tests = lc.parse_tests(p["tests"]) if "tests" in p else (cur["tests"] if cur else [])
            refs = lc.parse_documents(p["documents"]) if "documents" in p else None
        except lc.LifecycleError as e:
            raise _lc_error(e)
        docs = self._passport_docs(conn, inst["project_id"], refs) if refs is not None else (cur["documents"] if cur else [])
        return {"fields": fields, "tests": tests, "documents": docs}

    def _passport_hash(self, conn, row: dict, inst_id: str, content: dict) -> str:
        """SHA-256 содержимого паспорта (vector.core.lifecycle.passport_hash)."""
        comp = self._get(conn, instance_compositions, inst_id, "instance_id")
        return lc.passport_hash(template_code=row["template_code"], template_version=row["template_version"],
                                template_sha=row["template_hash"], instance_id=inst_id, composition_hash=comp["content_hash"],
                                fields=content["fields"], tests=content["tests"], documents=content["documents"])

    def _passport_create(self, ctx, p, ev):
        """passport.create {id, instance_id, fields?, tests?, documents?} — черновик паспорта по текущей версии
        шаблона-мастера. Один паспорт на экземпляр (второй — отказ с id существующего).
        """
        only_keys(p, {"id", "instance_id", "fields", "tests", "documents"})
        pid = req_uuid(p, "id")
        inst = self._require(ctx.conn, product_instances, req_uuid(p, "instance_id"), "Экземпляр")
        auth = self.policy.check(ctx.conn, ctx.user_id, "passport.edit", ctx.now, project_id=inst["project_id"])
        self._ensure_new(ctx.conn, passports, pid)
        other = self._get(ctx.conn, passports, inst["id"], "instance_id")
        if other is not None:
            raise Conflict("passport_exists", "У этого экземпляра уже есть паспорт — откройте его.",
                           {"current": self.v_passport(ctx.conn, other)})
        t = lc.template(*lc.CURRENT_TEMPLATE)
        row = {"template_code": t["code"], "template_version": t["version"], "template_hash": lc.template_hash(t)}
        content = self._passport_content(ctx.conn, inst, t, p)
        h = self._passport_hash(ctx.conn, row, inst["id"], content)
        ctx.conn.execute(passports.insert().values(
            id=pid, instance_id=inst["id"], project_id=inst["project_id"], **row, status="draft",
            fields_json=canon(content["fields"]), tests_json=canon(content["tests"]),
            documents_json=canon(content["documents"]), content_hash=h, version=1, created_by=ctx.user_id,
            created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("passport", pid, auth, None, 1, {"event": "PassportCreated", "instance_id": inst["id"], **row, "content_hash": h})
        return {"passport": self.v_passport(ctx.conn, self._get(ctx.conn, passports, pid)),
                "product_instance": self.v_instance(ctx.conn, inst)}

    def _passport_save(self, ctx, p, ev):
        """passport.save {id, fields?, tests?, documents?} — сохранить черновик (переданные части заменяются целиком)."""
        only_keys(p, {"id", "fields", "tests", "documents"})
        pp = self._require(ctx.conn, passports, req_uuid(p, "id"), "Паспорт")
        inst = self._get(ctx.conn, product_instances, pp["instance_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "passport.edit", ctx.now, project_id=inst["project_id"])
        view = lambda x: self.v_passport(ctx.conn, x)  # noqa: E731
        self._check_version(pp, ev, view)
        t = lc.template(pp["template_code"], pp["template_version"])
        cur = view(pp)
        content = self._passport_content(ctx.conn, inst, t, p, cur)
        h = self._passport_hash(ctx.conn, pp, inst["id"], content)
        if h == pp["content_hash"]:
            return {"passport": cur, "unchanged": True}
        n = self._update_versioned(ctx, passports, pp, {"fields_json": canon(content["fields"]),
                                                        "tests_json": canon(content["tests"]),
                                                        "documents_json": canon(content["documents"]), "content_hash": h,
                                                        "updated_by": ctx.user_id, "updated_at": ctx.now})
        changed = [k for k in ("fields", "tests", "documents") if content[k] != cur[k]]
        ctx.log("passport", pp["id"], auth, pp["version"], n["version"],
                {"event": "PassportSaved", "changed": changed, "content_hash": h})
        return {"passport": view(n)}

    def _passport_export_draft(self, ctx, p, ev):
        """passport.export_draft {id} — черновой экспорт паспорта (только РП проекта, только при связи).

        Ответ — {export: {export_no, file_name, html, content_hash, label}}. Пометка «Черновик, не сертификат» стоит на
        каждой странице. Экспорт журналируется: номер, версия паспорта, SHA-256 содержимого, кто и на каком основании.
        """
        only_keys(p, {"id"})
        pp = self._require(ctx.conn, passports, req_uuid(p, "id"), "Паспорт")
        inst = self._get(ctx.conn, product_instances, pp["instance_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "passport.export_draft", ctx.now, project_id=inst["project_id"])
        view = lambda x: self.v_passport(ctx.conn, x)  # noqa: E731
        self._check_version(pp, ev, view)
        pv, iv = view(pp), self.v_instance(ctx.conn, inst)
        if not pv["template_ok"]:
            raise Rejected("template_mismatch", "Шаблон паспорта не совпадает с шаблоном программы — экспорт невозможен. "
                                                "Обратитесь к администратору.")
        if not iv["composition_ok"]:
            raise Rejected("composition_integrity", "Состав экземпляра не совпадает с контрольной суммой (SHA-256) — "
                                                    "экспорт невозможен. Обратитесь к администратору.")
        no = (ctx.conn.execute(sa.select(sa.func.max(passport_exports.c.export_no))
                               .where(passport_exports.c.passport_id == pp["id"])).scalar() or 0) + 1
        ctx.conn.execute(passport_exports.insert().values(
            passport_id=pp["id"], export_no=no, passport_version=pp["version"], content_hash=pp["content_hash"],
            authority=auth.describe(), exported_by=ctx.user_id, exported_at=ctx.now))
        who = self._actor_label(ctx, auth)
        at = ctx.now.strftime("%d.%m.%Y %H:%M (UTC)")
        page = lc.render_draft_html(passport=pv, instance=iv, export_no=no, exported_at=at, exported_by=who,
                                    content_hash=pp["content_hash"])
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{iv['kit_code']}_{iv['series']}_{iv['serial_no']}")
        ctx.log("passport", pp["id"], auth, pp["version"], pp["version"],
                {"event": "PassportDraftExported", "export_no": no, "content_hash": pp["content_hash"],
                 "instance_id": inst["id"], "label": lc.DRAFT_LABEL})
        return {"export": {"export_no": no, "file_name": f"Паспорт_черновик_{safe}_экспорт{no}.html", "html": page,
                           "content_hash": pp["content_hash"], "label": lc.DRAFT_LABEL},
                "passport": view(pp)}

    # ------------------------------------------------------------ pull
    def _pull_lifecycle(self, conn, uid: str, now) -> dict:
        """Экземпляры и паспорта — по праву instance.read в проекте; закрытые наряды, по которым можно
        зарегистрировать экземпляры (остаток), — тем, у кого есть instance.register в проекте; документы проекта с
        версиями — для выбора в паспорте (по праву passport.edit).
        """
        out: dict[str, Any] = {"product_instances": [], "passports": [], "instance_work_orders": [], "passport_documents": []}
        pids = [r.id for r in conn.execute(sa.select(projects.c.id).order_by(projects.c.code))]
        readable = [pid for pid in pids if self.policy.can(conn, uid, "instance.read", now, pid)]
        if readable:
            for r in conn.execute(sa.select(product_instances).where(product_instances.c.project_id.in_(readable))
                                  .order_by(product_instances.c.kit_id, product_instances.c.series, product_instances.c.serial_no)):
                out["product_instances"].append(self.v_instance(conn, dict(r._mapping)))
            for r in conn.execute(sa.select(passports).where(passports.c.project_id.in_(readable))):
                out["passports"].append(self.v_passport(conn, dict(r._mapping)))
        reg = [pid for pid in pids if self.policy.can(conn, uid, "instance.register", now, pid)]
        if reg:
            counts = dict(conn.execute(sa.select(product_instances.c.work_order_id, sa.func.count())
                                       .group_by(product_instances.c.work_order_id)).all())
            for r in conn.execute(sa.select(work_orders, kits.c.code.label("kit_code"), projects.c.code.label("pcode"))
                                  .join(kits, kits.c.id == work_orders.c.kit_id).join(projects, projects.c.id == work_orders.c.project_id)
                                  .where(work_orders.c.project_id.in_(reg), work_orders.c.status == "closed")
                                  .order_by(work_orders.c.doc_no)):
                left = r.qty - counts.get(r.id, 0)
                out["instance_work_orders"].append({"id": r.id, "doc_no": r.doc_no, "title": r.title, "qty": r.qty,
                                                    "registered": counts.get(r.id, 0), "left": left,
                                                    "kit_id": r.kit_id, "kit_code": r.kit_code,
                                                    "project_id": r.project_id, "project_code": r.pcode})
        edit = [pid for pid in readable if self.policy.can(conn, uid, "passport.edit", now, pid)]
        if edit:
            for r in conn.execute(sa.select(documents).where(documents.c.project_id.in_(edit), documents.c.current_version_no > 0)
                                  .order_by(documents.c.code)):
                out["passport_documents"].append({"id": r.id, "project_id": r.project_id, "code": r.code, "title": r.title,
                                                  "current_version_no": r.current_version_no})
        return out
