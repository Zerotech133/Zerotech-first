# -*- coding: utf-8 -*-
"""org 矩阵迁移（P2）：一席一档 + matrix_assignments，替代"项目部门各存一份完整 profile"。

背景：org.json 里 68 条 profile / 60 个唯一 agent_id——8 个席位在「职能部」与
「项目行营（proj_*）」各有一份档案（项目那份还带 analysis_type/项目工具等），
条目数把一个人算两次，任何「共多少席」口径都虚。

目标结构（schema v2）：
  - 职能部（非 project）的 profile = **唯一权威档**，新增：
        "matrix_assignments": [{ "dept": "proj_x", ...项目侧原始字段..., "migrated_from": "proj_x" }]
  - 项目部门（type=project / dept=proj_*）只保留 `agents` 名单，`agent_profiles` 清空；
  - 双写校验（--check）：迁移前视图(68 条按 dept+agent) 与 迁移后视图(60 主档 + 8 matrix) 逐字段相等，
    证明**无信息丢失**；不等则拒绝 --apply。

用法（先验证后写）：
    python agent_platform/tools/migrate_org_matrix.py --check      # 只读，打印 68→60 与双写校验
    python agent_platform/tools/migrate_org_matrix.py --apply      # 备份 org.json 后写入
    python agent_platform/tools/migrate_org_matrix.py --dry-run    # 只打印计划
只写 registry/org.json（apply 时先备份 .bak-matrix-<ts>），不碰其他真源。
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
ROOT = PLATFORM.parent
ORG_REL = Path("agent_platform") / "registry" / "org.json"


def load_org(root: Path) -> dict:
    return json.loads((Path(root) / ORG_REL).read_text(encoding="utf-8"))


def _profiles(d: dict) -> list:
    ap = d.get("agent_profiles") or []
    return list(ap.values()) if isinstance(ap, dict) else list(ap)


def _is_project(d: dict) -> bool:
    return bool(d.get("type") == "project" or str(d.get("dept") or "").startswith("proj_"))


def _set_profiles(d: dict, profs: list) -> None:
    if isinstance(d.get("agent_profiles"), dict):
        d["agent_profiles"] = {str(p.get("agent_id")): p for p in profs}
    else:
        d["agent_profiles"] = profs


def plan(org: dict) -> dict:
    """返回迁移计划：{moves, conflicts, before_profiles, after_profiles}。"""
    func: dict[str, tuple[str, dict]] = {}
    projects: list[dict] = []
    for d in org.get("departments") or []:
        if _is_project(d):
            projects.append(d)
            continue
        for p in _profiles(d):
            aid = str(p.get("agent_id") or "")
            if aid and aid not in func:
                func[aid] = (str(d.get("dept")), p)
    moves, conflicts = [], []
    for d in projects:
        dept = str(d.get("dept"))
        for p in _profiles(d):
            aid = str(p.get("agent_id") or "")
            if not aid:
                continue
            if aid not in func:
                conflicts.append({"dept": dept, "agent": aid,
                                  "reason": "职能部无权威档案，无法合并"})
                continue
            moves.append({"dept": dept, "agent": aid,
                          "functional_dept": func[aid][0],
                          "profile": copy.deepcopy(p)})
    before = sum(len(_profiles(d)) for d in org.get("departments") or [])
    return {"moves": moves, "conflicts": conflicts,
            "before_profiles": before,
            "after_profiles": before - len(moves),
            "project_depts": [str(d.get("dept")) for d in projects]}


def _old_view(org: dict) -> dict:
    """迁移前视图：所有部门档案表条目（已迁移数据的 matrix 字段也剔除，保证幂等校验可比）。"""
    out = {}
    for d in org.get("departments") or []:
        for p in _profiles(d):
            out[(str(d.get("dept")), str(p.get("agent_id")))] = {
                k: v for k, v in p.items() if k != "matrix_assignments"}
    return out


def _new_view(org: dict) -> dict:
    """迁移后视图：职能部主档（去掉新增 matrix_assignments 字段）+ 主档 matrix 条目（去掉迁移标记）。"""
    out = {}
    for d in org.get("departments") or []:
        if _is_project(d):
            continue
        for p in _profiles(d):
            main = {k: v for k, v in p.items() if k != "matrix_assignments"}
            out[(str(d.get("dept")), str(p.get("agent_id")))] = main
            for m in p.get("matrix_assignments") or []:
                m2 = {k: v for k, v in m.items() if k != "migrated_from"}
                dept_key = str(m2.pop("dept", ""))
                out[(dept_key, str(p.get("agent_id")))] = m2
    return out


def migrate(org: dict) -> dict:
    """原地迁移（幂等）：项目部 profile 并入职能部主档 matrix_assignments 后清空。"""
    p = plan(org)
    if p["conflicts"]:
        raise ValueError("存在无法合并的档案：%s" % json.dumps(p["conflicts"], ensure_ascii=False))
    by_dept = {str(d.get("dept")): d for d in org.get("departments") or []}
    for mv in p["moves"]:
        func_dept = by_dept[mv["functional_dept"]]
        target = None
        for fp in _profiles(func_dept):
            if str(fp.get("agent_id")) == mv["agent"]:
                target = fp
                break
        if target is None:
            raise ValueError("主档消失：%s" % mv["agent"])
        entry = copy.deepcopy(mv["profile"])
        entry["dept"] = mv["dept"]
        entry["migrated_from"] = mv["dept"]
        target.setdefault("matrix_assignments", [])
        if not any(m.get("dept") == mv["dept"] for m in target["matrix_assignments"]):
            target["matrix_assignments"].append(entry)
        target["matrix_assignments"].sort(key=lambda m: m.get("dept", ""))
    for proj_dept in p["project_depts"]:
        d = by_dept[proj_dept]
        _set_profiles(d, [])
    return org


def check(org: dict) -> dict:
    """只读双写校验：迁移前视图 == 迁移后视图（逐字段）；有冲突则 ok=False。"""
    p = plan(org)
    if p["conflicts"]:
        return {"ok": False, "error": "conflicts", "plan": p,
                "old_entries": sum(len(_profiles(d)) for d in org.get("departments") or []),
                "new_entries": 0, "missing": [], "diff": [],
                "after_profiles": p["before_profiles"]}
    after = migrate(copy.deepcopy(org))
    old, new = _old_view(org), _new_view(after)
    missing = [k for k in old if k not in new]
    diff = [k for k in old if k in new and old[k] != new[k]]
    return {"ok": not missing and not diff, "old_entries": len(old),
            "new_entries": len(new), "missing": missing, "diff": diff[:10],
            "plan": p, "after_profiles": sum(
                len(_profiles(d)) for d in after.get("departments") or [])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="org 矩阵迁移（P2）：一席一档 + matrix_assignments")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--check", action="store_true", help="只读：计划 + 双写校验")
    ap.add_argument("--apply", action="store_true", help="备份后写入 org.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root)
    org = load_org(root)
    rep = check(org)
    if args.dry_run or (not args.check and not args.apply):
        out = {"plan": rep["plan"], "check_ok": rep["ok"]}
    elif args.check:
        out = rep
    else:  # --apply
        if not rep["ok"]:
            print(json.dumps({"ok": False, "error": "双写校验未过，拒绝写入",
                              "diff": rep["diff"], "missing": rep["missing"]},
                             ensure_ascii=False))
            return 1
        after = migrate(copy.deepcopy(org))
        org_path = root / ORG_REL
        bak = org_path.with_name("org.json.bak-matrix-%s" % time.strftime("%Y%m%d-%H%M%S"))
        bak.write_text(org_path.read_text(encoding="utf-8"), encoding="utf-8")
        org_path.write_text(json.dumps(after, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        out = {"ok": True, "backup": str(bak), "before_profiles": rep["plan"]["before_profiles"],
               "after_profiles": rep["after_profiles"], "moves": len(rep["plan"]["moves"])}
    if args.json or not args.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
