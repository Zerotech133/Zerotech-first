# -*- coding: utf-8 -*-
"""信号台账：方案产出的【所有】满足条件的票，自动登记 → 监控 → 到期复盘。

与 hold_track.py 的区别：
  hold_track 只看「最新一个信号日」的第 1 日；本脚本把每个信号日的全部满足条件
  票（主选 confluence_multi + auto 池 confluence_multi）登记成 cohort，长期跟踪
  到 N=1/3/5/10/20 日到期结算，并滚动监控超额。方案是常驻的，不是盯某天的几只票。

口径（与 hold_track 一致，写死）：
  信号日 D = selection_<asof>.json 的 asof（当日收盘数据选出）
  执行日 T = D 之后第一个交易日，按 T 日开盘价入场
  第 N 日  = T 起第 N 个交易日收盘（T 当天收盘记第 1 日）
  收益     = close_M×adj_M / (open_T×adj_T) - 1   （复权精确，除权不失真）
  基准     = 两融标的池（信号日在册、仅股票）同窗口等权均值
  盘中累计 = (1 + 昨收累计) × (1 + 现价/pre_close - 1) - 1
             pre_close 已处理当日除权，故盘中累计也精确

文件布局（本目录 cohorts/）：
  cohort_<asof>_<pool>.json      每个信号日×池一个文件，含登记/快照/结算
  settlements.jsonl              每次结算追加一行（审计用）
  status_latest.json             最近一次 --snap 的全景，供复盘报告读取
  mktcache/daily_<date>.parquet  整市场日线缓存（一次拉取，反复使用）
  mktcache/adjf_<date>.parquet   整市场复权因子缓存

用法：
  python cohort_track.py --register              # 扫描 selection_*，登记新 cohort
  python cohort_track.py --snap                  # 盘中/收盘 全景快照（所有 live cohort）
  python cohort_track.py --settle                # 结算已到期的 N 日（15:00 后才结当日）
  python cohort_track.py --register --snap --settle   # 15:30 自动化的完整动作
"""
import os, sys, json, glob, time, datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FACTOR_DIR = r"C:\项目\zerobound-kg\factor_proto"
DATA = os.path.join(FACTOR_DIR, "data")
COHORT_DIR = os.path.join(HERE, "cohorts")
CACHE = os.path.join(COHORT_DIR, "mktcache")
HIST_MARGIN = r"C:\Temp\hf_probe\hist\margin.parquet"  # delta 文件只覆盖近 11 天，更早日期兜底

API = "https://api.tushare.pro"
MATURITIES = (1, 3, 5, 10, 20)
POOLS = [("main", "selection_%s.json", "主选·三条全中"),
         ("auto", "selection_%s_auto.json", "auto池·生产")]
SETTLE_JSONL = os.path.join(COHORT_DIR, "settlements.jsonl")
STATUS_LATEST = os.path.join(COHORT_DIR, "status_latest.json")


def _api(name, params, retry=3):
    import requests
    token = os.environ.get("TUSHARE_MCP_TOKEN", "")
    if not token:
        sys.exit("FATAL: 缺 TUSHARE_MCP_TOKEN")
    last = None
    for att in range(retry):
        try:
            r = requests.post(API, json={"api_name": name, "token": token,
                                         "params": params, "fields": ""}, timeout=30)
            j = r.json()
            if j.get("code") == 0 and j.get("data"):
                fd = j["data"]
                return [dict(zip(fd["fields"], x)) for x in fd["items"]]
            last = j.get("code")
        except Exception as e:
            last = str(e)
        time.sleep(0.8 + att * 0.8)
    raise RuntimeError("tushare %s %s 失败: %s" % (name, params, last))


# ── 交易日历（覆盖未来，登记到期日要用） ───────────────────────────
_CAL = None
def trade_cal():
    global _CAL
    if _CAL is not None:
        return _CAL
    cp = os.path.join(DATA, "trade_cal_cache.json")
    if os.path.exists(cp):
        cal = json.load(open(cp, encoding="utf-8"))
        if isinstance(cal, list) and cal and cal[-1] >= "20261201":
            _CAL = sorted(cal)
            return _CAL
    rows = _api("trade_cal", {"exchange": "SSE", "start_date": "20260101",
                              "end_date": "20271231", "is_open": "1"})
    _CAL = sorted(r["cal_date"] for r in rows)
    json.dump(_CAL, open(cp, "w", encoding="utf-8"))
    return _CAL


