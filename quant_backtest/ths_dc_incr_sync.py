# -*- coding: utf-8 -*-
"""同花顺/东财概念系列 · 增量续拉器（补 tdx_sync.py 的覆盖缺口）。

背景（2026-10-15 05:45 实证）：
  · `tdx_sync.py` 表清单里**完全没有** ths_*/dc_*/tdx_* 系列
    （grep ths_daily|dc_daily|tdx_index 全部 0 命中）；
  · 结果 `ts_ths_daily` 停在 **20260929**（最后分区 10-01 17:30 一次性拉完），
    而同源的 `ts_dc_daily` 到 20261013 —— **ths 断流 10 个交易日**。
  · 对 387 C轴：ths 概念日线是「映射层→验证层」的中间环节（概念指数 vs 个股超额），
    断流 ⇒ C 轴 P1.5 三层联动看板算不出最近 10 日。

本脚本：只从本地已有代码清单续拉缺口日期，**追加式写新分区**，不改历史分区。
纪律：只写 ts_ths_daily / ts_dc_daily / ts_ths_hot / ts_dc_hot 四个表的增量分区。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx")
BASE = "http://rookie.xtcloud-tech.com:48088"
SESS = requests.Session()
RETRY = 3
SLEEP = 0.6  # ⚠️ 2026-10-15 判例：0.2s 会触发远端 403 限流；
#   单日单页已验证可通，故节流到 0.6s + 403 冷却 20s。
PAGE = 5000
LIMIT_COOLDOWN = 20.0

# 表 → (API path, 日期列名)
# ⚠️ 2026-10-15 06:10 判例（P0 级，差点写出 205000 行脏分区）：
#   该API **不认 `trade_date` 过滤参数** —— 传 `trade_date=20260930` 会静默返回
#   最新日（20261014）的数据，HTTP 仍是 200，看起来「成功」。
#   正确过滤参数是 **`start_date` / `end_date`（区间）**；响应元数据自带
#   `date_col` / `total` / `total_exact` 可自证过滤是否生效。
#   ⇒ 下方拉取一律走区间参数，且每页校验返回日期落在请求区间内（越界即报错），
#     杜绝「过滤失效 → 全表翻页 → 重复行」这类静默脏数据。
TARGETS = {
    "ts_ths_daily": ("/api/data/table/ts_ths_daily", "trade_date"),
    "ts_dc_daily": ("/api/data/table/ts_dc_daily", "trade_date"),
}

# 单区间总行数安全阀（ths 缺口 7 天≈1.2 万行；留足余量但防失控翻页）
MAX_RANGE_ROWS = 400000


def _get(path, params=None, timeout=60):
    last = None
    for i in range(RETRY):
        try:
            r = SESS.get(BASE + path, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 403:
                # 频控：长冷却后重试（判例：短重试只会继续吃 403）
                last = "HTTP 403 (频控)"
                time.sleep(LIMIT_COOLDOWN * (i + 1))
                continue
            last = "HTTP %s %s" % (r.status_code, r.text[:80])
        except Exception as e:  # noqa: BLE001
            last = "%s %s" % (type(e).__name__, str(e)[:80])
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("GET %s failed: %s" % (path, last))


def local_days(tbl: str, date_col: str = "trade_date") -> set[str]:
    import glob
    import pyarrow.parquet as pq

    ds = set()
    for p in sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet"))):
        try:
            pf = pq.ParquetFile(p)
            if date_col not in pf.schema_arrow.names:
                continue
            ds.update(
                str(x)
                for x in pq.read_table(p, columns=[date_col]).column(0).to_pylist()
                if x is not None
            )
        except Exception:
            continue
    return ds


def manifest_path(tbl: str) -> str:
    return os.path.join(OUT, tbl, "_manifest.json")


def load_mf(p: str) -> dict:
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_mf(p: str, m: dict) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def next_part(tbl: str) -> str:
    import glob

    parts = sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    idx = 0
    if parts:
        tail = os.path.basename(parts[-1]).split("_")[1].split(".")[0]
        idx = int(tail) + 1
    return os.path.join(OUT, tbl, "part_%04d.parquet" % idx)


def _chunks(days: list[str], span: int = 3):
    """把交易日列表按span 个一组切成连续区间（连续日合并成一次区间查询）。"""
    for i in range(0, len(days), span):
        part = days[i : i + span]
        yield part[0], part[-1]


def trading_days_since(start: str, end: str) -> list[str]:
    """用 trade_cal（本地湖）取 start..end 之间的交易日。"""
    import glob
    import pyarrow.parquet as pq

    cal_parts = sorted(
        glob.glob(os.path.join(HERE, "data", "tushare", "trade_cal", "part_*.parquet"))
    )
    if not cal_parts:
        return []
    cols = None
    frames = []
    for p in cal_parts:
        pf = pq.ParquetFile(p)
        if cols is None:
            cols = pf.schema_arrow.names
        need = [c for c in ("cal_date", "is_open") if c in cols]
        if len(need) == 2:
            frames.append(pq.read_table(p, columns=need).to_pandas())
    if not frames:
        return []
    import pandas as pd

    df = pd.concat(frames, ignore_index=True)
    df["cal_date"] = df["cal_date"].astype(str)
    sel = df[(df["is_open"] == 1) & (df["cal_date"] >= start) & (df["cal_date"] <= end)]
    return sorted(sel["cal_date"].unique().tolist())


def main() -> int:
    ap = argparse.ArgumentParser(description="ths/dc 概念系列增量续拉")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument(
        "--start", default="", help="起始交易日 YYYYMMDD（默认自动取本地最新+1）"
    )
    ap.add_argument("--end", default="", help="结束交易日（默认今天）")
    ap.add_argument("--only", default="", help="只拉某表（逗号分隔）")
    args = ap.parse_args()

    tbls = list(TARGETS)
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        tbls = [t for t in tbls if t in want]
        if not tbls:
            print("可选：%s" % ", ".join(TARGETS))
            return 2

    end = args.end or time.strftime("%Y%m%d")
    print("=== ths/dc 增量续拉 · end=%s ===" % end)

    import pandas as pd

    for tbl in tbls:
        path, dcol = TARGETS[tbl]
        have = local_days(tbl, dcol)
        local_max = max(have) if have else ""
        start = args.start or (str(int(local_max) + 1) if local_max else "20260901")
        print(
            "\n[%s] 本地 %d 交易日，最新 %s → 待补 %s..%s"
            % (tbl, len(have), local_max or "-", start, end)
        )
        days = trading_days_since(start, end)
        missing = [d for d in days if d not in have]
        print(
            "  区间交易日 %d 个，真缺 %d 个 %s"
            % (len(days), len(missing), missing[:12])
        )
        if not missing:
            print("  无缺口，跳过")
            continue
        if args.dryrun:
            continue

        frames = []
        # ⚠️ 一律用区间参数拉整个缺口区间（而非逐日trade_date）：
        #   逐日 + 被忽略的过滤 = 每「天」都翻一遍全表 = 行数爆炸。
        for chunk_start, chunk_end in _chunks(missing, span=3):
            got = 0
            offset = 0
            chunk_frames = []
            while True:
                params = {
                    "start_date": chunk_start,
                    "end_date": chunk_end,
                    "limit": PAGE,
                    "offset": offset,
                }
                try:
                    j = _get(path, params)
                except Exception as e:  # noqa: BLE001
                    print(
                        "  %s..%s offset=%d FAIL %s"
                        % (chunk_start, chunk_end, offset, str(e)[:70])
                    )
                    break
                cols = j.get("columns") or []
                rows = j.get("rows") or []
                if not rows:
                    break
                # 过滤生效自证：返回日期必须落在请求区间内，否则过滤参数被忽略
                bad = [
                    r
                    for r in rows
                    if not (chunk_start <= str(r[cols.index(dcol)]) <= chunk_end)
                ]
                if bad:
                    raise RuntimeError(
                        "过滤失效：请求 %s..%s 返回越界日期 %s（API 已变更过滤参数名）"
                        % (
                            chunk_start,
                            chunk_end,
                            sorted({str(r[cols.index(dcol)]) for r in bad})[:5],
                        )
                    )
                # 该 API 返回 rows 为**数组**（配 columns 元组），不是对象数组
                chunk_frames.append(pd.DataFrame(rows, columns=cols))
                got += len(rows)
                if len(rows) < PAGE:
                    break
                offset += PAGE
                if offset > MAX_RANGE_ROWS:  # 安全阀
                    raise RuntimeError(
                        "区间 %s..%s 超过安全阀 %d 行，疑似过滤失效"
                        % (chunk_start, chunk_end, MAX_RANGE_ROWS)
                    )
                time.sleep(SLEEP)
            if chunk_frames:
                frames.extend(chunk_frames)
                print("  %s..%s ok rows=%d" % (chunk_start, chunk_end, got))
            else:
                print("  %s..%s 空" % (chunk_start, chunk_end))

        if not frames:
            print("  无新数据可写")
            continue
        new = pd.concat(frames, ignore_index=True)
        # 只保留真缺口日期（区间内可能夹带源端已有的非交易日/已入库日）
        new = new[new[dcol].astype(str).isin(missing)]
        if new.empty:
            print("  区间内无缺口日期数据，跳过")
            continue
        # 全键去重（ts_code + trade_date）—— 脏数据绝不落盘
        key = [c for c in ("ts_code", dcol) if c in new.columns]
        before = len(new)
        if len(key) == 2:
            new = new.drop_duplicates(subset=key)
        if len(new) != before:
            print("  ⚠️ 去重 %d 行（%d→%d）" % (before - len(new), before, len(new)))
        got_days = sorted(new[dcol].astype(str).unique().tolist())
        miss_after = [d for d in missing if d not in got_days]
        if miss_after:
            print("  ⚠️ 源端未覆盖的缺口日（不写）：%s" % miss_after)
        os.makedirs(os.path.join(OUT, tbl), exist_ok=True)
        op = next_part(tbl)
        new.to_parquet(op, index=False)
        rng = "%s..%s" % (
            str(new[dcol].astype(str).min()),
            str(new[dcol].astype(str).max()),
        )
        print(
            "  写入 %s rows=%d 期间 %s 覆盖 %d/%d 天"
            % (os.path.basename(op), len(new), rng, len(got_days), len(missing))
        )
        mp = manifest_path(tbl)
        mf = load_mf(mp)
        mf["last_incr"] = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "part": os.path.basename(op),
            "rows": int(len(new)),
            "range": "%s..%s" % (start, end),
        }
        save_mf(mp, mf)

    print("\n完成 %s" % time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
