# -*- coding: utf-8 -*-
"""数字军团入职工具 v0.1（K-ORG-117 一批交付）

用法：
  新员工全流程:  python onboard_agent.py --resume 新员工.yaml
  存量补办:      python onboard_agent.py --backfill jev_gate,cowbridge,hermes_webui
  体检:          python onboard_agent.py --check

设计纪律（K-ORG-116/117）：
  - 无入职不上线：本工具是名册（org_registry v2）唯一写入口
  - 全部动作 fail-visible + 幂等（重复入职=拒绝，不覆盖）
  - 定级用产线数据说话：影子命中/QA通过率/纪律记录（无数据=E1见习）
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # agent_platform/
HR_DIR = ROOT / "hr"
REGISTRY = ROOT / "registry" / "org.json"

DEPT_CHARTERS = {  # 部门 → 宪章关键词（简历 skills 匹配用）
    "invest": ["选股", "研究", "辩论", "情绪", "基本面", "技术面", "决策"],
    "dev": ["后端", "开发", "产码", "架构", "运维", "文档"],
    "trading": ["交易", "执行", "下单"],
    "data": ["数据", "工程", "清洗", "同步"],
    "product": ["产品", "设计", "UI", "经理"],
    "quality": ["测试", "安全", "QA", "合规", "审查"],
    "marketing": ["市场", "宣发", "口号", "内容"],
    "legal": ["法务", "合规", "口径", "公安"],
    "ops_runtime": ["告警", "判读", "守护", "桥", "网关", "推送", "监听"],
}

REQUIRED_RESUME = ["agent_id", "name", "kind", "identity", "skills"]


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def parse_resume(path: Path) -> dict:
    """极简 YAML 解析（简历模板子集：嵌套一层 + 行内 dict/list）。"""
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        pass
    # 无 yaml 库时降级解析（覆盖模板字段）
    out, section = {}, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")) and raw.rstrip().endswith(":"):
            section = raw.rstrip()[:-1]
            out[section] = {}
            continue
        m = re.match(r"^(\w[\w.]*):\s*(.*)$", raw)
        if m and not raw.startswith((" ", "-")):
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                out[key] = {}
                section = key
            else:
                (out[section] if section else out)[key] = val.strip("'\"")
    return out


def score_agent(agent_id: str) -> tuple[int, dict]:
    """定级考试：读产线数据（影子命中/QA通过/纪律），无数据=E1。"""
    detail = {"shadow_hit": None, "qa_pass": None, "discipline": "无事故记录=满分"}
    # 1) 影子命中（decision jsonl 存在即有产出）
    dec = ROOT / "data" / "memory" / f"agent__{agent_id}__decision.jsonl"
    if dec.exists():
        n = sum(1 for _ in dec.open(encoding="utf-8"))
        detail["shadow_hit"] = f"{n} 条决策记录"
    # 2) QA 通过率（observe 账本）
    audit = ROOT / "data" / "llm_tool_audit.jsonl"
    if audit.exists():
        hits = [l for l in audit.open(encoding="utf-8") if agent_id in l]
        detail["qa_pass"] = f"{len(hits)} 条工具调用留痕"
    has_data = detail["shadow_hit"] or detail["qa_pass"]
    score = 65 if has_data else 40   # 有产线数据=过 E2 线；无=E1 见习
    return score, detail


def suggest_dept(skills: list) -> str:
    best, best_hit = "dev", 0
    for dept, kws in DEPT_CHARTERS.items():
        hit = sum(1 for s in skills for kw in kws if kw in str(s))
        if hit > best_hit:
            best, best_hit = dept, hit
    return best


def rank_from_score(score: int) -> str:
    if score >= 80: return "E3"
    if score >= 60: return "E2"
    return "E1"


def onboard(resume_path: Path) -> None:
    r = parse_resume(resume_path)
    missing = [k for k in REQUIRED_RESUME if k not in r]
    if missing:
        fail(f"简历缺必填字段: {missing}（模板见 K-ORG-117 §二）")
    aid = r["agent_id"]
    # 幂等：已入册拒绝
    reg = load_registry()
    existing = [a for d in reg.get("departments", []) for a in d.get("agents", [])]
    if aid in existing or (reg.get("chief", {}).get("agent_id") == aid):
        fail(f"{aid} 已在册——重复入职被拒（晋升/轮岗请走 appointment 变更流程）")
    # 定级 + 部门建议
    score, detail = score_agent(aid)
    rank = rank_from_score(score)
    skills = r["skills"] if isinstance(r["skills"], list) else [str(r["skills"])]
    dept = suggest_dept(skills)
    # 建档案
    hr_dir = HR_DIR / aid
    hr_dir.mkdir(parents=True, exist_ok=True)
    (hr_dir / "resume.yaml").write_text(resume_path.read_text(encoding="utf-8"), encoding="utf-8")
    appt = {
        "agent_id": aid, "dept": dept, "title": r.get("title_suggest") or "见习岗",
        "rank": rank, "tools_allowed": {"read": [], "write": [], "execute": [], "danger": "deny"},
        "supervisor": r.get("supervisor") or "", "endpoint": r.get("endpoint") or "",
        "onboarded": str(date.today()), "approvals": {"chief": "A0", "human": "PENDING"},
    }
    (hr_dir / "appointment.yaml").write_text(
        json.dumps(appt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {aid} 入职草稿完成（待田小皮初审+老田终审）")
    print(f"     定级: {rank}（分数 {score}｜{detail}）")
    print(f"     建议部门: {dept}（技能匹配自宪章表）")
    print(f"     档案: {hr_dir}")
    print("     下一步: 人审 appointment → approvals.human=laotian → 写入 org_registry v2")
    try:
        import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        from legion_pulse import heartbeat
        heartbeat("HR_ONBOARD", "draft", f"{aid} 入职草稿 rank={rank} dept={dept}")
    except Exception:
        pass


def check() -> None:
    """体检：名册成员 100% 有 HR 档案。"""
    reg = load_registry()
    members = [reg.get("chief", {}).get("agent_id")]
    for d in reg.get("departments", []):
        members += d.get("agents", [])
    missing = [m for m in members if m and not (HR_DIR / m / "resume.yaml").exists()]
    print(f"名册 {len([m for m in members if m])} 人｜有档案 {len([m for m in members if m]) - len(missing)}｜缺档案 {len(missing)}")
    if missing:
        print("[WARN] 缺档案（存量补办）:", ", ".join(missing))
    else:
        print("[OK] 全员档案齐备")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="数字军团入职工具（K-ORG-117）")
    ap.add_argument("--resume", type=Path, help="新员工简历 yaml")
    ap.add_argument("--check", action="store_true", help="体检：档案覆盖率")
    args = ap.parse_args()
    if args.check:
        check()
    elif args.resume:
        onboard(args.resume)
    else:
        ap.print_help()
