# -*- coding: utf-8 -*-
"""黄忠 2026-11-04 Go/No-Go 决策工具（只读）。

用途：把「六条复核报告 + 周 KPI + 唯一 commit 覆盖率 + 事故台账」合成为
11-04 观察期复核会的 **Go/No-Go 决策草案**（JSON + 中文 Markdown 草案）。
不写仓库主文件、不触发验收、不改验收单/证据，只落 md 草案到 --draft-dir。

输入（默认 <repo-root>/work/huangzhong/）：
  · review_*.json        —— 最新一份六条复核报告（**必选**；缺失即报错，退出码 3）。
                            取 items（六条状态/实测）、summary、kpi（runs/merge_done/覆盖率）。
  · acceptance_*.json    —— 唯一 commit 覆盖率：verdict=PASS 按 commit.sha 去重，
                            排除 drills/drill_ledger_*.json 列出的演练单。
  · incidents.jsonl      —— 可选事故台账；缺失视为「无已知事故」并在输出/MD 标注。
输出：
  · --json               —— 打印完整决策结构；
  · <draft-dir>/2026-11-04_黄忠GoNoGo决策草案_<ts>.md —— 中文决策草案（默认
                            C:\\项目\\deliverables；**不写 work/**）。

决策规则（照 11-04 复核口径，不放水）：
  · 六条全 ✓ 且 kpi.coverage_rate ≥ 10%  → GO（接入「观察不阻断」，需老田 L3 单独批准）；
  · 六条全 ✓ 但覆盖率 < 10%              → 条件化 GO（列覆盖率缺口与补强条件）；
  · 其余                                  → NO-GO（列缺项）；④ tracking 时明确
                                            「仅时间项待满窗」并给出满窗/跟踪样本数。

边界：纯只读；fail-visible（缺文件/坏行/结构异常不编造数据，记 warnings 或报错）。
本工具输出为**草案**，不是最终回执，也不构成 Go 决定；最终由老田 11-04 人工签署。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_DRAFT_DIR = Path(r"C:\项目\deliverables")
COVERAGE_THRESHOLD = 0.10          # 覆盖率硬阈值（11-04 Go 门槛）
REVIEW_STALE_HOURS = 24.0          # review 报告超过 24h 建议 11-04 现场重跑
OK_STATUSES = {"✓"}
EXIT_CODES = {"GO": 0, "CONDITIONAL_GO": 2, "NO_GO": 1}
MAX_LIST = 50


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区 aware）；测试可注入 now。"""
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


def _read_jsonl(path: Path) -> dict:
    text, err = _read_text(path)
    if text is None:
        return {"path": str(path), "exists": Path(path).exists() and err != "missing",
                "rows": [], "bad_lines": 0, "error": err}
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return {"path": str(path), "exists": True, "rows": rows,
            "bad_lines": bad, "error": None}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return rows[:limit]


