# -*- coding: utf-8 -*-
"""曹操军团实战就绪审计（只读，2026-10-29 WO-LEGION-READY）。

检查：
1) 角色是否到位（11 席 / 前端/后端/AI/量化/运维/文档/架构覆盖 / 2 组 / 汇报链）；
2) 能力卡基础信息是否齐（专长/工具/职级/任务域/学习状态）；
3) 任务分派机制是否存在（devtask / tianfeng_dispatch / dev_orchestrator / 总纲）；
4) 真席位执行统计（新单 owner 是否 DSH-Lead 代做）。

用法：
  python -X utf8 tools/legion_readiness.py --json
  python -X utf8 tools/legion_readiness.py --md
  python -X utf8 tools/legion_readiness.py --out docs/dev/legion_readiness.md
  python -X utf8 tools/legion_readiness.py --check   # 角色/链/能力卡/机制齐 rc0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "backend": "后端",
    "frontend": "前端/UI",
    "ai": "AI/模型",
    "quant": "量化",
    "devops": "运维/SRE",
    "docs": "文档",
    "architecture": "架构",
}
PROXY_OWNERS = {"DSH-Lead"}
CUTOFF = "2026-10-27T16:00"


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _profiles(org):
    for d in (org or {}).get("departments") or []:
        if d.get("dept") == "dev":
            return [p for p in d.get("agent_profiles") or []
                    if isinstance(p, dict) and p.get("agent_id")]
    return []


def _cards(root, profiles):
    learn_dir = root / "agent_platform" / "config" / "seat_learning"
    cards = []
    for p in profiles:
        sid = p.get("agent_id", "")
        attrs = p.get("attributes") or {}
        tools = attrs.get("tools_allowed") or {}
        cfg = _load(learn_dir / (sid + ".json"), {}) if learn_dir.is_dir() else {}
        cards.append({
            "id": sid,
            "name": p.get("cn_name") or "",
            "role_title": p.get("role_title") or "",
            "discipline": p.get("discipline") or "",
            "grade": p.get("grade") or "",
            "group": p.get("group") or "",
            "report_to": p.get("report_to") or "",
            "role_profile": p.get("role_profile") or "",
            "specialties": list(attrs.get("specialties") or []),
            "tools_read": list(tools.get("read") or []),
            "tools_write": list(tools.get("write") or []),
            "matrix": bool(p.get("matrix_assignments")),
            "learning": (cfg.get("modules") and "learning:" + str(cfg.get("modules"))) or "未建学习计划",
            "card_ready": bool(attrs.get("specialties")) and bool(p.get("grade")) and bool(p.get("report_to")),
        })
    return cards


def compute(root=None):
    root = Path(root or ROOT)
    org = _load(root / "agent_platform" / "registry" / "org.json", {})
    tasks = _load(root / ".worktrees" / "tasks.json", {})
    profiles = _profiles(org)
    cards = _cards(root, profiles)
    ids = {c["id"] for c in cards}

    disciplines = {}
    for c in cards:
        disciplines[c["discipline"]] = disciplines.get(c["discipline"], 0) + 1
    role_missing = [k for k in REQUIRED if disciplines.get(k, 0) < 1]
    leads = [c for c in cards if c["id"] in ("DEV1", "DEV5")]
    chain_bad = []
    for c in cards:
        rt = c["report_to"]
        if rt and rt not in ids and rt not in ("A0", "老田"):
            chain_bad.append(c["id"] + "->" + rt)
    cards_bad = [c["id"] for c in cards if not c["card_ready"]]
    learning_bad = [c["id"] for c in cards if c["learning"] == "未建学习计划"]

    dev_done = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict):
            continue
        if (t.get("dept") or "").lower() != "dev":
            continue
        if t.get("status") == "done" and t.get("merged"):
            dev_done.append({"id": tid, "owner": (t.get("owner") or "").strip(),
                             "created": str(t.get("created") or "")[:16]})
    new_dev = [r for r in dev_done if r["created"] >= CUTOFF]
    new_seat = [r for r in new_dev if r["owner"] not in PROXY_OWNERS]
    seat_share = round(len(new_seat) / len(new_dev), 4) if new_dev else None
    inflight = [tid for tid, t in (tasks or {}).items()
                if isinstance(t, dict) and (t.get("dept") or "").lower() == "dev"
                and t.get("status") in ("doing", "review")]

    mechanism_files = ["tools/devtask.py", "tools/tianfeng_dispatch.py",
                       "tools/dev_orchestrator.py"]
    mechanism = {f: (root / f).is_file() for f in mechanism_files}

    role_ok = not role_missing
    chain_ok = not chain_bad
    cards_ok = not cards_bad
    mech_ok = all(mechanism.values())
    true_exec_ok = seat_share is not None and seat_share >= 0.5
    return {
        "schema": "legion_readiness/v1",
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "seats": len(cards),
        "disciplines": disciplines,
        "required": REQUIRED,
        "role_missing": role_missing,
        "groups": (org.get("departments") and [
            {"id": g.get("id"), "lead": g.get("lead"),
             "members": g.get("members")} for d in org["departments"]
            if d.get("dept") == "dev" for g in (d.get("groups") or [])]) or [],
        "chain_bad": chain_bad,
        "cards": cards,
        "cards_bad": cards_bad,
        "learning_bad": learning_bad,
        "inflight_dev": len(inflight),
        "dev_done": len(dev_done),
        "new_dev_done": len(new_dev),
        "new_seat_exec": len(new_seat),
        "seat_exec_share_new": seat_share,
        "mechanism": mechanism,
        "verdict": {
            "role_ok": role_ok, "chain_ok": chain_ok, "cards_ok": cards_ok,
            "mechanism_ok": mech_ok, "true_exec_ok": true_exec_ok,
            "ready_for_combat": role_ok and chain_ok and cards_ok and mech_ok,
        },
    }


def render_md(m):
    lines = ["# 曹操军团实战就绪审计", "",
             "- as-of：%s｜席位 %d｜在制 dev %d｜新单真席位执行 %s"
             % (m["generated_at"], m["seats"], m["inflight_dev"],
                ("%s/%s" % (m["new_seat_exec"], m["new_dev_done"]))
                if m["new_dev_done"] else "—"), "",
             "## 一、角色到位", ""]
    for k, cn in m["required"].items():
        n = m["disciplines"].get(k, 0)
        lines.append("- %s：%d 席 %s" % (cn, n, "✅" if n else "❌"))
    lines += ["", "## 二、汇报链/分组", ""]
    for g in m["groups"]:
        lines.append("- %s · 组长 %s · 成员 %s"
                     % (g.get("id"), g.get("lead"),
                        "、".join(g.get("members") or [])))
    lines += ["", "## 三、能力卡", "",
              "| 席位 | 角色 | 域 | 职级 | 组 | 专长 | 工具写域 | 学习 |",
              "|---|---|---|---|---|---|---|---|"]
    for c in m["cards"]:
        lines.append("| %s %s | %s | %s | %s | %s | %d | %d | %s |"
                     % (c["id"], c["name"], c["role_title"], c["discipline"],
                        c["grade"], c["group"], len(c["specialties"]),
                        len(c["tools_write"]), c["learning"]))
    lines += ["", "## 四、任务分派机制", ""]
    for k, v in m["mechanism"].items():
        lines.append("- %s：%s" % (k, "✅" if v else "❌"))
    lines += ["", "## 五、判定", ""]
    for k, v in m["verdict"].items():
        lines.append("- %s：%s" % (k, "✅" if v else ("⚠️" if k == "true_exec_ok" else "❌")))
    lines += ["", "> 能力卡可生成 ≠ 已认证；学习计划仅 DEV7 起步。"
                  "真席位执行率按 block 后新单 owner 统计。"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    m = compute(args.root)
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=1))
    if args.md or args.out:
        txt = render_md(m)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(txt, encoding="utf-8")
            print("written", args.out)
        else:
            print(txt)
    if args.check:
        v = m["verdict"]
        ok = v["role_ok"] and v["chain_ok"] and v["cards_ok"] and v["mechanism_ok"]
        print("READINESS", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
