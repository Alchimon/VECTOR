"""Вычислитель формул таблиц (Ф1.1, 55-office-documents.md § 4): арифметика, ссылки A1, диапазоны A1:B9,
функции СУММ/SUM, СРЗНАЧ/AVERAGE, МИН/MIN, МАКС/MAX, СЧЁТ/COUNT, ЕСЛИ/IF, ОКРУГЛ/ROUND, конкатенация «&».

Хранение формул — каноническая EN-нотация (SUM); русские имена принимаются и переводятся при разборе.
Циклические ссылки — ошибка ячейки #ЦИКЛ! (не зависание). Округление математическое (как в Excel), деньги —
Decimal. Парсер — рекурсивный спуск; грамматика намеренно минимальна (расширение — следующие порции).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from .model import parse_addr, make_addr

# Русские имена функций → канонические EN (как в локализованном Excel).
FUNC_RU_TO_EN = {"СУММ": "SUM", "СРЗНАЧ": "AVERAGE", "МИН": "MIN", "МАКС": "MAX", "СЧЁТ": "COUNT",
                 "СЧЕТ": "COUNT", "ЕСЛИ": "IF", "ОКРУГЛ": "ROUND"}

MAX_STEPS = 100_000                                # страховка от вырожденных формул чужого файла


class FormulaError(Exception):
    """Ошибка формулы: текст для ячейки (#ИМЯ?, #ДЕЛ/0!, #ЦИКЛ!, #ЗНАЧ!)."""
    def __init__(self, cell_text: str):
        super().__init__(cell_text)
        self.cell_text = cell_text                 # то, что покажет ячейка


_TOKEN_RE = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:[.,]\d+)?)                    # число (запятая — русская десятичная)
    | (?P<range>\$?[A-ZА-Я]{1,3}\$?\d+\s*:\s*\$?[A-ZА-Я]{1,3}\$?\d+)   # диапазон A1:B9
    | (?P<ref>\$?[A-ZА-Я]{1,3}\$?\d+)             # ссылка A1 (и русские буквы — терпимо)
    | (?P<str>"[^"]*")                            # строковый литерал
    | (?P<func>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9.]*)(?=\s*\()         # функция
    | (?P<op><=|>=|<>|[+\-*/^&(),;=<>])           # операторы и разделители (многосимвольные — первыми)
    )""", re.VERBOSE)


def _to_latin_ref(ref: str) -> str:
    """Русские буквы колонок (похожи на латиницу) → латиница: А1 → A1. Без '$'."""
    table = str.maketrans("АВСЕНКМОРТХасенкмортх", "ABCEHKMOPTXacehkmoptx")
    return ref.replace("$", "").translate(table)


class _Tok:
    def __init__(self, kind, val):
        self.kind, self.val = kind, val
    def __repr__(self):                            # для отладки тестов
        return f"<{self.kind}:{self.val}>"


def _tokenize(text: str) -> list[_Tok]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise FormulaError("#ИМЯ?")
        pos = m.end()
        for kind in ("num", "range", "ref", "str", "func", "op"):
            v = m.group(kind)
            if v is not None:
                if kind == "num":
                    v = v.replace(",", ".")
                elif kind == "ref":
                    v = _to_latin_ref(v.replace("$", "").replace(" ", ""))
                elif kind == "range":
                    v = [_to_latin_ref(p.replace("$", "").strip()) for p in v.split(":")]
                elif kind == "func":
                    v = FUNC_RU_TO_EN.get(v.upper(), v.upper())
                elif kind == "str":
                    v = v[1:-1]
                out.append(_Tok(kind, v))
                break
    out.append(_Tok("end", None))
    return out


class _Parser:
    """Рекурсивный спуск: сравнение → конкатенация → +− → */ → ^ → унарный − → атом."""

    def __init__(self, tokens, resolve):
        self.toks, self.i, self.resolve, self.steps = tokens, 0, resolve, 0

    def _peek(self) -> _Tok:
        return self.toks[self.i]

    def _next(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect_op(self, op: str) -> None:
        t = self._next()
        if t.kind != "op" or t.val != op:
            raise FormulaError("#ИМЯ?")

    def _tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise FormulaError("#ЗНАЧ!")

    def parse(self):
        v = self._cmp()
        if self._peek().kind != "end":
            raise FormulaError("#ИМЯ?")
        return v

    def _cmp(self):
        left = self._concat()
        while self._peek().kind == "op" and self._peek().val in ("=", "<>", "<", ">", "<=", ">="):
            op = self._next().val
            right = self._concat()
            left = self._compare(op, left, right)
        return left

    @staticmethod
    def _compare(op, a, b) -> bool:
        a, b = _Parser._coerce_pair(a, b)
        try:
            return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        except TypeError:
            raise FormulaError("#ЗНАЧ!") from None

    @staticmethod
    def _coerce_pair(a, b):
        """Число со строкой-числом сравниваем как числа (Excel так делает); иначе — как есть."""
        if isinstance(a, Decimal) and isinstance(b, str):
            try:
                return a, Decimal(b.replace(",", "."))
            except InvalidOperation:
                return a, b
        if isinstance(b, Decimal) and isinstance(a, str):
            try:
                return Decimal(a.replace(",", ".")), b
            except InvalidOperation:
                return a, b
        return a, b

    def _concat(self):
        left = self._add()
        while self._peek().kind == "op" and self._peek().val == "&":
            self._next()
            right = self._add()
            left = _to_text(left) + _to_text(right)
        return left

    def _add(self):
        left = self._mul()
        while self._peek().kind == "op" and self._peek().val in ("+", "-"):
            op = self._next().val
            right = self._mul()
            left = _num(left) + _num(right) if op == "+" else _num(left) - _num(right)
        return left

    def _mul(self):
        left = self._pow()
        while self._peek().kind == "op" and self._peek().val in ("*", "/"):
            op = self._next().val
            right = self._pow()
            if op == "*":
                left = _num(left) * _num(right)
            else:
                d = _num(right)
                if d == 0:
                    raise FormulaError("#ДЕЛ/0!")
                left = _num(left) / d
        return left

    def _pow(self):
        base = self._unary()
        while self._peek().kind == "op" and self._peek().val == "^":
            self._next()
            exp = self._unary()
            base = _num(base) ** _num(exp)
        return base

    def _unary(self):
        if self._peek().kind == "op" and self._peek().val == "-":
            self._next()
            return -_num(self._unary())
        if self._peek().kind == "op" and self._peek().val == "+":
            self._next()
            return _num(self._unary())
        return self._atom()

    def _atom(self):
        self._tick()
        t = self._next()
        if t.kind == "num":
            return Decimal(t.val)
        if t.kind == "str":
            return t.val
        if t.kind == "ref":
            return self.resolve(t.val)
        if t.kind == "range":
            return ("RANGE", t.val[0], t.val[1])     # диапазон — только аргументом функции
        if t.kind == "func":
            self._expect_op("(")
            args = []
            if not (self._peek().kind == "op" and self._peek().val == ")"):
                args.append(self._cmp())
                while self._peek().kind == "op" and self._peek().val in (",", ";"):
                    self._next()
                    args.append(self._cmp())
            self._expect_op(")")
            return self._call(t.val, args)
        if t.kind == "op" and t.val == "(":
            v = self._cmp()
            self._expect_op(")")
            return v
        raise FormulaError("#ИМЯ?")

    # ---------------------------------------------------------- функции
    def _call(self, name: str, args):
        flat = []
        for a in args:
            if isinstance(a, tuple) and a and a[0] == "RANGE":
                flat.extend(self._expand_range(a[1], a[2]))
            else:
                flat.append(a)
        nums = [v for v in flat if isinstance(v, Decimal)]
        if name == "SUM":
            return sum(nums, Decimal(0))
        if name == "AVERAGE":
            if not nums:
                raise FormulaError("#ДЕЛ/0!")
            return sum(nums, Decimal(0)) / len(nums)
        if name == "MIN":
            return min(nums) if nums else Decimal(0)
        if name == "MAX":
            return max(nums) if nums else Decimal(0)
        if name == "COUNT":
            return Decimal(len(nums))
        if name == "IF":
            if not 2 <= len(args) <= 3:
                raise FormulaError("#ЗНАЧ!")
            cond = _truthy(args[0])
            if cond:
                return args[1]
            return args[2] if len(args) == 3 else False
        if name == "ROUND":
            if len(args) != 2:
                raise FormulaError("#ЗНАЧ!")
            digits = int(_num(args[1]))
            q = Decimal(1).scaleb(-digits)
            return _num(args[0]).quantize(q, rounding=ROUND_HALF_UP)
        raise FormulaError("#ИМЯ?")

    def _expand_range(self, a: str, b: str) -> list:
        r1, c1 = parse_addr(a)
        r2, c2 = parse_addr(b)
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 100_000:
            raise FormulaError("#ЗНАЧ!")             # диапазон-«тяжеловес» из чужого файла
        out = []
        for r in range(min(r1, r2), max(r1, r2) + 1):
            for c in range(min(c1, c2), max(c1, c2) + 1):
                out.append(self.resolve(make_addr(r, c)))
        return out


def _num(v) -> Decimal:
    """Значение → Decimal: пусто — 0; булево — 1/0; строка-число — число; прочее — #ЗНАЧ!."""
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal(0)
    if isinstance(v, bool):
        return Decimal(1 if v else 0)
    if isinstance(v, str):
        try:
            return Decimal(v.replace(",", ".").strip())
        except (InvalidOperation, ValueError):
            raise FormulaError("#ЗНАЧ!") from None
    raise FormulaError("#ЗНАЧ!")


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return v != 0
    if v is None:
        return False
    return bool(v)


