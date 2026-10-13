# -*- coding: utf-8 -*-
"""331：监督官方 tdx_sync 实例(65096)跑完→自动验证 manifest/ts_daily→落 _331_watch.log。"""
import json
import os
import subprocess
import time

import pandas as pd

QBT = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(QBT, "_331_watch.log"), "w", encoding="utf-8")
OFFICIAL = "65096"


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV"],
                       capture_output=True)
    out = r.stdout.decode("gbk", errors="replace")
    return pid in out


def log(msg):
    print(msg, flush=True)
    LOG.write(msg + "\n")
    LOG.flush()


t0 = time.time()
while alive(OFFICIAL):
    if time.time() - t0 > 3600:  # 最多等 1h
        log("TIMEOUT waiting official instance after 3600s")
        break
    time.sleep(60)

log("official instance gone at %.0fs" % (time.time() - t0))

# 验证
m = json.load(open(os.path.join(QBT, "data", "tdx", "_manifest.json"), encoding="utf-8"))
log("manifest OK tables=%d ts_daily rows=%s done=%s"
    % (len(m), m.get("ts_daily", {}).get("rows"), m.get("ts_daily", {}).get("done")))

df = pd.read_parquet(os.path.join(QBT, "data", "tdx", "ts_daily"),
                     columns=["ts_code", "trade_date"])
d1 = str(df.loc[df.ts_code == "000001.SZ", "trade_date"].max())
log("lake ts_daily d1 = %s %s" % (d1, "PASS" if d1 > "20261012" else "STILL OLD"))

# 抽查 3 表 parquet 可读
for t in ["ts_daily", "ts_adj_factor", "ts_dividend_vip"]:
    p = os.path.join(QBT, "data", "tdx", t)
    if os.path.isdir(p):
        try:
            files = [os.path.join(p, f) for f in os.listdir(p) if f.endswith(".parquet")]
            n = sum(pd.read_parquet(f, columns=None).shape[0] if os.path.getsize(f) < 5e7
                    else -1 for f in files[-2:])
            log("spot %s: parts=%d latest2 rows=%s OK" % (t, len(files), n))
        except Exception as e:
            log("spot %s: FAIL %s" % (t, e))

log("watch done")
LOG.close()
