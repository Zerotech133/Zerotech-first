# -*- coding: utf-8 -*-
r"""上行提醒链模块：荀攸 → 司马懿 → 田丰（N9，执行块 11 月计划）。

**纪律（fail-visible / 零串味）：**
  - 本模块**不触网**（不连飞书、不 HTTP）、**不写库**、**不交易**；
    只读 strategy_layer.xunyou 的分档常量与 assign_tiers 产物；
  - 所有产出为「研究上行报文」字符串/结构化 dict，无任何交易指令；
  - `build_upstream_message` 仅生成可读报文字符串并标注
    `RESEARCH_ONLY_NOT_PRODUCTION` / `BLOCK_CUTOVER`，**不直连飞书、
    不自动发送**——发送由调度层负责；
  - 输入损坏（缺 symbol/tier 等关键字段）即显式抛错（fail-visible），
    绝不静默吞掉；
  - 分歧票（divergence=True）强制人工复核、**绝不自动上行告警**，
    即使被同时标 T1 也以 divergence 优先。

依赖：
  - 输入 = 经 `xunyou.assign_tiers` 处理的带 `tier` 行列表（每行至少含
    symbol / score / action / divergence / tier / review_required）；
  - 只读 xunyou 顶部常量（T1_SCORE_MIN 等）用于可读 reason 与口径说明，
    **不修改 xunyou.py**。

上行链：
    upstream_chain = ["荀攸", "司马懿", "田丰"]
"""

from __future__ import annotations

import sys

_HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
_QUANT_BT = __import__("os").path.dirname(_HERE)
if _QUANT_BT not in sys.path:
    sys.path.insert(0, _QUANT_BT)

from strategy_layer import xunyou  # noqa: E402

UPSTREAM_CHAIN = ["荀攸", "司马懿", "田丰"]
SCOPE = "RESEARCH_ONLY_NOT_PRODUCTION"
VERDICT = "BLOCK_CUTOVER"


def _is_divergent(row: dict) -> bool:
    v = row.get("divergence")
    return v is True or v == "true"


def _divergence_actions(row: dict):
    """从 combined 行抽取多信号 action（分歧口径的可读成因）。

    优先取 contributions 中各条目的 action（若存在且为 dict），
    否则退回该行本身的单一 action。不造数——只用入参既有字段。
    """
    contribs = row.get("contributions")
    actions = []
    if isinstance(contribs, dict):
        for entry in contribs.values():
            if isinstance(entry, dict):
                a = entry.get("action")
                if a and a not in actions:
                    actions.append(a)
    if not actions:
        a = row.get("action")
        if a is not None:
            actions.append(a)
    return actions


def _t1_reason(row: dict) -> str:
    return "score=%.1f 达 T1 告警档（>%s 且当日排名入围），走量化交易部门上行链" % (
        float(row.get("score", 0.0)),
        xunyou.T1_SCORE_MIN,
    )


