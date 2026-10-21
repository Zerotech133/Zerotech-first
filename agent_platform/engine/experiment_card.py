# -*- coding: utf-8 -*-
"""对照实验卡（WO-LEGION-S24 · 进化层 Z2）。

一次对照实验 = 三条并行的臂（Harness² 的 parallel probe）：
  baseline   基线装置（不动）
  candidate  候选改动（只写 diff，省略即继承）
  adversary  反方改动（证伪用；可缺省）
核验项（verifier）在实验设计时就写死，禁止事后加判据。

纪律（《赵云进化层V3》§六 + V3.1）：
  · 无对比证据不采纳（无证据 = kept_base）；
  · 任何采纳必须签字（Paperclip = 老田 approval_gate），未签字一律无效；
  · 候选 diff 在**建卡时**就过 Z5 防穿越断言（lookahead_guard），
    带未来列/裁决键/泄露路径的改动根本不许进入实验；
  · 本模块只产出经验**候选**，不写经验库五域（入库走 Z4 签字链）。
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import lookahead_guard  # noqa: E402

ARMS = ("baseline", "candidate", "adversary")
STATUSES = ("draft", "probing", "judged", "adopted", "rejected")


class CardError(ValueError):
    """实验卡不合规（结构/顺序/证据缺失）。"""


class SignatureRequired(RuntimeError):
    """无签字不得结案（Paperclip=老田 approval_gate）。"""


@dataclass
class Arm:
    name: str
    diff: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    note: str = ""


@dataclass
class ExperimentCard:
    card_id: str
    hypothesis: str
    arms: dict
    verifier: list
    budget: int = 1
    status: str = "draft"
    verdict: dict = field(default_factory=dict)
    experience_candidate: dict = field(default_factory=dict)


def _check_diff_safe(diff: dict, label: str) -> None:
    """候选/反方 diff 的键与路径值必须先过 Z5；越不过就不许建卡。"""
    keys = list(diff.keys()) if isinstance(diff, dict) else []
    paths = [v for v in (diff.values() if isinstance(diff, dict) else [])
             if isinstance(v, str) and ("/" in v or "\\" in v)]
    try:
        lookahead_guard.assert_clean(columns=keys, paths=paths, stage="evolve")
    except lookahead_guard.LeakageDetected as exc:
        raise CardError("%s diff rejected by lookahead guard: %s" % (label, exc)) from exc


def create_card(hypothesis: str,
                baseline_diff: dict | None = None,
                candidate_diff: dict | None = None,
                adversary_diff: dict | None = None,
                verifier: list | None = None,
                budget: int = 1,
                card_id: str | None = None) -> ExperimentCard:
    if not (hypothesis or "").strip():
        raise CardError("hypothesis required")
    if candidate_diff is None:
        raise CardError("candidate diff required (compare needs a change)")
    if not verifier:
        raise CardError("verifier required (核验项须实验设计时写死)")
    if budget < 1:
        raise CardError("budget must be >= 1")

    _check_diff_safe(baseline_diff or {}, "baseline")
    _check_diff_safe(candidate_diff, "candidate")
    if adversary_diff:
        _check_diff_safe(adversary_diff, "adversary")

    arms = {"baseline": Arm("baseline", dict(baseline_diff or {}))}
    arms["candidate"] = Arm("candidate", dict(candidate_diff))
    if adversary_diff:
        arms["adversary"] = Arm("adversary", dict(adversary_diff))
    return ExperimentCard(
        card_id=card_id or "EX-%s" % uuid.uuid4().hex[:10],
        hypothesis=hypothesis.strip(),
        arms=arms,
        verifier=list(verifier),
        budget=budget,
    )


def record_metrics(card: ExperimentCard, arm: str, metrics: dict) -> None:
    if arm not in card.arms:
        raise CardError("unknown arm: %s" % arm)
    if not isinstance(metrics, dict) or not metrics:
        raise CardError("metrics must be non-empty dict")
    card.arms[arm].metrics.update({k: float(v) for k, v in metrics.items()})
    if card.status == "draft":
        card.status = "probing"


def compare(card: ExperimentCard) -> dict:
    """产出对比表：每臂指标 + 相对基线的差值 + 是否优于基线（按指标方向）。

    metric 方向约定：键名以 `_lower_better` 结尾表示越小越好，其余越大越好。
    """
    if "baseline" not in card.arms or not card.arms["baseline"].metrics:
        raise CardError("baseline metrics missing (无基线不可比)")
    base = card.arms["baseline"].metrics
    table = {"card_id": card.card_id, "hypothesis": card.hypothesis, "arms": {}}
    for name, arm in card.arms.items():
        if not arm.metrics:
            continue
        row = {}
        for k, v in arm.metrics.items():
            delta = None
            if k in base:
                delta = round(v - base[k], 6)
            better = None
            if delta is not None and name != "baseline":
                better = (delta < 0) if k.endswith("_lower_better") else (delta > 0)
            row[k] = {"value": v, "delta_vs_baseline": delta, "better": better}
        table["arms"][name] = row
    cand = table["arms"].get("candidate")
    if cand:
        flags = [m["better"] for m in cand.values() if m["better"] is not None]
        table["candidate_better"] = bool(flags) and all(flags)
    return table


def decide(card: ExperimentCard, adopt: bool, signed_by: str, reason: str = "") -> dict:
    """结案：必须签字；采纳必须有对比证据支撑。"""
    if not (signed_by or "").strip():
        raise SignatureRequired("无签字不得结案（Paperclip = 老田 approval_gate）")
    table = compare(card)
    if adopt and not table.get("candidate_better"):
        raise CardError("无对比证据支撑采纳（无证据 = kept_base）")
    card.status = "adopted" if adopt else "rejected"
    card.verdict = {"adopt": bool(adopt), "signed_by": signed_by.strip(),
                    "reason": reason, "table": table}
    card.experience_candidate = {
        "cue": card.hypothesis,
        "lesson": ("采纳：" if adopt else "否决：") + (reason or "（未写理由）"),
        "evidence": "card=%s verifier=%d" % (card.card_id, len(card.verifier)),
        "confidence": 0.6 if adopt else 0.4,
        "signed_by": signed_by.strip(),
        "note": "候选经验——入库须走 Z4 签字链",
    }
    return card.verdict


def to_markdown(card: ExperimentCard) -> str:
    table = card.verdict.get("table") or compare(card)
    lines = ["## 对照实验 %s" % card.card_id,
             "**假设**：%s" % card.hypothesis,
             "**核验项**：%s" % "；".join(card.verifier),
             "",
             "| 臂 | 指标 | 值 | 相对基线 | 优于基线 |",
             "|---|---|---|---|---|"]
    for arm, row in table["arms"].items():
        for k, m in row.items():
            lines.append("| %s | %s | %s | %s | %s |" % (
                arm, k, m["value"],
                "—" if m["delta_vs_baseline"] is None else ("%+g" % m["delta_vs_baseline"]),
                "—" if m["better"] is None else ("是" if m["better"] else "否")))
    lines.append("")
    lines.append("**结论**：%s" % (("采纳" if card.verdict.get("adopt") else "否决")
                                + "（签字：%s）" % card.verdict.get("signed_by")
                                if card.verdict else "未结案"))
    return "\n".join(lines)


def save(card: ExperimentCard, root: Path | None = None) -> Path:
    d = Path(root) if root else (_PLATFORM / "data" / "experiments")
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.json" % card.card_id)
    p.write_text(json.dumps(asdict(card), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _demo() -> int:
    """手工跑通一次三卡并列实验并打印对比表（Z2 验收判据）。"""
    card = create_card(
        hypothesis="RiskGuard 判据描述含糊（critical 拦截后验 +0.65%）应改为样本外验证口径",
        baseline_diff={},
        candidate_diff={"criteria/riskguard.md": "新增样本外验证条款"},
        adversary_diff={"criteria/riskguard.md": "维持原判据 + 补充注释"},
        verifier=["样本外达标", "回滚预案在位", "误拦率不升"],
        budget=1,
    )
    record_metrics(card, "baseline", {"misblock_rate_lower_better": 0.18, "oos_return": 0.004})
    record_metrics(card, "candidate", {"misblock_rate_lower_better": 0.11, "oos_return": 0.009})
    record_metrics(card, "adversary", {"misblock_rate_lower_better": 0.17, "oos_return": 0.005})
    print(to_markdown(card))
    v = decide(card, adopt=True, signed_by="laotian", reason="误拦率 -7pp 且样本外收益 +0.5pp")
    print("\nverdict:", json.dumps({k: v[k] for k in ("adopt", "signed_by")}, ensure_ascii=False))
    print("experience_candidate:", json.dumps(card.experience_candidate, ensure_ascii=False))
    print("status:", card.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
