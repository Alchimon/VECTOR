"""Склад и закупочная потребность (сценарий С4, первая часть; R-13, R-14).

Модуль — примесь (mixin) к `Service`, как work.py и calc.py: транзакция SERIALIZABLE, идемпотентность по
operation_id, аудит и проверка версий — в service.py; здесь только складские правила.

Что есть в этом срезе:

    номенклатура, склады (справочники; правка офлайн через очередь)
    приход ─┐
    выдача ─┼─▶ неизменяемый журнал движений ──▶ остаток (stock_balances, производное, с CHECK в БД)
    сторно ─┘
    резерв: active ──выдачи по резерву──▶ consumed
                 └──stock.release──────▶ released (неизрасходованное вернулось в доступное)
    потребность: снимок ПОСЛЕДНЕЙ зафиксированной версии расчёта комплекта (без цен), строки сопоставлены
                 с номенклатурой по точному коду и единице; покрытие строки = резервы под неё.
    ручное сопоставление (need.match_line, A-25): закупка указывает позицию номенклатуры для строки, которую
                 автомат не сопоставил (или сопоставил не так); решение — неизменяемая запись с причиной,
                 строка-снимок не меняется. Пересопоставить можно, пока под строку нет резервов.
    перемещение между складами (A-28, D-29): stock.transfer — отгрузка (расход на отправителе, статус «В пути»);
                 stock.transfer_accept — приёмка (приход на получателе, «Принято»); stock.transfer_cancel — возврат
                 отправителю («Возвращено»). Только из доступного (резервы не переезжают); сторно движений перемещения
                 запрещено — в пути его принимают или возвращают, после приёмки исправляют обратным перемещением.
                 Одношаговые перемещения схемы v8 (stock_transfers) остаются в журнале как история.

Главные инварианты (каждый закреплён тестом в tests/server/test_stock.py):

* доступно = остаток − резервы; выдать без резерва или зарезервировать можно только доступное. Проверка — в
  транзакции проводки по строке остатка с условием по version (две параллельные выдачи последней единицы не
  пройдут обе); БД дополнительно не даст записать отрицательный остаток (CHECK);
* проводки, резервы и формирование потребности — только онлайн (online_required): офлайн ничего не
  ставится в очередь — склад и деньги «задним числом» не меняются;
* журнал движений неизменяем (триггеры БД); исправление — сторно, и не больше одного на движение;
* остаток всегда равен свёртке журнала и резервов (`ledger_mismatches` — для тестов и диагностики);
* никаких порогов, сроков резерва и количеств по умолчанию (Q2, A-23).

Чего здесь НЕТ (следующие шаги С4, см. issue «Этап 4 / С4»): черновики движений, заказ поставщику и факт
поставки, совместное решение «склад + закупка», перемещение вместе с резервом, корректировка остатка (нужна
подпись руководителя склада), пополнение расходников по порогу.
"""
from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import consolidation as cs
from vector.core import money, stock
from vector.core.errors import Conflict, Denied, Rejected

from .db import (calc_version_lines, calc_versions, item_aliases, kits, procurement_allocation_receipts,
                 procurement_allocations, procurement_batch_lines, procurement_batches,
                 procurement_need_line_match_units, procurement_need_line_matches, procurement_need_lines,
                 procurement_needs, projects, unit_conversions, signatures, stock_balances, stock_item_packs,
                 stock_items, stock_movements, stock_reservations, stock_shipment_losses, stock_shipment_terms,
                 stock_shipments, stock_transfer_acts, stock_transfers, users, warehouses, work_order_issues)
from . import security
from .validation import canon, iso, only_keys, opt_date, opt_str, req_str, req_uuid

# Сколько последних движений отдаётся в pull (полный журнал — запросом истории/отчётом позже).
PULL_MOVEMENTS_LIMIT = 1000


def _act_content(sh: dict, a: dict) -> dict:
    """Каноническое содержание акта недостачи (D-30) — то, что подписывает приёмщик (D-31). Строится из строк БД
    одинаково при подписи и при проверке: количества — ровно 4 знака, время — ISO UTC.
    """
    return {"doc": "stock_transfer_act", "act_no": a["act_no"], "shipment_id": sh["id"], "item_id": sh["item_id"],
            "from_warehouse_id": sh["from_warehouse_id"], "to_warehouse_id": sh["to_warehouse_id"],
            "shipped_qty": money.to_json(a["shipped_qty"]), "accepted_qty": money.to_json(a["accepted_qty"]),
            "shortage_qty": money.to_json(a["shortage_qty"]), "reason": a["reason"], "signed_by": a["signed_by"],
            "signer_name": a["signer_name"], "signed_at": iso(a["signed_at"])}


def _loss_content(sh: dict, lo: dict) -> dict:
    """Каноническое содержание акта об утрате (D-31) — что подписывает приёмщик."""
    return {"doc": "stock_loss_act", "act_no": lo["act_no"], "shipment_id": sh["id"], "item_id": sh["item_id"],
            "from_warehouse_id": sh["from_warehouse_id"], "to_warehouse_id": sh["to_warehouse_id"],
            "qty": money.to_json(lo["qty"]), "reason": lo["reason"], "signed_by": lo["signed_by"],
            "signer_name": lo["signer_name"], "signed_at": iso(lo["signed_at"])}


def _digest(content: dict) -> str:
    """SHA-256 канонического JSON содержания документа (hex)."""
    return hashlib.sha256(canon(content).encode("utf-8")).hexdigest()


def _v_signature(sig: Optional[dict], content: dict) -> Optional[dict]:
    """Подпись документа для клиента: способ, кто и когда, и совпадает ли содержание с подписанным (content_ok).
    None — документ подписан до появления способов подписи (акты схемы v10): подписант и время есть в самом акте.
    """
    if not sig or not sig.get("id"):
        return None
    return {"method": sig["method"], "method_label": stock.SIGNATURE_METHOD_LABELS_RU.get(sig["method"], sig["method"]),
            "signer_name": sig["signer_name"], "signed_at": iso(sig["signed_at"]),
            "content_ok": sig["content_sha256"] == _digest(content)}


def _v_act(a: dict, sh: Optional[dict] = None, sig: Optional[dict] = None) -> dict:
    """Акт недостачи (D-30) для клиента: номер, отгружено / принято / недостача, причина, кто и когда подписал,
    подпись (D-31: способ и проверка содержания).
    """
    return {"id": a["id"], "act_no": a["act_no"], "shipped_qty": money.to_json(a["shipped_qty"]),
            "accepted_qty": money.to_json(a["accepted_qty"]), "shortage_qty": money.to_json(a["shortage_qty"]),
            "reason": a["reason"], "signed_by": a["signed_by"], "signer_name": a["signer_name"],
            "signed_at": iso(a["signed_at"]), "signature": _v_signature(sig, _act_content(sh, a)) if sh else None}


def _v_loss(lo: dict, sh: dict, sig: Optional[dict]) -> dict:
    """Акт об утрате (D-31) для клиента."""
    return {"act_no": lo["act_no"], "qty": money.to_json(lo["qty"]), "reason": lo["reason"],
            "signed_by": lo["signed_by"], "signer_name": lo["signer_name"], "signed_at": iso(lo["signed_at"]),
            "signature": _v_signature(sig, _loss_content(sh, lo))}


def _qty(p: dict, key: str = "qty") -> Decimal:
    """Количество из запроса (строго > 0, ≤ 4 знаков) или отказ с указанием поля."""
    try:
        return stock.parse_qty(p.get(key))
    except stock.StockError as e:
        raise Rejected(e.code, e.message_ru, {"field": key})


def _code(p: dict, key: str = "code") -> str:
    """Код склада/позиции из запроса (точный, без пробелов, A-20) или отказ с указанием поля."""
    try:
        return stock.check_code(p.get(key), "Код")
    except stock.StockError as e:
        raise Rejected(e.code, e.message_ru, {"field": key})


def _bool(p: dict, key: str) -> bool:
    """Логическое поле запроса (true/false); строки «true»/«1» не принимаются."""
    v = p.get(key)
    if not isinstance(v, bool):
        raise Rejected("field_type", f"Поле «{key}» должно быть true или false.", {"field": key})
    return v


def _stock_rejected(e: stock.StockError) -> Rejected:
    """Нарушение складского правила из vector.core.stock → ответ-отказ с тем же кодом и подробностями."""
    return Rejected(e.code, e.message_ru, e.details)


