# -*- coding: utf-8 -*-
"""板块结构层：把 1877 个同花顺板块指数当成 1877 只票，跑同一套缠论。

为什么要这一层
--------------
池子（pool_state）只看得到个股，缺的正是中间那层：市 → **板块** → 个股。
一只票涨，是它自己走出来的，还是整个板块在动？没有板块层，这个问题
在页面上永远只能靠人眼扫涨幅榜去猜。

本层不发明任何新算法
--------------------
板块指数天然是一条 OHLCV 序列，直接喂 `pool_state.analyse`——与个股**同源**。
这不只是省事：口径同源才能做交叉验证（板块三买 + 成分股三买 = 真共振；
板块三卖 + 成分股三买 = 个股逆势，风险更高）。另写一套板块指标，两边
的"三买"含义不同，放在一张表上就是误导。

数据来源（本地仓，零 API）
--------------------------
· ts_ths_index   1665 个板块定义（type: I 行业 / N 概念 / BB 宽基 / R 地域
                 / S 统计 / ST 风格 / TH 主题）
· ts_ths_daily   1877 个板块指数日线，20230731 起，最新到 2026-11-06
· ts_ths_member  38.3 万条成分（1664 板块）
· pool_state_<数据日>.json  当日个股结构（九态 buckets），算成分共振

口径声明
--------
· 板块指数是**加权合成**，它的中枢不等于任何一只成分股的中枢。板块结构
  回答"这个板块整体走到哪了"，不能直接拿来当个股买卖点。
· 成分共振用池子当日落盘（有条件筛选），未覆盖的成分股算"未判"，不当作
  "弱"。分母写清楚，不拿未判的充数。
· 板块涨跌幅来自指数，与成分股等权涨幅不同——指数是市值加权，龙头权重大。
  要等权就看 `ew_pct`（本模块另算），两个都给。

用法
----
  python sector_state.py                     # 最新交易日，打印
  python sector_state.py --types I           # 只看行业板块
  python sector_state.py --save              # 落 reports/sector_state_*.json
  python sector_state.py --top 40 --detail 银行
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool_state as ps  # noqa: E402  判定口径唯一来源

TDX = os.path.join(HERE, "data", "tdx")
REPORTS = ps.REPORTS

# ── 板块宇宙 ────────────────────────────────────────────────
# 只留行业(I)与概念(N)。宽基/地域/统计/风格/主题指数不是"板块"：
# 「昨日涨停表现」「同花顺微盘」「安徽」拿来做板块轮动没有意义。
KEEP_TYPES = ("I", "N")

# 同花顺代码段 = 板块层级（实测 1665 个定义的分布）：
#   881xxx  I   90  行业一级
#   884xxx  I  257  行业二级
#   700xxx  I  428  行业三四级（含 14 组重复指数，收盘序列逐日完全相同）
#   885xxx  N  293  概念
#   886xxx  N   97  概念（新）
#   883xxx  S  105  统计类（昨日涨停/资金前十）——噪音，不入池
#   703xxx  TH 250  主题/金仓组合
#   882xxx  R   33  地域
SEG_LEVEL = {
    "L1": ("881",),
    "L2": ("884",),
    "L3": ("700",),
    "N": ("885", "886"),
}
# 默认：行业一级 + 概念。粒度接近人的认知（"白酒""银行""半导体"），
# 三四级（"白酒Ⅲ""白酒Ⅳ(A股)"）是同一件事换三种写法，只会让页面上
# 同一波行情出现三遍。
LEVEL_DEFAULT = "L1N"


def segs_of(level):
    """'L1N' / 'L1,N' / 'L1 L2' / 'all' → 代码段集合。"""
    lv = (level or LEVEL_DEFAULT).strip().upper()
    if lv == "ALL":
        out = []
        for v in SEG_LEVEL.values():
            out.extend(v)
        return tuple(out)
    keys = re.findall(r"L[123]|(?<![A-Z])N(?![A-Z])", lv)
    out = []
    for k in keys:
        out.extend(SEG_LEVEL.get(k, ()))
    return tuple(dict.fromkeys(out)) or tuple(SEG_LEVEL["L1"])

# 伪概念：名字里带这些的多半是选股结果集或指数编制口径，不是产业主题
DROP_WORDS = ("样本股", "成份股", "成分股", "融资融券", "昨日", "全收益",
              "等权", "加权", "指数", "ST股", "摘帽", "预增", "预减",
              "举牌", "高送转", "破净", "次新", "超跌", "百元", "低价")

MIN_MEMBERS = 8        # 成分少于 8 只，截面统计没有意义
MIN_BARS = ps.MIN_BARS  # 与个股同一门槛（250 根）

BUY_STAGES = ("一买", "二买", "三买", "三买待")
WEAK_STAGES = ("三卖", "中枢下", "离开段下")


# ────────────────────────────────────────────────────────────
# 1. 读数据
# ────────────────────────────────────────────────────────────
def _load_table(name, cols=None):
    fs = sorted(glob.glob(os.path.join(TDX, name, "part_*.parquet")))
    if not fs:
        raise SystemExit("[fail] 缺表 %s（%s）" % (name, os.path.join(TDX, name)))
    dfs = [pd.read_parquet(f, columns=cols) for f in fs]
    return pd.concat(dfs, ignore_index=True)


def load_sectors():
    """板块定义 → {code: {name, type, count, seg}}"""
    idx = _load_table("ts_ths_index", ["ts_code", "name", "count", "type"])
    out = {}
    for r in idx.itertuples(index=False):
        code = str(r.ts_code)
        out[code] = {"name": str(r.name), "type": str(r.type),
                     "count": int(r.count) if pd.notna(r.count) else 0,
                     "seg": code[:3]}
    return out


def load_bars():
    """板块指数日线，一次读全量（129 万行，约 1.4GB 内存峰值可控）。"""
    d = _load_table("ts_ths_daily",
                    ["ts_code", "trade_date", "open", "high", "low", "close",
                     "vol", "avg_price", "pct_change"])
    d["trade_date"] = d["trade_date"].astype("int64")
    return d


def load_members():
    """成分映射 → {板块code: [成分code,...]}；con_code 统一成 000001.SZ 形式。"""
    d = _load_table("ts_ths_member", ["ts_code", "con_code"])
    out = {}
    for code, grp in d.groupby("ts_code", sort=False):
        out[str(code)] = [str(x) for x in grp["con_code"].unique()]
    return out


def _to_bars(g):
    """板块指数行 → pool_state.analyse 要的 DataFrame。

    amount 用 vol × avg_price 折算：tushare 的 amount 单位是千元，
    vol 是股，avg_price 是元 → /1000 得千元。个股门槛 MIN_AMT_YI 直接可比。
    """
    vol = g["vol"].to_numpy(float)
    avg = g["avg_price"].to_numpy(float)
    return pd.DataFrame({
        "trade_date": g["trade_date"].to_numpy(),
        "open": g["open"].to_numpy(float),
        "high": g["high"].to_numpy(float),
        "low": g["low"].to_numpy(float),
        "close": g["close"].to_numpy(float),
        "volume": vol,
        "amount": np.nan_to_num(vol * avg, nan=0.0) / 1000.0,
        "pct_chg": np.nan_to_num(g["pct_change"].to_numpy(float), nan=0.0) / 100.0,
    })


# ────────────────────────────────────────────────────────────
# 2. L1 市场宽度
# ────────────────────────────────────────────────────────────
def market_breadth(asof, use_cache=True):
    """全市场当日涨跌家数——L1 那一层"今天整体能不能做"。

    用 pyarrow.dataset 一次扫 data/ashare（5,573 只约 6 秒）。逐个
    read_parquet 要 40 秒以上，5800 次小文件 IO 的成本全在打开上。

    缓存：结果按交易日落 reports/sector_market_<asof>.json。
    实测在读完 129 万行板块日线之后再扫，会从 6 秒掉到 90 秒
    （内存被占住，页缓存被挤掉），所以①调用点要放在读大表之前，
    ②算过一次就落盘，重跑直接复用。

    涨跌用 pct_chg 符号，平盘单列——把平盘并进下跌会让"净宽"看起来
    比实际更差，这也是池子统计曾经踩过的坑。
    """
    cp = os.path.join(REPORTS, "sector_market_%d.json" % int(asof))
    if use_cache and os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("ok"):
                d["cached"] = True
                return d
        except Exception:
            pass

    import pyarrow.dataset as ds
    try:
        d = ds.dataset(ps.ASHARE, format="parquet")
        tb = d.to_table(columns=["ts_code", "pct_chg", "amount"],
                        filter=ds.field("trade_date") == str(int(asof)))
    except Exception as e:
        return {"ok": 0, "why": "读全市场失败：%s" % e}
    df = tb.to_pandas()
    if df.empty:
        return {"ok": 0, "why": "%d 全市场无数据" % asof}
    p = pd.to_numeric(df["pct_chg"], errors="coerce").dropna()
    up = int((p > 0).sum())
    dn = int((p < 0).sum())
    flat = int((p == 0).sum())
    lim = int((p >= 9.8).sum())          # 涨停/准涨停（主板口径）
    lim20 = int((p >= 19.5).sum())       # 双创 20cm 涨停
    out = {
        "ok": 1, "asof": int(asof), "total": int(len(p)),
        "up": up, "down": dn, "flat": flat,
        "limit_up": lim, "limit_up_20": lim20,
        "net": round((up - dn) / max(up + dn, 1), 4),
        "med": round(float(p.median()) / 100.0, 4),
        "mean": round(float(p.mean()) / 100.0, 4),
        "amt_wan": round(float(pd.to_numeric(df["amount"], errors="coerce").sum()), 0),
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        os.makedirs(REPORTS, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out


# ────────────────────────────────────────────────────────────
# 3. 板块结构
# ────────────────────────────────────────────────────────────
def _ret(close, n):
    if len(close) <= n or close[-n - 1] <= 0:
        return None
    return float(close[-1] / close[-n - 1] - 1.0)


def build(asof=None, level=LEVEL_DEFAULT, min_members=MIN_MEMBERS,
          verbose=True, refresh_breadth=False):
    t0 = time.time()
    segs = segs_of(level)

    # 市场宽度先算：此时内存干净，pyarrow 扫 5,573 个文件约 6 秒；
    # 等 129 万行板块日线进了内存再扫，会掉到 90 秒（见 market_breadth 注释）。
    if asof is None:
        asof = int(_load_table("ts_ths_daily", ["trade_date"])["trade_date"].max())
    asof = int(asof)
    breadth = market_breadth(asof, use_cache=not refresh_breadth)

    sec_def = load_sectors()
    bars = load_bars()
    members = load_members()

    cal = np.sort(bars["trade_date"].unique())
    if asof not in set(int(x) for x in cal):
        raise SystemExit("[fail] %d 不是板块指数的交易日（最新 %d）"
                         % (asof, int(cal[-1])))

    if verbose:
        print("[1/4] 板块宇宙：%d 个定义，段 %s，%d 个有日线，%d 个有成分"
              % (len(sec_def), "/".join(segs), bars["ts_code"].nunique(),
                 len(members)))

    rows, skipped = [], {"seg": 0, "word": 0, "members": 0, "bars": 0,
                         "pv": 0, "dup": 0}
    groups = {c: g for c, g in bars.groupby("ts_code", sort=False)}
    seen_fp = {}                       # 60 日收盘指纹 → 先到的板块代码

    for code, g in groups.items():
        meta = sec_def.get(code)
        if not meta or meta["seg"] not in segs:
            skipped["seg"] += 1
            continue
        if any(w in meta["name"] for w in DROP_WORDS):
            skipped["word"] += 1
            continue
        mem = members.get(code) or []
        if len(mem) < min_members:
            skipped["members"] += 1
            continue

        g = g[g["trade_date"] <= asof].sort_values("trade_date")
        if len(g) < MIN_BARS:
            skipped["bars"] += 1
            continue
        if int(g["trade_date"].iloc[-1]) != asof:
            skipped["pv"] += 1          # 当日停牌/无数据，不拿旧价冒充
            continue

        # 重复指数治理：700 段里「通信设备Ⅲ」「通信设备Ⅳ」收盘序列逐日
        # 完全相同——同一只指数登记了两次。留着会让同一波行情在页面上
        # 出现两遍，且 RPS 把同一只算两次。按最近 60 日收盘指纹去重。
        fp = hash(tuple(np.round(g["close"].to_numpy(float)[-60:], 3)))
        if fp in seen_fp:
            skipped["dup"] += 1
            continue
        seen_fp[fp] = code

        df = _to_bars(g)
        r = ps.analyse(df, code)
        if not r:
            skipped["pv"] += 1
            continue

        close = df["close"].to_numpy(float)
        rows.append({
            "code": code, "name": meta["name"], "type": meta["type"],
            "seg": meta["seg"], "n_mem": len(mem),
            "close": r.get("close"),
            "pct": r.get("pct"),
            "r5": _ret(close, 5), "r20": _ret(close, 20),
            "r60": _ret(close, 60), "r120": _ret(close, 120),
            "stage": r.get("stage"), "pos": r.get("pos"),
            "trend": r.get("trend"), "trend_dir": r.get("trend_dir"),
            "zg": r.get("zg"), "zd": r.get("zd"),
            "dist_zg": r.get("dist_zg"), "dist_zd": r.get("dist_zd"),
            "piv_n_bi": r.get("piv_n_bi"), "piv_over": r.get("piv_over"),
            "sig_age": r.get("sig_age"), "sig_type": r.get("sig_type"),
            "_members": mem,
        })

    if verbose:
        print("[2/4] 结构判定：%d 个板块有效（跳过 %s）"
              % (len(rows), ", ".join("%s=%d" % kv for kv in skipped.items())))

    if verbose:
        if breadth.get("ok"):
            print("[3/4] 市场宽度：%d 只，涨 %d / 跌 %d / 平 %d / 涨停 %d，净宽 %+.0f%%%s"
                  % (breadth["total"], breadth["up"], breadth["down"],
                     breadth["flat"], breadth["limit_up"], breadth["net"] * 100,
                     "（缓存）" if breadth.get("cached") else ""))
        else:
            print("[3/4] 市场宽度缺失：%s" % breadth.get("why"))

    # ── RPS：截面百分位，越高越强 ──
    def _rps(key):
        vals = [(x[key], i) for i, x in enumerate(rows) if x.get(key) is not None]
        if not vals:
            return
        vals.sort()
        n = len(vals)
        for rank, (_, i) in enumerate(vals):
            rows[i]["rps" + key[1:]] = round(rank / max(n - 1, 1) * 100, 1)

    _rps("r20")
    _rps("r60")

    return {"asof": asof, "rows": rows, "skipped": skipped, "segs": segs,
            "n_def": len(sec_def), "cal_max": int(cal[-1]),
            "breadth": breadth, "elapsed": time.time() - t0}


# ────────────────────────────────────────────────────────────
# 3. 成分共振：板块结构 × 个股池子
# ────────────────────────────────────────────────────────────
def load_pool(asof):
    """读当日池子九态（pool_state_<asof>.json）。缺则返回 {}。"""
    p = os.path.join(REPORTS, "pool_state_%d.json" % asof)
    if not os.path.exists(p):
        return {}, None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}, None
    stage_of = {}
    for st, items in (d.get("buckets") or {}).items():
        for it in items or []:
            c = it.get("code")
            if c and c not in stage_of:
                stage_of[str(c)] = st
    return stage_of, d.get("run_at")


def attach_pool(rows, asof, verbose=True):
    """把成分股的池子状态挂到板块上。

    注意分母：只有**被池子判过**的成分股才进分母。没进池子的（成交额不足、
    ST、次新、结构不足）标成 unjudged，不算弱，也不进共振率。
    """
    stage_of, run_at = load_pool(asof)
    if not stage_of:
        if verbose:
            print("[4/4] 无 %d 的池子落盘，成分共振跳过（不拿旧数据充数）" % asof)
        return None, None

    for r in rows:
        mem = r.pop("_members", [])
        judged = [c for c in mem if c in stage_of]
        buy = [c for c in judged if stage_of[c] in BUY_STAGES]
        weak = [c for c in judged if stage_of[c] in ("三卖", "中枢下")]
        r["n_judged"] = len(judged)
        r["n_buy"] = len(buy)
        r["n_weak"] = len(weak)
        r["resonance"] = (len(buy) / len(judged)) if judged else None
        r["weak_rate"] = (len(weak) / len(judged)) if judged else None
        r["buy_codes"] = buy[:12]
    if verbose:
        hit = sum(1 for r in rows if r.get("n_judged"))
        print("[4/4] 成分共振：%d/%d 个板块有池子成分（池子 %d 只，落盘 %s）"
              % (hit, len(rows), len(stage_of), run_at or "?"))
    return stage_of, run_at


# ────────────────────────────────────────────────────────────
# 4. 打印
# ────────────────────────────────────────────────────────────
def _dw(s):
    """显示宽度：CJK 记 2。"""
    w = 0
    for ch in str(s):
        w += 2 if ord(ch) > 0x1100 and not (0x2000 <= ord(ch) <= 0x206F) else 1
    return w


def _p(s, width, right=False):
    s = "" if s is None else str(s)
    pad = max(width - _dw(s), 0)
    return (" " * pad + s) if right else (s + " " * pad)


def _pct(x, n=1):
    return "-" if x is None else ("%+.*f%%" % (n, x * 100))


def _sig_fail(r):
    """卖点类信号 + 价已回到上沿外 = 该卖点被证伪。

    缠论里三卖之后价格重回中枢上方，原卖点就失效了，往往对应更高级别的
    转强。把它和"三卖"并排显示却不加区分，等于把信号的反面当信号本身。
    返回 True 时页面在结构列打 * 号。
    """
    return (r.get("stage") in ("三卖", "中枢下", "离开段")
            and r.get("pos") == "上沿外")


def show(res, top=25, detail=None):
    rows, asof = res["rows"], res["asof"]
    n = len(rows)
    print("\n板块结构层 · %d · %d 个板块有效（定义 %d，段 %s）  用时 %.1fs"
          % (asof, n, res["n_def"], "/".join(res.get("segs") or []),
             res["elapsed"]))

    # 分布
    from collections import Counter
    sc, pc, tc = Counter(r["stage"] for r in rows), Counter(r["pos"] for r in rows), \
        Counter(r["trend"] for r in rows)
    print("\n── 1. 板块层分布" + "─" * 76)
    print("  结构：" + "  ".join("%s %d" % (k, v) for k, v in sc.most_common()))
    print("  位置：" + "  ".join("%s %d" % (k, v) for k, v in pc.most_common()))
    print("  趋势：" + "  ".join("%s %d" % (k, v) for k, v in tc.most_common()))
    buy = sum(sc.get(s, 0) for s in BUY_STAGES)
    print("  买卖点侧：买点类 %d 个（%.1f%%）· 三卖 %d 个 · 中枢下 %d 个"
          % (buy, buy / max(n, 1) * 100, sc.get("三卖", 0), sc.get("中枢下", 0)))

    # 强势：结构买点类 + 强度靠前
    def _rowline(r, with_pool=True):
        st = (r["stage"] or "-") + ("*" if _sig_fail(r) else "")
        res_ = (_p(r["name"][:10], 20) + _p(r["type"], 3)
                + _p(st, 8) + _p(r["pos"] or "-", 8)
                + _p(r["trend"] or "-", 5)
                + _p(_pct(r.get("r5")), 8, 1) + _p(_pct(r.get("r20")), 8, 1)
                + _p(_pct(r.get("r60")), 8, 1)
                + _p("%.0f" % r["rps20"] if r.get("rps20") is not None else "-", 6, 1))
        if with_pool:
            if r.get("n_judged"):
                res_ += _p("%d/%d" % (r["n_buy"], r["n_judged"]), 8, 1)
                res_ += _p("%.0f%%" % (r["resonance"] * 100), 7, 1)
            else:
                res_ += _p("-", 8, 1) + _p("-", 7, 1)
        return res_

    head = (_p("板块", 20) + _p("类", 3) + _p("结构", 8) + _p("位置", 8)
            + _p("趋势", 5) + _p("5日", 8, 1) + _p("20日", 8, 1)
            + _p("60日", 8, 1) + _p("RPS20", 6, 1) + _p("买/判", 8, 1)
            + _p("共振", 7, 1))

    print("\n── 2. 结构买点类（板块级别）" + "─" * 64)
    picks = [r for r in rows if r["stage"] in BUY_STAGES]
    picks.sort(key=lambda r: -(r.get("rps20") or 0))
    if picks:
        print(head)
        for r in picks[:top]:
            print(_rowline(r))
        if len(picks) > top:
            print("  …另有 %d 个，本页只列前 %d" % (len(picks) - top, top))
    else:
        print("  今日无板块级买点。")

    print("\n── 3. 走弱（三卖 / 中枢下，按 20 日跌幅排序）" + "─" * 52)
    weak = [r for r in rows if r["stage"] in ("三卖", "中枢下")]
    weak.sort(key=lambda r: (r.get("r20") if r.get("r20") is not None else 0))
    if weak:
        print(head)
        for r in weak[:top]:
            print(_rowline(r))
        if len(weak) > top:
            print("  …另有 %d 个" % (len(weak) - top))

    print("\n── 4. 强势且结构未破（趋势为上涨 / 上沿外，按 20 日涨幅）" + "─" * 44)
    strong = [r for r in rows if (r.get("r20") or -9) > 0.05
              and r["pos"] in ("上沿外", "枢内")]
    strong.sort(key=lambda r: -(r.get("r20") or 0))
    if strong:
        print(head)
        for r in strong[:top]:
            print(_rowline(r))
        if len(strong) > top:
            print("  …另有 %d 个" % (len(strong) - top))
    else:
        print("  无。")

    n_fail = sum(1 for r in rows if _sig_fail(r))
    print("\n  注：① 板块结构由板块指数跑出，与个股同源，但不可当个股买卖点。"
          "\n      ② 「买/判」= 成分股中处于买点类的只数 / 被池子判过的成分数；"
          "未进池子的成分不计入分母，也不算弱。"
          "\n      ③ 结构列带 * 的 %d 个：原为卖点类，价已回到中枢上沿之外，"
          "该卖点已被证伪。" % n_fail)

    if detail:
        _show_detail(rows, detail)


def _show_detail(rows, kw):
    hit = [r for r in rows if kw in r["name"]]
    if not hit:
        print("\n  未找到板块：%s" % kw)
        return
    for r in hit[:3]:
        print("\n── 明细 · %s（%s）" % (r["name"], r["code"]) + "─" * 50)
        for k in ("type", "n_mem", "close", "pct", "r5", "r20", "r60", "r120",
                  "stage", "pos", "trend", "zg", "zd", "dist_zg", "dist_zd",
                  "piv_n_bi", "piv_over", "sig_type", "sig_age",
                  "n_judged", "n_buy", "n_weak", "resonance"):
            v = r.get(k)
            if k in ("pct", "r5", "r20", "r60", "r120", "resonance", "weak_rate",
                     "dist_zg", "dist_zd"):
                v = _pct(v, 2)
            print("  %-10s %s" % (k, v))
        if r.get("buy_codes"):
            print("  成分买点   %s" % " ".join(r["buy_codes"]))


# ────────────────────────────────────────────────────────────
# 5. 落盘
# ────────────────────────────────────────────────────────────
def save(res):
    asof = res["asof"]
    rows = []
    for r in res["rows"]:
        x = dict(r)
        x.pop("_members", None)
        rows.append(x)
    payload = {
        "asof": asof, "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_sectors": len(rows), "n_def": res["n_def"],
        "segs": list(res.get("segs") or []),
        "skipped": res["skipped"],
        "market": res.get("breadth") or {"ok": 0},
        "params": {"segs": list(res.get("segs") or []),
                   "min_members": MIN_MEMBERS,
                   "min_bars": MIN_BARS, "drop_words": list(DROP_WORDS)},
        "sectors": rows,
    }
    os.makedirs(REPORTS, exist_ok=True)
    p = os.path.join(REPORTS, "sector_state_%d.json" % asof)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("\n[ok] %s  %.1fKB" % (p, os.path.getsize(p) / 1024))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", type=int, default=None)
    ap.add_argument("--level", default=LEVEL_DEFAULT,
                    help="L1 行业一级 / L2 二级 / L3 三四级 / N 概念 / all；"
                         "可组合如 L1N（默认）")
    ap.add_argument("--min-members", type=int, default=MIN_MEMBERS)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--detail", default=None, help="看某个板块明细（名字片段）")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--no-pool", action="store_true", help="不挂成分共振")
    ap.add_argument("--refresh-breadth", action="store_true",
                    help="强制重算市场宽度（默认按交易日缓存）")
    a = ap.parse_args()

    res = build(asof=a.asof, level=a.level, min_members=a.min_members,
                refresh_breadth=a.refresh_breadth)
    if not a.no_pool:
        attach_pool(res["rows"], res["asof"])
    else:
        for r in res["rows"]:
            r.pop("_members", None)
    show(res, top=a.top, detail=a.detail)
    if a.save:
        save(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
