# -*- coding: utf-8 -*-
"""模型档位执行点检查（model-binding-check）：org 席位绑定 vs model_tiers 档位策略。

检查项（只读：不写 org.json / model_tiers.json / 任何 registry 真源）：
  1. tier 合规：每个 department.agent_profiles 的 `llm_binding`，其模型 tier 必须落在
     model_tiers.json `grade_policy[grade].allow` 内（E3=strong / E2=strong+fast /
     E1=fast+local，以档位表为准）；
  2. E3 fallback：E3 席必须带非空 `llm_fallback`，且首项 != 主绑定——主绑（glm 429/
     不可用）挂掉时要能真降级，首项等于主绑定等于没有降级链；
  3. unknown：模型名不在 `model_tiers.models` 表内 → 单列 unknown（无法判档，
     不冒充违规；模型表缺项应按注册流程补录）；
  4. warning：模型在表内但 enabled=false → 提示「绑定当前不可调用」，不计 violation。

范围过滤（2026-10-27 Lead 校准，验收口径）：
  · `--dept <dev[,invest]>` / `--grades <E3[,E2]>` 只把指定部门/职级的席位纳入本报告；
  · `--strict` 只对**过滤后的集合**判违规（未过滤=全组织，含历史存量违规，适合盘点）；
  · summary 带 `scope`（归一化后的过滤条件）与 `full_scope`（全组织 checked/violations
    等汇总，供看存量），过滤时 strict 不看 full_scope；
  · 过滤条件一个席位都没匹配上（大概率参数打错）→ 退出码 2，拒绝「空门禁假绿」。

约定：`llm_binding` 为 "none"/空 的席位是规则执行体（无 LLM 依赖，
org_enroll.BINDING_ALLOW 显式允许 "none"），按 unbound 跳过；本地模型兼容表内键
`qwen3.5:9b` 与绑定写法 `ollama/qwen3.5:9b` 两种形态。

退出码：0=报告口径（或 --strict 且 scope 内无 violation）/ 1=--strict 且 scope 内
有 violation / 2=注册表缺失不可读、或过滤范围为空。unknown 单独计数、不拦截
（模型表缺项需人工补录，不当档位违规）。

用法（在仓库/worktree 根跑）：
  python -X utf8 agent_platform/tools/model_binding_check.py --root . --json
  python -X utf8 agent_platform/tools/model_binding_check.py --root . --dept dev \
      --grades E3 --json --strict        # 验收口径：只看 dev 部 E3 席
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
REGISTRY = Path("agent_platform") / "registry"
ORG_FILE = "org.json"
TIERS_FILE = "model_tiers.json"
E3_GRADE = "E3"
# 无 LLM 依赖的显式声明（org_enroll.BINDING_ALLOW 允许 "none"）
UNBOUND_VALUES = frozenset({"none", "null", "-", "n/a", "无"})
_STATUS_LABELS = {"ok": "OK", "warn": "WARN", "violation": "VIOL",
                  "unknown": "UNKNOWN", "unbound": "SKIP"}


def _read_json(path: Path):
    """读 UTF-8（含 BOM）JSON；坏 JSON/缺文件抛 OSError/ValueError 由 main 兜。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _model_name(value) -> str:
    """llm_binding / llm_fallback 单项归一为模型名（兼容 {"model": ...} 写法）。"""
    if isinstance(value, dict):
        for key in ("model", "binding", "name"):
            if value.get(key):
                return str(value[key]).strip()
        return ""
    return str(value or "").strip()


def _is_unbound(binding: str) -> bool:
    return not binding or binding.lower() in UNBOUND_VALUES


def _lookup_model(model: str, models: dict) -> str | None:
    """返回 models 表命中键；本地模型表内键不带 ollama/ 前缀，绑定侧带。"""
    if model in models:
        return model
    if model.startswith("ollama/") and model.split("/", 1)[1] in models:
        return model.split("/", 1)[1]
    return None


def _fallback_models(profile: dict) -> list:
    """llm_fallback 归一为模型名列表（容忍 str/单 dict 写法）。"""
    raw = profile.get("llm_fallback")
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [_model_name(item) for item in raw]


def _split_csv(value: str, *, upper: bool = False) -> list:
    """逗号分隔归一（去空白/去重/中文逗号兼容；dept 小写、grades 大写）。"""
    out = []
    for token in str(value or "").replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        token = token.upper() if upper else token.lower()
        if token not in out:
            out.append(token)
    return out


def _in_scope(entry: dict, depts: list, grades: list) -> bool:
    """条目是否落在过滤集合内（无过滤恒 True）。"""
    if depts and str(entry.get("dept") or "").lower() not in depts:
        return False
    if grades and str(entry.get("grade") or "").strip().upper() not in grades:
        return False
    return True


