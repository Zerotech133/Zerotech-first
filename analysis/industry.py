# -*- coding: utf-8 -*-
"""申万行业映射数据地基（P0Q）。

数据源（tushare，权威、含 L1/L2/L3）：
  - index_classify(level=L1/L2/L3, src=SW2021)   → 行业分类树
  - index_member_all(l1_code=...)                → 各行业当前成分（is_new=Y）
缓存：`work/analysis/cache/sw/{classify,member}.parquet`（不改进 curated 数据仓）
用法：
  python -m analysis.industry --refresh    # 拉取并刷新缓存
  python -m analysis.industry --check      # 覆盖率/样例自检
"""
import argparse
import json
import os
import random
import sys

import pandas as pd

from . import config

DIR = os.path.join(config.WORK, "cache", "sw")


def _ts_call(api, params):
    sys.path.insert(0, os.path.join(config.MAIN_ROOT, "quant_backtest"))
    import tushare_sync as T
    return T.ts_call(api, params)


def _df(r):
    if isinstance(r, pd.DataFrame):
        return r
    if isinstance(r, dict) and r.get("fields"):
        return pd.DataFrame(r.get("items") or [], columns=r["fields"])
    return pd.DataFrame(r or [])


def refresh(levels=("L1", "L2", "L3")):
    os.makedirs(DIR, exist_ok=True)
    frames = []
    for level in levels:
        r = _df(_ts_call("index_classify", {"level": level, "src": "SW2021"}))
        print("[classify]", level, len(r), flush=True)
        frames.append(r)
    cls = pd.concat(frames, ignore_index=True).drop_duplicates("index_code")
    cls.to_parquet(os.path.join(DIR, "classify.parquet"), index=False)

    parts = []
    for _, row in cls[cls["level"] == "L1"].iterrows():
        try:
            m = _df(_ts_call("index_member_all", {"l1_code": row["index_code"]}))
            parts.append(m)
            print("[member]", row["industry_name"], len(m), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[member] ERR", row["industry_name"], str(e)[:80], flush=True)
    mem = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(mem):
        mem = mem[mem["is_new"].astype(str) == "Y"].drop_duplicates("ts_code")
        mem.to_parquet(os.path.join(DIR, "member.parquet"), index=False)
    print("[done] classify=%d member=%d dir=%s" % (len(cls), len(mem), DIR), flush=True)
    return {"classify": len(cls), "member": len(mem), "dir": DIR}


def load():
    cp, mp = os.path.join(DIR, "classify.parquet"), os.path.join(DIR, "member.parquet")
    cls = pd.read_parquet(cp) if os.path.exists(cp) else None
    mem = pd.read_parquet(mp) if os.path.exists(mp) else None
    return cls, mem


def map_stock(code):
    """当前申万归属（tushare 权威）；缓存缺失或未收录 → {}（调用方回退旧逻辑）。"""
    try:
        cls, mem = load()
        if mem is not None and len(mem):
            hit = mem[mem["ts_code"] == code]
            if len(hit):
                r = hit.iloc[0]
                return {"l1_code": str(r.get("l1_code") or ""), "l1_name": str(r.get("l1_name") or ""),
                        "l2_code": str(r.get("l2_code") or ""), "l2_name": str(r.get("l2_name") or ""),
                        "l3_code": str(r.get("l3_code") or ""), "l3_name": str(r.get("l3_name") or ""),
                        "match": "tushare_sw"}
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/industry.py", exc)
    return {}


def l2_peers(l2_code, code, limit=80):
    """同申万二级成分（当前）。"""
    try:
        cls, mem = load()
        if mem is None or not len(mem) or not l2_code:
            return []
        hit = mem[mem["l2_code"].astype(str) == str(l2_code)]
        return [c for c in hit["ts_code"].astype(str).tolist() if c != code][:limit]
    except Exception:  # noqa: BLE001
        return []


def check(codes=None, sample=100):
    cls, mem = load()
    if mem is None:
        return {"ok": False, "error": "member cache missing; run --refresh"}
    codes = codes or []
    if not codes:
        allc = mem["ts_code"].astype(str).tolist()
        codes = random.sample(allc, min(sample, len(allc)))
    hit = 0
    samples = []
    for c in codes:
        m = map_stock(c)
        if m.get("l1_name"):
            hit += 1
        if len(samples) < 8:
            samples.append((c, m.get("l1_name"), m.get("l2_name")))
    return {"ok": True, "coverage": round(hit / max(len(codes), 1) * 100, 1),
            "checked": len(codes), "l1_count": int(mem["l1_code"].nunique()),
            "l2_count": int(mem["l2_code"].nunique()), "total": int(len(mem)),
            "samples": samples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--codes", default="")
    a = ap.parse_args()
    if a.refresh:
        print(json.dumps(refresh(), ensure_ascii=False), flush=True)
    if a.check:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
        print(json.dumps(check(codes), ensure_ascii=False, indent=1), flush=True)
    if not (a.refresh or a.check):
        ap.print_help()


if __name__ == "__main__":
    main()
