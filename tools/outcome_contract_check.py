# -*- coding: utf-8 -*-
"""W3 outcome 契约只读检查器（outcome_contract_check）。

背景（计划 v2 W3 / 11-02 outcome 回写）：
  ``agent_platform/data/shadow/decision_outcomes.jsonl`` 的每行 outcome 必须满足
  W3 证据契约，防止 11-02 回写缺 ``decision_id`` / ``watch_reason`` / ``evidence_refs``
  或伪造 ``hit`` / ``model``。本工具**只读**：不写 outcome、不改 shadow_ledger、
  不碰任务板/审批，不重启任何服务。

用法（默认 Markdown；--json 机器可读；退出码 0=pass / 1=fail / 2=unknown）::

    python -X utf8 tools/outcome_contract_check.py                    # 严格全量（当前账本）
    python -X utf8 tools/outcome_contract_check.py --json
    # 11-02 回写验收：只对 date>=since 的新行执行完整契约，更早行按 legacy 只查
    # 类型/重复/伪造 hit（不要求新增字段）
    python -X utf8 tools/outcome_contract_check.py --json --since 2026-11-02
    python -X utf8 tools/outcome_contract_check.py --path <jsonl> --since 2026-11-02 --out C:\\Temp\\x.md

契约（W3 outcome contract v1，临时口径；见任务 task-15 / W3 闭环审计）：
  必需：``decision_id``（非空，规范形如 ``D1-<yyyymmdd>-<10 hex>``）、
        ``date``（YYYY-MM-DD 或 YYYYMMDD）、``symbol``（非空，建议 6 位 A 股代码）、
        ``direction``（非空方向词；W3 口径待 D1 确认，未知词只告警）。
  可空但类型必须正确：``hit`` ∈ {null, true/false, 0/1}；
        ``model`` ∈ {null, 非空字符串}。
  存在性：``watch_reason``（可为 null，但键必须存在）、``evidence_refs``
        （必须为 list；空 list 视为「无证据」违规）。
  重复：``decision_id`` 重复 → 违规；坏 JSON 行 → 违规。
  「伪造 hit」守卫：``hit`` 非空但方向为 WATCH/HOLD/FLAT/0 → 违规；
        ``hit`` 非空但缺 ``next_day_return``/``next_close`` → 告警（不伪造结论）。
  告警（不判负）：direction 未知取值、decision_id 非标准格式、symbol 非 6 位、
        watch_reason 为 null、hit 缺收益字段。

只读边界（硬约束）：不写 decision_outcomes.jsonl / shadow_ledger.jsonl /
tasks.json / approvals；无网络；不启停服务。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Optional

SCHEMA = "outcome_contract_check/v1"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "agent_platform" / "data" / "shadow" / "decision_outcomes.jsonl"
REQUIRED_FIELDS = ("decision_id", "date", "symbol", "direction")
# 方向词口径（临时）：W3 尚未拍板 direction 的取值域，先按五档 recommendation 扩展；
# 未知词只 warning，不判负，避免把新口径误杀。
DIRECTIONAL_VALUES = {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL",
                      "LONG", "SHORT", "1", "-1", "+1"}
NON_DIRECTIONAL_VALUES = {"WATCH", "HOLD", "FLAT", "NONE", "0"}
KNOWN_DIRECTIONS = DIRECTIONAL_VALUES | NON_DIRECTIONAL_VALUES
DECISION_ID_RE = re.compile(r"^D1-\d{8}-[0-9a-f]{10}$")
DATE_DASH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_COMPACT_RE = re.compile(r"^\d{8}$")
SYMBOL_RE = re.compile(r"^\d{6}$")
MAX_SAMPLES_DEFAULT = 20


# ---------------------------------------------------------------------------
# 基础判定
# ---------------------------------------------------------------------------
def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_date(value) -> Optional[dt.date]:
    """接受 YYYY-MM-DD / YYYYMMDD；非法返回 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if DATE_COMPACT_RE.match(text):
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
        if DATE_DASH_RE.match(text):
            return dt.date.fromisoformat(text)
    except ValueError:
        return None
    return None


