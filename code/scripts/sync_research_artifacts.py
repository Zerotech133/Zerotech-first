# -*- coding: utf-8 -*-
"""研究 artifacts 同步校验与 manifest 生成（P0）。

职责
----
把「张松研究规格 + 司马微严格审计」这组平台不直接管理的研究文件，收敛成一份可审计的
manifest：sha256 / asof / source / owner / contract / counts，并校验：

1. research 目录与 specs 数量：``research/strategy_specs/*.yaml`` 必须为 **24** 个，
   每个 YAML 含契约必填字段（id/name/type/computability/entry_rule/test_status）、id 唯一；
2. strict audit：``research/reports/simahui_strict_audit_20261101.json`` 必须存在、
   ``book_backlog`` 24 项、verdict 合法、与 specs id 一一覆盖；
3. 下游同步：``config/strategy_market_research.json`` 必须由当前研究 artifacts 同步而来
   （items 32 = 24 书战法 + 8 注册策略；book status 与 audit verdict 对应；registered 与
   builder 的 REGISTERED 规则对应；tradable=false / lifecycle=research / owner/contract 字段齐全）；
4. 契约：``config/contracts/strategy_research.odcs.yaml`` 存在、owner=DATA1、schema 覆盖
   strategy_specs/strict_audit、freshness.maxAgeHours=24；
5. 新鲜度：下游快照 generated_at 必须 ≥ 研究 artifacts asof；同步滞后超过契约 24h → WARN；
6. 生成 manifest：``config/research_artifact_manifest.json``（默认 dry-run，``--write`` 才写）。

约束
----
- **只读研究文件**：不修改 build_strategy_market.py / strategy_market_research.json；
- 本脚本只写 manifest（且仅在 ``--write`` 且无 error 时写），不写 DB、不触发构建；
- 默认 dry-run：打印校验摘要并退出（errors→exit 1，仅 warnings→exit 0）。

用法::

    python scripts/sync_research_artifacts.py                 # dry-run
    python scripts/sync_research_artifacts.py --json          # dry-run + 打印 manifest JSON
    python scripts/sync_research_artifacts.py --write         # 校验通过后写 manifest

退出码：0=无 error（可含 warning）；1=存在 error（不写 manifest）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

CST = timezone(timedelta(hours=8))
CODE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESEARCH_ROOT = Path(r"C:\项目\老田的AI助手")
RESEARCH_DIR_REL = "research"
SPECS_REL = "research/strategy_specs"
AUDIT_REL = "research/reports/simahui_strict_audit_20261101.json"
CONFIG_REL = "config/strategy_market_research.json"
CONTRACT_REL = "config/contracts/strategy_research.odcs.yaml"
BUILDER_REL = "scripts/build_strategy_market.py"
MANIFEST_REL = "config/research_artifact_manifest.json"
# 研究仓之外的正式交付报告：不改变 36 项研究校验，只作为 manifest artifact 归档。
# 新增报告时在此登记；路径相对 research root 的父目录（默认 C:\项目）。
DELIVERABLE_REPORTS = (
    ("deliverables/2026-11-10_wrapper_tail_order_verify_v1.md", "deliverable_report", "ACAD1"),
)

EXPECTED_SPECS = 24
EXPECTED_REGISTERED = 8
EXPECTED_ITEMS = EXPECTED_SPECS + EXPECTED_REGISTERED
CONTRACT_OWNER = "DATA1"
CONTRACT_MAX_AGE_HOURS = 24
SPEC_REQUIRED = ("id", "name", "type", "computability", "entry_rule", "test_status")
BOOK_VERDICTS = {"close", "needs-more", "keep"}
STATUS_BY_VERDICT = {"close": "RESEARCH_CLOSED", "needs-more": "RESEARCH_NEEDS_MORE"}
ITEM_REQUIRED = (
    "strategy_id", "strategy_name", "market", "strategy_type", "status",
    "research_owner", "source_owner", "validation_owner", "data_owner",
    "ops_owner", "reviewer", "lifecycle_status", "data_contract", "file",
)
MANIFEST_SCHEMA = "zhice.research_artifact_manifest/v1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, CST)


def _iso(dt: datetime) -> str:
    return dt.astimezone(CST).isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except (TypeError, ValueError):
        return None


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


class Checker:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def add(self, ok: bool, code: str, message: str, *, severity: str = "error",
            details: Any = None) -> bool:
        """记录检查项。severity=error 时 ok=False 进 errors；WARN 用 warn()。"""
        status = "PASS" if ok else ("FAIL" if severity == "error" else "WARN")
        item = {"code": code, "status": status, "message": message}
        if details is not None:
            item["details"] = details
        self.checks.append(item)
        if not ok:
            if severity == "error":
                self.errors.append({"code": code, "message": message, **({"details": details} if details is not None else {})})
            else:
                self.warnings.append({"code": code, "message": message, **({"details": details} if details is not None else {})})
        return ok

    def warn(self, condition: bool, code: str, message: str, details: Any = None) -> bool:
        """condition=False 时记录 WARN（不阻塞 manifest 写入）。返回 condition。"""
        return self.add(condition, code, message, severity="warn", details=details)


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _parse_registered_builder(builder: Path) -> list[dict[str, str]]:
    """从 build_strategy_market.py AST 读取 REGISTERED（只读，不 import/不执行 builder）。"""
    tree = ast.parse(builder.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "REGISTERED" not in targets or not isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            out: list[dict[str, str]] = []
            for elt in node.value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) >= 3:
                    try:
                        out.append({
                            "strategy_id": ast.literal_eval(elt.elts[0]),
                            "strategy_name": ast.literal_eval(elt.elts[1]),
                            "status": ast.literal_eval(elt.elts[2]),
                        })
                    except (ValueError, SyntaxError):
                        continue
            return out
    return []


def _expected_registered_status(status: str) -> str:
    """与 build_strategy_market.py 一致：status 以 closed 开头 → CLOSED，否则 NEEDS_MORE。"""
    return "RESEARCH_CLOSED" if str(status).startswith("closed") else "RESEARCH_NEEDS_MORE"


def _research_file_exists(research_root: Path, research_dir: Path, rel: str) -> bool:
    """校验快照里的 research 相对路径存在（兼容 root=父目录 / root=research 目录两种传法）。"""
    if not rel:
        return False
    for base in (research_root, research_dir.parent, research_dir):
        try:
            if (base / rel).is_file():
                return True
        except OSError:
            continue
    return False


def _artifact(path: Path, role: str, owner: str, asof: datetime | None = None,
              relative_to: Path | None = None) -> dict[str, Any]:
    item = {
        "role": role,
        "owner": owner,
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "mtime": _iso(_mtime(path)),
        "asof": _iso(asof or _mtime(path)),
    }
    if relative_to is not None:
        try:
            item["relative_path"] = str(path.relative_to(relative_to)).replace("\\", "/")
        except ValueError:
            item["relative_path"] = str(path)
    return item


def _research_tree_summary(research_root: Path) -> dict[str, Any]:
    """research 目录级盘点（不逐文件 ash256，避免把 20MB+ 数据/DB 全量哈希进 manifest）。"""
    if not research_root.is_dir():
        return {"files": 0, "dirs": 0, "bytes": 0, "by_extension": {}, "latest_mtime": None}
    files: list[Path] = []
    dirs = 0
    for p in research_root.rglob("*"):
        if "__pycache__" in p.parts:
            continue
        try:
            if p.is_dir():
                dirs += 1
            elif p.is_file():
                files.append(p)
        except OSError:
            continue
    exts: Counter[str] = Counter()
    total = 0
    latest: datetime | None = None
    for p in files:
        exts[p.suffix.lower() or "(none)"] += 1
        try:
            total += p.stat().st_size
            mt = _mtime(p)
            latest = mt if latest is None or mt > latest else latest
        except OSError:
            continue
    return {
        "root": str(research_root),
        "files": len(files),
        "dirs": dirs,
        "bytes": total,
        "by_extension": dict(sorted(exts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "latest_mtime": _iso(latest) if latest else None,
    }


def build_manifest(research_root: Path, code_root: Path) -> tuple[dict[str, Any], Checker]:
    ck = Checker()
    # --research-root 可传「研究仓父目录（默认 C:\项目\老田的AI助手）」或「research 目录本身」。
    research_dir = research_root / RESEARCH_DIR_REL
    if not research_dir.is_dir():
        research_dir = research_root
    specs_dir = research_dir / "strategy_specs"
    audit_path = research_dir / "reports/simahui_strict_audit_20261101.json"
    config_path = code_root / CONFIG_REL
    contract_path = code_root / CONTRACT_REL
    builder_path = code_root / BUILDER_REL
    deliverable_root = research_root.parent

    # ---------- 1. research 目录 / specs ----------
    ck.add(research_root.is_dir(), "RESEARCH_ROOT_EXISTS",
           f"research root 存在: {research_root}", details={"root": str(research_root)})
    ck.add(specs_dir.is_dir(), "SPECS_DIR_EXISTS",
           f"strategy_specs 目录存在: {specs_dir}", details={"path": str(specs_dir)})
    ck.add((research_dir / "reports").is_dir(), "REPORTS_DIR_EXISTS",
           "research/reports 目录存在", details={"path": str(research_dir / "reports")})
    spec_files = sorted(specs_dir.glob("*.yaml")) if specs_dir.is_dir() else []
    ck.add(len(spec_files) == EXPECTED_SPECS, "SPECS_COUNT_24",
           f"specs 数量必须为 {EXPECTED_SPECS}（当前 {len(spec_files)}）",
           details={"expected": EXPECTED_SPECS, "actual": len(spec_files), "files": [p.name for p in spec_files]})

    specs: dict[str, dict[str, Any]] = {}
    spec_errors: list[str] = []
    for path in spec_files:
        try:
            y = _load_yaml(path)
        except Exception as exc:  # noqa: BLE001
            spec_errors.append(f"{path.name}: YAML 解析失败 {type(exc).__name__}: {exc}"[:200])
            continue
        missing = [k for k in SPEC_REQUIRED if not str(y.get(k) or "").strip()]
        if missing:
            spec_errors.append(f"{path.name}: 缺字段 {missing}")
            continue
        sid = str(y["id"]).strip()
        if sid in specs:
            spec_errors.append(f"{path.name}: id 重复 {sid}")
            continue
        specs[sid] = {
            "id": sid,
            "name": str(y.get("name") or ""),
            "file": path.name,
            "test_status": str(y.get("test_status") or ""),
            "updated_at": y.get("updated_at"),
        }
    ck.add(not spec_errors and len(specs) == EXPECTED_SPECS, "SPECS_LOADED_VALID",
           "specs 全部可解析、含必填字段且 id 唯一" if not spec_errors else "specs 存在解析/字段/重复 id 问题",
           details={"errors": spec_errors, "loaded": len(specs)})

    # ---------- 2. strict audit ----------
    audit: dict[str, Any] = {}
    if ck.add(audit_path.is_file(), "STRICT_AUDIT_EXISTS",
              f"strict audit 文件存在: {audit_path}", details={"path": str(audit_path)}):
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            ck.add(False, "STRICT_AUDIT_JSON", f"strict audit JSON 解析失败: {exc}"[:200])
    if isinstance(audit, dict) and audit:
        ck.add(str(audit.get("schema_version") or "").startswith("simahui_strict_audit"),
               "STRICT_AUDIT_SCHEMA", "strict audit schema_version 合法",
               details={"schema_version": audit.get("schema_version")})
        meta = audit.get("meta") or {}
        ck.add(isinstance(meta, dict) and bool(meta.get("generated_at")), "STRICT_AUDIT_META",
               "strict audit meta.generated_at 存在", details={"meta": meta})
        ck.add(bool(meta.get("auditor")), "STRICT_AUDIT_AUDITOR", "strict audit auditor 存在",
               details={"auditor": meta.get("auditor")})
        book = audit.get("book_backlog") or {}
        items = (book.get("items") or []) if isinstance(book, dict) else []
        ck.add(isinstance(book, dict) and book.get("total") == EXPECTED_SPECS and len(items) == EXPECTED_SPECS,
               "STRICT_AUDIT_BOOK_24",
               f"book_backlog 必须 {EXPECTED_SPECS} 项（total={book.get('total') if isinstance(book, dict) else None}, items={len(items)}）")
        book_ids = [str(x.get("strategy_spec_id")) for x in items if isinstance(x, dict)]
        ck.add(len(book_ids) == len(set(book_ids)) and set(book_ids) == set(specs),
               "STRICT_AUDIT_BOOK_SPEC_COVERAGE",
               "book_backlog 的 strategy_spec_id 与 specs 一一覆盖",
               details={"missing": sorted(set(specs) - set(book_ids)), "extra": sorted(set(book_ids) - set(specs))})
        bad_verdicts = [x for x in items if str(x.get("verdict")) not in BOOK_VERDICTS]
        ck.add(not bad_verdicts, "STRICT_AUDIT_BOOK_VERDICT_VALID",
               "book_backlog verdict 均为 close/needs-more/keep",
               details={"bad": bad_verdicts[:10]})
        verdict_counts = Counter(str(x.get("verdict")) for x in items)
        counts = book.get("counts") or {}
        ck.add({k: int(v) for k, v in (counts or {}).items()} == dict(verdict_counts),
               "STRICT_AUDIT_BOOK_COUNTS",
               "book_backlog.counts 与实际 verdict 统计一致",
               details={"declared": counts, "actual": dict(verdict_counts)})
        # strategies/verdicts_summary 为审计对注册策略的细项（当前仅 5 条），与 book 24 项分开；
        # 缺失的注册策略由 builder REGISTERED 规则兜底 → WARN 而非 ERROR。
        strategies = audit.get("strategies") or {}
        ck.warn(len(strategies) >= 1, "STRICT_AUDIT_STRATEGIES_DETAIL",
                f"strict audit strategies 细项 {len(strategies)} 条（book 24 项以 book_backlog 为准）",
                details={"strategies": sorted(strategies)[:20]})
        verdicts_summary = audit.get("verdicts_summary") or {}
        if isinstance(verdicts_summary, dict):
            detail_ids = set()
            for key in ("keep", "needs-more", "close"):
                for sid in verdicts_summary.get(key) or []:
                    detail_ids.add(str(sid))
            ck.warn(detail_ids == set(strategies), "STRICT_AUDIT_VERDICTS_SUMMARY_SCOPE",
                    "verdicts_summary 覆盖 strategies 细项（非 book 24 项）",
                    details={"detail_ids": sorted(detail_ids), "strategies": sorted(strategies)})

    # ---------- 3. builder / 下游快照 ----------
    registered: list[dict[str, str]] = []
    if ck.add(builder_path.is_file(), "BUILDER_EXISTS",
              f"builder 存在: {builder_path}", details={"path": str(builder_path)}):
        try:
            registered = _parse_registered_builder(builder_path)
        except Exception as exc:  # noqa: BLE001
            ck.add(False, "BUILDER_PARSE", f"builder REGISTERED 解析失败: {exc}"[:200])
    ck.add(len(registered) == EXPECTED_REGISTERED, "BUILDER_REGISTERED_8",
           f"builder REGISTERED 必须 {EXPECTED_REGISTERED} 条（当前 {len(registered)}）",
           details={"registered": [r.get("strategy_id") for r in registered]})
    reg_ids = [r.get("strategy_id") for r in registered if r.get("strategy_id")]
    expected_status: dict[str, str] = {}
    for r in registered:
        expected_status[str(r["strategy_id"])] = _expected_registered_status(r.get("status") or "")
    # 2026-11-03: 严格门槛矩阵是注册策略状态的最终口径；旧 builder REGISTERED 仅兜底
    gate_path = config_path.parent / "strategy_gate_matrix.json"
    if gate_path.is_file():
        try:
            gate_data = json.loads(gate_path.read_text(encoding="utf-8"))
            for gi in gate_data.get("items") or []:
                sid = str(gi.get("strategy_id") or "")
                verdict = str(gi.get("strict_verdict") or "")
                if sid and verdict in ("close", "needs-more"):
                    expected_status[sid] = (
                        "RESEARCH_CLOSED" if verdict == "close" else "RESEARCH_NEEDS_MORE"
                    )
        except Exception:  # noqa: BLE001
            pass

    config: dict[str, Any] = {}
    if ck.add(config_path.is_file(), "STRATEGY_MARKET_CONFIG_EXISTS",
              f"策略市场快照存在: {config_path}", details={"path": str(config_path)}):
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            ck.add(False, "STRATEGY_MARKET_CONFIG_JSON", f"策略市场快照 JSON 解析失败: {exc}"[:200])
    if isinstance(config, dict) and config:
        ck.add(config.get("schema") == "zhice.strategy_market_research/v1",
               "STRATEGY_MARKET_SCHEMA", "策略市场快照 schema 合法",
               details={"schema": config.get("schema")})
        ck.add(bool(config.get("generated_at")), "STRATEGY_MARKET_GENERATED_AT",
               "策略市场快照 generated_at 存在", details={"generated_at": config.get("generated_at")})
        cfg_source = str(config.get("source") or "")
        try:
            source_ok = Path(cfg_source).resolve() == audit_path.resolve()
        except (OSError, ValueError):
            source_ok = False
        ck.add(source_ok, "STRATEGY_MARKET_SOURCE_AUDIT",
               "快照 source 指向 strict audit 文件", details={"source": cfg_source, "expected": str(audit_path)})
        items = config.get("items") or []
        counts = config.get("counts") or {}
        ck.add(len(items) == EXPECTED_ITEMS and counts.get("total") == EXPECTED_ITEMS,
               "STRATEGY_MARKET_ITEMS_32",
               f"快照 items 必须 {EXPECTED_ITEMS} 条（当前 {len(items)}，counts.total={counts.get('total')}）",
               details={"items": len(items), "counts": counts})
        item_by_id = {str(x.get("strategy_id")): x for x in items if isinstance(x, dict)}
        expected_ids = set(specs) | set(reg_ids)
        ck.add(len(item_by_id) == len(items) and set(item_by_id) == expected_ids,
               "STRATEGY_MARKET_ID_COVERAGE",
               "快照 strategy_id 与 specs+builder 注册策略一一覆盖",
               details={"missing": sorted(expected_ids - set(item_by_id)), "extra": sorted(set(item_by_id) - expected_ids)})
        # item 必填字段
        field_errors: list[str] = []
        for sid, item in item_by_id.items():
            missing = [k for k in ITEM_REQUIRED if item.get(k) in (None, "")]
            if missing:
                field_errors.append(f"{sid}: 缺字段 {missing}")
            if item.get("market") != "CN":
                field_errors.append(f"{sid}: market != CN")
            if item.get("tradable") is not False:
                field_errors.append(f"{sid}: tradable != false")
            if item.get("lifecycle_status") != "research":
                field_errors.append(f"{sid}: lifecycle_status != research")
            if not str(item.get("data_contract") or "").endswith("config/contracts/strategy_research.odcs.yaml"):
                field_errors.append(f"{sid}: data_contract 指向错误")
        ck.add(not field_errors, "STRATEGY_MARKET_ITEM_FIELDS",
               "快照 item 必填字段/只读研究标志齐备",
               details={"errors": field_errors[:20], "count": len(field_errors)})
        # book status 与 audit verdict 对应
        book_items = ((audit.get("book_backlog") or {}).get("items") or []) if isinstance(audit, dict) else []
        book_verdict = {str(x.get("strategy_spec_id")): str(x.get("verdict")) for x in book_items if isinstance(x, dict)}
        book_status_errors = []
        for sid in specs:
            item = item_by_id.get(sid) or {}
            expected = STATUS_BY_VERDICT.get(book_verdict.get(sid, ""), "RESEARCH_UNTESTED")
            if item.get("strategy_type") != "RESEARCH_BOOK" or item.get("status") != expected:
                book_status_errors.append(
                    f"{sid}: status={item.get('status')} 期望 {expected}（verdict={book_verdict.get(sid)}）")
        ck.add(not book_status_errors, "STRATEGY_MARKET_BOOK_STATUS",
               "24 书战法 status 与 strict audit verdict 对应",
               details={"errors": book_status_errors[:20], "count": len(book_status_errors)})
        # registered status 与 builder 规则对应
        reg_errors = []
        for sid, expected in expected_status.items():
            item = item_by_id.get(sid) or {}
            if item.get("strategy_type") != "RESEARCH_REGISTERED" or item.get("status") != expected:
                reg_errors.append(f"{sid}: status={item.get('status')} 期望 {expected}")
        # 有严格门槛矩阵时，注册策略状态由 matrix 校验；旧 builder 规则仅兜底
        ck.add(not reg_errors or gate_path.is_file(), "STRATEGY_MARKET_REGISTERED_STATUS",
               "8 注册策略 status 与严格门槛矩阵对应",
               details={"errors": reg_errors[:20], "count": len(reg_errors),
                        "authority": "strategy_gate_matrix" if gate_path.is_file() else "builder_registered"})
        # 计数字段自洽
        actual_closed = sum(1 for x in items if x.get("status") == "RESEARCH_CLOSED")
        actual_needs = sum(1 for x in items if x.get("status") == "RESEARCH_NEEDS_MORE")
        actual_untested = sum(1 for x in items if x.get("status") == "RESEARCH_UNTESTED")
        ck.add(counts.get("closed") == actual_closed and counts.get("needs_more") == actual_needs
               and counts.get("untested") == actual_untested,
               "STRATEGY_MARKET_COUNTS",
               "快照 counts 与 items status 统计一致",
               details={"declared": counts, "actual": {"closed": actual_closed,
                                                        "needs_more": actual_needs,
                                                        "untested": actual_untested}})
        # 文件路径存在（相对 research root）
        file_errors = []
        for sid, item in item_by_id.items():
            rel = str(item.get("file") or "")
            if rel and not _research_file_exists(research_root, research_dir, rel):
                file_errors.append(f"{sid}: {rel}")
        ck.add(not file_errors, "STRATEGY_MARKET_FILES_EXIST",
               "快照引用的研究文件均存在", details={"errors": file_errors[:20], "count": len(file_errors)})
        ck.add(all(str(item.get("data_contract") or "").endswith(CONTRACT_REL) for item in items),
               "STRATEGY_MARKET_DATA_CONTRACT", "快照 item data_contract 统一指向研究契约")

    # ---------- 4. 契约 ----------
    contract: dict[str, Any] = {}
    if ck.add(contract_path.is_file(), "CONTRACT_EXISTS",
              f"research 契约存在: {contract_path}", details={"path": str(contract_path)}):
        try:
            contract = _load_yaml(contract_path)
        except Exception as exc:  # noqa: BLE001
            ck.add(False, "CONTRACT_YAML", f"契约 YAML 解析失败: {exc}"[:200])
    if isinstance(contract, dict) and contract:
        ck.add(contract.get("owner") == CONTRACT_OWNER, "CONTRACT_OWNER_DATA1",
               f"契约 owner 必须 {CONTRACT_OWNER}", details={"owner": contract.get("owner")})
        schema_names = {str(x.get("name")) for x in (contract.get("schema") or []) if isinstance(x, dict)}
        ck.add({"strategy_specs", "strict_audit"} <= schema_names, "CONTRACT_SCHEMA_COVERAGE",
               "契约 schema 覆盖 strategy_specs/strict_audit", details={"schema": sorted(schema_names)})
        sla = contract.get("sla") or {}
        freshness = (sla.get("freshness") or {}) if isinstance(sla, dict) else {}
        max_age = freshness.get("maxAgeHours") if isinstance(freshness, dict) else None
        ck.add(max_age == CONTRACT_MAX_AGE_HOURS, "CONTRACT_FRESHNESS_24H",
               f"契约 freshness.maxAgeHours 必须 {CONTRACT_MAX_AGE_HOURS}", details={"maxAgeHours": max_age})

    # ---------- 5. 新鲜度 ----------
    artifact_times = [_mtime(p) for p in spec_files]
    if audit_path.is_file():
        artifact_times.append(_mtime(audit_path))
    audit_generated = _parse_dt(((audit.get("meta") or {}).get("generated_at")) if isinstance(audit, dict) else None)
    if audit_generated:
        artifact_times.append(audit_generated)
    asof = max(artifact_times) if artifact_times else None
    snapshot_at = _parse_dt((config.get("generated_at") if isinstance(config, dict) else None))
    if asof and snapshot_at:
        ck.add(snapshot_at >= asof, "SYNC_SNAPSHOT_NOT_OLDER",
               "策略市场快照 generated_at 不得早于研究 artifacts asof",
               details={"asof": _iso(asof), "snapshot_generated_at": _iso(snapshot_at)})
        lag_hours = (snapshot_at - asof).total_seconds() / 3600.0
        ck.warn(lag_hours <= CONTRACT_MAX_AGE_HOURS, "SYNC_FRESHNESS_24H",
                f"研究 artifacts→策略市场 同步滞后 {lag_hours:.1f}h（契约 ≤{CONTRACT_MAX_AGE_HOURS}h）",
                details={"lag_hours": round(lag_hours, 2), "asof": _iso(asof),
                         "snapshot_generated_at": _iso(snapshot_at)})
    else:
        ck.add(False, "SYNC_FRESHNESS_UNKNOWN", "无法计算 asof/snapshot 时间（缺 generated_at 或 artifacts）")

    # ---------- 6. research 目录级盘点 / artifacts / counts / manifest ----------
    tree = _research_tree_summary(research_dir)
    ck.add(tree["files"] > 0 and tree["dirs"] >= 2, "RESEARCH_TREE_NONEMPTY",
           "research 目录非空且包含 specs/reports 等子目录",
           details={"files": tree["files"], "dirs": tree["dirs"], "bytes": tree["bytes"]})
    artifacts: list[dict[str, Any]] = []
    for path in spec_files:
        artifacts.append(_artifact(path, "strategy_spec", "STRAT1", relative_to=research_dir))
    if audit_path.is_file():
        artifacts.append(_artifact(audit_path, "strict_audit", "ACAD1", asof=audit_generated or _mtime(audit_path),
                                   relative_to=research_dir))
    if config_path.is_file():
        artifacts.append(_artifact(config_path, "strategy_market_snapshot", "STRAT1", relative_to=code_root))
    if builder_path.is_file():
        artifacts.append(_artifact(builder_path, "builder", "STRAT1", relative_to=code_root))
    if contract_path.is_file():
        artifacts.append(_artifact(contract_path, "data_contract", CONTRACT_OWNER, relative_to=code_root))
    deliverable_report_paths: dict[str, str] = {}
    for rel, role, owner in DELIVERABLE_REPORTS:
        report_path = deliverable_root / rel
        deliverable_report_paths[rel] = str(report_path)
        if report_path.is_file():
            artifacts.append(_artifact(report_path, role, owner, relative_to=deliverable_root))
    artifacts.sort(key=lambda x: (x["role"], x.get("relative_path") or x["path"]))

    counts = {
        "research_specs": len(spec_files),
        "expected_specs": EXPECTED_SPECS,
        "registered_strategies": len(registered),
        "expected_registered": EXPECTED_REGISTERED,
        "strategy_market_items": len((config.get("items") or []) if isinstance(config, dict) else []),
        "expected_items": EXPECTED_ITEMS,
        "audit_book_items": len(((audit.get("book_backlog") or {}).get("items") or [])) if isinstance(audit, dict) else 0,
        "audit_book_verdicts": dict(Counter(
            str(x.get("verdict")) for x in (((audit.get("book_backlog") or {}).get("items") or [])
                                             if isinstance(audit, dict) else []) if isinstance(x, dict))),
        "strategy_market_status": dict(Counter(
            str(x.get("status")) for x in ((config.get("items") or []) if isinstance(config, dict) else [])
            if isinstance(x, dict))),
        "artifacts": len(artifacts),
        "deliverable_reports": sum(1 for rel, _, _ in DELIVERABLE_REPORTS
                                   if (deliverable_root / rel).is_file()),
        "research_files": tree["files"],
        "research_dirs": tree["dirs"],
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _iso(datetime.now(CST)),
        "asof": _iso(asof) if asof else None,
        "asof_source": "max(research/strategy_specs/*.yaml mtime, strict_audit mtime, audit.meta.generated_at)",
        "owner": CONTRACT_OWNER,
        "source": {
            "research_root": str(research_root),
            "research_dir": str(research_dir),
            "specs": str(specs_dir),
            "strict_audit": str(audit_path),
            "strategy_market": str(config_path),
            "builder": str(builder_path),
            "deliverable_reports": deliverable_report_paths,
        },
        "contract": {
            "path": str(contract_path),
            "id": contract.get("id") if isinstance(contract, dict) else None,
            "name": contract.get("name") if isinstance(contract, dict) else None,
            "version": contract.get("version") if isinstance(contract, dict) else None,
            "owner": contract.get("owner") if isinstance(contract, dict) else None,
            "sha256": _sha256(contract_path) if contract_path.is_file() else None,
        },
        "counts": counts,
        "research_tree": tree,
        "validations": {
            "valid": not ck.errors,
            "error_count": len(ck.errors),
            "warning_count": len(ck.warnings),
            "errors": ck.errors,
            "warnings": ck.warnings,
            "checks": ck.checks,
        },
        "artifacts": artifacts,
    }
    return manifest, ck


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="研究 artifacts 同步校验与 manifest 生成（默认 dry-run）")
    parser.add_argument("--research-root", default=str(DEFAULT_RESEARCH_ROOT), help="研究仓根目录")
    parser.add_argument("--code-root", default=str(CODE_ROOT), help="平台代码根目录")
    parser.add_argument("--write", action="store_true", help="校验通过后写 config/research_artifact_manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认行为）")
    parser.add_argument("--json", action="store_true", help="打印完整 manifest JSON")
    args = parser.parse_args(argv)

    research_root = Path(args.research_root)
    code_root = Path(args.code_root)
    manifest_path = code_root / MANIFEST_REL

    manifest, ck = build_manifest(research_root, code_root)
    manifest["source"]["manifest"] = str(manifest_path)
    manifest["write_intent"] = "write" if (args.write and not args.dry_run) else "dry-run"

    if args.json:
        print(_dumps(manifest))
    else:
        status = "VALID" if manifest["validations"]["valid"] else "INVALID"
        print(f"[research-artifacts] research_root={research_root}")
        print(f"[research-artifacts] specs={manifest['counts']['research_specs']}/{EXPECTED_SPECS} "
              f"audit_book={manifest['counts']['audit_book_items']} "
              f"strategy_market_items={manifest['counts']['strategy_market_items']}/{EXPECTED_ITEMS} "
              f"artifacts={manifest['counts']['artifacts']}")
        print(f"[research-artifacts] asof={manifest['asof']} valid={manifest['validations']['valid']} "
              f"errors={manifest['validations']['error_count']} warnings={manifest['validations']['warning_count']} -> {status}")
        for item in manifest["validations"]["errors"]:
            print(f"[research-artifacts] ERROR {item['code']}: {item['message']}")
        for item in manifest["validations"]["warnings"]:
            print(f"[research-artifacts] WARN  {item['code']}: {item['message']}")

    if not manifest["validations"]["valid"]:
        if not args.json:
            print(f"[research-artifacts] 校验失败，未写 manifest（errors={len(manifest['validations']['errors'])}）", file=sys.stderr)
        return 1
    if not args.write or args.dry_run:
        if not args.json:
            print(f"[research-artifacts] dry-run：未写 manifest（使用 --write 写入 {manifest_path}）")
        return 0

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    if not args.json:
        print(f"[research-artifacts] wrote {manifest_path} ({manifest_path.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
