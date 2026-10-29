# -*- coding: utf-8 -*-
"""R6 事件总线数据面（8091 侧 ingest/query，Gate 0 前置离线开发 M1 骨干）。

设计定稿：docs/design/2026-10-18_事件总线R6设计稿.md（§2 schema / §3 manifest
即白名单 / §7 风险处置）。口径对齐判例：api_flow.RERUN_OK 白名单服务端强制 +
拒绝报因 + caller 归因（L79-87/L664-666）；api_brief 模块自包含 + fail-visible。

纯函数层（测试/复用入口，Handler 只是薄壳）：
- load_manifest(manifest_path)      → 现读 manifest（不过缓存，§3.3 ①真自动）
- compute_event_id(source, type, run_key, ts)
                                    → sha256(source|type|run_key|ts_bucket) 截 24 hex
- ingest_event(body, ...)           → (http_code, resp_dict)
- query_events(source, type, since, until, limit, ...) → resp_dict（只读）

红线（设计稿 §0/§7）：
- 只落账不派单：ingest 零回写执行面，零调度决策（无控制回路）；
- 白名单即配置：双白名单交叉（source ∈ manifest.sources 且 type ∈ 该 source
  的 event_types[]）服务端强制，不过 → 403 + caller 归因落 rejected.jsonl +
  报因含当前合法集摘要；
- 幂等键最后防线（§7 风险 2）：24h 滚动去重索引（内存 dict + 落盘
  dedup_index.jsonl），同键重复 → 200 + dedup=true 不落账；
- 风暴折叠（§7 风险 1）：同 source+type 每分钟 N=30 上限，超出折叠为一条
  summary 事件（payload.count 计数，不丢量只丢条；同窗口内 summary 原地合并
  ——append-only 的唯一显式特例，仅风暴期 summary 行自身被重写）；
- append-only jsonl 落账：portal/data/events/events_YYYY-MM.jsonl（月轮转，
  与 api_brief 读 briefs 同款布局），UTF-8 / ensure_ascii=False；
- fail-visible：恒返回 (code, resp)，绝不向调用方抛异常；
- 纯函数级可测：manifest 路径 / events 目录全部参数注入，测试零真实文件。

portal_api.py 挂载补丁（Gate 0 后由主会话执行，共 4 行，M1 禁止真改）：
    import api_events  # R6 事件总线 /api/events/*（联动层三期 R6，Gate 0 后挂载）
    # ① Handler 多继承基类链尾追加: , api_events.Handler
    # ② do_POST 路由段加: if path == "/api/events/ingest": self._events_ingest(raw); return
    # ③ do_GET 路由段加: if path == "/api/events/query": self._events_query(qs); return
"""

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 事件落账目录（M1 约定：portal/data/events/，Gate 0 后才写真产线）
EVENTS_DIR = _REPO_ROOT / "portal" / "data" / "events"

#: manifest 白名单文件（每次 ingest 现读，不过缓存）
MANIFEST_PATH = Path(__file__).resolve().parent / "event_manifest.json"

SCHEMA_VER = "r6.v1"
#: severity 枚举（§2.1）
SEVERITIES = ("P0", "P1", "P2")
#: type 枚举冻结于 M1（§2.2，扩枚举=改 manifest+评审门）
TYPE_ENUM = frozenset([
    "task_run", "task_fail", "task_recovered", "task_recover_failed",
    "brief_ready", "artifact_ready", "alert_open", "alert_close",
    "rerun_refused",
])
#: severity 严重度序（折叠 summary 取最严重）
_SEV_RANK = {"P2": 0, "P1": 1, "P0": 2}

#: caller 归因口径（§2.1，循 K-ORG-109：task-*/hermes-*/ops-*）
_CALLER_RE = re.compile(r"^(task|hermes|ops)-\S+$")
#: source slug（§2.1）
_SOURCE_RE = re.compile(r"^[a-z0-9_]+$")

#: 风暴折叠阈值（§7 风险 1：同 source+type 每分钟 N=30）
FOLD_LIMIT = 30
#: 幂等去重窗口（§7 风险 2：24h 滚动）
DEDUP_TTL = timedelta(hours=24)

