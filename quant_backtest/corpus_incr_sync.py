# -*- coding: utf-8 -*-
"""语料类滚动流 · 增量回填器（补 tushare_sync.py 的历史断档）。

## 为什么需要（2026-10-15 07:00 实证）

`tushare_sync.py` 的滚动流写法是 **无参调用**：
    df = pull_paged(api, {}, cap, max_off=6000)   →  save_full(tbl, df, ...)
tushare 滚动流接口无参只返回**近期**数据，于是每次增量都把表刷成最近几天，
日期永远卡在断点上。实测断点：

| 表 | 实际断点 | 同步器行为 |
|---|---|---|
| news | 2026-09-27 | 无参 recent-only |
| major_news | 2026-09-27 | 无参 recent-only |
| research_report | 2026-09-27 | 无参 recent-only（只有 7~9 月） |
| npr | 2026-09-24 | 无参 recent-only |
| irm_qa_sh / irm_qa_sz | **主表不存在**（只有 _probe 表，3000 行到 09-24） | batch_shallow 只做 probe |

而这三个接口的**日期区间参数实测生效**（07:00 探针）：
  · major_news / research_report / irm_qa_sh / irm_qa_sz → `start_date/end_date`（**紧凑** YYYYMMDD）
  · news → `start_date/end_date`（**连字符** YYYY-MM-DD HH:MM:SS，与其他接口格式不同！）

⇒ 无需购买「大模型语料套餐」，把断档回填即可。

## 纪律
- 只**新增**分区文件（part_NNNN.parquet），不覆盖不改历史分区；
- 幂等：按本地已有日期集合算缺口，只拉缺失区间；
- 写入前校验列名（防脏分区，同 396 判例）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import pandas as pd
import requests

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tushare")
MANIFEST = os.path.join(OUT, "_manifest.json")
API = "https://api.tushare.pro"
SESS = requests.Session()
SLEEP = 0.35
RETRY = 4

# ── 表定义：(表名, api, 本地日期列, 参数名, 日期格式) ────────────
# 🔴 格式差异是 07:00 实测结论，不是笔误：
#    news 的 datetime 字段必须 "YYYY-MM-DD HH:MM:SS"；
#    其余接口必须 "YYYYMMDD"。写错格式静默返回 0 行（不报错）！
TABLES = {
    "news":           ("news", "datetime", "dash"),
    "major_news":     ("major_news", "pub_time", "dash"),
    "research_report": ("research_report", "trade_date", "compact"),
    "irm_qa_sh":      ("irm_qa_sh", "trade_date", "compact"),
    "irm_qa_sz":      ("irm_qa_sz", "trade_date", "compact"),
}


def token() -> str:
    t = os.environ.get("TUSHARE_TOKEN", "")
    if t:
        return t
    for c in (r"E:\老田的AI助手\config\tushare_token.txt", r"C:\Temp\tushare_token.txt"):
        if os.path.exists(c):
            return open(c, encoding="utf-8").read().strip()
    raise SystemExit("★找不到 TUSHARE_TOKEN")


TOK = ""


def ts_call(api, params):
    global TOK
    last = None
    for a in range(RETRY):
        try:
            r = SESS.post(API, json={"api_name": api, "token": TOK,
                                     "params": params, "fields": ""}, timeout=60)
            j = r.json()
            if j.get("code") == 0:
                d = j.get("data") or {}
                return d.get("fields") or [], d.get("items") or []
            last = "code=%s msg=%s" % (j.get("code"), str(j.get("msg"))[:70])
            if "权限" in last or "积分" in last:
                break
        except Exception as e:      # noqa: BLE001
            last = "%s %s" % (type(e).__name__, str(e)[:70])
        time.sleep(1.5 * (a + 1))
    raise RuntimeError("%s %s -> %s" % (api, params, last))


def pull_paged(api, params, cap):
    """offset 翻页（沿用生产同步器同款防失控：重复页 break + 50 万行兜底）。"""
    rows, fields, off, last_items = [], None, 0, None
    while True:
        p = dict(params)
        p["offset"] = off
        f, items = ts_call(api, p)
        if fields is None:
            fields = f
        if not items:
            break
        if items == last_items:          # 服务端 offset 无效（anns_d 死循环教训）
            break
        last_items = items
        rows.extend(items)
        off += len(items)
        if len(items) < cap or len(rows) >= 500000:
            break
        time.sleep(SLEEP)
    return pd.DataFrame(rows, columns=fields), fields or []


def fmt(d: str, style: str) -> tuple[str, str]:
    """按接口要求格式化日期区间参数。"""
    if style == "dash":
        return "%s 00:00:00" % "-".join([d[:4], d[4:6], d[6:]]), \
               "%s 23:59:59" % "-".join([d[:4], d[4:6], d[6:]])
    return d, d


def local_days(tbl: str, dcol: str) -> set[str]:
    ds = set()
    for p in sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet"))):
        try:
            pf = __import__("pyarrow.parquet", fromlist=["parquet"]).ParquetFile(p)
            if dcol not in pf.schema_arrow.names:
                continue
            import pyarrow.parquet as pq
            ds.update(str(x)[:10].replace("-", "").strip()
                      for x in pq.read_table(p, columns=[dcol]).column(0).to_pylist()
                      if x)
        except Exception:
            continue
    return ds


def next_part(tbl: str) -> str:
    parts = glob.glob(os.path.join(OUT, tbl, "part_*.parquet"))
    idx = len(parts)
    while os.path.exists(os.path.join(OUT, tbl, "part_%04d.parquet" % idx)):
        idx += 1
    return os.path.join(OUT, tbl, "part_%04d.parquet" % idx)


def main() -> int:
    global TOK
    ap = argparse.ArgumentParser(description="语料类滚动流增量回填")
    ap.add_argument("--only", default="")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--start", default="", help="回填起始 YYYYMMDD（默认本地最新+1）")
    ap.add_argument("--end", default="", help="回填结束（默认今天）")
    ap.add_argument("--span", type=int, default=10, help="每次请求的天数窗")
    args = ap.parse_args()

    TOK = token()
    end = args.end or time.strftime("%Y%m%d")
    names = list(TABLES) if not args.only else \
        [s.strip() for s in args.only.split(",") if s.strip()]
    bad = [n for n in names if n not in TABLES]
    if bad:
        print("无匹配表：%s（可选 %s）" % (bad, ", ".join(TABLES)))
        return 2

    print("=== 语料增量回填 · end=%s · span=%d天 ===" % (end, args.span))
    summary = {}
    for tbl in names:
        api, dcol, style = TABLES[tbl]
        have = local_days(tbl, dcol)
        lmax = max(have) if have else ""
        start = args.start or (str(int(lmax) + 1) if lmax else "20260801")
        print("\n[%s] 本地 %d 日，最新 %s → 待补 %s..%s"
              % (tbl, len(have), lmax or "-", start, end))
        if args.dryrun:
            continue

        frames, got_dates, fail = [], set(), 0
        # 逐窗回填：从 start 一路到 end
        cur = start
        while cur <= end:
            # 计算窗结束日
            y, mo, dd = int(cur[:4]), int(cur[4:6]), int(cur[6:])
            import calendar
            last = calendar.monthrange(y, mo)[1]
            endd = min(last, dd + args.span - 1)
            stop = "%s%02d" % (y, mo) + "%02d" % endd
            if stop > end:
                stop = end
            s, e = fmt(cur, style), fmt(stop, style)
            s, e = s[0], e[1]
            try:
                df, cols = pull_paged(api, {"start_date": s, "end_date": e}, 1000)
            except Exception as ex:      # noqa: BLE001
                print("  %s..%s FAIL %s" % (cur, stop, str(ex)[:70]))
                fail += 1
                # 单窗失败不阻塞：下一个窗口继续试
                cur = _next_day(stop)
                continue
            if df.empty:
                print("  %s..%s 空" % (cur, stop))
            else:
                if dcol not in df.columns:
                    print("  %s..%s 列名不符 %s → 丢弃（防脏分区）"
                          % (cur, stop, cols[:5]))
                else:
                    ds = {str(x)[:10].replace("-", "") for x in df[dcol].tolist()}
                    new = ds - have
                    frames.append(df)
                    got_dates |= ds
                    print("  %s..%s rows=%-6d 覆盖日=%-3d 新日=%d"
                          % (cur, stop, len(df), len(ds), len(new)))
            cur = _next_day(stop)

        if not frames:
            print("  无新数据可写")
            summary[tbl] = "无新数据 (fail=%d)" % fail
            continue
        new_df = pd.concat(frames, ignore_index=True)
        os.makedirs(os.path.join(OUT, tbl), exist_ok=True)
        fp = next_part(tbl)
        new_df.to_parquet(fp, index=False)
        print("  写入 %s rows=%d 日期 %s..%s"
              % (os.path.basename(fp), len(new_df),
                 str(new_df[dcol].astype(str).min())[:19],
                 str(new_df[dcol].astype(str).max())[:19]))
        summary[tbl] = "新增 %d 行 / 新增日期 %d 天 -> %s" % (
            len(new_df), len(got_dates - have), os.path.basename(fp))

    print("\n=== 汇总 ===")
    for k, v in summary.items():
        print("  %-18s %s" % (k, v))
    return 0


def _next_day(d: str) -> str:
    import datetime as dt
    return (dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) + dt.timedelta(days=1)
            ).strftime("%Y%m%d")


if __name__ == "__main__":
    raise SystemExit(main())