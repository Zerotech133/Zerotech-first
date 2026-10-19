# -*- coding: utf-8 -*-
"""雪球 L1 主力同步器（pysnowball 0.1.8）+ L2 补充批占位（T05 暂缓）。

设计依据：xueqiu-collector-design-2026-10-16.md §2.3。
pysnowball 0.1.8 实测口径（utls.py 源码核验，非 DataFrame）：
  · 所有接口返回 json.loads 的完整 dict：{"error_code":0, "data":{…}}；
  · quotec(symbols) 批量免 token → data.items[]；
  · kline(symbol, period='day', count=-N) 前复权固定 type=before
    （api_ref.kline URL 内写死）→ data.item[] + data.column[]；
  · capital_flow(symbol) → data.items[]（近 90 日序列）；
  · northbound_shareholding_sh/sz(date) 是 HKEX 官网爬取（bs4 解析），
    返回 list[dict]（code/name/shareholding/shareholding_percent），
    非雪球 API——本批独立 try/except，失败记 manifest 不阻塞；
  · earningforecast(symbol) → data.items[]。

落库（全部 HERE/data/xueqiu/ 自包含）：
  xq_quote_snap/d_YYYYMMDD.parquet     收盘快照（quotec 免 token）
  xq_kline_d/<ts_code>.parquet         前复权日线（逐码增量）
  xq_capital_flow/d_YYYYMMDD.parquet   资金流（逐码→按日汇总）
  xq_northbound/d_YYYYMMDD.parquet     北向持股（空日不落片）
  xq_earningforecast/<ts_code>.parquet 盈利预测（逐码，周频）

降级矩阵（设计 §4.1）：缺 token → quotec 批照跑，token 批
skip_no_token，rc=0（降级≠失败）。probe 失败 → 全批 degraded 退出 rc=0。

用法：
  python xueqiu_sync.py --batch all      # snap+kline+flow+north
  python xueqiu_sync.py --batch snap
  python xueqiu_sync.py --batch kline --codes SH600519,SZ000338
  python xueqiu_sync.py --status
"""
import argparse
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X


# ── 目录与路径工具 ───────────────────────────────────────────
def _tbl_dir(tbl: str) -> str:
    d = os.path.join(X.OUT_DIR, tbl)
    os.makedirs(d, exist_ok=True)
    return d


def _date_path(tbl: str, ymd: str) -> str:
    return os.path.join(_tbl_dir(tbl), "d_%s.parquet" % ymd)


def _code_path(tbl: str, ts_code: str) -> str:
    return os.path.join(_tbl_dir(tbl), "%s.parquet" % ts_code)


def _snow_df(j: dict, items_key: str = "items") -> pd.DataFrame:
    """pysnowball 完整 JSON → data.<items_key> DataFrame（空安全）。

    BUG-2 修复（QA Round-1）：雪球 token 失效常态是 HTTP 200 +
    error_code=400016（utls.fetch 只对 HTTP≠200 raise，不拦业务错码）。
    此处必须 raise 让上层 except 归入 fail_codes → manifest partial/
    failed，绝不落空片伪成功（设计 §4.1"400016 ⇒ 提醒换 token"）。
    """
    if not isinstance(j, dict):
        return pd.DataFrame()
    err = j.get("error_code")
    if err not in (None, 0):
        raise RuntimeError("snowball error_code=%s %s"
                           % (err, str(j.get("error_description"))[:60]))
    data = j.get("data") or {}
    items = data.get(items_key) if isinstance(data, dict) else None
    return pd.DataFrame(items) if items else pd.DataFrame()


# ── token 注入链 ─────────────────────────────────────────────
def _set_token(token: str) -> None:
    """pysnowball set_token（内部写 XUEQIUTOKEN）；异常时手动写 env
    兜底——sync_* 的 skip_no_token 检查读环境变量，绝不能因此丢。"""
    import os
    try:
        import pysnowball as ball
        ball.set_token(token)
    except Exception as e:
        print("[init_ball] set_token 异常（env 兜底）：%s" % str(e)[:60])
    finally:
        os.environ["XUEQIUTOKEN"] = token


