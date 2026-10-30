# -*- coding: utf-8 -*-
"""缠论工作台 P1 数据底座：多周期 K 线统一加载（不依赖 pandas）。

periods: 1d / 60m / 30m
- 1d: zhice-db v2_bar_daily（CN/US/HK/CRYPTO 四市场统一）
- 60m/30m 优先级：
  · CN     → 本地 tushare 仓 stk_mins 1min 重采样（中证800近1年）
             → 缺失回源远程 API → 再回落 db 重采样兜底
  · CRYPTO → 币安公共 K 线（30m/1h，免钥）
  · US/HK  → zhice-db v2_bar_minute 1 分钟基线 SQL 重采样（存量短：
             US 自 2026-08-18、HK 自 2026-09-02，部分标的缺失）
  · CN/CRYPTO 在上方源不可用时同样回落 db 重采样兜底
- db 直连失败自动降级 docker exec psql（同 data_loader 兜底思路）。
- 查询全部参数化（%s），仅桶宽/条数等整数内联。

load_kline(code, period="1d", limit=500) →
  {"ok": True,  "code", "market", "period", "source", "bars": [{dt,o,h,l,c,v}]}
  {"ok": False, "error": "..."}
bars 升序；dt 按市场本地时区显示（CN/HK/CRYPTO=北京时区，US=美东）。
"""

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging

try:
    from tools.safe_urlopen import safe_urlopen
except ImportError:  # 作为脚本直接运行时
    from safe_urlopen import safe_urlopen

try:
    from bar_dto import BarQuery
except ImportError:  # pragma: no cover - package import fallback
    from .bar_dto import BarQuery

HERE = os.path.dirname(os.path.abspath(__file__))
DB_CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["db"]
QP_ENV = r"D:\青萍之末2\.env"
TZ_CN = ZoneInfo("Asia/Shanghai")
TZ_US = ZoneInfo("America/New_York")
PERIODS = ("1d", "60m", "30m", "5m")
# K20：5m 供多级别推演卡（三石三级联立：日定方向/30f定段落/5f定时点）
MIN_FREQ = {
    "60m": ("60min", "1h", 3600, 120),
    "30m": ("30min", "30m", 1800, 60),
    "5m": ("5min", "5m", 300, 12),
}
# 分钟存量口径（页面如实提示用）
MIN_COVER = {
    "US": "US 分钟线存量约 2026-08-18 起（截至 2026-09-18）",
    "HK": "HK 分钟线存量约 2026-09-02 起",
    "CN": "CN 分钟线本地仓覆盖中证800近1年（1min 重采样），其余标的回源远程/db 兜底",
    "CRYPTO": "CRYPTO 分钟线 db 兜底存量约 2026-08-21 起",
}


