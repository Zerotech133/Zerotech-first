# -*- coding: utf-8 -*-
"""org 模型一致性 gate（K-ORG-20261031-ORMMODEL / W4 决策 D2）。

单一真源：`agent_platform/registry/org.json` → `agent_profiles[].llm_binding`。
本工具只读校验派生口径是否与 org 一致，**不写任何配置**、不触网、不重启：

- `agent_platform/hr/<SEAT>/resume.yaml` 的 `identity.model`（62 席人事档案）
- `agent_platform/config/seat_heartbeat_seats.json`（心跳探针通道，单值覆盖多席）
- `portal/agent_ide_bundle.json`（派生展示卡）
- `agent_platform/registry/model_tiers.json`（档位/启用白名单）

规则（至少 7 条，编号稳定便于 gate 消费）：

R1  input_required          org 缺失/空 → fail（自身输入错误，退出码 2）
R2  org_duplicate_seat      org 内同一席位重复出现 → fail
R3  unknown_seat            hr / 心跳名单 / bundle 出现 org 未登记席位 → fail
                            （EXTRA_KNOWN_SEATS 已登记的既有非编制角色如 ZHAOYUN → info）
R4  hr_model_conflict       hr 具体模型与 org 不一致 → fail（业务 LLM 席）
R5  hr_model_gap            hr 未落具体模型（按工单通道/按各组件绑定/规则/运行时）→ info（过渡白名单）
R6  heartbeat_single_model  心跳单值模型覆盖多席且与 org 不一致 → warn；
                            配置已声明 `model_role: probe_only` 时降为 info（探针通道，不与业务模型混用）
R7  ai1_declared_effective  AI1 declared(target)/effective(中转) 特例 → 允许，info 登记
R8  bundle_drift            bundle 业务席 `llm` 与 org 不一致 → fail；bundle 缺业务席 → warn
R9  tier_policy             org 绑定不在 model_tiers（或 enabled=false）→ fail；
                            E3 席绑定非 strong 档 → warn（显式例外登记为 info）
R10 heartbeat_status_drift  心跳 status 目录多出未在名单的席 / 名单席缺 status → warn

退出码：
  0 = 一致（无 fail；`--strict` 时还要求无 warn）
  1 = 有漂移（存在 fail，或 `--strict` 且存在 warn）
  2 = 输入错误（org/model_tiers 缺失、JSON 解析失败等）

用法：
  python tools/org_model_consistency.py              # 人类摘要 + 退出码
  python tools/org_model_consistency.py --json       # 机器可读
  python tools/org_model_consistency.py --strict     # 严格模式（warn 也阻断）
  python tools/org_model_consistency.py --root <repo>  # 指定仓库根（测试用）
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "org_model_consistency/v1"

ORG_REL = "agent_platform/registry/org.json"
TIERS_REL = "agent_platform/registry/model_tiers.json"
HR_REL = "agent_platform/hr"
HEARTBEAT_REL = "agent_platform/config/seat_heartbeat_seats.json"
BUNDLE_REL = "portal/agent_ide_bundle.json"
STATUS_REL = "agent_platform/data/seat_heartbeat"

PROVIDERS = ("ollama/", "glm/", "deepseek/", "openai/", "claude/")
# 长 token 优先匹配，避免 "glm-5.3-flash" 被 "glm-5.3" 误判为双模型；均为归一化（无 provider 前缀）
KNOWN_MODELS = (
    "qwen3.6:35b-a3b",
    "glm-5.3-flash",
    "deepseek-v4-pro",
    "deepseek-flash",
    "deepseek-chat",
    "glm-5.3",
    "qwen3.5:9b",
    "gpt-6.0",
)
DESCRIPTIVE_MARKERS = ("按工单通道", "按各组件绑定", "见 org", "org.json",
                       "规则", "运行时", "none")
AI1_SEAT = "AI1"
DECLARED_MARKERS = ("effective=", "declared")
# 已登记的既有“非 66 席”角色：出现在 bundle/心跳时只记 info，不按未知席 fail
EXTRA_KNOWN_SEATS = {
    "ZHAOYUN": "指挥总部成员（command_hq），非 66 席编制；bundle 展示用",
}
# 显式例外的 E3 席（工具内登记；不放松 model_tiers 白名单）
E3_EXCEPTIONS = {
    "GUANYU": "E3 绑定 deepseek/deepseek-flash（fast 档），2026-10-31 W4 决策登记例外；待 ARCH1 复核",
}

_LEVELS = ("fail", "warn", "info")


class InputError(ValueError):
    """输入缺失/不可解析（退出码 2）。"""


# ---------------------------------------------------------------- 归一化
def normalize_model(text) -> str:
    """去掉 provider 前缀并小写，便于跨口径比较。"""
    value = str(text or "").strip().lower()
    for prefix in PROVIDERS:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def concrete_models(text) -> list:
    """从任意文本中抽取已登记的模型 token（长 token 优先、去重保序）。"""
    rest = str(text or "").lower()
    found = []
    for token in sorted(KNOWN_MODELS, key=len, reverse=True):
        if token in rest:
            found.append(token)
            rest = rest.replace(token, " ")
    return found


def is_descriptive(text) -> bool:
    value = str(text or "").strip()
    if not value or value.lower() == "none":
        return True
    return any(marker in value for marker in DESCRIPTIVE_MARKERS)


def _as_bool(value) -> bool:
    return value is True


# ---------------------------------------------------------------- 输入加载
def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError("读取失败：%s（%s）" % (path, exc)) from exc
    except ValueError as exc:
        raise InputError("JSON 不可解析：%s（%s）" % (path, exc)) from exc


def _resume_model(path: Path) -> str:
    """从 resume.yaml 抽 identity.model（只读、不依赖 PyYAML）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^\s{2}model:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else ""


