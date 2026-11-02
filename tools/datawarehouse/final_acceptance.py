#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3-08 终验收一键检查（G1–G10，只读，输出证据 JSON/MD）。

依据 `deliverables/2026-11-02_S3-08终验收_检查清单_v1.md` §1 的 Gate 定义，把一个 Gate
采集为 {pass|fail|expected-risk|unknown} + evidence（含证据路径/关键值）；总判定：
**除 expected-risk 外全 pass 才 PASS**；任一 fail → FAIL；无 fail 但有 unknown → UNKNOWN
（fail-visible，绝不把缺失证据硬编码为通过）。

Gate 一览（默认 evidence 源）：
  G1 今晚 TdxSync 四批   任务 LastResult+LastRunTime；数据级 = TdxSyncProbe 严格窗口记录的
                          rows_delta_total>0 + data/tdx/<关键表>/part_*.parquet mtime>窗口起点
  G2 TdxSyncProbe 严格判定  19:20+ 最近一条 code/fail/lock_hits（probe 任务日志优先，回退 JSONL）
  G3 qualify 分布       warehouse_registry.json 的 qualification_status 分布，unknown<40 判过
  G4 XmapSync（明早 06:00）  任务 LastResult=0 且今日已跑；附日志尾部
  G5 KgDailySync（06:20 链）任务 rc=0 + catalog.json 当日更新 + registry.catalog_sha256 对齐
  G6 DataWarehouseSnapshot（06:40 首跑）  任务今日 06:40 后 rc=0 + snapshot.generated_at 当日 +
                         dw_snapshot_daily.log 末行 ok/rc + snapshot.catalog.version=当前 catalog.version
  G7 NoticeBackfill（06:45）  \\ZeroBound\\NoticeBackfill rc=0 且今日已跑（旧任务 Disabled 仅记录）
  G8 A3RotationCanary（07:00）rc=1 属 accepted-risk（非失败）；rc=0 记 pass（风险已消除）
  G9 SentimentBT（07:20）  rc=0 + 两产物 age<48h + 未出现当日 push 痕迹
  G10 KPI 复跑          调用/导入 kpi_report.build_report()：ok≥9、unknown=0、K3=ok

用法：
  python tools/datawarehouse/final_acceptance.py                       # 今日验收（写 JSON+MD）
  python tools/datawarehouse/final_acceptance.py --date 20261103       # 明早 09:00 终验收
  python tools/datawarehouse/final_acceptance.py --date 20261103 --night-date 20261102
  python tools/datawarehouse/final_acceptance.py --json-only           # 只写 JSON、不写 MD
  python tools/datawarehouse/final_acceptance.py --json-out X.json --md-out X.md
退出码：0=PASS / 1=FAIL / 2=UNKNOWN / 3=参数错误。

只读：不触发任务、不改数据/catalog/registry、不重启；唯一写入=输出 JSON/MD（默认
research/reports/final_acceptance_<date>.json|.md）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

TOOL = "final_acceptance"
VERSION = "v1"

_REPO_FALLBACK = Path(__file__).resolve().parents[2]
try:  # 与数据仓工具族同口径（worktree 内运行也解析回主仓）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import common as _common  # noqa: E402

    REPO = _common.REPO
    DATA = _common.DATA
except Exception:  # noqa: BLE001
    REPO = _REPO_FALLBACK
    DATA = REPO / "quant_backtest" / "data"

REPORTS = REPO / "research" / "reports"
CATALOG = DATA / "catalog.json"
REGISTRY = DATA / "warehouse_registry.json"
MANIFEST = DATA / "tdx" / "_manifest.json"
TDX_DIR = DATA / "tdx"
SNAPSHOT = REPO / "portal" / "static" / "dw_snapshot.json"
PROBE_LOG = Path(r"C:\Temp\tdx_sync_probe.log")
PROBE_TASK_LOG = Path(r"C:\Temp\tdx_sync_probe_task.log")
DW_LOG = Path(r"C:\Temp\dw_snapshot_daily.log")
A3_LOG = Path(r"C:\Temp\rotation_canary.log")
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
KG_CATALOG_OUT = Path(r"C:\Temp\kg_catalog_build_out.log")
XMAP_LOG = REPO / "quant_backtest" / "logs" / "xmap_sync.out"
SENTIMENT_RESULT = REPO / "news_collector" / "data" / "sentiment_bt_result.json"
JEV_IMPACT = REPO / "news_collector" / "data" / "jev_impact.json"
SENTIMENT_PUSHED = REPO / "news_collector" / "data" / "sentiment_bt_pushed.json"

TASK_TDX = [
    r"\ZeroBound\TdxSync_A_高频",
    r"\ZeroBound\TdxSync_B_财务",
    r"\ZeroBound\TdxSync_C_低频",
    r"\ZeroBound\TdxSync_D_缺口",
]
TASK_PROBE = r"\ZeroBound\TdxSyncProbe"
TASK_XMAP = r"\XmapSync"
TASK_KG = r"\KgDailySync"
TASK_DW = r"\ZeroBound\DataWarehouseSnapshot"
TASK_NOTICE = r"\ZeroBound\NoticeBackfill"
TASK_NOTICE_OLD = r"\NoticeBackfill"
TASK_A3 = r"\A3RotationCanary"
TASK_SENT = r"\ZeroBound\SentimentBT"

KEY_TABLES = ("ts_daily", "ts_daily_basic", "ts_index_daily", "ts_moneyflow",
              "ts_margin_detail", "ts_hk_daily", "ts_fx_daily")
WINDOW_START = dt.time(17, 30)       # TdxSync A 批首跑
READY_AFTER = dt.time(19, 20)        # 看门狗严格判定起点
UNKNOWN_THRESHOLD = 40               # G3/K8：unknown<40
KPI_MIN_OK = 9                       # G10：ok>=9
SENTIMENT_MAX_AGE_H = 48             # G9：两产物 age<48h

