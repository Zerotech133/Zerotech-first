# -*- coding: utf-8 -*-
"""日终收口轮（WO-LEGION-P0FIX-20261026 · P0-2，Z 链接线）。

背景（2026-10-26 三路审计实证）：Z2/Z3/Z4/编排四模块**无任何生产调用方**——
`experiment_card.save()` 唯一调用点在测试里，`agent_platform/data/experiments`
目录不存在，`/api/evolution` 的 experiments 恒为 `[]`。本模块是第一个真实调用方：

  真实账本（task_exit_audit 摘要 + window_diff）→ Z2 实验卡（draft/probing）
  → E.save() 落到 `agent_platform/data/experiments/` → /api/evolution 可见。

纪律（与赵云进化层六条硬规则一致）：
  · **不造数**：卡片内容全部来自真实账本；窗口不可用（账本不足两次扫描）时
    如实返回 error，不生成半成品卡；
  · **不越签字门**：默认只产卡、不结案、不写经验（decision_required=True）；
    只有显式传 `signed_by` 且给出 `cue/lesson` 时才走 Z4 入库，并交给
    signature_guard 按渐进模式验签（未配公钥时如实记 verified=False）；
  · 不新增判据、不改 Z5/Z4 闸门逻辑。

用法：
    python agent_platform/tools/closeout_round.py --json
    python agent_platform/tools/closeout_round.py --hours 24 --json
    # 签字入库（需本人签名文件；未配公钥时按渐进模式记 unverified）
    python agent_platform/tools/closeout_round.py --signed-by 老田 \
        --signature-file _vault/signatures/closeout.json \
        --cue "…" --lesson "…"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_TOOLS = Path(__file__).resolve().parent
_PLATFORM = _TOOLS.parent
for _p in (str(_PLATFORM), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine import experiment_card as E  # noqa: E402
from engine import experience_store as S  # noqa: E402
import task_exit_audit as ta  # noqa: E402

DEFAULT_EXPERIMENTS_ROOT = _PLATFORM / "data" / "experiments"


def build_card(summary: dict, window: dict, hours: float) -> E.ExperimentCard:
    """用真实账本数字构造 Z2 实验卡（观察证据卡，不构成结论）。"""
    new_n = len(window.get("new_failures") or [])
    persist_n = len(window.get("persisting_failures") or [])
    fixed_n = len(window.get("fixed_in_window") or [])
    failed = int(summary.get("failed", 0))
    hypothesis = (
        "日终收口（%s）：快照失败 %d 条 vs 窗口 %gh 新增 %d / 持续 %d / 已修复 %d —— "
        "窗口三分类是否比快照单点更能反映真实变化"
        % (str(summary.get("generated_at", "?")), failed, float(hours),
           new_n, persist_n, fixed_n)
    )
    card = E.create_card(
        hypothesis=hypothesis,
        baseline_diff={},
        candidate_diff={"method/task_exit_audit":
                        "window_diff(%gh)" % float(hours)},
        verifier=["账本至少两次扫描（不足即如实报错，不硬算）",
                  "非失败码（exit 3 / 0x800710E0 / 0xC000013A / 0x41001 / 0x41306）不计失败",
                  "快照数字与窗口数字可回溯到同一账本"],
        budget=1,
    )
    E.record_metrics(card, "baseline", {
        "failed_snapshot_lower_better": float(failed),
        "new_failures_lower_better": float(new_n),
        "persisting_failures_lower_better": float(persist_n),
        "fixed_in_window": float(fixed_n),
    })
    return card


def run(hours: float = 24.0,
        experiments_root: Optional[Path] = None,
        experience_root: Optional[Path] = None,
        signed_by: str = "",
        signature: Optional[dict] = None,
        cue: str = "",
        lesson: str = "",
        confidence: float = 0.5,
        now: Optional[float] = None) -> dict:
    """跑一轮收口。返回结构化结果；不抛业务异常（签名/校验异常原样抛出）。"""
    summary = ta.summary_only()
    if isinstance(summary, dict) and "error" in summary:
        return {"ok": False, "stage": "summary",
                "error": "无可用扫描摘要，先跑 task_exit_audit --scan：%s"
                         % summary.get("error")}
    window = ta.window_diff(hours=hours)
    if isinstance(window, dict) and "error" in window:
        return {"ok": False, "stage": "window",
                "error": "窗口对比不可用（%s）；不生成半成品卡" % window.get("error")}

    card = build_card(summary, window, hours)
    card_path = E.save(card, root=experiments_root or DEFAULT_EXPERIMENTS_ROOT)

    out = {
        "ok": True,
        "card_id": card.card_id,
        "card_path": str(card_path),
        "card_status": card.status,
        "hours": float(hours),
        "summary": {
            "generated_at": summary.get("generated_at"),
            "total_scanned": summary.get("total_scanned"),
            "failed": summary.get("failed"),
            "upstream_hold": summary.get("upstream_hold"),
            "disabled": summary.get("disabled"),
        },
        "window": {
            "window_hours": window.get("window_hours"),
            "new_failures": window.get("new_failures"),
            "persisting_failures": window.get("persisting_failures"),
            "fixed_in_window": window.get("fixed_in_window"),
        },
        "decision_required": True,
        "experience": None,
        "note": "只产观察卡（未结案、未入库）；结案/入库须 signed_by + 签名（Z2/Z4 签字门）",
    }

    if not (signed_by or "").strip():
        return out

    if not (cue or "").strip() or not (lesson or "").strip():
        throw = {"ok": False, "stage": "signature_input",
                 "error": "签字入库需要 --cue 与 --lesson（经验内容由调用方撰写，本工具不代写）",
                 "card_id": card.card_id, "card_path": str(card_path)}
        return throw

    evidence = ("closeout card=%s；快照失败 %s；窗口 %gh 新增 %d / 持续 %d / 已修复 %d"
                % (card.card_id, summary.get("failed"), float(hours),
                   len(window.get("new_failures") or []),
                   len(window.get("persisting_failures") or []),
                   len(window.get("fixed_in_window") or [])))
    entry = S.write_entry(
        domain="supervision", cue=cue, lesson=lesson, evidence=evidence,
        confidence=confidence, signed_by=signed_by,
        root=experience_root, signature=signature, now=now)
    out["experience"] = entry.as_dict()
    out["decision_required"] = False
    out["note"] = "已签字 → 经验入库 %s（验签：%s）" % (
        entry.entry_id, "verified" if entry.verified else "unverified")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="日终收口轮：真实账本 → Z2 观察卡（可签字入 Z4）")
    ap.add_argument("--hours", type=float, default=24.0, help="窗口小时数（默认 24）")
    ap.add_argument("--json", action="store_true", help="打印 JSON 结果")
    ap.add_argument("--signed-by", default="", help="签字人（不传则只产卡，不结案）")
    ap.add_argument("--signature-file", default="", help="签名 JSON 文件（scheme/sig/ts/nonce）")
    ap.add_argument("--cue", default="", help="经验 cue（签字入库时必填）")
    ap.add_argument("--lesson", default="", help="经验 lesson（签字入库时必填）")
    ap.add_argument("--confidence", type=float, default=0.5)
    ap.add_argument("--experiments-root", default="", help="实验卡落盘根（测试用）")
    ap.add_argument("--experience-root", default="", help="经验库落盘根（测试用）")
    args = ap.parse_args(argv)

    signature = None
    if args.signature_file:
        try:
            with open(args.signature_file, "r", encoding="utf-8") as f:
                signature = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print("[FAIL] 签名文件不可读：%s" % exc, file=sys.stderr)
            return 1

    result = run(
        hours=args.hours,
        experiments_root=Path(args.experiments_root) if args.experiments_root else None,
        experience_root=Path(args.experience_root) if args.experience_root else None,
        signed_by=args.signed_by, signature=signature,
        cue=args.cue, lesson=args.lesson, confidence=args.confidence)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result.get("ok"):
        print("[SKIP] %s" % result.get("error"))
    else:
        w = result["window"]
        print("实验卡 %s 已落盘（status=%s）：%s"
              % (result["card_id"], result["card_status"], result["card_path"]))
        print("窗口 %gh：新增 %d / 持续 %d / 已修复 %d"
              % (w["window_hours"], len(w["new_failures"]),
                 len(w["persisting_failures"]), len(w["fixed_in_window"])))
        print(result["note"])

    if not result.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
