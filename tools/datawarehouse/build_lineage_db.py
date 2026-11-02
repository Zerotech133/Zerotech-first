# -*- coding: utf-8 -*-
"""S3-05 派生血缘入 DB：hypotheses / 策略库 / KB 四份 lineage YAML → research/lineage.db。

只读输入；输出 SQLite（nodes/edges/meta）+ JSON 摘要。幂等重建。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CAT = REPO / "agent_platform" / "catalogs"
HYP = REPO / "research" / "hypotheses.db"
STRAT = CAT / "strategy_registry.json"
LINEAGE_YAMLS = ["report_rc_lineage.yaml", "research_reports_kb_lineage.yaml",
                 "strategy_classics_lineage.yaml", "youtube_quant_corpus_lineage.yaml"]
OUT_DB = REPO / "research" / "lineage.db"
OUT_JSON = REPO / "research" / "reports" / "lineage_20261102.json"


def _norm_path(p: str) -> str:
    return str(p).replace("\\", "/").strip()


def _load_lineage_yaml(p: Path) -> dict:
    # 读血缘 YAML；兼容文件头部的编码注释与三引号 docstring（历史文件格式）。
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith('"""'):
        i += 1
        while i < len(lines) and '"""' not in lines[i]:
            i += 1
        i += 1
    body = "\n".join(lines[i:])
    return yaml.safe_load(body) or {}


def build(out_db: Path = OUT_DB, out_json: Path = OUT_JSON) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(nid: str, kind: str, label: str = "", ref: str = "", **meta):
        if not nid:
            return
        cur = nodes.get(nid)
        if cur is None:
            nodes[nid] = {"id": nid, "kind": kind, "label": label or nid, "ref": ref,
                          "meta": json.dumps(meta, ensure_ascii=False)}
        else:
            if kind and cur["kind"] == "unknown":
                cur["kind"] = kind
            if label and cur["label"] == nid:
                cur["label"] = label

    def add_edge(src: str, dst: str, kind: str, op: str = "", **meta):
        if not src or not dst:
            return
        edges.append({"src": src, "dst": dst, "kind": kind, "op": op,
                      "meta": json.dumps(meta, ensure_ascii=False)})

    # 1) hypotheses
    hyp_stats = {"rows": 0, "with_evidence": 0}
    if HYP.exists():
        con = sqlite3.connect("file:" + str(HYP) + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        for r in con.execute("select * from hypotheses"):
            hyp_stats["rows"] += 1
            hid = str(r["id"])
            add_node(hid, "hypothesis", label=str(r["title"] or hid), ref=str(r["evidence_path"] or ""),
                     status=r["status"], owner=r["owner"], conclusion=str(r["conclusion"] or "")[:200])
            ev = str(r["evidence_path"] or "")
            if ev:
                hyp_stats["with_evidence"] += 1
                for seg in ev.replace(";", ",").split(","):
                    seg = seg.strip()
                    if seg and (("/" in seg) or seg.endswith(".md") or seg.endswith(".json")):
                        nid = "path:" + _norm_path(seg)
                        add_node(nid, "evidence", label=Path(seg).name, ref=_norm_path(seg))
                        add_edge(hid, nid, "evidence_path")
        con.close()

    # 2) strategy registry
    strat_stats = {"strategies": 0, "with_source_hypothesis": 0}
    if STRAT.exists():
        reg = json.loads(STRAT.read_text(encoding="utf-8"))
        for s in reg.get("strategies") or []:
            sid = str(s.get("id") or "")
            if not sid:
                continue
            strat_stats["strategies"] += 1
            add_node("strategy:" + sid, "strategy", label=str(s.get("name") or sid),
                     ref=str(reg.get("library", {}).get("registry_file") or ""),
                     status=s.get("status"), owner=s.get("owner"), stype=s.get("type"))
            src = str(s.get("source_hypothesis") or "")
            if src:
                strat_stats["with_source_hypothesis"] += 1
                hid = src if src.startswith("H") else ("H" + src)
                add_node(hid, "hypothesis", label=hid)
                add_edge(hid, "strategy:" + sid, "derived_strategy")

    # 3) lineage YAMLs
    yaml_stats = {"files": 0, "nodes": 0, "edges": 0}
    for name in LINEAGE_YAMLS:
        p = CAT / name
        if not p.exists():
            continue
        doc = _load_lineage_yaml(p)
        yaml_stats["files"] += 1
        for n in doc.get("nodes") or []:
            nid = "lineage:" + str(n.get("id") or "")
            add_node(nid, "data_layer", label=str(nid), ref=_norm_path(str(n.get("path") or "")),
                     layer=n.get("layer"), desc=str(n.get("desc") or "")[:200], lineage_file=name)
            yaml_stats["nodes"] += 1
        for e in doc.get("edges") or []:
            add_edge("lineage:" + str(e.get("from")), "lineage:" + str(e.get("to")),
                     "data_flow", op=str(e.get("op") or ""), schema=e.get("schema"), lineage_file=name)
            yaml_stats["edges"] += 1

    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    out_db.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_db.with_name(out_db.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE nodes(id TEXT PRIMARY KEY, kind TEXT, label TEXT, ref TEXT, meta TEXT);
    CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, op TEXT, meta TEXT,
                       PRIMARY KEY(src, dst, kind));
    CREATE INDEX idx_edges_src ON edges(src);
    CREATE INDEX idx_edges_dst ON edges(dst);
    """)
    con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?)",
                    [(n["id"], n["kind"], n["label"], n["ref"], n["meta"]) for n in nodes.values()])
    uniq = {}
    for e in edges:
        uniq[(e["src"], e["dst"], e["kind"])] = e
    con.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?)",
                    [(e["src"], e["dst"], e["kind"], e["op"], e["meta"]) for e in uniq.values()])
    meta = {"built_at": ts, "hypotheses": json.dumps(hyp_stats, ensure_ascii=False),
            "strategies": json.dumps(strat_stats, ensure_ascii=False),
            "lineage_yaml": json.dumps(yaml_stats, ensure_ascii=False),
            "node_count": len(nodes), "edge_count": len(uniq)}
    con.executemany("INSERT INTO meta VALUES(?,?)", list(meta.items()))
    con.commit()
    con.close()
    tmp.replace(out_db)

    summary = {"db": str(out_db), "built_at": ts, "nodes": len(nodes), "edges": len(uniq),
               "by_kind": {}, "hypotheses": hyp_stats, "strategies": strat_stats, "lineage_yaml": yaml_stats}
    kinds: dict[str, int] = {}
    for n in nodes.values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    summary["by_kind"] = kinds
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-db", default=str(OUT_DB))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    args = ap.parse_args(argv)
    summary = build(Path(args.out_db), Path(args.out_json))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
