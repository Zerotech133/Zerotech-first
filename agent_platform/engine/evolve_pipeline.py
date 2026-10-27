# -*- coding: utf-8 -*-
"""进化层端到端流水线（WO-LEGION-S27）。

把五个模块串成一条可执行链路（探→试→裁→记）：

  Z2 experiment_card  建卡（三臂 + 核验项）
        │  建卡内部即过 Z5
        ▼
  Z5 lookahead_guard  防穿越断言（未来列/裁决键/泄露路径）
        ▼
  探/试               record_metrics（基线 / 候选 / 反方各跑一次）
        ▼
  Z2 compare         对比表（相对基线差值 + 方向判定）
        ▼
  Z3 anonymous_review 匿名横评 → 建议胜出（未解盲，只出建议）
        ▼
  签字门              老田 approval_gate（未签字 → 到此为止，不入库）
        ▼
  Z4 experience_store 四字段经验入库（须 signed_by；riskguard 域永远只读）

本模块只做编排：不新增判据、不绕过任何闸门；未签字时返回
``decision_required=True`` 且**不写经验库**。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import anonymous_review as R  # noqa: E402
from engine import experiment_card as E  # noqa: E402
from engine import experience_store as S  # noqa: E402


@dataclass
class PipelineResult:
    card: object
    table: dict
    review: object
    decision_required: bool = True
    entry: object | None = None
    note: str = ""


def run(
    hypothesis: str,
    candidate_diff: dict,
    baseline_metrics: dict,
    candidate_metrics: dict,
    adversary_diff: dict | None = None,
    adversary_metrics: dict | None = None,
    verifier: list | None = None,
    domain: str = "stock_screen",
    signed_by: str = "",
    reason: str = "",
    experience_root: Path | None = None,
    signature: dict | None = None,
    now: float | None = None,
) -> PipelineResult:
    """跑完整链路。signed_by 为空 → 停在签字门，不入库。

    P0-3：`signature` 透传给 Z2 decide 与 Z4 write_entry（同一签名对象，
    两次分别按各自 kind/item_id 验签）；未提供时按渐进模式记录 verified=False。
    """
    card = E.create_card(
        hypothesis=hypothesis,
        candidate_diff=candidate_diff,
        adversary_diff=adversary_diff,
        verifier=verifier or ["样本外达标", "回滚预案在位"],
    )
    E.record_metrics(card, "baseline", baseline_metrics)
    E.record_metrics(card, "candidate", candidate_metrics)
    if adversary_metrics:
        E.record_metrics(card, "adversary", adversary_metrics)
    table = E.compare(card)

    review = R.review(card)  # 匿名横评（未解盲）
    R.apply_to_card(card, review)  # 只写建议

    result = PipelineResult(card=card, table=table, review=review)

    if not (signed_by or "").strip():
        result.note = "停在签字门：未签字不入库（approval_gate 本人闭环）"
        return result

    adopt = bool(table.get("candidate_better"))
    E.decide(
        card,
        adopt=adopt,
        signed_by=signed_by,
        reason=reason,
        signature=signature,
        now=now,
    )
    R.reveal(review)  # 决定后解盲
    R.apply_to_card(card, review)  # 解盲后回写（覆盖匿名期建议，带 reveal_map）
    cand = card.experience_candidate
    result.entry = S.write_entry(
        domain=domain,
        cue=cand["cue"],
        lesson=cand["lesson"],
        evidence=cand["evidence"],
        confidence=cand["confidence"],
        signed_by=signed_by,
        root=experience_root,
        signature=signature,
        now=now,
    )
    result.decision_required = False
    result.note = "已签字 → 经验入库 %s" % result.entry.entry_id
    return result


def report(res: PipelineResult) -> str:
    card = res.card
    lines = [
        "# 进化层链路报告 %s" % card.card_id,
        "",
        "## 1. 假设",
        card.hypothesis,
        "",
        "## 2. 三臂 diff",
        "| 臂 | 改动项 |",
        "|---|---|",
    ]
    for name, arm in card.arms.items():
        lines.append(
            "| %s | %s |" % (name, "、".join(sorted(arm.diff.keys())) or "（不改）")
        )
    lines += ["", "## 3. 对比表（Z2）"]
    for arm, row in res.table["arms"].items():
        cells = [
            "%s=%s(%s)"
            % (
                k,
                v["value"],
                "—"
                if v["delta_vs_baseline"] is None
                else "%+g" % v["delta_vs_baseline"],
            )
            for k, v in row.items()
        ]
        lines.append("- **%s**：%s" % (arm, "；".join(cells)))
    lines += ["", "## 4. 匿名横评（Z3，未解盲）"]
    for i, anon in enumerate(res.review.ranking, 1):
        lines.append(
            "%d. %s — %s（%s）"
            % (i, anon, res.review.scores[anon], res.review.rationale[anon])
        )
    lines += ["", "## 5. 签字门"]
    if res.entry is not None:
        lines.append(
            "已签字（%s）→ 入库 **%s**（域 %s）"
            % (card.verdict.get("signed_by"), res.entry.entry_id, res.entry.domain)
        )
    else:
        lines.append("**%s**" % res.note)
        lines.append("建议胜出 = %s（解盲须签字后）" % res.review.winner)
    return "\n".join(lines)


def _demo() -> int:
    print("=== A. 不签字（应停在签字门，不入库）===")
    a = run(
        hypothesis="判据描述改为样本外验证口径是否更优",
        candidate_diff={"criteria/riskguard.md": "样本外验证条款"},
        adversary_diff={"criteria/riskguard.md": "维持原判据"},
        baseline_metrics={"misblock_lower_better": 0.18, "oos_return": 0.004},
        candidate_metrics={"misblock_lower_better": 0.11, "oos_return": 0.009},
        adversary_metrics={"misblock_lower_better": 0.17, "oos_return": 0.005},
        experience_root=Path(__import__("tempfile").mkdtemp()),
    )
    print(report(a))

    print("\n=== B. 签字（入库到临时库演示）===")
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        b = run(
            hypothesis="判据描述改为样本外验证口径是否更优",
            candidate_diff={"criteria/riskguard.md": "样本外验证条款"},
            baseline_metrics={"misblock_lower_better": 0.18},
            candidate_metrics={"misblock_lower_better": 0.11},
            signed_by="laotian",
            reason="误拦率 -7pp",
            experience_root=Path(td),
        )
        print(report(b))
        hits = S.search("误拦率", root=Path(td))
        print("\n检索验证：", hits[0]["entry_id"] if hits else "MISS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
