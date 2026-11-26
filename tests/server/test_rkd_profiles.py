"""Профили шифров ревизий и статусы выдачи (D-62, этап 3б blueprint 52).

Профиль схемы шифров задаёт заказчик/проект: шаблон шифра (регулярное выражение), пример и словарь статусов
выдачи (IFC/CEF и т.п.). Комплектный профиль переопределяет проектный; одновременно активен один профиль на
область. Шаблон проверяется при создании ревизии; статус выдачи — одна действующая запись на ревизию со
снимком подписи из словаря. Команда docprofile.preview ничего не записывает — предпросмотр перед созданием.
"""
from __future__ import annotations


from tests.conftest import new_id
from tests.server.test_rkd import add_version, make_rev, pull, ready_rev, rev_of, trans, upload  # noqa: F401
from tests.server.test_rkd import pd as pd  # noqa: F401  # фикстура: проект с РП/ГИП/инженером, комплект, документ


def make_profile(s, pid, kit=None, pattern=r"[A-Z]{2}\d{2}", example="AB01",
                 statuses=None, name="ГОСТ 21.1101"):
    """РП создаёт активный профиль шифров; возвращает представление профиля."""
    p = {"id": new_id(), "project_id": pid, "name": name}
    if kit:
        p["kit_id"] = kit
    if pattern is not None:
        p["label_pattern"] = pattern
    if example is not None:
        p["label_example"] = example
    if statuses is not None:
        p["issue_statuses"] = statuses
    r = s.ok("docprofile.create", p)
    return r["profile"]


IFC_CEF = [{"code": "IFC", "label_ru": "Для строительства"}, {"code": "CEF", "label_ru": "Для сведения"}]


def reject(s, command, payload, code, expected_version=None):
    """Команда обязана быть отклонена именно с этим кодом; возвращает конверт ответа."""
    r = s.cmd(command, payload, expected_version=expected_version)
    assert r["status"] in ("rejected", "denied", "conflict"), (command, r)
    assert r["code"] == code, (command, r)
    return r


# ====================================================================== профили: создание и правила
def test_profile_create_rules(pd):
    """Создание: РП/ГИП могут, участник — нет; один активный на область; шаблон обязан компилироваться,
    пример — соответствовать шаблону; коды статусов непусты и уникальны."""
    pd_ = pd
    rp, eng, pid, kit = pd_["rp"], pd_["eng"], pd_["pid"], pd_["kit"]
    assert reject(eng, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Х"},
                  "no_authority")["status"] == "denied"
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Х",
                                     "label_pattern": "["}, "bad_label_pattern")
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Х",
                                     "label_pattern": r"\d{2}", "label_example": "AB"}, "example_mismatch")
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Х",
                                     "issue_statuses": [{"code": "IFC", "label_ru": "А"},
                                                        {"code": "ifc", "label_ru": "Б"}]}, "bad_issue_statuses")
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Х",
                                     "issue_statuses": [{"code": "IFC", "label_ru": ""}]}, "bad_issue_statuses")
    prof = make_profile(rp, pid, statuses=IFC_CEF)
    assert prof["active"] is True and prof["label_pattern"] == r"[A-Z]{2}\d{2}"
    assert prof["issue_statuses"] == IFC_CEF and prof["revisions"] == 0
    # Второй активный профиль на проект целиком — отказ; на КОНКРЕТНЫЙ комплект — можно (переопределяет).
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "name": "Другой"},
           "profile_active_exists")
    kit_prof = make_profile(rp, pid, kit=kit, pattern=r"\d{2}[A-Z]", example="01A", name="Комплектный")
    assert kit_prof["kit_id"] == kit
    # Несуществующий комплект подставить нельзя.
    reject(rp, "docprofile.create", {"id": new_id(), "project_id": pid, "kit_id": new_id(), "name": "Х"},
           "not_found")


def test_profile_update_and_activate(pd):
    """Правка профиля версионируется и не трогает созданные ревизии; повторное включение/отключение — отказ;
    включение при занятой области — отказ; ожидание версии — конфликт."""
    pd_ = pd
    rp, pid = pd_["rp"], pd_["pid"]
    prof = make_profile(rp, pid, pattern=None, example=None, statuses=IFC_CEF)
    reject(rp, "docprofile.update", {"id": prof["id"]}, "nothing_to_update",
           expected_version=prof["version"])
    r = rp.ok("docprofile.update", {"id": prof["id"], "label_pattern": r"[A-Z]{3}"},
              expected_version=prof["version"])
    assert r["profile"]["version"] == prof["version"] + 1 and r["profile"]["label_pattern"] == r"[A-Z]{3}"
    assert reject(rp, "docprofile.update", {"id": prof["id"], "name": "Стало"}, "version_conflict",
                  expected_version=prof["version"])["status"] == "conflict"
    # Пример, не подходящий под НОВЫЙ шаблон, отклоняется (проверка по результирующей паре).
    reject(rp, "docprofile.update", {"id": prof["id"], "label_example": "12"}, "example_mismatch",
           expected_version=r["profile"]["version"])
    # Отключение и повторное отключение; включение и повторное включение.
    v = r["profile"]["version"]
    reject(rp, "docprofile.activate", {"id": prof["id"], "active": True}, "already_in_state",
           expected_version=v)
    r = rp.ok("docprofile.activate", {"id": prof["id"], "active": False}, expected_version=v)
    assert r["profile"]["active"] is False
    reject(rp, "docprofile.activate", {"id": prof["id"], "active": False}, "already_in_state",
           expected_version=r["profile"]["version"])
    # Пока первый отключён, второй активировать можно; включение первого поверх активного второго — отказ.
    second = make_profile(rp, pid, name="Второй", pattern=None, example=None)
    reject(rp, "docprofile.activate", {"id": prof["id"], "active": True}, "profile_active_exists",
           expected_version=r["profile"]["version"])
    rp.ok("docprofile.activate", {"id": second["id"], "active": False}, expected_version=second["version"])


