# -*- coding: utf-8 -*-
"""行情替代源路由器（T4，tushare 主源 + akshare 备援）。

背景：雪球行情域 403 "Seek IP Blacklisted" 整域拉黑（探针
tools/xq_quote_alt_probe.py P0-4 实测，docs/2026-10-18_行情替代源探针
报告.md），xq_quote_snap / xq_kline_d 两表长期 degraded 断供。本模块
把探针验证过的替代源搬进采集管道：

  路由：tushare Pro（主源，实测 522ms、daily 与 akshare 逐位一致）
       → akshare（备援）
       → 两源全挂 fail-visible（调用方写 manifest status=
         failed_visible，绝不静默返回空）。

凭证：环境变量 TUSHARE_TOKEN → 仓内 .env.local XT_DATA_KEY（探针同源）。
每次启动显式 ts.set_token（幂等；tushare 虽自落 ~/.tushare 缓存，显式
注入免环境漂移）。token 值绝不打印，日志一律 mask（前4后4）。

单位口径（2026-10-19 实测对齐）：
  · tushare realtime_quote：VOLUME=股、AMOUNT=元，与雪球快照同口径直用；
    percent/chg 由 PRICE/PRE_CLOSE 自算；
  · tushare pro_bar(qfq)/daily：vol=手（×100→股）、amount=千元
    （×1000→元），对齐雪球 kline 口径；qfq 不可得时落 daily 不复权
    （行级 note 标 adj=raw）；
  · akshare stock_zh_a_daily(qfq)：volume=股、amount=元 直用；
  · kline timestamp 统一 "YYYY-MM-DD 00:00:00"（trade_date 锚点）。
    xq_kline_d 现存量为空（degraded 断供），无混源对齐问题。

代理纪律（判例：宿主代理假 502/假 403）：tushare/akshare 内部
requests trust_env 读环境变量——网络调用统一包 no_proxy_env() 临时清
代理，退出逐键还原（不破坏雪球侧 xq_proxy 出口池语义）。
"""
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy")


class no_proxy_env:
    """临时清代理环境变量（判例：宿主代理假 502/假 403），退出还原。

    with no_proxy_env(): ... 块内 HTTP(S)_PROXY/ALL_PROXY 等被摘除、
    NO_PROXY=*；退出逐键还原（含 NO_PROXY 原值）——雪球侧
    xq_proxy.apply_proxy_env 写入的出口在块外不受影响。
    """

    def __enter__(self):
        self._saved = {k: os.environ[k] for k in _PROXY_KEYS
                       if k in os.environ}
        for k in self._saved:
            os.environ.pop(k, None)
        self._saved_np = os.environ.get("NO_PROXY")
        os.environ["NO_PROXY"] = "*"
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            os.environ[k] = v
        if self._saved_np is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = self._saved_np
        return False


def is_index_symbol(sym: str) -> bool:
    """指数锚判定（T4 快照/日线剔除用）。

    SH000xxx/SH880xxx/SH899xxx、SZ399xxx 为指数；SZ000xxx 是平安银行
    等个股不误伤。起因：tushare 新浪 realtime 源对 000001 双关
    （上证指数 vs 平安银行），指数混入快照产生污染行（11.50 挂在
    SH000001 名下，2026-10-19 验收实证），故指数一律不进替代源管道。
    """
    m = re.match(r"^(SH|SZ)(\d{6})$", str(sym or "").upper())
    if not m:
        return False
    pfx, code = m.group(1), m.group(2)
    return ((pfx == "SH" and code.startswith(("000", "880", "899")))
            or (pfx == "SZ" and code.startswith("399")))


def mask_token(tok: str) -> str:
    """token 脱敏（前4后4）；过短返回定长掩码（绝不泄全文）。"""
    tok = str(tok or "")
    if len(tok) <= 8:
        return "****"
    return tok[:4] + "****" + tok[-4:]


def load_ts_token(repo_root: str | None = None) -> str:
    """token 查找：env TUSHARE_TOKEN → 仓内 .env.local XT_DATA_KEY。

    容忍等号两侧空格与 BOM（xueqiu_common.load_token 同款防御）。
    找不到返回 ""（调用方 RuntimeError/降级）。
    """
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    envf = Path(repo_root or HERE) / ".env.local"
    if envf.exists():
        try:
            for ln in envf.read_text(encoding="utf-8-sig").splitlines():
                s = ln.strip().lstrip("\ufeff")
                if s.replace(" ", "").startswith("XT_DATA_KEY="):
                    v = s.split("=", 1)[1].strip()
                    if v:
                        return v
        except Exception:
            pass
    return ""


