# -*- coding: utf-8 -*-
"""市场准入判定引擎（admission）—— F1 纯函数判定，零 IO 零网络。

定位（量化大屏 v3 · 市场准入与策略师观点墙）
  输入策源三态 + 盘前倾向（overall/prob）+ 三判据门通过数 + 风控市场级闸，
  输出准入三态判定：
    closed 关门  —— regime=risk_off 或 风控硬拦（一票否决）
    narrow 窄门  —— neutral 且（overall<0 或 prob<50 或 影子闸拦截）；
                    其余未达宽门条件的组合（如 risk_on 但条件不全）兜底窄门
    wide   宽门  —— regime=risk_on 且 三门≥2 且 overall>0 且 prob≥55
    unknown 未知 —— 判定所依赖的输入缺源（缺快照/缺字段），绝不 fail-open

纪律（对齐 regime_center / §6 双轨条款）
  · 纯函数：本模块不读文件不联网，输入由调用方（dashboard_api）采集；
  · 缺源不猜：any 关键输入 None → verdict=unknown + missing 显式列出；
  · 资格口径：本引擎判定「能不能做多」，与 /api/brief fin.verdict 的
    「怎么执行」执行口径分立，禁互改；
  · 阈值常量化：调参只动 THRESHOLDS，禁止散落魔数。
"""
from datetime import datetime

# ── 判定阈值（定稿 2026-10-19，调参只改这里）─────────────────────────
THRESHOLDS = {
    "wide_prob_min": 55,        # 宽门胜率下限（%），≥ 才可宽门
    "wide_overall_min_gt": 0,   # 宽门 overall 严格大于此值
    "wide_gates_min": 2,        # 宽门三门通过数下限（X/3）
    "narrow_prob_lt": 50,       # neutral 下 prob 低于此值触发窄门条件
    "narrow_overall_lt": 0,     # neutral 下 overall 低于此值触发窄门条件
    "counter_prob_margin": 5,   # 反证：宽门 prob 距下限不足此值标注逼近
}

VERDICT_CN = {"closed": "关门", "narrow": "窄门", "wide": "宽门",
              "unknown": "未知"}

REGIMES = ("risk_off", "risk_on", "neutral")


