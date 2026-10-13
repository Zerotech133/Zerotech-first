# -*- coding: utf-8 -*-
"""chief_patrol — 田小皮监督回路巡检器（K-ORG-026 批E1）。

田小皮 = 平行空间的老田：严格按 org_baseline.json 计划基线，只读巡检三源执行数据，
产出四色判读（超前/正常/滞后/阻塞）+ 建议动作。判读落 memory 黑板（chief observation），
不代跑任何执行动作（merge/派单执行权在各岗与团队线）。

三源（全部只读）：
  1. agent_platform/registry/org_baseline.json   计划基线（应然）
  2. .worktrees/tasks.json                        devtask 工单实库（实然·状态）
  3. portal/data/dev_events.jsonl                 开发事件流（实然·活动）

用法：
  python tools/chief_patrol.py            # 巡检并落判读文件+黑板
  python tools/chief_patrol.py --dry      # 只打印不落盘
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASELINE = ROOT / "agent_platform" / "registry" / "org_baseline.json"
TASKS = ROOT / ".worktrees" / "tasks.json"
EVENTS = ROOT / "portal" / "data" / "dev_events.jsonl"
OBS_DIR = ROOT / "agent_platform" / "data" / "shadow"
BLACKBOARD = ROOT / "agent_platform" / "data" / "memory" / "dept__chief__observation.jsonl"

GREEN, BLUE, AMBER, RED = "🟢正常", "🔵超前", "🟡滞后", "🔴阻塞"


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def today_events_count():
    """今日事件数（dev_events.jsonl 当天行数）。"""
    n = 0
    try:
        today = time.strftime("%Y-%m-%d")
        for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
            if today in line:
                n += 1
    except Exception:  # noqa: BLE001
        pass
    return n


def task_summary(tasks):
    """工单状态计数 + doing 超时单 + review 清单。"""
    st = {"doing": [], "review": [], "done": 0, "aborted": 0}
    now = datetime.now()
    th = load_json(BASELINE).get("thresholds", {})
    stall_h = th.get("doing_stall_hours", 48)
    for tid, t in tasks.items():
        s = t.get("status")
        if s == "done":
            st["done"] += 1
        elif s == "aborted":
            st["aborted"] += 1
        elif s in ("doing", "review"):
            st[s].append(tid)
            if s == "doing":
                try:
                    c = datetime.strptime(t.get("created", ""), "%Y-%m-%d %H:%M:%S")
                    if now - c > timedelta(hours=stall_h):
                        t["_stall_h"] = int((now - c).total_seconds() // 3600)
                except Exception:  # noqa: BLE001
                    pass
    st["doing_stalled"] = [t for t in
                           [dict(v, id=k) for k, v in tasks.items()
                            if v.get("status") == "doing" and "_stall_h" in v]]
    return st


def patrol():
    base = load_json(BASELINE)
    tasks = load_json(TASKS)
    ts = task_summary(tasks)
    ev_today = today_events_count()
    th = base.get("thresholds", {})
    rev_n = len(ts["review"])
    rev_warn = th.get("review_backlog_warn", 20)
    lines = []
    lines.append("# 田小皮巡检判读 %s" % time.strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append("> 依据：org_baseline.json 计划基线 × 三源执行数据（工单库/事件流/黑板）。只读监督，不代跑执行。")
    lines.append("")
    lines.append("## 一、组织脉搏")
    lines.append("- 工单库：%d done / %d doing（%d 超时）/ %d review / %d aborted"
                 % (ts["done"], len(ts["doing"]), len(ts["doing_stalled"]), rev_n, ts["aborted"]))
    lines.append("- 今日开发事件：%d 条" % ev_today)
    act = len(ts["doing"]) + ev_today
    lines.append("- 组织活性：%s（doing+事件 = %d）" % ("正常运转" if act > 0 else "⚠ 停摆", act))
    lines.append("")

    lines.append("## 二、计划符合度（按批）")
    for b in base.get("batches", []):
        st = b.get("status")
        color = {"done": GREEN, "doing": BLUE, "planned": "⚪待启动",
                 "blocked_until_1015": "🧊冻结(10-15影子解禁)"}.get(st, st)
        done_items = [i for i in b.get("items", []) if i.get("done")]
        lines.append("- **%s %s** — %s（%d/%d 项已销账，deadline %s）"
                     % (b["id"], b["name"], color, len(done_items),
                        len(b.get("items", [])), b.get("deadline") or "不设"))
        for i in b.get("items", []):
            mark = "✅" if i.get("done") else ("🧊" if st == "blocked_until_1015" else "⬜")
            lines.append("  - %s %s %s（%s）" % (mark, i["id"], i["task"], i.get("owner", "?")))
    lines.append("")

    lines.append("## 三、风险与建议动作")
    risks = []
    if rev_n >= th.get("review_backlog_p0", 30):
        risks.append((RED, "review 积压 %d 单达 P0 线" % rev_n, "田小皮直接干预：冻结新单派发，全员清 review"))
    elif rev_n >= rev_warn:
        risks.append((AMBER, "review 积压 %d 单越过警戒线（%d）" % (rev_n, rev_warn),
                      "QA1 四批质检（K-ORG-025a）按序推进，本周期内须清至 <10"))
    if ts["doing_stalled"]:
        for t in ts["doing_stalled"]:
            risks.append((AMBER, "doing 单 %s 已 %dh 无流转（%s）"
                          % (t["id"], t["_stall_h"], t.get("owner", "?")),
                          "田小皮点名：owner 说明进度或转 blocked"))
    gate_b = [b for b in base.get("batches", []) if str(b.get("status", "")).startswith("blocked")]
    for b in gate_b:
        risks.append(("🧊", "%s 被 [%s] 冻结" % (b["name"], b.get("gate", "")),
                      "解禁日前不得改冻结面；解禁后 48h 内补跑"))
    if not risks:
        risks.append((GREEN, "无越线风险", "维持当前节奏"))
    for color, risk, act in risks:
        lines.append("- %s **%s** → %s" % (color, risk, act))
    lines.append("")
    lines.append("---")
    lines.append("*判读人：田小皮（A0）· 工具 tools/chief_patrol.py 只读产出 · 飞书推送随批 B 解禁（E3）*")
    return "\n".join(lines), ts, rev_n


def main():
    dry = "--dry" in sys.argv
    report, ts, rev_n = patrol()
    print(report)
    if dry:
        return 0
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    out = OBS_DIR / ("chief_patrol_%s.md" % time.strftime("%Y-%m-%d"))
    out.write_text(report, encoding="utf-8", newline="\n")
    # 黑板：判读摘要落 chief observation（老田与全组织可读）
    try:
        BLACKBOARD.parent.mkdir(parents=True, exist_ok=True)
        with open(BLACKBOARD, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M"),
                "event": "patrol_verdict",
                "agent": "A0",
                "desc": "巡检判读：doing=%d(stall %d)/review=%d/今日事件=%d，详见 %s"
                        % (len(ts["doing"]), len(ts["doing_stalled"]), rev_n,
                           today_events_count(), out.name),
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    print("\n[pattern] 判读落盘：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
