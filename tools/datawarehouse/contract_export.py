# -*- coding: utf-8 -*-
"""数据契约生成器：catalog v12 六核心表 → ODCS v3 契约（datacontract-cli 可 lint）。

背景（P1-④，2026-10-27）：
- 数据仓已有一真源 `quant_backtest/data/catalog.json`（v12/245 表）；
- 本工具把「六核心表」从 catalog+parquet 实际 schema 生成为 Open Data Contract Standard
  （ODCS v3）YAML，落 `agent_platform/catalogs/contracts/`，供 datacontract-cli lint/导出，
  并为下游 dmart/看板提供 breaking-change 预警基线。

用法：
  python tools/datawarehouse/contract_export.py --check      # 校验契约与 catalog 是否一致
  python tools/datawarehouse/contract_export.py --apply      # 重新生成
  python tools/datawarehouse/contract_export.py --json
退出码：0=一致/成功；1=不一致/生成失败；2=致命。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = common.DATA
CATALOG = DATA / "catalog.json"
OUT_DIR = REPO / "agent_platform" / "catalogs" / "contracts"

CORE_TABLES = ["daily", "adj_factor", "daily_basic", "index_daily", "moneyflow", "margin_detail"]

# pyarrow 类型 -> ODCS logicalType（保守映射，未知一律 string 并记 physicalType）
ARROW_TYPE_MAP = {
    "string": "string", "large_string": "string", "utf8": "string",
    "int8": "integer", "int16": "integer", "int32": "integer", "int64": "integer",
    "uint8": "integer", "uint16": "integer", "uint32": "integer", "uint64": "integer",
    "float": "number", "double": "number", "halffloat": "number",
    "bool": "boolean",
    "date32": "date", "date64": "date", "timestamp": "timestamp",
}


def _map_type(arrow_type) -> str:
    s = str(arrow_type).lower()
    for k, v in ARROW_TYPE_MAP.items():
        if s.startswith(k):
            return v
    return "string"


def _sample_schema(entry: dict) -> list[tuple[str, str]]:
    """从 store 抽 1 个 parquet 读实际列类型；失败回退 catalog schema（全 string）。"""
    store = Path(entry.get("store") or "")
    cols: list[tuple[str, str]] = []
    if store.exists():
        try:
            import pyarrow.parquet as pq
            files = sorted(store.rglob("*.parquet"))
            if files:
                sch = pq.read_schema(files[-1])
                cols = [(f.name, _map_type(f.type)) for f in sch]
        except Exception:  # noqa: BLE001
            cols = []
    if not cols:
        cols = [(name, "string") for name in (entry.get("schema") or [])]
    return cols


def build_contract(name: str, entry: dict) -> dict:
    cols = _sample_schema(entry)
    pk = entry.get("primary_key") or []
    props = []
    for col, typ in cols:
        p = {"name": col, "logicalType": typ, "physicalType": typ, "required": col in pk}
        if col in pk:
            p["primaryKey"] = True
        if entry.get("date_col") == col:
            p["description"] = "业务日期列（event date）"
        elif entry.get("code_col") == col:
            p["description"] = "标的代码列"
        props.append(p)
    store = (entry.get("store") or "").replace("\\", "/")
    rel = store.split("/quant_backtest/data/")[-1] if "/quant_backtest/data/" in store else store
    contract = {
        "apiVersion": "v3.0.2",
        "kind": "DataContract",
        "id": "zerobond." + name,
        "name": name,
        "version": "1.0.0",
        "status": "draft",
        "description": {
            "purpose": (entry.get("desc") or ("数据仓核心表 %s（来源 %s，频率 %s）" % (
                name, entry.get("source", "?"), entry.get("freq", "?")))),
            "limitations": "catalog 基线 v12；schema 取自 store 抽样 parquet footer",
        },
        "domain": "quant",
        "dataProduct": "zerobond-datawarehouse",
        "team": [{"username": "data1@open-metadata.org", "name": "DATA1 张松", "role": "data steward"}],
        "servers": [{
            "server": "local-datawarehouse",
            "type": "local",
            "path": rel,
            "format": "parquet",
        }],
        "schema": [{
            "name": name,
            "physicalName": rel,
            "logicalType": "object",
            "properties": props,
        }],
        "slaProperties": [
            {"property": "frequency", "value": str(entry.get("freq", "D"))},
        ],
    }
    return contract


def dump_yaml(obj: dict) -> str:
    try:
        import yaml
    except ImportError:
        print("PyYAML 缺失", file=sys.stderr)
        raise SystemExit(2)
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=120)


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def plan(apply: bool = False) -> dict:
    cat = load_catalog()
    tables = cat.get("tables") or {}
    out = {"code": 0, "tables": [], "missing": []}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in CORE_TABLES:
        entry = tables.get(name)
        if not entry:
            out["missing"].append(name)
            continue
        text = dump_yaml(build_contract(name, entry))
        path = OUT_DIR / (name + ".odcs.yaml")
        exists = path.exists()
        same = exists and path.read_text(encoding="utf-8") == text
        if apply and not same:
            path.write_text(text, encoding="utf-8", newline="\n")
        out["tables"].append({"table": name, "path": str(path.relative_to(REPO)),
                              "exists": exists, "matches_catalog": same,
                              "columns": len(build_contract(name, entry)["schema"][0]["properties"])})
    if out["missing"]:
        out["code"] = 1
    if not apply and any(not t["matches_catalog"] for t in out["tables"]):
        out["code"] = 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="catalog → ODCS 数据契约生成器")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lint", action="store_true", help="用 datacontract-cli 校验生成的 ODCS（若已安装）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not CATALOG.exists():
        print("catalog missing: %s" % CATALOG, file=sys.stderr)
        return 2
    res = plan(apply=args.apply)
    if args.lint:
        import shutil
        dc = shutil.which("datacontract") or r"C:\项目\py312\Scripts\datacontract.exe"
        lint = []
        for tinfo in res["tables"]:
            path = REPO / tinfo["path"]
            if not Path(dc).exists() or not path.exists():
                lint.append({"table": tinfo["table"], "lint": "skipped"})
                continue
            env2 = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", TERM="dumb")
            r = __import__("subprocess").run([dc, "lint", str(path)], capture_output=True,
                                             text=True, encoding="utf-8", errors="replace",
                                             env=env2, timeout=300)
            lint.append({"table": tinfo["table"], "lint": "ok" if r.returncode == 0 else "fail",
                         "rc": r.returncode})
        res["lint"] = lint
        if any(x.get("lint") == "fail" for x in lint):
            res["code"] = 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for t in res["tables"]:
            print("%-14s %-6s cols=%-3d %s" % (t["table"], "OK" if t["matches_catalog"] else "DIFF",
                                               t["columns"], t["path"]))
        if res["missing"]:
            print("missing tables:", res["missing"])
        print("result:", "OK" if res["code"] == 0 else "DRIFT")
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