def init_ball(mf: X.XqManifest) -> dict:
    """token 池优先（P1-4）→ 三级链兜底 → set_token → quotec_probe()
    → token_probe()（失效自动池内换号，上限 4 次）。

    返回 {token, probe, token_probe}；probe 结果写 manifest['health']。
    token 缺失：打印警告（不 raise），token 批由各 sync_* 统一
    skip_no_token，token_probe 无需探测（肯定无效）。
    """
    # 2026-10-17 判例：雪球 kline 要求完整 cookie 集，pysnowball 纯
    # token header 返回空 items——先挂完整 cookie 补丁（幂等/降级安全）
    try:
        from xq_cookie_patch import patch_pysnowball_cookies
        patch_pysnowball_cookies()
    except Exception:
        pass
    # P1-4：token 池（config/xq_tokens.json）优先；未启用/全冷却降级
    # 三级链（L1 .env.local → L2 浏览器 → L3 空），零配置行为不变。
    pool = None
    cur = None                             # 池内 TokenEntry（三级链为 None）
    token, tok_name = "", ""
    try:
        from xq_tokens import get_pool
        pool = get_pool()
        if pool.enabled():
            e = pool.pick()
            if e is not None:
                cur = e
                token, tok_name = e.token, e.name
                pool.mark_used(e)
                pool.save()
            mf.mark_health("token_pool", "%d entries" % len(pool.entries))
    except Exception:
        pool = None                         # 池模块任何异常 → 三级链
    if not token:
        token = X.load_token()
    if token:
        _set_token(token)
    else:
        print("[init_ball] XQ_A_TOKEN 未配置：token 批将 skip_no_token"
              "（quotec 快照批免 token 照跑）")
    probe = X.quotec_probe("SH000001")
    mf.mark_health("quotec_probe", probe)
    mf.mark_health("token_present", bool(token))
    mf.mark_health("token_name", tok_name or "chain")
    print("[init_ball] quotec_probe ok=%s latency=%dms err=%s"
          % (probe["ok"], probe["latency_ms"], probe["err"] or "-"))
    # P1-5 登录态预检 + P1-4 池内换号循环：两级探测矩阵第二级
    # （quotec=网络可达性，token_probe=登录态）。ok=True 且明确失效
    # （400016 类）时：池 mark_fail 冷却并换下一条重试；三级链单
    # token 无替代者、网络故障（ok=False）token 未知——均不换。
    tp: dict = {}
    if token and probe["ok"]:
        for _ in range(4):                     # 换号上限（池容量兜底）
            X.jitter_sleep()
            tp = X.token_probe("SH600519")
            mf.mark_health("token_probe", tp)
            mf.mark_health("token_checked_at",
                           datetime.now().isoformat(timespec="seconds"))
            print("[init_ball] token_probe ok=%s valid=%s err=%s"
                  % (tp["ok"], tp["valid"], tp["err"] or "-"))
            if not tp["ok"]:
                break                          # 网络故障：token 未知
            if tp["valid"]:
                if pool is not None and cur is not None:
                    pool.mark_ok(cur)
                    pool.save()
                break                          # token 有效
            # 明确失效：池换号 / 三级链无替代者退出
            if pool is not None and cur is not None:
                pool.mark_fail(cur)
                pool.save()
                nxt = pool.pick()
                if nxt is None:
                    break                      # 池尽：仍失效 → 跳批路径
                cur = nxt
                token, tok_name = nxt.token, nxt.name
                pool.mark_used(cur)
                pool.save()
                _set_token(token)
                mf.mark_health("token_name", tok_name)
                print("[init_ball] token 失效换号 → %s" % tok_name)
                continue
            break                              # 三级链单 token：无替代
    return {"token": token, "probe": probe, "token_probe": tp}


# ── 快照批（免 token）────────────────────────────────────────
SNAP_COLS = ["symbol", "ts_code", "current", "percent", "chg", "volume",
             "amount", "pe", "pb", "market_capital", "float_market_capital",
             "trade_date", "timestamp"]


def sync_quote_snap(codes: list[str], ymd: str,
                    mf: X.XqManifest | None = None) -> int:
    """quotec 分批(≤50/次)收盘快照→xq_quote_snap/d_YYYYMMDD.parquet。

    免 token。返回落库行数；全批失败/空返回 0（manifest 记 failed/empty）。
    """
    mf = mf or X.XqManifest()
    syms = [c for c in (X.norm_symbol(c) for c in codes) if c]
    if not syms:
        mf.mark("xq_quote_snap", done=False, rows=0, status="empty",
                note="无代码（池空或 --codes 未给）")
        return 0
    try:
        import pysnowball as ball
    except ModuleNotFoundError:
        mf.mark("xq_quote_snap", done=False, rows=0, status="failed",
                note="pysnowball 未安装")
        return 0
    # 指数基准 SH000001 恒入快照（健康对账锚点，设计 §3.1）
    if "SH000001" not in syms:
        syms.insert(0, "SH000001")
    rows: list[dict] = []
    tripped = False
    for i in range(0, len(syms), X.QUOTEC_BATCH):
        batch = syms[i:i + X.QUOTEC_BATCH]
        try:
            j = ball.quotec(",".join(batch))
            df = _snow_df(j)
            if df.empty:
                # 接口真空（批次内全指数/无权限标的等）：跳过该批不落行
                continue
            rows.extend(_norm_snap(df))
        except Exception as e:
            # 含 error_code 业务错（_snow_df raise，QA Round-1 BUG-2 同源）
            if X.looks_ip_blocked(e):    # P0-1：403 拉黑→熔断停轮
                X.ip_block_trip(mf)
                tripped = True
                print("[ALERT][ip_blocked] quotec 403 熔断：%s → 冷却 %d 分钟"
                      % (str(e)[:80], X.IP_BLOCK_COOLDOWN_MIN))
                break
            print("  quotec 批 %d-%d 失败：%s"
                  % (i, i + len(batch), str(e)[:80]))
        if i + X.QUOTEC_BATCH < len(syms):
            X.jitter_sleep()
    if not rows:
        mf.mark("xq_quote_snap", done=False, rows=0,
                status="ip_blocked" if tripped else "failed",
                note=("quotec 403 熔断停轮" if tripped
                      else "quotec 全批失败/空"))
        mf.save()
        return 0
    df = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "timestamp"]).sort_values("symbol").reset_index(
        drop=True)
    path = _date_path("xq_quote_snap", ymd)
    X.atomic_parquet(df, path)
    mf.mark("xq_quote_snap", done=True, rows=len(df),
            file=os.path.basename(path),
            status="partial" if tripped else "ok",
            note="403 熔断部分批未采" if tripped else "")
    mf.save()
    print("  xq_quote_snap: %d 行 → %s" % (len(df), os.path.basename(path)))
    return len(df)


