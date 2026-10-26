# -*- coding: utf-8 -*-
"""模型覆盖生效编排（L2 门控）：只生成计划 + 落审批 marker，**不自动改真源/不自动重启**。

定位：
  · 审批闭环批准 `model_change` 后，覆盖配置写 data/zhaoyun_model_overrides.json；
  · 本模块把覆盖转成"L2 生效计划"（改 org/bundle 真源 + 载体重启需人工执行），
    并以 approved_by + confirm 双条件落 marker（可审计），防止误触；
  · 真源修改/重启属 L2：本模块**永远不执行**，只输出命令与回执。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
OVERRIDES = PLATFORM / "data" / "zhaoyun_model_overrides.json"
MARKER = PLATFORM / "data" / "model_overrides_applied.json"
CANDIDATES = ["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro",
              "ollama/qwen3.5:9b", "glm/glm-5.3-flash"]
CONFIRM = "APPROVE_L2_MODEL"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def plan() -> dict:
    data = _read(OVERRIDES) or {}
    rows = [r for r in data.get("overrides") or [] if isinstance(r, dict)]
    latest: dict = {}
    for r in rows:
        seat = str(r.get("seat") or "")
        if seat:
            latest[seat] = r
    pending = []
    for seat, r in latest.items():
        cand = str(r.get("candidate") or "")
        steps = [
            "人工（L2）：修改 agent_platform/registry/org.json 或 portal/agent_ide_bundle.json 的 %s 绑定 → %s" % (seat, cand),
            "人工（L2）：重启对应载体（8796 使用哨兵 flag + LegionOps 任务；其他载体按 SOP）",
            "人工：回执写入工单 note（命令/结果/回滚点）",
        ]
        pending.append({"seat": seat, "candidate": cand,
                        "candidate_valid": cand in CANDIDATES,
                        "approved_by": r.get("approved_by") or "",
                        "request_id": r.get("request_id") or "",
                        "steps": steps})
    digest = hashlib.sha256(json.dumps(pending, sort_keys=True,
                                       ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {"ok": True, "count": len(pending), "pending": pending,
            "plan_digest": digest,
            "l2_required": True,
            "note": "本计划只读；生效需老田明确批准后人工执行（改真源+重启= L2）"}


def apply(approved_by: str = "", confirm: str = "") -> dict:
    approver = str(approved_by or "").strip()
    conf = str(confirm or "").strip()
    if not approver:
        return {"ok": False, "error": "l2_approval_required",
                "note": "必须提供 approved_by（老田本人）"}
    if conf != CONFIRM:
        return {"ok": False, "error": "l2_confirm_required",
                "expected_confirm": CONFIRM,
                "note": "二次确认不匹配：本动作 L2，需人工逐字确认"}
    p = plan()
    if not p["count"]:
        return {"ok": False, "error": "no_pending_overrides", "plan": p}
    marker = {"schema": "zhaoyun_model_apply_marker/v1",
              "approved_by": approver, "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "plan_digest": p["plan_digest"], "pending": p["pending"],
              "executed": False,
              "note": "仅登记 L2 批准；真源修改/重启由人执行，Agent 不自动做"}
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "applied": False, "marker_written": str(MARKER),
            "plan": p, "marker": marker,
            "note": "L2 批准已登记；请人工执行计划步骤（Agent 不自动改真源/不自动重启）"}


def latest() -> dict:
    m = _read(MARKER)
    return {"ok": True, "marker": m, "plan": plan()}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args()
    if args.apply:
        print(json.dumps(apply(args.approved_by, args.confirm), ensure_ascii=False, indent=1))
    elif args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(latest(), ensure_ascii=False, indent=1))