def _f(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _token():
    try:
        with open(QP_ENV, encoding="utf-8") as f:
            for line in f:
                if line.startswith("TUSHARE_TOKEN="):
                    return line.strip().split("=", 1)[1]
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_data.py", exc)
    return ""


def _db_query(sql, params):
    """参数化查询；psycopg2 直连优先，docker psql 兜底。返回 rows 列表。"""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=DB_CFG["host"],
            port=DB_CFG["port"],
            user=DB_CFG["user"],
            password=DB_CFG["password"],
            dbname=DB_CFG["dbname"],
            connect_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return _db_query_docker(sql, params)


def _db_query_docker(sql, params):
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
        if isinstance(v, datetime):
            v = str(v)
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
        raise RuntimeError("psql: " + out.stderr.strip()[:150])
    return [tuple(l.split("\x1f")) for l in out.stdout.strip().splitlines() if l]


def _parse_ts(v):
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[: 19 + 5], fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _norm(code):
    """多形态代码 → (market, symbol)。支持 600519 / 600519.SH / sh600519 /
    00700 / rt_hk00700 / AAPL / gb_aapl / BTC/USDT / BTCUSDT 等。"""
    u = str(code or "").strip().upper()
    if not u:
        return None
    if "/" in u:
        return ("CRYPTO", u)
    if u != "USDT" and u.endswith("USDT"):
        return ("CRYPTO", u[:-4] + "/USDT")
    if u.startswith("GB_"):
        return ("US", u[3:])
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", u)
    if m:
        return ("CN", m.group(2) + "." + m.group(1))
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", u)
    if m:
        return ("CN", u)
    m = re.match(r"^(?:RT_)?HK(\d{1,5})$", u)
    if m:
        return ("HK", m.group(1).zfill(5))
    if re.match(r"^\d{6}$", u):
        if u.startswith("92") or u[0] in "48":
            suf = "BJ"
        elif u[0] in "03":
            suf = "SZ"
        else:
            suf = "SH"
        return ("CN", u + "." + suf)
    if re.match(r"^\d{4,5}$", u):
        return ("HK", u.zfill(5))
    if re.match(r"^[A-Z]{2,6}(\.[A-Z])?$", u):
        return ("US", u)
    return None


def _bar(t, o, h, l, c, v, tz, daily=False):
    d = _parse_ts(t)
    if d.tzinfo:
        d = d.astimezone(tz)
    else:
        d = d.replace(tzinfo=timezone.utc).astimezone(tz)
    return {
        "dt": d.strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M"),
        "o": _f(o),
        "h": _f(h),
        "l": _f(l),
        "c": _f(c),
        "v": round(float(v) if v else 0, 3),
    }


def _daily(market, symbol, limit):
    sql = (  # nosec B608 - 值参数化/内部标识符（已抽查）
        "SELECT trade_date, open, high, low, close, volume "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "FROM v2_bar_daily WHERE market=%s AND symbol=%s "
        "ORDER BY trade_date DESC LIMIT " + str(int(limit))
    )
    rows = _db_query(sql, [market, symbol])
    bars = [
        _bar(td, o, h, l, c, v, TZ_CN, daily=True)
        for td, o, h, l, c, v in rows
        if _f(c) is not None
    ]
    bars.reverse()
    return bars


def _tdx_daily_fallback(market, symbol, limit):
    """zhice-db 日线空结果时的 TDX 本地仓兜底（CN=ts_daily / HK=ts_hk_daily）。

    TDX 与 tushare daily 同源同值（2026-09-27 parity 14/14），仅作
    zhice-db 故障时的备份链路；历史自 2023-09 起，无 ETF/LOF。
    输出 bar 形态与 _bar(daily=True) 一致（dt/o/h/l/c/v，无 pc）。"""
    try:
        import tdx_read as tdxr
    except Exception:
        return []
    tbl = {"CN": "ts_daily", "HK": "ts_hk_daily"}.get(market)
    if not tbl:
        return []
    cand = [symbol + ".HK", symbol] if market == "HK" else [symbol]
    try:
        df = tdxr.read_range(
            tbl,
            codes=cand,
            columns=["ts_code", "trade_date", "open", "high", "low", "close", "vol"],
        )
    except Exception:
        return []
    if df is None or df.empty:
        return []
    rows = {}
    for r in df.to_dict("records"):
        try:
            o, h, l, c, v = (
                float(r.get(k)) for k in ("open", "high", "low", "close", "vol")
            )
        except (TypeError, ValueError):
            continue
        if any(x != x for x in (o, h, l, c, v)) or c <= 0 or v <= 0:
            continue  # NaN/停牌行，与 parquet 清洗同口径
        d8 = str(r.get("trade_date", "")).strip().replace("-", "")[:8]
        if len(d8) != 8 or not d8.isdigit():
            continue
        rows[d8] = (o, h, l, c, v)
    out = []
    for d8 in sorted(rows)[-int(limit) :]:
        o, h, l, c, v = rows[d8]
        out.append(
            {
                "dt": "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:]),
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": round(v, 3),
            }
        )
    return out


def _minute_resample(market, symbol, period, limit):
    """v2_bar_minute 1 分钟基线 → 30m/60m SQL 重采样（US/HK 主源，其余兜底）。"""
    bucket = MIN_FREQ[period][2]
    start = datetime.now(timezone.utc) - timedelta(days=45)
    sql = (  # nosec B608 - 值参数化/内部标识符（已抽查）
        "SELECT to_timestamp(floor(extract(epoch FROM bar_time) / "  # nosec B608 - 值参数化/内部标识符（已抽查）
        + str(bucket)
        + ") * "
        + str(bucket)
        + ") AS bucket, "
        "(array_agg(open ORDER BY bar_time))[1], max(high), min(low), "
        "(array_agg(close ORDER BY bar_time DESC))[1], sum(volume) "
        "FROM v2_bar_minute WHERE market=%s AND symbol=%s "
        "AND bar_time >= %s GROUP BY bucket "
        "ORDER BY bucket DESC LIMIT " + str(int(limit))
    )
    tz = TZ_US if market == "US" else TZ_CN
    rows = _db_query(sql, [market, symbol, start])
    bars = [
        _bar(b, o, h, l, c, v, tz) for b, o, h, l, c, v in rows if _f(c) is not None
    ]
    bars.reverse()
    return bars


def _bucket_label(t, bm_min):
    """A 股会话相对分桶：上午 09:30 起、下午 13:00 起，按 bm_min 分钟切桶，
    桶标结束时刻（对齐 tushare 原生 30/60min 惯例）；11:30/15:00 收盘
    瞬时 bar 并入前一桶。返回该桶的标签 datetime。"""
    m = t.hour * 60 + t.minute
    if m < 780:  # 上午场（09:30-11:30）
        start, off = 570, m - 570
    else:  # 下午场（13:00-15:00）
        start, off = 780, m - 780
    if t.second == 0 and m in (690, 900) and off > 0:
        off -= 1  # 收盘瞬时并入前桶
    lab = start + (off // bm_min + 1) * bm_min
    return t.replace(hour=lab // 60 % 24, minute=lab % 60, second=0)


def _ts_mins_local(ts_code, freq, days, limit):
    """本地 tushare 仓 stk_mins（1min 逐码月度）→ 30m/60m 重采样。

    本地缺失/空数据返回 []，由上层回源远程。"""
    try:
        import datamart

        df = datamart.read(
            "stk_mins",
            codes=ts_code,
            start=(datetime.now(TZ_CN) - timedelta(days=days)).strftime("%Y%m%d"),
            end=datetime.now(TZ_CN).strftime("%Y%m%d"),
        )
    except Exception:
        return []
    if df is None or df.empty or "trade_time" not in df.columns:
        return []
    bm_min = 30 if str(freq).startswith("30") else 60
    rows = []
    for r in df.to_dict("records"):
        try:
            t = datetime.strptime(
                str(r["trade_time"])[:19], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=TZ_CN)
            o, h, l, c = (float(r.get(k)) for k in ("open", "high", "low", "close"))
            v = float(r.get("vol") or 0)
        except (TypeError, ValueError):
            continue
        if c != c or c <= 0:
            continue
        rows.append((t, o, h, l, c, v))
    rows.sort(key=lambda x: x[0])
    out, cur, key0 = [], None, None
    for t, o, h, l, c, v in rows:
        lab = _bucket_label(t, bm_min)
        if key0 != lab:
            if cur:
                out.append(cur)
            key0 = lab
            cur = [lab, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur:
        out.append(cur)
    return [_bar(t, o, h, l, c, v, TZ_CN) for t, o, h, l, c, v in out[-limit:]]


def _ts_mins(ts_code, freq, days, limit):
    """CN 分钟线：本地 stk_mins 重采样优先（中证800覆盖近1年），
    缺失/未覆盖标的回源远程 API（权限不足时抛错走 db 兜底）。
    返回 (bars, source_tag)。"""
    bars = _ts_mins_local(ts_code, freq, days, limit)
    if bars:
        return bars, "tushare stk_mins 本地仓"
    body = json.dumps(
        {
            "api_name": "stk_mins",
            "token": _token(),
            "params": {
                "ts_code": ts_code,
                "freq": freq,
                "start_date": (datetime.now(TZ_CN) - timedelta(days=days)).strftime(
                    "%Y-%m-%d 00:00:00"
                ),
            },
            "fields": "",
        }
    ).encode()
    req = urllib.request.Request(
        "http://api.tushare.pro",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with safe_urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode())
    if j.get("code") != 0:
        raise RuntimeError(str(j.get("message"))[:100])
    items, fields = j["data"]["items"], j["data"]["fields"]
    ix = {f: i for i, f in enumerate(fields)}
    recs = []
    for it in items:
        try:
            recs.append(
                (
                    datetime.strptime(
                        str(it[ix["trade_time"]]), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=TZ_CN),
                    it[ix["open"]],
                    it[ix["high"]],
                    it[ix["low"]],
                    it[ix["close"]],
                    it[ix["vol"]],
                )
            )
        except Exception:
            continue
    recs.sort(key=lambda r: r[0])
    return (
        [_bar(t, o, h, l, c, v, TZ_CN) for t, o, h, l, c, v in recs[-limit:]],
        "tushare stk_mins 远程",
    )


def _binance(pair, interval, limit):
    sym = pair.replace("/", "")
    url = (
        "https://api.binance.com/api/v3/klines?symbol="
        + urllib.parse.quote(sym)
        + "&interval="
        + interval
        + "&limit="
        + str(int(min(limit, 1000)))
    )
    with safe_urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode())
    return [
        _bar(
            datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            k[1],
            k[2],
            k[3],
            k[4],
            k[5],
            TZ_CN,
        )
        for k in data
    ]


def _bar_store_result(
    code,
    period,
    limit,
    bar_store,
    as_of=None,
    start=None,
    end=None,
    closed_only=False,
    explain=False,
):
    """Adapt an explicitly injected BarStore to the legacy K-line contract."""
    market_symbol = _norm(code)
    if not market_symbol:
        return {"ok": False, "error": "未识别「%s」" % code}
    market, symbol = market_symbol
    normalized_period = str(period or "1d").lower()
    if normalized_period not in PERIODS:
        return {
            "ok": False,
            "error": "period 仅支持 1d / 60m / 30m / 5m，收到「%s」"
            % normalized_period,
        }
    try:
        requested_limit = max(1, int(limit or 500))
    except (TypeError, ValueError):
        requested_limit = 500
    try:
        query = BarQuery(
            start=start, end=end, as_of=as_of, closed_only=bool(closed_only)
        )
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "加载失败：%s" % str(exc)[:120]}
    try:
        bars = bar_store.query(query)
    except Exception as exc:
        return {"ok": False, "error": "加载失败：%s" % str(exc)[:120]}
    selected = [
        bar
        for bar in bars
        if str(bar.symbol).upper() == str(symbol).upper()
        and str(bar.market).upper() == str(market).upper()
        and str(bar.timeframe).lower() == normalized_period
    ]
    selected = selected[-requested_limit:] if selected else []
    tz = TZ_US if market == "US" else TZ_CN
    daily = normalized_period == "1d"
    legacy_bars = []
    for bar in selected:
        local_dt = bar.dt.astimezone(tz)
        legacy_bars.append(
            {
                "dt": local_dt.strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M"),
                "o": _f(bar.o),
                "h": _f(bar.h),
                "l": _f(bar.l),
                "c": _f(bar.c),
                "v": round(float(bar.v), 3) if bar.v is not None else 0,
            }
        )
    result = {
        "ok": True,
        "code": symbol,
        "market": market,
        "period": normalized_period,
        "source": "BarStore",
        "bars": legacy_bars,
    }
    if explain:
        metadata = getattr(bar_store, "explain", lambda: {})()
        if isinstance(metadata, dict):
            result["metadata"] = metadata
    return result


def load_kline(
    code,
    period="1d",
    limit=500,
    *,
    bar_store=None,
    as_of=None,
    start=None,
    end=None,
    closed_only=False,
    explain=False,
):
    """统一入口：任意形态代码 + 周期 → K 线 dict（见模块 docstring）。"""
    if bar_store is not None:
        return _bar_store_result(
            code, period, limit, bar_store, as_of, start, end, closed_only, explain
        )
    period = str(period or "1d").lower()
    if period not in PERIODS:
        return {
            "ok": False,
            "error": "period 仅支持 1d / 60m / 30m / 5m，收到「%s」" % period,
        }
    nm = _norm(code)
    if not nm:
        return {
            "ok": False,
            "error": "未识别「%s」。支持：600519 / 600519.SH / sh600519 / "
            "00700 / AAPL / gb_nvda / BTC/USDT" % code,
        }
    market, sym = nm
    try:
        limit = max(50, min(int(limit or 500), 1000))
    except (TypeError, ValueError):
        limit = 500

    def ok(bars, source):
        return {
            "ok": True,
            "code": sym,
            "market": market,
            "period": period,
            "source": source,
            "bars": bars,
        }

    try:
        if period == "1d":
            bars = _daily(market, sym, limit)
            if bars:
                return ok(bars, "zhice-db v2_bar_daily")
            bars = _tdx_daily_fallback(market, sym, limit)
            if bars:
                return ok(bars, "TDX 日线兜底")
        else:
            ts_freq, bn_intv, _, days = MIN_FREQ[period]
            if market == "CN":
                try:
                    bars, tag = _ts_mins(sym, ts_freq, days, limit)
                    if bars:
                        return ok(bars, tag)
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_data.py", exc)
            elif market == "CRYPTO":
                try:
                    bars = _binance(sym, bn_intv, limit)
                    if bars:
                        return ok(bars, "币安公共K线")
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_data.py", exc)
            bars = _minute_resample(market, sym, period, limit)
            if bars:
                return ok(bars, "zhice-db v2_bar_minute 重采样")
        if period == "1d":
            return {
                "ok": False,
                "error": "「%s/%s」日线无数据：标的可能不在库或已退市"
                "（可在量化中心确认代码）" % (market, sym),
            }
        return {
            "ok": False,
            "error": "「%s/%s」%s 无数据：%s（1 分钟基线，部分标的缺失）"
            % (market, sym, period, MIN_COVER[market]),
        }
    except Exception as ex:
        return {"ok": False, "error": "加载失败：%s" % str(ex)[:120]}
    return {"ok": False, "error": "未知数据场景"}
