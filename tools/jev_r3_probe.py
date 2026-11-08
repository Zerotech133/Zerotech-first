# -*- coding: utf-8 -*-
"""HIT-R3-20261107 · 「补标的映射」只读试点探针（不写任何 DB）。

背景
----
`dec_audit.hit IS NULL` 的 107,066 行里有 104,530 行 `companies` 为空。
R3 设想「从 title/content/jev.reason 解析公司→代码 / 行业→指数，回填 companies」。
本探针**只评估可行性与准确率**，分三层给出可复核指标，绝不回填：

* S1 结构化前缀：`ann:` 公告标题形如 ``[600095 湘财股份]``，本身已带代码+名称，
  用证券主数据校验「解析率 / 主数据命中率 / 名称一致率 / 补完标的后可定价率」。
* S2 自然语言：其余无标的行，复用 `intel_entity_extract.extract_entities` 的
  exact / weak 层与 `NameMatcher` 的 candidate 层，统计可提取性与歧义率。
* GOLD 遮蔽复原：把「已有 companies」的行当金标准，从原文重新提取，
  算 precision / recall（零主观、可复跑）。

只读保证
--------
* 两个 sqlite 均以 ``file:<path>?mode=ro`` 打开，并执行 ``PRAGMA query_only=1``；
* 模块内**不含任何** INSERT / UPDATE / DELETE / DDL / `--apply`；
* parquet 只以 `pq.read_table` 只读；
* 默认不写任何文件；仅当显式给 `--json-out` 时原子写该 JSON（证据用）。

用法
----
    python -X utf8 tools/jev_r3_probe.py --sample 300 --seed 42
    python -X utf8 tools/jev_r3_probe.py --sample 300 --json-out reports/_tmp_x/r3.json
    python -X utf8 tools/jev_r3_probe.py --no-name-map --sample 100

分层口径：S1 全量（结构化、确定性、仅 6,016 行）；S2 / GOLD 各抽 `--sample` 条
（`random.Random(seed).sample`，同 seed 可复现）。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import intel_entity_extract as iex  # noqa: E402  复用既有只读提取器
import pyarrow as pa  # noqa: E402
import pyarrow.compute as pc  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

DEFAULT_NEWS = ROOT / "news_collector" / "news.db"
DEFAULT_AUDIT = ROOT / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"
DEFAULT_NAMEMAP = ROOT / "quant_backtest" / "data" / "stock_names.json"
DEFAULT_MASTER = ROOT / "quant_backtest" / "data" / "ashare" / "_names.json"
CURATED = ROOT / "quant_backtest" / "data" / "tushare_curated"
BENCH_CODE = "000300.SH"

NO_CODES = "(companies IS NULL OR companies IN ('','[]'))"
PREFIX_RE = re.compile(r"\[\s*(\d{6})\s+([^\]]+?)\s*\]")


# ── 只读基础设施 ───────────────────────────────────────────────
def open_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + Path(path).resolve().as_posix() + "?mode=ro",
                          uri=True, timeout=20)
    con.execute("PRAGMA query_only=1")
    return con


def load_master(path: Path) -> dict:
    """ashare/_names.json: {ts_code: name} → (6位码→ts_code, 6位码→名称)。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    code2ts: dict[str, str] = {}
    code2name: dict[str, str] = {}
    for ts, name in raw.items():
        code = str(ts).strip().split(".")[0]
        if re.fullmatch(r"\d{6}", code):
            code2ts[code] = str(ts).strip().upper()
            code2name[code] = str(name).strip()
    return {"n": len(code2ts), "code2ts": code2ts, "code2name": code2name}


def guess_ts(code: str) -> str:
    """主数据未命中时的板块兜底（与 intel_entity_extract 归一口径一致）。"""
    if code.startswith(("60", "68", "90")):
        return code + ".SH"
    if code.startswith(("00", "30")):
        return code + ".SZ"
    if code.startswith(("8", "4", "92")):
        return code + ".BJ"
    return code + ".SH"


def sym_to_ts(sym: str) -> str:
    """SH600095 → 600095.SH（extract_entities 输出 → dec_audit.companies 口径）。"""
    if not sym or len(sym) < 8:
        return ""
    return sym[2:] + "." + sym[:2]


def name_consistent(master_name: str, prefix_name: str) -> bool:
    """名称一致（含简称包含关系）；ST / *ST / 更名由调用方单独计入 mismatch。"""
    if not master_name or not prefix_name:
        return False
    return master_name in prefix_name or prefix_name in master_name


