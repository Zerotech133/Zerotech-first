# -*- coding: utf-8 -*-
"""devtask — 多 Agent 协作开发调度器（K-STOCK-104 方案丙）。

一单一线程：每个任务单 = 独立 git worktree + 独立分支，worker 互不踩脚。

命令：
  new <TID> <一句话描述>   建任务单+worktree+分支 task/<TID>
  list                     任务单总览（谁在干什么/分支/进度）
  port <服务名>            申请分配一个未注册端口（并提示写回 REGISTRY）
  merge <TID>              过评审门（三守卫）后合并回 master 并清场
  board [out.html]         生成看板 HTML（默认 docs/dev/devboard.html）
  drop <TID>               放弃任务（留原因，清 worktree）

依赖：git（Windows 原生，无 tmux）；评审门工具在 tools/ 下。
"""
import json
import os
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WT_DIR = os.path.join(REPO, ".worktrees")
TASKS = os.path.join(WT_DIR, "tasks.json")
GATES = [("Mixin方法集", [sys.executable,
                          os.path.join(REPO, "tools", "_mixin_integrity.py"),
                          "--check"]),
         ("导航统一", [sys.executable,
                       os.path.join(REPO, "tools", "unify_nav.py"),
                       "--check"]),
         ("端口注册", [sys.executable,
                       os.path.join(REPO, "tools", "check_ports.py")])]


