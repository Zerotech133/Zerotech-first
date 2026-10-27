# -*- coding: utf-8 -*-
"""缠论多级别联立判定 · 纯函数层（D2 二梯队 V1 回测验证用）。

目标：单级别买卖点信号 → 三级别同向确认过滤，压跨级别假信号。
草案默认规则（docs/plans/2026-10-07_D2二梯队缠论联立立项草案.md）：
  30F 一买 + 5F 底分型成立 + 1F 不新低，三条件同时成立才放行。
规则全部参数化于 RULESETS，回测脚本按键取用做组合对照。

口径（与回测脚本一致，报告同步声明）：
· 上级信号 = chanlun_signal 在上级周期（如 30m）产出的买向信号
  （type/dt/price），联立不改变信号生成逻辑，只做放行/拦截。
· 底分型成立 = 中级周期截断至信号 dt 的 K 线（重放口径，无未来函数）
  在确认窗（信号 dt 前 lookback 根）内存在已确认底分型——分型之后
  尚有 ≥ confirm_bars 根 K 线（含另一侧确认），防止分型未成熟。
· 不新低 = 低级周期在信号 dt 前 low_lookback 根 K 线内的最低价
  ≥ 信号价 × (1 − low_tol)——即低级别未跌破上级买点低点（下跌
  动能衰竭的表达），low_tol 为容差（默认 0.2%，毛刺容忍）。
· 全部判定只用 dt ≤ 信号 dt 的数据；复用 chanlun_fractal / chanlun_bi
  的现有口径，不重写缠论结构。

judge(upper_signal, mid_bars, low_bars, ruleset_key) →
  {"ok": bool, "ruleset": key, "checks": [{"name","ok","detail"}], "no": 拦截原因}
纯函数无 IO；输入序列不修改（内部截断拷贝）。
"""

from chanlun_fractal import _merge_inclusion as _mi, find_fractals as _ff

# ── 联立规则集（参数化；回测对照至少跑 A/B 两档） ──────────────
# upper_types：放行参与联立的上级信号类型（草案默认档=仅一买最严格；
#   全买向口径为样本量考虑的变体，回测分口径统计）
RULESETS = {
    # 草案默认档：30F 买向 + 5F 底分型成立 + 1F 不新低
    "A_30F_5F_1F": {
        "upper_period": "30m",
        "mid_period": "5m",
        "low_period": "1m",
        "upper_types": ("1买", "2买", "3买"),
        "mid_lookback": 24,  # 底分型确认窗：中级前 24 根（5m 半日）
        "mid_confirm_bars": 2,  # 分型后 ≥2 根中级 K 线才视为成立
        "low_window": 60,  # 不新低：低级近 60 根 vs 前 60 根（各 1 小时）
        "low_tol": 0.002,  # 低级低点容差 0.2%
    },
    # 草案简化档：日线买向 + 30F 底分型成立 + 5F 不新低
    "B_D_30F_5F": {
        "upper_period": "1d",
        "mid_period": "30m",
        "low_period": "5m",
        "upper_types": ("1买", "2买", "3买"),
        "mid_lookback": 8,  # 30m×8 = 1 日
        "mid_confirm_bars": 2,
        "low_window": 24,  # 5m×24 = 2 小时
        "low_tol": 0.002,
    },
    # 严格变体（草案字面口径）：上级仅一买参与联立，其余同默认档
    "A1_30F_5F_1F_only1": {
        "upper_period": "30m",
        "mid_period": "5m",
        "low_period": "1m",
        "upper_types": ("1买",),
        "mid_lookback": 24,
        "mid_confirm_bars": 2,
        "low_window": 60,
        "low_tol": 0.002,
    },
    # 敏感性变体（宽松档）：更长观察窗 + 更大容差，保留更多信号，
    # 用于检验结论是否对参数敏感（回测对照第 3 组合）
    "A2_30F_5F_1F_wide": {
        "upper_period": "30m",
        "mid_period": "5m",
        "low_period": "1m",
        "upper_types": ("1买", "2买", "3买"),
        "mid_lookback": 48,  # 5m 一日
        "mid_confirm_bars": 2,
        "low_window": 120,  # 各 2 小时
        "low_tol": 0.005,  # 容差放宽至 0.5%
    },
}


