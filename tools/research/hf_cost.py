# -*- coding: utf-8 -*-
"""成交成本的唯一口径（第十八轮，K-COST-CALIBER）。

**为什么要有这个文件**

前十七轮里，三套账各写各的成本，同一个策略在三份文件里有三个净值：

    hf_book      佣金 20bp + slip_bp 固定滑点 + impact_k×(ATR/px)×√(Q/ADV)
    trade_book   零成本（实测 ret == exit_px/entry_px - 1，差 0.0bp）
    shadow_book  只扣 7.5 + 12.5bp 佣金，无滑点

三个数摆在一起时，看的人以为是策略在做不同的事，其实是记账不同。
本模块把「成本」收敛成一层：**佣金按买卖分档，滑点按单边加，两者分开报**。

**为什么佣金和滑点必须分开**

佣金是确定的（券商费率，可以谈到万 1），滑点是估计的（成交价与决策价的差，
由单笔金额占日成交额的比例决定）。混成一个数之后，看到「成本 122bp」
无法判断该去谈佣金，还是该去降冲击——这两件事的解法完全不同。

`hf_phase5` 的四档校准（S1 12.2 / S2 24.2 / S3 50.2 / S4 111.2 bp 双边）
给的也是双边总额，本模块按佣金占比把它拆到买卖两侧，保证刻度连续。

**口径定义**

    总双边 bp = 佣金双边 bp + 2 × 单边滑点 bp

    买入费率 = 佣金买侧 + 滑点
    卖出费率 = 佣金卖侧 + 滑点

    笔级净收益（乘法，与 portfolio_mtm 引擎一致）：
        (1 + gross) × (1 - sell) / (1 + buy) - 1

    组合级年化成本（加法，与 hf_phase5.annual_cost_pp 一致）：
        换手 × 总双边 bp

**注意卖出侧费率高于买入侧**（12.5 vs 7.5），因为卖出多一道印花税/过户费。
这不是笔误，别「对齐」成两边一样——那样会把成本系统性低估 5bp。
"""
from __future__ import annotations

# 买卖两侧的佣金（含卖出印花税与过户费）。A 股现状：买入只收佣金，
# 卖出多收 0.05% 印花税 + 0.001% 过户费，所以卖侧几乎必然是买侧的 1.6 倍。
COMM_BUY_BP = 7.5
COMM_SELL_BP = 12.5
COMM_RT_BP = COMM_BUY_BP + COMM_SELL_BP      # 20.0

# 四档标准刻度。前两档用于对齐现有两套账，后两档用于判断「还剩多少」。
#   none  零成本      —— trade_book 现状（毛收益）
#   comm  只佣金      —— shadow_book 现状
#   s3    S3 保守     —— hf_phase5 校准表第 3 档，双边 50.2bp
#   real  实测        —— 单边 51bp（hf_phase5 的全成本档）+ 佣金，双边 122bp
LEVELS = (
    {"k": "none", "label": "零成本",     "comm_bp": 0.0,  "slip_bp": 0.0,
     "who": "不扣任何成本，仅用于对照旧账"},
    {"k": "comm", "label": "只佣金",     "comm_bp": 20.0, "slip_bp": 0.0,
     "who": "万 2.5 双边 + 卖出印花税，无滑点"},
    {"k": "s3",   "label": "S3 保守",    "comm_bp": 20.0, "slip_bp": 15.1,
     "who": "万 2.5 + 中小盘 + 单笔 0.3~1% 日成交额"},
    {"k": "real", "label": "实测 51bp",  "comm_bp": 20.0, "slip_bp": 51.0,
     "who": "hf_phase5 全成本档：51bp 单边滑点/冲击 + 佣金"},
)


def rates(comm_bp=None, slip_bp=0.0):
    """→ (buy_rate, sell_rate)，小数形式（0.00122 表示 122bp）。

    comm_bp=None 表示用默认双边 20bp；传 0.0 表示完全不收佣金（毛口径）。
    传 None 与传 0.0 语义不同，别混：None 是「用默认」，0.0 是「不要」。
    """
    c = COMM_RT_BP if comm_bp is None else float(comm_bp)
    s = float(slip_bp or 0.0)
    if c <= 0:
        b = sw = 0.0
    else:
        # 按买卖占比拆分，保证 comm_bp 给的就是双边总额
        b = c * (COMM_BUY_BP / COMM_RT_BP)
        sw = c * (COMM_SELL_BP / COMM_RT_BP)
    return (b + s) / 1e4, (sw + s) / 1e4


def total_bp(comm_bp=None, slip_bp=0.0):
    """双边总成本（bp）。这是报价用的那个数。"""
    b, s = rates(comm_bp, slip_bp)
    return (b + s) * 1e4


def net_ret(gross, comm_bp=None, slip_bp=0.0):
    """笔级：毛收益 → 净收益。乘法口径，与引擎逐位一致。

    gross 是小数（0.05 表示 +5%）。乘除而不是相减，是因为复利下
    「先乘 (1+buy)、再乘 (1-sell)」才是真实发生的顺序；两者在 100bp
    量级差 1e-4 相对误差，但既然能算对就不该算近似。
    """
    b, s = rates(comm_bp, slip_bp)
    return (1.0 + gross) * (1.0 - s) / (1.0 + b) - 1.0


def ann_cost_pp(turnover_yr, comm_bp=None, slip_bp=0.0):
    """组合级：年换手 × 双边 bp → 年化成本（百分点）。

    加法口径，与 hf_phase5 的 annual_cost_pp 一致。turnover_yr 必须是
    **双边**换手（买卖各算一次），已经是双边；再乘 0.5 会把它写成一半。
    """
    return float(turnover_yr) * total_bp(comm_bp, slip_bp) / 1e4 * 100.0


def level(key):
    """按 k 取刻度，找不到给出可用列表（静默回落会让报告引错档）。"""
    for x in LEVELS:
        if x["k"] == key:
            return dict(x)
    raise KeyError("未知成本刻度 %r，可用：%s"
                   % (key, ", ".join(x["k"] for x in LEVELS)))


def describe(comm_bp=None, slip_bp=0.0):
    """一行说明，写进 JSON 的 meta，避免以后有人猜这个数怎么来的。"""
    b, s = rates(comm_bp, slip_bp)
    return ("佣金双边 %.1fbp（买 %.1f / 卖 %.1f）+ 单边滑点 %.1fbp"
            "→ 买 %.1fbp / 卖 %.1fbp，双边合计 %.1fbp"
            % ((b + s) * 1e4 - 2 * slip_bp, b * 1e4 - slip_bp,
               s * 1e4 - slip_bp, slip_bp, b * 1e4, s * 1e4,
               (b + s) * 1e4))