def _norm_snap(df: pd.DataFrame) -> list[dict]:
    """quotec data.items → SNAP_COLS 口径（ms→本地时间串）。"""
    out = []
    for _, r in df.iterrows():
        sym = str(r.get("symbol") or "").upper()
        ts_code = X.to_ts_code(sym)
        if not ts_code:
            continue                        # 指数/非 A 股不入快照表
        ts = X.ms_to_dt(r.get("timestamp"))
        out.append({
            "symbol": sym, "ts_code": ts_code,
            "current": r.get("current"), "percent": r.get("percent"),
            "chg": r.get("chg"), "volume": r.get("volume"),
            "amount": r.get("amount"), "pe": r.get("pe"), "pb": r.get("pb"),
            "market_capital": r.get("market_capital"),
            "float_market_capital": r.get("float_market_capital"),
            "trade_date": X.ms_to_ymd(r.get("timestamp")) or
            datetime.now().strftime("%Y%m%d"),
            "timestamp": ts,
        })
    return out


# ── 日线批（token，逐码增量）─────────────────────────────────
def _kline_status(tripped: bool, has_success: bool, has_bad: bool) -> str:
    """判断日线批状态：零成功必败，403 全败独立标记熔断。"""
    if tripped:
        return "partial" if has_success else "ip_blocked"
    if not has_success:
        return "failed"
    return "partial" if has_bad else "ok"


def sync_kline(codes: list[str], max_symbols: int = X.MAX_SYMBOLS,
               mf: X.XqManifest | None = None) -> int:
    """kline(period=day, 前复权 type=before 固定) 增量→xq_kline_d/<ts_code>.parquet。

    断点=文件存在则合并去重续拉（同 timestamp 后写覆盖）；返回累计行数。
    count=-284 取近一年余量，增量合并天然幂等。
    """
    from xq_quote_router import is_index_symbol

    mf = mf or X.XqManifest()
    syms = [s for s in (X.norm_symbol(c) for c in codes)
            if s and not is_index_symbol(s)]
    if len(syms) > max_symbols:
        print("  kline: %d 码超上限 %d，截断（设计 §4.4）"
              % (len(syms), max_symbols))
        syms = syms[:max_symbols]
    if not syms:
        mf.mark("xq_kline_d", done=False, rows=0, status="empty",
                note="无代码")
        return 0
    if not os.environ.get("XUEQIUTOKEN", ""):
        mf.mark("xq_kline_d", done=False, rows=0, status="skip_no_token",
                note="XQ_A_TOKEN 缺失，降级 skip")
        mf.save()
        print("  xq_kline_d: skip_no_token")
        return 0
    try:
        import pysnowball as ball
    except ModuleNotFoundError:
        mf.mark("xq_kline_d", done=False, rows=0, status="failed",
                note="pysnowball 未安装")
        return 0
    total, ok_codes, fail_codes, empty_codes = 0, [], [], []
    tripped = False
    alt_note = ""          # T4：替代源兜底接管说明（manifest note 可见）
    router_errors: list[str] = []
    for i, sym in enumerate(syms, 1):
        # BUG-3 修复（QA Round-1）：BJ 等无 ts_code 映射的符号不落库
        # （否则 _code_path 生成空名 ".parquet"）；归入 fail_codes 可见
        ts_code = X.to_ts_code(sym)
        if not ts_code:
            fail_codes.append(sym)
            print("  kline %s 失败：无 ts_code 映射（BJ/非沪深），跳过"
                  % sym)
            continue
        try:
            # BUG-1 修复（QA Round-1）：api_ref.kline URL 模板自带负号
            # （"count=-{}"），count 传正数 284 才拼出 count=-284
            j = ball.kline(sym, period="day", count=284)
            rows = _norm_kline(j, sym)
            if not rows:
                # 旧片存在也不能让本轮空回包伪装成功或触发重写。
                empty_codes.append(sym)
                print("  kline %s 空返回，不落片" % sym)
                continue
            path = _code_path("xq_kline_d", ts_code)
            old = (pd.read_parquet(path) if os.path.exists(path)
                   else pd.DataFrame())
            merged = _merge_kline(old, rows)
            if merged.empty:
                # 非空新行却合并为空：仍不可写空片或误报成功。
                empty_codes.append(sym)
                print("  kline %s 空返回，不落片" % sym)
                continue
            X.atomic_parquet(merged, path)
            total += len(merged)
            ok_codes.append(sym)
        except Exception as e:
            if X.looks_ip_blocked(e):    # P0-1：403 拉黑→熔断停轮
                X.ip_block_trip(mf)
                tripped = True
                print("[ALERT][ip_blocked] kline 403 熔断：%s → 冷却 "
                      "%d 分钟，剩余 %d 码跳过"
                      % (str(e)[:80], X.IP_BLOCK_COOLDOWN_MIN,
                         len(syms) - i))
                break
            # note 携带错误摘要（QA Round-1 BUG-2 回归用例：manifest 直接
            # 可见 400016 等错码，运维不翻日志即可定位"token 失效"）
            fail_codes.append("%s(%s)" % (sym, str(e)[:60]))
            print("  kline %s 失败：%s" % (sym, str(e)[:70]))
        if i < len(syms):
            X.jitter_sleep()
    if not ok_codes:
        # T4（2026-10-19）：雪球侧零成功（403 拉黑/接口挂/全码失败）
        # → 替代源日线兜底（tushare 主源 → akshare 备援，逐码落片，
        #   行口径与 _norm_kline 逐列对齐；token 缺失 skip 分支不在此列，
        #   该分支属配置缺失语义，保持 skip_no_token 不混路由）
        try:
            from xq_quote_router import fetch_daily
            by_sym, rnote, rerrs = fetch_daily(syms, days=284)
        except Exception as e:                    # noqa: BLE001
            by_sym, rnote, rerrs = {}, "", [
                "router crash: %s" % str(e)[:80]]
        router_errors = [str(err)[:80] for err in rerrs[:3]]
        requested = set(syms)
        for sym, krows in by_sym.items():
            # 二次校验路由回包：请求外键、指数、串码行绝不可参与旧片合并。
            if sym not in requested or is_index_symbol(sym):
                continue
            ts_code = X.to_ts_code(sym)
            if not ts_code or not isinstance(krows, (list, tuple)):
                continue
            safe_rows = [row for row in krows if isinstance(row, dict)
                         and row.get("symbol") == sym
                         and not is_index_symbol(row.get("symbol"))
                         and ("ts_code" not in row
                              or row["ts_code"] == ts_code)]
            if not safe_rows:
                continue                    # 不拿旧片充作本轮成功
            path = _code_path("xq_kline_d", ts_code)
            old = (pd.read_parquet(path) if os.path.exists(path)
                   else pd.DataFrame())
            merged = _merge_kline(old, safe_rows)
            if merged.empty:
                continue
            X.atomic_parquet(merged, path)
            total += len(merged)
            ok_codes.append(sym)
        if ok_codes:
            alt_note = ("替代源兜底(%s): %d/%d码成功"
                        % (rnote, len(ok_codes), len(syms)))
            print("  xq_kline_d: %s" % alt_note)
        else:
            if not router_errors:
                router_errors.append("替代源无有效请求股票日线行")
            print("  xq_kline_d: 替代源亦挂: %s" % "; ".join(router_errors))
    bad = fail_codes + empty_codes
    # 状态分级（设计 §7.7 如实记录）：全败=failed，部分败=partial；
    # 熔断=ip_blocked（P0-1：与 failed 区分，冷却自愈语义）
    status = _kline_status(tripped, bool(ok_codes), bool(bad))
    note_parts = []
    if alt_note:
        note_parts.append(alt_note)
    if router_errors:
        note_parts.append("替代源警告: %s" % "; ".join(router_errors)
                          if ok_codes else
                          "替代源失败: %s" % "; ".join(router_errors))
    if fail_codes:
        note_parts.append("%d codes skipped: %s"
                          % (len(fail_codes), ",".join(fail_codes[:8])))
    if empty_codes:
        note_parts.append("%d codes empty: %s"
                          % (len(empty_codes), ",".join(empty_codes[:8])))
    mf.mark("xq_kline_d", done=bool(ok_codes), rows=total,
            status=status, note="; ".join(note_parts))
    mf.save()
    print("  xq_kline_d: %d/%d 码成功，累计 %d 行"
          % (len(ok_codes), len(syms), total))
    return total


