# -*- coding: utf-8 -*-
"""缠论信号样本积累：自选池标的日线 → 结构信号事件统计去重落盘。

背景：P4 验证层（chanlun_backtest）上线后，缠论信号暂不接入榜单与卡片
推送；本任务每日收盘后跑一轮自选池（news_config.json watchlist）日线，
把「20 根窗口已成熟（r20 非 None）」的信号事件以
(market, code, period, type, dt) 唯一键去重追加到样本库，作为跨时间
样本积累；接入评估时直接读本库统计各类型 n / 胜率 / 均值。

口径与 chanlun_backtest 一致：方向收益（买点涨为胜 / 卖点跌为胜）、
理论价基准；r60 首见不足则记 null，窗口成熟后按同一口径回填
（仅向后看已收盘数据，无未来函数）。

用法:
  python chanlun_sample.py            # 跑一轮积累并打印库存统计
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SAMPLES = os.path.join(HERE, "data", "chanlun_samples.jsonl")
EVAL_CACHE = os.path.join(HERE, "data", "chanlun_eval_cache.json")

from chanlun_backtest import _dir_ret  # noqa: E402  回填与回测同口径


def _watchlist():
    """自选池 → [(market, db_symbol, name)]（去重，US/CRYPTO 双注册原样保留）。"""
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "news_collector")))
    import runner

    cfg = os.path.join(runner.NEWS_DIR, "news_config.json")
    wl = json.load(open(cfg, encoding="utf-8")).get("watchlist", [])
    return runner.resolve_watchlist(wl)


def _load_keys(path):
    keys = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                keys.add(
                    (
                        r.get("market"),
                        r.get("code"),
                        r.get("period"),
                        r.get("type"),
                        r.get("dt"),
                    )
                )
    return keys


def _atomic_write(path, rows):
    """整库重写（tempfile + os.replace，守原子写约束）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _backfill_r60(rows, kl):
    """对既有样本中 r60=null 且窗口已成熟的行，按 chanlun_backtest 同一
    口径回填（买点=+60根收益，卖点=反向）；返回回填条数。"""
    bars = kl["bars"]
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    n = 0
    for r in rows:
        if (r.get("market"), r.get("code")) != (kl["market"], kl["code"]):
            continue
        if r.get("r60") is not None or not r.get("price"):
            continue
        i0 = idx.get(r.get("dt"))
        if i0 is None or i0 + 60 >= len(bars):
            continue
        r["r60"] = round(_dir_ret(r["type"], r["price"], bars[i0 + 60]["c"]), 4)
        n += 1
    return n


