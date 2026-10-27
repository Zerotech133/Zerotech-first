# -*- coding: utf-8 -*-
"""321 最小探针：只探 ts_rt_min 关键日期，禁代理，短超时快失败。"""

import time

import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
S = requests.Session()
S.trust_env = False

for day in ("20260901", "20260902", "20260915", "20261012"):
    for i in range(2):
        try:
            r = S.get(
                BASE + "/api/data/table/ts_rt_min",
                params={"start_date": day, "end_date": day, "limit": 2},
                timeout=25,
            )
            if r.status_code == 200:
                d = r.json()
                rows = d.get("rows") or []
                print(
                    day,
                    "total=",
                    d.get("total"),
                    "| t=",
                    rows[0].get("time") if rows else "-",
                )
                break
            print(day, "http", r.status_code)
        except Exception as e:
            print(day, "retry", i, str(e)[:70])
        time.sleep(1)
