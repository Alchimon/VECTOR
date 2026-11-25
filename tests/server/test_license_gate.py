"""Лицензионные ворота сервера (D-81; формат и правила — vector.core.modules, стратегия — 53-ecosystem.md).

Лицензия — подписанный вендором файл (Ed25519), проверка офлайн; без файла — демо/стенд (всё доступно). Модуль
вне лицензии: изменения отклоняются (module_not_licensed), раздел можно подключить докупив. Подписка истекла:
льготный период всё ещё работает, затем «только чтение» (module_readonly на изменения; чтение и выгрузка —
всегда). Данные клиента не становятся заложниками счёта.
"""
from __future__ import annotations

from datetime import date

import pytest

from conftest import PWD, World, new_id
from vector.core import modules as M
from vector.core.contracts import Contracts
from vector.server.service import Service


def lic(kind="subscription", valid_until="2026-12-31", modules=("pdm",), grace_days=14, support_until="2026-12-31"):
    """Объект лицензии напрямую (разбор файла и подпись покрыты в tests/core/test_modules.py)."""
    return M.License(license_id="L-2026-T1", customer="ООО «Испытатель»", issued_at=date(2026, 1, 1), kind=kind,
                     valid_until=date.fromisoformat(valid_until) if valid_until else None,
                     support_until=date.fromisoformat(support_until) if support_until else None,
                     modules=frozenset(modules), seats=None, grace_days=grace_days, key_id="test")


@pytest.fixture
def lworld(svc, clock, engine, fast_policy):
    """Мир с лицензией «только pdm», подписка до 2026-12-31 (сейчас 2026-11-16 — действует)."""
    svc_lic = Service(engine, contracts=Contracts.load(), clock=clock, sec=fast_policy,
                      vendor_license=lic())
    return World(svc_lic, clock)


def _pull(sess):
    r = sess.query("pull")
    assert r["status"] == "accepted"
    return r["result"]


class TestLicenseGate:
    def test_demo_mode_without_license_allows_everything(self, world):
        """Нет файла лицензии — демо/стенд: все выпущенные модули доступны, pull сообщает режим."""
        r = _pull(world.admin)["license"]
        assert r["mode"] == "demo"
        assert all(m["state"] in ("active", "planned") for m in r["modules"])
        # Команда платного модуля в демо работает (права — отдельный контур, роль rp1).
        world.login("rp1").ok("customer.create", {"id": new_id(), "name": "Демо Заказчик"})

    def test_licensed_module_works_unlicensed_rejected(self, lworld):
        rp = lworld.login("rp1")
        rp.ok("customer.create", {"id": new_id(), "name": "Заказчик"})             # pdm — в лицензии
        # Склад — вне лицензии: отказ по лицензии идёт РАНЬШЕ проверки прав (сначала «модуль не подключён»).
        r = lworld.admin.cmd("warehouse.create", {"id": new_id(), "code": "СК-1", "name": "Основной"})
        assert (r["status"], r["code"]) == ("rejected", "module_not_licensed")
        assert "Склад и закупка" in r["message_ru"] and r["operation_id"]
        # Повтор — тот же отказ (как unknown_command: до транзакции, без записи операции).
        r2 = lworld.admin.cmd("warehouse.create", {"id": new_id(), "code": "СК-2", "name": "Другой"})
        assert r2["code"] == "module_not_licensed"
        # Качество и производство — тоже вне лицензии.
        assert rp.cmd("ecr.submit", {"id": new_id(), "title": "x"})["code"] == "module_not_licensed"
        assert rp.cmd("wo.release", {"id": new_id(), "tp_id": new_id(), "qty": 1})["code"] == "module_not_licensed"

    def test_platform_commands_always_allowed(self, lworld):
        """Вход, учётки, оргструктура, уведомления — платформа: лицензия их не ограничивает никогда."""
        lworld.admin.ok("admin.create_user", {"login": "user01", "display_name": "U1", "initial_password": PWD})
        lworld.admin.ok("org.unit_create", {"id": new_id(), "name": "Отдел", "kind": "department"})
        lworld.clock.advance(seconds=61)
        s = lworld.login("user01")
        assert s.cmd("account.change_password", {"current_password": PWD, "new_password": PWD + "-x"})["status"] == "accepted"

    def test_grace_period_still_works_then_readonly(self, lworld):
        """Подписка истекла 2026-12-31; льготные 14 дней — работает; после — только чтение."""
        rp = lworld.login("rp1")
        rp.ok("customer.create", {"id": new_id(), "name": "До истечения"})
        lworld.clock.advance(days=50)                                  # 2027-01-05: в льготном периоде
        assert lworld.svc.license_status().modules["pdm"].state == "grace"
        rp = lworld.login("rp1")
        rp.ok("customer.create", {"id": new_id(), "name": "В льготный период"})
        lworld.clock.advance(days=20)                                  # 2027-01-25: readonly
        assert lworld.svc.license_status().modules["pdm"].state == "readonly"
        r = lworld.login("rp1").cmd("customer.create", {"id": new_id(), "name": "После истечения"})
        assert (r["status"], r["code"]) == ("rejected", "module_readonly")
        assert "Данные сохраняются" in r["message_ru"]
        # Чтение и выгрузка — всегда: pull работает, данные на месте (сеансы после сдвига часов — новые;
        # читает rp1 — у admin нет бизнес-прав, только администрирование).
        assert _pull(lworld.login("rp1"))["customers"]

    def test_perpetual_never_readonly(self, svc, clock, engine, fast_policy):
        w = World(Service(engine, contracts=Contracts.load(), clock=clock, sec=fast_policy,
                          vendor_license=lic(kind="perpetual", valid_until=None, support_until=None)), clock)
        clock.advance(days=3650)                                       # через 10 лет — работает
        w.login("rp1").ok("customer.create", {"id": new_id(), "name": "Бессрочно"})
        assert "Поддержка не включена" in _pull(w.login("admin"))["license"]["support_label_ru"]

    def test_pull_carries_license_block(self, lworld):
        r = _pull(lworld.admin)["license"]
        assert (r["mode"], r["customer"], r["kind_label_ru"]) == ("licensed", "ООО «Испытатель»", "Подписка")
        assert r["valid_until"] == "2026-12-31" and r["license_id"] == "L-2026-T1"
        by_id = {m["id"]: m for m in r["modules"]}
        assert by_id["pdm"]["state"] == "active"
        assert by_id["supply"]["state"] == "not_licensed"
        assert by_id["schema"]["state"] == "planned"                   # модули экосистемы — «в разработке»
