# -*- coding: utf-8 -*-
"""上证指数日线落盘（大盘环境/抗跌领涨指标的数据底座）。

用途：
  为框架的大盘环境判断与"抗跌领涨"类指标提供统一的指数日线底座。
  落盘 data/index_daily/sh000001.parquet（独立目录，不混个股 ashare），
  列：trade_date('YYYYMMDD')/open/close/high/low/vol/amount。
  独立小工具，不挂定时任务、不推送。

数据源（按可用性）：
  1. 本地 tdx 仓 data/tdx/ts_index_daily（000001.SH，tushare schema，无网络）
  2. tushare pro.index_daily 兜底/增量（token 读 D:\\青萍之末2\\.env 的
     TUSHARE_TOKEN，默认拉近 400 交易日，与本地按 trade_date 去重合并）

用法：
  "C:\\Program Files\\Python311\\python.exe" -X utf8 index_daily.py            # tdx 优先，缺失则自动兜底拉取
  "C:\\Program Files\\Python311\\python.exe" -X utf8 index_daily.py --pull    # 强制走 tushare 增量合并
  "C:\\Program Files\\Python311\\python.exe" -X utf8 index_daily.py --show    # 仅读回打印行数与最新日期
"""

import argparse
import glob
import os
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "index_daily")
TDX_IDX = os.path.join(HERE, "data", "tdx", "ts_index_daily")
ENV_PATH = r"D:\青萍之末2\.env"
TARGETS = {  # 落盘文件名 -> (tdx ts_code, tushare ts_code)
    "sh000001.parquet": ("000001.SH", "000001.SH"),  # 上证指数（TDX 侧即 999999）
}
PULL_DAYS = 400
COLS = ["trade_date", "open", "close", "high", "low", "vol", "amount"]


def _token():
    """P0-4：优先环境变量 TUSHARE_TOKEN，再回落同目录 .env.local XT_DATA_KEY
    （SYSTEM 任务可读，与 tushare_sync/九维同源），最后兼容旧 D:\\青萍之末2\\.env。"""
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    _local = os.path.join(HERE, ".env.local")
    if os.path.exists(_local):
        for ln in open(_local, encoding="utf-8"):
            if ln.startswith("XT_DATA_KEY="):
                return ln.split("=", 1)[1].strip()
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("TUSHARE_TOKEN="):
                    return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _from_tdx(ts_code):
    parts = sorted(glob.glob(os.path.join(TDX_IDX, "part_*.parquet")))
    if not parts:
        return pd.DataFrame()
    try:
        df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    except Exception:
        return pd.DataFrame()
    df = df[df["ts_code"] == ts_code].copy()
    return _tidy(df)


def _from_tushare(ts_code, days=PULL_DAYS):
    tok = _token()
    if not tok:
        print("PULL SKIP (no TUSHARE_TOKEN in %s)" % ENV_PATH)
        return pd.DataFrame()
    try:
        import tushare as ts

        pro = ts.pro_api(tok)
        end = pd.Timestamp.today().strftime("%Y%m%d")
        start = (pd.Timestamp.today() - pd.Timedelta(days=int(days * 1.6))).strftime(
            "%Y%m%d"
        )
        raw = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
        if raw is None or raw.empty:
            return pd.DataFrame()
        print(
            "tushare pulled %d rows %s~%s"
            % (len(raw), raw["trade_date"].min(), raw["trade_date"].max())
        )
        return _tidy(raw)
    except Exception as e:
        print("PULL FAIL: %r" % e)
        return pd.DataFrame()


def _tidy(df):
    """统一列：trade_date 'YYYYMMDD' 字符串 + OHLCV，按日期排序去重。"""
    cols = [c for c in COLS if c in df.columns]
    df = df[cols].copy()
    df["trade_date"] = df["trade_date"].astype(str).str[:8]
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (
        df.dropna(subset=["close"])
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _atomic_write(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".parquet", dir=os.path.dirname(path))
    os.close(fd)
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)  # 原子替换，读者要么旧版要么新版
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def update(name, force_pull=False):
    tdx_code, ts_code = TARGETS[name]
    path = os.path.join(OUT_DIR, name)
    df = _from_tdx(tdx_code)
    src = []
    if len(df):
        src.append("tdx %d rows" % len(df))

    old = None
    if os.path.exists(path):
        try:
            old = _tidy(pd.read_parquet(path))
        except Exception:
            old = None
    if old is not None and len(old):
        df = pd.concat([df, old], ignore_index=True)

    # 拉取条件：强制 / 合并后仍为空 / 数据陈旧（最新日距今天超过 10 个自然日）
    stale = False
    if len(df):
        gap = (pd.Timestamp.today() - pd.to_datetime(df["trade_date"].max())).days
        stale = gap > 10
    if force_pull or df.empty or stale:
        fresh = _from_tushare(ts_code)
        if len(fresh):
            df = pd.concat([df, fresh], ignore_index=True)
            src.append("tushare +%d rows" % len(fresh))
        elif df.empty:
            print("%s: no data available" % name)
            return
    df = _tidy(df)
    _atomic_write(df, path)
    print(
        "%s <- %s | rows=%d latest=%s"
        % (name, " + ".join(src) or "existing", len(df), df["trade_date"].iloc[-1])
    )


def show():
    for name in TARGETS:
        path = os.path.join(OUT_DIR, name)
        if not os.path.exists(path):
            print("%s: MISSING" % name)
            continue
        df = pd.read_parquet(path)
        print(
            "%s rows=%d range=%s~%s cols=%s"
            % (
                name,
                len(df),
                df["trade_date"].iloc[0],
                df["trade_date"].iloc[-1],
                list(df.columns),
            )
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="上证指数日线落盘（数据底座，不推送）")
    ap.add_argument(
        "--pull", action="store_true", help="强制走 tushare 增量合并（默认 tdx 优先）"
    )
    ap.add_argument("--show", action="store_true", help="仅读回打印行数与最新日期")
    a = ap.parse_args()
    if a.show:
        show()
    else:
        for _name in TARGETS:
            update(_name, force_pull=a.pull)
        show()
