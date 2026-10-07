# -*- coding: utf-8 -*-
"""K35 大盘-板块-个股 三线联动复盘 · 数据层（MVP，影子观察）。

选型（2026-10-02 用户确认：路线 A / TDX 880xxx / 日线-30m-5m）：
  · 个股: chanlun_data.load_kline（CN tushare 分钟重采样 + zhice/TDX 兜底）
  · 大盘: load_kline 指数（默认 000001.SH 上证；--index 可换 399006.SZ 等）
  · 板块: TDX 880xxx——日线 tdx_sector_daily；30m/5m 由 tdx_sector_min
    1 分钟重采样（label=right 对齐 tushare 分钟桶结束时刻惯例）
  · 个股→板块: ts_stock_basic.industry 名匹配 tdx_sector_def.name，
    seg 优先级 industry881 > industry_l1 > old_industry；别名表兜底；
    可显式 --block 881019 / --block-name 化纤 覆盖
  · 对齐: 个股时间轴为主，板块/大盘按 dt 精确匹配、缺失前向填充
  · 归一化: 三线公共首根 close 为 0% 基准，(c/c0-1)*100

板块分钟尾巴刷新: tdx_sector_min 月度拉取（止 2026-09-24），
refresh_min_tail() 走远端日期过滤增量补到最新交易日，落
data/tdx/tdx_sector_min_tail/part_0000.parquet（独立尾巴仓，不动月度分片）。

纪律：只读影子展示，不构成买卖依据；五类联动信号标注（抗跌/滞涨/
共振底/孤军/三共振）已由 linkage_signals.py v1 实现（判定+侧车+门户
展示，不推送；攒影子样本后再议升级）。

用法:
  python linkage.py 301216.SZ                     # 默认三周期摘要
  python linkage.py 301216.SZ --period 30m --last 480
  python linkage.py 301216.SZ --block 881019 --refresh-min
"""
import glob
import json
import os
import sys
import time
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TDX = os.path.join(HERE, "data", "tdx")
SEC_MIN_TAIL = os.path.join(TDX, "tdx_sector_min_tail")
REMOTE = "http://rookie.xtcloud-tech.com:48088/api/data/table/tdx_sector_min"
PAGE = 5000

# 归一化图配色/周期与 chanlun_plot 对齐
PERIODS = ("1d", "30m", "5m")
DEFAULT_LAST = {"1d": 250, "30m": 480, "5m": 480}
DEFAULT_INDEX = "000001.SH"
SEG_PRI = ("industry881", "industry_l1", "old_industry")

# tushare industry 名 → TDX 板块名（精确匹配失败时兜底；按需增补）
INDUSTRY_ALIAS = {
    "化学纤维": "化纤",
    "化学原料": "化学原料",
    "农药化肥": "农用化工",
    "农药": "农用化工",
    "日用化学": "日用化工",
    "石油贸易": "石油贸易",
}

_DEF_CACHE = {}


# ---------------------------------------------------------------- 板块定义
def sector_defs():
    """{code: {"name":…, "seg":…}}，懒加载缓存。"""
    if _DEF_CACHE:
        return _DEF_CACHE
    for p in sorted(glob.glob(os.path.join(TDX, "tdx_sector_def",
                                           "part_*.parquet"))):
        df = pd.read_parquet(p, columns=["code", "name", "seg"])
        for _, r in df.iterrows():
            _DEF_CACHE[str(r["code"])] = {"name": str(r["name"]),
                                          "seg": str(r["seg"])}
    return _DEF_CACHE


def _stock_industry(code):
    """ts_stock_basic.industry（cn 6 位/后缀码均可）。"""
    import tdx_read as tdxr
    for cand in (code, code.split(".")[0]):
        try:
            df = tdxr.read_all("ts_stock_basic", codes=[cand])
        except Exception:
            df = pd.DataFrame()
        if len(df) and "industry" in df.columns:
            return str(df.iloc[0]["industry"] or "") or None, \
                str(df.iloc[0].get("name") or "")
    return None, ""


