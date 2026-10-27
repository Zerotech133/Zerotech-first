# -*- coding: utf-8 -*-
"""缠论工作台 P9 复合叠加评估 · 多侧面因子组合（影子预研，只评估不拦截）。

背景：P5~P8 六类单因子侧面标注（bei 盘整背驰 / bei2 同向腿背驰 /
mbei MACD 背驰 / tbe 趋势背驰 / mt 60m 次级别共振 / vv 量能缩量 /
reg 大盘顺风）已全部影子上线，单因子子集与全集的对照在样本面板与
预警池页并行展示。本模块把它们组合成「复合规则」——全部因子同时
成立才算复合成立，用于验证「多条件共振才有意义」的教学假设能否在
可成交口径 / 纸面口径下成立。

判定语义（与 P7「未覆盖记 None 绝不计 False」铁律一致）：
· 任一因子不可判（None：60m 未覆盖 / 无指数 / 无前序笔 / 无量数据 /
  信号前中枢不足）→ 整条复合记 None（不可判），绝不按 ✗ 处理；
· 全部因子可判且全部 True → 复合成立（True）；
· 全部因子可判且有任一 False → 复合不成立（False）。
评估侧分母单列「可判 cov」数，与单因子列的「覆盖」口径一致；
样本口径（eval_samples，重放命中 hit）与预警池口径（pool_eval，
检出时 tags）共用本表，两页并行影子、择优汰劣。

RULES 新增规则只需加一行，评估与页面自动跟随（展示名不超过 14 字）。
judge(hit, row, sig_type, factors) → True/False/None
judge_all(hit, row, sig_type) → {rule_id: True/False/None}（仅适用规则）
  hit=重放命中信号（样本口径）或 pool tags dict（预警池口径）；
  row=样本行 / 仓位 dict（mt/reg 等行级因子；池口径与 hit 同传 tags）。
"""

from chanlun_flow import aligned as _aligned


def _flag(f):
    """直接标注因子：hit 有该键取 hit，否则回退行级字段。"""

    def g(hit, row, t):
        if hit is not None and f in hit:
            return hit.get(f)
        return (row or {}).get(f)

    return g


def _reg_factor(hit, row, t):
    v = hit.get("reg") if hit is not None and "reg" in hit else (row or {}).get("reg")
    return _aligned(v, t)


# 因子名 → 取值函数（返回 True/False/None）
FACTOR_GETTERS = {
    "bei": _flag("bei"),  # P5  盘整背驰（回踩 vs 离开笔，3类）
    "bei2": _flag("bei2"),  # P6-b 同向腿背驰（vs 离开笔前同向腿，3类）
    "mbei": _flag("mbei"),  # P6-a MACD 背驰（1类）
    "tbe": _flag("tbe"),  # P6-c 趋势背驰双中枢（1类）
    "vv": _flag("vv"),  # P8-a 量能缩量（全类型）
    "pt": _flag("pt"),  # P2  K线形态共振（全类型，True/False 恒可判）
    "mt": _flag("mt"),  # P7  60m 次级别共振（行级，池口径无）
    "reg": _reg_factor,  # P8-b 大盘顺风（行级，方向对齐）
}

# 复合规则表：全部因子同时成立才算复合成立
RULES = [
    {
        "id": "c_mt",
        "name": "双背驰共振(M+趋)",
        "types": ("1买", "1卖"),
        "factors": ("mbei", "tbe"),
    },
    {
        "id": "c_tv",
        "name": "趋✓+量✓",
        "types": ("1买", "1卖"),
        "factors": ("tbe", "vv"),
    },
    {"id": "c_tr", "name": "趋✓+势顺", "types": ("1卖",), "factors": ("tbe", "reg")},
    {
        "id": "c_full",
        "name": "三重共振(M+趋+量)",
        "types": ("1买", "1卖"),
        "factors": ("mbei", "tbe", "vv"),
    },
    {
        "id": "c_tm",
        "name": "趋✓+次✓(60m)",
        "types": ("1买", "1卖"),
        "factors": ("tbe", "mt"),
    },
    {
        "id": "c_dp",
        "name": "双盘整背驰(✓+盘✓)",
        "types": ("3买", "3卖"),
        "factors": ("bei", "bei2"),
    },
    {
        "id": "c_bv",
        "name": "背驰✓+量✓",
        "types": ("3买", "3卖"),
        "factors": ("bei", "vv"),
    },
    {
        "id": "c_vr",
        "name": "量✓+势顺",
        "types": ("3买", "3卖"),
        "factors": ("vv", "reg"),
    },
    {
        "id": "c_tp",
        "name": "趋✓+形✓",
        "types": ("1买", "1卖"),
        "factors": ("tbe", "pt"),
    },
    {
        "id": "c_bp",
        "name": "背驰✓+形✓",
        "types": ("3买", "3卖"),
        "factors": ("bei", "pt"),
    },
    {
        "id": "c_vp",
        "name": "量✓+形✓",
        "types": ("1买", "1卖", "3买", "3卖"),
        "factors": ("vv", "pt"),
    },
]


def judge(hit, row, sig_type, factors):
    """复合判定：任一因子 None → None；否则全部 True 才 True。"""
    vals = []
    for f in factors:
        g = FACTOR_GETTERS.get(f)
        v = g(hit, row, sig_type) if g else None
        if v is None:
            return None
        vals.append(bool(v))
    return all(vals)


def judge_all(hit, row, sig_type):
    """该信号类型适用的全部复合规则判定（rule_id → True/False/None）。"""
    return {
        r["id"]: judge(hit, row, sig_type, r["factors"])
        for r in RULES
        if sig_type in r["types"]
    }
