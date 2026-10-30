# -*- coding: utf-8 -*-
"""crypto_daily 每日增量同步器（K53-C）

data/crypto/<PAIR>_USDT.parquet 续拉：binance 1d klines → tushare 风格列。
- 末日续拉：从本地最后一日的 UTC 0 点起取，严格 > 本地末日才写入
- 口径对齐原表：volume 置 None（原灌入口径仅 amount=quote 成交额）
- pre_close/pct_chg 由 close 链推算，与首行衔接
计划任务 CryptoSync 每日 07:00（binance 7×24，UTC 日线北京时间 08:00 起累积）。
"""

import json
import os
import time

import pandas as pd
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crypto")
# K-mig (10-16): 本机直连 api.binance.com 不可达（GFW），改用官方行情专用镜像
# data-api.binance.vision（与 data.binance.vision 同域可达，API 路径完全一致）
API = "https://data-api.binance.vision/api/v3/klines"


def _kl(sym, start_ms):
    url = (
        API
        + "?symbol="
        + sym
        + "&interval=1d&limit=1000"
        + "&startTime="
        + str(start_ms)
    )
    with safe_urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def sync_pair(fname):
    pair = os.path.splitext(fname)[0]  # BTC_USDT
    sym = pair.replace("_", "")  # BTCUSDT
    ts_code = pair.replace("_", "/")  # BTC/USDT
    path = os.path.join(DIR, fname)
    df = pd.read_parquet(path)
    last = str(df["trade_date"].max())  # YYYYMMDD
    # 末行重拉：最后一日常为未收盘日 K，删除后重取以覆盖更新（已收盘则同值无害）
    df = df[df["trade_date"].astype(str) < last]
    # K104：拉取锚点 = 删除后的新末日 prev（原实现锚 last 且 d<=last 丢弃，
    # 拉回的 last 完整 K 永远写不回——「覆盖更新」语义落空，每次拉到新数据
    # 即永久丢本地 last 日，实证 20261003 被删被丢）
    prev = str(df["trade_date"].max()) if len(df) else None
    if prev:
        start_ms = int(
            pd.Timestamp(
                "%s-%s-%s" % (prev[:4], prev[4:6], prev[6:8]), tz="UTC"
            ).timestamp()
            * 1000
        )
    else:
        start_ms = 0
    prev_close = (
        float(df.sort_values("trade_date")["close"].iloc[-1]) if len(df) else None
    )
    new = []
    for k in _kl(sym, start_ms):
        d = time.strftime("%Y%m%d", time.gmtime(k[0] / 1000))
        if prev and d <= prev:
            continue  # prev 已在本地；last 完整版及其后全部写回
        c = float(k[4])
        new.append(
            {
                "ts_code": ts_code,
                "trade_date": d,
                "close": c,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "volume": None,
                "amount": float(k[7]),
                "pre_close": prev_close,
                "pct_chg": (
                    round((c / prev_close - 1) * 100, 4) if prev_close else None
                ),
            }
        )
        prev_close = c
    if new:
        pd.concat([df, pd.DataFrame(new)], ignore_index=True).to_parquet(
            path, index=False
        )
    return len(new)


def _enabled():
    """同步开关（默认 0 = 停用）。

    WO-QT-20261021-094：统一供给层 zhice-db `v2_bar_daily` 已含 CRYPTO
    37 对（130 根/只，跨度 2026-06-14 起）且自有活水（每日更新到当日），
    本脚本直连 Binance 属**重复搬运同一批数据**，与 WO-085「量化所有
    Agent 一律在情报部门读数据、不得另接数据源」冲突 → 默认停用。
    数据改走 `export_us_hk.py --market CRYPTO` 从统一供给层导出。

    回滚：置环境变量 CRYPTO_SYNC_ENABLED=1 即恢复旧行为，无需改代码。
    """
    v = os.environ.get("CRYPTO_SYNC_ENABLED", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def main():
    if not _enabled():
        print(
            "CRYPTO SYNC DISABLED (WO-QT-20261021-094): "
            "数据统一走 export_us_hk.py --market CRYPTO（v2_bar_daily）；"
            "置 CRYPTO_SYNC_ENABLED=1 可回滚"
        )
        return 0
    fs = sorted(f for f in os.listdir(DIR) if f.endswith(".parquet"))
    total = 0
    for f in fs:
        for attempt in range(3):
            try:
                n = sync_pair(f)
                total += n
                print("[%s] +%d" % (f, n))
                break
            except Exception as e:
                print("[%s] retry%d %s" % (f, attempt, str(e)[:70]))
                time.sleep(3)
        time.sleep(0.4)
    print("CRYPTO SYNC DONE total +%d rows" % total)


if __name__ == "__main__":
    main()
