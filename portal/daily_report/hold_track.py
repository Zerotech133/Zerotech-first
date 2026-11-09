# -*- coding: utf-8 -*-
"""持仓跟踪：算「这批票买了以后到现在怎么样」。

为什么要单独算，而不是直接看涨跌幅：
  日报原来只报信号日当天的涨跌（如 -9.19%），那是「买之前」的事。
  真正该看的是「按信号买入之后赚了多少」，两者方向可以完全相反。
  跨日跟踪实测：auto 池 1 日超额 +0.02pp（≈抛硬币）、20 日 +2.00pp（胜率 74%）。
  只报当日就会把慢因子误判成死因子。这一列就是为了修这个问题。

口径（写死，不给参数）：
  信号日 D   = --asof，数据截止日，选股就是用这天的数据做的
  执行日 T   = D 之后第一个交易日，按 T 日【开盘价】入场
  持有日 N   = T 之后经过的交易日数，T 当天收盘记第 1 日，盘中为该日进行中
  入场价     = T 日开盘价
  累计收益   = 现价 / 入场价 - 1
  基准       = 两融标的池（仅股票）同期「今开→现」均值
  超额       = 累计收益 - 基准

  注：第 1 日收益只用当日 open/close，两者同为今日原始价，不涉及复权。
      多日累计需要 fct 复权因子，当前只做第 1 日，跨日扩展点见 _day_index()。

用法：
  python hold_track.py --asof 20261106 --pull            # 现拉全市场快照
  python hold_track.py --asof 20261106 --snap <json>     # 用已有快照
"""
import os, sys, json, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FACTOR_DIR = r"C:\项目\zerobound-kg\factor_proto"
DATA = os.path.join(FACTOR_DIR, "data")
LATEST = os.path.join(HERE, "hold_latest.json")

API = "https://api.tushare.pro"


