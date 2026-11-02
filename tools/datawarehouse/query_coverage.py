# -*- coding: utf-8 -*-
"""S3-06 P1 专用查询覆盖率实测脚本（2026-11-02 task-50）。

对 `dataset_inventory_*.json` 的 314 数据集逐项判定 L/P/F/A 四维（全部走
DatasetRegistry.query() 引擎，与 CLI/HTTP/MCP 三面同源），输出
`research/reports/dataset_query_coverage_<date>.json`（K6 唯一验收证据）。

四维判定（方案 v1 §五）：
  L 列表：query(limit<=5) 返回非空 rows（行级数据）；
  P 分页：limit=k offset=0 与 offset=k 两页可区分；或 count<=k 时单页 + has_more=false 正确；
  F 条件：用首个可区分字段做 where，结果数 < 全集（或空结果 + no_data 可解释）；
  A 全量：count 可达（任意整数）+ truncated 显式（不静默截断）。
  L∧P∧F∧A 全过 → ready。

用法：
  python tools/datawarehouse/query_coverage.py [--json] [--out PATH] [--limit N]
退出码：0 完成（覆盖率不达标也为 0，由 JSON 判断）；1 环境/输出错误。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import (  # noqa: E402
    DatasetRegistry,
    QueryNotSupported,
    QueryParamError,
)

DIM_KEYS = ("L", "P", "F", "A")
EXCLUDE_GIT_MARK = ".git"


def _try_query(reg: DatasetRegistry, dataset_id: str, **kw) -> dict:
    try:
        return {"ok": True, "result": reg.query(dataset_id, **kw)}
    except (QueryNotSupported, QueryParamError, PermissionError, KeyError) as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    except Exception as exc:  # noqa: BLE001 — 单数据集异常不影响全量测量
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def _judge_sqlite_or_parquet(reg: DatasetRegistry, dataset_id: str,
                             max_page: int = 5) -> dict:
    """行级载体四维判定（sqlite / parquet）。失败项记 reason（不静默）。"""
    out = {"L": False, "P": False, "F": False, "A": False,
           "failed_dims": [], "reason": "", "evidence": {}}
    r1 = _try_query(reg, dataset_id, limit=max_page, count=True)
    if not r1["ok"]:
        out["failed_dims"] = list(DIM_KEYS)
        out["reason"] = "engine_error: %s" % r1["error"]
        return out
    res = r1["result"]
    out["carrier"] = res.get("carrier")
    columns = res.get("columns") or []
    total = res.get("count")
    out["evidence"]["count"] = total
    out["evidence"]["truncated"] = res.get("truncated")
    rows = res.get("rows") or []
    # L：非空列表
    if rows:
        out["L"] = True
    else:
        out["failed_dims"].append("L")
        out["reason"] = out["reason"] or "L: query 返回 0 行（state=%s, reason=%s）" % (
            res.get("state"), res.get("reason"))
    # A：count 可达 + truncated 显式
    if isinstance(total, int) and "truncated" in res:
        out["A"] = True
    else:
        out["failed_dims"].append("A")
        out["reason"] = (out["reason"] + "; " if out["reason"] else "") + "A: count 不可达/无 truncated 标记"
    # P：两页可区分；或单页 has_more=false 正确
    if isinstance(total, int) and total > 0:
        k = max(1, min(max_page, total // 2 or 1))
        p1 = _try_query(reg, dataset_id, limit=k, offset=0, count=True)
        p2 = _try_query(reg, dataset_id, limit=k, offset=k, count=True)
        if p1["ok"] and p2["ok"]:
            a, b = p1["result"], p2["result"]
            ra, rb = a.get("rows") or [], b.get("rows") or []
            single_page = isinstance(total, int) and total <= k
            differ = bool(ra) and bool(rb) and ra != rb
            if single_page:
                out["P"] = (a.get("has_more") is False
                            and a.get("next_offset") is None)
                out["evidence"]["pagination"] = "single_page"
            elif differ and "next_offset" in a:
                out["P"] = True
                out["evidence"]["pagination"] = "two_pages_differ"
            if not out["P"]:
                out["failed_dims"].append("P")
                out["reason"] = ((out["reason"] + "; ") if out["reason"] else "") + \
                    "P: 两页不可区分/单页标记错误（p1=%s, p2=%s）" % (
                        len(ra), len(rb))
        else:
            out["failed_dims"].append("P")
            out["reason"] = ((out["reason"] + "; ") if out["reason"] else "") + \
                "P: 分页查询失败（%s / %s）" % (p1.get("error"), p2.get("error"))
    else:
        out["failed_dims"].append("P")
        out["reason"] = (out["reason"] + "; " if out["reason"] else "") + "P: 总数为 0/不可达"
    # F：白名单字段条件查询，结果严格小于全集（或空 + no_data 可解释）
    if isinstance(total, int) and total > 0 and columns:
        tried = []
        attempts = 0
        for idx, col in enumerate(columns[:10]):
            if idx >= max((len(r) for r in rows), default=0):
                break
            vals = []
            for row in rows[:5]:
                if idx < len(row) and row[idx] is not None and row[idx] not in vals:
                    vals.append(row[idx])
            for val in vals[:2]:
                attempts += 1
                if attempts > 15:
                    break
                fq = _try_query(reg, dataset_id,
                                where=[{"field": col, "op": "=", "value": val}],
                                limit=max_page, count=True)
                if not fq["ok"]:
                    continue
                fr = fq["result"]
                fc = fr.get("count")
                if fc is not None and (fc < total or total <= 1):
                    out["F"] = True
                    out["evidence"]["filter"] = {"field": col, "value": val,
                                                 "count": fc, "total": total}
                    break
                if fr.get("state") == "no_data" and fc == 0 and fr.get("reason"):
                    out["F"] = True
                    out["evidence"]["filter"] = {"field": col, "value": val,
                                                 "count": 0,
                                                 "reason": fr.get("reason")}
                    break
            tried.append(col)
            if out["F"] or attempts > 15:
                break
        if not out["F"]:
            out["failed_dims"].append("F")
            out["reason"] = ((out["reason"] + "; ") if out["reason"] else "") + \
                "F: 无可区分字段（试过 %s）" % (tried or columns[:10])
    out["ready"] = all(out[k] for k in DIM_KEYS)
    return out


def judge_dataset(reg: DatasetRegistry, dataset_id: str, max_page: int = 5) -> dict:
    """单数据集四维判定（覆盖率与单测共用入口）。"""
    d = reg.get(dataset_id)
    if not d:
        return {"dataset_id": dataset_id, "kind": None, "carrier": "unknown",
                "L": False, "P": False, "F": False, "A": False, "ready": False,
                "failed_dims": list(DIM_KEYS), "reason": "not_found",
                "evidence": {}}
    # 载体识别走同一引擎（不支持的载体在 query 前即可判定 fails）
    r0 = _try_query(reg, dataset_id, limit=1, count=True)
    base = {"dataset_id": dataset_id, "kind": d.get("kind"),
            "domain": d.get("domain"),
            "carrier": "unknown", "L": False, "P": False, "F": False, "A": False,
            "ready": False, "failed_dims": list(DIM_KEYS), "reason": "",
            "evidence": {}}
    if not r0["ok"]:
        base["reason"] = "not_supported: %s" % r0["error"]
        p = Path(str(d.get("path") or ""))
        if not p.is_absolute():
            p = REPO / p
        if not p.exists():
            base["reason"] = "no_data: 路径不存在"
            base["excluded"] = True
        return base
    base["carrier"] = r0["result"].get("carrier")
    if base["carrier"] in ("sqlite_table", "parquet_file", "dir_parquet"):
        judged = _judge_sqlite_or_parquet(reg, dataset_id, max_page)
        base.update(judged)
        return base
    # 非行级载体：JSONL 基础行不算四维就绪（P2），空目录/混合目录显式 no_data/not_supported
    res = r0["result"]
    if base["carrier"] == "dir_empty":
        base["excluded"] = True
        base["reason"] = "N/A: 空目录（无数据）"
    elif "jsonl" in str(base["carrier"]):
        base["reason"] = "not_supported: jsonl 行级条件/计数属 P2"
    else:
        base["reason"] = "not_supported: 载体 %s 属 P2" % base["carrier"]
    return base


def _is_junk(d: dict) -> bool:
    path = str(d.get("path") or "").lower()
    return EXCLUDE_GIT_MARK in path.split("\\") or EXCLUDE_GIT_MARK in path.split("/")


def measure(limit_only: int | None = None, max_page: int = 5) -> dict:
    reg = DatasetRegistry()
    rows = []
    for d in reg.datasets:
        if limit_only and len(rows) >= limit_only:
            break
        try:
            rows.append(judge_dataset(reg, d["dataset_id"], max_page=max_page))
        except Exception as exc:  # noqa: BLE001
            rows.append({"dataset_id": d["dataset_id"], "kind": d.get("kind"),
                         "carrier": "unknown", "L": False, "P": False, "F": False,
                         "A": False, "ready": False, "failed_dims": list(DIM_KEYS),
                         "reason": "exception: %s: %s" % (type(exc).__name__, str(exc)[:160]),
                         "evidence": {}})
    by_id = {d["dataset_id"]: d for d in reg.datasets}
    excluded = []
    for r in rows:
        if r.get("excluded"):
            excluded.append({"id": r["dataset_id"],
                             "reason": r.get("reason") or "N/A"})
        elif _is_junk(by_id.get(r["dataset_id"]) or {}):
            excluded.append({"id": r["dataset_id"], "reason": "N/A: .git 伪数据集"})
    excluded_ids = {e["id"] for e in excluded}
    for r in rows:
        if r["dataset_id"] in excluded_ids:
            r["ready"] = False
    ready = sum(1 for r in rows if r.get("ready"))
    total_raw = len(rows)
    denominator = total_raw - len(excluded)

    def _bucket(key):
        c = Counter()
        for r in rows:
            k = str(r.get(key) or "unknown")
            c["total:" + k] += 1
            if r.get("ready"):
                c["ready:" + k] += 1
        return {k: v for k, v in sorted(c.items())}

    return {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "schema": "dataset_query_coverage/v1（S3-06 P1；四维 L/P/F/A 实测，engine=DatasetRegistry.query）",
        "total_raw": total_raw,
        "denominator": denominator,
        "excluded": sorted(excluded, key=lambda x: x["id"]),
        "ready": ready,
        "coverage_raw_pct": round(ready * 100.0 / total_raw, 2) if total_raw else 0.0,
        "coverage_clean_pct": round(ready * 100.0 / denominator, 2) if denominator else 0.0,
        "by_domain": _bucket("domain"),
        "by_kind": _bucket("kind"),
        "by_carrier": _bucket("carrier"),
        "failures": [
            {"dataset_id": r["dataset_id"], "kind": r.get("kind"),
             "carrier": r.get("carrier"), "failed_dims": r.get("failed_dims") or [],
             "reason": (r.get("reason") or "")[:300]}
            for r in rows if not r.get("ready") and r["dataset_id"] not in excluded_ids
        ],
        "carriers": {r["dataset_id"]: r.get("carrier") for r in rows},
        "dims": {r["dataset_id"]: {k: bool(r.get(k)) for k in DIM_KEYS} for r in rows},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S3-06 专用查询覆盖率实测（只读）")
    ap.add_argument("--json", action="store_true", help="stdout 打印完整 JSON")
    ap.add_argument("--out", default=str(REPO / "research" / "reports"
                                        / ("dataset_query_coverage_%s.json"
                                           % datetime.date.today().strftime("%Y%m%d"))))
    ap.add_argument("--max-page", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个数据集（冒烟用；0=全量）")
    args = ap.parse_args(argv)
    # 覆盖率实测不写审计日志（避免 314*N 行噪音）；可用环境变量覆盖
    import os
    os.environ.setdefault("DW_QUERY_AUDIT", "off")
    report = measure(limit_only=args.limit or None, max_page=args.max_page)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("coverage: total_raw=%d denominator=%d ready=%d raw=%.2f%% clean=%.2f%%"
          % (report["total_raw"], report["denominator"], report["ready"],
             report["coverage_raw_pct"], report["coverage_clean_pct"]))
    print("by_carrier:", json.dumps(report["by_carrier"], ensure_ascii=False))
    print("by_kind:", json.dumps(report["by_kind"], ensure_ascii=False))
    print("excluded:", [e["id"] for e in report["excluded"]])
    print("out:", out)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
