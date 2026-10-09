# -*- coding: utf-8 -*-
"""US 行情源（K-STOCK-044 M1）：新浪 getDailyK 拉取 + 拆股前复权 + CSV 缓存 + UsMarket。

数据流：
  新浪 US_MinKService.getDailyK（akshare stock_us_daily 的端点已失效，本模块直连活端点）
    → 原始日线 {d,o,h,l,c,v}（未复权，2020-08-31 AAPL 499→129 实测断裂确认）
    → 拆股断裂启发式前复权（整齐比例候选 + 修正后当日涨跌幅 ≤20% 双约束）
    → CSV 缓存 quant_backtest/data/us/{SYM}.csv（列 date/open/high/low/close/volume/pre_close，
      date=YYYY-MM-DD，pre_close=前日 close）
  UsMarket（与 ParquetMarket 同鸭子契约：trading_days() + bar()）只读缓存不在线拉——
  引擎 settle/mark 与 ashare 湖同构离线跑，缓存更新走独立 refresh CLI（automation 每日一次）。

设计要点：
  · 代码映射：青萍 gb_AAPL → 新浪 symbol aapl → 缓存 AAPL.csv；引擎内 code 保留 gb_AAPL 原样（审计可溯）
  · 日历基准 SPY：trading_days() 只取日期序列，与价格断裂无关，无需复权
  · 前复权理由：模拟盘买入都在近期，前复权近期价=真实价、pct 连续、止损与 max_dd 不被拆股假触发
  · CRYPTO 不在此范围（无日历语义，独立再议）
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # E:\老田的AI助手
DEFAULT_US_DIR = ROOT / "quant_backtest" / "data" / "us"
CAL_SYMBOL = "SPY"  # 日历基准（宽基 ETF，流动性最好、不会退市）

_SINA_URL = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php"
             "/abc/US_MinKService.getDailyK?symbol={sym}")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
_TIMEOUT = 30

# 拆股整齐比例候选（美股常见）；判定=修正后当日涨跌幅须在 ±20% 内（美股无涨跌停，
# ±20% 已极宽；真实暴跌 cur*4/prev 不可能落回 ±20%，不会误判）
_RATIO_CANDIDATES = (2, 3, 4, 5, 7, 10, 20)


def sina_symbol(code: str) -> str:
    """引擎 code（gb_AAPL）→ 新浪 symbol（aapl）；spy/裸码原样小写。"""
    c = code.strip().lower()
    return c[3:] if c.startswith("gb_") else c


def _et_date_hhmm(ts_iso: str) -> tuple[str, str]:
    """ISO 时刻（本机=北京时区口径）→ 美东 (YYYYMMDD, HHMM)。

    zoneinfo 可用走真时区（自动处理 DST）；Windows 缺 tzdata 时降级固定 -12h
    （夏令时口径；冬令时应 -13h，automation 跑批时点远离 16:00 收盘界，1 小时
    误差无实际影响——降级分支注释显性化）。"""
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        dt = (_dt.fromisoformat(str(ts_iso)[:19]).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
              .astimezone(ZoneInfo("America/New_York")))
        return dt.strftime("%Y%m%d"), dt.strftime("%H%M")
    except Exception:  # noqa: BLE001  ZoneInfo 找不到 tzdata 等
        dt = datetime.fromisoformat(str(ts_iso)[:19]) - timedelta(hours=12)
        return dt.strftime("%Y%m%d"), dt.strftime("%H%M")


def _fetch_sina(sym: str) -> list[dict]:
    """拉新浪原始日线（未复权）。返回 [{date,open,high,low,close,volume}]，按日期升序。"""
    r = requests.get(_SINA_URL.format(sym=sym), headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    body = r.text
    try:
        s, e = body.index("(") + 1, body.rindex(")")
        rows = json.loads(body[s:e])
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"新浪 {sym} 返回非预期（反爬或端点变更）: {body[:80]!r}") from exc
    out = []
    for row in rows:
        try:
            out.append({"date": str(row["d"]),
                        "open": float(row["o"]), "high": float(row["h"]),
                        "low": float(row["l"]), "close": float(row["c"]),
                        "volume": float(row["v"])})
        except (KeyError, TypeError, ValueError):
            continue  # 单行脏数据跳过，不断链
    if not out:
        raise RuntimeError(f"新浪 {sym} 无有效日线行")
    return out


def _split_ratio(prev_close: float, cur_close: float) -> int:
    """断裂日拆股比例推断：1=非拆股（普通波动）；n=回溯把历史价 ÷n。"""
    if prev_close <= 0 or cur_close <= 0:
        return 1
    for r in _RATIO_CANDIDATES:
        if abs(cur_close * r / prev_close - 1) <= 0.20:
            return r
    return 1


def split_adjust(rows: list[dict]) -> list[dict]:
    """拆股前复权（就地滚动修正历史价）：断裂日把之前所有行价格 ÷ratio，多次拆股天然支持。"""
    for i in range(1, len(rows)):
        r = _split_ratio(rows[i - 1]["close"], rows[i]["close"])
        if r > 1:
            for j in range(i):
                for k in ("open", "high", "low", "close"):
                    rows[j][k] = rows[j][k] / r
    return rows


def cache_path(symbol: str, us_dir: Path | str | None = None) -> Path:
    d = Path(us_dir) if us_dir else DEFAULT_US_DIR
    return d / f"{sina_symbol(symbol).upper()}.csv"


def refresh(symbol: str, us_dir: Path | str | None = None) -> Path:
    """拉取→复权→写 CSV（含 pre_close=前日 close）。返回缓存路径。"""
    sym = sina_symbol(symbol)
    rows = split_adjust(_fetch_sina(sym))
    out = cache_path(sym, us_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume", "pre_close"])
        prev_close = ""
        for row in rows:
            w.writerow([row["date"], row["open"], row["high"], row["low"],
                        row["close"], row["volume"], prev_close])
            prev_close = row["close"]
    tmp.replace(out)  # 原子写（同 hires.json 口径）
    return out


class UsMarket:
    """US 行情接口（与 ParquetMarket 同鸭子契约），只读 CSV 缓存。系统 Python311 环境非必需（纯 stdlib）。"""

    def __init__(self, us_dir: Path | str | None = None, cal_symbol: str = CAL_SYMBOL):
        self.us_dir = Path(us_dir) if us_dir else DEFAULT_US_DIR
        self.cal_symbol = cal_symbol
        self._days: list[str] | None = None

    def _file(self, code: str) -> Path:
        return self.us_dir / f"{sina_symbol(code).upper()}.csv"

    def _read(self, code: str) -> list[dict] | None:
        f = self._file(code)
        if not f.exists():
            return None
        with open(f, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def trading_days(self) -> list[str]:
        """交易日历（YYYYMMDD 字符串，引擎口径），取自日历基准票 SPY。"""
        if self._days is None:
            rows = self._read(self.cal_symbol)
            if not rows:
                raise FileNotFoundError(f"日历基准票缺失: {self._file(self.cal_symbol)}"
                                        f"（先跑 python paper/us_market.py refresh spy）")
            self._days = [r["date"].replace("-", "") for r in rows]
        return self._days

    def bar(self, code: str, trade_date: str) -> dict | None:
        """单日 bar（引擎契约：trade_date/open/high/low/close/pre_close；YYYYMMDD 口径）。"""
        rows = self._read(code)
        if rows is None:
            return None
        want = str(trade_date)
        for r in rows:
            if r["date"].replace("-", "") == want:
                return {"trade_date": want,
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "pre_close": float(r["pre_close"]) if r["pre_close"] else float(r["close"])}
        return None

    # ---- 引擎契约的其余方法（与 ParquetMarket 同形；CSV 顺序即日期升序） ----

    def last_bar(self, code: str) -> dict | None:
        rows = self._read(code)
        if not rows:
            return None
        r = rows[-1]
        return {"trade_date": r["date"].replace("-", ""),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "pre_close": float(r["pre_close"]) if r["pre_close"] else float(r["close"])}

    def last_bar_before(self, code: str, trade_date: str) -> dict | None:
        """≤ 给定日的最后一根 bar（AsOfMarket 时点视角用）。"""
        rows = self._read(code)
        if not rows:
            return None
        want = str(trade_date)
        hit = [r for r in rows if r["date"].replace("-", "") <= want]
        if not hit:
            return None
        r = hit[-1]
        return {"trade_date": r["date"].replace("-", ""),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "pre_close": float(r["pre_close"]) if r["pre_close"] else float(r["close"])}

    def first_bar_on_or_after(self, code: str, trade_date: str) -> dict | None:
        """≥ 给定日的第一根 bar（settle 纠偏用：plan_date 无行情时顺延）。"""
        rows = self._read(code)
        if not rows:
            return None
        want = str(trade_date)
        for r in rows:
            if r["date"].replace("-", "") >= want:
                return {"trade_date": r["date"].replace("-", ""),
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "pre_close": float(r["pre_close"]) if r["pre_close"] else float(r["close"])}
        return None

    def next_trading_day(self, ts_iso: str) -> str | None:
        """决策时刻之后第一个（可能的）美股交易日（09:30 ET 界，与 A 股 _next_td_impl 同构）。

        ⚠️ 两处不能直接复用 _next_td_impl：① 其日历入参是本地口径，US 须先转美东；
        ② 界是 A 股 09:30——恰是美股 09:30 开盘，同界可直接沿用。
        口径（防止偷价）：信号先于开盘价产生才允许当日撮合——
          · 转美东后 hhmm < 09:30（盘前，开盘未发生）→ 当日开盘价可成交，plan=当日
          · 盘中/盘后（开盘价已是过去时）→ plan=次一交易日（湖内或周末规则外推）
        节假日误外推无害：该日 bar 永远缺失 → settle 走 first_bar_on_or_after 顺延。
        实例：青萍 ts=北京 23:37（=ET 11:37 盘中）→ plan=次日，不偷当日开盘价。
        """
        days = self.trading_days()
        day, hhmm = _et_date_hhmm(ts_iso)
        if day in days and hhmm < "0930":
            return day                      # 美东盘前：当日开盘价尚未产生，可成交
        for d in days:
            if d > day:
                return d
        cur = datetime.strptime(day, "%Y%m%d")   # 湖尾外推（周末规则，上限防死循环）
        if hhmm >= "0930":
            cur += timedelta(days=1)
        for _ in range(10):
            if cur.weekday() < 5:
                return cur.strftime("%Y%m%d")
            cur += timedelta(days=1)
        return None


def main(argv: list[str], us_dir: Path | str | None = None) -> int:
    """CLI：python paper/us_market.py refresh gb_aapl gb_msft spy（增量全量同价——端点返回全史）。"""
    if len(argv) >= 2 and argv[0] == "refresh":
        for sym in argv[1:]:
            p = refresh(sym, us_dir)
            n = len(list(csv.DictReader(open(p, encoding="utf-8"))))
            print(json.dumps({"ok": True, "symbol": sina_symbol(sym),
                              "rows": n, "path": str(p)}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
