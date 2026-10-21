# -*- coding: utf-8 -*-
"""匿名横评（WO-LEGION-S25 · 进化层 Z3）。

Harness² selection.py（selection_pool + assign_ids）的落地：候选改动**匿名编号**
后交给横评者打分排序，评审期间不得知道哪个是候选、哪个是反方、出自谁手，
防「偏袒某个标签」。决定仍须签字（Z2 decide），本模块只出**建议**。

流程：
  build_pool(card)        从实验卡取 candidate/adversary 组池（baseline 仅作参照）
  assign_anon_ids(pool)   确定性匿名编号（salt=card_id，不含 arm 名）
  review(card, judge=)    匿名横评 → ReviewResult（排名/分数/理由，匿名期不泄来源）
  reveal(result)          显式解盲：返回 anon→origin 映射（只在决定后调用）
  apply_to_card(card, r)  把横评建议写进 card.verdict["review"]，不设 adopt

纪律：
  · 评审者不得结案——ReviewResult 不含 signed_by，采纳与否仍走 experiment_card.decide；
  · 匿名期 rationale 与 scores 的键只能是匿名 ID；
  · 判据（metrics 方向）与实验卡一致：`_lower_better` 后缀为越小越好。
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import experiment_card as E  # noqa: E402

REVIEWABLE_ARMS = ("candidate", "adversary")


class ReviewError(ValueError):
    """横评不合规（池为空/缺基线/重复解盲等）。"""


@dataclass
class Candidate:
    anon_id: str
    origin: str            # 匿名期仅内部持有，绝不进 rationale
    metrics: dict = field(default_factory=dict)
    diff_keys: list = field(default_factory=list)


@dataclass
class ReviewResult:
    card_id: str
    scores: dict           # anon_id -> score
    ranking: list          # anon_id 有序（优→劣）
    winner: str            # 匿名 ID
    rationale: dict        # anon_id -> 文字（匿名期不含 origin）
    judge: str
    _reveal_map: dict = field(default_factory=dict)
    revealed: bool = False


def build_pool(card) -> list[Candidate]:
    pool = []
    for arm_name in REVIEWABLE_ARMS:
        arm = card.arms.get(arm_name)
        if arm and arm.metrics:
            pool.append(Candidate(anon_id="", origin=arm_name,
                                  metrics=dict(arm.metrics),
                                  diff_keys=sorted(arm.diff.keys())))
    if not pool:
        raise ReviewError("no reviewable arm with metrics (先 record_metrics)")
    return pool


def assign_anon_ids(pool: list[Candidate], salt: str) -> list[Candidate]:
    """确定性匿名编号：sha256(salt+origin+index) 前 6 位，大写；不含 origin 片段。"""
    for i, c in enumerate(pool):
        raw = hashlib.sha256(("%s|%s|%d" % (salt, c.origin, i)).encode("utf-8")).hexdigest()
        c.anon_id = "C-" + raw[:6].upper()
    ids = [c.anon_id for c in pool]
    if len(set(ids)) != len(ids):
        raise ReviewError("anon id collision")
    return pool


def _score_against_baseline(metrics: dict, baseline: dict) -> tuple[float, list]:
    """按 better/worse 计数打分；差值绝对值作 tie-break。返回 (score, 明细)。"""
    better = worse = 0
    magnitude = 0.0
    detail = []
    for k, v in metrics.items():
        if k not in baseline:
            continue
        delta = v - baseline[k]
        magnitude += abs(delta)
        is_better = (delta < 0) if k.endswith("_lower_better") else (delta > 0)
        if abs(delta) < 1e-12:
            verdict = "tie"
        elif is_better:
            better += 1
            verdict = "better"
        else:
            worse += 1
            verdict = "worse"
        detail.append({"metric": k, "delta": round(delta, 6), "verdict": verdict})
    score = better - worse + min(magnitude, 1.0) * 1e-3
    return round(score, 6), detail


def judge_by_metrics(card, pool: list[Candidate]) -> tuple[dict, dict, dict]:
    """默认评审者：按指标相对基线优劣打分（零模型、可解释）。"""
    baseline = card.arms["baseline"].metrics
    if not baseline:
        raise ReviewError("baseline metrics missing (无基线不可横评)")
    scores, rationale, details = {}, {}, {}
    for c in pool:
        score, detail = _score_against_baseline(c.metrics, baseline)
        scores[c.anon_id] = score
        better = sum(1 for d in detail if d["verdict"] == "better")
        worse = sum(1 for d in detail if d["verdict"] == "worse")
        rationale[c.anon_id] = "指标 %d 项优 / %d 项劣（对基线）" % (better, worse)
        details[c.anon_id] = detail
    return scores, rationale, details


def review(card, judge=None) -> ReviewResult:
    """匿名横评。judge 可注入（未来接诸葛亮 GPT6.0）；默认指标规则版。"""
    pool = assign_anon_ids(build_pool(card), salt=card.card_id)
    judge_name = "metrics-rule-v1"
    if judge is None:
        scores, rationale, details = judge_by_metrics(card, pool)
    else:
        scores, rationale = judge(pool, card)
        details = {}
        judge_name = getattr(judge, "__name__", "custom")
    ranking = sorted(scores, key=lambda k: (-scores[k], k))
    return ReviewResult(
        card_id=card.card_id,
        scores=scores,
        ranking=ranking,
        winner=ranking[0],
        rationale={k: rationale[k] for k in ranking},
        judge=judge_name,
        _reveal_map={c.anon_id: c.origin for c in pool},
    )


def reveal(result: ReviewResult) -> dict:
    """显式解盲：返回匿名 ID → 原始臂名映射；重复解盲幂等。"""
    result.revealed = True
    return dict(result._reveal_map)


def report_markdown(card, result: ReviewResult) -> str:
    """横评报告：匿名期显示匿名 ID；已解盲则附来源。"""
    lines = ["## 匿名横评 %s" % result.card_id,
             "评审者：%s ｜ 匿名编号：%d 个" % (result.judge, len(result.scores)),
             "",
             "| 名次 | 编号 | 得分 | 理由 |",
             "|---|---|---|---|"]
    for i, anon in enumerate(result.ranking, 1):
        lines.append("| %d | %s | %s | %s |" % (i, anon, result.scores[anon], result.rationale[anon]))
    lines.append("")
    lines.append("**胜出（建议）**：%s" % result.winner)
    if result.revealed:
        lines.append("**已解盲**：%s" % "；".join("%s→%s" % (k, v) for k, v in result._reveal_map.items()))
    else:
        lines.append("**未解盲**（决定后由签字人显式解盲）")
    lines.append("")
    lines.append("**注**：本报告只是建议；采纳与否须走 experiment_card.decide 并签字。")
    return "\n".join(lines)


def apply_to_card(card, result: ReviewResult) -> None:
    """把横评建议写进 card.verdict['review']；不设 adopt、不写 signed_by。"""
    card.verdict["review"] = {
        "judge": result.judge,
        "scores": result.scores,
        "ranking": result.ranking,
        "winner": result.winner,
        "revealed": result.revealed,
        "reveal_map": dict(result._reveal_map) if result.revealed else {},
        "note": "建议（未签字）；采纳走 decide",
    }


def _demo() -> int:
    """Z3 验收：一次真实横评，输出建议（含解盲前后对照）。"""
    card = E.create_card(
        hypothesis="RiskGuard 判据描述应为样本外验证口径",
        candidate_diff={"criteria/riskguard.md": "新增样本外验证条款"},
        adversary_diff={"criteria/riskguard.md": "维持原判据+注释"},
        verifier=["样本外达标", "回滚预案在位", "误拦率不升"],
    )
    E.record_metrics(card, "baseline", {"misblock_rate_lower_better": 0.18, "oos_return": 0.004})
    E.record_metrics(card, "candidate", {"misblock_rate_lower_better": 0.11, "oos_return": 0.009})
    E.record_metrics(card, "adversary", {"misblock_rate_lower_better": 0.17, "oos_return": 0.005})
    r = review(card)
    print(report_markdown(card, r))
    apply_to_card(card, r)
    m = reveal(r)
    print("\n解盲后：", m)
    print("建议胜出 =", m.get(r.winner))
    print("card.verdict.review.winner =", card.verdict["review"]["winner"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
