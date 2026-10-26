# -*- coding: utf-8 -*-
"""日终收口轮（WO-LEGION-P0FIX-20261026 首版 · WO-LEGION-CLOCK-20261026 加固）。

链路：真实账本（task_exit_audit 摘要 + window_diff）→ Z2 观察卡（kind=observation）
→ `E.save()` 原子落盘 `agent_platform/data/experiments/` → `/api/evolution` 可见。

本版（CLOCK 单）新增：
  · **as-of 守卫**：摘要龄 >6h 告警、>12h 拒出卡（防"拿旧账本当 24h 窗口"）；
  · **--if-changed**：摘要+窗口摘要哈希去重，避免同日重复跑产生重复卡；
  · **保留策略**：超过 keep 张的旧卡移入 `_expired/`（移动而非删除，可回滚）；
  · 卡内写入 `kind=observation / decision_required / window_summary / created_at`。

纪律（与赵云进化层六条硬规则一致）：
  · **不造数**：卡片内容全部来自真实账本；窗口不可用 / 摘要过期即如实报错；
  · **不越签字门**：默认只产卡、不结案、不写经验（decision_required=True）；
    显式传 `signed_by` 且给出 `cue/lesson` 时才走 Z4 入库（signature_guard 渐进验签）；
  · 不新增判据、不改 Z5/Z4 闸门逻辑。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
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
DEFAULT_WARN_AGE_HOURS = 6.0
DEFAULT_MAX_AGE_HOURS = 12.0
DEFAULT_KEEP = 100


def _parse_iso_ts(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        ts = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts.timestamp()


def summary_age_hours(summary: dict, now: float) -> Optional[float]:
    """摘要生成时刻距今小时数；解析失败返回 None（按"未知"处理，不冒充新鲜）。"""
    ts = _parse_iso_ts(str(summary.get("generated_at", "")))
    if ts is None:
        return None
    return max(0.0, (float(now) - ts) / 3600.0)


def change_digest(summary: dict, window: dict) -> str:
    """摘要+窗口的稳定哈希：只有数据真的变了才值得再落一张卡。"""
    payload = {
        "summary": {k: summary.get(k) for k in
                    ("generated_at", "total_scanned", "ok", "failed",
                     "upstream_hold", "disabled", "never_run")},
        "window": {k: window.get(k) for k in
                   ("window_hours", "new_failures", "persisting_failures",
                    "fixed_in_window")},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _state_path(experiments_root: Optional[Path],
                state_file: Optional[Path]) -> Path:
    if state_file is not None:
        return Path(state_file)
    root = Path(experiments_root) if experiments_root else DEFAULT_EXPERIMENTS_ROOT
    return root / ".closeout_state.json"


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _card_created_at(p: Path) -> str:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return str(data.get("created_at") or data.get("card_id") or "")
    except (OSError, ValueError):
        return ""


def prune_cards(experiments_root: Path, keep: int = DEFAULT_KEEP) -> int:
    """保留最新 keep 张卡，其余移入 _expired/（移动而非删除，可回滚）。"""
    if keep <= 0:
        return 0
    root = Path(experiments_root)
    if not root.exists():
        return 0
    # CLOCKFIX：.closeout_state.json 等点文件不是卡片，绝不参与轮转
    cards = [p for p in root.glob("*.json")
             if p.is_file() and not p.name.startswith(".")]
    cards.sort(key=lambda p: (_card_created_at(p), p.stat().st_mtime))
    overflow = cards[:-keep] if len(cards) > keep else []
    if not overflow:
        return 0
    expired = root / "_expired"
    expired.mkdir(parents=True, exist_ok=True)
    moved = 0
    for p in overflow:
        try:
            os.replace(p, expired / p.name)
            moved += 1
        except OSError:
            continue
    return moved


def build_card(summary: dict, window: dict, hours: float) -> E.ExperimentCard:
    """用真实账本数字构造 Z2 观察卡（不构成结论）。"""
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
        kind="observation",
    )
    E.record_metrics(card, "baseline", {
        "failed_snapshot_lower_better": float(failed),
        "new_failures_lower_better": float(new_n),
        "persisting_failures_lower_better": float(persist_n),
        "fixed_in_window": float(fixed_n),
    })
    card.decision_required = True
    card.window_summary = {
        "window_hours": float(hours),
        "new_failures": new_n,
        "persisting_failures": persist_n,
        "fixed_in_window": fixed_n,
        "failed_snapshot": failed,
        "summary_generated_at": summary.get("generated_at"),
    }
    return card


def run(hours: float = 24.0,
        experiments_root: Optional[Path] = None,
        experience_root: Optional[Path] = None,
        signed_by: str = "",
        signature: Optional[dict] = None,
        cue: str = "",
        lesson: str = "",
        confidence: float = 0.5,
        now: Optional[float] = None,
        warn_age_hours: float = DEFAULT_WARN_AGE_HOURS,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
        if_changed: bool = False,
        state_file: Optional[Path] = None,
        keep: int = DEFAULT_KEEP) -> dict:
    """跑一轮收口。返回结构化结果；不抛业务异常（签名/校验异常原样抛出）。"""
    summary = ta.summary_only()
    if isinstance(summary, dict) and "error" in summary:
        return {"ok": False, "stage": "summary",
                "error": "无可用扫描摘要，先跑 task_exit_audit --scan：%s"
                         % summary.get("error")}

    now_ts = float(now) if now is not None else time.time()
    age = summary_age_hours(summary, now_ts)
    if age is not None and age > float(max_age_hours):
        return {"ok": False, "stage": "stale",
                "error": "扫描摘要已 %.1fh 未更新（>%.0fh），拒绝拿旧账本当窗口；"
                         "先跑 task_exit_audit --scan" % (age, float(max_age_hours)),
                "summary_age_hours": round(age, 2)}
    stale_warning = None
    if age is None:
        stale_warning = "summary_generated_at 不可解析：as-of 未知（不冒充新鲜）"
    elif age > float(warn_age_hours):
        stale_warning = ("扫描摘要龄 %.1fh（>%.0fh 告警线）：数字偏旧，仅作观察"
                         % (age, float(warn_age_hours)))

    window = ta.window_diff(hours=hours)
    if isinstance(window, dict) and "error" in window:
        return {"ok": False, "stage": "window",
                "error": "窗口对比不可用（%s）；不生成半成品卡" % window.get("error")}

    digest = change_digest(summary, window)
    sp = _state_path(experiments_root, state_file)
    state = _load_state(sp)
    if if_changed and state.get("last_digest") == digest:
        return {"ok": True, "skipped": True, "reason": "unchanged",
                "digest": digest, "summary_age_hours": None if age is None else round(age, 2),
                "note": "摘要与窗口与上次一致（--if-changed），未重复落卡"}

    card = build_card(summary, window, hours)
    card_path = E.save(card, root=experiments_root or DEFAULT_EXPERIMENTS_ROOT)
    root = Path(experiments_root) if experiments_root else DEFAULT_EXPERIMENTS_ROOT
    state.update({"last_digest": digest, "last_run_at": card.created_at,
                  "last_card_id": card.card_id})
    _save_state(sp, state)
    pruned = prune_cards(root, keep=keep)

    out = {
        "ok": True,
        "skipped": False,
        "card_id": card.card_id,
        "card_path": str(card_path),
        "card_status": card.status,
        "created_at": card.created_at,
        "kind": card.kind,
        "decision_required": card.decision_required,
        "window_summary": card.window_summary,
        "hours": float(hours),
        "digest": digest,
        "summary_age_hours": None if age is None else round(age, 2),
        "stale_warning": stale_warning,
        "pruned": pruned,
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
        "experience": None,
        "note": "只产观察卡（未结案、未入库）；结案/入库须 signed_by + 签名（Z2/Z4 签字门）",
    }

    if not (signed_by or "").strip():
        return out

    if not (cue or "").strip() or not (lesson or "").strip():
        return {"ok": False, "stage": "signature_input",
                "error": "签字入库需要 --cue 与 --lesson（经验内容由调用方撰写，本工具不代写）",
                "card_id": card.card_id, "card_path": str(card_path)}

    evidence = ("closeout card=%s；快照失败 %s；窗口 %gh 新增 %d / 持续 %d / 已修复 %d；digest=%s"
                % (card.card_id, summary.get("failed"), float(hours),
                   len(window.get("new_failures") or []),
                   len(window.get("persisting_failures") or []),
                   len(window.get("fixed_in_window") or []), digest[:16]))
    entry = S.write_entry(
        domain="supervision", cue=cue, lesson=lesson, evidence=evidence,
        confidence=confidence, signed_by=signed_by,
        root=experience_root, signature=signature, now=now_ts)
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
    ap.add_argument("--warn-age-hours", type=float, default=DEFAULT_WARN_AGE_HOURS)
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--if-changed", action="store_true",
                    help="摘要+窗口与上次一致则不重复落卡")
    ap.add_argument("--state-file", default="", help="去重状态文件（默认 <root>/.closeout_state.json）")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help="保留最新 N 张卡，其余移入 _expired/（默认 100，0=不清理）")
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
        cue=args.cue, lesson=args.lesson, confidence=args.confidence,
        warn_age_hours=args.warn_age_hours, max_age_hours=args.max_age_hours,
        if_changed=args.if_changed,
        state_file=Path(args.state_file) if args.state_file else None,
        keep=args.keep)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not result.get("ok"):
        print("[SKIP] %s" % result.get("error"))
    elif result.get("skipped"):
        print("[SKIP] %s" % result.get("note"))
    else:
        w = result["window"]
        print("观察卡 %s 已落盘（kind=%s status=%s created_at=%s）：%s"
              % (result["card_id"], result["kind"], result["card_status"],
                 result["created_at"], result["card_path"]))
        print("窗口 %gh：新增 %d / 持续 %d / 已修复 %d"
              % (w["window_hours"], len(w["new_failures"]),
                 len(w["persisting_failures"]), len(w["fixed_in_window"])))
        if result.get("stale_warning"):
            print("[WARN] %s" % result["stale_warning"])
        print(result["note"])

    if not result.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
