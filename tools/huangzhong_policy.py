# -*- coding: utf-8 -*-
"""黄忠策略即代码（P1-A · WO-HZ-POLICY-AS-CODE-20261031，只读）。

把「改动类型/风险 → 必需证据集 + 判定阈值 + 建议动作」写成可配置策略（policy.json），
供后续阻断分级使用。**本阶段（默认现状）执行动作恒为 observe（不阻断）**：
策略里即使声明 action=block，也只输出诉求（requested_action）并标 needs_L3=true，
由老田 L3 单独批准后才允许阻断——本工具不做任何强制点接入。

用法：
  python -X utf8 tools/huangzhong_policy.py --json --changed-files "tools/a.py,docs/b.md"
  python -X utf8 tools/huangzhong_policy.py --json --commit <sha>     # git diff 取改动
  python -X utf8 tools/huangzhong_policy.py --json --policy my.json --evidence ev.json
  python -X utf8 tools/huangzhong_policy.py --json --md report.md

策略文件：
  默认读 <repo-root>/work/huangzhong/policy.json；不存在 → 用内置 default_policy
  （输出 source="builtin"）。--policy 指定自定义 JSON。策略文件存在但不可读/结构
  非法 → rc=2（fail-visible，不静默回退内置策略）。

策略结构（huangzhong_policy/v1）：
  {
    "version": "v1",
    "default_action": "observe",              # observe | block
    "rules": [{
      "name": "规则名",
      "match": {"paths_glob": ["tools/**"], "risk_keywords": ["secret"]},
      "required_evidence": ["baseline", "redteam", "rollback_point"],
      "min_baseline_passed": 1,               # 基线通过数下限（且 failed=0）
      "max_evidence_age_hours": 48,           # 证据年龄门（认证口径 48h）
      "min_redteam_pass_rate": 0.8,           # 可选：红队通过率下限
      "action": "observe",                    # observe | block（block 强制降级）
      "note": "说明"
    }]
  }
  匹配语义：paths_glob 非空 → 任一改动文件命中任一 glob；risk_keywords 非空 →
  任一关键词命中改动路径或 commit message；两类条件同时非空时须同时满足（AND）。

证据快照（可选 --evidence，用于判定 missing_evidence/阈值未达）：
  {"baseline": {"passed": 100, "failed": 0, "age_hours": 2.0},
   "redteam": {"pass_rate": 0.9, "age_hours": 1.5},
   "rollback_point": "git revert <sha>", "data_backup": "..."}
  未提供快照 → missing_evidence=null 并 warning（未判定 ≠ 已齐备，fail-visible）。

外部命令（git diff / git log）统一经模块级 `_run(cmd, cwd, timeout)`，测试可注入；
退出码：0=评估完成；1=输入不完整（git 取改动失败/证据快照不可读）；2=策略文件坏。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_REL = Path("work") / "huangzhong" / "policy.json"
POLICY_SCHEMA = "huangzhong_policy/v1"
EXIT_OK, EXIT_INCOMPLETE, EXIT_POLICY_ERROR = 0, 1, 2
OBSERVE, BLOCK = "observe", "block"
ACTIONS = (OBSERVE, BLOCK)
DEFAULT_MAX_AGE_HOURS = 48.0
RUN_TIMEOUT = 120.0
MAX_LIST = 50

# 现状口径（2026-10-31 P1-A）：全 observe 不阻断；证据 ≤48h；基线无失败；
# 红队 pass_rate ≥0.8；rollback_point 必需。——内置规则照该口径。
BUILTIN_NOTE = ("现状口径（P1-A）：全 observe 不阻断；证据年龄 ≤48h、基线无失败、"
                "红队 pass_rate ≥0.8、rollback_point 必需")


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601/空格格式/epoch 秒）；无时区按本机时区解释。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    text, err = _read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return rows[:limit]


def _log(msg: str) -> None:
    print("[huangzhong_policy] " + str(msg), file=sys.stderr)


def _run(cmd: list, cwd: Path | str | None = None,
         timeout: float = RUN_TIMEOUT) -> dict:
    """执行外部命令（模块级；测试可 monkeypatch）。不抛异常，失败如实返回。

    返回 {ok, rc, stdout, stderr, error, cmd, cwd, elapsed_s}；ok 仅表示命令成功
    启动/未超时，业务返回码由调用方解释。
    """
    cmd = [str(c) for c in cmd]
    started = _now()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                "error": "%s: %s" % (type(e).__name__, e), "cmd": cmd,
                "cwd": str(cwd) if cwd else None,
                "elapsed_s": round((_now() - started).total_seconds(), 2)}
    return {"ok": True, "rc": proc.returncode,
            "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "error": None, "cmd": cmd, "cwd": str(cwd) if cwd else None,
            "elapsed_s": round((_now() - started).total_seconds(), 2)}


def _split_files(value) -> list[str]:
    """--changed-files / 列表输入 → 规范化去重文件列表（正斜杠、去空白）。"""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for item in items:
        s = str(item).strip().replace("\\", "/")
        while s.startswith("./"):
            s = s[2:]
        if s and s not in out:
            out.append(s)
    return out


# --------------------------------------------------------------- 策略：加载/校验

def builtin_policy() -> dict:
    """内置 default_policy：现状口径（全 observe；证据 ≤48h；基线无失败；
    红队 ≥0.8；rollback_point 必需）。"""
    return {
        "version": "v1",
        "default_action": OBSERVE,
        "rules": [
            {
                "name": "代码改动-观察",
                "match": {"paths_glob": ["tools/**", "portal/**",
                                         "agent_platform/**", "common/**",
                                         "k_stock/**", "scripts/**",
                                         "quant_backtest/**"],
                          "risk_keywords": []},
                "required_evidence": ["baseline", "redteam", "rollback_point"],
                "min_baseline_passed": 1,
                "max_evidence_age_hours": DEFAULT_MAX_AGE_HOURS,
                "min_redteam_pass_rate": 0.8,
                "action": OBSERVE,
                "note": BUILTIN_NOTE,
            },
            {
                "name": "数据根改动-观察",
                "match": {"paths_glob": ["quant_backtest/data/**", "data/**",
                                         "**/*.sql", "**/*.db"],
                          "risk_keywords": []},
                "required_evidence": ["baseline", "redteam", "rollback_point",
                                      "data_backup"],
                "min_baseline_passed": 1,
                "max_evidence_age_hours": DEFAULT_MAX_AGE_HOURS,
                "min_redteam_pass_rate": 0.8,
                "action": OBSERVE,
                "note": ("数据根改动须附备份证据（AGENTS §3 数据根互斥）；"
                         "本阶段仅提示观察，不阻断"),
            },
            {
                "name": "文档/交付物-观察",
                "match": {"paths_glob": ["docs/**", "deliverables/**",
                                         "*.md", "**/*.md"],
                          "risk_keywords": []},
                "required_evidence": ["rollback_point"],
                "min_baseline_passed": 0,
                "max_evidence_age_hours": DEFAULT_MAX_AGE_HOURS,
                "action": OBSERVE,
                "note": "纯文档/交付物改动：最低限回滚点；仍全 observe 不阻断",
            },
        ],
    }


def _validate_rule(raw: dict, idx: int) -> tuple[dict | None, list[str]]:
    errors = []
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("rules[%d].name 缺失/非字符串" % idx)
        name = "规则%d" % (idx + 1)
    match = raw.get("match")
    if match is None:
        match = {}
    if not isinstance(match, dict):
        errors.append("rules[%d].match 不是对象" % idx)
        match = {}
    globs = match.get("paths_glob", [])
    if not isinstance(globs, list) or not all(
            isinstance(g, str) and g.strip() for g in globs):
        errors.append("rules[%d].match.paths_glob 须为字符串数组" % idx)
        globs = []
    kws = match.get("risk_keywords", [])
    if not isinstance(kws, list) or not all(
            isinstance(k, str) and k.strip() for k in kws):
        errors.append("rules[%d].match.risk_keywords 须为字符串数组" % idx)
        kws = []
    required = raw.get("required_evidence", [])
    if not isinstance(required, list) or not all(
            isinstance(r, str) and r.strip() for r in required):
        errors.append("rules[%d].required_evidence 须为字符串数组" % idx)
        required = []
    min_passed = raw.get("min_baseline_passed", 0)
    if not isinstance(min_passed, int) or isinstance(min_passed, bool) \
            or min_passed < 0:
        errors.append("rules[%d].min_baseline_passed 须为非负整数" % idx)
        min_passed = 0
    max_age = raw.get("max_evidence_age_hours", DEFAULT_MAX_AGE_HOURS)
    if not _num(max_age) or float(max_age) <= 0:
        errors.append("rules[%d].max_evidence_age_hours 须为正数" % idx)
        max_age = DEFAULT_MAX_AGE_HOURS
    min_rate = raw.get("min_redteam_pass_rate")
    if min_rate is not None and (not _num(min_rate) or not 0 <= float(min_rate) <= 1):
        errors.append("rules[%d].min_redteam_pass_rate 须为 0~1 数值或 null" % idx)
        min_rate = None
    action = raw.get("action", OBSERVE)
    if action not in ACTIONS:
        errors.append("rules[%d].action 须为 observe/block" % idx)
        action = OBSERVE
    note = raw.get("note", "")
    if not isinstance(note, str):
        errors.append("rules[%d].note 须为字符串" % idx)
        note = ""
    if errors:
        return None, errors
    return {"name": name.strip(),
            "match": {"paths_glob": [g.strip().replace("\\", "/") for g in globs],
                      "risk_keywords": [k.strip() for k in kws]},
            "required_evidence": [r.strip() for r in required],
            "min_baseline_passed": int(min_passed),
            "max_evidence_age_hours": float(max_age),
            "min_redteam_pass_rate": (float(min_rate)
                                      if min_rate is not None else None),
            "action": action, "note": note}, []


def _validate_policy(data) -> tuple[dict | None, list[str]]:
    """校验/归一化策略结构；返回 (policy, errors)。errors 非空 → 策略不可用。"""
    if not isinstance(data, dict):
        return None, ["策略顶层不是 JSON 对象"]
    errors = []
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("version 缺失/非字符串")
        version = "v1"
    default_action = data.get("default_action", OBSERVE)
    if default_action not in ACTIONS:
        errors.append("default_action 须为 observe/block")
        default_action = OBSERVE
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append("rules 须为非空数组")
        raw_rules = []
    rules = []
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            errors.append("rules[%d] 不是对象" % i)
            continue
        rule, rule_errors = _validate_rule(raw, i)
        errors.extend(rule_errors)
        if rule is not None:
            rules.append(rule)
    policy = {"schema": POLICY_SCHEMA, "version": version.strip(),
              "default_action": default_action, "rules": rules}
    return policy, errors


def load_policy(policy_path: Path | str | None, root: Path) -> dict:
    """加载策略：默认 work/huangzhong/policy.json（缺失→内置 default_policy）。

    返回 {ok, source(builtin|file), path, policy, error}；坏文件 → ok=False（rc2，
    不静默回退内置策略）。
    """
    root = Path(root)
    explicit = bool(policy_path)
    path = Path(policy_path) if explicit else root / DEFAULT_POLICY_REL
    if not path.exists():
        if explicit:
            return {"ok": False, "source": "file", "path": str(path),
                    "policy": None,
                    "error": "策略文件不存在：%s" % path}
        return {"ok": True, "source": "builtin", "path": None,
                "policy": builtin_policy(), "error": None}
    data, err = _read_json(path)
    if err is not None:
        return {"ok": False, "source": "file", "path": str(path),
                "policy": None,
                "error": "策略文件不可读：%s（%s）" % (path, err)}
    parsed, errors = _validate_policy(data)
    if errors:
        return {"ok": False, "source": "file", "path": str(path),
                "policy": None,
                "error": "策略文件结构非法：%s" % "；".join(errors)}
    return {"ok": True, "source": "file", "path": str(path),
            "policy": parsed, "error": None}


# ------------------------------------------------------------------ 规则匹配

def _match_glob(path: str, pattern: str) -> bool:
    """改动路径 × glob 匹配（fnmatch 口径，兼容 `**`/裸文件名）。"""
    path = path.replace("\\", "/").strip()
    pattern = pattern.replace("\\", "/").strip()
    if not path or not pattern:
        return False
    if fnmatch.fnmatch(path, pattern):
        return True
    if "/" not in pattern and fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern):
        return True
    if pattern.startswith("**/"):
        rest = pattern[3:]
        if fnmatch.fnmatch(path, rest) or fnmatch.fnmatch(
                path, "*/" + rest):
            return True
    return False


def match_rule(rule: dict, changed_files: list[str],
               commit_message: str | None = None) -> dict:
    """规则 × 改动集匹配：glob/keyword 非空类别须同时满足（AND）。"""
    match = rule.get("match") or {}
    globs = match.get("paths_glob") or []
    keywords = match.get("risk_keywords") or []
    haystack = []
    for f in changed_files:
        low = str(f).lower()
        haystack.append(low)
        haystack.append(low.rsplit("/", 1)[-1])
    if commit_message:
        haystack.append(str(commit_message).lower())
    matched_files = []
    if globs:
        for f in changed_files:
            if any(_match_glob(f, g) for g in globs):
                matched_files.append(f)
    matched_keywords = []
    for kw in keywords:
        needle = str(kw).strip().lower()
        if needle and any(needle in h for h in haystack):
            matched_keywords.append(str(kw))
    glob_ok = (not globs) or bool(matched_files)
    keyword_ok = (not keywords) or bool(matched_keywords)
    matched = bool(changed_files) and glob_ok and keyword_ok
    if matched and not globs:
        matched_files = list(changed_files)
    return {"matched": matched, "matched_files": matched_files,
            "matched_keywords": matched_keywords}


# ------------------------------------------------------------------ 证据判定

def load_evidence(evidence_path: Path | str | None) -> dict:
    """可选证据快照：{provided, path, snapshot, error}；不可读如实标注。"""
    if not evidence_path:
        return {"provided": False, "path": None, "snapshot": None,
                "error": None}
    path = Path(evidence_path)
    if not path.exists():
        return {"provided": False, "path": str(path), "snapshot": None,
                "error": "证据快照文件不存在：%s" % path}
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        return {"provided": False, "path": str(path), "snapshot": None,
                "error": "证据快照不可读：%s" % (err or "顶层不是 JSON 对象")}
    return {"provided": True, "path": str(path), "snapshot": data,
            "error": None}


def _evidence_age(item: dict, now: dt.datetime) -> float | None:
    age = item.get("age_hours")
    if _num(age):
        return float(age)
    gen = _parse_ts(item.get("generated_at"))
    if gen is not None:
        return round((now - gen).total_seconds() / 3600.0, 2)
    return None


def _evidence_present(value) -> bool:
    return not (value is None or value == "" or value == [] or value == {})


def check_evidence(evidence: dict, matched_rules: list[dict],
                   required: list[str], now: dt.datetime) -> dict:
    """证据快照 × 必需证据/阈值 → {provided, present, missing, unmet, ok}。"""
    out = {"provided": bool(evidence.get("provided")),
           "path": evidence.get("path"), "error": evidence.get("error"),
           "present": {}, "missing_evidence": None,
           "unmet_requirements": [], "ok": None, "warnings": []}
    if not out["provided"]:
        if evidence.get("error"):
            out["warnings"].append("证据快照不可读：%s（fail-visible，未判定）"
                                   % evidence["error"])
        return out
    snapshot = evidence.get("snapshot") or {}
    present = {name: _evidence_present(snapshot.get(name)) for name in required}
    missing = [name for name in required if not present[name]]
    unmet, seen = [], set()

    def _add(msg):
        if msg not in seen:
            seen.add(msg)
            unmet.append(msg)

    for rule in matched_rules:
        base = snapshot.get("baseline")
        base = base if isinstance(base, dict) else None
        redteam = snapshot.get("redteam")
        redteam = redteam if isinstance(redteam, dict) else None
        max_age = rule.get("max_evidence_age_hours")
        if base is not None:
            failed = base.get("failed")
            if _num(failed) and failed > 0:
                _add("规则「%s」：baseline.failed=%s > 0（基线存在失败）"
                     % (rule["name"], failed))
            passed = base.get("passed")
            min_passed = rule.get("min_baseline_passed")
            if _num(passed) and _num(min_passed) and passed < min_passed:
                _add("规则「%s」：baseline.passed=%s < min_baseline_passed=%s"
                     % (rule["name"], passed, min_passed))
            age = _evidence_age(base, now)
            if age is None:
                out["warnings"].append("规则「%s」：baseline 证据年龄不可判定"
                                       "（age_hours/generated_at 缺失）" % rule["name"])
            elif _num(max_age) and age > max_age:
                _add("规则「%s」：baseline 证据年龄 %.1fh > %.0fh"
                     % (rule["name"], age, max_age))
        if redteam is not None:
            rate = redteam.get("pass_rate")
            min_rate = rule.get("min_redteam_pass_rate")
            if _num(rate) and _num(min_rate) and rate < min_rate:
                _add("规则「%s」：redteam.pass_rate=%s < %s"
                     % (rule["name"], rate, min_rate))
            age = _evidence_age(redteam, now)
            if age is None:
                out["warnings"].append("规则「%s」：redteam 证据年龄不可判定"
                                       "（age_hours/generated_at 缺失）" % rule["name"])
            elif _num(max_age) and age > max_age:
                _add("规则「%s」：redteam 证据年龄 %.1fh > %.0fh"
                     % (rule["name"], age, max_age))
    out["present"] = present
    out["missing_evidence"] = missing
    out["unmet_requirements"] = unmet
    out["ok"] = (not missing) and (not unmet)
    return out


# ------------------------------------------------------------------ 评估主体

def evaluate(changed_files: list[str], policy: dict, evidence: dict,
             now: dt.datetime,
             commit_message: str | None = None) -> dict:
    """按策略评估改动：命中规则/必需证据/缺失/动作（block 强制降级 observe）。"""
    matched_rules = []
    for rule in policy["rules"]:
        m = match_rule(rule, changed_files, commit_message=commit_message)
        if not m["matched"]:
            continue
        matched_rules.append({
            "name": rule["name"],
            "action": rule["action"],
            "required_evidence": list(rule["required_evidence"]),
            "min_baseline_passed": rule["min_baseline_passed"],
            "max_evidence_age_hours": rule["max_evidence_age_hours"],
            "min_redteam_pass_rate": rule.get("min_redteam_pass_rate"),
            "note": rule.get("note") or "",
            "matched_files": _cap(m["matched_files"]),
            "matched_keywords": m["matched_keywords"],
        })
    required = []
    for rule in matched_rules:
        for name in rule["required_evidence"]:
            if name not in required:
                required.append(name)
    requested_actions = [r["action"] for r in matched_rules]
    if matched_rules:
        requested_action = BLOCK if BLOCK in requested_actions else OBSERVE
    elif changed_files:
        requested_action = policy["default_action"]
    else:
        requested_action = OBSERVE
    needs_l3 = requested_action == BLOCK
    # P1-A 默认现状：执行动作恒 observe；block 一律强制降级（升阻断需老田 L3 批准）
    action = OBSERVE
    downgraded = needs_l3
    ev = check_evidence(evidence, matched_rules, required, now)
    warnings = list(ev.get("warnings") or [])
    rationale = []
    if matched_rules:
        rationale.append("命中 %d 条策略规则：%s"
                         % (len(matched_rules),
                            "、".join(r["name"] for r in matched_rules)))
    elif changed_files:
        rationale.append("未命中任何规则 → 取 default_action=%s"
                         "（本阶段执行动作恒 observe，不阻断）"
                         % policy["default_action"])
    else:
        rationale.append("无改动文件输入，未命中任何规则"
                         "（fail-visible：未评估 ≠ 无风险）")
    if needs_l3:
        rationale.append("策略诉求 block：按 P1-A 口径强制降级为 observe、"
                         "needs_L3=true（升级阻断须老田 L3 单独批准）")
    else:
        rationale.append("策略诉求 observe：本阶段默认现状，不阻断 merge")
    if not ev["provided"]:
        rationale.append("未提供证据快照（--evidence），missing_evidence 未判定"
                         "（fail-visible：未验证 ≠ 已齐备）")
    else:
        if ev["missing_evidence"]:
            rationale.append("证据缺失 %d 项：%s"
                             % (len(ev["missing_evidence"]),
                                "、".join(ev["missing_evidence"])))
        if ev["unmet_requirements"]:
            rationale.append("证据阈值未达 %d 项"
                             % len(ev["unmet_requirements"]))
        if ev["ok"]:
            rationale.append("证据快照满足命中规则的必需证据与阈值"
                             "（仅格式/数值核对，未做真实性核验）")
    suggestion = "建议动作：observe（不阻断）"
    if needs_l3:
        suggestion += "；策略要求 block 已降级，需老田 L3 单独批准后方可阻断"
    if ev["missing_evidence"]:
        suggestion += "；先补齐缺失证据：%s" % "、".join(ev["missing_evidence"])
    elif ev["unmet_requirements"]:
        suggestion += "；先刷新/整改未达阈值项"
    if not ev["provided"] and changed_files:
        suggestion += "；可用 --evidence 提供证据快照做齐备性判定"
    return {"matched_rules": matched_rules,
            "required_evidence": required,
            "missing_evidence": ev["missing_evidence"],
            "unmet_requirements": ev["unmet_requirements"],
            "evidence": ev,
            "requested_action": requested_action,
            "action": action,
            "needs_L3": needs_l3,
            "downgraded": downgraded,
            "rationale": rationale,
            "suggestion": suggestion,
            "warnings": warnings}


# ------------------------------------------------------------ 改动来源（git）

def _git_changed_files(commit: str, root: Path, warnings: list[str]) -> dict:
    """git diff --name-only <commit>^..<commit> 取改动（失败回退 git show；fail-visible）。"""
    info = {"source": "git", "commit": commit, "cmd": None, "fallback": None,
            "error": None, "returncode": None}
    cmd = ["git", "diff", "--name-only", "%s^..%s" % (commit, commit)]
    res = _run(cmd, cwd=root, timeout=RUN_TIMEOUT)
    info["cmd"], info["returncode"] = cmd, res.get("rc")
    files, err = [], None
    if res.get("ok") and res.get("rc") == 0:
        files = _split_lines(res.get("stdout"))
        info["method"] = "git-diff"
        return {"files": files, "info": info}
    err = res.get("error") or ("git diff rc=%s" % res.get("rc"))
    warnings.append("git diff 取改动失败（%s），回退 git show" % err)
    cmd2 = ["git", "show", "--name-only", "--format=", commit]
    res2 = _run(cmd2, cwd=root, timeout=RUN_TIMEOUT)
    info["fallback"] = {"cmd": cmd2, "returncode": res2.get("rc")}
    if res2.get("ok") and res2.get("rc") == 0:
        files = _split_lines(res2.get("stdout"))
        info["method"] = "git-show"
        return {"files": files, "info": info}
    info["method"] = None
    info["error"] = "git 取改动失败（diff/show 均不可用：%s）" % (
        err + "；" + (res2.get("error") or ("rc=%s" % res2.get("rc"))))
    warnings.append(info["error"])
    return {"files": [], "info": info}


def _split_lines(text: str | None) -> list[str]:
    files = []
    for line in (text or "").splitlines():
        s = line.strip().replace("\\", "/")
        if s and s not in files:
            files.append(s)
    return files


def _git_commit_message(commit: str, root: Path,
                        warnings: list[str]) -> str | None:
    res = _run(["git", "log", "-1", "--format=%B", commit], cwd=root,
               timeout=RUN_TIMEOUT)
    if res.get("ok") and res.get("rc") == 0:
        msg = (res.get("stdout") or "").strip()
        return msg or None
    warnings.append("git log 取 commit message 失败（%s），risk_keywords 仅按"
                    "文件路径匹配" % (res.get("error") or ("rc=%s" % res.get("rc"))))
    return None


def resolve_changed_files(changed_files, commit: str | None, root: Path,
                          warnings: list[str]) -> dict:
    """解析改动来源：--changed-files 优先；否则 --commit 走 git；都没有→空输入。"""
    explicit = _split_files(changed_files)
    if explicit:
        return {"files": explicit, "source": "changed-files", "git": None,
                "error": None}
    if commit:
        got = _git_changed_files(commit, Path(root), warnings)
        return {"files": got["files"], "source": "commit",
                "git": got["info"], "error": got["info"].get("error")}
    warnings.append("未提供 --changed-files / --commit，按空改动评估"
                    "（fail-visible：未评估 ≠ 无风险）")
    return {"files": [], "source": "none", "git": None, "error": None}


# ------------------------------------------------------------------ MD 报告

def _render_md(rep: dict) -> str:
    md = ["# 黄忠策略即代码评估报告（P1-A，默认 observe 不阻断）", "",
          "- 生成时间：%s（Asia/Shanghai）" % rep["generated_at"],
          "- 仓库根：`%s`" % rep["repo_root"],
          "- 策略来源：**%s**%s" % (
              rep["policy"]["source"],
              "（`%s`）" % rep["policy"]["path"] if rep["policy"].get("path") else ""),
          "- 策略版本：%s；规则数：%d；default_action=%s"
          % (rep["policy"].get("version") or "?",
             rep["policy"].get("rules_count", 0),
             rep["policy"].get("default_action") or "?"),
          "- 改动来源：%s（%d 个文件%s）"
          % (rep["input"]["source"], len(rep.get("changed_files") or []),
             "，commit=%s" % rep["input"]["commit"] if rep["input"].get("commit")
             else ""),
          "- **结论：action = %s｜requested_action = %s｜needs_L3 = %s**"
          % (rep["action"], rep["requested_action"], rep["needs_L3"]),
          "", "## 一、改动文件", ""]
    if rep.get("changed_files"):
        for f in rep["changed_files"]:
            md.append("- `%s`" % f)
    else:
        md.append("- （无改动文件输入）")
    md += ["", "## 二、命中规则", ""]
    if rep["matched_rules"]:
        md += ["| 规则 | 诉求动作 | 必需证据 | 阈值 | 说明 |",
               "|---|---|---|---|---|"]
        for r in rep["matched_rules"]:
            th = "证据≤%.0fh；基线通过≥%d" % (r["max_evidence_age_hours"],
                                              r["min_baseline_passed"])
            if _num(r.get("min_redteam_pass_rate")):
                th += "；红队≥%s" % r["min_redteam_pass_rate"]
            md.append("| %s | %s | %s | %s | %s |"
                      % (r["name"], r["action"],
                         "、".join(r["required_evidence"]) or "—", th,
                         (r["note"] or "—")[:60]))
        for r in rep["matched_rules"]:
            if r["matched_keywords"]:
                md.append("- %s：命中关键词 %s"
                          % (r["name"], "、".join(r["matched_keywords"])))
    else:
        md.append("- 未命中规则 → default_action=%s（执行动作恒 observe）"
                  % (rep["policy"].get("default_action") or "?"))
    md += ["", "## 三、必需证据（命中规则并集）", "",
           "- %s" % ("、".join(rep["required_evidence"]) or "—")]
    ev = rep["evidence"]
    md += ["", "## 四、证据判定", ""]
    if ev.get("provided"):
        md.append("- 快照：`%s`" % ev.get("path"))
        md.append("- 缺失证据：%s"
                  % ("、".join(ev["missing_evidence"]) if ev["missing_evidence"]
                     else "无"))
        md.append("- 未达阈值项：%s"
                  % ("；".join(ev["unmet_requirements"])
                     if ev["unmet_requirements"] else "无"))
        md.append("- 证据判定：%s" % ("通过（格式/数值核对）" if ev.get("ok")
                                      else "不通过/未齐备"))
    else:
        md.append("- 未提供证据快照（--evidence）→ missing_evidence 未判定"
                  "（fail-visible：未验证 ≠ 已齐备）")
    md += ["", "## 五、理由与建议", ""]
    md += ["- " + x for x in rep["rationale"]]
    md.append("")
    md.append("- **%s**" % rep["suggestion"])
    if rep.get("warnings"):
        md += ["", "## 六、Warnings", ""]
        md += ["- " + w for w in rep["warnings"]]
    md += ["", "## 七、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_policy.py --json "
           "--changed-files \"tools/huangzhong_review.py,docs/x.md\"",
           "```",
           "",
           "> 本工具只读评估、默认 observe 不阻断；阻断分级与强制点接入需另行"
           "立项并经老田 L3 批准。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(changed_files=None, commit: str | None = None,
        policy_path: Path | str | None = None, root: Path | str | None = None,
        evidence_path: Path | str | None = None,
        now: dt.datetime | None = None) -> dict:
    """策略即代码评估主流程（只读；返回完整 JSON 结构）。"""
    now = now or _now()
    root = Path(root) if root else ROOT
    warnings: list[str] = []

    pol = load_policy(policy_path, root)
    if not pol["ok"]:
        return {"ok": False, "tool": "huangzhong_policy.py",
                "version": "v1 (2026-10-31)", "generated_at": _iso(now),
                "repo_root": str(root), "read_only": True,
                "error": pol["error"], "exit_code": EXIT_POLICY_ERROR,
                "policy": {"source": pol["source"], "path": pol["path"],
                           "error": pol["error"]},
                "warnings": warnings}
    policy = pol["policy"]
    _log("策略加载：source=%s，规则 %d 条"
         % (pol["source"], len(policy["rules"])))

    resolved = resolve_changed_files(changed_files, commit, root, warnings)
    changed = resolved["files"]
    _log("改动文件 %d 个（来源 %s）" % (len(changed), resolved["source"]))

    commit_message = None
    if commit and any(rule["match"]["risk_keywords"]
                      for rule in policy["rules"]):
        commit_message = _git_commit_message(commit, root, warnings)

    evidence = load_evidence(evidence_path)
    if evidence.get("error"):
        warnings.append("证据快照不可读：%s（fail-visible）" % evidence["error"])

    result = evaluate(changed, policy, evidence, now,
                      commit_message=commit_message)
    warnings.extend(result.pop("warnings", []) or [])

    exit_code = EXIT_OK
    if resolved.get("error") or evidence.get("error"):
        exit_code = EXIT_INCOMPLETE
    rep = {
        "ok": True,
        "tool": "huangzhong_policy.py",
        "version": "v1 (2026-10-31)",
        "generated_at": _iso(now),
        "repo_root": str(root),
        "read_only": True,
        "exit_code": exit_code,
        "policy": {"source": pol["source"], "path": pol["path"],
                   "schema": policy.get("schema"),
                   "version": policy["version"],
                   "default_action": policy["default_action"],
                   "rules_count": len(policy["rules"]),
                   "rules": [r["name"] for r in policy["rules"]]},
        "input": {"source": resolved["source"], "commit": commit or None,
                  "changed_files_count": len(changed),
                  "git": resolved.get("git"),
                  "error": resolved.get("error")},
        "changed_files": changed,
        "evidence_input": evidence,
        "matched_rules": result["matched_rules"],
        "required_evidence": result["required_evidence"],
        "missing_evidence": result["missing_evidence"],
        "unmet_requirements": result["unmet_requirements"],
        "evidence": result["evidence"],
        "requested_action": result["requested_action"],
        "action": result["action"],
        "needs_L3": result["needs_L3"],
        "downgraded": result["downgraded"],
        "rationale": result["rationale"],
        "suggestion": result["suggestion"],
        "warnings": warnings,
        "report_md": None,
    }
    _log("评估完成：匹配 %d 条；action=%s needs_L3=%s exit=%d"
         % (len(rep["matched_rules"]), rep["action"], rep["needs_L3"],
            rep["exit_code"]))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠策略即代码（P1-A；只读，默认 observe 不阻断）")
    ap.add_argument("--json", action="store_true", help="输出完整评估 JSON")
    ap.add_argument("--policy", default="",
                    help="策略 JSON 路径（默认 work/huangzhong/policy.json；"
                         "缺失用内置 default_policy）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--commit", default="",
                    help="取该 commit 的改动文件（git diff --name-only，经 _run）")
    ap.add_argument("--changed-files", default="",
                    help="逗号分隔改动文件列表（优先于 --commit）")
    ap.add_argument("--evidence", default="",
                    help="可选：证据快照 JSON 路径（baseline/redteam/"
                         "rollback_point 等键）")
    ap.add_argument("--md", default="", help="可选：中文 Markdown 报告输出路径")
    args = ap.parse_args(argv)
    rep = run(changed_files=args.changed_files or None,
              commit=args.commit or None,
              policy_path=args.policy or None,
              root=args.repo_root or None,
              evidence_path=args.evidence or None)
    if args.md and rep.get("ok"):
        path = Path(args.md)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render_md(rep), encoding="utf-8")
            rep["report_md"] = str(path)
        except OSError as e:
            rep.setdefault("warnings", []).append(
                "报告落盘失败：%s: %s" % (type(e).__name__, e))
    if args.json or not rep.get("ok"):
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("策略即代码评估：action=%s（requested=%s，needs_L3=%s）；"
              "命中 %d 条规则；必需证据：%s"
              % (rep.get("action"), rep.get("requested_action"),
                 rep.get("needs_L3"), len(rep.get("matched_rules") or []),
                 "、".join(rep.get("required_evidence") or []) or "—"))
    return int(rep.get("exit_code", EXIT_OK))


if __name__ == "__main__":
    raise SystemExit(main())
