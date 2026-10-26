# -*- coding: utf-8 -*-
"""田丰派工回执台账（dispatch-live）。

用途：田丰把拆好的任务派给部门/AI worker 后，落一条 JSONL 回执，便于
`/api/tianfeng` 工作流区块与属性页显示"派给谁/状态/超期/卡点"。

边界：
- 只写 `work/tianfeng_dispatch.jsonl`（可 --path 覆盖）；不写生产数据、不发消息；
- 台账 append-only；状态机 dispatched → accepted → done / blocked；
- L2 动作（生产/客户消息/资金/审批/宿主机）不得由本工具放行，仍报老田。

用法：
  python -X utf8 tools/tianfeng_dispatch.py --to "研发部/DEV1" --task K-xxx \
      --acceptance "测试通过+回滚点" --due 2026-10-30 --status dispatched
  python -X utf8 tools/tianfeng_dispatch.py --list [--json]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _repo_paths import repo_root  # noqa: E402

REPO = repo_root(HERE)
DEFAULT_PATH = REPO / "work" / "tianfeng_dispatch.jsonl"
VALID_STATUS = ("dispatched", "accepted", "done", "blocked")


def _read(path: Path) -> list:
    rows = []
    if not path.exists():
        return rows
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def append(to: str, task: str, acceptance: str = "", due: str = "",
           note: str = "", status: str = "dispatched",
           path: Path | None = None) -> dict:
    """追加一条派工回执；参数不合法直接抛 ValueError（失败可见）。"""
    to = str(to or "").strip()
    task = str(task or "").strip()
    if not to or not task:
        raise ValueError("派工必须提供 to（接收方）与 task（任务/工单）")
    if status not in VALID_STATUS:
        raise ValueError("status 必须是 %s" % (VALID_STATUS,))
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("due 格式须 YYYY-MM-DD") from exc
    entry = {
        "schema": "tianfeng_dispatch/v1",
        "id": "dsp-%s-%04d" % (time.strftime("%Y%m%d-%H%M%S"), random.randint(0, 9999)),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "to": to, "task": task, "acceptance": acceptance, "due": due,
        "status": status, "note": note,
    }
    p = Path(path) if path else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def summary(path: Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    rows = _read(p)
    today = time.strftime("%Y-%m-%d")
    open_rows = [r for r in rows if r.get("status") in ("dispatched", "accepted")]
    blocked = [r for r in rows if r.get("status") == "blocked"]
    done = [r for r in rows if r.get("status") == "done"]
    overdue = [r for r in open_rows
               if r.get("due") and str(r.get("due")) < today]
    return {"path": str(p), "total": len(rows),
            "open": len(open_rows), "blocked": len(blocked), "done": len(done),
            "overdue": overdue[-10:], "recent": rows[-8:],
            "by_to": {t: sum(1 for r in open_rows if r.get("to") == t)
                      for t in {r.get("to") for r in open_rows}}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰派工回执台账（append-only JSONL）")
    ap.add_argument("--to", default="", help="接收方（部门/AI worker）")
    ap.add_argument("--task", default="", help="任务/工单号/一句话")
    ap.add_argument("--acceptance", default="", help="验收标准")
    ap.add_argument("--due", default="", help="期望完成日 YYYY-MM-DD")
    ap.add_argument("--status", default="dispatched", choices=VALID_STATUS)
    ap.add_argument("--note", default="")
    ap.add_argument("--path", default="", help="台账路径（默认 work/tianfeng_dispatch.jsonl）")
    ap.add_argument("--list", action="store_true", help="查看回执摘要")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)
    p = Path(args.path) if args.path else None
    if args.list:
        s = summary(p)
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=1))
        else:
            print("total=%d open=%d blocked=%d done=%d overdue=%d path=%s"
                  % (s["total"], s["open"], s["blocked"], s["done"],
                     len(s["overdue"]), s["path"]))
            for r in s["recent"]:
                print("- %s [%s] %s → %s due=%s" % (r.get("id"), r.get("status"),
                                                    r.get("task"), r.get("to"), r.get("due")))
        return 0
    try:
        e = append(args.to, args.task, args.acceptance, args.due,
                   args.note, args.status, p)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "entry": e}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