def next_trading_day(d):
    cal = trade_cal()
    for x in cal:
        if x > d:
            return x
    return None


def nth_from(start, n):
    """start 起第 n 个交易日（start 当天记第 1 个）。"""
    cal = [x for x in trade_cal() if x >= start]
    return cal[n - 1] if len(cal) >= n else None


def prev_trading_day(d):
    cal = [x for x in trade_cal() if x < d]
    return cal[-1] if cal else None


# ── 整市场日线 / 复权因子（带缓存） ───────────────────────────────
def load_daily(date):
    """返回 dict: code -> {open, close, pre_close}（不复权原始价）。"""
    fp = os.path.join(CACHE, "daily_%s.parquet" % date)
    if os.path.exists(fp):
        df = pd.read_parquet(fp)
    else:
        rows = _api("daily", {"trade_date": date})
        if not rows:
            return {}
        df = pd.DataFrame(rows)
        os.makedirs(CACHE, exist_ok=True)
        df.to_parquet(fp)
    return {r.ts_code: {"open": float(r.open), "close": float(r.close),
                        "pre_close": float(r.pre_close)}
            for r in df.itertuples()}


def load_adjf(date):
    """返回 Series: code -> adj_factor。"""
    fp = os.path.join(CACHE, "adjf_%s.parquet" % date)
    if os.path.exists(fp):
        return pd.read_parquet(fp).set_index("ts_code")["adj_factor"].astype(float)
    rows = _api("adj_factor", {"trade_date": date})
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    os.makedirs(CACHE, exist_ok=True)
    df.to_parquet(fp)
    return df.set_index("ts_code")["adj_factor"].astype(float)


def _adj_px(px_map, adjf, code, field):
    """code 在该日的复权价；缺价/停牌返回 None。"""
    r = px_map.get(code)
    if not r or not r.get(field):
        return None
    f = adjf.get(code)
    return r[field] * float(f) if f else None


# ── 两融标的池（信号日在册、仅股票） ─────────────────────────────
def margin_pool(asof):
    mg = pd.read_parquet(os.path.join(DATA, "margin_detail_delta.parquet"),
                         columns=["trade_date", "ts_code", "rzye"])
    mg["d"] = mg.trade_date.astype(str).str[:8]
    sub = mg[(mg.d == asof) & (mg.rzye > 0)]
    if len(sub) == 0 and os.path.exists(HIST_MARGIN):
        h = pd.read_parquet(HIST_MARGIN)
        h["d"] = h.trade_date.astype(str).str[:8]
        sub = h[(h.d == asof)]
    sb = pd.read_parquet(os.path.join(DATA, "stock_basic_full.parquet"),
                         columns=["ts_code"])
    stocks = set(sb.ts_code.astype(str))
    return set(sub.ts_code.astype(str)) & stocks


