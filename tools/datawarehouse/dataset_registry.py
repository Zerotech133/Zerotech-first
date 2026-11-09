# -*- coding: utf-8 -*-
"""数据集级统一登记/取数（WP-I1/I3 骨架；DWH-103 wave1 增 pg_table/contract_artifact）。

- 数据来源：research/reports/dataset_inventory_*.json（全域盘点，文件级 v1 口径 314 数据集）
- 能力：list / search / get / sample / emit（生成 dataset_registry YAML 草稿）
- **DWH-103 wave1（ADR-019）**：
  * 新 kind `pg_table`：智策 PG 5446 核心表（wave1 ≤15 张）。只读 DSN 解析优先级：
    显式参数 > 环境变量 `ZHICE_V2_DSN` > `jev_common.DB_DSN` fallback（与
    `portal/api_acad1_pool` 同源；`DW_PG_DSN_FALLBACK=0` 可显式关闭 fallback）。
    **凭据不打印/不落盘**；全部来源不可用 → `connector_status=missing_dsn`（不假绿）；
    连接失败 → `connector_unavailable`，`sample()` 抛 `ConnectorUnavailable`（fail-visible）。
  * 新 kind `contract_artifact`：`C:\\Temp` 契约产物**显式白名单**（不扩 ALLOWED_ROOTS、
    禁递归扫描、仅直属文件）。缺失/sha256 漂移 → 行状态 missing/sha256_mismatch，
    `sample()` 抛 `ContractArtifactUnavailable`（fail-visible）。
  * 兼容：`stats()` 与 `self.datasets`（文件级）保持原 314/265/72 口径不变；
    新增 `stats_v2()` 合并口径（`denominator_version=v2`）。
- **DWH-103 wave2（ADR-019 D3，2026-11-02 task-25）**：
  * `ZHICE_V2_DSN` 可用时，`pg_table` 条目 = **动态发现 `public.v2_%` 全量**
    （`discover_v2_tables(conn)`，以发现结果为准，**不硬编码 83/84 等数量**）；
    `stats_v2().pg.total`=发现数、`pg.available`=可读元数据数（`pg_class.relkind='r'`）；
    发现/元数据查询异常 fail-visible（不吞、不假绿）。
  * `sample()` 只接受发现集合内且符合 `^[a-z_][a-z0-9_]*$` 的标识符（只读
    `SELECT * FROM "<schema>"."<table>" LIMIT n`，不拼接任意输入；越界/未发现表拒绝）。
  * 全部 DSN 来源不可用（`ZHICE_V2_DSN` 未设且 jev_common fallback 不可用/被
    `DW_PG_DSN_FALLBACK=0` 关闭）时保持 wave1 行为（固定清单 + `missing_dsn`，不报错、不假绿）。
- 边界：只读；PG 仅只读会话 SELECT；不写业务数据/数据库/`C:\\Temp`；不递归扫描 Temp。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

def _repo_root() -> Path:
    """仓根：worktree 内运行也解析回主仓（与 tools/datawarehouse/common.py 同口径）。

    2026-11-08（K-CI-20261108-PATH）：原为硬编码 ``Path(r"C:\\项目\\老田的AI助手")``。
    干净检出（GitHub runner / 新克隆 / 换盘符 / 换机器）上该路径不存在 →
    ALLOWED_ROOTS 全灭、inventory 与 registry 全部读空 → datawarehouse 整片判红。
    这是路径可移植性缺陷，不是"数据缺失"，不能靠 CI 环境豁免糊过去。
    """
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        g = cand / ".git"
        try:
            if g.is_dir():
                return cand
            if g.is_file():
                txt = g.read_text(encoding="utf-8", errors="replace").strip()
                if txt.lower().startswith("gitdir:"):
                    raw = txt.split(":", 1)[1].strip()
                    gitdir = Path(raw)
                    if not gitdir.is_absolute():
                        gitdir = (cand / gitdir).resolve()
                    try:
                        return gitdir.parents[2]  # <main>/.git/worktrees/<n> → <main>
                    except IndexError:
                        return cand
                return cand
        except OSError:
            continue
    # 兜底：按目录层级找 agent_platform（主仓必有）；绝不回落硬编码绝对路径
    for cand in (here, *here.parents):
        if (cand / "agent_platform").is_dir():
            return cand
    return here.parents[1]


REPO = _repo_root()
ALLOWED_ROOTS = [
    REPO / "quant_backtest",
    REPO / "research",
    REPO / "knowledge_docs",
    REPO / "portal",
    REPO / "agent_platform",
    REPO / "news_collector",
    REPO / "k_stock",
    Path(r"C:\项目\知识库"),
    Path(r"C:\项目\PDF"),
    Path(r"C:\aios\automation_runs"),
]  # ADR-019 D4：**不把 C:\\Temp 加进来**（世界可写目录，扩根=任意文件读入口）

OWNER_HINTS = [
    ("quant_backtest/data", "DATA1"),
    ("research", "DSH-Lead"),
    ("知识库", "DSH-Lead"),
    ("PDF", "DSH-Lead"),
    ("agent_platform", "A0"),
    ("news_collector", "INTEL0"),
    ("k_stock", "T1"),
    ("automation_runs", "INTEL0"),
]

# ---------- DWH-103 / ADR-019 D2/D3：pg_table（wave1） ----------
PG_DSN_ENV = "ZHICE_V2_DSN"
PG_DSN_FALLBACK_ENV = "DW_PG_DSN_FALLBACK"  # "0" 关闭 jev_common fallback（测试/应急）
PG_CONNECTOR = "pg@127.0.0.1:5446/zhice_os_v2"  # 逻辑连接串标签（不含凭据）
PG_SCHEMA_WHITELIST = ("public",)  # 跨库/其他 schema 需 A0 加白（ADR-019 D2）
PG_TIMEOUT_S = 5
PG_DOMAIN = "平台运行"
# wave1：组合 manifest 已引用 + ACAD1/D1 核心表（≤15 张）；wave2 全 83+ 张另单（ADR-019 D3）
# ACAD1-DATAREFS（2026-11-04）：补 research reviews/decisions 数据源（G6 收口；表不存在时探测 fail-visible）
PG_WAVE1_TABLES = (
    {"table": "v2_acad1_pool", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_selection_pool", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_stock_research", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_selection_report", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_strategy_ownership", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_acad1_review", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_base_signal", "owner": "D1", "registered": True, "exposed": True},
    {"table": "v2_score_component", "owner": "D1", "registered": True, "exposed": True},
    {"table": "v2_lusu_decision", "owner": "D1", "registered": True, "exposed": True},
)

# ---------- IFS-POOL（2026-11-03 WO-IFS-POOL-20261103）：显式 dataset ref 登记 ----------
# D1 6 个 data ref 的 dataset registry 条目：能注册的带真实载体（active），
# 暂未落盘的写 planned 并强制 owner/due（不伪造空账/空文件）。只读加载，不改基线 stats。
DECLARED_REFS_PATH = Path(__file__).resolve().parent / "dataset_refs_declared.json"
DECLARED_REFS_ENV = "DW_DECLARED_REFS_PATH"
DECLARED_REFS_SCHEMA = "dataset_refs_declared/v1"
DECLARED_DOMAIN = "平台运行"
DECLARED_STATUSES = ("active", "planned")

# ---------- DWH-103 wave2（ADR-019 D3，2026-11-02 task-25）：动态发现 public.v2_% 全量 ----------
# 口径：DSN 可用时以**发现结果为准**（不硬编码 83/84 等数量）；DSN 未设置退回 wave1 固定清单。
# LIKE 中 `\_` 转义下划线：只匹配字面 `v2_` 前缀，`%` 才是通配符。
PG_DISCOVER_SQL = (
    "SELECT tablename FROM pg_tables "
    "WHERE schemaname = 'public' AND tablename LIKE 'v2\\_%' ORDER BY 1"
)
PG_METADATA_SQL = (
    "SELECT c.relname, c.relkind, c.reltuples FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = ANY(%s)"
)
PG_AVAILABLE_RELKINDS = ("r",)  # 可读元数据（普通表）；视图/分区父表等不计 available
PG_DYNAMIC_OWNER_DEFAULT = "DATA1"  # 动态发现表的 owner 占位（P7：v1.1 由 owner 逐表声明）

# ---------- DWH-103 / ADR-019 D4：C:\\Temp 契约产物白名单（wave1） ----------
TEMP_ROOT = Path(r"C:\Temp")
# 显式受控清单：逐条登记、只允许 temp_root 直属文件、禁递归扫描、不扩 ALLOWED_ROOTS。
# 过渡口径：v0 原地登记（不迁移）；迁移契约目录属 wave2/L2。
TEMP_CONTRACT_WHITELIST = (
    {
        "artifact_id": "acad1_evidence_bundle",
        "path": r"C:\Temp\acad1_evidence_bundle.json",
        "owner": "ACAD1", "produced_by_job": "job/acad1-evidence",
        "schema_ref": "acad1-evidence/v1", "retention_days": 30,
        "sha256": "cb1a8665fe73e51af85db1e65d7f193a4c7c465506a8e7a866596ab252148d31",
        "status": "active",
    },
    {
        "artifact_id": "zhice_job_status",
        "path": r"C:\Temp\zhice_job_status.json",
        "owner": "A0", "produced_by_job": "job/zhice-job-status",
        "schema_ref": "zhice-job-status/v1", "retention_days": 7,
        "sha256": "8aeeec5d96dfbf7c629fa9d850f02c7d414638cb627af88fc22cf4e9256e3775",
        "status": "active",
    },
    {
        "artifact_id": "data_contract_report",
        "path": r"C:\Temp\data_contract_report.json",
        "owner": "A0", "produced_by_job": "job/data-contract",
        "schema_ref": "data-contract-report/v1", "retention_days": 7,
        "sha256": "c6f6d4ef158dc13cdfd700dd470a850122eba7d9775c8e604be98642f09b4dc8",
        "status": "active",
    },
    {
        "artifact_id": "dw_contract_report",
        "path": r"C:\Temp\dw_contract_report.json",
        "owner": "DATA1", "produced_by_job": "job/dw-contract",
        "schema_ref": "dw-contract-report/v1", "retention_days": 7,
        "sha256": "51590b1020f25fde27dab643935bf45f639bd47045b4f4555477dcfaf996f96d",
        "status": "active",
    },
)

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# ---------- R12 D4：契约产物 sha256 基线外置 ----------
# 原实现把 sha256 写死成源码常量，而四个产物的 produced_by_job 全是定时任务，
# 每次重跑字节必变 → 常量一过期就永久报红（实测 3/4 常量为更早一次生成的字节，
# 文件两天没动仍不匹配）。基线外置后：源码常量降为「出厂默认」，可被基线覆盖；
# 定时产物 hash 变化登记 drift（info，不告警），只有静态产物（produced_by_job 为空）
# hash 不符才算真 mismatch。
CONTRACT_BASELINE_PATH = Path(__file__).resolve().parent / "contract_baseline.json"
CONTRACT_BASELINE_ENV = "DW_CONTRACT_BASELINE"
CONTRACT_BASELINE_SCHEMA = "contract_baseline/v1"
CONTRACT_DRIFT_STATUSES = ("drift",)


def load_contract_baseline(path=None) -> dict:
    """读基线文件；缺失/损坏一律返回空基线（退回出厂常量），**不抛异常**。

    基线是运维状态而非业务数据，不允许因它缺失让整个注册表崩掉。
    """
    p = Path(path or os.environ.get(CONTRACT_BASELINE_ENV) or CONTRACT_BASELINE_PATH)
    out = {"schema": CONTRACT_BASELINE_SCHEMA, "path": str(p),
           "loaded": False, "baselines": {}}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    raw = obj.get("baselines") if isinstance(obj, dict) else None
    if not isinstance(raw, dict):
        return out
    for aid, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        sha = str(rec.get("sha256") or "").strip().lower()
        if len(sha) != 64 or not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue  # 非法基线忽略，避免把坏值当期望值（宁可退回出厂常量）
        out["baselines"][str(aid)] = {
            "sha256": sha,
            "confirmed_at": rec.get("confirmed_at"),
            "confirmed_by": rec.get("confirmed_by"),
        }
    out["loaded"] = bool(out["baselines"])
    return out


def _mtime_iso(p: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    except OSError:
        return ""

# ---------- S3-06 P1：通用 query 能力（2026-11-02 task-50）----------
QUERY_DEFAULT_LIMIT = 100
QUERY_MAX_LIMIT = 1000
QUERY_MAX_OFFSET = 100000
QUERY_MAX_ORDER_KEYS = 2
QUERY_MAX_IN_VALUES = 100
QUERY_MAX_LIKE_LEN = 64
QUERY_MAX_SCAN_ROWS = 2_000_000
QUERY_MAX_SCAN_BYTES = 512 * 1024 * 1024
QUERY_MAX_FILES = 500          # 目录内 parquet 文件发现上限（超出显式 truncated）
QUERY_MAX_JSONL_FILES = 200
QUERY_MAX_JSONL_LINES = 5000
QUERY_TIMEOUT_S = 10.0
QUERY_PARQUET_ORDER_ROWS = 200_000  # parquet 内存排序扫描上限（超限 not_supported，P2 下推）
# ---------- S3-06 P2（2026-11-02 task-57）：jsonl / mixed / nested 四维 ----------
QUERY_JSONL_SCAN_LINES = 200_000   # jsonl 单次查询扫描行上限（超限 truncated 可见）
QUERY_JSONL_SCAN_BYTES = 64 * 1024 * 1024
QUERY_JSONL_SCHEMA_DOCS = 200      # 字段白名单推断样本（首批文档）
QUERY_JSONL_ORDER_ROWS = 100_000   # jsonl 排序最大匹配行（超限 not_supported）
QUERY_FILES_MAX = 5000             # mixed/nested 文件级载体发现上限
QUERY_FILES_FIELDS = ("path", "name", "ext", "size", "mtime", "depth")
QUERY_FILES_MAX_DEPTH = 4          # 文件级发现深度上限（防深递归）
QUERY_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "like")
QUERY_AUDIT_ENV = "DW_QUERY_AUDIT"
QUERY_AUDIT_LOG = Path(r"C:\Temp\dw_query_audit.log")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TIME_COL_HINTS = (
    "trade_date", "date", "pub_date", "ann_date", "notice_date", "created_at",
    "updated_at", "datetime", "timestamp", "trade_time", "end_date", "report_date",
    "publish_date", "cal_date", "month", "year", "quarter", "day", "ts",
)


def _jsonable(v):
    """行值 → JSON 安全类型（date/datetime/bytes 等可序列化）。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    try:
        return str(v)
    except Exception:  # noqa: BLE001
        return None


