# -*- coding: utf-8 -*-
"""ts 长尾 10 表增量参数探针（一次性，2026-11-05）。

目的：确定每张表"按什么参数能拉到最新数据"，为 ts_tail_sync.py 的 recipe 定参。
只拉 2-3 行，不动任何落盘。
"""
import io
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = ""
env = os.path.join(HERE, ".env.local")
for ln in open(env, encoding="utf-8"):
    if ln.startswith("XT_DATA_KEY="):
        TOKEN = ln.split("=", 1)[1].strip()

API = "https://api.tushare.pro"
S = requests.Session()
S.trust_env = False  # 探针直连：实测 tushare 直连 1.1s / 代理 12.3s


def call(api, params, limit=3):
    body = {"api_name": api, "token": TOKEN, "params": params, "fields": ""}
    try:
        r = S.post(API, json=body, timeout=40)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:120])}
    if d.get("code") != 0:
        return {"ok": False, "err": "%s %s" % (d.get("code"), str(d.get("msg"))[:160])}
    data = d.get("data") or {}
    fields = data.get("fields") or []
    items = (data.get("items") or [])[:limit]
    return {"ok": True, "n": len(data.get("items") or []), "fields": fields[:14],
            "sample": items}


CASES = [
    ("irm_qa_sh", {"trade_date": "20261104"}),
    ("irm_qa_sh", {"ts_code": "600519.SH"}),
    ("irm_qa_sz", {"trade_date": "20261104"}),
    ("irm_qa_sz", {"ts_code": "000001.SZ"}),
    ("express", {"period": "20260930"}),
    ("express", {"ann_date": "20261104"}),
    ("hk_basic", {}),
    ("hk_daily", {"trade_date": "20261104"}),
    ("hk_daily", {"ts_code": "00700.HK"}),
    ("hk_hold", {"trade_date": "20261104"}),
    ("moneyflow_hsgt", {"trade_date": "20261104"}),
    ("moneyflow_hsgt", {}),
    ("top_inst", {"trade_date": "20261104"}),
    ("forecast", {"period": "20260930"}),
    ("forecast", {"ann_date": "20261104"}),
    ("index_weight", {"index_code": "000906.SH", "start_date": "20261001",
                      "end_date": "20261104"}),
    ("index_weight", {"index_code": "000906.SH", "trade_date": "20261031"}),
]

out = []
for api, params in CASES:
    r = call(api, params)
    r["api"] = api
    r["params"] = params
    out.append(r)
    tag = "OK n=%s" % r.get("n") if r.get("ok") else "FAIL %s" % r.get("err")
    print("%-16s %-52s %s" % (api, json.dumps(params, ensure_ascii=False), tag))

with io.open(os.path.join(HERE, "..", "reports", "data_hub", "ts_tail_probe_20261105.json"),
             "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("written reports/data_hub/ts_tail_probe_20261105.json")