def fetch_news(con: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """批量取标题/正文/JEV，900 个一批避开 SQLite 变量上限。"""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        qs = ",".join("?" * len(chunk))
        sql = ("SELECT id, source, title, content, jev, stocks FROM news"
               " WHERE id IN (%s)" % qs)
        for nid, src, title, content, jev, stocks in con.execute(sql, chunk):
            out[nid] = {"source": src, "title": title or "", "content": content or "",
                        "jev": jev or "", "stocks": stocks}
    return out


def scope_of(jev_text: str) -> str:
    try:
        o = json.loads(jev_text or "{}")
    except (ValueError, TypeError):
        return "<bad_json>"
    return str((o or {}).get("scope") or "<none>") if isinstance(o, dict) else "<bad_json>"


def sample_ids(rows: list, n: int, seed: int) -> list:
    if n <= 0 or n >= len(rows):
        return list(rows)
    return random.Random(seed).sample(rows, n)


# ── 行情（只读，与 jev_audit.load_returns 同口径） ───────────────
def quote_availability(targets: list[tuple[str, str]]) -> dict[str, object]:
    """targets: [(ts_code, trade_day)] → 有个股收益且当日有沪深300基准的集合。"""
    codes = {c for c, _ in targets}
    days = {d for _, d in targets}
    t = pq.read_table(CURATED / "daily.parquet",
                      columns=["ts_code", "trade_date", "pct_chg"])
    mask = pc.and_(pc.is_in(t["trade_date"], value_set=pa.array(sorted(days))),
                   pc.is_in(t["ts_code"], value_set=pa.array(sorted(codes))))
    sub = t.filter(mask)
    stock = {(str(c), str(d)) for c, d, p in zip(
        sub.column("ts_code").to_pylist(), sub.column("trade_date").to_pylist(),
        sub.column("pct_chg").to_pylist()) if p is not None}
    ti = pq.read_table(CURATED / "index_daily.parquet",
                       columns=["ts_code", "trade_date", "pct_chg"])
    mi = pc.and_(pc.equal(ti["ts_code"], BENCH_CODE),
                 pc.is_in(ti["trade_date"], value_set=pa.array(sorted(days))))
    si = ti.filter(mi)
    bench = {str(d) for d, p in zip(si.column("trade_date").to_pylist(),
                                    si.column("pct_chg").to_pylist()) if p is not None}
    # 注意：同一 (代码, 交易日) 可能对应多条公告，priced_rows 必须按「行」计数；
    # priced_unique_pairs 只作诊断，不能当作可转化的 hit 行数（曾误用，已修正）。
    priced_pairs = {(c, d) for c, d in targets if (c, d) in stock and d in bench}
    priced_rows = sum(1 for c, d in targets if (c, d) in priced_pairs)
    return {"targets_n": len(targets), "stock_pairs": len(stock),
            "bench_days": len(bench), "priced_rows": priced_rows,
            "priced_unique_pairs": len(priced_pairs)}


# ── 三层试点 ───────────────────────────────────────────────────
def probe_s1(news: sqlite3.Connection, audit: sqlite3.Connection,
             master: dict) -> dict:
    rows = audit.execute(
        "SELECT news_id, trade_day, bias FROM dec_audit"
        " WHERE hit IS NULL AND " + NO_CODES + " AND news_id LIKE 'ann:%'"
    ).fetchall()
    fetched = fetch_news(news, [r[0] for r in rows])
    parsed, master_hit, consistent, mismatches = 0, 0, 0, []
    targets: list[tuple[str, str]] = []
    for nid, day, bias in rows:
        m = PREFIX_RE.search(fetched.get(nid, {}).get("title", ""))
        if not m:
            continue
        parsed += 1
        code, pname = m.group(1), m.group(2)
        ts = master["code2ts"].get(code) or guess_ts(code)
        targets.append((ts, str(day)))
        mname = master["code2name"].get(code)
        if mname:
            master_hit += 1
            if name_consistent(mname, pname):
                consistent += 1
            elif len(mismatches) < 8:
                mismatches.append({"id": nid, "code": code,
                                   "prefix_name": pname, "master_name": mname})
    q = quote_availability(targets)
    return {
        "total": len(rows), "prefix_parsed": parsed,
        "master_hit": master_hit, "name_consistent": consistent,
        "name_mismatch": master_hit - consistent,
        "name_mismatch_samples": mismatches,
        "quote": {"targets_n": q["targets_n"], "priced_rows": q["priced_rows"],
                  "priced_unique_pairs": q["priced_unique_pairs"],
                  "stock_pairs": q["stock_pairs"], "bench_days": q["bench_days"]},
    }


def probe_s2(news: sqlite3.Connection, audit: sqlite3.Connection,
             sample: int, seed: int, matcher) -> dict:
    rows = audit.execute(
        "SELECT news_id, source, bias, trade_day FROM dec_audit"
        " WHERE hit IS NULL AND " + NO_CODES + " AND news_id NOT LIKE 'ann:%'"
    ).fetchall()
    picked = sample_ids(rows, sample, seed)
    fetched = fetch_news(news, [r[0] for r in picked])
    cat = collections.Counter()
    scopes = collections.Counter()
    sources = collections.Counter()
    weak_rows = 0
    cand_symbols: set[str] = set()
    samples: list[dict] = []
    for nid, source, bias, day in picked:
        rec = fetched.get(nid)
        if rec is None:
            cat["not_found_in_news"] += 1
            continue
        sources[str(source)] += 1
        scopes[scope_of(rec["jev"])] += 1
        text = rec["title"] + "\n" + rec["content"]
        ent = iex.extract_entities(text)
        if ent["weak"]:
            weak_rows += 1
        cand: list[tuple[str, str]] = []
        if matcher:
            cand, _ = matcher.find_symbols(text)
        syms = {s for _, s in cand}
        cand_symbols |= syms
        if ent["exact"]:
            kind = "exact_code"
        elif len(syms) == 1:
            kind = "name_candidate_only"
        elif len(syms) > 1:
            kind = "ambiguous_candidate"
        else:
            kind = "no_entity"
        cat[kind] += 1
        if len(samples) < 8 and kind != "no_entity":
            samples.append({"id": nid, "kind": kind,
                            "exact": ent["exact"][:3],
                            "candidates": sorted(syms)[:3],
                            "title": rec["title"][:90]})
    return {"total": len(rows), "sampled": len(picked),
            "classification": dict(cat), "scope_distribution": dict(scopes),
            "source_distribution": dict(sources.most_common(10)),
            "weak_rows": weak_rows, "distinct_candidate_symbols": len(cand_symbols),
            "samples": samples}


def probe_gold(news: sqlite3.Connection, audit: sqlite3.Connection,
               sample: int, seed: int) -> dict:
    rows = audit.execute(
        "SELECT news_id, companies FROM dec_audit"
        " WHERE companies IS NOT NULL AND companies NOT IN ('','[]')"
    ).fetchall()
    picked = sample_ids(rows, sample, seed)
    fetched = fetch_news(news, [r[0] for r in picked])
    tp = fp = fn = 0
    rows_with_exact = rows_exact_full = 0
    bad_companies = 0
    for nid, comp in picked:
        try:
            truth = {str(x) for x in json.loads(comp or "[]")}
        except (ValueError, TypeError):
            bad_companies += 1
            continue
        rec = fetched.get(nid)
        if rec is None:
            continue
        ent = iex.extract_entities(rec["title"] + "\n" + rec["content"])
        pred = {sym_to_ts(s) for s in ent["exact"]}
        pred.discard("")
        tp += len(pred & truth)
        fp += len(pred - truth)
        fn += len(truth - pred)
        if pred:
            rows_with_exact += 1
        if truth and truth <= pred:
            rows_exact_full += 1
    return {"total": len(rows), "sampled": len(picked),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "rows_with_exact": rows_with_exact,
            "rows_exact_full_match": rows_exact_full,
            "bad_companies_rows": bad_companies}


# ── CLI ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HIT-R3 只读试点探针（绝不写 DB）")
    p.add_argument("--sample", type=int, default=300, help="S2 / GOLD 每组抽样条数")
    p.add_argument("--seed", type=int, default=42, help="抽样随机种子")
    p.add_argument("--news-db", default=str(DEFAULT_NEWS))
    p.add_argument("--audit-db", default=str(DEFAULT_AUDIT))
    p.add_argument("--name-map", default=str(DEFAULT_NAMEMAP))
    p.add_argument("--master", default=str(DEFAULT_MASTER))
    p.add_argument("--no-name-map", action="store_true", help="跳过名称候选层")
    p.add_argument("--json-out", help="显式指定时才写该 JSON 文件")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    master = load_master(Path(args.master))
    matcher = None
    name_meta: dict = {"enabled": False}
    if not args.no_name_map:
        nm = iex.load_name_map(Path(args.name_map))
        matcher = iex.NameMatcher(nm["name_to_symbol"])
        name_meta = {"enabled": True, "path": nm["path"], "entries": nm["entries"],
                     "raw_entries": nm["raw_entries"],
                     "skipped_unsupported": nm["skipped_unsupported"]}
    news, audit = open_readonly(Path(args.news_db)), open_readonly(Path(args.audit_db))
    try:
        s1 = probe_s1(news, audit, master)
        s2 = probe_s2(news, audit, args.sample, args.seed, matcher)
        gold = probe_gold(news, audit, args.sample, args.seed)
        total = audit.execute("SELECT COUNT(*) FROM dec_audit").fetchone()[0]
        hit_null = audit.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE hit IS NULL").fetchone()[0]
    finally:
        news.close()
        audit.close()
    priced = s1["quote"]["priced_rows"]
    out = {
        "probe": "HIT-R3-20261107", "readonly": True, "wrote_db_rows": 0,
        "meta": {"sample": args.sample, "seed": args.seed,
                 "news_db": str(args.news_db), "audit_db": str(args.audit_db),
                 "master_n": master["n"], "name_map": name_meta,
                 "bench_code": BENCH_CODE,
                 "s1_mode": "full scan", "s2_gold_mode": "random sample"},
        "baseline": {"dec_audit_total": total, "hit_null": hit_null,
                     "hit_null_pct": round(100.0 * hit_null / total, 4) if total else None},
        "s1_structured_prefix": s1, "s2_free_text": s2, "gold_masked_replay": gold,
        "projection": {
            "note": "仅按 fill_posterior 定价条件（个股+基准均有收益）估算 S1 可转化的 hit 上限；未写库",
            "s1_priced_max": priced,
            "hit_null_if_s1_filled": hit_null - priced,
            "hit_null_pct_if_s1_filled": round(100.0 * (hit_null - priced) / total, 4) if total else None,
        },
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.json_out:
        dest = Path(args.json_out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
        print("json_out:", dest)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