def load_org(path: Path) -> dict:
    """解析 org.json → 席位/chief/部门信息（不改变任何内容）。"""
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise InputError("org.json 顶层必须是对象")
    profiles = {}
    duplicates = []
    departments = raw.get("departments") or []
    for dept in departments:
        if not isinstance(dept, dict):
            continue
        for profile in dept.get("agent_profiles") or []:
            if not isinstance(profile, dict):
                continue
            seat = str(profile.get("agent_id") or "").strip()
            if not seat:
                continue
            if seat in profiles:
                duplicates.append(seat)
            profiles[seat] = profile
    chief = raw.get("chief") or {}
    chief_id = str(chief.get("agent_id") or "").strip()
    return {
        "profiles": profiles,
        "chief": chief,
        "chief_id": chief_id,
        "known": set(profiles) | ({chief_id} if chief_id else set()),
        "duplicates": sorted(set(duplicates)),
        "department_count": len(departments),
        "raw": raw,
    }


def load_hr(hr_dir: Path) -> dict:
    models = {}
    if not hr_dir.is_dir():
        return models
    for seat_dir in sorted(hr_dir.iterdir()):
        if not seat_dir.is_dir():
            continue
        resume = seat_dir / "resume.yaml"
        if resume.is_file():
            models[seat_dir.name] = _resume_model(resume)
    return models


def load_heartbeat(path: Path):
    if not path.is_file():
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise InputError("seat_heartbeat_seats.json 顶层必须是对象")
    return raw


def load_bundle(path: Path):
    if not path.is_file():
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise InputError("agent_ide_bundle.json 顶层必须是对象")
    agents = raw.get("agents") or {}
    return agents if isinstance(agents, dict) else {}


def load_tiers(path: Path):
    if not path.is_file():
        return None
    raw = _read_json(path)
    return raw if isinstance(raw, dict) else None


def load_status_seats(status_dir: Path) -> list:
    if not status_dir.is_dir():
        return []
    return sorted(p.name for p in status_dir.iterdir() if p.is_dir())


def load_all(root) -> dict:
    """加载全部只读输入；org/model_tiers 缺失视为输入错误。"""
    root = Path(root)
    org_path = root / ORG_REL
    if not org_path.is_file():
        raise InputError("缺少 org.json：%s" % org_path)
    tiers_path = root / TIERS_REL
    return {
        "root": str(root),
        "org": load_org(org_path),
        "hr": load_hr(root / HR_REL),
        "heartbeat": load_heartbeat(root / HEARTBEAT_REL),
        "bundle": load_bundle(root / BUNDLE_REL),
        "tiers": load_tiers(tiers_path),
        "status_seats": load_status_seats(root / STATUS_REL),
    }


# ---------------------------------------------------------------- 判定
def _add(issues, level, rule, seat, message, **extra) -> None:
    item = {"level": level, "rule": rule, "seat": str(seat or ""),
            "message": message}
    item.update(extra)
    issues.append(item)