def setup_tushare(token: str | None = None, repo_root: str | None = None):
    """import tushare + 显式 set_token，返回 (ts_mod, pro_api)。

    每次启动显式 set_token（幂等，免 ~/.tushare 缓存环境漂移）。
    缺库 / token 不可得 raise RuntimeError（调用方降级 akshare）。
    """
    try:
        import tushare
    except ImportError as e:
        raise RuntimeError("tushare 未安装: %s" % e) from e
    tok = (token or load_ts_token(repo_root)).strip()
    if not tok:
        raise RuntimeError("tushare token 不可得"
                           "（TUSHARE_TOKEN / .env.local XT_DATA_KEY）")
    tushare.set_token(tok)
    print("[router] tushare set_token ok (token=%s)" % mask_token(tok))
    return tushare, tushare.pro_api(tok)


def _f(v):
    """安全 float（''/None/NaN → None）。"""
    try:
        f = float(v)
        return f if f == f else None               # NaN 防御
    except (TypeError, ValueError):
        return None


# ── 快照（xq_quote_snap 口径）────────────────────────────────
def _ts_to_sym(tc: str, pairs: dict) -> str:
    """ts_code → 雪球符号（600519.SH → SH600519）。"""
    m = tc.split(".")
    if len(m) == 2 and m[1].upper() in ("SH", "SZ", "BJ"):
        return m[1].upper() + m[0]
    return ""


def snapshot_tushare(syms: list[str], ts_mod=None,
                     batch: int = 50) -> tuple[list, list]:
    """tushare realtime_quote 批量快照 → xq_quote_snap 行口径。

    Args:
        syms: 雪球符号列表（SH600519/SZ000001/SH000001 指数锚）。
        ts_mod: 已 setup 的 tushare 模块（None 则内部 setup）。
        batch: 单请求符号数（URL 长度约束，对齐 QUOTEC_BATCH）。

    Returns:
        (rows, errs)：rows 为 SNAP_COLS 口径 dict 列表（volume=股、
        amount=元、pe/pb/市值=None——tushare 新浪实时源不含估值）；
        errs 为未覆盖码/空批原因（partial 信号，不阻断已得行）。
    """
    rows: list[dict] = []
    errs: list[str] = []
    pairs = {}                                    # ts_code → xq_symbol
    for s in syms:
        if is_index_symbol(s):
            errs.append("指数锚不入快照片: %s" % s)
            continue
        tc = X.to_ts_code(s)
        if tc:
            pairs[tc] = s
    if not pairs:
        errs.append("无可转 ts_code 的符号")
        return [], errs
    ts = ts_mod if ts_mod is not None else setup_tushare()[0]
    with no_proxy_env():
        cs = list(pairs)
        for i in range(0, len(cs), batch):
            part = cs[i:i + batch]
            try:
                df = ts.realtime_quote(ts_code=",".join(part))
            except Exception as e:                # noqa: BLE001
                errs.append("batch %d: %s" % (i, str(e)[:60]))
                continue
            if df is None or df.empty:
                errs.append("batch %d: realtime_quote 空" % i)
                continue
            for _, r in df.iterrows():
                tc = str(r.get("TS_CODE") or "").strip()
                price = _f(r.get("PRICE"))
                pre = _f(r.get("PRE_CLOSE"))
                if not tc or tc not in pairs or not price:
                    continue
                d = str(r.get("DATE") or "").strip()
                t = str(r.get("TIME") or "").strip()
                ymd = d if d.isdigit() and len(d) == 8 else \
                    datetime.now().strftime("%Y%m%d")
                ts_str = ("%s-%s-%s %s" % (ymd[:4], ymd[4:6], ymd[6:8],
                                           t)) if t else \
                    "%s-%s-%s 00:00:00" % (ymd[:4], ymd[4:6], ymd[6:8])
                vol = _f(r.get("VOLUME"))
                amt = _f(r.get("AMOUNT"))
                rows.append({
                    "symbol": pairs[tc], "ts_code": tc,
                    "current": round(price, 4),
                    "percent": (round((price / pre - 1) * 100, 2)
                                if pre else None),
                    "chg": (round(price - pre, 2) if pre else None),
                    "volume": int(vol) if vol is not None else None,
                    "amount": amt,
                    "pe": None, "pb": None,
                    "market_capital": None, "float_market_capital": None,
                    "trade_date": ymd, "timestamp": ts_str,
                })
    covered = {r["ts_code"] for r in rows}
    miss = [c for c in pairs if c not in covered]
    if miss:
        errs.append("未覆盖: %s" % ",".join(miss[:8]))
    return rows, errs


