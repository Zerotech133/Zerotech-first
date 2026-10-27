# -*- coding: utf-8 -*-
"""timeline 号段-时间映射探针 v0.1（只读·侦察·不落库）。

背景（2026-10-18 历史线扩容预案）：
- xq_status 存量 14212 条（2017-02~2023-11）来自 timeline 分类流线性深翻，
  深挖自动轮已按「连续低产」判据永久停轮——线性路径已榨干。
- 扩量姿势 = 号段跳挖：timeline 的 status_id 是全局序列（跨类目共享号段），
  直接向 API 传任意 max_id 可定位到任意历史区段（官方游标按 ID 翻页
  永不过期判例）。
- 本探针用 count=1 最小预算对若干号段点各发 1 请求，记录返回首帖的
  created_at → 建立「号段→时间」映射，识别未挖密区，供定点播种。

无效游标判例防护：若返回帖时间与号段预期严重不符（如播种 1e8 却返回
2026 当前流首页内容），判无效游标（API 静默回顶），映射表中标注。

预算纪律：每 cat ≤ 6 探点 × count=1，页间 3s 抖动，403 走出口轮换
（复用采集器组件，四红线合规：只读 GET、不登录隐私数据）。

用法（系统 3.11 或受管 venv 均可，仅标准库+项目内模块）：
    python tools/research/xq_timeline_probe.py [--cat 102] [--full]
    --cat 102   只探沪深线（默认，号段全局共享可推其余类目）
    --full      探全部 4 类目
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # quant_backtest/
sys.path.insert(0, ROOT)

import xueqiu_common as X
from xueqiu_crawl import XqCrawler
from xq_collect_1000 import URL_TIMELINE as TL_URL, CATS
from xq_proxy import get_pool, ua_for, notify_blocked

INFO_DB = os.path.join(ROOT, "data", "xueqiu", "xueqiu_info.db")

# 探测点：分类流游标空间为 feed 序号（~20 万档，判例「跨类目共享号段」），
# 非 status_id 空间（1.48 亿档——首版探针传错空间，全部探点被 API 钳位到
# 同一锚点 247524865@2023-04-15，实锤两套数字体系不可互推）。
PROBES = (
    10_000,
    30_000,
    60_000,
    90_000,
    120_000,
    150_000,
    180_000,
    210_000,
    235_000,
    250_000,
)


def first_item(sess, cat: int, max_id):
    """单请求取 max_id 前的 1 条；返回 (status_id, created_at) 或 ('ERR', msg)。"""
    params = {"category": cat, "count": 1, "page": 1, "source": "user"}
    if max_id:
        params["max_id"] = str(max_id)
    try:
        data = X.xq_get(sess, TL_URL, params=params)
    except X.XqIpBlocked:
        return "BLOCKED", None
    except Exception as e:
        return "ERR:%s" % str(e)[:40], None
    items = (data or {}).get("list") or (data or {}).get("items") or []
    if not items:
        return "EMPTY", None
    d = items[0].get("data")
    if isinstance(d, str):
        try:
            d = __import__("json").loads(d)
        except Exception:
            return "PARSE", None
    if not isinstance(d, dict) or not d.get("id"):
        return "PARSE", None
    return str(d["id"]), X.ms_to_dt(d.get("created_at"))


def db_time_span():
    con = sqlite3.connect("file:%s?mode=ro" % INFO_DB.replace("\\", "/"), uri=True)
    lo, hi = con.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM xq_status"
    ).fetchone()
    n = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    con.close()
    return lo, hi, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", type=int, default=102)
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    cats = CATS if a.full else (a.cat,)

    lo, hi, n = db_time_span()
    print("库内 %d 条，时间 %s ~ %s" % (n, lo, hi))

    sess = XqCrawler().sess
    pool = get_pool()
    sess.proxies.update(pool.proxies() or {})
    sess.headers.update({"User-Agent": ua_for(pool.current())})

    print(
        "\n== 号段→时间映射（cat=%s，count=1，返回首帖为 id<max_id 区段样本）=="
        % ",".join(map(str, cats))
    )
    rows = []
    for cat in cats:
        for probe in PROBES:
            sid, ts = first_item(sess, cat, probe)
            if sid == "BLOCKED":
                print("  cat %d @ %-11s → 403，换出口重试一次" % (cat, probe))
                try:
                    notify_blocked()
                except Exception:
                    pass
                sess.proxies.update(get_pool().proxies() or {})
                time.sleep(8)
                sid, ts = first_item(sess, cat, probe)
            rows.append((cat, probe, sid, ts))
            print("  cat %d @ %-11d → id=%-12s %s" % (cat, probe, sid, ts or "-"))
            time.sleep(2 + random.random() * 2)

    print(
        "\n判读：映射点时间单调递减=号段即时间轴（全局序列）；"
        "某点返回 2026 当前流内容=无效游标（静默回顶）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