def _iter_profiles(org: dict):
    for dept in org.get("departments") or []:
        if not isinstance(dept, dict):
            continue
        dept_id = str(dept.get("dept") or "")
        for profile in dept.get("agent_profiles") or []:
            if isinstance(profile, dict):
                yield dept_id, profile


def _scan(org: dict, tiers: dict) -> tuple:
    """全组织扫描，返回 (rows, violations, unknown, warnings, unbound)。"""
    models = tiers.get("models") if isinstance(tiers.get("models"), dict) else {}
    grade_policy = (tiers.get("grade_policy")
                    if isinstance(tiers.get("grade_policy"), dict) else {})
    rows, violations, unknown, warnings, unbound = [], [], [], [], []
    for dept_id, profile in _iter_profiles(org):
        agent_id = str(profile.get("agent_id") or "")
        grade = str(profile.get("grade") or "").strip()
        binding = _model_name(profile.get("llm_binding"))
        row = {"dept": dept_id, "agent_id": agent_id, "grade": grade,
               "binding": binding, "tier": "", "status": "ok"}
        if _is_unbound(binding):
            row["status"] = "unbound"
            unbound.append({"dept": dept_id, "agent_id": agent_id,
                            "grade": grade, "binding": binding or "none"})
            rows.append(row)
            continue
        problems = []
        key = _lookup_model(binding, models)
        meta = models.get(key) if key is not None else None
        if not isinstance(meta, dict):
            unknown.append({"dept": dept_id, "agent_id": agent_id,
                            "grade": grade, "binding": binding,
                            "model": binding,
                            "detail": "模型不在 model_tiers.models 表内，无法判档"})
            row["status"] = "unknown"
        else:
            tier = str(meta.get("tier") or "")
            row["tier"] = tier
            policy = grade_policy.get(grade)
            if not isinstance(policy, dict):
                problems.append(("grade_unknown",
                                 "grade=%r 不在 grade_policy，无法校验档位" % grade))
            else:
                allow = policy.get("allow")
                allow = list(allow) if isinstance(allow, (list, tuple)) else []
                if tier not in allow:
                    problems.append(("tier_not_allowed",
                                     "tier=%s 不在 %s.allow=%s（绑定 %s）"
                                     % (tier, grade, allow, binding)))
            if meta.get("enabled", True) is False:
                warnings.append({"dept": dept_id, "agent_id": agent_id,
                                 "grade": grade, "binding": binding,
                                 "detail": "模型 enabled=false，绑定当前不可调用"})
                row["status"] = "warn"
        if grade == E3_GRADE:
            fallback = _fallback_models(profile)
            if not fallback or not fallback[0]:
                problems.append(("fallback_missing",
                                 "E3 席必须带非空 llm_fallback（主绑定降级链）"))
            elif fallback[0] == binding:
                problems.append(("fallback_same_as_binding",
                                 "llm_fallback 首项与主绑定相同（%s），降级链无效"
                                 % binding))
        if problems:
            row["status"] = "violation"
            for kind, detail in problems:
                violations.append({"dept": dept_id, "agent_id": agent_id,
                                   "grade": grade, "binding": binding,
                                   "tier": row["tier"], "kind": kind,
                                   "detail": detail})
        rows.append(row)
    return rows, violations, unknown, warnings, unbound


def _counts(rows: list, violations: list, unknown: list, warnings: list,
            unbound: list) -> dict:
    return {"checked": sum(1 for row in rows if row["status"] != "unbound"),
            "violations": len(violations), "unknown": len(unknown),
            "warnings": len(warnings), "unbound": len(unbound)}


def check(org: dict, tiers: dict, depts: list | None = None,
          grades: list | None = None) -> dict:
    """执行只读校验；depts/grades 为归一化过滤集合（空=全组织）。"""
    depts = list(depts or [])
    grades = list(grades or [])
    rows, violations, unknown, warnings, unbound = _scan(org, tiers)
    full_scope = _counts(rows, violations, unknown, warnings, unbound)
    rows = [item for item in rows if _in_scope(item, depts, grades)]
    violations = [item for item in violations if _in_scope(item, depts, grades)]
    unknown = [item for item in unknown if _in_scope(item, depts, grades)]
    warnings = [item for item in warnings if _in_scope(item, depts, grades)]
    unbound = [item for item in unbound if _in_scope(item, depts, grades)]
    summary = _counts(rows, violations, unknown, warnings, unbound)
    summary["scope"] = {"dept": depts, "grades": grades}
    summary["full_scope"] = full_scope
    return {"ok": not violations, "summary": summary,
            "violations": violations, "unknown": unknown,
            "warnings": warnings, "unbound": unbound, "rows": rows}