def snapshot_akshare(syms: list[str]) -> tuple[list, list]:
    """akshare 东财全表快照 stock_zh_a_spot_em（备援）→ 快照行口径。

    全表一次请求后过滤目标码（单股盘口端点本机 RemoteDisconnected
    判例，改全表口径）。返回 (rows, errs)；rows 含 pe/pb/市值（东财
    有，补 tushare 新浪源的估值缺口）。
    """
    errs = ["指数锚不入快照片: %s" % s for s in syms if is_index_symbol(s)]
    allowed = [s for s in syms if not is_index_symbol(s)]
    if not allowed:
        return [], errs or ["无请求码"]
    try:
        import akshare
    except ImportError as e:
        return [], errs + ["akshare 未安装: %s" % e]
    with no_proxy_env():
        try:
            df = akshare.stock_zh_a_spot_em()
        except Exception as e:                    # noqa: BLE001
            return [], errs + ["stock_zh_a_spot_em: %s" % str(e)[:80]]
    if df is None or df.empty:
        return [], errs + ["stock_zh_a_spot_em 返回空"]
    want = {X.to_ts_code(s): s for s in allowed if X.to_ts_code(s)}
    want_bare = {tc.split(".")[0]: tc for tc in want}
    now = datetime.now()
    rows = []
    got = set()
    for _, r in df.iterrows():
        code = str(r.get("代码") or "")
        tc = want_bare.get(code)
        if not tc:
            continue
        price = _f(r.get("最新价"))
        if price is None:
            continue
        got.add(tc)
        vol = _f(r.get("成交量"))                 # 手 → 股
        rows.append({
            "symbol": want[tc], "ts_code": tc,
            "current": round(price, 4),
            "percent": _f(r.get("涨跌幅")), "chg": _f(r.get("涨跌额")),
            "volume": int(vol * 100) if vol is not None else None,
            "amount": _f(r.get("成交额")),        # 元
            "pe": _f(r.get("市盈率-动态")), "pb": _f(r.get("市净率")),
            "market_capital": _f(r.get("总市值")),
            "float_market_capital": _f(r.get("流通市值")),
            "trade_date": now.strftime("%Y%m%d"),
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
    miss = [tc for tc in want if tc not in got]
    if miss:
        errs.append("未覆盖: %s" % ",".join(miss[:8]))
    return rows, errs


def fetch_snapshot(syms: list[str]) -> tuple[list, str, list]:
    """快照路由：tushare → akshare，两源全挂返回空行+聚合原因。

    Returns:
        (rows, source, errs)：rows 非空时 source ∈ {"tushare",
        "akshare"}；两源全挂 rows=[]、source=""、errs 聚合两源原因
        （调用方 fail-visible，绝不静默空）。
    """
    errs: list[str] = ["指数锚不入快照片: %s" % s for s in syms
                       if is_index_symbol(s)]
    allowed = [s for s in syms if not is_index_symbol(s)]
    if not allowed:
        return [], "", errs or ["无请求码"]
    try:
        rows, e1 = snapshot_tushare(allowed)
        errs += ["tushare: %s" % x for x in e1]
        if rows:
            return rows, "tushare", errs
    except Exception as e:                        # noqa: BLE001
        errs.append("tushare crash: %s" % str(e)[:80])
    try:
        rows, e2 = snapshot_akshare(allowed)
        errs += ["akshare: %s" % x for x in e2]
        if rows:
            return rows, "akshare", errs
    except Exception as e:                        # noqa: BLE001
        errs.append("akshare crash: %s" % str(e)[:80])
    return [], "", errs


# ── 日线（xq_kline_d 口径）───────────────────────────────────
def daily_tushare(sym: str, days: int = 300, ts_mod=None,
                  pro=None) -> tuple[list, str]:
    """单码日线：pro_bar(qfq) 优先，失败落 pro.daily（不复权）。

    Returns:
        (rows, adj)：adj ∈ {"qfq", "raw"}（行级复权口径标记）。
        vol 手→股、amount 千元→元（对齐雪球 kline 口径）。
    Raises:
        RuntimeError: 两路均失败 / 空返回 / 无 ts_code 映射。
    """
    if is_index_symbol(sym):
        raise RuntimeError("%s 指数锚不入日线管道" % sym)
    ts_code = X.to_ts_code(sym)
    if not ts_code:
        raise RuntimeError("%s 无 ts_code 映射（BJ/非沪深）" % sym)
    if ts_mod is None or pro is None:
        ts_mod, pro = setup_tushare()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days * 1.7) + 30)
             ).strftime("%Y%m%d")
    adj, df = "qfq", None
    with no_proxy_env():
        try:
            df = ts_mod.pro_bar(ts_code=ts_code, api=pro, adj="qfq",
                                start_date=start, end_date=end)
        except Exception:                         # noqa: BLE001
            df = None
        if df is None or df.empty:                # qfq 不可得 → 不复权
            df = pro.daily(ts_code=ts_code, start_date=start,
                           end_date=end)
            adj = "raw"
    if df is None or df.empty:
        raise RuntimeError("tushare daily 空返回")
    df = df.sort_values("trade_date").tail(int(days))
    rows = []
    for _, r in df.iterrows():
        td = str(r.get("trade_date") or "")
        if not (td.isdigit() and len(td) == 8):
            continue
        vol, amt = _f(r.get("vol")), _f(r.get("amount"))
        rows.append({
            "symbol": sym, "ts_code": ts_code,
            "timestamp": "%s-%s-%s 00:00:00"
                         % (td[:4], td[4:6], td[6:8]),
            "volume": int(vol * 100) if vol is not None else None,
            "open": _f(r.get("open")), "high": _f(r.get("high")),
            "low": _f(r.get("low")), "close": _f(r.get("close")),
            "chg": _f(r.get("change")), "percent": _f(r.get("pct_chg")),
            "turnoverrate": None,
            "amount": amt * 1000 if amt is not None else None,
            "trade_date": td,
        })
    if not rows:
        raise RuntimeError("tushare daily 行解析为空")
    return rows, adj


