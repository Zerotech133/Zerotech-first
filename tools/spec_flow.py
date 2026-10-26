# -*- coding: utf-8 -*-
"""spec-driven-delivery：规范→计划→任务→收敛 的产物链与检查。

用法：
  python -X utf8 tools/spec_flow.py init <id> --title "..." [--owner 田丰] [--root DIR]
  python -X utf8 tools/spec_flow.py check <id> [--json] [--root DIR]
  python -X utf8 tools/spec_flow.py list [--json] [--root DIR]
产物默认落 `deliverables/specs/<id>/`：constitution.md / spec.md / plan.md / tasks.md / converge.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_ROOT = REPO / "deliverables" / "specs"

STAGES = [
    ("constitution.md", ["## 原则", "## 边界", "## 质量门"]),
    ("spec.md", ["## 目标", "## 场景", "## 验收标准", "## 非目标"]),
    ("plan.md", ["## 架构", "## 接口/数据", "## 回滚"]),
    ("tasks.md", ["## 任务清单"]),
    ("converge.md", ["## 证据", "## 结论"]),
]

TEMPLATES = {
    "constitution.md": """# 项目宪法 · {title}

## 原则
- 单一事实源；先证据后结论。
- 改动可回滚；L2 动作报老田。

## 边界
- 不碰生产数据；不越权发消息/改系统配置。

## 质量门
- 测试/审计/回滚点齐全才收敛。
""",
    "spec.md": """# 需求规格 · {title}

## 目标
（要解决什么问题，成功标准）

## 场景
- 场景 1：

## 验收标准
- [ ] 可测断言 1

## 非目标
- 明确不做什么
""",
    "plan.md": """# 实施计划 · {title}

## 架构
（组件/数据流/依赖）

## 接口/数据
（接口契约/数据结构/迁移）

## 回滚
（回滚步骤/证据）
""",
    "tasks.md": """# 任务清单 · {title}

## 任务清单
| ID | 任务 | Owner | 验收 | 状态 |
|---|---|---|---|---|
| T1 | 第一项 | 田丰 | 可测断言 | doing |
""",
    "converge.md": """# 收敛结论 · {title}

## 证据
（测试/审计/证据包路径）

## 结论
partial
""",
}


def _dir(spec_id: str, root: Path | None = None) -> Path:
    root = Path(root) if root else DEFAULT_ROOT
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", str(spec_id or "")):
        raise ValueError("spec_id 只允许字母数字 ._-，2–80 字符")
    return root / spec_id


def init(spec_id: str, title: str, owner: str = "田丰", root: Path | None = None) -> dict:
    d = _dir(spec_id, root)
    d.mkdir(parents=True, exist_ok=True)
    for name, tpl in TEMPLATES.items():
        f = d / name
        if not f.exists():
            f.write_text(tpl.format(title=title), encoding="utf-8")
    meta = d / "meta.json"
    meta.write_text(json.dumps({"schema": "spec_flow/v1", "id": spec_id, "title": title,
                                "owner": owner,
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "stages": [x[0] for x in STAGES]},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "dir": str(d), "files": [x[0] for x in STAGES]}


def _section_body(text: str, heading: str) -> str:
    m = re.search(re.escape(heading) + r"\s*\n(.*?)(?=\n## |\Z)", text, re.S)
    body = m.group(1).strip() if m else ""
    if body.startswith("（") and body.endswith("）"):
        return ""
    return body


def check(spec_id: str, root: Path | None = None) -> dict:
    d = _dir(spec_id, root)
    stages, missing = [], []
    for name, headings in STAGES:
        f = d / name
        item = {"file": name, "ok": False, "missing": []}
        if not f.exists():
            item["missing"].append("file")
            missing.append({"file": name, "reason": "missing_file"})
            stages.append(item)
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        miss = [h for h in headings if not _section_body(text, h)]
        if name == "tasks.md":
            rows = [ln for ln in text.splitlines() if ln.strip().startswith("| T")]
            if not rows:
                miss.append("任务行")
        if name == "converge.md":
            body = (text.split("## 结论")[-1] or "").strip().lower()
            if not any(x in body for x in ("converged", "partial", "failed")):
                miss.append("结论值(converged/partial/failed)")
            item["verdict"] = "converged" if "converged" in body else (
                "partial" if "partial" in body else "failed" if "failed" in body else "")
        item["missing"] = miss
        item["ok"] = not miss
        if miss:
            missing.append({"file": name, "reason": "; ".join(miss)})
        stages.append(item)
    return {"schema": "spec_check/v1", "id": spec_id, "dir": str(d),
            "ok": all(s["ok"] for s in stages), "stages": stages, "missing": missing}


def list_specs(root: Path | None = None) -> list:
    root = Path(root) if root else DEFAULT_ROOT
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "meta.json").exists():
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except Exception:
                meta = {"id": d.name}
            c = check(d.name, root)
            out.append({"id": d.name, "title": meta.get("title"), "ok": c["ok"],
                        "missing": c["missing"]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 spec-driven-delivery 产物链")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("id")
    i.add_argument("--title", default="")
    i.add_argument("--owner", default="田丰")
    i.add_argument("--root", default="")
    c = sub.add_parser("check")
    c.add_argument("id")
    c.add_argument("--json", action="store_true")
    c.add_argument("--root", default="")
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    l.add_argument("--root", default="")
    args = ap.parse_args(argv)
    root = Path(args.root) if getattr(args, "root", "") else None
    if args.cmd == "init":
        res = init(args.id, args.title or args.id, args.owner, root)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "check":
        res = check(args.id, root)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print("spec %s ok=%s" % (args.id, res["ok"]))
            for s in res["stages"]:
                print("  %-18s %s %s" % (s["file"], "PASS" if s["ok"] else "FAIL",
                                         "; ".join(s["missing"])))
        return 0 if res["ok"] else 1
    rows = list_specs(root)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print("%-40s ok=%s missing=%d" % (r["id"], r["ok"], len(r["missing"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