def _sqlite_since_value(con, table, col, since_n, t_type):
    """sqlite since 下界值：按列实际存储格式选择（INT/8位数字/ISO 文本）。

    2026-11-02 修复：此前所有非 INT 列一律用 YYYYMMDD 比较，
    而 xq_status.created_at 等列为 ISO 文本 → '2026-11-02...' >= '20261001'
    恒 false（假绿空结果）。
    """
    if "INT" in (t_type or "").upper():
        return int(since_n["ymd"])
    try:
        q = 'SELECT "%s" FROM "%s" WHERE "%s" IS NOT NULL LIMIT 1' % (
            col.replace('"', '""'), table.replace('"', '""'), col.replace('"', '""'))
        row = con.execute(q).fetchone()
        sample = row[0] if row else None
    except Exception:  # noqa: BLE001
        sample = None
    if isinstance(sample, (bytes, bytearray)):
        try:
            sample = sample.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            sample = None
    if isinstance(sample, str):
        s = sample.strip()
        if "-" in s or "T" in s:
            return since_n["date"].isoformat()
        if len(s) == 8 and s.isdigit():
            return since_n["ymd"]
    return since_n["ymd"]


def _norm_since(since):
    """since → {'date': date, 'ymd': 'YYYYMMDD'}；非法 fail-visible。"""
    if since in (None, ""):
        return None
    s = str(since).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            return {"date": d, "ymd": d.strftime("%Y%m%d")}
        except ValueError:
            continue
    raise QueryParamError("since 非法（支持 YYYY-MM-DD / YYYYMMDD）：%r" % since)


def _norm_columns(columns):
    if columns in (None, "", []):
        return None
    if isinstance(columns, str):
        cols = [c.strip() for c in columns.split(",") if c.strip()]
    else:
        cols = [str(c).strip() for c in columns]
    if not cols:
        return None
    for c in cols:
        if not _FIELD_RE.fullmatch(c):
            raise QueryParamError("columns 非法字段名：%r" % c)
    return cols


def _parse_where_value(raw: str):
    s = raw.strip()
    if not s:
        return ""
    if s.lower() == "null":
        return None
    try:
        return json.loads(s)
    except ValueError:
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s


def _parse_where_expr(expr: str) -> dict:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|!=|=|>|<)\s*(.+)$", expr.strip())
    if not m:
        raise QueryParamError("where 表达式非法（需 field op value）：%r" % expr)
    return {"field": m.group(1), "op": m.group(2), "value": _parse_where_value(m.group(3))}


def _norm_where(where):
    """结构化 where → [{field,op,value}]；**不接受原始 SQL**（防注入，值全参数化）。"""
    if where in (None, "", []):
        return []
    if isinstance(where, str):
        s = where.strip()
        if s.startswith("["):
            try:
                where = json.loads(s)
            except ValueError as exc:
                raise QueryParamError("where JSON 解析失败：%s" % str(exc)[:120])
        else:
            where = [_parse_where_expr(x) for x in s.split(";") if x.strip()]
    elif isinstance(where, dict):
        where = [{"field": k, "op": "=", "value": v} for k, v in where.items()]
    out = []
    for item in where:
        if isinstance(item, (list, tuple)):
            if len(item) == 2:
                item = {"field": item[0], "op": "=", "value": item[1]}
            elif len(item) == 3:
                item = {"field": item[0], "op": item[1], "value": item[2]}
            else:
                raise QueryParamError("where 条件项非法：%r" % (item,))
        if not isinstance(item, dict) or "field" not in item:
            raise QueryParamError("where 条件项非法：%r" % (item,))
        field = str(item["field"]).strip()
        op = str(item.get("op") or "=").strip().lower()
        value = item.get("value")
        if not _FIELD_RE.fullmatch(field):
            raise QueryParamError("where 字段名非法：%r" % field)
        if op not in QUERY_OPS:
            raise QueryParamError("where 算子非法：%r（允许 %s）" % (op, "/".join(QUERY_OPS)))
        if op == "in":
            if not isinstance(value, (list, tuple)) or not value:
                raise QueryParamError("in 操作需要非空数组")
            if len(value) > QUERY_MAX_IN_VALUES:
                raise QueryParamError("in 数组超上限（%d）" % QUERY_MAX_IN_VALUES)
            value = list(value)
        elif op == "like":
            if not isinstance(value, str) or not value or len(value) > QUERY_MAX_LIKE_LEN:
                raise QueryParamError("like 需要 1~%d 字符字符串" % QUERY_MAX_LIKE_LEN)
        elif isinstance(value, (list, tuple, dict, set, bytes)):
            raise QueryParamError("where 值需为标量（field=%r 收到 %s）"
                                  % (field, type(value).__name__))
        out.append({"field": field, "op": op, "value": value})
    return out


def _norm_order(order):
    if order in (None, "", []):
        return []
    if isinstance(order, str):
        order = [x.strip() for x in order.split(",") if x.strip()]
    out = []
    for item in order:
        if isinstance(item, str):
            bits = item.split(":", 1)
            item = {"field": bits[0].strip(),
                    "desc": len(bits) > 1 and bits[1].strip().lower() in ("desc", "1", "true")}
        elif isinstance(item, (list, tuple)):
            item = {"field": item[0], "desc": bool(item[1]) if len(item) > 1 else False}
        if not isinstance(item, dict):
            raise QueryParamError("order 项非法：%r" % (item,))
        field = str(item.get("field") or "").strip()
        if not _FIELD_RE.fullmatch(field):
            raise QueryParamError("order 字段名非法：%r" % field)
        out.append({"field": field, "desc": bool(item.get("desc"))})
    if len(out) > QUERY_MAX_ORDER_KEYS:
        raise QueryParamError("order 最多 %d 个键" % QUERY_MAX_ORDER_KEYS)
    return out


def _pick_time_column(names):
    """按列名启发式挑时间列（schema 白名单内，不猜数据语义）。"""
    lowered = {str(n).lower(): str(n) for n in names}
    for hint in _TIME_COL_HINTS:
        if hint in lowered:
            return lowered[hint]
    for n in names:
        if re.search(r"(date|_at$|time|day|month|year|^ts$|_ts$)", str(n), re.I):
            return str(n)
    return None


def _like_contains(value: str) -> str:
    """like 值 → 匹配模式（支持前缀/后缀/子串，限 64 字符）。"""
    v = value
    if v.startswith("%") and v.endswith("%") and len(v) > 2:
        return v[1:-1]
    if v.startswith("%"):
        return v[1:]
    if v.endswith("%"):
        return v[:-1]
    return v


