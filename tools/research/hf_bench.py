# -*- coding: utf-8 -*-
"""基准与 β 的唯一口径（G3，2026-11-08 第十七轮）。

## 为什么要有这个文件

前十六轮所有 α 都是 `净值 − 000985.CSI 涨幅`。两个缺陷叠在一起：

1. **基准选错**：000985 是市值加权。样本池里绝大多数是中小盘，市值加权
   会把「小盘涨得比大盘多」记成缠论的本事。实测高估 3.5~25 倍。
2. **隐含 β=1**：不做任何 β 调整，等于假设组合满仓跟指数同涨同跌。实际
   敞口 84% 上下浮动，涨的时候少涨、跌的时候也少跌，两头都被算错。

正确口径（与产线 `chanlun_alpha` 同源，不另写一套）：

    alpha = raw − β × (等权全A 区间收益)

β 用 `chanlun_alpha.beta_of(code, 入场日)`：只用**入场日之前**的窗口回归，
严格无未来函数；样本不足按 1.0 兜底并标记 `est=False`。

## 为什么不自己写 β

本仓库已经有三处 β 实现（`chanlun_alpha.beta_of`、`hf_phase4.beta_alpha`、
`chanlun_exit_policy` 里的内联版）。第四套只会制造第四个口径。这里只做
**薄封装 + 聚合**，回归本身一律委托 `chanlun_alpha`。

`hf_phase4.beta_alpha` 是另一回事：它用**净值**回归，得到「有效净敞口」，
用来回答「少投钱所以少亏」这类问题，不是逐笔 α 的口径。两个都要留，
名字上区分清楚：`regress_beta`（组合级，事后归因）vs `beta_of`（逐笔级，事前可执行）。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(ROOT, "quant_backtest") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "quant_backtest"))

BENCH_SRC = "等权全A（chanlun_alpha.bench_ew，8148 日，1993-04~2026-11）"
LEGACY_SRC = "000985.CSI 市值加权 + β=1（旧口径，仅用于对照，勿用于决策）"

_CA = None


def _ca():
    """延迟导入 chanlun_alpha：它 import numpy/pandas，别拖慢不用的脚本。"""
    global _CA
    if _CA is None:
        import chanlun_alpha
        _CA = chanlun_alpha
    return _CA


def bench():
    """等权全A 基准：{'dates','ret','n','pos','src'}。"""
    return _ca().bench_ew()


def bench_ret(d0, d1):
    """基准在 (d0, d1] 的累计收益，与「收→收」的 raw 区间对齐。

    基准区间不可得返回 None——**不伪造成 0**。伪造 0 等于把这段时间的
    市场涨跌算成组合的 α，是这一类统计里最常见的静默污染。
    """
    return _ca().bench_window_ret(d0, d1)


def beta_of(code, asof_date):
    """入场前滚动 β（无未来函数）。返回 (beta, est, n_pairs)。

    默认窗口 60 日、最少 20 对，与产线 `chanlun_alpha` 完全一致；
    改窗口请改 chanlun_alpha 的常量，不要在这里加参数——两处参数就是两个口径。
    """
    return _ca().beta_of(code, asof_date)


def alpha_of(raw, d0, d1, beta):
    """逐笔超额 = raw − β × 基准区间收益。基准不可得返回 None。"""
    br = bench_ret(d0, d1)
    if br is None:
        return None, None
    b = 1.0 if beta is None else float(beta)
    return raw - b * br, br


def beta_used(trades, default=1.0):
    """成交加权 β：Σ(金额 × 持有天数 × β) / Σ(金额 × 持有天数)。

    为什么不按笔数简单平均：10% 仓位的票和 1% 仓位的票对组合的 β 贡献
    差十倍，按笔数平均会把小仓位的 β 权重放大。为什么不按金额平均：
    持 5 天和持 60 天的敞口暴露不是一个量级。金额 × 天数是敞口暴露的
    一阶近似，虽仍忽略复利，但比前两种都近。
    """
    num = den = 0.0
    for t in trades:
        b = t.get("beta")
        if b is None:
            b = default
        w = abs(t.get("amt") or 0) * max(1, t.get("hold_days") or 1)
        num += b * w
        den += w
    return (num / den) if den > 0 else None


def regress_beta(nav_rows, b=None):
    """组合净值对基准的日度回归：r_p = a + β·r_b。

    与 `hf_phase4.beta_alpha` 同算法，但基准换成等权全A。β 是「实际跑出来的
    有效净敞口」，a 是扣掉 β 后的日超额，a/平均仓位 可跨敞口档位比较。

    注意这是**事后归因**，不能用它替代逐笔 β 做决策（它知道全期结果）。
    保留它是为了对照：若逐笔 β 成交加权值与回归 β 差得远，说明 β 估计有问题。
    """
    bb = b or bench()
    dates, ret, pos = bb["dates"], bb["ret"], bb["pos"]
    rs, ri = [], []
    for i in range(1, len(nav_rows)):
        d, dp = str(nav_rows[i]["d"]), str(nav_rows[i - 1]["d"])
        j, k = pos.get(d), pos.get(dp)
        if j is None or k is None or j != k + 1:
            # 只取基准相邻交易日：中间缺日会把两天收益当成一天，斜率被稀释
            continue
        ri.append(float(ret[j]))
        rs.append(float(nav_rows[i]["ret"]))
    n = len(rs)
    if n < 20:
        return None
    mx = sum(ri) / n
    my = sum(rs) / n
    vx = sum((x - mx) ** 2 for x in ri)
    if vx <= 0:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(ri, rs)) / vx
    a = my - beta * mx
    res = [y - (a + beta * x) for x, y in zip(ri, rs)]
    se = (sum(r * r for r in res) / max(1, n - 2) * (1.0 / n + mx * mx / vx)) ** 0.5
    return {"beta": round(beta, 3), "alpha_d": round(a, 6),
            "alpha_t": round(a / se, 2) if se > 0 else None,
            "mkt_d": round(mx, 6), "n": n}


def ew_index_close(base=100.0):
    """把等权全A 的日收益累乘成「指数点位」，返回 {YYYYMMDD: level}。

    为什么要伪装成一个"收盘价"：仓库里已经有一批函数（`hf_phase4.beta_alpha`、
    `hf_phase5` 的 excess_nav、`hf_phase6` 的势档）都按 `{日期: 收盘价}` 取数，
    靠 `idx[d]/idx[dp]-1` 算日收益。把等权基准拼成同形状的字典，
    就能让它们一行不改地切到新口径——**换数据源，不换接口**。

    绝对点位无意义（base=100 只是锚），有意义的是任意两天的比值。
    基准只覆盖 1993-04~2026-11，超出范围的日期查不到，调用方必须容忍缺失
    （宁可跳过，也不要用 0 或插值填——那是伪造市场收益）。
    """
    bb = bench()
    lvl, acc = {}, base
    for d, r in zip(bb["dates"], bb["ret"]):
        acc *= (1.0 + float(r))
        lvl[d] = acc
    return lvl


def bench_span():
    """基准覆盖区间，写进产物的 meta 便于事后核对。"""
    bb = bench()
    d = bb["dates"]
    return [d[0], d[-1], len(d)]