def _iso_now():
    """ISO 8601 带本地时区（秒精度），如 2026-10-19T15:30:00+08:00。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _num(v):
    """宽松数值化（int/float/数字字符串），失败返回 None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_admission(regime, overall, prob, gates_passed,
                       shadow_block, hard_block, now=None):
    """纯函数准入判定。

    参数
      regime        : str|None  策源三态 risk_off/risk_on/neutral
      overall       : num|None  盘前倾向总分（<0 偏空，>0 偏多）
      prob          : num|None  盘前胜率（%）
      gates_passed  : int|None  三判据门通过数 X/3（regime 快照 gates）
      shadow_block  : bool|None 风控市场级闸影子拦截（action=shadow_block）
      hard_block    : bool|None 风控市场级闸硬拦（action=block）
      now           : datetime|None 注入时钟（测试用），默认本地现在

    返回 dict：verdict / verdict_cn / reasons / counter_evidence /
    inputs 回显 / thresholds 回显 / missing / computed_at。
    """
    ov, pb = _num(overall), _num(prob)
    gp = None
    if gates_passed is not None:
        try:
            gp = int(gates_passed)
        except (TypeError, ValueError):
            gp = None
    missing = []
    if regime not in REGIMES:
        missing.append("regime")
    if ov is None:
        missing.append("overall")
    if pb is None:
        missing.append("prob")
    if gp is None:
        missing.append("gates_passed")
    if hard_block is None:
        missing.append("hard_block")

    reasons = []
    counter = []

    def _closed():
        reasons.append("regime=risk_off（策源三态一票否决）"
                       if regime == "risk_off"
                       else "风控硬拦：市场级闸 action=block")
        if regime == "risk_off" and hard_block:
            reasons.append("风控硬拦：市场级闸 action=block（双判据同向）")
        if (ov is not None and ov > THRESHOLDS["wide_overall_min_gt"]
                and pb is not None and pb >= THRESHOLDS["wide_prob_min"]):
            counter.append("关门但 overall/prob 已达宽门线"
                           "（regime/风控否决优先，禁止据 overall/prob 放行）")

    def _narrow(trigger_bits):
        if regime == "risk_on":
            fails = []
            if gp is not None and gp < THRESHOLDS["wide_gates_min"]:
                fails.append("三门 %d/3<%d" % (gp, THRESHOLDS["wide_gates_min"]))
            if ov is not None and ov <= THRESHOLDS["wide_overall_min_gt"]:
                fails.append("overall %g≤%g" % (ov, THRESHOLDS["wide_overall_min_gt"]))
            if pb is not None and pb < THRESHOLDS["wide_prob_min"]:
                fails.append("prob %g<%g" % (pb, THRESHOLDS["wide_prob_min"]))
            reasons.append(("regime=risk_on 但宽门条件不全（%s）→ 兜底窄门"
                            % "；".join(fails)) if fails
                           else "regime=risk_on 但宽门条件不全 → 兜底窄门")
            counter.append("窄门但 regime=risk_on（多转信号与准入不一致，"
                           "关注宽门条件补齐节奏）")
        else:  # neutral
            if trigger_bits:
                reasons.append("neutral 且 %s" % "、".join(trigger_bits))
            else:
                reasons.append("neutral 未达宽门条件 → 兜底窄门")
        if shadow_block:
            counter.append("影子闸拦截与当前判定并存（影子语义只记不拦，"
                           "升级 enforce 须拍板）")

    if regime == "risk_off" or hard_block is True:
        verdict = "closed"
        _closed()
    elif missing:
        # 非关门但判定依赖缺源字段 → unknown，绝不 fail-open 假绿
        verdict = "unknown"
        reasons.append("判定输入缺源: %s" % ", ".join(missing))
    elif regime == "risk_on" and gp >= THRESHOLDS["wide_gates_min"] \
            and ov > THRESHOLDS["wide_overall_min_gt"] \
            and pb >= THRESHOLDS["wide_prob_min"]:
        verdict = "wide"
        reasons.append("regime=risk_on")
        reasons.append("三门 %d/3≥%d" % (gp, THRESHOLDS["wide_gates_min"]))
        reasons.append("overall %g>%g" % (ov, THRESHOLDS["wide_overall_min_gt"]))
        reasons.append("prob %g≥%g" % (pb, THRESHOLDS["wide_prob_min"]))
        if pb < THRESHOLDS["wide_prob_min"] + THRESHOLDS["counter_prob_margin"]:
            counter.append("宽门但胜率 %g%% 逼近下限 %g%%（边际宽门）"
                           % (pb, THRESHOLDS["wide_prob_min"]))
        if gp == THRESHOLDS["wide_gates_min"]:
            counter.append("宽门且三门 %d/3 刚好压线（无冗余余量）" % gp)
    else:
        verdict = "narrow"
        bits = []
        if regime == "neutral":
            if ov < THRESHOLDS["narrow_overall_lt"]:
                bits.append("overall %g<%g" % (ov, THRESHOLDS["narrow_overall_lt"]))
            if pb < THRESHOLDS["narrow_prob_lt"]:
                bits.append("prob %g<%g" % (pb, THRESHOLDS["narrow_prob_lt"]))
            if shadow_block:
                bits.append("影子闸拦截")
        _narrow(bits)

    return {
        "verdict": verdict,
        "verdict_cn": VERDICT_CN[verdict],
        "reasons": reasons,
        "counter_evidence": counter,
        "inputs": {
            "regime": regime,
            "overall": overall,
            "prob": prob,
            "gates_passed": gp if gp is not None else gates_passed,
            "shadow_block": shadow_block,
            "hard_block": hard_block,
        },
        "thresholds": dict(THRESHOLDS),
        "missing": missing,
        "computed_at": (datetime.now().astimezone() if now is None
                        else now.astimezone()
                        ).isoformat(timespec="seconds"),
    }