def _order_key(value):
    """排序键：数值按数值序、文本按字符串序（修复 JSONL/文件级 '10'<'9' 字符串序 BUG）。

    2026-11-02 task-78：此前 jsonl/files 排序对数值列用 str()，多位数与单位数乱序；
    None 由 _sorted_rows 单独置末尾（desc 也不提前）。
    """
    if isinstance(value, bool):
        return (0, 0.0, "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    return (0, 0.0, str(value))


def _sorted_rows(rows, field, desc):
    """按字段排序：有值行类型感知排序；None/缺字段行固定置末尾（升/降序一致）。"""
    present = [r for r in rows if r.get(field) is not None]
    missing = [r for r in rows if r.get(field) is None]
    present.sort(key=lambda r: _order_key(r.get(field)), reverse=bool(desc))
    return present + missing


def _cmp_value(actual, op: str, expected) -> bool:
    """jsonl/文件级 where 比较（类型宽松、绝不抛错；None 不匹配）。"""
    def _numeric(x):
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        try:
            return float(str(x).strip())
        except (TypeError, ValueError):
            return None

    if op == "in":
        return any(_cmp_value(actual, "=", v) for v in expected)
    if op == "like":
        if actual is None:
            return False
        return _like_contains(str(expected)) in str(actual)
    if actual is None:
        return False
    a, b = _numeric(actual), _numeric(expected)
    if op in (">", ">=", "<", "<=") and a is not None and b is not None:
        return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
    sa, sb = str(actual), str(expected)
    if op == "=":
        return sa == sb
    if op == "!=":
        return sa != sb
    if op == ">":
        return sa > sb
    if op == ">=":
        return sa >= sb
    if op == "<":
        return sa < sb
    if op == "<=":
        return sa <= sb
    return False


def _match_doc(doc: dict, where) -> bool:
    for w in where:
        if w["field"] not in doc:
            return False
        if not _cmp_value(doc.get(w["field"]), w["op"], w["value"]):
            return False
    return True


def _audit_query(record: dict) -> None:
    """追加 JSONL 查询审计（best-effort，不记录数据内容/凭据，失败不影响只读查询）。"""
    path = os.environ.get(QUERY_AUDIT_ENV, str(QUERY_AUDIT_LOG))
    if not path or str(path).strip().lower() in ("off", "none", "0"):
        return
    try:
        pp = Path(path)
        pp.parent.mkdir(parents=True, exist_ok=True)
        with pp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


class RegistryError(RuntimeError):
    """registry 只读探针/白名单失败基类（fail-visible）。"""


class ConnectorUnavailable(RegistryError):
    """pg_table 连接器不可用（missing_dsn / driver_missing / connector_unavailable）。"""

    def __init__(self, message: str, status: str = "connector_unavailable"):
        super().__init__(message)
        self.status = status


class ContractArtifactUnavailable(RegistryError):
    """contract_artifact 缺失/漂移/非文件（fail-visible，不静默丢弃）。"""


class QueryError(RegistryError):
    """query 参数/载体错误基类：code 供 HTTP/CLI 映射（400/404/409/504）。"""

    def __init__(self, message: str, code: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


class QueryParamError(QueryError):
    """参数非法（未知字段/非法算子/超上限/越权路径）→ HTTP 400。"""

    def __init__(self, message, detail=None):
        super().__init__(message, 400, detail)


class QueryNotSupported(QueryError):
    """载体能力不支持（P1 未实现/参数组合不支持）→ HTTP 409，fail-visible。"""

    def __init__(self, message, detail=None):
        super().__init__(message, 409, detail)


def load_declared_refs(path: str | Path | None = None) -> list[dict]:
    """加载显式 dataset ref 声明（IFS-POOL，只读）。

    - 文件缺失 → 返回 []（可选能力，不阻塞既有 registry 基线）；
    - 契约非法（schema/重复 ref/缺 owner/planned 缺 due）→ 抛 RegistryError（fail-visible）；
    - 只读取 JSON，不写任何文件。
    """
    target = Path(
        path or os.environ.get(DECLARED_REFS_ENV) or str(DECLARED_REFS_PATH)
    )
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError("declared refs 读取失败 %s: %s" % (target, exc)) from exc
    if not isinstance(raw, dict) or raw.get("schema") != DECLARED_REFS_SCHEMA:
        raise RegistryError(
            "declared refs 契约非法: %s（schema 必须为 %s）"
            % (target, DECLARED_REFS_SCHEMA)
        )
    refs = raw.get("refs")
    if not isinstance(refs, list):
        raise RegistryError("declared refs.refs 必须为 list: %s" % target)
    seen: set[str] = set()
    out: list[dict] = []
    for index, item in enumerate(refs):
        where = "refs[%d]" % index
        if not isinstance(item, dict):
            raise RegistryError("%s 必须为 dict" % where)
        ref = str(item.get("ref") or "").strip()
        if not ref.startswith("dataset/"):
            raise RegistryError("%s ref 必须以 dataset/ 开头: %r" % (where, ref))
        if ref in seen:
            raise RegistryError("declared ref 重复: %s" % ref)
        if not str(item.get("owner") or "").strip():
            raise RegistryError("%s(%s) 缺 owner" % (where, ref))
        status = str(item.get("status") or "")
        if status not in DECLARED_STATUSES:
            raise RegistryError(
                "%s(%s) status 非法: %r（允许: %s）"
                % (where, ref, status, ", ".join(DECLARED_STATUSES))
            )
        if status == "planned" and not str(item.get("due") or "").strip():
            raise RegistryError("%s(%s) planned 必须声明 due" % (where, ref))
        seen.add(ref)
        out.append(dict(item))
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_dsn(dsn: str) -> str:
    """把 SQLAlchemy 风格 PG URL 归一为 libpq DSN；普通 DSN 原样返回。"""
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgres+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


def _pg_label_from_dsn(dsn: str) -> str:
    """DSN → 不含凭据的逻辑连接标签（host:port/db）；非 URL DSN 不展示内容。"""
    text = _normalize_dsn(str(dsn or "")).strip()
    if not text:
        return PG_CONNECTOR
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(text)
        if str(parts.scheme).startswith("postgres"):
            host = parts.hostname or "?"
            port = parts.port or "?"
            dbname = (parts.path or "").lstrip("/") or "?"
            return "pg@%s:%s/%s" % (host, port, dbname)
    except Exception:  # noqa: BLE001 — 任何解析失败都不回显 DSN（可能含密码）
        pass
    return PG_CONNECTOR


def _pg_label_from_kwargs(kwargs: dict) -> str:
    """连接 kwargs → 不含凭据的逻辑连接标签。"""
    try:
        return "pg@%s:%s/%s" % (
            kwargs.get("host") or "?",
            kwargs.get("port") or "?",
            kwargs.get("dbname") or "?",
        )
    except Exception:  # noqa: BLE001
        return PG_CONNECTOR


def _jev_common_connect_kwargs():
    """读取 jev_common.DB_DSN（与 portal/api_acad1_pool 同款只读真源）。

    返回 (kwargs|None, reason)；**不打印/返回密码以外的连接内容**，reason 只含
    异常类型名（避免异常消息里可能夹带的路径/凭据片段落日志）。
    """
    jev_dir = str(REPO / "jev-eval")
    try:
        if jev_dir not in sys.path:
            sys.path.insert(0, jev_dir)
        import jev_common  # noqa: PLC0415 — 延迟导入：不引入模块级副作用/不硬依赖

        raw = jev_common.DB_DSN
        return {
            "host": str(raw["host"]),
            "port": int(raw["port"]),
            "user": str(raw["user"]),
            "password": str(raw["password"]),
            "dbname": str(raw["dbname"]),
            "connect_timeout": PG_TIMEOUT_S,
        }, None
    except Exception as exc:  # noqa: BLE001 — fallback 不可用必须 fail-visible（不假绿）
        return None, "jev_common_unavailable:%s" % type(exc).__name__


def resolve_pg_connection(pg_dsn: str | None = None) -> dict:
    """解析 PG 只读连接目标；返回不含密码的连接描述（供日志/状态展示）。

    优先级（**不打印/不落盘凭据**）：
    1. 显式参数 ``pg_dsn``（``""`` = 显式禁用连接器，不回退；测试/运维用）；
    2. 环境变量 ``ZHICE_V2_DSN``；
    3. ``jev_common.DB_DSN`` fallback（``DW_PG_DSN_FALLBACK=0`` 可显式关闭）。

    返回 ``{source, dsn, kwargs, label, reason}``：
    - source: explicit / env / jev_common / none
    - dsn: libpq DSN 字符串（env/explicit）或 None（jev_common 用 kwargs）
    - kwargs: psycopg2 连接参数 dict（jev_common fallback）或 None
    - label: 逻辑连接标签（host:port/db，**不含凭据**）
    - reason: 不可用原因（fail-visible；不含凭据）
    """
    if pg_dsn is not None:
        dsn = str(pg_dsn).strip()
        if not dsn:
            return {"source": "none", "dsn": None, "kwargs": None,
                    "label": PG_CONNECTOR, "reason": "explicit_empty_dsn"}
        return {"source": "explicit", "dsn": dsn, "kwargs": None,
                "label": _pg_label_from_dsn(dsn), "reason": ""}
    env_dsn = os.environ.get(PG_DSN_ENV, "").strip()
    if env_dsn:
        return {"source": "env", "dsn": env_dsn, "kwargs": None,
                "label": _pg_label_from_dsn(env_dsn), "reason": ""}
    if os.environ.get(PG_DSN_FALLBACK_ENV, "1").strip() == "0":
        return {"source": "none", "dsn": None, "kwargs": None,
                "label": PG_CONNECTOR, "reason": "fallback_disabled"}
    kwargs, reason = _jev_common_connect_kwargs()
    if kwargs:
        return {"source": "jev_common", "dsn": None, "kwargs": kwargs,
                "label": _pg_label_from_kwargs(kwargs), "reason": ""}
    return {"source": "none", "dsn": None, "kwargs": None,
            "label": PG_CONNECTOR, "reason": reason or "dsn_unavailable"}


def _pg_connect_default(dsn):
    """只读连接工厂（wave2 默认）：libpq DSN 字符串或 psycopg2 kwargs dict。

    - DSN 字符串：兼容 SQLAlchemy 风格 URL → libpq（运维常直接复用 .env 的 DB_MAIN_URL）；
    - kwargs dict：jev_common fallback（连接密码只在内存中，不打印/不落盘）；
    - 异常向上抛（fail-visible）；连接建立后强制只读会话。
    """
    # 2026-11-08（K-CI-20261108-PGVALID）：空 DSN 属**参数校验**，必须在加载驱动之前判。
    # 原顺序先 import psycopg2 → 干净 runner 未装驱动时抛 driver_missing，把"参数为空"这个
    # 更根本的错误盖住了（test_empty_target_rejected 于是只在装了驱动的机器上过，云端必红）。
    target = None
    if not isinstance(dsn, dict):
        target = _normalize_dsn(str(dsn or ""))
        if not target:
            raise ConnectorUnavailable("missing_dsn: DSN 为空", status="missing_dsn")
    try:
        import psycopg2 as pg
    except ImportError:
        try:
            import psycopg as pg  # type: ignore
        except ImportError as exc:  # noqa: BLE001
            raise ConnectorUnavailable("driver_missing: 未安装 psycopg2/psycopg",
                                       status="driver_missing") from exc
    if isinstance(dsn, dict):
        connect_kwargs = dict(dsn)
        connect_kwargs.setdefault("connect_timeout", PG_TIMEOUT_S)
        con = pg.connect(**connect_kwargs)
    else:
        con = pg.connect(target, connect_timeout=PG_TIMEOUT_S)
    try:
        con.set_session(readonly=True, autocommit=True)
    except Exception:  # noqa: BLE001 — psycopg3 无 set_session：退化为只读事务外查询
        try:
            con.autocommit = True
        except Exception:  # noqa: BLE001
            pass
    return con


def discover_v2_tables(conn) -> list[str]:
    """只读发现 `public.v2_%` 全量（DWH-103 wave2，ADR-019 D3）。

    - SQL 固定：`SELECT tablename FROM pg_tables WHERE schemaname='public'
      AND tablename LIKE 'v2\\_%' ORDER BY 1`（`\\_` 转义下划线，% 为通配符）；
    - 返回**排序去重**后的表名列表（以发现结果为准，不硬编码数量）；
    - 连接/查询异常**向上抛**（fail-visible：不吞、不假绿）。
    """
    cur = conn.cursor()
    try:
        cur.execute(PG_DISCOVER_SQL)
        rows = cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    return sorted({str(row[0]) for row in rows if row and row[0]})


def discover_v2_metadata(conn, tables: list[str]) -> dict:
    """只读元数据：`public.<table>` 的 relkind/reltuples（available 口径 relkind='r'）。"""
    if not tables:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(PG_METADATA_SQL, (list(tables),))
        rows = cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    meta = {}
    for name, relkind, estimate in rows:
        try:
            est = None if estimate is None or float(estimate) < 0 else int(estimate)
        except (TypeError, ValueError):
            est = None
        meta[str(name)] = {"relkind": str(relkind or ""), "row_estimate": est}
    return meta


def _default_pg_probe(dsn: str, tables=None, connect=None) -> dict:
    """默认探针（wave2）：动态发现 public.v2_% 全量 + 元数据（只读）。

    返回 `{"schema": "public", "discovered": [...], "metadata": {table: {relkind, row_estimate}}}`；
    兼容 wave1 注入探针的返回形状（dict{(schema,table): reltuples} 或 set），由 `_probe_pg` 分派。
    """
    con = None
    try:
        con = (connect or _pg_connect_default)(dsn)
        names = discover_v2_tables(con)
        meta = discover_v2_metadata(con, names)
        return {"schema": PG_SCHEMA_WHITELIST[0], "discovered": names, "metadata": meta}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


def slugify(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    return s or "dataset"


class DatasetRegistry:
    MAX_JSON_BYTES = 50 * 1024 * 1024
    MAX_DIR_SCAN = 500

    def __init__(self, inventory_path: Path | str | None = None,
                 allowed_roots: list[Path] | None = None,
                 max_json_bytes: int | None = None,
                 pg_dsn: str | None = None,
                 pg_tables=None,
                 pg_probe=None,
                 pg_connect=None,
                 temp_whitelist=None,
                 temp_root: Path | str | None = None,
                 declared_refs_path: Path | str | None = None,
                 contract_baseline=None):
        if inventory_path is None:
            cands = sorted((REPO / "research/reports").glob("dataset_inventory_*.json"))
            if not cands:
                raise FileNotFoundError("未找到 dataset_inventory_*.json，请先跑全域盘点")
            inventory_path = cands[-1]
        self.inventory_path = Path(inventory_path)
        self.allowed_roots = allowed_roots or ALLOWED_ROOTS
        self.max_json_bytes = max_json_bytes or self.MAX_JSON_BYTES
        raw = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        self.datasets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for d in raw.get("datasets", []):
            if d.get("missing") or d.get("error"):
                continue
            base = slugify(str(d.get("suggested_id") or d.get("label") or "dataset"))
            did = base
            i = 2
            while did in seen:
                did = f"{base}_{i}"
                i += 1
            seen.add(did)
            owner = "DATA1"
            for hint, who in OWNER_HINTS:
                if hint in str(d.get("path", "")):
                    owner = who
                    break
            self.datasets.append({
                "dataset_id": did,
                "domain": d.get("domain"),
                "label": d.get("label"),
                "kind": d.get("kind"),
                "path": d.get("path"),
                "mb": d.get("mb"),
                "rows": d.get("rows"),
                "registered": bool(d.get("registered")),
                "exposed": bool(d.get("exposed")),
                "owner": owner,
                "access": "read_only",
                "unified_entry": True,
            })
        # 附加数据集级别名（去掉 domain 前缀后的短名冲突时保留 id）
        self._by_id = {d["dataset_id"]: d for d in self.datasets}
        # DWH-103 wave1：pg_table / contract_artifact 两节（不进入文件级 datasets/stats v1）
        # ACAD1/DW 同源只读 DSN：显式参数 > ZHICE_V2_DSN > jev_common.DB_DSN fallback。
        # 连接描述只保留 host:port/db 标签，密码仅在内存 kwargs 中（不打印/不落盘）。
        self.pg_connection = resolve_pg_connection(pg_dsn)
        self.pg_dsn = self.pg_connection.get("dsn") or ""
        self.pg_kwargs = self.pg_connection.get("kwargs")
        self.pg_connector_source = self.pg_connection.get("source") or "none"
        self.pg_connector_label = self.pg_connection.get("label") or PG_CONNECTOR
        self.pg_tables = [dict(x) for x in (pg_tables if pg_tables is not None else PG_WAVE1_TABLES)]
        self._pg_probe = pg_probe
        self._pg_connect = pg_connect
        self.pg_discovered = None  # wave2：动态发现集合（DSN 可用时）；None=wave1/缺 DSN 口径
        self.temp_root = Path(temp_root) if temp_root is not None else TEMP_ROOT
        self.contract_baseline = (contract_baseline if contract_baseline is not None
                                  else load_contract_baseline())
        if temp_whitelist is not None:
            # 调用方显式给的清单视为「测试/临时口径」，不套运维基线，避免隐式改写期望值
            self.temp_whitelist = [dict(x) for x in temp_whitelist]
        else:
            self.temp_whitelist = [self._apply_baseline(dict(x))
                                   for x in TEMP_CONTRACT_WHITELIST]
        self.connector_status = "unprobed"
        self.connector_error = ""
        self._disc_cache: dict = {}
        self.pg_datasets = self._build_pg_datasets()
        self.contract_artifacts = self._build_contract_artifacts()
        # IFS-POOL：显式 ref 登记（D1 6 data refs；缺失载体=false active，planned 必须 owner/due）
        self.declared_refs_path = Path(
            declared_refs_path
            or os.environ.get(DECLARED_REFS_ENV)
            or str(DECLARED_REFS_PATH)
        )
        self.declared_specs = load_declared_refs(self.declared_refs_path)
        self.declared_datasets = self._build_declared_datasets()
        self._extra_by_id = {d["dataset_id"]: d
                             for d in (self.pg_datasets + self.contract_artifacts
                                       + self.declared_datasets)}
        self._declared_by_alias: dict[str, dict] = {}
        for row in self.declared_datasets:
            for alias in row.get("ref_aliases") or []:
                self._declared_by_alias.setdefault(str(alias), row)

    # ---------- 查询 ----------
    def stats(self) -> dict:
        """v1 文件级口径（ADR-019 D6：字段/数值不变，314/265/72 基线受保护）。"""
        total = len(self.datasets)
        return {
            "inventory": str(self.inventory_path),
            "total": total,
            "registered": sum(1 for d in self.datasets if d["registered"]),
            "exposed": sum(1 for d in self.datasets if d["exposed"]),
            "by_domain": _count_by(self.datasets, "domain"),
            "by_kind": _count_by(self.datasets, "kind"),
        }

    def stats_v2(self) -> dict:
        """合并口径 v2（ADR-019 D6）：文件级 v1 + pg + contract_artifacts。

        "不假绿"：`all` 只计**可用**的 pg 表（连接器 ok 且表存在）与 **active** 契约产物；
        声明的 wave1 全量在 pg.total/contract_artifacts.total 中单列。
        """
        v1 = self.stats()
        pg_available = [d for d in self.pg_datasets if d.get("available")]
        art_active = [d for d in self.contract_artifacts if d.get("status") == "active"]
        # 合并口径"不假绿"：只按行自身 registered/exposed 标记求和（available 行未必已登记/已暴露）
        pg_registered = sum(1 for d in pg_available if d["registered"])
        pg_exposed = sum(1 for d in pg_available if d["exposed"])
        art_registered = sum(1 for d in art_active if d["registered"])
        art_exposed = sum(1 for d in art_active if d["exposed"])
        return {
            "denominator_version": "v2",
            "generated_at": _now(),
            "connector_status": self.connector_status,
            "connector_error": self.connector_error,
            "connector_source": self.pg_connector_source,
            "file_inventory": {
                "total": v1["total"], "registered": v1["registered"],
                "exposed": v1["exposed"], "by_kind": v1["by_kind"],
            },
            "pg": {
                "total": len(self.pg_datasets),
                "available": len(pg_available),
                "registered": sum(1 for d in self.pg_datasets if d["registered"]),
                "exposed": sum(1 for d in self.pg_datasets if d["exposed"]),
                "connector_status": self.connector_status,
                "connector_source": self.pg_connector_source,
                "by_status": _count_by(self.pg_datasets, "status"),
            },
            "contract_artifacts": {
                "total": len(self.contract_artifacts),
                "active": len(art_active),
                "missing": sum(1 for d in self.contract_artifacts if d.get("status") == "missing"),
                "mismatch": sum(1 for d in self.contract_artifacts
                                if d.get("status") == "sha256_mismatch"),
                "drift": sum(1 for d in self.contract_artifacts
                             if d.get("status") in CONTRACT_DRIFT_STATUSES),
                "by_status": _count_by(self.contract_artifacts, "status"),
            },
            "all": {
                "total": v1["total"] + len(pg_available) + len(art_active),
                "registered": v1["registered"] + pg_registered + art_registered,
                "exposed": v1["exposed"] + pg_exposed + art_exposed,
            },
        }

    def list(self, domain: str | None = None, q: str | None = None,
             registered: bool | None = None, exposed: bool | None = None,
             limit: int = 50, kinds=None) -> list[dict]:
        """默认仅文件级（向后兼容 v1）；kinds='all' 或指定 kind 时含 pg/契约产物。"""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        if limit < 0:
            limit = 0  # 负值不得触发 Python 负切片语义（会静默截掉尾部元素）
        if kinds is None:
            pool = self.datasets
        elif kinds == "all":
            pool = (self.datasets + self.pg_datasets + self.contract_artifacts
                    + self.declared_datasets)
        else:
            wanted = {kinds} if isinstance(kinds, str) else set(kinds)
            pool = [d for d in (self.datasets + self.pg_datasets
                                + self.contract_artifacts + self.declared_datasets)
                    if d["kind"] in wanted]
        out = []
        ql = (q or "").lower()
        for d in pool:
            if domain and d["domain"] != domain:
                continue
            if registered is not None and d["registered"] != registered:
                continue
            if exposed is not None and d["exposed"] != exposed:
                continue
            if ql and ql not in (str(d["label"]) + " " + str(d["path"]) + " " + d["dataset_id"]).lower():
                continue
            out.append(d)
        return out[:limit]

    def get(self, dataset_id: str) -> dict | None:
        return self._by_id.get(dataset_id) or self._extra_by_id.get(dataset_id)

    def resolve_ref(self, ref: str) -> dict | None:
        """manifest ref 权威查表（ADR-019 D5）：dataset/pg/<token>、dataset/file/<token>。

        过渡期只覆盖新 kind + 文件级 token 兜底；文件级全量 ref_aliases 表化属 wave2（ADR D5 目标态）。
        """
        if not ref or "/" not in ref:
            return None
        for row in self.pg_datasets + self.contract_artifacts:
            if ref in (row.get("ref_aliases") or []):
                return row
        kind, rest = ref.split("/", 1)
        token = rest.split("/")[-1]
        if kind == "pg":
            for row in self.pg_datasets:
                if token in (row["physical"]["table"], row["dataset_id"]):
                    return row
        elif kind == "file":
            for row in self.contract_artifacts:
                if token in (row.get("artifact_id"), row["dataset_id"]):
                    return row
        elif kind in ("dir", "sqlite_table", "corpus_layer", "registry"):
            for row in self.datasets:
                if token in (row["dataset_id"], str(row.get("label") or "").split(".")[-1]):
                    return row
        # IFS-POOL：显式声明的 dataset/file/<token> ref（D1 6 data refs 等）
        declared = self._declared_by_alias.get(ref) or self._declared_by_alias.get(token)
        if declared is not None:
            return declared
        return None

    # ---------- IFS-POOL：显式 ref 登记（D1 6 data refs） ----------
    def _build_declared_datasets(self) -> list[dict]:
        """显式声明 ref → registry 行。

        载体存在（file/dir）= active；声明 planned 且载体未落盘 = planned（必须 owner/due）；
        声明 active 但载体缺失 = declared_missing（fail-visible，不假绿成 active）。
        只读：仅 stat 路径，不创建/不写任何文件。
        """
        rows: list[dict] = []
        for spec in self.declared_specs:
            ref = str(spec["ref"])
            token = ref.split("/")[-1]
            rel = str(spec.get("path") or "")
            path = Path(rel) if rel else None
            if path is not None and not path.is_absolute():
                path = REPO / path
            carrier = "missing"
            try:
                if path is not None and path.is_dir():
                    carrier = "dir"
                elif path is not None and path.is_file():
                    carrier = "file"
            except OSError:
                carrier = "unreadable"
            declared_status = str(spec.get("status") or "planned")
            if carrier in ("dir", "file"):
                status = "active"
            elif declared_status == "planned":
                status = "planned"
            else:
                status = "declared_missing"
            rows.append({
                "dataset_id": str(spec.get("dataset_id")
                                  or ("declared_%s" % slugify(token))),
                "domain": spec.get("domain") or DECLARED_DOMAIN,
                "label": spec.get("label") or token,
                "kind": "file_dataset",
                "path": str(path) if path is not None else "",
                "owner": str(spec.get("owner") or ""),
                "authority": spec.get("authority"),
                "canonical_ref": spec.get("canonical_ref"),
                "schema_ref": spec.get("schema_ref"),
                "producer_job": spec.get("producer_job"),
                "declared_status": declared_status,
                "status": status,
                "carrier": carrier,
                "registered": True,
                "exposed": False,
                "access": "read_only",
                "ref": ref,
                "ref_aliases": [token, ref],
                "due": spec.get("due"),
                "due_source": spec.get("due_source"),
                "reason": spec.get("reason"),
                "declared_at": spec.get("declared_at"),
                "note": spec.get("note"),
                "unified_entry": True,
            })
        return rows

    def declared_summary(self) -> dict:
        """IFS-POOL 显式 ref 登记汇总（只读报告；不进入 stats v1 基线）。"""
        return {
            "path": str(self.declared_refs_path),
            "total": len(self.declared_datasets),
            "active": [d["ref"] for d in self.declared_datasets
                       if d["status"] == "active"],
            "planned": [{"ref": d["ref"], "owner": d["owner"], "due": d.get("due")}
                        for d in self.declared_datasets
                        if d["status"] == "planned"],
            "declared_missing": [d["ref"] for d in self.declared_datasets
                                 if d["status"] == "declared_missing"],
        }

    # ---------- DWH-103：pg_table 构建/探测/取样 ----------
    def _build_pg_datasets(self) -> list[dict]:
        rows = []
        for spec in self.pg_tables:
            table = str(spec.get("table") or "").strip()
            schema = str(spec.get("schema") or PG_SCHEMA_WHITELIST[0])
            if not table.startswith("v2_") or not _TABLE_RE.fullmatch(table):
                continue  # wave1 命名约束：仅 v2_* 业务表
            if schema not in PG_SCHEMA_WHITELIST:
                continue
            rows.append({
                "dataset_id": "pg_%s_%s" % (schema, table),
                "domain": spec.get("domain") or PG_DOMAIN,
                "label": "%s.%s" % (schema, table),
                "kind": "pg_table",
                "path": PG_CONNECTOR,
                "connector": PG_CONNECTOR,
                "connector_source": self.pg_connector_source,
                "physical": {"schema": schema, "table": table},
                "ref": "dataset/pg/%s" % table,
                "owner": spec.get("owner") or "DATA1",
                "registered": bool(spec.get("registered", True)),
                "exposed": bool(spec.get("exposed", True)),
                "access": "read_only",
                "freshness": None,       # ADR-019 D2/P7：v0 不声明（防假新鲜度）
                "row_estimate": None,
                "status": "planned",
                "available": False,
                "ref_aliases": [table, "dataset/pg/%s" % table],
                "unified_entry": True,
            })
        self._probe_pg(rows)
        return rows

    def _owner_spec(self, table: str) -> dict:
        """wave1 固定清单中该表的声明（owner/registered/exposed）；动态发现时用于合并声明。"""
        for spec in self.pg_tables:
            if str(spec.get("table") or "") == table:
                return dict(spec)
        return {}

    def _pg_dynamic_row(self, schema: str, table: str, meta: dict) -> dict:
        """wave2 动态发现行：发现即登记（不硬编码数量），owner 优先用 wave1 声明否则占位。"""
        spec = self._owner_spec(table)
        known = bool(spec)
        return {
            "dataset_id": "pg_%s_%s" % (schema, table),
            "domain": spec.get("domain") or PG_DOMAIN,
            "label": "%s.%s" % (schema, table),
            "kind": "pg_table",
            "path": PG_CONNECTOR,
            "connector": PG_CONNECTOR,
            "connector_source": self.pg_connector_source,
            "physical": {"schema": schema, "table": table},
            "ref": "dataset/pg/%s" % table,
            "owner": spec.get("owner") or PG_DYNAMIC_OWNER_DEFAULT,
            "owner_source": "declared" if known else "dynamic_placeholder",
            "registered": True,      # wave2：动态发现即纳登记（ADR-019 D3：83/83）
            "exposed": bool(spec.get("exposed", False)),
            "access": "read_only",
            "freshness": None,       # ADR-019 D2/P7：v0 不声明（防假新鲜度）
            "row_estimate": meta.get("row_estimate"),
            "relkind": meta.get("relkind") or "",
            "status": "planned",
            "available": False,
            "ref_aliases": [table, "dataset/pg/%s" % table],
            "unified_entry": True,
        }

    def _probe_pg(self, rows: list[dict]) -> None:
        """连接探测（ADR-019 D2/D3 wave2）。

        - 无 DSN → wave1 固定清单 + missing_dsn（不报错、不假绿）；
        - 注入探针（wave1/测试）→ 以注入结果标记固定清单（保持 wave1 兼容）；
        - 默认探针（DSN 可用）→ **动态发现 public.v2_% 全量**，行集合以发现结果为准
          （不硬编码数量）；available = `pg_class.relkind='r'` 的可读元数据数；
          发现/元数据异常 fail-visible（不吞）。
        """
        if not rows:
            self.connector_status = "disabled"
            return
        target = self.pg_dsn or self.pg_kwargs
        if not target:
            self.connector_status = "missing_dsn"
            if self.pg_connection.get("reason"):
                self.connector_error = str(self.pg_connection["reason"])
            for row in rows:
                row["status"] = "missing_dsn"
            return
        tables = [row["physical"]["table"] for row in rows]
        try:
            if self._pg_probe is not None:  # wave1 兼容：注入探针只覆盖固定清单
                found = self._pg_probe(target, tables)
            else:                            # wave2：默认探针动态发现全量
                found = _default_pg_probe(target, tables, connect=self._pg_connect)
        except Exception as exc:  # noqa: BLE001 — fail-visible，不静默跳过
            self.connector_status = getattr(exc, "status", "connector_unavailable")
            self.connector_error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            for row in rows:
                row["status"] = self.connector_status
            return
        self.connector_status = "ok"
        if isinstance(found, dict) and found.get("discovered") is not None:
            # wave2：动态发现全量（发现结果即行集合，不硬编码 83/84）
            names = [str(n) for n in found.get("discovered") or []]
            meta_all = found.get("metadata") or {}
            schema = str(found.get("schema") or PG_SCHEMA_WHITELIST[0])
            dynamic = []
            for table in names:
                if not _TABLE_RE.fullmatch(table):
                    continue  # 发现结果也过命名白名单（安全兜底）
                dynamic.append(self._pg_dynamic_row(schema, table, meta_all.get(table) or {}))
            rows[:] = dynamic
            self.pg_discovered = set(names)
            for row in rows:
                row["available"] = row.get("relkind") in PG_AVAILABLE_RELKINDS
                row["status"] = "active" if row["available"] else "metadata_unreadable"
            return
        # wave1/注入探针：固定清单存在性
        for row in rows:
            key = (row["physical"]["schema"], row["physical"]["table"])
            hit = key in found or row["physical"]["table"] in found
            row["status"] = "active" if hit else "table_missing"
            row["available"] = hit
            if isinstance(found, dict):
                est = found.get(key, found.get(row["physical"]["table"]))
                row["row_estimate"] = est

    def _sample_pg(self, d: dict, n: int) -> dict:
        if self.connector_status != "ok":
            raise ConnectorUnavailable(
                "pg_table 不可用（connector_status=%s）%s"
                % (self.connector_status,
                   ("：" + self.connector_error) if self.connector_error else ""),
                status=self.connector_status,
            )
        if not d.get("available"):
            raise ConnectorUnavailable(
                "pg_table 未在实例中命中（status=%s）: %s"
                % (d.get("status"), d.get("label")),
                status=d.get("status") or "table_missing",
            )
        schema = str(d["physical"]["schema"])
        table = str(d["physical"]["table"])
        if schema not in PG_SCHEMA_WHITELIST or not _TABLE_RE.fullmatch(schema) \
                or not _TABLE_RE.fullmatch(table):
            raise PermissionError("pg_table 不在 schema/命名白名单: %s.%s" % (schema, table))
        discovered = getattr(self, "pg_discovered", None)
        if discovered is not None and table not in discovered:
            raise PermissionError(
                "pg_table 未在发现集合（拒绝任意标识符）: %s.%s" % (schema, table))
        con = None
        try:
            target = self.pg_dsn or self.pg_kwargs
            con = (self._pg_connect or _pg_connect_default)(target)
            cur = con.cursor()
            cur.execute('SELECT * FROM "%s"."%s" LIMIT %%s' % (schema, table), (int(n),))
            cols = [c[0] for c in cur.description or []]
            rows = [list(r) for r in cur.fetchall()]
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
        return {"dataset_id": d["dataset_id"], "kind": "pg_table",
                "connector": PG_CONNECTOR, "physical": d["physical"],
                "columns": cols, "sample": rows, "row_estimate": d.get("row_estimate")}

    # ---------- DWH-103：contract_artifact 构建/白名单/取样 ----------
    def _apply_baseline(self, spec: dict) -> dict:
        """用运维基线覆盖出厂 sha256；未登记的 artifact 保持源码常量。"""
        aid = str(spec.get("artifact_id") or "")
        rec = (self.contract_baseline.get("baselines") or {}).get(aid)
        if not rec:
            return spec
        spec["sha256_factory"] = spec.get("sha256")
        spec["sha256"] = rec["sha256"]
        spec["baseline_confirmed_at"] = rec.get("confirmed_at")
        spec["baseline_confirmed_by"] = rec.get("confirmed_by")
        return spec

    def _build_contract_artifacts(self) -> list[dict]:
        rows = []
        for spec in self.temp_whitelist:
            aid = str(spec.get("artifact_id") or "").strip()
            raw_path = str(spec.get("path") or "").strip()
            if not aid or not raw_path:
                continue
            p = Path(raw_path)
            if not p.is_absolute():
                continue
            row = {
                "dataset_id": "tmp_%s" % slugify(aid),
                "domain": spec.get("domain") or "平台运行",
                "label": aid,
                "kind": "contract_artifact",
                "path": str(p),
                "artifact_id": aid,
                "owner": spec.get("owner") or "A0",
                "produced_by_job": spec.get("produced_by_job"),
                "schema_ref": spec.get("schema_ref"),
                "retention_days": spec.get("retention_days"),
                "sha256": spec.get("sha256"),
                "access": "read_only",
                "registered": True,
                "exposed": True,
                "status": "active",
                "available": False,
                "ref_aliases": [aid, "dataset/file/%s" % aid],
                "unified_entry": True,
            }
            row.update(self._contract_state(row))
            rows.append(row)
        return rows

    def _contract_state(self, row: dict) -> dict:
        p = Path(str(row["path"]))
        try:
            if p.is_dir():
                return {"status": "not_a_file", "available": False,
                        "reason": "contract_artifact 不允许目录（禁递归扫描）"}
            if not p.is_file():
                return {"status": "missing", "available": False, "reason": "白名单契约产物缺失"}
            actual = _sha256_file(p)
            expected = row.get("sha256")
            if expected and actual != expected:
                if row.get("produced_by_job"):
                    # 定时产物字节每次重跑都变，与基线不同属常态 → drift（info），不告警、仍可读
                    return {"status": "drift", "available": True,
                            "sha256_expected": expected, "sha256_actual": actual,
                            "drift_since": _mtime_iso(p),
                            "baseline_confirmed_at": row.get("baseline_confirmed_at"),
                            "size_bytes": p.stat().st_size,
                            "reason": ("定时产物字节变动（%s）；非篡改，登记 drift 不告警"
                                       % row.get("produced_by_job"))}
                return {"status": "sha256_mismatch", "available": False,
                        "reason": "sha256 漂移：expected %s actual %s" % (expected, actual)}
            return {"status": "active", "available": True,
                    "sha256_actual": actual or expected, "size_bytes": p.stat().st_size}
        except OSError as exc:
            return {"status": "missing", "available": False,
                    "reason": "读取失败: %s" % str(exc)[:120]}

    def contract_path_allowed(self, path) -> bool:
        """白名单判定：必须精确命中显式清单，且是 temp_root 的**直属文件**（禁递归/任意路径）。"""
        try:
            p = Path(str(path))
        except (TypeError, ValueError):
            return False
        if not p.is_absolute():
            return False
        try:
            resolved = p.resolve(strict=False)
            root = Path(str(self.temp_root)).resolve(strict=False)
        except OSError:
            return False
        if resolved == root or resolved.parent != root:  # 仅直属文件，禁子目录递归
            return False
        for spec in self.temp_whitelist:
            try:
                if Path(str(spec.get("path") or "")).resolve(strict=False) == resolved:
                    return True
            except OSError:
                continue
        return False

    def _sample_contract(self, d: dict) -> dict:
        p = Path(str(d["path"]))
        if not self.contract_path_allowed(p):
            raise PermissionError("契约产物不在显式白名单（禁递归/任意路径）: %s" % p)
        if p.is_dir():
            raise ContractArtifactUnavailable("contract_artifact 不允许目录（禁递归扫描）: %s" % p)
        if not p.is_file():
            raise ContractArtifactUnavailable("契约产物缺失（fail-visible）: %s" % p)
        actual = _sha256_file(p)
        expected = d.get("sha256")
        drift = None
        if expected and actual != expected:
            if not d.get("produced_by_job"):
                raise ContractArtifactUnavailable(
                    "契约产物 sha256 漂移（fail-visible；可能被改写）: %s" % p)
            # 定时产物：字节变动属常态，取样仍放行，但把 drift 一并带回调用方（可审计）
            drift = {"sha256_expected": expected, "sha256_actual": actual,
                     "drift_since": _mtime_iso(p),
                     "produced_by_job": d.get("produced_by_job")}
        base = {"dataset_id": d["dataset_id"], "kind": "contract_artifact",
                "artifact_id": d.get("artifact_id"), "path": str(p),
                "schema_ref": d.get("schema_ref"), "owner": d.get("owner"),
                "status": "drift" if drift else "active", "sha256": actual,
                "size_bytes": p.stat().st_size}
        if drift:
            base["drift"] = drift
            base["note_drift"] = ("定时产物字节已变动（非篡改）：expected≠actual，"
                                  "登记 drift 不告警；需人工确认时跑 refresh_contract_baseline.py")
        if p.stat().st_size > self.max_json_bytes:
            base["preview_head"] = p.read_text(encoding="utf-8", errors="ignore")[:2000]
            base["note"] = "大 JSON：仅预览头部（>max_json_bytes）"
            return base
        text = p.read_text(encoding="utf-8", errors="ignore")
        if p.suffix == ".jsonl":
            lines = [x for x in text.splitlines() if x.strip()][:3]
            base["sample"] = [json.loads(x) if _is_json(x) else x for x in lines]
            return base
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise ContractArtifactUnavailable(
                "契约产物 JSON 解析失败（fail-visible）: %s" % str(exc)[:120])
        base["sample"] = obj if not isinstance(obj, list) else obj[:3]
        return base

    # ---------- 取数 ----------
    def _resolve(self, d: dict) -> Path:
        p = Path(str(d["path"]))
        if not p.is_absolute():
            p = REPO / p
        rp = p.resolve()
        if not any(str(rp).lower().startswith(str(Path(r).resolve()).lower()) for r in self.allowed_roots):
            raise PermissionError(f"路径不在白名单: {rp}")
        return rp

    def _latest_mtime(self, p: Path) -> str | None:
        try:
            if p.is_file():
                return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            best = 0.0
            scanned = 0
            for cur, dirs, files in os.walk(p):
                for f in files:
                    try:
                        m = os.path.getmtime(os.path.join(cur, f))
                    except OSError:
                        continue
                    best = max(best, m)
                    scanned += 1
                    if scanned >= self.MAX_DIR_SCAN:
                        break
                if scanned >= self.MAX_DIR_SCAN:
                    break
            return datetime.datetime.fromtimestamp(best).strftime("%Y-%m-%d %H:%M") if best else None
        except Exception:  # noqa: BLE001
            return None

    def sample(self, dataset_id: str, n: int = 3) -> dict:
        d = self.get(dataset_id)
        if not d:
            raise KeyError(f"未知 dataset_id: {dataset_id}")
        kind = d.get("kind")
        if kind == "pg_table":
            return self._sample_pg(d, n)
        if kind == "contract_artifact":
            return self._sample_contract(d)
        p = self._resolve(d)
        if not p.exists():
            return {"dataset_id": dataset_id, "exists": False, "path": str(p)}
        if kind == "sqlite_table":
            # label 形如 db.table
            table = str(d["label"]).split(".")[-1]
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            try:
                cur = con.execute(f'SELECT * FROM "{table}" LIMIT ?', (n,))
                cols = [c[0] for c in cur.description or []]
                rows = [list(r) for r in cur.fetchall()]
            finally:
                con.close()
            return {"dataset_id": dataset_id, "kind": kind, "table": table, "columns": cols, "sample": rows}
        if p.is_file() and p.suffix == ".parquet":
            import pyarrow.parquet as pq
            t = pq.read_table(p).slice(0, n)
            return {"dataset_id": dataset_id, "kind": kind, "columns": t.column_names, "sample": t.to_pylist()}
        if p.is_file() and p.suffix in (".json", ".jsonl"):
            if p.stat().st_size > self.max_json_bytes:
                head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                return {"dataset_id": dataset_id, "kind": kind, "path": str(p),
                        "size_mb": round(p.stat().st_size / 1e6, 1),
                        "preview_head": head, "note": "大 JSON：仅预览头部（>max_json_bytes）"}
            text = p.read_text(encoding="utf-8", errors="ignore")
            if p.suffix == ".jsonl":
                lines = [x for x in text.splitlines() if x.strip()][:n]
                return {"dataset_id": dataset_id, "kind": kind, "sample": [json.loads(x) if _is_json(x) else x for x in lines]}
            obj = json.loads(text)
            return {"dataset_id": dataset_id, "kind": kind, "sample": obj if not isinstance(obj, list) else obj[:n]}
        # 目录/其他：有界采样（顶层结构 + 最新文件），避免大目录全量 rglob
        if p.is_dir():
            tops = sorted(os.listdir(p))[:n]
            newest = []
            seen = 0
            for cur, dirs, files in os.walk(p):
                for f in files:
                    fp = os.path.join(cur, f)
                    try:
                        newest.append((os.path.getmtime(fp), os.path.relpath(fp, p)))
                    except OSError:
                        pass
                    seen += 1
                    if seen >= 500 or len(newest) >= 50:
                        break
                if seen >= 500 or len(newest) >= 50:
                    break
            newest.sort(reverse=True)
            return {"dataset_id": dataset_id, "kind": kind, "path": str(p),
                    "freshness": self._latest_mtime(p),
                    "top_entries": tops,
                    "newest_files": [x[1] for x in newest[:n]],
                    "scan_truncated": True}
        return {"dataset_id": dataset_id, "kind": kind, "path": str(p),
                "freshness": self._latest_mtime(p), "files": [p.name],
                "file_count": 1}

    # ---------- S3-06 P1：通用 query（2026-11-02 task-50） ----------
    def query(self, dataset_id: str, where=None, order=None, limit: int = 100,
              offset: int = 0, since=None, columns=None, count: bool = False) -> dict:
        """通用只读查询（三面同源：CLI/HTTP/MCP）。

        参数：
          dataset_id 数据集 id；where 结构化条件（[{field,op,value}] / dict / JSON 串，
          op ∈ = != > >= < <= in like；**不接受原始 SQL**）；order [{field,desc}]（≤2 键，仅
          sqlite / 小 parquet 支持）；limit 默认 100，上限 1000；offset 上限 100000；
          since YYYY-MM-DD/YYYYMMDD 时间下界；columns 列投影白名单；count 返回总数。

        载体（P1）：sqlite_table → 参数化 WHERE/ORDER BY；parquet 文件/目录 → pyarrow
        dataset 过滤 + 列投影；jsonl → columns+offset/limit 基础行（条件/排序/计数 P2）；
        其余载体（pg/契约/jsonl 复杂条件/混合目录）→ QueryNotSupported（fail-visible）。

        返回契约：ok/dataset_id/kind/carrier/state(ok|no_data)/columns/rows/count/
        next_offset/has_more/truncated/evidence{engine,elapsed_ms,scanned_rows,...}。
        失败：KeyError（未知 id）/PermissionError（越权）/QueryParamError(400)/
        QueryNotSupported(409)——均 fail-visible，不回退 sample 假绿。
        """
        t0 = time.monotonic()
        d = self.get(dataset_id)
        if not d:
            raise KeyError("未知 dataset_id: %s" % dataset_id)
        try:
            limit = int(limit)
            offset = int(offset)
        except (TypeError, ValueError):
            raise QueryParamError("limit/offset 必须是整数")
        if limit < 1 or limit > QUERY_MAX_LIMIT:
            raise QueryParamError("limit 需在 1~%d（收到 %d）" % (QUERY_MAX_LIMIT, limit))
        if offset < 0 or offset > QUERY_MAX_OFFSET:
            raise QueryParamError("offset 需在 0~%d（收到 %d）" % (QUERY_MAX_OFFSET, offset))
        where_l = _norm_where(where)
        order_l = _norm_order(order)
        cols = _norm_columns(columns)
        since_n = _norm_since(since)
        kind = d.get("kind")
        if kind in ("pg_table", "contract_artifact"):
            _audit_query({"ts": _now(), "dataset_id": dataset_id, "kind": kind,
                          "carrier": kind, "error": "QueryNotSupported: %s 载体 P1/P2 不支持行级 query" % kind,
                          "limit": limit, "offset": offset})
            raise QueryNotSupported(
                "%s 载体 P1/P2 不支持行级 query（契约产物走 sample；PG 走 P3）" % kind,
                {"carrier": kind, "hint": "sample() 可用"})
        p = self._resolve(d)
        disc = self._discover(p)
        carrier = self._carrier_for(kind, p, disc)
        base = {"dataset_id": dataset_id, "kind": kind, "carrier": carrier,
                "ok": True, "truncated": bool(disc.get("parquet_truncated")
                                              or disc.get("jsonl_truncated"))}
        try:
            if carrier in ("sqlite_table",):
                out = self._query_sqlite(p, d, where_l, order_l, limit, offset, since_n, cols, count)
            elif carrier in ("parquet_file", "dir_parquet"):
                out = self._query_parquet(p, disc, where_l, order_l, limit, offset,
                                          since_n, cols, count)
            elif carrier in ("jsonl_file", "dir_jsonl"):
                out = self._query_jsonl(p, disc, where_l, order_l, limit, offset,
                                        since_n, cols, count)
            elif carrier in ("files", "dir_files"):
                out = self._query_files(p, disc, where_l, order_l, limit, offset,
                                        since_n, cols, count)
            elif carrier == "dir_empty":
                out = self._query_no_data("目录为空（0 文件）：P1 无行可查", disc)
            elif carrier == "missing":
                out = self._query_no_data("数据集路径不存在", disc)
            else:
                raise QueryNotSupported(
                    "载体 %s 的 P1 行级 query 未实现（P2 文件级/语料索引）" % carrier,
                    {"carrier": carrier, "path": str(p),
                     "hint": "可用 sample()/list() 获取条目清单"})
        except QueryError as exc:
            _audit_query({"ts": _now(), "dataset_id": dataset_id, "kind": kind,
                          "carrier": carrier, "error": "%s: %s" % (type(exc).__name__, exc),
                          "limit": limit, "offset": offset})
            raise
        except BaseException as exc:  # noqa: BLE001 — 审计后原样抛出（fail-visible）
            _audit_query({"ts": _now(), "dataset_id": dataset_id, "kind": kind,
                          "carrier": carrier, "error": "%s: %s" % (type(exc).__name__, exc),
                          "limit": limit, "offset": offset})
            raise
        elapsed = int((time.monotonic() - t0) * 1000)
        out.update(base)
        out["evidence"] = dict(out.get("evidence") or {})
        out["evidence"]["elapsed_ms"] = elapsed
        out.setdefault("has_more", False)
        out["next_offset"] = (offset + len(out.get("rows") or [])) if out["has_more"] else None
        _audit_query({"ts": _now(), "dataset_id": dataset_id, "kind": kind,
                      "carrier": carrier, "state": out.get("state"),
                      "where_conditions": len(where_l), "order_keys": len(order_l),
                      "since": since_n["ymd"] if since_n else None, "limit": limit,
                      "offset": offset, "count_req": bool(count),
                      "rows": len(out.get("rows") or []), "count": out.get("count"),
                      "truncated": out.get("truncated"), "elapsed_ms": elapsed})
        return out

    # ---------- query 载体内核 ----------
    def _discover(self, p: Path) -> dict:
        """一次有界遍历：发现 parquet/jsonl 文件（超上限显式 truncated，不递归无界）。

        parquet 文件超过 QUERY_MAX_FILES 时取「头部一半 + 尾部一半」窗口（避免只取到
        空分片/历史空 part；尾窗口由 deque 维护，不存全量路径），截断状态显式可见。
        结果按路径缓存（同一 registry 进程内多次 query 复用，明细 dict 可变用于惰性修正）。
        """
        from collections import deque
        key = str(p)
        cached = self._disc_cache.get(key)
        if cached is not None:
            return cached
        out = {"parquet": [], "parquet_truncated": False, "parquet_total": 0,
               "jsonl": [], "jsonl_truncated": False, "jsonl_total": 0,
               "other_files": 0, "files_total": 0, "empty": False,
               "files_meta": [], "files_truncated": False,
               "dirs_scanned": 0, "dirs_truncated": False, "max_depth": 0}
        if p.is_file():
            out["files_total"] = 1
            out["parquet_total"] = 1 if p.name.lower().endswith(".parquet") else 0
            low = p.name.lower()
            if low.endswith(".parquet"):
                out["parquet"] = [p]
            elif low.endswith((".jsonl", ".ndjson", ".json", ".jsonl.gz", ".json.gz")):
                out["jsonl"] = [p]
                out["jsonl_total"] = 1
            else:
                out["other_files"] = 1
            out["files_meta"] = [self._file_meta(p, p.parent, 0)]
            return out
        if not p.is_dir():
            return out
        cap_head = max(1, QUERY_MAX_FILES // 2)
        cap_tail = max(1, QUERY_MAX_FILES - cap_head)
        head, tail = [], deque(maxlen=cap_tail)
        for cur, dirs, fnames in os.walk(p):
            out["dirs_scanned"] += 1
            dirs.sort()
            fnames.sort()
            depth = len(Path(cur).relative_to(p).parts) if Path(cur) != p else 0
            out["max_depth"] = max(out["max_depth"], depth)
            if depth > QUERY_FILES_MAX_DEPTH:
                dirs[:] = []
                continue
            for fn in fnames:
                out["files_total"] += 1
                low = fn.lower()
                if low.endswith(".parquet"):
                    out["parquet_total"] += 1
                    if len(head) < cap_head:
                        head.append(Path(cur) / fn)
                    else:
                        tail.append(Path(cur) / fn)
                elif low.endswith((".jsonl", ".ndjson", ".json", ".jsonl.gz", ".json.gz")):
                    out["jsonl_total"] += 1
                    if len(out["jsonl"]) < QUERY_MAX_JSONL_FILES:
                        out["jsonl"].append(Path(cur) / fn)
                    else:
                        out["jsonl_truncated"] = True
                else:
                    out["other_files"] += 1
                if len(out["files_meta"]) < QUERY_FILES_MAX:
                    out["files_meta"].append(self._file_meta(Path(cur) / fn, p, depth))
                else:
                    out["files_truncated"] = True
            if out["dirs_scanned"] >= 2000:
                out["dirs_truncated"] = True
                break
        out["parquet"] = head + list(tail)
        out["parquet_truncated"] = out["parquet_total"] > len(out["parquet"])
        out["jsonl_truncated"] = out["jsonl_truncated"] or (
            out["jsonl_total"] > len(out["jsonl"]))
        out["empty"] = out["files_total"] == 0
        self._disc_cache[key] = out
        return out

    @staticmethod
    def _file_meta(fp: Path, root: Path, depth: int) -> dict:
        """文件级元数据（相对路径/扩展名/大小/mtime，只读 stat）。"""
        try:
            st = fp.stat()
        except OSError:
            return {"path": fp.name, "name": fp.name, "ext": fp.suffix.lower(),
                    "size": None, "mtime": None, "depth": depth}
        try:
            rel = str(fp.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = fp.name
        return {"path": rel, "name": fp.name, "ext": fp.suffix.lower(),
                "size": st.st_size, "mtime": st.st_mtime, "depth": depth}

    @staticmethod
    def _carrier_for(kind, p: Path, disc: dict) -> str:
        if kind in ("pg_table", "contract_artifact"):
            return kind
        if not p.exists():
            return "missing"
        if kind == "sqlite_table":
            return "sqlite_table"
        if disc["parquet"]:
            return "parquet_file" if p.is_file() else "dir_parquet"
        if disc["jsonl"]:
            # 纯 jsonl/json 目录 → 行级；json + 其他文件混布（mixed/nested）→ 文件级四维
            if disc["other_files"] > 0 and disc.get("files_meta"):
                return "files" if p.is_file() else "dir_files"
            return "jsonl_file" if p.is_file() else "dir_jsonl"
        if disc["empty"]:
            return "dir_empty"
        # 2026-11-02 task-57 P2：mixed/nested/语料层 → 文件级载体（只读文件清单，L/P/F/A）
        if disc.get("files_meta"):
            return "files" if p.is_file() else "dir_files"
        return "unsupported"

    @staticmethod
    def _check_field(field: str, names: list) -> None:
        if field not in names:
            raise QueryParamError("未知字段：%r" % field, {"allowed_fields": list(names)})

    @staticmethod
    def _query_no_data(reason: str, disc: dict) -> dict:
        return {"state": "no_data", "reason": reason, "columns": [], "rows": [],
                "count": None, "has_more": False, "truncated": False,
                "evidence": {"engine": "none", "files_total": disc.get("files_total"),
                             "files_scanned": 0}}

    # ---------- sqlite ----------
    @staticmethod
    def _sqlite_pred(w: dict) -> tuple:
        col = '"%s"' % w["field"].replace('"', '""')
        op = w["op"]
        val = w["value"]
        if op == "in":
            return "%s IN (%s)" % (col, ",".join(["?"] * len(val))), list(val)
        if op == "like":
            return "%s LIKE ?" % col, ["%" + _like_contains(str(val)) + "%"]
        return "%s %s ?" % (col, op), [val]

    def _query_sqlite(self, p: Path, d: dict, where, order, limit, offset,
                      since, columns, count) -> dict:
        table = str(d.get("label") or "").split(".")[-1].strip()
        if not _TABLE_RE.fullmatch(table.lower()):
            raise QueryParamError("sqlite 表名非法：%r" % table)
        qtable = table.replace('"', '""')
        con = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True, timeout=5)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA busy_timeout=5000")
            info = [(r[1], str(r[2] or "")) for r in
                    con.execute('PRAGMA table_info("%s")' % qtable).fetchall()]
            names = [n for n, _ in info]
            types = dict(info)
            if not names:
                raise QueryNotSupported("sqlite 表不存在或为空：%s" % table,
                                        {"table": table, "db": str(p)})
            for w in where:
                self._check_field(w["field"], names)
            for o in order:
                self._check_field(o["field"], names)
            select = list(columns) if columns else list(names)
            for c in select:
                self._check_field(c, names)
            parts, params = [], []
            for w in where:
                sql, vals = self._sqlite_pred(w)
                parts.append(sql)
                params.extend(vals)
            since_col = None
            if since:
                since_col = _pick_time_column(names)
                if not since_col:
                    raise QueryParamError("since 不支持：该表无时间列",
                                          {"allowed_fields": names})
                t_type = types.get(since_col, "").upper()
                val = _sqlite_since_value(con, table, since_col, since, t_type)
                parts.append('"%s" >= ?' % since_col.replace('"', '""'))
                params.append(val)
            where_sql = (" WHERE " + " AND ".join(parts)) if parts else ""
            order_sql = ""
            if order:
                order_sql = " ORDER BY " + ", ".join(
                    '"%s" %s' % (o["field"].replace('"', '""'),
                                 "DESC" if o["desc"] else "ASC") for o in order)
            total = None
            if count:
                total = int(con.execute(
                    'SELECT COUNT(*) FROM "%s"%s' % (qtable, where_sql), params).fetchone()[0])
            sel_sql = ", ".join('"%s"' % c.replace('"', '""') for c in select)
            sql = ('SELECT %s FROM "%s"%s%s LIMIT ? OFFSET ?'
                   % (sel_sql, qtable, where_sql, order_sql))
            rows = [[_jsonable(v) for v in r] for r in
                    con.execute(sql, params + [limit + 1, offset]).fetchall()]
        finally:
            con.close()
        has_more = len(rows) > limit
        rows = rows[:limit]
        state = "ok" if rows else "no_data"
        ev = {"engine": "sqlite", "db": str(p), "table": table,
              "rows_returned": len(rows),
              "scanned_rows": total if total is not None else offset + len(rows),
              "where_applied": bool(where), "order_applied": bool(order),
              "since_applied": bool(since), "since_column": since_col,
              "count_source": "count(*)" if count else None,
              "read_only": True}
        out = {"state": state, "columns": select, "rows": rows, "count": total,
               "has_more": has_more, "truncated": False, "evidence": ev}
        if state == "no_data":
            out["reason"] = "查询命中 0 行（过滤条件/时间下界无匹配）"
        return out

    # ---------- parquet ----------
    @staticmethod
    def _pa_value(value, typ):
        import pyarrow as pa
        if value is None:
            return None
        if pa.types.is_string(typ) or pa.types.is_large_string(typ):
            return str(value)
        if pa.types.is_boolean(typ):
            s = str(value).strip().lower()
            if s in ("true", "1", "yes"):
                return True
            if s in ("false", "0", "no"):
                return False
            raise QueryParamError("布尔字段值非法：%r" % value)
        if pa.types.is_integer(typ):
            try:
                return int(value)
            except (TypeError, ValueError):
                raise QueryParamError("整型字段值非法：%r" % value)
        if pa.types.is_floating(typ):
            try:
                return float(value)
            except (TypeError, ValueError):
                raise QueryParamError("浮点字段值非法：%r" % value)
        if pa.types.is_date(typ):
            return _norm_since(value)["date"]
        if pa.types.is_timestamp(typ):
            return datetime.datetime.combine(_norm_since(value)["date"], datetime.time())
        return value

    def _pa_pred(self, schema, w: dict):
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
        f = w["field"]
        if f not in schema.names:
            raise QueryParamError("未知字段：%r" % f, {"allowed_fields": list(schema.names)})
        typ = schema.field(f).type
        field = ds.field(f)
        if w["op"] == "in":
            values = [self._pa_value(v, typ) for v in w["value"]]
            return field.isin(pa.array(values, type=typ))
        if w["op"] == "like":
            if not (pa.types.is_string(typ) or pa.types.is_large_string(typ)):
                raise QueryParamError("like 仅支持字符串列：%s" % f)
            return pc.match_substring(field, _like_contains(str(w["value"])))
        scalar = pa.scalar(self._pa_value(w["value"], typ), type=typ)
        op = w["op"]
        return {"=": field == scalar, "!=": field != scalar, ">": field > scalar,
                ">=": field >= scalar, "<": field < scalar, "<=": field <= scalar}[op]

    def _pa_since(self, schema, since_col: str, since: dict):
        import pyarrow as pa
        import pyarrow.dataset as ds
        typ = schema.field(since_col).type
        if pa.types.is_date(typ):
            val = pa.scalar(since["date"], type=typ)
        elif pa.types.is_timestamp(typ):
            val = pa.scalar(datetime.datetime.combine(since["date"], datetime.time()), type=typ)
        elif pa.types.is_integer(typ):
            val = pa.scalar(int(since["ymd"]), type=typ)
        else:
            val = pa.scalar(since["ymd"], type=typ)
        return ds.field(since_col) >= val

    def _open_parquet(self, files: list):
        """打开 parquet dataset；跨文件 schema 不一致时逐级降级（fail-visible）：

        ① 统一 schema（unify_schemas default）→ ② 最大化「同 schema 文件子集」（记录
        schema_subset/被排除文件数，truncated 可见）→ ③ 抛 QueryNotSupported。
        返回 (dataset, info)；info={'schema_subset':bool,'files_used':n,'files_total':n}
        """
        import pyarrow as pa
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq

        def _sig(schema) -> tuple:
            return tuple((f.name, str(f.type)) for f in schema)

        dataset = ds.dataset([str(f) for f in files], format="parquet")
        schema = dataset.schema
        if not any(pa.types.is_null(t) for t in schema.types):
            return dataset, {"schema_subset": False,
                             "files_used": len(files), "files_total": len(files)}
        schemas = []
        for f in files:
            try:
                schemas.append((f, pq.read_schema(str(f))))
            except Exception:  # noqa: BLE001
                continue
        if not schemas:
            raise QueryNotSupported("parquet 读取失败：无可用 schema", {"files": len(files)})
        # ① 统一 schema
        try:
            unified = pa.unify_schemas([s for _, s in schemas], promote_options="default")
            ds_u = ds.dataset([str(f) for f, _ in schemas], format="parquet", schema=unified)
            return ds_u, {"schema_subset": False,
                          "files_used": len(schemas), "files_total": len(files)}
        except Exception:  # noqa: BLE001 — ② 同 schema 最大子集
            groups: dict = {}
            for f, s in schemas:
                groups.setdefault(_sig(s), []).append((f, s))
            best_sig, best = max(groups.items(), key=lambda kv: len(kv[1]))
            ds_best = ds.dataset([str(f) for f, _ in best], format="parquet",
                                 schema=best[0][1])
            return ds_best, {"schema_subset": len(best) < len(files),
                             "files_used": len(best), "files_total": len(files),
                             "excluded_files": len(files) - len(best)}

    def _nonempty_files(self, files: list) -> list:
        """按 footer 元数据挑出非空 parquet 文件（0 行占位文件不参与行查询）。"""
        import pyarrow.parquet as pq
        out = []
        for f in files:
            try:
                if pq.ParquetFile(str(f)).metadata.num_rows > 0:
                    out.append(f)
            except Exception:  # noqa: BLE001
                continue
        return out

    def _query_parquet(self, p: Path, disc: dict, where, order, limit, offset,
                       since, columns, count) -> dict:
        files = disc.get("parquet") or []
        if not files:
            return self._query_no_data("未发现 parquet 文件", disc)
        dataset, open_info = self._open_parquet(files)
        if disc.get("parquet_truncated") and not disc.get("_nonempty_probe"):
            # 目录被截断时探测一次：若发现的窗口全是 0 行占位分片 → 改取非空文件子集
            disc["_nonempty_probe"] = True
            try:
                empty = dataset.count_rows() == 0
            except Exception:  # noqa: BLE001
                empty = False
            if empty:
                ne = self._nonempty_files(files)
                if ne and len(ne) < len(files):
                    disc["parquet"] = ne
                    disc["parquet_nonempty_filtered"] = True
                    files = ne
                    dataset, open_info = self._open_parquet(files)
        schema = dataset.schema
        names = list(schema.names)
        for w in where:
            self._check_field(w["field"], names)
        for o in order:
            self._check_field(o["field"], names)
        select = list(columns) if columns else list(names)
        for c in select:
            self._check_field(c, names)
        preds = [self._pa_pred(schema, w) for w in where]
        since_col = None
        if since:
            since_col = _pick_time_column(names)
            if not since_col:
                raise QueryParamError("since 不支持：该数据集无时间列",
                                      {"allowed_fields": names})
            preds.append(self._pa_since(schema, since_col, since))
        expr = None
        for e in preds:
            expr = e if expr is None else (expr & e)
        total = int(dataset.count_rows(filter=expr)) if count else None
        bytes_total = 0
        for f in files:
            try:
                bytes_total += f.stat().st_size
            except OSError:
                pass
        truncated = bool(disc.get("parquet_truncated")) or bool(open_info.get("schema_subset"))
        rows, scanned = [], 0
        if order:
            total_sort = int(dataset.count_rows(filter=expr))
            if total_sort > QUERY_PARQUET_ORDER_ROWS:
                raise QueryNotSupported(
                    "parquet 排序需全表扫描（%d 行 > %d），P1 仅支持小结果集；"
                    "建议先加 where 收窄或等 P2 下推" % (total_sort, QUERY_PARQUET_ORDER_ROWS),
                    {"rows": total_sort})
            data = dataset.to_table(columns=select, filter=expr).to_pylist()
            scanned = len(data)
            for o in reversed(order):
                data = _sorted_rows(data, o["field"], o["desc"])
            window = data[offset:offset + limit + 1]
            has_more = len(window) > limit
            rows = [[_jsonable(r.get(c)) for c in select] for r in window[:limit]]
            ev = {"engine": "parquet", "files": len(files), "file_count": len(files),
                  "files_used": open_info.get("files_used"),
                  "files_total": open_info.get("files_total"),
                  "schema_subset": bool(open_info.get("schema_subset")),
                  "scanned_rows": scanned, "scanned_bytes": bytes_total,
                  "order_applied": "in_memory", "order_keys": len(order),
                  "rows_returned": len(rows), "truncated_discovery": truncated}
        else:
            target = limit + 1
            skipped = 0
            deadline = time.monotonic() + QUERY_TIMEOUT_S
            scanner = dataset.scanner(columns=select, filter=expr)
            for batch in scanner.to_batches():
                scanned += batch.num_rows
                if scanned > QUERY_MAX_SCAN_ROWS or time.monotonic() > deadline:
                    truncated = True
                    break
                if skipped < offset:
                    need = offset - skipped
                    if batch.num_rows <= need:
                        skipped += batch.num_rows
                        continue
                    batch = batch.slice(need)
                    skipped = offset
                for r in batch.to_pylist():
                    if len(rows) >= target:
                        break
                    rows.append([_jsonable(r.get(c)) for c in select])
                if len(rows) >= target:
                    break
            has_more = len(rows) > limit
            rows = rows[:limit]
            ev = {"engine": "parquet", "files": len(files),
                  "files_used": open_info.get("files_used"),
                  "files_total": open_info.get("files_total"),
                  "schema_subset": bool(open_info.get("schema_subset")),
                  "scanned_rows": scanned, "scanned_bytes": bytes_total,
                  "where_applied": bool(where), "order_applied": False,
                  "since_applied": bool(since), "since_column": since_col,
                  "rows_returned": len(rows),
                  "truncated_discovery": truncated,
                  "count_source": "dataset.count_rows" if count else None}
        state = "ok" if rows else "no_data"
        out = {"state": state, "columns": select, "rows": rows, "count": total,
               "has_more": has_more, "truncated": truncated, "evidence": ev}
        if state == "no_data":
            out["reason"] = "查询命中 0 行（过滤条件/时间下界无匹配）"
        return out

    # ---------- jsonl / json（P2：where/order/count/since 流式） ----------
    @staticmethod
    def _iter_json_docs(f: Path):
        """逐文档产出（jsonl/ndjson 逐行；json 整个数组/对象；gzip 嗅探；坏行跳过）。"""
        import gzip
        low = f.name.lower()
        try:
            with f.open("rb") as fh:
                magic = fh.read(2)
        except OSError:
            return
        try:
            if magic == b"\x1f\x8b":
                raw = gzip.open(str(f), "rt", encoding="utf-8", errors="replace")
            else:
                raw = f.open("r", encoding="utf-8", errors="replace")
        except OSError:
            return
        with raw:
            if low.endswith((".jsonl", ".ndjson", ".jsonl.gz")):
                for line in raw:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        yield obj
                    elif isinstance(obj, list):
                        for it in obj:
                            if isinstance(it, dict):
                                yield it
                return
            try:
                obj = json.loads(raw.read())
            except ValueError:
                return
            for it in (obj if isinstance(obj, list) else [obj]):
                if isinstance(it, dict):
                    yield it

    @staticmethod
    def _since_value_ok(value, since) -> bool:
        """行内时间值是否 >= since（支持 YYYYMMDD/ISO/时间戳；非法值不匹配）。"""
        if value is None:
            return False
        digits = re.sub(r"\D", "", str(value))
        if len(digits) >= 8:
            return digits[:8] >= since["ymd"]
        try:
            return _norm_since(str(value))["ymd"] >= since["ymd"]
        except QueryParamError:
            return str(value) >= since["ymd"]

    def _query_jsonl(self, p: Path, disc: dict, where, order, limit, offset,
                     since, columns, count) -> dict:
        files = disc.get("jsonl") or []
        if not files:
            return self._query_no_data("未发现 jsonl/json 文件", disc)
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        truncated = bool(disc.get("jsonl_truncated"))
        since_ts = (datetime.datetime.combine(since["date"], datetime.time()).timestamp()
                    if since else None)
        # ① 字段白名单推断（首批文档，有界）
        known, seen = [], set()
        scanned = 0
        for f in files[:20]:
            for doc in self._iter_json_docs(f):
                for k in doc.keys():
                    if k not in seen:
                        seen.add(k)
                        known.append(k)
                scanned += 1
                if scanned >= QUERY_JSONL_SCHEMA_DOCS:
                    break
            if scanned >= QUERY_JSONL_SCHEMA_DOCS:
                break
        if not known:
            return self._query_no_data("jsonl/json 文档为空或无对象记录", disc)
        for w in where:
            self._check_field(w["field"], known)
        for o in order:
            self._check_field(o["field"], known)
        select = list(columns) if columns else list(known)
        for c in select:
            self._check_field(c, known)
        since_col = _pick_time_column(known) if since else None
        # ② 流式扫描（where 先于 offset/limit；count 需全扫到上限）
        matched = 0
        window = []
        order_rows = []
        stopped = False
        scanned = 0
        files_seen = 0
        for f in files:
            files_seen += 1
            if since and not since_col:
                try:
                    if f.stat().st_mtime < since_ts:
                        continue
                except OSError:
                    pass
            for doc in self._iter_json_docs(f):
                scanned += 1
                if scanned > QUERY_JSONL_SCAN_LINES or time.monotonic() > deadline:
                    truncated = True
                    stopped = True
                    break
                if since_col and since and not self._since_value_ok(doc.get(since_col), since):
                    continue
                if where and not _match_doc(doc, where):
                    continue
                matched += 1
                if order:
                    if matched > QUERY_JSONL_ORDER_ROWS:
                        raise QueryNotSupported(
                            "jsonl 排序匹配行超过上限（%d），请先加 where 收窄或分页遍历"
                            % QUERY_JSONL_ORDER_ROWS,
                            {"engine": "jsonl", "matched": matched})
                    order_rows.append({k: doc.get(k) for k in select})
                elif len(window) < offset + limit + 1:
                    window.append({k: doc.get(k) for k in select})
                if not count and not order and len(window) >= offset + limit + 1:
                    stopped = True
                    break
            if stopped:
                break
        if order:
            for o in reversed(order):
                order_rows = _sorted_rows(order_rows, o["field"], o["desc"])
            page = order_rows[offset:offset + limit + 1]
            has_more = len(page) > limit
            page = page[:limit]
            rows = [[_jsonable(r.get(c)) for c in select] for r in page]
            total = len(order_rows) if count else None
        else:
            has_more = len(window) > offset + limit
            page = window[offset:offset + limit]
            rows = [[_jsonable(r.get(c)) for c in select] for r in page]
            total = matched if count else None
        state = "ok" if rows else "no_data"
        ev = {"engine": "jsonl", "files": len(files), "files_scanned": files_seen,
              "scanned_lines": scanned, "docs_matched": matched,
              "schema_fields": len(known), "rows_returned": len(rows),
              "where_applied": bool(where), "order_applied": bool(order),
              "since_applied": bool(since),
              "since_column": since_col,
              "since_mode": ("row_column" if since_col else "file_mtime") if since else None,
              "truncated_discovery": truncated,
              "scan_cap_lines": QUERY_JSONL_SCAN_LINES,
              "count_source": "stream_count" if count else None}
        out = {"state": state, "columns": select, "rows": rows, "count": total,
               "has_more": has_more, "truncated": truncated, "evidence": ev}
        if state == "no_data":
            out["reason"] = "jsonl 扫描无匹配文档（过滤/时间下界无命中或字段缺失）"
        return out

    # ---------- mixed/nested 文件级（P2：只列已登记文件，L/P/F/A 四维） ----------
    def _query_files(self, p: Path, disc: dict, where, order, limit, offset,
                     since, columns, count) -> dict:
        files = disc.get("files_meta") or []
        if not files:
            return self._query_no_data("目录内无已登记文件", disc)
        fields = list(QUERY_FILES_FIELDS)
        for w in where:
            self._check_field(w["field"], fields)
        for o in order:
            self._check_field(o["field"], fields)
        select = list(columns) if columns else list(fields)
        for c in select:
            self._check_field(c, fields)
        since_ts = (datetime.datetime.combine(since["date"], datetime.time.min)
                    .timestamp()) if since else None
        rows = []
        for meta in files:
            mtime = meta.get("mtime")
            try:
                mt_iso = (datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                          if mtime is not None else None)
            except (OverflowError, OSError, ValueError):
                mt_iso = None
            row = {"path": meta.get("path"), "name": meta.get("name"),
                   "ext": meta.get("ext"), "size": meta.get("size"),
                   "mtime": mt_iso, "depth": meta.get("depth")}
            if since_ts is not None and (mtime is None or mtime < since_ts):
                continue
            if where and not _match_doc(row, where):
                continue
            rows.append({k: row.get(k) for k in select})
        total = len(rows) if count else None
        if order:
            for o in reversed(order):
                rows = _sorted_rows(rows, o["field"], o["desc"])
        page = rows[offset:offset + limit + 1]
        has_more = len(page) > limit
        page = page[:limit]
        out_rows = [[_jsonable(r.get(c)) for c in select] for r in page]
        truncated = bool(disc.get("files_truncated") or disc.get("dirs_truncated"))
        state = "ok" if out_rows else "no_data"
        ev = {"engine": "files", "files_total": disc.get("files_total"),
              "files_cataloged": len(files), "rows_returned": len(out_rows),
              "where_applied": bool(where), "order_applied": bool(order),
              "since_applied": bool(since),
              "truncated_discovery": truncated,
              "max_depth": disc.get("max_depth"),
              "count_source": "in_memory_manifest" if count else None}
        out = {"state": state, "columns": select, "rows": out_rows, "count": total,
               "has_more": has_more, "truncated": truncated, "evidence": ev}
        if state == "no_data":
            out["reason"] = "文件清单无匹配项（过滤/时间下界无命中）"
        return out

    # ---------- 导出登记 YAML 草稿 ----------
    def emit_yaml(self, out_path: Path | str) -> Path:
        import yaml
        payload = {
            "version": 1,
            "status": "draft_pending_DATA1_merge",
            "generated_at": _now(),
            "source_inventory": str(self.inventory_path),
            "note": "数据集级统一登记草稿（WP-I1）；合并进 data_catalog v7 需 DATA1 审核",
            "stats": self.stats(),
            # DWH-103 wave1（ADR-019 D6/D7）：新增两节与 stats_v2（v1 字段/数值不变）
            "stats_v1": self.stats(),
            "stats_v2": self.stats_v2(),
            "pg_tables": [dict(d) for d in self.pg_datasets],
            "contract_artifacts": [dict(d) for d in self.contract_artifacts],
            "domains": {},
        }
        for d in self.datasets:
            payload["domains"].setdefault(d["domain"], []).append({
                "dataset_id": d["dataset_id"], "label": d["label"], "kind": d["kind"],
                "path": d["path"], "owner": d["owner"], "registered": d["registered"],
                "exposed": d["exposed"], "access": d["access"],
            })
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return out


def _count_by(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:  # noqa: BLE001
        return False


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
