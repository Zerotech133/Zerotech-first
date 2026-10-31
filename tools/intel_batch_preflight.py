# -*- coding: utf-8 -*-
"""人工复核批次入库前质量闸（WO-INTEL-COMPANIES-AUDIT / P0）。

输入 `intel_review_batch.py` 生成的 JSONL（或 CSV），只读检查：
  · companies 格式/未知码/名称张冠李戴（硬错误）
  · 批次 id/标题/hash 重复（硬错误）
  · weak_link（文中无公司名证据，警告）
  · style_risk（网评/研讨/报告/例会/大会/趋势/软文 + 无具体数据，警告）

用法：
    python tools/intel_batch_preflight.py --batch batch.jsonl --json
    python tools/intel_batch_preflight.py --batch batch.jsonl --strict
退出码：0=无硬错误；1=有硬错误；2=输入错误。
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "jev_companies_audit.py"
DEFAULT_MASTER = ROOT / "quant_backtest" / "data" / "ashare" / "_names.json"
STYLE_WORDS = ("网评", "评论", "研讨会", "论坛", "报告发布", "报告", "例会",
               "产销形势", "大会", "开幕", "趋势", "软文", "专访", "观察")
CONCRETE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿元|万元|亿|万|%|个百分点|吨|GW|GWh|台|单|订单|合同)|"
    r"中标|签约|订单|合同|业绩|营收|净利|分红|回购|增持|减持|处罚|立案|亏损|预亏")
BULL_WORDS = ("利多", "利好", "预期升温", "上涨", "改善", "超预期", "增长",
              "受益", "看好", "回升")
BEAR_WORDS = ("利空", "利淡", "下跌", "下滑", "承压", "亏损", "预亏", "走弱",
              "回落", "风险升温")
CLUSTER_WORDS = ("胡塞", "也门", "沙特", "伊朗", "以色列", "中东", "红海",
                 "地缘", "油价", "原油")
CLUSTER_GROUPS = {"geopolitics": CLUSTER_WORDS}


def _reason_bias_contradiction(bias, reason):
    """返回矛盾关键词；bias 与 reason 方向相反即命中。"""
    if not reason:
        return []
    if bias == "bear":
        return [w for w in BULL_WORDS if w in reason]
    if bias == "bull":
        return [w for w in BEAR_WORDS if w in reason]
    return []


def _audit_mod():
    spec = importlib.util.spec_from_file_location("_jev_companies_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_records(path):
    p = Path(path)
    if not p.is_file():
        raise RuntimeError("batch 不存在：%s" % p)
    if p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise RuntimeError("坏 JSON 行：%s" % exc)
        if isinstance(obj, dict):
            rows.append(obj)
    if not rows:
        raise RuntimeError("batch 为空")
    return rows


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def check(records, master):
    """→ report dict；不写任何文件。"""
    mod = _audit_mod()
    name_by_code, name_to_codes, master_meta = mod._load_master(master)
    hard, weak, style = [], [], []
    anchor_missing, cluster_hits = [], collections.defaultdict(list)
    seen_ids, titles, hashes = set(), collections.defaultdict(list), collections.defaultdict(list)
    for i, r in enumerate(records, 1):
        rid = str(r.get("id") or r.get("news_id") or "").strip()
        title = str(r.get("title") or "").strip()
        jev = r.get("jev")
        if not isinstance(jev, dict):
            try:
                jev = json.loads(r.get("original_jev") or "{}")
            except (TypeError, ValueError):
                jev = {}
        reason = str(jev.get("reason") or r.get("reason") or "")
        comps = r.get("companies")
        if not isinstance(comps, list):
            comps = jev.get("companies") if isinstance(jev.get("companies"), list) else []
        bias = str(r.get("bias") or jev.get("bias") or "")
        contra = _reason_bias_contradiction(bias, reason)
        if contra:
            hard.append({"row": i, "id": rid, "error": "reason_bias_contradiction",
                         "bias": bias, "words": contra})
        anchor = r.get("anchor") or jev.get("anchor")
        if bias in ("bull", "bear") and not isinstance(anchor, dict) and not comps:
            anchor_missing.append({"row": i, "id": rid, "bias": bias,
                                   "error": "asset_anchor_missing"})
        for group, words in CLUSTER_GROUPS.items():
            if any(w in title for w in words):
                cluster_hits[group].append(rid)
        # 重复 id/title/hash
        if not rid:
            hard.append({"row": i, "error": "id_empty"})
        elif rid in seen_ids:
            hard.append({"row": i, "id": rid, "error": "duplicate_id"})
        else:
            seen_ids.add(rid)
        titles[title].append(rid)
        hashes[_sha(title + "|" + reason)].append(rid)
        if not isinstance(comps, list):
            hard.append({"row": i, "id": rid, "error": "companies_not_list"})
            comps = []
        seen_codes = set()
        joined = title + "\n" + reason
        for item in comps:
            code = str(item).strip() if item is not None else ""
            if not re.match(r"^\d{6}$", code):
                hard.append({"row": i, "id": rid, "error": "company_code_format",
                             "value": item})
                continue
            if code in seen_codes:
                hard.append({"row": i, "id": rid, "error": "company_code_duplicate",
                             "code": code})
                continue
            seen_codes.add(code)
            names = name_by_code.get(code) or []
            if not names:
                hard.append({"row": i, "id": rid, "error": "company_code_unknown",
                             "code": code})
                continue
            if any(n in joined for n in names):
                continue
            mentioned = [(n, cs) for n, cs in name_to_codes.items()
                         if len(n) >= 2 and n in joined]
            if mentioned:
                mname, mcodes = max(mentioned, key=lambda x: len(x[0]))
                if rid.startswith("report:") or "研报" in title:
                    weak.append({"row": i, "id": rid, "code": code,
                                 "name": names[0], "error": "weak_link_name_not_in_text"})
                    continue
                if mname.endswith("证券") and not names[0].endswith("证券"):
                    weak.append({"row": i, "id": rid, "code": code,
                                 "name": names[0], "error": "weak_link_name_not_in_text"})
                    continue
                if code not in mcodes:
                    hard.append({"row": i, "id": rid, "error": "company_name_mismatch",
                                 "code": code, "master_name": names[0],
                                 "mentioned_name": mname, "mentioned_code": mcodes[0]})
                    continue
            weak.append({"row": i, "id": rid, "code": code, "name": names[0],
                         "error": "weak_link_name_not_in_text"})
        if bias in ("bull", "bear") and any(w in title for w in STYLE_WORDS):
            if not CONCRETE_RE.search(joined):
                style.append({"row": i, "id": rid, "bias": bias,
                              "title": title[:80], "error": "style_risk_no_concrete"})
    duplicate_titles = [{"title": t, "ids": ids} for t, ids in titles.items()
                        if len(ids) > 1]
    duplicate_hashes = [{"hash": h, "ids": ids} for h, ids in hashes.items()
                        if len(ids) > 1]
    event_clusters = [{"cluster": w, "n": len(ids), "ids": ids[:20]}
                      for w, ids in cluster_hits.items() if len(ids) >= 3]
    report = {
        "schema": "intel_batch_preflight/v1",
        "master": master_meta, "records": len(records),
        "hard_errors": hard, "weak_links": weak, "style_risks": style,
        "anchor_missing": anchor_missing, "event_clusters": event_clusters,
        "duplicate_titles": duplicate_titles, "duplicate_hashes": duplicate_hashes,
        "counts": {"hard": len(hard), "weak": len(weak), "style": len(style),
                   "anchor_missing": len(anchor_missing),
                   "event_clusters": len(event_clusters),
                   "duplicate_titles": len(duplicate_titles),
                   "duplicate_hashes": len(duplicate_hashes)},
    }
    report["ok"] = not hard and not duplicate_titles and not duplicate_hashes
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="批次入库前 companies/去重质量闸")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    ap.add_argument("--strict", action="store_true", help="weak/style 也算失败")
    ap.add_argument("--strict-anchor", action="store_true",
                    help="bull/bear 无资产锚也算失败")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        records = load_records(args.batch)
        rep = check(records, args.master)
    except (RuntimeError, OSError) as exc:
        print("[ERR] %s" % exc, file=sys.stderr)
        return 2
    failed = (not rep["ok"]
              or (args.strict and (rep["weak_links"] or rep["style_risks"]))
              or (args.strict_anchor and rep["anchor_missing"]))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("batch preflight: records=%d hard=%d weak=%d style=%d dup_title=%d dup_hash=%d"
              % (rep["records"], rep["counts"]["hard"], rep["counts"]["weak"],
                 rep["counts"]["style"], rep["counts"]["duplicate_titles"],
                 rep["counts"]["duplicate_hashes"]))
        for e in rep["hard_errors"][:20]:
            print("  [HARD] %s" % e)
        for e in rep["weak_links"][:10]:
            print("  [WEAK] %s" % e)
        for e in rep["style_risks"][:10]:
            print("  [STYLE] %s" % e)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
