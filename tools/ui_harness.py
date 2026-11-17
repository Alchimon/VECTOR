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
    """Временный сервер для стенда интерфейса: SQLite во временном каталоге, быстрый хэш паролей (только тесты), первый
    администратор.
    """
    from vector.server import db, security
    from vector.server.service import Service
    security.configure_hasher_for_tests()
    eng = db.make_engine(f"sqlite:///{tmp / 'server.sqlite'}")
    db.create_schema(eng, str(uuid.uuid4()), datetime.now(timezone.utc))
    svc = Service(eng)
    svc.bootstrap_admin("admin", PWD, "Администратор системы")
    return svc


def seed(svc):
    """Демо-данные интерфейса — общие с тестовым стендом (vector.server.demo)."""
    from vector.server.demo import _seed
    return _seed(svc, "admin", PWD, PWD)


class Harness:
    """Стенд интерфейса: настоящий Backend и QML-окно 1440×900 против in-process сервера с демо-данными через
    LoopbackTransport. Используется тестами tests/ui и для скриншотов справки.
    """
    def __init__(self, theme="light"):
        """Поднять сервер с демо-данными, клиент с хранилищем в памяти и облегчённым scrypt, загрузить QML в заданной
        теме.
        """
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
            """Фабрика SyncClient для Backend: loopback-транспорт вместо HTTPS."""
            return SyncClient(self.transport, url, self.tmp / "profiles", store, "ui-device", kdf={"n": 2 ** 10, "r": 8, "p": 1})
        self.backend = Backend(client_factory=factory, settings=settings, auto_timers=False)
        self.engine = create_engine(self.backend, family)
        self.win = self.engine.rootObjects()[0]
        self.win.setProperty("width", 1440)
        self.win.setProperty("height", 900)

    def pump(self, seconds=0.3):
        """Обрабатывать события Qt seconds секунд (анимации, привязки, сигналы рабочего потока)."""
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait_idle(self, timeout=15):
        """Дождаться, пока backend закончит фоновые задания (не дольше timeout), и дать интерфейсу дорисоваться."""
        end = time.time() + timeout
        self.pump(0.05)
        while self.backend._busy and time.time() < end:
            self.pump(0.05)
        self.pump(0.25)

    def login(self, login="a.smirnova", password=PWD):
        """Войти демо-пользователем (по умолчанию РП a.smirnova) и дождаться загрузки данных."""
        self.backend.login("https://vector.demo.local:8443", login, password, "")
        self.wait_idle()

    def items(self, prop):
        """Все видимые и скрытые QQuickItem, у которых есть свойство prop (для поиска элементов по тексту)."""
        from PySide6.QtQuick import QQuickItem
        # findChildren идёт по дереву QObject и не видит делегаты Repeater (их QObject-родитель — не окно),
        # поэтому дополнительно обходим визуальное дерево (childItems) и объединяем результаты без повторов.
        found, seen = [], set()

        def add(i):
            """Добавить элемент в результат, если у него есть свойство prop и он ещё не добавлен."""
            if id(i) not in seen and i.property(prop) is not None:
                seen.add(id(i))
                found.append(i)
        for i in self.win.findChildren(QQuickItem):
            add(i)
        stack = [self.win.contentItem()]
        while stack:
            it = stack.pop()
            add(it)
            stack.extend(it.childItems())
        return found

    def nav(self):
        """{текст пункта меню: виден ли} — NavItem узнаём по паре свойств text+iconName+active."""
        return {i.property("text"): i.isVisible() for i in self.items("iconName")
                if i.property("active") is not None and i.property("text")}

    def page(self):
        """Текущая страница оболочки (свойство page у Shell)."""
        return self.shell().property("page")

    def click(self, item):
        """Щелчок мышью по центру элемента — как пользователь, через события окна."""
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint())
        self.wait_idle()

    def find(self, name):
        """Объект QML по objectName."""
        from PySide6.QtCore import QObject
        return self.win.findChild(QObject, name)

    def shell(self):
        """Оболочка главного экрана (Shell.qml, objectName «shell»)."""
        from PySide6.QtQuick import QQuickItem
        return self.win.findChild(QQuickItem, "shell")

    def go(self, page, oid=""):
        """Перейти на страницу page (и объект oid) через функцию go() оболочки — как пункт меню."""
        sh = self.shell()
        from PySide6.QtCore import QMetaObject, Q_ARG
        QMetaObject.invokeMethod(sh, "go", Q_ARG("QVariant", page), Q_ARG("QVariant", oid))
        self.wait_idle()

    def kit_work(self, kit_id: str, tab: str = "tasks"):
        """Открыть на странице проекта комплект kit_id и вкладку tasks|documents (как щелчком пользователя)."""
        page = next(i for i in self.items("selectedKitId") if i.isVisible())
        page.setProperty("selectedKitId", kit_id)
        self.wait_idle()
        kw = next(i for i in self.items("kitTasks") if i.isVisible())
        kw.setProperty("tab", tab)
        self.wait_idle()
        return kw

    def scroll_detail_to(self, item):
        """Прокрутить карточку справа так, чтобы item оказался вверху видимой области (для скриншотов)."""
        from PySide6.QtCore import QPointF
        from PySide6.QtQuick import QQuickItem
        p = item
        while p is not None and p.property("contentY") is None:
            p = p.parentItem()
        if p is not None:
            y = item.mapToItem(p.property("contentItem"), QPointF(0, 0)).y()
            p.setProperty("contentY", max(0.0, y - 20))
            self.pump(0.2)

    def shot(self, path: Path):
        """Сохранить снимок окна в PNG (для справки и визуальной проверки)."""
        self.pump(0.4)
        img = self.win.grabWindow()
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path))
        return img

    def close(self):
        """Закрыть окно и движок QML, затем рабочий поток backend (в этом порядке — иначе привязки читают удалённый
        backend).
        """
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


