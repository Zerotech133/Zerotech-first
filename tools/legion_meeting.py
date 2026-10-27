# -*- coding: utf-8 -*-
"""军团晨会引擎 + 知识提炼器（LCS-2 四制一环）

legion_meeting.py：每工作日 09:00 跑——各部门生成结构化晨会记录
  输入：昨日复盘卡 + 今日排程 + 在途工单 + 阻塞（超期单）
  输出：data/meetings/YYYY-MM-DD_<dept>_晨会.md（含 lessons 提炼位）

knowledge_distill.py 逻辑并入：lessons 直接写 agent__<ID>__knowledge.jsonl
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat
import logging

ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
WORKTREES = ROOT / ".worktrees"
MEET_DIR = ROOT / "agent_platform" / "data" / "meetings"
MEM_DIR = ROOT / "agent_platform" / "data" / "memory"


def cn_name(aid: str) -> str:
    try:
        for ln in (HR / aid / "resume.yaml").read_text(encoding="utf-8").splitlines():
            if ln.startswith("cn_name:"):
                return ln.split(":", 1)[1].strip()
            if ln.startswith("name:"):
                return ln.split(":", 1)[1].strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/legion_meeting.py", exc)
    return aid


def latest_reviews() -> dict:
    base = ROOT / "agent_platform" / "data" / "daily_review"
    for d in sorted(base.glob("2*"), reverse=True):
        if (d / "_summary.json").exists():
            out = {}
            for f in d.glob("*.md"):
                q1 = q2 = q3 = ""
                for ln in f.read_text(encoding="utf-8").splitlines():
                    if "①" in ln:
                        q1 = ln.split("：", 1)[-1].strip()
                    elif "②" in ln:
                        q2 = ln.split("：", 1)[-1].strip()
                    elif "③" in ln:
                        q3 = ln.split("：", 1)[-1].strip()
                out[f.stem] = {"q1": q1, "q2": q2, "q3": q3}
            return out
    return {}


def today_schedules() -> list:
    try:
        rows = json.loads((ROOT / "agent_platform" / "data" / "schedules.json").read_text(encoding="utf-8"))
        return [r for r in rows if r.get("enabled")]
    except Exception:
        return []


def open_tasks() -> dict:
    try:
        t = json.loads((WORKTREES / "tasks.json").read_text(encoding="utf-8"))
        mine = {}
        for k, v in t.items():
            if isinstance(v, dict) and v.get("status") in ("doing", "review"):
                mine[k] = v
        return mine
    except Exception:
        return {}


def _task_hits(t: dict, members: list, lead: str) -> list:
    """工单归属判定：desc 含 `[成员ID]` 标签，或 owner 精确等于成员ID/部长ID。

    只做精确 ID 匹配——tasks.json 的 owner 有 162 条 unknown-agent 及多种
    非成员取值，模糊匹配会把无关工单挂到部门名下。
    """
    desc = str(t.get("desc", ""))
    owner = str(t.get("owner") or "")
    # run() 已把 lead 并入 members，此处去重避免同一成员重复出行
    pool = list(dict.fromkeys(list(members) + ([lead] if lead else [])))
    return [m for m in pool
            if ("[%s]" % m) in desc or (owner and owner == m)]


# created 实测格式：'2026-10-18 16:53:47'（空格分隔，212 条）为主，
# 另有 ISO 'T' 分隔、缺秒（20 条）与纯日期（45 条）。
_TS_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def _parse_created(raw) -> float:
    """解析 created 时间戳；无法解析返回 None（由调用方显式记录，不静默吞掉）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    for f in _TS_FMTS:
        try:
            return time.mktime(time.strptime(s, f))
        except ValueError:
            continue
    return None


def academy_plan() -> list:
    """研究院固定研究链，统一研究语言并避免被当作普通职能部门。"""
    return [
        "课题入口：投研/数据/研发/产品/市场/法务/田丰提交需求",
        "统一研究语言：问题→对象→假设→证据口径→验证方法→结论等级→行动建议→风险边界",
        "策略方向：STRAT1 提出图形/因子/交易假设 → BACK1 回测与样本外验证",
        "数据智能方向：DATA1 登记血缘与口径 → AI1 评测 Agent 能力、路由与知识反哺",
        "情报方向：INTEL1 对标行业、开源项目、竞品、政策和客户需求",
        "商业方向：COMM1 论证客户、交付、成本、定价、收入与服务边界",
        "产品方向：PROD1 形成产品需求、验收指标、试点方案和交付清单",
        "工程与合规：ARCH1 评估工程化；LEG1 审查数据授权、对外发布与合规边界",
        "最终评审：ACAD1 组织研究院评审，跨部门联评后报田丰拍板",
        "结果分流：补证/No-Go/入库/试点/产品化/终止，禁止研究结论悬空",
    ]


