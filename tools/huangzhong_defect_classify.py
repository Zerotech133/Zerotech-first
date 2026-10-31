# -*- coding: utf-8 -*-
"""黄忠缺陷分类与周报（taxonomy v1，只读）：REJECT/OBSERVE 原因归类 + 周聚合。

数据源（只读）：
  · `work/huangzhong/acceptance_*.json` —— 验收单（取 verdict∈{REJECT,OBSERVE} 的 reasons）
  · `work/huangzhong/drills/drill_ledger_*.json` —— 演练台账（用于把演练单从自然单中分离；
    缺台账时退化为验收单自带的 drill 标记，并给 warning）

taxonomy v1（确定性关键词映射，**按优先级取首个命中类**，全部命中词留档供审计）：
  evidence_stale    证据超龄/过期/超过上限/年龄不可判定
  evidence_missing  证据缺失/不可读/未生成/无法判定
  baseline_failed   测试基线失败（failed/error/测试红）
  redteam_low       红队未过/通过率低于阈值
  rollback_missing  无回滚点/回滚不可用
  schema_invalid    schema/字段/格式/JSON 解析非法/commit 无法读取
  mode_violation    模式/口径/blocking/越权违反
  unknown           以上关键词均未命中（fail-visible：单列 unknown，不硬塞）

输出：JSON（stdout）+ md 周报（默认 `C:\\项目\\deliverables\\2026-10-31_黄忠缺陷分类周报_<日期>.md`，
可用 `--out-dir` 覆盖）。纯 stdlib、只读数据源、不写 work/、不改 accept/review。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_WORK_DIR = "HUANGZHONG_WORK_DIR"
DEFAULT_DAYS = 7
MAX_DAYS = 3650
DEFAULT_LIMIT = 10

TAXONOMY = (
    "evidence_stale",
    "evidence_missing",
    "baseline_failed",
    "redteam_low",
    "rollback_missing",
    "schema_invalid",
    "mode_violation",
    "unknown",
)
# 优先级顺序即 TAXONOMY 顺序：先命中先归类（越具体的类越靠前）
TAXONOMY_RULES = {
    "evidence_stale": ("超龄", "过期", "stale", "超过上限", "上限", "年龄不可判定"),
    "evidence_missing": ("缺失", "缺少", "缺 ", "缺pass_rate", "未生成", "不存在",
                         "不可读", "无法读取", "无法判定", "missing", "not found"),
    "redteam_low": ("红队", "redteam", "通过率", "pass_rate", "安全通过率"),
    "baseline_failed": ("基线", "baseline", "failed=", "error=", "测试红",
                        "测试失败"),
    "rollback_missing": ("回滚", "rollback", "无回滚点", "revert 不可用"),
    "schema_invalid": ("schema", "字段", "格式", "json", "解析", "非法", "invalid",
                       "commit 无法读取"),
    "mode_violation": ("mode", "模式", "口径", "blocking", "阻断", "违反", "越权"),
}
NON_PASS_VERDICTS = ("REJECT", "OBSERVE")
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_SHA_FILE_RE = re.compile(r"^acceptance_([0-9a-fA-F]{8,40})\.json$")


# --------------------------------------------------------------------- 路径/时间

def _main_repo_root(repo_root: Path) -> Path:
    """工作树（.worktrees/<tid>/）内也能定位主仓：取祖先中名为「老田的AI助手」的目录。"""
    for anc in (repo_root, *repo_root.parents):
        if anc.name == "老田的AI助手":
            return anc
    return repo_root


MAIN_REPO = _main_repo_root(ROOT)
DEFAULT_WORK_DIR = MAIN_REPO / "work" / "huangzhong"
DEFAULT_OUT_DIR = MAIN_REPO.parent / "deliverables"


def resolve_work_dir(work_dir=None) -> Path:
    if work_dir is not None and str(work_dir).strip():
        return Path(work_dir)
    env = str(os.environ.get(ENV_WORK_DIR) or "").strip()
    if env:
        return Path(env)
    return DEFAULT_WORK_DIR


def resolve_out_dir(out_dir=None) -> Path:
    if out_dir is not None and str(out_dir).strip():
        return Path(out_dir)
    return DEFAULT_OUT_DIR


def parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601 / 常见空格格式 / epoch 秒）；失败返回 None（无时区按本机时区）。"""
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
        for fmt in _TS_FORMATS:
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


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str:
    return ts.isoformat(timespec="seconds") if ts is not None else ""


