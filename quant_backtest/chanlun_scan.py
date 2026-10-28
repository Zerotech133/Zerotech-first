# -*- coding: utf-8 -*-
"""缠论全A股扫描 → 预警池 → 模拟交易（影子观察：不推送、不拦截）。

数据: data/ashare/*.parquet（每日 23:35 计划任务 AI量化RPS共振 经
rps_resonance 增量同步，tushare daily 不复权口径）。扫描输入=截至最后一
根收盘的 bars，信号「检出日」= 最后一根 K 线日期，可成交价 = 检出日收盘
——逐日扫描天然含确认滞后，无未来函数。

模拟交易: 检出日收盘入场，持有 HOLD_DAYS 个交易日按当日收盘离场，或期间
同票出现反向类别信号（按其检出日收盘）提前离场；方向收益口径与
chanlun_backtest._dir_ret 一致（买点涨为胜 / 卖点跌为胜，卖点为方向
统计非做空损益）。

回填: --backfill 对窗口内历史信号做截短重放（移植 eval_samples 口径）
找最早可检出时点 lag，检测窗末根收盘为可成交价；重放不中弃掉。

侧面标签（全类型入池，各自独立积累战绩）: mbei(P6-a MACD)/bei(P5 盘整)/
bei2(P6-b 同向腿)/vv(P8-a 量能)/tbe(P6-c 趋势背驰) 取 True 子集，
reg(P8-b 大盘) 取顺风子集（chanlun_flow.aligned）；mt(P7) 全市场无 60m
数据恒 None 不判。复评门槛与样本面板一致：n≥10 且显著优于全集，经用户
确认才谈拦截。

用法:
  python chanlun_scan.py --backfill   # 一次性初始回填（重放找可成交价）
  python chanlun_scan.py --scan       # 每日 15:35 计划任务入口（CN 默认）
  python chanlun_scan.py --scan --market US   # 美股扫描（独立池链 _us）
  python chanlun_scan.py --scan --market HK   # 港股扫描（独立池链 _hk）
"""

import argparse
import logging
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── 市场注册表（多市场）：数据目录与产出文件链按市场隔离；
#    CN 路径与历史完全一致（行为零变化），US/HK 全套独立文件链。
#    US/HK 数据由 export_us_hk.py 从 zhice-db（PG v2_bar_daily）导出
#    至 data/us|hk/*.parquet，schema 与 ashare 对齐；fresh 检查走
#    zhice latest_dates 比对（无 tushare 交易日历/TDX 兜底）。
MKT = {
    "CN": {"dir": "ashare", "sfx": "", "restore_code": True},
    "US": {"dir": "us", "sfx": "_us", "restore_code": False},
    "HK": {"dir": "hk", "sfx": "_hk", "restore_code": False},
}
_CUR_MKT = "CN"

ASHARE = os.path.join(HERE, "data", MKT["CN"]["dir"])
POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
PAPER = os.path.join(HERE, "data", "chanlun_paper.json")
POOL_EVAL = os.path.join(HERE, "data", "chanlun_pool_eval_cache.json")
SHADOW_1SELL_TBE = os.path.join(HERE, "data", "chanlun_shadow_1sell_tbe.jsonl")
PUSHED_1SELL_TBE = os.path.join(HERE, "data", "chanlun_pushed_1sell_tbe.json")
CODE_RESTORE = True  # CN 文件名 600519_SH.parquet → 600519.SH；US/HK 原样
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))


def set_market(market):
    """切换市场上下文：重绑数据目录与产出文件链（US/HK 独立池/纸面/
    影子/推送文件，与 CN 产线互不影响）。必须在 main() 最早处调用。"""
    global _CUR_MKT, ASHARE, POOL, PAPER, POOL_EVAL
    global SHADOW_1SELL_TBE, PUSHED_1SELL_TBE, CODE_RESTORE
    cfg = MKT[market]
    _CUR_MKT = market
    ASHARE = os.path.join(HERE, "data", cfg["dir"])
    sfx = cfg["sfx"]
    POOL = os.path.join(HERE, "data", "chanlun_pool%s.jsonl" % sfx)
    PAPER = os.path.join(HERE, "data", "chanlun_paper%s.json" % sfx)
    POOL_EVAL = os.path.join(HERE, "data", "chanlun_pool_eval_cache%s.json" % sfx)
    SHADOW_1SELL_TBE = os.path.join(
        HERE, "data", "chanlun_shadow_1sell_tbe%s.jsonl" % sfx
    )
    PUSHED_1SELL_TBE = os.path.join(
        HERE, "data", "chanlun_pushed_1sell_tbe%s.json" % sfx
    )
    CODE_RESTORE = cfg["restore_code"]


BACKFILL_DAYS = 120  # 初始回填窗口（交易日，按各股自身bar序列计）
MAX_LAG = 60  # 回填重放最大确认滞后（同 eval_samples）
HOLD_DAYS = 20  # 模拟交易持有交易日数
MIN_BARS = 120  # 少于此根数的次新股不参与（结构噪声大）
LIMIT_BARS = 500  # 每日扫描喂给 analyze 的 bar 数（对齐样本口径）
FRESH_LAG = 2  # 本地数据落后开市日数 ≥ 此值视为不同步
CLOSED_CAP = 5000  # 账本 closed 截留上限
SIDES = ("mbei", "bei", "bei2", "vv", "tbe")  # True 子集型侧面

# TDX 兜底（ashare 滞后时由 _check_fresh 填充：缺失交易日 → {ts_code: OHLCV}）
TDX_RESCUE = {}

# ── 后复权（W1-2 任务A 20261014）──────────────────────────
# 背景: data/ashare/*.parquet 为 tushare daily 不复权价，除权缺口产生
# 假分型污染笔/中枢/三类买卖点。开关: config.json chanlun.use_hfq
# （键缺失/读取失败 = false 保持旧行为；产线显式置 true）。
# 回退: 该键改 false 即一键回退，无需动代码。
# 判例: 归一基准 = 全序列最后一根因子 → 最新根价格 = 不复权真实价，
# 每日扫描检出信号的 price/entry_px 推送语义零漂移；历史根等比缩放，
# 与 tushare hfq 比值口径一致（跨除权收益可比）。
HFQ_FALLBACK = set()  # 降级票（因子缺失 → 退回不复权），scan 后落盘
_HFQ_CFG = None  # None=读 config.json；测试/进程内可显式覆盖
_ADJ_CACHE = None  # {ts_code: (td8 ndarray, factor ndarray)} 懒加载一次


def _use_hfq():
    """chanlun.use_hfq 开关（惰性读一次）。

    坑: 键缺失默认 False（旧行为）——老计划任务在 config 未升级的
    过渡期行为零变化；config.json 已显式写 true 才启用。"""
    global _HFQ_CFG
    if _HFQ_CFG is None:
        try:
            with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            _HFQ_CFG = bool((cfg.get("chanlun") or {}).get("use_hfq", False))
        except Exception:
            _HFQ_CFG = False
    return _HFQ_CFG


def _adj_map():
    """湖 ts_adj_factor 全表懒加载（adj_factor_sync 每日 18:10 增量入湖，
    后复权累积因子，历史值固定）。表未就绪/读取失败 → {}（全局降级）。

    坑: 全表 ~420 万行，只取 3 列转 per-code ndarray（内存 ~百MB 级），
    勿转 {code: {date: f}} 字典（Python 对象开销放大 5 倍以上）。"""
    global _ADJ_CACHE
    if _ADJ_CACHE is None:
        try:
            import tdx_read as tdxr

            if not tdxr.table_ready("ts_adj_factor"):
                _ADJ_CACHE = {}
            else:
                df = tdxr.read_range(
                    "ts_adj_factor", columns=["ts_code", "trade_date", "adj_factor"]
                )
                m = {}
                if df is not None and not df.empty:
                    g_all = df.assign(
                        _td=df["trade_date"].astype(str).str[:8],
                        _fa=pd.to_numeric(df["adj_factor"], errors="coerce"),
                    ).dropna(subset=["_fa"])
                    for code, g in g_all.groupby("ts_code", sort=False):
                        td = g["_td"].to_numpy()
                        fa = g["_fa"].to_numpy(dtype=float)
                        o = td.argsort()
                        m[code] = (td[o], fa[o])
                _ADJ_CACHE = m
        except Exception:
            _ADJ_CACHE = {}
    return _ADJ_CACHE


def _chain_ratio(prev_c, pc, inverse=False):
    """pre_close 链单步比率（K254 口径，出界守卫 0.05~20）。

    正向: r = c(t-1)/pre_close(t)；逆向（头部回补）: r = pre_close(t+1)/c(t)。
    坑: pre_close 缺失/非正 → r=1（继承前根因子，不修代错修——除权恰逢
    pc 缺失概率低，次日因子表同步后自愈）。"""
    if not prev_c or not pc or pc <= 0:
        return 1.0
    r = (prev_c / pc) if not inverse else (pc / prev_c)
    return r if 0.05 <= r <= 20.0 else 1.0


