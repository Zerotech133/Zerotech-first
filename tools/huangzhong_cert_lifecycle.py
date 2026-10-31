# -*- coding: utf-8 -*-
"""黄忠认证生命周期 dry-run v1（只读编排；**不创建任务、不发消息、不改配置**）。

用途：把认证生命周期（到期/复演/抽查/证据刷新/下一复核日）检查成一张待办清单，
供黄忠/DSH-Lead 在每日简报或复核会前 1 分钟看清「下一步该做什么」。

数据源（全部只读）：
  · `work/huangzhong/cert_ledger.json`            认证台账（无则标注缺失）
  · `work/huangzhong/drills/drill_ledger_*.json`  受控拒收演练台账
  · `work/huangzhong/audit_sample_3.json`         赵云抽查 3 单记录
  · `agent_platform/data/test_baseline/latest.json` + `work/p1/redteam/redteam_*.json` 证据新鲜度
  · 最新 `review_*.json` / `acceptance_*.json`    上下文状态

清单项（固定 5 项）：
  ① 认证复评到期（cert 最近 issued / valid_until；无正式签发记录 → 按「首次认证 90 天」提示）
  ② 演练复演到期（最近演练 > 30 天 / 从未 → 待办）
  ③ 赵云抽查到期（最近抽查 > 30 天 / 无 → 待办）
  ④ 证据刷新（baseline / redteam age > 24h 或缺失 → 待办）
  ⑤ 下一复核日（固定 2026-11-04；`--days-ahead` 内 → 待办）

产物：`<out-dir>/<今天>_黄忠认证生命周期_<date>.md`（JSON 走 stdout `--json`）
CLI：`--json` / `--days-ahead 30` / `--work-dir` / `--out-dir`（默认 C:\\项目\\deliverables）/
     `--repo-root` / `--now`（ISO，测试复算）/ `--no-write`（只算不落盘）

dry-run 边界：只输出 checklist；**不创建任务、不发飞书/任何消息、不改台账/配置/计划任务**；
唯一写盘 = 本清单 md。日期经模块级 `_now()`（测试可 patch）。fail-visible：坏 JSON/缺文件如实标注。
退出码：0 = 生成成功（有无待办均 0）；2 = 参数错误或致命写盘错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_DAYS_AHEAD = 30
DRILL_INTERVAL_DAYS = 30
AUDIT_INTERVAL_DAYS = 30
EVIDENCE_MAX_AGE_HOURS = 24.0
FIRST_CERT_DAYS = 90
DEFAULT_VALID_DAYS = 90
NEXT_REVIEW_DATE = "2026-11-04"
MAX_LIST = 20

TOOL_NAME = "huangzhong_cert_lifecycle.py"
VERSION = "v1 (2026-10-31)"

OK, TODO, MISSING, BAD = "ok", "todo", "missing", "bad"
SEVERITY_HIGH, SEVERITY_WARN, SEVERITY_INFO = "high", "warn", "info"
SEVERITY_TEXT = {SEVERITY_HIGH: "高（阻断级）", SEVERITY_WARN: "中（需安排）",
                 SEVERITY_INFO: "低（提示）"}


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（模块级；测试可 monkeypatch 固定时钟）。"""
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


def _parse_date(raw) -> dt.date | None:
    if isinstance(raw, dt.date) and not isinstance(raw, dt.datetime):
        return raw
    ts = _parse_ts(raw)
    if ts is not None:
        return ts.date()
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def _fmt_hours(value) -> str:
    return "%.1fh" % value if _num(value) else "—"


def _fmt_days(value) -> str:
    return "%.1f 天" % value if _num(value) else "—"


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return list(rows[:limit])


def _log(msg: str) -> None:
    """进度日志走 stderr（stdout 留给 --json；测试可 monkeypatch 静音）。"""
    print("[cert-lifecycle] " + str(msg), file=sys.stderr)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _latest_file(directory: Path, pattern: str,
                 exclude_markers: tuple = ()) -> Path | None:
    directory = Path(directory)
    if not directory.is_dir():
        return None
    files = [p for p in directory.glob(pattern)
             if p.is_file()
             and not any(m.lower() in p.name.lower() for m in exclude_markers)]
    if not files:
        return None
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return files[-1]


def _days_between(due: dt.date | None, now: dt.datetime) -> int | None:
    return (due - now.date()).days if due is not None else None


# ------------------------------------------------------------------ 数据加载

CERT_ISSUED_KEYS = ("issued_at", "issued", "certified_at", "certified",
                    "last_issued_at", "issue_date")
CERT_VALID_UNTIL_KEYS = ("valid_until", "expires_at", "expiry", "expire_at",
                         "valid_to")
CERT_VALID_DAYS_KEYS = ("validity_days", "valid_days")