def select_escalation(tiered_rows: list) -> dict:
    """将 assign_tiers 后的带 tier 行装配为结构化上行通知。

    参数：
        tiered_rows: list[dict]，每行至少含 symbol/score/action/
                     divergence/tier/review_required（assign_tiers 产物）。

    返回（结构化上行通知）：见模块 docstring / 任务交付规格。
    """
    if not isinstance(tiered_rows, list):
        raise TypeError("tiered_rows 必须是 list（assign_tiers 产物）")

    # ── fail-visible：关键字段缺失显式抛错 ──────────────────────────
    for r in tiered_rows:
        if not isinstance(r, dict):
            raise TypeError("tiered_rows 元素必须是 dict")
        if "symbol" not in r:
            raise ValueError("tiered_rows 元素缺 'symbol' 字段（fail-visible）")
        if "tier" not in r:
            raise ValueError("tiered_rows 元素缺 'tier' 字段——未经验 assign_tiers 处理")

    # ── T1 排名（按 score 降序、1 起计；分歧票不占额度）──────────────
    t1_rows = [r for r in tiered_rows if r.get("tier") == "T1"]
    t1_sorted = sorted(t1_rows, key=lambda r: -float(r.get("score", 0.0)))
    t1_rank = {r["symbol"]: i for i, r in enumerate(t1_sorted, 1)}

    # ── 装配各段 ───────────────────────────────────────────────────
    t1_alerts = []
    auto_alert_symbols = []
    for r in t1_sorted:
        sym = r["symbol"]
        # 零串味：即便被标 T1，divergence 也强制排除出自动告警
        if _is_divergent(r):
            continue
        t1_alerts.append(
            {
                "symbol": sym,
                "score": r.get("score"),
                "rank": t1_rank[sym],
                "reason": _t1_reason(r),
            }
        )
        if sym not in auto_alert_symbols:
            auto_alert_symbols.append(sym)

    t2_watch = []
    for r in sorted(
        [r for r in tiered_rows if r.get("tier") == "T2"],
        key=lambda r: -float(r.get("score", 0.0)),
    ):
        t2_watch.append({"symbol": r["symbol"], "score": r.get("score")})

    divergences = []
    for r in tiered_rows:
        if _is_divergent(r):
            divergences.append(
                {
                    "symbol": r["symbol"],
                    "actions": _divergence_actions(r),
                    "combined_action": r.get("action"),
                }
            )

    # ── manual_review_required：divergence + 任意 review_required 行 ─
    manual_review_required = []
    for r in tiered_rows:
        needs = _is_divergent(r) or bool(r.get("review_required"))
        if needs and r["symbol"] not in manual_review_required:
            manual_review_required.append(r["symbol"])

    notes = (
        "上行提醒链（%s→%s→%s）：T1 触发盯盘/提醒、T2 仅人工关注不自动告警；"
        "divergence=true 与任意 review_required 行一律人工复核、绝不自动上行。"
        "本模块不触网/不写库/不交易（%s ｜ %s）。"
        % (UPSTREAM_CHAIN[0], UPSTREAM_CHAIN[1], UPSTREAM_CHAIN[2], SCOPE, VERDICT)
    )

    return {
        "upstream_chain": list(UPSTREAM_CHAIN),
        "t1_alerts": t1_alerts,
        "t2_watch": t2_watch,
        "divergences": divergences,
        "manual_review_required": manual_review_required,
        "suppressed_trading": True,  # 声明：本模块不产出任何交易指令
        "auto_alert_symbols": auto_alert_symbols,
        "notes": notes,
    }


def build_upstream_message(notice: dict) -> str:
    """生成给 司马懿→田丰 的可读上行报文（纯文本，仅返回字符串）。

    **不直连飞书、不自动发送**——发送由调度层负责。报文标注
    RESEARCH_ONLY_NOT_PRODUCTION / BLOCK_CUTOVER。
    """
    chain = notice.get("upstream_chain", UPSTREAM_CHAIN)
    lines = [
        "=" * 56,
        "六度选股上行提醒链 · 荀攸盘后上行报文",
        "上行路：%s" % " → ".join(chain),
        "性质：%s ｜ 切流裁决：%s" % (SCOPE, VERDICT),
        "声明：本报文为研究产物，不触网、不写库、不交易；不直连飞书、不自动发送",
        "=" * 56,
        "",
        "【T1 告警档（走量化交易部门上行链，自动盯盘/提醒）】",
    ]
    t1 = notice.get("t1_alerts") or []
    if t1:
        for a in t1:
            lines.append(
                "  %d. %s | score=%.1f | %s"
                % (a["rank"], a["symbol"], float(a["score"] or 0.0), a["reason"])
            )
    else:
        lines.append("  （无）")

    lines += [
        "",
        "【T2 人工关注档（仅人工关注，不自动告警）】",
    ]
    t2 = notice.get("t2_watch") or []
    if t2:
        for w in t2:
            lines.append(
                "  - %s | score=%.1f" % (w["symbol"], float(w["score"] or 0.0))
            )
    else:
        lines.append("  （无）")

    lines += [
        "",
        "【人工复核（分歧票 / review_required，绝不自动上行）】",
    ]
    divs = notice.get("divergences") or []
    if divs:
        for d in divs:
            lines.append(
                "  - %s | actions=%s | combined_action=%s"
                % (d["symbol"], d["actions"], d["combined_action"])
            )
    else:
        lines.append("  （无分歧票）")

    lines += [
        "",
        "【待人工复核清单（manual_review_required）】",
        "  %s" % ("、".join(notice.get("manual_review_required") or []) or "（无）"),
        "",
        "【自动告警标的（auto_alert_symbols，已剔除 divergence）】",
        "  %s" % ("、".join(notice.get("auto_alert_symbols") or []) or "（无）"),
        "",
        "【交易指令声明】",
        "  suppressed_trading=%s —— 本模块不产出任何交易指令"
        % notice.get("suppressed_trading"),
        "",
        "END %s | %s | 不直连飞书/不自动发送" % (SCOPE, VERDICT),
    ]
    return "\n".join(lines)