GATE_SPECS = [
    ("G1", "今晚 TdxSync 四批（任务+数据级）"),
    ("G2", "TdxSyncProbe 严格判定（19:20+）"),
    ("G3", "qualify 分布 / K8 复判"),
    ("G4", "XmapSync 明早首跑"),
    ("G5", "KgDailySync 链"),
    ("G6", "DataWarehouseSnapshot 首跑（K3）"),
    ("G7", "NoticeBackfill（PT8H）"),
    ("G8", "A3RotationCanary（accepted-risk）"),
    ("G9", "SentimentBT 首跑"),
    ("G10", "KPI 复跑（kpi_report）"),
]
GATE_TITLES = dict(GATE_SPECS)


# ---------------------------------------------------------------- 基础工具
def _safe_print(text: str) -> None:
    """GBK/CP936 控制台也能输出（emoji/中文失败时按 UTF-8 字节兜底）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            print(text.encode("ascii", "replace").decode("ascii"))


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _parse_dt(value) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_now().tzinfo)
    return parsed


def _parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(str(text), "%Y%m%d").date()


def _read_text(path: Path | None) -> tuple[str | None, str]:
    if path is None:
        return None, "path_none"
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace"), ""
    except OSError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _read_json(path: Path | None):
    text, err = _read_text(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return None, "json_invalid: %s" % (exc,)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_json_objects(text: str):
    """从可能有缩进/多段的文本中依次解析 JSON 对象（probe 任务日志=连续 pretty JSON）。"""
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        while idx < length and text[idx] in " \r\n\t":
            idx += 1
        if idx >= length:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except ValueError:
            # 跳过本行损坏/非 JSON 段
            nl = text.find("\n", idx)
            if nl < 0:
                break
            idx = nl + 1
            continue
        yield obj
        idx = end


def _gate(gate: str, status: str, *, evidence: dict | None = None,
          reason: str = "", notes: str = "") -> dict:
    return {
        "gate": gate,
        "title": GATE_TITLES.get(gate, gate),
        "status": status,
        "reason": reason,
        "notes": notes,
        "evidence": evidence or {},
    }


def _split_task(spec: str) -> tuple[str, str]:
    spec = str(spec)
    idx = spec.rfind("\\")
    if idx < 0:
        return "\\", spec
    return spec[:idx + 1], spec[idx + 1:]


def _task_key(spec: str) -> str:
    return str(spec)


def _task_last_run(record: dict | None) -> dt.datetime | None:
    return _parse_dt((record or {}).get("last_run"))


def _task_result(record: dict | None):
    return (record or {}).get("last_result")


def query_tasks(specs: list[str], timeout: int = 60) -> tuple[dict, str]:
    """只读查询计划任务：返回 ({spec_key: record}, error)。"""
    rows = []
    for spec in specs:
        path, name = _split_task(spec)
        rows.append("@{path='%s'; name='%s'}" % (path.replace("'", "''"),
                                                 name.replace("'", "''")))
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        "$spec=@(%s)\n"
        "$out=@()\n"
        "foreach($s in $spec){\n"
        "  $t=Get-ScheduledTask -TaskName $s.name -TaskPath $s.path -ErrorAction SilentlyContinue\n"
        "  $k=$s.path+$s.name\n"
        "  if(-not $t){ $out+=[pscustomobject]@{key=$k; found=$false}; continue }\n"
        "  $i=Get-ScheduledTaskInfo -TaskName $s.name -TaskPath $s.path -ErrorAction SilentlyContinue\n"
        "  $lr=''; $nr=''\n"
        "  if($i -and $i.LastRunTime){ $lr=$i.LastRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz') }\n"
        "  if($i -and $i.NextRunTime){ $nr=$i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz') }\n"
        "  $out+=[pscustomobject]@{key=$k; found=$true; state=[string]$t.State;"
        " last_run=$lr; next_run=$nr;"
        " last_result=$(if($i){[long]$i.LastTaskResult}else{$null})}\n"
        "}\n"
        "$out|ConvertTo-Json -Compress -Depth 4\n" % ",".join(rows)
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, "task_query_exception: %r" % (exc,)
    if proc.returncode != 0:
        return {}, "task_query_rc=%d: %s" % (proc.returncode, (proc.stderr or "")[-200:])
    text = (proc.stdout or "").strip()
    if not text:
        return {}, "task_query_empty"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {}, "task_query_json: %s" % (exc,)
    items = data if isinstance(data, list) else [data]
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        result = item.get("last_result")
        try:
            result = int(result) if result is not None else None
        except (TypeError, ValueError):
            result = None
        out[key] = {
            "found": bool(item.get("found", True)),
            "state": str(item.get("state") or ""),
            "last_run": str(item.get("last_run") or ""),
            "next_run": str(item.get("next_run") or ""),
            "last_result": result,
        }
    return out, ""


def _latest_part_mtime(table_dir: Path) -> float:
    if not table_dir.is_dir():
        return 0.0
    newest = 0.0
    try:
        for part in table_dir.rglob("*.parquet"):
            try:
                newest = max(newest, part.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return 0.0
    return newest


# ---------------------------------------------------------------- G1
def resolve_night_date(date: dt.date, now: dt.datetime) -> dt.date:
    """验收日 T 的“今晚”= T-1；若当天 19:20 后手工验收则今晚=当天。"""
    if now.date() == date and now.time() >= READY_AFTER:
        return date
    return date - dt.timedelta(days=1)


def collect_g1(*, date: dt.date, now: dt.datetime, night_date: dt.date | None = None,
               tasks: dict | None = None, probe_entry: dict | None = None,
               fresh_tables: dict | None = None, tdx_dir: Path = TDX_DIR,
               task_specs: list[str] | None = None) -> dict:
    """G1：今晚四批（任务 LastResult/LastRun + 数据级 rows 增量 & part mtime）。"""
    night = night_date or resolve_night_date(date, now)
    specs = list(task_specs or TASK_TDX)
    evidence: dict = {"night_date": night.isoformat(), "tasks": {}}
    problems: list[str] = []
    unknown: list[str] = []
    if tasks is None:
        tasks, err = query_tasks(specs)
        if err:
            return _gate("G1", "unknown", evidence=evidence,
                         reason="task_query_failed: %s" % err)
    tasks = tasks or {}
    window_start = dt.datetime.combine(night, WINDOW_START, tzinfo=now.tzinfo)
    for spec in specs:
        rec = tasks.get(spec)
        ev = {"found": bool((rec or {}).get("found")), "state": (rec or {}).get("state", ""),
              "last_run": (rec or {}).get("last_run", ""),
              "last_result": _task_result(rec)}
        evidence["tasks"][spec] = ev
        if not rec or not rec.get("found"):
            unknown.append("task_missing:%s" % spec)
            continue
        if _task_result(rec) != 0:
            problems.append("last_result_nonzero:%s=%s" % (spec, _task_result(rec)))
        last_run = _task_last_run(rec)
        if last_run is None:
            unknown.append("last_run_missing:%s" % spec)
        elif last_run.date() != night or last_run < window_start:
            problems.append("not_run_in_night:%s(%s)" % (spec, ev["last_run"]))

    # 数据级 1：TdxSyncProbe 严格窗口记录的 rows_delta_total（与 G2 同源）
    entry = probe_entry
    if entry is None:
        entries, source, perr = load_probe_entries(night, now)
        entry = entries[-1] if entries else None
        evidence["probe_source"] = source
        if perr:
            unknown.append("probe_log:%s" % perr)
    if entry is None:
        unknown.append("probe_strict_entry_missing(rows_delta_total 不可证)")
        evidence["rows_delta_total"] = None
    else:
        rows_delta = entry.get("rows_delta_total")
        evidence["probe_ts"] = entry.get("ts") or entry.get("generated_at")
        evidence["rows_delta_total"] = rows_delta
        evidence["probe_fail_codes"] = entry.get("fail_codes")
        if not isinstance(rows_delta, int):
            unknown.append("rows_delta_total_missing")
        elif rows_delta <= 0:
            problems.append("rows_delta_total<=0(%s)" % rows_delta)

    # 数据级 2：关键表最新 part mtime > 窗口起点（直接扫盘，只读）
    fresh: dict = {}
    if fresh_tables is not None:
        fresh = dict(fresh_tables)
    else:
        for table in KEY_TABLES:
            newest = _latest_part_mtime(Path(tdx_dir) / table)
            fresh[table] = {
                "latest_part_mtime": dt.datetime.fromtimestamp(newest).astimezone().isoformat(
                    timespec="seconds") if newest else "",
                "fresh": bool(newest and newest >= window_start.timestamp()),
            }
    evidence["fresh_tables"] = fresh
    fresh_count = sum(1 for item in fresh.values() if (item or {}).get("fresh"))
    evidence["fresh_table_count"] = fresh_count
    if fresh_tables is None and not fresh:
        unknown.append("tdx_dir_unreadable")
    elif fresh_count == 0:
        problems.append("no_part_mtime_after_window")

    if problems:
        return _gate("G1", "fail", evidence=evidence, reason="; ".join(problems))
    if unknown:
        return _gate("G1", "unknown", evidence=evidence, reason="; ".join(unknown))
    return _gate("G1", "pass", evidence=evidence,
                 reason="四批 rc=0 + rows_delta_total>0 + 关键表 part 已刷新")


# ---------------------------------------------------------------- G2
def load_probe_entries(night_date: dt.date, now: dt.datetime,
                       task_log: Path = PROBE_TASK_LOG,
                       jsonl_log: Path = PROBE_LOG) -> tuple[list[dict], str, str]:
    """严格窗口（night 19:20 之后）的 probe 记录，按时间升序。

    优先解析 probe 任务日志（完整 JSON，含 lock 列表）；不可用时回退 JSONL 摘要日志。
    """
    entries: list[dict] = []
    source = ""
    errors: list[str] = []
    text, err = _read_text(task_log)
    if text:
        source = str(task_log)
        for obj in _iter_json_objects(text):
            if not isinstance(obj, dict):
                continue
            ts = _parse_dt(obj.get("generated_at") or obj.get("ts"))
            if ts is None:
                continue
            if ts.date() != night_date or ts.time() < READY_AFTER:
                continue
            obj = dict(obj)
            obj["_ts"] = ts
            obj["ts"] = obj.get("generated_at") or obj.get("ts")
            entries.append(obj)
    else:
        errors.append("task_log:%s" % (err or "empty"))
    if not entries:
        text2, err2 = _read_text(jsonl_log)
        if text2:
            source = source or str(jsonl_log)
            for line in text2.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ts = _parse_dt(obj.get("ts") or obj.get("generated_at"))
                if ts is None or ts.date() != night_date or ts.time() < READY_AFTER:
                    continue
                obj = dict(obj)
                obj["_ts"] = ts
                obj.setdefault("fail_codes", [])
                entries.append(obj)
        else:
            errors.append("jsonl_log:%s" % (err2 or "empty"))
    entries.sort(key=lambda item: item["_ts"])
    return entries, source, "; ".join(errors)


def collect_g2(*, date: dt.date, now: dt.datetime, night_date: dt.date | None = None,
               entries: list[dict] | None = None,
               task_log: Path = PROBE_TASK_LOG,
               jsonl_log: Path = PROBE_LOG) -> dict:
    """G2：19:20+ 最近一条严格判定记录 code/fail/lock_hits。"""
    night = night_date or resolve_night_date(date, now)
    evidence: dict = {"night_date": night.isoformat(), "since": READY_AFTER.strftime("%H:%M")}
    source = ""
    errors = ""
    if entries is None:
        entries, source, errors = load_probe_entries(night, now, task_log, jsonl_log)
    else:
        # 注入数据同样执行严格窗口过滤（date==night 且 >=19:20），避免测试/复用绕过判据
        filtered = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ts = _parse_dt(entry.get("_ts") or entry.get("ts") or entry.get("generated_at"))
            if ts is None or ts.date() != night or ts.time() < READY_AFTER:
                continue
            entry = dict(entry)
            entry["_ts"] = ts
            filtered.append(entry)
        entries = sorted(filtered, key=lambda item: item["_ts"])
    evidence["source"] = source or "injected"
    if errors:
        evidence["source_errors"] = errors
    if not entries:
        return _gate("G2", "unknown", evidence=evidence,
                     reason="无 19:20 后 probe 严格判定记录（%s）" % (errors or "not_found"))
    latest = entries[-1]
    evidence["latest_ts"] = latest.get("ts")
    evidence["code"] = latest.get("code")
    evidence["fail"] = latest.get("fail")
    evidence["warn"] = latest.get("warn")
    evidence["fail_codes"] = latest.get("fail_codes") or []
    lock_rows = latest.get("lock")
    if isinstance(lock_rows, list):
        evidence["lock_hits"] = sum(1 for row in lock_rows
                                    if isinstance(row, dict) and row.get("exists"))
        lock_source = "task_log.lock"
    else:
        evidence["lock_hits"] = 1 if any("lock" in str(code) for code in
                                         evidence["fail_codes"]) else 0
        lock_source = "derived_from_fail_codes"
    evidence["lock_source"] = lock_source
    evidence["entries_checked"] = len(entries)
    problems = []
    if latest.get("code") != 0:
        problems.append("code=%s" % latest.get("code"))
    if latest.get("fail") not in (0, None):
        problems.append("fail=%s" % latest.get("fail"))
    if evidence["lock_hits"] != 0:
        problems.append("lock_hits=%s" % evidence["lock_hits"])
    if problems:
        return _gate("G2", "fail", evidence=evidence, reason="; ".join(problems))
    return _gate("G2", "pass", evidence=evidence,
                 reason="严格窗口 rc=0 / fail=0 / lock_hits=0")


# ---------------------------------------------------------------- G3
def collect_g3(*, date: dt.date, now: dt.datetime, registry_data: dict | None = None,
               registry_path: Path = REGISTRY,
               threshold: int = UNKNOWN_THRESHOLD) -> dict:
    """G3：qualification_status 分布，unknown<40 判过（清单 §1 G3 / K8）。"""
    evidence: dict = {"threshold": threshold}
    reg = registry_data
    if reg is None:
        reg, err = _read_json(registry_path)
        if reg is None:
            return _gate("G3", "unknown", evidence={"path": str(registry_path), "error": err},
                         reason="registry 不可读：%s" % (err or "missing"))
    evidence["path"] = str(registry_path)
    rows = [row for row in (reg.get("physical_tables") or []) if isinstance(row, dict)]
    dist = Counter(str(row.get("qualification_status") or "unknown") for row in rows)
    unknown = int(dist.get("unknown", 0))
    evidence["physical_count"] = len(rows)
    evidence["distribution"] = dict(sorted(dist.items()))
    evidence["unknown"] = unknown
    if not rows:
        return _gate("G3", "unknown", evidence=evidence, reason="registry.physical_tables 为空")
    if unknown >= threshold:
        return _gate("G3", "fail", evidence=evidence,
                     reason="unknown=%d >= 阈值 %d" % (unknown, threshold))
    return _gate("G3", "pass", evidence=evidence,
                 reason="unknown=%d < %d" % (unknown, threshold))


# ---------------------------------------------------------------- 任务类 Gate（G4/G7/G8/G9）
def _collect_task_gate(gate: str, spec: str, *, date: dt.date, now: dt.datetime,
                       tasks: dict | None, min_time: dt.time | None = None,
                       pass_verdict: str = "pass",
                       expected_risk_on_result: int | None = None,
                       extra_evidence: dict | None = None) -> dict:
    evidence: dict = {"task": spec, **(extra_evidence or {})}
    if tasks is None:
        tasks, err = query_tasks([spec])
        if err:
            return _gate(gate, "unknown", evidence=evidence, reason="task_query_failed: %s" % err)
    rec = (tasks or {}).get(spec)
    if not rec or not rec.get("found"):
        return _gate(gate, "unknown", evidence=evidence, reason="task_missing:%s" % spec)
    evidence.update({"state": rec.get("state"), "last_run": rec.get("last_run"),
                     "next_run": rec.get("next_run"),
                     "last_result": rec.get("last_result")})
    last_run = _task_last_run(rec)
    if last_run is None:
        return _gate(gate, "unknown", evidence=evidence, reason="last_run_missing")
    if last_run.date() != date:
        return _gate(gate, "fail", evidence=evidence,
                     reason="not_run_today(last_run=%s)" % rec.get("last_run"))
    if min_time is not None and last_run.time() < min_time:
        return _gate(gate, "fail", evidence=evidence,
                     reason="last_run_before_%s(%s)" % (min_time.strftime("%H:%M"),
                                                        rec.get("last_run")))
    result = rec.get("last_result")
    if expected_risk_on_result is not None and result == expected_risk_on_result:
        return _gate(gate, "expected-risk", evidence=evidence,
                     reason="rc=%s 为 accepted-risk（预期，不阻断）" % result)
    if result != 0:
        return _gate(gate, "fail", evidence=evidence, reason="last_result=%s" % result)
    return _gate(gate, pass_verdict, evidence=evidence, reason="rc=0 且今日已跑")


def _tail_lines(path: Path, limit: int = 5) -> list[str]:
    text, _ = _read_text(path)
    if not text:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def collect_g4(*, date, now, tasks=None, xmap_log: Path = XMAP_LOG) -> dict:
    return _collect_task_gate("G4", TASK_XMAP, date=date, now=now, tasks=tasks,
                              extra_evidence={"log": str(xmap_log),
                                              "log_tail": _tail_lines(xmap_log, 3)})


def collect_g7(*, date, now, tasks=None, old_task_disabled: bool | None = None) -> dict:
    extra: dict = {"old_task": TASK_NOTICE_OLD, "old_task_disabled": old_task_disabled}
    if tasks is None:
        tasks, err = query_tasks([TASK_NOTICE, TASK_NOTICE_OLD])
        if err:
            return _gate("G7", "unknown", evidence=extra, reason="task_query_failed: %s" % err)
    old = (tasks or {}).get(TASK_NOTICE_OLD) or {}
    extra["old_task_disabled"] = str(old.get("state") or "").startswith("Disabled")
    res = _collect_task_gate("G7", TASK_NOTICE, date=date, now=now, tasks=tasks,
                             min_time=dt.time(6, 45), extra_evidence=extra)
    if res["status"] in ("pass", "expected-risk") and not extra["old_task_disabled"]:
        res["notes"] = "新任务 rc=0；旧任务未 Disabled（管理员收口待办，见清单 §4.2/L2#4）"
    return res


def collect_g8(*, date, now, tasks=None, a3_log: Path = A3_LOG) -> dict:
    evidence = {"log": str(a3_log), "log_tail": _tail_lines(a3_log, 2)}
    if tasks is None:
        tasks, err = query_tasks([TASK_A3])
        if err:
            return _gate("G8", "unknown", evidence=evidence, reason="task_query_failed: %s" % err)
    rec = (tasks or {}).get(TASK_A3)
    if not rec or not rec.get("found"):
        return _gate("G8", "unknown", evidence=evidence, reason="task_missing:%s" % TASK_A3)
    evidence.update({"state": rec.get("state"), "last_run": rec.get("last_run"),
                     "last_result": rec.get("last_result")})
    last_run = _task_last_run(rec)
    if last_run is None or last_run.date() != date:
        return _gate("G8", "fail", evidence=evidence,
                     reason="not_run_today(last_run=%s)" % rec.get("last_run"))
    result = rec.get("last_result")
    if result == 1:
        return _gate("G8", "expected-risk", evidence=evidence,
                     reason="A3 canary rc=1 属 accepted-risk（裁定 B；8 处旧值未轮换不变）")
    if result == 0:
        return _gate("G8", "pass", evidence=evidence, reason="canary rc=0（轮换风险已消除）")
    return _gate("G8", "fail", evidence=evidence, reason="canary last_result=%s" % result)


def collect_g9(*, date, now, tasks=None,
               sentiment_path: Path = SENTIMENT_RESULT,
               jev_impact_path: Path = JEV_IMPACT,
               pushed_path: Path = SENTIMENT_PUSHED) -> dict:
    evidence: dict = {"products": {}}
    products = {"sentiment_bt_result.json": sentiment_path, "jev_impact.json": jev_impact_path}
    for label, path in products.items():
        try:
            mtime = dt.datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone()
        except OSError:
            evidence["products"][label] = {"path": str(path), "exists": False}
            continue
        age_h = (now - mtime).total_seconds() / 3600.0
        evidence["products"][label] = {
            "path": str(path), "exists": True, "mtime": _iso(mtime),
            "age_hours": round(age_h, 1), "fresh": age_h < SENTIMENT_MAX_AGE_H,
        }
    pushed = Path(pushed_path)
    push_today = False
    try:
        push_mtime = dt.datetime.fromtimestamp(pushed.stat().st_mtime).astimezone()
        push_today = push_mtime.date() == date
        evidence["pushed_file"] = {"path": str(pushed), "mtime": _iso(push_mtime),
                                   "push_today": push_today}
    except OSError:
        evidence["pushed_file"] = {"path": str(pushed), "exists": False, "push_today": False}
    res = _collect_task_gate("G9", TASK_SENT, date=date, now=now, tasks=tasks,
                             min_time=dt.time(7, 20), extra_evidence=evidence)
    if res["status"] != "pass":
        return res
    missing = [label for label, item in evidence["products"].items() if not item.get("exists")]
    stale = [label for label, item in evidence["products"].items()
             if item.get("exists") and not item.get("fresh")]
    problems = []
    if missing:
        problems.append("products_missing:%s" % ",".join(missing))
    if stale:
        problems.append("products_stale(>%dh):%s" % (SENTIMENT_MAX_AGE_H, ",".join(stale)))
    if push_today:
        problems.append("push_detected(today)")
    if problems:
        return _gate("G9", "fail", evidence=evidence, reason="; ".join(problems))
    return _gate("G9", "pass", evidence=evidence,
                 reason="rc=0 且两产物 <48h、无当日 push")


# ---------------------------------------------------------------- G5 / G6 / G10
def collect_g5(*, date, now, tasks=None, catalog_path: Path = CATALOG,
               registry_path: Path = REGISTRY, kg_log: Path = KG_LOG,
               kg_catalog_out: Path = KG_CATALOG_OUT) -> dict:
    evidence: dict = {"catalog": str(catalog_path), "registry": str(registry_path),
                      "log_tail": _tail_lines(kg_log, 4),
                      "catalog_build_tail": _tail_lines(kg_catalog_out, 3)}
    if tasks is None:
        tasks, err = query_tasks([TASK_KG])
        if err:
            return _gate("G5", "unknown", evidence=evidence, reason="task_query_failed: %s" % err)
    rec = (tasks or {}).get(TASK_KG)
    if not rec or not rec.get("found"):
        return _gate("G5", "unknown", evidence=evidence, reason="task_missing:%s" % TASK_KG)
    evidence.update({"state": rec.get("state"), "last_run": rec.get("last_run"),
                     "last_result": rec.get("last_result")})
    problems = []
    last_run = _task_last_run(rec)
    if rec.get("last_result") != 0:
        problems.append("task_last_result=%s" % rec.get("last_result"))
    if last_run is None or last_run.date() != date:
        problems.append("not_run_today(last_run=%s)" % rec.get("last_run"))
    catalog, cat_err = _read_json(catalog_path)
    if catalog is None:
        return _gate("G5", "unknown", evidence=evidence, reason="catalog 不可读：%s" % cat_err)
    try:
        cat_mtime = dt.datetime.fromtimestamp(Path(catalog_path).stat().st_mtime).astimezone()
    except OSError:
        cat_mtime = None
    evidence["catalog_mtime"] = _iso(cat_mtime)
    evidence["catalog_version"] = catalog.get("version")
    evidence["catalog_updated"] = catalog.get("updated")
    if cat_mtime is None or cat_mtime.date() != date:
        problems.append("catalog_not_updated_today")
    registry, reg_err = _read_json(registry_path)
    if registry is None:
        problems.append("registry_unreadable:%s" % reg_err)
    else:
        try:
            catalog_sha = _sha256_file(catalog_path)
        except OSError as exc:
            catalog_sha = ""
            problems.append("catalog_sha_error:%s" % exc)
        evidence["catalog_sha256"] = catalog_sha
        evidence["registry_catalog_sha256"] = registry.get("catalog_sha256")
        if catalog_sha and registry.get("catalog_sha256") != catalog_sha:
            problems.append("registry_sha_mismatch")
    if problems:
        return _gate("G5", "fail", evidence=evidence, reason="; ".join(problems))
    return _gate("G5", "pass", evidence=evidence,
                 reason="KgDailySync rc=0 + catalog 当日更新 + registry sha 对齐")


def _last_dw_log(path: Path) -> tuple[dict | None, str]:
    text, err = _read_text(path)
    if not text:
        return None, err or "empty"
    last = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            last = obj
    return last, "" if last else "no_json_line"


def collect_g6(*, date, now, tasks=None, snapshot_path: Path = SNAPSHOT,
               dw_log: Path = DW_LOG, catalog_path: Path = CATALOG) -> dict:
    evidence: dict = {"task": TASK_DW, "snapshot": str(snapshot_path),
                      "dw_log": str(dw_log)}
    if tasks is None:
        tasks, err = query_tasks([TASK_DW])
        if err:
            return _gate("G6", "unknown", evidence=evidence, reason="task_query_failed: %s" % err)
    rec = (tasks or {}).get(TASK_DW)
    if not rec or not rec.get("found"):
        return _gate("G6", "unknown", evidence=evidence, reason="task_missing:%s" % TASK_DW)
    evidence.update({"state": rec.get("state"), "last_run": rec.get("last_run"),
                     "last_result": rec.get("last_result")})
    problems = []
    missing = []
    last_run = _task_last_run(rec)
    if rec.get("last_result") != 0:
        problems.append("task_last_result=%s" % rec.get("last_result"))
    if last_run is None:
        problems.append("task_last_run_missing")
    elif last_run.date() != date or last_run.time() < dt.time(6, 40):
        problems.append("not_run_after_0640(last_run=%s)" % rec.get("last_run"))
    snap, snap_err = _read_json(snapshot_path)
    if snap is None:
        missing.append("snapshot_unreadable:%s" % snap_err)
    else:
        generated = _parse_dt(snap.get("generated_at"))
        evidence["snapshot_generated_at"] = snap.get("generated_at")
        evidence["snapshot_catalog"] = snap.get("catalog")
        if generated is None:
            problems.append("snapshot_generated_at_missing")
        elif generated.date() != date:
            problems.append("snapshot_not_today(generated_at=%s)" % snap.get("generated_at"))
    log_entry, log_err = _last_dw_log(dw_log)
    if log_entry is None:
        missing.append("dw_log_unreadable:%s" % log_err)
    else:
        evidence["dw_log_last"] = {key: log_entry.get(key) for key in
                                   ("tool", "finished_at", "generated_at", "ok", "rc",
                                    "catalog_version")}
        if log_entry.get("ok") is not True or log_entry.get("rc") != 0:
            problems.append("dw_log_not_ok(ok=%s,rc=%s)" % (log_entry.get("ok"),
                                                            log_entry.get("rc")))
        log_generated = _parse_dt(log_entry.get("generated_at"))
        if log_generated is None or log_generated.date() != date:
            problems.append("dw_log_not_today")
    catalog, cat_err = _read_json(catalog_path)
    if catalog is None:
        missing.append("catalog_unreadable:%s" % cat_err)
    else:
        current_version = catalog.get("version")
        evidence["catalog_version"] = current_version
        snap_version = ((snap or {}).get("catalog") or {}).get("version")
        evidence["snapshot_catalog_version"] = snap_version
        if snap is not None and (snap_version is None
                                 or snap_version != current_version):
            problems.append("catalog_version_mismatch(snapshot=%s/catalog=%s)"
                            % (snap_version, current_version))
    if problems:
        return _gate("G6", "fail", evidence=evidence, reason="; ".join(problems))
    if missing:
        return _gate("G6", "unknown", evidence=evidence,
                     reason="证据缺失（unknown，不硬编码判过/判败）：" + "; ".join(missing))
    return _gate("G6", "pass", evidence=evidence,
                 reason="06:40 后 rc=0 + snapshot 当日 + 日志 ok + catalog 版本一致")


def _default_kpi_builder(target_date: dt.date) -> dict:
    """导入 kpi_report.build_report（只读；不写文件）。"""
    import importlib.util

    tool = Path(__file__).resolve().parent / "kpi_report.py"
    spec = importlib.util.spec_from_file_location("kpi_report_for_acceptance", tool)
    if spec is None or spec.loader is None:
        raise RuntimeError("kpi_report.py 不可导入")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report(today=target_date)


def collect_g10(*, date, now, kpi_builder=None,
                min_ok: int = KPI_MIN_OK) -> dict:
    """G10：KPI 复跑（ok>=9、unknown=0、K3=ok）。"""
    evidence: dict = {"min_ok": min_ok}
    builder = kpi_builder or _default_kpi_builder
    try:
        report = builder(date)
    except Exception as exc:  # noqa: BLE001 — fail-visible
        return _gate("G10", "unknown", evidence=evidence,
                     reason="kpi_report 执行异常：%s: %s" % (type(exc).__name__, str(exc)[:200]))
    if not isinstance(report, dict):
        return _gate("G10", "unknown", evidence=evidence, reason="kpi_report 返回非 dict")
    summary = report.get("summary") or {}
    kpis = report.get("kpis") or {}
    k3 = (kpis.get("K3") or {}) if isinstance(kpis, dict) else {}
    evidence.update({
        "measured_at": report.get("measured_at"),
        "summary": summary,
        "kpi_status": {key: (val or {}).get("status") for key, val in
                       (kpis.items() if isinstance(kpis, dict) else [])},
        "k3": {key: k3.get(key) for key in ("status", "value", "evidence")},
    })
    problems = []
    if int(summary.get("ok") or 0) < min_ok:
        problems.append("ok=%s < %d" % (summary.get("ok"), min_ok))
    if int(summary.get("unknown") or 0) != 0:
        problems.append("unknown=%s != 0" % summary.get("unknown"))
    if k3.get("status") != "ok":
        problems.append("K3=%s（期 K3 转 ok：dw_snapshot 每日自动）" % k3.get("status"))
    if problems:
        return _gate("G10", "fail", evidence=evidence, reason="; ".join(problems))
    return _gate("G10", "pass", evidence=evidence,
                 reason="ok=%s>=%d、unknown=0、K3=ok" % (summary.get("ok"), min_ok))


COLLECTORS = {
    "G1": collect_g1, "G2": collect_g2, "G3": collect_g3, "G4": collect_g4,
    "G5": collect_g5, "G6": collect_g6, "G7": collect_g7, "G8": collect_g8,
    "G9": collect_g9, "G10": collect_g10,
}


# ---------------------------------------------------------------- 汇总
def collect_all(*, date: dt.date | None = None, now: dt.datetime | None = None,
                night_date: dt.date | None = None, overrides: dict | None = None,
                tasks: dict | None = None, kpi_builder=None) -> list[dict]:
    """逐 Gate 采集；overrides={gate: dict(status/evidence/reason)} 供测试/复用。"""
    date = date or _now().date()
    now = now or _now()
    night = night_date or resolve_night_date(date, now)
    overrides = overrides or {}
    gates: list[dict] = []
    for gate, _title in GATE_SPECS:
        if gate in overrides:
            override = overrides[gate] or {}
            gates.append(_gate(gate, str(override.get("status") or "unknown"),
                               evidence=override.get("evidence") or {},
                               reason=str(override.get("reason") or "")))
            continue
        fn = COLLECTORS[gate]
        try:
            kwargs: dict = {"date": date, "now": now}
            if gate in ("G1", "G2"):
                kwargs["night_date"] = night
            if gate in ("G1", "G4", "G5", "G6", "G7", "G8", "G9"):
                kwargs["tasks"] = tasks
            if gate == "G10":
                kwargs["kpi_builder"] = kpi_builder
            gates.append(fn(**kwargs))
        except Exception as exc:  # noqa: BLE001 — 单 Gate 失败不中断（fail-visible）
            gates.append(_gate(gate, "unknown",
                               reason="采集异常：%s: %s" % (type(exc).__name__, str(exc)[:200])))
    return gates


def build_report(gates: list[dict], *, date: dt.date, now: dt.datetime,
                 night_date: dt.date | None = None) -> dict:
    counts = Counter(str(gate.get("status")) for gate in gates)
    if counts.get("fail"):
        verdict = "FAIL"
    elif counts.get("unknown"):
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    return {
        "tool": TOOL,
        "version": VERSION,
        "generated_at": _iso(now),
        "date": date.isoformat(),
        "night_date": (night_date or resolve_night_date(date, now)).isoformat(),
        "verdict": verdict,
        "counts": {
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "expected-risk": counts.get("expected-risk", 0),
            "unknown": counts.get("unknown", 0),
            "total": len(gates),
        },
        "definitions_source": "deliverables/2026-11-02_S3-08终验收_检查清单_v1.md §1（G1–G10）",
        "gates": gates,
        "environment": {
            "repo": str(REPO),
            "data": str(DATA),
            "host": "windows",
            "mode": "read-only（不触发任务/不改数据/不重启）",
        },
        "limitations": [
            "G1 数据级 rows 增量与 G2 同源（TdxSyncProbe 严格窗口记录）；probe 未装/未跑时 G1/G2=unknown（不硬编码判过）",
            "G4–G9 以「LastRunTime.date == 验收日」判明早窗口是否跑过；跨日长任务（NoticeBackfill PT8H）以 LastResult 为准",
            "G10 导入 kpi_report.build_report()（只读，不写文件）；K10 的 warn 不计失败（清单口径）",
            "expected-risk 仅 G8（A3 canary rc=1，裁定 B）计入；其它 Gate 不接受 expected-risk",
        ],
    }


_ICON = {"pass": "✅", "fail": "🔴", "expected-risk": "🟡", "unknown": "❔"}


def render_markdown(report: dict) -> str:
    gates = report.get("gates") or []
    lines = [
        "# S3-08 终验收一键检查（%s）" % report.get("generated_at"),
        "",
        "> 工具：`tools/datawarehouse/final_acceptance.py %s` ｜ 定义：%s ｜ 只读（不触发任务/不改数据/不重启）"
        % (report.get("version"), report.get("definitions_source")),
        "",
        "## 总判定：**%s** %s" % (report.get("verdict"), _ICON.get(
            "pass" if report.get("verdict") == "PASS" else
            "fail" if report.get("verdict") == "FAIL" else "unknown", "")),
        "",
        "- 验收日：%s ｜ 今晚（T 窗）：%s ｜ 采集：%s" % (
            report.get("date"), report.get("night_date"), report.get("generated_at")),
        "- 计数：pass=%s / fail=%s / expected-risk=%s / unknown=%s（共 %s Gate）" % (
            (report.get("counts") or {}).get("pass"),
            (report.get("counts") or {}).get("fail"),
            (report.get("counts") or {}).get("expected-risk"),
            (report.get("counts") or {}).get("unknown"),
            (report.get("counts") or {}).get("total")),
        "",
        "| Gate | 内容 | 状态 | 判定依据/证据 |",
        "|---|---|---|---|",
    ]
    for gate in gates:
        evidence = gate.get("evidence") or {}
        if gate.get("gate") in ("G1",):
            brief = "tasks=%s；rows_delta_total=%s；fresh_tables=%s" % (
                len(evidence.get("tasks") or {}), evidence.get("rows_delta_total"),
                evidence.get("fresh_table_count"))
        elif gate.get("gate") == "G2":
            brief = "ts=%s；code=%s；fail=%s；lock_hits=%s" % (
                evidence.get("latest_ts"), evidence.get("code"), evidence.get("fail"),
                evidence.get("lock_hits"))
        elif gate.get("gate") == "G3":
            brief = "unknown=%s / threshold=%s；dist=%s" % (
                evidence.get("unknown"), evidence.get("threshold"),
                str(evidence.get("distribution")))
        elif gate.get("gate") == "G6":
            brief = "last_run=%s；snapshot=%s；log_ok=%s；catalog=%s" % (
                (evidence.get("last_run") or "-"), evidence.get("snapshot_generated_at"),
                (evidence.get("dw_log_last") or {}).get("ok"),
                evidence.get("snapshot_catalog_version"))
        elif gate.get("gate") == "G10":
            brief = "kpi=%s；K3=%s" % (str(evidence.get("summary")),
                                       (evidence.get("k3") or {}).get("status"))
        else:
            brief = "last_run=%s；last_result=%s；state=%s" % (
                evidence.get("last_run"), evidence.get("last_result"), evidence.get("state"))
        lines.append("| %s | %s | %s %s | %s |" % (
            gate.get("gate"), gate.get("title"), _ICON.get(gate.get("status"), ""),
            gate.get("status"), (gate.get("reason") or "").replace("|", "/")[:220] + "；" + brief))
    lines += ["", "## 说明", ""]
    for note in report.get("limitations") or []:
        lines.append("- %s" % note)
    lines += ["", "## 明早 09:00 复跑", "",
              "```powershell",
              "cd C:\\项目\\老田的AI助手",
              "& 'C:\\项目\\py312\\python.exe' -X utf8 tools\\datawarehouse\\final_acceptance.py --date 20261103",
              "# 退出码 0=PASS / 1=FAIL / 2=UNKNOWN；JSON=research/reports/final_acceptance_20261103.json",
              "```", ""]
    return "\n".join(lines)


def write_outputs(report: dict, *, json_path: Path, md_path: Path | None,
                  json_only: bool = False) -> dict:
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    written = {"json": str(json_path), "md": None}
    if not json_only and md_path is not None:
        md_path = Path(md_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
        written["md"] = str(md_path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S3-08 终验收一键检查 G1–G10（只读）")
    ap.add_argument("--date", default="", help="验收日 YYYYMMDD（默认今天；明早用 20261103）")
    ap.add_argument("--night-date", default="", help="T 窗日期 YYYYMMDD（默认按规则推断）")
    ap.add_argument("--json-out", default="", help="JSON 输出路径（默认 research/reports/final_acceptance_<date>.json）")
    ap.add_argument("--md-out", default="", help="Markdown 输出路径（默认 .md）")
    ap.add_argument("--json-only", action="store_true", help="只写 JSON、不写/不打印 Markdown")
    ap.add_argument("--now", default="", help="覆盖当前时间（ISO，复核/演练用）")
    args = ap.parse_args(argv)
    try:
        date = _parse_date(args.date) if args.date else _now().date()
        night_date = _parse_date(args.night_date) if args.night_date else None
    except ValueError as exc:
        print("参数错误：%s（--date/--night-date 需 YYYYMMDD）" % exc, file=sys.stderr)
        return 3
    now = _parse_dt(args.now) if args.now else _now()
    if now is None:
        print("参数错误：--now 需 ISO 时间", file=sys.stderr)
        return 3
    night = night_date or resolve_night_date(date, now)
    gates = collect_all(date=date, now=now, night_date=night)
    report = build_report(gates, date=date, now=now, night_date=night)
    stamp = date.strftime("%Y%m%d")
    json_path = Path(args.json_out) if args.json_out else REPORTS / ("final_acceptance_%s.json" % stamp)
    md_path = Path(args.md_out) if args.md_out else json_path.with_suffix(".md")
    written = write_outputs(report, json_path=json_path, md_path=md_path,
                            json_only=args.json_only)
    if args.json_only:
        _safe_print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _safe_print(render_markdown(report))
    print("FINAL_ACCEPTANCE verdict=%s counts=%s -> %s" % (
        report["verdict"], json.dumps(report["counts"], ensure_ascii=False),
        written["json"]), flush=True)
    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}.get(report["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
