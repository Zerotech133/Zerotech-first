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
import logging

ROOT = Path(__file__).resolve().parent  # agent_platform/
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
    except ImportError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/onboard_agent.py", exc)
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
    score = 65 if has_data else 40  # 有产线数据=过 E2 线；无=E1 见习
    return score, detail


def suggest_dept(skills: list) -> str:
    best, best_hit = "dev", 0
    for dept, kws in DEPT_CHARTERS.items():
        hit = sum(1 for s in skills for kw in kws if kw in str(s))
        if hit > best_hit:
            best, best_hit = dept, hit
    return best


def rank_from_score(score: int) -> str:
    if score >= 80:
        return "E3"
    if score >= 60:
        return "E2"
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
    (hr_dir / "resume.yaml").write_text(
        resume_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    appt = {
        "agent_id": aid,
        "dept": dept,
        "title": r.get("title_suggest") or "见习岗",
        "rank": rank,
        "tools_allowed": {"read": [], "write": [], "execute": [], "danger": "deny"},
        "supervisor": r.get("supervisor") or "",
        "endpoint": r.get("endpoint") or "",
        "onboarded": str(date.today()),
        "approvals": {"chief": "A0", "human": "PENDING"},
    }
    (hr_dir / "appointment.yaml").write_text(
        json.dumps(appt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {aid} 入职草稿完成（待田小皮初审+老田终审）")
    print(f"     定级: {rank}（分数 {score}｜{detail}）")
    print(f"     建议部门: {dept}（技能匹配自宪章表）")
    print(f"     档案: {hr_dir}")
    print(
        "     下一步: 人审 appointment → approvals.human=laotian → 写入 org_registry v2"
    )
    try:
        import sys as _s

        _s.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        from legion_pulse import heartbeat

        heartbeat("HR_ONBOARD", "draft", f"{aid} 入职草稿 rank={rank} dept={dept}")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/onboard_agent.py", exc)


def _yaml_scalar(v) -> str:
    """标量/列表 → 行内标量（JSON 字符串/数组即合法 YAML 标量子集）。"""
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    return json.dumps(str(v), ensure_ascii=False)


def _yaml_block(doc: dict) -> str:
    """极简 YAML 生成（简历模板结构：一层嵌套 dict + dict 列表 + 标量/行内列表）。"""
    lines = []
    for k, v in doc.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for k2, v2 in v.items():
                lines.append(f"  {k2}: {_yaml_scalar(v2)}")
        elif isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
            lines.append(f"{k}:")
            for item in v:
                for i, (k2, v2) in enumerate(item.items()):
                    lines.append(f"{'- ' if i == 0 else '  '}{k2}: {_yaml_scalar(v2)}")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    return "\n".join(lines) + "\n"


def _team_facts(aid: str):
    """辅助事实源：在 registry/team_*.json 中交叉定位 agent。返回 (file, team, entry)。"""
    for f in sorted((ROOT / "registry").glob("team_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            logging.getLogger(__name__).debug("swallowed exception at line 198")
            continue
        for a in data.get("agents", []):
            if isinstance(a, dict) and a.get("agent_id") == aid:
                return f, data, a
    return None, None, None


def backfill(aid: str) -> None:
    """存量补办：为「org 在册且 hr 缺档」的 agent 从事实源生成档案（禁编造）。

    闸门：① 不在 org.json 名册 → 拒绝（新员工走 --resume）；
         ② hr 已有档案 → 拒绝（防覆盖，变更走 appointment 流程）；
         ③ org.json 无 agent_profiles 事实档案 → 拒绝（禁编造）。
    无法从事实源得出的字段一律写 PENDING 占位并打印待补清单。
    """
    reg = load_registry()
    # 定位事实源（org.json 主源）
    profile, dept, where = None, None, ""
    chief = reg.get("chief", {})
    if chief.get("agent_id") == aid:
        profile = chief
        dept = {"dept": "chief", "name": "幕府", "lead": ""}
        where = "chief"
    else:
        for d in reg.get("departments", []):
            if aid in d.get("agents", []):
                dept, where = d, f"departments[{d.get('dept')}].agents"
                for p in d.get("agent_profiles", []):
                    if p.get("agent_id") == aid:
                        profile = p
                break
    if profile is None and dept is None:
        fail(
            f"{aid} 不在 org.json 名册——backfill 仅限在册补档（新员工请走 --resume 全流程）"
        )
    hr_dir = HR_DIR / aid
    if (hr_dir / "resume.yaml").exists() or (hr_dir / "appointment.yaml").exists():
        fail(
            f"{aid} 已有 hr 档案——backfill 拒绝（防覆盖；岗位变更请走 appointment 变更流程）"
        )
    if profile is None:
        fail(
            f"{aid} 在册（{where}）但 org.json 无 agent_profiles 事实档案——禁编造，请人工建档"
        )
    # 辅助事实源交叉核对（org.json 为准，不一致只告警不臆断）
    team_file, team_data, team_entry = _team_facts(aid)
    srcs = [
        f"registry/org.json#{where}" + ("" if where == "chief" else ".agent_profiles")
    ]
    if team_file:
        srcs.append(f"registry/{team_file.name}")
    warns = []
    if team_entry:
        for k in ("role_profile", "grade", "llm_binding"):
            if profile.get(k) and team_entry.get(k) and profile[k] != team_entry[k]:
                warns.append(
                    f"org.json 与 {team_file.name} 的 {k} 不一致（{profile[k]} vs {team_entry[k]}）——以 org.json 为准"
                )
    # 派生字段（全部取自事实源，禁编造）
    attrs = profile.get("attributes", {}) or {}
    charter = profile.get("charter_detail") or ""
    title = (
        charter.split("：")[0].split(":")[0].strip()
        or profile.get("role_profile")
        or aid
    )
    # 待补清单（事实源得不出 → PENDING 占位）
    pending = []
    if not profile.get("llm_binding"):
        pending.append("identity.model / appointment.llm_binding")
    if not attrs.get("specialties"):
        pending.append("resume.skills")
    if not profile.get("grade"):
        pending.append("appointment.rank")
    pending += [
        "resume.identity.born（注册批次日期）",
        "resume.education（可选）",
        "resume.self_statement（人审补充）",
        "resume.cn_name / cn_origin（待授名）",
        "appointment.endpoint",
        "appointment.approvals.human=PENDING（待老田终审）",
    ]
    # 生成简历（字段对齐既有档案模板）
    resume_doc = {
        "agent_id": aid,
        "name": title,
        "kind": "staff",
        "identity": {
            "origin": f"org.json 在册补档（--backfill）：{dept.get('name', '')}·{title}，注册依据见 org.json source",
            "model": profile.get("llm_binding") or "PENDING",
            "born": "PENDING（待人工补：注册批次日期见 org.json source）",
        },
        "education": [
            {
                "program": "PENDING（待人工补）",
                "source": "PENDING（待人工补）",
                "graduated": "PENDING（待人工补）",
            }
        ],
        "experience": [
            {
                "org": f"{dept.get('name', '')}（dept={dept.get('dept')}）",
                "role": title,
                "span": "PENDING（注册批次日期见 org.json source）",
                "kpi": charter or "PENDING",
            }
        ],
        "skills": attrs.get("specialties") or [],
        "self_statement": f"（backfill 初稿占位——由 onboard_agent.py --backfill 于 {date.today()} 生成，待人审补充）",
        "cn_name": "PENDING（待人工授名）",
        "cn_origin": "PENDING（待人工授名）",
    }
    # 生成交办书（结构对齐 OPT1/A0 任命书）
    appt = {
        "agent_id": aid,
        "dept": dept.get("dept"),
        "dept_note": f"{dept.get('name', '')}（org.json 事实源）"
        + (f"，team={team_data.get('team')}" if team_data else ""),
        "title": title,
        "rank": profile.get("grade") or "PENDING",
        "rank_note": profile.get("grade_note") or "",
        "llm_binding": profile.get("llm_binding") or "PENDING",
        "tools": profile.get("tools") or [],
        "memory_grants": profile.get("memory_grants") or {},
        "tools_allowed": attrs.get("tools_allowed")
        or {"read": [], "write": [], "execute": [], "danger": "deny"},
        "supervisor": dept.get("lead") or "",
        "endpoint": "",
        "onboarded": f"补办登记 {date.today()}",
        "approvals": {
            "chief": reg.get("chief", {}).get("agent_id") or "",
            "human": "PENDING",
        },
        "backfill_note": (
            f"--backfill 于 {date.today()} 生成，事实源={' + '.join(srcs)}；"
            f"kind=staff 按 agent_profile 在册惯例；待终审字段见 backfill 输出清单"
        ),
    }
    hr_dir.mkdir(parents=True, exist_ok=True)
    (hr_dir / "resume.yaml").write_text(_yaml_block(resume_doc), encoding="utf-8")
    (hr_dir / "appointment.yaml").write_text(
        json.dumps(appt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {aid} 补档完成（org 在册事实源生成，名册未动）")
    print(f"     事实源: {' + '.join(srcs)}")
    print(f"     档案: {hr_dir}")
    for w in warns:
        print(f"[WARN] {w}")
    print("     待补/待终审字段:")
    for p in pending:
        print(f"       - {p}")
    print(
        "     下一步: 人审两份 yaml（born/cn_name/endpoint 等待补）→ approvals.human=laotian"
    )
    try:
        import sys as _s

        _s.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        from legion_pulse import heartbeat

        heartbeat(
            "HR_BACKFILL",
            "draft",
            f"{aid} 补档 draft rank={appt['rank']} dept={appt['dept']}",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/onboard_agent.py", exc)


def check() -> None:
    """体检：名册成员 100% 有 HR 档案。"""
    reg = load_registry()
    members = [reg.get("chief", {}).get("agent_id")]
    for d in reg.get("departments", []):
        members += d.get("agents", [])
    missing = [m for m in members if m and not (HR_DIR / m / "resume.yaml").exists()]
    print(
        f"名册 {len([m for m in members if m])} 人｜有档案 {len([m for m in members if m]) - len(missing)}｜缺档案 {len(missing)}"
    )
    if missing:
        print("[WARN] 缺档案（存量补办）:", ", ".join(missing))
    else:
        print("[OK] 全员档案齐备")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="数字军团入职工具（K-ORG-117）")
    ap.add_argument("--resume", type=Path, help="新员工简历 yaml")
    ap.add_argument("--check", action="store_true", help="体检：档案覆盖率")
    ap.add_argument(
        "--backfill",
        metavar="AGENT_ID[,AGENT_ID...]",
        help="存量补办：为 org 在册但缺 hr 档案的 agent 从事实源补建档案（已有档案则拒绝防覆盖）",
    )
    args = ap.parse_args()
    if args.check:
        check()
    elif args.backfill:
        for aid in [a.strip() for a in args.backfill.split(",") if a.strip()]:
            backfill(aid)
    elif args.resume:
        onboard(args.resume)
    else:
        ap.print_help()