def _read_json(path: Path):
    """读 JSON；返回 (obj|None, err|None)。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "JSON 解析失败: %s" % exc


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "drill", "演练")
    return False


# ------------------------------------------------------------------ taxonomy 归类

def classify_reason(text) -> dict:
    """单条 reason → {category, matched_keywords, text}；确定性、优先级取首个命中类。"""
    raw = text if isinstance(text, str) else str(text)
    low = raw.lower()
    matched = []
    category = "unknown"
    for cat in TAXONOMY:
        keys = [k for k in TAXONOMY_RULES.get(cat, ()) if k.lower() in low]
        if keys:
            category = cat
            matched = keys
            break
    return {"category": category, "matched_keywords": matched,
            "text": raw.strip()[:400]}


def classify_reasons(reasons) -> list[dict]:
    if reasons is None:
        return []
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, (list, tuple)):
        return []
    return [classify_reason(r) for r in reasons]


# --------------------------------------------------------------------- 演练台账

def _ledger_entries(path: Path) -> list[dict]:
    """读一个 drill 台账文件：JSON（dict 内 drills/entries/… 列表，或 list）与 JSONL 两种。"""
    data, err = _read_json(path)
    if err is None:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                val = data.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            return [data]
        return []
    entries = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def load_drill_index(work_dir: Path) -> dict:
    """演练索引：{files_lower, sha8, ledger_files, readable, entries, bad}。

    兼容 review 的口径：台账条目里的验收单文件名 / target_commit / acceptance_report
    任一可定位到验收单即视为演练单。
    """
    work = Path(work_dir)
    paths: list[Path] = []
    for d in (work / "drills", work):
        if d.is_dir():
            for p in sorted(d.glob("drill_ledger_*.json")):
                if all(p.resolve() != q.resolve() for q in paths):
                    paths.append(p)
    files_lower, sha8, entries, bad = set(), set(), [], []
    for p in paths:
        got = _ledger_entries(p)
        if not got:
            bad.append({"file": p.name, "error": "不可读或空台账"})
            continue
        for raw in got:
            f = (raw.get("file") or raw.get("path") or raw.get("acceptance_file")
                 or raw.get("acceptance_report") or raw.get("acceptance")
                 or raw.get("report") or raw.get("report_json") or raw.get("json"))
            fname = Path(str(f)).name if f else ""
            if fname:
                files_lower.add(fname.lower())
            commit = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
            sha = (raw.get("sha") or raw.get("commit_sha") or commit.get("sha")
                   or raw.get("target_commit")
                   or (raw.get("commit") if isinstance(raw.get("commit"), str) else ""))
            sha = str(sha or "").strip()
            if not sha and fname:
                m = _SHA_FILE_RE.match(fname)
                if m:
                    sha = m.group(1)
            if len(sha) >= 8:
                sha8.add(sha[:8].lower())
            entries.append({"file": fname, "sha": sha,
                            "verdict": str(raw.get("verdict") or "").upper()})
    return {"files_lower": files_lower, "sha8": sha8,
            "ledger_files": [str(p) for p in paths],
            "readable": bool(paths) and not bad,
            "entries": entries, "bad": bad}


# --------------------------------------------------------------------- 扫描聚合

def _scan_sheets(work_dir: Path) -> dict:
    """扫 acceptance_*.json → {sheets, files, bad}；坏 json 计数并列出（不中断）。"""
    work = Path(work_dir)
    files = sorted(work.glob("acceptance_*.json"))
    sheets, bad = [], []
    for p in files:
        data, err = _read_json(p)
        if err is not None or not isinstance(data, dict):
            bad.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        verdict = data.get("verdict") if isinstance(data.get("verdict"), str) else ""
        sheets.append({
            "file": p.name,
            "path": str(p),
            "sha": sha.strip(),
            "verdict": verdict.strip().upper(),
            "generated_at": data.get("generated_at"),
            "reasons": data.get("reasons") if isinstance(data.get("reasons"), list) else [],
            "drill_flag": _truthy(data.get("drill")) or data.get("mode") == "drill",
        })
    return {"files": [p.name for p in files], "sheets": sheets, "bad": bad}


def _is_drill(sheet: dict, drill: dict) -> bool:
    if sheet.get("drill_flag"):
        return True
    if (sheet.get("file") or "").lower() in drill["files_lower"]:
        return True
    sha8 = (sheet.get("sha") or "").lower()[:8]
    return bool(sha8) and sha8 in drill["sha8"]


def _empty_bucket() -> dict:
    return {"sheets": 0, "reasons": 0, "verdicts": {"REJECT": 0, "OBSERVE": 0},
            "by_category": {}}


def _add_to_bucket(bucket: dict, sheet: dict, classified: list[dict]) -> None:
    bucket["sheets"] += 1
    bucket["reasons"] += len(classified)
    verdict = sheet.get("verdict")
    if verdict in bucket["verdicts"]:
        bucket["verdicts"][verdict] += 1
    for item in classified:
        cat = item["category"]
        bucket["by_category"][cat] = bucket["by_category"].get(cat, 0) + 1


def _ratios(by_category: dict, total: int) -> dict:
    if total <= 0:
        return {}
    return {cat: round(count / float(total), 4) for cat, count in by_category.items()}


def build_report(work_dir=None, days=DEFAULT_DAYS, now=None, limit=DEFAULT_LIMIT,
                 out_dir=None, write_md=True) -> dict:
    """扫描 + 归类 + 聚合 + 写 md（可选）；返回报告 dict（ok=false 仅当读源/写报告失败）。"""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_days", "detail": "days 须为整数"}
    if days < 1 or days > MAX_DAYS:
        return {"ok": False, "error": "invalid_days",
                "detail": "days 须在 1..%d" % MAX_DAYS}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_limit", "detail": "limit 须为整数"}
    if limit < 1:
        return {"ok": False, "error": "invalid_limit", "detail": "limit 须 >= 1"}
    now = now or now_local()
    window_start = now - dt.timedelta(days=days)
    work = resolve_work_dir(work_dir)
    if not work.is_dir():
        return {"ok": False, "error": "work_dir_unreadable",
                "detail": "目录不存在或不可读：%s" % work}
    scan = _scan_sheets(work)
    drill = load_drill_index(work)
    warnings = []
    if not drill["ledger_files"]:
        warnings.append("drill 台账缺失（work/huangzhong/drills/drill_ledger_*.json）："
                        "演练单仅能靠验收单 drill 标记识别")
    elif not drill["readable"]:
        warnings.append("drill 台账存在但不可读：演练分离可能不完整")

    natural, drill_bucket, combined = _empty_bucket(), _empty_bucket(), _empty_bucket()
    by_day: dict[str, dict] = {}
    by_commit: dict[str, dict] = {}
    samples = []
    bad_ts = []
    considered = 0
    for sheet in scan["sheets"]:
        verdict = sheet.get("verdict") or ""
        if verdict not in NON_PASS_VERDICTS:
            continue
        ts = parse_ts(sheet.get("generated_at"))
        if ts is None:
            bad_ts.append({"file": sheet["file"], "generated_at": sheet.get("generated_at")})
            continue
        if ts < window_start:
            continue
        considered += 1
        classified = classify_reasons(sheet.get("reasons"))
        is_drill = _is_drill(sheet, drill)
        target = drill_bucket if is_drill else natural
        _add_to_bucket(target, sheet, classified)
        _add_to_bucket(combined, sheet, classified)
        day = ts.date().isoformat()
        row = by_day.setdefault(day, {"natural": 0, "drill": 0, "reasons": 0,
                                      "verdicts": {"REJECT": 0, "OBSERVE": 0}})
        row["drill" if is_drill else "natural"] += 1
        row["reasons"] += len(classified)
        if verdict in row["verdicts"]:
            row["verdicts"][verdict] += 1
        key = (sheet.get("sha") or "").lower() or ("file:" + sheet["file"])
        commit_row = by_commit.setdefault(key, {
            "sha": sheet.get("sha") or "", "sha8": (sheet.get("sha") or "")[:8],
            "sheets": 0, "verdicts": {}, "drill": 0, "natural": 0})
        commit_row["sheets"] += 1
        commit_row["verdicts"][verdict] = commit_row["verdicts"].get(verdict, 0) + 1
        commit_row["drill" if is_drill else "natural"] += 1
        samples.append({
            "file": sheet["file"],
            "generated_at": _iso(ts),
            "verdict": verdict,
            "drill": is_drill,
            "sha8": (sheet.get("sha") or "")[:8],
            "reasons_total": len(classified),
            "reasons": classified,
        })

    for bucket in (natural, drill_bucket, combined):
        total = bucket["reasons"]
        bucket["by_category_order"] = [c for c in TAXONOMY if c in bucket["by_category"]]
        bucket["ratios"] = _ratios(bucket["by_category"], total)
    samples.sort(key=lambda s: s["generated_at"], reverse=True)
    samples = samples[:limit]
    trend = []
    cursor = window_start.date()
    end_date = now.date()
    while cursor <= end_date:
        key = cursor.isoformat()
        row = by_day.get(key, {"natural": 0, "drill": 0, "reasons": 0,
                               "verdicts": {"REJECT": 0, "OBSERVE": 0}})
        trend.append({"date": key, "natural": row["natural"], "drill": row["drill"],
                      "reasons": row["reasons"], "verdicts": row["verdicts"]})
        cursor += dt.timedelta(days=1)
    commits = sorted(by_commit.values(),
                     key=lambda r: (-r["sheets"], r["sha8"] or "zzz"))
    report = {
        "ok": True,
        "generated_at": _iso(now),
        "window_days": days,
        "window_start": _iso(window_start),
        "window_end": _iso(now),
        "work_dir": str(work),
        "acceptance_files": len(scan["files"]),
        "bad_files": len(scan["bad"]),
        "bad_file_list": scan["bad"],
        "nonpass_sheets_in_window": considered,
        "bad_generated_at": bad_ts,
        "drill_ledger_files": drill["ledger_files"],
        "drill_ledger_bad": drill["bad"],
        "natural": natural,
        "drill": drill_bucket,
        "combined": combined,
        "by_day": trend,
        "by_commit": commits,
        "recent_samples": samples,
        "taxonomy": list(TAXONOMY),
        "taxonomy_priority_note": "多关键词命中按 TAXONOMY 顺序取首个类；matched_keywords 留全部命中",
        "warnings": warnings,
    }
    if write_md:
        out = resolve_out_dir(out_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
            path = out / ("2026-10-31_黄忠缺陷分类周报_%s.md"
                          % now.strftime("%Y%m%d"))
            path.write_text(render_md(report), encoding="utf-8")
            report["report_md"] = str(path)
        except OSError as exc:
            report.update({"ok": False, "error": "report_write_failed",
                           "detail": "%s: %s" % (type(exc).__name__, exc)})
    return report


# -------------------------------------------------------------------> md 渲染

def _pct(n: int, total: int) -> str:
    return "%.1f%%" % (100.0 * n / total) if total else "-"


def render_md(rep: dict) -> str:
    nat, dll, com = rep["natural"], rep["drill"], rep["combined"]
    lines = [
        "# 黄忠缺陷分类周报（taxonomy v1）",
        "",
        "- 生成时间：%s" % rep["generated_at"],
        "- 窗口：最近 %d 天（%s ～ %s）" % (rep["window_days"],
                                          rep["window_start"], rep["window_end"]),
        "- 数据源（只读）：`%s` + `%s`"
        % (Path(rep["work_dir"]) / "acceptance_*.json",
           Path(rep["work_dir"]) / "drills" / "drill_ledger_*.json"),
        "- 口径：取 verdict ∈ {REJECT, OBSERVE} 的验收单 reasons；**演练/自然分离统计**；"
        "reason 级按 taxonomy v1 关键词归类，未命中记 unknown",
        "",
        "## 一、总览",
        "",
        "| 指标 | 自然 | 演练 | 合计 |",
        "|---|---|---|---|",
        "| 非 PASS 验收单（窗口内） | %d | %d | %d |"
        % (nat["sheets"], dll["sheets"], com["sheets"]),
        "| REJECT | %d | %d | %d |"
        % (nat["verdicts"]["REJECT"], dll["verdicts"]["REJECT"],
           com["verdicts"]["REJECT"]),
        "| OBSERVE | %d | %d | %d |"
        % (nat["verdicts"]["OBSERVE"], dll["verdicts"]["OBSERVE"],
           com["verdicts"]["OBSERVE"]),
        "| 缺陷原因条数 | %d | %d | %d |"
        % (nat["reasons"], dll["reasons"], com["reasons"]),
        "",
        "## 二、按缺陷类别（reason 级，含占比）",
        "",
        "| 缺陷类别 | 自然 | 演练 | 合计 | 占比 |",
        "|---|---|---|---|---|",
    ]
    for cat in TAXONOMY:
        cn = nat["by_category"].get(cat, 0)
        cd = dll["by_category"].get(cat, 0)
        cc = com["by_category"].get(cat, 0)
        if cc == 0 and cat == "unknown":
            continue
        lines.append("| %s | %d | %d | %d | %s |"
                     % (cat, cn, cd, cc, _pct(cc, com["reasons"])))
    if not com["reasons"]:
        lines.append("| （窗口内无缺陷原因） | 0 | 0 | 0 | - |")
    lines += [
        "",
        "## 三、按日趋势",
        "",
        "| 日期 | 自然单 | 演练单 | 原因数 | REJECT | OBSERVE |",
        "|---|---|---|---|---|---|",
    ]
    for row in rep["by_day"]:
        lines.append("| %s | %d | %d | %d | %d | %d |"
                     % (row["date"], row["natural"], row["drill"], row["reasons"],
                        row["verdicts"]["REJECT"], row["verdicts"]["OBSERVE"]))
    lines += [
        "",
        "## 四、按 commit 去重（潜在缺陷集中度）",
        "",
        "| commit sha8 | 非 PASS 单数 | 自然 | 演练 | verdict |",
        "|---|---|---|---|---|",
    ]
    if rep["by_commit"]:
        for row in rep["by_commit"]:
            verdicts = "/".join("%s=%d" % (k, v)
                                for k, v in sorted(row["verdicts"].items()))
            lines.append("| %s | %d | %d | %d | %s |"
                         % (row["sha8"] or "(无 sha)", row["sheets"],
                            row["natural"], row["drill"], verdicts))
    else:
        lines.append("| （无） | 0 | 0 | 0 | - |")
    lines += [
        "",
        "## 五、最近样本（≤%d 条，新→旧）" % len(rep["recent_samples"]),
        "",
    ]
    if rep["recent_samples"]:
        for s in rep["recent_samples"]:
            lines.append("- `%s` %s verdict=%s drill=%s sha8=%s"
                         % (s["generated_at"], s["file"], s["verdict"],
                            s["drill"], s["sha8"] or "-"))
            for item in s["reasons"]:
                lines.append("  - [%s] %s（命中：%s）"
                             % (item["category"], item["text"],
                                "、".join(item["matched_keywords"]) or "-"))
    else:
        lines.append("- （窗口内无 REJECT/OBSERVE 样本）")
    lines += [
        "",
        "## 六、数据质量（fail-visible）",
        "",
        "- 扫描验收单：%d 份；坏 json：%d 份" % (rep["acceptance_files"], rep["bad_files"]),
        "- `generated_at` 不可解析：%d 份" % len(rep["bad_generated_at"]),
        "- drill 台账：%d 份%s"
        % (len(rep["drill_ledger_files"]),
           ("（异常：%d）" % len(rep["drill_ledger_bad"]))
           if rep["drill_ledger_bad"] else ""),
    ]
    if rep["bad_file_list"]:
        for item in rep["bad_file_list"][:10]:
            lines.append("  - 坏文件：`%s`（%s）" % (item["file"], item["error"]))
    for warn in rep["warnings"]:
        lines.append("- 警告：%s" % warn)
    lines += [
        "",
        "## 七、口径与边界",
        "",
        "- taxonomy v1 优先级：%s" % " → ".join(TAXONOMY),
        "- 一条 reason 只归一个主类；多关键词命中按优先级取首个，命中词留档供审计。",
        "- 演练单识别：drill 台账列出的验收单文件名 / commit 前缀，或验收单自带 `drill=true`。",
        "- 本工具只读，不改验收单/复核报告，不写 work/；本报告为周报数据快照。",
        "",
        "---",
        "版本：taxonomy v1｜生成：`tools/huangzhong_defect_classify.py`｜"
        "工单：WO-HZ-DEFECT-20261031",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------ CLI

def _print_json(rep: dict) -> None:
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def _print_human(rep: dict) -> None:
    if not rep.get("ok"):
        print("ok=false error=%s detail=%s"
              % (rep.get("error"), rep.get("detail")))
        return
    com, nat, dll = rep["combined"], rep["natural"], rep["drill"]
    print("缺陷分类周报：窗口 %d 天；非 PASS 单 自然 %d / 演练 %d；原因 %d 条"
          % (rep["window_days"], nat["sheets"], dll["sheets"], com["reasons"]))
    for cat in rep["taxonomy"]:
        count = com["by_category"].get(cat, 0)
        if count:
            print("  %-16s %d（占比 %s）" % (cat, count, _pct(count, com["reasons"])))
    if rep.get("warnings"):
        for warn in rep["warnings"]:
            print("  WARN %s" % warn)
    if rep.get("report_md"):
        print("md 周报：%s" % rep["report_md"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="黄忠缺陷分类与周报（taxonomy v1，只读；REJECT/OBSERVE 原因归类+趋势）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="统计窗口天数（默认 7）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="最近样本条数（默认 10）")
    ap.add_argument("--work-dir", default=None,
                    help="数据源目录（默认 env HUANGZHONG_WORK_DIR 或主仓 work/huangzhong）")
    ap.add_argument("--out-dir", default=None,
                    help="md 周报输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rep = build_report(work_dir=args.work_dir, days=args.days, limit=args.limit,
                       out_dir=args.out_dir)
    if args.json:
        _print_json(rep)
    else:
        _print_human(rep)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