def _tier_of(tiers, binding):
    """按 model_tiers.models 精确/归一化查档位条目。"""
    if not isinstance(tiers, dict):
        return None, ""
    models = tiers.get("models") or {}
    if not isinstance(models, dict):
        return None, ""
    if binding in models:
        return models.get(binding), binding
    target = normalize_model(binding)
    for key, entry in models.items():
        if normalize_model(key) == target:
            return entry, key
    return None, ""


def _ai1_declared(profile) -> bool:
    note = str((profile or {}).get("binding_note") or "").lower()
    return any(marker in note for marker in DECLARED_MARKERS)


def audit(inputs: dict, strict: bool = False, e3_exceptions=None) -> dict:
    """纯函数：对已加载输入做一致性判定，返回结构化结果（不读写文件）。"""
    issues = []
    inputs = inputs or {}
    org = inputs.get("org") or {}
    profiles = org.get("profiles") or {}
    chief_id = org.get("chief_id") or ""
    known = set(org.get("known") or set(profiles))
    hr = inputs.get("hr") or {}
    heartbeat = inputs.get("heartbeat")
    bundle = inputs.get("bundle")
    tiers = inputs.get("tiers")
    status_seats = list(inputs.get("status_seats") or [])
    exceptions = dict(E3_EXCEPTIONS)
    if e3_exceptions:
        exceptions.update(e3_exceptions)

    if not profiles:
        _add(issues, "fail", "input_required", "", "org 无 agent_profiles，无法判定")
        return _finish(issues, strict, org=org, hr=hr, heartbeat=heartbeat)

    # R2 org 重复席位
    for seat in org.get("duplicates") or []:
        _add(issues, "fail", "org_duplicate_seat", seat, "org 内席位重复出现")

    # 心跳/档位前置
    ((tiers or {}).get("models") or {}) if isinstance(tiers, dict) else {}
    grade_policy = ((tiers or {}).get("grade_policy") or {}) if isinstance(tiers, dict) else {}
    if tiers is None:
        _add(issues, "fail", "tier_policy", "", "缺少 model_tiers.json，无法校验绑定白名单")

    heartbeat_model = str((heartbeat or {}).get("model") or "")
    heartbeat_role = str((heartbeat or {}).get("model_role") or "")
    heartbeat_seats = [str(s).upper() for s in ((heartbeat or {}).get("seats") or [])]

    # R3 未知席：hr / 心跳名单 / bundle（EXTRA_KNOWN_SEATS 只记 info）
    def _seat_issue(seat, source, message):
        if seat in EXTRA_KNOWN_SEATS:
            _add(issues, "info", "known_extra_seat", seat,
                 "%s（已登记非编制角色）" % EXTRA_KNOWN_SEATS[seat], source=source)
        else:
            _add(issues, "fail", "unknown_seat", seat, message, source=source)

    for seat in sorted(set(hr) - known):
        _seat_issue(seat, "hr", "hr 有档案但 org 未登记该席")
    for seat in sorted(set(heartbeat_seats) - known):
        _seat_issue(seat, "heartbeat", "心跳名单含 org 未登记席位")
    for seat in sorted(set(bundle or {}) - known):
        _seat_issue(seat, "bundle", "bundle 含 org 未登记席位")

    # 逐席规则
    for seat in sorted(profiles):
        profile = profiles[seat] or {}
        binding = str(profile.get("llm_binding") or "").strip()
        binding_norm = normalize_model(binding)
        grade = str(profile.get("grade") or "").strip()
        ai1_declared = (seat == AI1_SEAT and binding.startswith("openai/")
                        and _ai1_declared(profile))

        # R9 档位白名单 / 禁用 / E3 策略（runtime/规则席 org=none 不适用）
        if not binding:
            _add(issues, "fail", "tier_policy", seat, "org 未声明 llm_binding")
        elif binding_norm != "none" and tiers is not None:
            entry, matched = _tier_of(tiers, binding)
            if entry is None:
                _add(issues, "fail", "tier_policy", seat,
                     "org 绑定不在 model_tiers 白名单", binding=binding)
            else:
                enabled = entry.get("enabled") if isinstance(entry, dict) else None
                if enabled is False:
                    if ai1_declared:
                        _add(issues, "info", "ai1_declared_effective", seat,
                             "AI1 declared target 未启用，按 declared/effective 特例放行",
                             binding=binding)
                    else:
                        _add(issues, "fail", "tier_policy", seat,
                             "org 绑定模型 enabled=false", binding=binding)
                tier = str(entry.get("tier") or "") if isinstance(entry, dict) else ""
                allowed = ((grade_policy.get("E3") or {}).get("allow") or []) if grade == "E3" else []
                if grade == "E3" and allowed and "strong" in allowed and tier != "strong":
                    if seat in exceptions:
                        _add(issues, "info", "e3_policy_exception", seat,
                             "E3 绑定非 strong 档，已登记例外：%s" % exceptions[seat],
                             binding=binding, tier=tier)
                    else:
                        _add(issues, "warn", "tier_policy", seat,
                             "E3 席绑定非 strong 档（%s）" % (tier or "?"),
                             binding=binding)

        # R4/R5/R7 hr 口径
        hr_model = hr.get(seat)
        if hr_model is None:
            if seat != chief_id:
                _add(issues, "warn", "hr_missing", seat, "hr 无 resume.yaml（编制有、档案缺）")
        else:
            tokens = concrete_models(hr_model)
            if binding_norm == "none":
                if tokens:
                    _add(issues, "fail", "hr_model_conflict", seat,
                         "runtime/规则席 org=none，但 hr 写了具体模型",
                         org=binding, hr=hr_model)
                else:
                    _add(issues, "info", "hr_model_gap", seat,
                         "runtime/规则席豁免具体模型校验", org=binding, hr=hr_model)
            elif tokens:
                if tokens[0] != binding_norm:
                    if ai1_declared:
                        _add(issues, "info", "ai1_declared_effective", seat,
                             "AI1 hr 与 org declared 不一致，按 declared/effective 特例放行",
                             org=binding, hr=hr_model)
                    else:
                        _add(issues, "fail", "hr_model_conflict", seat,
                             "hr 具体模型与 org 不一致",
                             org=binding, hr=hr_model, hr_model=tokens[0])
            else:
                if ai1_declared:
                    _add(issues, "info", "ai1_declared_effective", seat,
                         "AI1 declared/effective 特例已登记", org=binding, hr=hr_model)
                elif is_descriptive(hr_model):
                    _add(issues, "info", "hr_model_gap", seat,
                         "hr 未落具体模型（按工单通道/按组件/规则/运行时）",
                         org=binding, hr=hr_model)
                else:
                    _add(issues, "warn", "hr_model_gap", seat,
                         "hr 模型文本无法识别（未落具体模型）", hr=hr_model)

        # R8 bundle 派生口径
        if isinstance(bundle, dict):
            card = bundle.get(seat)
            bundle_llm = str((card or {}).get("llm") or "") if isinstance(card, dict) else ""
            if binding_norm != "none" and binding:
                if not bundle_llm:
                    _add(issues, "warn", "bundle_drift", seat,
                         "bundle 缺该业务席 llm 字段", org=binding)
                elif normalize_model(bundle_llm) != binding_norm:
                    if ai1_declared:
                        _add(issues, "info", "ai1_declared_effective", seat,
                             "AI1 bundle 与 org declared 差异按特例放行",
                             org=binding, bundle=bundle_llm)
                    else:
                        _add(issues, "fail", "bundle_drift", seat,
                             "bundle llm 与 org 不一致",
                             org=binding, bundle=bundle_llm)
            else:
                if bundle_llm and not is_descriptive(bundle_llm):
                    _add(issues, "info", "bundle_runtime_label", seat,
                         "runtime/规则席 bundle 标注非空（仅展示层）", bundle=bundle_llm)

    # R6 心跳单值覆盖
    if heartbeat is None:
        _add(issues, "warn", "heartbeat_missing", "", "缺少 seat_heartbeat_seats.json")
    else:
        if heartbeat_role != "probe_only":
            conflicts = []
            for seat in heartbeat_seats:
                profile = profiles.get(seat) or {}
                binding = str(profile.get("llm_binding") or "").strip()
                if not binding or normalize_model(binding) == "none":
                    continue
                if normalize_model(binding) != normalize_model(heartbeat_model):
                    conflicts.append(seat)
            if conflicts:
                _add(issues, "warn", "heartbeat_single_model", "",
                     "心跳单值模型 %r 覆盖 %d 席且与 org 不一致（未声明 probe_only）"
                     % (heartbeat_model, len(conflicts)),
                     heartbeat_model=heartbeat_model, seats=conflicts[:12],
                     seat_count=len(conflicts))
        else:
            _add(issues, "info", "heartbeat_single_model", "",
                 "心跳模型已声明 probe_only（探针通道，不参与业务模型一致性校验）",
                 heartbeat_model=heartbeat_model)

    # R10 心跳 status 目录漂移
    if status_seats:
        extra = sorted(set(status_seats) - set(heartbeat_seats))
        missing = sorted(set(heartbeat_seats) - set(status_seats))
        for seat in extra:
            _add(issues, "warn", "heartbeat_status_drift", seat,
                 "心跳 status 目录存在但不在名单（残留/漂移）", source="status")
        for seat in missing:
            _add(issues, "warn", "heartbeat_status_drift", seat,
                 "心跳名单含该席但无 status 目录", source="status")

    return _finish(issues, strict, org=org, hr=hr, heartbeat=heartbeat)


