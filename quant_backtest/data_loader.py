# -*- coding: utf-8 -*-
"""行情数据加载：zhice-db v2_bar_daily → pandas.DataFrame。

直连 127.0.0.1:5432，失败自动降级 docker exec zhice-db psql。
返回 DataFrame（index=trade_date, columns=open/high/low/close/volume/turnover/pre_close）。
"""
import json
import os
import subprocess
import threading

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB_CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["db"]

# P1-1：模块级单连接被 ThreadingHTTPServer 多线程并发使用（非线程安全，
# 会出现协议错乱/连接假死）。改为 threading.local() 每线程独立连接，
# 范式对齐 chanlun_data.py（查询间连接隔离）。
_tls = threading.local()


def _direct_conn():
    import psycopg2
    c = getattr(_tls, "conn", None)
    if c is not None and c.closed:
        c = None
    if c is None:
        c = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            user=DB_CFG["user"], password=DB_CFG["password"],
            dbname=DB_CFG["dbname"])
        c.autocommit = True
        _tls.conn = c
    return c


def _query_direct(sql, params):
    c = _direct_conn()
    with c.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return cols, rows


def _query_docker(sql, params):
    args = ["docker", "exec", "-i", DB_CFG["container"], "psql",
            "-U", DB_CFG["user"], "-d", DB_CFG["dbname"],
            "-A", "-t", "-F", "\x1f", "-v", "ON_ERROR_STOP=1"]
    parts = sql.split("%s")
    sql2 = parts[0]
    for i, v in enumerate(params or []):
        if isinstance(v, (list, tuple)):      # 数组参数：内联 ARRAY[...] 字面量
            sql2 += ("ARRAY[" + ",".join(
                "'%s'" % str(x).replace("'", "''") for x in v) + "]"
                + parts[i + 1])
        else:
            sql2 += ":'p%d'" % i + parts[i + 1]
            args += ["-v", "p%d=%s" % (i, v)]
    out = subprocess.run(args, input=sql2, capture_output=True, text=True,
                         timeout=30, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError("psql 失败: " + out.stderr.strip()[:200])
    return None, [tuple(l.split("\x1f")) for l in out.stdout.strip().splitlines() if l]


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_bars(market, symbols, start=None, end=None):
    """加载单市场多只股票日线。返回 {symbol: DataFrame}。"""
    if isinstance(symbols, str):
        symbols = [symbols]
    sql = ("SELECT symbol, trade_date, open, high, low, close, volume, turnover, pre_close "
           "FROM v2_bar_daily WHERE market=%s AND symbol = ANY(%s)")
    params = [market, list(symbols)]
    if start:
        sql += " AND trade_date >= %s"
        params.append(start)
    if end:
        sql += " AND trade_date <= %s"
        params.append(end)
    sql += " ORDER BY symbol, trade_date"
    try:
        cols, rows = _query_direct(sql, params)
    except Exception as _e:      # 诊断：直连失败原因落盘（SYSTEM 等上下文）
        try:
            import time as _t
            with open(r"C:\Temp\db_direct_err.log", "a",
                      encoding="utf-8") as f:
                f.write("%s load_bars %s %r\n" % (
                    _t.strftime("%m-%d %H:%M:%S"), market, _e))
        except OSError:
            pass
        cols, rows = _query_docker(sql, params)

    frames = {}
    for row in rows:
        if len(row) < 9:
            continue
        sym, td = row[0], row[1]
        frames.setdefault(sym, []).append({
            "trade_date": str(td),
            "open": _to_float(row[2]), "high": _to_float(row[3]),
            "low": _to_float(row[4]), "close": _to_float(row[5]),
            "volume": _to_float(row[6]), "turnover": _to_float(row[7]),
            "pre_close": _to_float(row[8]),
        })
    out = {}
    for sym, recs in frames.items():
        df = pd.DataFrame(recs)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        df = df.dropna(subset=["close"])
        out[sym] = df
    return out


def available_symbols(market, limit=None):
    sql = "SELECT DISTINCT symbol FROM v2_bar_daily WHERE market=%s ORDER BY symbol"
    if limit:
        sql += " LIMIT %d" % int(limit)
    try:
        _, rows = _query_direct(sql, [market])
    except Exception:
        _, rows = _query_docker(sql, [market])
    return [r[0] for r in rows]


def latest_dates(market, symbols):
    """批量查每只标的最新交易日 → {symbol: 'YYYYMMDD'}（增量导出比对用）。
    库内无该标的的键缺省。直连失败走 docker 兜底（与 load_bars 同款）。"""
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols:
        return {}
    sql = ("SELECT symbol, max(trade_date) FROM v2_bar_daily "
           "WHERE market=%s AND symbol = ANY(%s) GROUP BY symbol")
    params = [market, list(symbols)]
    try:
        _, rows = _query_direct(sql, params)
    except Exception:
        _, rows = _query_docker(sql, params)
    out = {}
    for sym, td in rows:
        if td is None:
            continue
        s = str(td).replace("-", "")[:8]
        if s:
            out[sym] = s
    return out