def _normalize_direction(value) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.strip().upper()
    return ""


def _hit_type_ok(value) -> bool:
    """hit 可空；非空只允许 bool / 0 / 1（拒绝 2、0.5、"yes" 等伪值）。"""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value in (0, 1)
    return False


def _model_type_ok(value) -> bool:
    """model 可空；非空必须是非空字符串。"""
    if value is None:
        return True
    return _is_nonempty_str(value)


def _watch_reason_type_ok(value) -> bool:
    """watch_reason 可为 null / 字符串 / 非空字符串列表。"""
    if value is None or _is_nonempty_str(value):
        return True
    if isinstance(value, list):
        return bool(value) and all(_is_nonempty_str(item) for item in value)
    return False


def _is_directional(record: dict) -> str:
    """行的有效方向（优先 direction，回退 recommendation）；未知返回原词。"""
    direction = _normalize_direction(record.get("direction"))
    if direction:
        return direction
    return _normalize_direction(record.get("recommendation"))


def _check_row(record: dict, *, strict: bool) -> dict:
    """校验单行；返回 violations / warnings / missing / present / row_values。"""
    violations = []
    warnings = []
    missing = []
    present = {"watch_reason": "watch_reason" in record,
               "evidence_refs": "evidence_refs" in record}
    # 1) 必需字段：strict 行强制；legacy 行（--since 之前）不要求新契约字段，
    #    但存在即仍做类型校验（见 2）。
    if strict:
        for field in REQUIRED_FIELDS:
            value = record.get(field)
            if field == "direction":
                if not _normalize_direction(value):
                    missing.append(field)
                    violations.append("missing_required:" + field)
                continue
            if not _is_nonempty_str(value):
                missing.append(field)
                violations.append("missing_required:" + field)
    # 2) 类型校验
    if _is_nonempty_str(record.get("date")) and _parse_date(record.get("date")) is None:
        violations.append("invalid_type:date")
    if _is_nonempty_str(record.get("symbol")) and not SYMBOL_RE.match(
            record.get("symbol").strip()):
        warnings.append("symbol_format")
    if not _hit_type_ok(record.get("hit")):
        violations.append("invalid_type:hit")
    if not _model_type_ok(record.get("model")):
        violations.append("invalid_type:model")
    direction = _normalize_direction(record.get("direction"))
    if direction and direction not in KNOWN_DIRECTIONS:
        warnings.append("direction_unknown_value")
    if _is_nonempty_str(record.get("decision_id")) and not DECISION_ID_RE.match(
            record.get("decision_id").strip()):
        warnings.append("decision_id_format")
    # 3) watch_reason / evidence_refs：存在性 + 类型
    if present["watch_reason"]:
        if not _watch_reason_type_ok(record.get("watch_reason")):
            violations.append("invalid_type:watch_reason")
        elif record.get("watch_reason") is None:
            warnings.append("watch_reason_null")
    if present["evidence_refs"]:
        refs = record.get("evidence_refs")
        if not isinstance(refs, list):
            violations.append("invalid_type:evidence_refs")
        elif not refs:
            violations.append("evidence_refs_empty")
    if strict:
        for field in ("watch_reason", "evidence_refs"):
            if not present[field]:
                missing.append(field)
                violations.append("missing_field:" + field)
    # 4) 「伪造 hit」守卫
    hit = record.get("hit")
    if hit is not None and _hit_type_ok(hit):
        effective = _is_directional(record)
        if effective in NON_DIRECTIONAL_VALUES:
            violations.append("hit_without_direction:" + effective)
        elif effective and effective not in KNOWN_DIRECTIONS:
            warnings.append("hit_direction_unknown")
        if record.get("next_day_return") is None and record.get("next_close") is None:
            warnings.append("hit_without_return")
    return {"violations": violations, "warnings": warnings,
            "missing": missing, "present": present,
            "decision_id": record.get("decision_id"),
            "date": record.get("date"),
            "symbol": record.get("symbol"),
            "direction": direction,
            "hit": hit,
            "model": record.get("model")}


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def _read_rows(path: Path):
    """返回 (rows, parse_errors, read_error)；rows = [(lineno, dict)]。"""
    rows = []
    parse_errors = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return rows, parse_errors, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            parse_errors.append({"line": lineno, "error": "json: %s" % str(exc)[:80]})
            continue
        if not isinstance(record, dict):
            parse_errors.append({"line": lineno, "error": "not_object"})
            continue
        rows.append((lineno, record))
    return rows, parse_errors, ""