def _hfq_scale(bars, code):
    """bars（升序 [{dt,o,h,l,c,v,pc}]）就地后复权（最新根=1 基准）。

    因子源: 湖 ts_adj_factor；头部/尾部/内部无因子日以 pre_close 链接续
    （正向 f(t)=f(t-1)*c(t-1)/pc(t)，逆向 f(t)=f(t+1)*pc(t+1)/c(t)）。
    OHLC 与 pc 同乘 f/f_base（pc 复权后除权缺口消失 → _exdiv_between
    对已复权序列自然不触发，评估不再误剔跨除权样本）；v 不动。
    降级: 该票因子表整段缺失（fmap 无此码 / 全序列无命中 / 末根因子
    非正）→ 原样返回 False 并记 HFQ_FALLBACK；个别日期缺失走链接续
    不降级。返回 True=已复权。"""
    fmap = _adj_map().get(code)
    if not fmap or not bars:
        HFQ_FALLBACK.add(code)
        return False
    n = len(bars)
    ds, fs = fmap
    f = [None] * n
    for i, b in enumerate(bars):
        j = int(ds.searchsorted(b["dt"].replace("-", "")))
        if j < len(ds) and ds[j] == b["dt"].replace("-", ""):
            f[i] = float(fs[j])
    k0 = next((i for i, v in enumerate(f) if v is not None), None)
    if k0 is None:
        HFQ_FALLBACK.add(code)
        return False
    for i in range(k0 - 1, -1, -1):  # 头部回补（逆向链）
        f[i] = f[i + 1] * _chain_ratio(
            bars[i + 1].get("pc"), bars[i]["c"], inverse=True
        )
    for i in range(k0 + 1, n):  # 尾部/内部接续（正向链）
        if f[i] is None:
            f[i] = f[i - 1] * _chain_ratio(bars[i - 1]["c"], bars[i].get("pc"))
    base = f[-1]
    if not base or base <= 0:
        HFQ_FALLBACK.add(code)
        return False
    for i, b in enumerate(bars):
        k = f[i] / base
        for key in ("o", "h", "l", "c"):
            b[key] = round(b[key] * k, 6)
        if b.get("pc") is not None:
            b["pc"] = b["pc"] * k
    return True


from chanlun_signal import analyze  # noqa: E402
from chanlun_gray import enrich as _gray_enrich  # noqa: E402
from chanlun_backtest import _dir_ret, TYPES  # noqa: E402
from chanlun_flow import regime_at, aligned  # noqa: E402
from chanlun_composite import judge_all as _comp_judge_all, RULES as _COMP_RULES  # noqa: E402


# ── 基础工具 ──────────────────────────────────────────────
def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _td8(v):
    """trade_date（int/str 混存）→ 'YYYYMMDD'。"""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = s.replace("-", "")[:8]
    return s


def _td_dt(v):
    """trade_date → 'YYYY-MM-DD'（analyze 的 bar dt 口径）。"""
    s = _td8(v)
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return str(v)[:10]