def _norm_kline(j: dict, sym: str) -> list[dict]:
    """kline JSON（data.item 数组行 + data.column 列名）→ 标准行。

    雪球 kline 返回 data.column=['timestamp','volume','open','high',
    'low','close','chg','percent','turnoverrate','amount',…] 与
    data.item=[[v1,v2,…],…]（按 column 序对齐）。
    """
    if not isinstance(j, dict):
        return []
    err = j.get("error_code")
    if err not in (None, 0):
        # BUG-2 修复（QA Round-1）：直读原始 JSON 的路径同样拦业务错码
        # （400016 token 失效 → raise → fail_codes，绝不伪成功）
        raise RuntimeError("snowball error_code=%s %s"
                           % (err, str(j.get("error_description"))[:60]))
    data = j.get("data") or {}
    cols = data.get("column") or []
    items = data.get("item") or []
    if not cols or not items:
        return []
    ts_code = X.to_ts_code(sym)
    out = []
    for arr in items:
        if not isinstance(arr, (list, tuple)) or len(arr) < len(cols):
            continue
        r = dict(zip(cols, arr))
        ts = X.ms_to_dt(r.get("timestamp"))
        if not ts:
            continue
        out.append({
            "symbol": sym, "ts_code": ts_code, "timestamp": ts,
            "volume": r.get("volume"), "open": r.get("open"),
            "high": r.get("high"), "low": r.get("low"),
            "close": r.get("close"), "chg": r.get("chg"),
            "percent": r.get("percent"),
            "turnoverrate": r.get("turnoverrate"),
            "amount": r.get("amount"),
            "trade_date": ts[:10].replace("-", ""),
        })
    return out


