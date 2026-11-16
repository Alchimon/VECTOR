"""Стенд интерфейса: настоящий клиент (Backend + QML) против in-process сервера на временной SQLite с демо-данными.

Используется тестами tests/ui (маркер ui) и для скриншотов справочного центра:
  LD_LIBRARY_PATH=.venv/stubgl QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
      python tools/ui_harness.py --out docs/screenshots

Демо-данные вымышлены и создаются через обычные команды сервиса (права, аудит, версии — настоящие).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

PWD = "Демо-пароль-2026"


def build_server(tmp: Path):
    from vector.server import db, security
    from vector.server.service import Service
    security.configure_hasher_for_tests()
    eng = db.make_engine(f"sqlite:///{tmp / 'server.sqlite'}")
    db.create_schema(eng, str(uuid.uuid4()), datetime.now(timezone.utc))
    svc = Service(eng)
    svc.bootstrap_admin("admin", PWD, "Администратор системы")
    return svc


def seed(svc):
    def login(lg):
        r = svc.handle({"type": "login", "login": lg, "password": PWD, "device_id": "seed"}, "seed")
        assert r["status"] == "accepted", r
        return r["token"]

    def cmd(tok, c, p, ev=None):
        r = svc.handle({"type": "command", "token": tok, "operation_id": str(uuid.uuid4()), "command": c, "payload": p, "expected_version": ev}, "seed")
        assert r["status"] == "accepted", (c, r)
        return r["result"]

    adm = login("admin")
    people = {}
    for lg, name, role in [("a.smirnova", "Смирнова Анна", "rp"), ("p.volkov", "Волков Павел", "gip"), ("e.orlova", "Орлова Елена", "dept_head"),
                           ("d.kuznetsov", "Кузнецов Дмитрий", "engineer"), ("m.lebedev", "Лебедев Максим", "lead_engineer"),
                           ("o.nikitina", "Никитина Ольга", "accountant")]:
        uid = cmd(adm, "admin.create_user", {"login": lg, "display_name": name, "initial_password": PWD})["user_id"]
        cmd(adm, "admin.grant_role", {"user_id": uid, "role": role, "reason": "демо"})
        t = login(lg)
        cmd(t, "account.change_password", {"current_password": PWD, "new_password": PWD + "!"})
        cmd(t, "account.change_password", {"current_password": PWD + "!", "new_password": PWD})
        people[lg] = uid
    cmd(adm, "admin.create_user", {"login": "n.sokolov", "display_name": "Соколов Никита", "initial_password": PWD})
    rp, head = login("a.smirnova"), login("e.orlova")

    custs = {}
    for name, inn, note in [("АО «Энергомаш»", "7701234567", "Ключевой заказчик, энергетика"), ("ООО «ТеплоСеть-Юг»", "3443123456", ""),
                            ("ПАО «Волгохимпром»", "3444987654", "Тендеры через ЭТП"), ("МУП «Водоканал»", None, "ИНН уточняется")]:
        cid = str(uuid.uuid4())
        cmd(rp, "customer.create", {"id": cid, "name": name, **({"inn": inn} if inn else {}), **({"note": note} if note else {})})
        custs[name] = cid

    def tender(cust, title, deadline, path):
        tid = str(uuid.uuid4())
        cmd(rp, "tender.create", {"id": tid, "customer_id": custs[cust], "title": title, "submission_deadline": deadline})
        v = 1
        for st in path:
            cmd(rp, "tender.transition", {"id": tid, "to": st, **({"reason": "Заказчик перенёс закупку"} if st in ("cancelled", "lost") else {})}, v)
            v += 1
        return tid

    t_won = tender("АО «Энергомаш»", "АСУ ТП котельной № 3: проектирование и поставка", "2026-10-20", ["in_work", "bid_submitted", "won"])
    t_won2 = tender("ПАО «Волгохимпром»", "Модернизация системы ПАЗ цеха № 5", "2026-09-30", ["in_work", "bid_submitted", "won"])
    t_sub = tender("ООО «ТеплоСеть-Юг»", "Диспетчеризация ЦТП (12 объектов)", "2026-12-05", ["in_work", "bid_submitted"])
    tender("ООО «ТеплоСеть-Юг»", "Узлы учёта тепловой энергии", "2026-12-18", ["in_work"])
    tender("МУП «Водоканал»", "КИПиА насосной станции 2-го подъёма", None, [])
    tender("ПАО «Волгохимпром»", "Пусконаладка САУ компрессорной", "2026-08-01", ["cancelled"])

    k1 = str(uuid.uuid4())
    cmd(rp, "tkp.create", {"id": k1, "tender_id": t_won, "label": "ТКП-27/2026", "amount": "18450000.00", "currency": "RUB", "note": "Вариант с резервированием контроллеров"})
    cmd(head, "tkp.approve", {"id": k1, "comment": "Согласовано"}, 1)
    cmd(rp, "tkp.record_sent", {"id": k1, "sent_on": "2026-10-18", "channel": "ЭТП"}, 2)
    k2 = str(uuid.uuid4())
    cmd(rp, "tkp.create", {"id": k2, "tender_id": t_sub, "label": "ТКП-31/2026", "amount": "6230500.50", "currency": "RUB"})
    cmd(head, "tkp.approve", {"id": k2}, 1)
    cmd(rp, "tkp.create", {"id": str(uuid.uuid4()), "tender_id": t_sub, "label": "ТКП-31/2026 (вариант Б)"})

    pid = str(uuid.uuid4())
    cmd(rp, "project.start_rkd", {"id": pid, "tender_id": t_won, "code": "П-2026-014", "title": "АСУ ТП котельной № 3"})
    cmd(adm, "project.assign", {"project_id": pid, "user_id": people["p.volkov"], "project_role": "gip", "reason": "Приказ № 112"})
    gip = login("p.volkov")
    cmd(gip, "project.assign", {"project_id": pid, "user_id": people["a.smirnova"], "project_role": "rp", "reason": "Приказ № 112"})
    cmd(rp, "project.assign", {"project_id": pid, "user_id": people["d.kuznetsov"], "project_role": "participant", "reason": "Распределение работ"})
    cmd(rp, "project.assign", {"project_id": pid, "user_id": people["m.lebedev"], "project_role": "participant", "reason": "Распределение работ"})
    for code, title in [("АТХ", "Автоматизация технологических процессов"), ("ЭМ", "Силовое электрооборудование"),
                        ("КИП", "Контрольно-измерительные приборы"), ("СС", "Сети связи и диспетчеризация")]:
        cmd(rp, "kit.create", {"id": str(uuid.uuid4()), "project_id": pid, "code": code, "title": title})
    pid2 = str(uuid.uuid4())
    cmd(rp, "project.start_rkd", {"id": pid2, "tender_id": t_won2, "code": "П-2026-011", "title": "ПАЗ цеха № 5"})
    cmd(adm, "project.assign", {"project_id": pid2, "user_id": people["a.smirnova"], "project_role": "rp", "reason": "Приказ № 98"})
    cmd(rp, "kit.create", {"id": str(uuid.uuid4()), "project_id": pid2, "code": "ПАЗ", "title": "Противоаварийная защита"})
    return {"customers": custs, "project": pid, "tender_won": t_won, "tender_sub": t_sub}


class Harness:
    def __init__(self, theme="light"):
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QGuiApplication

        from vector.client.app import create_engine, load_fonts
        from vector.client.backend import Backend
        from vector.client.sync import SyncClient
        from vector.client.transport import LoopbackTransport
        from vector.client.vault import MemoryStore

        self.tmp = Path(tempfile.mkdtemp(prefix="vector-ui-"))
        self.app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        family = load_fonts()
        self.svc = build_server(self.tmp)
        self.ids = seed(self.svc)
        self.transport = LoopbackTransport(self.svc, "10.0.0.20")
        store = MemoryStore()
        settings = QSettings(str(self.tmp / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("ui/theme", theme)

        def factory(url, ca):
            return SyncClient(self.transport, url, self.tmp / "profiles", store, "ui-device", kdf={"n": 2 ** 10, "r": 8, "p": 1})
        self.backend = Backend(client_factory=factory, settings=settings, auto_timers=False)
        self.engine = create_engine(self.backend, family)
        self.win = self.engine.rootObjects()[0]
        self.win.setProperty("width", 1440)
        self.win.setProperty("height", 900)

    def pump(self, seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait_idle(self, timeout=15):
        end = time.time() + timeout
        self.pump(0.05)
        while self.backend._busy and time.time() < end:
            self.pump(0.05)
        self.pump(0.25)

    def login(self, login="a.smirnova", password=PWD):
        self.backend.login("https://vector.demo.local:8443", login, password, "")
        self.wait_idle()

    def items(self, prop):
        """Все видимые и скрытые QQuickItem, у которых есть свойство prop (для поиска элементов по тексту)."""
        from PySide6.QtQuick import QQuickItem
        return [i for i in self.win.findChildren(QQuickItem) if i.property(prop) is not None]

    def nav(self):
        """{текст пункта меню: виден ли} — NavItem узнаём по паре свойств text+iconName+active."""
        return {i.property("text"): i.isVisible() for i in self.items("iconName")
                if i.property("active") is not None and i.property("text")}

    def page(self):
        return self.shell().property("page")

    def click(self, item):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint())
        self.wait_idle()

    def find(self, name):
        from PySide6.QtCore import QObject
        return self.win.findChild(QObject, name)

    def shell(self):
        from PySide6.QtQuick import QQuickItem
        return self.win.findChild(QQuickItem, "shell")

    def go(self, page, oid=""):
        sh = self.shell()
        from PySide6.QtCore import QMetaObject, Q_ARG
        QMetaObject.invokeMethod(sh, "go", Q_ARG("QVariant", page), Q_ARG("QVariant", oid))
        self.wait_idle()

    def shot(self, path: Path):
        self.pump(0.4)
        img = self.win.grabWindow()
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path))
        return img

    def close(self):
        # Сначала окно и движок QML (иначе привязки читают уже удалённый backend), затем рабочий поток backend.
        from PySide6.QtCore import QCoreApplication, QEvent
        self.win.hide()
        self.win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.engine.clearComponentCache()
        del self.win
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.pump(0.05)
        self.backend.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    args = ap.parse_args()
    out = Path(args.out)
    for theme in ("light", "dark"):
        h = Harness(theme)
        h.shot(out / f"{theme}-01-login.png")
        h.login()
        h.go("tenders", h.ids["tender_won"])
        h.shot(out / f"{theme}-02-tender-won.png")
        h.go("tenders", h.ids["tender_sub"])
        h.shot(out / f"{theme}-03-tender-tkp.png")
        h.go("projects", h.ids["project"])
        h.shot(out / f"{theme}-04-project.png")
        h.go("customers", h.ids["customers"]["АО «Энергомаш»"])
        h.shot(out / f"{theme}-05-customer.png")
        # Офлайн: правка без связи + конфликт с коллегой
        h.transport.online = False
        cid = h.ids["customers"]["ООО «ТеплоСеть-Юг»"]
        c = h.backend.client
        ver = c.get_local("customer", cid)["version"]
        h.backend.execute("t", "customer.update", {"id": cid, "note": "Контакт: главный инженер, доб. 214"}, ver)
        h.backend.execute("t", "customer.create", {"id": str(uuid.uuid4()), "name": "ООО «Новый объект» (офлайн)"}, 0)
        h.wait_idle()
        tok = h.svc.handle({"type": "login", "login": "e.orlova", "password": PWD, "device_id": "x"}, "x")["token"]
        h.svc.handle({"type": "command", "token": tok, "operation_id": str(uuid.uuid4()), "command": "customer.update",
                      "payload": {"id": cid, "note": "Договор через холдинг"}, "expected_version": ver}, "x")
        h.go("customers", cid)
        h.shot(out / f"{theme}-06-offline-pending.png")
        h.transport.online = True
        h.backend.syncNow()
        h.wait_idle()
        h.go("sync")
        h.shot(out / f"{theme}-07-sync-conflict.png")
        h.backend.logout()
        h.wait_idle()
        h.login("admin")
        h.go("admin")
        h.shot(out / f"{theme}-08-admin.png")
        h.close()
    print("Скриншоты:", out)


if __name__ == "__main__":
    main()
