# -*- coding: utf-8 -*-
"""JEV companies 质量门（WO-INTEL-COMPANIES-20261031）。

只读审计 news.db.jev_review_queue / JEV JSON，不开写、不改队列：
  1. companies 必须是 list[str]，每个代码 ^\\d{6}$；禁止逗号串/空值/重复；
  2. 代码必须在本地证券主数据（默认 quant_backtest/data/ashare/_names.json）；
  3. 代码↔名称一致性：主数据名称必须出现在 title 或 jev.reason，否则 weak_link；
  4. 同标题/同 JEV hash 重复（pending/resolved 均报）；
  5. 全批 conf/impact 零方差 → unreliable；
  6. 文体风险：网评/研讨/报告发布/例会/趋势综述/软文 + bull/bear 且无具体数据 → style_risk。

用法：
    python tools/jev_companies_audit.py --db news.db --json
    python tools/jev_companies_audit.py --db news.db --batch batch.jsonl --strict
退出码：0=无硬错误；1=有硬错误（格式/未知码/名称不符/重复）；2=输入错误。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "news_collector" / "news.db"
DEFAULT_MASTER = ROOT / "quant_backtest" / "data" / "ashare" / "_names.json"
CODE_RE = re.compile(r"^\d{6}$")
STYLE_WORDS = ("网评", "评论", "研讨会", "论坛", "报告发布", "报告", "例会",
               "产销形势", "大会", "开幕", "趋势", "软文", "专访", "观察")
CONCRETE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿元|万元|亿|万|%|个百分点|吨|GW|GWh|台|单|订单|合同)|"
    r"中标|签约|订单|合同|业绩|营收|净利|分红|回购|增持|减持|处罚|立案|亏损|预亏")


def _load_master(path):
    p = Path(path)
    if not p.is_file():
        return {}, {}, {"path": str(p), "exists": False, "n": 0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("主数据读取失败：%s" % exc)
    # 支持 {"code2name": {...}} 或直接 {ts_code: name}
    data = raw.get("code2name") if isinstance(raw, dict) and "code2name" in raw else raw
    if not isinstance(data, dict):
        raise RuntimeError("主数据格式不支持：%s" % p)
    out = collections.defaultdict(list)
    name_to_codes = collections.defaultdict(set)
    for k, v in data.items():
        ts = str(k).strip().upper()
        code = ts.split(".")[0]
        name = str(v).strip()
        if CODE_RE.match(code) and name:
            out[code].append(name)
            name_to_codes[name].add(code)
    return (dict(out), {n: sorted(cs) for n, cs in name_to_codes.items()},
            {"path": str(p), "exists": True, "n": len(out)})


def _hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _companies_of(row, jev):
    if isinstance(row.get("companies"), list):
        return row["companies"]
    if isinstance(jev, dict) and isinstance(jev.get("companies"), list):
        return jev["companies"]
    return []


def _normal_code(item):
    return str(item).strip() if item is not None else ""


def audit_db(db=None, master=None, batch=None, status=None):
    dbf = Path(db or DEFAULT_DB)
    if not dbf.is_file():
        raise RuntimeError("news.db 不存在：%s" % dbf)
    name_by_code, name_to_codes, master_meta = _load_master(master or DEFAULT_MASTER)
    con = sqlite3.connect("file:" + dbf.resolve().as_posix() + "?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    sql = ("SELECT id,title,stocks,original_jev,status,bias,conf,review_type "
           "FROM jev_review_queue")
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    rows = list(con.execute(sql, args))
    con.close()
    if batch:
        bp = Path(batch)
        if not bp.is_file():
            raise RuntimeError("batch 不存在：%s" % bp)
        btext = bp.read_text(encoding="utf-8")
        brows = [json.loads(x) for x in btext.splitlines() if x.strip()]
        # batch 审计时只覆盖 batch 中 id（若存在 db 记录则合并）
        ids = {str(r.get("id")) for r in brows if r.get("id")}
        rows = [r for r in rows if r["id"] in ids]
    hard, weak, style = [], [], []
    titles = collections.defaultdict(list)
    hashes = collections.defaultdict(list)
    confs, impacts = collections.Counter(), collections.Counter()
    for r in rows:
        raw = r["original_jev"]
        try:
            jev = json.loads(raw or "{}")
        except (TypeError, ValueError):
            jev = {}
            hard.append({"id": r["id"], "error": "jev_bad_json"})
        title = str(r["title"] or "")
        reason = str(jev.get("reason") or "")
        comps = _companies_of({"companies": None}, jev)
        # batch 行可能把 companies 放在顶层；DB 行没有
        try:
            nid = r["id"]
        except (TypeError, KeyError):
            nid = ""
        titles[title.strip()].append(nid)
        hashes[_hash((title + "|" + reason).strip())].append(nid)
        confs[r["conf"]] += 1
        impacts[jev.get("impact")] += 1
        if not isinstance(comps, list):
            hard.append({"id": nid, "error": "companies_not_list"})
            continue
        seen = set()
        for item in comps:
            code = _normal_code(item)
            if not CODE_RE.match(code):
                hard.append({"id": nid, "error": "company_code_format",
                             "value": item})
                continue
            if code in seen:
                hard.append({"id": nid, "error": "company_code_duplicate",
                             "code": code})
                continue
            seen.add(code)
            names = name_by_code.get(code) or []
            if not names:
                hard.append({"id": nid, "error": "company_code_unknown",
                             "code": code, "master": master_meta["exists"]})
                continue
            # 名称一致：名称出现在 title/reason 才强链；
            # 文中出现另一家主数据公司名 → name_mismatch 硬错误。
            joined = title + "\n" + reason
            matched = [n for n in names if n and n in joined]
            if not matched:
                mentioned = [(n, cs) for n, cs in name_to_codes.items()
                             if len(n) >= 2 and n in joined]
                if mentioned:
                    mname, mcodes = max(mentioned, key=lambda x: len(x[0]))
                    # 个股研报标题里的券商名（东吴/国金/太平洋等）是发布机构，
                    # 不是目标公司；研报只保留 weak/hard 未知码，不做名称反查。
                    if nid.startswith("report:") or "研报" in title:
                        weak.append({"id": nid, "code": code, "name": names[0],
                                     "error": "weak_link_name_not_in_text"})
                        continue
                    # 非研报但名称是券商机构时也跳过
                    if mname.endswith("证券") and not names[0].endswith("证券"):
                        weak.append({"id": nid, "code": code, "name": names[0],
                                     "error": "weak_link_name_not_in_text"})
                        continue
                    if code not in mcodes:
                        hard.append({"id": nid, "error": "company_name_mismatch",
                                     "code": code, "master_name": names[0],
                                     "mentioned_name": mname,
                                     "mentioned_code": mcodes[0]})
                        continue
                weak.append({"id": nid, "code": code, "name": names[0],
                             "error": "weak_link_name_not_in_text"})
        bias = str(r["bias"] or jev.get("bias") or "")
        if bias in ("bull", "bear") and any(w in title for w in STYLE_WORDS):
            if not CONCRETE_RE.search(title + "\n" + reason):
                style.append({"id": nid, "bias": bias, "title": title[:80],
                              "error": "style_risk_no_concrete"})
    duplicate_titles = [{"title": t, "ids": ids} for t, ids in titles.items()
                        if len(ids) > 1]
    duplicate_hashes = [{"hash": h, "ids": ids} for h, ids in hashes.items()
                        if len(ids) > 1]
    zero_variance = {
        "conf_distinct": len(confs), "impact_distinct": len(impacts),
        "unreliable": len(confs) <= 1 and len(impacts) <= 1 and len(rows) > 1,
    }
    report = {
        "schema": "jev_companies_audit/v1",
        "db": str(dbf), "batch": str(batch) if batch else None,
        "status": status or "all",
        "master": master_meta, "total": len(rows),
        "hard_errors": hard, "weak_links": weak, "style_risks": style,
        "duplicate_titles": duplicate_titles,
        "duplicate_hashes": duplicate_hashes,
        "zero_variance": zero_variance,
        "counts": {"hard": len(hard), "weak": len(weak), "style": len(style),
                   "duplicate_titles": len(duplicate_titles),
                   "duplicate_hashes": len(duplicate_hashes)},
    }
    report["ok"] = not hard
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV companies 质量门（只读）")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    ap.add_argument("--batch", default="")
    ap.add_argument("--status", default="", help="pending/resolved/unreviewed_legacy")
    ap.add_argument("--strict", action="store_true", help="weak/style 也算失败")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        rep = audit_db(args.db, args.master, args.batch or None,
                       args.status or None)
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        print("[ERR] %s" % exc, file=sys.stderr)
        return 2
    failed = not rep["ok"] or (args.strict and (rep["weak_links"] or rep["style_risks"]))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("companies quality: total=%d hard=%d weak=%d style=%d dup_title=%d dup_hash=%d"
              % (rep["total"], rep["counts"]["hard"], rep["counts"]["weak"],
                 rep["counts"]["style"], rep["counts"]["duplicate_titles"],
                 rep["counts"]["duplicate_hashes"]))
        for e in rep["hard_errors"][:20]:
            print("  [HARD] %s" % e)
        for e in rep["weak_links"][:10]:
            print("  [WEAK] %s" % e)
        for e in rep["style_risks"][:10]:
            print("  [STYLE] %s" % e)
        if rep["zero_variance"]["unreliable"]:
            print("  [WARN] conf/impact 零方差，标记 unreliable")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