def _merge_kline(old: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    """旧片+新行按 (symbol, timestamp) 去重合并（后写覆盖）。"""
    new = pd.DataFrame(rows)
    if old is None or old.empty:
        return new
    return (pd.concat([old, new], ignore_index=True)
            .drop_duplicates(subset=["symbol", "timestamp"], keep="last")
            .sort_values("timestamp").reset_index(drop=True))


# ── 赢利预测批（token，逐码）────────────────────────────────
def sync_earningforecast(codes: list[str],
                         mf: X.XqManifest | None = None) -> int:
    """earningforecast→xq_earningforecast/<ts_code>.parquet（周频增量）。"""
    mf = mf or X.XqManifest()
    syms = [s for s in (X.norm_symbol(c) for c in codes) if s]
    if not syms:
        mf.mark("xq_earningforecast", done=False, rows=0, status="empty",
                note="无代码")
        return 0
    if not os.environ.get("XUEQIUTOKEN", ""):
        mf.mark("xq_earningforecast", done=False, rows=0,
                status="skip_no_token", note="XQ_A_TOKEN 缺失")
        mf.save()
        print("  xq_earningforecast: skip_no_token")
        return 0
    try:
        import pysnowball as ball
    except ModuleNotFoundError:
        mf.mark("xq_earningforecast", done=False, rows=0, status="failed",
                note="pysnowball 未安装")
        return 0
    syms = syms[:X.MAX_SYMBOLS]
    total, ok_codes, fail_codes, empty_codes = 0, [], [], []
    tripped = False
    for i, sym in enumerate(syms, 1):
        ts_code = X.to_ts_code(sym)
        if not ts_code:                      # BUG-3：BJ 等无映射不落库
            fail_codes.append(sym)
            print("  forecast %s 失败：无 ts_code 映射，跳过" % sym)
            continue
        try:
            j = ball.earningforecast(sym)
            rows = _norm_forecast(_snow_df(j), sym)
            path = _code_path("xq_earningforecast", ts_code)
            old = (pd.read_parquet(path) if os.path.exists(path)
                   else pd.DataFrame())
            new = pd.DataFrame(rows)
            merged = (pd.concat([old, new], ignore_index=True)
                      .drop_duplicates(subset=["symbol", "ann_date",
                                               "broker"], keep="last")
                      .sort_values("ann_date").reset_index(drop=True)
                      ) if not old.empty else new
            if merged.empty:
                # BUG-2 修复：空数据不落空片（伪成功防线）
                empty_codes.append(sym)
                continue
            X.atomic_parquet(merged, path)
            total += len(merged)
            ok_codes.append(sym)
        except Exception as e:
            if X.looks_ip_blocked(e):    # P0-1：403 拉黑→熔断停轮
                X.ip_block_trip(mf)
                tripped = True
                print("[ALERT][ip_blocked] forecast 403 熔断：%s → 冷却 "
                      "%d 分钟，剩余 %d 码跳过"
                      % (str(e)[:80], X.IP_BLOCK_COOLDOWN_MIN,
                         len(syms) - i))
                break
            fail_codes.append(sym)
            print("  forecast %s 失败：%s" % (sym, str(e)[:70]))
        if i < len(syms):
            X.jitter_sleep()
    bad = fail_codes + empty_codes
    # BUG-2 修复：异常码（400016 token 失效等）不再被误标 ok/empty；
    # 熔断=ip_blocked（P0-1）
    status = ("ip_blocked" if tripped and not ok_codes
              else "partial" if tripped
              else "failed" if not ok_codes and fail_codes
              else "ok" if not bad else "partial")
    note = "; ".join(filter(None, [
        "%d codes skipped: %s" % (len(fail_codes),
                                  ",".join(fail_codes[:8]))
        if fail_codes else "",
        "%d codes empty: %s" % (len(empty_codes),
                                ",".join(empty_codes[:8]))
        if empty_codes else ""]))
    mf.mark("xq_earningforecast", done=bool(ok_codes), rows=total,
            status=status, note=note)
    mf.save()
    print("  xq_earningforecast: %d/%d 码，累计 %d 行"
          % (len(ok_codes), len(syms), total))
    return total


def _norm_forecast(df: pd.DataFrame, sym: str) -> list[dict]:
    """盈利预测 data.items → 标准行（ann_date 统一 YYYYMMDD）。"""
    out = []
    ts_code = X.to_ts_code(sym)
    for _, r in df.iterrows():
        ann = str(r.get("created_at") or r.get("ann_date") or "")[:10]
        if ann[:4].isdigit():
            ann = ann.replace("-", "")
        out.append({
            "symbol": sym, "ts_code": ts_code, "ann_date": ann,
            "broker": r.get("org_name") or r.get("broker") or "",
            "target_price": r.get("target_price"),
            "eps_next_year": r.get("eps_next_year"),
            "eps_this_year": r.get("eps_this_year"),
            "pe_next_year": r.get("pe_next_year"),
        })
    return out


# ── 资金流批（token，逐码→按日落片）──────────────────────────
def sync_capital_flow(codes: list[str], ymd: str,
                      mf: X.XqManifest | None = None) -> int:
    """capital_flow→xq_capital_flow/d_YYYYMMDD.parquet（主力/散户净流入）。

    data.items 为近 90 日日频序列，取最新一行落当日片。
    """
    mf = mf or X.XqManifest()
    syms = [s for s in (X.norm_symbol(c) for c in codes) if s]
    if not syms:
        mf.mark("xq_capital_flow", done=False, rows=0, status="empty",
                note="无代码")
        return 0
    if not os.environ.get("XUEQIUTOKEN", ""):
        mf.mark("xq_capital_flow", done=False, rows=0,
                status="skip_no_token", note="XQ_A_TOKEN 缺失")
        mf.save()
        print("  xq_capital_flow: skip_no_token")
        return 0
    try:
        import pysnowball as ball
    except ModuleNotFoundError:
        mf.mark("xq_capital_flow", done=False, rows=0, status="failed",
                note="pysnowball 未安装")
        return 0
    rows: list[dict] = []
    fail_syms: list[str] = []                # BUG-2：区分失败与真空
    syms = syms[:X.MAX_SYMBOLS]
    tripped = False
    for i, sym in enumerate(syms, 1):
        try:
            j = ball.capital_flow(sym)
            rows.extend(_pick_last_flow(_snow_df(j), sym, ymd))
        except Exception as e:
            if X.looks_ip_blocked(e):    # P0-1：403 拉黑→熔断停轮
                X.ip_block_trip(mf)
                tripped = True
                print("[ALERT][ip_blocked] flow 403 熔断：%s → 冷却 "
                      "%d 分钟，剩余 %d 码跳过"
                      % (str(e)[:80], X.IP_BLOCK_COOLDOWN_MIN,
                         len(syms) - i))
                break
            fail_syms.append(sym)
            print("  flow %s 失败：%s" % (sym, str(e)[:70]))
        if i < len(syms):
            X.jitter_sleep()
    if not rows:
        # BUG-2 修复：全部失败（如 400016 token 失效）≠ 口径停发的真空，
        # 分别记 failed / empty，不再误导诊断；熔断=ip_blocked（P0-1）
        if tripped:
            mf.mark("xq_capital_flow", done=False, rows=0,
                    status="ip_blocked",
                    note="403 熔断停轮（%d 码已采列表，冷却 %d 分钟）"
                         % (len(fail_syms), X.IP_BLOCK_COOLDOWN_MIN))
        elif fail_syms and len(fail_syms) == len(syms):
            mf.mark("xq_capital_flow", done=False, rows=0, status="failed",
                    note="全批失败（%d 码，含 error_code 异常，检查 "
                         "XQ_A_TOKEN）" % len(fail_syms))
        else:
            mf.mark("xq_capital_flow", done=False, rows=0, status="empty",
                    note="全批空返回（接口变更或无数据）")
        mf.save()
        return 0
    df = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "timestamp"]).sort_values("symbol").reset_index(
        drop=True)
    path = _date_path("xq_capital_flow", ymd)
    X.atomic_parquet(df, path)
    mf.mark("xq_capital_flow", done=True, rows=len(df),
            file=os.path.basename(path),
            status="partial" if tripped else "ok",
            note="403 熔断部分码未采" if tripped else "")
    mf.save()
    print("  xq_capital_flow: %d 行" % len(df))
    return len(df)