def load_cert(work: Path, now: dt.datetime) -> dict:
    """认证台账 cert_ledger.json：签发/有效期/首次认证锚点。"""
    path = Path(work) / "cert_ledger.json"
    row = {"status": MISSING, "path": str(path), "generated_at": None,
           "entries": 0, "issued_at": None, "valid_until": None,
           "basis": None, "due_date": None, "days_left": None,
           "validity_days": None, "note": None, "error": None}
    if not path.exists():
        row["note"] = "cert_ledger.json 缺失（无认证台账；需补建并确认首次认证）"
        return row
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["error"] = err or "JSON 顶层不是对象"
        row["note"] = "cert_ledger.json 不可读：%s" % row["error"]
        return row
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    row["entries"] = len(entries)
    row["generated_at"] = data.get("generated_at")
    issued_raw = None
    for key in CERT_ISSUED_KEYS:
        if data.get(key) not in (None, ""):
            issued_raw = data.get(key)
            row["basis"] = "field:%s" % key
            break
    if issued_raw is None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in CERT_ISSUED_KEYS:
                if entry.get(key) not in (None, ""):
                    issued_raw = entry.get(key)
                    row["basis"] = "entry.%s" % key
                    break
            if issued_raw is not None:
                break
    issued = _parse_ts(issued_raw) if issued_raw is not None else None
    row["issued_at"] = _iso(issued) or (str(issued_raw) if issued_raw else None)
    valid_raw = None
    for key in CERT_VALID_UNTIL_KEYS:
        if data.get(key) not in (None, ""):
            valid_raw = data.get(key)
            row["basis"] = "field:%s" % key
            break
    valid_until = _parse_date(valid_raw) if valid_raw is not None else None
    valid_days = None
    for key in CERT_VALID_DAYS_KEYS:
        if _num(data.get(key)):
            valid_days = int(data[key])
            row["validity_days"] = valid_days
            break
    if valid_days is None:
        valid_days = DEFAULT_VALID_DAYS
    anchor = issued
    if anchor is None:
        anchor_dates = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ts = _parse_ts(entry.get("at") or entry.get("generated_at")
                           or entry.get("date"))
            if ts is not None:
                anchor_dates.append(ts)
        gen = _parse_ts(data.get("generated_at"))
        if gen is not None:
            anchor_dates.append(gen)
        anchor = min(anchor_dates) if anchor_dates else None
    if valid_until is None and anchor is not None:
        if issued is not None:
            valid_until = (issued + dt.timedelta(days=int(valid_days))).date()
            row["basis"] = (row["basis"] or "issued") + "+valid_days=%d" % int(valid_days)
        else:
            valid_until = (anchor + dt.timedelta(days=FIRST_CERT_DAYS)).date()
            row["basis"] = "first_cert_90d(anchor=%s)" % anchor.date().isoformat()
    if issued_raw is None:
        row["status"] = "no_issued_record"
        row["note"] = ("台账无正式签发记录（仅有样本补录/无 issued 字段）："
                       "按「首次认证 90 天」窗口提示（anchor=%s）"
                       % (anchor.date().isoformat() if anchor else "无基准日期"))
    else:
        row["status"] = OK
    row["valid_until"] = valid_until.isoformat() if valid_until else None
    row["due_date"] = valid_until.isoformat() if valid_until else None
    row["days_left"] = _days_between(valid_until, now)
    return row


def load_drills(work: Path, now: dt.datetime) -> dict:
    """受控拒收演练台账：最近一次演练时间/条数/年龄。"""
    directory = Path(work) / "drills"
    files = sorted(directory.glob("drill_ledger_*.json")) if directory.is_dir() else []
    row = {"status": MISSING, "files": [], "entries": 0, "latest": None,
           "age_days": None, "bad_files": [], "note": None, "error": None}
    if not files:
        row["note"] = "drills/drill_ledger_*.json 缺失（从未登记受控拒收演练）"
        return row
    row["files"] = [str(p) for p in files]
    times: list[dt.datetime] = []
    for path in files:
        data, err = _read_json(path)
        if err is not None or not isinstance(data, dict):
            row["bad_files"].append({"file": path.name,
                                     "error": err or "JSON 顶层不是对象"})
            continue
        entries = data.get("drills")
        if not isinstance(entries, list):
            entries = data.get("entries") if isinstance(data.get("entries"), list) else []
        row["entries"] += len(entries)
        gen = _parse_ts(data.get("generated_at"))
        if gen is not None:
            times.append(gen)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ts = _parse_ts(entry.get("result_at") or entry.get("at")
                           or entry.get("generated_at") or entry.get("verified_at"))
            if ts is not None:
                times.append(ts)
    if not times:
        row["status"] = BAD if row["bad_files"] else MISSING
        row["note"] = ("演练台账无可解析时间（%d 个坏文件）"
                       % len(row["bad_files"]) if row["bad_files"]
                       else "演练台账无演练记录")
        row["error"] = row["note"]
        return row
    latest = max(times)
    row["latest"] = _iso(latest)
    row["age_days"] = round((now - latest).total_seconds() / 86400.0, 2)
    row["status"] = OK
    if row["bad_files"]:
        row["note"] = "%d 个演练台账文件坏 JSON 已跳过" % len(row["bad_files"])
    return row


