# -*- coding: utf-8 -*-
"""D4–D5 试点：M4/v1.1 S2 签字卡（构建 + 可选发送，回调走 approval_control）。\n\n生产判例：签字卡须 critical=True（否则夜间 silent window 会静默丢弃——2026-10-30 00:39 实测）。

默认 dry-run（只打印卡片摘要）。--send 经 portal/feishu_card_v2.send_card（bot DM）。
不写审批台账（等老田点按钮回调才会写 approval）。
用法：
  python tools/s2_sign_pilot.py --dry-run
  python tools/s2_sign_pilot.py --send
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "portal"))

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("tools_approval_card", str(REPO / "tools" / "approval_card.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_card = _mod.build_card

TASK = "M4/v1.1 验收签字（试点）"
SUMMARY = ("M4 投研与模拟执行线（2026-10-17 七门制总验收，417/417 全绿）"
           " + v1.1 真实量化聚合实施（策略/影子/回测只读、白名单、k>=5 掩码，squash 0e2cb4aff）。"
           "试点：确认 S2 卡片按钮/回调链路。")
EVIDENCE_HASH = "0e2cb4aff"
EVIDENCE_URL = "http://127.0.0.1:8734/devops"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--receive-id", default="")
    ap.add_argument("--receive-id-type", default="open_id")
    args = ap.parse_args(argv)
    card = build_card(TASK, SUMMARY, level="S2", evidence_hash=EVIDENCE_HASH,
                      evidence_url=EVIDENCE_URL)
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "task": TASK, "evidence_hash": EVIDENCE_HASH,
           "card_title": card["header"]["title"]["content"],
           "buttons": [b["text"]["content"] for col in card["body"]["elements"]
                       if col.get("tag") == "column_set" for x in col["columns"]
                       for b in x["elements"]],
           "dry_run": not args.send}
    if not args.send:
        out["note"] = "dry-run：未发送；加 --send 发送到 bot DM"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    from feishu_card_v2 import send_card  # noqa: E402
    rid = args.receive_id or None
    ok, why = send_card(card, receive_id=rid, receive_id_type=args.receive_id_type, critical=True)
    out.update({"sent": bool(ok), "why": str(why)[:200]})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = REPO / "work" / "audit-20261030" / ("s2_pilot_send_%s.json" % stamp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["receipt"] = str(p)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
