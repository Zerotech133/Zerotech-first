# -*- coding: utf-8 -*-
"""黄忠 11-04 复核 Preflight 一键检查（只读编排）。

用途：把 11-04 09:30 前置检查自动化成一条命令，输出 preflight JSON（stdout）+
中文 Markdown 报告（默认 C:\\项目\\deliverables\\2026-11-04_黄忠复核preflight_<ts>.md）。

五项检查：
  ① evidence：读 agent_platform/data/test_baseline/latest.json 与最新
     work/p1/redteam/redteam_*.json → generated_at/age_hours/stale（认证门 48h）；
  ② review：子进程跑 tools/huangzhong_review.py --json（cwd=repo），再解析
     work/huangzhong/review_*.json 最新一份 → 六条 status/measured、kpi、报告年龄；
  ③ go_no_go：tools/huangzhong_go_no_go.py 存在才跑（子进程 --json --draft-dir <out>），
     捕获返回码（0 GO / 2 条件化 / 1 NO-GO / 3 数据错误）与 decision；
  ④ maturity：直扫 acceptance_*.json（PASS 非演练），算 now 时 mature/tracking、
     最早 window_end（generated_at+7d）与 11-04 10:00 预计满窗数；
  ⑤ incidents：work/huangzhong/incidents.jsonl 存在→行数/近窗口计数；缺失→warning。

判定合成：
  BLOCKER = 证据超龄/缺失、review 报告缺失/不可读、go_no_go 数据错误；
  WARNING = incidents 缺失、review 报告年龄 >24h、可容忍的坏文件/子进程异常；
  ready = blockers 为空（④ tracking 属 expected，不阻塞）。

边界：只读编排（本工具不写业务数据；review/go_no_go 子进程各自落报告）；
所有外部命令经模块级 `_run(cmd, cwd, timeout)` 可 monkeypatch；所有输入
缺失/坏行一律容错并如实标注（fail-visible，不编造）。

目标日期参数化（HZ-FINAL-DATE-20261104）：默认目标时点 = 2026-11-04 10:00，行为与
v1 完全一致；显式 `--target-date 2026-11-10`（或 ISO 日期时间）把 ④ 满窗判定、
报告标题/文件名切到目标日期，`--now` 可固定当前时钟用于 11-10 复算/测试。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
BASELINE_REL = Path("agent_platform") / "data" / "test_baseline" / "latest.json"
REDTEAM_REL_DIR = Path("work") / "p1" / "redteam"
REPORT_PREFIX = "2026-11-04_黄忠复核preflight"
TZ_CN = dt.timezone(dt.timedelta(hours=8))
TARGET_TIME = dt.datetime(2026, 11, 4, 10, 0, tzinfo=TZ_CN)  # 11-04 复盘会时点（默认目标）
DEFAULT_TARGET_DATE = "2026-11-04"   # 默认目标日期；显式 --target-date 可复算其他日期
TARGET_HOUR = 10                     # 仅给日期时补的默认小时（复盘会 10:00）
EVIDENCE_MAX_AGE_HOURS = 48.0   # 认证口径证据年龄门
REVIEW_STALE_HOURS = 24.0       # review 报告超过 24h 需现场重跑
MATURITY_DAYS = 7               # ④ 满窗窗口
RUN_TIMEOUT = 300.0             # 子进程超时（秒）
GNG_EXIT_MAP = {0: "GO", 1: "NO_GO", 2: "CONDITIONAL_GO", 3: "数据错误"}
OK, FAIL, WARN, SKIP = "✓", "✗", "⚠", "—"
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


def _parse_target_date(raw=None) -> dt.datetime:
    """解析目标日期/时刻（默认 TARGET_TIME = 2026-11-04 10:00）。

    支持：None/空串（默认）、'2026-11-10'（补 TARGET_HOUR 10:00）、ISO 日期时间。
    非法输入抛 ValueError（CLI 捕获后按参数错误 rc=2 处理）。
    """
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=TZ_CN)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return TARGET_TIME
    try:
        if "T" in text or " " in text:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            day = dt.date.fromisoformat(text)
            parsed = dt.datetime(day.year, day.month, day.day,
                                 TARGET_HOUR, 0, tzinfo=TZ_CN)
    except ValueError as exc:
        raise ValueError(
            "--target-date 无法解析：%r（支持 2026-11-10 或 ISO 日期时间）" % text
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ_CN)


def _target_label(target: dt.datetime) -> str:
    """目标日期短标签：默认 '11-04'，显式 11-10 → '11-10'。"""
    return target.strftime("%m-%d")


def _target_label_hm(target: dt.datetime) -> str:
    """目标日期+时刻标签：默认 '11-04 10:00'。"""
    return "%s %s" % (target.strftime("%m-%d"), target.strftime("%H:%M"))


def _target_label_from_iso(raw) -> str:
    """从 rep/maturity 的 target_time（ISO）反推短标签；解析失败回退默认 '11-04'。"""
    target = _parse_ts(raw)
    return _target_label(target if isinstance(target, dt.datetime) else TARGET_TIME)


def _target_hm_from_iso(raw) -> str:
    """从 ISO target_time 反推 'MM-DD HH:MM'；解析失败回退默认 '11-04 10:00'。"""
    target = _parse_ts(raw)
    return _target_label_hm(target if isinstance(target, dt.datetime) else TARGET_TIME)


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
    """读 JSONL（可选文件）：{path, exists, rows, bad_lines, error}；坏行容错。"""
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


def _fmt_h(v) -> str:
    return "%.1f" % float(v) if _num(v) else "?"


def _log(msg: str) -> None:
    """进度日志（stderr；测试可 monkeypatch 静音）。"""
    print("[preflight] " + str(msg), file=sys.stderr)


def _run(cmd: list, cwd: Path | str | None = None,
         timeout: float = RUN_TIMEOUT) -> dict:
    """执行外部命令（模块级；测试可 monkeypatch）。不抛异常，失败如实返回。

    返回 {ok, rc, stdout, stderr, error, cmd, cwd, elapsed_s}；`rc` 是真实返回码，
    `ok` 只表示命令成功启动/未超时，业务返回码由调用方解释（review 正常判非全过
    时返回 1，不算失败）。
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