def load_audit(work: Path, now: dt.datetime) -> dict:
    """赵云抽查记录 audit_sample_3.json：最近抽查时间/一致性/年龄。"""
    path = Path(work) / "audit_sample_3.json"
    row = {"status": MISSING, "path": str(path), "generated_at": None,
           "age_days": None, "result": None, "consistent": None, "total": None,
           "note": None, "error": None}
    if not path.exists():
        row["note"] = "audit_sample_3.json 缺失（从未登记赵云抽查 3 单）"
        return row
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["error"] = err or "JSON 顶层不是对象"
        row["note"] = "audit_sample_3.json 不可读：%s" % row["error"]
        return row
    gen = _parse_ts(data.get("generated_at") or data.get("reviewed_at")
                    or data.get("at"))
    row.update({"status": OK,
                "generated_at": _iso(gen) or data.get("generated_at"),
                "age_days": (round((now - gen).total_seconds() / 86400.0, 2)
                             if gen is not None else None),
                "result": data.get("result"),
                "consistent": data.get("consistent"),
                "total": data.get("total")})
    if gen is None:
        row["note"] = "抽查记录缺 generated_at（无法判定年龄，按缺失处理）"
        row["status"] = BAD
    return row


def _evidence_entry(path: Path, now: dt.datetime, kind: str) -> dict:
    """单份证据新鲜度（门 24h）。"""
    row = {"kind": kind, "path": str(path), "exists": path.exists(),
           "generated_at": None, "age_hours": None, "stale": None,
           "status": MISSING, "note": None, "error": None}
    if not path.exists():
        row["note"] = "%s 证据文件缺失" % kind
        return row
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["error"] = err or "JSON 顶层不是对象"
        row["note"] = "%s 证据不可读：%s" % (kind, row["error"])
        return row
    raw = data.get("generated_at")
    gen = _parse_ts(raw)
    row["generated_at"] = _iso(gen) or (raw if isinstance(raw, str) else None)
    if gen is None:
        row["status"] = BAD
        row["note"] = "%s 证据缺 generated_at（无法判定年龄）" % kind
        return row
    age = round((now - gen).total_seconds() / 3600.0, 2)
    row["age_hours"] = age
    row["stale"] = age > EVIDENCE_MAX_AGE_HOURS
    row["status"] = OK
    if row["stale"]:
        row["note"] = ("%s 证据超龄 %s > %.0fh（generated_at=%s）"
                       % (kind, _fmt_hours(age), EVIDENCE_MAX_AGE_HOURS,
                          row["generated_at"]))
    return row


def load_evidence(repo: Path, work: Path, now: dt.datetime) -> dict:
    """证据新鲜度：基线 latest.json + 最新红队报告（门 24h）。"""
    repo = Path(repo)
    baseline = _evidence_entry(
        repo / "agent_platform" / "data" / "test_baseline" / "latest.json",
        now, "baseline")
    redteam_dir = repo / "work" / "p1" / "redteam"
    newest = _latest_file(redteam_dir, "redteam_*.json")
    if newest is None:
        redteam = {"kind": "redteam", "path": str(redteam_dir),
                   "exists": False, "generated_at": None, "age_hours": None,
                   "stale": None, "status": MISSING,
                   "note": "红队报告缺失（work/p1/redteam/redteam_*.json 无文件）",
                   "error": None}
    else:
        redteam = _evidence_entry(newest, now, "redteam")
    stale = [row["kind"] for row in (baseline, redteam)
             if row["status"] != OK or row["stale"] is True]
    return {"baseline": baseline, "redteam": redteam,
            "max_age_hours": EVIDENCE_MAX_AGE_HOURS,
            "stale_kinds": stale, "status": OK if not stale else TODO}