#: 落账月文件 / 去重索引 / 拒绝归因账（同目录布局）
LEDGER_FMT = "events_{}.jsonl"
DEDUP_FILE = "dedup_index.jsonl"
REJECT_FILE = "rejected.jsonl"

#: 落账字段白名单（信封投影，多余键剥除——防账面漂移）
_ENVELOPE_FIELDS = ("event_id", "type", "source", "ts", "caller", "payload",
                    "schema_ver")

_STATE_LOCK = threading.Lock()
# 进程内状态（按 events_dir 隔离，测试可多目录并存）：
#   _DEDUP[dir][event_id] = 索引写入时刻 epoch（24h 过期）
#   _FOLD[dir][(source,type,minute)] = {"summary": {...}, "idx": 行号}
_DEDUP = {}
_FOLD = {}


# ----------------------------------------------------------------------
# manifest（白名单即配置，现读不过缓存）
# ----------------------------------------------------------------------

def load_manifest(manifest_path=None):
    """现读 event_manifest.json；坏/缺 → ValueError（fail-visible 上抛给 ingest）。"""
    p = Path(manifest_path) if manifest_path else MANIFEST_PATH
    with open(p, "r", encoding="utf-8") as fh:
        m = json.load(fh)
    if not isinstance(m, dict) or not isinstance(m.get("sources"), list):
        raise ValueError("event_manifest 结构非法（缺 sources[]）")
    return m


def _source_entry(manifest, source):
    """取 source 条目；未注册/enabled=false → None。"""
    for s in manifest.get("sources", []):
        if isinstance(s, dict) and s.get("source") == source:
            return s if s.get("enabled") else None
    return None


# ----------------------------------------------------------------------
# 幂等键（§2.1：sha256(source|type|run_key|ts_bucket) 截 24 hex）
# ----------------------------------------------------------------------

def compute_event_id(source, event_type, run_key, ts):
    """ts_bucket = 事件自然桶 = ts 的日期段（+08:00 本地日，如 '2026-10-18'）。

    日桶口径：重跑产生新 run_key → 合法新事件；同 run_key 同日重试 → 幂等去重。
    """
    ts_bucket = str(ts)[:10]
    raw = "%s|%s|%s|%s" % (source, event_type, run_key, ts_bucket)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# ----------------------------------------------------------------------
# 去重索引（内存 dict + 落盘 dedup_index.jsonl，24h 滚动）
# ----------------------------------------------------------------------

def _dedup_path(events_dir):
    return Path(events_dir) / DEDUP_FILE