def _pick_last_flow(df: pd.DataFrame, sym: str, ymd: str) -> list[dict]:
    """资金流近 90 日序列 → 取最新一行落当日片。"""
    if df.empty:
        return []
    r = df.iloc[-1]
    ts = X.ms_to_dt(r.get("timestamp"))
    return [{
        "symbol": sym, "ts_code": X.to_ts_code(sym),
        "timestamp": ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": (ts[:10].replace("-", "") if ts else ymd),
        "main_net_inflow": r.get("main_net_inflow"),
        "main_net_inflow_rate": r.get("main_net_inflow_rate"),
        "retail_net_inflow": r.get("retail_net_inflow"),
    }]


# ── 北向批（token，HKEX 官网爬取）────────────────────────────
def sync_northbound(ymd: str, mf: X.XqManifest | None = None) -> int:
    """northbound_shareholding_sh/sz→xq_northbound/d_YYYYMMDD.parquet。

    pysnowball 实测口径：两函数爬 HKEX 官网（BeautifulSoup 解析 HTML 表），
    返回 list[dict]（code/name/shareholding/shareholding_percent），
    code 为 5 位 HKEX 编码（tran_code 才转 SZ/SH 符号）。
    空返回（口径停发，2024-08 后）不落片、manifest 记 empty、rc 不受
    影响（设计 R4 对策）。当日片已存在则跳过（幂等防重爬）。
    """
    mf = mf or X.XqManifest()
    path = _date_path("xq_northbound", ymd)
    if os.path.exists(path):
        mf.mark("xq_northbound", done=True, rows=0,
                file=os.path.basename(path), status="ok",
                note="当日片已存在，跳过（checkpoint）")
        mf.save()
        print("  xq_northbound: 当日片已存在，跳过")
        return 0
    try:
        import pysnowball as ball
    except ModuleNotFoundError:
        mf.mark("xq_northbound", done=False, rows=0, status="failed",
                note="pysnowball 未安装")
        return 0
    rows: list[dict] = []
    try:
        for fn_i, fn_name in enumerate(("northbound_shareholding_sh",
                                        "northbound_shareholding_sz")):
            fn = getattr(ball, fn_name, None)
            if fn is None:
                continue
            if fn_i:                         # P0-2：两次 HKEX 抓取间节奏
                X.jitter_sleep()
            lst = fn()                       # list[dict] 或 None（异常吞掉）
            if not lst:
                continue
            for r in lst:
                sym = _hkex_to_symbol(r.get("code"))
                if not sym:
                    continue
                rows.append({
                    "symbol": sym, "ts_code": X.to_ts_code(sym),
                    "trade_date": ymd,
                    "share_number": r.get("shareholding"),
                    "share_ratio": r.get("shareholding_percent"),
                    "hold_market_capital": None,
                })
    except Exception as e:
        mf.mark("xq_northbound", done=False, rows=0, status="failed",
                note=str(e)[:100])
        mf.save()
        print("  xq_northbound 失败：%s" % str(e)[:80])
        return 0
    if not rows:
        mf.mark("xq_northbound", done=False, rows=0, status="empty",
                note="空返回（口径停发），不落片")
        mf.save()
        print("  xq_northbound: empty（不落片）")
        return 0
    out = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "trade_date"]).sort_values("symbol").reset_index(
        drop=True)
    X.atomic_parquet(out, path)
    mf.mark("xq_northbound", done=True, rows=len(out),
            file=os.path.basename(path), status="ok")
    mf.save()
    print("  xq_northbound: %d 行" % len(out))
    return len(out)


