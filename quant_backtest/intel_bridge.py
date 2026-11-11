# -*- coding: utf-8 -*-
"""三源证据桥：图谱(马钧) + OSINT 七维采集(马良) + 因子(蒋琬)。

定位：只读聚合层。不改生产脚本、不写库、不动清单。
给一组股票代码，输出「单票证据卡」——每个源各自 hit/miss，未命中写 miss 和原因，
绝不把「没有数据」美化成「中性」或 0。

三源口径（全部调用生产既有实现，不复制逻辑）：

1) 图谱（马钧 / 构建席 KGMAP1）
   portal/api_intel_graph.build_view() → dossier(code)
   边类型：co_occurrence（ts_top_inst 末 2 分区席位共现）/ holding（top10_holders tail 300）
   规模护栏是生产既有设定，此处不改。

2) OSINT 七维（马良 / OSINT1），对应岗位定义「七个源头」：
   D1 互动易        data/irm/<code>.jsonl
   D2 上证 e 互动    data/sse/<code>.jsonl
   D3 淘股吧        data/taoguba/<code>.jsonl
   D4 雪球定向      news_collector/data/xq_timeline/<code>.jsonl
   D5 千股千评      news_collector/data/qianping/qianping_<basis>.jsonl（全市场日快照）
   D6 新闻流        news_collector/news.db → news.stocks JSON 数组
   D7 研报          tushare/research_report + tushare/report_rc

   注意：D1~D4 是稀疏采集队列，D5~D7 是全市场覆盖。
   任何「覆盖率」结论必须七维一起算，只算前四维会把覆盖率低估一半以上。

3) 因子（蒋琬 / FACTOR1）
   tools/research/factor_lib → Panel → FACTORS[name].compute()
   基准日截面全市场分位（0~100），带 direction 标注。

用法：
  python intel_bridge.py --codes 000538.SZ,000429.SZ --basis 20261110
  python intel_bridge.py --from-picks ../portal/data/todays_picks.json --basis 20261110 --out ../portal/data/evidence_cards.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUANT = REPO / "quant_backtest"
PORTAL = REPO / "portal"
RESEARCH = REPO / "tools" / "research"

FACTOR_NAMES = ("vol20", "mom20", "max_ret20", "rev5")

JSONL_SOURCES = (
    ("D1_irm", REPO / "data" / "irm"),
    ("D2_sse", REPO / "data" / "sse"),
    ("D3_taoguba", REPO / "data" / "taoguba"),
    ("D4_xq", REPO / "news_collector" / "data" / "xq_timeline"),
)
QIANPING_DIR = REPO / "news_collector" / "data" / "qianping"
NEWS_DB = REPO / "news_collector" / "news.db"
TUSHARE = QUANT / "data" / "tushare"

_cache: dict = {}


# ---------------------------------------------------------------- 图谱
def _graph_view():
    if "graph" in _cache:
        return _cache["graph"]
    for p in (str(PORTAL), str(QUANT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from api_intel_graph import build_view

        _cache["graph"] = build_view()
        _cache["graph_err"] = None
    except Exception as exc:
        _cache["graph"] = None
        _cache["graph_err"] = f"{type(exc).__name__}: {exc}"
    return _cache["graph"]


def graph_part(code: str) -> dict:
    view = _graph_view()
    if view is None:
        return {"coverage": "miss", "reason": "graph_unavailable",
                "detail": _cache.get("graph_err")}
    d = view.dossier(code)
    n = int(d.get("total_edges") or 0)
    edges = d.get("edges") or []
    types: dict[str, int] = {}
    for e in edges:
        k = e.get("rel_type", "?")
        types[k] = types.get(k, 0) + 1
    return {
        "coverage": "hit" if n else "miss",
        "reason": None if n else "no_edge_in_view",
        "total_edges": n,
        "shown": int(d.get("shown") or 0),
        "edge_types": types,
        "top_edges": [
            {"rel": e.get("rel_type"), "left": e.get("left"), "right": e.get("right"),
             "evidence": e.get("evidence_key")}
            for e in edges[:3]
        ],
    }


# ---------------------------------------------------------------- 七维采集
def _cutoff(basis: str, days: int) -> str:
    try:
        b = _dt.date(int(basis[:4]), int(basis[4:6]), int(basis[6:8]))
    except Exception:
        return ""
    return (b - _dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _norm_date(v: str) -> str:
    s = str(v)[:10].replace("/", "-")
    if len(s) >= 8 and s[:4].isdigit() and "-" not in s:
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _jsonl_part(code: str, cutoff: str) -> dict:
    six = code.split(".")[0]
    by: dict[str, dict] = {}
    total = recent = 0
    hits = 0
    latest = ""
    for tag, root in JSONL_SOURCES:
        fp = root / f"{six}.jsonl"
        if not fp.exists():
            by[tag] = {"rows": 0, "latest": None}
            continue
        rows = 0
        lat = ""
        rec = 0
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows += 1
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    inner = o.get("data") if isinstance(o.get("data"), dict) else {}
                    for k in ("date", "publish_time", "create_time", "trade_date", "time"):
                        v = o.get(k) or inner.get(k)
                        if not v:
                            continue
                        s = _norm_date(v)
                        if len(s) == 10 and s[:4].isdigit():
                            if s > lat:
                                lat = s
                            if cutoff and s >= cutoff:
                                rec += 1
                        break
        except Exception:
            by[tag] = {"rows": 0, "latest": None, "error": "read_failed"}
            continue
        by[tag] = {"rows": rows, "latest": lat or None, "recent": rec}
        total += rows
        recent += rec
        if rows:
            hits += 1
        if lat > latest:
            latest = lat
    return {"hits": hits, "rows": total, "recent": recent, "latest": latest, "by": by}


def _qianping_part(code: str, basis: str) -> dict:
    """D5 千股千评：全市场日快照，一只票一行。"""
    if "qp" not in _cache:
        fp = QIANPING_DIR / f"qianping_{basis}.jsonl"
        idx: dict[str, dict] = {}
        meta = {"file": fp.name, "exists": fp.exists(), "rows": 0}
        if fp.exists():
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    c = str(o.get("code") or o.get("ts_code") or "").split(".")[0]
                    if c:
                        idx[c] = o
                    meta["rows"] += 1
        _cache["qp"] = idx
        _cache["qp_meta"] = meta
    six = code.split(".")[0]
    row = _cache["qp"].get(six)
    meta = _cache["qp_meta"]
    if not row:
        return {"coverage": "miss", "reason": "not_in_snapshot", "snapshot": meta["file"],
                "snapshot_rows": meta["rows"]}
    keep = {k: row.get(k) for k in
            ("name", "px", "chg_pct", "score", "rank", "rank_up", "focus_idx",
             "inst_part", "main_cost", "dist_maincost", "turnover_pct", "pe_ttm")
            if row.get(k) is not None}
    return {"coverage": "hit", "reason": None, "snapshot": meta["file"],
            "snapshot_rows": meta["rows"], "fields": keep}


def _news_part(code: str, cutoff: str) -> dict:
    """D6 新闻流：news.stocks 是 JSON 数组，用 LIKE 精确匹配带后缀代码。"""
    if "news" not in _cache:
        try:
            con = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
            _cache["news"] = con
        except Exception as exc:
            _cache["news"] = None
            _cache["news_err"] = f"{type(exc).__name__}: {exc}"
    con = _cache["news"]
    if con is None:
        return {"coverage": "miss", "reason": "db_unavailable",
                "detail": _cache.get("news_err")}
    try:
        rows = con.execute(
            "select count(*), max(ts), min(ts) from news where stocks like ?",
            (f'%"{code}"%',),
        ).fetchone()
        rec = con.execute(
            "select count(*) from news where stocks like ? and ts >= ?",
            (f'%"{code}"%', cutoff),
        ).fetchone()[0]
    except Exception as exc:
        return {"coverage": "miss", "reason": "query_failed",
                "detail": f"{type(exc).__name__}: {exc}"}
    n = int(rows[0] or 0)
    return {"coverage": "hit" if n else "miss",
            "reason": None if n else "no_tagged_news",
            "rows": n, "recent": int(rec or 0),
            "latest": (str(rows[1])[:10] if rows[1] else None)}


def _research_part(code: str, cutoff: str) -> dict:
    """D7 研报：research_report（日更）+ report_rc（按月分片，取近 3 个月）。"""
    import pandas as pd

    if "rr" not in _cache:
        frames = []
        for p in sorted((TUSHARE / "research_report").glob("part_*.parquet")):
            try:
                frames.append(pd.read_parquet(p, columns=["ts_code", "trade_date"]))
            except Exception:
                continue
        _cache["rr"] = (pd.concat(frames, ignore_index=True) if frames
                        else pd.DataFrame(columns=["ts_code", "trade_date"]))
    if "rc" not in _cache:
        files = sorted((TUSHARE / "report_rc").glob("rc_*.parquet"))[-3:]
        frames = []
        for p in files:
            try:
                frames.append(pd.read_parquet(p, columns=["ts_code", "report_date"]))
            except Exception:
                continue
        _cache["rc"] = (pd.concat(frames, ignore_index=True) if frames
                        else pd.DataFrame(columns=["ts_code", "report_date"]))
        _cache["rc_files"] = [p.name for p in files]

    rr = _cache["rr"]
    rc = _cache["rc"]
    rr_hit = rr[rr["ts_code"] == code] if len(rr) else rr
    rc_hit = rc[rc["ts_code"] == code] if len(rc) else rc
    n_rr = int(len(rr_hit))
    n_rc = int(len(rc_hit))
    latest = ""
    if n_rr:
        latest = max(latest, str(rr_hit["trade_date"].max())[:10])
    if n_rc:
        latest = max(latest, str(rc_hit["report_date"].max())[:10])
    recent = 0
    if n_rr and cutoff:
        recent += int(sum(str(x)[:10] >= cutoff for x in rr_hit["trade_date"]))
    if n_rc and cutoff:
        recent += int(sum(str(x)[:10] >= cutoff for x in rc_hit["report_date"]))
    return {
        "coverage": "hit" if (n_rr or n_rc) else "miss",
        "reason": None if (n_rr or n_rc) else "no_report",
        "research_report_rows": n_rr,
        "report_rc_rows": n_rc,
        "recent": recent,
        "latest": latest or None,
        "rc_window_files": _cache.get("rc_files"),
    }


def osint_part(code: str, basis: str, recent_days: int = 30) -> dict:
    cutoff = _cutoff(basis, recent_days)
    j = _jsonl_part(code, cutoff)
    qp = _qianping_part(code, basis)
    nw = _news_part(code, cutoff)
    rs = _research_part(code, cutoff)

    dims = {
        "D1_irm": j["by"].get("D1_irm", {}),
        "D2_sse": j["by"].get("D2_sse", {}),
        "D3_taoguba": j["by"].get("D3_taoguba", {}),
        "D4_xq": j["by"].get("D4_xq", {}),
        "D5_qianping": {"coverage": qp["coverage"], "fields": qp.get("fields")},
        "D6_news": {"rows": nw.get("rows", 0), "latest": nw.get("latest"),
                    "coverage": nw["coverage"]},
        "D7_research": {"rows": rs.get("research_report_rows", 0) + rs.get("report_rc_rows", 0),
                        "latest": rs.get("latest"), "coverage": rs["coverage"]},
    }
    n_hit = sum(1 for v in dims.values() if v.get("coverage", "hit" if v.get("rows") else "miss") == "hit")
    dims_hit = [k for k, v in dims.items()
                if v.get("coverage", "hit" if v.get("rows") else "miss") == "hit"]
    # 只累计「与这只票相关」的行：D5 快照是全市场文件，命中时本票只贡献 1 行
    total_rows = (j["rows"]
                  + (1 if qp["coverage"] == "hit" else 0)
                  + (nw.get("rows", 0) if nw["coverage"] == "hit" else 0)
                  + rs.get("research_report_rows", 0) + rs.get("report_rc_rows", 0))
    return {
        "coverage": "hit" if n_hit else "miss",
        "reason": None if n_hit else "not_in_any_queue",
        "dims_hit": f"{n_hit}/7",
        "dims_hit_list": dims_hit,
        "total_rows": total_rows,
        "recent_30d": j["recent"] + nw.get("recent", 0) + rs.get("recent", 0),
        "latest": max([x for x in (j["latest"], nw.get("latest"), rs.get("latest")) if x] or [""]) or None,
        "recent_cutoff": cutoff,
        "dims": dims,
    }


# ---------------------------------------------------------------- 因子
def _factor_panel(basis: str):
    if "panel" in _cache:
        return _cache["panel"]
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    import factor_lib

    start = f"{int(basis[:4]) - 1}{basis[4:]}"
    _cache["panel"] = factor_lib.load_panel(start, basis, horizon=5, universe="all", load_roe=False)
    _cache["factor_lib"] = factor_lib
    return _cache["panel"]


def factor_part(codes: list[str], basis: str) -> dict:
    try:
        panel = _factor_panel(basis)
        fl = _cache["factor_lib"]
    except Exception as exc:
        return {"coverage": "miss", "reason": "panel_unavailable",
                "detail": f"{type(exc).__name__}: {exc}"}

    dates = [d for d in panel.dates if d <= basis]
    if not dates:
        return {"coverage": "miss", "reason": "basis_out_of_panel"}
    day = dates[-1]

    per_code: dict[str, dict] = {c: {} for c in codes}
    meta: dict[str, dict] = {}
    for name in FACTOR_NAMES:
        spec = fl.FACTORS.get(name)
        if spec is None:
            continue
        try:
            mat = spec.compute(panel)
        except Exception as exc:
            meta[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if day not in mat.index:
            meta[name] = {"error": "date_missing"}
            continue
        row = mat.loc[day]
        valid = row.dropna()
        if valid.empty:
            continue
        pct = valid.rank(pct=True) * 100.0
        meta[name] = {"direction": spec.direction, "family": spec.family,
                      "n_valid": int(valid.size)}
        for c in codes:
            if c in pct.index and pct.loc[c] == pct.loc[c]:
                per_code[c][name] = round(float(pct.loc[c]), 1)

    hit = sum(1 for c in codes if per_code.get(c))
    return {"coverage": "hit" if hit else "miss",
            "reason": None if hit else "no_factor_value",
            "basis_used": day, "factor_meta": meta, "per_code": per_code}


# ---------------------------------------------------------------- 组装
def cards(codes: list[str], basis: str) -> dict:
    codes = [c.strip() for c in codes if c and c.strip()]
    t0 = time.time()
    _graph_view()
    f = factor_part(codes, basis)

    out = []
    stat = {"full": 0, "partial": 0, "factor_only": 0, "none": 0}
    dim_stat: dict[str, int] = {}
    for c in codes:
        gp = graph_part(c)
        op = osint_part(c, basis)
        fv = f.get("per_code", {}).get(c) or {}
        fp = {"coverage": "hit" if fv else "miss",
              "reason": None if fv else "no_factor_value",
              "values_pct": fv}
        for k, v in op["dims"].items():
            if v.get("coverage", "hit" if v.get("rows") else "miss") == "hit":
                dim_stat[k] = dim_stat.get(k, 0) + 1
        n_hit = sum(1 for x in (gp, op, fp) if x["coverage"] == "hit")
        verdict = ("full" if n_hit == 3 else "partial" if n_hit == 2
                   else "factor_only" if n_hit == 1 and fp["coverage"] == "hit" else "none")
        stat[verdict] += 1
        out.append({"code": c, "verdict": verdict, "graph": gp,
                    "osint": op, "factor": fp})

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "basis": basis,
        "n_codes": len(codes),
        "stat": stat,
        "osint_dim_stat": dim_stat,
        "factor_basis": f.get("basis_used"),
        "factor_meta": f.get("factor_meta"),
        "cards": out,
        "elapsed_sec": round(time.time() - t0, 1),
    }


def _codes_from_picks(path: str, tiers: tuple[str, ...] = ("T2",)) -> list[str]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return [str(s.get("code")) for s in d.get("stocks", []) if s.get("tier") in tiers]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--from-picks", default="")
    ap.add_argument("--tiers", default="T2")
    ap.add_argument("--basis", required=True, help="YYYYMMDD")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if a.codes:
        codes = [c for c in a.codes.split(",") if c.strip()]
    elif a.from_picks:
        codes = _codes_from_picks(a.from_picks, tuple(a.tiers.split(",")))
    else:
        print("需要 --codes 或 --from-picks", file=sys.stderr)
        return 2

    res = cards(codes, a.basis)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"written {a.out}")
    print(json.dumps({"n": res["n_codes"], "stat": res["stat"],
                      "osint_dim_stat": res["osint_dim_stat"],
                      "elapsed_sec": res["elapsed_sec"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