def _to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ИСТИНА" if v else "ЛОЖЬ"
    if isinstance(v, Decimal):
        s = format(v, "f")
        return s[:-2] if s.endswith(".0") else s
    return str(v)


def evaluate(formula: str, resolve, _depth: int = 0) -> Optional[object]:
    """Вычислить формулу (с «=» или без). resolve(addr) → значение ячейки (Decimal/str/bool/None);
    resolve обязан сам вычислять зависимости и отслеживать циклы (см. recalc в sheet_calc).

    Результат: Decimal | str | bool. Ошибки — FormulaError с текстом для ячейки (#ДЕЛ/0! и т.п.).
    """
    text = formula.strip()
    if text.startswith("="):
        text = text[1:]
    if not text:
        raise FormulaError("#ИМЯ?")
    return _Parser(_tokenize(text), resolve).parse()


def normalize(formula: str) -> str:
    """Привести формулу к каноническому виду для хранения: '=SUM(A1:A9)' (EN-имена, латинские колонки)."""
    toks = _tokenize(formula.strip().lstrip("="))
    out = []
    for t in toks:
        if t.kind == "end":
            break
        if t.kind == "num":
            out.append(t.val)
        elif t.kind == "ref":
            out.append(t.val)
        elif t.kind == "range":
            out.append(f"{t.val[0]}:{t.val[1]}")
        elif t.kind == "str":
            out.append(f'"{t.val}"')
        elif t.kind == "func":
            out.append(t.val)
        else:
            out.append(t.val)
    return "=" + "".join(out)