# HKEX 5 位编码 → 雪球符号（pysnowball/hkex.tran_code 全段映射，本地
# 实现避免 import 其内部模块；QA Round-1 Minor-1：补 92→SH602/94→SH604
# 两段，对照 hkex.py tran_code 映射表，防未来新增漏采）
_HKEX_MAP = {"70": "SZ000", "71": "SZ001", "72": "SZ002", "73": "SZ003",
             "77": "SZ300", "78": "SZ301", "90": "SH600", "91": "SH601",
             "92": "SH602", "93": "SH603", "94": "SH604", "95": "SH605",
             "30": "SH688"}


def _hkex_to_symbol(code) -> str:
    """HKEX 5 位编码（如 000338→'70'+'00338'）→ SZ000338；非法返回 ""。"""
    s = str(code or "").strip()
    if not s.isdigit() or len(s) != 5:
        return ""
    return _HKEX_MAP.get(s[:2], "") + s[2:]


# ── L2 补充批（akshare，T05 P2——签名先落，实现暂缓）─────────
def sync_ak_hot(ymd: str, mf: X.XqManifest | None = None) -> int:
    """L2：akshare stock_hot_deal_xq→xq_hot_deal/d_*.parquet（T05 实现）。"""
    mf = mf or X.XqManifest()
    mf.mark("xq_hot_deal", done=False, rows=0, status="skip",
            note="T05（P2）暂缓，未实现")
    mf.save()
    print("  xq_hot_deal: skip（T05 P2 暂缓）")
    return 0


def sync_ak_inner_trade(ymd: str, mf: X.XqManifest | None = None) -> int:
    """L2：akshare stock_inner_trade_xq→xq_inner_trade/d_*.parquet（T05）。"""
    mf = mf or X.XqManifest()
    mf.mark("xq_inner_trade", done=False, rows=0, status="skip",
            note="T05（P2）暂缓，未实现")
    mf.save()
    print("  xq_inner_trade: skip（T05 P2 暂缓）")
    return 0


# ── 状态摘要 ─────────────────────────────────────────────────
def status() -> None:
    """打印 manifest 摘要（各表 done/rows/status/last_run + health）。"""
    mf = X.XqManifest()
    data = mf.load()
    print("雪球仓：%s" % X.OUT_DIR)
    if not os.path.isdir(X.OUT_DIR):
        print("  （目录不存在，尚无任何采集）")
        return
    tbls = [k for k in data if k != "health"]
    if not tbls:
        print("  （manifest 空，尚无批次记录）")
    else:
        print("%-20s %-6s %-8s %-14s %-17s %s"
              % ("table", "done", "rows", "status", "last_run", "note"))
        for t in sorted(tbls):
            e = data.get(t) or {}
            print("%-20s %-6s %-8s %-14s %-17s %s"
                  % (t, "Y" if e.get("done") else "-",
                     e.get("rows", 0), e.get("status", "-"),
                     (e.get("last_run") or "—")[:16],
                     (e.get("note") or "")[:40]))
    h = data.get("health") or {}
    probe = h.get("quotec_probe") or {}
    print("health: quotec_probe ok=%s latency=%dms ts=%s token_present=%s"
          % (probe.get("ok"), probe.get("latency_ms", 0),
             probe.get("ts", "—"), h.get("token_present")))


