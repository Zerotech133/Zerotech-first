# -*- coding: utf-8 -*-
"""学习结论静态导出（W3-3 / task-151）：conclusions lib → portal/static/learning_conclusions.json。

输入（只读）：research/reports/learning_conclusions_lib_20261103.json
  - schema_version: learning_conclusions.v1
  - conclusions: Top100（字段与 consumer_contract.required_fields 对齐）
  - consumer_contract: endpoint/query/required_fields/legions
  - meta: 总数 730、Top100、分布、仲裁队列 24
输出：portal/static/learning_conclusions.json（Top100 + summary + consumer_contract + query_support）
  **确定性输出**（generated_at 取源库生成时间），重复导出字节一致（幂等）。
  **当前仅静态**：建议 `GET /api/learning/conclusions` 由 8091 接线，本工具不改 portal_api.py、不加路由。

CLI::
    python tools/kb_corpus/export_conclusions.py build --write
    python tools/kb_corpus/export_conclusions.py build --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
SRC = R / "learning_conclusions_lib_20261103.json"
STATIC = REPO / "portal" / "static"
OUT = STATIC / "learning_conclusions.json"
DATE = "20261103"
SCHEMA = "learning_conclusions.v1"
ENDPOINT = "/api/learning/conclusions"
STATIC_URL = "/static/learning_conclusions.json"
TOP_N = 100


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dist(items: list[dict], fn) -> dict:
    return dict(Counter(fn(x) for x in items))


def build(*, src_path: str | Path = SRC, out_path: str | Path = OUT, write: bool = False) -> dict:
    src = load(Path(src_path))
    conclusions = [c for c in (src.get("conclusions") or []) if isinstance(c, dict)]
    contract = src.get("consumer_contract") or {}
    required = list(contract.get("required_fields") or [])
    meta = src.get("meta") or {}
    total_lib = int(meta.get("conclusions_total") or len(conclusions))
    # ---- 校验：必填字段 / 嵌套字段 ----
    errors: list[str] = []
    for i, c in enumerate(conclusions):
        for f in required:
            if f not in c:
                errors.append(f"conclusions[{i}] 缺字段 {f}")
        ex = c.get("executability")
        if not (isinstance(ex, dict) and ex.get("level") in ("A", "B", "C")):
            errors.append(f"conclusions[{i}] executability.level 非法")
        tc = c.get("tactic")
        if not (isinstance(tc, dict) and tc.get("category")):
            errors.append(f"conclusions[{i}] tactic.category 缺失")
        if not isinstance(c.get("sources"), list):
            errors.append(f"conclusions[{i}] sources 非数组")
    if len(conclusions) != TOP_N:
        errors.append(f"Top100 应为 {TOP_N}，实际 {len(conclusions)}")
    if src.get("schema_version") != SCHEMA:
        errors.append(f"源 schema_version 应为 {SCHEMA}")
    # ---- 汇总 ----
    exec_dist = _dist(conclusions, lambda x: x["executability"]["level"])
    tactic_dist = _dist(conclusions, lambda x: x["tactic"]["category"])
    conf_dist = _dist(conclusions, lambda x: x.get("confidence"))
    claim_dist = _dist(conclusions, lambda x: x.get("claim_type"))
    review_dist = _dist(conclusions, lambda x: x.get("review_status"))
    quant_ok = sum(1 for c in conclusions
                   if c["executability"]["level"] in ("A", "B") and c.get("confidence") in ("高", "中"))
    research_ok = sum(1 for c in conclusions if c.get("confidence") == "高")
    payload = {
        "schema_version": SCHEMA,
        "generated_at": src.get("generated_at"),
        "date": src.get("date") or DATE,
        "source": {
            "path": str(src_path), "sha256": sha(Path(src_path)),
            "generated_at": src.get("generated_at"), "task": src.get("task"),
            "title": src.get("title"), "read_only": src.get("read_only"),
            "read_only_verified": src.get("read_only_verified"),
        },
        "endpoint": {
            "current": STATIC_URL,
            "static_copy": "portal/static/learning_conclusions.json",
            "suggested_api": ENDPOINT,
            "status": "static_only_contract_first（当前静态导出；8091 API 待接线，契约先行）",
        },
        "consumer_contract": contract,
        "query_support": {
            "params": contract.get("query_params") or {},
            "server_side": False,
            "note": "当前静态端点由消费方按参数本地过滤；8091 接线后由服务端过滤（见契约文档）",
            "examples": [
                f"{STATIC_URL}（全量 Top100）",
                f"{ENDPOINT}?confidence=高&limit=20（建议 API 形式）",
                f"{ENDPOINT}?tactic=缠论&executability=A&limit=50",
            ],
        },
        "summary": {
            "conclusions_total_lib": total_lib,
            "top_n": TOP_N,
            "returned": len(conclusions),
            "confidence_dist": conf_dist,
            "executability_dist": exec_dist,
            "tactic_dist": tactic_dist,
            "claim_type_dist": claim_dist,
            "review_status_dist": review_dist,
            "multi_source_conclusions": meta.get("conclusions_multi_source"),
            "conflict_count": meta.get("conflict_count", 0),
            "arbitration_queue_count": meta.get("arbitration_queue_count", 0),
            "score_range": [min(c.get("score") for c in conclusions), max(c.get("score") for c in conclusions)],
        },
        "legions": {
            **{k: v for k, v in (contract.get("legions") or {}).items()},
            "counts": {"quant": quant_ok, "research": research_ok,
                       "arbitration": int(meta.get("arbitration_queue_count") or 0)},
        },
        "arbitration_queue": src.get("arbitration_queue") or [],
        "conclusions": conclusions,
        "validation": {"ok": not errors, "errors": errors[:20],
                       "required_fields": required, "checked": len(conclusions)},
        "disclaimer": "学习结论为研究输出，不构成投资建议；军团消费需按 consumer_contract 门禁（四关/PIT/引用可追溯）。",
    }
    payload["source_unchanged"] = (payload["source"]["sha256"] == sha(Path(src_path)))
    # ---- 写入（确定性：仅内容变化时写） ----
    if write:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
        prev = out.read_text(encoding="utf-8") if out.is_file() else None
        if prev == text:
            payload["_write"] = {"changed": False, "path": str(out)}
        else:
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(out)
            payload["_write"] = {"changed": True, "path": str(out), "bytes": out.stat().st_size}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="学习结论静态导出（当前静态；API 契约先行）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--src", default=str(SRC))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(src_path=args.src, out_path=args.out, write=args.write)
    v, s = payload["validation"], payload["summary"]
    if args.json:
        print(json.dumps({k: v2 for k, v2 in payload.items() if k != "_write"}, ensure_ascii=False, indent=2))
    else:
        print(f"[export_conclusions] {payload['schema_version']} | Top100={s['returned']}（库内 {s['conclusions_total_lib']}）"
              f" | 置信 {json.dumps(s['confidence_dist'], ensure_ascii=False)}"
              f" | 可执行 {json.dumps(s['executability_dist'], ensure_ascii=False)}")
        print(f"  校验：{'PASS' if v['ok'] else 'FAIL'} | required={len(v['required_fields'])} 字段 × {v['checked']} 条"
              f" | 军团视图 counts={json.dumps(payload['legions']['counts'], ensure_ascii=False)}")
        if payload.get("_write"):
            w = payload["_write"]
            print(f"  输出：{w['path']}（changed={w['changed']}" + (f", {w['bytes']}B" if w.get('bytes') else "") + "）")
        if not args.write:
            print("  （未写盘；加 --write 输出静态文件）")
        for e in v["errors"][:6]:
            print("  -", e, file=sys.stderr)
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
