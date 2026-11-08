# -*- coding: utf-8 -*-
"""候风模拟盘（hf_book）：把候风的持仓/风控框架叠加到平台结构池上。

**为什么另起一本账**

平台已有两本账，回答的是不同问题：

    trade_book   产线台账原样（hold20 / 反向信号离场）→ 过去实际怎么走
    shadow_book  v3 影子盘（预算制、势档开关）        → 规则改了会怎样
    hf_book      候风口径模拟盘（本文件）             → 今天该拿什么、明天止损止盈在哪

候风那套在 `quant_backtest/_qpzm_rd_20261106/paper/` 里已经跑了 3843 行执行账本，
但一直没接进平台看板。本文件按它的冻结参数、用它自己的口径重跑一遍。

**规则出处（逐条可查，不是重新发明）**

    持有期 5 日          M2_FROZEN.json / M2_PAPER_README.md
    inv_vol 权重          paper_daily.py:69-73（--weight-scheme inv_vol --top-n 30）
    链式净值              paper_daily.py:84-103  nav_t = nav_{t-1}×(1+daily_ret_t)
    逐日盯市              portfolio_sim equity_curve（cash / mv / n_pos / util）
    SL = max(上日持有最高 − 2×ATR14, 成本×0.96)
    TP = 上日持有最高 − 1×ATR14                       guardian_agent_v1.0.4.py:126-127
    四级告警 🟠破位 / 🟡距止损<3% / ✅回撤1ATR / 🟢正常  同文件 129-136
    T/M/P 三标签 + 决策矩阵                            sell_methodology_v1.md:23-55
    "卖点规则一律不自动执行"                           同文件 §五（8% 硬止损 −80 万）

**三条与产线台账不同的口径（都是被投诉的"不对"之处）**

1. **T+1 开盘建仓**：台账 entry = 检出日收盘。收盘才知道信号、又按收盘价成交，
   是同一时点的自我实现。候风用次日开盘，本文件跟随。
2. **固定 5 日**：台账 hold20 且反向信号提前离场，两个变量混在一起，看不出是
   持有期赚的还是信号择时赚的。候风只留时间这一个变量。
3. **SL/TP 只提示不执行**：guardian 算出位、写进账本，但 `reason` 仍可能是 hold。
   本文件同样不拿 SL/TP 改净值和胜率——只在持仓行上显示"位在哪、还差多远"。

用法::

    python -X utf8 tools/research/hf_book.py                  # 默认 120 交易日窗
    python -X utf8 tools/research/hf_book.py --days 60
    python -X utf8 tools/research/hf_book.py --degrade        # 退化副本（自检用）
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
import zlib
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest", "data")
TDX = os.path.join(QBT, "tdx")
ASH = os.path.join(QBT, "ashare")
FULL = os.path.join(QBT, "chanlun_paper_full.json")
OUT = os.path.join(ROOT, "portal", "hf_book.json")
OUT_TRADES = os.path.join(ROOT, "portal", "hf_trades.json")

sys.path.insert(0, os.path.join(ROOT, "tools", "research"))
try:
    import chan_struct as CS
except Exception:      # 结构位是增强项，缺了也必须能跑 ATR 口径
    CS = None

# ---------------------------------------------------------------- 冻结参数
P = {
    "hold": 5,              # 候风 M2 冻结：固定 5 日
    "top_n": 30,            # --top-n 30
    "capital": 10_000_000,  # 1000 万，与 portfolio_sim 同基准
    "max_w": 0.10,          # 单票上限（risk_gate single_name_max）
    "lot": 100,             # 整手
    "min_ticket": 0.003,    # 碎单闸门：单笔 <0.3% 权益整笔不投
    "buy_cost": 0.00075,    # 买 0.075%
    "sell_cost": 0.00125,   # 卖 0.125%（含印花税）
    "atr_n": 14,            # ATR14
    "sl_atr": 2.0,          # SL = hi − 2×ATR
    "sl_floor": 0.96,       # 且不低于成本×0.96
    "tp_atr": 1.0,          # TP = hi − 1×ATR
    "max_pos": 40,          # 总持仓上限：候风 avg_positions 41.53（portfolio_sim 60 日实测）
    "days": 120,            # 回放窗口（交易日）
    # ---- 2026-11-08 四项升级（默认全关，保证原基线逐位不变）----
    "sl_mode": "struct",    # struct=日线中枢 ZG / 笔低点优先，ATR 兜底（默认）；atr=纯 ATR
    "sl_max_dist": 0.20,    # 结构位离现价超过 20% 视为不可用：趋势中笔低点可能很远，
                             # 那种「止损」形同虚设，退回 ATR 才是能用的位
    "sizing": "inv_vol",    # inv_vol=逆波动率等权；risk=风险预算
    "risk_pct": 0.01,       # 风险预算：单笔最大亏损 = 净值 × 1%
    "risk_floor": 0.04,     # 止损距离下限 4%（ATR 极小时防仓位爆炸）
    "exit_mode": "hold5",   # hold5=固定5日；tag_all=红橙全卖；tag_red=只红；tag_half=橙卖半+红清
    # ---- 2026-11-08 第十二轮（默认全关，基线逐位不变）----
    "exit_lock_days": 0,    # 出场后现金锁定 N 个交易日。0=不锁（基线）。
                             # 理由：标签出场每笔少亏 0.59%，但当天又投进同样负期望的新仓，
                             # 改善被吃掉。锁 N 天才检验"这笔钱到底该不该立刻回去"。
    "rank": "lag_asc",      # 名额分配排序：lag_asc（基线）/ lag_desc / t123 / t213 / t321 / rand
    "short_gross": 1.0,     # 空头账本总敞口上限（占权益），与多头盘对称
    # ---- 2026-11-08 第十三轮：净敞口闸门（默认 1.0=关，基线逐位不变）----
    # 前面十二轮全在改选股规则，结论都是噪声。唯一反复有效的动作是降净敞口，
    # 但那到底是"少亏"还是"更有效率"，只有 ret_per_util 能回答：
    # 等比例缩仓时 ret_per_util 不变（零信息），若随降敞口上升，才说明
    # 低敞口时段的持仓确实更差——那才叫择时能力。
    "gross_cap": 1.0,       # mv/equity 上限。1.0=只受 max_pos 硬顶（基线）
    # ---- 可交易性闸门（默认关=保基线；凡是用 hf_hist 长历史信号跑，必须开）----
    # 不开这个闸门，"次日开盘建仓"会把一字涨停板当成能买进：北交所 30% 板
    # 连拉三天就是 +105%，一笔就把净值抬上去。基线 161 天里这类票少，
    # 所以开不开差别不大；长历史全市场扫描里这类票是主要收益来源，
    # 不开就是拿不可成交的收益当成绩。
    "limit_gate": False,
}
BUY_TYPES = ("1买", "2买", "3买")
SELL_TYPES = ("1卖", "2卖", "3卖")
IDX_CODE = "000985.CSI"


def _d(s):
    return str(s or "").replace("-", "")[:8]


def _f(x, d=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


# ---------------------------------------------------------------- 基础数据
def load_axis():
    """交易日历：日更个股 000001_SZ（候风 paper_daily.py:46-50 同源）。

    不用 000001_SH——那是指数文件，更新滞后，会把 as-of 拖后一天。
    """
    import pandas as pd
    f = os.path.join(ASH, "000001_SZ.parquet")
    d = pd.read_parquet(f, columns=["trade_date"])
    return sorted({str(x).replace("-", "")[:8] for x in d["trade_date"].tolist()})


def load_index_blacklist():
    """指数黑名单：台账里混进来的只有指数，用 ts_index_daily 的代码集反向排除。"""
    import pandas as pd
    bad = set()
    for p in glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet")):
        d = pd.read_parquet(p, columns=["ts_code"])
        bad |= set(d["ts_code"].astype(str).unique())
    return bad


def load_names():
    import pandas as pd
    f = os.path.join(TDX, "ts_stock_basic.parquet")
    if not os.path.exists(f):
        return {}
    d = pd.read_parquet(f)
    col = "name" if "name" in d.columns else None
    if not col:
        return {}
    return {str(c): str(n) for c, n in zip(d["ts_code"], d[col]) if isinstance(n, str)}


def load_industry(asof):
    """申万一级 PIT（与 trade_book 同源，避免两处行业口径打架）。"""
    try:
        from pit_industry import load_sw_l1_industry
        s = load_sw_l1_industry().at(asof)
        return {str(k): str(v) for k, v in s.items()}
    except Exception as e:                                   # noqa: BLE001
        print("  ! 行业映射不可用：%s" % e)
        return {}


def load_index_close():
    """中证全指收盘，用于同期指数收益（alpha 的口径参考）。"""
    import pandas as pd
    clos = {}
    for p in glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet")):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == IDX_CODE]
        for dt, c in zip(d["trade_date"].astype(str), d["close"]):
            c = _f(c)
            if c and c > 0:
                clos[_d(dt)] = c
    return dict(sorted(clos.items()))


class Bars:
    """单票行情 + 收益指数。

    `f[i]` = ∏(close/pre_close)，是**收益指数**而不是复权价倍数。两者关系是
    `adj[i] = c[0]·f[i]`，所以绝不能写 `c[i]·f[i]`——那等于把收益乘了两次，
    5 天能算出 +200%（首版就是这样把净值顶到 +96% 的）。

    持仓定价统一走 `px_at(i, ei)`：以建仓日收盘价为锚，按收益指数演进。
    跨除权自动正确（除权日 pre_close 已是除权后参考价，c/pc 就是真实涨跌幅），
    且价格刻度仍是人看得懂的真实价。

    ATR 用真实价算（不缩放）：持有窗只有 5 日，窗内除权极少；用复权尺度反而
    会让 SL/TP 的绝对值失去"多少钱"的含义。
    """

    __slots__ = ("dates", "o", "h", "l", "c", "v", "atr", "pos", "f")

    def __init__(self, dates, o, h, l, c, v, pc):
        self.dates = dates
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v
        n = len(dates)
        self.pos = {d: i for i, d in enumerate(dates)}
        self.f = [1.0] * n
        for i in range(1, n):
            base = pc[i] if pc[i] and pc[i] > 0 else c[i - 1]
            self.f[i] = self.f[i - 1] * (c[i] / base) if base else self.f[i - 1]
        tr = [0.0] * n
        for i in range(n):
            if i == 0 or pc[i] <= 0:
                tr[i] = h[i] - l[i]
            else:
                tr[i] = max(h[i] - l[i], abs(h[i] - pc[i]), abs(l[i] - pc[i]))
        n_atr = P["atr_n"]
        self.atr = [None] * n
        for i in range(n_atr - 1, n):
            seg = tr[i - n_atr + 1: i + 1]
            self.atr[i] = sum(seg) / len(seg)

    def i_of(self, d):
        return self.pos.get(d)

    def px_at(self, i, ei):
        """持仓价：建仓日收盘 × 收益倍数。i/ei 都是该票 bar 下标。"""
        i = max(0, min(i, len(self.c) - 1))
        if ei < 0 or ei >= len(self.c) or self.c[ei] <= 0 or self.f[ei] == 0:
            return self.c[i]
        return self.c[ei] * self.f[i] / self.f[ei]

    def px_at_k(self, i, ei, kind="c"):
        """任意字段的归一价（kind='o' 用于次日开盘卖出的成交价）。

        px_at 的语义是「当日的复权收盘价（原始尺度）」，所以取开盘价不能直接
        `o[i] × f[i]/f[ei]`——f 是收益指数，再乘 o[i] 等于把收益乘两次（首版把
        惠丰钻石的 −33% 算成 −53%）。必须补上 o[i]/c[i] 这个「日内位置」比例。
        """
        arr = {"o": self.o, "h": self.h, "l": self.l, "c": self.c}[kind]
        i = max(0, min(i, len(arr) - 1))
        if ei < 0 or ei >= len(self.c) or self.c[ei] <= 0 or self.f[ei] == 0:
            return arr[i]
        if self.c[i] <= 0:
            return arr[i]
        return arr[i] / self.c[i] * self.c[ei] * self.f[i] / self.f[ei]


def load_bars(code, keep_from):
    """读单票日线，只保留 keep_from 之后的 bar（前留 60 根给 ATR/MA 预热）。"""
    import pandas as pd
    f = os.path.join(ASH, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        return None
    d = pd.read_parquet(f, columns=["trade_date", "open", "high", "low", "close",
                                    "pre_close", "volume"])
    if d is None or len(d) < 30:
        return None
    d = d.assign(td=d["trade_date"].astype(str).str.replace("-", "").str[:8])
    d = d.sort_values("td")
    dates = d["td"].tolist()
    cut = 0
    for i, dt in enumerate(dates):
        if dt >= keep_from:
            cut = i
            break
    cut = max(0, cut - 60)
    dates = dates[cut:]
    o = [max(_f(x, 0.0), 0.0) for x in d["open"].tolist()[cut:]]
    h = [max(_f(x, 0.0), 0.0) for x in d["high"].tolist()[cut:]]
    lo = [max(_f(x, 0.0), 0.0) for x in d["low"].tolist()[cut:]]
    c = [max(_f(x, 0.0), 0.0) for x in d["close"].tolist()[cut:]]
    v = [max(_f(x, 0.0), 0.0) for x in d["volume"].tolist()[cut:]]
    pc = [max(_f(x, 0.0), 0.0) for x in d["pre_close"].tolist()[cut:]]
    # 剔除尾部无收盘的脏行
    while dates and c[-1] <= 0:
        for arr in (dates, o, h, lo, c, v, pc):
            arr.pop()
    if len(dates) < 30:
        return None
    return Bars(dates, o, h, lo, c, v, pc)


# ---------------------------------------------------------------- 信号
def build_signals(rows, axis, bad_idx, names, keep_from, kinds=None):
    """台账行 → 候风建仓计划：detect 日的**次一交易日开盘**建仓。

    台账 entry_date == detect_date（都是检出日收盘）。候风不允许同刻成交，
    所以建仓日统一后移一个交易日。停牌（无 bar）则该笔作废并计数。
    """
    kinds = tuple(kinds or BUY_TYPES)
    ax_pos = {d: i for i, d in enumerate(axis)}
    plans = defaultdict(list)
    stat = {"rows": 0, "not_buy": 0, "index": 0, "no_detect": 0, "too_old": 0,
            "dup": 0, "no_next": 0}
    seen = set()
    for r in rows:
        stat["rows"] += 1
        typ = str(r.get("type") or "")
        if typ not in kinds:
            stat["not_buy"] += 1
            continue
        code = str(r.get("code") or "")
        if code in bad_idx:
            stat["index"] += 1
            continue
        det = _d(r.get("detect_date"))
        if det not in ax_pos:
            stat["no_detect"] += 1
            continue
        if det < keep_from:
            stat["too_old"] += 1
            continue
        di = ax_pos[det]
        if di + 1 >= len(axis):
            stat["no_next"] += 1
            continue
        ed = axis[di + 1]
        key = (code, ed)
        if key in seen:
            stat["dup"] += 1
            continue
        seen.add(key)
        sig = _d(r.get("dt") or r.get("sig_dt"))
        lag = ax_pos[det] - ax_pos[sig] if sig in ax_pos else None
        plans[ed].append({
            "code": code,
            "name": names.get(code, ""),
            "type": typ,
            "sig_dt": sig,
            "detect": det,
            "lag": lag,
        })
    return plans, stat


# ---------------------------------------------------------------- 标签
def tmp_tags(b, i, entry_px, px_now, hi, was_fav8):
    """T/M/P 三标签（sell_methodology_v1.md:23-43），全部收盘价口径、无前视。"""
    c = b.c
    n = len(c)
    ma5 = sum(c[max(0, i - 4): i + 1]) / min(5, i + 1)
    ma10 = sum(c[max(0, i - 9): i + 1]) / min(10, i + 1)
    if px_now > ma5 and px_now > ma10:
        T = "T1"
    elif i >= 1 and c[i - 1] >= ma5:
        T = "T2"
    elif i >= 2 and c[i] < ma10 and c[i - 1] < ma10:
        T = "T3"
    else:
        T = "T1" if px_now >= ma5 else "T2"
    lo10 = min(c[max(0, i - 9): i + 1])
    if px_now < lo10:
        T = "T4"

    v5 = sum(b.v[max(0, i - 5): i]) / max(1, len(b.v[max(0, i - 5): i]))
    up = i >= 1 and c[i] > c[i - 1]
    if i >= 1 and v5 > 0 and b.v[i] >= 1.8 * v5 and c[i] <= c[i - 1]:
        M = "M3"
    elif i >= 5:
        seg = c[i - 5: i + 1]
        dn = sum(1 for k in range(1, len(seg)) if seg[k] < seg[k - 1])
        peak = max(seg)
        dd = (peak - c[i]) / peak if peak else 0
        M = "M2" if (dn >= 2 and dd > 0.08) else "M1"
    else:
        M = "M1"

    ret = px_now / entry_px - 1 if entry_px else 0
    if ret > 0.08:
        Pm = "P1"
    elif ret < -0.08:
        Pm = "P3"
    elif was_fav8 and ret <= 0.01:
        Pm = "P2"
    else:
        Pm = ""
    return T, M, Pm


def idx_on(b, d):
    """按日历日取 bar 下标：停牌日退回该票最后一个 <= d 的 bar（候风"停牌挂 as-of"）。"""
    j = b.i_of(d)
    if j is not None:
        return j
    j = len(b.dates) - 1
    while j > 0 and b.dates[j] > d:
        j -= 1
    return j


def action_of(T, M, Pm):
    """决策矩阵（sell_methodology_v1.md:49-55）。机器只给等级，不执行。"""
    if T == "T4" or Pm == "P3":
        return "red", "人工立即复核"
    if T == "T3" or (T == "T2" and M != "M1"):
        return "orange", "减半 + 剩余设保本"
    if Pm == "P1":
        return "green", "只兑现一半"
    if T == "T2" or M in ("M2", "M3"):
        return "yellow", "明日盯确认"
    return "green", "持有"


# ---------------------------------------------------------------- 主模拟
_TYPE_ORDER = {"t123": ("1买", "2买", "3买"),
               "t213": ("2买", "1买", "3买"),
               "t321": ("3买", "2买", "1买")}


def limit_pct(code, name):
    """涨跌停幅度：ST 5%，创业板/科创 20%，北交所 30%，其余 10%。

    用板块属性而不是「当日涨幅>9.5%」，因为创业板 10% 的涨幅根本不是涨停，
    用统一阈值会误杀一整批正常票。
    """
    n = (name or "").upper()
    if "ST" in n:
        return 0.05
    c = str(code or "")
    if c.startswith(("688", "689")) or c[:3] in ("300", "301"):
        return 0.20
    if c.startswith(("8", "4", "92")):        # 北交所 8xxxxx / 4xxxxx / 920xxx
        return 0.30
    return 0.10


def rank_key(mode):
    """名额分配排序。

    默认 lag 升序是候风原口径。lag_desc 是必须测的反直觉一档——台账里
    lag>20 的样本 alpha 反而最好（+1.08%，t=3.58）。rand 是零信息基线：
    任何排序规则不显著好于 rand，就等于没在筛。
    """
    if mode == "lag_desc":
        return lambda x: (-(x["lag"] if x["lag"] is not None else 99), x["code"])
    if mode in _TYPE_ORDER:
        order = {t: k for k, t in enumerate(_TYPE_ORDER[mode])}
        return lambda x: (order.get(x["type"], 9),
                          x["lag"] if x["lag"] is not None else 99, x["code"])
    if mode.startswith("rand"):
        # 不能用 hash()：字符串哈希每进程加盐，同一份数据两次跑结果不同
        salt = mode.split(":")[1] if ":" in mode else "0"
        return lambda x: (zlib.crc32((salt + x["code"] + x["detect"]).encode()), x["code"])
    return lambda x: (x["lag"] if x["lag"] is not None else 99, x["code"])


def simulate(axis, plans, bars, entry_at_close=False, collect_skip=False):
    """逐日推进：先结算到期 → 再开盘建仓 → 收盘盯市（顺序同 portfolio_mtm）。

    entry_at_close=True 是**退化档**（自检用）：用检出日收盘价成交，等于
    "收盘才知道信号、又按收盘价买入"，制造前视，自检必须能把它抓出来。
    """
    hold = P["hold"]
    lot = P["lot"]
    mx = P["max_w"]
    cap0 = P["capital"]
    cash = cap0
    live = []
    nav_rows = []
    trades = []
    skipped_room = 0
    skip_days = []          # 按日拆开的被挡信号（collect_skip 时填）
    locked = []             # 出场后冷静期：{"amt": 金额, "rel": 可动用日索引}
    n_locked_block = 0      # 因冷却被压掉的建仓次数（诊断用）
    i0 = None
    for i, d in enumerate(axis):
        if d in plans:
            i0 = i
            break
    if i0 is None:
        i0 = len(axis) - 1

    by_entry = plans

    for i in range(i0, len(axis)):
        d = axis[i]

        # ⓪ 标签触发 → 次日开盘卖出（sell_methodology 的等级真的执行会怎样）
        #    顺序必须在"开盘建仓"之前：同一天先卖后买，现金才够。
        if P["exit_mode"] != "hold5":
            keep = []
            for p in live:
                if not p.get("tag_sell"):
                    keep.append(p)
                    continue
                b = bars[p["code"]]
                xi = b.i_of(d)
                if xi is None or b.o[xi] <= 0:      # 停牌：挂着等复牌
                    keep.append(p)
                    continue
                px = b.px_at_k(xi, p["entry_i"], "o")
                q_sell = p["qty"]
                # 只有"橙色触发的半仓"才卖一半；红色永远全清（否则 qty 会被反复减半）
                if p.get("tag_half") and p.get("tag_kind") == "orange" and p["qty"] > lot:
                    q_sell = (p["qty"] // 2 // lot) * lot
                    if q_sell <= 0:
                        q_sell = p["qty"]
                proceeds = q_sell * px * (1 - P["sell_cost"])
                cash += proceeds
                if P["exit_lock_days"] > 0:
                    locked.append({"amt": proceeds, "rel": i + P["exit_lock_days"]})
                # 反事实：如果按原计划持到 exit_i，价格会是多少（只用于事后对账）
                alt_i = min(p["exit_i"], len(b.c) - 1)
                alt_px = b.px_at(alt_i, p["entry_i"])
                if q_sell >= p["qty"]:
                    p["alt_exit_px"] = alt_px
                    p["exit_date"] = b.dates[xi]
                    p["exit_px"] = px
                    p["exit_reason"] = "tag_" + p.get("tag_kind", "")
                    p["ret"] = px / p["entry_px"] - 1 - (P["buy_cost"] + P["sell_cost"])
                    p["hold_days"] = xi - p["entry_i"]
                    trades.append(p)
                else:
                    # 卖一半：单独记一笔已实现，剩余仓位继续持到 5 日。
                    # path 必须深拷贝——否则剩余仓位后续 append 会改到这笔的记录。
                    rec = dict(p)
                    rec["path"] = [dict(x) for x in p.get("path") or []]
                    rec["alt_exit_px"] = alt_px
                    rec["qty"] = q_sell
                    rec["amt"] = q_sell * p["entry_px"]
                    rec["exit_date"] = b.dates[xi]
                    rec["exit_px"] = px
                    rec["exit_reason"] = "tag_half"
                    rec["ret"] = px / p["entry_px"] - 1 - (P["buy_cost"] + P["sell_cost"])
                    rec["hold_days"] = xi - p["entry_i"]
                    trades.append(rec)
                    p["qty"] -= q_sell
                    p["half_done"] = True
                    p["tag_sell"] = False
                    keep.append(p)
            live = keep

        # ① 到期结算（收盘）
        keep = []
        for p in live:
            b = bars[p["code"]]
            # 到期判定必须用**日期**，不能用 bar 索引：exit_i 是该票自己的 bar 下标，
            # i 是全市场日历下标，两者空间不同，混比会随机提前平仓（首版踩过）。
            if p["exit_d"] is not None and d >= p["exit_d"]:
                xi = b.i_of(p["exit_d"])
                if xi is None:
                    xi = b.i_of(d)
                if xi is None:
                    keep.append(p)
                    continue
                px = b.px_at(xi, p["entry_i"])
                proceeds = p["qty"] * px * (1 - P["sell_cost"])
                cash += proceeds
                if P["exit_lock_days"] > 0:
                    locked.append({"amt": proceeds, "rel": i + P["exit_lock_days"]})
                p["exit_date"] = b.dates[xi]
                p["exit_px"] = px
                p["exit_reason"] = "hold5"
                p["ret"] = px / p["entry_px"] - 1 - (P["buy_cost"] + P["sell_cost"])
                p["hold_days"] = xi - p["entry_i"]
                trades.append(p)
            else:
                keep.append(p)
        live = keep

        # ② 开盘建仓
        cands = by_entry.get(d) or []
        if cands:
            # 冷静期到期释放。释放日 == 出场日 + N，当天就能用（rel > i 的仍在锁）
            if P["exit_lock_days"] > 0:
                locked = [L for L in locked if L["rel"] > i]
            avail = cash - sum(L["amt"] for L in locked) if locked else cash
            prev_d = axis[i - 1] if i > 0 else d
            mv0 = sum(p["qty"] * bars[p["code"]].px_at(
                idx_on(bars[p["code"]], prev_d), p["entry_i"]) for p in live)
            eq0 = cash + mv0
            budget = eq0 / hold
            if P["gross_cap"] < 1.0:
                # 净敞口闸门：今天最多把市值推到 gross_cap×eq0，缺口才是今天的额度。
                # 用"硬闸"而不是"按比例缩单"——缩单会让 util 缓慢爬升，
                # 看不出闸门在哪一档真正挡住，也没法逐档对比。
                budget = min(budget, max(0.0, P["gross_cap"] * eq0 - mv0))
            cands = sorted(cands, key=rank_key(P["rank"]))[: P["top_n"]]
            # 总持仓上限：候风 60 日 avg 41.5 只、util 40%。不加这条，缠论池的信号密度
            # （日均 20+ 新信号）会把 util 顶到 90%，仓位密度就与候风不可比了。
            room = max(0, P["max_pos"] - len(live))
            n_blocked = max(0, len(cands) - room)
            if n_blocked:
                skipped_room += n_blocked
                if collect_skip:
                    bt = defaultdict(int)
                    for sx in cands[room:]:
                        bt[sx["type"]] += 1
                    skip_days.append({
                        "d": d, "n_cand": len(cands), "n_take": room,
                        "n_block": n_blocked, "room": room, "n_live": len(live),
                        "by_type": dict(bt),
                    })
            cands = cands[:room]
            # 敞口闸门挡住的这批，不是"名额不够"，必须分开记：
            # 否则 gross=0.5 的实验看起来像"信号变少了"，其实是钱被锁住了。
            if budget <= 0.0 and cands:
                if collect_skip:
                    bt = defaultdict(int)
                    for sx in cands:
                        bt[sx["type"]] += 1
                    skip_days.append({
                        "d": d, "n_cand": len(cands), "n_take": 0,
                        "n_block": len(cands), "room": room, "n_live": len(live),
                        "by_type": dict(bt), "why": "gross",
                    })
                cands = []
            picks = []
            for s in cands:
                b = bars.get(s["code"])
                if b is None:
                    continue
                ei = b.i_of(d)
                if ei is None or ei <= 0:
                    continue
                # 尺度统一：复权价一律除以 f[ei]，于是建仓价 == 当日真实价，
                # 与持仓盯市用的 px/scale 同一把尺子（首版两边没对齐，收益被放大 3 倍）。
                o = b.c[ei] if entry_at_close else b.o[ei]
                if o <= 0:
                    continue
                if P["limit_gate"] and ei > 0 and b.c[ei - 1] > 0:
                    lim = limit_pct(s["code"], s["name"])
                    # 开盘就顶在涨停价上：排队也未必成交，一律当买不进。
                    # 0.999 是容差（涨停价四舍五入到分）。
                    if o >= b.c[ei - 1] * (1 + lim) * 0.999:
                        P["_n_limit_block"] = P.get("_n_limit_block", 0) + 1
                        continue
                a0 = b.atr[ei]
                if not a0 or a0 <= 0:
                    continue
                picks.append((s, b, ei, o, a0 / o))
            if picks:
                if P["sizing"] == "risk":
                    # 风险预算：单笔亏到止损位时最多亏 净值×risk_pct。
                    # 止损距离取 max(2ATR/px, risk_floor)——结构位的语义更准，
                    # 但这里要保持"事先可算"，所以用 ATR 的 2 倍作距离代理。
                    iw = []
                    for _, _, _, _, v in picks:
                        dist = max(2.0 * v, P["risk_floor"])
                        iw.append(min(P["max_w"], P["risk_pct"] / dist))
                    # 现金不够时等比压缩（否则最后几只会吃掉全部现金）
                    need = sum(iw) * eq0
                    scale = min(1.0, avail / need) if need > 0 else 1.0
                    iw = [w * scale for w in iw]
                    amts = [eq0 * w for w in iw]
                else:
                    iw = [1.0 / max(v, 1e-4) for _, _, _, _, v in picks]
                    sw = sum(iw)
                    alloc = min(budget, avail)
                    amts = [alloc * (w / sw) for w in iw]
                n_skip_lot = 0
                for (s, b, ei, o, _), amt in zip(picks, amts):
                    amt0 = amt
                    amt = min(amt, mx * eq0, avail)
                    if amt < amt0 * 0.5:
                        n_locked_block += 1
                    q = int(amt / (o * (1 + P["buy_cost"])) // lot) * lot
                    if s["code"].startswith(("688", "689")):
                        q = int(amt / (o * (1 + P["buy_cost"])) // 200) * 200
                    if q <= 0:
                        n_skip_lot += 1
                        continue
                    cost = q * o * (1 + P["buy_cost"])
                    if cost < P["min_ticket"] * eq0:
                        n_skip_lot += 1
                        continue
                    if cost > avail:
                        continue
                    cash -= cost
                    avail -= cost
                    st = None
                    if P["sl_mode"] == "struct" and CS is not None:
                        try:
                            st = CS.struct_level(s["code"], d, o)
                        except Exception:
                            st = None
                    live.append({
                        "st": st,
                        "code": s["code"], "name": s["name"], "type": s["type"],
                        "sig_dt": s["sig_dt"], "detect": s["detect"], "lag": s["lag"],
                        "entry_date": d, "entry_i": ei, "entry_px": o,
                        "qty": q, "amt": cost,
                        "exit_i": ei + hold,
                        "exit_d": b.dates[ei + hold] if ei + hold < len(b.dates) else None, "peak": o, "max_fav": 0.0, "max_adv": 0.0,
                        "path": [], "alerts": 0,
                    })

        # ③ 收盘盯市 + 逐日位（SL/TP 只提示，不改账）
        mv = 0.0
        for p in live:
            b = bars[p["code"]]
            jj = idx_on(b, d)
            px = b.px_at(jj, p["entry_i"])
            mv += p["qty"] * px
            p["peak"] = max(p["peak"], px)
            r = px / p["entry_px"] - 1
            p["max_fav"] = max(p["max_fav"], r)
            p["max_adv"] = min(p["max_adv"], r)
            if len(p["path"]) <= hold:
                p["path"].append({"d": d, "px": round(px, 3), "ret": round(r * 100, 2)})
            # 位：只用"截至上一交易日"的高点链（guardian v1.0.4 as-of 口径）
            hi_prior = p.get("hi_prior", p["entry_px"])
            a = b.atr[jj] or 0
            if a > 0:
                sl_atr = max(hi_prior - P["sl_atr"] * a, p["entry_px"] * P["sl_floor"])
                tp = hi_prior - P["tp_atr"] * a
                # 结构位优先：中枢 ZG / 笔低点由结构定义，ATR 只是波动率兜底
                st = p.get("st")
                if P["sl_mode"] == "struct" and st and st.get("sl")                         and st["sl"] > px * (1 - P["sl_max_dist"]):
                    sl, sl_kind, sl_basis = st["sl"], st["kind"], st["basis"]
                elif P["sl_mode"] == "struct" and st and st.get("sl"):
                    # 结构位在远方（趋势中笔低点可能很差几个价位），退回 ATR
                    sl, sl_kind = sl_atr, "atr_far"
                    sl_basis = "结构位 %.2f 距现价 %.0f%% 过远，改用 ATR 兜底" % (
                        st["sl"], (px / st["sl"] - 1) * 100)
                else:
                    sl, sl_kind, sl_basis = sl_atr, "atr", "ATR：持有期高点 − 2×ATR14"
                lvl = "🟢"
                if px <= sl:
                    lvl = "🟠"
                elif px - sl < 0.03 * px:
                    lvl = "🟡"
                elif px <= tp and hi_prior > p["entry_px"]:
                    lvl = "✅"
                p["sl"], p["tp"], p["atr"], p["lvl"] = sl, tp, a, lvl
                p["px"] = px
                p["sl_atr"], p["sl_kind"], p["sl_basis"] = sl_atr, sl_kind, sl_basis
                if lvl != "🟢":
                    p["alerts"] += 1
            p["hi_prior"] = max(hi_prior, px)

            # 标签出场：收盘算等级，标记次日开盘卖（不在收盘成交，避免前视）
            if P["exit_mode"] != "hold5" and not p.get("tag_sell"):
                T2, M2, P2 = tmp_tags(b, jj, p["entry_px"], px, p["peak"],
                                      p["max_fav"] > 0.08)
                lv2, _act2 = action_of(T2, M2, P2)
                if P["exit_mode"] == "tag_half":
                    # 候风 §五 唯一为正的口径：橙色卖一半 + 剩余保本；红色全清
                    if lv2 == "red":
                        p["tag_sell"], p["tag_kind"] = True, "red"
                    elif lv2 == "orange" and not p.get("half_done"):
                        p["tag_sell"], p["tag_kind"], p["tag_half"] = True, "orange", True
                elif P["exit_mode"] == "tag_red":
                    if lv2 == "red":
                        p["tag_sell"], p["tag_kind"] = True, "red"
                else:
                    if lv2 in ("red", "orange"):
                        p["tag_sell"], p["tag_kind"] = True, lv2

        eq = cash + mv
        prev = nav_rows[-1]["equity"] if nav_rows else cap0
        nav_rows.append({
            "d": d, "equity": round(eq, 2), "cash": round(cash, 2), "mv": round(mv, 2),
            "n_pos": len(live), "util": round(mv / eq, 4) if eq > 0 else 0.0,
            "ret": round(eq / prev - 1, 6) if prev else 0.0,
            "nav": round(eq / cap0, 6),
        })

    # 尾部在途（未到 exit_i）也入 trades，标记 open
    for p in live:
        b = bars[p["code"]]
        jj = len(b.dates) - 1
        px = b.px_at(jj, p["entry_i"])
        p["exit_date"] = None
        p["exit_px"] = px
        p["exit_reason"] = "open"
        p["ret"] = px / p["entry_px"] - 1 - P["buy_cost"]
        p["hold_days"] = jj - p["entry_i"]
        trades.append(p)
    return nav_rows, trades, live, skipped_room, skip_days


def stats_of(nav_rows, trades):
    if not nav_rows:
        return {}
    eqs = [r["equity"] for r in nav_rows]
    rets = [r["ret"] for r in nav_rows[1:]]
    peak, mdd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (statistics.mean(rets) / sd * math.sqrt(244)) if sd > 0 else 0.0
    closed = [t for t in trades if t["exit_reason"] != "open"]
    rs = [t["ret"] for t in closed]
    return {
        "total_return": round(eqs[-1] / P["capital"] - 1, 6),
        "max_drawdown": round(mdd, 6),
        "sharpe": round(sharpe, 3),
        "daily_win_rate": round(sum(1 for x in rets if x > 0) / len(rets), 4) if rets else 0,
        "n_days": len(nav_rows),
        "n_trades": len(closed),
        "n_open": len(trades) - len(closed),
        "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 4) if rs else 0,
        "avg_ret": round(statistics.mean(rs), 6) if rs else 0,
        "median_ret": round(statistics.median(rs), 6) if rs else 0,
        "avg_hold": round(statistics.mean([t["hold_days"] for t in closed]), 2) if closed else 0,
        "avg_util": round(statistics.mean([r["util"] for r in nav_rows]), 4),
        "avg_pos": round(statistics.mean([r["n_pos"] for r in nav_rows]), 2),
    }


# ---------------------------------------------------------------- 组合上下文
def build_context(days, sig_path=None):
    """一次加载全部数据，供引擎与对照盘复用（避免每个组合重读 489 只 parquet）。

    sig_path 可换成 `chanlun_hist_signals.json`（hf_hist.py 产出的 871 天长历史），
    让「换窗口」这一类检验有 3.5 年可比，而不是只在 161 天里打转。
    """
    axis = load_axis()
    asof = axis[-1]
    win = axis[-days:] if len(axis) > days else axis
    keep_from = win[0]

    full = json.load(open(sig_path or FULL, encoding="utf-8"))
    rows = full.get("rows") or []
    bad = load_index_blacklist()
    names = load_names()
    ind = load_industry(asof)

    plans, sstat = build_signals(rows, axis, bad, names, keep_from)
    # 卖点信号同时备好：空头账本要用同一批 bars，不能重复读 489 个 parquet
    splans, sstat_s = build_signals(rows, axis, bad, names, keep_from, SELL_TYPES)
    codes = sorted({s["code"] for v in plans.values() for s in v}
                   | {s["code"] for v in splans.values() for s in v})
    bars, miss = {}, []
    for c in codes:
        b = load_bars(c, keep_from)
        if b is None:
            miss.append(c)
        else:
            bars[c] = b
    plans = {d: [s for s in v if s["code"] in bars] for d, v in plans.items()}
    plans = {d: v for d, v in plans.items() if v}
    splans = {d: [s for s in v if s["code"] in bars] for d, v in splans.items()}
    splans = {d: v for d, v in splans.items() if v}
    return {"axis": axis, "asof": asof, "win": win, "keep_from": keep_from,
            "plans": plans, "splans": splans, "bars": bars, "names": names,
            "ind": ind, "miss": miss, "sstat": sstat, "sstat_s": sstat_s}


def filter_plans(plans, only=None):
    """按买点类型过滤建仓计划。only=None 表示不筛（全量）。"""
    if not only:
        return plans
    only = set(only)
    out = {}
    for d, v in plans.items():
        xs = [s for s in v if s["type"] in only]
        if xs:
            out[d] = xs
    return out


def run_once(ctx, only=None, entry_at_close=False, collect_skip=False):
    """跑一个组合，返回 (nav_rows, trades, live, skipped_room, plans, skip_days)。"""
    plans = filter_plans(ctx["plans"], only)
    nav_rows, trades, live, skipped_room, skip_days = simulate(
        ctx["axis"], plans, ctx["bars"], entry_at_close=entry_at_close,
        collect_skip=collect_skip)
    return nav_rows, trades, live, skipped_room, plans, skip_days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=P["days"])
    ap.add_argument("--only", default="",
                    help="只做指定买点，逗号分隔，如 1买,2买；留空=全部")
    ap.add_argument("--degrade", action="store_true",
                    help="退化副本：建仓用检出日收盘（前视），自检应报红")
    ap.add_argument("--sl-mode", default=P["sl_mode"], choices=("atr", "struct"),
                    help="止损口径：struct=日线中枢 ZG/笔低点优先，ATR 兜底")
    ap.add_argument("--sizing", default=P["sizing"], choices=("inv_vol", "risk"),
                    help="建仓权重：risk=按风险预算定仓位")
    ap.add_argument("--risk-pct", type=float, default=P["risk_pct"],
                    help="风险预算：单笔最大亏损占净值比例（默认 1%%）")
    ap.add_argument("--exit-mode", default=P["exit_mode"],
                    choices=("hold5", "tag_all", "tag_red", "tag_half"),
                    help="出场：tag_all=红/橙次日开盘卖；tag_red=只红")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--out-trades", default=OUT_TRADES)
    ap.add_argument("--gross", type=float, default=P["gross_cap"],
                    help="净敞口上限 mv/equity（1.0=关，基线；0.5=最多半仓）")
    ap.add_argument("--limit-gate", action="store_true",
                    help="涨停不可买（长历史全市场扫描必须开，否则一字板算成收益）")
    a = ap.parse_args()

    P["sl_mode"] = a.sl_mode
    P["sizing"] = a.sizing
    P["risk_pct"] = a.risk_pct
    P["exit_mode"] = a.exit_mode
    P["gross_cap"] = a.gross
    P["limit_gate"] = a.limit_gate
    P.pop("_n_limit_block", None)

    ctx = build_context(a.days)
    axis, asof, win, bars = ctx["axis"], ctx["asof"], ctx["win"], ctx["bars"]
    names, ind, miss, sstat = ctx["names"], ctx["ind"], ctx["miss"], ctx["sstat"]

    only = None
    if a.only:
        only = [t.strip() for t in a.only.split(",") if t.strip()]
        bad_t = [t for t in only if t not in BUY_TYPES]
        if bad_t:
            print("未知买点类型：%s（可选 %s）" % (bad_t, "/".join(BUY_TYPES)))
            return 2

    nav_rows, trades, live, skipped_room, plans, skip_days = run_once(
        ctx, only=only, entry_at_close=a.degrade, collect_skip=True)

    # 当日持仓快照（含 SL/TP/TMP）
    asof_i = len(axis) - 1
    pos_rows = []
    was8 = {}
    for p in live:
        b = bars[p["code"]]
        jj = idx_on(b, axis[asof_i])
        px = b.px_at(jj, p["entry_i"])
        T, M, Pm = tmp_tags(b, jj, p["entry_px"], px, p["peak"],
                            p["max_fav"] > 0.08)
        lv, act = action_of(T, M, Pm)
        pos_rows.append({
            "code": p["code"], "name": p["name"] or names.get(p["code"], ""),
            "ind": ind.get(p["code"], "未分类"), "type": p["type"],
            "sig_dt": p["sig_dt"], "detect": p["detect"], "lag": p["lag"],
            "entry_date": p["entry_date"], "entry_px": round(p["entry_px"], 3),
            "qty": p["qty"], "amt": round(p["amt"], 2),
            "px": round(px, 3), "pnl_pct": round((px / p["entry_px"] - 1) * 100, 2),
            "pnl_amt": round(p["qty"] * (px - p["entry_px"]), 2),
            "peak": round(p["peak"], 3),
            "hi_prior": round(p.get("hi_prior", p["entry_px"]), 3),
            "atr": round(p.get("atr") or 0, 3),
            "sl": round(p.get("sl") or 0, 3), "tp": round(p.get("tp") or 0, 3),
            "sl_atr": round(p.get("sl_atr") or 0, 3),
            "sl_kind": p.get("sl_kind", "atr"),
            "sl_basis": p.get("sl_basis", ""),
            "zg": (p.get("st") or {}).get("zg"),
            "zd": (p.get("st") or {}).get("zd"),
            "dist_sl": round((px / p["sl"] - 1) * 100, 2) if p.get("sl") else None,
            "dist_tp": round((p["tp"] / px - 1) * 100, 2) if p.get("tp") else None,
            "lvl": p.get("lvl", "🟢"), "alerts": p["alerts"],
            "held": jj - p["entry_i"] + 1, "left": max(0, p["exit_i"] - jj),
            "T": T, "M": M, "P": Pm, "act": act, "act_txt": act_txt(lv, act),
            "max_fav": round(p["max_fav"] * 100, 2),
            "max_adv": round(p["max_adv"] * 100, 2),
            "path": p["path"],
        })
    pos_rows.sort(key=lambda x: {"🟠": 0, "🟡": 1, "✅": 2, "🟢": 3}.get(x["lvl"], 9))

    # 当日动作
    today = axis[-1]
    buys = [t for t in trades if t["entry_date"] == today]
    sells = [t for t in trades if t["exit_date"] == today]

    idxc = load_index_close()
    idx0 = next((v for k, v in idxc.items() if k >= win[0]), None)
    idx1 = idxc.get(today)

    st = stats_of(nav_rows, trades)
    st["idx_ret"] = round(idx1 / idx0 - 1, 6) if idx0 and idx1 else None
    st["capital"] = P["capital"]
    if st.get("total_return") is not None and st.get("idx_ret") is not None:
        st["alpha"] = round(st["total_return"] - st["idx_ret"], 6)

    trade_rows = []
    for t in trades:
        trade_rows.append({
            "code": t["code"], "name": t["name"] or names.get(t["code"], ""),
            "ind": ind.get(t["code"], "未分类"), "type": t["type"],
            "sig_dt": t["sig_dt"], "detect": t["detect"], "lag": t["lag"],
            "entry_date": t["entry_date"], "entry_px": round(t["entry_px"], 3),
            "qty": t["qty"], "amt": round(t["amt"], 2),
            "exit_date": t["exit_date"], "exit_px": round(t["exit_px"], 3)
            if t["exit_px"] is not None else None,
            "exit_reason": t["exit_reason"],
            "ret": round(t["ret"] * 100, 2),
            "hold_days": t["hold_days"],
            "max_fav": round(t["max_fav"] * 100, 2),
            "max_adv": round(t["max_adv"] * 100, 2),
            "path": t["path"],
        })

    out = {
        "asof": asof,
        "meta": {
            "engine": "hf_book v1 · 候风口径",
            "only": only or list(BUY_TYPES),
            "window": [win[0], asof],
            "days": len(nav_rows),
            "params": P,
            "rule_src": {
                "hold5": "M2_FROZEN.json / M2_PAPER_README.md",
                "inv_vol_top30": "paper_daily.py:69-73",
                "chain_nav": "paper_daily.py:84-103",
                "sl_tp": "guardian_agent_v1.0.4.py:126-127 (as-of 上日收盘)",
                "tmp_tags": "sell_methodology_v1.md:23-55",
                "no_auto_exit": "sell_methodology_v1.md §五（机器执行一律为负）",
            },
            "caveat": "SL/TP 只提示不执行；净值由固定 5 日时钟产生",
            "signal_stat": sstat,
            "n_codes": len(bars),
            "skipped_room": skipped_room,
            "exit_lock_days": P["exit_lock_days"],
            "rank": P["rank"],
            "n_missing_bars": len(miss),
        },
        "stats": st,
        "nav": nav_rows,
        "positions": pos_rows,
        "today": {
            "date": today,
            "buy": [{"code": t["code"], "name": t["name"], "type": t["type"],
                     "px": round(t["entry_px"], 3), "qty": t["qty"]} for t in buys],
            "sell": [{"code": t["code"], "name": t["name"], "type": t["type"],
                      "px": round(t["exit_px"], 3) if t["exit_px"] else None,
                      "qty": t["qty"],
                      "ret": round(t["ret"] * 100, 2), "reason": t["exit_reason"]}
                     for t in sells],
        },
        "by_type": by_type(trade_rows),
        "by_lvl": by_lvl(pos_rows),
        "skip_days": skip_days,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(a.out_trades, "w", encoding="utf-8", newline="") as f:
        json.dump({"asof": asof, "rows": trade_rows}, f, ensure_ascii=False,
                  separators=(",", ":"))

    print("asof=%s 窗 %s~%s  %d 日" % (asof, win[0], asof, len(nav_rows)))
    print("信号 %d 笔 → 建仓 %d 笔（缺bar %d，作废 %d）" % (
        sstat["rows"], len(trade_rows), len(miss), sstat["no_detect"] + sstat["too_old"]))
    print("净值 %.2f%%  回撤 %.2f%%  夏普 %.2f  笔胜率 %.1f%%  均持 %.1f 日  仓位 %.1f%%" % (
        st["total_return"] * 100, st["max_drawdown"] * 100, st["sharpe"],
        st["win_rate"] * 100, st["avg_hold"], st["avg_util"] * 100))
    print("持仓 %d 只  今日买 %d 卖 %d" % (len(pos_rows), len(buys), len(sells)))
    for r in pos_rows[:8]:
        print("  %s %-6s %s %s  浮盈%+.2f%%  现价%.2f  SL %.2f(%+.1f%%)  TP %.2f(%+.1f%%)  %s" % (
            r["lvl"], r["code"], r["type"], r["name"][:6], r["pnl_pct"], r["px"],
            r["sl"], r["dist_sl"] or 0, r["tp"], r["dist_tp"] or 0, r["act_txt"]))
    print("→ %s" % a.out)
    return 0


def simulate_short(axis, plans, bars, entry_at_close=False):
    """镜像空头账本：卖点信号 → T+1 开盘融券卖出 → 持有期收盘买回。

    记账走复式，不能只加现金：
        开仓：cash += q×o×(1−sell_cost)，同时挂负债 q×o
              → 权益 = cash − 空头市值 = 原权益 − 手续费（不凭空多钱）
        盯市：权益 = cash − Σ q×px_now
        平仓：cash −= q×exit_px×(1+buy_cost)，负债移除
    单笔收益（对名义本金）= (entry_px − exit_px)/entry_px − 双边成本。

    这是**研究腿**，不是可执行腿：A 股散户没有券源，融券余额也覆盖不到
    这些标的。它回答的是"卖点信号方向性准不准"，不是"这样能赚多少"。
    """
    hold = P["hold"]
    lot = P["lot"]
    mx = P["max_w"]
    cap0 = P["capital"]
    cash = cap0
    live = []
    nav_rows = []
    trades = []
    i0 = None
    for i, d in enumerate(axis):
        if d in plans:
            i0 = i
            break
    if i0 is None:
        return [], [], []

    for i in range(i0, len(axis)):
        d = axis[i]

        # ① 到期买回（收盘）
        keep = []
        for p in live:
            b = bars[p["code"]]
            if p["exit_d"] is not None and d >= p["exit_d"]:
                xi = b.i_of(p["exit_d"])
                if xi is None:
                    xi = b.i_of(d)
                if xi is None:
                    keep.append(p)
                    continue
                px = b.px_at(xi, p["entry_i"])
                cash -= p["qty"] * px * (1 + P["buy_cost"])
                p["exit_date"] = b.dates[xi]
                p["exit_px"] = px
                p["exit_reason"] = "hold%d" % hold
                # 空头收益 = (卖价 − 买回价)/卖价。写成 entry/exit−1 是倒数，
                # 跌幅越大虚增越厉害（688496 那笔 −37% 会算成 +59%），首版踩过。
                p["ret"] = 1 - px / p["entry_px"] - (P["buy_cost"] + P["sell_cost"])
                p["hold_days"] = xi - p["entry_i"]
                trades.append(p)
            else:
                keep.append(p)
        live = keep

        # ② 开盘卖出开仓
        cands = plans.get(d) or []
        if cands:
            prev_d = axis[i - 1] if i > 0 else d
            liab0 = sum(p["qty"] * bars[p["code"]].px_at(
                idx_on(bars[p["code"]], prev_d), p["entry_i"]) for p in live)
            eq0 = cash - liab0
            budget = eq0 / hold
            cands = sorted(cands, key=rank_key(P["rank"]))[: P["top_n"]]
            room = max(0, P["max_pos"] - len(live))
            cands = cands[:room]
            picks = []
            for sg in cands:
                b = bars.get(sg["code"])
                if b is None:
                    continue
                ei = b.i_of(d)
                if ei is None or ei <= 0:
                    continue
                o = b.c[ei] if entry_at_close else b.o[ei]
                if o <= 0:
                    continue
                a0 = b.atr[ei]
                if not a0 or a0 <= 0:
                    continue
                picks.append((sg, b, ei, o, a0 / o))
            if picks:
                iw = [1.0 / max(v, 1e-4) for _, _, _, _, v in picks]
                sw = sum(iw)
                # 空头没有"现金不够"的问题，受限的是总敞口
                gross_room = max(0.0, P["short_gross"] * eq0 - liab0)
                alloc = min(budget, gross_room)
                amts = [alloc * (w / sw) for w in iw]
                for (sg, b, ei, o, _), amt in zip(picks, amts):
                    amt = min(amt, mx * eq0)
                    q = int(amt / (o * (1 - P["sell_cost"])) // lot) * lot
                    if sg["code"].startswith(("688", "689")):
                        q = int(amt / (o * (1 - P["sell_cost"])) // 200) * 200
                    if q <= 0:
                        continue
                    notional = q * o
                    if notional < P["min_ticket"] * eq0:
                        continue
                    cash += notional * (1 - P["sell_cost"])
                    live.append({
                        "code": sg["code"], "name": sg["name"], "type": sg["type"],
                        "sig_dt": sg["sig_dt"], "detect": sg["detect"], "lag": sg["lag"],
                        "entry_date": d, "entry_i": ei, "entry_px": o,
                        "qty": q, "amt": notional,
                        "exit_i": ei + hold,
                        "exit_d": b.dates[ei + hold] if ei + hold < len(b.dates) else None,
                        "peak": o, "path": [],
                    })

        # ③ 收盘盯市
        liab = 0.0
        for p in live:
            b = bars[p["code"]]
            jj = idx_on(b, d)
            px = b.px_at(jj, p["entry_i"])
            liab += p["qty"] * px
            if len(p["path"]) <= hold:
                p["path"].append({"d": d, "px": round(px, 3),
                                  "ret": round((1 - px / p["entry_px"]) * 100, 2)})
        eq = cash - liab
        prev = nav_rows[-1]["equity"] if nav_rows else cap0
        nav_rows.append({
            "d": d, "equity": round(eq, 2), "cash": round(cash, 2), "mv": round(liab, 2),
            "n_pos": len(live), "util": round(liab / eq, 4) if eq > 0 else 0.0,
            "ret": round(eq / prev - 1, 6) if prev else 0.0,
            "nav": round(eq / cap0, 6),
        })

    for p in live:
        b = bars[p["code"]]
        jj = len(b.dates) - 1
        px = b.px_at(jj, p["entry_i"])
        p["exit_date"] = None
        p["exit_px"] = px
        p["exit_reason"] = "open"
        p["ret"] = 1 - px / p["entry_px"] - P["sell_cost"]
        p["hold_days"] = jj - p["entry_i"]
        trades.append(p)
    return nav_rows, trades, live


def combine_nav(long_nav, short_nav, w):
    """把空头账本按 w 叠到多头净值上：nav = long + w·(short − 1)。

    这是**叠加**口径：多头满仓不动，另外拿 w 份名义本金做空（需要额外保证金）。
    w=0 退回多头；w=1 等于「多头 + 同等名义空头」。
    另有一种**腾挪**口径（从多头让出 w 的资金）：
        nav = (1−w)·long + w·short
    两者不能混用，hf_phase3._comb 里分开算。
    """
    if not short_nav:
        return [r["nav"] for r in long_nav]
    m = {r["d"]: r["nav"] for r in short_nav}
    return [r["nav"] + w * (m.get(r["d"], 1.0) - 1.0) for r in long_nav]


def nav_stats(navs):
    """从净值序列算 总收益/最大回撤。"""
    if not navs:
        return {}
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return {"total": navs[-1] - 1.0, "mdd": mdd, "n": len(navs)}


def act_txt(lv, act):
    return {"red": "立即复核", "orange": "减半保本", "yellow": "明日盯",
            "green": "持有"}.get(lv, act)


def by_type(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["type"]].append(r["ret"])
    out = {}
    for k, v in g.items():
        out[k] = {"n": len(v), "mean": round(statistics.mean(v), 2),
                  "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}
    return dict(sorted(out.items()))


def by_lvl(pos):
    g = defaultdict(int)
    for p in pos:
        g[p["lvl"]] += 1
    return g


if __name__ == "__main__":
    sys.exit(main())