def resolve_block(code, block=None, block_name=None):
    """个股 → TDX 板块。返回 {code,name,seg,how} 或 None。"""
    defs = sector_defs()
    if block:
        b = str(block).split(".")[0]
        if b in defs:
            return {"code": b, "name": defs[b]["name"],
                    "seg": defs[b]["seg"], "how": "explicit-code"}
        return None
    want = block_name
    how = "explicit-name"
    if not want:
        ind, _nm = _stock_industry(code)
        if not ind:
            return None
        want = ind
        how = "industry:" + ind
        want = INDUSTRY_ALIAS.get(want, want)
    # 精确名匹配（seg 优先级），失败退包含匹配
    for seg in SEG_PRI + ("concept", "style"):
        for c, d in defs.items():
            if d["name"] == want and d["seg"] == seg:
                return {"code": c, "name": d["name"], "seg": d["seg"],
                        "how": how}
    for seg in SEG_PRI:
        for c, d in defs.items():
            if d["seg"] == seg and len(want) >= 2 and (
                    want in d["name"] or d["name"] in want):
                return {"code": c, "name": d["name"], "seg": d["seg"],
                        "how": how + "(fuzzy)"}
    return None


# ---------------------------------------------------------------- 板块K线
def _read_parquet_code(tbl_dir, code, columns=None):
    """按 code 列下推过滤读全部分片（含尾巴仓），返回合并 df 或空。"""
    frames = []
    for pat in (os.path.join(tbl_dir, "part_*.parquet"),):
        for p in sorted(glob.glob(pat)):
            try:
                df = pd.read_parquet(p, columns=columns,
                                     filters=[("code", "=", code)])
            except Exception:
                df = pd.read_parquet(p, columns=columns)
                df = df[df["code"] == code]
            if len(df):
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else \
        pd.DataFrame(columns=columns or ["code", "trade_date", "close"])


def block_bars(block_code, period, limit=500):
    """板块 88x → [{dt,close,…}]（dt 与 load_kline 惯例对齐）。"""
    if period == "1d":
        df = _read_parquet_code(os.path.join(TDX, "tdx_sector_daily"),
                                block_code,
                                ["code", "trade_date", "open", "high",
                                 "low", "close", "vol", "amount"])
        if not len(df):
            return []
        df = df.sort_values("trade_date").tail(int(limit))

        def _d8(s):
            s = str(s)
            return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if \
                len(s) == 8 and s.isdigit() else s[:10]

        return [{"dt": _d8(r["trade_date"]), "close": float(r["close"]),
                 "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "vol": float(r["vol"] or 0)}
                for _, r in df.iterrows()]
    # 分钟：月度分片 + 尾巴仓 → 1 分钟 → 重采样
    frames = []
    for sub in ("tdx_sector_min", "tdx_sector_min_tail"):
        d = os.path.join(TDX, sub)
        df = _read_parquet_code(d, block_code,
                                ["code", "trade_date", "trade_time",
                                 "open", "high", "low", "close", "vol"])
        if len(df):
            frames.append(df)
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["trade_date", "trade_time"])
    ts = pd.to_datetime(df["trade_date"].astype(str).str.slice(0, 10)
                        + " " + df["trade_time"].astype(str))
    df = df.assign(dt=ts).set_index("dt").sort_index()
    rule = "5min" if period == "5m" else "30min"
    agg = df[["open", "high", "low", "close", "vol"]].resample(
        rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "vol": "sum"}).dropna(subset=["close"])
    agg = agg.tail(int(limit))
    return [{"dt": i.strftime("%Y-%m-%d %H:%M"), "close": float(r["close"]),
             "vol": float(r["vol"] or 0)} for i, r in agg.iterrows()]


def min_local_max_date():
    """tdx_sector_min（分片+尾巴）现有最大 trade_date。"""
    hi = ""
    for sub in ("tdx_sector_min", "tdx_sector_min_tail"):
        for p in sorted(glob.glob(os.path.join(TDX, sub, "part_*.parquet"))):
            try:
                v = pd.read_parquet(p, columns=["trade_date"])["trade_date"]
                v = v.astype(str).max()
                hi = max(hi, v)
            except Exception:
                pass
    return hi


def refresh_min_tail(quiet=False):
    """远端日期过滤增量补 tdx_sector_min 尾巴（幂等）。"""
    hi = min_local_max_date()
    if not hi:
        return {"ok": False, "error": "本地无 tdx_sector_min 基线"}
    start = str(int(hi[:8]) + 1)  # YYYYMMDD+1（跨月末由远端空集自然消化）
    rows, offset = [], 0
    while True:
        url = ("%s?limit=%d&offset=%d&start_date=%s" %
               (REMOTE, PAGE, offset, start))
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8"))
        got = d.get("rows", [])
        if not got:
            break
        rows.extend(dict(zip(d["columns"], x)) for x in got)
        offset += len(got)
        total = d.get("total") or 0
        if total and offset >= total:
            break
        time.sleep(0.15)
    if not rows:
        return {"ok": True, "from": start, "rows": 0, "note": "无新数据"}
    os.makedirs(SEC_MIN_TAIL, exist_ok=True)
    fp = os.path.join(SEC_MIN_TAIL, "part_0000.parquet")
    new = pd.DataFrame(rows)
    if os.path.exists(fp):
        old = pd.read_parquet(fp)
        new = pd.concat([old, new], ignore_index=True)
    new = new.drop_duplicates(subset=["code", "trade_date", "trade_time"],
                              keep="last")
    new.to_parquet(fp, index=False)
    if not quiet:
        print("tail +%d rows (%s → %s)" % (
            len(rows), start, str(new["trade_date"].astype(str).max())))
    return {"ok": True, "from": start, "rows": len(rows)}


