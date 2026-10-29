# -*- coding: utf-8 -*-
"""zhuge_c1_decisions.py — P1-4 与 C1 质询通道打通。

映射（只读 C1 账本 → 写 P1-4 决策账本；不改 C1 账本）：
- 质询卡 created/open   → 决策记录 proposed（source_card=card_id）
- closed accepted       → 决策记录 approved（by=响应/结案人）
- closed rejected       → 决策记录 rejected
- answered              → 保持 proposed（待结案）；如已存在则跳过
幂等：按 source_card 查找已有记录，不重复创建；可 --dry-run。

用法：
  python tools/zhuge_c1_decisions.py --sync [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

REPO_ROOT = Path(__file__).resolve().parent.parent
C1_LEDGER_REL = Path("work") / "zhuge_challenge" / "cards.jsonl"


def _tools_dir() -> str:
    return str(Path(__file__).resolve().parent)


def _root() -> Path:
    override = os.environ.get("ZHUGE_C1DEC_REPO")
    return Path(override).resolve() if override else REPO_ROOT


def _c1_and_dr():
    tools = _tools_dir()
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import zhuge_challenge as c1  # noqa: E402
    import zhuge_decision_record as dr  # noqa: E402
    return c1, dr


def _review_at(card_date: str) -> str:
    try:
        d = datetime.strptime(str(card_date)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        d = datetime.now(timezone(timedelta(hours=8)))
    return (d + timedelta(days=7)).strftime("%Y-%m-%d")


def _question_text(card: dict) -> str:
    qs = card.get("ref_questions") or []
    lines = []
    for q in qs[:8]:
        text = (q.get("question") or "").strip()
        target = q.get("target_task") or ""
        if text:
            lines.append("- %s%s" % (text, ("（任务 %s）" % target) if target else ""))
    snap = card.get("target_plan_snapshot") or {}
    if snap.get("path"):
        lines.append("（快照：%s，source=%s）" % (snap.get("path"), snap.get("source_kind")))
    return "\n".join(lines) or "（质询卡无问题文本）"


def _recommendation(card: dict) -> str:
    return ("请田丰回应质询：接受→按卡整改并留痕；拒绝→说明理由并留痕；"
            "本记录只做结构化决策留痕，不派单、不执行。")


def _find_existing(dr, card_id: str) -> dict | None:
    rows = [r for r in dr._fold(dr._load_events()).values()
            if r.get("source_card") == card_id]
    rows.sort(key=lambda r: str(r.get("id")))
    return rows[-1] if rows else None


def sync_card(card: dict, dr=None, dry_run: bool = False) -> dict:
    _, dr = _c1_and_dr() if dr is None else (None, dr)
    card_id = card.get("card_id")
    if not card_id:
        return {"ok": False, "error": "card_id_missing"}
    existing = _find_existing(dr, card_id)
    actions = []
    if existing is None:
        create_kwargs = dict(
            title="质询卡 %s 处理决定" % card_id,
            question=_question_text(card),
            recommendation=_recommendation(card),
            alternatives=["接受整改", "拒绝并说明理由"],
            evidence=[str(card.get("target_plan_snapshot", {}).get("path") or ""),
                      "severity=%s" % card.get("severity"),
                      "questions=%d" % len(card.get("ref_questions") or [])],
            risks=["质询被忽略/逾期未响应", "整改未留痕"],
            confidence="unknown",
            tags=["c1", "challenge", card_id],
            source_card=card_id,
            review_at=_review_at(card.get("date")),
        )
        if dry_run:
            rid = "(dry-run)"
            actions.append({"action": "would_create", "card": card_id})
        else:
            out = dr.create(**create_kwargs)
            if not out.get("ok"):
                return {"ok": False, "card": card_id, "error": out.get("error")}
            rid = out["id"]
            actions.append({"action": "created", "id": rid})
            existing = dr.show(rid).get("record") or {}
    else:
        rid = existing.get("id")
    status = (existing or {}).get("status")
    if card.get("status") == "closed" and status not in ("approved", "rejected",
                                                         "implemented", "superseded"):
        outcome = card.get("outcome")
        target = "approved" if outcome == "accepted" else "rejected"
        by = card.get("responded_by") or card.get("closed_by") or "c1-system"
        note = "C1 结案 %s：%s" % (outcome, (card.get("close_note") or "")[:200])
        if dry_run:
            actions.append({"action": "would_decide", "id": rid, "status": target})
        else:
            out = dr.decide(rid, target, by=by, note=note)
            if not out.get("ok"):
                return {"ok": False, "card": card_id, "id": rid,
                        "error": out.get("error")}
            actions.append({"action": "decided", "id": rid, "status": target})
            if outcome == "accepted":
                dr.update(rid, outcome="C1 结案 accepted：%s" % ((card.get("close_note") or "")[:400]))
            # 决策/回填后重新取记录；否则 sync_card 返回的是决策前的 stale status。
            existing = dr.show(rid).get("record") or existing
    return {"ok": True, "card": card_id, "id": rid, "status": (existing or {}).get("status"),
            "actions": actions}


def sync_all(ledger: Path | None = None, dry_run: bool = False) -> dict:
    c1, dr = _c1_and_dr()
    path = Path(ledger) if ledger else (_root() / C1_LEDGER_REL)
    if not path.is_file():
        return {"ok": True, "cards": 0, "actions": [], "ledger": str(path)}
    cards, _events = c1.load_ledger(path)
    rows = []
    for cid in sorted(cards):
        rows.append(sync_card(cards[cid], dr=dr, dry_run=dry_run))
    out = {"ok": True, "cards": len(cards), "ledger": str(path),
           "created": sum(1 for r in rows for a in r.get("actions", []) if a["action"] == "created"),
           "decided": sum(1 for r in rows for a in r.get("actions", []) if a["action"] == "decided"),
           "rows": rows}
    # C3↔P1-4（可选）：同一次同步里回填采纳 domain 信号
    if os.environ.get("ZHUGE_C3_DECISION_SYNC", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            import zhuge_c3_decisions
            out["c3"] = zhuge_c3_decisions.sync_adoptions(dry_run=dry_run)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/zhuge_c1_decisions.py", exc)
    return out


def enabled() -> bool:
    return os.environ.get("ZHUGE_C1_DECISION_SYNC", "").strip().lower() in ("1", "true", "yes", "on")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C1 ↔ decision record bridge")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.sync:
        print(json.dumps({"ok": True, "enabled": enabled(),
                          "usage": "--sync [--dry-run] [--ledger PATH]"},
                         ensure_ascii=False, indent=2))
        return 0
    out = sync_all(Path(args.ledger) if args.ledger else None, dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
