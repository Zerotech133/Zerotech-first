# -*- coding: utf-8 -*-
"""黄忠验收语料库与一致性基准 v1（只读；不写 work/）。

用途：把历史验收单导出为可回溯语料（JSONL），并计算一致性/漂移基准：
  ① 语料概览：数量、verdict 分布、legacy(v0) 数、演练数
  ② 同 commit 多单一致性：同 sha 多张验收单 verdict 是否一致（列不一致）
  ③ 按日趋势
  ④ 原因（reasons）关键词频次 Top
  ⑤ 与最新 review 六条快照交叉（review ① 通过数 vs corpus PASS unique 差异）

数据源（只读）：
  · `work/huangzhong/acceptance_*.json`（全部 verdict）
  · `work/huangzhong/drills/drill_ledger_*.json`（演练单识别：文件名/目标 commit 匹配）
  · `work/huangzhong/review_*.json`（最新六条复核快照，用于交叉）

产物（默认 `C:\\项目\\deliverables`）：
  · `<out>/<date>_黄忠验收语料_<date>.jsonl`（一行一条，UTF-8）
  · `<out>/<date>_黄忠语料基准_<date>.md`（中文基准报告）
CLI：`--json` / `--work-dir` / `--out-dir` / `--limit`（导出条数上限，默认 0=全部）/
     `--repo-root` / `--now`（测试复算）/ `--no-write`

边界：只读（不改验收单/台账/配置，不写 `work/`）；唯一写盘 = 语料 JSONL + md 报告；
fail-visible（坏 JSON/坏时间/缺演练台账如实标注，不编造）；不触发验收、不发消息。
退出码：0 = 导出成功（含 0 条/有坏文件均 0）；2 = 参数错误或致命写盘错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_LIMIT = 0                     # 0 = 全部
TOP_TOKENS = 15
MAX_LIST = 20

TOOL_NAME = "huangzhong_corpus.py"
VERSION = "v1 (2026-10-31)"
VERDICTS = ("PASS", "REJECT", "OBSERVE")

_OK, _MISSING, _BAD = "ok", "missing", "bad"


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts is not None else None


def _parse_ts(raw) -> dt.datetime | None:
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=_tz())
    if not isinstance(raw, (str, int, float)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    if isinstance(raw, (int, float)) or text.replace(".", "", 1).isdigit():
        try:
            return dt.datetime.fromtimestamp(float(text), tz=_tz())
        except (OverflowError, OSError, ValueError):
            return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def _fmt_pct(value) -> str:
    return "%.2f%%" % (value * 100) if _num(value) else "—"


def _fmt_hours(value) -> str:
    return "%.1fh" % value if _num(value) else "—"


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return list(rows[:limit])


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _log(msg: str) -> None:
    """进度日志走 stderr（stdout 留给 --json；测试可 monkeypatch 静音）。"""
    print("[corpus] " + str(msg), file=sys.stderr)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _get(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ------------------------------------------------------------------ 数据加载

def _evidence_age_fields(data: dict) -> list[str]:
    """缺失的证据年龄字段（legacy 判定；同 review v1.1 / evidence_lint 口径）。"""
    missing: list[str] = []
    for kind in ("baseline", "redteam"):
        ev = _get(data, "evidence." + kind)
        for field in ("age_hours", "stale"):
            if not isinstance(ev, dict) or field not in ev:
                missing.append("evidence.%s.%s" % (kind, field))
    return missing


def load_drill_index(work: Path) -> dict:
    """演练台账索引：按验收单文件名/目标 commit sha8 匹配演练单。"""
    directory = Path(work) / "drills"
    files = sorted(directory.glob("drill_ledger_*.json")) if directory.is_dir() else []
    index = {"status": _MISSING, "files": [], "entries": 0, "by_file": {},
             "by_sha8": {}, "bad": [], "note": None}
    if not files:
        index["note"] = "drills/drill_ledger_*.json 缺失（演练单无法识别）"
        return index
    index["files"] = [str(p) for p in files]
    for path in files:
        data, err = _read_json(path)
        if err is not None or not isinstance(data, dict):
            index["bad"].append({"file": path.name,
                                 "error": err or "JSON 顶层不是对象"})
            continue
        drills = data.get("drills")
        if not isinstance(drills, list):
            drills = data.get("entries") if isinstance(data.get("entries"), list) else []
        for entry in drills:
            if not isinstance(entry, dict):
                continue
            index["entries"] += 1
            report = entry.get("acceptance_report") or entry.get("file") or ""
            if isinstance(report, str) and report.strip():
                key = Path(report.replace("\\", "/")).name.lower()
                if key:
                    index["by_file"][key] = entry
            target = entry.get("target_commit") or entry.get("sha") or ""
            if isinstance(target, str) and len(target.strip()) >= 8:
                index["by_sha8"][target.strip().lower()[:8]] = entry
    index["status"] = _OK if index["entries"] else _BAD
    if not index["entries"]:
        index["note"] = "演练台账无可解析条目"
    elif index["bad"]:
        index["note"] = "%d 个演练台账文件坏 JSON 已跳过" % len(index["bad"])
    return index


def load_review(work: Path) -> dict:
    """最新 review 快照（用于交叉：review ① 通过数）。"""
    directory = Path(work)
    files = [p for p in directory.glob("review_*.json") if p.is_file()] \
        if directory.is_dir() else []
    row = {"status": _MISSING, "path": None, "generated_at": None,
           "summary": None, "kpi": None, "passed_samples": None,
           "passed_target": None, "note": None}
    if not files:
        row["note"] = "review_*.json 缺失（无法交叉）"
        return row
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    path = files[-1]
    data, err = _read_json(path)
    row["path"] = str(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = _BAD
        row["note"] = "review 报告不可读：%s" % err
        return row
    row["status"] = _OK
    row["generated_at"] = data.get("generated_at")
    row["summary"] = data.get("summary")
    row["kpi"] = data.get("kpi")
    # ① PASS 样本量 measured 形如 "58/10 单"
    for item in data.get("items") or []:
        if isinstance(item, dict) and str(item.get("id")) == "①":
            measured = str(item.get("measured") or "")
            m = re.search(r"(\d+)\s*/\s*(\d+)", measured)
            if m:
                row["passed_samples"] = int(m.group(1))
                row["passed_target"] = int(m.group(2))
            break
    return row


# ------------------------------------------------------------------ 语料扫描

def _record_from(data: dict, path: Path, now: dt.datetime,
                 drill_index: dict) -> dict:
    """单张验收单 → 语料记录。"""
    sha = str(_get(data, "commit.sha") or "").strip()
    sha8 = sha[:8] if len(sha) >= 8 else ""
    gen_raw = data.get("generated_at")
    gen = _parse_ts(gen_raw)
    baseline = _get(data, "evidence.baseline") or {}
    redteam = _get(data, "evidence.redteam") or {}
    if not isinstance(baseline, dict):
        baseline = {}
    if not isinstance(redteam, dict):
        redteam = {}
    filename = path.name
    drill_entry = (drill_index.get("by_file") or {}).get(filename.lower())
    if drill_entry is None and sha8:
        drill_entry = (drill_index.get("by_sha8") or {}).get(sha8.lower())
    drill = bool(drill_entry) or bool(data.get("drill")) \
        or str(data.get("mode") or "") == "drill"
    report_md = path.with_suffix(".md")
    record = {
        "acceptance_id": data.get("acceptance_id") or path.stem,
        "file": filename,
        "verdict": str(data.get("verdict") or "").upper(),
        "commit": sha,
        "sha8": sha8,
        "reasons": list(data.get("reasons") or []) if isinstance(
            data.get("reasons") or [], list) else [],
        "generated_at": gen_raw if gen_raw is not None else None,
        "generated_at_ts": _iso(gen),
        "date": gen.date().isoformat() if gen is not None else None,
        "mode": data.get("mode"),
        "blocking": data.get("blocking"),
        "legacy": bool(_evidence_age_fields(data)),
        "drill": drill,
        "drill_id": (drill_entry or {}).get("drill_id"),
        "evidence": {
            "baseline": {"generated_at": baseline.get("generated_at"),
                         "age_hours": baseline.get("age_hours"),
                         "stale": baseline.get("stale")},
            "redteam": {"generated_at": redteam.get("generated_at"),
                        "age_hours": redteam.get("age_hours"),
                        "stale": redteam.get("stale")},
        },
        "report_md": str(report_md) if report_md.is_file() else None,
        "rollback_point": data.get("rollback_point"),
        "closed_by": data.get("closed_by"),
        "_ts": gen,
        "_path": str(path),
    }
    return record


def scan_corpus(work: Path, now: dt.datetime, drill_index: dict) -> dict:
    """扫 acceptance_*.json → 语料记录（按 generated_at 升序）。"""
    directory = Path(work)
    files = sorted(directory.glob("acceptance_*.json")) if directory.is_dir() else []
    records: list[dict] = []
    bad_files: list[dict] = []
    bad_time: list[dict] = []
    for path in files:
        data, err = _read_json(path)
        if err is not None or not isinstance(data, dict):
            bad_files.append({"file": path.name,
                              "error": err or "JSON 顶层不是对象"})
            continue
        record = _record_from(data, path, now, drill_index)
        if record["_ts"] is None:
            bad_time.append({"file": path.name,
                             "generated_at": record.get("generated_at")})
        records.append(record)
    records.sort(key=lambda r: (r["_ts"] or dt.datetime.min.replace(tzinfo=_tz()),
                                r["file"]))
    return {"records": records, "total_files": len(files),
            "bad_files": bad_files, "bad_time": bad_time}


# ------------------------------------------------------------------ 基准计算

def _public_record(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _reason_tokens(records: list[dict], top: int = TOP_TOKENS) -> list[dict]:
    """reasons 简单分词关键词频次（ASCII 词 + CJK 连续段）。"""
    stop = {"the", "and", "for", "with", "from", "that", "this", "http", "https"}
    counts: dict[str, int] = {}
    for record in records:
        for reason in record.get("reasons") or []:
            text = str(reason or "")
            for token in re.split(r"[^\w\u4e00-\u9fff]+", text):
                token = token.strip().lower()
                if not token or token in stop:
                    continue
                # 丢掉纯数字/含数字片段（时间戳、阈值数字等噪音，如 27t15 / 2026）
                if re.search(r"\d", token):
                    continue
                if len(token) < 2:
                    continue
                counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"token": k, "count": v} for k, v in ordered[:top]]


def compute_benchmark(corpus: dict, drill_index: dict, review: dict) -> dict:
    records = corpus["records"]
    usable = [r for r in records if r.get("verdict") in VERDICTS]
    by_verdict = {v: 0 for v in VERDICTS}
    for record in usable:
        by_verdict[record["verdict"]] += 1
    pass_records = [r for r in usable if r["verdict"] == "PASS"]
    pass_commits = sorted({r["sha8"] for r in pass_records if r.get("sha8")})
    legacy = [r["file"] for r in records if r.get("legacy")]
    drill = [r["file"] for r in records if r.get("drill")]

    # 同 commit 一致性
    groups: dict[str, list[dict]] = {}
    for record in records:
        sha = record.get("commit") or ""
        if not sha:
            continue
        groups.setdefault(sha, []).append(record)
    consistent_groups = 0
    inconsistent: list[dict] = []
    multi_groups = 0
    for sha, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        multi_groups += 1
        votes: dict[str, int] = {}
        for row in rows:
            votes[row["verdict"]] = votes.get(row["verdict"], 0) + 1
        ok = len(votes) == 1
        if ok:
            consistent_groups += 1
        else:
            inconsistent.append({
                "sha": sha, "sha8": sha[:8], "group_size": len(rows),
                "verdicts": votes, "consistent": False,
                "records": [{"file": r["file"], "verdict": r["verdict"],
                             "generated_at": r.get("generated_at")}
                            for r in rows],
            })

    # 按日趋势
    daily: dict[str, dict] = {}
    for record in usable:
        date = record.get("date")
        if not date:
            continue
        bucket = daily.setdefault(date, {"date": date, "PASS": 0, "REJECT": 0,
                                         "OBSERVE": 0, "total": 0})
        bucket[record["verdict"]] += 1
        bucket["total"] += 1
    daily_rows = [daily[k] for k in sorted(daily)]

    # 与 review 交叉
    review_passed = review.get("passed_samples")
    corpus_pass_unique = len(pass_commits)
    cross = {"review_path": review.get("path"),
             "review_generated_at": review.get("generated_at"),
             "review_passed_samples": review_passed,
             "review_passed_target": review.get("passed_target"),
             "corpus_pass_sheets": len(pass_records),
             "corpus_pass_unique_commits": corpus_pass_unique,
             "diff_review_minus_corpus": (review_passed - corpus_pass_unique
                                          if _num(review_passed) else None),
             "status": review.get("status")}
    if _num(review_passed):
        if review_passed == corpus_pass_unique:
            cross["note"] = "review ① 通过数与 corpus PASS unique 一致"
        else:
            cross["note"] = ("review ① 通过数 %d ≠ corpus PASS unique %d"
                             "（口径/时点差异：review 报告可能早于最新验收单，"
                             "或回填单不计入 review 口径；仅作交叉提示，不改判据）"
                             % (review_passed, corpus_pass_unique))
    else:
        cross["note"] = "review ① 通过数不可解析（报告缺失或格式变化）"

    dates = [r["date"] for r in usable if r.get("date")]
    return {
        "total_records": len(records),
        "usable_records": len(usable),
        "by_verdict": by_verdict,
        "legacy_count": len(legacy),
        "legacy_files": _cap(legacy),
        "drill_count": len(drill),
        "drill_files": _cap(drill),
        "pass_unique_commits": corpus_pass_unique,
        "date_span": {"first": dates[0] if dates else None,
                      "last": dates[-1] if dates else None,
                      "days": len(daily_rows)},
        "bad_files": corpus.get("bad_files") or [],
        "bad_time": corpus.get("bad_time") or [],
        "consistency": {"multi_sheet_groups": multi_groups,
                        "consistent_groups": consistent_groups,
                        "inconsistent_count": len(inconsistent),
                        "inconsistent": _cap(inconsistent)},
        "daily": daily_rows,
        "reason_top": _reason_tokens(records),
        "cross": cross,
        "drill_index": {"status": drill_index.get("status"),
                        "files": _cap(drill_index.get("files") or []),
                        "entries": drill_index.get("entries"),
                        "note": drill_index.get("note")},
    }


# ------------------------------------------------------------------ 渲染 / 导出

def _conclusion(stats: dict) -> str:
    by = stats.get("by_verdict") or {}
    parts = ["语料 %d 条（PASS %d / REJECT %d / OBSERVE %d）"
             % (stats.get("total_records") or 0, by.get("PASS", 0),
                by.get("REJECT", 0), by.get("OBSERVE", 0))]
    parts.append("legacy %d、演练 %d" % (stats.get("legacy_count") or 0,
                                        stats.get("drill_count") or 0))
    consistency = stats.get("consistency") or {}
    if consistency.get("multi_sheet_groups"):
        parts.append("同 commit 组 %d（一致 %d / 不一致 %d）"
                     % (consistency.get("multi_sheet_groups"),
                        consistency.get("consistent_groups"),
                        consistency.get("inconsistent_count")))
    cross = stats.get("cross") or {}
    if _num(cross.get("diff_review_minus_corpus")):
        parts.append("review 交叉差 %+d" % cross["diff_review_minus_corpus"])
    bad = len(stats.get("bad_files") or []) + len(stats.get("bad_time") or [])
    if bad:
        parts.append("坏文件/坏时间 %d（已跳过并标注）" % bad)
    return "；".join(parts) + "。"


def render_md(rep: dict) -> str:
    date_text = rep.get("date") or ""
    stats = rep.get("stats") or {}
    by = stats.get("by_verdict") or {}
    consistency = stats.get("consistency") or {}
    cross = stats.get("cross") or {}
    warnings = rep.get("warnings") or []

    lines: list[str] = []
    lines.append("# 黄忠验收语料基准 · %s" % date_text)
    lines.append("")
    lines.append("> 生成：%s（Asia/Shanghai）｜工具：`tools/%s` %s｜工单：`WO-HZ-CORPUS-20261031`"
                 % (rep.get("generated_at") or "—", TOOL_NAME, VERSION))
    lines.append("> 边界：只读（不改验收单/台账/配置，不写 `work/`）；唯一写盘 = 语料 JSONL + 本报告；"
                 "不触发验收、不发消息。")
    lines.append("")
    lines.append("**一句话结论：** %s" % rep.get("conclusion"))
    lines.append("")

    # 一、语料概览
    lines.append("## 一、语料概览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append("| 语料条数 | **%s**（扫描文件 %s，导出 %s） |"
                 % (_fmt_num(stats.get("total_records")),
                    _fmt_num(rep.get("total_files")),
                    _fmt_num(rep.get("exported_rows"))))
    lines.append("| PASS / REJECT / OBSERVE | %s / %s / %s |"
                 % (_fmt_num(by.get("PASS")), _fmt_num(by.get("REJECT")),
                    _fmt_num(by.get("OBSERVE"))))
    lines.append("| legacy(v0) 单 | %s |" % _fmt_num(stats.get("legacy_count")))
    lines.append("| 演练单（台账匹配） | %s |" % _fmt_num(stats.get("drill_count")))
    lines.append("| PASS unique commit | %s |"
                 % _fmt_num(stats.get("pass_unique_commits")))
    span = stats.get("date_span") or {}
    lines.append("| 时间跨度 | %s ~ %s（%s 天有数据） |"
                 % (span.get("first") or "—", span.get("last") or "—",
                    _fmt_num(span.get("days"))))
    lines.append("| 导出上限（--limit） | %s |"
                 % ("全部" if not rep.get("limit") else rep.get("limit")))
    lines.append("")
    if stats.get("legacy_files"):
        lines.append("- legacy 清单（缺证据年龄字段，v0 口径）：%s"
                     % "、".join("`%s`" % f for f in stats["legacy_files"]))
    if stats.get("drill_files"):
        lines.append("- 演练单清单：%s"
                     % "、".join("`%s`" % f for f in stats["drill_files"]))
    lines.append("")

    # 二、同 commit 一致性
    lines.append("## 二、同 commit 多单一致性")
    lines.append("")
    lines.append("- 多单 commit 组：**%s**；verdict 一致：**%s**；不一致：**%s**"
                 % (_fmt_num(consistency.get("multi_sheet_groups")),
                    _fmt_num(consistency.get("consistent_groups")),
                    _fmt_num(consistency.get("inconsistent_count"))))
    lines.append("")
    if consistency.get("inconsistent"):
        lines.append("| commit | 组内单数 | verdict 分布 | 单据（file / verdict / time） |")
        lines.append("|---|---|---|---|")
        for group in consistency["inconsistent"]:
            votes = "、".join("%s=%d" % (k, v) for k, v in
                             sorted((group.get("verdicts") or {}).items()))
            detail = "<br>".join("%s / %s / %s"
                                 % (_cell(r.get("file")), _cell(r.get("verdict")),
                                    _cell(r.get("generated_at")))
                                 for r in _cap(group.get("records") or [], 6))
            lines.append("| `%s` | %s | %s | %s |"
                         % (_cell(group.get("sha8")),
                            _fmt_num(group.get("group_size")),
                            _cell(votes), detail))
    else:
        lines.append("- 无同 commit 不一致（多单组内 verdict 全部一致）。")
    lines.append("")

    # 三、按日趋势
    lines.append("## 三、按日趋势")
    lines.append("")
    daily = stats.get("daily") or []
    if daily:
        lines.append("| 日期 | PASS | REJECT | OBSERVE | 合计 |")
        lines.append("|---|---|---|---|---|")
        for row in _cap(daily, 31):
            lines.append("| %s | %s | %s | %s | %s |"
                         % (row.get("date"), row.get("PASS", 0),
                            row.get("REJECT", 0), row.get("OBSERVE", 0),
                            row.get("total", 0)))
    else:
        lines.append("- 无按日数据（全部验收单缺可解析时间）。")
    lines.append("")

    # 四、原因词频
    lines.append("## 四、原因（reasons）关键词 Top %d" % TOP_TOKENS)
    lines.append("")
    tokens = stats.get("reason_top") or []
    if tokens:
        lines.append("| 关键词 | 次数 |")
        lines.append("|---|---|")
        for row in tokens:
            lines.append("| %s | %s |" % (_cell(row.get("token")),
                                          _fmt_num(row.get("count"))))
    else:
        lines.append("- reasons 为空或无可统计关键词（观察期 PASS 单 reasons 多为空）。")
    lines.append("")

    # 五、review 交叉
    lines.append("## 五、与最新 review 六条快照交叉")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| review 报告 | `%s`（%s） |"
                 % (cross.get("review_path") or "—",
                    cross.get("review_generated_at") or "—"))
    lines.append("| review ① 通过数 / 目标 | %s / %s |"
                 % (_fmt_num(cross.get("review_passed_samples")),
                    _fmt_num(cross.get("review_passed_target"))))
    lines.append("| corpus PASS 单 / unique commit | %s / %s |"
                 % (_fmt_num(cross.get("corpus_pass_sheets")),
                    _fmt_num(cross.get("corpus_pass_unique_commits"))))
    lines.append("| 差（review - corpus unique） | %s |"
                 % (_fmt_num(cross.get("diff_review_minus_corpus"))
                    if _num(cross.get("diff_review_minus_corpus")) else "—"))
    lines.append("")
    lines.append("- 说明：%s" % (cross.get("note") or "—"))
    lines.append("")

    # 六、fail-visible
    lines.append("## 六、数据源与 fail-visible")
    lines.append("")
    if warnings:
        for warn in _cap(warnings, 20):
            lines.append("- %s" % warn)
    else:
        lines.append("- 无告警（扫描文件均可读、时间均可解析、演练台账可读）")
    lines.append("")
    lines.append("- 语料 JSONL：`%s`" % (rep.get("corpus_path") or "（未落盘）"))
    lines.append("- 数据源目录：`%s`" % (rep.get("work_dir") or "—"))
    lines.append("- 演练台账：%s"
                 % ("、".join("`%s`" % f for f in
                             _cap((stats.get("drill_index") or {}).get("files") or [], 5))
                    or "（缺失/无条目）"))
    lines.append("- legacy 判定：缺 `evidence.baseline|redteam` 的 `age_hours`/`stale` 字段"
                 "（同 review v1.1 / evidence_lint 口径）")
    lines.append("")

    # 七、生成信息
    lines.append("## 七、生成信息")
    lines.append("")
    lines.append("- 生成时间：`%s`｜工作目录：`%s`｜输出目录：`%s`"
                 % (rep.get("generated_at") or "—", rep.get("work_dir") or "—",
                    rep.get("out_dir") or "—"))
    lines.append("- 记录字段：acceptance_id / verdict / commit(sha) / reasons / generated_at / "
                 "mode / blocking / evidence(age,stale) / legacy / drill / report_md")
    lines.append("- 只读：不写 `work/`、不改台账/配置；唯一写盘 = 语料 JSONL + 本报告。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("版本：%s ｜ 作者：DSH-Lead · cert-v2 ｜ 工单：WO-HZ-CORPUS-20261031 ｜ "
                 "状态：验收语料库与一致性基准（只读）" % date_text)
    lines.append("")
    return "\n".join(lines)


def _write_jsonl(path: Path, records: list[dict]) -> tuple[int, str | None]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for record in records:
                fh.write(json.dumps(_public_record(record), ensure_ascii=False)
                         + "\n")
        return len(records), None
    except OSError as exc:
        return 0, "语料 JSONL 落盘失败：%s: %s" % (type(exc).__name__, exc)


# ------------------------------------------------------------------ 编排

def run(work_dir: Path | str | None = None,
        out_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        limit: int = DEFAULT_LIMIT,
        now: dt.datetime | None = None,
        write_files: bool = True) -> dict:
    """扫描语料、计算基准并导出 JSONL + md（返回 JSON 摘要）。"""
    clock = now or _now()
    repo = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else repo / "work" / "huangzhong"
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    limit = max(0, int(limit or 0))
    date_text = clock.date().isoformat()

    drill_index = load_drill_index(work)
    corpus = scan_corpus(work, clock, drill_index)
    records = corpus["records"]
    if limit:
        records = records[-limit:]
    review = load_review(work)
    stats = compute_benchmark({"records": records,
                               "bad_files": corpus["bad_files"],
                               "bad_time": corpus["bad_time"]},
                              drill_index, review)

    warnings: list[str] = []
    if drill_index.get("status") == _MISSING:
        warnings.append("演练台账缺失：%s" % (drill_index.get("note") or "无法识别演练单"))
    elif drill_index.get("status") == _BAD:
        warnings.append("演练台账异常：%s" % (drill_index.get("note") or "无条目"))
    for item in corpus["bad_files"]:
        warnings.append("坏验收单已跳过：%s（%s）" % (item.get("file"), item.get("error")))
    for item in corpus["bad_time"]:
        warnings.append("验收单 generated_at 不可解析：%s（%r）"
                        % (item.get("file"), item.get("generated_at")))
    if review.get("status") == _MISSING:
        warnings.append("最新 review 缺失：交叉项不可用")
    elif review.get("status") == _BAD:
        warnings.append("最新 review 不可读：%s" % review.get("note"))

    rep = {
        "ok": True,
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": _iso(clock),
        "date": date_text,
        "repo_root": str(repo),
        "work_dir": str(work),
        "out_dir": str(out),
        "limit": limit,
        "read_only": True,
        "total_files": corpus["total_files"],
        "stats": stats,
        "records": records,
        "review": review,
        "warnings": warnings,
        "corpus_path": None,
        "report_path": None,
        "exported_rows": 0,
        "error": None,
    }
    rep["conclusion"] = _conclusion(stats)

    if write_files:
        out.mkdir(parents=True, exist_ok=True)
        corpus_path = out / ("%s_黄忠验收语料_%s.jsonl" % (date_text, date_text))
        report_path = out / ("%s_黄忠语料基准_%s.md" % (date_text, date_text))
        exported, err = _write_jsonl(corpus_path, records)
        rep["exported_rows"] = exported
        if err is not None:
            rep["ok"] = False
            rep["error"] = err
            _log(err)
        else:
            rep["corpus_path"] = str(corpus_path)
            _log("语料已落盘：%s（%d 条）" % (corpus_path, exported))
        try:
            report_path.write_text(render_md(rep), encoding="utf-8")
            rep["report_path"] = str(report_path)
            _log("基准报告已落盘：%s" % report_path)
        except OSError as exc:
            rep["ok"] = False
            rep["error"] = (rep["error"] or "") + "；基准报告落盘失败：%s" % exc
            _log("基准报告落盘失败：%s" % exc)
    return rep


def _summary(rep: dict) -> dict:
    """stdout JSON 摘要（含语料记录，便于管道消费）。"""
    stats = rep.get("stats") or {}
    return {
        "ok": bool(rep.get("ok")),
        "tool": rep.get("tool"),
        "version": rep.get("version"),
        "generated_at": rep.get("generated_at"),
        "date": rep.get("date"),
        "limit": rep.get("limit"),
        "read_only": rep.get("read_only"),
        "conclusion": rep.get("conclusion"),
        "total_files": rep.get("total_files"),
        "total_records": stats.get("total_records"),
        "by_verdict": stats.get("by_verdict"),
        "legacy_count": stats.get("legacy_count"),
        "drill_count": stats.get("drill_count"),
        "pass_unique_commits": stats.get("pass_unique_commits"),
        "date_span": stats.get("date_span"),
        "consistency": stats.get("consistency"),
        "daily": stats.get("daily"),
        "reason_top": stats.get("reason_top"),
        "cross": stats.get("cross"),
        "drill_index": stats.get("drill_index"),
        "review": {"status": (rep.get("review") or {}).get("status"),
                   "path": (rep.get("review") or {}).get("path"),
                   "generated_at": (rep.get("review") or {}).get("generated_at")},
        "bad_files": stats.get("bad_files"),
        "bad_time": stats.get("bad_time"),
        "warnings": rep.get("warnings") or [],
        "corpus_path": rep.get("corpus_path"),
        "report_path": rep.get("report_path"),
        "exported_rows": rep.get("exported_rows"),
        "records": [_public_record(r) for r in rep.get("records") or []],
        "error": rep.get("error"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠验收语料库导出 + 一致性基准（只读；不写 work/）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要（含语料记录）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--out-dir", default="",
                    help="输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="导出条数上限（默认 0=全部；>0 取最新 N 条）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--now", default="",
                    help="固定生成时钟（ISO；默认当前时间，测试/复算用）")
    ap.add_argument("--no-write", action="store_true",
                    help="只计算并输出，不落 JSONL/md")
    args = ap.parse_args(argv)
    try:
        fixed_now = _parse_ts(args.now) if args.now else None
        if args.now and fixed_now is None:
            raise ValueError("--now 无法解析：%r" % args.now)
        rep = run(work_dir=args.work_dir or None,
                  out_dir=args.out_dir or None,
                  repo_root=args.repo_root or None,
                  limit=args.limit,
                  now=fixed_now,
                  write_files=not args.no_write)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    # 记录列表附到 rep，供 _summary 输出
    summary = _summary(rep)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print("黄忠验收语料基准：%s" % rep.get("conclusion"))
        print("  语料：%s" % (rep.get("corpus_path") or "（--no-write 未落盘）"))
        print("  报告：%s" % (rep.get("report_path") or "（--no-write 未落盘）"))
        for warn in _cap(rep.get("warnings") or [], 10):
            print("  [warn] %s" % warn)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
