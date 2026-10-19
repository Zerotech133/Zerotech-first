# -*- coding: utf-8 -*-
"""HK 行情源 + HK 影子账本跑批（镜像 US 先例 K-STOCK-044，HK 灰位期 · 影子账本纪律）。

数据流（灰位期 P-1 倾向：主源 zhice-db 导出湖，本地到本地零联网）：
  zhice-db v2_bar_daily market='HK'（经 quant_backtest/export_us_hk.py --market HK 导出）
    → quant_backtest/data/hk/{code5}.parquet（ts_code=00700 5 位，2026-10 实存 3400+ 只）
    → refresh() 本地转 CSV 缓存 quant_backtest/data/hk_paper/{code5}.csv
      （列 date/open/high/low/close/volume/pre_close，date=YYYY-MM-DD，
        pre_close=前日 close——湖内 pre_close 列实量 NaN，转换时补齐，与 US 缓存同构）
  HkMarket（与 ParquetMarket/UsMarket 同鸭子契约：6 方法）只读缓存不在线拉——
  引擎 settle/mark 与 US 同构离线跑，缓存更新走本模块 refresh CLI（automation 每日一次）。
  兜底源 TDX ts_hk_daily（xtcloud 真源 2023-07 起）：灰位期未接，TODO 挂账——
  chanlun_data.py:175-188 已有 HK TDX 兜底先例，实装期照抄即可。

HK 口径四项（G3 拍板倾向落地，2026-10-19）：
  · T+0 不做冻结：照 US 挂账先例（P-3）——引擎 available_qty 的 T+1 冻结对 HK
    依然生效（仅多锁一天可卖，保守无害）。TODO(引擎工单)：available_qty 市场开关，
    HK 现货实为 T+0 交易（T+2 交收）。
  · LOT 灰位期放开（P-4）：HK 每手股数各异（腾讯 100/汇丰 400），引擎 LOT=100
    硬卡必错。本模块 apply_hk_engine_waivers() 在跑批进程内运行时豁免
    （engine.LOT=1），不动 engine.py 文件——5 处 A 股硬编码是引擎工单另行拍板。
  · 印花税（P-5 HKD 原币）：HK 现行印花税双边 0.1%（2023-11-17 起由 0.13% 下调，
    2026-10 查证仍 0.1%），常量 STAMP_DUTY。走 PaperEngine config 参数零侵入；
    注：引擎 _fee_buy 无印花税参数，买入侧印花税缺失 → 灰位期费用略低估，挂账
    TODO(引擎工单)：_fee_buy 增加 stamp_tax_buy 参数。
  · 涨跌停不做 ±10% 硬拦：HK 无涨跌停制度。apply_hk_engine_waivers() 同时包装
    engine.limit_ratio（.HK/5 位数字码 → 1.0，照 gb_ 前缀 :189 豁免先例）。
  · 代码规范：5 位数字 00700.HK 形态（与 A 股 6 位 600519.SH 区分）；引擎内
    code 保留 00700.HK 原样（审计可溯，同 gb_AAPL 先例）；缓存文件名取 5 位数字。
    引擎 _norm_stock 对非 A 股形态原样放行（engine.py:174-177），零改动直通。
  · 日历基准票（P-2 倾向）：2800.HK 盈富基金（类 SPY 定位，宽基 ETF 不退市）。

跑批（镜像 cli.py _us_pass/cmd_settle/cmd_mark 的 US 段，独立进程不改 cli.py）：
  python k_stock/paper/hk_market.py refresh 02800 00700    # 湖→CSV 缓存（日历票必刷）
  python k_stock/paper/hk_market.py run [--dry-run]        # hk_% 账本 settle → mark
  python k_stock/paper/hk_market.py status                 # 账户/持仓/盈亏（HKD 原币）

账本落点：共享 k_stock/data/paper.db 的 hk_% 账户行（与 us_qingping 同库多账户同构）；
portal_status.hk_status / /api/paper_status hk 块本轮不做（8091 重启报批后接线即点亮），
数据形态已按 us_status 读取口径落库（equity_marks/positions_snapshot 同构）。

TODO(引擎工单拍板后回填，本文件豁免即可下线)：
  1. limit_ratio 加 hk_/.HK 前缀 → 1.0（照 gb_ :189 一行先例）
  2. LOT 经 config 参数化（HK 每手表灰位期放开，实装后补每手股数元数据）
  3. _fee_buy 印花税参数（HK 双边 0.1%）
  4. available_qty T+0 市场开关（HK 挂账保守起步，与 US 同先例）
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # 仓库根
DEFAULT_HK_EXPORT_DIR = ROOT / "quant_backtest" / "data" / "hk"        # zhice-db 导出湖（只读）
DEFAULT_HK_DIR = ROOT / "quant_backtest" / "data" / "hk_paper"         # CSV 缓存（本模块独占落点）
DEFAULT_DB = HERE.parent / "data" / "paper.db"                          # 共享账本（hk_% 账户行独占）
CAL_SYMBOL = "02800"  # 日历基准票：2800.HK 盈富基金（P-2 拍板倾向，类 SPY 语义）

# HK 现行证券印花税：双边 0.1%（2023-11-17 起生效下调，2026-10 复核仍为 0.1%）。
# 依据：香港《印花税条例》(Cap. 117) 附表 8，2023 年《2023 年印花税（修订）（证券转让）条例》。
STAMP_DUTY = 0.001
# HK 券商佣金参考口径（灰位期默认，可经 config 覆盖）：佣金 0.03%、最低 3 HKD
# （主流互联网券商档位；实装期按老田选定券商修正）。
COMMISSION_RATE = 0.0003
MIN_COMMISSION = 3.0

HK_ACCOUNT = "hk_qingping"        # HK 影子账本（HKD 原币，与 main/us 不混算）
HK_STRATEGY = "qingping_hk"       # 信号源通道标签（灰位期预留，信号适配器另行工单）

_HK_CODE_RE = re.compile(r"^(\d{1,5})(?:\.HK)?$", re.IGNORECASE)


def normalize_hk_code(raw: str) -> str:
    """任意 HK 码形态 → 引擎规范码 00700.HK（5 位 zfill；与 A 股 6 位形态区分）。

    老票 1-4 位亦常见（如 "1" 长和、"700" 腾讯），统一 zfill(5)。"""
    s = str(raw).strip()
    m = _HK_CODE_RE.match(s.upper())
    if not m:
        return s
    return m.group(1).zfill(5) + ".HK"


def file_code(code: str) -> str:
    """引擎码 00700.HK → 缓存/湖文件码 00700（5 位数字）。"""
    return str(code).split(".")[0].zfill(5)


def is_hk_code(code: str) -> bool:
    """HK 码判定：.HK 后缀 或 1-5 位纯数字（A 股 6 位带后缀不会误伤）。"""
    s = str(code).strip().upper()
    if s.endswith(".HK"):
        return True
    return bool(re.fullmatch(r"\d{1,5}", s))


def _hkt_date_hhmm(ts_iso: str) -> tuple[str, str]:
    """ISO 时刻（本机=北京时区口径）→ 香港时区 (YYYYMMDD, HHMM)。

    HKT=UTC+8 无夏令时，与本机（Asia/Shanghai）同刻——真时区路径仅作显性化；
    zoneinfo 不可用时降级本机口径（+0h，误差恒为 0，与 US 的 -12h 降级不同）。"""
    try:
        from zoneinfo import ZoneInfo
        dt = (datetime.fromisoformat(str(ts_iso)[:19]).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
              .astimezone(ZoneInfo("Asia/Hong_Kong")))
        return dt.strftime("%Y%m%d"), dt.strftime("%H%M")
    except Exception:  # noqa: BLE001  ZoneInfo 找不到 tzdata 等
        dt = datetime.fromisoformat(str(ts_iso)[:19])
        return dt.strftime("%Y%m%d"), dt.strftime("%H%M")


def export_parquet_path(code: str, export_dir: Path | str | None = None) -> Path:
    return Path(export_dir or DEFAULT_HK_EXPORT_DIR) / f"{file_code(code)}.parquet"


def refresh_from_export(code: str, hk_dir: Path | str | None = None,
                        export_dir: Path | str | None = None) -> Path:
    """zhice-db 导出湖 parquet → CSV 缓存（本地转换，零联网）。

    pre_close 湖内实量 NaN → 以前日 close 补齐（与 US 缓存同构；首行以前日收盘
    缺失时回填当日 open，保证非空——引擎 settle 无涨跌停拦截场景不消费该字段
    做拦截，仅作 bar 契约完整性）。原子写（同 us_market.refresh 口径）。"""
    src = export_parquet_path(code, export_dir)
    if not src.exists():
        raise FileNotFoundError(f"zhice-db 导出湖缺失: {src}"
                                f"（先跑 python quant_backtest/export_us_hk.py --market HK）")
    import pandas as pd  # 惰性：系统 Python311 环境（与 ParquetMarket 同口径）
    df = pd.read_parquet(src).sort_values("trade_date")
    rows = []
    prev_close = ""
    for _, r in df.iterrows():
        close = float(r["close"])
        pre = float(r["pre_close"]) if pd.notna(r["pre_close"]) else (
            float(prev_close) if prev_close != "" else float(r["open"]))
        d = str(r["trade_date"])
        rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                     "open": float(r["open"]), "high": float(r["high"]),
                     "low": float(r["low"]), "close": close,
                     "volume": float(r["volume"]), "pre_close": pre})
        prev_close = close
    if not rows:
        raise RuntimeError(f"导出湖 {src} 无有效行")
    out = Path(hk_dir or DEFAULT_HK_DIR) / f"{file_code(code)}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume", "pre_close"])
        for row in rows:
            w.writerow([row["date"], row["open"], row["high"], row["low"],
                        row["close"], row["volume"], row["pre_close"]])
    tmp.replace(out)  # 原子写（同 hires.json / us_market 口径）
    return out


class HkMarket:
    """HK 行情接口（与 ParquetMarket/UsMarket 同鸭子契约 6 方法），只读 CSV 缓存。
    纯 stdlib，系统 Python311 环境非必需（与 UsMarket 同口径）。"""

    def __init__(self, hk_dir: Path | str | None = None, cal_symbol: str = CAL_SYMBOL):
        self.hk_dir = Path(hk_dir) if hk_dir else DEFAULT_HK_DIR
        self.cal_symbol = cal_symbol
        self._days: list[str] | None = None

    def _file(self, code: str) -> Path:
        return self.hk_dir / f"{file_code(code)}.csv"

    def _read(self, code: str) -> list[dict] | None:
        f = self._file(code)
        if not f.exists():
            return None
        with open(f, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    @staticmethod
    def _bar_of(r: dict) -> dict:
        return {"trade_date": str(r["date"]).replace("-", ""),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "pre_close": float(r["pre_close"]) if r["pre_close"] else float(r["close"])}

    def trading_days(self) -> list[str]:
        """交易日历（YYYYMMDD 引擎口径），取自日历基准票 2800.HK 盈富基金。"""
        if self._days is None:
            rows = self._read(self.cal_symbol)
            if not rows:
                raise FileNotFoundError(f"日历基准票缺失: {self._file(self.cal_symbol)}"
                                        f"（先跑 python paper/hk_market.py refresh 02800）")
            self._days = [str(r["date"]).replace("-", "") for r in rows]
        return self._days

    def bar(self, code: str, trade_date: str) -> dict | None:
        """单日 bar（引擎契约：trade_date/open/high/low/close/pre_close；YYYYMMDD 口径）。"""
        rows = self._read(code)
        if rows is None:
            return None
        want = str(trade_date)
        for r in rows:
            if str(r["date"]).replace("-", "") == want:
                return self._bar_of(r)
        return None

    def last_bar(self, code: str) -> dict | None:
        rows = self._read(code)
        if not rows:
            return None
        return self._bar_of(rows[-1])

    def last_bar_before(self, code: str, trade_date: str) -> dict | None:
        """≤ 给定日的最后一根 bar（AsOfMarket 时点视角用）。"""
        rows = self._read(code)
        if not rows:
            return None
        want = str(trade_date)
        hit = [r for r in rows if str(r["date"]).replace("-", "") <= want]
        if not hit:
            return None
        return self._bar_of(hit[-1])

    def first_bar_on_or_after(self, code: str, trade_date: str) -> dict | None:
        """≥ 给定日的第一根 bar（settle 纠偏用：plan_date 无行情时顺延）。"""
        rows = self._read(code)
        if not rows:
            return None
        want = str(trade_date)
        for r in rows:
            if str(r["date"]).replace("-", "") >= want:
                return self._bar_of(r)
        return None

    def next_trading_day(self, ts_iso: str) -> str | None:
        """决策时刻之后第一个（可能的）港股交易日（09:30 HKT 界，与 A 股/US 同构）。

        口径（防止偷价，同 us_market.next_trading_day）：信号先于开盘价产生才允许
        当日撮合——盘前（hhmm<0930 HKT）→ 当日；盘中/盘后 → 次一交易日（湖内或
        周末规则外推，节假日误外推无害：该日 bar 永远缺失 → settle 走
        first_bar_on_or_after 顺延）。"""
        days = self.trading_days()
        day, hhmm = _hkt_date_hhmm(ts_iso)
        if day in days and hhmm < "0930":
            return day                      # HKT 盘前：当日开盘价尚未产生，可成交
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


# ---------------- 引擎运行时豁免（灰位期，进程内生效，不改 engine.py 文件） ----------------

def apply_hk_engine_waivers() -> None:
    """HK 口径运行时豁免（灰位期落地，进程内生效；引擎工单拍板后本函数下线）：

    · engine.LOT = 1：HK 每手股数各异，LOT=100 硬卡必错（P-4 灰位期放开）。
      本跑批进程只跑 hk_% 账本，不影响 main/us 账本进程。
    · engine.limit_ratio 包装：.HK / 4-5 位数字码 → 1.0（HK 无涨跌停，
      照 engine.py:189 gb_ 前缀豁免先例）；A 股/US 码转发原实现零影响。
    仅可对 k_stock/paper/engine.py 模块调用（import 同目录模块）。"""
    import engine as paper_engine  # noqa: PLC0415  同目录（HERE 已在 sys.path）
    paper_engine.LOT = 1
    orig_limit_ratio = paper_engine.limit_ratio

    def _hk_aware_limit_ratio(code: str) -> float:
        if is_hk_code(code):
            return 1.0   # HK 无涨跌停制度，±10% 拦截是误杀源（G3 §3.3）
        return orig_limit_ratio(code)

    paper_engine.limit_ratio = _hk_aware_limit_ratio


# ---------------- HK 影子账本通道（镜像 cli.py _us_pass，独立进程不改 cli.py） ----------------

def hk_accounts(db_path: Path | str) -> list[str]:
    """paper.db 中 hk_% 账本清单（升序；库不存在返回空——灰态不算错，同 US 先例）。"""
    import sqlite3
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT account_id FROM account WHERE account_id LIKE 'hk_%'").fetchall()
    finally:
        con.close()
    return sorted(r[0] for r in rows)


def hk_symbols_in_db(db_path: Path | str) -> list[str]:
    """paper.db 中 HK 敞口标的（pending 单 + 持仓，hk_% 账本，5 位码去重升序）。

    settle 前按需 refresh 用：只刷有真实敞口的票 + 日历基准票，不盲目全量。"""
    import sqlite3
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT stock FROM orders WHERE account_id LIKE 'hk_%' AND status='pending' "
            "UNION "
            "SELECT stock FROM positions WHERE account_id LIKE 'hk_%' AND qty>0"
        ).fetchall()
    finally:
        con.close()
    return sorted({file_code(r[0]) for r in rows if is_hk_code(str(r[0]))})


def hk_pass(db_path: Path | str | None = None, do_refresh: bool = True) -> dict | None:
    """HK 独立账本通道（镜像 cli.py _us_pass）：存在 hk_% 账本才跑。

    settle 前自动 refresh（日历基准票 + 敞口票，湖→CSV 本地转换）；单票失败降级
    记录不炸 settle。无 hk_% 账本返回 None（灰态）。"""
    from engine import PaperEngine  # noqa: PLC0415  同目录
    db = Path(db_path) if db_path else DEFAULT_DB
    aids = hk_accounts(db)
    if not aids:
        return None
    refreshed, failed = [], []
    if do_refresh:
        try:
            refresh_from_export(CAL_SYMBOL)
            refreshed.append(CAL_SYMBOL)
        except Exception as ex:  # noqa: BLE001  日历失败仍可 settle（湖内已有 bar 照常成交）
            failed.append(f"{CAL_SYMBOL}: {ex}")
        for sym in hk_symbols_in_db(db):
            try:
                refresh_from_export(sym)
                refreshed.append(sym)
            except Exception as ex:  # noqa: BLE001
                failed.append(f"{sym}: {ex}")
    apply_hk_engine_waivers()
    return {"accounts": aids,
            "engines": [(aid, PaperEngine(db, HkMarket(), account=aid)) for aid in aids],
            "refreshed": refreshed, "refresh_fail": failed}


def hk_default_config() -> dict:
    """HK 费率 config（PaperEngine cfg 覆盖 _DEFAULTS，零侵入）。

    HKD 原币口径（P-5）：initial_cash 100 万 HKD；佣金 0.03%/最低 3 HKD；
    卖出印花税 0.1%（STAMP_DUTY，2023-11-17 起现行）。买入侧印花税引擎无参数，
    灰位期费用略低估——TODO(引擎工单) _fee_buy 增 stamp_tax_buy。"""
    return {"initial_cash": 1_000_000.0,
            "commission_rate": COMMISSION_RATE,
            "min_commission": MIN_COMMISSION,
            "stamp_tax": STAMP_DUTY}


# ---------------- CLI ----------------

def _print_eq(e) -> None:
    eq = e.equity()
    v = e.verify()
    print(f"现金 {eq['cash']:>12,.2f} HKD | 持仓市值 {eq['market_value']:>12,.2f} "
          f"| 总资产 {eq['total']:>12,.2f} | 仓位 {eq['pos_pct']:.1%} | "
          f"对账 {'OK' if v['ok'] else 'FAIL'}")


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(HERE))
    if len(argv) >= 2 and argv[0] == "refresh":
        for sym in argv[1:]:
            p = refresh_from_export(normalize_hk_code(sym))
            n = len(list(csv.DictReader(open(p, encoding="utf-8"))))
            print(json.dumps({"ok": True, "symbol": file_code(sym),
                              "rows": n, "path": str(p)}, ensure_ascii=False))
        return 0
    if argv and argv[0] == "init":
        """开立 HK 影子账本（hk_qingping，HKD 原币；幂等——已存在则原样返回）。"""
        from engine import PaperEngine  # noqa: PLC0415  同目录
        apply_hk_engine_waivers()
        eng = PaperEngine(DEFAULT_DB, HkMarket(), config=hk_default_config(),
                          account=HK_ACCOUNT)
        acc = eng.ensure_account(HK_ACCOUNT)
        print(json.dumps({"ok": True, "account_id": HK_ACCOUNT, "cash": acc["cash"],
                          "currency": "HKD", "db": str(DEFAULT_DB)}, ensure_ascii=False))
        return 0
    if argv and argv[0] == "run":
        dry_run = "--dry-run" in argv[1:]
        hk = hk_pass(do_refresh=not dry_run)
        if not hk:
            print(json.dumps({"ok": True, "note": "无 hk_% 账本（灰态，同 US 先例 ok=True）"},
                             ensure_ascii=False))
            return 0
        for aid, eng in hk["engines"]:
            try:
                r = eng.settle(dry_run=dry_run)
            except Exception as ex:  # noqa: BLE001
                print(f"HK 账本 {aid} settle 失败（不阻塞主链）: {ex}")
                continue
            print(f"HK 账本 {aid} 撮合: 成交 {len(r['filled'])} / 搁置 {len(r['rejected'])}"
                  f"（refresh {len(hk['refreshed'])} 票"
                  f"{'，失败 ' + str(len(hk['refresh_fail'])) if hk['refresh_fail'] else ''}）")
            for f in r["filled"]:
                print(f"  #{f.get('id', '-')} {f['stock']} {f['side']} {f['qty']} 股 "
                      f"@ {f['price']}（费 {f['fee']:.2f}）")
            for x in r["rejected"]:
                print(f"  #{x['id']} {x['stock']}: {x['why']}")
            if not dry_run:
                _print_eq(eng)
                m = eng.mark()
                print(f"快照 {m['date']}: 总资产 {m['total']:,.2f} HKD "
                      f"仓位 {m['pos_pct']:.1%} 待撮合 {m['pending']} "
                      f"对账 {'OK' if m['verify_ok'] else 'FAIL'}")
        return 0
    if argv and argv[0] == "status":
        db = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
        hk = hk_pass(db, do_refresh=False)
        if not hk:
            print("（无 hk_% 账本，灰态）")
            return 0
        for aid, eng in hk["engines"]:
            print(f"== {aid} ==")
            _print_eq(eng)
            for p in eng.pending(aid):
                print(f"  #{p['id']} [{p['source']}] {p['side']} {p['stock']} {p['qty']} 股 "
                      f"@ {p['plan_date']} 开盘 | {p['reason']}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