def _cut(bars, dt, n=None):
    """升序 bars 截断至 dt ≤ 给定时点（重放口径）；n 给定时取末 n 根。"""
    keep = [b for b in bars if b["dt"] <= dt]
    return keep[-n:] if n else keep


def check_upper_type(upper_signal, mid_bars, low_bars, rule):
    """上级信号类型在放行集合内。"""
    ok = upper_signal.get("type") in rule["upper_types"]
    return {
        "name": "upper_type",
        "ok": ok,
        "detail": "%s in %s"
        % (upper_signal.get("type"), "/".join(rule["upper_types"])),
    }


def check_mid_bottom_fractal(upper_signal, mid_bars, low_bars, rule):
    """中级底分型成立：截断重放序列确认窗内存在已确认底分型。"""
    lookback = rule["mid_lookback"]
    conf = rule["mid_confirm_bars"]
    cut = _cut(mid_bars, upper_signal["dt"])
    detail = "mid_bars=%d" % len(cut)
    if len(cut) < conf + 1:
        return {
            "name": "mid_bottom_fractal",
            "ok": False,
            "detail": detail + " 中级数据不足",
        }
    frs = _ff(_mi(cut))
    window = frs[-lookback:] if len(frs) > lookback else frs
    # 已确认：分型之后还需 conf 根 K 线（分型第 3 根已在其内，再留余量）
    n_c = len(cut)
    hits = [
        f
        for f in window
        if f["type"] == "底" and n_c - 1 - _dt_index(cut, f["dt"]) >= conf
    ]
    return {
        "name": "mid_bottom_fractal",
        "ok": bool(hits),
        "detail": detail + " 底分型=%d" % len(hits),
    }


def check_low_no_lower_low(upper_signal, mid_bars, low_bars, rule):
    """低级不新低（更高低点 HL 语义）：低级近 low_window 根的最低价
    ≥ 其前 low_window 根的最低价 × (1 − tol)——低级别下跌动能衰竭、
    不再创段内新低。冒烟校准：旧口径（窗内低点 ≥ 信号价）在买点上
    几乎恒真（笔终点即低点），无判别力，弃用。"""
    w = rule["low_window"]
    tol = rule["low_tol"]
    cut = _cut(low_bars, upper_signal["dt"])[-2 * w :]
    if len(cut) < 2 * w:
        return {
            "name": "low_no_lower_low",
            "ok": False,
            "detail": "低级数据不足 %d/%d" % (len(cut), 2 * w),
        }
    recent = min(b.get("l") or b.get("c") for b in cut[w:])
    prior = min(b.get("l") or b.get("c") for b in cut[:w])
    floor = prior * (1 - tol)
    return {
        "name": "low_no_lower_low",
        "ok": recent >= floor,
        "detail": "recent_min=%.4g prior_min=%.4g floor=%.4g" % (recent, prior, floor),
    }


def _dt_index(bars, dt):
    """dt → 截断序列内索引（找不到返回末根，保守）。"""
    for i in range(len(bars) - 1, -1, -1):
        if bars[i]["dt"] == dt:
            return i
    return len(bars) - 1


CHECKS = (check_upper_type, check_mid_bottom_fractal, check_low_no_lower_low)


def judge(upper_signal, mid_bars, low_bars, ruleset_key):
    """联立判定：上级买向信号 + 中/低级别序列 → 放行与否。

    全部检查通过 ok=True；任一不过 ok=False，no=首个未过项名。
    """
    rule = RULESETS[ruleset_key]
    checks = [fn(upper_signal, mid_bars, low_bars, rule) for fn in CHECKS]
    no = next((c["name"] for c in checks if not c["ok"]), None)
    return {"ok": no is None, "ruleset": ruleset_key, "checks": checks, "no": no}
