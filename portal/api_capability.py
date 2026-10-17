# -*- coding: utf-8 -*-
"""M3-P5 能力看板只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/capability/board
    → 聚合统一产物登记仓（artifacts.db）+ 审批注册表（approvals.db）
    的只读投影，回答总纲验收句五问的「看板版」：
    来源（source_system/source_ref）、审查（凭据 content_hash 绑定率）、
    版本（revision/supersede 链组）、负责人（produced_by）、覆盖
    （各产线登记/凭据计数）。

只读纪律（沿 replay_query._load_artifacts 判例）：
- mode=ro SQL 直连，不经 Store 构造（建库写副作用）——库未建时
  返回 ledger_exists=false 的空投影而非报错（看板是观测面）；
- 无写路径、无缓存（低频直读）；fail loud 500 由 _json 上层兜底。

凭据 content_hash 绑定率（M3-P3 口径）：
- bound    = content_hash != ''（新口径凭据，批 A 发 B 可检测）；
- legacy   = content_hash == ''（旧凭据跳过校验，零迁移不回填）；
- rate     = bound / total，legacy 不计入分母扣减——率只描述
  新口径渗透度，看板注释注明 legacy 属固有历史而非缺陷。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 产线覆盖清单（source_system / target_type 前缀 → 展示名）。
# 新域上线在此追加一行即可，缺位计 0——看板宁可显示 0 也不静默漏行。
PRODUCTION_LINES = [
    ("gzh", "自媒体产线"),
    ("pitch", "路演 PPT 产线"),
    ("dev", "软件产线"),
    ("skill", "Skill 产线"),
    ("intel", "情报裁决产线"),
]


def _ap_db(rel: str) -> Path:
    return _REPO_ROOT / "agent_platform" / "data" / rel


def _ro_rows(db: Path, sql: str, args=()):
    """mode=ro 直连取行（库未建 → None，上层给 ledger_exists=false）。"""
    if not db.exists():
        return None
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True,
                          timeout=30)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _artifact_projection(limit_recent: int = 30) -> dict:
    rows = _ro_rows(
        _ap_db("artifacts/artifacts.db"),
        "SELECT artifact_id, kind, path, content_hash, fact_hash,"
        " revision, supersedes, workitem_id, source_system, source_ref,"
        " produced_by, trace_id, registered_at"
        " FROM artifacts ORDER BY registered_at, rowid")
    if rows is None:
        return {"ledger_exists": False, "total": 0, "by_source": {},
                "by_kind": {}, "superseded_count": 0,
                "recent": [], "supersede_chains": []}
    arts = [dict(r) for r in rows]
    by_source, by_kind = {}, {}
    for a in arts:
        src = a["source_system"] or "(unspecified)"
        by_source[src] = by_source.get(src, 0) + 1
        by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1

    # supersede 链组：沿 supersedes 指针从链尾（无人指向它）回溯。
    # 只输出 length>1 的链（单条目无版本演进，不值得占看板）。
    superseded_by = {}  # old_id -> [new_id, ...]
    for a in arts:
        if a["supersedes"]:
            superseded_by.setdefault(a["supersedes"], []).append(
                a["artifact_id"])
    heads = [a["artifact_id"] for a in arts
             if a["artifact_id"] not in superseded_by]
    chains = []
    for head in heads:
        chain, seen, cur = [], set(), head
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            prev = next((a["supersedes"] for a in arts
                         if a["artifact_id"] == cur), "")
            cur = prev or None
        if len(chain) > 1:
            chains.append(list(reversed(chain)))  # 旧→新（与 store.chain 同口径）
    chains.sort(key=len, reverse=True)

    recent = list(reversed(arts[-limit_recent:]))
    return {
        "ledger_exists": True,
        "total": len(arts),
        "by_source": dict(sorted(by_source.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "superseded_count": sum(1 for a in arts if a["supersedes"]),
        "recent": [
            {"artifact_id": a["artifact_id"], "kind": a["kind"],
             "path": a["path"],
             "content_hash": (a["content_hash"] or a["fact_hash"] or ""),
             "revision": a["revision"], "supersedes": a["supersedes"],
             "workitem_id": a["workitem_id"],
             "source_system": a["source_system"],
             "source_ref": a["source_ref"],
             "produced_by": a["produced_by"],
             "trace_id": a["trace_id"],
             "registered_at": a["registered_at"]}
            for a in recent],
        "supersede_chains": chains,
    }


def _approval_projection() -> dict:
    rows = _ro_rows(
        _ap_db("approvals/approvals.db"),
        "SELECT approval_id, target_type, target_id, from_status,"
        " to_status, decision, decided_by, trace_id, content_hash,"
        " decided_at FROM approvals ORDER BY decided_at, rowid")
    if rows is None:
        return {"ledger_exists": False, "total": 0,
                "content_hash_bound": 0, "content_hash_binding_rate": 0.0,
                "legacy_unbound": 0, "by_target_type": {},
                "by_decision": {}, "recent": []}
    aps = [dict(r) for r in rows]
    by_type, by_decision, by_prefix = {}, {}, {}
    bound = 0
    for r in aps:
        by_type[r["target_type"]] = by_type.get(r["target_type"], 0) + 1
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
        # 产线归属：target_type 精确匹配 + target_id 'domain:ref' 前缀
        # 兜底（30 条 workitem 型凭据的产线归属全靠后者）
        prefix = str(r["target_id"]).partition(":")[0]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        if r["content_hash"]:
            bound += 1
    total = len(aps)
    return {
        "ledger_exists": True,
        "total": total,
        "content_hash_bound": bound,
        "content_hash_binding_rate": round(bound / total, 4) if total else 0.0,
                "legacy_unbound": total - bound,
                "by_target_type": dict(sorted(by_type.items())),
                "by_target_prefix": dict(sorted(by_prefix.items())),
        "by_decision": dict(sorted(by_decision.items())),
        "recent": [
            {"approval_id": r["approval_id"],
             "target_type": r["target_type"], "target_id": r["target_id"],
             "from_status": r["from_status"], "to_status": r["to_status"],
             "decision": r["decision"], "decided_by": r["decided_by"],
             "content_hash": r["content_hash"],
             "trace_id": r["trace_id"], "decided_at": r["decided_at"]}
            for r in reversed(aps[-30:])],
    }


def build_capability_board() -> dict:
    """看板聚合主入口（供 mixin 与测试共用）。"""
    artifacts = _artifact_projection()
    approvals = _approval_projection()

    # 产线覆盖：登记仓按 source_system 首段前缀计数；凭据按
    # target_type 精确匹配 ∪ target_id 'domain:ref' 前缀兜底
    # （workitem 型凭据的产线归属全靠后者）。
    src_counts = {}
    for k, v in artifacts.get("by_source", {}).items():
        src_counts[k.split(".")[0]] = src_counts.get(k.split(".")[0], 0) + v
    type_counts = approvals.get("by_target_type", {})
    prefix_counts = approvals.get("by_target_prefix", {})
    lines = []
    for prefix, label in PRODUCTION_LINES:
        ap_n = max(type_counts.get(prefix, 0), prefix_counts.get(prefix, 0))
        lines.append({
            "prefix": prefix, "label": label,
            "artifact_count": src_counts.get(prefix, 0),
            "approval_count": ap_n,
            "registered": bool(src_counts.get(prefix) or ap_n),
        })
    return {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": artifacts,
        "approvals": approvals,
        "production_lines": lines,
        "note": {
            "content_hash": "凭据绑定率 = content_hash 非空占比；"
                            "legacy_unbound 为旧口径存量（跳过校验，"
                            "不回填不迁移），属固有历史而非缺陷",
            "supersede_chains": "仅展示长度>1 的版本链（有演进才成链）",
        },
    }


# ══ 观察季看板（M5 刀4 追加段）═════════════════════════════════════
#
# GET /api/platform/observe —— 影子决策账本链只读投影，供 Hermes 8787
# console 页「观察季 Shadow」卡片经 K-ORG-112 转调消费（Hermes 永不
# 直读数据文件，本端点是唯一数据面）。
#
# 聚合五源（全部只读）：
#   shadow/shadow_ledger.jsonl    影子账本（JSONL，半行容错）
#   shadow/monday_checkpoint.log  周一巡检 7 门（取最后一次巡检块）
#   shadow/eval_last_run.log      eval 三判据（J1/J2/J3/J4）
#   perf_ledger.jsonl             后验账本（JSONL，半行容错）
#   shadow/daily_report_*.md      首席日报（取最新，首段 300 字）
#
# fail-visible：某源文件不存在 → 对应段 {"missing": true}，不静默空、
# 不抛异常；JSONL 损坏/半行跳过并计入 corrupt_lines。

_GATE_RE = re.compile(r"^\[(PASS|FAIL|PENDING|SKIP)\]\s*(.*)$")
_VERDICT_RE = re.compile(r"^\[(J\d[^\]]*)\]\s*(.*)$")
_RUN_HEADER_MARK = "护航巡检 2"  # 巡检块标题行（含时间戳）


def _read_jsonl(path: Path):
    """JSONL 只读逐行解析（损坏/半行跳过并计数，绝不整文件崩）。

    Returns (rows, corrupt_lines)；文件不存在 → None（上层 fail-visible）。
    """
    if not path.exists():
        return None
    rows: list = []
    corrupt = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                corrupt += 1
    return rows, corrupt


def _file_mtime(path: Path):
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None


def _shadow_projection() -> dict:
    """影子账本投影：规模/损坏计数/最新交易日进度/parse_ok 率/最近 5 行。"""
    got = _read_jsonl(_ap_db("shadow/shadow_ledger.jsonl"))
    if got is None:
        return {"missing": True}
    rows, corrupt = got
    dates: set = set()
    latest_done = latest_failed = ""
    parse_ok = parse_fail = 0
    for r in rows:
        d = str(r.get("date") or "")
        st = str(r.get("status") or "")
        if d:
            dates.add(d)
        if st == "done" and d > latest_done:
            latest_done = d
        elif st == "failed" and d > latest_failed:
            latest_failed = d
        # parse_ok 仅旧样本缺字段——只对显式布尔计数，缺位不入分母
        if r.get("parse_ok") is True:
            parse_ok += 1
        elif r.get("parse_ok") is False:
            parse_fail += 1
    latest_date = max(dates) if dates else None
    latest_rows = ([r for r in rows if str(r.get("date") or "") == latest_date]
                   if latest_date else [])
    latest_done_count = sum(1 for r in latest_rows
                            if r.get("status") == "done")
    return {
        "ledger_rows": len(rows),
        "corrupt_lines": corrupt,
        "latest_date": latest_date,
        "latest_date_done": latest_done or None,
        "latest_date_failed": latest_failed or None,
        "latest_date_rows": len(latest_rows),
        "latest_date_done_count": latest_done_count,
        "parse_ok_count": parse_ok,
        "parse_fail_count": parse_fail,
        "recent": [
            {"date": r.get("date"), "symbol": r.get("symbol"),
             "status": r.get("status"), "parse_ok": r.get("parse_ok"),
             "score": r.get("score")}
            for r in rows[-5:]],
    }


def _checkpoint_projection() -> dict:
    """周一巡检投影：取日志中最后一次巡检块的 7 门状态。"""
    path = _ap_db("shadow/monday_checkpoint.log")
    if not path.exists():
        return {"missing": True}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"missing": True}
    lines = text.splitlines()
    start = max((i for i, l in enumerate(lines) if _RUN_HEADER_MARK in l),
                default=None)
    if start is None:
        return {"log_mtime": _file_mtime(path), "no_run_found": True,
                "gates": [], "summary": ""}
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[start])
    gates: list = []
    summary = ""
    for line in lines[start + 1:]:
        s = line.strip()
        if not s:
            continue
        if set(s) == {"="}:
            continue  # 块内/块间 ===== 分隔线（标题行两侧各一条，直接跳过）
        if _RUN_HEADER_MARK in s:
            break  # 进入下一个巡检块，收束
        gm = _GATE_RE.match(s)
        if gm:
            status, rest = gm.group(1), gm.group(2).strip()
            name, _, detail = rest.partition("——")
            gates.append({"name": name.strip(), "status": status,
                          "detail": detail.strip()})
            continue
        if s.startswith("结论:"):
            summary = s[len("结论:"):].strip()
    return {"log_mtime": _file_mtime(path),
            "run_at": m.group(1) if m else "",
            "gates": gates, "summary": summary}


def _eval_projection() -> dict:
    """eval 三判据投影：[J*] 判据行 + 结论行。"""
    path = _ap_db("shadow/eval_last_run.log")
    if not path.exists():
        return {"missing": True}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"missing": True}
    verdicts: list = []
    conclusion = ""
    for line in text.splitlines():
        s = line.strip()
        vm = _VERDICT_RE.match(s)
        if vm:
            crit, rest = vm.group(1).strip(), vm.group(2).strip()
            st_m = re.match(r"^(PASS|FAIL|PENDING|WARN|SKIP)\b", rest)
            verdicts.append({"criterion": crit,
                             "status": st_m.group(1) if st_m else None,
                             "text": rest})
            continue
        if s.startswith("影子评估结论"):
            conclusion = s
    return {"log_mtime": _file_mtime(path), "verdicts": verdicts,
            "conclusion": conclusion}


def _perf_projection(limit_recent: int = 5) -> dict:
    """后验账本投影：行数/损坏计数/最近 5 行摘要。"""
    got = _read_jsonl(_ap_db("perf_ledger.jsonl"))
    if got is None:
        return {"missing": True}
    rows, corrupt = got
    return {
        "rows": len(rows),
        "corrupt_lines": corrupt,
        "recent": [
            {"date": r.get("date"), "symbol": r.get("symbol"),
             "recommendation": r.get("recommendation"),
             "score": r.get("score"), "confidence": r.get("confidence"),
             "ret_pct": r.get("ret_pct"), "source": r.get("source")}
            for r in rows[-limit_recent:]],
    }


def _report_projection() -> dict:
    """首席日报投影：最新一份 daily_report_*.md 首段 300 字。"""
    d = _ap_db("shadow")
    if not d.is_dir():
        return {"missing": True}
    reports = sorted(d.glob("daily_report_*.md"))
    if not reports:
        return {"missing": True}
    path = reports[-1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"missing": True}
    # 首段 300 字：剔除标题行后取正文开头
    body = "\n".join(l for l in text.splitlines()
                     if not l.startswith("#")).strip()
    return {"latest_date": path.stem.replace("daily_report_", "", 1),
            "path": path.name, "excerpt": body[:300]}


def build_observe_state() -> dict:
    """观察季看板聚合主入口（供 mixin 与测试共用）。"""
    shadow = _shadow_projection()
    perf = _perf_projection()
    report = _report_projection()
    latest_trade_date = None
    if not shadow.get("missing"):
        latest_trade_date = shadow.get("latest_date")
    if not latest_trade_date and not report.get("missing"):
        latest_trade_date = report.get("latest_date")
    return {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "latest_trade_date": latest_trade_date,
        "shadow": shadow,
        "checkpoint": _checkpoint_projection(),
        "eval": _eval_projection(),
        "perf": perf,
        "report": report,
        "note": {
            "architecture": "K-ORG-112：8091 只读 agent_platform 数据文件，"
                            "Hermes 8787 仅转调本端点，永不直读数据文件",
            "fail_visible": "missing:true = 源文件不存在；corrupt_lines = "
                            "JSONL 损坏/半行跳过计数",
        },
    }


class Handler:
    """能力看板 mixin（模块名绑定：portal_api 里 import api_capability）。"""

    def _capability_board(self):
        self._json(build_capability_board())

    def _platform_observe(self, qs):
        # 观察季看板（影子账本/巡检/eval/perf/日报 只读聚合）。
        # Hermes 8787 /api/observe/state 经 K-ORG-112 转调至此，
        # Hermes 永不直读 agent_platform 数据文件。
        self._json(build_observe_state())
