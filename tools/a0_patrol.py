# -*- coding: utf-8 -*-
"""田丰总监察（K-ORG-121 L4 · A0 巡察）

职责（老田拍板：田丰要监控所有人有没有执行）：
1. 全员点名：谁 48h 无心跳（该干活没干活的直接亮牌）
2. 工单体检：doing >72h 催办、review 滞留、孤儿单
3. 影子纪律：shadow 成员若有产码动作=违规
4. 产出判读：调 qwen3.5:9b 给一段一句话简报（可选 --llm；qwen2.5:7b 已于 10-24 清理）

用法：
  python tools/a0_patrol.py            # 纯规则巡察
  python tools/a0_patrol.py --llm      # 带 LLM 判读
挂载：计划任务 A0Patrol 每 30min（下一步注册）
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat, recent
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"


def patrol() -> dict:
    org = json.loads(ORG.read_text(encoding="utf-8"))
    members = [org.get("chief", {}).get("agent_id", "A0")]
    for d in org.get("departments", []):
        members += d.get("agents", [])
    acts = {}
    for r in recent(60 * 48):
        acts.setdefault(r.get("caller", "?"), 0)
        acts[r["caller"]] = acts.get(r["caller"], 0) + 1

    idle_48h = [m for m in set(members) if m not in acts]
    # 运行体豁免：探针活≠心跳（他们由守护任务看护，这里不算失职）
    runtimes = {"JEV_GATE", "JEV_REVIEW", "JEV_QD", "LARK_CARDS", "COWBRIDGE", "HERMES_UI"}
    idle_real = [m for m in idle_48h if m not in runtimes]

    tasks = {}
    try:
        tasks = json.loads((ROOT / ".worktrees" / "tasks.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/a0_patrol.py", exc)
    now = time.time()
    overdue, review_stuck = [], []
    for k, v in tasks.items():
        if not isinstance(v, dict):
            continue
        try:
            ch = time.mktime(time.strptime(str(v.get("created", ""))[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        age_h = (now - ch) / 3600
        if v.get("status") == "doing" and age_h > 72:
            overdue.append({"tid": k, "age_h": round(age_h, 0), "desc": str(v.get("desc", ""))[:36]})
        if v.get("status") == "review" and age_h > 168:
            review_stuck.append({"tid": k, "age_h": round(age_h, 0)})

    # ── v2 三级监督链（K-ORG-122）──
    # 层1：A0 督部长——各部长今日是否有履职心跳（daily_review/council 类动作）
    org_full = json.loads(ORG.read_text(encoding="utf-8"))
    dept_report = []
    for d in org_full.get("departments", []):
        lead = d.get("lead")
        if not lead:
            continue
        dept_members = [m for m in d.get("agents", []) if m not in runtimes]
        idle_in_dept = [m for m in dept_members if m in idle_real]
        overdue_in_dept = [o for o in overdue if any(m in str(o.get("desc", "")) for m in dept_members)]
        lead_active_today = bool([r for r in recent(60 * 24) if r.get("caller") == lead])
        grade = "优" if (not idle_in_dept and not overdue_in_dept) else ("良" if lead_active_today else "待督办")
        dept_report.append({"dept": d.get("name", d.get("dept")), "lead": lead,
                            "members": len(dept_members), "idle": idle_in_dept,
                            "overdue": len(overdue_in_dept),
                            "lead_active": lead_active_today, "grade": grade})
    # 层2：部长督成员——违规清单直接点名（写给部长看的催办单）
    escalations = []
    for dp in dept_report:
        if dp["idle"] or dp["overdue"]:
            escalations.append({"to_lead": dp["lead"], "dept": dp["dept"],
                                "idle_members": dp["idle"], "overdue_n": dp["overdue"],
                                "msg": f"请{dp['lead']}催办：{len(dp['idle'])}人无任务活动、{dp['overdue']}单超期"})

    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "n_members": len(set(members)), "n_active_48h": len(acts),
        "idle_48h": sorted(idle_real), "n_idle": len(idle_real),
        "overdue_doing": overdue, "review_stuck": review_stuck,
        "dept_grades": dept_report,       # A0 督部长：部门评级
        "escalations": escalations,       # 部长督成员：催办清单
        "verdict": "全员在岗" if not (idle_real or overdue or review_stuck) else
                   f"{len(idle_real)}人无心跳 | {len(overdue)}单超期 | {len(review_stuck)}单滞留 | {len(escalations)}部门待督办",
    }
    heartbeat("A0", "patrol", f"巡察完成: {report['verdict']}",
              {"idle": len(idle_real), "overdue": len(overdue), "escalations": len(escalations)})
    for es in escalations:
        heartbeat(es["to_lead"], "escalated", es["msg"][:80], {"from": "A0"})
    return report


def llm_brief(report: dict) -> str:
    try:
        import urllib.request
        p = f"""你是田丰（参谋长），对军团巡察报告做一句话判读（风险优先，30字内）：
在册{report['n_members']}人/48h活跃{report['n_active_48h']}/无心跳{report['n_idle']}人/超期工单{len(report['overdue_doing'])}张。
判读："""
        body = json.dumps({"model": "qwen3.5:9b", "prompt": p, "stream": False,
                           "options": {"num_predict": 60}}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with safe_urlopen(req, timeout=120) as r:
            return json.loads(r.read())["response"].strip()[:80]
    except Exception as e:
        return f"(LLM 判读不可用: {type(e).__name__})"


if __name__ == "__main__":
    rep = patrol()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if "--llm" in sys.argv:
        print("田丰判读:", llm_brief(rep))
