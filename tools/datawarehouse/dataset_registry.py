# -*- coding: utf-8 -*-
"""数据集级统一登记/取数（WP-I1/I3 骨架）。

- 数据来源：research/reports/dataset_inventory_*.json（全域盘点，301 数据集）
- 能力：list / search / get / sample / emit（生成 dataset_registry YAML 草稿）
- 边界：只读；路径限定在平台数据根/知识库/自动化产物白名单内；不写业务数据。
"""
from __future__ import annotations

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
]

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


def slugify(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    return s or "dataset"


class DatasetRegistry:
    MAX_JSON_BYTES = 50 * 1024 * 1024
    MAX_DIR_SCAN = 500

    def __init__(self, inventory_path: Path | str | None = None,
                 allowed_roots: list[Path] | None = None,
                 max_json_bytes: int | None = None):
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

    # ---------- 查询 ----------
    def stats(self) -> dict:
        total = len(self.datasets)
        return {
            "inventory": str(self.inventory_path),
            "total": total,
            "registered": sum(1 for d in self.datasets if d["registered"]),
            "exposed": sum(1 for d in self.datasets if d["exposed"]),
            "by_domain": _count_by(self.datasets, "domain"),
            "by_kind": _count_by(self.datasets, "kind"),
        }

    def list(self, domain: str | None = None, q: str | None = None,
             registered: bool | None = None, exposed: bool | None = None,
             limit: int = 50) -> list[dict]:
        out = []
        ql = (q or "").lower()
        for d in self.datasets:
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
        return self._by_id.get(dataset_id)

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
                import datetime
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
            import datetime
            return datetime.datetime.fromtimestamp(best).strftime("%Y-%m-%d %H:%M") if best else None
        except Exception:  # noqa: BLE001
            return None

    def sample(self, dataset_id: str, n: int = 3) -> dict:
        d = self.get(dataset_id)
        if not d:
            raise KeyError(f"未知 dataset_id: {dataset_id}")
        p = self._resolve(d)
        if not p.exists():
            return {"dataset_id": dataset_id, "exists": False, "path": str(p)}
        kind = d["kind"]
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
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")
