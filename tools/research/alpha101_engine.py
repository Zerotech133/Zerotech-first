"""Alpha101 表达式引擎（Tushare factor_list 的 101 条公式）。

设计
----
- 公式来源：``tools/research/alpha101_formulas.json``（从 ``tushare.factor_list`` 快照提取，
  已清理中文说明/辅助定义，``IndClass.sector``/``SubIndustry`` 归一为 ``Industry``，
  ``Delta_Mix`` 归一为 ``(High+Low)/2``，``^`` 归一为 ``**``）。
- 解析：手写词法 + 递归下降，支持 Alpha101 常见的 C 风格三元表达式 ``cond ? a : b``、
  比较/布尔运算、四则/乘方，以及 ``Rank``/``Scale``/``IndNeutralize``/``Ts_*``/
  ``Decay_Linear``/``Correlation`` 等函数。
- 求值：输入为 factor_lib.Panel（或任何有 fields/dates/codes/load_meta 的同构对象），
  输出与面板同形状的 DataFrame；所有滚动窗口只用过去数据（PIT）。
- 变量口径：
  ``Close/Open/High/Low/Volume`` 来自日行情；``VWAP = amount*10/vol``；
  ``Returns = adj_close.pct_change()``；``Cap = total_mv``；``ADVn = MA(volume, n)``；
  ``Industry`` 来自 stock_basic.industry（Tushare 无 SubIndustry，统一用 industry）。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FORMULA_FILE = Path(__file__).with_name("alpha101_formulas.json")


class FormulaError(ValueError):
    """公式提取/解析/求值错误。"""


def load_formulas(path: str | Path | None = None) -> dict[str, str]:
    """读取 Alpha101 公式表（name -> expression）。"""
    p = Path(path) if path else FORMULA_FILE
    payload = json.loads(p.read_text(encoding="utf-8"))
    formulas = payload.get("formulas", payload)
    if not isinstance(formulas, dict) or not formulas:
        raise FormulaError(f"公式表为空或格式错误: {p}")
    return {str(k): str(v) for k, v in formulas.items()}


def clean_formula(text: str) -> str:
    """清理单条公式（兼容未来从描述文本直接传入）。"""
    text = text.strip()
    if "Alpha" in text and "=" in text:
        text = text.split("Alpha", 1)[1]
        text = text.split("=", 1)[1]
    text = text.split("说明:", 1)[0]
    text = re.sub(r"\s+", " ", text)
    text = text.replace("IndClass.sector", "Industry").replace("IndClass", "Industry")
    text = text.replace("SubIndustry", "Industry").replace("Sector", "Industry")
    text = text.replace("Delta_Mix", "(High+Low)/2").replace("^", "**")
    text = re.split(r"[^\x00-\x7f]", text)[0].strip().rstrip(";").strip()
    return text


_TOKEN_RE = re.compile(
    r"""
    \s+
  | (?P<NUMBER>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
  | (?P<OP>\*\*|<=|>=|==|!=|\|\||&&|[+\-*/%^<>!(),?:])
    """,
    re.VERBOSE,
)

_FUNCTIONS = {
    "Rank", "Scale", "IndNeutralize", "Delay", "Delta", "Sum", "Product", "StdDev",
    "Ts_Min", "Ts_Max", "Ts_ArgMin", "Ts_ArgMax", "Ts_Rank", "Decay_Linear",
    "Correlation", "Covariance", "Sign", "Abs", "Log", "Min", "Max",
    "SignedPower", "IF",
}


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise FormulaError(f"无法识别的字符 {text[pos]!r}（位置 {pos}）: {text}")
        pos = m.end()
        kind = m.lastgroup
        if kind is None:
            continue
        tokens.append((kind, m.group()))
    tokens.append(("EOF", ""))
    return tokens


# ---------------------------------------------------------------- 求值器
class _Engine:
    def __init__(self, panel: Any):
        self.panel = panel
        self.dates = pd.Index(panel.dates)
        self.codes = pd.Index(panel.codes)
        self.fields = panel.fields
        self.load_meta = getattr(panel, "load_meta", {}) or {}
        self._cache: dict[str, Any] = {}

    # ---------- 基础工具
    def _field(self, name: str) -> pd.DataFrame:
        if name in self._cache:
            return self._cache[name]
        if name in self.fields:
            df = self.fields[name]
        elif name == "adj_close":
            df = self.fields.get("close")
        elif name == "vwap":
            amount = self.fields.get("amount")
            vol = self.fields.get("vol")
            if amount is None or vol is None:
                raise FormulaError("VWAP 需要 amount/vol 字段")
            df = amount * 10.0 / vol.replace(0.0, np.nan)
        elif name == "returns":
            close = self.fields.get("adj_close", self.fields.get("close"))
            if close is None:
                raise FormulaError("Returns 需要 close/adj_close 字段")
            df = close.pct_change()
        elif name == "cap":
            df = self.fields.get("total_mv")
            if df is None:
                raise FormulaError("Cap 需要 total_mv 字段")
        else:
            raise FormulaError(f"未知字段/变量: {name}")
        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df, index=self.dates, columns=self.codes)
        df = df.reindex(index=self.dates, columns=self.codes)
        self._cache[name] = df
        return df

    def _df(self, value: Any) -> pd.DataFrame:
        if isinstance(value, pd.DataFrame):
            return value.reindex(index=self.dates, columns=self.codes)
        if isinstance(value, pd.Series):
            if len(value) == len(self.codes) and value.index.hasnans is False:
                # 列向量（行业等），按行广播
                if set(map(str, value.index)) == set(map(str, self.codes)):
                    return pd.DataFrame(
                        np.tile(value.to_numpy(dtype=float), (len(self.dates), 1)),
                        index=self.dates, columns=self.codes)
            if len(value) == len(self.dates):
                return pd.DataFrame(
                    np.tile(value.to_numpy(dtype=float).reshape(-1, 1), (1, len(self.codes))),
                    index=self.dates, columns=self.codes)
            raise FormulaError(f"Series 长度无法与面板对齐: {len(value)}")
        if np.isscalar(value):
            return pd.DataFrame(float(value), index=self.dates, columns=self.codes)
        raise FormulaError(f"不支持的表达式值: {type(value)!r}")

    def _where(self, cond: Any, a: Any, b: Any) -> pd.DataFrame:
        c = self._df(cond).astype(bool)
        av = self._df(a)
        bv = self._df(b)
        return av.where(c, bv)

    def _bin(self, op: str, a: Any, b: Any) -> Any:
        if op == "||":
            return self._df(a).astype(bool) | self._df(b).astype(bool)
        if op == "&&":
            return self._df(a).astype(bool) & self._df(b).astype(bool)
        if op in ("<", ">", "<=", ">=", "==", "!="):
            av = self._df(a) if not isinstance(a, (int, float)) else a
            bv = self._df(b) if not isinstance(b, (int, float)) else b
            # geometry 等元数据不参与比较；这里只对数值/DataFrame 运算
            return {
                "<": lambda x, y: x < y, ">": lambda x, y: x > y,
                "<=": lambda x, y: x <= y, ">=": lambda x, y: x >= y,
                "==": lambda x, y: x == y, "!=": lambda x, y: x != y,
            }[op](av, bv)
        if op in ("+", "-", "*", "/", "%"):
            av = self._df(a) if not isinstance(a, (int, float)) else a
            bv = self._df(b) if not isinstance(b, (int, float)) else b
            fn = {"+": lambda x, y: x + y, "-": lambda x, y: x - y,
                  "*": lambda x, y: x * y, "/": lambda x, y: x / y,
                  "%": lambda x, y: x % y}[op]
            with np.errstate(divide="ignore", invalid="ignore"):
                return fn(av, bv)
        raise FormulaError(f"不支持的运算符: {op}")

    def _pow(self, a: Any, b: Any) -> pd.DataFrame:
        av = self._df(a) if not isinstance(a, (int, float)) else a
        bv = self._df(b) if not isinstance(b, (int, float)) else b
        with np.errstate(divide="ignore", invalid="ignore"):
            return av ** bv

    def _rank(self, x: Any) -> pd.DataFrame:
        return self._df(x).rank(axis=1, pct=True)

    def _scale(self, x: Any) -> pd.DataFrame:
        d = self._df(x)
        denom = d.abs().sum(axis=1)
        return d.div(denom, axis=0)

    def _ind_neutralize(self, x: Any, group: Any) -> pd.DataFrame:
        d = self._df(x)
        if d.empty:
            return d
        if group is None:
            group = self.load_meta.get("industry") if self.load_meta else None
        if group is None:
            # 缺少行业字段时退化为不做中性化，保证合成/空面板可计算
            return d
        if isinstance(group, pd.Series):
            g = group.reindex(self.codes).fillna("UNKNOWN")
        elif isinstance(group, pd.DataFrame):
            g = group.iloc[0].reindex(self.codes).fillna("UNKNOWN")
        else:
            raise FormulaError("IndNeutralize 第二个参数必须是行业字段")
        means = d.T.groupby(g).transform("mean").T
        return d - means

    def _rolling(self, d: pd.DataFrame, w: int):
        w = int(w)
        return d.rolling(w, min_periods=min(w, max(2, w // 2)))

    # ---------- Alpha101 函数
    def _fn_delay(self, d: pd.DataFrame, n: int) -> pd.DataFrame:
        return d.shift(int(n))

    def _fn_delta(self, d: pd.DataFrame, n: int) -> pd.DataFrame:
        return d - d.shift(int(n))

    def _fn_ts_rank(self, d: pd.DataFrame, w: int) -> pd.DataFrame:
        w = int(w)

        def last_rank(a: np.ndarray) -> float:
            v = a[np.isfinite(a)]
            if v.size == 0:
                return np.nan
            last = a[-1]
            if not np.isfinite(last):
                return np.nan
            return float((v <= last).sum() / v.size)
        return self._rolling(d, w).apply(last_rank, raw=True)

    def _fn_arg(self, d: pd.DataFrame, w: int, which: str) -> pd.DataFrame:
        w = int(w)

        def arg(a: np.ndarray) -> float:
            if not np.isfinite(a).any():
                return np.nan
            return float(np.nanargmax(a) if which == "max" else np.nanargmin(a))
        return self._rolling(d, w).apply(arg, raw=True)

    def _fn_decay_linear(self, d: pd.DataFrame, w: int) -> pd.DataFrame:
        w = int(w)

        def weighted(a: np.ndarray) -> float:
            mask = np.isfinite(a)
            if not mask.any():
                return np.nan
            # 滚动窗口未满时，a 的长度可能小于 w，按实际长度取「最近优先」权重
            ww = np.arange(len(a), 0, -1, dtype=float)
            return float(np.dot(a[mask], ww[mask]) / ww[mask].sum())
        return self._rolling(d, w).apply(weighted, raw=True)

    def _roll_moments(self, x: pd.DataFrame, y: pd.DataFrame, w: int):
        mx = self._rolling(x, w).mean()
        my = self._rolling(y, w).mean()
        cov = self._rolling(x * y, w).mean() - mx * my
        vx = self._rolling(x * x, w).mean() - mx * mx
        vy = self._rolling(y * y, w).mean() - my * my
        return cov, vx, vy

    def call(self, name: str, args: list[Any]) -> Any:
        if name not in _FUNCTIONS:
            raise FormulaError(f"未知函数: {name}")
        if name == "IF":
            if len(args) != 3:
                raise FormulaError("IF 需要 3 个参数")
            return self._where(args[0], args[1], args[2])
        if name in ("Min", "Max"):
            if len(args) != 2:
                raise FormulaError(f"{name} 需要 2 个参数")
            a, b = self._df(args[0]), self._df(args[1])
            return a.where(a <= b, b) if name == "Min" else a.where(a >= b, b)
        if name == "Rank":
            return self._rank(args[0])
        if name == "Scale":
            return self._scale(args[0])
        if name == "IndNeutralize":
            if len(args) == 1:
                group = self.load_meta.get("industry")
            elif len(args) == 2:
                group = args[1]
            else:
                raise FormulaError("IndNeutralize 需要 1 或 2 个参数")
            return self._ind_neutralize(args[0], group)
        if name == "Sign":
            return np.sign(self._df(args[0]))
        if name == "Abs":
            return self._df(args[0]).abs()
        if name == "Log":
            return np.log(self._df(args[0]).where(self._df(args[0]) > 0))
        if name == "SignedPower":
            x = self._df(args[0])
            p = self._df(args[1]) if not np.isscalar(args[1]) else float(args[1])
            return np.sign(x) * (x.abs() ** p)
        if name in ("Delay", "Delta"):
            x = self._df(args[0])
            n = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            return self._fn_delay(x, n) if name == "Delay" else self._fn_delta(x, n)
        if name in ("Sum", "Product", "StdDev", "Ts_Min", "Ts_Max"):
            x = self._df(args[0])
            w = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            if name == "Sum":
                return self._rolling(x, w).sum()
            if name == "Product":
                return self._rolling(x, w).apply(
                    lambda a: float(np.nanprod(a)) if np.isfinite(a).any() else np.nan, raw=True)
            if name == "StdDev":
                return self._rolling(x, w).std(ddof=0)
            if name == "Ts_Min":
                return self._rolling(x, w).min()
            return self._rolling(x, w).max()
        if name == "Ts_ArgMax":
            x = self._df(args[0])
            w = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            return self._fn_arg(x, w, "max")
        if name == "Ts_ArgMin":
            x = self._df(args[0])
            w = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            return self._fn_arg(x, w, "min")
        if name == "Ts_Rank":
            x = self._df(args[0])
            w = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            return self._fn_ts_rank(x, w)
        if name == "Decay_Linear":
            x = self._df(args[0])
            w = int(self._df(args[1]).iloc[0, 0]) if not np.isscalar(args[1]) else int(args[1])
            return self._fn_decay_linear(x, w)
        if name in ("Correlation", "Covariance"):
            x, y = self._df(args[0]), self._df(args[1])
            w = int(self._df(args[2]).iloc[0, 0]) if not np.isscalar(args[2]) else int(args[2])
            cov, vx, vy = self._roll_moments(x, y, w)
            if name == "Covariance":
                return cov
            return cov / np.sqrt((vx * vy).clip(lower=0.0))
        raise FormulaError(f"函数未实现: {name}")

    def variable(self, name: str) -> Any:
        low = name.lower()
        if name in ("Close", "Open", "High", "Low", "Volume"):
            return self._field({"Close": "close", "Open": "open", "High": "high",
                                "Low": "low", "Volume": "vol"}[name])
        if name == "VWAP":
            return self._field("vwap")
        if name == "Returns":
            return self._field("returns")
        if name == "Cap":
            return self._field("cap")
        if name == "Industry":
            return self.load_meta.get("industry")
        m = re.fullmatch(r"ADV(\d+)", name)
        if m:
            n = int(m.group(1))
            return self._rolling(self._field("vol"), n).mean()
        raise FormulaError(f"未知变量: {name}")


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], engine: _Engine):
        self.tokens = tokens
        self.i = 0
        self.engine = engine

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.i]

    def advance(self) -> tuple[str, str]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def match(self, value: str) -> bool:
        if self.peek()[1] == value:
            self.advance()
            return True
        return False

    def expect(self, value: str) -> None:
        tok = self.advance()
        if tok[1] != value:
            raise FormulaError(f"期望 {value!r}，实际 {tok[1]!r}（位置 {self.i - 1}）")

    def parse(self) -> Any:
        value = self.ternary()
        if self.peek()[0] != "EOF":
            raise FormulaError(f"公式尾部多余内容: {self.tokens[self.i:]}")
        return value

    def ternary(self) -> Any:
        cond = self.or_expr()
        if self.match("?"):
            a = self.ternary()
            self.expect(":")
            b = self.ternary()
            return self.engine._where(cond, a, b)
        return cond

    def or_expr(self) -> Any:
        value = self.and_expr()
        while self.match("||"):
            value = self.engine._bin("||", value, self.and_expr())
        return value

    def and_expr(self) -> Any:
        value = self.comparison()
        while self.match("&&"):
            value = self.engine._bin("&&", value, self.comparison())
        return value

    def comparison(self) -> Any:
        value = self.additive()
        while self.peek()[1] in ("<", ">", "<=", ">=", "==", "!="):
            op = self.advance()[1]
            value = self.engine._bin(op, value, self.additive())
        return value

    def additive(self) -> Any:
        value = self.multiplicative()
        while self.peek()[1] in ("+", "-"):
            op = self.advance()[1]
            value = self.engine._bin(op, value, self.multiplicative())
        return value

    def multiplicative(self) -> Any:
        value = self.unary()
        while self.peek()[1] in ("*", "/", "%"):
            op = self.advance()[1]
            value = self.engine._bin(op, value, self.unary())
        return value

    def unary(self) -> Any:
        if self.match("+"):
            return self.unary()
        if self.match("-"):
            return self.engine._bin("*", -1.0, self.unary())
        if self.match("!"):
            return self.engine._bin("==", self.unary(), 0.0)
        return self.power()

    def power(self) -> Any:
        value = self.primary()
        if self.peek()[1] in ("^", "**"):
            self.advance()
            return self.engine._pow(value, self.unary())
        return value

    def primary(self) -> Any:
        kind, text = self.advance()
        if kind == "NUMBER":
            return float(text)
        if kind == "IDENT":
            if self.match("("):
                args: list[Any] = []
                if not self.match(")"):
                    while True:
                        if self.peek()[1] == ")":
                            self.advance()
                            break
                        args.append(self.ternary())
                        if self.match(","):
                            continue
                        self.expect(")")
                        break
                return self.engine.call(text, args)
            return self.engine.variable(text)
        if text == "(":
            value = self.ternary()
            self.expect(")")
            return value
        raise FormulaError(f"无法解析的 token: {kind} {text!r}")


def _balance_parentheses(text: str) -> str:
    """Tushare 公式描述里个别公式缺少结尾 ')'，这里补齐；多余右括号视为错误。"""
    opens = text.count("(")
    closes = text.count(")")
    if closes > opens:
        raise FormulaError(f"右括号多于左括号: {text}")
    return text + ")" * (opens - closes)


def evaluate_formula(formula: str, panel: Any) -> pd.DataFrame:
    """解析并求值一条 Alpha101 公式，返回与 panel 同形状的 float DataFrame。"""
    formula = _balance_parentheses(clean_formula(formula))
    engine = _Engine(panel)
    parser = _Parser(tokenize(formula), engine)
    value = parser.parse()
    out = engine._df(value).astype(float)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out.reindex(index=engine.dates, columns=engine.codes)


def evaluate_all(panel: Any, formulas: dict[str, str] | None = None) -> dict[str, pd.DataFrame]:
    formulas = formulas or load_formulas()
    return {name: evaluate_formula(f, panel) for name, f in formulas.items()}