def _violation_kind(code: str) -> str:
    return code.split(":", 1)[0]


def collect(path: Optional[Path] = None, *, since: Optional[str] = None,
            now: Optional[dt.datetime] = None,
            max_samples: int = MAX_SAMPLES_DEFAULT) -> dict:
    """只读校验 decision_outcomes.jsonl 并返回报告（fail-soft：读不到文件 → unknown）。"""
    target = Path(path or DEFAULT_PATH)
    now = now or dt.datetime.now().astimezone()
    report = {
        "schema": SCHEMA,
        "generated_at": now.astimezone().isoformat(timespec="seconds"),
        "path": str(target),
        "since": str(since) if since else None,
        "verdict": "unknown",
        "counts": {"rows": 0, "strict_rows": 0, "legacy_rows": 0,
                   "valid_rows": 0, "invalid_rows": 0, "parse_errors": 0,
                   "duplicate_ids": 0, "hit_non_null": 0,
                   "model_non_null": 0, "hit_and_model": 0,
                   "watch_reason_present": 0, "evidence_refs_present": 0},
        "missing": {field: 0 for field in REQUIRED_FIELDS + ("watch_reason",
                                                             "evidence_refs")},
        "violations": {},
        "warnings": {},
        "samples": [],
        "notes": ["只读检查：不写 outcome/账本、不重启服务、不代回写。"],
    }
    since_date = None
    if since:
        since_date = _parse_date(since)
        if since_date is None:
            report["verdict"] = "unknown"
            report["notes"].append("--since 无法解析（需 YYYY-MM-DD 或 YYYYMMDD），"
                                   "未校验任何行")
            return report
    rows, parse_errors, read_error = _read_rows(target)
    report["counts"]["rows"] = len(rows)
    report["counts"]["parse_errors"] = len(parse_errors)
    if read_error:
        report["verdict"] = "unknown"
        report["notes"].append("账本不可读：%s" % read_error)
        return report
    if not rows and not parse_errors:
        report["verdict"] = "unknown"
        report["notes"].append("账本为空（0 行 JSON），无可校验数据")
        return report

    seen_ids = {}
    all_duplicates = {}
    violation_counts = {}
    warning_counts = {}
    samples = []
    strict_rows = legacy_rows = valid_rows = invalid_rows = 0
    for lineno, record in rows:
        row_date = _parse_date(record.get("date"))
        strict = (since_date is None) or (row_date is not None and row_date >= since_date)
        result = _check_row(record, strict=strict)
        if since_date is not None:
            if strict:
                strict_rows += 1
            else:
                legacy_rows += 1
        else:
            strict_rows += 1
        if result["violations"]:
            invalid_rows += 1
        else:
            valid_rows += 1
        for field in result["missing"]:
            report["missing"][field] = report["missing"].get(field, 0) + 1
        for code in result["violations"]:
            kind = _violation_kind(code)
            violation_counts[kind] = violation_counts.get(kind, 0) + 1
        for code in result["warnings"]:
            warning_counts[code] = warning_counts.get(code, 0) + 1
        decision_id = record.get("decision_id")
        if _is_nonempty_str(decision_id):
            key = decision_id.strip()
            if key in seen_ids:
                all_duplicates.setdefault(key, [seen_ids[key], lineno])
            else:
                seen_ids[key] = lineno
        for field, flag in (("watch_reason", "watch_reason_present"),
                            ("evidence_refs", "evidence_refs_present")):
            if result["present"][field]:
                report["counts"][flag] += 1
        hit = record.get("hit")
        model = record.get("model")
        if hit is not None and _hit_type_ok(hit):
            report["counts"]["hit_non_null"] += 1
        if _model_type_ok(model) and model is not None:
            report["counts"]["model_non_null"] += 1
        if hit is not None and model is not None and _hit_type_ok(hit) and _model_type_ok(model):
            report["counts"]["hit_and_model"] += 1
        if result["violations"] and len(samples) < max(1, int(max_samples)):
            samples.append({"line": lineno, "decision_id": result["decision_id"],
                            "date": result["date"], "symbol": result["symbol"],
                            "violations": result["violations"],
                            "warnings": result["warnings"]})
    report["counts"].update({
        "strict_rows": strict_rows,
        "legacy_rows": legacy_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "duplicate_ids": len(all_duplicates),
    })
    report["violations"] = dict(sorted(violation_counts.items()))
    report["warnings"] = dict(sorted(warning_counts.items()))
    report["samples"] = samples
    if all_duplicates:
        report["duplicate_samples"] = [
            {"decision_id": key, "lines": lines}
            for key, lines in list(all_duplicates.items())[:max(1, int(max_samples))]]
    if parse_errors:
        report["parse_error_samples"] = parse_errors[:max(1, int(max_samples))]
    hard = (bool(parse_errors) or bool(all_duplicates)
            or any(report["violations"].values()))
    if hard:
        report["verdict"] = "fail"
        return report
    if since_date is not None and strict_rows == 0:
        report["verdict"] = "unknown"
        report["notes"].append("无 date>=%s 的行可校验（回写尚未发生？）" % since)
        return report
    report["verdict"] = "pass"
    return report


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
_ISSUE_HINTS = {
    "missing_required": "必需字段缺失（decision_id/date/symbol/direction）",
    "missing_field": "W3 新增字段缺失（watch_reason/evidence_refs；--since 前的 legacy 行不要求）",
    "invalid_type": "字段类型错误（hit/model/date/watch_reason/evidence_refs）",
    "evidence_refs_empty": "evidence_refs 为空列表（视为无证据）",
    "hit_without_direction": "非方向裁决却带 hit（疑似伪造 hit）",
    "duplicate_decision_id": "decision_id 重复",
}