# ── CLI ──────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """argparse CLI。rc：全批失败=1，部分失败/降级/empty=0（不阻塞）。"""
    ap = argparse.ArgumentParser(description="雪球 L1 数据同步器")
    ap.add_argument("--batch", default="all",
                    choices=["snap", "kline", "flow", "north", "forecast",
                             "ak", "all"],
                    help="all=snap+kline+flow+north；ak=T05 暂缓占位")
    ap.add_argument("--codes", default="",
                    help="逗号分隔代码；缺省取缠论池 pool_codes")
    ap.add_argument("--pool-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=X.MAX_SYMBOLS)
    ap.add_argument("--status", action="store_true",
                    help="仅打印 manifest 摘要")
    a = ap.parse_args(argv)
    if a.status:
        status()
        return 0
    mf = X.XqManifest()
    mf.load()
    # P0-1 熔断：冷却期内整轮跳过（连 quotec probe 都不发——probe 也是
    # 雪球请求，撞上只会延长拉黑期）。rc=1 让 wrapper 告警（数据缺口必须可见）。
    blocked = X.ip_blocked_active(mf)
    if blocked:
        print("[ALERT][ip_blocked] 熔断冷却中 %s，本轮全部批次跳过"
              "（勿手动连触采集）" % blocked)
        for tbl in ("xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                    "xq_northbound", "xq_earningforecast"):
            mf.mark(tbl, done=False, rows=0, status="ip_blocked",
                    note="cooldown %s" % blocked)
        mf.save()
        return 1
    # P0-3：出口代理接线——把当前出口写进 HTTP(S)_PROXY，pysnowball
    # 内部 requests（trust_env）与 quotec probe 随之全走代理；空配置
    # 降级直连（并清除继承的系统代理键，避免与本池语义冲突）。
    try:
        from xq_proxy import apply_proxy_env
        pname = apply_proxy_env()
        if pname:
            print("[main] 出口代理: %s" % pname)
    except Exception:
        pass
    # P2-9 单任务容错：init_ball 意外异常（token 池坏文件序列化、
    # env 写入失败等理论外路径）不得拖垮 main——全批 degraded 退出
    # rc=0（与 probe 失败同语义），manifest 必落盘。
    try:
        ctx = init_ball(mf)                  # token 注入 + quotec 探测
    except Exception as e:
        for tbl in ("xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                    "xq_northbound", "xq_earningforecast"):
            mf.mark(tbl, done=False, rows=0, status="degraded",
                    note="init_ball crash: %s" % str(e)[:80])
        mf.save()
        print("[main] init_ball 异常（%s），全批降级退出 rc=0" % str(e)[:80])
        return 0
    if not ctx["probe"]["ok"]:
        # probe 失败=网络/雪球侧故障：全批 degraded 退出（rc=0），
        # 提示手动 L3 接管（设计 §4.2 矩阵第三行）
        for tbl in ("xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                    "xq_northbound"):
            mf.mark(tbl, done=False, rows=0, status="degraded",
                    note="quotec_probe fail: %s" % ctx["probe"]["err"])
        mf.save()
        print("[main] quotec 探测失败（%s），全批降级退出；"
              "可手动 python xueqiu_crawl.py --mode takeover 走 L3 接管"
              % ctx["probe"]["err"])
        return 0
    ymd = datetime.now().strftime("%Y%m%d")
    if a.codes:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    else:
        codes = X.pool_codes(a.pool_days, a.limit)
        print("缠论池模式：近 %d 天活跃股 %d 只（上限 %d）"
              % (a.pool_days, len(codes), a.limit))
    if a.batch == "ak":
        sync_ak_hot(ymd, mf)
        sync_ak_inner_trade(ymd, mf)
        return 0
    names = (["snap", "kline", "flow", "north"] if a.batch == "all"
             else [a.batch])
    # P1-5：token 预检失效（ok=True + valid=False = 明确 400016 类业务
    # 错码）→ 跳过全部 token 依赖批（kline/flow/north/forecast），免
    # token 的 snap 照跑——避免 N 码逐个失败浪费请求与节奏时间。
    # ok=False（网络故障）不跳批：token 状态未知，沿用各批如实失败路径。
    tp = ctx.get("token_probe") or {}
    token_dead = bool(tp.get("ok") and not tp.get("valid"))
    if token_dead:
        for tbl in ("xq_kline_d", "xq_capital_flow",
                    "xq_northbound", "xq_earningforecast"):
            mf.mark(tbl, done=False, rows=0, status="failed",
                    note="token_probe %s" % (tp.get("err") or "invalid"))
        mf.save()
        names = [n for n in names if n == "snap"]
        print("[ALERT][token_invalid] XQ_A_TOKEN 预检失效（%s）："
              "token 批已跳过（快照照跑）；请浏览器登录 xueqiu.com 续期"
              % (tp.get("err") or "invalid"))
    results: dict[str, int] = {}
    for name in names:
        # P0-2 批间节奏：批内循环尾「末码不睡」（i<len 短路），批衔接处
        # （上批末请求→下批首请求、quotec probe→首批）由这里兜底，
        # 杜绝背靠背连发提前触发 IP 风控。
        X.jitter_sleep()
        try:
            if name == "snap":
                r = sync_quote_snap(codes, ymd, mf)
            elif name == "kline":
                r = sync_kline(codes, a.limit, mf)
            elif name == "flow":
                r = sync_capital_flow(codes, ymd, mf)
            elif name == "north":
                r = sync_northbound(ymd, mf)
            else:                            # forecast
                r = sync_earningforecast(codes, mf)
            results[name] = r
        except Exception as e:               # 单批兜底：绝不让异常出 main
            results[name] = -1
            print("[main] 批 %s 异常：%s" % (name, str(e)[:100]))
    failed = [n for n, v in results.items() if v == -1]
    # P1 修复（审计）：批内业务失败（manifest status=failed，如 400016
    # token 失效、网络全败）同样计入「全批失败」——sync_* 内部吞错返回
    # 0，仅凭 -1 判定会让全败批次伪成功 rc=0，wrapper 不告警静默失败。
    # degraded/skip_no_token/empty/partial 仍 rc=0（降级矩阵口径不变）。
    _TBL_OF = {"snap": "xq_quote_snap", "kline": "xq_kline_d",
               "flow": "xq_capital_flow", "north": "xq_northbound",
               "forecast": "xq_earningforecast"}
    for n in names:
        if results.get(n, 0) != -1 and n in _TBL_OF and \
                (mf.get(_TBL_OF[n]) or {}).get("status") == "failed" and \
                n not in failed:
            failed.append(n)
    if names and len(failed) == len(names):  # 全批失败才 rc=1
        print("[main] 全批失败：%s → rc=1" % ",".join(failed))
        return 1
    if token_dead:
        # P1-5：token 失效即使快照成功也 rc=1——token 死亡必须人工
        # 介入（浏览器重登），「失败必告警」语义不允许静默降级。
        print("[main] token 预检失效，token 批已跳过 → rc=1 告警")
        return 1
    print("[main] 完成：%s" % ", ".join(
        "%s=%s" % (n, v) for n, v in results.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