def _fv(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def pull_snapshot(codes, batch=25, retry=4):
    """拉全市场实时。rt_k 批量 50 只会有 5-10% 批次整批失败，故 25 只一批 + 重试。"""
    import requests
    token = os.environ.get("TUSHARE_MCP_TOKEN", "")
    if not token:
        sys.exit("FATAL: 缺 TUSHARE_MCP_TOKEN")
    got, errs = {}, 0
    t0 = time.time()
    nb = (len(codes) + batch - 1) // batch
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        for att in range(retry):
            try:
                r = requests.post(API, json={"api_name": "rt_k", "token": token,
                                             "params": {"ts_code": ",".join(chunk)},
                                             "fields": ""}, timeout=30)
                j = r.json()
                if j.get("code") == 0:
                    fd = j["data"]
                    for row in fd["items"]:
                        rec = dict(zip(fd["fields"], row))
                        got[rec["ts_code"]] = rec
                    break
                errs += 1
            except Exception:
                errs += 1
            time.sleep(0.6 + att * 0.6)
        if (i // batch + 1) % 40 == 0:
            print("  %d/%d 批  %d 只  %.0fs" % (i // batch + 1, nb, len(got), time.time() - t0))
        time.sleep(0.2)
    return got, errs


def trading_days_between(d0, d1):
    """D 之后到 T 之间的交易日数（含两端不重复计）。用日历缓存，不猜节假日。"""
    import pandas as pd
    cp = os.path.join(DATA, "trade_cal_cache.json")
    cal = None
    if os.path.exists(cp):
        cal = json.load(open(cp, encoding="utf-8"))
    # 缓存不够新就重拉
    if not cal or d1 not in cal:
        import requests
        token = os.environ.get("TUSHARE_MCP_TOKEN", "")
        r = requests.post(API, json={"api_name": "trade_cal", "token": token,
                                     "params": {"exchange": "SSE",
                                                "start_date": "20260101",
                                                "end_date": datetime.date.today().strftime("%Y%m%d"),
                                                "is_open": "1"}, "fields": ""}, timeout=30)
        j = r.json()
        if j.get("code") == 0:
            fd = j["data"]
            cal = [dict(zip(fd["fields"], x))["cal_date"] for x in fd["items"]]
            json.dump(cal, open(cp, "w", encoding="utf-8"))
    if not cal:
        return None
    ds = [d for d in cal if d0 < d <= d1]
    return ds


def main():
    import pandas as pd

    asof = "20261106"
    snap_path = None
    out_path = None
    do_pull = False
    for i, a in enumerate(sys.argv):
        if a == "--asof":
            asof = sys.argv[i + 1]
        elif a == "--snap":
            snap_path = sys.argv[i + 1]
        elif a == "--out":
            out_path = sys.argv[i + 1]
        elif a == "--pull":
            do_pull = True

    # ── 1. 在市标的（asof 日有行情 = 在市）
    dl = pd.read_parquet(os.path.join(DATA, "daily_delta.parquet"),
                         columns=["ts_code", "trade_date", "close"])
    dl["d"] = dl.trade_date.astype(str).str[:8]
    cur = dl[dl.d == asof]
    mkt = set(cur.ts_code.astype(str))
    last_close = dict(zip(cur.ts_code.astype(str), cur.close.astype(float)))
    print("asof %s 在市 %d 只" % (asof, len(mkt)))

    # ── 2. 两融标的（仅股票，剥 ETF）
    sb = pd.read_parquet(os.path.join(DATA, "stock_basic_full.parquet"),
                         columns=["ts_code"])
    stocks = set(sb.ts_code.astype(str))
    mg = pd.read_parquet(os.path.join(DATA, "margin_detail_delta.parquet"),
                         columns=["trade_date", "ts_code", "rzye"])
    mg["d"] = mg.trade_date.astype(str).str[:8]
    mg = mg[(mg.d == asof) & (mg.rzye > 0)]
    margin_pool = (set(mg.ts_code.astype(str)) & stocks) & mkt
    print("两融标的（仅股票）%d 只" % len(margin_pool))

    # ── 3. 快照
    t_start = None
    if snap_path:
        snap = json.load(open(snap_path, encoding="utf-8"))
        got, at = snap["rows"], snap["at"]
        print("用已有快照 %s（%s，%d 只）" % (os.path.basename(snap_path), at, len(got)))
    elif do_pull:
        print("现拉全市场实时…")
        t_start = datetime.datetime.now().strftime("%H:%M:%S")
        got, errs = pull_snapshot(sorted(mkt))
        at = datetime.datetime.now().strftime("%H:%M:%S")
        print("拉取完成 %d/%d 只，错误 %d 次，时刻 %s" % (len(got), len(mkt), errs, at))
    else:
        sys.exit("需要 --pull 或 --snap")

    # ── 3b. 补拉 ──
    # rt_k 批量失败不是随机的：本次缺的 300394 恰是批次里偏弱的一只，
    # 缺它让 7 只均值从 +2.48% 虚高到 +2.78%。不补齐就会系统性高估。
    if do_pull:
        miss_all = sorted(c for c in mkt if c not in got)
        if miss_all:
            print("补拉缺失 %d 只…" % len(miss_all))
            extra, _ = pull_snapshot(miss_all, batch=10, retry=5)
            got.update(extra)
            print("  补到 %d 只，仍缺 %d" % (len(extra), len(miss_all) - len(extra)))

    # ── 4. 基准：两融标的池今开→现
    bret, missing = [], 0
    for c in margin_pool:
        r = got.get(c)
        if not r:
            missing += 1
            continue
        o, n = _fv(r.get("open")), _fv(r.get("close"))
        if not (o and n):
            missing += 1
            continue
        bret.append(n / o - 1)
    if not bret:
        sys.exit("FATAL: 基准池无有效样本")
    bench = sum(bret) / len(bret)
    print("基准池 %d 只有效（缺 %d），今开→现 %+.2f%%" % (len(bret), missing, bench * 100))

    # ── 5. 各批次
    def load_picks(fn, key):
        p = os.path.join(FACTOR_DIR, fn)
        if not os.path.exists(p):
            return []
        d = json.load(open(p, encoding="utf-8"))
        return list(d.get(key) or [])

    batches = []
    specs = [
        ("main", "三条全中（本页主选）", "selection_%s.json" % asof, "confluence_multi"),
        ("auto", "auto 池（生产池）", "selection_%s_auto.json" % asof, "confluence_multi"),
    ]
    for key, label, fn, jk in specs:
        picks = load_picks(fn, jk)
        if not picks:
            continue
        rows, n_ok, miss = [], 0, []
        for c in picks:
            r = got.get(c)
            if not r:
                miss.append(c)
                continue
            o, n = _fv(r.get("open")), _fv(r.get("close"))
            if not (o and n):
                miss.append(c)
                continue
            ret = n / o - 1
            n_ok += 1
            rows.append({
                "code": c, "name": r.get("name") or "",
                "entry": round(o, 3), "now": round(n, 3),
                "ret": round(ret, 6), "excess": round(ret - bench, 6),
                "pre_close": _fv(r.get("pre_close")),
                "prev_close": round(last_close[c], 3) if c in last_close else None,
            })
        if not n_ok:
            continue
        avg = sum(x["ret"] for x in rows) / n_ok
        win = sum(1 for x in rows if x["excess"] > 0)
        batches.append({
            "key": key, "label": label,
            "n": len(picks), "n_priced": n_ok, "missing": miss,
            "ret": round(avg, 6), "excess": round(avg - bench, 6),
            "win": win,
            "rows": sorted(rows, key=lambda x: -x["ret"]),
        })
        print("  %-6s %2d/%2d 只  今开→现 %+.2f%%  超额 %+.2fpp  跑赢 %d" % (
            key, n_ok, len(picks), avg * 100, (avg - bench) * 100, win))

    if out_path is None:
        out_path = os.path.join(HERE, "hold_%s.json" % asof)

    # ── 6. 持有日序号
    today = datetime.date.today().strftime("%Y%m%d")
    tdays = trading_days_between(asof, today)
    day_index = len(tdays) if tdays else 0
    entry_date = tdays[0] if tdays else None
    # 收盘后跑的判定：快照时刻 >= 15:00
    try:
        hh, mm = int(at.split(":")[0]), int(at.split(":")[1])
        partial = (hh * 60 + mm) < 15 * 60
    except Exception:
        partial = True

    pack = {
        "asof": asof,
        "entry_date": entry_date,
        "day_index": day_index,
        "as_of": at,
        "as_of_from": t_start,
        "partial": partial,
        "bench": {"label": "两融标的池", "n": len(bret), "ret": round(bench, 6),
                  "missing": missing},
        "batches": batches,
    }
    if not batches:
        sys.exit("FATAL: 没有任何批次有有效价")

    json.dump(pack, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 同时写一份 latest：生成器默认读它，省得每天手传路径。
    # 归档那份按 asof 命名，第二天不会被覆盖掉。
    if os.path.abspath(out_path) != os.path.abspath(LATEST):
        json.dump(pack, open(LATEST, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    print()
    print("持有第 %d 日（%s 开盘入场）%s" % (day_index, entry_date,
                                     "·盘中未收盘" if partial else "·已收盘"))
    print("落盘 → %s" % out_path)
    if os.path.abspath(out_path) != os.path.abspath(LATEST):
        print("      → %s（供生成器默认读取）" % LATEST)


if __name__ == "__main__":
    main()
