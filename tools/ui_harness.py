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

PWD = "Демо-пароль-2026"  # noqa: S105 — пароль демо-стенда, задокументирован в test-pc.md


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
    # Стенд интерфейса входит под разными демо-учётками много раз подряд с одного адреса; боевое ограничение
    # (20 попыток входа в минуту с адреса) здесь только мешает снимать экраны — ограничение проверяют серверные тесты.
    svc.rate.per_minute = 1000
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
    """Снять скриншоты 01–52 в светлой и тёмной теме в каталог --out (по умолчанию docs/screenshots)."""
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
        # Предпросмотр импорта из .xlsx (R-31): позиции добавятся только после подтверждения.
        QMetaObject.invokeMethod(ed, "showXlsxPreview", Q_ARG("QVariant", _plain([
            {"code": "КБЛ-01", "name": "Кабель ВВГнг 3×2,5", "unit": "м", "qty": "120.5", "unit_price": "99.99", "note": ""},
            {"code": "АТХ-01", "name": "Шкаф автоматики", "unit": "шт", "qty": "2", "unit_price": "1500000.00", "note": "осн."},
            {"code": "ДТЧ-14", "name": "Датчик температуры", "unit": "шт", "qty": "4", "unit_price": "", "note": "цена неизвестна"},
        ])), Q_ARG("QVariant", _plain([])))
        h.wait_idle()
        h.shot(out / f"{theme}-13b-calc-xlsx-import.png")
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "calcXlsxPreview").setProperty("visible", False)
        h.wait_idle()
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
        # Корректировка остатка (D-77): создана складом, ждёт подписи руководителя отдела — вкладка со списком.
        h.backend.execute("t", "stock.adjust", {"id": str(uuid.uuid4()), "warehouse_id": h.ids["warehouses"]["СК-1"],
                                                "item_id": h.ids["items"]["ХМТ-200"], "new_qty": "50",
                                                "reason": "Акт инвентаризации № 7 от 20.11.2026: найдены 2 шт в зоне приёмки"}, 0)
        h.wait_idle()
        sp.setProperty("tab", "adjustments")
        h.wait_idle()
        h.shot(out / f"{theme}-73-stock-adjustments.png")
        sp.setProperty("tab", "balances")
        h.wait_idle()
        # Предложения перемещения (D-78): закупка предложила хомуты со СК-2 (одно предложение склад уже принял —
        # резерв переброшен, второе ждёт решения) — вкладка «Предложения».
        h.backend.logout()
        h.wait_idle()
        h.login("s.morozov")
        _p1, _p2 = str(uuid.uuid4()), str(uuid.uuid4())
        h.backend.execute("t", "proposal.propose",
                          {"id": _p1, "warehouse_id": h.ids["warehouses"]["СК-2"], "item_id": h.ids["items"]["ХМТ-200"],
                           "to_project_id": h.ids["project"], "qty": "50",
                           "reason": "Для П-2026-014 хомуты нужны до конца недели, свободные лежат на СК-2"}, 0)
        h.backend.execute("t", "proposal.propose",
                          {"id": _p2, "warehouse_id": h.ids["warehouses"]["СК-2"], "item_id": h.ids["items"]["ХМТ-200"],
                           "to_project_id": h.ids["project"], "qty": "20",
                           "reason": "Крепёж для силовых щитов — в ПАЗ цеха № 5 свои уже едут"}, 0)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        h.login("i.belova")
        h.backend.execute("t", "proposal.decide", {"id": _p2, "decision": "accept",
                                                   "note": "Свободных хватило, резерв переброшен"}, 1)
        h.wait_idle()
        h.go("stock")
        sp = next(i for i in h.items("whOptions") if i.isVisible())
        sp.setProperty("tab", "proposals")
        h.wait_idle()
        h.shot(out / f"{theme}-74-stock-proposals.png")
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
        # Контроль расчёта и сверка со счётом (D-34): та же карточка, прокрученная к блоку проверок.
        h.scroll_detail_to(h.find("poInvoiceGrossKv"))
        h.shot(out / f"{theme}-31-purchase-calc-control.png")
        h.backend.setOrdersFilter("active")
        h.backend.logout()
        h.wait_idle()
        # Замещения (D-34): руководитель отдела — своё запланированное замещение и покрытие ролей.
        h.login("e.orlova")
        h.go("delegations")
        h.shot(out / f"{theme}-32-delegations.png")
        h.backend.logout()
        h.wait_idle()
        # Уведомления (D-35, A-40): руководитель отдела — заказ № 5 на согласовании и замещение ГИП.
        h.login("e.orlova")
        h.go("notifications")
        h.shot(out / f"{theme}-33-notifications.png")
        h.backend.logout()
        h.wait_idle()
        # Замещение роли в проекте (D-35): m.lebedev замещает ГИП П-2026-014 — профиль ролей и команда проекта.
        h.login("m.lebedev")
        dp = _plain(h.backend.property("me"))["delegated_project_roles"][0]
        h.go("projects", dp["project_id"])
        h.shot(out / f"{theme}-34-project-deputy.png")
        h.backend.logout()
        h.wait_idle()
        # Производство (D-36): исполнитель — «Мои операции» с таймером операции в работе.
        h.login("k.pavlov")
        h.shot(out / f"{theme}-35-production-my-ops.png")
        h.backend.logout()
        h.wait_idle()
        # Мастер (РП, A-42): наряд № 1 — снимок, операции, материалы и причины, почему закрыть нельзя.
        h.login("a.smirnova")
        h.go("production")
        h.find("productionPage").setProperty("tab", "orders")
        h.wait_idle()
        wp = next(i for i in h.items("all") if i.isVisible())
        wp.setProperty("selectedId", h.ids["work_orders"][0])
        h.wait_idle()
        h.shot(out / f"{theme}-36-work-order.png")
        h.backend.logout()
        h.wait_idle()
        # Технолог: утверждённый ТП и редактор черновика (проверка операций по строкам, норма на изделие).
        h.login("t.fedorova")
        h.find("productionPage").setProperty("tab", "tps")
        h.wait_idle()
        tpp = next(i for i in h.items("all") if i.isVisible())
        tpp.setProperty("selectedId", h.ids["tech_processes"][0])
        h.wait_idle()
        h.shot(out / f"{theme}-37-tech-process.png")
        ed = next(o for o in h.win.findChildren(QObject) if o.objectName() == "tpEditor")
        draft = next(t for t in _plain(tpp.property("all")) if t["id"] == h.ids["tech_processes"][1])
        QMetaObject.invokeMethod(ed, "openFor", Q_ARG("QVariant", draft), Q_ARG("QVariant", False))
        h.wait_idle()
        h.shot(out / f"{theme}-38-tech-process-editor.png")
        ed.setProperty("visible", False)
        h.wait_idle()
        # Подсказки (D-37): наведение мыши на кнопку «Новый ТП» — пояснение и расшифровка «ТП»; затем справочник (F1).
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtTest import QTest
        btn = next(i for i in h.items("tipText") if i.isVisible() and i.property("text") == "Новый ТП")
        QTest.mouseMove(h.win, btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2)).toPoint())
        h.pump(0.9)
        h.shot(out / f"{theme}-39-tooltip.png")
        QTest.mouseMove(h.win, QPoint(700, 880))
        h.pump(0.3)
        h.go("help")
        h.shot(out / f"{theme}-40-glossary.png")
        # Вкладка «Руководство» (issue #2): встроенное руководство пользователя — разделы слева, текст справа.
        h.click(h.find("tabGuide"))
        QTest.mouseMove(h.win, QPoint(700, 500))  # убрать мышь с вкладки, чтобы подсказка не закрывала заголовок
        h.wait_idle()
        h.shot(out / f"{theme}-40b-help-guide.png")
        h.click(h.find("tabGlossary"))
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Изделия и паспорта (D-38): РП — карточка экземпляра «АТХ № 0001 (серия 2026)» (связи, паспорт, история);
        # ведущий инженер — редактор черновика паспорта.
        h.login("a.smirnova")
        h.go("products", h.ids["product_instances"][0])
        h.shot(out / f"{theme}-41-product-instance.png")
        h.backend.logout()
        h.wait_idle()
        h.login("m.lebedev")
        h.go("products", h.ids["product_instances"][0])
        pe = next(o for o in h.win.findChildren(QObject) if o.objectName() == "passportEditor")
        pp = _plain(h.backend.property("lifecycleOverview"))["items"][0]["passport"]
        QMetaObject.invokeMethod(pe, "openFor", Q_ARG("QVariant", pp))
        h.wait_idle()
        h.shot(out / f"{theme}-42-passport-editor.png")
        pe.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Качество (D-39): РП — карточка рекламации № 1 (действия CAPA, история); ГИП — запрос на изменение № 1.
        h.login("a.smirnova")
        h.go("quality", h.ids["claims"][0])
        h.shot(out / f"{theme}-43-quality-claim.png")
        h.backend.logout()
        h.wait_idle()
        h.login("p.volkov")
        h.go("quality", h.ids["change_requests"][0])
        h.shot(out / f"{theme}-44-quality-ecr.png")
        h.backend.logout()
        h.wait_idle()
        # Рекламация на партию без номера изделия, срок ответа «подходит» (D-40): ОТК — карточка рекламации № 2 и форма
        # регистрации (охват, тяжесть, срок ответа заказчику).
        h.login("l.egorova")
        h.go("quality", h.ids["claims"][1])
        h.shot(out / f"{theme}-45-quality-batch-claim.png")
        cf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "claimForm")
        QMetaObject.invokeMethod(cf, "openFor")
        h.wait_idle()
        h.shot(out / f"{theme}-46-quality-claim-form.png")
        cf.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Рабочее время (D-42): руководитель отдела — отчёт сотрудника на проверке; инженер — редактор черновика за
        # сегодня (смена, строки, итоги «не распределено»).
        h.login("e.orlova")
        h.go("worktime", h.ids["daily_reports"][0])
        h.shot(out / f"{theme}-47-worktime-review.png")
        h.backend.logout()
        h.wait_idle()
        h.login("d.kuznetsov")
        h.go("worktime", h.ids["daily_reports"][1])
        wv = h.backend.property("worktimeOverview")
        wv = wv.toVariant() if hasattr(wv, "toVariant") else wv
        rep = next(r for r in wv["mine"] if r["id"] == h.ids["daily_reports"][1])
        ed = next(o for o in h.win.findChildren(QObject) if o.objectName() == "reportEditor")
        QMetaObject.invokeMethod(ed, "openFor", Q_ARG("QVariant", rep), Q_ARG("QVariant", rep["work_date"]))
        h.wait_idle()
        h.shot(out / f"{theme}-48-worktime-editor.png")
        ed.setProperty("visible", False)
        h.wait_idle()
        # D-43: отчёт за позавчера, собранный программой («На самопроверке», источники строк), и форма самопроверки.
        h.go("worktime", h.ids["daily_reports"][4])
        h.shot(out / f"{theme}-49-worktime-assembled.png")
        sf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "reportSubmitForm")
        QMetaObject.invokeMethod(sf, "openFor")
        sf.setProperty("marks", {"shift": True, "lines": True})
        h.wait_idle()
        h.shot(out / f"{theme}-50-worktime-self-check.png")
        sf.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Оргструктура (D-44): инженер — дерево и «кто проверяет мои отчёты»; администратор — форма подразделения.
        h.login("d.kuznetsov")
        h.go("org", h.ids["org_units"][2])
        h.shot(out / f"{theme}-51-org-structure.png")
        h.backend.logout()
        h.wait_idle()
        h.login("admin")
        h.go("org", h.ids["org_units"][1])
        uf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "orgUnitForm")
        ov = h.backend.property("orgOverview")
        ov = ov.toVariant() if hasattr(ov, "toVariant") else ov
        QMetaObject.invokeMethod(uf, "openFor", Q_ARG("QVariant", ov["cards"][h.ids["org_units"][1]]), Q_ARG("QVariant", ""))
        h.wait_idle()
        h.shot(out / f"{theme}-52-org-unit-form.png")
        uf.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Таймер рабочего дня (D-46, A-49 пп. 2–4): инженер — день идёт, перерыв был, запрос на фактическое начало на
        # согласовании; второй инженер — окно «Начните рабочий день» после входа; руководитель — запросы на решение.
        h.login("d.kuznetsov")
        h.go("worktime")
        h.shot(out / f"{theme}-53-workday-timer.png")
        h.backend.logout()
        h.wait_idle()
        h.backend.setWorkdayPromptAuto(True)                # на стенде интерфейса окно само не открывается
        h.login("m.lebedev")
        h.shot(out / f"{theme}-54-workday-prompt.png")
        # Окно «настаивает» (D-47): закрытое открывается снова, поэтому сначала выключаем автопоказ и выходим.
        h.backend.setWorkdayPromptAuto(False)
        h.backend.logout()
        h.wait_idle()
        h.login("e.orlova")
        h.go("worktime")
        h.wait_idle()
        # Окно запросов — у карточки таймера на странице (у карточки в боковой панели — своё, «…Mini»).
        rp = next((o for o in h.win.findChildren(QObject) if o.objectName() == "workdayRequestsPopup"), None)
        if rp is None:
            print("workdayRequestsPopup не найден; страница:", h.shell().property("page"),
                  sorted({o.objectName() for o in h.win.findChildren(QObject) if o.objectName().startswith("workday")}))
        if rp is not None:
            rp.setProperty("visible", True)
            h.wait_idle()
            h.shot(out / f"{theme}-55-workday-requests.png")
            rp.setProperty("visible", False)
            h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        # Графики работы и забытое «Завершить» (D-47, A-51): i.belova — день завершён автоматически, запрос «я работал
        # дольше» на согласовании; d.kuznetsov — напоминание «Рабочий день по графику закончился» (часы клиента
        # подставлены на конец дня по графику); администратор — справочник графиков и форма графика.
        h.login("i.belova")
        h.go("worktime")
        h.shot(out / f"{theme}-56-workday-auto-finished.png")
        h.backend.logout()
        h.wait_idle()
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        class _Clock:
            """Подставные часы клиента (SyncClient.clock) для снимка напоминания."""
            def __init__(self, t):
                self.t = t

            def now(self):
                return self.t
        h.backend.setWorkdayPromptAuto(True)
        h.login("d.kuznetsov")
        c = h.backend.client
        c.clock = _Clock(_dt.now(_tz.utc) + _td(hours=20))
        due = _dt.fromisoformat(c.workday_reminder()["due"])
        c.clock = _Clock(due + _td(minutes=3))
        h.backend.workdayCheckReminder()
        h.wait_idle()
        h.shot(out / f"{theme}-57-workday-end-prompt.png")
        # A-54: никакой активности клавиатуры/мыши не собираем; без ответа день завершается по времени напоминания.
        c.clock = _Clock(due + _td(minutes=31))
        h.backend.workdayCheckReminder()
        h.wait_idle()
        h.shot(out / f"{theme}-62-workday-auto-finish.png")
        h.backend.setWorkdayPromptAuto(False)
        c.clock = None
        h.backend.logout()
        h.wait_idle()
        # D-49 (A-53 п. 3): руководитель коммерческой службы — отчёт сотрудника со сверхурочными.
        h.login("v.zaitseva")
        h.go("worktime", h.ids["day_mark_reports"][0])
        h.wait_idle()
        h.shot(out / f"{theme}-63-worktime-day-mark.png")
        h.backend.logout()
        h.wait_idle()
        # ---------- Этапы 3–7 blueprint 52: РКД, потоки, ПНР/FAT, аналитика, нормы (D-64…D-73) ----------
        # Данные для снимков: шаблон FAT (начальник сервисной службы — e.orlova, A-58), предварительное
        # испытание и запрос ПНР по демо-проекту, предложение нормы от ведущего инженера (выборка — завершённые
        # операции «Сборка шкафа управления» нарядов № 1 и № 2).

        def _svc(login, command, payload, ev=None):
            """Команда на сервере стенда от имени демо-пользователя (подготовка данных для снимков)."""
            tok = h.svc.handle({"type": "login", "login": login, "password": PWD, "device_id": "shots"}, "x")["token"]
            if command == "__pull__":
                return h.svc.handle({"type": "query", "token": tok, "query": "pull"}, "x")["result"]
            msg = {"type": "command", "token": tok, "operation_id": str(uuid.uuid4()), "command": command,
                   "payload": payload}
            if ev is not None:
                msg["expected_version"] = ev
            r = h.svc.handle(msg, "x")
            if r["status"] != "accepted":
                raise RuntimeError(f"демо-команда {command} не принята: {r}")
            return r["result"]

        pid = h.ids["project"]
        tpl = _svc("e.orlova", "checklist.create",
                   {"id": str(uuid.uuid4()), "kind": "fat", "name": "FAT: шкафы управления",
                    "sections": [{"code": "gen", "title": "Общие проверки"}, {"code": "el", "title": "Электрика"}]})
        _svc("p.volkov", "fat.create", {"id": str(uuid.uuid4()), "project_id": pid, "kind": "preliminary",
                                        "template_id": tpl["checklist"]["id"],
                                        "note": "Предварительное испытание на стенде"})
        _svc("p.volkov", "pnr.create", {"id": str(uuid.uuid4()), "project_id": pid, "kind": "site",
                                        "title": "ПНР на площадке котельной № 3", "need_power": True,
                                        "planned_on": "2026-12-20"})
        _svc("m.lebedev", "norm.propose", {"id": str(uuid.uuid4()), "op_name": "Сборка шкафа управления",
                                           "work_center": "Участок сборки", "proposed_min": "210",
                                           "comment": "По факту нарядов № 1 и № 2"})
        h.login("a.smirnova")
        h.go("projects", pid)
        h.wait_idle()
        # Потоки и риск-разрешения проекта (D-65).
        t = next(i for i in h.items("text") if i.isVisible() and str(i.property("text") or "").startswith("Потоки и риски"))
        h.scroll_detail_to(t)
        h.shot(out / f"{theme}-64-project-flow.png")
        # ПНР, испытания (FAT) и документы доставки (D-70).
        h.backend.syncNow()
        h.wait_idle()
        t = next(i for i in h.items("text") if i.isVisible() and str(i.property("text") or "").startswith("ПНР, испытания и доставка"))
        h.scroll_detail_to(t)
        h.shot(out / f"{theme}-65-project-pnr-fat.png")
        # Аналитика проекта: план/факт потоков, сводка испытаний (D-71).
        t = next(i for i in h.items("text") if i.isVisible() and str(i.property("text") or "").startswith("Аналитика проекта"))
        h.scroll_detail_to(t)
        h.shot(out / f"{theme}-66-project-analytics.png")
        # РКД комплекта (D-64): бизнес-ревизии, замечания, трансмитталы.
        kw = h.kit_work(h.ids["kits"]["АТХ"], "tasks")
        kw.setProperty("tab", "rkd")
        h.wait_idle()
        h.scroll_detail_to(kw)
        h.shot(out / f"{theme}-67-kit-rkd.png")
        h.backend.logout()
        h.wait_idle()
        # Нормы трудоёмкости (D-72): предложение в карточке наряда № 1 и статистика операций (агрегат).
        h.login("e.orlova")
        h.go("production")
        h.find("productionPage").setProperty("tab", "orders")
        h.wait_idle()
        wp = next(i for i in h.items("all") if i.isVisible())
        wp.setProperty("selectedId", h.ids["work_orders"][0])
        h.wait_idle()
        t = next(i for i in h.items("text") if i.isVisible() and str(i.property("text") or "") == "Нормы трудоёмкости")
        h.scroll_detail_to(t)
        h.shot(out / f"{theme}-68-work-order-norms.png")
        sd = next(o for o in h.win.findChildren(QObject) if o.objectName() == "normStatsForm")
        QMetaObject.invokeMethod(sd, "openFor")
        h.wait_idle()
        h.shot(out / f"{theme}-69-norm-stats.png")
        sd.setProperty("visible", False)
        h.wait_idle()
        # Сводная аналитика портфеля (D-73): раздел «Аналитика» — сроки потоков, lead-time закупок, замены, рекламации.
        h.go("analytics")
        h.wait_idle()
        h.shot(out / f"{theme}-70-portfolio-analytics.png")
        h.backend.logout()
        h.wait_idle()
        # ---------- D-74: ТК-контур сверхурочных — заявка с основанием и кадровые категории ----------
        # Администратор настраивает учётный период и назначает d.kuznetsov категорию «инвалид» (с основанием);
        # d.kuznetsov открывает форму заявки: основание из справочника ст. 99/113 ТК РФ со статьёй, отметки
        # «согласие получено» / «право отказа разъяснено»; в «Администрировании» — секция «Кадровые категории».
        _svc("admin", "overtime.period_set", {"mode": "month"})
        _svc("admin", "hr.category_set", {"user_id": h.ids["people"]["d.kuznetsov"], "categories": ["disabled"],
                                          "reason": "Справка МСЭ № 42 от 10.11.2026"})
        h.login("d.kuznetsov")
        h.go("worktime")
        h.wait_idle()
        of = next(o for o in h.win.findChildren(QObject) if o.objectName() == "overtimeRequestForm")
        QMetaObject.invokeMethod(of, "openFor")
        h.wait_idle()
        h.find("overtimeWorkDate").setProperty("text", "2026-11-25")
        h.find("overtimeStartAt").setProperty("text", "2026-11-25T18:00:00+03:00")
        h.find("overtimeEndAt").setProperty("text", "2026-11-25T20:00:00+03:00")
        h.find("overtimeMinutes").setProperty("text", "120")
        h.find("overtimeReason").setProperty("text", "Неотложная подготовка испытаний шкафа управления")
        h.find("overtimeGround").setProperty("currentIndex", 0)
        h.find("overtimeConsent").setProperty("checked", True)
        h.find("overtimeNotice").setProperty("checked", True)
        h.wait_idle()
        h.shot(out / f"{theme}-71-overtime-request-form.png")
        of.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        h.login("admin")
        h.go("admin")
        h.wait_idle()
        apg = next(i for i in h.items("users") if i.isVisible() and i.property("selectedId") is not None)
        apg.setProperty("selectedId", h.ids["people"]["d.kuznetsov"])
        h.wait_idle()
        t = next(i for i in h.items("text") if i.isVisible() and str(i.property("text") or "") == "Кадровые категории")
        h.scroll_detail_to(t)
        h.shot(out / f"{theme}-72-admin-hr-categories.png")
        h.backend.logout()
        h.wait_idle()
        h.login("admin")
        h.go("org", h.ids["org_units"][2])
        sp = next(o for o in h.win.findChildren(QObject) if o.objectName() == "schedulesPopup")
        sp.setProperty("visible", True)
        h.wait_idle()
        h.shot(out / f"{theme}-58-work-schedules.png")
        sp.setProperty("visible", False)
        h.wait_idle()
        ov = h.backend.property("orgOverview")
        ov = ov.toVariant() if hasattr(ov, "toVariant") else ov
        sf = next(o for o in h.win.findChildren(QObject) if o.objectName() == "scheduleForm")
        QMetaObject.invokeMethod(sf, "openFor", Q_ARG("QVariant", next(x for x in ov["schedules"] if x["name"] == "Смена наладчиков")))
        h.wait_idle()
        h.shot(out / f"{theme}-59-work-schedule-form.png")
        sf.setProperty("visible", False)
        h.wait_idle()
        # Производственный календарь (D-48, A-52 п. 3): годы, откуда календарь, особые дни; форма правки дня.
        cp = next(o for o in h.win.findChildren(QObject) if o.objectName() == "calendarPopup")
        cp.setProperty("visible", True)
        h.wait_idle()
        h.shot(out / f"{theme}-60-production-calendar.png")
        df = next(o for o in h.win.findChildren(QObject) if o.objectName() == "calendarDayForm")
        # Пустая форма (как «Изменить день»): openFor(null) из Python не вызвать — поля заполняются напрямую.
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "calDayDate").setProperty("text", "30.12.2026")
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "calDayKind").setProperty("currentIndex", 1)
        next(o for o in h.win.findChildren(QObject) if o.objectName() == "calDayTitle").setProperty("text", "Решение директора")
        df.setProperty("visible", True)
        h.wait_idle()
        h.shot(out / f"{theme}-61-production-calendar-day.png")
        df.setProperty("visible", False)       # форма — внутри окна календаря: сначала она, затем само окно
        cp.setProperty("visible", False)
        h.wait_idle()
        h.backend.logout()
        h.wait_idle()
        h.login("admin")
        # Настройки компании (D-26, A-26): администратор, вкладка «Настройки компании».
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