def _match_sha(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if len(a) < 8 or len(b) < 8:
        return a == b
    return a[:8] == b[:8]


def _status_ok(status) -> bool:
    """六条状态是否 ✓（容忍 OK/PASS 别名，不把 pending/tracking 当通过）。"""
    return isinstance(status, str) and status.strip() in OK_STATUSES


def _status_is_tracking(status) -> bool:
    return isinstance(status, str) and "tracking" in status.strip().lower()


# ---------------------------------------------------------- 输入：最新复核报告

def load_latest_review(work_dir: Path) -> dict:
    """读最新 review_*.json（按文件 mtime 倒序，取第一份可解析且结构完整的）。"""
    work_dir = Path(work_dir)
    files = sorted(work_dir.glob("review_*.json"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True)
    warnings, tried = [], []
    for p in files:
        data, err = _read_json(p)
        if err or not isinstance(data, dict):
            warnings.append("复核报告不可读已跳过：%s（%s）" % (p.name, err or "顶层非对象"))
            tried.append(p.name)
            continue
        if not isinstance(data.get("items"), list) or not isinstance(data.get("kpi"), dict):
            warnings.append("复核报告结构不完整已跳过：%s（缺 items/kpi）" % p.name)
            tried.append(p.name)
            continue
        return {"path": str(p), "data": data, "candidates": len(files),
                "warnings": warnings, "skipped": tried, "error": None}
    if not files:
        return {"path": None, "data": None, "candidates": 0, "warnings": warnings,
                "skipped": tried,
                "error": "未找到六条复核报告（%s/review_*.json 缺失）；"
                         "请先运行 tools/huangzhong_review.py --json" % work_dir}
    return {"path": None, "data": None, "candidates": len(files), "warnings": warnings,
            "skipped": tried,
            "error": "复核报告全部不可读/结构不完整（候选 %d 份：%s）"
                     % (len(files), "、".join(tried[:5]))}


def normalize_review(review_data: dict, warnings: list[str]) -> dict:
    """归一化复核报告：items（六条）+ summary + kpi，并做一致性 fail-visible 检查。"""
    raw_items = review_data.get("items") or []
    items = []
    for it in raw_items:
        if not isinstance(it, dict):
            warnings.append("复核报告 items 中存在非对象条目，已跳过")
            continue
        items.append({
            "id": str(it.get("id") or ""),
            "title": it.get("title") or "",
            "status": it.get("status") if isinstance(it.get("status"), str) else "",
            "measured": it.get("measured") or "",
            "target": it.get("target") or "",
            "value": it.get("value") if isinstance(it.get("value"), dict) else {},
            "detail": it.get("detail") if isinstance(it.get("detail"), dict) else {},
        })
    statuses = {i["id"]: i["status"] for i in items}
    passed = sum(1 for i in items if _status_ok(i["status"]))
    all_ok = len(items) == 6 and passed == 6
    if len(items) != 6:
        warnings.append("复核报告条目数为 %d（应为 6），六条全 ✓ 判定不成立" % len(items))
    summary = review_data.get("summary") if isinstance(review_data.get("summary"), dict) else {}
    review_passed = summary.get("passed")
    if isinstance(review_passed, int) and review_passed != passed:
        warnings.append("复核报告 summary.passed=%d 与 items ✓ 数=%d 不一致（以 items 为准）"
                        % (review_passed, passed))
    review_overall = summary.get("overall_pass")
    if isinstance(review_overall, bool) and review_overall != all_ok:
        warnings.append("复核报告 summary.overall_pass=%s 与 items 六条全 ✓=%s 不一致"
                        "（以 items 为准）" % (review_overall, all_ok))
    raw_kpi = review_data.get("kpi") if isinstance(review_data.get("kpi"), dict) else {}
    kpi = {
        "runs": raw_kpi.get("runs"),
        "counts": raw_kpi.get("counts") if isinstance(raw_kpi.get("counts"), dict) else {},
        "merge_done": raw_kpi.get("merge_done"),
        "coverage_rate": raw_kpi.get("coverage_rate"),
        "window_days": raw_kpi.get("window_days") or review_data.get("window_days"),
        "merge_done_available": raw_kpi.get("merge_done_available"),
    }
    if not _num(kpi["coverage_rate"]):
        warnings.append("复核报告 kpi.coverage_rate 缺失/不可读 → 无法满足 Go 覆盖率门槛")
    if not _num(kpi["merge_done"]) or (kpi["merge_done"] or 0) <= 0:
        warnings.append("复核报告 kpi.merge_done 缺失/为 0 → 覆盖率口径不可判定")
    return {"items": items, "statuses": statuses, "passed": passed,
            "total": len(items), "all_ok": all_ok, "summary": summary, "kpi": kpi}


# ------------------------------------------------ 输入：唯一 commit 覆盖率（补充口径）

def load_drill_ids(work_dir: Path, warnings: list[str]) -> dict:
    """读 drills/drill_ledger_*.json（兼容 work 根）→ 演练单文件名/sha8 集合。"""
    work_dir = Path(work_dir)
    paths: list[Path] = []
    for d in (work_dir / "drills", work_dir):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("drill_ledger_*.json")):
            if all(p.resolve() != q.resolve() for q in paths):
                paths.append(p)
    files, sha8, entries, bad = set(), set(), 0, []
    for p in paths:
        data, err = _read_json(p)
        if err is not None:
            bad.append({"file": p.name, "error": err})
            continue
        rows = data if isinstance(data, list) else None
        if rows is None and isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if rows is None:
            bad.append({"file": p.name, "error": "未找到演练条目数组"})
            continue
        for e in rows:
            if not isinstance(e, dict):
                bad.append({"file": p.name, "error": "条目不是对象"})
                continue
            entries += 1
            f = (e.get("acceptance_report") or e.get("file") or e.get("path")
                 or e.get("acceptance_file") or e.get("report"))
            if isinstance(f, str) and f.strip():
                files.add(Path(f).name.lower())
            commit = e.get("commit") if isinstance(e.get("commit"), dict) else {}
            sha = e.get("target_commit") or e.get("sha") or e.get("commit_sha") \
                or commit.get("sha") or (e.get("commit") if isinstance(
                    e.get("commit"), str) else "")
            if isinstance(sha, str) and len(sha.strip()) >= 8:
                sha8.add(sha.strip().lower()[:8])
    for row in bad:
        warnings.append("drill 台账异常：%s（%s）" % (row["file"], row["error"]))
    return {"files": [str(p) for p in paths], "entries": entries,
            "files_lower": files, "sha8": sha8, "bad": bad}


def compute_unique_coverage(work_dir: Path, merge_done, warnings: list[str],
                            drills: dict, since: dt.datetime | None = None) -> dict:
    """唯一 commit 覆盖率：PASS 单按 commit.sha 去重（排除演练单）/ merge_done。

    since = 复核报告 generated_at：用于计算「复核后新增验收单」，解释两边计数时间差。
    """
    work_dir = Path(work_dir)
    sheets, invalid = [], []
    for p in sorted(work_dir.glob("acceptance_*.json")):
        data, err = _read_json(p)
        if err or not isinstance(data, dict):
            invalid.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        sheets.append({
            "file": p.name,
            "verdict": (data.get("verdict") or "").upper()
            if isinstance(data.get("verdict"), str) else "",
            "sha": sha.strip(),
            "generated_at": data.get("generated_at"),
            "drill_flag": bool(data.get("drill")) or data.get("mode") == "drill",
        })
    for row in invalid:
        warnings.append("验收单不可读已跳过：%s（%s）" % (row["file"], row["error"]))
    pass_sheets = [s for s in sheets if s["verdict"] == "PASS"
                   and not s["drill_flag"]
                   and s["file"].lower() not in drills["files_lower"]
                   and not (s["sha"][:8].lower() in drills["sha8"]
                            if len(s["sha"]) >= 8 else False)]
    by_sha, missing_sha = {}, []
    newer_than_since = set()
    for s in pass_sheets:
        if len(s["sha"]) < 8:
            missing_sha.append(s["file"])
            continue
        sha = s["sha"].lower()
        by_sha.setdefault(sha, s)
        gen_sheet = _parse_ts(s.get("generated_at"))
        if since is not None and gen_sheet is not None and gen_sheet > since:
            newer_than_since.add(sha)
    unique_commit = len(by_sha)
    unique_coverage = None
    if _num(merge_done) and merge_done > 0:
        unique_coverage = round(unique_commit / float(merge_done), 4)
    else:
        warnings.append("merge_done 不可用 → 唯一 commit 覆盖率不可判定")
    if missing_sha:
        warnings.append("有 %d 份 PASS 单缺 commit.sha，未计入唯一 commit 覆盖率：%s"
                        % (len(missing_sha), "、".join(missing_sha[:5])))
    return {
        "pass_sheets_raw": len(pass_sheets),
        "unique_commit": unique_commit,
        "unique_coverage": unique_coverage,
        "merge_done": merge_done,
        "duplicate_sheets": len(pass_sheets) - unique_commit - len(missing_sha),
        "newer_than_review": len(newer_than_since),
        "excluded_drills": sorted(
            s["file"] for s in sheets
            if s["file"].lower() in drills["files_lower"]
            or (len(s["sha"]) >= 8 and s["sha"][:8].lower() in drills["sha8"])),
        "missing_sha": missing_sha,
        "invalid_files": invalid,
        "threshold": COVERAGE_THRESHOLD,
        "note": "唯一 commit 覆盖率 = 去重 PASS commit 数 / kpi.merge_done"
                "（排除演练单；抽检回填单仍按单计 commit）。补充口径，用于核对"
                "runs 覆盖率是否被重复验收注水。",
    }


# --------------------------------------------------------------- 输入：事故台账

def load_incidents(work_dir: Path, warnings: list[str]) -> dict:
    path = Path(work_dir) / "incidents.jsonl"
    got = _read_jsonl(path)
    if not got["exists"]:
        note = ("incidents.jsonl 缺失 → 视为无已知事故（需人工确认；"
                "可选台账落 work/huangzhong/incidents.jsonl）")
        return {"path": str(path), "exists": False, "rows": 0, "bad_lines": 0,
                "latest": None, "note": note}
    if got["bad_lines"]:
        warnings.append("incidents.jsonl 坏行 %d 行已跳过" % got["bad_lines"])
    latest = None
    for rec in got["rows"]:
        for key in ("ts", "generated_at", "date", "time", "at"):
            ts = _parse_ts(rec.get(key))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
            break
    return {"path": str(path), "exists": True, "rows": len(got["rows"]),
            "bad_lines": got["bad_lines"], "latest": _iso(latest),
            "note": "事故台账存在：%d 条（只读引用；漏放判定详见复核报告 ④）"
                    % len(got["rows"])}


# ------------------------------------------------------------------- 决策合成

def _tracking_info(items: list[dict]) -> dict:
    """从复核报告 ④ 条目解析满窗/跟踪样本（字段缺失时如实标注）。"""
    it = next((i for i in items if i["id"] == "④"), None)
    if it is None:
        return {"item_id": "④", "found": False, "is_tracking": False,
                "mature_count": None, "tracking_count": None,
                "samples": [], "mature_samples": [], "earliest_window_end": None,
                "leaks": None,
                "note": "复核报告未找到 ④（漏放）条目"}
    detail, value = it["detail"], it["value"]
    is_tracking = _status_is_tracking(it["status"])
    mature = detail.get("mature_count")
    if not _num(mature):
        mature = value.get("mature")
    tracking = detail.get("tracking_count")
    if not _num(tracking):
        tracking = value.get("tracking")
    samples = detail.get("tracking_samples")
    samples = [s for s in samples if isinstance(s, dict)] if isinstance(samples, list) else []
    mature_samples = detail.get("mature_samples")
    mature_samples = [s for s in mature_samples if isinstance(s, dict)] \
        if isinstance(mature_samples, list) else []
    if not _num(tracking) and samples:
        tracking = len(samples)
    earliest = None
    for s in samples:
        ts = _parse_ts(s.get("window_end"))
        if ts is not None and (earliest is None or ts < earliest):
            earliest = ts
    return {
        "item_id": "④", "found": True, "status": it["status"],
        "title": it["title"], "measured": it["measured"],
        "is_tracking": is_tracking,
        "mature_count": mature if _num(mature) else None,
        "tracking_count": tracking if _num(tracking) else None,
        "leaks": value.get("leaks") if _num(value.get("leaks")) else None,
        "samples": _cap(samples),
        "mature_samples": _cap(mature_samples),
        "earliest_window_end": _iso(earliest),
        "note": ("满窗样本 %s 单、跟踪 %s 单%s"
                 % (mature if _num(mature) else "?",
                    tracking if _num(tracking) else "?",
                    "，最早 %s 满窗" % _iso(earliest) if earliest else "")),
    }


def decide(items: list[dict], all_ok: bool, passed: int, kpi: dict,
           coverage: dict, tracking: dict, warnings: list[str]) -> dict:
    """合成 Go/No-Go 决定（照 11-04 门槛，不放水）。"""
    non_ok = [i for i in items if not _status_ok(i["status"])]
    gaps = [{"id": i["id"], "title": i["title"], "status": i["status"],
             "measured": i["measured"]} for i in non_ok]
    rationales: list[str] = []
    conditions: list[str] = []
    kpi_cov = kpi.get("coverage_rate") if _num(kpi.get("coverage_rate")) else None
    unique_cov = coverage.get("unique_coverage")
    only_time_gap = bool(non_ok) and all(_status_is_tracking(i["status"]) for i in non_ok)
    tracking_note = None
    if only_time_gap:
        tracking_note = ("仅时间项待满窗：④ 当前满窗样本 %s 单、跟踪 %s 单%s；"
                         "11-04 满窗后重跑复核（0 事故则 ④ 转 ✓，六条可达 6/6）"
                         % (tracking.get("mature_count")
                            if _num(tracking.get("mature_count")) else "?",
                            tracking.get("tracking_count")
                            if _num(tracking.get("tracking_count")) else "?",
                            "，最早 %s 满窗" % tracking["earliest_window_end"]
                            if tracking.get("earliest_window_end") else ""))
        rationales.append(tracking_note)
    if all_ok and kpi_cov is not None and kpi_cov >= COVERAGE_THRESHOLD:
        code = "GO"
        label = "建议 GO"
        suggestion = ("六条全 ✓ 且覆盖率 %.2f%% ≥ %.0f%%：建议接入「merge 前置·观察不阻断」；"
                      "需老田 L3 单独批准后执行（本草案不构成批准）"
                      % (kpi_cov * 100, COVERAGE_THRESHOLD * 100))
        rationales.insert(0, "六条全 ✓（%d/6），kpi 覆盖率 %.2f%% ≥ %.0f%%"
                          % (passed, kpi_cov * 100, COVERAGE_THRESHOLD * 100))
        if unique_cov is not None and unique_cov < COVERAGE_THRESHOLD:
            conditions.append(
                "补充口径提示：unique-commit 覆盖率 %.2f%% < %.0f%%，建议核对 runs 覆盖率"
                "是否含重复验收注水（不阻断本次 GO，但应在认证材料说明）"
                % (unique_cov * 100, COVERAGE_THRESHOLD * 100))
    elif all_ok and kpi_cov is not None:
        code = "CONDITIONAL_GO"
        label = "建议 条件化 GO"
        cur = "%.2f%%" % (kpi_cov * 100)
        suggestion = ("六条已全 ✓，但覆盖率 %s < %.0f%%：建议「条件化 GO」——先批覆盖率"
                      "提升方案（提频/抽检口径），达标后再接 merge 前置；需老田 L3 单独批准"
                      % (cur, COVERAGE_THRESHOLD * 100))
        rationales.insert(0, "六条全 ✓（%d/6）但覆盖率 %s < %.0f%%"
                          % (passed, cur, COVERAGE_THRESHOLD * 100))
        gaps.append({"id": "COV", "title": "覆盖率不足", "status": "gap",
                     "measured": "%s < %.0f%%" % (cur, COVERAGE_THRESHOLD * 100)})
        conditions.extend([
            "覆盖率提升到 ≥%.0f%%（当前 %s）；可选：提高验收频次（30 分钟/小时）或"
            "抽检口径拍板" % (COVERAGE_THRESHOLD * 100, cur),
            "达标后重跑 tools/huangzhong_review.py + tools/huangzhong_go_no_go.py 复核",
            "老田 L3 单独批准（本草案不构成批准）",
        ])
        if unique_cov is not None and unique_cov < COVERAGE_THRESHOLD:
            conditions.append("unique-commit 覆盖率 %.2f%% 同为不足，需与 runs 口径一并说明"
                              % (unique_cov * 100))
    else:
        code = "NO_GO"
        label = "建议 NO-GO"
        if all_ok:
            # 六条全 ✓ 但覆盖率数据不可判定 → fail-visible，不编造、不放行
            gaps.append({"id": "COV", "title": "覆盖率不可判定", "status": "unknown",
                         "measured": "kpi.coverage_rate 缺失/不可读"})
            rationales.insert(0, "六条全 ✓（6/6）但 kpi.coverage_rate 缺失/不可读 → "
                                 "无法确认覆盖率 ≥%.0f%% 门槛；按 fail-visible 判 NO-GO"
                                 "（不编造数据）" % (COVERAGE_THRESHOLD * 100))
            suggestion = ("六条已全 ✓，但覆盖率不可判定，无法确认 ≥%.0f%% 门槛："
                          "补跑 tools/huangzhong_review.py 生成含 kpi 的复核报告后重跑本草稿"
                          % (COVERAGE_THRESHOLD * 100))
        else:
            suggestion = ("六条未全 ✓（%d/6）：按缺口补证后重跑复核与本草稿；"
                          "本次不接 merge 前置（Go 需老田 L3 单独批准）" % passed)
            rationales.insert(0, "六条仅 %d/6 ✓，未达 GO 前置" % passed)
            for i in non_ok:
                rationales.append("%s %s：%s（%s）" % (i["id"], i["title"], i["status"],
                                                     i["measured"]))
            if kpi_cov is not None and kpi_cov < COVERAGE_THRESHOLD:
                rationales.append("覆盖率 %.2f%% < %.0f%%（即使六条全 ✓ 也只能条件化 GO）"
                                  % (kpi_cov * 100, COVERAGE_THRESHOLD * 100))
    if kpi_cov is None and code != "NO_GO":
        warnings.append("覆盖率不可判定：decision 不应为 %s（fail-visible 兜底）" % code)
    return {"code": code, "label": label, "suggestion": suggestion,
            "rationale": rationales, "gaps": gaps, "conditions": conditions,
            "tracking_note": tracking_note,
            "l3_required": True,
            "rule": "六条全✓且kpi覆盖率≥%.0f%%→GO；六条全✓但覆盖率<%.0f%%→条件化GO；"
                    "否则NO-GO" % (COVERAGE_THRESHOLD * 100, COVERAGE_THRESHOLD * 100)}


# ------------------------------------------------------------------- 报告渲染

def _render_md(rep: dict) -> str:
    d = rep["decision"]
    six = rep["six"]
    cov = rep["coverage"]
    kpi = rep["kpi"]
    tr = rep["tracking"]
    inc = rep["incidents"]
    md = ["# 黄忠 2026-11-04 Go/No-Go 决策草案（自动生成）", "",
          "> **草案性质**：本文件由只读工具 `tools/huangzhong_go_no_go.py` 自动合成，"
          "供 11-04 观察期复核会讨论，**不是最终回执、不构成 Go 决定**；"
          "最终由老田人工签署。",
          "> 生成时间：%s ｜ 数据根：`%s` ｜ 依据复核报告：`%s`"
          % (rep["generated_at"], rep["repo_root"], rep["review"].get("path")),
          "> 复核报告 generated_at：%s（年龄 %s h）"
          % (rep["review"].get("generated_at"),
             rep["review"].get("age_hours") if rep["review"].get("age_hours") is not None
             else "?"),
          "", "## 一、决策建议", "",
          "**%s**" % d["label"], "",
          "- 依据：%s" % d["rule"],
          "- 结论说明：%s" % d["suggestion"],
          "- 六条：**%d/6 ✓**（复核报告 summary.overall_pass=%s）"
          % (six["passed"], rep["review"].get("overall_pass")),
          "- 覆盖率：kpi %.2f%%（runs %s / merge_done %s）；unique-commit %s"
          % ((kpi.get("coverage_rate") or 0) * 100, kpi.get("runs"),
             kpi.get("merge_done"),
             ("%.2f%%（%s/%s）" % (cov["unique_coverage"] * 100,
                                  cov.get("unique_commit"), cov.get("merge_done")))
             if cov.get("unique_coverage") is not None
             else "不可判定（%s 个唯一 commit）" % cov.get("unique_commit")),
          "- L3 提示：**Go/条件化 GO 均需老田 L3 单独批准**（本草案不构成批准）。", ""]
    if d.get("rationale"):
        md += ["**判定依据**", ""] + ["- " + x for x in d["rationale"]] + [""]
    md += ["## 二、六条明细", "",
           "| # | 指标 | 状态 | 实测 | 达标线 |",
           "|---|---|---|---|---|"]
    for it in rep["six_items"]:
        md.append("| %s | %s | %s | %s | %s |"
                  % (it["id"], it["title"], it["status"], it["measured"], it["target"]))
    md += ["", "## 三、缺口与条件", ""]
    if d.get("gaps"):
        md += ["**缺口（按优先级）**", ""]
        for g in d["gaps"]:
            md.append("- %s %s：%s（%s）" % (g["id"], g["title"], g["status"],
                                            g["measured"]))
        md.append("")
    if d.get("conditions"):
        md += ["**条件/补强项**", ""] + ["- " + c for c in d["conditions"]] + [""]
    if not d.get("gaps") and not d.get("conditions"):
        md += ["- 无", ""]
    md += ["## 四、覆盖率（唯一 commit 补充口径）", "",
           "- kpi 覆盖率：**%.2f%%**（%s runs / %s merge_done；窗口 %s 天）"
           % ((kpi.get("coverage_rate") or 0) * 100, kpi.get("runs"),
              kpi.get("merge_done"), kpi.get("window_days")),
           "- 阈值：≥%.0f%%（11-04 Go 门槛）" % (COVERAGE_THRESHOLD * 100),
           "- 唯一 commit：**%s** 个（PASS 单 %s 份，重复单 %s 份，缺 sha %s 份；"
           "排除演练单 %s 份）"
           % (cov.get("unique_commit"), cov.get("pass_sheets_raw"),
              cov.get("duplicate_sheets"), len(cov.get("missing_sha") or []),
              len(cov.get("excluded_drills") or [])),
           "- unique-commit 覆盖率：**%s**"
           % (("%.2f%%" % (cov["unique_coverage"] * 100))
              if cov.get("unique_coverage") is not None else "不可判定"),
           "- 交叉核对：复核报告 ①=%s 单；%s"
           % (cov.get("review_item1_count"),
              (cov.get("cross_check") or {}).get("detail") or "不可判定"),
           "- 口径：%s" % cov.get("note"), ""]
    md += ["## 五、④ 漏放（满窗 / 跟踪）", ""]
    if tr.get("found"):
        md.append("- 状态：**%s**（%s）；%s"
                  % (tr.get("status"), tr.get("title"), tr.get("note")))
        if tr.get("mature_samples"):
            md.append("- 已满窗样本：")
            for s in tr["mature_samples"]:
                md.append("  - %s（sha %s）" % (s.get("file"), s.get("sha8")))
        if tr.get("is_tracking") and tr.get("samples"):
            shown = len(tr["samples"])
            total = tr.get("tracking_count")
            md.append("- 跟踪中样本（满窗日）%s："
                      % ("（共 %s 单，仅列前 %d 条）" % (total, shown)
                         if _num(total) and total > shown else ""))
            for s in tr["samples"]:
                md.append("  - %s（sha %s）：%s 满窗"
                          % (s.get("file"), s.get("sha8"), s.get("window_end")))
        if tr.get("is_tracking"):
            md.append("- 说明：**仅时间项待满窗** —— 满窗样本 %s 单、跟踪 %s 单；"
                      "11-04 满窗后重跑，若无事故 ④ 转 ✓。"
                      % (tr.get("mature_count"), tr.get("tracking_count")))
    else:
        md.append("- 复核报告未找到 ④ 条目（fail-visible，不编造）")
    md += ["", "## 六、事故台账", "",
           "- `%s`：%s" % (inc.get("path"), inc.get("note")),
           "", "## 七、依据路径与时间戳", "",
           "- 复核报告：`%s`（generated_at=%s，窗口 %s 天）"
           % (rep["review"].get("path"), rep["review"].get("generated_at"),
              kpi.get("window_days")),
           "- 验收单目录：`%s`（%d 份 acceptance_*.json）"
           % (rep["work_dir"], cov.get("pass_sheets_raw")),
           "- drill 台账：%s" % ("、".join("`%s`" % x for x in rep["drill_ledger_files"])
                                if rep.get("drill_ledger_files") else "无"),
           "- 事故台账：`%s`（exists=%s）" % (inc.get("path"), inc.get("exists")),
           "- 本草案：`%s`" % rep.get("draft", {}).get("path"), ""]
    md += ["## 八、Warnings（fail-visible，未编造数据）", ""]
    md += ["- " + w for w in rep["warnings"]] if rep.get("warnings") else \
        ["- 无（fail-visible 检查全部通过）"]
    md.append("")
    md += ["## 九、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_review.py --json",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_go_no_go.py --json",
           "```",
           "",
           "> 免责声明：本草案由工具自动合成，数据时点见上；Go/条件化 GO 均需老田 L3 单独批准，"
           "不得以本草案替代人工签署的复核回执。", ""]
    return "\n".join(md)


# --------------------------------------------------------------------- 主流程

def run(work_dir: Path | str | None = None, draft_dir: Path | str | None = None,
        repo_root: Path | str | None = None, now: dt.datetime | None = None,
        write_draft: bool = True) -> dict:
    """合成 Go/No-Go 决策草案；缺失/坏数据 fail-visible（不写 draft、返回 ok=False）。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    drafts = Path(draft_dir) if draft_dir else DEFAULT_DRAFT_DIR
    warnings: list[str] = []

    loaded = load_latest_review(work)
    warnings.extend(loaded["warnings"])
    if loaded["error"]:
        return {"ok": False, "tool": "huangzhong_go_no_go.py",
                "version": "v1 (2026-10-31)",
                "generated_at": _iso(now),
                "repo_root": str(root), "work_dir": str(work),
                "error": loaded["error"], "warnings": warnings,
                "draft": {"path": None, "written": False}}
    review_data = loaded["data"]
    review = normalize_review(review_data, warnings)
    review_meta = {
        "path": loaded["path"],
        "version": review_data.get("version"),
        "generated_at": review_data.get("generated_at"),
        "window_days": review_data.get("window_days"),
        "overall_pass": (review_data.get("summary") or {}).get("overall_pass")
        if isinstance(review_data.get("summary"), dict) else None,
        "candidates": loaded["candidates"],
        "skipped_unreadable": loaded["skipped"],
        "age_hours": None,
    }
    gen = _parse_ts(review_data.get("generated_at"))
    if gen is not None:
        review_meta["age_hours"] = round((now - gen).total_seconds() / 3600.0, 2)
        if review_meta["age_hours"] > REVIEW_STALE_HOURS:
            warnings.append("复核报告生成于 %.1f 小时前（>%.0fh），建议 11-04 现场重跑 "
                            "tools/huangzhong_review.py 后再出 Go/No-Go 草案"
                            % (review_meta["age_hours"], REVIEW_STALE_HOURS))
    else:
        warnings.append("复核报告 generated_at 缺失/不可解析（无法判定数据时点，fail-visible）")

    drills = load_drill_ids(work, warnings)
    coverage = compute_unique_coverage(work, review["kpi"].get("merge_done"),
                                       warnings, drills, since=gen)
    # 与复核报告 ① 计数交叉核对（fail-visible）：复核后新增验收单可解释计数差
    item1 = next((i for i in review["items"] if i["id"] == "①"), None)
    review_unique = item1["value"].get("count") if (
        item1 is not None and isinstance(item1["value"], dict)) else None
    coverage["review_item1_count"] = review_unique if _num(review_unique) else None
    if _num(review_unique):
        delta = coverage["unique_commit"] - review_unique
        newer = coverage.get("newer_than_review") or 0
        if delta == 0:
            coverage["cross_check"] = {"state": "consistent",
                                       "detail": "与复核报告 ① 计数一致"}
        elif delta == newer:
            coverage["cross_check"] = {
                "state": "explained_by_newer",
                "detail": "复核后新增 %d 张 PASS 单，当前 unique 含新增（时间差已解释）"
                          % newer}
        else:
            coverage["cross_check"] = {
                "state": "mismatch",
                "detail": "复核报告 ①=%s，当前 unique=%s，复核后新增=%d"
                          % (review_unique, coverage["unique_commit"], newer)}
            warnings.append(
                "交叉核对不一致：复核报告 ① value.count=%s，本工具按 "
                "acceptance_*.json 去重 unique_commit=%s（复核后新增 %d 张无法完全解释）"
                % (review_unique, coverage["unique_commit"], newer))
    else:
        coverage["cross_check"] = {"state": "unavailable",
                                   "detail": "复核报告 ① 计数不可读"}
    incidents = load_incidents(work, warnings)
    tracking = _tracking_info(review["items"])
    decision = decide(review["items"], review["all_ok"], review["passed"],
                      review["kpi"], coverage, tracking, warnings)

    rep = {
        "ok": True,
        "tool": "huangzhong_go_no_go.py",
        "version": "v1 (2026-10-31)",
        "generated_at": _iso(now),
        "repo_root": str(root),
        "work_dir": str(work),
        "draft_dir": str(drafts),
        "read_only": True,
        "review": review_meta,
        "six_items": [{"id": i["id"], "title": i["title"], "status": i["status"],
                       "measured": i["measured"], "target": i["target"]}
                      for i in review["items"]],
        "six": {"passed": review["passed"], "total": review["total"],
                "all_ok": review["all_ok"],
                "threshold": COVERAGE_THRESHOLD,
                "statuses": review["statuses"]},
        "kpi": review["kpi"],
        "coverage": coverage,
        "tracking": tracking,
        "incidents": incidents,
        "drill_ledger_files": drills["files"],
        "decision": decision,
        "warnings": warnings,
    }
    rep["draft"] = {"path": None, "written": False}
    if write_draft:
        drafts.mkdir(parents=True, exist_ok=True)
        fn = "2026-11-04_黄忠GoNoGo决策草案_%s.md" % now.strftime("%Y%m%d_%H%M%S")
        dp = drafts / fn
        # 先登记 draft 路径再渲染，保证 md 内「本草案」路径不为空
        rep["draft"] = {"path": str(dp), "written": True}
        dp.write_text(_render_md(rep), encoding="utf-8")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠 11-04 Go/No-Go 决策草案（只读）")
    ap.add_argument("--json", action="store_true", help="输出完整决策 JSON")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--draft-dir", default="",
                    help="md 草案输出目录（默认 C:\\项目\\deliverables）")
    args = ap.parse_args(argv)
    rep = run(work_dir=args.work_dir or None,
              draft_dir=args.draft_dir or None,
              repo_root=args.repo_root or None)
    if args.json or not rep.get("ok"):
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("Go/No-Go 草案：%s（六条 %d/%d ✓，kpi 覆盖率 %s）→ %s"
              % (rep["decision"]["label"], rep["six"]["passed"],
                 rep["six"]["total"],
                 ("%.2f%%" % (rep["kpi"]["coverage_rate"] * 100))
                 if _num(rep["kpi"].get("coverage_rate")) else "?",
                 rep.get("draft", {}).get("path")))
    if not rep.get("ok"):
        return 3
    return EXIT_CODES.get(rep["decision"]["code"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
