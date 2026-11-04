# -*- coding: utf-8 -*-
"""08:50 定时投递：W0 22 doing 处置建议 → A0 inbox。

设计约束：
- 不得在 08:50 前投递；调度器误触发/人工试跑也跳过。
- 投递前重新取 W0 当前计数，正文同时保留 23:16 基线，避免拿旧数字当“当前”。
- 只走 internal_notify → inbox_gateway，不新增队列。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEADLINE = datetime(2026, 11, 5, 8, 50, 0)
DOC = "docs/ops/2026-11-05_W0_22doing处置建议_v1.md"
TITLE = "W0 08:50 决策：22 doing 逐单处置（6 done/9 merge/3 review/4 keep）"


def _current_counts() -> tuple[int, int, int, bool]:
    """返回 doing/worktree/parked，以及是否与 23:16 基线一致。"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "w0_inventory.py"), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("w0_inventory rc=%s stderr=%s" % (proc.returncode, proc.stderr[-400:]))
    inv = json.loads(proc.stdout)
    doing = int(inv["doing"])
    worktrees = int(inv["worktrees"])
    tasks_file = ROOT / ".worktrees" / "tasks.json"
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    parked = sum(1 for t in tasks.values() if isinstance(t, dict) and t.get("status") == "parked")
    return doing, worktrees, parked, (doing, worktrees, parked) != (22, 32, 8)


def build_body(doing: int, worktrees: int, parked: int, changed: bool = False) -> str:
    return f"""08:50 W0 定时决策提醒（23:16 基线，08:50 复核）。

23:16 基线：doing 22 / worktree 32 / parked 8（目标 8/20/8）。
08:50 复核：doing {doing} / worktree {worktrees} / parked {parked}。{'（与基线不同，以复核数为准）' if changed else ''}

逐单依据：{DOC}（commit bcf5372d1）。

建议口径（按 23:16 的 22 单清单）：
- DONE 6：P2-8、P2-9、PROTO、WORKBENCH、WO-INTEL-EVID-API、K-INTEL-P7D（P7D 需 owner 确认；done=任务收口，分支保留，不等于已合 master）。
- MERGE 9：H2、MONPAGE、H2B、F2、NORTH、MONSCHED、H3、F3、MONCFG（K-STOCK 依赖链，排在 HZ/UI 之后）。
- REVIEW 3：UI-MOBILE-RESIDUAL、UI-GATECHECK、UI-A11Y2（大分支/缺独立自动化证据，不直接关）。
- KEEP 4：MON（脏 2）、DATA2（脏 6）、W1（双阻塞）、CATALOG（待 ARCH1+A0 批准）。

请 A0 给书面结论：
D1 是否批准 6 单立即 done（分支保留、worktree 回收）？
D2 是否批准 9 单 K-STOCK 合入链及排序授权（HZ/UI 优先）？
D3 MON/DATA2 脏 worktree 由谁提交/审查？
D4 UI 三支的验收人/门禁口径？
D5 CATALOG、W1 是否维持另批处理？

未获书面口径前：不批量改 tasks.json，不把 doing/worktree/parked 写成达标。"""


def main() -> int:
    if "--force" not in sys.argv and datetime.now() < DEADLINE:
        print("skip: before 2026-11-05 08:50 local time")
        return 0

    sys.path.insert(0, str(ROOT / "agent_platform"))
    from gateway import internal_notify

    doing, worktrees, parked, changed = _current_counts()
    body = build_body(doing, worktrees, parked, changed)
    if len(body) > internal_notify.MAX_BODY:
        raise RuntimeError("body too long: %d" % len(body))
    env = internal_notify.emit(
        "A0", TITLE, body, kind="question", source="w0-0850",
        link=DOC, actor="lead",
    )
    print(json.dumps(env, ensure_ascii=False))
    return 0 if env.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
