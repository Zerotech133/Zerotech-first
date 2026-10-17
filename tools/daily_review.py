# -*- coding: utf-8 -*-
"""军团每日复盘引擎（K-ORG-122）：每个 Agent 每天回答三问
① 我今天有没有任务 ② 我的任务是什么/进展 ③ 我的进化在哪里

产出：agent_platform/data/daily_review/YYYY-MM-DD/<agent>.md
流程：17:40 各成员自动复盘（任务来自工单+心跳）→ 部长汇总(17:50) → A0 总判读(17:55) → 大屏展示
挂载：计划任务 LegionDailyReview 17:40
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat, recent

ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
WORKTREES = ROOT / ".worktrees"
OUT = ROOT / "agent_platform" / "data" / "daily_review"


def my_tasks(aid: str) -> tuple:
    t = {}
    try:
        t = json.loads((WORKTREES / "tasks.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    mine_open, mine_done_recent = [], []
    for k, v in t.items():
        if not isinstance(v, dict):
            continue
        desc = str(v.get("desc", ""))
        if f"[{aid}]" in desc or v.get("owner") == aid:
            if v.get("status") in ("doing", "review"):
                mine_open.append({"tid": k, "desc": desc[:60], "status": v["status"]})
            elif v.get("status") == "done":
                mine_done_recent.append(k)
    return mine_open, mine_done_recent


def my_acts_today(aid: str) -> list:
    today = time.strftime("%Y-%m-%d")
    return [r for r in recent(60 * 24) if r.get("caller") == aid and str(r.get("ts", "")).startswith(today)]


def evolution_hint(aid: str, acts, tasks_open) -> str:
    """进化点：从活动与任务推导一句可执行的成长建议。"""
    if tasks_open:
        return f"今日有 {len(tasks_open)} 张在途单——复盘完成后在工单留 3 行小结（做了什么/卡在哪/明天首步），沉淀进 decision 记忆。"
    if acts:
        return f"今日有 {len(acts)} 次活动但无正式工单——把重复性动作固化为工具/判例，写进 knowledge 记忆。"
    return "今日无任务无活动——主动向部长领单，或自主研修（把学到的一条判例写进知识库）。"


def member_review(aid: str) -> dict:
    tasks_open, done_recent = my_tasks(aid)
    acts = my_acts_today(aid)
    skill_line = ""
    try:
        for ln in (HR / aid / "resume.yaml").read_text(encoding="utf-8").splitlines():
            if ln.startswith("skills:"):
                skill_line = ln.split(":", 1)[1].strip()[:80]; break
    except Exception:
        pass
    has_task = bool(tasks_open or acts)
    return {
        "agent_id": aid, "has_task": has_task,
        "q1": "✅ 有任务" if has_task else "❌ 今日无任务",
        "q2": ("；".join(f"{t['tid']}({t['status']})" for t in tasks_open) or
               ("，".join(sorted(set(str(a.get("action")) for a in acts))) if acts else "—"))[:120],
        "q3": evolution_hint(aid, acts, tasks_open),
        "acts_n": len(acts), "skills": skill_line,
    }


def run(daily_dir: Path = None) -> dict:
    day = time.strftime("%Y-%m-%d")
    out_dir = daily_dir or (OUT / day)
    out_dir.mkdir(parents=True, exist_ok=True)
    org = json.loads(ORG.read_text(encoding="utf-8"))
    members, depts = [], {}
    chief = org.get("chief", {}).get("agent_id", "A0")
    all_ids = [chief]
    for d in org.get("departments", []):
        all_ids += d.get("agents", [])
    for aid in dict.fromkeys(all_ids):
        r = member_review(aid)
        members.append(r)
        depts.setdefault(_dept_of(aid, org), []).append(r)
        # 成员复盘卡（md）
        (out_dir / f"{aid}.md").write_text(
            f"# {aid} 每日复盘 {day}\n\n"
            f"**① 我今天有没有任务**：{r['q1']}\n\n"
            f"**② 我的任务/进展**：{r['q2']}\n\n"
            f"**③ 我的进化**：{r['q3']}\n\n"
            f"*活动 {r['acts_n']} 次 ｜ 技能 {r['skills'] or '待赋'}*\n", encoding="utf-8")
    # 部长汇总
    summary = {"day": day, "n": len(members),
               "no_task": [m["agent_id"] for m in members if not m["has_task"]],
               "depts": {k: {"n": len(v), "no_task": [m["agent_id"] for m in v if not m["has_task"]]} for k, v in depts.items()}}
    (out_dir / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    heartbeat("A0A", "daily_review", f"{day} 复盘汇总：{len(members)}员/{len(summary['no_task'])}人无任务")

    # ── v2 四段工作流（K-ORG-123）：部长点评 → 部门日报 → A0 军团日报 ──
    org_full = json.loads(ORG.read_text(encoding="utf-8"))
    lead_of = {d.get("dept"): d.get("lead") for d in org_full.get("departments", []) if d.get("lead")}
    dept_reports = []
    for dept, mems in depts.items():
        lead = lead_of.get(dept, "—")
        n_active = sum(1 for m in mems if m["has_task"])
        is_academy = dept == "academy"
        no_task = [m["agent_id"] for m in mems if not m["has_task"]]
        top_acts = sorted(mems, key=lambda m: -m["acts_n"])[:3]
        # 部长点评（规则版：逐员一句评语写进成员卡）
        for m in mems:
            if m["has_task"]:
                cmt = f"部长点评({lead})：今日履职，任务推进{m['acts_n']}次活动，保持节奏。"
            else:
                cmt = f"部长点评({lead})：今日无任务——明日主动领单（已列入部门催办）。"
            m["lead_comment"] = cmt
            f = out_dir / f"{m['agent_id']}.md"
            txt = f.read_text(encoding="utf-8") if f.exists() else ""
            if "部长点评" not in txt:
                f.write_text(txt + "\n**④ 部长点评**：" + cmt + "\n", encoding="utf-8")
        # 部门日报（部长 → A0 的汇报材料）
        rep = {"dept": dept, "lead": lead, "n": len(mems), "n_active": n_active,
               "no_task": no_task,
               "top": [{"agent": m["agent_id"], "acts": m["acts_n"]} for m in top_acts],
               "to_a0": f"{dept}日报({lead}→A0)：{len(mems)}员在册/{n_active}人履职/{len(no_task)}人无任务；"
                        + (f"表扬 {'、'.join(str(t.get('agent', t.get('agent_id', '?'))) for t in top_acts[:2])}；催办 {'、'.join(no_task[:5])}明日领单。" if mems else ""),
               "special_focus": ("研究课题必须记录假设、数据口径、验证证据、评审结论和下一步转化动作。" if is_academy else "")}
        (out_dir / f"_dept_{dept}.md").write_text(
            "# %s 部门日报 %s（%s → 田丰）\n\n%s\n\n- 在册 %d｜履职 %d｜无任务 %d\n" % (
                dept, day, lead, rep["to_a0"], len(mems), n_active, len(no_task))
            + ("\n- 研究院专题：" + rep["special_focus"] if rep["special_focus"] else "")
            + "".join("\n- %s：%s %s" % (m["agent_id"], "✅" if m["has_task"] else "❌", m["q2"][:50]) for m in mems),
            encoding="utf-8")
        dept_reports.append(rep)
        heartbeat(lead, "dept_daily_report", rep["to_a0"][:100], {"to": "A0"})
    # A0 军团日报
    tot_active = sum(1 for m in members if m["has_task"])
    corps = {"day": day, "n": len(members), "n_active": tot_active,
             "n_no_task": len(summary["no_task"]),
             "verdict": f"军团日报：{len(members)}员/{tot_active}履职/{len(summary['no_task'])}无任务；{len(dept_reports)}部门已交日报"}
    (out_dir / "_corps_daily.md").write_text(
        "# 军团日报 %s（田丰 → 老田）\n\n%s\n" % (day, corps["verdict"])
        + "".join("\n## %s（%s）\n%s\n" % (r["dept"], r["lead"], r["to_a0"]) for r in dept_reports),
        encoding="utf-8")
    heartbeat("A0", "corps_daily", corps["verdict"])
    summary["dept_reports"] = dept_reports
    summary["corps"] = corps
    return summary


def _dept_of(aid, org) -> str:
    for d in org.get("departments", []):
        if aid in d.get("agents", []):
            return d.get("dept", "?")
    return "chief"


if __name__ == "__main__":
    s = run()
    print(json.dumps(s, ensure_ascii=False, indent=2)[:600])