def _dependencies(formula: str) -> set[str]:
    """Все адреса, от которых зависит формула (ссылки + ячейки диапазонов) — для топологии пересчёта."""
    deps: set[str] = set()
    try:
        toks = _tokenize(formula.strip().lstrip("="))
    except FormulaError:
        return deps
    for t in toks:
        if t.kind == "ref":
            deps.add(t.val)
        elif t.kind == "range":
            try:
                r1, c1 = parse_addr(t.val[0])
                r2, c2 = parse_addr(t.val[1])
            except ValueError:
                continue
            if (max(r1, r2) - min(r1, r2) + 1) * (max(c1, c2) - min(c1, c2) + 1) > 100_000:
                continue
            for r in range(min(r1, r2), max(r1, r2) + 1):
                for c in range(min(c1, c2), max(c1, c2) + 1):
                    deps.add(make_addr(r, c))
    return deps


def recalc(sheet) -> dict[str, object]:
    """Пересчитать все формулы листа: значения обновляются в самих ячейках. Циклы → #ЦИКЛ! в ячейках цикла.

    Топологический порядок по графу зависимостей (Kahn): каждая формула вычисляется один раз, когда её
    входы уже посчитаны; узлы в циклах получают #ЦИКЛ!, остальные считаются честно. Возвращает
    {адрес: значение} всех формульных ячеек.
    """
    formulas = {a: c.formula for a, c in sheet.cells.items() if c.formula is not None}
    deps = {a: _dependencies(f) & (set(formulas) | set(sheet.cells)) for a, f in formulas.items()}
    # Kahn: входы, которые сами формулы, должны быть вычислены раньше.
    pending = {a: {d for d in ds if d in formulas} for a, ds in deps.items()}
    order: list[str] = []
    progress = True
    while pending and progress:
        progress = False
        for a in list(pending):
            if not pending[a]:
                order.append(a)
                del pending[a]
                progress = True
                for ds in pending.values():
                    ds.discard(a)
    result: dict[str, object] = {}
    for a in pending:                                 # циклы и зависящие от циклов
        sheet.cells[a].value = "#ЦИКЛ!"
        result[a] = "#ЦИКЛ!"
    for addr in order:
        cell = sheet.cells[addr]
        resolve = lambda ad: sheet.get(ad).value      # входы уже посчитаны (топологический порядок)
        try:
            cell.value = evaluate(cell.formula, resolve)
        except FormulaError as e:
            cell.value = e.cell_text
        result[addr] = cell.value
    return result