# ── 登记 ─────────────────────────────────────────────────────────
def register():
    made = []
    for asof_path in sorted(glob.glob(os.path.join(FACTOR_DIR, "selection_2*.json"))):
        base = os.path.basename(asof_path)
        if base.endswith("_auto.json"):
            asof, pool = base[len("selection_"):-len("_auto.json")], "auto"
        elif base.endswith("_wf_strp.json") or base.endswith("_wf.json"):
            continue  # 实验变体不进台账
        else:
            asof, pool = base[len("selection_"):-len(".json")], "main"
        if len(asof) != 8 or not asof.isdigit():
            continue
        out = os.path.join(COHORT_DIR, "cohort_%s_%s.json" % (asof, pool))
        if os.path.exists(out):
            continue
        d = json.load(open(asof_path, encoding="utf-8"))
        codes = sorted(set(d.get("confluence_multi") or []))
        if not codes:
            continue
        entry = next_trading_day(asof)
        os.makedirs(COHORT_DIR, exist_ok=True)
        pack = {
            "asof": asof, "pool": pool,
            "pool_label": dict((p, l) for p, _, l in POOLS)[pool],
            "n": len(codes), "codes": codes,
            "entry_date": entry,
            "maturities": {str(n): nth_from(entry, n) for n in MATURITIES if entry},
            "registered_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "settled": {}, "snapshots": [],
        }
        json.dump(pack, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        made.append((asof, pool, len(codes)))
        print("登记 cohort %s/%s：%d 只，入场 %s，到期 %s"
              % (asof, pool, len(codes), entry, pack["maturities"]))
    if not made:
        print("没有新 cohort 需要登记")
    return made


# ── 结算 ─────────────────────────────────────────────────────────
def _market_closed():
    now = datetime.datetime.now()
    return (now.hour * 60 + now.minute) >= 15 * 60 or now.date().isoformat().replace("-", "") > _today()


def _today():
    return datetime.date.today().strftime("%Y%m%d")


def settle():
    """结算所有到期日 <= 当前可结算日的未结项。当日到期只在收盘后结算。"""
    today = _today()
    results = []
    for fp in sorted(glob.glob(os.path.join(COHORT_DIR, "cohort_*.json"))):
        c = json.load(open(fp, encoding="utf-8"))
        entry = c["entry_date"]
        if not entry:
            continue
        asof, pool = c["asof"], c["pool"]
        for N in MATURITIES:
            key = str(N)
            prev = c["settled"].get(key)
            if prev is not None and not prev.get("error"):
                continue  # 已成功结算才跳过；历史错误项在日线数据可用后自动重试
            m = (c["maturities"] or {}).get(key)
            if not m or m > today:
                continue
            if m == today and not _market_closed():
                continue  # 盘中不结算当日，防午盘价冒充收盘价
            # 入场价与到期价（复权）
            dm, am = load_daily(m), load_adjf(m)
            de, ae = load_daily(entry), load_adjf(entry)
            rets, miss = [], []
            for code in c["codes"]:
                px = _adj_px(dm, am, code, "close")
                po = _adj_px(de, ae, code, "open")
                if px is None or po is None:
                    miss.append(code)
                    continue
                rets.append(px / po - 1)
            if not rets:
                c["settled"][key] = {"m": m, "error": "无有效价格", "miss": miss}
                continue
            # 基准：两融标的池同窗口等权
            pool_codes = margin_pool(asof)
            brets = []
            for code in pool_codes:
                px = _adj_px(dm, am, code, "close")
                po = _adj_px(de, ae, code, "open")
                if px is None or po is None:
                    continue
                brets.append(px / po - 1)
            bench = sum(brets) / len(brets) if brets else None
            rec = {
                "asof": asof, "pool": pool, "n": N, "m": m,
                "n_used": len(rets), "n_miss": len(miss),
                "ret_mean": round(sum(rets) / len(rets), 6),
                "ret_med": round(sorted(rets)[len(rets) // 2], 6),
                "bench": round(bench, 6) if bench is not None else None,
                "excess": round(sum(rets) / len(rets) - bench, 6) if bench is not None else None,
                "win": round(sum(1 for x in rets if bench is not None and x > bench) / len(rets), 4),
                "entry_date": entry, "source": "daily",
                "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            c["settled"][key] = rec
            results.append(rec)
            print("结算 %s/%s 第%s日: 收益 %+.2f%%  基准 %+.2f%%  超额 %+.2fpp  胜率 %.0f%%  (%d/%d)"
                  % (asof, pool, N, rec["ret_mean"] * 100,
                     (rec["bench"] or 0) * 100,
                     ((rec["excess"] or 0) * 100), rec["win"] * 100,
                     rec["n_used"], c["n"]))
        json.dump(c, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if results:
        with open(SETTLE_JSONL, "a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        print("没有到期待结项")
    return results


# ── 全景快照（所有 live cohort） ────────────────────────────────
def snap():
    from hold_track import pull_snapshot
    today = _today()
    yday = prev_trading_day(today)
    # 在市标的 & 实时价
    dl = pd.read_parquet(os.path.join(DATA, "daily_delta.parquet"),
                         columns=["ts_code", "trade_date", "close"])
    dl["d"] = dl.trade_date.astype(str).str[:8]
    asof_view = dl.d.max()  # 最新有日线的日期
    mkt = sorted(set(dl[dl.d == asof_view].ts_code.astype(str)))
    print("现拉全市场实时（%d 只）…" % len(mkt))
    t0 = datetime.datetime.now().strftime("%H:%M:%S")
    got, errs = pull_snapshot(mkt)
    at = datetime.datetime.now().strftime("%H:%M:%S")
    miss_all = sorted(c for c in mkt if c not in got)
    if miss_all:
        extra, _ = pull_snapshot(miss_all, batch=10, retry=5)
        got.update(extra)
    print("快照完成 %d/%d 只，时刻 %s（%s 起）" % (len(got), len(mkt), at, t0))

    def fv(x):
        try:
            v = float(x)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    # 今日基准两套口径（分母不同，必须按 cohort 窗口选用）：
    #   open-based  = 现价/今开 - 1   → 入场日=今天的 cohort 的当日段（与入场价同基准）
    #   precl-based = 现价/昨收 - 1   → 老 cohort 的当日段（昨收已处理除权）
    pool_all = margin_pool(asof_view)
    bo, bp = [], []
    for c in pool_all:
        r = got.get(c)
        if not r:
            continue
        n, pc, o = fv(r.get("close")), fv(r.get("pre_close")), fv(r.get("open"))
        if n and pc:
            bp.append(n / pc - 1)
        if n and o:
            bo.append(n / o - 1)
    bench_tm = sum(bp) / len(bp) if bp else None       # 昨收→现
    bench_open = sum(bo) / len(bo) if bo else None     # 今开→现
    print("基准今日: 今开→现 %+.2f%%（%d 只）  昨收→现 %+.2f%%（%d 只）"
          % (bench_open * 100, len(bo), bench_tm * 100, len(bp)))

    cohorts_out = []
    for fp in sorted(glob.glob(os.path.join(COHORT_DIR, "cohort_*.json"))):
        c = json.load(open(fp, encoding="utf-8"))
        entry = c["entry_date"]
        if not entry or entry > today:
            cohorts_out.append({"asof": c["asof"], "pool": c["pool"],
                                "state": "未入场", "entry_date": entry})
            continue
        # 昨收累计（若昨天已收盘有日线；入场日=今天则累计为 0）
        cum_ok = {}
        bench_cum_y = 0.0
        if yday >= entry:
            dy, ay = load_daily(yday), load_adjf(yday)
            de, ae = load_daily(entry), load_adjf(entry)
            vals, bvals = [], []
            for code in c["codes"]:
                py = _adj_px(dy, ay, code, "close")
                po = _adj_px(de, ae, code, "open")
                if py is None or po is None:
                    continue
                vals.append(py / po - 1)
            pc_pool = margin_pool(c["asof"])
            for code in pc_pool:
                py = _adj_px(dy, ay, code, "close")
                po = _adj_px(de, ae, code, "open")
                if py is None or po is None:
                    continue
                bvals.append(py / po - 1)
            if vals:
                cum_ok = {"n_used": len(vals),
                          "cum": sum(vals) / len(vals)}
            if bvals:
                bench_cum_y = sum(bvals) / len(bvals)
        rows, miss = [], []
        for code in c["codes"]:
            r = got.get(code)
            if not r:
                miss.append(code)
                continue
            o, n, pc = fv(r.get("open")), fv(r.get("close")), fv(r.get("pre_close"))
            if not (o and n and pc):
                miss.append(code)
                continue
            tm = n / pc - 1          # 昨收→现（跨日段用）
            om = n / o - 1           # 今开→现（入场日段用）
            if entry == today:
                cum_s = om           # 入场日：与台账入场价同基准，必须是开仓口径
            else:
                base_cum = cum_ok.get("cum")
                cum_s = (1 + base_cum) * (1 + tm) - 1 if base_cum is not None else None
            rows.append({"code": code, "name": r.get("name") or "",
                         "entry": round(o, 3), "now": round(n, 3),
                         "today": round(om, 6), "cum": round(cum_s, 6) if cum_s is not None else None})
        n_used = len([x for x in rows if x["cum"] is not None])
        # 基准累计：与 cohort 同窗口同口径
        if entry == today:
            bench_cum = bench_open
        else:
            bench_cum = ((1 + bench_cum_y) * (1 + bench_tm) - 1
                         if (bench_tm is not None) else None)
        rec = {
            "asof": c["asof"], "pool": c["pool"], "entry_date": entry,
            "day_index": len([x for x in trade_cal() if entry <= x <= today]),
            "n": c["n"], "n_used": n_used, "n_miss": len(miss),
            "cum_today_mean": round(sum(x["cum"] for x in rows if x["cum"] is not None) / n_used, 6) if n_used else None,
            "bench_cum": round(bench_cum, 6) if bench_cum is not None else None,
            "settled": c.get("settled", {}),
            "rows": sorted(rows, key=lambda x: -(x["cum"] if x["cum"] is not None else -9)),
            "miss": miss,
        }
        if rec["cum_today_mean"] is not None and rec["bench_cum"] is not None:
            rec["excess"] = round(rec["cum_today_mean"] - rec["bench_cum"], 6)
        cohorts_out.append(rec)
        # 写回 cohort 文件（快照留最近 5 条）
        c.setdefault("snapshots", []).append({
            "at": at, "cum": rec["cum_today_mean"],
            "bench": rec.get("bench_cum"), "excess": rec.get("excess")})
        c["snapshots"] = c["snapshots"][-5:]
        json.dump(c, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        si = rec.get("day_index")
        print("%s/%s 持有第%s日: 累计 %s  基准 %s  超额 %s  (%d/%d)"
              % (c["asof"], c["pool"], si,
                 ("%+.2f%%" % (rec["cum_today_mean"] * 100)) if rec["cum_today_mean"] is not None else "-",
                 ("%+.2f%%" % (rec["bench_cum"] * 100)) if rec.get("bench_cum") is not None else "-",
                 ("%+.2fpp" % (rec["excess"] * 100)) if rec.get("excess") is not None else "-",
                 n_used, c["n"]))

    status = {
        "run_at": at, "as_of_from": t0, "asof_view": asof_view, "today": today,
        "bench_open_today": round(bench_open, 6) if bench_open is not None else None,
        "bench_tm_today": round(bench_tm, 6) if bench_tm is not None else None,
        "bench_pool_n": len(bo),
        "cohorts": cohorts_out,
    }
    json.dump(status, open(STATUS_LATEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    _write_hold_pack(status)
    print("落盘 → %s" % STATUS_LATEST)
    return status


def _write_hold_pack(status):
    """把台账快照转成日报 H5 的 hold 数据包（hold_latest.json 契约不变）。

    只取 asof == 最新 report_*.json 的 cohort（H5 持有卡是该信号日的第 1 日视图）；
    同一 asof 的 main/auto 共享入场日与窗口，基准同一个。
    """
    reps = sorted(glob.glob(os.path.join(DATA, "report_2*.json")))
    if not reps:
        print("（无 report 数据包，跳过 hold pack）")
        return
    rasof = max(os.path.basename(x)[len("report_"):-len(".json")] for x in reps)
    cs = [c for c in status["cohorts"]
          if c.get("asof") == rasof and c.get("cum_today_mean") is not None]
    if not cs:
        print("（台账里没有 asof=%s 的有效 cohort，hold pack 不更新）" % rasof)
        return
    bench = cs[0].get("bench_cum")
    batches = []
    for c in cs:
        rows = [dict(r, excess=round(r["cum"] - bench, 6))
                for r in c["rows"] if r.get("cum") is not None] if bench is not None else []
        batches.append({
            "key": c["pool"],
            "label": "三条全中（本页主选）" if c["pool"] == "main" else "auto 池（生产池）",
            "n": c["n"], "n_priced": c["n_used"], "missing": c.get("miss", []),
            "ret": c["cum_today_mean"], "excess": c.get("excess"),
            "win": sum(1 for r in rows if r["excess"] > 0),
            "rows": rows,
        })
    hh, mm = status["run_at"].split(":")[:2]
    pack = {
        "asof": rasof,
        "entry_date": cs[0]["entry_date"],
        "day_index": cs[0]["day_index"],
        "as_of": status["run_at"],
        "as_of_from": status.get("as_of_from"),
        "partial": (int(hh) * 60 + int(mm)) < 15 * 60,
        "bench": {"label": "两融标的池", "n": status.get("bench_pool_n"),
                  "ret": bench, "missing": 0},
        "batches": batches,
        "source": "cohort_track",
    }
    hdir = os.path.dirname(COHORT_DIR)
    out = os.path.join(hdir, "hold_%s.json" % rasof)
    latest = os.path.join(hdir, "hold_latest.json")
    json.dump(pack, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(pack, open(latest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("hold pack → %s（+ hold_latest.json）" % os.path.basename(out))


DEGRADE_LINE = {
    "n_full": 20,      # 滚动窗口：最近 20 个已结算样本
    "mean_pp": -1.0,   # 超额均值阈值（pp）
    "win": 40.0,       # 胜率阈值（%）
    "min_warn": 10,    # 样本数 <10 只预警不判；10~19 达标线预警
}
# 降级线口径（老田 2026-11-09 裁定）：auto 池最近 20 个已结算样本滚动超额均值
# < −1.0pp 且胜率 < 40% → 触发「建议降级」报警；机制只报数，降级动作由老田执行。


def rolling_alpha():
    """按 N 滚动汇总已结算超额（auto 池），附降级线评估。"""
    fp = os.path.join(COHORT_DIR, "settlements.jsonl")
    if not os.path.exists(fp):
        return {}
    rows = [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]
    out = {}
    for N in MATURITIES:
        ex = [r["excess"] for r in rows
              if r.get("n") == N and r.get("pool") == "auto"
              and r.get("excess") is not None]
        if ex:
            out[str(N)] = {"n": len(ex), "mean_pp": round(sum(ex) / len(ex) * 100, 2),
                           "win": round(sum(1 for x in ex if x > 0) / len(ex) * 100, 0)}
    # 降级线评估（老田 2026-11-09 裁定，只报数不动作）
    ex_all = [r["excess"] for r in rows
              if r.get("pool") == "auto" and r.get("excess") is not None]
    n_s = len(ex_all[-DEGRADE_LINE["n_full"]:])
    if n_s:
        win = sum(1 for x in ex_all[-DEGRADE_LINE["n_full"]:] if x > 0) / n_s * 100
        mean_pp = sum(ex_all[-DEGRADE_LINE["n_full"]:]) / n_s * 100
        breach = mean_pp < DEGRADE_LINE["mean_pp"] and win < DEGRADE_LINE["win"]
        if n_s < DEGRADE_LINE["min_warn"]:
            status = "insufficient"
        elif breach:
            status = "BREACH"
        elif mean_pp < DEGRADE_LINE["mean_pp"] or win < DEGRADE_LINE["win"]:
            status = "warn"
        else:
            status = "ok"
        out["_degrade"] = {
            "line": "最近%d个已结算样本 超额均值<%.1fpp 且 胜率<%.0f%% 建议降级（只报警，降级由老田执行）"
                    % (DEGRADE_LINE["n_full"], DEGRADE_LINE["mean_pp"], DEGRADE_LINE["win"]),
            "n": n_s, "mean_pp": round(mean_pp, 2), "win": round(win, 0),
            "status": status,
        }
    return out


def main():
    do_reg = do_snap = do_settle = False
    for a in sys.argv[1:]:
        if a == "--register":
            do_reg = True
        elif a == "--snap":
            do_snap = True
        elif a == "--settle":
            do_settle = True
    if not (do_reg or do_snap or do_settle):
        print(__doc__)
        sys.exit(2)
    if do_reg:
        register()
    if do_settle:
        settle()
    if do_snap:
        st = snap()
        ra = rolling_alpha()
        if ra:
            print("auto 池滚动超额（已结算项汇总）: %s" % json.dumps(ra, ensure_ascii=False))


if __name__ == "__main__":
    main()