def daily_akshare(sym: str, days: int = 300) -> list:
    """akshare 新浪日K stock_zh_a_daily(qfq)（备援）→ kline 行口径。

    volume=股、amount=元 直用（新浪口径）；chg/percent 由相邻收盘
    自算。失败 raise（调用方记 errs）。
    """
    if is_index_symbol(sym):
        raise RuntimeError("%s 指数锚不入日线管道" % sym)
    try:
        import akshare
    except ImportError as e:
        raise RuntimeError("akshare 未安装: %s" % e) from e
    with no_proxy_env():
        df = akshare.stock_zh_a_daily(symbol=sym.lower(), adjust="qfq")
    if df is None or df.empty:
        raise RuntimeError("stock_zh_a_daily 返回空")
    df = df.tail(int(days))
    rows, prev = [], None
    for _, r in df.iterrows():
        d = getattr(r.get("date"), "date", lambda: r.get("date"))()
        d = str(d)[:10]
        if len(d) != 10:
            continue
        close = _f(r.get("close"))
        rows.append({
            "symbol": sym, "ts_code": X.to_ts_code(sym),
            "timestamp": "%s 00:00:00" % d,
            "volume": (int(_f(r.get("volume")))
                       if _f(r.get("volume")) is not None else None),
            "open": _f(r.get("open")), "high": _f(r.get("high")),
            "low": _f(r.get("low")), "close": close,
            "chg": (round(close - prev, 2)
                    if (prev and close is not None) else None),
            "percent": (round((close / prev - 1) * 100, 2)
                        if (prev and close) else None),
            "turnoverrate": None,
            "amount": _f(r.get("amount")),
            "trade_date": d.replace("-", ""),
        })
        prev = close
    if not rows:
        raise RuntimeError("akshare daily 行解析为空")
    return rows


def fetch_daily(syms: list[str], days: int = 300) -> tuple[dict, str, list]:
    """日线路由：逐码 tushare → akshare，返回逐码行集与来源摘要。

    Returns:
        (by_sym, note, errs)：by_sym={xq_symbol: rows}（成功码）；
        note 形如 "tushare_qfq×58,tushare_raw×2"（来源×码数，manifest
        note 用）；errs 聚合失败码原因。两源全挂 by_sym={} 且 errs
        非空（调用方 fail-visible）。
    """
    by_sym: dict = {}
    srcs: list[str] = []
    errs: list[str] = []
    ts_mod = pro = None
    inited = False
    for sym in syms:
        if is_index_symbol(sym):
            errs.append("%s 指数锚不入日线管道" % sym)
            continue
        try:
            if not inited:
                ts_mod, pro = setup_tushare()
                inited = True
            rows, adj = daily_tushare(sym, days, ts_mod, pro)
            by_sym[sym] = rows
            srcs.append("tushare_%s" % adj)
            continue
        except Exception as e:                    # noqa: BLE001
            errs.append("%s tushare: %s" % (sym, str(e)[:60]))
        try:
            by_sym[sym] = daily_akshare(sym, days)
            srcs.append("akshare_qfq")
        except Exception as e:                    # noqa: BLE001
            errs.append("%s akshare: %s" % (sym, str(e)[:60]))
    if srcs:
        cnt: dict = {}
        for s in srcs:
            cnt[s] = cnt.get(s, 0) + 1
        note = ",".join("%s×%d" % kv for kv in sorted(cnt.items()))
    else:
        note = ""
    if not by_sym and not errs:
        errs.append("无请求码")
    return by_sym, note, errs
