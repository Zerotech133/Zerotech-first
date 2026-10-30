# -*- coding: utf-8 -*-
"""批量导出美股/港股/加密 K 线到本地 parquet（zhice-db → data/us|hk|crypto/*.parquet）。

设计原则（对齐项目纪律）：
· 增量导出：已有 parquet 且数据未过期（最新 trade_date == 数据库最新）→ 跳过；
· 批量查询：load_bars 每次最多 200 只，分批循环；
· 字段对齐 A 股：ts_code/trade_date/close/open/high/low/volume/amount/pre_close/pct_chg；
· 文件名：US 用 ticker（AAPL.parquet），HK 用 5 位数字（00700.parquet），
  CRYPTO 斜杠转下划线（BTC_USDT.parquet）；
· 零修改数据库，纯只读导出。

运行：cd quant_backtest && python export_us_hk.py [--market US|HK|CRYPTO] [--batch 200] [--force]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data_loader as dl  # noqa: E402


def _align_schema(df, symbol, market):
    """对齐 A 股 parquet 字段：ts_code/trade_date/close/open/high/low/
    volume/amount/pre_close/pct_chg。"""
    if market == "HK":
        ts_code = symbol.zfill(5)  # 00001 → 00700
    else:
        ts_code = symbol  # AAPL / BTC/USDT（保持斜杠格式）
    out = pd.DataFrame(
        {
            "ts_code": ts_code,
            "trade_date": df.index.strftime("%Y%m%d"),
            "close": df["close"],
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "volume": df["volume"],
            # 不依赖 DataFrame.get（pandas 3.x 才有的方法，2.x 会 AttributeError）
            "amount": df["turnover"] if "turnover" in df.columns else df["volume"],
            "pre_close": df["pre_close"],
        }
    )
    # WO-QT-20261021-104 审计修复：pre_close 为 0 时 (close/pre_close-1) 得 inf，
    # 会把 inf 写进 parquet 污染下游（均值/排序/归一会全盘失真）。CN 实测已有
    # 609 行 pre_close=0。统一把 0 与非数值置 NaN —— NaN 表示「未知涨跌幅」，
    # 不伪造数值（不造数红线）。pre_close 本身缺失（US 实测 2935 只标的全空）
    # 同样得 NaN，属如实反映源数据缺口，需情报侧补。
    pc = pd.to_numeric(out["pre_close"], errors="coerce")
    pc = pc.where(pc != 0)
    out["pct_chg"] = (out["close"] / pc - 1).round(4) * 100
    return out


def _fname(out_dir, symbol):
    return out_dir / ("%s.parquet" % symbol.replace("/", "_").replace(".", "_"))


def _local_meta(fn):
    """读本地 parquet 的 (行数, 首日8, 末日8)；不存在/读失败 → (0, '', '')。

    WO-QT-20261021-092：原增量只判 old_max<db_max 便从 old_max+1 续拉，
    **从不看 old_min**——首导时库里只有近端数据的标的，后续库补灌更早
    历史也永远补不回本地，头部空洞永不复原（实测 45% 美股标的缺一半
    历史）。故导出前必须读本地起点。"""
    if not fn.exists():
        return 0, "", ""
    try:
        d = pd.read_parquet(fn, columns=["trade_date"])
    except Exception:
        return 0, "", ""
    if d.empty:
        return 0, "", ""
    lo = str(d["trade_date"].min()).replace("-", "")[:8]
    hi = str(d["trade_date"].max()).replace("-", "")[:8]
    return len(d), lo, hi


def _topup_symbol(market, symbol, fn, db_max):
    """已有 parquet 但落后于库 → 增量拉缺口并与旧文件合并覆盖写。"""
    old = pd.read_parquet(fn)
    old_max = str(old["trade_date"].max()).replace("-", "")[:8]
    if old_max >= db_max:
        return "skip"
    frames = dl.load_bars(
        market,
        [symbol],
        start=(pd.to_datetime(old_max) + pd.Timedelta(days=1)).strftime("%Y%m%d"),
    )
    df = frames.get(symbol)
    if df is None or df.empty:
        return "skip"  # 库也无新增（罕见：db_max 全市场口径）
    out = _align_schema(df, symbol, market)
    merged = (
        pd.concat([old, out], ignore_index=True)
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
    )
    merged.to_parquet(fn, index=False)
    return "upd"


def _topup_batch(market, out_dir, topup):
    """批量增量（2026-10-30 P82）：一次 DB 查询拉本批全部缺口，再逐只合并写盘。

    原实现逐只 `_topup_symbol` → 每只一次 DB 往返（含 docker 回退 ~0.5-1s），
    3416 只时可 >600s（R1 18:30 任务因此卡死）。本函数把 DB 往返降为 每批 1 次。
    """
    import pandas as pd

    syms = [s for s, _ in topup]
    mins = [pd.to_datetime(m) for _, m in topup]
    start = (min(mins) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    try:
        frames = dl.load_bars(market, syms, start=start)
    except Exception as e:  # noqa: BLE001
        print("  topup batch 失败(%d 只): %s" % (len(syms), str(e)[:100]))
        return 0, 0, len(syms)
    upd = skip = fail = 0
    for s, m in topup:
        try:
            df = frames.get(s)
            if df is None or df.empty:
                skip += 1
                continue
            out = _align_schema(df, s, market)
            out = out[out["trade_date"] > m]
            if out.empty:
                skip += 1
                continue
            fn = _fname(out_dir, s)
            old = pd.read_parquet(fn)
            merged = (pd.concat([old, out], ignore_index=True)
                      .drop_duplicates(subset=["trade_date"], keep="last")
                      .sort_values("trade_date"))
            merged.to_parquet(fn, index=False)
            upd += 1
        except Exception as e:  # noqa: BLE001
            print("  %s 批量增量失败: %s" % (s, str(e)[:80]))
            fail += 1
    return upd, skip, fail


def export_market(market, batch=200, force=False):
    """导出指定市场全部标的到 data/{market}/（目录不存在自动创建）。"""
    out_dir = Path(HERE) / "data" / market.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = dl.available_symbols(market)
    print("%s: %d 只标的待导出" % (market, len(symbols)))
    ok, skip, fail, upd, kept = 0, 0, 0, 0, 0
    t0 = time.time()
    n_redo = 0
    for i in range(0, len(symbols), batch):
        batch_syms = symbols[i : i + batch]
        # 增量比对（WO-QT-20261021-092）：**两端**比对库内起止——
        #   库内起点更早 → 头部空洞 → 整只重导（redo）
        #   库内终点更新 → 尾部缺口 → 追加增量（topup）
        #   两端齐 → skip
        todo, topup, redo = [], [], {}
        if force:
            todo = batch_syms
        else:
            fresh = [s for s in batch_syms if not _fname(out_dir, s).exists()]
            todo = list(fresh)
            have = [s for s in batch_syms if s not in todo]
            if have:
                try:
                    rng = dl.symbol_ranges(market, have)
                except Exception as e:
                    print("  symbol_ranges 失败(本批按过期处理): %s" % str(e)[:80])
                    rng = {}
                for s in have:
                    rg = rng.get(s)
                    if not rg:
                        skip += 1  # 库无数据，无从增量
                        continue
                    db_min, db_max = rg
                    n_old, o_min, o_max = _local_meta(_fname(out_dir, s))
                    if n_old and db_min < o_min:
                        redo[s] = n_old  # 头部空洞：整只重导
                    elif db_max > o_max:
                        topup.append((s, db_max))
                    else:
                        skip += 1
        if topup:
            b_upd, b_skip, b_fail = _topup_batch(market, out_dir, topup)
            upd += b_upd
            skip += b_skip
            fail += b_fail
        if not todo and not redo:
            continue
        load_syms = list(todo) + list(redo)
        try:
            frames = dl.load_bars(market, load_syms)
        except Exception as e:
            print("  batch %d-%d 失败: %s" % (i, i + len(load_syms), e))
            fail += len(load_syms)
            continue
        for s, df in frames.items():
            if df is None or df.empty:
                fail += 1
                continue
            try:
                out = _align_schema(df, s, market)
                # 保护：重导反而变少（库内残片/回档）→ 保留旧文件不覆盖
                old_n = redo.get(s)
                if old_n is not None and len(out) < old_n:
                    print(
                        "  %s 重导行数反而减少 %d<%d，保留旧文件" % (s, len(out), old_n)
                    )
                    kept += 1
                    continue
                out.to_parquet(_fname(out_dir, s), index=False)
                ok += 1
                if s in redo:
                    n_redo += 1
            except Exception as e:
                print("  %s 写盘失败: %s" % (s, e))
                fail += 1
        if (i // batch) % 10 == 0:
            print(
                "  进度 %d/%d，new=%d redo=%d upd=%d skip=%d fail=%d kept=%d"
                % (i, len(symbols), ok - n_redo, n_redo, upd, skip, fail, kept)
            )
    print(
        "%s 完成: new=%d redo=%d upd=%d skip=%d fail=%d kept=%d 耗时 %.1fs"
        % (market, ok - n_redo, n_redo, upd, skip, fail, kept, time.time() - t0)
    )
    return {
        "market": market,
        "new": ok - n_redo,
        "redo": n_redo,
        "upd": upd,
        "skip": skip,
        "fail": fail,
        "kept": kept,
    }


def write_active(market, window_days=10):
    """写活跃标的清单 data/{mkt}/_active.json（近 N 自然日有新K线）。
    供 chanlun_scan 裁剪 universe（US 5920 只里 ~5374 只是历史僵尸，
    全量白扫 ~40s；清单后只扫活跃 ~500 只）。清单缺失时 scan 回退全量。"""
    out_dir = Path(HERE) / "data" / market.lower()
    try:
        symbols = dl.available_symbols(market)
        fresh = dl.latest_dates(market, symbols)
        cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y%m%d")
        active = sorted(s for s, d in fresh.items() if d >= cutoff)
        meta = {
            "market": market,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cutoff": cutoff,
            "window_days": window_days,
            "active": active,
        }
        tmp = out_dir / "_active.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out_dir / "_active.json")  # 原子替换
        print(
            "%s active list: %d/%d (cutoff %s)"
            % (market, len(active), len(symbols), cutoff)
        )
        return len(active)
    except Exception as e:
        print("%s write_active skip: %s" % (market, e))
        return -1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["US", "HK", "CRYPTO"])
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    # 2026-10-29 并发导出治理：同一市场同刻只允许一个导出进程，
    # 避免 write_active 的 `_active.json.tmp` 原子替换在 WinError 32 下被跳过。
    tools_dir = str(Path(HERE).parent / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import file_lock  # noqa: PLC0415

    out_dir = Path(HERE) / "data" / args.market.lower()
    lock = out_dir / ".export.lock"
    timeout = float(os.environ.get("EXPORT_LOCK_TIMEOUT", "600"))
    tok = file_lock.acquire(lock, "export_us_hk:%s" % args.market, timeout=timeout)
    if not tok:
        print("[export-lock] busy: %s（另一导出进程持锁，等待 %.0fs 超时）"
              % (file_lock.held(lock), timeout))
        sys.exit(2)
    try:
        export_market(args.market, args.batch, args.force)
        write_active(args.market)
    finally:
        file_lock.release(lock, tok)
