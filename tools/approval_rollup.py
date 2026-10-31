# -*- coding: utf-8 -*-
"""M4 签字汇总（只读 CLI）——2026-10-31 WO-M4-SIGNERMAP-20261031。

用法：
  python tools/approval_rollup.py <task>
  python tools/approval_rollup.py <task> --json
  python tools/approval_rollup.py <task> --ledger <approvals.jsonl> \
      --config <approval_signers.json>

只读：读 approval JSONL + agent_platform/config/approval_signers.json，
调用 ``portal/approval_control.evaluate_signoff`` 计算该 task 的签收状态，
输出 signed（已签角色）/ missing（缺签角色）/ complete（是否四方齐）。
**不写台账、不改审批状态、不伪造签字**；未命中映射的 open_id 一律按
roles_unresolved 处理（不猜）。退出码 0=汇总成功（不论是否签齐，看 complete）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORTAL = REPO / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import approval_control as AC  # noqa: E402  —— 同仓纯函数判定真源

DEFAULT_CONFIG = REPO / "agent_platform" / "config" / "approval_signers.json"


def _read_jsonl(path):
    """读 JSONL（坏行跳过；文件缺失 → []）。只读，不创建/不修改。

    utf-8-sig：容忍 Windows/PowerShell 写出的 UTF-8 BOM（否则首行解析失败被跳过）。
    """
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def load_signers(path=None):
    """读 open_id→角色映射（与 feishu_bot_ws 同一契约）；缺失/坏 JSON → {}，不猜。"""
    p = Path(path) if path else DEFAULT_CONFIG
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("map"), dict):
        return {}
    mapping = {}
    for key, role in doc["map"].items():
        op = str(key or "").strip()
        role = str(role).strip().upper() if isinstance(role, str) else ""
        if op and role:
            mapping[op] = role
    return mapping


def rollup(task, ledger=None, config=None):
    """某 task 的签收汇总 dict（只读）。

    键：task/ledger/config/role_map_size/state/complete/signed/signed_signers/
    missing/required/signed_count/reason。
    """
    task = str(task or "").strip()
    ledger = str(ledger or AC.LEDGER)
    config = str(config or DEFAULT_CONFIG)
    role_of = load_signers(config)
    signoff = AC.evaluate_signoff(_read_jsonl(ledger), task,
                                  role_of=role_of or None)
    return {
        "task": task,
        "ledger": ledger,
        "config": config,
        "role_map_size": len(role_of),
        "state": signoff["state"],
        "complete": signoff["state"] == "complete",
        "signed": list(signoff.get("signed_roles") or []),
        "signed_signers": [dict(x) for x in (signoff.get("signers") or [])],
        "missing": list(signoff.get("missing_signers") or []),
        "required": list(signoff.get("required_signers") or []),
        "signed_count": signoff.get("distinct_signers", 0),
        "reason": signoff.get("reason") or "",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 签字只读汇总")
    ap.add_argument("task", help="工单/任务标识（approval 台账 task 字段）")
    ap.add_argument("--ledger", default=None,
                    help="approval JSONL 路径（默认 agent_platform/data/approvals.jsonl）")
    ap.add_argument("--config", default=None,
                    help="signer 映射路径（默认 agent_platform/config/approval_signers.json）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)
    data = rollup(args.task, args.ledger, args.config)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print("task=%s complete=%s state=%s signed=%s missing=%s reason=%s"
              % (data["task"], data["complete"], data["state"],
                 ",".join(data["signed"]) or "-",
                 ",".join(data["missing"]) or "-",
                 data["reason"] or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
