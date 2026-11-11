# -*- coding: utf-8 -*-
"""方案 A 原型：JEV 图结果 -> sqlite（离线 / 纯新增 / 不接 8091）。

设计稿：docs/JEV_快照转sqlite_ingest原型设计.md
规范口径：docs/零界技能开发规范_v1.0.md
    ingest.py = 取数与计算层（可用 pandas / parquet）
    api.py    = 只读查询层（stdlib only / 30s / 8MB）

取数：portal/api_jev_graph.compute(day, fund=False, kind=...)
    直接复用线上计算函数，**不复制 build() 的计算逻辑**，避免两套口径。

离线保证（全部只在本次进程内生效，不写任何生产文件）：
  1. with_fund=False        8091 的 fund=0 档，跳过资金流与研报/公告
  2. JEV_MINS_FALLBACK=off  关 xtcloud 单票分钟远端兜底（_mins_fetch）
  3. JEV_MINS_WRITE=0       分钟层只读，不回写 parquet
  4. partial(build, with_chain=False)
     关掉 compute() 默认开启的连板/席位/龙虎榜 xtcloud 三连请求。
  5. G._warm_many = 空实现
     build() 的批量分钟兜底走 _warm_many -> _xt().mins_many(workers=8)，
     **不检查 JEV_MINS_FALLBACK**，实测首版仍产生上百次出站连接。
     置空后保留 _mins_local() 本地分钟读取，不牺牲本地快照。
  6. 出站 socket 哨兵：非回环 connect 一律拒绝并计数，如实写进 source_sig。
     设计稿 §4.4 只列了 1/2，3–6 是本轮实测补上的。

写库：graph_json(day, payload, built_at, source_sig, flags)
     <db>.tmp -> os.replace；失败保留旧库；零删除（临时库用 DROP TABLE 复写）。

口径保护：payload 除与 8091 同款的 NaN/Inf -> null 清洗外，不重算、不重排、
不补默认值；三旗标恒成立，分钟台账 minute_read 原样带入。
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import queue
import re
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

STATION = Path("C:/aios")
PORTAL = STATION / "portal"
SAFE = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_COMPANY = "local-selftest"
DEFAULT_SKILL = "jev-graph-sqlite-prototype"
DB_NAME = "jev.db"

# 阈值与编号一律码点构造，防打印/传输层串味（跨会话铁律）。
DEF_TOP = int("".join(chr(c) for c in (56,)))
DEF_TIMEOUT_S = float("".join(chr(c) for c in (49, 50, 48)))

TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS graph_json ("
    "day TEXT PRIMARY KEY, payload TEXT NOT NULL, built_at TEXT NOT NULL, "
    "source_sig TEXT NOT NULL, flags TEXT NOT NULL)"
)
INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_graph_built ON graph_json(built_at)"
SELECT_ALL = "SELECT day, payload, built_at, source_sig, flags FROM graph_json"
UPSERT_SQL = (
    "INSERT OR REPLACE INTO graph_json "
    "(day, payload, built_at, source_sig, flags) VALUES (?, ?, ?, ?, ?)"
)
HAS_TABLE = "SELECT name FROM sqlite_master WHERE type='table' AND name='graph_json'"

# append-only 审计台账：每次 ingest 追加一行，永不 UPDATE / DELETE。
# 修的缺口：graph_json 按 day 走 INSERT OR REPLACE，同日重跑会覆盖该行 source_sig，
# 导致「这次图是由哪些输入算出来的」无法回溯。台账把输入摘要逐次留痕。
AUDIT_DDL = (
    "CREATE TABLE IF NOT EXISTS ingest_audit ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "day TEXT NOT NULL, built_at TEXT NOT NULL, "
    "payload_sha TEXT NOT NULL, source_sig_sha TEXT NOT NULL, "
    "n_files INTEGER NOT NULL, blocked_outbound INTEGER NOT NULL, "
    "offline TEXT NOT NULL, flags TEXT NOT NULL)"
)
AUDIT_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_audit_day ON ingest_audit(day, seq)"
)
AUDIT_SELECT_ALL = (
    "SELECT seq, day, built_at, payload_sha, source_sig_sha, n_files, "
    "blocked_outbound, offline, flags FROM ingest_audit ORDER BY seq"
)
AUDIT_INSERT_FULL = (
    "INSERT INTO ingest_audit (seq, day, built_at, payload_sha, source_sig_sha, "
    "n_files, blocked_outbound, offline, flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
AUDIT_INSERT = (
    "INSERT INTO ingest_audit (day, built_at, payload_sha, source_sig_sha, "
    "n_files, blocked_outbound, offline, flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
HAS_AUDIT = "SELECT name FROM sqlite_master WHERE type='table' AND name='ingest_audit'"

# P2.3 三旗标：原型结果不得进判定闸 A/B，恒不校准。
FLAGS = {"coverage_only": True, "gate_eligible": False, "calibrated": False}


def safe_part(value, fallback):
    """防路径逃逸：companyId / skillId 只允许安全字符，否则回落到本地自测缺省值。"""
    if value and SAFE.fullmatch(value):
        return value
    return fallback


def db_path(company_id, skill_id):
    root = Path.home() / ".zerobound" / "instances" / "default" / "skill-data"
    return root / company_id / skill_id / DB_NAME


def san(obj):
    """与 portal_api.py::_json 内嵌 _san 逐行等价（2026-11-10 核对）。

    8091 线上就把 NaN/Inf 清洗成 null；照抄它才能做到「同结构」。
    这是序列化层的既有行为，不是对业务口径字段的改写。
    """
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))) else obj
    if isinstance(obj, dict):
        return {k: san(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [san(v) for v in obj]
    return obj


class OfflineGuard:
    """出站连接哨兵。非回环 connect 直接拒绝并记账；退出时恢复原实现。"""

    def __init__(self):
        self.blocked = []
        self._connect = None

    @staticmethod
    def _loopback(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", "ignore")
        host = str(host)
        return (host.startswith("127.") or host in
                ("localhost", "::1", "0:0:0:0:0:0:0:1", ""))

    def __enter__(self):
        outer = self
        self._connect = socket.socket.connect

        def guarded(sock, address, _orig=self._connect):
            host = address[0] if isinstance(address, (tuple, list)) and address else ""
            if outer._loopback(host):
                return _orig(sock, address)
            outer.blocked.append(str(host)[:120])
            raise OSError("offline prototype: outbound connect blocked")

        socket.socket.connect = guarded
        return self

    def __exit__(self, *exc_info):
        socket.socket.connect = self._connect
        return False


def compute_bounded(fn, timeout_s):
    """daemon 线程跑计算，主流程带超时；超时或异常都不写库，旧库原样保留。"""
    box = queue.Queue(maxsize=1)

    def runner():
        try:
            box.put(("ok", fn()))
        except BaseException as exc:  # noqa: BLE001
            box.put(("err", type(exc).__name__ + ": " + str(exc)))

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    try:
        status, value = box.get(timeout=max(0.0, float(timeout_s)))
    except queue.Empty:
        raise TimeoutError("compute exceeded timeout_s=" + str(timeout_s))
    if status != "ok":
        raise RuntimeError(value)
    return value


def snapshot_sources(day):
    """参与计算的快照清单，只 stat 不读内容；缺失记 absent，不补值。"""
    rel = [
        "quant_backtest/data/tushare/daily/d_" + day + ".parquet",
        "quant_backtest/data/tushare/_jev_snap/disc_" + day + ".json",
        "quant_backtest/data/tushare/_jev_snap/flow_" + day + ".json",
        "quant_backtest/data/tushare/intraday_board/board_" + day + ".json",
        # _jev_concept/<day>.json 由 _theme_mod().strength() 读取，build() 总会调用；
        # 文件缺席同样参与决策（决定 theme.has=false），故缺席也要记进摘要。
        "quant_backtest/data/tushare/_jev_concept/" + day + ".json",
        "portal/social_" + day + ".json",
        "news_collector/news.db",
        "agent_platform/data/jev_audit/jev_audit.db",
    ]
    mins_dir = STATION / "quant_backtest/data/tushare/stk_mins"
    month_suffix = "_" + day[:6] + ".parquet"
    if mins_dir.is_dir():
        for name in sorted(os.listdir(str(mins_dir))):
            if name.endswith(month_suffix):
                rel.append("quant_backtest/data/tushare/stk_mins/" + name)
    # stock_basic 决定行业板块划分，是不随 day 变但会影响整图的基础数据。
    basic_dir = STATION / "quant_backtest/data/tushare/stock_basic"
    if basic_dir.is_dir():
        for name in sorted(os.listdir(str(basic_dir))):
            if name.endswith(".parquet"):
                rel.append("quant_backtest/data/tushare/stock_basic/" + name)

    rows = []
    for item in rel:
        p = STATION / item
        if p.is_file():
            st = p.stat()
            rows.append({"path": item, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
        else:
            rows.append({"path": item, "absent": True})
    return rows


def source_signature(day, guard, elapsed_s):
    files = snapshot_sources(day)
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    body = {
        "sha256": digest,
        "n_files": len(files),
        "files": files,
        "elapsed_s": round(float(elapsed_s), 3),
        "offline": {
            "with_fund": False,
            "with_chain": False,
            "mins_fallback": "off",
            "mins_write": "off",
            "mins_warm_many": "disabled",
            "blocked_outbound": len(guard.blocked),
            "blocked_hosts": sorted(set(guard.blocked)),
        },
    }
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def atomic_write(db, row, audit_row):
    """旧库 -> 临时库 -> os.replace。零删除，失败旧库完好。

    graph_json   按 day UPSERT（幂等，一日一行）。
    ingest_audit 只追加：旧台账行按原 seq 全量搬回，再插入本次一行，
    因此历史输入（source_sig_sha / offline / flags）不会因覆盖而丢失。
    台账读取若失败会直接抛错、不写库 —— 宁可本次失败，也不丢审计历史。
    """
    tmp = db.with_name(db.name + ".tmp")
    db.parent.mkdir(parents=True, exist_ok=True)

    old_rows, old_audit = [], []
    if db.is_file():
        old = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        try:
            try:
                if old.execute(HAS_TABLE).fetchone():
                    old_rows = [tuple(r) for r in old.execute(SELECT_ALL).fetchall()]
            except sqlite3.Error:
                old_rows = []
            if old.execute(HAS_AUDIT).fetchone():
                old_audit = [tuple(r) for r in old.execute(AUDIT_SELECT_ALL).fetchall()]
        finally:
            old.close()

    con = sqlite3.connect(str(tmp))
    try:
        # 不删文件：上次失败残留的 tmp 直接 DROP 复写，规避 Windows 删除红线。
        con.execute("DROP TABLE IF EXISTS graph_json")
        con.execute("DROP TABLE IF EXISTS ingest_audit")
        con.execute(TABLE_DDL)
        con.execute(INDEX_DDL)
        con.execute(AUDIT_DDL)
        con.execute(AUDIT_INDEX_DDL)
        for old_row in old_rows:
            con.execute(UPSERT_SQL, old_row)
        con.execute(UPSERT_SQL, row)
        for audit in old_audit:
            con.execute(AUDIT_INSERT_FULL, audit)
        cur = con.execute(AUDIT_INSERT, audit_row)
        seq = int(cur.lastrowid)
        con.commit()
    finally:
        con.close()
    os.replace(str(tmp), str(db))
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None,
                    help="选票日 YYYYMMDD；缺省沿用 jev_graph.resolve_day")
    ap.add_argument("--timeout-s", type=float, default=DEF_TIMEOUT_S)
    ap.add_argument("--kind", default="industry", choices=("industry", "theme"))
    ap.add_argument("--top", type=int, default=DEF_TOP)
    args = ap.parse_args()

    # 关掉分钟层远端兜底与回写；chain 层由下面的 partial 关。
    os.environ["JEV_MINS_FALLBACK"] = "off"
    os.environ["JEV_MINS_WRITE"] = "0"

    if str(PORTAL) not in sys.path:
        sys.path.insert(0, str(PORTAL))
    import api_jev_graph as API  # noqa: E402
    import jev_graph as G  # noqa: E402

    day = G.resolve_day(args.day)
    # compute() 没有 with_chain 参数，只能在本进程内临时限定 build 的默认值。
    # 不改文件、不跨进程、不影响 8091 常驻服务。
    G.build = functools.partial(G.build, with_chain=False)
    # build() 的批量分钟兜底不认 JEV_MINS_FALLBACK，单独置空；本地分钟照常读。
    G._warm_many = lambda need, day, limit=None, budget_s=None: {}

    company = safe_part(os.environ.get("ZB_COMPANY_ID"), DEFAULT_COMPANY)
    skill = safe_part(os.environ.get("ZB_SKILL_ID"), DEFAULT_SKILL)
    db = db_path(company, skill)

    started = time.time()
    with OfflineGuard() as guard:
        payload = compute_bounded(
            lambda: API.compute(day, top=args.top, fund=False, kind=args.kind),
            args.timeout_s,
        )
        clean = san(payload)
        # 严格 JSON：不加 default=，numpy 等类型若混进来就明确失败，不静默转字符串。
        text = json.dumps(clean, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
        if not isinstance(clean, dict) or not clean.get("ok"):
            raise RuntimeError("compute 未返回 ok 图结果")
        built_at = datetime.now().isoformat(timespec="microseconds")
        sig = source_signature(day, guard, time.time() - started)
        sig_obj = json.loads(sig)
        payload_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        audit_row = (
            day, built_at, payload_sha, sig_obj["sha256"],
            int(sig_obj["n_files"]),
            int((sig_obj.get("offline") or {}).get("blocked_outbound") or 0),
            json.dumps(sig_obj.get("offline") or {}, sort_keys=True,
                       ensure_ascii=False),
            json.dumps(FLAGS, sort_keys=True, ensure_ascii=False),
        )
        audit_seq = atomic_write(
            db,
            (day, text, built_at, sig,
             json.dumps(FLAGS, sort_keys=True, ensure_ascii=False)),
            audit_row,
        )
        blocked = len(guard.blocked)

    raw = text.encode("utf-8")
    network = clean.get("network") or {}
    receipt = {
        "ok": True,
        "day": day,
        "db": str(db),
        "builtAt": built_at,
        "payloadSha256": payload_sha,
        "payloadBytes": len(raw),
        "auditSeq": audit_seq,
        "topKeys": sorted(clean.keys()),
        "nBoards": len(clean.get("boards") or []),
        "nNodes": len(network.get("nodes") or []),
        "nEdges": len(network.get("edges") or []),
        "minuteRead": clean.get("minute_read"),
        "flags": FLAGS,
        "offline": {"blocked_outbound": blocked, "with_chain": False,
                    "with_fund": False, "mins_fallback": "off",
                    "mins_warm_many": "disabled"},
        "sourceSig": json.loads(sig),
        "elapsedS": round(time.time() - started, 3),
    }
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "message": str(exc)}, ensure_ascii=False))
        sys.exit(1)