def _finish(issues, strict, org=None, hr=None, heartbeat=None) -> dict:
    counts = {level: sum(1 for item in issues if item["level"] == level)
              for level in _LEVELS}
    fail = counts["fail"]
    warn = counts["warn"]
    exit_code = 0
    if fail:
        exit_code = 1
    elif strict and warn:
        exit_code = 1
    occupied = len((org or {}).get("profiles") or {})
    hr_count = len(hr or {})
    heartbeat_seats = len(((heartbeat or {}).get("seats")) or [])
    rule_counts = {}
    for item in issues:
        rule_counts[item["rule"]] = rule_counts.get(item["rule"], 0) + 1
    return {
        "schema": SCHEMA,
        "ok": fail == 0,
        "consistent": fail == 0,
        "strict": bool(strict),
        "exit_code": exit_code,
        "summary": {
            "fail": fail, "warn": warn, "info": counts["info"],
            "issues": len(issues), "seats": occupied, "hr": hr_count,
            "heartbeat_seats": heartbeat_seats,
        },
        "counts": {
            "hr_conflict": rule_counts.get("hr_model_conflict", 0),
            "hr_gap": rule_counts.get("hr_model_gap", 0),
            "heartbeat_single_model": rule_counts.get("heartbeat_single_model", 0),
            "bundle_drift": rule_counts.get("bundle_drift", 0),
            "tier_policy": rule_counts.get("tier_policy", 0),
            "unknown_seat": rule_counts.get("unknown_seat", 0),
            "heartbeat_status_drift": rule_counts.get("heartbeat_status_drift", 0),
        },
        "issues": issues,
    }