class StockMixin:
    """Обработчики warehouse.* / item.* / stock.* / need.generate и складские данные для pull."""

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_warehouse(o: dict) -> dict:
        """Склад для клиента: код, название, примечание, действует ли, версия."""
        return {"id": o["id"], "code": o["code"], "name": o["name"], "note": o["note"], "active": bool(o["active"]),
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_item(o: dict, pack_qty: Optional[Decimal] = None) -> dict:
        """Позиция номенклатуры для клиента: код, название, единица, примечание, кратность заказа (D-59),
        действует ли, версия."""
        return {"id": o["id"], "code": o["code"], "name": o["name"], "unit": o["unit"], "note": o["note"],
                "pack_qty": money.to_json(pack_qty),
                "active": bool(o["active"]), "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_alias(o: dict) -> dict:
        """Другое обозначение позиции (аналог, D-25) для клиента."""
        return {"id": o["id"], "item_id": o["item_id"], "alias": o["alias"], "note": o["note"], "active": bool(o["active"]),
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_conversion(o: dict) -> dict:
        """Пересчёт единиц для клиента: «1 from_unit = factor to_unit», общий (item_id = None) или для позиции."""
        return {"id": o["id"], "item_id": o["item_id"], "from_unit": o["from_unit"], "to_unit": o["to_unit"],
                "factor": money.to_json(o["factor"]), "note": o["note"], "active": bool(o["active"]),
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_balance(o: dict) -> dict:
        """Остаток позиции на складе: остаток / резерв / доступно (строки с 4 знаками) и версия строки."""
        b = stock.Balance(o["on_hand"], o["reserved"])
        return {"warehouse_id": o["warehouse_id"], "item_id": o["item_id"], **b.to_json(), "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_reservation(o: dict) -> dict:
        """Резерв для клиента: количество, израсходовано, осталось (0, если резерв закрыт), статус с подписью, связь с
        потребностью, причины.
        """
        return {"id": o["id"], "warehouse_id": o["warehouse_id"], "item_id": o["item_id"], "project_id": o["project_id"],
                "need_id": o["need_id"], "need_line_id": o["need_line_id"], "qty": money.to_json(o["qty"]),
                "consumed": money.to_json(o["consumed"]),
                "remaining": money.to_json(o["qty"] - o["consumed"] if o["status"] == "active" else Decimal(0)),
                "status": o["status"], "status_label": stock.RESERVATION_LABELS_RU.get(o["status"], o["status"]),
                "reason": o["reason"], "close_reason": o["close_reason"], "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_movement(o: dict, reversed_by: Optional[str] = None, leg: Optional[dict] = None) -> dict:
        """Движение журнала для клиента: номер, вид и подпись («Приход», «Сторно прихода», «Расход по перемещению»),
        количество, проект/резерв, ссылка на сторнируемое и сторнирующее, основание.

        leg — если движение относится к перемещению (см. `_legs`): id перемещения, направление (out — отгрузка,
        in — приёмка, return — возврат отправителю) и второй склад маршрута. Для таких движений сторно недоступно.
        """
        if leg is not None:
            tr = {"transfer_id": leg["transfer_id"], "transfer_direction": leg["direction"],
                  "transfer_peer_warehouse_id": leg["peer_warehouse_id"]}
            label = stock.TRANSFER_LABELS_RU[leg["direction"]]
        else:
            tr = {"transfer_id": None, "transfer_direction": None, "transfer_peer_warehouse_id": None}
            # Сторно имеет противоположный вид (сторно прихода — расход), поэтому подпись берём по исходному
            # движению: «Сторно прихода», а не «Сторно: Выдача» — так понятнее, что именно отменено.
            label = stock.STORNO_LABELS_RU[o["kind"]] if o["reverses_id"] else stock.MOVEMENT_LABELS_RU[o["kind"]]
        return {"id": o["id"], "doc_no": o["doc_no"], "kind": o["kind"], **tr,
                "kind_label": label,
                "warehouse_id": o["warehouse_id"], "item_id": o["item_id"], "qty": money.to_json(o["qty"]),
                "project_id": o["project_id"], "reservation_id": o["reservation_id"], "reverses_id": o["reverses_id"],
                "reversed_by": reversed_by, "source_doc": o["source_doc"], "reason": o["reason"],
                "posted_by": o["posted_by"], "posted_at": iso(o["posted_at"])}

    @staticmethod
    def v_shipment(o: dict, expected_on=None, act: Optional[dict] = None, act_sig: Optional[dict] = None,
                   loss: Optional[dict] = None, loss_sig: Optional[dict] = None) -> dict:
        """Перемещение (D-29) для клиента: позиция, маршрут, количество, статус с подписью, движения отгрузки и закрытия
        (приёмки или возврата), основание, кто и когда отгрузил и закрыл, версия (для приёмки/возврата).

        expected_on — ожидаемая дата прибытия «для справки» (D-30) или None; act — акт недостачи (D-30) или None;
        loss — акт об утрате (D-31) или None: тогда действующий статус «lost», хотя в БД строка осталась in_transit.
        act_sig / loss_sig — подписи актов (D-31). accepted_qty — сколько фактически пришло на получатель.
        """
        status = "lost" if loss else o["status"]
        a = _v_act(act, o, act_sig) if act else None
        accepted = (act["accepted_qty"] if act else o["qty"]) if status == "received" else None
        return {"id": o["id"], "item_id": o["item_id"], "from_warehouse_id": o["from_warehouse_id"],
                "to_warehouse_id": o["to_warehouse_id"], "qty": money.to_json(o["qty"]), "status": status,
                "status_label": stock.SHORTAGE_STATUS_LABEL_RU if act else stock.TRANSFER_STATUS_LABELS_RU[status],
                "accepted_qty": money.to_json(accepted) if accepted is not None else None,
                "expected_on": expected_on.isoformat() if expected_on else None, "act": a,
                "loss": _v_loss(loss, o, loss_sig) if loss else None,
                "out_movement_id": o["out_movement_id"], "close_movement_id": o["close_movement_id"],
                "source_doc": o["source_doc"], "reason": o["reason"], "close_reason": o["close_reason"],
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "closed_by": o["closed_by"], "closed_at": iso(o["closed_at"]) if o["closed_at"] else None,
                "legacy": False}

    @staticmethod
    def v_transfer(o: dict) -> dict:
        """Одношаговое перемещение схемы v8 (история) в том же виде, что и v_shipment: сразу «Принято», без версии —
        менять его нечем. legacy=True, чтобы клиент не предлагал действий.
        """
        return {"id": o["id"], "item_id": o["item_id"], "from_warehouse_id": o["from_warehouse_id"],
                "to_warehouse_id": o["to_warehouse_id"], "qty": money.to_json(o["qty"]), "status": "received",
                "status_label": stock.TRANSFER_STATUS_LABELS_RU["received"],
                "out_movement_id": o["out_movement_id"], "close_movement_id": o["in_movement_id"],
                "source_doc": o["source_doc"], "reason": o["reason"], "close_reason": None, "version": None,
                "created_by": o["posted_by"], "created_at": iso(o["posted_at"]), "closed_by": o["posted_by"],
                "closed_at": iso(o["posted_at"]), "legacy": True, "accepted_qty": money.to_json(o["qty"]),
                "expected_on": None, "act": None, "loss": None}

    def _shipment_view(self, conn, sid: str) -> dict:
        """Перемещение sid в виде для клиента — вместе со сроком прибытия и актом недостачи (если есть)."""
        return self._shipment_views(conn, stock_shipments.c.id == sid)[0]

    @staticmethod
    def _shipment_views(conn, where) -> list[dict]:
        """Перемещения по условию where — одним запросом с внешними соединениями к срокам и актам (D-30), без длинных
        списков id в запросе (у MS SQL предел ~2100 параметров).
        """
        asig, lsig = signatures.alias("asig"), signatures.alias("lsig")
        cols = ([c.label("act__" + c.name) for c in stock_transfer_acts.c] +
                [c.label("los__" + c.name) for c in stock_shipment_losses.c] +
                [c.label("asg__" + c.name) for c in asig.c] + [c.label("lsg__" + c.name) for c in lsig.c])
        q = (sa.select(stock_shipments, stock_shipment_terms.c.expected_on, *cols)
             .select_from(stock_shipments.outerjoin(stock_shipment_terms,
                                                    stock_shipment_terms.c.shipment_id == stock_shipments.c.id)
                          .outerjoin(stock_transfer_acts, stock_transfer_acts.c.shipment_id == stock_shipments.c.id)
                          .outerjoin(stock_shipment_losses, stock_shipment_losses.c.shipment_id == stock_shipments.c.id)
                          .outerjoin(asig, sa.and_(asig.c.object_type == "stock_transfer_act",
                                                   asig.c.object_id == stock_transfer_acts.c.id, asig.c.purpose == "act"))
                          .outerjoin(lsig, sa.and_(lsig.c.object_type == "stock_loss_act",
                                                   lsig.c.object_id == stock_shipments.c.id, lsig.c.purpose == "act")))
             .where(where))
        out = []
        for r in conn.execute(q):
            m = dict(r._mapping)
            part = {pre: {k[5:]: m.pop(k) for k in list(m) if k.startswith(pre + "__")} for pre in ("act", "los", "asg", "lsg")}
            exp = m.pop("expected_on")
            act = part["act"] if part["act"].get("id") else None
            loss = part["los"] if part["los"].get("shipment_id") else None
            out.append(StockMixin.v_shipment(m, exp, act, part["asg"], loss, part["lsg"]))
        return out

    def _sign(self, ctx, p: dict, object_type: str, object_id: str, content: dict) -> dict:
        """Подписать документ приёмщиком склада способом из настройки stock.act_signature (D-31).

        * sign_act = true — явное «подписываю» в любом способе (подпись не ставится «по умолчанию»);
        * password — ещё и пароль вошедшего пользователя (sign_password): защищает от действия с чужого незаблокированного
          компьютера. Неверных паролей — не больше порога блокировки входа за минуту (sign_rate); пароль никуда не
          сохраняется и в хэш запроса не входит (vector.core.redact);
        * qes — КЭП: пока не подключена → отказ (fail-closed; опубликовать такое значение настройки тоже нельзя).

        Записывает неизменяемую строку signatures с SHA-256 содержания документа. Вызывается внутри транзакции команды:
        любой отказ откатывает и сам документ.
        """
        if p.get("sign_act") is not True:
            raise Rejected("act_not_signed", "Акт не подписан: подтвердите, что вы — приёмщик и количество пересчитано.",
                           {"field": "sign_act"})
        method = self.config_value(ctx.conn, "stock.act_signature")
        if method == "qes":
            raise Rejected("qes_unavailable", "В настройках выбрана подпись КЭП, но она ещё не подключена — акт подписать "
                           "нельзя. Обратитесь к администратору.")
        if method == "password":
            pwd = p.get("sign_password")
            if not isinstance(pwd, str) or not pwd:
                raise Rejected("sign_password_required", "Для подписи акта введите свой пароль.", {"field": "sign_password"})
            if self.sign_rate.blocked(ctx.user_id, ctx.now.timestamp()):
                raise Rejected("rate_limited", "Слишком много неверных паролей подписи. Подождите минуту.")
            if not security.verify_password(ctx.user["pwd_hash"], pwd)[0]:
                # Неудача учитывается (allow записывает попытку). Отказ откатывает транзакцию, но счётчик — в памяти
                # сервера, поэтому учёт сохраняется.
                self.sign_rate.allow(ctx.user_id, ctx.now.timestamp())
                raise Rejected("bad_sign_password", "Пароль неверный — акт не подписан, ничего не проведено.",
                               {"field": "sign_password"})
        sig = {"id": str(uuid.uuid5(uuid.UUID(object_id), "signature/" + object_type)), "object_type": object_type,
               "object_id": object_id, "purpose": "act", "method": method, "signer_id": ctx.user_id,
               "signer_name": content["signer_name"], "signed_at": ctx.now, "content_sha256": _digest(content)}
        ctx.conn.execute(signatures.insert().values(**sig))
        return sig

    def _loss_of(self, conn, sid: str):
        """Акт об утрате перемещения sid (строка) или None."""
        return conn.execute(sa.select(stock_shipment_losses).where(stock_shipment_losses.c.shipment_id == sid)).first()

    @staticmethod
    def _legs(conn, since=None) -> dict[str, dict]:
        """Какие движения журнала относятся к перемещениям: movement_id → {transfer_id, direction, peer_warehouse_id}.

        since — не раньше этого времени (для pull: окно последних движений; движения перемещения проведены в моменты
        его отгрузки и закрытия). Учитываются и одношаговые перемещения v8, и перемещения «в пути» v9.
        """
        legs: dict[str, dict] = {}
        q1, q2 = sa.select(stock_transfers), sa.select(stock_shipments)
        if since is not None:
            q1 = q1.where(stock_transfers.c.posted_at >= since)
            q2 = q2.where(sa.or_(stock_shipments.c.created_at >= since, stock_shipments.c.closed_at >= since))
        for t in conn.execute(q1):
            legs[t.out_movement_id] = {"transfer_id": t.id, "direction": "out", "peer_warehouse_id": t.to_warehouse_id}
            legs[t.in_movement_id] = {"transfer_id": t.id, "direction": "in", "peer_warehouse_id": t.from_warehouse_id}
        for t in conn.execute(q2):
            legs[t.out_movement_id] = {"transfer_id": t.id, "direction": "out", "peer_warehouse_id": t.to_warehouse_id}
            if t.close_movement_id:
                # Приёмка — приход на получателе (второй склад — отправитель); возврат — приход обратно на отправителе
                # (второй склад — тот, куда товар так и не доехал).
                legs[t.close_movement_id] = ({"transfer_id": t.id, "direction": "in", "peer_warehouse_id": t.from_warehouse_id}
                                             if t.status == "received" else
                                             {"transfer_id": t.id, "direction": "return", "peer_warehouse_id": t.to_warehouse_id})
        return legs

    # ------------------------------------------------------------ справочники
    def _warehouse_create(self, ctx, p, ev):
        """warehouse.create {id, code, name, note?} — новый склад (A-20). Можно офлайн (очередь). Код уникален."""
        only_keys(p, {"id", "code", "name", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "warehouse.create", ctx.now)
        wid, code = req_uuid(p, "id"), _code(p)
        self._ensure_new(ctx.conn, warehouses, wid)
        if self._get(ctx.conn, warehouses, code, "code"):
            raise Rejected("code_exists", f"Склад с кодом «{code}» уже есть.", {"field": "code"})
        row = {"id": wid, "code": code, "name": req_str(p, "name", 200), "note": opt_str(p, "note", 1000), "active": True,
               "version": 1, "created_by": ctx.user_id, "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(warehouses.insert().values(**row))
        ctx.log("warehouse", wid, auth, None, 1, {"code": code, "name": row["name"]})
        return {"warehouse": self.v_warehouse(self._get(ctx.conn, warehouses, wid))}

    def _warehouse_update(self, ctx, p, ev):
        """warehouse.update {id, name?, note?, active?} + версия — изменить или отключить склад (код не меняется). На
        отключённый склад нельзя приходовать и резервировать.
        """
        only_keys(p, {"id", "name", "note", "active"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "warehouse.update", ctx.now)
        o = self._require(ctx.conn, warehouses, req_uuid(p, "id"), "Склад")
        self._check_version(o, ev, self.v_warehouse)
        ch: dict[str, Any] = {}
        if "name" in p:
            ch["name"] = req_str(p, "name", 200)
        if "note" in p:
            ch["note"] = opt_str(p, "note", 1000)
        if "active" in p:
            ch["active"] = _bool(p, "active")
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (bool(o[k]) if k == "active" else o[k]) for k in ch}
        n = self._update_versioned(ctx, warehouses, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("warehouse", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"warehouse": self.v_warehouse(n)}

    def _item_create(self, ctx, p, ev):
        """item.create {id, code, name, unit, note?, pack_qty?} — новая позиция номенклатуры (склад или закупка,
        A-20). Можно офлайн. pack_qty — кратность заказа (барабан/упаковка; D-59): необязательна, > 0.
        """
        only_keys(p, {"id", "code", "name", "unit", "note", "pack_qty"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "item.create", ctx.now)
        iid, code = req_uuid(p, "id"), _code(p)
        self._ensure_new(ctx.conn, stock_items, iid)
        if self._get(ctx.conn, stock_items, code, "code"):
            raise Rejected("code_exists", f"Позиция номенклатуры с кодом «{code}» уже есть.", {"field": "code"})
        pack = self._pack_qty_of(p)
        row = {"id": iid, "code": code, "name": req_str(p, "name", 300), "unit": req_str(p, "unit", 20),
               "note": opt_str(p, "note", 1000), "active": True, "version": 1, "created_by": ctx.user_id,
               "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(stock_items.insert().values(**row))
        if pack is not None:
            ctx.conn.execute(stock_item_packs.insert().values(item_id=iid, pack_qty=pack))
        ctx.log("stock_item", iid, auth, None, 1, {"code": code, "name": row["name"], "unit": row["unit"],
                                                   "pack_qty": money.to_json(pack)})
        return {"stock_item": self.v_item(self._get(ctx.conn, stock_items, iid), pack)}

    def _pack_qty_of(self, p: dict):
        """Кратность заказа из запроса: None — не задана; иначе положительное количество (ошибка — отказ)."""
        v = p.get("pack_qty")
        if v in (None, ""):
            return None
        try:
            q = money.parse_decimal(v)
        except money.MoneyError as e:
            raise Rejected(e.code, f"«Кратность заказа»: {e.message_ru}", {"field": "pack_qty"})
        if q <= 0:
            raise Rejected("qty_not_positive", "Кратность заказа должна быть больше нуля.", {"field": "pack_qty"})
        return q

    def _pack_of(self, conn, iid: str) -> Optional[Decimal]:
        """Кратность заказа позиции (None — не задана)."""
        r = self._get(conn, stock_item_packs, iid, "item_id")
        return r["pack_qty"] if r else None

    def _item_update(self, ctx, p, ev):
        """item.update {id, name?, unit?, note?, active?} + версия — изменить позицию.

        Единицу измерения нельзя менять, если по позиции уже были остатки или движения: прошлые количества молча
        поменяли бы смысл. Поля, совпадающие с текущими значениями, не считаются изменением.
        """
        only_keys(p, {"id", "name", "unit", "note", "active", "pack_qty"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "item.update", ctx.now)
        o = self._require(ctx.conn, stock_items, req_uuid(p, "id"), "Номенклатура")
        self._check_version(o, ev, lambda x: self.v_item(x, self._pack_of(ctx.conn, x["id"])))
        ch: dict[str, Any] = {}
        if "name" in p:
            ch["name"] = req_str(p, "name", 300)
        if "unit" in p:
            ch["unit"] = req_str(p, "unit", 20)
        if "note" in p:
            ch["note"] = opt_str(p, "note", 1000)
        if "active" in p:
            ch["active"] = _bool(p, "active")
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        # Кратность заказа (D-59) — таблица-расширение: null/"" снимает кратность, значение — ставит.
        pack_old = self._pack_of(ctx.conn, o["id"])
        pack_new = self._pack_qty_of(p) if "pack_qty" in p else pack_old
        pack_changed = pack_new != pack_old
        if not ch and not pack_changed:
            raise Rejected("no_changes", "Нет изменений.")
        if "unit" in ch:
            used = ctx.conn.execute(sa.select(sa.func.count()).select_from(stock_balances)
                                    .where(stock_balances.c.item_id == o["id"])).scalar()
            if used:
                # Иначе все прошлые количества молча поменяли бы смысл (было «м», стало «шт»).
                raise Rejected("unit_locked", "По позиции уже есть остатки или движения — единицу измерения менять нельзя. "
                               "Заведите новую позицию номенклатуры.", {"field": "unit"})
        before = {k: (bool(o[k]) if k == "active" else o[k]) for k in ch}
        n = self._update_versioned(ctx, stock_items, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        if pack_changed:
            ctx.conn.execute(stock_item_packs.delete().where(stock_item_packs.c.item_id == o["id"]))
            if pack_new is not None:
                ctx.conn.execute(stock_item_packs.insert().values(item_id=o["id"], pack_qty=pack_new))
        if pack_changed:
            before["pack_qty"], ch["pack_qty"] = money.to_json(pack_old), money.to_json(pack_new)
        ctx.log("stock_item", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"stock_item": self.v_item(n, pack_new)}

    # ------------------------------------------------------------ справочник аналогов и пересчёта (D-25, A-27)
    def _alias_text(self, ctx, p, exclude_id: Optional[str] = None) -> str:
        """Другое обозначение из запроса: непустое, ≤ 100 символов, не совпадает с кодом позиции номенклатуры и с
        другим обозначением. Иначе одно обозначение указывало бы на две позиции — автомат сопоставлял бы наугад.
        """
        alias = req_str(p, "alias", 100).strip()
        if not alias:
            raise Rejected("field_required", "Поле «alias» обязательно.", {"field": "alias"})
        if self._get(ctx.conn, stock_items, alias, "code"):
            raise Rejected("alias_is_code", f"«{alias}» — уже код позиции номенклатуры; другим обозначением он быть не может.",
                           {"field": "alias"})
        dup = self._get(ctx.conn, item_aliases, alias, "alias")
        if dup and dup["id"] != exclude_id:
            it = self._get(ctx.conn, stock_items, dup["item_id"])
            raise Rejected("alias_exists", f"Обозначение «{alias}» уже относится к позиции «{it['code']}».", {"field": "alias"})
        return alias

    def _item_alias_create(self, ctx, p, ev):
        """item_alias.create {id, item_id, alias, note?} — другое обозначение позиции (аналог). Только при связи.

        По нему need.generate сопоставляет строку расчёта автоматически, если единица строки совпадает с единицей
        позиции (иначе строка остаётся для ручного решения закупки с пересчётом).
        """
        only_keys(p, {"id", "item_id", "alias", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "item_alias.create", ctx.now)
        aid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, item_aliases, aid)
        it = self._require(ctx.conn, stock_items, req_uuid(p, "item_id"), "Номенклатура")
        alias = self._alias_text(ctx, p)
        ctx.conn.execute(item_aliases.insert().values(
            id=aid, item_id=it["id"], alias=alias, note=opt_str(p, "note", 500), active=True, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("item_alias", aid, auth, None, 1, {"item_code": it["code"], "alias": alias})
        return {"item_alias": self.v_alias(self._get(ctx.conn, item_aliases, aid))}

    def _item_alias_update(self, ctx, p, ev):
        """item_alias.update {id, alias?, note?, active?} + версия. Позицию у обозначения не меняют — заведите новое
        (иначе история «это обозначение значило X» потерялась бы).
        """
        only_keys(p, {"id", "alias", "note", "active"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "item_alias.update", ctx.now)
        o = self._require(ctx.conn, item_aliases, req_uuid(p, "id"), "Обозначение")
        self._check_version(o, ev, self.v_alias)
        ch: dict[str, Any] = {}
        if "alias" in p:
            ch["alias"] = self._alias_text(ctx, p, exclude_id=o["id"])
        if "note" in p:
            ch["note"] = opt_str(p, "note", 500)
        if "active" in p:
            ch["active"] = _bool(p, "active")
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (bool(o[k]) if k == "active" else o[k]) for k in ch}
        n = self._update_versioned(ctx, item_aliases, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("item_alias", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"item_alias": self.v_alias(n)}

    def _unit_conversion_create(self, ctx, p, ev):
        """unit_conversion.create {id, from_unit, to_unit, factor, item_id?, note?} — «1 from_unit = factor to_unit».

        item_id не указан — общий пересчёт («км → м» = 1000) для любой позиции в to_unit; указан — только для этой
        позиции, и to_unit обязан быть её единицей. Коэффициент — Decimal > 0, ≤ 4 знаков (A-27); пара единиц
        уникальна в своей области (общий / позиция).
        """
        only_keys(p, {"id", "from_unit", "to_unit", "factor", "item_id", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "unit_conversion.create", ctx.now)
        cid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, unit_conversions, cid)
        fu, tu = req_str(p, "from_unit", 20).strip(), req_str(p, "to_unit", 20).strip()
        if not fu or not tu:
            raise Rejected("field_required", "Укажите обе единицы.", {"field": "from_unit" if not fu else "to_unit"})
        if fu == tu:
            raise Rejected("units_same", "Единицы совпадают — пересчёт не нужен.", {"field": "to_unit"})
        factor = _qty(p, "factor")
        it = None
        if p.get("item_id") is not None:
            it = self._require(ctx.conn, stock_items, req_uuid(p, "item_id"), "Номенклатура")
            if it["unit"] != tu:
                raise Rejected("to_unit_mismatch", f"Единица позиции «{it['code']}» — «{it['unit']}»; пересчёт для позиции "
                               f"должен вести в неё, а не в «{tu}».", {"field": "to_unit"})
        key = it["id"] if it else "*"
        dup = ctx.conn.execute(sa.select(unit_conversions.c.id).where(
            unit_conversions.c.item_key == key, unit_conversions.c.from_unit == fu, unit_conversions.c.to_unit == tu)).first()
        if dup:
            raise Rejected("conversion_exists", f"Пересчёт «{fu} → {tu}»" + (f" для «{it['code']}»" if it else "") +
                           " уже есть — измените его, а не заводите второй.", {"conversion_id": dup.id})
        ctx.conn.execute(unit_conversions.insert().values(
            id=cid, item_id=it["id"] if it else None, item_key=key, from_unit=fu, to_unit=tu, factor=factor,
            note=opt_str(p, "note", 500), active=True, version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("unit_conversion", cid, auth, None, 1, {"from_unit": fu, "to_unit": tu, "factor": money.to_json(factor),
                                                         "item_code": it["code"] if it else None})
        return {"unit_conversion": self.v_conversion(self._get(ctx.conn, unit_conversions, cid))}

    def _unit_conversion_update(self, ctx, p, ev):
        """unit_conversion.update {id, factor?, note?, active?} + версия. Единицы и позицию не меняют — это уже
        другой пересчёт. Принятые решения о сопоставлении не меняются: их коэффициент зафиксирован (A-27).
        """
        only_keys(p, {"id", "factor", "note", "active"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "unit_conversion.update", ctx.now)
        o = self._require(ctx.conn, unit_conversions, req_uuid(p, "id"), "Пересчёт единиц")
        self._check_version(o, ev, self.v_conversion)
        ch: dict[str, Any] = {}
        if "factor" in p:
            ch["factor"] = _qty(p, "factor")
        if "note" in p:
            ch["note"] = opt_str(p, "note", 500)
        if "active" in p:
            ch["active"] = _bool(p, "active")
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        fmt = lambda k, v: money.to_json(v) if k == "factor" else v  # noqa: E731
        before = {k: fmt(k, bool(o[k]) if k == "active" else o[k]) for k in ch}
        n = self._update_versioned(ctx, unit_conversions, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("unit_conversion", o["id"], auth, o["version"], n["version"],
                {"before": before, "after": {k: fmt(k, v) for k, v in ch.items()}})
        return {"unit_conversion": self.v_conversion(n)}

    def _find_conversion(self, conn, item: dict, from_unit: str) -> Optional[dict]:
        """Действующий пересчёт из from_unit в единицу позиции: сначала для самой позиции, затем общий."""
        for key in (item["id"], "*"):
            r = conn.execute(sa.select(unit_conversions).where(
                unit_conversions.c.item_key == key, unit_conversions.c.from_unit == from_unit,
                unit_conversions.c.to_unit == item["unit"], unit_conversions.c.active == sa.true())).mappings().first()
            if r:
                return dict(r)
        return None

    # ------------------------------------------------------------ остаток (строка с версией)
    def _wh_item(self, ctx, p, for_new_stock: bool) -> tuple[dict, dict]:
        """Склад и позиция из запроса; для операций, увеличивающих запас или резерв (приход, резерв), оба должны быть
        действующими.
        """
        w = self._require(ctx.conn, warehouses, req_uuid(p, "warehouse_id"), "Склад")
        it = self._require(ctx.conn, stock_items, req_uuid(p, "item_id"), "Номенклатура")
        if for_new_stock and not w["active"]:
            raise Rejected("warehouse_inactive", f"Склад «{w['code']}» отключён: приход и резерв на него невозможны.")
        if for_new_stock and not it["active"]:
            raise Rejected("item_inactive", f"Позиция «{it['code']}» отключена: приход и резерв по ней невозможны.")
        return w, it

    def _load_balance(self, conn, wid: str, iid: str) -> tuple[Optional[dict], stock.Balance]:
        """Строка остатка (или None, если движений ещё не было) и значение Balance для правил ядра."""
        r = conn.execute(sa.select(stock_balances).where(stock_balances.c.warehouse_id == wid,
                                                         stock_balances.c.item_id == iid)).first()
        if r is None:
            return None, stock.Balance()
        row = dict(r._mapping)
        return row, stock.Balance(row["on_hand"], row["reserved"])

    def _save_balance(self, ctx, wid: str, iid: str, row: Optional[dict], b: stock.Balance) -> None:
        """Записать новый остаток. Обновление — только при неизменной version (защита от гонки поверх SERIALIZABLE)."""
        if row is None:
            ctx.conn.execute(stock_balances.insert().values(warehouse_id=wid, item_id=iid, on_hand=b.on_hand,
                                                            reserved=b.reserved, version=1, updated_at=ctx.now))
            return
        n = ctx.conn.execute(stock_balances.update().where(
            stock_balances.c.warehouse_id == wid, stock_balances.c.item_id == iid,
            stock_balances.c.version == row["version"]).values(
            on_hand=b.on_hand, reserved=b.reserved, version=row["version"] + 1, updated_at=ctx.now)).rowcount
        if n != 1:
            raise Conflict("stock_conflict", "Остаток изменился параллельно. Обновите данные и повторите.", {"current": None})

    def _post(self, ctx, auth, *, mid: str, kind: str, w: dict, it: dict, qty: Decimal, project_id=None,
              reservation_id=None, reverses_id=None, source_doc=None, reason=None, details=None) -> dict:
        """Провести движение в журнал: следующий сквозной номер документа, запись (неизменяемая — триггер БД), журнал
        аудита с кодами и единицей. Возвращает представление движения.
        """
        no = (ctx.conn.execute(sa.select(sa.func.max(stock_movements.c.doc_no))).scalar() or 0) + 1
        ctx.conn.execute(stock_movements.insert().values(
            id=mid, doc_no=no, kind=kind, warehouse_id=w["id"], item_id=it["id"], qty=qty, project_id=project_id,
            reservation_id=reservation_id, reverses_id=reverses_id, source_doc=source_doc, reason=reason,
            posted_by=ctx.user_id, posted_at=ctx.now))
        ctx.log("stock_movement", mid, auth, None, None,
                {"doc_no": no, "kind": kind, "warehouse_code": w["code"], "item_code": it["code"], "unit": it["unit"],
                 "qty": money.to_json(qty), "project_id": project_id, "reservation_id": reservation_id,
                 "reverses_id": reverses_id, "source_doc": source_doc, "reason": reason, **(details or {})})
        return self.v_movement(self._get(ctx.conn, stock_movements, mid))

    def _project_ref(self, ctx, pid: str) -> dict:
        """Проект по id для складской операции (выдача/резерв под проект) или отказ not_found."""
        return self._require(ctx.conn, projects, pid, "Проект")

    # ------------------------------------------------------------ проводки
    def _stock_receive(self, ctx, p, ev):
        """stock.receive {id, warehouse_id, item_id, qty, source_doc, reason?} — приход на склад. Только онлайн, только
        склад (A-21).

        Основание (накладная, акт) обязательно (A-24). Остаток увеличивается в той же транзакции, что и запись
        движения.
        """
        only_keys(p, {"id", "warehouse_id", "item_id", "qty", "source_doc", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.receive", ctx.now)
        mid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_movements, mid)
        w, it = self._wh_item(ctx, p, for_new_stock=True)
        qty = _qty(p)
        src = req_str(p, "source_doc", 200)      # основание прихода обязательно (накладная, акт…) — A-24
        row, b = self._load_balance(ctx.conn, w["id"], it["id"])
        try:
            nb = stock.receive(b, qty)
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        mv = self._post(ctx, auth, mid=mid, kind="receipt", w=w, it=it, qty=qty, source_doc=src,
                        reason=opt_str(p, "reason", 2000), details={"balance_after": nb.to_json()})
        return {"movement": mv, "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()}}

    def _stock_issue(self, ctx, p, ev):
        """Выдача на проект: из доступного или по резерву (тогда проект берётся из резерва)."""
        only_keys(p, {"id", "warehouse_id", "item_id", "qty", "project_id", "reservation_id", "reason", "work_order_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.issue", ctx.now)
        mid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_movements, mid)
        w, it = self._wh_item(ctx, p, for_new_stock=False)
        qty = _qty(p)
        reason = req_str(p, "reason", 2000)
        res = None
        if p.get("reservation_id") is not None:
            res = self._require(ctx.conn, stock_reservations, req_uuid(p, "reservation_id"), "Резерв")
            if res["status"] != "active":
                raise Rejected("reservation_state", f"Резерв уже {stock.RESERVATION_LABELS_RU[res['status']].lower()} — "
                               "выдача по нему невозможна.")
            if (res["warehouse_id"], res["item_id"]) != (w["id"], it["id"]):
                raise Rejected("reservation_mismatch", "Резерв относится к другому складу или другой позиции.")
            pid = res["project_id"]
            if p.get("project_id") is not None and p["project_id"] != pid:
                raise Rejected("reservation_mismatch", "Резерв сделан на другой проект.")
        else:
            pid = req_uuid(p, "project_id")
        pr = self._project_ref(ctx, pid)
        # Выдача на наряд (D-36): наряд того же проекта и открытый; связь — неизменяемая строка work_order_issues.
        wo = self._wo_for_issue(ctx, p.get("work_order_id"), pid)
        row, b = self._load_balance(ctx.conn, w["id"], it["id"])
        try:
            if res is None:
                nb = stock.issue_free(b, qty)
            else:
                nb = stock.issue_reserved(b, qty, res["qty"] - res["consumed"])
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        res_view = None
        if res is not None:
            consumed = res["consumed"] + qty
            ch = {"consumed": consumed, "updated_by": ctx.user_id, "updated_at": ctx.now}
            if consumed == res["qty"]:
                ch["status"] = "consumed"
            n = self._update_versioned(ctx, stock_reservations, res, ch)
            ctx.log("stock_reservation", res["id"], auth, res["version"], n["version"],
                    {"issued": money.to_json(qty), "consumed": money.to_json(consumed), "status": n["status"],
                     "movement_id": mid})
            res_view = self.v_reservation(n)
        mv = self._post(ctx, auth, mid=mid, kind="issue", w=w, it=it, qty=qty, project_id=pid,
                        reservation_id=res["id"] if res else None, reason=reason,
                        details={"project_code": pr["code"], "balance_after": nb.to_json(),
                                 **({"work_order_id": wo["id"], "work_order_no": wo["doc_no"]} if wo else {})})
        out = {"movement": mv, "reservation": res_view,
               "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()}}
        if wo is not None:
            ctx.conn.execute(work_order_issues.insert().values(movement_id=mid, work_order_id=wo["id"]))
            out["work_order"] = self.v_work_order(ctx.conn, wo)
        return out

    def _stock_reverse(self, ctx, p, ev):
        """Сторно: встречное движение. Резерв, израсходованный выдачей, НЕ восстанавливается (товар — в доступное)."""
        only_keys(p, {"id", "movement_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.reverse", ctx.now)
        mid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_movements, mid)
        orig = self._require(ctx.conn, stock_movements, req_uuid(p, "movement_id"), "Движение")
        reason = req_str(p, "reason", 2000)
        if orig["reverses_id"] is not None:
            raise Rejected("cannot_reverse_storno", "Сторно не сторнируется. Если сторно ошибочно — проведите движение заново.")
        tr = self._transfer_of_movement(ctx.conn, orig["id"])
        if tr is not None:
            # Сторно одного движения перемещения «потеряло» бы товар: он ушёл бы с одного склада, оставшись учтённым на
            # другом (или «в пути»). Перемещение исправляют целиком (A-28, D-29).
            codes = {w.id: w.code for w in ctx.conn.execute(sa.select(warehouses.c.id, warehouses.c.code).where(
                warehouses.c.id.in_([tr["from"], tr["to"]])))}
            a, b = codes[tr["from"]], codes[tr["to"]]
            fix = ("Перемещение ещё в пути: примите его на складе-получателе или верните отправителю."
                   if tr["status"] == "in_transit" else
                   f"Перемещение утрачено (акт об утрате № {tr.get('loss_act_no')}); нашёлся груз — оформите приход с "
                   "основанием «найдено по акту об утрате»." if tr["status"] == "lost" else
                   f"Ошибочное перемещение исправляют обратным перемещением «{b} → {a}».")
            raise Rejected("transfer_leg", f"Движение №{orig['doc_no']} — часть перемещения «{a} → {b}». Отдельно его не "
                           f"сторнируют. {fix}", {"transfer_id": tr["id"]})
        done = ctx.conn.execute(sa.select(stock_movements.c.id).where(stock_movements.c.reverses_id == orig["id"])).first()
        if done:
            raise Rejected("already_reversed", f"Движение №{orig['doc_no']} уже сторнировано.", {"reversed_by": done.id})
        if orig["kind"] == "receipt":
            # Приход по строке заказа из закупочного пакета распределён по распределениям (D-59): сторно снимает
            # созданные резервы (их остаток возвращается в доступное, и приход можно сторнировать). Если по
            # резерву уже была выдача — сторнировать приход нельзя: сначала сторнируйте выдачу.
            dists = [dict(r._mapping) for r in ctx.conn.execute(
                sa.select(procurement_allocation_receipts)
                .where(procurement_allocation_receipts.c.movement_id == orig["id"]))]
            ress = {d["reservation_id"]: self._get(ctx.conn, stock_reservations, d["reservation_id"])
                    for d in dists if d["reservation_id"]}
            for res in ress.values():
                if res is not None and (res["consumed"] > 0 or res["status"] == "consumed"):
                    raise Rejected("receipt_consumed",
                                   f"Приход №{orig['doc_no']} распределён в резервы, по которым уже есть выдача — "
                                   "сторнировать его нельзя. Сначала сторнируйте выдачу, затем приход.",
                                   {"movement_id": orig["id"], "reservation_id": res["id"]})
            for res in ress.values():
                if res is not None and res["status"] == "active":
                    self._release_reservation(ctx, res, f"Сторно прихода № {orig['doc_no']}", auth,
                                              {"reverses_movement_id": orig["id"]})
        w = self._get(ctx.conn, warehouses, orig["warehouse_id"])
        it = self._get(ctx.conn, stock_items, orig["item_id"])
        row, b = self._load_balance(ctx.conn, w["id"], it["id"])
        try:
            nb = stock.reverse_receipt(b, orig["qty"]) if orig["kind"] == "receipt" else stock.receive(b, orig["qty"])
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        kind = "issue" if orig["kind"] == "receipt" else "receipt"
        mv = self._post(ctx, auth, mid=mid, kind=kind, w=w, it=it, qty=orig["qty"], project_id=orig["project_id"],
                        reverses_id=orig["id"], source_doc=orig["source_doc"], reason=reason,
                        details={"reverses_doc_no": orig["doc_no"], "balance_after": nb.to_json()})
        return {"movement": mv, "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()}}

    # ------------------------------------------------------------ перемещение между складами
    def _transfer_of_movement(self, conn, mid: str) -> Optional[dict]:
        """Перемещение, к которому относится движение mid (v8 или v9), или None: {id, from, to, status}."""
        t = conn.execute(sa.select(stock_transfers).where(sa.or_(stock_transfers.c.out_movement_id == mid,
                                                                 stock_transfers.c.in_movement_id == mid))).first()
        if t is not None:
            return {"id": t.id, "from": t.from_warehouse_id, "to": t.to_warehouse_id, "status": "received"}
        t = conn.execute(sa.select(stock_shipments).where(sa.or_(stock_shipments.c.out_movement_id == mid,
                                                                 stock_shipments.c.close_movement_id == mid))).first()
        if t is not None:
            lo = self._loss_of(conn, t.id)              # утраченное в БД остаётся in_transit (D-31)
            return {"id": t.id, "from": t.from_warehouse_id, "to": t.to_warehouse_id,
                    "status": "lost" if lo is not None else t.status, "loss_act_no": lo.act_no if lo is not None else None}
        return None

    def _stock_transfer(self, ctx, p, ev):
        """stock.transfer {id, item_id, from_warehouse_id, to_warehouse_id, qty, reason, source_doc?, expected_on?} — ОТГРУЗИТЬ
        количество позиции на другой склад (A-28, D-29). Только онлайн, только склад.

        Одна транзакция: расход (issue) на отправителе + запись перемещения в статусе «В пути». Приход на получателе
        будет при приёмке (stock.transfer_accept); до тех пор товара нет ни на одном складе — его нельзя выдать или
        зарезервировать, а в клиенте он показан отдельно как «в пути».

        Отгружать можно только ДОСТУПНОЕ на отправителе (остаток − резервы): резерв закреплён за складом, и «увезти»
        его молча нельзя — сначала снять резерв (перемещение вместе с резервом — открытый вопрос A-28). Получатель
        должен быть действующим складом; отправитель может быть отключён (так освобождают закрываемый склад).
        Позиция может быть отключена: запас компании не растёт.

        expected_on (ГГГГ-ММ-ДД, по желанию) — ожидаемая дата прибытия «для справки» (D-30): не раньше дня отгрузки
        (UTC), ни на что не влияет.
        """
        only_keys(p, {"id", "item_id", "from_warehouse_id", "to_warehouse_id", "qty", "reason", "source_doc",
                      "expected_on"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.transfer", ctx.now)
        tid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_shipments, tid)
        self._ensure_new(ctx.conn, stock_transfers, tid)      # id перемещений v8 и v9 не пересекаются
        it = self._require(ctx.conn, stock_items, req_uuid(p, "item_id"), "Номенклатура")
        src = self._require(ctx.conn, warehouses, req_uuid(p, "from_warehouse_id"), "Склад-отправитель")
        dst = self._require(ctx.conn, warehouses, req_uuid(p, "to_warehouse_id"), "Склад-получатель")
        if src["id"] == dst["id"]:
            raise Rejected("same_warehouse", "Склад-отправитель и склад-получатель совпадают — перемещать некуда.")
        if not dst["active"]:
            raise Rejected("warehouse_inactive", f"Склад «{dst['code']}» отключён: перемещение на него невозможно.")
        qty = _qty(p)
        reason = req_str(p, "reason", 2000)              # зачем перемещаем — обязательно, как у выдачи (A-24)
        doc = opt_str(p, "source_doc", 200)              # накладная на перемещение — если оформляется
        expected_on = opt_date(p, "expected_on")         # срок прибытия «для справки» (D-30)
        if expected_on is not None and expected_on < ctx.now.date():
            raise Rejected("expected_before_shipment", "Ожидаемая дата прибытия раньше дня отгрузки "
                           f"({ctx.now.date().strftime('%d.%m.%Y')}, UTC).", {"field": "expected_on"})
        row_s, b_s = self._load_balance(ctx.conn, src["id"], it["id"])
        try:
            nb_s = stock.issue_free(b_s, qty)            # только из доступного на отправителе
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, src["id"], it["id"], row_s, nb_s)
        # id движений выводятся из id перемещения: повтор транзакции (_tx) даёт те же id, а по журналу видно, какой
        # операции принадлежит движение.
        out_id = str(uuid.uuid5(uuid.UUID(tid), "out"))
        common = {"transfer_id": tid, "from_warehouse_code": src["code"], "to_warehouse_code": dst["code"]}
        self._post(ctx, auth, mid=out_id, kind="issue", w=src, it=it, qty=qty, source_doc=doc, reason=reason,
                   details={**common, "balance_after": nb_s.to_json()})
        ctx.conn.execute(stock_shipments.insert().values(
            id=tid, item_id=it["id"], from_warehouse_id=src["id"], to_warehouse_id=dst["id"], qty=qty,
            status="in_transit", out_movement_id=out_id, source_doc=doc, reason=reason, version=1,
            created_by=ctx.user_id, created_at=ctx.now))
        if expected_on is not None:
            ctx.conn.execute(stock_shipment_terms.insert().values(shipment_id=tid, expected_on=expected_on))
        ctx.log("stock_transfer", tid, auth, None, 1,
                {**common, "status": "in_transit", "item_code": it["code"], "unit": it["unit"], "qty": money.to_json(qty),
                 "out_movement_id": out_id, "source_doc": doc, "reason": reason,
                 "expected_on": expected_on.isoformat() if expected_on else None})
        out = self._get(ctx.conn, stock_movements, out_id)
        return {"transfer": self._shipment_view(ctx.conn, tid),
                "movements": [self.v_movement(out, leg={"transfer_id": tid, "direction": "out",
                                                        "peer_warehouse_id": dst["id"]})],
                "balances": [{"warehouse_id": src["id"], "item_id": it["id"], **nb_s.to_json()}]}

    def _close_shipment(self, ctx, p, ev, command: str, outcome: str):
        """Общая часть приёмки и возврата: проверить право, версию и статус «В пути», провести приход (на получателе
        или обратно на отправителе), закрыть перемещение по version. Возвращает ответ команды.
        """
        auth = self.policy.check(ctx.conn, ctx.user_id, command, ctx.now)
        sh = self._require(ctx.conn, stock_shipments, req_uuid(p, "id"), "Перемещение")
        self._check_version(sh, ev, lambda o: self._shipment_view(ctx.conn, o["id"]))
        qty, shortage, short_reason = sh["qty"], Decimal(0), None     # сколько приходуется; недостача по акту
        if outcome == "received":
            note = opt_str(p, "comment", 2000)           # комментарий приёмки — необязателен
            if p.get("accepted_qty") is not None:
                qty = _qty(p, "accepted_qty")            # фактически пришло: > 0, ≤ 4 знаков
                if qty > sh["qty"]:
                    raise Rejected("accepted_exceeds_shipped", f"Принято ({money.to_json(qty)}) больше, чем отгружено "
                                   f"({money.to_json(sh['qty'])}). Излишек перемещением не оформляется — проведите "
                                   "его приходом с основанием.", {"field": "accepted_qty"})
                shortage = sh["qty"] - qty
            if shortage > 0:
                # Недостача — только актом, который подписывает приёмщик склада (D-30): причина обязательна, подпись
                # ставится ниже (_sign) способом из настройки stock.act_signature (D-31).
                short_reason = req_str(p, "shortage_reason", 2000)
        else:
            note = req_str(p, "reason", 2000)            # почему возвращаем — обязательно
        self._ensure_in_transit(ctx.conn, sh)
        it = self._get(ctx.conn, stock_items, sh["item_id"])
        src = self._get(ctx.conn, warehouses, sh["from_warehouse_id"])
        dst = self._get(ctx.conn, warehouses, sh["to_warehouse_id"])
        # Возврат принимается и на отключённый склад-отправитель: это не новый запас, а отмена отгрузки. Приёмка на
        # отключённый получатель — отказ с подсказкой (склад могли отключить, пока товар был в пути).
        if outcome == "received" and not dst["active"]:
            raise Rejected("warehouse_inactive", f"Склад-получатель «{dst['code']}» отключён: принять на него нельзя. "
                           "Включите склад или верните перемещение отправителю.")
        to_w = dst if outcome == "received" else src
        row, b = self._load_balance(ctx.conn, to_w["id"], it["id"])
        nb = stock.receive(b, qty)
        self._save_balance(ctx, to_w["id"], it["id"], row, nb)
        direction = "in" if outcome == "received" else "return"
        mid = str(uuid.uuid5(uuid.UUID(sh["id"]), direction))
        common = {"transfer_id": sh["id"], "from_warehouse_code": src["code"], "to_warehouse_code": dst["code"]}
        self._post(ctx, auth, mid=mid, kind="receipt", w=to_w, it=it, qty=qty, source_doc=sh["source_doc"],
                   reason=note or sh["reason"], details={**common, "direction": direction, "balance_after": nb.to_json()})
        n = self._update_versioned(ctx, stock_shipments, sh, {"status": outcome, "close_movement_id": mid,
                                                              "close_reason": note, "closed_by": ctx.user_id,
                                                              "closed_at": ctx.now})
        act_details = None
        if shortage > 0:
            # Акт недостачи: сквозной номер (max + 1; одновременные акты разойдутся по уникальному индексу и повтору
            # транзакции), имя подписанта — снимок на момент подписи. Неизменяем (триггеры БД).
            no = (ctx.conn.execute(sa.select(sa.func.max(stock_transfer_acts.c.act_no))).scalar() or 0) + 1
            signer = ctx.conn.execute(sa.select(users.c.display_name).where(users.c.id == ctx.user_id)).scalar_one()
            act_id = str(uuid.uuid5(uuid.UUID(sh["id"]), "act"))
            ctx.conn.execute(stock_transfer_acts.insert().values(
                id=act_id, shipment_id=sh["id"], act_no=no, shipped_qty=sh["qty"], accepted_qty=qty,
                shortage_qty=shortage, reason=short_reason, signed_by=ctx.user_id, signer_name=signer,
                signed_at=ctx.now))
            act_row = dict(self._get(ctx.conn, stock_transfer_acts, act_id))
            sig = self._sign(ctx, p, "stock_transfer_act", act_id, _act_content(sh, act_row))
            act_details = {"act_id": act_id, "act_no": no, "shortage_qty": money.to_json(shortage),
                           "shortage_reason": short_reason, "accepted_qty": money.to_json(qty),
                           "signature_method": sig["method"], "content_sha256": sig["content_sha256"]}
        ctx.log("stock_transfer", sh["id"], auth, sh["version"], n["version"],
                {**common, "status": outcome, "close_movement_id": mid, "close_reason": note,
                 "item_code": it["code"], "unit": it["unit"], "qty": money.to_json(sh["qty"]), "act": act_details})
        mv = self._get(ctx.conn, stock_movements, mid)
        peer = src["id"] if outcome == "received" else dst["id"]
        return {"transfer": self._shipment_view(ctx.conn, sh["id"]),
                "movements": [self.v_movement(mv, leg={"transfer_id": sh["id"], "direction": direction,
                                                       "peer_warehouse_id": peer})],
                "balances": [{"warehouse_id": to_w["id"], "item_id": it["id"], **nb.to_json()}]}

    def _stock_transfer_accept(self, ctx, p, ev):
        """stock.transfer_accept {id, comment?, accepted_qty?, shortage_reason?, sign_act?} + версия — ПРИНЯТЬ
        перемещение на складе-получателе (D-29): приход на получатель, статус «Принято». Только онлайн, только склад.

        accepted_qty — сколько фактически пришло (по умолчанию всё отгруженное). Если меньше — недостача оформляется
        актом (D-30): shortage_reason обязательна, подпись приёмщика — sign_act = true и, если так настроено,
        sign_password (D-31, _sign). Недостача списывается из учёта актом (не возвращается ни на один склад). Больше
        отгруженного принять нельзя; если не пришло ничего — stock.transfer_lost (акт об утрате).
        """
        only_keys(p, {"id", "comment", "accepted_qty", "shortage_reason", "sign_act", "sign_password"})
        return self._close_shipment(ctx, p, ev, "stock.transfer_accept", "received")

    def _ensure_in_transit(self, conn, sh: dict) -> None:
        """Перемещение ещё «в пути» (не принято, не возвращено и не утрачено) — иначе отказ transfer_state."""
        lo = self._loss_of(conn, sh["id"])
        status = "lost" if lo is not None else sh["status"]
        if status != "in_transit":
            raise Rejected("transfer_state", f"Перемещение уже в статусе «{stock.TRANSFER_STATUS_LABELS_RU[status]}» — "
                           "принять, вернуть или оформить утрату повторно нельзя.", {"status": status})

    def _stock_transfer_lost(self, ctx, p, ev):
        """stock.transfer_lost {id, reason, sign_act, sign_password?} + версия — оформить УТРАТУ перемещения «в пути»
        актом об утрате (D-31): не пришло ничего.

        Как правильно (решение D-31 по вопросу A-31): утрата — не возврат (товар к отправителю не вернулся) и не
        приёмка (на получатель ничего не пришло). Поэтому — отдельный документ: акт об утрате со своей нумерацией,
        причиной (обязательно: что известно — претензия перевозчику, номер, обстоятельства) и подписью приёмщика
        способом из настройки. Прихода нет; количество, уже списанное у отправителя при отгрузке, окончательно
        выбывает из учёта. Ни остатки, ни резервы не меняются — журнал движений сходится с остатками.
        Нашёлся груз — обычный приход с основанием «найдено по акту об утрате № N» (акт не отменяется).
        Только склад, только онлайн, по версии перемещения (одновременные «Принять» и «Утрата» — сработает одно).
        """
        only_keys(p, {"id", "reason", "sign_act", "sign_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.transfer_lost", ctx.now)
        sh = self._require(ctx.conn, stock_shipments, req_uuid(p, "id"), "Перемещение")
        self._check_version(sh, ev, lambda o: self._shipment_view(ctx.conn, o["id"]))
        reason = req_str(p, "reason", 2000)
        self._ensure_in_transit(ctx.conn, sh)
        it = self._get(ctx.conn, stock_items, sh["item_id"])
        src = self._get(ctx.conn, warehouses, sh["from_warehouse_id"])
        dst = self._get(ctx.conn, warehouses, sh["to_warehouse_id"])
        no = (ctx.conn.execute(sa.select(sa.func.max(stock_shipment_losses.c.act_no))).scalar() or 0) + 1
        signer = ctx.conn.execute(sa.select(users.c.display_name).where(users.c.id == ctx.user_id)).scalar_one()
        ctx.conn.execute(stock_shipment_losses.insert().values(
            shipment_id=sh["id"], act_no=no, qty=sh["qty"], reason=reason, signed_by=ctx.user_id, signer_name=signer,
            signed_at=ctx.now))
        lo = dict(self._loss_of(ctx.conn, sh["id"])._mapping)
        sig = self._sign(ctx, p, "stock_loss_act", sh["id"], _loss_content(sh, lo))
        # Версия перемещения растёт (второй кладовщик с той же версией получит конфликт); статус в БД остаётся
        # in_transit (ограничение v9), действующий — «Утрачено в пути» по акту.
        n = self._update_versioned(ctx, stock_shipments, sh, {"close_reason": reason})
        ctx.log("stock_transfer", sh["id"], auth, sh["version"], n["version"],
                {"transfer_id": sh["id"], "from_warehouse_code": src["code"], "to_warehouse_code": dst["code"],
                 "status": "lost", "item_code": it["code"], "unit": it["unit"], "qty": money.to_json(sh["qty"]),
                 "loss_act_no": no, "reason": reason, "signature_method": sig["method"],
                 "content_sha256": sig["content_sha256"]})
        return {"transfer": self._shipment_view(ctx.conn, sh["id"]), "movements": [], "balances": []}

    def _stock_transfer_cancel(self, ctx, p, ev):
        """stock.transfer_cancel {id, reason} + версия — ВЕРНУТЬ перемещение отправителю, пока оно в пути (D-29): приход
        обратно на склад-отправитель, статус «Возвращено отправителю». Причина обязательна.
        """
        only_keys(p, {"id", "reason"})
        return self._close_shipment(ctx, p, ev, "stock.transfer_cancel", "returned")

    # ------------------------------------------------------------ резервы
    def _need_line_covered(self, conn, need_id: str, line_id: str) -> Decimal:
        """Сколько строки потребности уже покрыто резервами: израсходованное + неизрасходованное у действующих."""
        total = Decimal(0)
        for r in conn.execute(sa.select(stock_reservations).where(stock_reservations.c.need_id == need_id,
                                                                  stock_reservations.c.need_line_id == line_id)):
            total += r.consumed + ((r.qty - r.consumed) if r.status == "active" else 0)
        return total

    def _stock_reserve(self, ctx, p, ev):
        """stock.reserve {id, warehouse_id, item_id, project_id, qty, reason, need_id?, need_line_id?} —
        зарезервировать количество за проектом (A-23).

        Можно только из доступного (остаток − резервы). Если указана строка потребности: она того же проекта,
        сопоставлена с этой позицией, и резерв не больше непокрытого по строке количества. Остаток не меняется —
        растёт резерв.
        """
        only_keys(p, {"id", "warehouse_id", "item_id", "project_id", "qty", "need_id", "need_line_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.reserve", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_reservations, rid)
        w, it = self._wh_item(ctx, p, for_new_stock=True)
        pr = self._project_ref(ctx, req_uuid(p, "project_id"))
        qty = _qty(p)
        reason = req_str(p, "reason", 2000)
        need_id = line_id = None
        if p.get("need_id") is not None or p.get("need_line_id") is not None:
            need = self._require(ctx.conn, procurement_needs, req_uuid(p, "need_id"), "Потребность")
            line_id = req_uuid(p, "need_line_id")
            ln = ctx.conn.execute(sa.select(procurement_need_lines).where(
                procurement_need_lines.c.need_id == need["id"], procurement_need_lines.c.line_id == line_id)).first()
            if ln is None:
                raise Rejected("not_found", "Строка потребности не найдена.", {"field": "need_line_id"})
            if need["project_id"] != pr["id"]:
                raise Rejected("need_mismatch", "Потребность относится к другому проекту.")
            m = self._line_matches(ctx.conn, need["id"]).get(line_id)
            line_item = m["item_id"] if m else ln.item_id
            factor = m["factor"] if m else Decimal(1)
            if line_item is None:
                raise Rejected("need_line_unmatched", "Строка потребности не сопоставлена с номенклатурой — резерв под неё "
                               "невозможен. " + (ln.match_note or ""))
            if line_item != it["id"]:
                raise Rejected("need_mismatch", "Строка потребности относится к другой позиции номенклатуры.")
            # Покрытие считается в единицах позиции: количество строки × зафиксированный коэффициент (A-27).
            open_qty = stock.convert_qty(ln.qty, factor) - self._need_line_covered(ctx.conn, need["id"], line_id)
            if qty > open_qty:
                # Резерв «про запас» сверх потребности — отдельное решение, а не побочный эффект (A-23).
                raise Rejected("exceeds_need", f"По строке потребности осталось покрыть {money.to_json(max(open_qty, Decimal(0)))}, "
                               f"а резервируется {money.to_json(qty)}.",
                               {"open": money.to_json(max(open_qty, Decimal(0))), "requested": money.to_json(qty)})
            need_id = need["id"]
        row, b = self._load_balance(ctx.conn, w["id"], it["id"])
        try:
            nb = stock.reserve(b, qty)
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        ctx.conn.execute(stock_reservations.insert().values(
            id=rid, warehouse_id=w["id"], item_id=it["id"], project_id=pr["id"], need_id=need_id, need_line_id=line_id,
            qty=qty, consumed=Decimal(0), status="active", reason=reason, close_reason=None, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("stock_reservation", rid, auth, None, 1,
                {"warehouse_code": w["code"], "item_code": it["code"], "project_code": pr["code"], "qty": money.to_json(qty),
                 "need_id": need_id, "need_line_id": line_id, "reason": reason, "balance_after": nb.to_json()})
        return {"reservation": self.v_reservation(self._get(ctx.conn, stock_reservations, rid)),
                "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()}}

    def _stock_release(self, ctx, p, ev):
        """stock.release {id, reason} + версия — снять действующий резерв: неизрасходованная часть возвращается в
        доступное, резерв получает статус «Снят» с причиной.
        """
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "stock.release", ctx.now)
        res = self._require(ctx.conn, stock_reservations, req_uuid(p, "id"), "Резерв")
        self._check_version(res, ev, self.v_reservation)
        reason = req_str(p, "reason", 2000)
        if res["status"] != "active":
            raise Rejected("reservation_state", f"Резерв уже {stock.RESERVATION_LABELS_RU[res['status']].lower()}.")
        n, nb = self._release_reservation(ctx, res, reason, auth)
        return {"reservation": self.v_reservation(n),
                "balance": {"warehouse_id": res["warehouse_id"], "item_id": res["item_id"], **nb.to_json()}}

    def _release_reservation(self, ctx, res: dict, reason: str, auth, extra: Optional[dict] = None):
        """Снять действующий резерв (общая часть stock.release и пересопоставления с release_active, D-26).

        Неизрасходованное возвращается в доступное, резерв получает «Снят» с причиной; запись аудита — на сам резерв.
        Вызывающий уже проверил права и состояние «active». Возвращает (новая строка резерва, новый баланс).
        """
        remaining = res["qty"] - res["consumed"]
        row, b = self._load_balance(ctx.conn, res["warehouse_id"], res["item_id"])
        try:
            nb = stock.release(b, remaining)
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, res["warehouse_id"], res["item_id"], row, nb)
        n = self._update_versioned(ctx, stock_reservations, res, {"status": "released", "close_reason": reason,
                                                                 "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("stock_reservation", res["id"], auth, res["version"], n["version"],
                {"status": "released", "released": money.to_json(remaining), "reason": reason, "balance_after": nb.to_json(),
                 **(extra or {})})
        return n, nb

    # ------------------------------------------------------------ потребность
    def _need_generate(self, ctx, p, ev):
        """Потребность из ПОСЛЕДНЕЙ зафиксированной версии расчёта комплекта (снимок строк без цен)."""
        only_keys(p, {"id", "calc_version_id"})
        nid = req_uuid(p, "id")
        cv_id = p.get("calc_version_id")
        if isinstance(cv_id, bool) or not isinstance(cv_id, int):
            raise Rejected("field_type", "Поле «calc_version_id» должно быть целым числом.", {"field": "calc_version_id"})
        cv = self._require(ctx.conn, calc_versions, cv_id, "Версия расчёта")
        auth = self.policy.check(ctx.conn, ctx.user_id, "need.generate", ctx.now, project_id=cv["project_id"])
        self._ensure_new(ctx.conn, procurement_needs, nid)
        dup = self._get(ctx.conn, procurement_needs, cv["id"], "calc_version_id")
        if dup:
            raise Rejected("need_exists", f"Потребность по версии v{cv['version_no']} уже сформирована.", {"need_id": dup["id"]})
        last = self._latest_version(ctx.conn, cv["kit_id"])
        if last["id"] != cv["id"]:
            raise Rejected("calc_version_superseded", f"Есть более новая зафиксированная версия v{last['version_no']} — "
                           "потребность формируется по последней.", {"latest_version_id": last["id"],
                                                                     "latest_version_no": last["version_no"]})
        items = {r.code: dict(r._mapping) for r in ctx.conn.execute(sa.select(stock_items))}
        by_id = {it["id"]: it for it in items.values()}
        # Другие обозначения (D-25): действующие, у действующих позиций. Код позиции важнее обозначения.
        aliases = {r.alias: by_id[r.item_id] for r in ctx.conn.execute(sa.select(item_aliases).where(item_aliases.c.active == sa.true()))
                   if r.item_id in by_id}
        rows, matched = [], 0
        for r in ctx.conn.execute(sa.select(calc_version_lines).where(calc_version_lines.c.version_id == cv["id"])
                                  .order_by(calc_version_lines.c.pos)):
            item_id, note = None, None
            it = items.get(r.code) if r.code else None
            via_alias = it is None and bool(r.code) and r.code in aliases
            if via_alias:
                it = aliases[r.code]
            if not r.code:
                note = "Нет обозначения — сопоставить с номенклатурой автоматически нельзя."
            elif it is None:
                note = f"Нет номенклатуры с кодом «{r.code}»."
            elif not it["active"]:
                note = f"Позиция номенклатуры «{r.code}» отключена."
            elif it["unit"] != r.unit:
                note = f"Единица «{r.unit}» не совпадает с единицей номенклатуры «{it['unit']}» — пересчёт не выполняется."
            elif via_alias:
                # Сопоставлена по другому обозначению: примечание сохраняем — по нему видно, ПОЧЕМУ так (match_source
                # «alias» в v_need). У точных совпадений по коду примечания нет.
                item_id, matched = it["id"], matched + 1
                note = f"По другому обозначению «{r.code}» → позиция «{it['code']}»."
            else:
                item_id, matched = it["id"], matched + 1
            rows.append({"need_id": nid, "pos": r.pos, "line_id": r.line_id, "code": r.code, "name": r.name,
                         "unit": r.unit, "qty": r.qty, "item_id": item_id, "match_note": note})
        ctx.conn.execute(procurement_needs.insert().values(
            id=nid, project_id=cv["project_id"], kit_id=cv["kit_id"], calc_version_id=cv["id"],
            calc_version_no=cv["version_no"], calc_content_hash=cv["content_hash"], line_count=len(rows),
            matched_count=matched, status="open", version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        if rows:
            ctx.conn.execute(procurement_need_lines.insert(), rows)
        kit = self._get(ctx.conn, kits, cv["kit_id"])
        ctx.log("procurement_need", nid, auth, None, 1,
                {"project_id": cv["project_id"], "kit_code": kit["code"], "calc_version_no": cv["version_no"],
                 "calc_content_hash": cv["content_hash"], "line_count": len(rows), "matched_count": matched})
        return {"need": self.v_need(ctx.conn, self._get(ctx.conn, procurement_needs, nid), with_stock=True)}

    def _line_matches(self, conn, need_id: str) -> dict[str, dict]:
        """Действующие ручные сопоставления строк потребности: line_id → последняя запись (наибольший seq)
        с полем count — сколько всего было ручных решений по строке (для истории в интерфейсе).
        """
        m, u = procurement_need_line_matches, procurement_need_line_match_units
        out: dict[str, dict] = {}
        for r in conn.execute(sa.select(m, u.c.factor, u.c.line_unit, u.c.item_unit)
                              .select_from(m.outerjoin(u, u.c.match_id == m.c.id))
                              .where(m.c.need_id == need_id).order_by(m.c.line_id, m.c.seq)):
            row = dict(r._mapping)
            # Нет записи о пересчёте — единицы совпадали, коэффициент 1.
            row["factor"] = row["factor"] if row["factor"] is not None else Decimal(1)
            out[r.line_id] = {**row, "count": r.seq}
        return out

    def _line_items(self, conn, need_id: str) -> dict[str, str]:
        """line_id → позиция номенклатуры по ручному сопоставлению (только для строк, где оно есть).
        Для остальных строк действует автоматическое item_id из снимка — вызывающий берёт его сам.
        """
        return {lid: m["item_id"] for lid, m in self._line_matches(conn, need_id).items()}

    def _need_match_line(self, ctx, p, ev):
        """need.match_line {id, need_id, line_id, item_id, reason, conversion_id?, factor?} + версия потребности —
        сопоставить строку потребности с позицией номенклатуры вручную (A-25, D-24..D-26, A-27).

        Зачем: автомат сопоставляет по точному коду или другому обозначению и только при совпадении единиц (A-22,
        D-25). Остальное решает закупка (D-24) и записывает почему.

        Правила (каждое закреплено тестом):
        * только закупка (право need.match_line в проекте потребности), только при связи;
        * позиция действующая и отличается от той, что действует сейчас;
        * единицы совпадают — пересчёт не нужен (conversion_id/factor передавать нельзя);
        * единицы разные — нужен действующий пересчёт «единица строки → единица позиции» из справочника (A-27).
          Клиент присылает conversion_id и factor, КОТОРЫЙ ПОКАЗАЛ пользователю; если в справочнике уже другой
          коэффициент (его изменили) — отказ conversion_changed: человек подтверждал другое число. Пересчитанное
          количество — точное (≤ 4 знаков), иначе conversion_inexact. Коэффициент фиксируется в решении навсегда;
        * под строку есть резервы — действует настройка procurement.rematch_reserved_line (D-26):
          «forbid» (и если не задана) — отказ need_line_reserved; «release_active» — действующие резервы снимаются
          с причиной из решения, но только если у пользователя ЕСТЬ право stock.release: настройка не расширяет
          права. Израсходованное (выдано со склада) не отменить — при нём отказ всегда;
        * expected_version потребности — два закупщика не перезапишут решения друг друга молча;
        * записи неизменяемы; новая получает следующий seq, прежние остаются историей.
        """
        only_keys(p, {"id", "need_id", "line_id", "item_id", "reason", "conversion_id", "factor"})
        need = self._require(ctx.conn, procurement_needs, req_uuid(p, "need_id"), "Потребность")
        auth = self.policy.check(ctx.conn, ctx.user_id, "need.match_line", ctx.now, project_id=need["project_id"])
        self._check_version(need, ev, lambda o: self.v_need(ctx.conn, o, with_stock=True))
        mid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, procurement_need_line_matches, mid)
        line_id = req_uuid(p, "line_id")
        reason = req_str(p, "reason", 2000)
        ln = ctx.conn.execute(sa.select(procurement_need_lines).where(
            procurement_need_lines.c.need_id == need["id"], procurement_need_lines.c.line_id == line_id)).first()
        if ln is None:
            raise Rejected("not_found", "Строка потребности не найдена.", {"field": "line_id"})
        it = self._require(ctx.conn, stock_items, req_uuid(p, "item_id"), "Позиция номенклатуры")
        if not it["active"]:
            raise Rejected("item_inactive", f"Позиция «{it['code']}» отключена — сопоставлять с ней нельзя.",
                           {"field": "item_id"})
        conv, factor, item_qty = self._match_conversion(ctx.conn, p, ln, it)
        matches = self._line_matches(ctx.conn, need["id"])
        current = matches[line_id]["item_id"] if line_id in matches else ln.item_id
        if current == it["id"]:
            raise Rejected("already_matched", "Строка уже сопоставлена с этой позицией.", {"field": "item_id"})
        released = self._rematch_reserves(ctx, need, ln, matches.get(line_id), reason)
        seq = matches[line_id]["count"] + 1 if line_id in matches else 1
        ctx.conn.execute(procurement_need_line_matches.insert().values(
            id=mid, need_id=need["id"], line_id=line_id, seq=seq, item_id=it["id"], previous_item_id=current,
            reason=reason, matched_by=ctx.user_id, matched_at=ctx.now))
        if conv is not None:
            ctx.conn.execute(procurement_need_line_match_units.insert().values(
                match_id=mid, conversion_id=conv["id"], line_unit=ln.unit, item_unit=it["unit"], factor=factor))
        # Число сопоставленных строк — производное поле потребности; версия растёт, чтобы параллельная правка
        # другой строки той же потребности получила конфликт, а не «старую» картину.
        items_now = {**{r.line_id: r.item_id for r in ctx.conn.execute(
            sa.select(procurement_need_lines.c.line_id, procurement_need_lines.c.item_id)
            .where(procurement_need_lines.c.need_id == need["id"]))}, **self._line_items(ctx.conn, need["id"])}
        matched = sum(1 for v in items_now.values() if v)
        n = self._update_versioned(ctx, procurement_needs, need, {"matched_count": matched,
                                                                  "updated_by": ctx.user_id, "updated_at": ctx.now})
        prev_code = self._get(ctx.conn, stock_items, current)["code"] if current else None
        details = {"line_id": line_id, "line_code": ln.code, "line_name": ln.name, "item_code": it["code"],
                   "previous_item_code": prev_code, "seq": seq, "matched_count": matched, "reason": reason}
        if conv is not None:
            details.update({"conversion_id": conv["id"], "factor": money.to_json(factor), "line_unit": ln.unit,
                            "item_unit": it["unit"], "item_qty": money.to_json(item_qty)})
        if released:
            details["released_reservations"] = released
        ctx.log("procurement_need", need["id"], auth, need["version"], n["version"], details)
        return {"need": self.v_need(ctx.conn, n, with_stock=True)}

    def _match_conversion(self, conn, p: dict, ln, it: dict):
        """Проверка единиц ручного решения. Возвращает (пересчёт | None, коэффициент, количество в единицах позиции)."""
        given = "conversion_id" in p or "factor" in p
        if it["unit"] == ln.unit:
            if given:
                raise Rejected("conversion_not_needed", "Единицы строки и позиции совпадают — пересчёт не нужен.",
                               {"field": "conversion_id"})
            return None, Decimal(1), ln.qty
        conv = self._find_conversion(conn, it, ln.unit)
        if conv is None:
            raise Rejected("unit_mismatch", f"Единица строки «{ln.unit}» не совпадает с единицей позиции «{it['unit']}», "
                           f"а пересчёта «{ln.unit} → {it['unit']}» в справочнике нет. Заведите его (Склад → Справочник "
                           "пересчёта) или выберите позицию с той же единицей.", {"field": "item_id"})
        if not given:
            raise Rejected("conversion_required", f"Нужно подтвердить пересчёт «1 {ln.unit} = "
                           f"{money.to_json(conv['factor'])} {it['unit']}».", {"field": "factor",
                                                                                "conversion": self.v_conversion(conv)})
        factor = _qty(p, "factor")
        if p.get("conversion_id") != conv["id"] or factor != conv["factor"]:
            raise Rejected("conversion_changed", "Пересчёт в справочнике отличается от показанного вам (его изменили). "
                           "Проверьте новый коэффициент и подтвердите заново.", {"field": "factor",
                                                                                "conversion": self.v_conversion(conv)})
        try:
            item_qty = stock.convert_qty(ln.qty, factor)
        except stock.StockError as e:
            raise _stock_rejected(e)
        return conv, factor, item_qty

    def _rematch_reserves(self, ctx, need: dict, ln, match: Optional[dict], reason: str) -> list[dict]:
        """Резервы под строку при пересопоставлении — по настройке procurement.rematch_reserved_line (D-26).

        Возвращает список снятых резервов (для аудита решения); пустой — резервов не было.
        """
        res_rows = [dict(r) for r in ctx.conn.execute(sa.select(stock_reservations).where(
            stock_reservations.c.need_id == need["id"], stock_reservations.c.need_line_id == ln.line_id,
            stock_reservations.c.status != "released")).mappings()]
        if not res_rows:
            return []
        unit = self._get(ctx.conn, stock_items, res_rows[0]["item_id"])["unit"]
        covered = sum((r["consumed"] + (r["qty"] - r["consumed"] if r["status"] == "active" else 0) for r in res_rows),
                      Decimal(0))
        consumed = sum((r["consumed"] for r in res_rows), Decimal(0))
        mode = self.config_value(ctx.conn, "procurement.rematch_reserved_line")
        if consumed > 0:
            raise Rejected("need_line_consumed", f"По строке уже выдано со склада {money.to_json(consumed)} {unit} — "
                           "пересопоставить нельзя: выданное не отменить сменой позиции.",
                           {"consumed": money.to_json(consumed), "reserved": money.to_json(covered)})
        if mode != "release_active":
            raise Rejected("need_line_reserved", f"Под строку уже зарезервировано {money.to_json(covered)} {unit} — "
                           "пересопоставить нельзя (настройка «Пересопоставление при резервах»: запрещено). "
                           "Сначала снимите резервы по этой строке.", {"reserved": money.to_json(covered), "setting": mode})
        try:
            rel_auth = self.policy.check(ctx.conn, ctx.user_id, "stock.release", ctx.now)
        except Denied:
            raise Rejected("need_line_reserved", f"Под строку зарезервировано {money.to_json(covered)} {unit}. Настройка "
                           "разрешает снять резервы при пересопоставлении, но права снимать резервы у вас нет — "
                           "попросите склад снять их.", {"reserved": money.to_json(covered), "setting": mode})
        out = []
        for r in res_rows:
            self._release_reservation(ctx, r, f"Пересопоставление строки потребности: {reason}", rel_auth,
                                      {"by_rematch": True, "need_id": need["id"], "need_line_id": ln.line_id})
            out.append({"id": r["id"], "released": money.to_json(r["qty"] - r["consumed"])})
        return out

    def _in_batches_by_need_line(self, conn) -> dict:
        """«В закупочных пакетах» по строкам потребности: (need_id, line_id) → сумма распределений действующих
        пакетов (Формируется/Сформирован; отменённые и закрытые не занимают покрытие, D-58/D-59)."""
        out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        q = (sa.select(procurement_allocations.c.need_id, procurement_allocations.c.need_line_id,
                       procurement_allocations.c.qty)
             .select_from(procurement_allocations
                          .join(procurement_batch_lines,
                                procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
                          .join(procurement_batches, procurement_batches.c.id == procurement_batch_lines.c.batch_id))
             .where(procurement_batches.c.status.in_(cs.COVERING_BATCH_STATUSES)))
        for r in conn.execute(q):
            out[(r.need_id, r.need_line_id)] += r.qty
        return out

    def v_need(self, conn, o: dict, with_stock: bool, available: Optional[dict] = None) -> dict:
        """Потребность со строками и покрытием. with_stock=False — «доступно» неизвестно (нет права stock.read)."""
        if with_stock and available is None:
            available = self._available_by_item(conn)
        lines = []
        matches = self._line_matches(conn, o["id"])
        # «Заказано, ждём» по строкам — из размещённых заказов поставщикам (purchasing.py, D-32); без цен.
        on_order = self._ordered_open_by_need_line(conn)
        # «В пакетах закупки» по строкам — распределения действующих пакетов (D-59); обеспеченность по
        # происхождению: резерв + в пакетах + заказано (blueprint 52, 2.3).
        in_batches = self._in_batches_by_need_line(conn)
        for r in conn.execute(sa.select(procurement_need_lines).where(procurement_need_lines.c.need_id == o["id"])
                              .order_by(procurement_need_lines.c.pos)):
            # Действующая позиция: ручное решение закупки (A-25) важнее автоматического из снимка.
            m = matches.get(r.line_id)
            item_id = m["item_id"] if m else r.item_id
            # Источник: ручное решение / автомат по коду / автомат по другому обозначению (у него есть примечание).
            source = "manual" if m else (("alias" if r.match_note else "auto") if r.item_id else None)
            # Покрытие — в единицах позиции: при ручном решении с пересчётом количество = qty × зафиксированный
            # коэффициент (A-27). Он был проверен на точность в момент решения, поэтому здесь ошибки быть не может.
            factor = m["factor"] if m else Decimal(1)
            item_qty = stock.convert_qty(r.qty, factor)
            avail = available.get(item_id, Decimal(0)) if (with_stock and item_id) else None
            cov = stock.coverage(item_qty, self._need_line_covered(conn, o["id"], r.line_id), avail)
            lines.append({"pos": r.pos, "line_id": r.line_id, "code": r.code, "name": r.name, "unit": r.unit,
                          "qty": money.to_json(r.qty), "item_id": item_id,
                          "match_note": None if item_id else r.match_note, "match_source": source,
                          "auto_match_note": r.match_note,
                          "match_reason": m["reason"] if m else None, "matched_by": m["matched_by"] if m else None,
                          "matched_at": iso(m["matched_at"]) if m else None, "match_count": m["count"] if m else 0,
                          "factor": money.to_json(factor) if (m and m["line_unit"]) else None,
                          "item_unit": m["item_unit"] if (m and m["line_unit"]) else r.unit,
                          "item_qty": money.to_json(item_qty), "coverage": cov,
                          "on_order": money.to_json(on_order.get((o["id"], r.line_id), Decimal(0))),
                          "in_batches": money.to_json(in_batches.get((o["id"], r.line_id), Decimal(0)))})
        last = self._latest_version(conn, o["kit_id"])
        kit, pr = self._get(conn, kits, o["kit_id"]), self._get(conn, projects, o["project_id"])
        return {"id": o["id"], "project_id": o["project_id"], "project_code": pr["code"], "project_title": pr["title"],
                "kit_id": o["kit_id"], "kit_code": kit["code"], "kit_title": kit["title"],
                "calc_version_id": o["calc_version_id"], "calc_version_no": o["calc_version_no"],
                "latest_calc_version_no": last["version_no"] if last else None,
                "superseded": bool(last and last["id"] != o["calc_version_id"]),
                "line_count": o["line_count"], "matched_count": o["matched_count"], "status": o["status"],
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]), "lines": lines}

    def _available_by_item(self, conn) -> dict:
        """Доступно по каждой позиции суммарно по всем складам — для покрытия строк потребности."""
        out: dict[str, Decimal] = defaultdict(Decimal)
        for r in conn.execute(sa.select(stock_balances)):
            out[r.item_id] += r.on_hand - r.reserved
        return out

    # ------------------------------------------------------------ данные для pull
    def _pull_stock(self, conn, uid: str, now) -> dict:
        """Складская часть pull.

        Склады, номенклатура, остатки, резервы и последние PULL_MOVEMENTS_LIMIT движений — только с правом
        stock.read (иначе пустые списки). Потребности — по need.read в их проекте; без stock.read доступное
        количество в них «неизвестно» (None). Кандидаты для формирования потребности — только тем, кто может
        need.generate.
        """
        out: dict[str, Any] = {"warehouses": [], "stock_items": [], "stock_balances": [], "stock_reservations": [],
                               "stock_movements": [], "stock_movements_truncated": False, "needs": [], "stock_projects": [],
                               "need_candidates": [], "item_aliases": [], "unit_conversions": [],
                               "stock_transfers": []}
        can_stock = self.policy.can(conn, uid, "stock.read", now)
        if can_stock:
            out["warehouses"] = [self.v_warehouse(dict(r._mapping)) for r in conn.execute(sa.select(warehouses).order_by(warehouses.c.code))]
            packs = {r.item_id: r.pack_qty for r in conn.execute(sa.select(stock_item_packs))}
            out["stock_items"] = [self.v_item(dict(r._mapping), packs.get(r.id))
                                  for r in conn.execute(sa.select(stock_items).order_by(stock_items.c.code))]
            # Справочник аналогов и пересчёта единиц (D-25, A-27) — тем же, кто видит номенклатуру.
            out["item_aliases"] = [self.v_alias(dict(r._mapping)) for r in conn.execute(sa.select(item_aliases).order_by(item_aliases.c.alias))]
            out["unit_conversions"] = [self.v_conversion(dict(r._mapping)) for r in conn.execute(
                sa.select(unit_conversions).order_by(unit_conversions.c.from_unit, unit_conversions.c.to_unit))]
            out["stock_balances"] = [self.v_balance(dict(r._mapping)) for r in conn.execute(
                sa.select(stock_balances).order_by(stock_balances.c.warehouse_id, stock_balances.c.item_id))]
            out["stock_reservations"] = [self.v_reservation(dict(r._mapping)) for r in conn.execute(
                sa.select(stock_reservations).order_by(stock_reservations.c.created_at, stock_reservations.c.id))]
            rows = [dict(r._mapping) for r in conn.execute(sa.select(stock_movements).order_by(stock_movements.c.doc_no.desc())
                                                           .limit(PULL_MOVEMENTS_LIMIT + 1))]
            out["stock_movements_truncated"] = len(rows) > PULL_MOVEMENTS_LIMIT
            rows = rows[:PULL_MOVEMENTS_LIMIT]
            rev = {r.reverses_id: r.id for r in conn.execute(sa.select(stock_movements.c.id, stock_movements.c.reverses_id)
                                                             .where(stock_movements.c.reverses_id.isnot(None)))}
            # Перемещения, к которым относятся отданные движения: берём перемещения, отгруженные или закрытые не раньше
            # самого раннего движения выборки (без длинного списка id в запросе — у MS SQL предел ~2100 параметров).
            since = min(m["posted_at"] for m in rows) if rows else None
            legs = self._legs(conn, since) if rows else {}
            out["stock_movements"] = [self.v_movement(m, rev.get(m["id"]), legs.get(m["id"])) for m in rows]
            # Перемещения: ВСЕ «в пути» (по ним нужны действия) и закрытые/одношаговые — в том же окне, что и движения.
            # «В пути» по-настоящему — без акта об утрате (утраченные в БД тоже in_transit, D-31).
            # Псевдоним: сама выборка уже соединена с stock_shipment_losses, без него подзапрос «скоррелируется» целиком.
            la = stock_shipment_losses.alias("la")
            lost = sa.exists().where(la.c.shipment_id == stock_shipments.c.id)
            cond = sa.and_(stock_shipments.c.status == "in_transit", ~lost)
            if since is not None:
                lost_since = sa.exists().where(la.c.shipment_id == stock_shipments.c.id, la.c.signed_at >= since)
                cond = sa.or_(cond, stock_shipments.c.created_at >= since, stock_shipments.c.closed_at >= since,
                              lost_since)
            trs = self._shipment_views(conn, cond)
            if since is not None:
                trs += [self.v_transfer(dict(r._mapping)) for r in conn.execute(
                    sa.select(stock_transfers).where(stock_transfers.c.posted_at >= since))]
            # Сначала «в пути», внутри групп — новые сверху (сортировки устойчивы: сперва по времени, затем по группе).
            trs.sort(key=lambda t: t["created_at"], reverse=True)
            out["stock_transfers"] = sorted(trs, key=lambda t: t["status"] != "in_transit")
            # Склад выдаёт и резервирует «на проект» — ему нужен минимальный перечень проектов (код, название, статус).
            out["stock_projects"] = [{"id": r.id, "code": r.code, "title": r.title, "status": r.status}
                                     for r in conn.execute(sa.select(projects.c.id, projects.c.code, projects.c.title,
                                                                     projects.c.status).order_by(projects.c.code))]
        # Кандидаты для «Сформировать потребность»: последняя зафиксированная версия каждого комплекта, по которой
        # потребности ещё нет. Без цен и сумм — закупке нужен только факт версии (A-22).
        out["need_candidates"] = []
        have = {r.calc_version_id for r in conn.execute(sa.select(procurement_needs.c.calc_version_id))}
        latest: dict[str, dict] = {}
        for v in conn.execute(sa.select(calc_versions).order_by(calc_versions.c.kit_id, calc_versions.c.version_no)):
            latest[v.kit_id] = dict(v._mapping)
        for kit_id, v in sorted(latest.items(), key=lambda kv: kv[1]["frozen_at"]):
            if v["id"] in have or not self.policy.can(conn, uid, "need.generate", now, v["project_id"]):
                continue
            k, pr = self._get(conn, kits, kit_id), self._get(conn, projects, v["project_id"])
            out["need_candidates"].append({"calc_version_id": v["id"], "version_no": v["version_no"], "kit_id": kit_id,
                                           "kit_code": k["code"], "kit_title": k["title"], "project_id": pr["id"],
                                           "project_code": pr["code"], "project_title": pr["title"],
                                           "line_count": v["line_count"], "frozen_at": iso(v["frozen_at"])})
        available = self._available_by_item(conn) if can_stock else None
        for n in conn.execute(sa.select(procurement_needs).order_by(procurement_needs.c.created_at, procurement_needs.c.id)):
            if self.policy.can(conn, uid, "need.read", now, n.project_id):
                out["needs"].append(self.v_need(conn, dict(n._mapping), with_stock=can_stock, available=available))
        return out

    # ------------------------------------------------------------ диагностика
    def ledger_mismatches(self) -> list[dict]:
        """Сверка: остаток = Σ приходов − Σ выдач; резерв = Σ неизрасходованного у действующих резервов.

        Пустой список — всё сходится. Используется тестами и может использоваться проверкой БД (vector-server check).
        """
        with self.engine.connect() as conn:
            want: dict[tuple, list] = defaultdict(lambda: [Decimal(0), Decimal(0)])
            for m in conn.execute(sa.select(stock_movements)):
                want[(m.warehouse_id, m.item_id)][0] += m.qty * stock.MOVEMENT_SIGN[m.kind]
            for r in conn.execute(sa.select(stock_reservations).where(stock_reservations.c.status == "active")):
                want[(r.warehouse_id, r.item_id)][1] += r.qty - r.consumed
            have = {(b.warehouse_id, b.item_id): (b.on_hand, b.reserved) for b in conn.execute(sa.select(stock_balances))}
        bad = []
        for k in sorted(set(want) | set(have)):
            w = tuple(want.get(k, (Decimal(0), Decimal(0))))
            h = have.get(k, (Decimal(0), Decimal(0)))
            if w != h:
                bad.append({"warehouse_id": k[0], "item_id": k[1], "ledger": [money.to_json(x) for x in w],
                            "balance": [money.to_json(x) for x in h]})
        return bad