def _load_dedup(events_dir, now_epoch):
    """载入该目录的 24h 内索引（懒加载一次 + 落盘行恢复，进程重启不失效）。"""
    key = str(events_dir)
    if key in _DEDUP:
        return _DEDUP[key]
    idx = {}
    try:
        with open(_dedup_path(events_dir), "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                    eid, rec_at = rec.get("event_id"), float(rec.get("recorded_at", 0))
                except (ValueError, TypeError):
                    continue
                if rec_at + DEDUP_TTL.total_seconds() > now_epoch:
                    idx[eid] = rec_at
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_events.py", exc)
    _DEDUP[key] = idx
    return idx


def _record_dedup(events_dir, event_id, now_epoch):
    """内存记 + 追加落盘索引行（断电可恢复）。"""
    key = str(events_dir)
    idx = _DEDUP.setdefault(key, {})
    # 顺手清过期（滚动窗口）
    cutoff = now_epoch - DEDUP_TTL.total_seconds()
    for k in [k for k, t in idx.items() if t <= cutoff]:
        idx.pop(k, None)
    idx[event_id] = now_epoch
    try:
        Path(events_dir).mkdir(parents=True, exist_ok=True)
        with open(_dedup_path(events_dir), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_id": event_id,
                                 "recorded_at": now_epoch},
                                ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_events.py", exc)


def reset_state(events_dir=None):
    """清进程内去重/折叠状态（仅测试用；events_dir=None 清全部）。"""
    with _STATE_LOCK:
        if events_dir is None:
            _DEDUP.clear()
            _FOLD.clear()
        else:
            _DEDUP.pop(str(events_dir), None)
            _FOLD.pop(str(events_dir), None)


# ----------------------------------------------------------------------
# 信封校验（§2.1）
# ----------------------------------------------------------------------

def _parse_ts(ts):
    """ISO8601 且必须 +08:00 时区（§2.1 硬约束）→ datetime；非法返回 None。"""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.utcoffset() != timedelta(hours=8):
        return None
    return dt


def validate_envelope(body):
    """信封结构校验 → 错误串列表（空 = 通过）。只校验结构，白名单在 ingest。"""
    errs = []
    if not isinstance(body, dict):
        return ["body 非对象"]
    if body.get("schema_ver") != SCHEMA_VER:
        errs.append("schema_ver 必须为 %s" % SCHEMA_VER)
    if not isinstance(body.get("type"), str) or not body.get("type"):
        errs.append("type 缺失或非字符串")
    src = body.get("source")
    if not isinstance(src, str) or not _SOURCE_RE.match(src):
        errs.append("source 必须匹配 ^[a-z0-9_]+$")
    if _parse_ts(body.get("ts")) is None:
        errs.append("ts 必须为 ISO8601 +08:00 时刻")
    caller = body.get("caller")
    if not isinstance(caller, str) or not _CALLER_RE.match(caller):
        errs.append("caller 必须为 task-*/hermes-*/ops-* 归因")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        errs.append("payload 必须为对象")
    else:
        if not str(payload.get("task") or "").strip():
            errs.append("payload.task 必填（计划任务名）")
        if payload.get("severity") not in SEVERITIES:
            errs.append("payload.severity 必须为 P0/P1/P2 之一")
    return errs


# ----------------------------------------------------------------------
# 落账（append-only jsonl，月轮转）
# ----------------------------------------------------------------------

def _ledger_path(events_dir, ts):
    return Path(events_dir) / (LEDGER_FMT.format(str(ts)[:7]))


def _append_line(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _log_rejection(events_dir, caller, reason, body):
    """403/422 的 caller 归因落账（api_flow 拒绝归因同款精神，独立账面）。"""
    try:
        _append_line(Path(events_dir) / REJECT_FILE, {
            "rejected_at": datetime.now(timezone(timedelta(hours=8)))
                              .isoformat(timespec="seconds"),
            "caller": (caller or "")[:64],
            "reason": reason,
            "body": body if isinstance(body, dict) else None,
        })
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_events.py", exc)


def _lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _rewrite_line(path, idx, obj):
    """同窗口 summary 原地合并（append-only 唯一特例，见模块 docstring）。"""
    lines = _lines(path)
    if 0 <= idx < len(lines):
        lines[idx] = json.dumps(obj, ensure_ascii=False) + "\n"
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(str(tmp), str(path))


# ----------------------------------------------------------------------
# ingest（§2.3 双白名单 + §7 风险 1/2 节流幂等）
# ----------------------------------------------------------------------

def ingest_event(body, manifest_path=None, events_dir=None, now=None):
    """事件上报入口 → (http_code, resp)。

    处理序：信封校验(400) → 双白名单(403+归因落账) → 幂等键重算(422) →
    24h 去重(200 dedup=true) → 风暴折叠(折叠进 summary) → append-only 落账(200)。
    now：注入当前时刻（epoch 秒，测试用）；缺省取系统时间。
    """
    try:
        return _ingest(body, manifest_path, events_dir, now)
    except Exception as exc:  # manifest 坏/盘故障等 → fail-visible 500
        return 500, {"ok": False, "error": "ingest 内部失败: %r" % exc,
                     "schema_ver": SCHEMA_VER}


def _ingest(body, manifest_path, events_dir, now):
    d = Path(events_dir) if events_dir else EVENTS_DIR
    errs = validate_envelope(body)
    if errs:
        return 400, {"ok": False, "error": "信封校验失败",
                     "details": errs, "schema_ver": SCHEMA_VER}
    caller = body["caller"]
    source, etype = body["source"], body["type"]
    payload = body["payload"]

    # 双白名单交叉（§2.3）：manifest 现读，服务端强制
    manifest = load_manifest(manifest_path)
    entry = _source_entry(manifest, source)
    if entry is None:
        allowed = sorted(s.get("source", "") for s in manifest.get("sources", [])
                         if isinstance(s, dict) and s.get("enabled"))
        reason = "source 未注册于 event_manifest（白名单拒绝）"
        _log_rejection(d, caller, reason, body)
        return 403, {"ok": False, "error": reason, "caller": caller,
                     "allowed_sources": allowed, "schema_ver": SCHEMA_VER}
    if etype not in entry.get("event_types", []):
        allowed_t = sorted(entry.get("event_types", []))
        reason = "type %s 越权：不在 source %s 的 event_types（双白名单交叉拒绝）" % (
            etype, source)
        _log_rejection(d, caller, reason, body)
        return 403, {"ok": False, "error": reason, "caller": caller,
                     "source": source, "allowed_types": allowed_t,
                     "schema_ver": SCHEMA_VER}

    # 幂等键服务端重算（§2.1）：客户端不一致 → 422
    run_key = str(payload.get("run_key", "") or "")
    event_id = compute_event_id(source, etype, run_key, body["ts"])
    client_id = body.get("event_id")
    if client_id is not None and client_id != event_id:
        reason = "event_id 与服务端重算不一致（幂等键 sha256(source|type|run_key|ts_bucket) 截 24 hex）"
        _log_rejection(d, caller, reason, body)
        return 422, {"ok": False, "error": reason, "caller": caller,
                     "expected_event_id": event_id, "schema_ver": SCHEMA_VER}

    now_epoch = float(now) if now is not None else datetime.now().timestamp()
    envelope = {
        "event_id": event_id, "type": etype, "source": source,
        "ts": body["ts"], "caller": caller,
        "payload": payload, "schema_ver": SCHEMA_VER,
    }

    with _STATE_LOCK:
        # 24h 滚动去重（§7 风险 2 最后防线）：重复 → 200 dedup=true 不落账
        idx = _load_dedup(d, now_epoch)
        prev = idx.get(event_id)
        if prev is not None and prev + DEDUP_TTL.total_seconds() > now_epoch:
            return 200, {"ok": True, "dedup": True, "event_id": event_id,
                         "first_recorded_at": prev, "schema_ver": SCHEMA_VER}

        # 风暴折叠（§7 风险 1）：同 source+type 每分钟 >N=30 → summary。
        # 窗口计数以账面普通条数推导（st.n 首见时从当月账回填，进程重启不漂移）。
        minute = str(body["ts"])[:16]
        fold_key = (source, etype, minute)
        folds = _FOLD.setdefault(str(d), {})
        st = folds.get(fold_key)
        if st is None:
            st = folds[fold_key] = {"n": _count_window(d, source, etype, minute),
                                    "summary": None, "idx": -1}
        if st["n"] >= FOLD_LIMIT:
            resp = _fold_into_summary(d, st, fold_key, envelope)
            _record_dedup(d, event_id, now_epoch)
            return resp

        # append-only 落账
        _append_line(_ledger_path(d, body["ts"]), envelope)
        st["n"] += 1
        _record_dedup(d, event_id, now_epoch)
    resp = {"ok": True, "dedup": False, "folded": False,
            "event_id": event_id, "schema_ver": SCHEMA_VER}
    return 200, resp


def _count_window(events_dir, source, etype, minute):
    """同 (source,type,分钟) 窗口已落账的普通事件条数（读当月账面推导）。"""
    p = _ledger_path(events_dir, minute + ":00+08:00")
    n = 0
    for ln in _lines(p):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if (rec.get("source") == source and rec.get("type") == etype
                and str(rec.get("ts", ""))[:16] == minute
                and rec.get("payload", {}).get("folded") is not True):
            n += 1
    return n


def _fold_into_summary(events_dir, st, fold_key, envelope):
    """折叠：同窗口 summary 一条，payload.count 累计（不丢量只丢条）。"""
    source, etype, minute = fold_key
    if st["summary"] is None:
        run_key = "fold:%s" % minute
        summary = {
            "event_id": compute_event_id(source, etype, run_key, minute + ":00+08:00"),
            "type": etype, "source": source,
            "ts": minute + ":00+08:00", "caller": "hermes-eventbus",
            "payload": {
                "task": envelope["payload"].get("task", ""),
                "severity": envelope["payload"].get("severity", "P2"),
                "run_key": run_key,
                "summary": "风暴折叠：同 source+type 每分钟超 %d 条" % FOLD_LIMIT,
                "folded": True, "count": 1,
            },
            "schema_ver": SCHEMA_VER,
        }
        _append_line(_ledger_path(events_dir, summary["ts"]), summary)
        st["idx"] = len(_lines(_ledger_path(events_dir, summary["ts"]))) - 1
        st["summary"] = summary
        return 200, {"ok": True, "dedup": False, "folded": True,
                     "event_id": summary["event_id"],
                     "fold_count": 1, "schema_ver": SCHEMA_VER}
    # 同窗口后续折叠 → 原地合并计数 + severity 取最严重
    summary = st["summary"]
    summary["payload"]["count"] += 1
    old, new = summary["payload"]["severity"], envelope["payload"]["severity"]
    if _SEV_RANK.get(new, 0) > _SEV_RANK.get(old, 0):
        summary["payload"]["severity"] = new
    _rewrite_line(_ledger_path(events_dir, summary["ts"]), st["idx"], summary)
    return 200, {"ok": True, "dedup": False, "folded": True,
                 "event_id": summary["event_id"],
                 "fold_count": summary["payload"]["count"],
                 "schema_ver": SCHEMA_VER}


# ----------------------------------------------------------------------
# query（只读投影）
# ----------------------------------------------------------------------

def query_events(source=None, event_type=None, since=None, until=None,
                 limit=50, events_dir=None):
    """按 source/type/时间窗只读查询（新在前）。fail-visible：坏行跳过计数。"""
    d = Path(events_dir) if events_dir else EVENTS_DIR
    try:
        n = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        n = 50
    meta = {"limit": n, "files_scanned": 0, "skipped_bad": 0}
    items = []
    if not d.is_dir():
        return {"ok": True, "items": [], "count": 0, "meta": meta}
    for f in sorted(d.glob("events_*.jsonl"), reverse=True):
        if f.name in (DEDUP_FILE, REJECT_FILE) or not f.name.startswith("events_"):
            continue
        meta["files_scanned"] += 1
        for ln in reversed(_lines(f)):  # 行倒序 = 新在前
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                meta["skipped_bad"] += 1
                continue
            if not isinstance(rec, dict):
                meta["skipped_bad"] += 1
                continue
            if source and rec.get("source") != source:
                continue
            if event_type and rec.get("type") != event_type:
                continue
            ts = str(rec.get("ts", ""))
            if since and ts < str(since):
                continue
            if until and ts > str(until):
                continue
            items.append({k: rec.get(k) for k in _ENVELOPE_FIELDS})
            if len(items) >= n:
                return {"ok": True, "items": items, "count": len(items),
                        "meta": meta}
    return {"ok": True, "items": items, "count": len(items), "meta": meta}


# ----------------------------------------------------------------------
# Handler mixin（portal_api.Handler 多继承薄壳；挂载片段见模块 docstring）
# ----------------------------------------------------------------------

class Handler:
    """portal_api.Handler 多继承 mixin（Gate 0 后挂载 /api/events/*）。"""

    def _events_ingest(self, raw):
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = None  # 坏 JSON → 统一走 400 信封校验路径
        code, resp = ingest_event(body)
        self._json(resp, code)

    def _events_query(self, qs):
        p = dict(part.split("=", 1) for part in (qs or "").split("&") if "=" in part)
        resp = query_events(
            source=p.get("source") or None,
            event_type=p.get("type") or None,
            since=p.get("since") or None,
            until=p.get("until") or None,
            limit=p.get("limit", 50),
        )
        self._json(resp)
