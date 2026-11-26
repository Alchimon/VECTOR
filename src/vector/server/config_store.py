"""Настройки компании по модели 07 (первая часть; D-26, A-26).

Модуль — примесь (mixin) к `Service`, как stock.py и calc.py: транзакция, идемпотентность по operation_id, аудит
и проверка прав — в service.py; здесь только правила настроек.

Как устроено:

    contracts/settings.slice1.json ── каталог: ключ, допустимые значения, strictest, области, решение
                 │
    config.publish {key, value, reason} + expected_version (номер действующей версии; 0 — не задавалась)
                 │
                 ▼
    config_versions: (key, scope, version) — НЕИЗМЕНЯЕМЫЕ записи; действует наибольшая версия.
    Откат = публикация прежнего значения новой версией (история не переписывается, 07 §3 п. 5).

Главные правила:

* настройка вне каталога не существует — произвольных ключей и значений нет (07 §4);
* «не задано» → действует самое строгое значение каталога (strictest), а не выдуманное «по умолчанию» (07 §5);
* публикует администратор (A-26), только при связи, с причиной; параллельная публикация другим администратором
  даёт конфликт версии, а не молчаливую перезапись;
* настройка не может расширить права — она лишь выбирает вариант поведения, заложенный в коде и описанный в каталоге.

Чего здесь НЕТ (следующие шаги 07): черновик и предпросмотр «что изменится», области отдел / проект / роль /
пользователь, офлайн-пометка «настройки на версию N» в клиенте.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import sqlalchemy as sa

from vector.core.errors import Conflict, Rejected

from .db import config_versions, users
from .validation import iso, only_keys, req_str

COMPANY = "company"


class ConfigMixin:
    """Публикация и чтение настроек компании. Используется как примесь к `Service`."""

    # ------------------------------------------------------------ чтение
    def _config_items(self) -> dict:
        """Каталог настроек из контрактов: ключ → описание (тип, значения, strictest, области)."""
        return self.contracts.settings.get("items", {})

    def _config_current(self, conn, key: str, scope_type: str = COMPANY, scope_id: str = "") -> Optional[dict]:
        """Действующая (последняя) версия настройки в области или None, если настройка не задавалась."""
        return conn.execute(sa.select(config_versions).where(
            config_versions.c.key == key, config_versions.c.scope_type == scope_type,
            config_versions.c.scope_id == scope_id).order_by(config_versions.c.version.desc()).limit(1)).mappings().first()

    def config_value(self, conn, key: str) -> Any:
        """Значение настройки для правил домена: опубликованное или, если не задано, самое строгое (strictest).

        Вызывается внутри транзакции команды — значение читается в момент эффекта, как и права.
        """
        item = self._config_items()[key]          # KeyError = ошибка программы: правило ссылается на ключ вне каталога
        cur = self._config_current(conn, key)
        return json.loads(cur["value_json"]) if cur else item["strictest"]

    def v_config(self, conn, key: str, names: Optional[dict] = None) -> dict:
        """Настройка для клиента: описание из каталога + действующее значение, кто/когда/почему его задал."""
        item = self._config_items()[key]
        cur = self._config_current(conn, key)
        value = json.loads(cur["value_json"]) if cur else item["strictest"]
        labels = {v["value"]: v["label_ru"] for v in item.get("values", [])}
        if cur and names is None:
            names = {r.id: r.display_name for r in conn.execute(sa.select(users.c.id, users.c.display_name)
                                                                .where(users.c.id == cur["author"]))}
        return {"key": key, "group": item.get("group"), "label_ru": item["label_ru"],
                "description_ru": item.get("description_ru"), "type": item["type"], "values": item.get("values", []),
                "strictest": item["strictest"], "scope_type": COMPANY, "value": value, "value_label_ru": labels.get(value),
                "is_set": cur is not None, "version": cur["version"] if cur else 0,
                "set_by": (names or {}).get(cur["author"]) if cur else None,
                "set_at": iso(cur["created_at"]) if cur else None, "reason": cur["reason"] if cur else None,
                "decision": item.get("decision")}

    def _pull_config(self, conn) -> list[dict]:
        """Все настройки каталога с действующими значениями — для pull (видны всем: это правила работы, а не
        секреты; клиент по ним показывает подсказки, решение всё равно принимает сервер).
        """
        names = {r.id: r.display_name for r in conn.execute(sa.select(users.c.id, users.c.display_name))}
        return [self.v_config(conn, k, names) for k in sorted(self._config_items())]

    # ------------------------------------------------------------ публикация
    def _config_publish(self, ctx, p, ev):
        """config.publish {key, value, reason, scope_type?} + expected_version — опубликовать новую версию настройки.

        expected_version — номер версии, которую видел администратор (0 — «не задавалась»). Если за это время
        опубликовали другую версию — конфликт с текущим состоянием (как у любых объектов с версией).
        """
        only_keys(p, {"key", "value", "reason", "scope_type"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "config.publish", ctx.now)
        key = p.get("key")
        items = self._config_items()
        if not isinstance(key, str) or key not in items:
            raise Rejected("unknown_setting", "Такой настройки нет в каталоге настроек.", {"field": "key"})
        item = items[key]
        scope = p.get("scope_type", COMPANY)
        if scope not in item.get("scopes", [COMPANY]) or scope != COMPANY:
            raise Rejected("bad_scope", "Эта настройка задаётся только для компании в целом.", {"field": "scope_type"})
        value = p.get("value")
        allowed = [v["value"] for v in item.get("values", [])]
        if item["type"] != "enum" or value not in allowed:
            raise Rejected("bad_value", "Недопустимое значение настройки. Допустимо: " +
                           ", ".join(v["label_ru"] for v in item.get("values", [])) + ".", {"field": "value"})
        opt = next(v for v in item["values"] if v["value"] == value)
        if opt.get("available") is False:
            # Вариант описан в каталоге, но возможность ещё не подключена (например, КЭП): публикация отклоняется,
            # иначе правило «как подписывать» указало бы на то, чего нет, и работа встала бы (fail-closed).
            raise Rejected("value_unavailable", opt.get("unavailable_ru") or "Этот вариант пока недоступен.", {"field": "value"})
        reason = req_str(p, "reason", 2000)
        cur = self._config_current(ctx.conn, key)
        cur_version = cur["version"] if cur else 0
        if ev != cur_version:
            raise Conflict("version_conflict", f"Настройку уже изменили: ваша версия {ev}, действует {cur_version}. "
                           "Обновите данные и решите заново.", {"expected_version": ev, "current": self.v_config(ctx.conn, key)})
        previous = json.loads(cur["value_json"]) if cur else None
        if cur is not None and previous == value:
            raise Rejected("no_changes", "Это значение уже действует.")
        ctx.conn.execute(config_versions.insert().values(
            id=str(uuid.uuid4()), key=key, scope_type=COMPANY, scope_id="", version=cur_version + 1,
            value_json=json.dumps(value, ensure_ascii=False), reason=reason, author=ctx.user_id, created_at=ctx.now))
        ctx.log("config", key, auth, cur_version, cur_version + 1,
                {"scope_type": COMPANY, "value": value, "previous": previous,
                 "previous_effective": previous if cur else item["strictest"], "reason": reason})
        return {"config": self.v_config(ctx.conn, key)}