def morning_meeting(dept_name: str, lead: str, members: list, reviews: dict,
                    scheds: list, tasks: dict, dept: str = "") -> str:
    NL = chr(10)
    day = time.strftime("%Y-%m-%d")
    att = "、".join(cn_name(m) for m in members) or "（空部门）"
    lines = []
    lines.append("# %s 晨会 %s（主持：%s）" % (dept_name, day, cn_name(lead)))
    lines.append("")
    lines.append("**与会**：%s" % att)
    lines.append("")
    lines.append("## 一、昨日完成（读复盘卡）")
    for m in members:
        r = reviews.get(m, {})
        done = r.get("q1", "—")
        detail = (r.get("q2") or "—")[:44]
        lines.append("- %s（%s）：%s｜%s" % (cn_name(m), m, done, detail))
    lines.append("")
    lines.append("## 二、今日任务（排程+派单）")
    hit = False
    for s in scheds:
        if s.get("agent") in members:
            hit = True
            lines.append("- %s（%s）排程：%s [%s]" % (cn_name(s["agent"]), s["agent"], s.get("desc", "")[:40], s.get("cron", "")))
    for tid, t in tasks.items():
        for m in _task_hits(t, members, lead):
            lines.append("- %s（%s）在途工单：%s [%s]" % (cn_name(m), m, tid, str(t.get("desc", ""))[:40]))
            hit = True
    if not hit:
        lines.append("- （无排程/在途单——部长现场派单或成员领单）")
    lines.append("")
    lines.append("## 三、阻塞求助（超期/依赖）")
    blocked = False
    now = time.time()
    ts_bad = []
    for tid, t in tasks.items():
        if t.get("status") != "doing":
            continue
        for m in _task_hits(t, members, lead):
            ch = _parse_created(t.get("created"))
            if ch is None:
                ts_bad.append((tid, str(t.get("created", ""))))
                continue
            if now - ch > 72 * 3600:
                lines.append("- ⚠️ %s（%s）工单 %s 超 72h——部长 %s 介入" % (cn_name(m), m, tid, cn_name(lead)))
                blocked = True
    if ts_bad:
        # 时间字段不可解析的工单不得无声消失：计数 + 逐条留痕，并计入阻塞待办。
        lines.append("- ⚠️ 工单时间字段解析失败 %d 条（需部长 %s 核对 created）——%s"
                     % (len(ts_bad), cn_name(lead),
                        "；".join("%s（created=%r）" % (tid, raw) for tid, raw in ts_bad)))
        blocked = True
    if not blocked:
        lines.append("- 无阻塞")
    lines.append("")
    if dept == "academy":
        lines.append("## 四、研究院固定工作链")
        lines.extend("- " + x for x in academy_plan())
        lines.append("")
        lines.append("## 五、决议与教训（会后人填/提炼器自动回填）")
    else:
        lines.append("## 四、决议与教训（会后人填/提炼器自动回填）")
    lines.append("- decisions:（待填）")
    lines.append("- lessons:（待填——须可证伪可复用，部务会拍板后入知识环）")
    return NL.join(lines) + NL


def run():
    MEET_DIR.mkdir(parents=True, exist_ok=True)
    org = json.loads(ORG.read_text(encoding="utf-8"))
    reviews = latest_reviews()
    scheds = today_schedules()
    tasks = open_tasks()
    day = time.strftime("%Y-%m-%d")
    n = 0
    for d in org.get("departments", []):
        dept = d.get("dept", "?")
        if dept.startswith("proj_"):  # 项目部并入职能部晨会
            continue
        lead = d.get("lead") or "—"
        members = list(dict.fromkeys([a for a in d.get("agents", [])]))
        if d.get("lead"):  # lead 不在 agents 内时并入本次晨会成员集合（ops_runtime/chief_line）
            members = list(dict.fromkeys([d.get("lead")] + members))
        md = morning_meeting(d.get("name", dept), lead,
                             members, reviews, scheds, tasks, dept)
        (MEET_DIR / ("%s_%s_晨会.md" % (day, dept))).write_text(md, encoding="utf-8")
        n += 1
    lead0 = org.get("chief", {}).get("agent_id", "A0")
    heartbeat(lead0, "morning_meeting", "%s 晨会材料生成 %d 部门" % (day, n))
    return n


if __name__ == "__main__":
    print("生成部门晨会记录：", run(), "份")
