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
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WT_DIR = os.path.join(REPO, ".worktrees")
TASKS = os.path.join(WT_DIR, "tasks.json")
PROJECTS = os.path.join(REPO, "agent_platform", "registry", "projects.json")


CPT_DB = os.path.join(REPO, "agent_platform", "data", "checkpoints.db")
_CKPT = None


def _checkpointer():
    """K-ORG-090/G4：工单 checkpoint 惰性单例——第一次用到才连 db，
    避免每次 CLI 调用（list/board 等不挂线的命令）都开连接。"""
    global _CKPT
    if _CKPT is None:
        sys.path.insert(0, os.path.join(REPO, "agent_platform"))
        from engine.checkpoints import Checkpointer
        _CKPT = Checkpointer(CPT_DB)
    return _CKPT


def _ckpt(tid, node_key, owner, state):
    """挂线快照（K-ORG-090/G4）：DEVTASK_CHECKPOINT=0 时跳过（回退开关）；
    任何异常只 WARN 不阻断——devtask 是产码主链，挂线零行为差异。"""
    if os.environ.get("DEVTASK_CHECKPOINT", "") == "0":
        return
    try:
        _checkpointer().save_task_node(tid, node_key, owner, state)
    except Exception as e:  # noqa: BLE001  fail-open：快照失败不挡工单
        print("[WARN] checkpoint 写入失败（不阻断主流程）：%s" % e)


def _load_projects():
    """K-ORG-057：项目线注册表（缺文件/坏 JSON 一律降级为空，不挡道）。"""
    try:
        return json.load(open(PROJECTS, encoding="utf-8")).get("projects", [])
    except Exception:  # noqa: BLE001
        return []


def _resolve_project(pid):
    """校验项目线：未注册/已归档 → 拒收（判据3：跨线工单必须显式声明合法 project）。"""
    for pr in _load_projects():
        if pr.get("project_id") == pid:
            if pr.get("status", "active") != "active":
                raise SystemExit("项目线 %s 已归档（archived）——新工单拒收，请选活跃线或先在组织图恢复" % pid)
            return pr
    raise SystemExit("项目线 %s 未注册——拒收。先在组织图部门卡「+ 子项目」注册，"
                     "或用已注册线：%s" % (pid, ", ".join(
                         p.get("project_id", "?") for p in _load_projects()) or "（无）"))


def _emit(kind, task, detail=""):
    """事件回流（K-STOCK-114c）：merge/guard 动作自动进平台事件层，失败不挡道。"""
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        from events import emit
        emit(kind, task=task, agent=os.environ.get("DEVTOOL_AGENT", "devtask"),
             detail=detail)
    except Exception:  # noqa: BLE001  事件层永不阻塞调度器
        pass
    # 飞书卡尽力而为：合并→回执卡；守卫失败→告警卡（任一失败静默）
    try:
        if kind in ("merge_done", "guard_fail"):
            from notify_devops import card_event
            from feishu_card_v2 import send_card
            send_card(card_event(
                "merge" if kind == "merge_done" else "blocked",
                task, detail))
    except Exception:  # noqa: BLE001
        pass
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


_EV_RE = re.compile(r"#ev:(commit|file|trace):([^\s，。；,;]+)")


def _ev_precheck(desc, tasks):
    """K-ORG-090/T2：开单 desc 中 #ev 证据引用预检（087/T2 spec 模板侧）。
    commit→git cat-file -e 校验 sha；file@sha→cat-file -e sha:path；
    trace→tasks.json 在册。只 WARN 不拦单（fail-open）——坏引用到产码侧
    由 agent_coder.resolve_evidence fail-visible 标注，此处提前一层暴露给开单人。"""
    warns = []
    for kind, ref in _EV_RE.findall(desc):
        if kind == "commit":
            r = subprocess.run(["git", "cat-file", "-e", ref + "^{commit}"],
                               cwd=REPO, capture_output=True)
            if r.returncode != 0:
                warns.append("#ev:commit:%s 不在仓库（检查 sha）" % ref)
        elif kind == "file":
            path, _, sha = ref.rpartition("@")  # rpartition 返回 (head, sep, tail)：head=路径 tail=sha
            if not sha or not path:
                warns.append("#ev:file:%s 格式应为 路径@sha" % ref)
                continue
            r = subprocess.run(["git", "cat-file", "-e", "%s:%s" % (sha, path)],
                               cwd=REPO, capture_output=True)
            if r.returncode != 0:
                warns.append("#ev:file:%s@%s 该提交下不存在" % (path, sha))
        else:  # trace
            if ref not in tasks:
                warns.append("#ev:trace:%s 不在工单账本" % ref)
    return warns


_VAGUE_RE = re.compile(r"正确处理|合理|酌情")


