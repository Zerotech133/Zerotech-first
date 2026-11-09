# -*- coding: utf-8 -*-
"""青萍之末每日选股批次回测（qpzm_daily_batch）—— K238。

「一天选出来的股票，全选进入一个选股的回测」：每个交易日的选股批次
（买入+观察）整批统一进出场，回答「今天这套选股行不行」。

与 qpzm_backtest（全量参数网格锚）的分工：
  qpzm_backtest     评参数：历史所有信号聚合，验证打分体系参数稳定性
  qpzm_daily_batch  评批次：逐日批次跟踪，挂在每日选股结果后面展示

批次归档（reports/rps_daily_batches.jsonl，一行一天，date 升序）：
  live    产线 run_daily 尾部归档（完整四维口径，含 JEV），同 date 幂等覆盖
  replay  本脚本 --backfill 用 qpzm_backtest 纯计算层复算（JEV 缺维三维
          口径 tech/rps/env=0.4667/0.3333/0.2，现行权重 75/50 闸门开）；
          live 行永不覆盖，replay 行重跑即刷新

回测口径（与 qpzm_backtest 完全一致）：
  T 日收盘信号 → T+1 开盘买入 → T+1 起第 N 根 bar 收盘卖出；N∈{5,10,20}
  成本 0.30% 往返（实盘口径，config.trade.by_market.CN 推导：单边 15bp×2）；
  出场日停牌（无 bar）的交易剔除并计数
  基准 = 同一信号日全体可交易股票（≥55 根自身 bar 且进出场价齐全）
         等权同窗口；超额 = 批次均值 − 基准均值
  持有期未满（T+N 超出数据尾）→ exit 取最新收盘 =「至今收益」，
  partial=true，页面明示口径

范围：批次中 market=CN 且建议 ∈ {买入, 观察} 的票；
      「回避（环境闸门）」是被否决的信号，不计入批次回测。

用法（产线解释器唯一合法路径，junction 路径避免中文路径问题）:
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/qpzm_daily_batch.py \
      --backfill 60     # 回填近 60 个交易日 replay 批次（首次/手动）
  ... --run             # 批次回测主流程（读归档 → 写 rps_batch_backtest.json）
  ... --archive-live    # 从现有 rps_latest.json 归档一次（补今天）
  ... --all             # archive-live + backfill 60 + run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
REPORTS = os.path.join(HERE, "reports")
DATA_DIR = os.path.join(HERE, "data", "ashare")
BATCHES_PATH = os.path.join(REPORTS, "rps_daily_batches.jsonl")
REPORT_PATH = os.path.join(REPORTS, "rps_batch_backtest.json")
SNAP_PATH = os.path.join(REPORTS, "rps_latest.json")

HOLDS = (5, 10, 20)
# T11（20261105）成本口径对齐实盘：批次回测是实盘池口径，往返成本必须与
# 实盘一致（CN = 2×(cost_bps 12 + slippage_bps 3)bp = 30bp）。原 0.0015
# 是旧锚点，会把批次收益与逐日曲线整体高估 15bp。
# 复用 qpzm_backtest.default_cost_roundtrip（T8 已实现，勿重复实现）。
# 顶层 import 实测无输出副作用（stdout 空，约 1.7s 主要是 numpy/pandas，
# 本模块已依赖）；万一 import 失败，退回同一实盘口径常量，保证
# archive-live 等不依赖回测引擎的路径仍可跑。
try:
    from qpzm_backtest import default_cost_roundtrip as _default_cost_roundtrip

    COST = _default_cost_roundtrip("CN")
    COST_SOURCE = "config:CN(往返,default_cost_roundtrip)"
except Exception as _cost_ex:  # 引擎/配置缺失时不让归档路径陪葬
    COST = 0.0030
    COST_SOURCE = "fallback:0.0030(import_failed)"
    print("BATCH cost: 推导失败，回退 0.0030（%s）" % _cost_ex, flush=True)
MIN_BARS_ELIG = 55  # 与 qpzm_backtest.MIN_BARS_ELIG 一致
_SUF = {"sz": ".SZ", "sh": ".SH", "bj": ".BJ"}


# ── 码制转换 ─────────────────────────────────────────────────
def code_to_wide(code):
    """sz000993 → 000993.SZ（data/ashare wide 列名口径）；非 A 股前缀 None。"""
    c = str(code or "").strip()
    suf = _SUF.get(c[:2].lower())
    return (c[2:] + suf) if suf and len(c) > 2 else None


def wide_to_code(col):
    """000993.SZ → sz000993（归档码制统一，live/replay 同码制）。"""
    c = str(col or "").strip()
    if "." not in c:
        return c
    body, sfx = c.split(".", 1)
    pfx = {".SZ": "sz", ".SH": "sh", ".BJ": "bj"}.get("." + sfx.upper())
    return (pfx + body) if pfx else c


def _sel_picks(picks):
    """批次里可回测的票：market=CN 且建议∈{买入,观察}（剥离环境闸门后缀）。"""
    out = []
    for p in picks or []:
        act = str(p.get("action", "")).replace("（环境闸门）", "")
        if p.get("market") == "CN" and act in ("买入", "观察"):
            out.append(dict(p, _act=act))
    return out


# ── 数据层：轻量价矩阵 ───────────────────────────────────────
def load_px(data_dir=DATA_DIR, log_every=1500):
    """读 trade_date/open/high/close/pre_close → union 交易日索引 wide。

    K254：close/open 为后复权口径（own-bar pre_close 链因子）。
    返回 dict: dates(np int64), open/close(DataFrame), eligible(bool,
    自身 bar 数 ≥ MIN_BARS_ELIG，口径同 qpzm_backtest),
    unfill(bool, T+1 一字板=开=高=收且开盘涨幅达板)。
    """
    files = sorted(
        f
        for f in os.listdir(data_dir)
        if f.endswith(".parquet") and not f.startswith("_")
    )
    codes, per = [], []
    t0 = __import__("time").time()
    for i, f in enumerate(files):
        try:
            df = pd.read_parquet(
                os.path.join(data_dir, f),
                columns=["trade_date", "open", "high", "close", "pre_close"],
            )
        except Exception:
            continue
        if len(df) < 2:
            continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        codes.append(f[:-8].replace("_", "."))
        per.append(df)
        if (i + 1) % log_every == 0:
            print(
                "  px loaded %d/%d (%.0fs)"
                % (i + 1, len(files), __import__("time").time() - t0),
                flush=True,
            )
    if not per:
        raise RuntimeError("no parquet data in %s" % data_dir)
    union = np.unique(np.concatenate([d["trade_date"].to_numpy() for d in per])).astype(
        "int64"
    )
    dpos = {int(d): i for i, d in enumerate(union)}
    S, D = len(per), len(union)
    print(
        "px universe %d stocks, union %d dates (%s ~ %s)" % (S, D, union[0], union[-1]),
        flush=True,
    )
    op = np.full((D, S), np.nan, dtype="float32")
    cl = np.full((D, S), np.nan, dtype="float32")
    nb = np.zeros((D, S), dtype="int32")
    uf = np.zeros((D, S), dtype=bool)
    for j, df in enumerate(per):
        rows = np.array([dpos[int(d)] for d in df["trade_date"].to_numpy()])
        c_raw = df["close"].astype(float)
        o_raw = df["open"].astype(float)
        # K254 后复权因子链（own-bar）：f(t)=f(t-1)*close(t-1)/pre_close(t)
        pc = pd.to_numeric(df["pre_close"], errors="coerce")
        ratio = (c_raw.shift(1) / pc).astype(float)
        ratio = ratio.where(ratio.between(0.05, 20.0), 1.0).fillna(1.0)
        fct = ratio.cumprod()
        op[rows, j] = (o_raw * fct).to_numpy()
        cl[rows, j] = (c_raw * fct).to_numpy()
        nb[rows, j] = np.arange(1, len(df) + 1)
        # 一字板 mask（原始价判定）：开=高=收 且 开盘涨幅达板
        h_raw = df["high"].astype(float)
        rg = o_raw / pc.replace(0.0, np.nan) - 1.0
        code6 = codes[j]
        lim = (
            0.295
            if code6.endswith(".BJ")
            else (0.195 if code6[2:4] in ("30", "68") else 0.095)
        )
        uf[rows, j] = (
            (o_raw >= h_raw - 1e-9)
            & ((o_raw - c_raw).abs() <= 1e-6)
            & (rg >= lim - 0.005)
            & rg.notna()
        ).to_numpy()
    return {
        "dates": union,
        "codes": codes,
        "open": pd.DataFrame(op, index=union, columns=codes),
        "close": pd.DataFrame(cl, index=union, columns=codes),
        "eligible": pd.DataFrame(nb >= MIN_BARS_ELIG, index=union, columns=codes),
        "unfill": pd.DataFrame(uf, index=union, columns=codes),
    }


# ── 批次归档（jsonl，一行一天，幂等） ────────────────────────
def _read_batches(path=BATCHES_PATH):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    return out


def _write_batches(rows, path=BATCHES_PATH):
    """按 date 去重（同 date 保留最后一行）→ date 升序 → 原子写。"""
    by = {}
    for r in rows:
        by[str(r.get("date"))] = r
    rows2 = sorted(by.values(), key=lambda r: str(r.get("date")))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in rows2:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def archive_daily_batch(snap_path=SNAP_PATH, batches_path=BATCHES_PATH):
    """rps_latest.json → 当日 live 批次行（同 date 覆盖，幂等）。"""
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)
    res = snap.get("res") or []
    date = str(snap.get("latest") or "")
    if not res or not date:
        print("BATCH archive: empty res/date, skip")
        return None
    env_cn = next(
        (
            {"score": ev.get("score"), "safe": ev.get("safe"), "state": ev.get("state")}
            for ev in snap.get("envs", [])
            if "A股" in str(ev.get("label", ""))
        ),
        None,
    )
    row = {
        "date": date,
        "ts": str(snap.get("ts") or ""),
        "source": "live",
        "env": env_cn,
        "picks": [
            {
                "code": r.get("code"),
                "name": r.get("name"),
                "market": r.get("market"),
                "action": r.get("action"),
                "score": r.get("score"),
                "dims": r.get("dims"),
            }
            for r in res
        ],
    }
    rows = [r for r in _read_batches(batches_path) if str(r.get("date")) != date]
    rows.append(row)
    _write_batches(rows, batches_path)
    n_sel = len(_sel_picks(row["picks"]))
    print(
        "BATCH archive: %s live picks=%d CN买入/观察=%d"
        % (date, len(row["picks"]), n_sel),
        flush=True,
    )
    # W5 影子采集（gate-shadow-dev 20261014）：环境闸门三档虚拟对照
    # θ∈{40,45,50}，只记不改——双刀直接读 qd_scan_cache 产物（不改
    # qd_scanner），cross 由 market_scan 挂载点自采。注记式 try/except，
    # 采集失败不影响产线；collect_run 自身亦不抛（双保险）。
    try:
        import gate_shadow_collect as _gsc

        _gsc.collect_run(strategies=("qd", "qpzm"))
    except Exception as _ex:
        print("GATE_SHADOW fail: %s" % str(_ex)[:120], flush=True)
    # 复盘落地（breadth-mon 20261014）：宽度 45% 穿越确认 + 5 日假穿越
    # 防护监测。只告警不拦任何策略；首跑自动播种不回放历史。注记式
    # try/except，监测失败不影响产线（monitor_run 自身亦不抛）。
    try:
        import breadth_crossing_monitor as _bcm

        _bcm.monitor_run()
    except Exception as _ex:
        print("BREADTH_MONITOR fail: %s" % str(_ex)[:120], flush=True)
    # W8 议题一前置（20261015）：qpzm 自口径 vs 湖口径宽度并行对照，
    # 只记不改，攒一周以上供口径统一评审；同日幂等，注记式隔离。
    try:
        import breadth_parallel as _bpp

        _bpp.run()
    except Exception as _ex:
        print("BREADTH_PARALLEL fail: %s" % str(_ex)[:120], flush=True)
    # A1 拍板落地（20261015）：vol_target A/B 对照采集位。状态机
    # waiting→tracking（CONFIRM_HOLD 事件流闩锁翻相），预注册判据
    # 首日冻结 sha 防篡改；纯纸面记录零交易行为，注记式隔离。
    try:
        import voltarget_ab as _vab

        _vab.advance()
    except Exception as _ex:
        print("VOLTARGET_AB fail: %s" % str(_ex)[:120], flush=True)
    return row


def _env_at(env_df, d):
    """env_gate DataFrame 取某日快照。"""
    r = env_df.loc[d]
    return {
        "breadth": None if pd.isna(r["breadth"]) else float(r["breadth"]),
        "med20": None if pd.isna(r["med20"]) else float(r["med20"]),
        "score": None if pd.isna(r["score"]) else round(float(r["score"])),
        "safe": bool(r["safe"]),
    }


def _pool_codes_cn():
    """产线自选池（news_config.json watchlist）CN 票 → wide 列名集合。

    与 rps_resonance.resonance_watchlist 同源（runner.NEWS_DIR 指向
    ../news_collector）；复算批次 = 今天的池子在历史上的信号（池子随
    时间变化属已知近似，页面以「复算口径」chip 标注）。
    """
    p = os.path.abspath(os.path.join(HERE, "..", "news_collector", "news_config.json"))
    with open(p, encoding="utf-8") as f:
        wl = json.load(f)["watchlist"]
    return {c for c in (code_to_wide(e.get("code")) for e in wl) if c}


def backfill(n_days=60, batches_path=BATCHES_PATH):
    """qpzm_backtest 纯计算层复算近 n_days 个交易日批次（replay 口径）。

    限定产线自选池 CN 票（与 resonance_watchlist 同池），非全市场信号；
    JEV 缺维三维口径（tech/rps/env=0.4667/0.3333/0.2），现行权重 75/50
    闸门开。live 行不覆盖；窗口内旧 replay 行整体刷新；空批次日不归档。
    """
    import qpzm_backtest as QB

    wide = QB.load_wide(QB.DATA_DIR)
    pool = _pool_codes_cn()
    cols = [c for c in wide["codes"] if c in pool]
    if not cols:
        raise RuntimeError("pool codes not found in wide universe")
    print("BATCH pool: %d/%d CN codes in universe" % (len(cols), len(pool)), flush=True)
    env_df = QB.env_gate(wide["above20"], wide["ret20"])
    mix, _na = QB.cross_rps({p: wide["chg%d" % p] for p in QB.PERIODS}, QB.RPS_W_CUR)
    tech_w = QB.tech_score(wide["longs"], wide["above20"])
    _score, buy, obs = QB.combine_score(
        tech_w[cols],
        mix[cols],
        env_df,
        buy_th=QB.TH_CUR[0],
        obs_th=QB.TH_CUR[1],
        gate_on=True,
        eligible=wide["eligible"][cols],
    )
    sig = buy | obs
    dates = [int(d) for d in list(wide["dates"])[-int(n_days) :]]
    win = {str(d) for d in dates}
    # 窗口内旧 replay 行整体清掉（口径变更不残留），live 与窗口外不动
    existing = {
        str(r.get("date")): r
        for r in _read_batches(batches_path)
        if not (str(r.get("date")) in win and r.get("source") == "replay")
    }
    n_new = n_keep = 0
    d_first = d_last = None
    for d in dates:
        ds = str(d)
        if existing.get(ds, {}).get("source") == "live":
            n_keep += 1
            continue
        col = sig.loc[d]
        sel = col[col].index.tolist()
        if not sel:
            continue
        buy_d = buy.loc[d]
        existing[ds] = {
            "date": ds,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "replay",
            "env": _env_at(env_df, d),
            "picks": [
                {
                    "code": wide_to_code(c),
                    "name": "",
                    "market": "CN",
                    "action": "买入" if bool(buy_d.get(c)) else "观察",
                    "score": None,
                    "dims": "tech+rps+env",
                }
                for c in sel
            ],
        }
        n_new += 1
        d_first = d_first or ds
        d_last = ds
    _write_batches(existing.values(), batches_path)
    print(
        "BATCH backfill: +%d replay (%s ~ %s), live kept %d"
        % (n_new, d_first or "-", d_last or "-", n_keep),
        flush=True,
    )
    return n_new


# ── 批次回测主流程 ───────────────────────────────────────────
def _r6(x):
    return round(float(x), 6) if x is not None and not pd.isna(x) else None


def _r4(x):
    return round(float(x), 4) if x is not None and not pd.isna(x) else None


def _daily_curve(px, i, cols, max_n, cost):
    """K251 批次逐日盯市曲线：T+1 开盘进场起，每一天都给出收益，不打包。

    口径：
      等权组合，entry = T+1 开盘；全程无成交的票剔除（n_traded）；
      停牌日 close 用前值填充（ffill）→ 组合成分从进场日起恒定；
      cum_t   = mean_j(close_t/entry_j) − 1 − cost（成本按日常数扣，
                逐日差分 day_t = cum_t − cum_{t−1} 即纯日收益，
                且末日出数的 cum 与窗口口径可直接对读）；
      bench_cum = 同信号日 eligible 且 T+1 有开盘价的全体票，同口径
                  （K254 一字板剔除）；
      excess  = cum − bench_cum；窗口不足 max_n（数据尾）→ partial。
    返回 {k,date,day,cum,bench_cum,excess,n_traded,partial} 或 None。
    """
    n_av = min(int(max_n), len(px["dates"]) - 1 - i)
    if n_av < 1 or not cols:
        return None
    entry = px["open"].iloc[i + 1][cols]
    valid = entry.notna()
    if "unfill" in px:
        valid = valid & ~px["unfill"].iloc[i + 1][cols].fillna(False)
    cols_v = [c for c in cols if bool(valid.get(c))]
    if not cols_v:
        return None
    # 批次曲线
    C = px["close"].iloc[i + 1 : i + 1 + n_av][cols_v].ffill()
    cum = C.div(entry[cols_v], axis=1).mean(axis=1) - 1.0 - cost
    cum = cum.ffill()
    # 基准曲线（全体可交易票，同窗口同口径）
    e0 = px["open"].iloc[i + 1]
    bun = px["unfill"].iloc[i + 1] if "unfill" in px else None
    bcols = [
        c
        for c in px["close"].columns
        if bool(px["eligible"].iloc[i].get(c))
        and pd.notna(e0.get(c))
        and not (bun is not None and bool(bun.get(c)))
    ]
    if bcols:
        B = px["close"].iloc[i + 1 : i + 1 + n_av][bcols].ffill()
        bcum = B.div(e0[bcols], axis=1).mean(axis=1) - 1.0 - cost
        bcum = bcum.ffill()
        bcum_l = [_r6(v) for v in bcum.tolist()]
    else:
        bcum_l = [None] * n_av
    cum_l = [_r6(v) for v in cum.tolist()]
    ex_l = [
        _r6(a - b) if (a is not None and b is not None) else None
        for a, b in zip(cum_l, bcum_l)
    ]
    day_l = [cum_l[0]]
    for t in range(1, len(cum_l)):
        a, b = cum_l[t], cum_l[t - 1]
        day_l.append(_r6(a - b) if (a is not None and b is not None) else None)
    return {
        "k": list(range(1, len(cum_l) + 1)),
        "date": [int(d) for d in cum.index],
        "day": day_l,
        "cum": cum_l,
        "bench_cum": bcum_l,
        "excess": ex_l,
        "n_traded": len(cols_v),
        "partial": bool(n_av < int(max_n)),
    }


def run_batch_backtest(
    batches_path=BATCHES_PATH,
    out_path=REPORT_PATH,
    holds=HOLDS,
    cost=COST,
    data_dir=DATA_DIR,
    cost_source=None,
):
    """逐批次算 holds 各档收益/胜率/超额 → rps_batch_backtest.json。"""
    batches = _read_batches(batches_path)
    if not batches:
        print("BATCH run: no batches, skip")
        return None
    px = load_px(data_dir)
    dpos = {int(d): i for i, d in enumerate(px["dates"])}
    n_dates = len(px["dates"])
    out = []
    for b in sorted(batches, key=lambda r: str(r.get("date"))):
        date = str(b.get("date") or "")
        if date == "latest":
            # K255 自定义批次：latest=最新可回测信号日（T+1 已开盘）
            date = str(px["dates"][-2])
        picks = _sel_picks(b.get("picks"))
        row = {
            "date": date,
            "source": b.get("source"),
            "env_safe": (b.get("env") or {}).get("safe"),
            "n_picks": len(picks),
            "n_buy": sum(1 for p in picks if p["_act"] == "买入"),
            "n_obs": sum(1 for p in picks if p["_act"] == "观察"),
            "holds": {},
        }
        d = int(date) if date.isdigit() else None
        if d is None or d not in dpos:
            row["note"] = "no_bar"
        elif not picks:
            row["note"] = "empty"
        elif dpos[d] + 1 >= n_dates:
            row["note"] = "await_t1"  # 信号日=数据尾，T+1 未开
        else:
            i = dpos[d]
            cols = [c for c in (code_to_wide(p.get("code")) for p in picks) if c]
            entry = px["open"].iloc[i + 1][cols]
            unf = (
                px["unfill"].iloc[i + 1][cols].fillna(False) if "unfill" in px else None
            )
            CL = px["close"].ffill()  # K254 停牌按最后可得价结算
            for n in holds:
                partial = (i + n) > (n_dates - 1)
                i_exit = min(i + n, n_dates - 1)
                exitp = CL.iloc[i_exit][cols]
                valid = entry.notna() & exitp.notna()
                if unf is not None:
                    valid = valid & ~unf
                r = exitp[valid] / entry[valid] - 1.0 - cost
                # 基准：同信号日全体可交易股票，同窗口等权（K254 对称口径）
                bmask = (
                    px["eligible"].iloc[i]
                    & px["open"].iloc[i + 1].notna()
                    & CL.iloc[i_exit].notna()
                )
                if unf is not None:
                    bmask = bmask & ~px["unfill"].iloc[i + 1]
                br = CL.iloc[i_exit][bmask] / px["open"].iloc[i + 1][bmask] - 1.0 - cost
                raw_close = px["close"].iloc[i_exit][cols]
                n_unfill = (
                    int((entry.notna() & exitp.notna() & unf).sum())
                    if unf is not None
                    else 0
                )
                n_susp = int(
                    (
                        entry.notna()
                        & exitp.notna()
                        & raw_close.isna()
                        & (~unf if unf is not None else True)
                    ).sum()
                )
                mean = _r6(r.mean()) if len(r) else None
                bench = _r6(br.mean()) if len(br) else None
                row["holds"][str(n)] = {
                    "n_traded": int(valid.sum()),
                    "n_dropped": int(len(cols) - int(valid.sum())),
                    "n_unfillable": n_unfill,
                    "n_suspend_exit": n_susp,
                    "mean": mean,
                    "bench_mean": bench,
                    "excess": _r6(r.mean() - br.mean())
                    if (mean is not None and bench is not None)
                    else None,
                    "median": _r6(r.median()) if len(r) else None,
                    "win_rate": _r4((r > 0).mean()) if len(r) else None,
                    "partial": bool(partial),
                }
            # K251 逐日盯市：每天的收益全部展开（不打包）
            try:
                row["daily"] = _daily_curve(px, i, cols, max(holds), cost)
            except Exception as _ex:
                row["daily"] = None
                row["daily_err"] = str(_ex)[:80]
        out.append(row)
    out.reverse()  # date 倒序，页面直接渲染
    report = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "interpreter": sys.executable,
            "n_batches": len(out),
            "holds": list(holds),
            "cost_roundtrip": cost,
            "cost_source": cost_source
            or ("cli(显式)" if cost != COST else COST_SOURCE),
            "min_bars_eligible": MIN_BARS_ELIG,
            "design": (
                "T 日收盘批次 → T+1 开盘买入 → 持有 N 日收盘卖出；"
                "成本 %.2f%% 往返（默认 CN 实盘 30bp，--cost 可覆盖）；"
                "基准=同日全体可交易等权；"
                "持有期未满取最新收盘=至今收益(partial)；"
                "仅 CN 买入+观察，环境闸门回避不计；"
                "K254：后复权口径(pre_close 链)、T+1 一字板剔除"
                "(n_unfillable)、出场停牌按最后可得价结算"
                "(n_suspend_exit)，基准对称同口径"
            )
            % (cost * 100.0),
            "daily_curve": "K251 逐日盯市：T+1 开盘进场起每天给出 当日/累计/"
            "超额；等权、停牌日 close 前值填充、成本按日常数扣"
            "（cum 含 −cost，差分即纯日收益）；深度=max(holds)；"
            "K254 起后复权口径+一字板剔除",
        },
        "batches": out,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out_path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    n_ok = sum(1 for r in out if r["holds"])
    print(
        "BATCH run: %d batches (%d priced) -> %s" % (len(out), n_ok, out_path),
        flush=True,
    )
    return report


def spawn_backtest(log_path=None):
    """产线 run_daily 尾部调用：子进程跑 --run，输出落日志（不阻塞主链）。

    python.exe + DETACHED + stdin=DEVNULL + stdout/stderr 落文件
    （09-26 判例：pythonw+DETACHED 会静默秒死；重定向文件必须显式）。
    """
    log_path = log_path or os.path.join(HERE, "logs", "rps_batch_bt.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "ab")
    p = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--run"],
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    print("BATCH backtest spawned pid=%s log=%s" % (p.pid, log_path), flush=True)
    return p.pid


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/qpzm_daily_batch.py", exc)
    ap = argparse.ArgumentParser(description="青萍之末每日选股批次回测")
    ap.add_argument(
        "--backfill",
        type=int,
        default=0,
        metavar="N",
        help="回填近 N 个交易日 replay 批次",
    )
    ap.add_argument("--run", action="store_true", help="批次回测主流程")
    ap.add_argument(
        "--archive-live",
        action="store_true",
        help="从 rps_latest.json 归档当日 live 批次",
    )
    ap.add_argument(
        "--all", action="store_true", help="archive-live + backfill 60 + run"
    )
    ap.add_argument("--hold", default="5,10,20", help="持有交易日，逗号分隔")
    ap.add_argument("--cost", type=float, default=COST)
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--out", default=REPORT_PATH)
    ap.add_argument(
        "--batches",
        default=BATCHES_PATH,
        help="批次 JSONL 输入路径（K255 自定义批次用）",
    )
    args = ap.parse_args(argv)
    holds = sorted({int(x) for x in str(args.hold).split(",") if x})
    if args.all:
        args.archive_live = True
        args.backfill = args.backfill or 60
        args.run = True
    if not (args.run or args.backfill or args.archive_live):
        ap.print_help()
        return 1
    if args.archive_live:
        archive_daily_batch()
    if args.backfill:
        backfill(args.backfill)
    if args.run:
        run_batch_backtest(
            batches_path=args.batches,
            holds=holds,
            data_dir=args.data_dir,
            out_path=args.out,
            cost=args.cost,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