def load_latest_review(work: Path, now: dt.datetime) -> dict:
    """最新六条复核（上下文；不影响 checklist 计数）。"""
    path = _latest_file(Path(work), "review_*.json")
    row = {"status": MISSING, "path": None, "generated_at": None,
           "age_hours": None, "summary": None, "note": None}
    if path is None:
        row["note"] = "review_*.json 缺失"
        return row
    data, err = _read_json(path)
    row["path"] = str(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["note"] = "review 报告不可读：%s" % err
        return row
    gen = _parse_ts(data.get("generated_at"))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    row.update({"status": OK,
                "generated_at": _iso(gen) or data.get("generated_at"),
                "age_hours": (round((now - gen).total_seconds() / 3600.0, 2)
                              if gen is not None else None),
                "summary": {"overall_pass": summary.get("overall_pass"),
                            "passed": summary.get("passed"),
                            "total": summary.get("total")}})
    return row


def load_latest_acceptance(work: Path, now: dt.datetime) -> dict:
    """最新验收单（上下文；排除演练单）。"""
    path = _latest_file(Path(work), "acceptance_*.json",
                        exclude_markers=("drill",))
    row = {"status": MISSING, "path": None, "generated_at": None,
           "age_hours": None, "verdict": None, "commit": None, "note": None}
    if path is None:
        row["note"] = "acceptance_*.json 缺失"
        return row
    data, err = _read_json(path)
    row["path"] = str(path)
    if err is not None or not isinstance(data, dict):
        row["status"] = BAD
        row["note"] = "验收单不可读：%s" % err
        return row
    gen = _parse_ts(data.get("generated_at"))
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    row.update({"status": OK,
                "generated_at": _iso(gen) or data.get("generated_at"),
                "age_hours": (round((now - gen).total_seconds() / 3600.0, 2)
                              if gen is not None else None),
                "verdict": data.get("verdict"),
                "commit": (commit.get("sha") or "")[:12] or None})
    return row


# ------------------------------------------------------------------ checklist 构建

def _item(item_id: str, title: str, status: str, due: str | None,
          severity: str, action: str, evidence: list,
          detail: dict | None = None) -> dict:
    return {"id": item_id, "title": title, "status": status, "due": due,
            "severity": severity, "action": action, "evidence": list(evidence),
            "detail": detail or {}}


def build_checklist(cert: dict, drills: dict, audit: dict, evidence: dict,
                    now: dt.datetime, days_ahead: int) -> list[dict]:
    """生成固定 5 项 checklist（status=todo 计入待办）。"""
    checklist: list[dict] = []

    # ① 认证复评到期
    cert_evidence = [cert.get("path") or "work/huangzhong/cert_ledger.json"]
    if cert.get("status") == MISSING:
        checklist.append(_item(
            "cert_reeval", "认证复评到期（cert_ledger 缺失）", TODO, None,
            SEVERITY_WARN,
            "补建 work/huangzhong/cert_ledger.json，并完成首次认证"
            "（认证 v2 六条全过 + 老田签字）", cert_evidence,
            {"reason": "cert_ledger_missing"}))
    elif cert.get("status") == BAD:
        checklist.append(_item(
            "cert_reeval", "认证复评到期（台账不可读）", TODO, None,
            SEVERITY_WARN,
            "修复 cert_ledger.json（坏 JSON 不可读）：%s" % (cert.get("error") or "?"),
            cert_evidence, {"reason": "cert_ledger_bad_json"}))
    else:
        due = cert.get("due_date")
        days_left = cert.get("days_left")
        basis = cert.get("basis") or "—"
        no_issued = cert.get("status") == "no_issued_record"
        detail = {"basis": basis, "issued_at": cert.get("issued_at"),
                  "valid_until": due, "days_left": days_left,
                  "entries": cert.get("entries"),
                  "no_issued_record": no_issued}
        if _num(days_left) and days_left < 0:
            checklist.append(_item(
                "cert_reeval", "认证复评到期（已过期）", TODO, due,
                SEVERITY_HIGH,
                "认证已过期 %d 天：立即按认证 v2 复评（六条全过 + 老田签字）"
                % abs(int(days_left)), cert_evidence, detail))
        elif _num(days_left) and days_left <= int(days_ahead):
            action = ("认证将在 %d 天后到期（basis=%s）：安排复评"
                      "（认证 v2 六条 + 老田签字）" % (int(days_left), basis))
            if no_issued:
                action = ("无正式签发记录，按首次认证 90 天窗口跟踪"
                          "（due=%s，剩 %d 天）：确认首次认证签发"
                          % (due, int(days_left)))
            checklist.append(_item(
                "cert_reeval", "认证复评到期", TODO, due, SEVERITY_WARN,
                action, cert_evidence, detail))
        else:
            action = ("认证有效期至 %s（剩 %s 天），未进入 %d 天预警窗"
                      % (due or "—", _fmt_num(days_left), int(days_ahead)))
            if no_issued:
                action = ("无正式签发记录：按「首次认证 90 天」跟踪"
                          "（anchor 推算 due=%s，剩 %s 天），不阻塞"
                          % (due or "—", _fmt_num(days_left)))
            checklist.append(_item(
                "cert_reeval", "认证复评到期", OK, due, SEVERITY_INFO,
                action, cert_evidence, detail))

    # ② 演练复演到期
    drill_evidence = _cap(drills.get("files") or ["work/huangzhong/drills/"])
    if drills.get("status") == MISSING:
        checklist.append(_item(
            "drill_rehearsal", "演练复演到期（从未演练）", TODO, None,
            SEVERITY_WARN,
            "按认证 v2 §5 完成 ≥1 次真实受控拒收演练（REJECT/OBSERVE），"
            "登记 drills/drill_ledger_*.json 并留见证", drill_evidence,
            {"reason": "never"}))
    elif drills.get("status") == BAD:
        checklist.append(_item(
            "drill_rehearsal", "演练复演到期（台账不可读）", TODO, None,
            SEVERITY_WARN,
            "修复 drills/drill_ledger_*.json：%s"
            % (drills.get("note") or drills.get("error") or "坏 JSON"),
            drill_evidence, {"reason": "bad_ledger"}))
    else:
        age = drills.get("age_days")
        detail = {"latest": drills.get("latest"), "age_days": age,
                  "entries": drills.get("entries"),
                  "interval_days": DRILL_INTERVAL_DAYS}
        if _num(age) and age > DRILL_INTERVAL_DAYS:
            checklist.append(_item(
                "drill_rehearsal", "演练复演到期", TODO,
                (now + dt.timedelta(days=int(DRILL_INTERVAL_DAYS - age))).date().isoformat(),
                SEVERITY_WARN,
                "距上次演练 %s > %d 天：安排复演（认证 v2 §5，禁造数据，留证据链/复位）"
                % (_fmt_days(age), DRILL_INTERVAL_DAYS), drill_evidence, detail))
        else:
            checklist.append(_item(
                "drill_rehearsal", "演练复演到期", OK, None, SEVERITY_INFO,
                "最近演练 %s（%s），未超 %d 天"
                % (drills.get("latest") or "—", _fmt_days(age),
                   DRILL_INTERVAL_DAYS), drill_evidence, detail))

    # ③ 赵云抽查到期
    audit_evidence = [audit.get("path") or "work/huangzhong/audit_sample_3.json"]
    if audit.get("status") == MISSING:
        checklist.append(_item(
            "audit_sample", "赵云抽查到期（从未抽查）", TODO, None,
            SEVERITY_WARN,
            "按认证 v2 ⑥ 完成赵云抽查 3 单（seed 可复现、逐项核对、3/3 一致），"
            "落 work/huangzhong/audit_sample_3.json", audit_evidence,
            {"reason": "never"}))
    elif audit.get("status") == BAD:
        checklist.append(_item(
            "audit_sample", "赵云抽查到期（记录不可读）", TODO, None,
            SEVERITY_WARN,
            "修复 audit_sample_3.json：%s"
            % (audit.get("note") or audit.get("error") or "坏 JSON"),
            audit_evidence, {"reason": "bad_record"}))
    else:
        age = audit.get("age_days")
        detail = {"latest": audit.get("generated_at"), "age_days": age,
                  "result": audit.get("result"),
                  "interval_days": AUDIT_INTERVAL_DAYS}
        if _num(age) and age > AUDIT_INTERVAL_DAYS:
            checklist.append(_item(
                "audit_sample", "赵云抽查到期", TODO,
                (now + dt.timedelta(days=int(AUDIT_INTERVAL_DAYS - age))).date().isoformat(),
                SEVERITY_WARN,
                "距上次抽查 %s > %d 天：安排新一轮赵云抽查 3 单（3/3 一致）"
                % (_fmt_days(age), AUDIT_INTERVAL_DAYS), audit_evidence, detail))
        else:
            checklist.append(_item(
                "audit_sample", "赵云抽查到期", OK, None, SEVERITY_INFO,
                "最近抽查 %s（%s），未超 %d 天"
                % (audit.get("generated_at") or "—", _fmt_days(age),
                   AUDIT_INTERVAL_DAYS), audit_evidence, detail))

    # ④ 证据刷新
    ev_evidence = [evidence["baseline"].get("path"),
                   evidence["redteam"].get("path")]
    stale = evidence.get("stale_kinds") or []
    if stale:
        detail = {"stale_kinds": stale,
                  "baseline": evidence["baseline"],
                  "redteam": evidence["redteam"],
                  "max_age_hours": EVIDENCE_MAX_AGE_HOURS}
        checklist.append(_item(
            "evidence_refresh", "证据刷新（%s 超龄/缺失）" % "、".join(stale),
            TODO, None, SEVERITY_WARN,
            "刷新证据：`python -X utf8 tools/test_baseline.py --run` + "
            "`python -X utf8 tools/zhuge_redteam.py --json`（或等每日 20:00 "
            "HuangzhongEvidenceRefresh 自动任务）；后续验收证据年龄须 ≤24h",
            ev_evidence, detail))
    else:
        detail = {"baseline_age_h": evidence["baseline"].get("age_hours"),
                  "redteam_age_h": evidence["redteam"].get("age_hours"),
                  "max_age_hours": EVIDENCE_MAX_AGE_HOURS}
        checklist.append(_item(
            "evidence_refresh", "证据刷新", OK, None, SEVERITY_INFO,
            "证据新鲜：基线 age≈%s、红队 age≈%s（门 %.0fh）"
            % (_fmt_hours(detail["baseline_age_h"]),
               _fmt_hours(detail["redteam_age_h"]), EVIDENCE_MAX_AGE_HOURS),
            ev_evidence, detail))

    # ⑤ 下一复核日（固定 2026-11-04）
    review_due = _parse_date(NEXT_REVIEW_DATE)
    days_left = _days_between(review_due, now)
    review_evidence = [
        "docs/plans/2026-10-30_黄忠11-04复核runbook_v1.md",
        "docs/plans/2026-10-31_黄忠11-04复核一键流程_v1.md",
    ]
    detail = {"due": NEXT_REVIEW_DATE, "days_left": days_left,
              "fixed": True}
    if _num(days_left) and days_left < 0:
        checklist.append(_item(
            "next_review", "下一复核日（已过）", TODO, NEXT_REVIEW_DATE,
            SEVERITY_HIGH,
            "复核日 %s 已过：确认是否已开复核会并出回执"
            "（deliverables/2026-11-04_黄忠观察期复核回执.md）"
            % NEXT_REVIEW_DATE, review_evidence, detail))
    elif _num(days_left) and days_left <= int(days_ahead):
        checklist.append(_item(
            "next_review", "下一复核日", TODO, NEXT_REVIEW_DATE,
            SEVERITY_WARN,
            "距观察期复核日 %s 还有 %d 天：备好自证包 + 按一键流程执行"
            "（huangzhong_review.py --json 等）" % (NEXT_REVIEW_DATE, int(days_left)),
            review_evidence, detail))
    else:
        checklist.append(_item(
            "next_review", "下一复核日", OK, NEXT_REVIEW_DATE, SEVERITY_INFO,
            "复核日 %s（剩 %s 天），未进入 %d 天预警窗"
            % (NEXT_REVIEW_DATE, _fmt_num(days_left), int(days_ahead)),
            review_evidence, detail))

    return checklist


# ------------------------------------------------------------------ 渲染

def _conclusion(checklist: list[dict]) -> str:
    todos = [item for item in checklist if item.get("status") == TODO]
    if not todos:
        return "全部健康：0 项待办（认证/演练/抽查/证据/复核日均在窗口内）。"
    high = [item for item in todos if item.get("severity") == SEVERITY_HIGH]
    ids = "、".join(str(item.get("id")) for item in todos)
    text = "待办 %d 项：%s。" % (len(todos), ids)
    if high:
        text += "其中 %d 项高优先级（%s）需立即处理。" % (
            len(high), "、".join(str(i.get("id")) for i in high))
    return text


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_md(rep: dict) -> str:
    date_text = rep.get("date") or ""
    checklist = rep.get("checklist") or []
    warnings = rep.get("warnings") or []
    cert = rep.get("cert") or {}
    drills = rep.get("drills") or {}
    audit = rep.get("audit") or {}
    evidence = rep.get("evidence") or {}
    review_ctx = rep.get("review") or {}
    acc_ctx = rep.get("acceptance") or {}
    todos = [item for item in checklist if item.get("status") == TODO]

    lines: list[str] = []
    lines.append("# 黄忠认证生命周期 · dry-run · %s" % date_text)
    lines.append("")
    lines.append("> 生成：%s（Asia/Shanghai）｜工具：`tools/%s` %s｜工单：`WO-HZ-CERTLIFE-20261031`"
                 % (rep.get("generated_at") or "—", TOOL_NAME, VERSION))
    lines.append("> **dry-run 边界**：本清单只输出待办，**不创建任务、不发飞书/任何消息、"
                 "不改台账/配置/计划任务**；唯一写盘 = 本 md。日期经 `_now()` 计算。")
    lines.append("")
    lines.append("**一句话结论：** %s" % rep.get("conclusion"))
    lines.append("")

    # 一、生命周期状态
    lines.append("## 一、生命周期状态")
    lines.append("")
    lines.append("| 项 | 状态 | 最近 | 到期/下次 | 备注 |")
    lines.append("|---|---|---|---|---|")

    def _row(label, status, latest, due, note):
        lines.append("| %s | %s | %s | %s | %s |"
                     % (_cell(label), _cell(status), _cell(latest),
                        _cell(due), _cell(note)))

    cert_basis = ("无签发记录·首次认证 90 天（%s）" % cert.get("basis")
                  if cert.get("status") == "no_issued_record"
                  else (cert.get("basis") or "—"))
    _row("认证有效期 cert_ledger", cert.get("status"),
         cert.get("issued_at") or "—", cert.get("due_date") or "—",
         "%s；剩 %s 天" % (cert_basis, _fmt_num(cert.get("days_left")))
         if cert.get("due_date") else cert.get("note") or "—")
    _row("受控拒收演练", drills.get("status"), drills.get("latest") or "—",
         ("超 %d 天复演" % DRILL_INTERVAL_DAYS) if drills.get("status") == OK else "—",
         "最近 %s；共 %s 条演练" % (_fmt_days(drills.get("age_days")),
                                   _fmt_num(drills.get("entries")))
         if drills.get("status") == OK else drills.get("note") or "—")
    _row("赵云抽查 3 单", audit.get("status"), audit.get("generated_at") or "—",
         ("超 %d 天再抽" % AUDIT_INTERVAL_DAYS) if audit.get("status") == OK else "—",
         "结果 %s（%s/%s）" % (audit.get("result") or "—",
                              _fmt_num(audit.get("consistent")),
                              _fmt_num(audit.get("total")))
         if audit.get("status") == OK else audit.get("note") or "—")
    base = evidence.get("baseline") or {}
    red = evidence.get("redteam") or {}
    _row("证据刷新（门 %.0fh）" % EVIDENCE_MAX_AGE_HOURS,
         evidence.get("status"),
         "基线 %s / 红队 %s" % (base.get("generated_at") or "—",
                               red.get("generated_at") or "—"),
         "≤%.0fh" % EVIDENCE_MAX_AGE_HOURS,
         "age：基线 %s、红队 %s%s"
         % (_fmt_hours(base.get("age_hours")), _fmt_hours(red.get("age_hours")),
            ("；超龄/缺失：%s" % "、".join(evidence.get("stale_kinds") or []))
            if evidence.get("stale_kinds") else "（新鲜）"))
    _row("下一复核日（固定）", "ok", NEXT_REVIEW_DATE,
         NEXT_REVIEW_DATE,
         "剩 %s 天（预警窗 %d 天）"
         % (_fmt_num(_days_between(_parse_date(NEXT_REVIEW_DATE),
                                   _parse_ts(rep.get("generated_at")) or _now())),
            rep.get("days_ahead") or DEFAULT_DAYS_AHEAD))
    lines.append("")
    lines.append("**上下文（不影响待办计数）**：")
    lines.append("")
    lines.append("- 最新六条复核：%s（%s/%s ✓）%s"
                 % (review_ctx.get("status"),
                    _fmt_num((review_ctx.get("summary") or {}).get("passed")),
                    _fmt_num((review_ctx.get("summary") or {}).get("total")),
                    "；报告 `%s`" % review_ctx.get("path")
                    if review_ctx.get("path") else ""))
    lines.append("- 最新验收单：%s（%s，commit `%s`）"
                 % (acc_ctx.get("status"), acc_ctx.get("verdict") or "—",
                    acc_ctx.get("commit") or "—"))
    lines.append("")

    # 二、待办清单
    lines.append("## 二、待办清单（dry-run 输出，不自动执行）")
    lines.append("")
    if todos:
        lines.append("| # | 项 | 到期 | 严重度 | 动作 | 证据 |")
        lines.append("|---|---|---|---|---|---|")
        for idx, item in enumerate(todos, 1):
            lines.append("| %d | %s | %s | %s | %s | %s |"
                         % (idx, _cell(item.get("title")),
                            _cell(item.get("due") or "—"),
                            _cell(SEVERITY_TEXT.get(item.get("severity"),
                                                    item.get("severity"))),
                            _cell(item.get("action")),
                            _cell("<br>".join(str(p) for p in
                                              _cap(item.get("evidence") or [], 3)))))
    else:
        lines.append("- 无待办：认证/演练/抽查/证据/复核日全部在窗口内。")
    lines.append("")

    # 三、全量 checklist（含已 OK 项，便于核对）
    lines.append("## 三、全量 checklist（5 项）")
    lines.append("")
    lines.append("| 项 | 状态 | 到期 | 严重度 | 动作 |")
    lines.append("|---|---|---|---|---|")
    for item in checklist:
        lines.append("| %s | %s | %s | %s | %s |"
                     % (_cell(item.get("title")), _cell(item.get("status")),
                        _cell(item.get("due") or "—"),
                        _cell(item.get("severity")), _cell(item.get("action"))))
    lines.append("")

    # 四、fail-visible 与证据
    lines.append("## 四、数据源与 fail-visible")
    lines.append("")
    if warnings:
        for warn in _cap(warnings, 20):
            lines.append("- %s" % warn)
    else:
        lines.append("- 无告警（5 项数据源均可读）")
    lines.append("")
    lines.append("- 认证台账：`%s`" % (cert.get("path") or "—"))
    lines.append("- 演练台账：`%s`" % "、".join(
        str(p) for p in _cap(drills.get("files") or [], 5)) if drills.get("files")
        else "- 演练台账：`work/huangzhong/drills/drill_ledger_*.json`")
    lines.append("- 抽查记录：`%s`" % (audit.get("path") or "—"))
    lines.append("- 证据：`agent_platform/data/test_baseline/latest.json`、"
                 "`work/p1/redteam/redteam_*.json`")
    lines.append("- 认证口径：`docs/plans/2026-10-30_黄忠认证标准v2与验收工具证据口径拍板_v1.md`")
    lines.append("- 复核口径：`docs/plans/2026-10-30_黄忠11-04复核runbook_v1.md`（v6）、"
                 "`docs/plans/2026-10-31_黄忠11-04复核一键流程_v1.md`（v1.1）")
    lines.append("")

    # 五、生成信息
    lines.append("## 五、生成信息")
    lines.append("")
    lines.append("- 生成时间：`%s`｜工作目录：`%s`｜窗口：`--days-ahead %s`"
                 % (rep.get("generated_at") or "—", rep.get("work_dir") or "—",
                    rep.get("days_ahead")))
    lines.append("- 待办数：**%d**｜checklist 项数：%d｜dry_run=%s｜side_effects=%s"
                 % (len(todos), len(checklist), rep.get("dry_run"),
                    rep.get("side_effects")))
    lines.append("- 只读：不写 `work/`、不建任务、不发消息、不改配置；唯一写盘 = 本 md。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("版本：%s ｜ 作者：DSH-Lead · cert-v2 ｜ 工单：WO-HZ-CERTLIFE-20261031 ｜ "
                 "状态：认证生命周期 dry-run（只读）" % date_text)
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 编排

def run(work_dir: Path | str | None = None,
        out_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        days_ahead: int = DEFAULT_DAYS_AHEAD,
        now: dt.datetime | None = None,
        write_report: bool = True) -> dict:
    """只读计算认证生命周期 checklist，并生成 md（返回 JSON 摘要）。"""
    clock = now or _now()
    repo = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else repo / "work" / "huangzhong"
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    days_ahead = max(1, int(days_ahead or DEFAULT_DAYS_AHEAD))
    date_text = clock.date().isoformat()

    cert = load_cert(work, clock)
    drills = load_drills(work, clock)
    audit = load_audit(work, clock)
    evidence = load_evidence(repo, work, clock)
    review_ctx = load_latest_review(work, clock)
    acc_ctx = load_latest_acceptance(work, clock)
    checklist = build_checklist(cert, drills, audit, evidence, clock, days_ahead)

    warnings: list[str] = []
    for name, row in (("认证台账", cert), ("演练台账", drills),
                      ("抽查记录", audit)):
        if row.get("status") in (MISSING, BAD):
            warnings.append("%s：%s" % (name, row.get("note") or row.get("error")
                                        or "缺失/不可读"))
    for kind, row in (("基线证据", evidence.get("baseline") or {}),
                      ("红队证据", evidence.get("redteam") or {})):
        if row.get("status") in (MISSING, BAD) or row.get("stale") is True:
            warnings.append("%s：%s" % (kind, row.get("note") or "超龄/缺失"))
    if review_ctx.get("status") in (MISSING, BAD):
        warnings.append("最新复核：%s" % (review_ctx.get("note") or "缺失/不可读"))
    if acc_ctx.get("status") in (MISSING, BAD):
        warnings.append("最新验收单：%s" % (acc_ctx.get("note") or "缺失/不可读"))

    rep = {
        "ok": True,
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": _iso(clock),
        "date": date_text,
        "repo_root": str(repo),
        "work_dir": str(work),
        "out_dir": str(out),
        "days_ahead": days_ahead,
        "next_review_date": NEXT_REVIEW_DATE,
        "read_only": True,
        "dry_run": True,
        "side_effects": "none",
        "cert": cert,
        "drills": drills,
        "audit": audit,
        "evidence": evidence,
        "review": review_ctx,
        "acceptance": acc_ctx,
        "checklist": checklist,
        "todo_count": len([i for i in checklist if i.get("status") == TODO]),
        "warnings": warnings,
        "report_path": None,
        "report_written": False,
        "error": None,
    }
    rep["conclusion"] = _conclusion(checklist)

    if write_report:
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / ("%s_黄忠认证生命周期_%s.md"
                             % (date_text, date_text))
        try:
            report_path.write_text(render_md(rep), encoding="utf-8")
            rep["report_path"] = str(report_path)
            rep["report_written"] = True
            _log("生命周期清单已落盘：%s" % report_path)
        except OSError as exc:
            rep["ok"] = False
            rep["error"] = "清单落盘失败：%s: %s" % (type(exc).__name__, exc)
            rep["report_path"] = str(report_path)
            _log(rep["error"])
    return rep


def _summary(rep: dict) -> dict:
    """stdout JSON 摘要（清单全量 + 关键上下文）。"""
    return {
        "ok": bool(rep.get("ok")),
        "tool": rep.get("tool"),
        "version": rep.get("version"),
        "generated_at": rep.get("generated_at"),
        "date": rep.get("date"),
        "days_ahead": rep.get("days_ahead"),
        "next_review_date": rep.get("next_review_date"),
        "read_only": rep.get("read_only"),
        "dry_run": rep.get("dry_run"),
        "side_effects": rep.get("side_effects"),
        "conclusion": rep.get("conclusion"),
        "todo_count": rep.get("todo_count"),
        "checklist": rep.get("checklist"),
        "cert": rep.get("cert"),
        "drills": rep.get("drills"),
        "audit": rep.get("audit"),
        "evidence": rep.get("evidence"),
        "review": rep.get("review"),
        "acceptance": rep.get("acceptance"),
        "warnings": rep.get("warnings") or [],
        "report_path": rep.get("report_path"),
        "report_written": rep.get("report_written"),
        "error": rep.get("error"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠认证生命周期 dry-run（只读清单；不建任务/不发消息）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--days-ahead", type=int, default=DEFAULT_DAYS_AHEAD,
                    help="预警窗口天数（默认 30；影响认证到期/下一复核日）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--out-dir", default="",
                    help="清单输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--now", default="",
                    help="固定生成时钟（ISO；默认当前时间，测试/复算用）")
    ap.add_argument("--no-write", action="store_true",
                    help="只计算并输出，不落 md（dry-run 只读冒烟用）")
    args = ap.parse_args(argv)
    try:
        fixed_now = _parse_ts(args.now) if args.now else None
        if args.now and fixed_now is None:
            raise ValueError("--now 无法解析：%r" % args.now)
        rep = run(work_dir=args.work_dir or None,
                  out_dir=args.out_dir or None,
                  repo_root=args.repo_root or None,
                  days_ahead=args.days_ahead,
                  now=fixed_now,
                  write_report=not args.no_write)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    summary = _summary(rep)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print("黄忠认证生命周期：%s" % rep.get("conclusion"))
        print("  清单：%s" % (rep.get("report_path") or "（--no-write 未落盘）"))
        for warn in _cap(rep.get("warnings") or [], 10):
            print("  [warn] %s" % warn)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