def render(result: dict, info_limit: int = 20) -> str:
    """人类可读摘要（fail/warn 全量；info 超限折叠）。"""
    summary = result.get("summary") or {}
    lines = [
        "[org_model_consistency] schema=%s strict=%s" % (
            result.get("schema"), result.get("strict")),
        "席位 %d / hr %d / 心跳名单 %d" % (
            summary.get("seats", 0), summary.get("hr", 0),
            summary.get("heartbeat_seats", 0)),
        "FAIL %d | WARN %d | INFO %d" % (
            summary.get("fail", 0), summary.get("warn", 0), summary.get("info", 0)),
    ]
    shown_info = 0
    for item in result.get("issues") or []:
        if item["level"] == "info":
            shown_info += 1
            if shown_info > info_limit:
                continue
        lines.append("- [%s][%s] %s%s" % (
            item["level"].upper(), item["rule"],
            (item.get("seat") + ": ") if item.get("seat") else "",
            item.get("message") or ""))
    hidden = max(0, summary.get("info", 0) - info_limit)
    if hidden:
        lines.append("- ... 另有 %d 条 info（--json 查看全量）" % hidden)
    if result.get("ok") and not (result.get("strict") and summary.get("warn")):
        lines.append("结论: 一致 → 退出码 0")
    else:
        lines.append("结论: 漂移（fail=%d warn=%d）→ 退出码 %d"
                     % (summary.get("fail", 0), summary.get("warn", 0),
                        result.get("exit_code", 1)))
    return "\n".join(lines)


def run(root, strict: bool = False, e3_exceptions=None) -> dict:
    """加载指定仓库根并判定；输入错误抛 InputError。"""
    inputs = load_all(root)
    result = audit(inputs, strict=strict, e3_exceptions=e3_exceptions)
    result["root"] = inputs.get("root", str(root))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="org 模型单一真源一致性 gate（只读）")
    parser.add_argument("--root", default=str(ROOT), help="仓库根（默认本仓）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：warn 也返回非零")
    args = parser.parse_args(argv)
    try:
        result = run(args.root, strict=args.strict)
    except InputError as exc:
        payload = {"schema": SCHEMA, "ok": False, "error": str(exc),
                   "exit_code": 2}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("[org_model_consistency] 输入错误：%s" % exc)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