def _refresh_eval_cache():
    """重放评估 → data/chanlun_eval_cache.json（门户样本统计面板直读）。"""
    rows = []
    if os.path.exists(SAMPLES):
        with open(SAMPLES, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    from chanlun_backtest import eval_samples, TYPES

    ev = eval_samples(rows)
    ok = [t for t in TYPES if (ev["types"].get(t) or {}).get("pass")]
    ev["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev["verdict"] = "可成交口径达标：%s；其余 n<10 或方向存疑，推送链路保持冻结" % (
        "、".join(ok) if ok else "暂无"
    )
    _atomic_write(EVAL_CACHE, [ev])
    print("eval_cache refreshed: %s" % ev["verdict"])


def _backfill_mt(rows, kl, st, sub_bars):
    """对既有样本中缺 mt 且 60m 窗口已覆盖的行回填 P7 共振标注；返回条数。"""
    from chanlun_mtf import confluence as _cf

    n = 0
    for r in rows:
        if (
            (r.get("market"), r.get("code")) != (kl["market"], kl["code"])
            or r.get("period") != "1d"
            or "mt" in r
        ):
            continue
        sig = next(
            (
                s
                for s in st["signals"]
                if s["dt"] == r.get("dt") and s["type"] == r.get("type")
            ),
            None,
        )
        if sig is None:
            continue
        mt, mtv = _cf(st["bis"], sig, sub_bars)
        if mt is None:
            continue  # 窗口仍未覆盖，等分钟数据自然增长
        r["mt"] = mt
        r["mt_hit"] = (mtv or {}).get("hit")
        n += 1
    return n


def _backfill_flow(rows, kl, st, idx_bars):
    """对存量样本回填 P8-a 量能 vv 与 P8-b 大盘 reg（只填缺失、可判才写）。"""
    from chanlun_flow import regime_at as _reg

    n = 0
    for r in rows:
        if (r.get("market"), r.get("code")) != (kl["market"], kl["code"]) or r.get(
            "period"
        ) != "1d":
            continue
        sig = next(
            (
                s
                for s in st["signals"]
                if s["dt"] == r.get("dt") and s["type"] == r.get("type")
            ),
            None,
        )
        if sig is None:
            continue
        if "vv" not in r and sig.get("vv") is not None:
            r["vv"] = sig["vv"]
            n += 1
        if "reg" not in r and idx_bars:
            reg, _ = _reg(kl["market"], r.get("dt"), idx_bars)
            if reg is not None:
                r["reg"] = reg
                n += 1
    return n


def _backfill_reg_trend(rows, kl, idx_bars):
    """P9 长周期趋势 regime 回填（close>MA200=True；只填缺失、可判才写）。"""
    from chanlun_flow import regime_trend_at as _rt

    n = 0
    if not idx_bars:
        return 0
    for r in rows:
        if (r.get("market"), r.get("code")) != (kl["market"], kl["code"]) or r.get(
            "period"
        ) != "1d":
            continue
        if "reg_trend" in r:
            continue
        rt, _ = _rt(kl["market"], r.get("dt"), idx_bars)
        if rt is not None:
            r["reg_trend"] = rt
            n += 1
    return n


def _backfill_trend(rows, kl, st):
    """对存量 1买/1卖 回填 P6-c 完整版趋势背驰 tbe（只填缺失、可判才写）。"""
    n = 0
    for r in rows:
        if (r.get("market"), r.get("code")) != (kl["market"], kl["code"]) or r.get(
            "type"
        ) not in ("1买", "1卖"):
            continue
        sig = next(
            (
                s
                for s in st["signals"]
                if s["dt"] == r.get("dt") and s["type"] == r.get("type")
            ),
            None,
        )
        if sig is None or "tbe" in r or sig.get("tbe") is None:
            continue
        r["tbe"] = sig["tbe"]
        n += 1
    return n


def run():
    from chanlun_data import load_kline
    from chanlun_backtest import analyze
    from chanlun_mtf import confluence
    from chanlun_flow import REGIME_INDEX, regime_at, regime_trend_at

    entries = _watchlist()
    print("watchlist: %d entries" % len(entries))
    keys = _load_keys(SAMPLES)
    lib = []
    if os.path.exists(SAMPLES):
        with open(SAMPLES, encoding="utf-8") as f:
            for line in f:
                try:
                    lib.append(json.loads(line))
                except Exception:
                    continue
    new_rows = []
    n_ok = 0
    n_bf = 0
    n_mt = 0
    n_flow = 0
    n_tr = 0
    n_rt = 0
    idx_cache = {}  # market → 指数日线（P8-b，每市场只加载一次）
    for mkt, sym, nm in entries:
        try:
            kl = load_kline(sym, "1d", 500)
            if not kl.get("ok"):
                continue  # 双注册另一市场兜底 / 无数据标的跳过
            n_ok += 1
            n_bf += _backfill_r60(lib, kl)
            st = analyze(kl["bars"])
            n_tr += _backfill_trend(lib, kl, st)
            # P7：best-effort 60m 次级别共振（缺数据 mt=None，不记 False）
            sub_bars = []
            try:
                rs = load_kline(sym, "60m", 1000)
                sub_bars = rs["bars"] if rs.get("ok") else []
            except Exception:
                sub_bars = []
            if sub_bars:
                n_mt += _backfill_mt(lib, kl, st, sub_bars)
            # P8-b：大盘指数（CN 上证 / CRYPTO BTC；US/HK 库内无指数）
            idx_bars = []
            if kl["market"] in REGIME_INDEX:
                if kl["market"] not in idx_cache:
                    ri = load_kline(REGIME_INDEX[kl["market"]], "1d", 500)
                    idx_cache[kl["market"]] = ri["bars"] if ri.get("ok") else []
                idx_bars = idx_cache[kl["market"]]
            if idx_bars:
                n_flow += _backfill_flow(lib, kl, st, idx_bars)
                n_rt += _backfill_reg_trend(lib, kl, idx_bars)
            for ev in st["events"]:
                if ev.get("r20") is None:
                    continue  # 20 根窗口未成熟，等后续自然成熟再收
                key = (kl["market"], kl["code"], "1d", ev["type"], ev["dt"])
                if key in keys:
                    continue
                keys.add(key)
                sig0 = next(
                    (
                        s
                        for s in st["signals"]
                        if s["dt"] == ev["dt"] and s["type"] == ev["type"]
                    ),
                    None,
                )
                mt, mtv = (None, None)
                if sub_bars and sig0 is not None:
                    mt, mtv = confluence(st["bis"], sig0, sub_bars)
                reg = None
                if idx_bars:
                    reg, _ = regime_at(kl["market"], ev["dt"], idx_bars)
                reg_trend = None
                if idx_bars:
                    reg_trend, _ = regime_trend_at(kl["market"], ev["dt"], idx_bars)
                new_rows.append(
                    {
                        "market": kl["market"],
                        "code": kl["code"],
                        "name": (nm or "")[:16],
                        "period": "1d",
                        "type": ev["type"],
                        "dt": ev["dt"],
                        "price": ev["price"],
                        "r5": ev["r5"],
                        "r10": ev["r10"],
                        "r20": ev["r20"],
                        "r60": ev["r60"],
                        "mt": mt,
                        "mt_hit": (mtv or {}).get("hit") if mtv else None,
                        "vv": (sig0 or {}).get("vv"),
                        "reg": reg,
                        "reg_trend": reg_trend,
                        "tbe": (sig0 or {}).get("tbe"),
                        "first_seen": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    }
                )
        except Exception as e:
            print("  skip %s/%s: %s" % (mkt, sym, str(e)[:80]))
    if new_rows or n_bf or n_mt or n_flow or n_tr or n_rt:
        _atomic_write(SAMPLES, lib + new_rows)
    # 库存统计（r20 口径）
    total = 0
    agg = {}
    with open(SAMPLES, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("r20") is None:
                continue
            total += 1
            a = agg.setdefault(r["type"], [0, 0, 0.0])
            a[0] += 1
            a[1] += 1 if r["r20"] > 0 else 0
            a[2] += r["r20"]
    print(
        "loaded=%d ok, new=%d, backfill_r60=%d, backfill_mt=%d, "
        "backfill_flow=%d, backfill_trend=%d, backfill_reg_trend=%d, "
        "library=%d (r20 mature)"
        % (n_ok, len(new_rows), n_bf, n_mt, n_flow, n_tr, n_rt, total)
    )
    for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
        if t in agg:
            n, w, s = agg[t]
            print(
                "  %s n=%d win=%.0f%% avg=%.2f%%" % (t, n, w * 100.0 / n, s * 100.0 / n)
            )
    try:
        _refresh_eval_cache()
    except Exception as e:
        print("eval_cache failed: %s" % str(e)[:120])


if __name__ == "__main__":
    run()