def _plain(v):
    """Свойство QML → обычное значение Python (QJSValue приходит не всегда — зависит от типа свойства)."""
    return v.toVariant() if hasattr(v, "toVariant") else v


def main():
    """Снять скриншоты 01–24 в светлой и тёмной теме в каталог --out (по умолчанию docs/screenshots)."""
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
        kw = h.kit_work(h.ids["kits"]["АТХ"], "tasks")
        h.scroll_detail_to(kw)
        h.shot(out / f"{theme}-09-kit-tasks.png")
        kw.setProperty("tab", "documents")
        h.wait_idle()
        h.scroll_detail_to(kw)
        h.shot(out / f"{theme}-10-kit-documents.png")
        # Расчёт комплекта (С3): сводка, позиции, сравнение с зафиксированной версией; редактор; итог проекта.
        kw.setProperty("tab", "calc")
        h.wait_idle()
        h.scroll_detail_to(kw)
        h.shot(out / f"{theme}-11-kit-calc.png")
        from PySide6.QtCore import QMetaObject, QObject, Q_ARG
        kc = next(i for i in h.items("freezeBlocker") if i.isVisible())
        h.scroll_detail_to(h.find("calcDiffSummary"))
        h.shot(out / f"{theme}-12-kit-calc-diff.png")
        ed = next(o for o in h.win.findChildren(QObject) if o.objectName() == "calcEditor")
        QMetaObject.invokeMethod(ed, "openFor", Q_ARG("QVariant", _plain(kc.property("kit"))),
                                 Q_ARG("QVariant", _plain(kc.property("v"))))
        QMetaObject.invokeMethod(ed, "setCell", Q_ARG("QVariant", 1), Q_ARG("QVariant", "qty"), Q_ARG("QVariant", "1,5"))
        h.wait_idle()
        h.shot(out / f"{theme}-13-calc-editor.png")
        ed.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        h.login("o.nikitina")
        h.go("finance")
        fp = next(i for i in h.items("all") if i.isVisible() and i.property("selectedId") is not None)
        fp.setProperty("selectedId", h.ids["project"])
        h.wait_idle()
        h.shot(out / f"{theme}-14-project-totals.png")
        h.backend.logout()
        h.wait_idle()
        # Склад и закупка (С4): кладовщик — остатки позиции и журнал; закупщик — потребность с покрытием.
        h.login("i.belova")
        sp = next(i for i in h.items("whOptions") if i.isVisible())
        sp.setProperty("selectedId", h.ids["items"]["АРМ"])
        h.wait_idle()
        h.shot(out / f"{theme}-15-stock.png")
        sp.setProperty("tab", "journal")
        h.wait_idle()
        h.shot(out / f"{theme}-16-stock-journal.png")
        # Справочник аналогов и пересчёта единиц (D-25, A-27).
        sp.setProperty("tab", "directory")
        h.wait_idle()
        h.shot(out / f"{theme}-19-stock-directory.png")
        # Перемещение между складами (A-28): форма по позиции НШВИ-1.5 (на двух складах после демо-перемещения).
        sp.setProperty("tab", "balances")
        sp.setProperty("selectedId", h.ids["items"]["НШВИ-1.5"])
        h.wait_idle()
        tf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "transferForm")
        QMetaObject.invokeMethod(tf, "openFor", Q_ARG("QVariant", _plain(sp.property("sel"))))
        h.wait_idle()
        for name, text in (("tQty", "50"), ("tReason", "Бригаде Петрова, монтаж щитов")):
            next(o for o in h.win.findChildren(QObject) if o.objectName() == name).setProperty("text", text)
        h.wait_idle()
        h.shot(out / f"{theme}-21-stock-transfer.png")
        QMetaObject.invokeMethod(tf, "close")
        h.wait_idle()
        # Перемещения «в пути» (D-29): вкладка со списком — демо-отгрузка ХМТ-200 ждёт приёмки, НШВИ уже принято.
        sp.setProperty("tab", "transit")
        h.wait_idle()
        h.shot(out / f"{theme}-22-stock-transit.png")
        # Приёмка с недостачей (D-30): форма по демо-перемещению ХМТ-200 «в пути» — пришло 95 из 100, акт с подписью.
        ov = _plain(sp.property("ov"))
        hmt = next(t for t in ov["transfers"] if t["status"] == "in_transit")
        af = next(o for o in h.win.findChildren(QObject) if o.objectName() == "acceptForm")
        QMetaObject.invokeMethod(af, "openFor", Q_ARG("QVariant", hmt))
        h.wait_idle()
        for name, text in (("acQty", "95"), ("acShortReason", "Пять хомутов с трещинами — не пригодны")):
            next(o for o in h.win.findChildren(QObject) if o.objectName() == name).setProperty("text", text)
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "acSign").setProperty("checked", True)
        # Подпись с вводом пароля (D-31, способ по умолчанию): в поле — точки, не текст.
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "acPwd").setProperty("text", PWD)
        h.wait_idle()
        h.shot(out / f"{theme}-23-transfer-shortage-act.png")
        QMetaObject.invokeMethod(af, "close")
        h.wait_idle()
        # Акт об утрате (D-31): форма «Утрата» по тому же перемещению — не пришло ничего.
        lf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "lostForm")
        QMetaObject.invokeMethod(lf, "openFor", Q_ARG("QVariant", hmt))
        h.wait_idle()
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "lsReason").setProperty(
            "text", "Не прибыло с попутным транспортом; претензия перевозчику № 15")
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "lsSign").setProperty("checked", True)
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "lsPwd").setProperty("text", PWD)
        h.wait_idle()
        h.shot(out / f"{theme}-24-transfer-loss-act.png")
        QMetaObject.invokeMethod(lf, "close")
        h.wait_idle()
        sp.setProperty("tab", "balances")
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        h.login("s.morozov")
        h.go("procurement")
        pp = next(i for i in h.items("nv") if i.isVisible())
        pp.setProperty("selectedId", h.ids["need"])
        h.wait_idle()
        h.shot(out / f"{theme}-17-procurement-need.png")
        # Ручное сопоставление строки без обозначения (кабель) с позицией номенклатуры (A-25).
        nv = _plain(pp.property("nv"))
        need = next(n for n in nv["needs"] if n["id"] == h.ids["need"])
        cable = next(ln for ln in need["lines"] if ln["name"].startswith("Кабель контрольный"))
        lm = next(o for o in h.win.findChildren(QObject) if o.objectName() == "lineMatch")
        QMetaObject.invokeMethod(lm, "openFor", Q_ARG("QVariant", cable))
        h.wait_idle()
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "lmReason").setProperty(
            "text", "Тот же кабель, в расчёте записан без обозначения")
        h.wait_idle()
        h.shot(out / f"{theme}-18-procurement-match.png")
        QMetaObject.invokeMethod(lm, "close")
        h.wait_idle()
        # Заказы поставщикам (D-32, D-33): вкладка «Заказы» — № 2 поставлен частично (НДС раздельно); редактор
        # черновика с ценами без НДС (строки как у № 5); справочник с реквизитами и договорами; форма реквизитов.
        sec = next(i for i in h.items("tabsModel") if i.isVisible())
        sec.setProperty("tab", "orders")
        h.wait_idle()
        op = next(i for i in h.items("pov") if i.isVisible())
        orders = {o["order_no"]: o for o in _plain(op.property("pov"))["orders"]}
        op.setProperty("selectedId", orders[2]["id"])
        h.wait_idle()
        h.shot(out / f"{theme}-25-purchase-orders.png")
        pe = next(o for o in h.win.findChildren(QObject) if o.objectName() == "poEditor")
        # Редактор показываем на новом черновике со строками № 5 (сам № 5 на согласовании и не правится).
        draft = dict(orders[5], id=str(uuid.uuid4()), title="Новый заказ", approval_state=None)
        QMetaObject.invokeMethod(pe, "openFor", Q_ARG("QVariant", draft))
        h.wait_idle()
        h.shot(out / f"{theme}-26-purchase-order-editor.png")
        QMetaObject.invokeMethod(pe, "close")
        h.wait_idle()
        sec.setProperty("tab", "suppliers")
        h.wait_idle()
        spn = next(i for i in h.items("sv") if i.isVisible() and "suppliers" in (_plain(i.property("sv")) or {}))
        sups = _plain(spn.property("sv"))["suppliers"]
        spn.setProperty("selectedId", next(x["id"] for x in sups if x["code"] == "ЭЛЕКТРОСНАБ"))
        h.wait_idle()
        h.shot(out / f"{theme}-27-suppliers.png")
        sf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "supForm")
        QMetaObject.invokeMethod(sf, "openFor", Q_ARG("QVariant", next(x for x in sups if x["code"] == "ЭЛЕКТРОСНАБ")))
        h.wait_idle()
        h.shot(out / f"{theme}-30-supplier-requisites.png")
        QMetaObject.invokeMethod(sf, "close")
        h.wait_idle()
        sec.setProperty("tab", "needs")
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Приём поставки складом по строке заказа № 1 (без цен — их видят закупка и бухгалтерия).
        h.login("i.belova")
        h.go("procurement")
        sec = next(i for i in h.items("tabsModel") if i.isVisible())
        sec.setProperty("tab", "orders")
        h.wait_idle()
        op = next(i for i in h.items("pov") if i.isVisible())
        o1 = next(o for o in _plain(op.property("pov"))["orders"] if o["order_no"] == 1)
        op.setProperty("selectedId", o1["id"])
        h.wait_idle()
        rf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "poReceiveForm")
        QMetaObject.invokeMethod(rf, "openFor", Q_ARG("QVariant", o1["lines"][0]))
        h.wait_idle()
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "prSrc").setProperty("text", "ТН № 230 от ООО «Шкафы-Про»")
        h.wait_idle()
        h.shot(out / f"{theme}-28-purchase-receive.png")
        QMetaObject.invokeMethod(rf, "close")
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Согласование (D-33): v.zaitseva — вкладка «На согласовании», карточка № 5 (цены без НДС, договор П-14).
        h.login("v.zaitseva")
        h.go("procurement")
        h.backend.setOrdersFilter("approval")
        h.wait_idle()
        op = next(i for i in h.items("pov") if i.isVisible())
        o5 = next(o for o in _plain(op.property("pov"))["orders"] if o["order_no"] == 5)
        op.setProperty("selectedId", o5["id"])
        h.wait_idle()
        h.shot(out / f"{theme}-29-purchase-approval.png")
        h.backend.setOrdersFilter("active")
        h.backend.logout()
        h.wait_idle()
        # Настройки компании (D-26, A-26): администратор, вкладка «Настройки компании».
        h.login("admin")
        h.go("admin")
        ap = next(i for i in h.items("sv") if i.isVisible())
        ap.setProperty("tab", "settings")
        h.wait_idle()
        h.shot(out / f"{theme}-20-admin-settings.png")
        h.backend.logout()
        h.wait_idle()
        h.login()
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
