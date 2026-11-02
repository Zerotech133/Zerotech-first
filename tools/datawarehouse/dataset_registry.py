# -*- coding: utf-8 -*-
"""数据集级统一登记/取数（WP-I1/I3 骨架；DWH-103 wave1 增 pg_table/contract_artifact）。

- 数据来源：research/reports/dataset_inventory_*.json（全域盘点，文件级 v1 口径 314 数据集）
- 能力：list / search / get / sample / emit（生成 dataset_registry YAML 草稿）
- **DWH-103 wave1（ADR-019）**：
  * 新 kind `pg_table`：智策 PG 5446 核心表（wave1 ≤15 张），只读 DSN 来自环境变量
    `ZHICE_V2_DSN`（不落盘）。缺 DSN → `connector_status=missing_dsn`（不假绿）；
    连接失败 → `connector_unavailable`，`sample()` 抛 `ConnectorUnavailable`（fail-visible）。
  * 新 kind `contract_artifact`：`C:\\Temp` 契约产物**显式白名单**（不扩 ALLOWED_ROOTS、
    禁递归扫描、仅直属文件）。缺失/sha256 漂移 → 行状态 missing/sha256_mismatch，
    `sample()` 抛 `ContractArtifactUnavailable`（fail-visible）。
  * 兼容：`stats()` 与 `self.datasets`（文件级）保持原 314/265/72 口径不变；
    新增 `stats_v2()` 合并口径（`denominator_version=v2`）。
- 边界：只读；PG 仅只读会话 SELECT；不写业务数据/数据库/`C:\\Temp`；不递归扫描 Temp。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

REPO = Path(r"C:\项目\老田的AI助手")
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
PG_CONNECTOR = "pg@127.0.0.1:5446/zhice_os_v2"  # 逻辑连接串标签（不含凭据）
PG_SCHEMA_WHITELIST = ("public",)  # 跨库/其他 schema 需 A0 加白（ADR-019 D2）
PG_TIMEOUT_S = 5
PG_DOMAIN = "平台运行"
# wave1：组合 manifest 已引用 + ACAD1/D1 核心表（≤15 张）；wave2 全 83 张另单（ADR-019 D3）
PG_WAVE1_TABLES = (
    {"table": "v2_acad1_pool", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_selection_pool", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_stock_research", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_selection_report", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_strategy_ownership", "owner": "ACAD1", "registered": True, "exposed": True},
    {"table": "v2_base_signal", "owner": "D1", "registered": True, "exposed": True},
    {"table": "v2_score_component", "owner": "D1", "registered": True, "exposed": True},
)

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
QUERY_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "like")
QUERY_AUDIT_ENV = "DW_QUERY_AUDIT"
QUERY_AUDIT_LOG = Path(r"C:\Temp\dw_query_audit.log")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TIME_COL_HINTS = (
    "trade_date", "date", "pub_date", "ann_date", "notice_date", "created_at",
    "updated_at", "datetime", "timestamp", "trade_time", "end_date", "report_date",
    "publish_date", "cal_date", "month", "year", "quarter", "day",
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
        if re.search(r"(date|_at$|time|day|month|year)", str(n), re.I):
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_pg_probe(dsn: str, tables) -> dict:
    """只读探针：pg_class/information_schema 查 wave1 表存在性 + reltuples 估算（不写库）。"""
    try:
        import psycopg2 as pg
    except ImportError:
        try:
            import psycopg as pg  # type: ignore
        except ImportError as exc:  # noqa: BLE001
            raise ConnectorUnavailable("driver_missing: 未安装 psycopg2/psycopg",
                                       status="driver_missing") from exc
    con = None
    try:
        con = pg.connect(dsn, connect_timeout=PG_TIMEOUT_S)
        try:
            con.set_session(readonly=True, autocommit=True)
        except Exception:  # noqa: BLE001 — psycopg3 无 set_session：退化为只读事务外查询
            try:
                con.autocommit = True
            except Exception:  # noqa: BLE001
                pass
        cur = con.cursor()
        cur.execute(
            "SELECT n.nspname, c.relname, c.reltuples FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = ANY(%s) AND c.relname = ANY(%s) AND c.relkind = 'r'",
            (list(PG_SCHEMA_WHITELIST), list(tables)),
        )
        out = {}
        for schema, name, estimate in cur.fetchall():
            out[(str(schema), str(name))] = (
                None if estimate is None or float(estimate) < 0 else int(estimate)
            )
        return out
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
                 temp_whitelist=None,
                 temp_root: Path | str | None = None):
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
        self.pg_dsn = (os.environ.get(PG_DSN_ENV, "") if pg_dsn is None else str(pg_dsn)).strip()
        self.pg_tables = [dict(x) for x in (pg_tables if pg_tables is not None else PG_WAVE1_TABLES)]
        self._pg_probe = pg_probe
        self.temp_root = Path(temp_root) if temp_root is not None else TEMP_ROOT
        self.temp_whitelist = [
            dict(x) for x in (temp_whitelist if temp_whitelist is not None
                              else TEMP_CONTRACT_WHITELIST)
        ]
        self.connector_status = "unprobed"
        self.connector_error = ""
        self._disc_cache: dict = {}
        self.pg_datasets = self._build_pg_datasets()
        self.contract_artifacts = self._build_contract_artifacts()
        self._extra_by_id = {d["dataset_id"]: d
                             for d in self.pg_datasets + self.contract_artifacts}

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
        return {
            "denominator_version": "v2",
            "generated_at": _now(),
            "connector_status": self.connector_status,
            "connector_error": self.connector_error,
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
                "by_status": _count_by(self.pg_datasets, "status"),
            },
            "contract_artifacts": {
                "total": len(self.contract_artifacts),
                "active": len(art_active),
                "missing": sum(1 for d in self.contract_artifacts if d.get("status") == "missing"),
                "mismatch": sum(1 for d in self.contract_artifacts
                                if d.get("status") == "sha256_mismatch"),
                "by_status": _count_by(self.contract_artifacts, "status"),
            },
            "all": {
                "total": v1["total"] + len(pg_available) + len(art_active),
                "registered": v1["registered"] + len(pg_available) + len(art_active),
                "exposed": v1["exposed"] + len(pg_available) + len(art_active),
            },
        }

    def list(self, domain: str | None = None, q: str | None = None,
             registered: bool | None = None, exposed: bool | None = None,
             limit: int = 50, kinds=None) -> list[dict]:
        """默认仅文件级（向后兼容 v1）；kinds='all' 或指定 kind 时含 pg/契约产物。"""
        if kinds is None:
            pool = self.datasets
        elif kinds == "all":
            pool = self.datasets + self.pg_datasets + self.contract_artifacts
        else:
            wanted = {kinds} if isinstance(kinds, str) else set(kinds)
            pool = [d for d in self.datasets + self.pg_datasets + self.contract_artifacts
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
        return None

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
                "physical": {"schema": schema, "table": table},
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

    def _probe_pg(self, rows: list[dict]) -> None:
        """连接探测：缺 DSN=missing_dsn；失败=connector_unavailable（均 fail-visible，不假绿）。"""
        if not rows:
            self.connector_status = "disabled"
            return
        if not self.pg_dsn:
            self.connector_status = "missing_dsn"
            for row in rows:
                row["status"] = "missing_dsn"
            return
        probe = self._pg_probe or _default_pg_probe
        try:
            found = probe(self.pg_dsn, [row["physical"]["table"] for row in rows])
        except Exception as exc:  # noqa: BLE001 — fail-visible，不静默跳过
            self.connector_status = getattr(exc, "status", "connector_unavailable")
            self.connector_error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            for row in rows:
                row["status"] = self.connector_status
            return
        self.connector_status = "ok"
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
        try:
            import psycopg2 as pg
        except ImportError:
            raise ConnectorUnavailable("driver_missing: 未安装 psycopg2", status="driver_missing")
        con = None
        try:
            con = pg.connect(self.pg_dsn, connect_timeout=PG_TIMEOUT_S)
            try:
                con.set_session(readonly=True, autocommit=True)
            except Exception:  # noqa: BLE001
                try:
                    con.autocommit = True
                except Exception:  # noqa: BLE001
                    pass
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
        if expected and actual != expected:
            raise ContractArtifactUnavailable(
                "契约产物 sha256 漂移（fail-visible；可能被改写）: %s" % p)
        base = {"dataset_id": d["dataset_id"], "kind": "contract_artifact",
                "artifact_id": d.get("artifact_id"), "path": str(p),
                "schema_ref": d.get("schema_ref"), "owner": d.get("owner"),
                "status": "active", "sha256": actual,
                "size_bytes": p.stat().st_size}
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
        p = self._resolve(d)
        disc = self._discover(p)
        carrier = self._carrier_for(kind, p, disc)
        base = {"dataset_id": dataset_id, "kind": kind, "carrier": carrier,
                "ok": True, "truncated": bool(disc.get("parquet_truncated")
                                              or disc.get("jsonl_truncated"))}
        try:
            if kind in ("pg_table", "contract_artifact"):
                raise QueryNotSupported(
                    "%s 载体 P1 不支持行级 query（契约产物走 sample；PG 走 P3）" % kind,
                    {"carrier": carrier, "hint": "sample() 可用"})
            if carrier in ("sqlite_table",):
                out = self._query_sqlite(p, d, where_l, order_l, limit, offset, since_n, cols, count)
            elif carrier in ("parquet_file", "dir_parquet"):
                out = self._query_parquet(p, disc, where_l, order_l, limit, offset,
                                          since_n, cols, count)
            elif carrier in ("jsonl_file", "dir_jsonl"):
                out = self._query_jsonl(p, disc, where_l, order_l, limit, offset,
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
               "jsonl": [], "jsonl_truncated": False,
               "other_files": 0, "files_total": 0, "empty": False,
               "dirs_scanned": 0, "dirs_truncated": False}
        if p.is_file():
            out["files_total"] = 1
            out["parquet_total"] = 1 if p.name.lower().endswith(".parquet") else 0
            low = p.name.lower()
            if low.endswith(".parquet"):
                out["parquet"] = [p]
            elif low.endswith((".jsonl", ".ndjson")):
                out["jsonl"] = [p]
            else:
                out["other_files"] = 1
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
            for fn in fnames:
                out["files_total"] += 1
                low = fn.lower()
                if low.endswith(".parquet"):
                    out["parquet_total"] += 1
                    if len(head) < cap_head:
                        head.append(Path(cur) / fn)
                    else:
                        tail.append(Path(cur) / fn)
                elif low.endswith((".jsonl", ".ndjson")):
                    if len(out["jsonl"]) < QUERY_MAX_JSONL_FILES:
                        out["jsonl"].append(Path(cur) / fn)
                    else:
                        out["jsonl_truncated"] = True
                else:
                    out["other_files"] += 1
            if out["dirs_scanned"] >= 2000:
                out["dirs_truncated"] = True
                break
        out["parquet"] = head + list(tail)
        out["parquet_truncated"] = out["parquet_total"] > len(out["parquet"])
        out["empty"] = out["files_total"] == 0
        self._disc_cache[key] = out
        return out

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
            return "jsonl_file" if p.is_file() else "dir_jsonl"
        if disc["empty"]:
            return "dir_empty"
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
                val = int(since["ymd"]) if "INT" in t_type else since["ymd"]
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
        import pyarrow.dataset as ds
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
                data.sort(key=lambda r: (r.get(o["field"]) is None, r.get(o["field"])),
                          reverse=o["desc"])
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

    # ---------- jsonl（P1 基础行，条件/排序/计数 P2，fail-visible） ----------
    def _query_jsonl(self, p: Path, disc: dict, where, order, limit, offset,
                     since, columns, count) -> dict:
        files = disc.get("jsonl") or []
        if not files:
            return self._query_no_data("未发现 jsonl 文件", disc)
        if where or order or count or since:
            raise QueryNotSupported(
                "jsonl 载体 P1 仅支持 columns 投影 + limit/offset 前 N 行；"
                "where/order/count/since 属 P2（明确 fail-visible，不回退 sample）",
                {"engine": "jsonl", "files": len(files)})
        select = list(columns) if columns else None
        rows, scanned, skipped = [], 0, 0
        truncated = bool(disc.get("jsonl_truncated"))
        out_columns = list(select) if select else []
        for f in files:
            try:
                fh = f.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if not line.strip():
                        continue
                    scanned += 1
                    if scanned > QUERY_MAX_JSONL_LINES:
                        truncated = True
                        break
                    if skipped < offset:
                        skipped += 1
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        raise QueryNotSupported("jsonl 行非对象，columns/行投影不支持")
                    if not select:
                        out_columns = list(obj.keys())
                        select = out_columns
                    rows.append([_jsonable(obj.get(c)) for c in select])
                    if len(rows) > limit:
                        break
            if len(rows) > limit or truncated:
                break
        has_more = len(rows) > limit
        rows = rows[:limit]
        state = "ok" if rows else "no_data"
        ev = {"engine": "jsonl", "files": len(files), "scanned_lines": scanned,
              "rows_returned": len(rows), "truncated_discovery": truncated,
              "columns_projection": bool(columns),
              "unsupported_params": ["where", "order", "count", "since"]}
        out = {"state": state, "columns": out_columns, "rows": rows, "count": None,
               "has_more": has_more, "truncated": truncated, "evidence": ev}
        if state == "no_data":
            out["reason"] = "jsonl 前 N 行无有效记录（文件空/格式不匹配）"
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
