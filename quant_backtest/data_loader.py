# -*- coding: utf-8 -*-
"""行情数据加载：zhice-db v2_bar_daily → pandas.DataFrame。

直连 127.0.0.1:5446（本机 zhice-db 实际映射；5432 被 RAGFlow infinity 占用，
2026-10-16 审计修正），失败自动降级 docker exec zhice-db psql。
返回 DataFrame（index=trade_date, columns=open/high/low/close/volume/turnover/pre_close）。
"""

import json
import os
import subprocess
import threading

import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DB_CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["db"]


def _db_password():
    """ZHICE_DB_PASSWORD 回落链：env → 本目录 .env.local →
    zhice 活部署 .env.local 的 V2_DB_PASSWORD（防轮换多副本漂移，
    对齐 kg_schema._load_neo4j_password 模式）→ config.json。"""
    pwd = os.environ.get("ZHICE_DB_PASSWORD", "").strip()
    if pwd:
        return pwd
    candidates = [
        (os.path.join(HERE, ".env.local"), "ZHICE_DB_PASSWORD"),
        (r"C:\项目\zhice-os-v2-windows-20260920\code\.env.local", "V2_DB_PASSWORD"),
    ]
    for path, key in candidates:
        if not os.path.isfile(path):
            continue
        try:
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line.startswith(key + "=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/data_loader.py", exc)
    return DB_CFG.get("password") or ""


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
            host=DB_CFG["host"],
            port=DB_CFG["port"],
            user=DB_CFG["user"],
            password=_db_password(),
            dbname=DB_CFG["dbname"],
            # 2026-10-30 P82：无超时的 connect 在 DB 抖动时会永久阻塞（R1 18:30 卡死根因之一）
            # 2026-10-30 R5 修复：psycopg2 的 connect_timeout 只接受整数；此前传
            # float("5.0") 会报 invalid integer value "5.0"（db_direct_err.log 45,922 条根因）。
            connect_timeout=int(float(os.environ.get("ZHICE_DB_CONNECT_TIMEOUT", "5"))),
        )
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
    args = [
        "docker",
        "exec",
        "-i",
        DB_CFG["container"],
        "psql",
        "-U",
        DB_CFG["user"],
        "-d",
        DB_CFG["dbname"],
        "-A",
        "-t",
        "-F",
        "\x1f",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    parts = sql.split("%s")
    sql2 = parts[0]
    for i, v in enumerate(params or []):
        if isinstance(v, (list, tuple)):  # 数组参数：内联 ARRAY[...] 字面量
            sql2 += (
                "ARRAY["
                + ",".join("'%s'" % str(x).replace("'", "''") for x in v)
                + "]"
                + parts[i + 1]
            )
        else:
            sql2 += ":'p%d'" % i + parts[i + 1]
            args += ["-v", "p%d=%s" % (i, v)]
    out = subprocess.run(
        args,
        input=sql2,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
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
    sql = (
        "SELECT symbol, trade_date, open, high, low, close, volume, turnover, pre_close "
        "FROM v2_bar_daily WHERE market=%s AND symbol = ANY(%s)"
    )
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
    except Exception as _e:  # 诊断：直连失败原因落盘（SYSTEM 等上下文）
        try:
            import time as _t

            with open(r"C:\Temp\db_direct_err.log", "a", encoding="utf-8") as f:
                f.write(
                    "%s load_bars %s %r\n" % (_t.strftime("%m-%d %H:%M:%S"), market, _e)
                )
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/data_loader.py", exc)
        cols, rows = _query_docker(sql, params)

    frames = {}
    for row in rows:
        if len(row) < 9:
            continue
        sym, td = row[0], row[1]
        frames.setdefault(sym, []).append(
            {
                "trade_date": str(td),
                "open": _to_float(row[2]),
                "high": _to_float(row[3]),
                "low": _to_float(row[4]),
                "close": _to_float(row[5]),
                "volume": _to_float(row[6]),
                "turnover": _to_float(row[7]),
                "pre_close": _to_float(row[8]),
            }
        )
    out = {}
    for sym, recs in frames.items():
        df = pd.DataFrame(recs)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        df = df.dropna(subset=["close"])
        out[sym] = df
    return out


def load_table(start=None, end=None, symbols=None, limit=None):
    """v2_bar_daily 扁平读取 → DataFrame（数据地基模块章程 §3：PG 入册读路径）。

    与 load_bars（按市场分 {symbol: DataFrame}，策略侧专用）不同，本函数
    返回单一 DataFrame 供 datamart layout "pg" 路由消费；symbol 保持库内
    形态（datamart 只做列名归一，不转换代码形态）。直连失败走 docker 兜底。
    """
    sql = (
        "SELECT symbol, market, trade_date, open, high, low, close, "
        "volume, turnover, pre_close FROM v2_bar_daily"
    )
    params, where = [], []
    if symbols:
        if isinstance(symbols, str):
            symbols = [symbols]
        where.append("symbol = ANY(%s)")
        params.append(list(symbols))
    if start:
        where.append("trade_date >= %s")
        params.append(start)
    if end:
        where.append("trade_date <= %s")
        params.append(end)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # 与 dm.read 尾部 dcol(trade_date) 稳定排序后 head(limit) 的语义对齐：
    # 日期优先排序，limit 下推时取到的才是"最早 N 行"而非字典序前 N 行。
    sql += " ORDER BY trade_date, symbol"
    if limit:
        sql += " LIMIT %d" % int(limit)
    try:
        cols, rows = _query_direct(sql, params)
    except Exception:
        cols, rows = _query_docker(sql, params)
    recs = []
    for row in rows:
        if len(row) < 10:
            continue
        td = str(row[2]).replace("-", "")[:8]
        recs.append(
            {
                "symbol": row[0],
                "market": row[1],
                "trade_date": td,
                "open": _to_float(row[3]),
                "high": _to_float(row[4]),
                "low": _to_float(row[5]),
                "close": _to_float(row[6]),
                "volume": _to_float(row[7]),
                "turnover": _to_float(row[8]),
                "pre_close": _to_float(row[9]),
            }
        )
    return pd.DataFrame(
        recs,
        columns=[
            "symbol",
            "market",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "pre_close",
        ],
    )


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
    sql = (
        "SELECT symbol, max(trade_date) FROM v2_bar_daily "
        "WHERE market=%s AND symbol = ANY(%s) GROUP BY symbol"
    )
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


def symbol_ranges(market, symbols):
    """批量查每只标的的库内起止交易日 → {symbol: (min8, max8)}。

    与 latest_dates 的区别：**同时给出头部起点**。增量导出若只看 max，
    库内后来补灌的更早历史永远导不出来（本地文件头部空洞永不复原）——
    WO-QT-20261021-092 判例。库内无该标的的键缺省。
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols:
        return {}
    sql = (
        "SELECT symbol, min(trade_date) AS d0, max(trade_date) AS d1 "
        "FROM v2_bar_daily WHERE market=%s AND symbol = ANY(%s) "
        "GROUP BY symbol"
    )
    params = [market, list(symbols)]
    try:
        _, rows = _query_direct(sql, params)
    except Exception:
        _, rows = _query_docker(sql, params)
    out = {}
    for row in rows:
        if len(row) < 3 or row[1] is None or row[2] is None:
            continue
        d0 = str(row[1]).replace("-", "")[:8]
        d1 = str(row[2]).replace("-", "")[:8]
        if d0 and d1:
            out[row[0]] = (d0, d1)
    return out
