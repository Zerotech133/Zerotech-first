# -*- coding: utf-8 -*-
"""kg_cq.py — Competency Questions 治理能力回归引擎（C3）。

把 cq_registry 里的"图谱必须能回答的问题"跑成 CI 断言：
  1) 静态守卫（始终）：CQ 引用的节点标签 / 关系类型必须仍存在于 ontology 的
     TBox。本体删类型即回归 → FAIL。
  2) 实时守卫（仅连上 Neo4j）：live_non_empty 的 CQ 还要求真实图里确有实例
     （捕捉"类型在、但建图断流"）。无 DB / 无 driver 时实时 SKIP（非 FAIL），
     可用性由 gate() 负责，CQ 只管能力完整性，二者正交。

CLI：python kg_cq.py [--report JSON] [--junit XML] [--strict] [--live] [--domain X]
退出码 0=通过 / 1=失败，供 CI 消费。无 --live 时仅跑静态（安全、离线、快）。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import ontology as O          # noqa: E402  受治理 TBox（类型真相源）
import cq_registry as CQ       # noqa: E402  CQ 注册表


# ── Cypher 引用抽取 ────────────────────────────────────────────────────────
_NODE = re.compile(r"\(([^()]*)\)")          # 节点 (var:Label)
_REL = re.compile(r"\[([^\[\]]*)\]")          # 关系 [var:TYPE]


def extract_refs(cypher):
    """从 Cypher 抽取引用的节点标签与关系类型（供静态校验）。

    节点标签来自 `()` 内 `:Label`；关系类型来自 `[]` 内 `:TYPE`
    （支持 `[:A|B]` 多类型与 `[:TYPE*1..3]` 变长，剥离 `*` 其后修饰）。
    """
    labels, rels = set(), set()
    for m in _NODE.finditer(cypher):
        for lm in re.finditer(r":\s*([A-Za-z_]\w*)", m.group(1)):
            labels.add(lm.group(1))
    for m in _REL.finditer(cypher):
        cm = re.search(r":\s*([A-Za-z_][\w|*.0-9]*)", m.group(1))
        if not cm:
            continue
        for tok in cm.group(1).split("|"):
            tok = re.sub(r"\*.*$", "", tok)   # 仅剥离变长修饰 *1..3，保留类型名内数字
            if tok:
                rels.add(tok)
    return labels, rels


def schema_validate(cqs):
    """静态校验：CQ 引用的 label/edge 必须存在于 TBox。返回 [(cq_id,kind,name)]。"""
    known_nodes = set(O.NODE_TYPES)
    known_edges = set(O.EDGE_TYPES)
    issues = []
    for cq in cqs:
        labels, rels = extract_refs(cq["cypher"])
        for l in sorted(labels):
            if l not in known_nodes:
                issues.append((cq["id"], "node", l))
        for r in sorted(rels):
            if r not in known_edges:
                issues.append((cq["id"], "edge", r))
    return issues


def live_run(cqs, session):
    """实时断言：在已连 Neo4j session 上跑每条 CQ。返回逐条结果列表。"""
    out = []
    for cq in cqs:
        rec = {
            "id": cq["id"],
            "question": cq["question"],
            "domain": cq["domain"],
            "cypher": cq["cypher"],
            "live_non_empty": cq["live_non_empty"],
            "ok": None,
            "skipped": False,
            "rows": 0,
            "value": None,
            "error": None,
        }
        try:
            rows = list(session.run(cq["cypher"]))
            rec["rows"] = len(rows)
            val = None
            if rows:
                first = rows[0]
                for k in first.keys():
                    v = first[k]
                    if isinstance(v, (int, float)):
                        val = v
                        break
            rec["value"] = val
            if cq["live_non_empty"]:
                if val is None or val <= 0:
                    rec["ok"] = False
                    rec["error"] = "live_non_empty 未满足（实例数=%s）" % (val,)
                else:
                    rec["ok"] = True
            else:
                rec["ok"] = True   # 仅要求可执行即通过
        except Exception as exc:  # noqa: BLE001
            rec["ok"] = False
            rec["error"] = str(exc)[:300]
        out.append(rec)
    return out


def assess(cqs=None, session=None, strict=False):
    """综合裁决。session=None → 实时部分全 SKIP。"""
    cqs = cqs or CQ.all_cqs()
    schema_issues = schema_validate(cqs)
    live = live_run(cqs, session) if session is not None else None
    schema_fail = len(schema_issues)
    live_fail = sum(1 for r in (live or []) if r["ok"] is False)
    ran = sum(1 for r in (live or []) if r["ok"] is not None)
    skipped = sum(1 for r in (live or []) if r["skipped"])
    passed = (schema_fail == 0) and (live_fail == 0)
    return {
        "cq_registry_version": CQ.CQ_REGISTRY_VERSION,
        "tbox_version": getattr(O, "TBOX_VERSION", None),
        "total": len(cqs),
        "schema_checked": len(cqs),
        "schema_failures": schema_fail,
        "schema_issues": [
            {"cq": i[0], "kind": i[1], "name": i[2]} for i in schema_issues
        ],
        "live_ran": ran,
        "live_skipped": skipped,
        "live_failures": live_fail,
        "live": live,
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "note": "无 Neo4j session 时实时断言 SKIP（可用性由 gate 负责）",
    }


# ── Neo4j 连接（仅 --live 时）──────────────────────────────────────────────
def _connect_neo4j():
    """解析凭据并尝试连接；缺失/失败返回 (None, None)。不静默抛错。"""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("[kg_cq] neo4j driver 未安装，实时断言 SKIP", file=sys.stderr)
        return None, None
    uri = os.environ.get("KG_NEO4J_URI") or os.environ.get("NEO4J_URI")
    user = os.environ.get("KG_NEO4J_USER") or os.environ.get("NEO4J_USER") or "neo4j"
    pw = os.environ.get("KG_NEO4J_PASSWORD") or os.environ.get("NEO4J_PASSWORD")
    db = os.environ.get("KG_NEO4J_DB") or os.environ.get("NEO4J_DB") or "neo4j"
    if not uri or not pw:
        print("[kg_cq] Neo4j 凭据未配置，实时断言 SKIP", file=sys.stderr)
        return None, None
    try:
        drv = GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=5)
        drv.verify_connectivity()
        return drv, db
    except Exception as exc:  # noqa: BLE001
        print("[kg_cq] Neo4j 连接失败: %s，实时断言 SKIP" % exc, file=sys.stderr)
        return None, None


# ── 报告输出 ───────────────────────────────────────────────────────────────
def _print_report(res, strict=False):
    print("=" * 64)
    print("KG Competency Questions 回归  (registry v%s / tbox v%s)"
          % (res["cq_registry_version"], res["tbox_version"]))
    print("=" * 64)
    print("CQ 总数            : %d" % res["total"])
    print("静态校验失败       : %d" % res["schema_failures"])
    for i in res["schema_issues"]:
        print("  ✗ [%s] 引用了不存在的 %s: %s" % (i["cq"], i["kind"], i["name"]))
    print("实时断言 运行/跳过 : %d / %d" % (res["live_ran"], res["live_skipped"]))
    print("实时断言失败       : %d" % res["live_failures"])
    for r in (res["live"] or []):
        if r["ok"] is False:
            print("  ✗ [%s] %s  (value=%s, err=%s)"
                  % (r["id"], r["question"][:40], r["value"], r["error"]))
    verdict = "PASS" if res["passed"] else "FAIL"
    print("-" * 64)
    print("裁决: %s   exit_code=%d" % (verdict, res["exit_code"]))
    print("=" * 64)


def _write_json(path, res):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("[kg_cq] JSON 报告已写: %s" % path)


def _write_junit(path, res):
    parts = ['<?xml version="1.0" encoding="utf-8"?>']
    total = res["total"] + (res["live_skipped"] or 0)
    fails = res["schema_failures"] + res["live_failures"]
    parts.append(
        '<testsuite name="kg_cq" tests="%d" failures="%d" '
        'errors="0" skipped="%d" timestamp="%s">'
        % (res["total"], fails, res["live_skipped"], res["checked_at"])
    )
    # 静态校验：每条 CQ 一个用例
    for cq in CQ.all_cqs():
        bad = [i for i in res["schema_issues"] if i["cq"] == cq["id"]]
        if bad:
            msg = "; ".join("%s %s" % (b["kind"], b["name"]) for b in bad)
            parts.append(
                '  <testcase name="%s" classname="cq.schema">'
                '<failure message="%s"/></testcase>' % (cq["id"], msg)
            )
        else:
            parts.append('  <testcase name="%s" classname="cq.schema"/>' % cq["id"])
    # 实时校验：仅已运行的用例
    for r in (res["live"] or []):
        if r["ok"] is None:
            parts.append(
                '  <testcase name="%s" classname="cq.live">'
                '<skipped/></testcase>' % r["id"]
            )
        elif r["ok"]:
            parts.append('  <testcase name="%s" classname="cq.live"/>' % r["id"])
        else:
            parts.append(
                '  <testcase name="%s" classname="cq.live">'
                '<failure message="%s"/></testcase>' % (r["id"], r["error"] or "live_fail")
            )
    parts.append("</testsuite>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print("[kg_cq] JUnit 报告已写: %s" % path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KG Competency Questions 回归引擎 (C3)")
    ap.add_argument("--report", help="输出 JSON 报告路径")
    ap.add_argument("--junit", help="输出 JUnit XML 报告路径")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式（保留位：当前静态+实时失败即 FAIL）")
    ap.add_argument("--live", action="store_true",
                    help="尝试连接 Neo4j 跑实时断言（默认仅静态）")
    ap.add_argument("--domain", help="仅跑指定治理域的 CQ")
    args = ap.parse_args(argv)

    cqs = CQ.all_cqs()
    if args.domain:
        cqs = [c for c in cqs if c["domain"] == args.domain]

    drv = None
    session = None
    if args.live:
        drv, db = _connect_neo4j()
        if drv:
            session = drv.session(database=db)
    try:
        res = assess(cqs, session=session, strict=args.strict)
    finally:
        if drv:
            drv.close()

    _print_report(res, strict=args.strict)
    if args.report:
        _write_json(args.report, res)
    if args.junit:
        _write_junit(args.junit, res)
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