def _print_table(rep: dict) -> None:
    summary = rep["summary"]
    scope = summary.get("scope") or {}
    scoped = bool(scope.get("dept") or scope.get("grades"))
    print("模型档位检查 | root=%s" % rep.get("root"))
    if scoped:
        print("范围: dept=%s / grades=%s（过滤后 checked=%d；"
              "全组织 checked=%d / violations=%d）"
              % (",".join(scope.get("dept") or []) or "-",
                 ",".join(scope.get("grades") or []) or "-",
                 summary["checked"], summary["full_scope"]["checked"],
                 summary["full_scope"]["violations"]))
    print("  %-8s %-10s %-10s %-5s %-28s %-8s"
          % ("STATUS", "AGENT", "DEPT", "GRADE", "BINDING", "TIER"))
    for row in rep["rows"]:
        print("  %-8s %-10s %-10s %-5s %-28s %-8s"
              % (_STATUS_LABELS.get(row["status"], row["status"]),
                 row["agent_id"], row["dept"], row["grade"] or "-",
                 row["binding"] or "-", row["tier"] or "-"))
    for v in rep["violations"]:
        print("[违规] %(agent_id)s %(dept)s %(grade)s %(kind)s: %(detail)s" % v)
    for u in rep["unknown"]:
        print("[未知] %(agent_id)s %(dept)s %(grade)s 模型 %(model)s "
              "不在 model_tiers.models（无法判档）" % u)
    for w in rep["warnings"]:
        print("[提示] %(agent_id)s %(dept)s %(grade)s %(detail)s" % w)
    print("汇总: checked=%d / violations=%d / unknown=%d / unbound=%d"
          % (summary["checked"], summary["violations"],
             summary["unknown"], summary["unbound"]))
    if scoped:
        full = summary["full_scope"]
        print("全组织参考（含存量，不参与本次 strict 判定）: checked=%d / "
              "violations=%d / unknown=%d / unbound=%d"
              % (full["checked"], full["violations"],
                 full["unknown"], full["unbound"]))
    elif summary["violations"]:
        print("注：model_tiers 拍板②「存量绑定不回刷」——以上为全量现状盘点，"
              "不代表本次新注册违规；验收可用 --dept/--grades 收窄范围。")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="模型档位执行点检查：org 席位 llm_binding vs model_tiers 档位策略")
    parser.add_argument("--root", default=str(REPO),
                        help="仓库根目录（默认本工具所在仓库，含 "
                             "agent_platform/registry/）")
    parser.add_argument("--dept", default="",
                        help="只检查指定部门（逗号分隔，如 dev,invest；默认全组织）")
    parser.add_argument("--grades", default="",
                        help="只检查指定职级（逗号分隔，如 E3,E2；默认全组织）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--strict", action="store_true",
                        help="过滤范围内有 violation 时退出码 1（门禁口径）")
    args = parser.parse_args(argv)
    depts = _split_csv(args.dept)
    grades = _split_csv(args.grades, upper=True)
    root = Path(args.root)
    org_path = root / REGISTRY / ORG_FILE
    tiers_path = root / REGISTRY / TIERS_FILE
    try:
        org = _read_json(org_path)
        tiers = _read_json(tiers_path)
    except (OSError, ValueError) as exc:
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        if args.json:
            print(json.dumps({"ok": False, "root": str(root.resolve()),
                              "error": "registry_unreadable", "detail": detail,
                              "expected": [str(org_path), str(tiers_path)]},
                             ensure_ascii=False, indent=1))
        else:
            print("[错误] 注册表读取失败：%s" % detail, file=sys.stderr)
            print("       期望路径：%s / %s" % (org_path, tiers_path),
                  file=sys.stderr)
        return 2
    rep = check(org, tiers, depts, grades)
    rep.update({"root": str(root.resolve()),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "strict": bool(args.strict)})
    summary = rep["summary"]
    if (depts or grades) and summary["checked"] == 0:
        detail = ("过滤范围未匹配任何已绑定 LLM 的席位（dept=%s / grades=%s），"
                  "拒绝空门禁" % (",".join(depts) or "-",
                                  ",".join(grades) or "-"))
        if args.json:
            print(json.dumps({"ok": False, "root": rep["root"],
                              "error": "scope_empty", "detail": detail,
                              "scope": summary["scope"],
                              "full_scope": summary["full_scope"]},
                             ensure_ascii=False, indent=1))
        else:
            print("[错误] %s" % detail, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        _print_table(rep)
    return 1 if (args.strict and summary["violations"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