# --------------------------------------------------------------- ① 证据新鲜度

def _evidence_entry(path: Path, now: dt.datetime, kind: str) -> dict:
    """单份证据新鲜度：generated_at/age_hours/stale（认证门 48h）。"""
    path = Path(path)
    row = {"kind": kind, "path": str(path), "exists": path.exists(),
           "generated_at": None, "age_hours": None, "stale": None,
           "status": SKIP, "error": None, "note": None}
    if not path.exists():
        row.update({"status": FAIL, "error": "missing",
                    "note": "%s 证据文件缺失" % kind})
        return row
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        row.update({"status": FAIL, "error": err or "JSON 顶层不是对象",
                    "note": "%s 证据文件不可读" % kind})
        return row
    raw_ts = data.get("generated_at")
    gen = _parse_ts(raw_ts)
    row["generated_at"] = _iso(gen) if gen is not None else (
        raw_ts if isinstance(raw_ts, (str, int, float)) else None)
    if gen is None:
        row.update({"status": FAIL, "error": "generated_at 缺失/不可解析",
                    "note": "无法判定证据年龄（fail-visible，按缺失处理）"})
    else:
        age = round((now - gen).total_seconds() / 3600.0, 2)
        row["age_hours"] = age
        row["stale"] = age > EVIDENCE_MAX_AGE_HOURS
        row["status"] = FAIL if row["stale"] else OK
        if row["stale"]:
            row["note"] = "%s 证据超龄 %sh > %.0fh" % (kind, _fmt_h(age),
                                                       EVIDENCE_MAX_AGE_HOURS)
    if kind == "redteam":
        row["passed"] = data.get("passed")
        row["total"] = data.get("total")
        row["pass_rate"] = data.get("pass_rate")
    return row