# ====================================================================== шлюз шифра при создании ревизии
def test_label_gate_on_revision_create(pd):
    """Ревизия создаётся только с шифром по шаблону действующего профиля; комплектный профиль переопределяет
    проектный; без профиля (или с отключённым) формат не проверяется."""
    pd_ = pd
    rp, eng, pid, kit = pd_["rp"], pd_["eng"], pd_["pid"], pd_["kit"]

    def new_doc(code):
        eng.ok("document.create", {"id": (did := new_id()), "kit_id": kit, "code": code, "title": code})
        return did

    def try_create(did, label):
        doc = [d for d in pull(eng)["documents"] if d["id"] == did][0]
        up = upload(eng, pid, f"файл {label}".encode())
        ver = eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": f"{label}.pdf"},
                     expected_version=doc["version"])["document_version"]["id"]
        return eng.cmd("docrev.create", {"id": new_id(), "document_id": did, "label": label,
                                         "doc_version_id": ver})

    # Без профиля — любой непустой шифр.
    r = try_create(pd_["doc"], "01")
    assert r["status"] == "accepted" and r["result"]["revision"]["label"] == "01"
    prof = make_profile(rp, pid, pattern=r"REV-\d{2}", example="REV-01")
    # Шифр не по шаблону — отказ с указанием профиля; по шаблону — создаётся.
    d1 = new_doc("АТХ-002")
    r = try_create(d1, "01")
    assert r["status"] == "rejected" and r["code"] == "label_pattern_mismatch", r
    r = try_create(d1, "REV-01")
    assert r["status"] == "accepted", r
    # Комплектный профиль переопределяет проектный: проверка идёт по его шаблону.
    kit_prof = make_profile(rp, pid, kit=kit, pattern=r"K\d{3}", example="K001", name="Комплектный")
    d2 = new_doc("АТХ-003")
    r = try_create(d2, "REV-02")
    assert r["status"] == "rejected" and r["code"] == "label_pattern_mismatch"
    r = try_create(d2, "K001")
    assert r["status"] == "accepted"
    # Отключение комплектного → снова действует проектный; отключение обоих → без проверки.
    rp.ok("docprofile.activate", {"id": kit_prof["id"], "active": False}, expected_version=kit_prof["version"])
    d3 = new_doc("АТХ-004")
    r = try_create(d3, "K002")
    assert r["status"] == "rejected" and r["code"] == "label_pattern_mismatch"
    rp.ok("docprofile.activate", {"id": prof["id"], "active": False}, expected_version=prof["version"])
    r = try_create(d3, "свободный шифр")
    assert r["status"] == "accepted"


# ====================================================================== предпросмотр
def test_preview(pd):
    """docprofile.preview ничего не записывает: без шаблона сверяет с действующим профилем (комплектный важнее),
    с шаблоном — проверяет его; без активного профиля — «не проверяется». Читать может вся команда проекта."""
    pd_ = pd
    rp, eng, pid, kit = pd_["rp"], pd_["eng"], pd_["pid"], pd_["kit"]
    # Нет профиля — любой шифр допустим, pattern=None.
    r = eng.ok("docprofile.preview", {"project_id": pid, "label": "любой"})
    assert r["matches"] is True and r["pattern"] is None and r["profile"] is None
    prof = make_profile(rp, pid, pattern=r"[A-Z]{2}\d{2}", example="AB01")
    r = eng.ok("docprofile.preview", {"project_id": pid, "label": "AB01"})
    assert r["matches"] is True and r["profile"]["id"] == prof["id"]
    r = eng.ok("docprofile.preview", {"project_id": pid, "label": "ab01"})
    assert r["matches"] is False
    # Комплектный профиль важнее проектного при указании комплекта; проектный — без него.
    make_profile(rp, pid, kit=kit, pattern=r"K\d{3}", example="K001", name="Комплектный")
    r = eng.ok("docprofile.preview", {"project_id": pid, "kit_id": kit, "label": "K001"})
    assert r["matches"] is True and r["profile"]["kit_id"] == kit
    r = eng.ok("docprofile.preview", {"project_id": pid, "kit_id": kit, "label": "AB01"})
    assert r["matches"] is False
    # Явный шаблон («пример» при настройке): профиль не ищется, БД не меняется.
    r = eng.ok("docprofile.preview", {"project_id": pid, "label": "ZZ99", "label_pattern": r"[A-Z]{2}\d{2}"})
    assert r["matches"] is True and r["profile"] is None and r["pattern"] == r"[A-Z]{2}\d{2}"
    reject(eng, "docprofile.preview", {"project_id": pid, "label": "x", "label_pattern": "["},
           "bad_label_pattern")
    # Предпросмотр не создал ни профилей, ни ревизий.
    res = pull(eng)
    assert len(res["document_profiles"]) == 2 and res["document_revisions"] == []
    # Чужой проект — запрет.
    reject(eng, "docprofile.preview", {"project_id": new_id(), "label": "x"}, "not_found")


