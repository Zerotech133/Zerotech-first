# -*- coding: utf-8 -*-
"""321 勘查：远端 ts_rt_min 最新日期与 14:45 快照可用性验证。"""

import time

import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
S = requests.Session()
S.trust_env = False  # 本会话 shell 带 HTTP_PROXY=127.0.0.1:7890，必须禁（tdx_sync 计划任务环境无此代理）


def get(path, **params):
    for i in range(3):
        try:
            r = S.get(BASE + path, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (i + 1))
        except Exception as e:
            print("retry", i, str(e)[:60])
    return None


# 1) 远端表状态
st = get("/api/data/status")
if st:
    for t in st.get("tables") or []:
        if isinstance(t, dict) and t.get("name") == "ts_rt_min":
            print("remote ts_rt_min status:", t)
            break
    else:
        print("status keys:", list(st)[:10] if isinstance(st, dict) else type(st))

# 2) 探 9 月之后是否有数据：按天翻 09-02 / 09-15 / 10-13
for day in ("20260902", "20260915", "20261012", "20261013"):
    d = get("/api/data/table/ts_rt_min", start_date=day, end_date=day, limit=3)
    if d is None:
        print(day, "FAIL")
        continue
    rows = d.get("rows") or []
    print(
        day,
        "rows(sampled)=",
        len(rows),
        "total=",
        d.get("total"),
        "| first time=",
        rows[0].get("time") if rows else "-",
    )
    time.sleep(0.5)
