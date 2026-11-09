# -*- coding: utf-8 -*-
"""缠论结构标注稳定性 / 可复现性度量（标注工具的质量指标）。

为什么换指标
------------
七轮审计已判定缠论结构不是收益信号（deliverables/2026-11-05_三线并行_v6_
口径审计与跨市场判定.md），系统定位改为「结构标注工具」。标注工具的 SLO 不是
收益显著性，而是**稳定性**：d0 日画出来的笔 / 中枢 / 买卖点，过 k 个交易日
还在不在同一个位置。本模块只量这件事，不碰收益、不碰回测。

重放范式（与 chanlun_conformance 同构，结构性禁止未来数据）
----------------------------------------------------------
对每个交易日 d0，**只喂 `bars[:d0+1]`** 跑 analyze（截断重放），得到 d0 收盘
时真正可见的标注；再在 d0+k 的截断结果里查该标注是否仍存在。任何一天的标注
都只由当天及之前的 K 线推出，不存在未来数据；同一标的的所有截断快照在**一次
逐日 passthrough** 里建好并缓存，避免重复计算（n 根 K 线只跑 n-120 次 analyze）。

存活判定口径（三条，逐条可单测）
--------------------------------
· 笔  ：同一对端点 (s_dt, e_dt) 仍在 bis 列表里（日期对是笔的身份）。
· 中枢：同 (zg, zd) 仍成立，相对容差 1e-9，即
        |x-y| <= 1e-9 * max(|x|, |y|)（两数皆为 0 时要求严格相等）。
· 信号：同 (type, dt) 仍在 points 里（points = analyze 输出的 signals 列表；
        仓库字段名是 signals，"points" 是任务书用词）。
注：笔的端点价由分型唯一确定，键 (s_dt, e_dt) 命中即端点价相同，故不另设
    「仅价格漂移」类别（实测 0 次，tautology）。

双口径（对账 conformance 需要）
------------------------------
A = chanlun_bi.find_signals（已废弃对照口径，依赖 MACD 面积 + 随行情延伸的
    中枢 e_dt，见 chanlun_bi.py 的 SIGNALS_DEPRECATED 声明）；
B = chanlun_signal.find_signals（权威口径，v6 审计钦定）。
两者在同一份 chanlun_bi.analyze 结果上求，结构只算一遍，A/B 各自计时无额外
结构开销。

两个切面（同一份数据，两种分母）
--------------------------------
· 主口径 all_d0：任务书口径，每个交易日 d0 的每条标注都记一次观测。结构越老
  被计越多次，因此偏向「已定型结构」，数值偏乐观，是「打开图表今天看到的标注
  有多稳」。
· 首见队列 cohort：每个结构只在其第一次出现时记一次。实测发现每根笔出生时
  必然是末笔（find_bis 只 append），所以此队列恰好等价于「新画出来的一笔，
  在 k 日后是否已被锁定」。「一笔画出来之后还在不在」问的就是这个。
两者同时给，任何一侧单独汇报都会误导。
⚠ cohort 的 PASS/WARN 列沿用 all_d0 的 PASS_MIN，**只作同尺对照，不是独立
  判据**：PASS_MIN 的推导前提是「已定型结构」（判据 ①），而首见队列刻意包含
  出生即末笔的未定型结构，WARN 是模型的预期结果而非缺陷。cohort 的独立判据
  留给 v2（需先积累 ≥2 次同 seed 复跑的基线）。

与 conformance「44% 消失」基线的对账（重要）
------------------------------------------
本模块用同一套截断重放复算 conformance 的两个量（见 baseline_reconcile）：
  · lag0（dt 当天是否可见）：A 0.0000、B 0.0000 —— 与「0/229（A）、0/299（B）」
    完全一致；
  · 确认滞后中位：B 实测 16.0 日 vs conformance 16.5 日（差 0.5 日）；A 实测
    1 日 vs 2 日（差 1 日，样本更小）。
结论：「44% 消失」= 1 - 集合级 Jaccard，**不是** 44% 的信号真的消失。理由是
定义性的：在线并集包含最后一天，而最后一天的可见集就是全历史集，故
S_full ⊆ U 恒成立，「全历史信号从未在线出现」的比例按定义恒为 0（本模块把
这个量记作 full_lost，它不是实测发现，是同义反复）。除掉这一项后差额只剩
一个方向：幽灵信号 —— 在线出现过、定型后不存在的信号（A 472/997 = 47.3%，
B 25/804 = 3.1%）。Jaccard 对称，把幻觉与丢失算进同一个差额；所以它只能当
「标注噪声总量」用，不能读成存活率。
成因指向算法可复核的两处差异（非拟合推断）：A 的三类把锚点取在
`bis[idx[pv["e_dt"]] - 1]`，即中枢的离开笔，而 pv["e_dt"] 会随中枢延伸整体
平移（find_pivots 里 end_j 持续推进），锚一移、信号就换位；B 的判定边界是
不动的 (zg, zd) 与线段极值，故稳定（集合级 0.97 vs A 0.53）。

判据出处与假设（PASS_MIN 里每个数字都要能追到下面三条之一）
----------------------------------------------------------
① 硬上界（可证伪的代码事实，不是经验值）
   chanlun_bi.find_bis 只修 `bis[-1]` 的 e_dt/e_price，已定型的笔不会被回溯
   改写；find_pivots 的 zg/zd 在成形时一次定死，之后延伸只改 e_dt/n_bi。
   ⇒ 非末笔（bi_inner）存活率理论上恒为 1.000，实测 < 1.000 说明「定型结构
     可被回溯改写」这条假设被证伪，属 bug 级告警（判定带 HARD 标记），不是
     行情噪声。
② 外部锚：chanlun_conformance 的 B 口径集合级 Jaccard = 0.987（路径依赖
   1.3%）是仓库內唯一已发布的稳定性实测。取 1 日容差 = 2%（1.3% 向上取整到
   整数百分点），即 TOL_BASE = 0.02。
③ 时间累积假设：标注翻掉率按随机游走式累积 ~ sqrt(k)，故
   TOL(k) = TOL_BASE * sqrt(k)，结构层 PASS_MIN = 1 - TOL(k)：
     k=1 → 0.980   k=5 → 0.955   k=20 → 0.911
   信号层是笔/线段/中枢的复合谓词，上游任一层翻掉都会销毁信号，按 union
   bound 取 2 倍容差（PASS_MIN = 1 - 2*TOL(k)）：
     k=1 → 0.960   k=5 → 0.911   k=20 → 0.821
   —— 这是**保守下界近似**（真实复合破坏概率 <= 各层之和），不是拟合值。
④ 使用场景兜底：人工据图决策时，一周内翻掉 >9%、一月内翻掉 >18% 的标注会
   直接变成误导，故信号层 @5 不得低于 0.90、@20 不得低于 0.80（③ 的数值
   恰好压在线上，两者一致即视为判据自洽；若将来要放宽，先改 ② 的锚）。

★ 这是**首版判据（v1）**，未做任何参数搜索。首次 `--limit 50 --workers 8`
  的实跑值即为其校准基线；后续同 seed 复跑只与这份 JSON 比对，不再回头改
  阈值去解释历史（避免 p-hacking）。
★ 观测非独立：同一结构会在多个 d0 被重复计数（逐日重放的固有口径，与
  chanlun_conformance 一致）。比率是加权平均，不可当独立样本做显著性检验。
  为此另给「首见队列」（cohort_first_seen）：每个结构只在其*第一次*出现的
  d0 记一次观测，是逐结构无重复的版本，作交叉校验。

产物
----
data/chanlun_annotation_stability.json
  survival            按 结构类型 × k 的存活率 / 样本数 / 半衰期 / 判定
  cohort_first_seen   首见队列（无重复计数）
  signal_by_type      按买卖点类型的拆分
  baseline_reconcile  与 conformance 44% 消失基线的对账（A/B 集合级 Jaccard）

用法
----
    python chanlun_annotation_stability.py --limit 50 --workers 8
    python chanlun_annotation_stability.py --limit 8 --seed 7     # 冒烟
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
OUT = os.path.join(DATA, "chanlun_annotation_stability.json")

KS = (1, 5, 20)          # 观测视野（交易日）
SEED = 42                # 抽样种子，固定后样本可复现
LIMIT = 50
WORKERS = 8
MIN_WARMUP = 120         # d0 起点：给包含处理/分型/笔足够上下文
MIN_BARS = 250           # 标的门槛：次新股与残表剔除
PIVOT_REL_TOL = 1e-9     # 中枢 zg/zd 相对容差（任务书口径）
INDEX_CODES = {"000001.SH"}  # data/ashare 里混入的上证指数，非「标的」

# A 口径（chanlun_bi.find_signals）的 t 值 → 与 B 口径同名的中文类型
A_TYPE = {"b1": "1买", "s1": "1卖", "b2": "2买", "s2": "2卖", "b3": "3买", "s3": "3卖"}

# ── PASS/WARN 判据（出处见模块 docstring ①②③④，一数一源）──────────────
TOL_BASE = 0.02  # ① 1 日容差 = conformance B 路径依赖 1.3% 向上取整
PASS_MIN = {
    # 结构层 = 1 - TOL_BASE*sqrt(k)
    "bi":       {1: 0.980, 5: 0.955, 20: 0.911},
    "pivot":    {1: 0.980, 5: 0.955, 20: 0.911},
    # 信号层 = 1 - 2*TOL_BASE*sqrt(k)（复合谓词，2 倍容差）
    "signal":   {1: 0.960, 5: 0.911, 20: 0.821},
}
# 硬上界：非末笔理论上恒等存活，任何缺失即 bug 级告警
HARD_KINDS = {"bi_inner": 1.000}
# 末笔本就可被后续分型修正，只作诊断，不参与判定
DIAG_ONLY = {"bi_tail", "signal_A"}


# ══════════════════════════ 纯函数（单测入口） ══════════════════════════
def bi_key(b):
    """笔的身份 = 端点日期对。"""
    return (b["s_dt"], b["e_dt"])


def signal_key(s):
    """信号的身份 = (类型, 结构端点日)。"""
    return (str(s["dt"]), s["type"])


def pivot_close(zg, zd, zg2, zd2, rel_tol=PIVOT_REL_TOL):
    """两个中枢区间是否等价（相对容差，两数皆 0 时退化为严格相等）。"""
    for a, b in ((zg, zg2), (zd, zd2)):
        if a == b:
            continue
        scale = max(abs(a), abs(b))
        if scale == 0.0 or abs(a - b) > rel_tol * scale:
            return False
    return True


def pivot_survives(pv, later_pivots, rel_tol=PIVOT_REL_TOL):
    """中枢存活：later 里存在同 (zg, zd) 的中枢（容差口径）。"""
    return any(pivot_close(pv[0], pv[1], q[0], q[1], rel_tol) for q in later_pivots)


def bi_survives(key, later_keys):
    """笔存活：同一对端点仍在 later 的 bis 里。"""
    return key in later_keys


def rate(hit, base):
    return (float(hit) / base) if base else None


def half_life(rates, ks=KS, cut=0.5):
    """存活率首次跌破 cut 的 k；视野内未跌破返回 None（删失，记 ">20"）。"""
    for k in ks:
        r = rates.get(k)
        if r is not None and r < cut:
            return k
    return None


def verdict(kind, rates, limits=None, ks=KS):
    """PASS / WARN 判定；kind 不在判据表里返回 None（纯诊断项）。"""
    limits = PASS_MIN if limits is None else limits
    if kind not in limits:
        return None
    lim = limits[kind]
    bad = [k for k in ks if rates.get(k) is not None and rates[k] < lim[k]]
    return "WARN" if bad else "PASS"


# ══════════════════════════ 截断快照 ══════════════════════════
def snapshot(bars, i):
    """d0=i 收盘时的可见标注（只喂 bars[:i+1]，无未来数据）。"""
    import chanlun_bi
    import chanlun_signal

    st = chanlun_bi.analyze(bars[: i + 1])
    bis = st["bis"]
    return {
        # 笔的身份 = 端点日期对；端点价随分型唯一确定，键命中即价同，无需另存
        "bi": [(b["s_dt"], b["e_dt"]) for b in bis],
        "piv": [(p["zg"], p["zd"]) for p in st["pivots"]],
        "sigA": {(str(s["d"]), A_TYPE.get(s["t"], s["t"])) for s in st["signals"]},
        "sigB": {
            (str(s["dt"]), s["type"])
            for s in chanlun_signal.find_signals(bis, st["segments"], st["pivots"])
        },
    }


def _add(acc, kind, k, hit, base=1):
    cell = acc.setdefault(kind, {}).setdefault(k, [0, 0])
    cell[0] += 1 if hit else 0
    cell[1] += base


def _observe(acc, s0, s1, k):
    """d0 快照 vs d0+k 快照 → 逐结构记账。"""
    later_bi = {(b[0], b[1]) for b in s1["bi"]}
    nb = len(s0["bi"])
    for j, b in enumerate(s0["bi"]):
        hit = (b[0], b[1]) in later_bi
        _add(acc, "bi", k, hit)
        _add(acc, "bi_tail" if j == nb - 1 else "bi_inner", k, hit)

    for pv in s0["piv"]:
        _add(acc, "pivot", k, pivot_survives(pv, s1["piv"]))

    for cal, kind in (("sigB", "signal"), ("sigA", "signal_A")):
        later = s1[cal]
        for sk in s0[cal]:
            hit = sk in later
            _add(acc, kind, k, hit)
            _add(acc, cal + "|" + sk[1], k, hit)


def _register(cohort, seen, s0, d0, snaps, last):
    """首见队列：结构第一次出现时记一次观测（逐结构无重复计数）。"""
    for kind, cur in (
        ("bi", s0["bi"]),
        ("piv", s0["piv"]),
        ("sigB", s0["sigB"]),
        ("sigA", s0["sigA"]),
    ):
        for item in cur:
            key = item[:2] if kind == "bi" else item
            if key in seen[kind]:
                continue
            seen[kind].add(key)
            for k in KS:
                if d0 + k > last:
                    continue
                tgt = snaps[d0 + k]
                if kind == "bi":
                    hit = key in {(b[0], b[1]) for b in tgt["bi"]}
                elif kind == "piv":
                    hit = pivot_survives(key, tgt["piv"])
                else:
                    hit = key in tgt[kind]
                label = {"bi": "bi", "piv": "pivot", "sigB": "signal", "sigA": "signal_A"}[kind]
                _add(cohort, label, k, hit)


def _stock_job(code, ks=KS, warmup=MIN_WARMUP, min_bars=MIN_BARS):
    """单标的逐日重放 → (acc, cohort, baseline, meta)。"""
    import chanlun_scan as cs

    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path) or []
    if len(bars) < min_bars:
        return {"code": code, "skip": "bars=%d<%d" % (len(bars), min_bars)}
    n = len(bars)
    last = n - 1
    snaps = {i: snapshot(bars, i) for i in range(warmup, n)}

    acc, cohort = {}, {}
    seen = {"bi": set(), "piv": set(), "sigB": set(), "sigA": set()}
    dt_idx = {b["dt"]: i for i, b in enumerate(bars)}
    first_day = {}  # (cal, key) -> 首次可见的 bar 索引
    for d0 in range(warmup, last + 1):
        s0 = snaps[d0]
        _register(cohort, seen, s0, d0, snaps, last)
        for cal in ("sigA", "sigB"):
            for key in s0[cal]:
                first_day.setdefault((cal, key), d0)
        for k in ks:
            if d0 + k <= last:
                _observe(acc, s0, snaps[d0 + k], k)

    # ── 对账基线：全历史账本 vs 在线可见并集（conformance 的口径）──
    full = snaps[last]
    base = {}
    for cal in ("sigA", "sigB"):
        union = set()
        for i in range(warmup, n):
            union |= snaps[i][cal]
        inter = len(full[cal] & union)
        uni = len(full[cal] | union)
        # 确认滞后：dt 落在重放窗口内的全历史信号，距首次可见多少根 K 线。
        # 与 conformance「lag0 = 0/229、中位 A 2 日 / B 16.5 日」同口径。
        lags = []
        shown_at_dt = 0
        for key in full[cal]:
            i = dt_idx.get(key[0])
            if i is None or i < warmup:
                continue  # 窗口外出生，左删失，不计
            d = first_day.get((cal, key))
            if d is None:
                continue
            lags.append(d - i)
            if d == i:
                shown_at_dt += 1
        base[cal] = {
            "full": len(full[cal]),
            "online": len(union),
            "inter": inter,
            "union": uni,
            "jaccard": (float(inter) / uni) if uni else None,
            "online_only": len(union - full[cal]),
            "lag_n": len(lags),
            "lag0": shown_at_dt,
            "lags": lags,
        }
    return {
        "code": code,
        "bars": n,
        "days": last - warmup + 1,
        "first": bars[warmup]["dt"],
        "last": bars[last]["dt"],
        "acc": acc,
        "cohort": cohort,
        "base": base,
    }


# ══════════════════════════ 汇总 ══════════════════════════
def _merge(dst, src):
    for kind, cells in src.items():
        for k, (h, b) in cells.items():
            cell = dst.setdefault(kind, {}).setdefault(k, [0, 0])
            cell[0] += h
            cell[1] += b


def _summary(acc, ks=KS):
    out = {}
    for kind, cells in sorted(acc.items()):
        rates = {k: rate(*cells[k]) for k in ks if k in cells}
        nums = {k: cells[k][1] for k in ks if k in cells}
        item = {
            "rate": {str(k): (round(v, 4) if v is not None else None) for k, v in rates.items()},
            "n": {str(k): nums[k] for k in sorted(nums)},
            "half_life": half_life(rates, ks),
        }
        item["verdict"] = verdict(kind, rates, ks=ks)
        if kind in HARD_KINDS:
            item["hard_min"] = HARD_KINDS[kind]
            if any(v is not None and v < HARD_KINDS[kind] for v in rates.values()):
                item["verdict"] = "WARN(HARD)"
        if kind in DIAG_ONLY:
            item["note"] = "诊断项，不参与 PASS/WARN"
        out[kind] = item
    return out


def _merge_base(dst, src):
    for cal, v in src.items():
        d = dst.setdefault(cal, {"full": 0, "online": 0, "inter": 0, "union": 0,
                                 "online_only": 0, "lag0": 0, "lags": [], "per_stock": []})
        for f in ("full", "online", "inter", "union", "online_only", "lag0"):
            d[f] += v[f]
        d["lags"].extend(v["lags"])
        d["per_stock"].append(v["jaccard"])


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _base_summary(d):
    out = {}
    for cal, v in d.items():
        ps = v.pop("per_stock")
        lags = v.pop("lags")
        jac = [j for j in ps if j is not None]
        v["jaccard_micro"] = round(v["inter"] / v["union"], 4) if v["union"] else None
        v["jaccard_macro"] = round(sum(jac) / len(jac), 4) if jac else None
        v["lag_n"] = len(lags)
        v["lag_median"] = _median(lags)
        v["lag_max"] = max(lags) if lags else None
        v["lag0_rate"] = round(v["lag0"] / len(lags), 4) if lags else None
        out[cal] = v
    return out


def _fmt(x):
    return "  n/a " if x is None else "%.3f" % x


# conformance 已发布数字（deliverables/2026-11-05_三线并行_v6_口径审计与跨市场判定.md §2.4）
CONFORMANCE = {
    "jaccard": {"sigA": 0.560, "sigB": 0.987},
    "lag_median": {"sigA": 2.0, "sigB": 16.5},
}


def _reconcile(bsum):
    """把本次实测与 conformance 的 44% 消失基线对齐，并标注每处差异的成因。"""
    out = {}
    for cal in ("sigA", "sigB"):
        v = bsum[cal]
        lost = v["full"] - v["inter"]
        out[cal] = {
            "my_jaccard_micro": v["jaccard_micro"],
            "conformance_jaccard": CONFORMANCE["jaccard"][cal],
            "gap": (None if v["jaccard_micro"] is None
                    else round(v["jaccard_micro"] - CONFORMANCE["jaccard"][cal], 4)),
            "my_lag_median": v["lag_median"],
            "conformance_lag_median": CONFORMANCE["lag_median"][cal],
            "my_lag0_rate": v["lag0_rate"],
            "full_lost": lost,
            "full_lost_rate": round(lost / v["full"], 4) if v["full"] else None,
            "full_lost_note": "tautology: 并集含最后一天 ⇒ S_full ⊆ U，恒为 0，非实测发现",
            "phantom": v["online_only"],
            "phantom_share": round(v["online_only"] / v["online"], 4) if v["online"] else None,
        }
    return {
        "note": (
            "44% 消失 = 1 - Jaccard，不是「44% 的信号真的消失」。"
            "「全历史信号未在线出现」的比例按定义恒为 0（并集含最后一天，"
            "S_full ⊆ U 是构造性的），因此该 44% 的全部差额只能来自反向："
            "幽灵信号（在线出现、定型后不存在，phantom）。"
            "Jaccard 对称，同时惩罚丢失与幻觉；"
            "conformance 的 0.560 与本实测同量级，差值来自抽样标的（50 vs 20）与"
            "其在线侧定义（无法复核：chanlun_conformance.py 读取被权限审批超时拦截）。"
        ),
        "by_caliber": out,
    }


def _print_table(summary, title):
    print("\n== %s ==" % title)
    print("%-16s %8s %7s %8s %7s %8s %7s %6s  %s"
          % ("结构", "n@1", "@1", "n@5", "@5", "n@20", "@20", "半衰期", "判定"))
    for kind in ("bi", "bi_inner", "bi_tail", "pivot", "signal", "signal_A"):
        if kind not in summary:
            continue
        s = summary[kind]
        r, nn = s["rate"], s["n"]
        hl = s["half_life"]
        hls = (">20" if hl is None else str(hl))
        print("%-16s %8d %7s %8d %7s %8d %7s %6s  %s"
              % (kind, nn.get("1", 0), _fmt(r.get("1")),
                 nn.get("5", 0), _fmt(r.get("5")),
                 nn.get("20", 0), _fmt(r.get("20")),
                 hls, s["verdict"] or "-"))


def main():
    ap = argparse.ArgumentParser(description="缠论结构标注稳定性（可复现性）度量")
    ap.add_argument("--limit", type=int, default=LIMIT, help="抽样标的数（0=全量）")
    ap.add_argument("--seed", type=int, default=SEED, help="抽样种子（固定可复现）")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--warmup", type=int, default=MIN_WARMUP)
    ap.add_argument("--min-bars", type=int, default=MIN_BARS)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(ASHARE) if f.endswith(".parquet"))
    codes = [f[:-8].replace("_", ".") for f in files]
    codes = [c for c in codes if c not in INDEX_CODES and not c.startswith("399")]
    random.Random(a.seed).shuffle(codes)
    pick = codes if a.limit <= 0 else codes[: a.limit]

    print("抽样 %d 只（seed=%d / 候选 %d） k=%s warmup=%d"
          % (len(pick), a.seed, len(codes), list(KS), a.warmup), flush=True)
    t0 = time.time()
    acc, cohort, base, used, skipped = {}, {}, {}, [], []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, part in enumerate(ex.map(_stock_job, pick, chunksize=1), 1):
            if part.get("skip"):
                skipped.append("%s(%s)" % (part["code"], part["skip"]))
                continue
            used.append(part)
            _merge(acc, part["acc"])
            _merge(cohort, part["cohort"])
            _merge_base(base, part["base"])
            if i % 10 == 0:
                print("  …%d/%d 完成，累计 %.0fs" % (i, len(pick), time.time() - t0),
                      flush=True)
    dt = time.time() - t0
    if not used:
        print("无可用样本")
        return

    summary = _summary(acc)
    summary_co = _summary(cohort)
    for k, v in summary_co.items():
        if v.get("verdict"):
            v["verdict_scope"] = "reference: same PASS_MIN as all_d0, not independently calibrated"
    by_type = {k.split("|", 1)[1]: v for k, v in summary.items() if "|" in k}
    bsum = _base_summary(base)

    total_obs = sum(sum(c[1] for c in cells.values()) for cells in acc.values())
    res = {
        "meta": {
            "script": "chanlun_annotation_stability.py",
            "definition": {
                "bi": "same (s_dt, e_dt) still in bis",
                "pivot": "same (zg, zd) within rel tol 1e-9",
                "signal": "same (type, dt) still in signals(points)",
                "replay": "truncated: analyze(bars[:d0+1]) each day, no future data",
            },
            "ks": list(KS),
            "seed": a.seed,
            "limit": a.limit,
            "workers": a.workers,
            "warmup": a.warmup,
            "min_bars": a.min_bars,
            "n_used": len(used),
            "n_skipped": len(skipped),
            "skipped": skipped[:20],
            "bars_total": sum(u["bars"] for u in used),
            "obs_total": total_obs,
            "runtime_s": round(dt, 1),
            "data_src": "data/ashare/*.parquet via chanlun_scan._bars_from_parquet (后复权口径)",
            "caveat": "同一结构跨多个 d0 重复计数（逐日重放固有），比率非独立样本",
            "argv": sys.argv,
            "conformance_ref": "deliverables/2026-11-05_三线并行_v6_口径审计与跨市场判定.md §2.4",
        },
        "pass_min": {k: {str(kk): vv for kk, vv in v.items()} for k, v in PASS_MIN.items()},
        "survival": summary,
        "cohort_first_seen": summary_co,
        "signal_by_type": by_type,
        "baseline_reconcile": bsum,
        "reconcile": _reconcile(bsum),
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    _print_table(summary, "逐日重放 · 主口径（每个 d0 的标注都计入，任务书口径）")
    print("\n-- 首见队列（每个结构只计一次 = 「画出来之后还在不在」）--")
    print("   （判定列沿用 all_d0 判据，仅同尺对照：本队列刻意包含出生即末笔的未定型结构）")
    print("%-10s %8s %7s %8s %7s %8s %7s %6s  %s"
          % ("结构", "n@1", "@1", "n@5", "@5", "n@20", "@20", "半衰期", "判定"))
    for kind in ("bi", "pivot", "signal", "signal_A"):
        if kind not in summary_co:
            continue
        s = summary_co[kind]
        r, nn = s["rate"], s["n"]
        print("%-10s %8d %7s %8d %7s %8d %7s %6s  %s"
              % (kind, nn.get("1", 0), _fmt(r.get("1")),
                 nn.get("5", 0), _fmt(r.get("5")),
                 nn.get("20", 0), _fmt(r.get("20")),
                 ">20" if s["half_life"] is None else s["half_life"],
                 s["verdict"] or "-"))

    print("\n-- 买卖点分类型（B 口径，主口径）--")
    for t, s in sorted(by_type.items()):
        print("  %-4s n@1=%-7d @1=%s  n@5=%-7d @5=%s  n@20=%-7d @20=%s"
              % (t, s["n"].get("1", 0), _fmt(s["rate"].get("1")),
                 s["n"].get("5", 0), _fmt(s["rate"].get("5")),
                 s["n"].get("20", 0), _fmt(s["rate"].get("20"))))

    print("\n-- 对账 conformance（全历史账本 vs 在线可见并集）--")
    for cal, name in (("sigA", "A 废弃口径"), ("sigB", "B 权威口径")):
        v = bsum[cal]
        print("  %s: Jaccard micro %.4f / macro %.4f；full=%d online=%d 交集=%d "
              "在线独有(幽灵)=%d；lag0=%.4f 滞后中位=%s 最大=%s n=%d"
              % (name, v["jaccard_micro"], v["jaccard_macro"], v["full"], v["online"],
                 v["inter"], v["online_only"], v["lag0_rate"] or 0.0,
                 v["lag_median"], v["lag_max"], v["lag_n"]))

    print("\n标的 %d 只（跳过 %d），K线合计 %d，观测 %d，用时 %.0fs"
          % (len(used), len(skipped), sum(u["bars"] for u in used), total_obs, dt))
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