# ====================================================================== статус выдачи ревизии
def test_set_issue(pd):
    """Статус выдачи — код из словаря действующего профиля; одна действующая запись, смена версионирует строку;
    код и подпись — снимок (правка словаря историю не переписывает)."""
    pd_ = pd
    rp, eng, pid = pd_["rp"], pd_["eng"], pd_["pid"]
    rev = make_rev(pd_, "01")
    # Нет профиля со словарём → отказ (проверяем на РП, у кого право есть).
    reject(rp, "docrev.set_issue", {"id": rev["id"], "status_code": "IFC"}, "no_issue_profile")
    # Профиль без словаря (пустой список) — тоже no_issue_profile.
    bare = make_profile(rp, pid, pattern=None, example=None, name="Без словаря")
    reject(rp, "docrev.set_issue", {"id": rev["id"], "status_code": "IFC"}, "no_issue_profile")
    rp.ok("docprofile.activate", {"id": bare["id"], "active": False}, expected_version=bare["version"])
    prof = make_profile(rp, pid, pattern=None, example=None, statuses=IFC_CEF)
    # Ставит РП (по контракту прав — проектное руководство), участник — нет.
    assert reject(eng, "docrev.set_issue", {"id": rev["id"], "status_code": "IFC"},
                  "no_authority")["status"] == "denied"
    reject(rp, "docrev.set_issue", {"id": rev["id"], "status_code": "XXX"}, "unknown_issue_code")
    r = rp.ok("docrev.set_issue", {"id": rev["id"], "status_code": "IFC"})
    issue = r["issue"]
    assert issue["status_code"] == "IFC" and issue["status_label"] == "Для строительства"
    assert issue["profile_id"] == prof["id"] and issue["version"] == 1
    # Код без учёта регистра; повтор той же установки — отказ «уже в этом состоянии».
    reject(rp, "docrev.set_issue", {"id": rev["id"], "status_code": "ifc"}, "already_in_state")
    r = rp.ok("docrev.set_issue", {"id": rev["id"], "status_code": "cef"})
    assert r["issue"]["status_code"] == "CEF" and r["issue"]["version"] == 2
    # Снимок: правка подписи в словаре профиля не переписывает установленный статус.
    prof2 = rp.ok("docprofile.update", {"id": prof["id"], "issue_statuses": [
        {"code": "IFC", "label_ru": "Для производства работ"}, {"code": "CEF", "label_ru": "Для сведения"}]},
        expected_version=prof["version"])["profile"]
    got = [i for i in pull(eng)["document_revision_issues"] if i["revision_id"] == rev["id"]][0]
    assert got["status_label"] == "Для сведения"
    # Профиль размеченных ревизий считает установки.
    got_prof = [x for x in pull(eng)["document_profiles"] if x["id"] == prof["id"]][0]
    assert got_prof["revisions"] == 1 and got_prof["issue_statuses"] == prof2["issue_statuses"]
    # Статус выдачи не мешает потоку ревизии: доработка и проверка идут как раньше.
    rev = trans(eng, rev_of(pd_, rev["id"]), "ready_for_rp")["result"]["revision"]
    assert rev["status"] == "ready_for_rp"


# ====================================================================== pull
def test_pull_slice(pd):
    """Pull отдаёт профили и статусы выдачи только читаемых проектов; посторонний пользователь их не видит."""
    pd_ = pd
    rp, eng, pid = pd_["rp"], pd_["eng"], pd_["pid"]
    prof = make_profile(rp, pid, statuses=IFC_CEF)
    rev = make_rev(pd_, "AB01")
    rp.ok("docrev.set_issue", {"id": rev["id"], "status_code": "IFC"})
    res = pull(eng)
    assert [x["id"] for x in res["document_profiles"]] == [prof["id"]]
    assert [x["revision_id"] for x in res["document_revision_issues"]] == [rev["id"]]
    world = pd_["world"]
    outsider = world.login("lead1")
    res = pull(outsider)
    assert res["document_profiles"] == [] and res["document_revision_issues"] == []