def load_evidence(root: Path, now: dt.datetime) -> dict:
    """① 证据新鲜度：基线 latest.json + 最新红队报告（48h 认证门）。"""
    root = Path(root)
    evidence = {"baseline": _evidence_entry(root / BASELINE_REL, now, "baseline"),
                "redteam": None, "redteam_candidates": 0,
                "redteam_skipped": [], "max_age_hours": EVIDENCE_MAX_AGE_HOURS,
                "status": FAIL, "blockers": [], "warnings": []}
    rt_dir = root / REDTEAM_REL_DIR
    cands = sorted(rt_dir.glob("redteam_*.json"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True) if rt_dir.is_dir() else []
    evidence["redteam_candidates"] = len(cands)
    redteam = None
    for p in cands:
        row = _evidence_entry(p, now, "redteam")
        if row["status"] != SKIP:
            redteam = row
            break
        evidence["redteam_skipped"].append({"file": p.name, "error": row["error"]})
    if redteam is None:
        redteam = {"kind": "redteam", "path": str(rt_dir), "exists": False,
                   "generated_at": None, "age_hours": None, "stale": None,
                   "status": FAIL, "error": "missing",
                   "note": "红队证据缺失/不可读（无可解析的 redteam_*.json）"}
    evidence["redteam"] = redteam
    evidence["status"] = (OK if evidence["baseline"]["status"] == OK
                          and redteam["status"] == OK else FAIL)

    for kind, label in (("baseline", "基线"), ("redteam", "红队")):
        row = evidence[kind]
        if row["status"] == OK:
            continue
        if row.get("stale") is True:
            evidence["blockers"].append(
                "%s证据超龄：%sh > %.0fh（generated_at=%s）"
                % (label, _fmt_h(row["age_hours"]), EVIDENCE_MAX_AGE_HOURS,
                   row.get("generated_at") or "?"))
        else:
            evidence["blockers"].append(
                "%s证据缺失/不可读：%s（%s）"
                % (label, row.get("error") or "?", row.get("note") or "无法判定新鲜度"))
    if evidence["redteam_skipped"]:
        evidence["warnings"].append(
            "红队证据候选中有 %d 份不可读已跳过"
            % len(evidence["redteam_skipped"]))
    return evidence


# ------------------------------------------------------------ ② 六条复核报告

def _load_latest_review(work: Path) -> dict:
    """读最新 review_*.json（按 mtime 倒序，取第一份可解析且结构完整的）。"""
    work = Path(work)
    files = sorted(work.glob("review_*.json"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True)
    skipped = []
    for p in files:
        data, err = _read_json(p)
        if err is not None or not isinstance(data, dict):
            skipped.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        if not isinstance(data.get("items"), list) or not isinstance(data.get("kpi"), dict):
            skipped.append({"file": p.name, "error": "缺 items/kpi 结构"})
            continue
        return {"path": str(p), "file": p.name, "data": data,
                "candidates": len(files), "skipped": _cap(skipped), "error": None}
    if not files:
        return {"path": None, "file": None, "data": None, "candidates": 0,
                "skipped": [], "error": "未找到复核报告（%s/review_*.json 缺失）"
                                        % work}
    return {"path": None, "file": None, "data": None, "candidates": len(files),
            "skipped": _cap(skipped),
            "error": "复核报告全部不可读/结构不完整（候选 %d 份）" % len(files)}


def check_review(root: Path, work: Path, now: dt.datetime) -> dict:
    """② 子进程跑 review 工具 → 解析最新 review_*.json → 六条/kpi/报告年龄。"""
    root, work = Path(root), Path(work)
    tool = root / "tools" / "huangzhong_review.py"
    default_work = root / DEFAULT_WORK_SUBDIR
    cmd = [sys.executable, "-X", "utf8", "tools/huangzhong_review.py", "--json"]
    try:
        custom_work = work.resolve() != default_work.resolve()
    except OSError:
        custom_work = str(work) != str(default_work)
    if custom_work:
        cmd += ["--work-dir", str(work)]
    res = _run(cmd, cwd=root, timeout=RUN_TIMEOUT)
    loaded = _load_latest_review(work)
    row = {"tool": str(tool), "cmd": cmd,
           "subprocess": {k: res.get(k) for k in ("ok", "rc", "error", "elapsed_s")},
           "stdout_tail": (res.get("stdout") or "")[-600:],
           "stderr_tail": (res.get("stderr") or "")[-600:],
           "path": loaded["path"], "file": loaded["file"],
           "candidates": loaded["candidates"], "skipped": loaded["skipped"],
           "items": [], "summary": None, "kpi": None, "report_version": None,
           "generated_at": None, "age_hours": None, "status": SKIP,
           "error": loaded["error"], "note": None,
           "blockers": [], "warnings": []}
    if loaded["data"] is None:
        row.update({"status": FAIL,
                    "note": "复核报告缺失/不可读 → 无法核对六条"})
        row["blockers"].append("复核报告缺失/不可读：%s" % loaded["error"])
        return row
    data = loaded["data"]
    items = []
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        items.append({
            "id": str(it.get("id") or ""),
            "title": it.get("title") or "",
            "status": it.get("status") if isinstance(it.get("status"), str) else "",
            "measured": it.get("measured") or "",
            "target": it.get("target") or "",
        })
    row["items"] = items
    row["summary"] = data.get("summary") if isinstance(data.get("summary"), dict) else None
    row["kpi"] = data.get("kpi")
    row["report_version"] = data.get("version")
    raw_ts = data.get("generated_at")
    gen = _parse_ts(raw_ts)
    row["generated_at"] = _iso(gen) if gen is not None else (
        raw_ts if isinstance(raw_ts, (str, int, float)) else None)
    if gen is not None:
        row["age_hours"] = round((now - gen).total_seconds() / 3600.0, 2)
    row["status"] = OK
    if res.get("ok") is False:
        row["warnings"].append(
            "review 子进程执行失败（%s），已回退读取现有最新复核报告"
            % (res.get("error") or "?"))
    elif res.get("rc") not in (0, 1):
        row["warnings"].append(
            "review 子进程返回码 %s（预期 0/1），结果可能不可信（fail-visible）"
            % res.get("rc"))
    if gen is None:
        row["warnings"].append("复核报告 generated_at 缺失/不可解析（fail-visible）")
    elif row["age_hours"] is not None and row["age_hours"] > REVIEW_STALE_HOURS:
        row["warnings"].append(
            "复核报告生成于 %sh 前（>%.0fh），建议 11-04 现场重跑 "
            "tools/huangzhong_review.py 后再判定"
            % (_fmt_h(row["age_hours"]), REVIEW_STALE_HOURS))
    if loaded["skipped"]:
        row["warnings"].append(
            "已跳过不可读/结构不完整的复核报告：%s"
            % "、".join(s["file"] for s in loaded["skipped"][:5]))
    return row


# --------------------------------------------------------- ③ Go/No-Go 草案

def check_go_no_go(root: Path, work: Path, out: Path, now: dt.datetime) -> dict:
    """③ tools/huangzhong_go_no_go.py 存在才跑；捕获返回码与 decision。"""
    root, work, out = Path(root), Path(work), Path(out)
    tool = root / "tools" / "huangzhong_go_no_go.py"
    default_work = root / DEFAULT_WORK_SUBDIR
    row = {"tool": str(tool), "available": tool.exists(), "cmd": [],
           "subprocess": None, "returncode": None, "exit_map": GNG_EXIT_MAP,
           "decision": None, "six": None, "kpi": None, "draft": None,
           "data_error": False, "status": SKIP, "error": None, "note": None,
           "blockers": [], "warnings": []}
    if not row["available"]:
        row["note"] = ("tools/huangzhong_go_no_go.py 不存在 → 跳过"
                       "（11-04 前补齐后可复跑）")
        row["warnings"].append("Go/No-Go 工具不存在：%s（已跳过，不阻塞 preflight）"
                               % tool)
        return row
    cmd = [sys.executable, "-X", "utf8", "tools/huangzhong_go_no_go.py",
           "--json", "--draft-dir", str(out)]
    try:
        custom_work = work.resolve() != default_work.resolve()
    except OSError:
        custom_work = str(work) != str(default_work)
    if custom_work:
        cmd += ["--work-dir", str(work)]
    res = _run(cmd, cwd=root, timeout=RUN_TIMEOUT)
    row["cmd"] = cmd
    row["returncode"] = res.get("rc")
    row["subprocess"] = {k: res.get(k) for k in ("ok", "rc", "error", "elapsed_s")}
    stdout = res.get("stdout") or ""
    data = None
    parse_err = None
    try:
        data = json.loads(stdout) if stdout.strip() else None
    except ValueError as e:
        parse_err = "stdout 非合法 JSON：%s" % e
    if isinstance(data, dict):
        row["decision"] = data.get("decision") if isinstance(
            data.get("decision"), dict) else None
        row["six"] = data.get("six") if isinstance(data.get("six"), dict) else None
        row["kpi"] = data.get("kpi") if isinstance(data.get("kpi"), dict) else None
        row["draft"] = data.get("draft") if isinstance(data.get("draft"), dict) else None
        if data.get("ok") is False:
            row["data_error"] = True
            row["error"] = "工具自报 ok=false（数据错误）"
    else:
        row["data_error"] = True
        row["error"] = parse_err or "子进程无 JSON 输出"
    rc = row["returncode"]
    if res.get("ok") is False:
        row["data_error"] = True
        row["error"] = "子进程执行失败：%s" % (res.get("error") or "?")
    elif rc == 3:
        row["data_error"] = True
        row["error"] = row["error"] or "返回码 3（数据错误）"
    elif rc is not None and rc not in GNG_EXIT_MAP:
        row["data_error"] = True
        row["error"] = row["error"] or ("返回码 %s 非预期（预期 0/1/2/3）" % rc)
    if row["data_error"]:
        row["status"] = FAIL
        row["blockers"].append("Go/No-Go 数据错误（rc=%s）：%s"
                               % (rc, row["error"]))
        return row
    if row["decision"] is None:
        row["decision"] = {"code": GNG_EXIT_MAP.get(rc, "UNKNOWN"),
                           "label": "（工具未返回 decision 字段，按返回码映射）",
                           "suggestion": ""}
        row["warnings"].append("go_no_go 输出缺 decision 字段，已按返回码映射")
    code = row["decision"].get("code")
    expected = GNG_EXIT_MAP.get(rc)
    if isinstance(code, str) and expected and code != expected:
        row["warnings"].append(
            "go_no_go 返回码 %s 与 decision.code=%s 不一致（fail-visible）"
            % (rc, code))
    row["status"] = OK
    return row


# --------------------------------------------------------------- ④ 满窗 maturity

def _load_drill_ids(work: Path) -> dict:
    """演练单标识（drill 台账文件名/sha8；兼容 work 根与 drills/）。"""
    work = Path(work)
    paths: list[Path] = []
    for d in (work / "drills", work):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("drill_ledger_*.json")):
            if all(p.resolve() != q.resolve() for q in paths):
                paths.append(p)
    files_lower, sha8 = set(), set()
    for p in paths:
        data, err = _read_json(p)
        if err is not None:
            continue
        rows = data if isinstance(data, list) else None
        if rows is None and isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if not isinstance(rows, list):
            continue
        for e in rows:
            if not isinstance(e, dict):
                continue
            f = (e.get("file") or e.get("path") or e.get("acceptance_report")
                 or e.get("acceptance_file") or e.get("report"))
            if isinstance(f, str) and f.strip():
                files_lower.add(Path(f).name.lower())
            commit = e.get("commit") if isinstance(e.get("commit"), dict) else {}
            sha = (e.get("target_commit") or e.get("sha") or e.get("commit_sha")
                   or commit.get("sha")
                   or (e.get("commit") if isinstance(e.get("commit"), str) else ""))
            if isinstance(sha, str) and len(sha.strip()) >= 8:
                sha8.add(sha.strip().lower()[:8])
    return {"files": [str(p) for p in paths],
            "files_lower": files_lower, "sha8": sha8}


def compute_maturity(work: Path, now: dt.datetime,
                     target: dt.datetime | None = None) -> dict:
    """④ 直扫 acceptance_*.json（PASS 非演练）→ mature/tracking/最早满窗/预计。"""
    work = Path(work)
    target = target or TARGET_TIME
    drills = _load_drill_ids(work)
    rows, invalid = [], []
    for p in sorted(work.glob("acceptance_*.json")):
        data, err = _read_json(p)
        if err is not None or not isinstance(data, dict):
            invalid.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        if str(data.get("verdict") or "").upper() != "PASS":
            continue
        if data.get("drill") or data.get("mode") == "drill":
            continue
        if p.name.lower() in drills["files_lower"]:
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        sha = sha.strip()
        if len(sha) >= 8 and sha.lower()[:8] in drills["sha8"]:
            continue
        gen = _parse_ts(data.get("generated_at"))
        if gen is None:
            invalid.append({"file": p.name,
                            "error": "generated_at 缺失/不可解析"})
            continue
        end = gen + dt.timedelta(days=MATURITY_DAYS)
        rows.append({"file": p.name, "sha8": sha[:8],
                     "generated_at": _iso(gen), "window_end": _iso(end),
                     "mature": now >= end, "expected_by_target": end <= target,
                     "_end": end})
    mature = [r for r in rows if r["mature"]]
    tracking = [r for r in rows if not r["mature"]]
    ordered = sorted(rows, key=lambda r: r["_end"])
    expected = [r for r in rows if r["expected_by_target"]]
    next_end = next((r for r in ordered if r["_end"] > now), None)
    first = ordered[0] if ordered else None
    for r in rows:
        r.pop("_end", None)
    sample_keys = ("file", "sha8", "generated_at", "window_end")
    return {
        "pass_samples": len(rows),
        "mature_count": len(mature),
        "tracking_count": len(tracking),
        "mature_at_1030": len(expected),
        "mature_at_1104": len(expected),
        "target_time": _iso(target),
        "window_days": MATURITY_DAYS,
        "earliest_window_end": first["window_end"] if first else None,
        "earliest_mature": mature[0]["window_end"] if mature else None,
        "next_window_end": next_end["window_end"] if next_end else None,
        "expected_mature_by_target": _cap(
            [{k: r[k] for k in sample_keys} for r in expected]),
        "mature_samples": _cap([{k: r[k] for k in sample_keys} for r in mature]),
        "tracking_samples": _cap([{k: r[k] for k in sample_keys}
                                  for r in tracking]),
        "invalid_files": _cap(invalid),
        "source": "acceptance_*.json 直扫（PASS 非演练；不依赖 review 明细 50 条截断）",
        "note": ("now 满窗 %d 单 / 跟踪 %d 单；%s 预计满窗 %d 单%s"
                 % (len(mature), len(tracking),
                    _target_label_hm(target), len(expected),
                    "（0 → ④ 将仍为 tracking，属预期，不阻塞 preflight）"
                    if not expected else "")),
        "status": OK if rows else SKIP,
        "blockers": [],
        "warnings": [],
    }


# --------------------------------------------------------------- ⑤ 事故台账

def load_incidents(work: Path, now: dt.datetime) -> dict:
    """⑤ work/huangzhong/incidents.jsonl：存在→行数/近窗口计数；缺失→warning。"""
    work = Path(work)
    path = work / "incidents.jsonl"
    got = _read_jsonl(path)
    row = {"path": str(path), "exists": got["exists"],
           "rows": len(got["rows"]), "bad_lines": got["bad_lines"],
           "latest": None, "window_days": MATURITY_DAYS, "recent_window": 0,
           "status": SKIP, "note": None, "blockers": [], "warnings": []}
    if not got["exists"]:
        row["status"] = WARN
        row["note"] = ("incidents.jsonl 缺失 → 无法自动核对事故台账；按无已知事故计"
                       "但需人工确认（漏放判定详见复核报告 ④）")
        row["warnings"].append(
            "incidents.jsonl 缺失：需人工确认无已知事故（漏放=0 依赖人工核实）")
        return row
    cutoff = now - dt.timedelta(days=MATURITY_DAYS)
    latest, recent = None, 0
    for rec in got["rows"]:
        ts = None
        for key in ("ts", "generated_at", "date", "time", "at", "timestamp"):
            ts = _parse_ts(rec.get(key))
            if ts is not None:
                break
        if ts is None:
            continue
        if latest is None or ts > latest:
            latest = ts
        if ts >= cutoff:
            recent += 1
    row.update({"latest": _iso(latest), "recent_window": recent, "status": OK,
                "note": "事故台账 %d 条（近 %d 天 %d 条；只读引用，漏放判定见"
                        "复核报告 ④）"
                        % (len(got["rows"]), MATURITY_DAYS, recent)})
    if got["bad_lines"]:
        row["warnings"].append("incidents.jsonl 坏行 %d 行已跳过"
                               % got["bad_lines"])
    return row


# --------------------------------------------------------------- 检查表/MD

def _collect_issues(sections: dict) -> tuple[list, list]:
    blockers, warnings = [], []
    for name in ("evidence", "review", "go_no_go", "maturity", "incidents"):
        sec = sections.get(name) or {}
        for msg in sec.get("blockers") or []:
            blockers.append({"check": name, "message": str(msg)})
        for msg in sec.get("warnings") or []:
            warnings.append({"check": name, "message": str(msg)})
    return blockers, warnings


def _evidence_brief(row: dict) -> str:
    if row.get("status") == OK:
        return "新鲜 %sh（门 ≤%.0fh）" % (_fmt_h(row.get("age_hours")),
                                         EVIDENCE_MAX_AGE_HOURS)
    if row.get("stale") is True:
        return "超龄 %sh > %.0fh" % (_fmt_h(row.get("age_hours")),
                                    EVIDENCE_MAX_AGE_HOURS)
    return "缺失/不可读（%s）" % (row.get("error") or "?")


def _build_checks(sections: dict) -> list:
    ev, rv = sections["evidence"], sections["review"]
    gng, mt, inc = sections["go_no_go"], sections["maturity"], sections["incidents"]
    # ① 证据
    ev_result = "基线：%s；红队：%s" % (_evidence_brief(ev["baseline"]),
                                       _evidence_brief(ev["redteam"]))
    # ② 复核
    if rv.get("items"):
        ok_n = sum(1 for it in rv["items"] if it.get("status") == OK)
        six = "六条 %d/%d ✓" % (ok_n, len(rv["items"]))
        stale = [it for it in rv["items"] if it.get("status") == "tracking"]
        if stale:
            six += "（%s tracking）" % "、".join(it["id"] for it in stale)
        rv_result = "%s；报告 %s（%sh 前）" % (
            six, rv.get("file") or "?", _fmt_h(rv.get("age_hours")))
    else:
        rv_result = "复核报告缺失/不可读（%s）" % (rv.get("error") or "?")
    # ③ Go/No-Go
    if not gng.get("available"):
        gng_result = "工具不存在，已跳过"
    elif gng.get("data_error"):
        gng_result = "数据错误（rc=%s）：%s" % (gng.get("returncode"),
                                               gng.get("error") or "?")
    else:
        dec = gng.get("decision") or {}
        gng_result = "rc=%s → %s（%s）" % (
            gng.get("returncode"), dec.get("code") or "?",
            dec.get("label") or "")
    # ④ 满窗
    mt_result = ("now 满窗 %d / 跟踪 %d；最早满窗 %s；%s 预计满窗 %d"
                 % (mt["mature_count"], mt["tracking_count"],
                    mt.get("earliest_window_end") or "—",
                    _target_hm_from_iso(mt.get("target_time")),
                    mt.get("mature_at_1030", 0)))
    # ⑤ 事故
    if inc.get("exists"):
        inc_result = ("存在 %d 条（近 %d 天 %d 条；latest=%s）"
                      % (inc["rows"], inc.get("window_days"),
                         inc.get("recent_window"), inc.get("latest") or "—"))
    else:
        inc_result = "incidents.jsonl 缺失（需人工确认无已知事故）"
    return [
        {"id": "1", "key": "evidence", "name": "证据新鲜度（48h 认证门）",
         "source": "agent_platform/data/test_baseline/latest.json；"
                   "work/p1/redteam/redteam_*.json（最新）",
         "result": ev_result, "status": ev.get("status", SKIP)},
        {"id": "2", "key": "review", "name": "六条复核报告（review）",
         "source": "python -X utf8 tools/huangzhong_review.py --json → "
                   "work/huangzhong/review_*.json（最新）",
         "result": rv_result, "status": rv.get("status", SKIP)},
        {"id": "3", "key": "go_no_go", "name": "Go/No-Go 决策草案",
         "source": "python -X utf8 tools/huangzhong_go_no_go.py --json "
                   "--draft-dir <out>（存在才跑）",
         "result": gng_result, "status": gng.get("status", SKIP)},
        {"id": "4", "key": "maturity", "name": "④ 满窗 maturity",
         "source": "acceptance_*.json 直扫（PASS 非演练；generated_at+7d）",
         "result": mt_result, "status": mt.get("status", SKIP)},
        {"id": "5", "key": "incidents", "name": "事故台账（incidents）",
         "source": "work/huangzhong/incidents.jsonl",
         "result": inc_result, "status": inc.get("status", SKIP)},
    ]


def _render_md(rep: dict) -> str:
    target = _parse_ts(rep.get("target_time"))
    target = target if isinstance(target, dt.datetime) else TARGET_TIME
    tlabel = _target_label(target)
    md = ["# 黄忠 %s 复核 Preflight 报告" % tlabel, "",
          "- 生成时间：%s（Asia/Shanghai）" % rep["generated_at"],
          "- 目标时点：%s（%s 复盘会 %s）"
          % (rep["target_time"], tlabel, target.strftime("%H:%M")),
          "- 仓库根：`%s`；数据目录：`%s`" % (rep["repo_root"], rep["work_dir"]),
          "- 只读编排：本工具不写业务数据；review/go_no_go 子进程各自落报告",
          "- **结论：ready = %s｜blockers = %d｜warnings = %d**"
          % (rep["ready"], len(rep["blockers"]), len(rep["warnings"])),
          "", "## 一、检查表", "",
          "| # | 检查项 | 命令/来源 | 结果 | 状态 |",
          "|---|---|---|---|---|"]
    for c in rep["checks"]:
        md.append("| %s | %s | %s | %s | %s |"
                  % (c["id"], c["name"], c["source"], c["result"], c["status"]))
    md += ["", "## 二、Blockers（ready=False 原因）", ""]
    if rep["blockers"]:
        for b in rep["blockers"]:
            md.append("- [%s] %s" % (b["check"], b["message"]))
    else:
        md.append("- 无")
    md += ["", "## 三、Warnings", ""]
    if rep["warnings"]:
        for w in rep["warnings"]:
            md.append("- [%s] %s" % (w["check"], w["message"]))
    else:
        md.append("- 无")

    rv = rep["review"]
    md += ["", "## 四、六条复核（review 最新报告）", ""]
    if rv.get("items"):
        summary = rv.get("summary") or {}
        sub = rv.get("subprocess") or {}
        md.append("- 报告：`%s`（生成于 %s；子进程 rc=%s）"
                  % (rv.get("path") or "?", rv.get("generated_at") or "?",
                     sub.get("rc")))
        md.append("- 综合：overall_pass = %s（%s/%s 条 ✓）"
                  % (summary.get("overall_pass", "?"),
                     summary.get("passed", "?"), summary.get("total", "?")))
        md += ["", "| 条目 | 状态 | 实测 | 达标线 |", "|---|---|---|---|"]
        for it in rv["items"]:
            md.append("| %s %s | %s | %s | %s |"
                      % (it["id"], it["title"], it["status"],
                         it["measured"], it["target"]))
        kpi = rv.get("kpi") or {}
        cov = kpi.get("coverage_rate")
        md += ["", "- KPI：窗口 %s 天；runs=%s；merge_done=%s；覆盖率=%s"
               % (kpi.get("window_days", "?"), kpi.get("runs", "?"),
                  kpi.get("merge_done", "?"),
                  ("%.2f%%" % (cov * 100)) if _num(cov) else "?")]
    else:
        md.append("- 复核报告缺失/不可读：%s（%s）"
                  % (rv.get("error") or "?", rv.get("note") or "?"))
    if rv.get("subprocess"):
        sub = rv["subprocess"]
        if sub.get("ok") is False or sub.get("rc") not in (0, 1):
            md.append("- 子进程：ok=%s rc=%s err=%s（fail-visible）"
                      % (sub.get("ok"), sub.get("rc"), sub.get("error") or "—"))

    ev = rep["evidence"]
    md += ["", "## 五、证据新鲜度（48h 认证门）", ""]
    for kind, label in (("baseline", "基线"), ("redteam", "红队")):
        e = ev[kind]
        extra = ""
        if kind == "redteam" and _num(e.get("pass_rate")):
            extra = "；pass_rate=%s（%s/%s）" % (
                e["pass_rate"], e.get("passed"), e.get("total"))
        md.append("- %s：`%s`；generated_at=%s；年龄=%sh；stale=%s%s"
                  % (label, e.get("path"), e.get("generated_at") or "?",
                     _fmt_h(e.get("age_hours")), e.get("stale"), extra))
    md.append("- 说明：48h 为认证口径工具默认门；11-04 运行门按 R3 收紧为 ≤24h"
              "（运行门严于认证门）")

    mt = rep["maturity"]
    md += ["", "## 六、④ 满窗（maturity）", "",
           "- PASS 非演练样本：%d 单；now 满窗 **%d** 单 / 跟踪 %d 单"
           % (mt["pass_samples"], mt["mature_count"], mt["tracking_count"]),
           "- 最早满窗：%s；下一满窗：%s；%s 预计满窗：**%d** 单"
           % (mt.get("earliest_window_end") or "—",
              mt.get("next_window_end") or "—",
              _target_hm_from_iso(mt.get("target_time")),
              mt.get("mature_at_1030", 0)),
           "- 口径：%s" % mt.get("note", "")]
    if mt.get("mature_samples"):
        md.append("- now 已满窗样本：%s"
                  % "、".join("%s（%s）" % (s["file"], s["window_end"])
                             for s in mt["mature_samples"][:10]))
    if mt.get("tracking_samples"):
        md.append("- 跟踪中（最早）样本：%s"
                  % "、".join("%s（%s 满窗）" % (s["file"], s["window_end"])
                             for s in mt["tracking_samples"][:10]))
    if mt.get("invalid_files"):
        md.append("- 不可读验收单跳过 %d 份：%s"
                  % (len(mt["invalid_files"]),
                     "、".join(str(r.get("file")) for r in mt["invalid_files"][:5])))

    inc = rep["incidents"]
    md += ["", "## 七、事故台账（incidents）", "",
           "- 路径：`%s`（exists=%s）" % (inc.get("path"), inc.get("exists")),
           "- %s" % (inc.get("note") or "")]

    rerun = ("C:\\项目\\py312\\python.exe -X utf8 "
             "tools\\huangzhong_1104_preflight.py --json")
    if tlabel != _target_label(TARGET_TIME):
        rerun += " --target-date %s" % target.date().isoformat()
    md += ["", "## 八、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           rerun,
           "```",
           "",
           ("> 本报告为 %s 复核前置自检（只读编排），不替代 Go/No-Go 决策与"
            "老田签字；ready=True 仅表示前置数据齐备，不等于认证/Go 通过。") % tlabel,
           ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(work_dir: Path | str | None = None, out_dir: Path | str | None = None,
        repo_root: Path | str | None = None, now: dt.datetime | None = None,
        target=None, write_report: bool = True) -> dict:
    """执行黄忠观察期复核 preflight（只读编排）并输出 MD 报告。

    work_dir 缺省 = repo_root/work/huangzhong；out_dir 缺省 = C:\\项目\\deliverables；
    now 可注入（测试/复算用）；target 可注入目标日期/时刻（缺省 2026-11-04 10:00，
    显式 2026-11-10 用于 11-10 复算）；write_report=False 时不落 MD（测试用）。
    """
    now = now or _now()
    target = _parse_target_date(target)
    label = _target_label(target)
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    _log("开始 %s preflight：repo=%s work=%s" % (label, root, work))

    evidence = load_evidence(root, now)
    _log("① 证据新鲜度：%s" % evidence["status"])
    review = check_review(root, work, now)
    _log("② 六条复核：%s" % review["status"])
    go_no_go = check_go_no_go(root, work, out, now)
    _log("③ Go/No-Go：rc=%s status=%s"
         % (go_no_go.get("returncode"), go_no_go["status"]))
    maturity = compute_maturity(work, now, target)
    _log("④ 满窗：now 满窗 %d / 跟踪 %d / %s 预计 %d"
         % (maturity["mature_count"], maturity["tracking_count"], label,
            maturity["mature_at_1030"]))
    incidents = load_incidents(work, now)
    _log("⑤ 事故台账：exists=%s" % incidents["exists"])

    sections = {"evidence": evidence, "review": review, "go_no_go": go_no_go,
                "maturity": maturity, "incidents": incidents}
    blockers, warnings = _collect_issues(sections)
    checks = _build_checks(sections)
    report_md = None
    if write_report:
        # 默认（2026-11-04）与 v1 命名完全一致；显式目标日期只替换前缀日期
        prefix = "%s_黄忠复核preflight" % target.date().isoformat()
        report_md = str(out / ("%s_%s.md"
                               % (prefix, now.strftime("%Y%m%d_%H%M%S"))))
    rep = {
        "ok": True,
        "tool": "huangzhong_1104_preflight.py",
        "version": "v1 (2026-10-31)",
        "generated_at": _iso(now),
        "target_time": _iso(target),
        "repo_root": str(root),
        "work_dir": str(work),
        "out_dir": str(out),
        "read_only": True,
        "checks": checks,
        "evidence": evidence,
        "review": review,
        "go_no_go": go_no_go,
        "maturity": maturity,
        "incidents": incidents,
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "report_md": report_md,
        "report_written": False,
    }
    if write_report:
        try:
            out.mkdir(parents=True, exist_ok=True)
            Path(report_md).write_text(_render_md(rep), encoding="utf-8")
            rep["report_written"] = True
            _log("报告已落盘：%s" % report_md)
        except OSError as e:
            msg = "报告落盘失败：%s: %s" % (type(e).__name__, e)
            rep["report_error"] = msg
            warnings.append({"check": "report", "message": msg})
            _log(msg)
    _log("preflight 完成：ready=%s blockers=%d warnings=%d"
         % (rep["ready"], len(rep["blockers"]), len(rep["warnings"])))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠观察期复核 preflight 一键检查（只读编排；"
                    "默认目标 2026-11-04 10:00）")
    ap.add_argument("--json", action="store_true", help="输出完整 preflight JSON")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--out-dir", default="",
                    help="MD 报告输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--now", default="",
                    help="固定当前时钟（ISO；默认当前时间，测试/11-10 复算用）")
    ap.add_argument("--target-date", default="",
                    help="显式目标日期/时刻（默认 2026-11-04 10:00；"
                         "支持 2026-11-10 或 ISO 日期时间）")
    args = ap.parse_args(argv)
    fixed_now = None
    if args.now:
        fixed_now = _parse_ts(args.now)
        if fixed_now is None:
            print("参数错误：--now 无法解析：%r" % args.now, file=sys.stderr)
            return 2
    try:
        target = _parse_target_date(args.target_date)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    rep = run(work_dir=args.work_dir or None,
              out_dir=args.out_dir or None,
              repo_root=args.repo_root or None,
              now=fixed_now,
              target=target)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("%s preflight：ready=%s（blockers=%d，warnings=%d）→ %s"
              % (_target_label(target), rep["ready"], len(rep["blockers"]),
                 len(rep["warnings"]), rep.get("report_md")))
    return 0 if rep["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
