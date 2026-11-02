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


class RegistryError(RuntimeError):
    """registry 只读探针/白名单失败基类（fail-visible）。"""


class ConnectorUnavailable(RegistryError):
    """pg_table 连接器不可用（missing_dsn / driver_missing / connector_unavailable）。"""

    def __init__(self, message: str, status: str = "connector_unavailable"):
        super().__init__(message)
        self.status = status


class ContractArtifactUnavailable(RegistryError):
    """contract_artifact 缺失/漂移/非文件（fail-visible，不静默丢弃）。"""


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