def _bars_from_parquet(path, limit=0):
    """parquet → bars [{dt,o,h,l,c,v,pc}]；剔除停牌/无效行；升序去重；
    limit>0 只取最近 N 根；清洗后不足 MIN_BARS 返回 None（次新排除）。"""
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    need = {"trade_date", "open", "high", "low", "close", "volume"}
    if not need.issubset(df.columns):
        return None
    cols = ["trade_date", "open", "high", "low", "close", "volume"]
    d = df[cols].copy()
    if "pre_close" in df.columns:
        d["pre_close"] = df["pre_close"]
    else:
        d["pre_close"] = None
    d = d.dropna(subset=["open", "high", "low", "close"])
    d = d[(d["close"] > 0) & (d["volume"] > 0)]
    if d.empty:
        return None
    rows = {}
    for td, o, h, l, c, v, pc in zip(
        d["trade_date"],
        d["open"],
        d["high"],
        d["low"],
        d["close"],
        d["volume"],
        d["pre_close"],
    ):
        rows[_td_dt(td)] = (
            float(o),
            float(h),
            float(l),
            float(c),
            float(v),
            None if pd.isna(pc) else float(pc),
        )
    dts = sorted(rows)
    if limit > 0:
        dts = dts[-limit:]
    if len(dts) < MIN_BARS:
        return None
    out = []
    for dt in dts:
        o, h, l, c, v, pc = rows[dt]
        out.append({"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v, "pc": pc})
    # 后复权（仅 CN；US/HK 无因子源且文件名非 ts_code 形态）
    if _use_hfq() and _CUR_MKT == "CN":
        _hfq_scale(out, os.path.basename(path)[:-8].replace("_", "."))
    return out


def _code_path(code):
    return os.path.join(ASHARE, code.replace(".", "_") + ".parquet")


def _ashare_files():
    if not os.path.isdir(ASHARE):
        return []
    return sorted(f for f in os.listdir(ASHARE) if f.endswith(".parquet"))


def _names():
    """ts_code → 股票名（CN 复用 rps_resonance 的 30 天缓存；
    US/HK 名称底册未接 → 空，池内显示代码）。"""
    if _CUR_MKT != "CN":
        return {}
    try:
        import rps_resonance as rps

        return rps._names_safe()
    except Exception:
        return {}


def _idx_bars():
    """P8-b 大盘环境用上证日线（zhice-db）；失败返回 []，reg 置 None。
    US/HK 大盘基准（SPY/恒生）未接 → []，reg=None（P8 口径不判不拦截）。"""
    if _CUR_MKT != "CN":
        return []
    try:
        from chanlun_data import load_kline

        ri = load_kline("000001.SH", "1d", 500)
        return ri["bars"] if ri.get("ok") else []
    except Exception:
        return []


def _reg_at(dt, idx_bars):
    if not idx_bars:
        return None
    try:
        return regime_at("CN", dt, idx_bars)[0]
    except Exception:
        return None


# ── 存储（原子写 + SQLite 双写） ─────────────────────────
def _load_pool():
    rows = []
    if os.path.exists(POOL):
        with open(POOL, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def _pool_keys(rows):
    return {(r.get("code"), r.get("type"), r.get("dt")) for r in rows}


def _write_pool(rows):
    _write_jsonl(POOL, rows)


def _write_jsonl(path, rows):
    """JSONL 原子写（tempfile + os.replace）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _load_paper():
    if os.path.exists(PAPER):
        try:
            with open(PAPER, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "chanlun_scan.py", exc
            )
    return {"updated_at": None, "open": [], "closed": []}


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ── 池行 / 仓位构造 ───────────────────────────────────────
def _npv_of(st, sig):
    """P10 结构时序标注（影子）：窗口内已完成中枢数（≤2/3+ 分层）。

    复用 chanlun_tgraph._fp 指纹提取（口径与历史回填一致）；
    惰性导入避免循环依赖。失败返回 None（不可判不拦截）。"""
    try:
        from chanlun_tgraph import _fp

        fp = _fp(st, sig)
        return fp.get("npv") if fp else None
    except Exception:
        return None


def _tradable(code, pct):
    """mask-first（K203）：检出日涨停收盘 → entry_px 不可成交 → tradable=False。
    阈值：主板/沪老 9.8 / 创业科创(3,688 开头) 19.8 / 北交 29.8。pct 缺 → None。
    US/HK 无涨跌停停牌口径 → None（可成交默认，不判 mask）。"""
    if pct is None or _CUR_MKT != "CN":
        return None
    th = (
        29.8
        if code.endswith(".BJ")
        else (19.8 if code[:3] in ("300", "301", "688") else 9.8)
    )
    return pct < th


DEPTH_DEEP_BARS = 250  # 「深数据」分档线（WO-QT-20261021-099 实证）
# 依据：A 股同股不同窗口对照（400 只 × 12 时点 = 4800 个对照点，88 个带信号），
# 仅取最近 130 根 vs ≤500 根全窗，比较「新检出」信号集合：
#   一致 61.4% / 短窗漏报 26.1% / **短窗虚报 12.5%**
#   且虚报样本 11 例中 3卖 7、3买 4——**几乎全部集中在第三类买卖点**。
# 缠论上说得通：3 类买卖点依赖中枢结构完整（中枢+离开段+回抽不破），
# 130 根窗口构出的中枢易残缺 → 误判。故 250 根作分档线，浅数据标记而非拦截
# （拦截属交易口径，须老田另批）。见 deliverables/2026-10-21_窗口深度对缠论信号影响实证_v1.md


def _depth_tier(n):
    """按可用 K 线根数分档：deep（≥250）/ shallow（<250）/ None（未知）。"""
    if n is None:
        return None
    return "deep" if n >= DEPTH_DEEP_BARS else "shallow"


def _make_row(
    code,
    name,
    sig,
    detect_date,
    lag,
    entry_px,
    reg,
    source,
    trend=None,
    tradable=None,
    depth=None,
):
    return {
        "market": _CUR_MKT,
        "code": code,
        "name": (name or "")[:16],
        "period": "1d",
        "type": sig["type"],
        "dt": sig["dt"],
        "price": sig.get("price"),
        "detect_date": detect_date,
        "lag": lag,
        "entry_px": entry_px,
        "tradable": tradable,
        "trend": trend,
        "mbei": sig.get("mbei"),
        "bei": sig.get("bei"),
        "bei2": sig.get("bei2"),
        "vv": sig.get("vv"),
        "reg": reg,
        "tbe": sig.get("tbe"),
        "mt": None,
        "npv": sig.get("npv"),
        # v4 灰度层（K25）：背驰双口径 + 中枢演化 + 置信度；
        # 历史行/backfill 无这些字段 → None，schema 缺列不影响消费方
        "div_area": sig.get("div_area"),
        "fv_area": sig.get("fv_area"),
        "div_dif": sig.get("div_dif"),
        "fv_dif": sig.get("fv_dif"),
        "pv_state": sig.get("pv_state"),
        "pv_dir": sig.get("pv_dir"),
        "pv_fv": sig.get("pv_fv"),
        "conf": sig.get("conf"),
        "n_prior": sig.get("n_prior"),
        "gray_v": "v4",
        # 数据深度分档（WO-099）：只标注不拦截；历史行无此字段 → None
        "depth_bars": depth,
        "depth_tier": _depth_tier(depth),
        # `completed_at` 是 /api/alerts 的 source_health 契约要求的字段
        # （portal/api_common.py 的 dated() 用 max(该批各行的 completed_at)
        # 作为"这一批扫完了"的证据）。_now_iso() 返回 `...Z`（UTC），
        # 正是 producer_time() 认的 tz-aware ISO 形式。
        # 2026-10-22 之前这一行没有该字段，缠论源被判
        # stale/unproven_completion，AlertWatch 因此拒跑。
        "completed_at": _now_iso(),
        "source": source,
        "first_seen": _now_iso(),
    }


def _open_positions(rows, led):
    """新入池行 → 开模拟仓位（检出日收盘入场，days_held=0）。
    按 (code,type,sig_dt) 幂等：已在账本（含 closed）的不重复开。"""
    have = {
        (p.get("code"), p.get("type"), p.get("sig_dt"))
        for p in led.get("open", []) + led.get("closed", [])
    }
    for r in rows:
        key = (r["code"], r["type"], r["dt"])
        if key in have:
            continue
        have.add(key)
        led["open"].append(
            {
                "code": r["code"],
                "name": r.get("name", ""),
                "type": r["type"],
                "sig_dt": r["dt"],
                "detect_date": r["detect_date"],
                "entry_date": r["detect_date"],
                "entry_px": r["entry_px"],
                "days_held": 0,
                "last_px": r["entry_px"],
                "pnl": 0.0,
                "tags": dict(
                    {
                        s: r.get(s)
                        for s in ("mbei", "bei", "bei2", "vv", "reg", "tbe", "tradable")
                    },
                    npv=r.get("npv"),
                ),
            }
        )


# ── 新鲜度检查（数据由 23:35 同步任务负责，缺数不静默补） ──
def _check_fresh():
    import random

    fs = _ashare_files()
    if not fs:
        print("SCAN no parquet under %s" % ASHARE)
        sys.exit(1)
    if _CUR_MKT != "CN":
        return _check_fresh_zhice(fs)
    max_d = ""
    for f in random.sample(fs, min(30, len(fs))):
        try:
            df = pd.read_parquet(os.path.join(ASHARE, f), columns=["trade_date"])
            if len(df):
                max_d = max(max_d, _td8(df["trade_date"].max()))
        except Exception:
            continue
    if not max_d:
        print("SCAN local parquet unreadable")
        sys.exit(1)
    try:
        import rps_resonance as rps

        rows, fields = rps._ts(
            "trade_cal",
            exchange="SSE",
            start_date=max_d,
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        i_o = fields.index("is_open")
        i_c = fields.index("cal_date")
        missing = sorted(r[i_c] for r in rows if r[i_o] == 1 and r[i_c] > max_d)
    except Exception as e:
        print("FRESH check skipped (tushare unavailable): %s" % str(e)[:80])
        return max_d
    # 当日 K 线盘后才有，必然缺失——剔除后再判滞后（OPS-0930b：
    # 否则 missing 含当日时 _tdx_rescue"全部可补才兜底"永远死锁）
    today = datetime.now().strftime("%Y%m%d")
    missing_core = [d for d in missing if d != today]
    if len(missing_core) >= FRESH_LAG:
        global TDX_RESCUE
        TDX_RESCUE = _tdx_rescue(missing_core)
        if TDX_RESCUE:
            print(
                "fresh rescue: ashare stale (local=%s, missing=%s)，"
                "TDX ts_daily 补齐 %d 日 → 继续扫描"
                % (max_d, ",".join(missing_core[:5]), len(TDX_RESCUE))
            )
            return max_d
        print(
            "SCAN stale: local=%s, missing open days=%s"
            % (max_d, ",".join(missing_core[:5]))
        )
        sys.exit(1)
    print("fresh ok: local latest trade day = %s" % max_d)
    return max_d


def _check_fresh_zhice(fs):
    """US/HK 新鲜度：抽查 parquet 最新交易日 vs zhice-db latest_dates，
    自然日落后 ≥4 天（跨周末+假日）视为断更退出；无交易日历/TDX 兜底，
    数据新鲜度由 export_us_hk 增量导出负责。"""
    import random
    import data_loader as dl

    sample = random.sample(fs, min(30, len(fs)))
    codes = [f[:-8] for f in sample]
    local = {}
    for f, c in zip(sample, codes):
        try:
            df = pd.read_parquet(os.path.join(ASHARE, f), columns=["trade_date"])
            if len(df):
                local[c] = _td8(df["trade_date"].max())
        except Exception:
            continue
    if not local:
        print("SCAN local parquet unreadable (%s)" % _CUR_MKT)
        sys.exit(1)
    try:
        db = dl.latest_dates(_CUR_MKT, list(local))
    except Exception as e:
        print(
            "FRESH(%s) check skipped (zhice unavailable): %s" % (_CUR_MKT, str(e)[:80])
        )
        return max(local.values())
    stale = [
        c
        for c in local
        if db.get(c) and (pd.to_datetime(db[c]) - pd.to_datetime(local[c])).days >= 4
    ]
    if len(stale) >= max(3, len(local) // 2):
        print(
            "SCAN stale(%s): local=%s vs db=%s, stale %d/%d"
            % (
                _CUR_MKT,
                max(local.values()),
                max(db.values() or ["?"]),
                len(stale),
                len(local),
            )
        )
        sys.exit(1)
    print("fresh(%s) ok: local latest = %s" % (_CUR_MKT, max(local.values())))
    return max(local.values())


def _tdx_rescue(missing):
    """ashare 滞后时的 TDX 兜底：缺失交易日全部可补才返回 {day: map}，否则 {}。

    TDX ts_daily 与 tushare daily 同源同值（2026-09-27 parity 14/14）；
    day_map 每日一次分片读取，仅在新鲜度门闸触发时调用（正常路径零开销）。"""
    if not missing:
        return {}
    try:
        import tdx_read as tdxr

        if not tdxr.table_ready("ts_daily"):
            return {}
        out = {}
        for d in missing:
            m = tdxr.day_map(
                "ts_daily", d, ["open", "high", "low", "close", "vol", "pre_close"]
            )
            if not m:
                return {}  # TDX 也缺该日 → 兜底失败，维持退出
            out[d] = m
        return out
    except Exception:
        return {}


def _tdx_topup(bars, code, maps):
    """用 TDX ts_daily 给滞后 parquet 补尾部 bar（升序去重，剔停牌/无效行）。

    pc（pre_close）缺失记 None → _exdiv_between 的跨除权剔除对该增量
    退化为不剔除（TDX 有 pre_close 列时正常）。"""
    if not maps or not bars:
        return bars
    have = {b["dt"] for b in bars}
    added = []
    for d in sorted(maps):
        rec = maps[d].get(code)
        if not rec:
            continue
        dt = _td_dt(d)
        if dt in have:
            continue
        try:
            o, h, l, c, v = (
                float(rec.get(k)) for k in ("open", "high", "low", "close", "vol")
            )
        except (TypeError, ValueError):
            continue
        if any(x != x for x in (o, h, l, c, v)) or c <= 0 or v <= 0:
            continue
        pc = rec.get("pre_close")
        added.append(
            {
                "dt": dt,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "pc": None if pc is None or pc != pc else float(pc),
            }
        )
    if not added:
        return bars
    # 后复权口径下补尾 bar 需链接续因子（K254 正向链），否则补入的
    # 不复权价会在接缝处撕开序列。坑: 基准=原末根（_hfq_scale 归一后
    # f=1）；raw_c 跟踪原始价链，缩放后不再参与比率。
    if _use_hfq() and _CUR_MKT == "CN" and bars:
        f, raw_c = 1.0, bars[-1]["c"]
        for b in added:
            f *= _chain_ratio(raw_c, b.get("pc"))
            raw_c = b["c"]
            b["o"], b["h"], b["l"], b["c"] = (
                round(b["o"] * f, 6),
                round(b["h"] * f, 6),
                round(b["l"] * f, 6),
                round(b["c"] * f, 6),
            )
            if b.get("pc") is not None:
                b["pc"] = b["pc"] * f
            bars.append(b)
        return bars
    return bars + added


# ── 初始回填（一次性） ────────────────────────────────────
def backfill(days=BACKFILL_DAYS):
    """窗口内历史信号 → 截短重放找最早检出日 → 可成交价入池。

    只回填各股自身最后 `days` 根内出现的信号；重放 m=1..MAX_LAG 检不出
    （确认需要尚不存在的后续K线）则弃掉，留给后续每日扫描自然检出。"""
    files = _ashare_files()
    names = _names()
    idx_bars = _idx_bars()
    pool = _load_pool()
    keys = _pool_keys(pool)
    print("backfill: %d files, window=%d bars, pool=%d" % (len(files), days, len(pool)))
    n_ok = n_new = n_drop = 0
    for fi, f in enumerate(files):
        code = f[:-8].replace("_", ".") if CODE_RESTORE else f[:-8]
        bars = _bars_from_parquet(os.path.join(ASHARE, f))
        if not bars:
            continue
        n_ok += 1
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        st = analyze(bars)
        win_start = max(0, len(bars) - days)
        for sig in st["signals"]:
            i0 = idx.get(sig["dt"])
            if i0 is None or i0 < win_start:
                continue
            key = (code, sig["type"], sig["dt"])
            if key in keys:
                continue
            lag = None
            for m in range(1, min(MAX_LAG, len(bars) - 1 - i0) + 1):
                st2 = analyze(bars[: i0 + 1 + m])
                if any(
                    s["type"] == sig["type"] and s["dt"] == sig["dt"]
                    for s in st2["signals"]
                ):
                    lag = m
                    break
            if lag is None:
                n_drop += 1  # 确认滞后超出已有数据，交由每日扫描
                continue
            ei = i0 + lag
            _pct = (
                (bars[ei]["c"] / bars[ei - 1]["c"] - 1) * 100
                if ei >= 1 and bars[ei - 1]["c"]
                else None
            )
            row = _make_row(
                code,
                names.get(code, ""),
                sig,
                bars[ei]["dt"],
                lag,
                bars[ei]["c"],
                _reg_at(sig["dt"], idx_bars),
                "backfill",
                tradable=_tradable(code, _pct),
            )
            pool.append(row)
            keys.add(key)
            n_new += 1
        if (fi + 1) % 500 == 0:
            print("  ..%d/%d files, pool +%d" % (fi + 1, len(files), n_new))
    _write_pool(pool)
    print(
        "backfill done: ok=%d new=%d drop(undetectable)=%d pool=%d"
        % (n_ok, n_new, n_drop, len(pool))
    )
    return pool


# ── 每日增量扫描 ──────────────────────────────────────────
def _active_files(files):
    """US/HK 活跃裁剪：只扫近 10 自然日有新K线的标的（清单由
    export_us_hk.write_active 产出到 data/{mkt}/_active.json；US 5920 只
    里 ~5374 只是历史僵尸，白扫无害但费时）。清单缺失/损坏回退全量
    （fail-open）。CN 不裁剪。"""
    if _CUR_MKT == "CN":
        return files, 0
    try:
        with open(os.path.join(ASHARE, "_active.json"), encoding="utf-8") as f:
            act = set(json.load(f).get("active") or [])
    except Exception:
        return files, 0
    if not act:
        return files, 0
    kept = [f for f in files if f[:-8] in act]
    return kept, len(files) - len(kept)


def scan_all():
    """全市场 analyze → 今日新检出信号入池。

    「新检出」= 在 analyze(bars) 可见、但 analyze(bars[:-1]) 不可见的信号
    （与回填重放的最早可检出口径一致）；检出日=最后一根K线，入场=其收盘。
    """
    files = _ashare_files()
    files, skipped = _active_files(files)  # US/HK 活跃裁剪（CN 原样）
    names = _names()
    idx_bars = _idx_bars()
    pool = _load_pool()
    keys = _pool_keys(pool)
    print(
        "scan: %d files (active trim -%d), pool=%d" % (len(files), skipped, len(pool))
    )
    n_ok = 0
    new_rows = []
    for f in files:
        code = f[:-8].replace("_", ".") if CODE_RESTORE else f[:-8]
        bars = _bars_from_parquet(os.path.join(ASHARE, f), limit=LIMIT_BARS)
        if not bars:
            continue
        bars = _tdx_topup(bars, code, TDX_RESCUE)
        n_ok += 1
        try:
            st = analyze(bars)
            prev = {(s["type"], s["dt"]) for s in analyze(bars[:-1])["signals"]}
        except Exception:
            continue
        # v4 灰度层：信号附加背驰双口径/中枢演化/置信度（就地标注，
        # 失败单信号记 None 不阻断扫描；prev 仅判可见性不 enrich）
        try:
            _gray_enrich(bars, st)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "chanlun_scan.py", exc
            )
        detect = bars[-1]["dt"]
        epx = bars[-1]["c"]
        # T 批：日线趋势影子字段（HH/HL+末笔延伸修正，K24 _trend 口径）；
        # 只标注不过滤——trend=up 前置过滤需先攒 (trend×exe20) 对照数据
        try:
            from chanlun_card import _trend

            t_state = _trend(st.get("bis") or [], epx)["state"]
        except Exception:
            t_state = None
        for sig in st["signals"]:
            if (sig["type"], sig["dt"]) in prev:
                continue  # 最新一根之前已可检出 → 非今日新检出
            key = (code, sig["type"], sig["dt"])
            if key in keys:
                continue
            keys.add(key)
            if sig.get("tbe") is True:
                sig["npv"] = _npv_of(st, sig)  # P10 结构时序标注（影子）
            new_rows.append(
                _make_row(
                    code,
                    names.get(code, ""),
                    sig,
                    detect,
                    None,
                    epx,
                    _reg_at(sig["dt"], idx_bars),
                    "scan",
                    trend=t_state,
                    tradable=_tradable(
                        code,
                        (
                            (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
                            if len(bars) >= 2 and bars[-2]["c"]
                            else None
                        ),
                    ),
                    depth=len(bars),
                )
            )
    if new_rows:
        try:  # S 批：影子打分（不拦截不推送）
            from chanlun_mlf import score_rows

            n_scored = score_rows(new_rows)
            print("ml score: %d/%d rows -> mls" % (n_scored, len(new_rows)))
        except Exception as e:
            print("ml score skip: %s" % str(e)[:120])
        _commit_new_rows(new_rows)  # 锁内读-改-写（与手动推池互斥）
    if _use_hfq() and _CUR_MKT == "CN":
        if HFQ_FALLBACK:  # W1-2 任务A：降级清单落盘留痕
            _write_json(
                os.path.join(HERE, "data", "chanlun_hfq_fallback.json"),
                {
                    "updated_at": _now_iso(),
                    "n": len(HFQ_FALLBACK),
                    "codes": sorted(HFQ_FALLBACK),
                },
            )
            print(
                "hfq fallback: %d 只无因子退回不复权 → "
                "data/chanlun_hfq_fallback.json" % len(HFQ_FALLBACK)
            )
        else:
            print("hfq on: 全市场因子覆盖正常")
    print("scan done: ok=%d new=%d pool=%d" % (n_ok, len(new_rows), len(_load_pool())))
    if n_ok == 0:
        _report_zero_scan(files)
    return new_rows


def _report_zero_scan(files):
    """n_ok==0 的唯一出口：必须把原因说清，不能静默空转。

    三种零各不相同，日志要分开：
      · files 空          → 根本没扫（数据目录空 / active 清单全裁）
      · files 非空但全浅  → 不可扫（数据深度不足，DEPTH GAP）
    两者都不是「今日无信号」——混为一谈会让值班误判策略失效或以为在盯。
    """
    if files:
        _report_depth_gap(files)
        return
    print(
        "scan: 0 只标的可扫（数据目录为空或 active 清单全被裁掉）"
        "——本轮未分析任何标的，**不是**「今日无信号」"
    )


def _parquet_rows(path):
    """读 parquet 行数（pyarrow 元数据优先，回退 pandas）。失败返回 None。"""
    try:
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:
        try:
            return int(len(pd.read_parquet(path, columns=["close"])))
        except Exception:
            return None


def _report_depth_gap(files):
    """全市场 0 只可扫时，显式报「数据深度缺口」，与「今日无信号」区分。

    WO-QT-20261021-094 判例：HK 池恒空的真因是库内港股中位 94 根 <
    MIN_BARS=120，而扫描器每天正常退出（returncode 0）、池恒 0——
    返回码绿而产出恒零，属长期假绿，值班极易误读为「策略今日无机会」。
    此处只在 n_ok==0 时触发（正常日零额外读盘开销），把两类零分开。
    """
    ns = []
    for f in files:
        n = _parquet_rows(os.path.join(ASHARE, f))
        if n is not None:
            ns.append(n)
    if not ns:
        print("depth check: 无法读取 parquet 行数，跳过")
        return
    ns.sort()
    med = ns[len(ns) // 2]
    n_pass = sum(1 for x in ns if x >= MIN_BARS)
    print(
        "DEPTH GAP [%s]: %d/%d 只低于 MIN_BARS=%d（中位 %d 根，最多 %d 根）"
        % (_CUR_MKT, len(ns) - n_pass, len(ns), MIN_BARS, med, ns[-1])
    )
    print(
        "  -> 本市场处于「数据深度静默期」：ok=0 是**不可扫**，"
        "不是今日无信号；数据源补齐历史前不得据此判断策略失效"
    )


def _pool_dual_write_sqlite(new_rows):
    """双写期：新信号同步写入 SQLite（INSERT OR IGNORE）。

    仅 CN 市场行入 SQLite：db 层的镜像表是 CN 池专属（quant.db
    chanlun_pool，与 test_sqlite_migration 对账口径一致），US/HK 行的
    主源是各自市场 JSONL（chanlun_pool_us/_hk.jsonl）。历史缺陷：市场
    切换只重绑 JSONL 文件链，双写仍打 CN 库 → 2026-10-07 对账发现
    38 条 US 行漂移入库（已清）。此处按 market 过滤收口，防复发。
    best-effort：失败只打印警告，不影响 JSONL 主流程。
    """
    cn_rows = [r for r in new_rows if r.get("market", "CN") == "CN"]
    if not cn_rows:
        return
    try:
        import db as _db

        conn = _db.get_conn()
        try:
            n = _db.pool_insert_batch(conn, cn_rows)
            conn.commit()
            print("sqlite dual-write: %d/%d inserted" % (n, len(cn_rows)))
        finally:
            conn.close()
    except Exception as e:
        print("sqlite dual-write warning: %s" % str(e)[:120])


@contextmanager
def _pool_lock():
    """POOL 写互斥（msvcrt 字节锁；获取失败/非 Windows 降级为无锁放行）。

    手动推池（portal 进程）与 15:35 扫描任务分属不同进程，原子替换只保证
    单次写不损坏文件，防不了两侧读-改-写窗口互相覆盖——两侧都必须持锁。"""
    lf = None
    locked = False
    try:
        import msvcrt

        lf = open(POOL + ".lock", "a+")
        lf.seek(0)
        msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
        locked = True
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "chanlun_scan.py", exc
        )
    try:
        yield
    finally:
        if lf is not None:
            if locked:
                try:
                    import msvcrt

                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "silent-except %s: %s", "chanlun_scan.py", exc
                    )
            lf.close()


def _commit_new_rows(new_rows):
    """锁内读-改-写池（并发安全），SQLite 双写（PK 幂等）在锁外。
    返回实际入池行数。"""
    with _pool_lock():
        pool = _load_pool()
        keys = _pool_keys(pool)
        rows = [
            r
            for r in new_rows
            if (r.get("code"), r.get("type"), r.get("dt")) not in keys
        ]
        if rows:
            pool.extend(rows)
            _write_pool(pool)
    if rows:
        _pool_dual_write_sqlite(rows)
    return len(rows)


_PM_LOCK = threading.Lock()  # 门户常驻进程内串行化（跨市场瞬态重绑互斥）


def push_manual(code, sig_type=None, sig_dt=None, market="CN"):
    """工作台手动推池：单票跑与 scan_all 同款增强管线，检出信号入池。

    source='manual' 与扫描行区分；三层幂等照旧（_pool_keys / SQLite PK /
    paper 账本 (code,type,sig_dt)）。
    market="CN"（默认，行为零变化）/"US"/"HK"：_PM_LOCK 内瞬态重绑该市场
    文件链后走同款管线，finally 还原（不留全局态；msvcrt 文件锁随 POOL
    路径自动切 _us/_hk 后缀，与对应市场扫描进程互斥）。US/HK：数据
    zhice-db 优先 / 本地 parquet 兜底，code 以 parquet 文件名 stem 为准
    （与扫描链同源）；无 TDX 兜底、无大盘 reg、tradable=None、名称空。
    返回 {"ok", "code", "detect", "pushed": [...], "skipped": [...]}。"""
    if market not in MKT:
        return {"ok": False, "error": "未知 market: %s（CN|US|HK）" % market}
    with _PM_LOCK:
        prev = _CUR_MKT
        set_market(market)
        try:
            return _push_manual_impl(code, sig_type, sig_dt)
        finally:
            set_market(prev)


def _push_manual_impl(code, sig_type=None, sig_dt=None):
    """push_manual 主体（调用方保证已按市场重绑文件链）。"""
    from chanlun_data import _norm

    nm = _norm(code)
    if _CUR_MKT == "CN":
        if not nm or nm[0] != "CN":
            return {"ok": False, "error": "仅支持 A 股代码（如 600337.SH）：%s" % code}
        code = nm[1]
    else:
        if nm and nm[0] != _CUR_MKT:
            return {"ok": False, "error": "代码「%s」不属于 %s 市场" % (code, _CUR_MKT)}
        code = str(code or "").strip().upper()
        p = _code_path(code)
        if not os.path.exists(p):
            return {
                "ok": False,
                "error": "%s 不在 %s 扫描 universe（无本地 parquet）"
                % (code, _CUR_MKT),
            }
        code = os.path.basename(p)[:-8]  # 文件名 stem=池内 code（与扫描链同源）
    # 数据源与工作台页面同源（chanlun_data.load_kline / zhice-db）：
    # 页面信号清单来自 load_kline，推池必须 WYSIWYG——同源 analyze 才能命中
    # 用户看到的信号；zhice-db 不可达时退回扫描 parquet。
    bars = None
    try:
        from chanlun_data import load_kline

        ri = load_kline(code, "1d", LIMIT_BARS)
        if ri.get("ok") and ri.get("bars"):
            bars = ri["bars"]
    except Exception:
        bars = None
    if not bars:
        path = _code_path(code)
        if not os.path.exists(path):
            return {"ok": False, "error": "zhice-db 与本地 parquet 均无 %s 数据" % code}
        bars = _bars_from_parquet(path, limit=LIMIT_BARS)
    if not bars:
        return {"ok": False, "error": "%s K 线不可用或不足 %d 根" % (code, MIN_BARS)}
    bars = _tdx_topup(bars, code, TDX_RESCUE)
    try:
        st = analyze(bars)
    except Exception as e:
        return {"ok": False, "error": "analyze 失败: %s" % str(e)[:120]}
    try:
        _gray_enrich(bars, st)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "chanlun_scan.py", exc
        )
    detect = bars[-1]["dt"]
    epx = bars[-1]["c"]
    try:
        from chanlun_card import _trend

        t_state = _trend(st.get("bis") or [], epx)["state"]
    except Exception:
        t_state = None
    idx_bars = _idx_bars()
    pct = (
        (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
        if len(bars) >= 2 and bars[-2]["c"]
        else None
    )
    name = _names().get(code, "")
    pre_keys = _pool_keys(_load_pool())
    new_rows, skipped = [], []
    for sig in st["signals"]:
        if sig_type and sig["type"] != sig_type:
            continue
        if sig_dt and sig["dt"] != sig_dt:
            continue
        key = (code, sig["type"], sig["dt"])
        if key in pre_keys:
            skipped.append({"type": sig["type"], "dt": sig["dt"], "reason": "已在池"})
            continue
        if sig.get("tbe") is True:
            sig["npv"] = _npv_of(st, sig)
        new_rows.append(
            _make_row(
                code,
                name,
                sig,
                detect,
                None,
                epx,
                _reg_at(sig["dt"], idx_bars),
                "manual",
                trend=t_state,
                tradable=_tradable(code, pct),
                depth=len(bars),
            )
        )
    n_committed = _commit_new_rows(new_rows) if new_rows else 0
    if n_committed < len(new_rows):  # 并发窗口撞车兜底计数
        skipped.append(
            {
                "type": "*",
                "dt": "*",
                "reason": "并发去重 %d 条" % (len(new_rows) - n_committed),
            }
        )
    pushed = [
        {
            "type": r["type"],
            "dt": r["dt"],
            "price": r["price"],
            "conf": r.get("conf"),
            "entry_px": r.get("entry_px"),
        }
        for r in new_rows
    ]
    return {
        "ok": True,
        "code": code,
        "detect": detect,
        "pushed": pushed,
        "skipped": skipped,
    }


# ── 模拟交易账本 ──────────────────────────────────────────
def _exdiv_between(bars, ei, xi):
    """(entry, exit] 内出现除权事件（pre_close 与前收盘缺口>0.5%）→ True。"""
    for j in range(max(ei + 1, 1), xi + 1):
        pc, prev = bars[j].get("pc"), bars[j - 1]["c"]
        if pc and prev and abs(pc - prev) > 0.005 * prev:
            return True
    return False


def update_paper():
    """逐票盯市：满 HOLD_DAYS 按当日收盘离场(hold20)；反向信号检出日收盘
    提前离场(reverse)；未到期按最后收盘浮动。回填的历史仓位一次性结算。"""
    pool = _load_pool()
    rev = {}  # code → {detect_date: 是否买类信号}
    for r in pool:
        rev.setdefault(r["code"], {})[r["detect_date"]] = r["type"].endswith("买")
    led = _load_paper()
    cache = {}
    still = []
    n_closed = 0
    for pos in led.get("open", []):
        code = pos["code"]
        if code not in cache:
            cache[code] = _tdx_topup(
                _bars_from_parquet(_code_path(code)) or [], code, TDX_RESCUE
            )
        bars = cache[code]
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        ei = idx.get(pos["entry_date"])
        if ei is None:  # 数据缺失（退市等），原样保留待下轮
            still.append(pos)
            continue
        exit_i, reason = None, None
        if ei + HOLD_DAYS < len(bars):
            exit_i, reason = ei + HOLD_DAYS, "hold20"
        my_buy = pos["type"].endswith("买")
        for dd, is_buy in rev.get(code, {}).items():
            if is_buy != my_buy and dd > pos["entry_date"]:
                ii = idx.get(dd)
                if ii is not None and ii > ei and (exit_i is None or ii < exit_i):
                    exit_i, reason = ii, "reverse"
        if exit_i is None:  # 未到期 → 盯市
            last = bars[-1]
            pos["days_held"] = len(bars) - 1 - ei
            pos["last_px"] = last["c"]
            pos["pnl"] = round(
                _dir_ret(pos["type"], pos["entry_px"], last["c"]) or 0.0, 4
            )
            still.append(pos)
            continue
        xb = bars[exit_i]
        ret = round(_dir_ret(pos["type"], pos["entry_px"], xb["c"]) or 0.0, 4)
        pos.update(
            days_held=exit_i - ei,
            last_px=xb["c"],
            pnl=ret,
            exit_date=xb["dt"],
            exit_px=xb["c"],
            ret=ret,
            exit_reason=reason,
            adj_flag=_exdiv_between(bars, ei, exit_i),
        )
        led.setdefault("closed", []).append(pos)
        n_closed += 1
    led["open"] = still
    if len(led.get("closed", [])) > CLOSED_CAP:
        led["closed"] = led["closed"][-CLOSED_CAP:]
    led["updated_at"] = _now_iso()
    _write_json(PAPER, led)
    print(
        "paper: open=%d closed=%d (+%d this round)"
        % (len(led["open"]), len(led["closed"]), n_closed)
    )
    return led


# ── 侧面战绩统计（门户直读缓存） ──────────────────────────
def _stats(rets):
    if not rets:
        return None
    n = len(rets)
    win = sum(1 for x in rets if x > 0)
    return {
        "n": n,
        "win": round(win * 100.0 / n, 1),
        "avg": round(sum(rets) * 100.0 / n, 2),
    }


def pool_eval():
    """按 (信号类型 × 侧面) 聚合纸面收益 → data/chanlun_pool_eval_cache.json。

    口径：closed 取已实现 ret、open 取盯市 pnl；跨除权事件（adj_flag）
    的仓位不计入（不复权数据收益失真）。n<10 不构成结论，影子观察。"""
    led = _load_paper()
    items = []  # (type, ret, tags, closed?)
    for pos in led.get("closed", []):
        if pos.get("adj_flag"):
            continue
        if pos.get("ret") is not None:
            items.append((pos["type"], pos["ret"], pos.get("tags", {}), 1))
    for pos in led.get("open", []):
        if pos.get("pnl") is not None:
            items.append((pos["type"], pos["pnl"], pos.get("tags", {}), 0))
    types = {}
    for t, ret, tags, closed in items:
        a = types.setdefault(
            t,
            {"all": [], "closed_n": 0, **{s: [] for s in SIDES}, "reg": [], "comp": {}},
        )
        a["all"].append(ret)
        a["closed_n"] += closed
        for s in SIDES:
            if tags.get(s) is True:
                a[s].append(ret)
        if aligned(tags.get("reg"), t) is True:
            a["reg"].append(ret)
        # P9 复合叠加（hit=row=tags；任一因子不可判 → None 不计 cov）
        for cid, cv in _comp_judge_all(tags, tags, t).items():
            if cv is None:
                continue
            c = a["comp"].setdefault(cid, {"cov": 0, "n": 0, "rets": []})
            c["cov"] += 1
            if cv is True:
                c["n"] += 1
                c["rets"].append(ret)
    out_types = {}
    for t in TYPES:
        if t not in types:
            continue
        a = types[t]
        comp_out = []
        for rl in _COMP_RULES:
            if t not in rl["types"]:
                continue
            c = a["comp"].get(rl["id"]) or {"cov": 0, "n": 0, "rets": []}
            comp_out.append(
                {
                    "id": rl["id"],
                    "name": rl["name"],
                    "factors": list(rl["factors"]),
                    "cov": c["cov"],
                    "n": c["n"],
                    "stats": _stats(c["rets"]),
                }
            )
        out_types[t] = {
            "all": _stats(a["all"]),
            "closed_n": a["closed_n"],
            "sides": {s: _stats(a[s]) for s in list(SIDES) + ["reg"]},
            "comp": comp_out,
        }
    # W1-2 任务A：口径标注随 use_hfq 开关走（后复权开启时跨除权样本
    # 由复权收益修正而非剔除；降级票仍是不复权 → adj_flag 照旧剔除）
    if _use_hfq() and _CUR_MKT == "CN":
        mode = "后复权口径（ts_adj_factor·最新日=1基准）"
        if HFQ_FALLBACK:
            mode += "·降级不复权%d只" % len(HFQ_FALLBACK)
    else:
        mode = "不复权口径（跨除权剔除）"
    cache = {
        "generated_at": _now_iso(),
        "hold_days": HOLD_DAYS,
        "n_pool": len(_load_pool()),
        "n_open": len(led.get("open", [])),
        "n_closed": len(led.get("closed", [])),
        "types": out_types,
        "note": "检出日收盘入场·%d交易日持有·%s·"
        "卖点为方向统计非做空损益·影子观察 n<10 不构成结论·"
        "复合叠加=全部因子同时成立，任一不可判记 None 不计"
        "可判数（comp 区，只评估不拦截）" % (HOLD_DAYS, mode),
    }
    _write_json(POOL_EVAL, cache)
    print("pool_eval refreshed: %s" % cache["generated_at"])
    return cache


# ── 影子日志：1卖 + tbe=True 专项追踪 ──────────────────────
def refresh_shadow_1sell_tbe():
    """从 chanlun_pool.jsonl + chanlun_paper.json 投影 1卖+tbe=True 影子日志。

    写入 data/chanlun_shadow_1sell_tbe.jsonl，每行字段：
      {market, code, name, type, dt, price, tbe, npv, detect_date, lag,
       entry_px, exe20, exit_date, exit_reason, status, first_seen}
    npv=P10 结构时序标注（扫描窗内已完成中枢数）：池行在线值优先，
    缺失回退 chanlun_tgraph 终修订结构回填（历史行），均缺为 null。

    - 检出信号时若 type=="1卖" 且 tbe is True → 入影子日志
    - exe20 来自 paper 仓位（closed 取 ret / open 取 pnl / 未开仓=null）
      未到期仓位取浮动 pnl 作为 exe20 估计；待 20 根成熟后 closed.ret 即
      最终可成交口径 exe20
    - status: closed（已平仓）/ open（持仓中）/ pending（无匹配仓位，多
      因跨除权剔除或信号未开仓）
    - lag 已在 pool 行回填阶段写入；entry_px 为检出日收盘

    影子日志只记录不推送。回测基线（chanlun_samples.jsonl A股导入子集）：
    n=10, win=90%, avg=+18.88%。日志积累后用于对照实盘扫描口径与回测口径
    的偏差，达标后经用户确认才谈启用推送。
    """
    pool = _load_pool()
    led = _load_paper()
    # 按 (code, sig_dt) 索引 paper 仓位（open + closed）
    paper_idx = {}
    for pos in led.get("open", []) + led.get("closed", []):
        paper_idx[(pos.get("code"), pos.get("sig_dt"))] = pos
    # P10 结构时序标注回填：历史行 npv 取 chanlun_tgraph 终修订结构口径，
    # 新行由 scan_all 在线计算（npv 优先取池行在线值）
    tg_npv = {}
    try:
        from chanlun_tgraph import rows as _tg_rows

        for tr in _tg_rows():
            tg_npv[(tr.get("code"), tr.get("type"), tr.get("sig_dt"))] = tr.get("npv")
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "chanlun_scan.py", exc
        )
    rows = []
    for r in pool:
        if r.get("type") != "1卖" or r.get("tbe") is not True:
            continue
        pos = paper_idx.get((r.get("code"), r.get("dt")))
        npv = r.get("npv")
        if npv is None:
            npv = tg_npv.get((r.get("code"), r.get("type"), r.get("dt")))
        exe20 = None
        exit_date = None
        status = "pending"
        exit_reason = None
        if pos:
            if pos.get("ret") is not None:  # closed
                exe20 = pos.get("ret")
                exit_date = pos.get("exit_date")
                status = "closed"
                exit_reason = pos.get("exit_reason")
            elif pos.get("pnl") is not None:  # open
                exe20 = pos.get("pnl")
                status = "open"
        rows.append(
            {
                "market": r.get("market"),
                "code": r.get("code"),
                "name": r.get("name", ""),
                "type": "1卖",
                "dt": r.get("dt"),
                "price": r.get("price"),
                "tbe": True,
                "npv": npv,
                "detect_date": r.get("detect_date"),
                "lag": r.get("lag"),
                "entry_px": r.get("entry_px"),
                "exe20": exe20,
                "exit_date": exit_date,
                "exit_reason": exit_reason,
                "status": status,
                "first_seen": r.get("first_seen") or _now_iso(),
            }
        )
    rows.sort(key=lambda x: ((x.get("detect_date") or ""), (x.get("code") or "")))
    _write_jsonl(SHADOW_1SELL_TBE, rows)
    closed = [r for r in rows if r["status"] == "closed" and r["exe20"] is not None]
    n_c = len(closed)
    win_c = sum(1 for r in closed if r["exe20"] > 0)
    avg_c = (sum(r["exe20"] for r in closed) / n_c) if n_c else 0.0
    n_o = sum(1 for r in rows if r["status"] == "open")
    n_p = sum(1 for r in rows if r["status"] == "pending")
    print(
        "shadow 1卖+tbe: total=%d closed=%d open=%d pending=%d "
        "win=%.0f%% avg=%.2f%% (baseline n=10 win=90%% avg=+18.88%%)"
        % (
            len(rows),
            n_c,
            n_o,
            n_p,
            win_c * 100.0 / n_c if n_c else 0,
            avg_c * 100.0 if n_c else 0,
        )
    )

    # P10 结构分层（影子观察）：npv=窗口内已完成中枢数，≤2 vs 3+
    def _npv_line(g):
        if not g:
            return "待样本"
        n = len(g)
        return "n=%d win=%.0f%% avg=%+.2f%%" % (
            n,
            sum(1 for v in g if v > 0) * 100.0 / n,
            sum(g) / n * 100.0,
        )

    c2 = [r["exe20"] for r in closed if r.get("npv") is not None and r["npv"] <= 2]
    c3 = [r["exe20"] for r in closed if r.get("npv") is not None and r["npv"] >= 3]
    print("shadow npv分层: ≤2枢 %s | 3+枢 %s" % (_npv_line(c2), _npv_line(c3)))
    return rows


# ── 飞书推送：1卖 + tbe=True 新信号 ────────────────────────
def _push_feishu_text(text):
    """复用 news_collector/send_feishu.py 发文本消息；返回是否成功。

    与 runner.push_feishu 同口径：写临时文件 → 子进程调 send_feishu.py。"""
    if not os.path.isdir(NEWS_DIR):
        print("push skip: news_collector not found at %s" % NEWS_DIR)
        return False
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_chanlun_1sell_tbe.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    args = [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp]
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        return (r.stdout or "").strip().startswith("SEND_OK")
    except Exception as e:
        print("push error: %s" % str(e)[:120])
        return False


def _load_pushed():
    """已推送 (code, dt) 键集合（幂等防重发）。"""
    if not os.path.exists(PUSHED_1SELL_TBE):
        return set()
    try:
        with open(PUSHED_1SELL_TBE, encoding="utf-8") as f:
            return {tuple(k) for k in json.load(f)}
    except Exception:
        return set()


def _save_pushed(keys):
    _write_json(PUSHED_1SELL_TBE, sorted(keys))


def _second_opinion_lines(fresh):
    """P-B1 第二意见段：npv 分层左尾 + 本信号归组（信息层，不改触发）。

    数据源 agent_tools.tgraph_left_tail（tgraph.jsonl 1卖+趋✓ 全集，
    npv≤2 vs 3+ 分组的 n/win/avg/p10/worst/<-30% 计数）；首个带 npv
    的信号标注归组。失败静默跳过不影响推送主流程。
    """
    out = []
    try:
        from agent_tools import call as _at

        g = (_at("tgraph_left_tail", sig_type="1卖", tbe=True) or {}).get("data") or {}
        le2, p3 = g.get("le2"), g.get("3p")
        if le2 and p3 and le2.get("n") and p3.get("n"):
            out.append("第二意见·npv分层（先例左尾口径）：")
            out.append(
                "· ≤2枢 n=%d win=%.0f%% avg=%+.1f%% p10=%+.0f%% "
                "worst=%+.0f%% <-30%%:%d例"
                % (
                    le2["n"],
                    le2["win"],
                    le2["avg"],
                    le2["p10"],
                    le2["worst"],
                    le2["lt30"],
                )
            )
            out.append(
                "· 3+枢 n=%d win=%.0f%% avg=%+.1f%% p10=%+.0f%% "
                "worst=%+.0f%% <-30%%:%d例"
                % (p3["n"], p3["win"], p3["avg"], p3["p10"], p3["worst"], p3["lt30"])
            )
        for r in fresh:
            npv = r.get("npv")
            if npv is None:
                continue
            grp = "≤2枢（左尾较浅）" if npv <= 2 else "3+枢（左尾深·含灾难尾）"
            out.append("· %s npv=%s → %s" % (r.get("code") or "?", npv, grp))
            break
        if out:
            out.append("（仅供第二意见参考，不改触发/拦截口径）")
    except Exception as e:
        print("second_opinion skip: %s" % str(e)[:80])
    return out


def push_1sell_tbe_alert(new_rows):
    """1卖+趋✓ 新信号 → 飞书文本推送。

    触发条件：new_rows 中 type=="1卖" 且 tbe is True 的行（即 scan_all
    返回的今日新检出信号，已剔除历史已入池）。
    幂等：按 (code, dt) 去重，已推送过的不再发。
    开关：config.json chanlun.push_1sell_tbe=true。

    消息内容：检出日 / 新信号数 / 逐条信号明细（代码·名称·信号日·入场价·
    lag）/ 回测基线 vs 实盘扫描偏差 / 免责声明 / 门户链接。

    影子观察期推送：当前 closed n=84 win=63% avg=+5.90% vs 基线 90%/+18.88%
    偏差未收敛，推送仅作为「信号已检出」通知，不构成做空或拦截依据。
    """
    cfg_path = os.path.join(HERE, "config.json")
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8"))
    except Exception:
        cfg = {}
    if not (cfg.get("chanlun") or {}).get("push_1sell_tbe"):
        print("push_1sell_tbe: disabled by config")
        return 0
    if not (cfg.get("feishu") or {}).get("push", True):
        print("push_1sell_tbe: master feishu.push off")
        return 0
    alerts = [
        r for r in (new_rows or []) if r.get("type") == "1卖" and r.get("tbe") is True
    ]
    if not alerts:
        print("push_1sell_tbe: no 1卖+趋✓ in new_rows")
        return 0
    pushed = _load_pushed()
    fresh = [r for r in alerts if (r.get("code"), r.get("dt")) not in pushed]
    if not fresh:
        print("push_1sell_tbe: %d alerts all pushed, skip" % len(alerts))
        return 0
    detect = fresh[0].get("detect_date") or _now_iso()[:10]
    lines = [
        "🚨 缠论强预警 · 1卖+趋✓",
        "检出日：%s" % detect,
        "新信号：%d 条" % len(fresh),
        "",
    ]
    for r in fresh:
        lines.append(
            "· %s %s · 信号 %s · 入场 %.2f · lag %s · npv %s"
            % (
                r.get("code") or "?",
                r.get("name") or "—",
                r.get("dt") or "?",
                r.get("entry_px") or 0,
                r.get("lag") if r.get("lag") is not None else "—",
                r.get("npv") if r.get("npv") is not None else "—",
            )
        )
    lines.append("")
    lines.append("回测基线：n=10 win=90% avg=+18.88%")
    # 实盘扫描当前战绩（从影子日志读 closed 统计）
    try:
        shadow = []
        if os.path.exists(SHADOW_1SELL_TBE):
            with open(SHADOW_1SELL_TBE, encoding="utf-8") as f:
                for ln in f:
                    try:
                        shadow.append(json.loads(ln))
                    except Exception:
                        continue
        closed = [
            r
            for r in shadow
            if r.get("status") == "closed" and r.get("exe20") is not None
        ]
        n_c = len(closed)
        if n_c:
            win_c = sum(1 for r in closed if r["exe20"] > 0)
            avg_c = sum(r["exe20"] for r in closed) / n_c
            lines.append(
                "实盘扫描：n=%d win=%.0f%% avg=%+.2f%%"
                % (n_c, win_c * 100.0 / n_c, avg_c * 100.0)
            )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "chanlun_scan.py", exc
        )
    # 同型先例摘要（semantica_mvp 实时检索 chanlun_paper.json closed[]）
    # 首次调用会加载 fastembed ONNX + 嵌入 5000 条先例（约 30s），
    # 后续进程缓存毫秒级；失败则跳过不影响推送主流程。
    try:
        from semantica_mvp import enrich_signal

        first_code = fresh[0].get("code") or "000001.SH"
        first_dt = fresh[0].get("dt") or detect
        summary = enrich_signal(first_code, "1卖", first_dt, tbe=True)
        lines.append("")
        lines.append(summary)
    except Exception as e:
        print("push_1sell_tbe: enrich skip: %s" % str(e)[:80])
    # P-B1 第二意见（npv 分层左尾+本信号归组；agent_tools 信息层，不改触发）
    lines.extend(_second_opinion_lines(fresh))
    # 事件面摘要（event_graph 个股近期事件流+多空净分；纯 jsonl 扫描不加载向量，
    # events.jsonl 未建则返回"无事件"，不影响推送主流程）
    try:
        from event_graph import summary_for_stock

        for r in fresh:
            ev_sum = summary_for_stock(r.get("code") or "", days=7)
            if ev_sum and "无事件" not in ev_sum:
                lines.append("")
                lines.append(ev_sum)
                break  # 只附首个有事件的标的，控制推送长度
    except Exception as e:
        print("push_1sell_tbe: event_graph skip: %s" % str(e)[:80])
    portal = (cfg.get("report") or {}).get("portal_url") or ""
    lines.append("")
    lines.append("影子观察期·偏差未收敛·不构成做空或拦截依据")
    if portal:
        lines.append("详情：%sbt-chanlun-pool" % portal)
    text = "\n".join(lines)
    ok = _push_feishu_text(text)
    if ok:
        for r in fresh:
            pushed.add((r.get("code"), r.get("dt")))
        _save_pushed(pushed)
        print("push_1sell_tbe: PUSHED %d alerts" % len(fresh))
        return len(fresh)
    print("push_1sell_tbe: SEND_FAILED")
    return 0


# ── 入口 ──────────────────────────────────────────────────
def fill_pool_trend(limit=0):
    """K31：pool 存量行补 trend（K29 起仅新行自带）。

    优先直查 TREND_BF（closed 信号级回填，零重放）；未命中行
    （open 仓/非模拟票）按 detect_date 截断重放 analyze+_trend
    （K24 口径，last_px=截断窗末收盘）。幂等：只处理 trend 为空的行。
    """
    pool = _load_pool()
    todo = [r for r in pool if r.get("trend") is None and r.get("detect_date")]
    print("fill_pool_trend: %d/%d rows todo" % (len(todo), len(pool)))
    from chanlun_card import _trend

    tb = {}
    tb_fp = os.path.join(HERE, "data", "chanlun_trend_backfill.jsonl")
    if os.path.exists(tb_fp):
        with open(tb_fp, encoding="utf-8") as f:
            for ln in f:
                try:
                    t = json.loads(ln)
                    tb[(t.get("code"), t.get("type"), t.get("dt"))] = t.get("trend")
                except Exception:
                    continue
    by_code = {}
    for r in todo:
        k = (r.get("code"), r.get("type"), r.get("dt"))
        if k in tb:
            r["trend"] = tb[k]
        else:
            by_code.setdefault(r["code"], []).append(r)
    n_hit = sum(1 for r in todo if r.get("trend"))
    print("  TREND_BF direct hit: %d, replay todo: %d codes" % (n_hit, len(by_code)))
    codes = sorted(by_code)
    if limit > 0:
        codes = codes[:limit]
    n_replay = 0
    for ci, code in enumerate(codes):
        bars = _bars_from_parquet(_code_path(code))
        if not bars:
            continue
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        by_cut = {}
        for r in by_code[code]:
            i = idx.get(r["detect_date"])
            if i is not None:
                by_cut.setdefault(i, []).append(r)
        for cut, rs in sorted(by_cut.items()):
            try:
                sub = bars[: cut + 1]
                t = _trend(analyze(sub).get("bis") or [], sub[-1]["c"])["state"]
            except Exception:
                continue
            for r in rs:
                r["trend"] = t
                n_replay += 1
        if (ci + 1) % 300 == 0:
            print("  ..%d/%d codes, replayed=%d" % (ci + 1, len(codes), n_replay))
    _write_pool(pool)
    n_still = sum(1 for r in pool if r.get("trend") is None)
    print(
        "fill_pool_trend done: hit=%d replay=%d, still empty=%d/%d"
        % (n_hit, n_replay, n_still, len(pool))
    )


def _auto_overlays(row):
    """K78b: 信号线自动叠加（入场/止损/目标）——plot_level overlays。

    口径与门户 K78 一致：止损看日线结构（3买=zg / 中枢内=笔低点），
    目标看 30m 中枢（等幅外推 / 中枢内=上沿）。入场价绿线。"""
    code = str(row.get("code") or "")
    ovs = []
    try:
        from chanlun_plot import CD
        from chanlun_bi import analyze

        d = CD.load_kline(code, "1d", 500)
        if not d.get("ok") or len(d.get("bars") or []) < 120:
            return ovs
        bars = d["bars"]
        px = float(bars[-1]["c"])
        st = analyze(bars)
        _all = st.get("pivots") or []
        piv = [p for p in _all if float(p["zd"]) < px] or _all
        if piv:
            zg, _zd = float(piv[-1]["zg"]), float(piv[-1]["zd"])
            if px > zg:
                ovs.append((zg, "止损", "#f85149"))
            else:
                lows = []
                for b in (st.get("bis") or [])[-5:]:
                    lows.append(
                        float(b["s_price"]) if b["dir"] == 1 else float(b["e_price"])
                    )
                if lows:
                    u = [x for x in lows if x < px]
                    ovs.append((max(u) if u else min(lows), "止损", "#f85149"))
        m = CD.load_kline(code, "30m", 500)
        if m.get("ok") and len(m.get("bars") or []) >= 120:
            ms = analyze(m["bars"])
            _ma = ms.get("pivots") or []
            mp = [p for p in _ma if float(p["zd"]) < px] or _ma
            if mp:
                mzg, mzd = float(mp[-1]["zg"]), float(mp[-1]["zd"])
                h = mzg - mzd
                if px > mzg:
                    ovs.append((mzg + h, "目标1", "#58a6ff"))
                    ovs.append((mzg + 2 * h, "目标2", "#58a6ff"))
                else:
                    ovs.append((mzg, "目标1", "#58a6ff"))
                    ovs.append((max(mzg + h, mzg * 1.03), "目标2", "#58a6ff"))
    except Exception:
        return ovs
    try:
        ev = float(row.get("entry_px"))
        if ev > 0:
            ovs.insert(0, (ev, "入场", "#3fb950"))
    except (TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "chanlun_scan.py", exc
        )
    return ovs


def plot_day_signals(rows):
    """K72: 当日检出信号自动出缠论结构图（1d 级），写 manifest 供门户展示。

    - 逐票调 chanlun_plot.plot_level（分型/笔/线段/中枢/三类买卖点全标注，
      输出 reports/card_png/{code}_1d_{last_dt}.png），失败单票跳过
    - K78b: 自动叠加信号线（入场/止损/目标，无手工标注口径冲突时可叠加）
    - manifest = data/chanlun_plots_manifest.json（当日检出全量 + png 文件名）
    """
    from chanlun_plot import plot_level, SHOW_DEFAULT

    out = []
    for r in rows:
        code = str(r.get("code") or "")
        if not code:
            continue
        try:
            png = plot_level(code, "1d", SHOW_DEFAULT["1d"], overlays=_auto_overlays(r))
        except Exception:
            png = None
        if png:
            out.append(
                {
                    "code": code,
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "dt": r.get("dt"),
                    "detect_date": r.get("detect_date"),
                    "png": os.path.basename(str(png)),
                }
            )
    mf = os.path.join(HERE, "data", "chanlun_plots_manifest.json")
    _write_json(mf, out)
    print("plot_day_signals: %d/%d png -> manifest" % (len(out), len(rows)))


def main():
    ap = argparse.ArgumentParser(description="chanlun full-market scan")
    ap.add_argument(
        "--market",
        default="CN",
        choices=sorted(MKT),
        help="market to scan: CN(ashare)/US/HK (default CN)",
    )
    ap.add_argument(
        "--backfill", action="store_true", help="one-shot history backfill with replay"
    )
    ap.add_argument(
        "--scan", action="store_true", help="daily full-market scan (default mode)"
    )
    ap.add_argument(
        "--fill-pool-trend",
        action="store_true",
        help="K31: backfill trend for existing pool rows",
    )
    ap.add_argument(
        "--plot",
        action="store_true",
        help="K72: plot 1d chanlun charts for today's signals",
    )
    args = ap.parse_args()
    if args.market != "CN":
        set_market(args.market)  # 数据目录+池/纸面/影子/推送文件链切换
    if args.fill_pool_trend and not args.scan and not args.plot:
        fill_pool_trend()  # 兼容旧 CLI：单独只补 trend
        return
    if args.backfill:
        pool = backfill()
        led = _load_paper()
        _open_positions([r for r in pool if r.get("source") == "backfill"], led)
        _write_json(PAPER, led)
        new_rows = []
    else:
        _check_fresh()
        new_rows = scan_all()
        led = _load_paper()
        _open_positions(new_rows, led)
        _write_json(PAPER, led)
    if args.fill_pool_trend:
        try:
            fill_pool_trend()  # K72 补齐班：指数/环境数据到齐后补 trend
        except Exception as e:
            print("fill_pool_trend skip: %s" % str(e)[:120])
    update_paper()
    pool_eval()
    refresh_shadow_1sell_tbe()
    if args.plot and not args.backfill:
        if _CUR_MKT != "CN":
            print("plot skip: %s 结构图暂未适配（阶段3）" % _CUR_MKT)
        else:
            try:
                _pool = _load_pool()
                _dmax = max(((r.get("detect_date") or "") for r in _pool), default="")
                plot_day_signals([r for r in _pool if r.get("detect_date") == _dmax])
            except Exception as e:
                print("plot skip: %s" % str(e)[:120])
    if new_rows and _CUR_MKT == "CN":
        push_1sell_tbe_alert(new_rows)  # US/HK 首期影子观察，不推送
    if _CUR_MKT == "CN":  # 先例缓存/事件图谱是 CN 池消费方
        # 刷新 Semantica 先例缓存（供门户读，不触发 fastembed，<1s）
        try:
            from semantica_mvp import dump_precedents_cache

            dump_precedents_cache()
        except Exception as e:
            print("dump_precedents_cache skip: %s" % str(e)[:120])
        # 刷新事件图谱（水位增量 + 向量矩阵重建，约 60s；失败不阻断扫描）
        try:
            from event_graph import build as _eg_build

            _eg_build()
        except Exception as e:
            print("event_graph build skip: %s" % str(e)[:120])


if __name__ == "__main__":
    main()