def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(report: dict) -> str:
    counts = report.get("counts") or {}
    lines = [
        "# W3 outcome 契约检查 · 只读",
        "",
        "> verdict: **%s** · path `%s` · generated_at %s"
        % (_STATUS_LABEL.get(str(report.get("verdict")), "UNKNOWN"),
           report.get("path") or "-", report.get("generated_at") or "-"),
        "> since=%s · 行数 %s（strict %s / legacy %s）· valid %s / invalid %s · "
        "parse_errors %s · duplicate_ids %s"
        % (report.get("since") or "-", counts.get("rows"), counts.get("strict_rows"),
           counts.get("legacy_rows"), counts.get("valid_rows"),
           counts.get("invalid_rows"), counts.get("parse_errors"),
           counts.get("duplicate_ids")),
        "> 只读：不写 outcome/账本、不重启服务、不代回写。",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| hit 非空 | %s |" % counts.get("hit_non_null"),
        "| model 非空 | %s |" % counts.get("model_non_null"),
        "| **hit+model 同非空** | **%s** |" % counts.get("hit_and_model"),
        "| watch_reason 存在 | %s |" % counts.get("watch_reason_present"),
        "| evidence_refs 存在 | %s |" % counts.get("evidence_refs_present"),
        "",
        "## 违规分类",
        "",
    ]
    violations = report.get("violations") or {}
    if violations:
        lines.extend(["| 违规 | 次数 | 说明 |", "|---|---|---|"])
        for kind, number in violations.items():
            lines.append("| %s | %s | %s |"
                         % (_md_cell(kind), number, _md_cell(_ISSUE_HINTS.get(kind, "-"))))
    else:
        lines.append("无违规。")
    missing = {k: v for k, v in (report.get("missing") or {}).items() if v}
    if missing:
        lines.extend(["", "## 缺字段计数", "", "| 字段 | 缺失行数 |", "|---|---|"])
        for field, number in missing.items():
            lines.append("| %s | %s |" % (_md_cell(field), number))
    warnings = report.get("warnings") or {}
    if warnings:
        lines.extend(["", "## 告警（不判负）", "", "| 告警 | 次数 |", "|---|---|"])
        for name, number in warnings.items():
            lines.append("| %s | %s |" % (_md_cell(name), number))
    samples = report.get("samples") or []
    if samples:
        lines.extend(["", "## 违规样例（前 %d 行）" % len(samples), ""])
        for sample in samples:
            lines.append("- line %s · decision_id=%s · date=%s · symbol=%s：%s"
                         % (sample.get("line"), sample.get("decision_id") or "-",
                            sample.get("date") or "-", sample.get("symbol") or "-",
                            _md_cell("；".join(sample.get("violations") or []))))
    parse_sample = report.get("parse_error_samples") or []
    if parse_sample:
        lines.extend(["", "## 坏行样例", ""])
        for item in parse_sample:
            lines.append("- line %s：%s" % (item.get("line"), _md_cell(item.get("error"))))
    lines.extend(["", "## 未决口径", "",
                  "- **direction 取值/来源**：W3 未拍板；当前 outcome writer 只写 "
                  "`recommendation`，direction 为新增契约字段（未知词只告警）。",
                  "- **watch_reason/evidence_refs**：`agent_platform/decision_outcomes.py` "
                  "当前输出字段无这两项；11-02 回写前需 D1 补写，否则 strict 判负。",
                  "- **legacy 行**：默认全量严格（当前 65 行 legacy 会判 fail）；"
                  "11-02 验收用 `--since 2026-11-02` 只校验新行，legacy 仅做类型/重复/"
                  "伪造 hit 检查。",
                  "",
                  "## 只读声明", "",
                  "- 只读 JSONL；不写 decision_outcomes/shadow_ledger/tasks/approvals；"
                  "无网络；不启停服务。"])
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="W3 outcome 契约只读检查器（不写账本/不重启服务）")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    fmt.add_argument("--markdown", action="store_true", help="输出 Markdown（默认）")
    parser.add_argument("--path", default="",
                        help="decision_outcomes.jsonl 路径（默认仓库 sidecar）")
    parser.add_argument("--since", default="",
                        help="W3 严格起点（YYYY-MM-DD/YYYYMMDD）：只对 >= 的行执行完整契约")
    parser.add_argument("--max-samples", type=int, default=MAX_SAMPLES_DEFAULT,
                        help="违规/坏行样例上限（默认 %s）" % MAX_SAMPLES_DEFAULT)
    parser.add_argument("--out", default="",
                        help="可选：把报告写入此文件（只写该文件，不写账本）")
    args = parser.parse_args(argv)
    report = collect(path=Path(args.path) if args.path else None,
                     since=args.since or None,
                     max_samples=args.max_samples)
    text = (json.dumps(report, ensure_ascii=False, indent=1)
            if args.json else render_markdown(report))
    print(text)
    if args.out:
        try:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + ("\n" if not text.endswith("\n") else ""),
                              encoding="utf-8")
        except OSError as exc:
            print("[warn] --out 写入失败（不影响判定）：%s" % exc, file=sys.stderr)
    verdict = str(report.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