# ---------------------------------------------------------------- 对齐归一
def _norm_dt(dt, period):
    s = str(dt)
    return s[:10] if period == "1d" else s[:16]


def series(code, period, limit=None, block=None, block_name=None,
           index_code=DEFAULT_INDEX, refresh=False):
    """三线取数+对齐+归一化 → 绘图包。"""
    from chanlun_data import load_kline
    limit = limit or DEFAULT_LAST.get(period, 480)
    rs = load_kline(code, period, int(limit))
    if not rs.get("ok"):
        return {"ok": False, "error": rs.get("error", "load_kline fail"),
                "code": code, "period": period}
    stock = {_norm_dt(b["dt"], period): float(b["c"])
             for b in rs["bars"]}
    dts = [_norm_dt(b["dt"], period) for b in rs["bars"]]

    lines, meta = {}, {}
    ri = load_kline(index_code, period, int(limit))
    if ri.get("ok") and ri["bars"]:
        lines["index"] = {_norm_dt(b["dt"], period): float(b["c"])
                          for b in ri["bars"]}
        meta["index"] = {"name": index_code,
                         "last": _norm_dt(ri["bars"][-1]["dt"], period)}
    meta["stock"] = {"last": dts[-1]}

    blk = resolve_block(code, block=block, block_name=block_name)
    if blk:
        bb = block_bars(blk["code"], period, int(limit))
        if bb:
            lines["block"] = {_norm_dt(b["dt"], period): b["close"]
                              for b in bb}
            meta["block"] = {"code": blk["code"], "name": blk["name"],
                             "seg": blk["seg"], "how": blk["how"],
                             "last": _norm_dt(bb[-1]["dt"], period)}
    meta["resolved_block"] = blk

    # 前向填充到个股时间轴
    filled, carry = {}, {}
    for key, mp in lines.items():
        out = []
        for d in dts:
            if d in mp:
                carry[key] = mp[d]
            out.append(carry.get(key))
        filled[key] = out

    # 公共首根（三线都有真实/已填充值）为 0% 基准
    keys = list(filled)
    base_i = 0
    for i in range(len(dts)):
        if all(filled[k][i] is not None for k in keys):
            base_i = i
            break
    def pct(arr):
        return None if arr[base_i] in (None, 0) else [
            None if v is None else round((v / arr[base_i] - 1) * 100, 3)
            for v in arr]

    n_ffill = {k: sum(1 for v in filled[k] if v is None) for k in keys}
    return {"ok": True, "code": code, "name": rs.get("name") or code,
            "period": period, "dts": dts,
            "stock": pct([stock[d] for d in dts]),
            "index": pct(filled.get("index", [])) if "index" in filled else None,
            "block": pct(filled.get("block", [])) if "block" in filled else None,
            "meta": meta, "base_dt": dts[base_i],
            "stock_last_close": stock[dts[-1]],
            "n": len(dts), "ffill_gaps": n_ffill}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="三线联动数据层")
    ap.add_argument("code")
    ap.add_argument("--period", default=",".join(PERIODS))
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--block", default=None)
    ap.add_argument("--block-name", default=None)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--refresh-min", action="store_true")
    a = ap.parse_args()
    if a.refresh_min:
        print(json.dumps(refresh_min_tail(), ensure_ascii=False))
    for per in a.period.split(","):
        r = series(a.code, per, a.last or None, block=a.block,
                   block_name=a.block_name, index_code=a.index)
        slim = {k: v for k, v in r.items()
                if k in ("ok", "period", "n", "base_dt", "ffill_gaps",
                         "meta", "error")}
        if r.get("ok"):
            slim["last_pct"] = {k: r[k][-1] for k in ("stock", "index",
                                                      "block") if r.get(k)}
        print(json.dumps(slim, ensure_ascii=False))


if __name__ == "__main__":
    main()