def _git(*args, cwd=REPO):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("git %s 失败: %s" % (" ".join(args),
                                             (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def _tasks():
    if os.path.exists(TASKS):
        return json.load(open(TASKS, encoding="utf-8"))
    return {}


def _save(tasks):
    os.makedirs(WT_DIR, exist_ok=True)
    json.dump(tasks, open(TASKS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def cmd_new(tid, *desc):
    desc = " ".join(desc) or "(无描述)"
    tasks = _tasks()
    if tid in tasks:
        raise SystemExit("工单 %s 已存在" % tid)
    branch = "task/" + tid
    wt = os.path.join(WT_DIR, tid)
    _git("worktree", "add", wt, "-b", branch)
    tasks[tid] = {"desc": desc, "branch": branch, "worktree": wt,
                  "status": "doing", "created": time.strftime("%F %T"),
                  "owner": os.environ.get("AGENT_NAME", "unknown-agent")}
    _save(tasks)
    print("工单 %s 已开：%s（分支 %s）owner=%s"
          % (tid, desc, branch, tasks[tid]["owner"]))
    print("→ Agent 请 cd %s 内干活，完工跑 devtask.py merge %s" % (wt, tid))
    return 0


def _ahead_behind(branch):
    try:
        ab = _git("rev-list", "--left-right", "--count",
                  "master..." + branch)
        behind, ahead = ab.split()
        return int(ahead), int(behind)
    except SystemExit:
        return 0, 0


def cmd_list():
    tasks = _tasks()
    print("%-10s %-8s %-22s %-28s %s" %
          ("工单", "状态", "owner/分支", "描述", "ahead/behind·末提交"))
    for tid, t in sorted(tasks.items()):
        ahead, behind = _ahead_behind(t["branch"])
        try:
            last = _git("log", "-1", "--format=%ar", t["branch"])
        except SystemExit:
            last = "?"
        print("%-10s %-8s %-22s %-28s +%d/-%d · %s"
              % (tid, t["status"], t["owner"][:9] + "/" + t["branch"][5:19],
                 t["desc"][:26], ahead, behind, last))
    if not tasks:
        print("（无任务单——先 devtask.py new）")
    return 0


def cmd_port(name):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from check_ports import REGISTRY, tcp_up, listening_ports
    used = set(REGISTRY) | set(listening_ports())
    cand = next(p for p in range(8100, 9999)
                if p not in used and not tcp_up(p))
    alloc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "ports_allocated.json")
    reg = json.load(open(alloc, encoding="utf-8")) if os.path.exists(
        alloc) else {}
    if name in reg:
        print("%s 已分配：%d" % (name, reg[name]))
        return 0
    reg[name] = cand
    json.dump(reg, open(alloc, "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    print("端口分配：%s → %d" % (name, cand))
    print("⚠ 立即写回 tools/check_ports.py REGISTRY（同一提交）："
          '%d: "%s",' % (cand, name))
    return 0


def cmd_merge(tid):
    tasks = _tasks()
    t = tasks.get(tid)
    if not t:
        raise SystemExit("无此工单")
    print("== 评审门 ==")
    for name, cmd in GATES:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        print("  %-8s %s" % (name, "✓" if ok else "✗ " +
                             (r.stdout or r.stderr).strip()[:160]))
        if not ok:
            print("门未过，拒绝合并。修完重跑。")
            t["status"] = "review"
            _save(tasks)
            return 1
    _git("merge", "--no-ff", t["branch"], "-m",
         "merge task/%s: %s" % (tid, t["desc"]))
    # 清场容错：并发环境下 worktree 可能已被 prune（prunable 状态）
    subprocess.run(["git", "worktree", "prune"], cwd=REPO,
                   capture_output=True)
    for cmd in (["git", "worktree", "remove", t["worktree"], "--force"],
                ["git", "branch", "-d", t["branch"]]):
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("  （清场提示：%s——不影响合并结果）"
                  % (r.stderr or "").strip()[:120])
    t["status"] = "done"
    t["merged"] = time.strftime("%F %T")
    _save(tasks)
    print("工单 %s 已合并清场 ✓" % tid)
    return 0


def cmd_drop(tid, *reason):
    tasks = _tasks()
    t = tasks.pop(tid, None)
    if not t:
        raise SystemExit("无此工单")
    try:
        _git("worktree", "remove", t["worktree"], "--force")
        _git("branch", "-D", t["branch"])
    except SystemExit:
        pass
    t["status"] = "aborted"
    t["abort_reason"] = " ".join(reason) or "(未填)"
    tasks[tid + "-aborted-" + time.strftime("%H%M%S")] = t
    _save(tasks)
    print("工单 %s 已放弃：%s" % (tid, t["abort_reason"]))
    return 0


def cmd_board(out=None):
    out = out or os.path.join(REPO, "docs", "dev", "devboard.html")
    tasks = _tasks()
    rows = ""
    lamp = {"doing": "#3fb950", "review": "#d29922", "done": "#58a6ff",
            "aborted": "#8b949e"}
    for tid, t in sorted(tasks.items(),
                         key=lambda kv: (kv[1]["status"], kv[0])):
        ahead, behind = _ahead_behind(t["branch"])
        rows += ("<tr><td><b>%s</b></td><td><span style='color:%s'>●"
                 "</span> %s</td><td>%s</td><td>%s</td><td>+%d/-%d</td>"
                 "<td>%s</td></tr>"
                 % (tid, lamp.get(t["status"], "#888"), t["status"],
                    t["owner"], t["desc"], ahead, behind,
                    t.get("merged", t["created"])))
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>研发部看板</title>"
            "<script src='/static/nav.js' defer></script>"
            "<style>body{background:#0d1117;color:#e6edf3;"
            "font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
            "padding:24px}table{border-collapse:collapse;width:100%%;"
            "font-size:13px}th,td{border:1px solid #21262d;"
            "padding:8px 12px;text-align:left}th{background:#1c2129;"
            "color:#8b949e}h1{font-size:19px}</style></head><body>"
            "<h1>💻 研发部看板 · 多 Agent 分工与进展</h1>"
            "<p style='color:#8b949e;font-size:12px'>数据源 "
            ".worktrees/tasks.json · 生成 %s · 刷新跑 "
            "devtask.py board</p>"
            "<table><tr><th>工单</th><th>状态</th><th>owner</th>"
            "<th>描述</th><th>ahead/behind</th><th>时间</th></tr>%s"
            "</table></body></html>"
            % (time.strftime("%F %T"), rows))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(html)
    print("看板已生成：%s（%d 单）" % (out, len(tasks)))
    return 0


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 0
    c, rest = a[0], a[1:]
    return {"new": lambda: cmd_new(*rest),
            "list": cmd_list,
            "port": lambda: cmd_port(*rest),
            "merge": lambda: cmd_merge(*rest),
            "drop": lambda: cmd_drop(*rest),
            "board": cmd_board}[c]()


if __name__ == "__main__":
    sys.exit(main())