def _ears_lint(desc):
    """K-ORG-080/N5：spec 验收判据 EARS 化 lint（模板见 knowledge_docs/spec_ears_template.md）。
    仅当 desc 含「## 验收判据」段时检查（无段=旧式单，直接跳过返回 []）：
      ① 判据行（"- WHEN" 开头）不足 3 条；
      ② 判据行含模糊词（正确处理/合理/酌情/句尾「等」）→ 逐行 WARN 不可断言；
      ③ 有验收判据段但缺「## 非目标」段（防 scope 蔓延）；
      ④ 缺「## QA1 判读指令」段（QA1 逐条判定依赖，K-ORG-071/072）。
    只 WARN 不拦单（fail-open，与 _ev_precheck 同款）。"""
    if "## 验收判据" not in desc:
        return []
    warns = []
    m = re.search(r"## 验收判据[^\n]*\n(.*?)(?=\n## |\Z)", desc, re.S)
    body = m.group(1) if m else ""
    crit = [ln for ln in body.splitlines()
            if ln.lstrip().startswith("- WHEN")]
    if len(crit) < 3:
        warns.append("判据不足 3 条（N5 模板建议）")
    for ln in crit:
        if _VAGUE_RE.search(ln) or ln.rstrip().endswith("等"):
            warns.append("判据不可断言（模糊词）：%s" % ln.strip()[:60])
    if "## 非目标" not in desc:
        warns.append("有「## 验收判据」段但缺「## 非目标」段（防 scope 蔓延）")
    if "## QA1 判读指令" not in desc:
        warns.append("缺「## QA1 判读指令」段（QA1 逐条判定依赖，K-ORG-071/072）")
    return warns


def cmd_new(tid, *desc):
    # K-ORG-057：--project <pid> 可选参数；缺省归 general 线（显式提示，不拒收——
    # 保兼容；判据3的「拒收」落在「声明了但不存在的线」上）
    desc = list(desc)
    project = "general"
    if "--project" in desc:
        i = desc.index("--project")
        if i + 1 >= len(desc):
            raise SystemExit("--project 需要跟项目线 id（如 --project quant）")
        project = desc.pop(i + 1)
        desc.pop(i)
        _resolve_project(project)  # 未注册/已归档 → SystemExit 拒收
    elif desc:
        print("[warn] 未声明 --project，归入 general 线（建议显式带线："
              "new %s --project <pid> ...）" % tid)
    desc = " ".join(desc) or "(无描述)"
    tasks = _tasks()
    if tid in tasks:
        raise SystemExit("工单 %s 已存在" % tid)
    # K-ORG-090/T2：#ev 引用预检（只 WARN 不拦单，fail-open）
    for w in _ev_precheck(desc, tasks):
        print("[warn] 证据引用预检: %s（不拦单；产码侧 resolve_evidence 会 fail-visible 标注）" % w)
    # K-ORG-080/N5：EARS 判据 lint（只 WARN 不拦单，fail-open）
    for w in _ears_lint(desc):
        print("[warn] EARS lint: %s" % w)
    branch = "task/" + tid
    wt = os.path.join(WT_DIR, tid)
    _git("worktree", "add", wt, "-b", branch)
    tasks[tid] = {"desc": desc, "branch": branch, "worktree": wt,
                  "status": "doing", "created": time.strftime("%F %T"),
                  "project": project,
                  "owner": os.environ.get("AGENT_NAME", "unknown-agent")}
    _save(tasks)
    _ckpt(tid, "kai", tasks[tid]["owner"],
          {"desc": desc, "branch": branch, "worktree": wt,
           "project": project})
    _emit("task_start", tid, "%s（owner=%s·project=%s）"
          % (desc, tasks[tid]["owner"], project))
    print("工单 %s 已开：%s（分支 %s）owner=%s project=%s"
          % (tid, desc, branch, tasks[tid]["owner"], project))
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


def cmd_list(*args):
    # K-ORG-057：--project <pid> 按线过滤；project 列入表
    proj_filter = None
    args = list(args)
    if "--project" in args:
        i = args.index("--project")
        proj_filter = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    tasks = _tasks()
    if proj_filter:
        tasks = {k: v for k, v in tasks.items()
                 if v.get("project", "general") == proj_filter}
        print("（过滤：project=%s）" % proj_filter)
    print("%-10s %-8s %-10s %-20s %-24s %s" %
          ("工单", "状态", "项目线", "owner/分支", "描述", "ahead/behind·末提交"))
    for tid, t in sorted(tasks.items()):
        ahead, behind = _ahead_behind(t["branch"])
        try:
            last = _git("log", "-1", "--format=%ar", t["branch"])
        except SystemExit:
            last = "?"
        print("%-10s %-8s %-10s %-20s %-24s +%d/-%d · %s"
              % (tid, t["status"], t.get("project", "general")[:8],
                 t["owner"][:8] + "/" + t["branch"][5:15],
                 t["desc"][:22], ahead, behind, last))
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
            _emit("guard_fail", tid, "门 %s 未过：%s"
                  % (name, ((r.stdout or r.stderr).strip() or "")[:120]))
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
    _ckpt(tid, "merge", t.get("owner", "devtask"), {"status": "done"})
    _emit("merge_done", tid, "三守卫全绿，已合并 master")
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
            "list": lambda: cmd_list(*rest),
            "port": lambda: cmd_port(*rest),
            "merge": lambda: cmd_merge(*rest),
            "drop": lambda: cmd_drop(*rest),
            "board": cmd_board}[c]()


if __name__ == "__main__":
    sys.exit(main())
