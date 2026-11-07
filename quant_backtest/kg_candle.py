# -*- coding: utf-8 -*-
"""Batch 4 分时粒度：xtcloud 分钟库 → 盘口事件（CandleEvent）入图。

情报学定位：日线结构看不到日内博弈——封板迟早、炸板、尾盘抢筹是
游资行为指纹（早板强势/晚板偷袭/尾盘对倒）。新边型 HAS_CANDLE，
不干扰 HAS_STRUCTURE / HAS_VOL_EVENT 既有链路。

数据源：data/tushare/stk_mins/{code}_{ym}.parquet（890 标的 × 202509 起
1 分钟线）+ data/tdx/ts_daily 的 pre_close（涨停价基准）。

事件口径（参数透明，只依赖当日分钟 + 前收）：
· 涨停价 = round_half_up_2(pre_close × (1+ratio))
  ratio：名称含 ST 5% / 创业(300,301)·科创(688) 20% / 北交所 30% / 其余 10%
· early_seal  早盘封板：首触涨停 ≤10:30 且收盘封住（强势指纹）
· seal        午间封板：10:30~14:30 首触且收盘封住
· late_seal   尾盘封板：首触 ≥14:30 且收盘封住（偷袭指纹）
· broken      炸板：盘中触涨停但收盘未封（n_touch 触板分钟数）
· late_surge  尾盘抢筹：14:30→收盘 ≥+2% 且尾盘量占比 ≥15%（非封板日）
· late_dump   尾盘砸盘：同上反向 ≤-2%
节点：MarketEvent(kind=candle, mtime=首触HHMM, pct=收盘涨幅, lim=涨停幅度)
      evt_id = code:candle:type:yyyymmdd
边：Security -[HAS_CANDLE {validStart=trade_date, validEnd=OPEN}]-> MarketEvent

用法：
  python kg_candle.py probe 000001.SZ 20260924   # 单日调试（人工核对口径）
  python kg_candle.py build [--ym 202609]        # 全量回填（或单月）
  python kg_candle.py sync [20260929]            # 增量保鲜（每日链；无参=
                                                # 仓内最近 5 个实际交易日）
"""

import os
import sys
import math
import argparse
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import tdx_read as tdxr
import linreg  # X3 血缘登记（best-effort，不阻断）

MINS_DIR = os.path.join(HERE, "data", "tushare", "stk_mins")

EARLY_CUT, LATE_CUT = "10:30", "14:30"
LATE_MOVE, LATE_VOL_MIN = 0.02, 0.15  # 尾盘异动阈值：±2% 且量占比≥15%

# ── P3-b 手法特征对象化：type → Technique 节点（中文名 + 监管映射）─────
# 监管映射为静态人工维护表，参照沪深交易所《证券异常交易实时监控细则》
# 拉抬/打压类条款（描述性关联，非处罚认定）——reg_tag 空串=无异常关联。
TECHNIQUES = {
    "early_seal": {
        "cn": "早盘封板",
        "reg_tag": "",
        "reg_rule": "常规强势特征，无监管异常关联",
    },
    "seal": {"cn": "午间封板", "reg_tag": "", "reg_rule": "常规封板，无监管异常关联"},
    "late_seal": {
        "cn": "尾盘封板",
        "reg_tag": "尾盘维持涨幅限制价格",
        "reg_rule": "尾盘阶段拉抬/维持涨停价——《证券异常交易"
        "实时监控细则》拉抬类观察情形（尾盘段）",
    },
    "broken": {
        "cn": "炸板（盘中拉抬未遂）",
        "reg_tag": "盘中拉抬股价",
        "reg_rule": "触及涨停后回落——拉抬类行为的未遂形态，配合 n_touch/量能观察",
    },
    "late_surge": {
        "cn": "尾盘抢筹",
        "reg_tag": "尾盘拉抬股价",
        "reg_rule": "细则明确列举的尾盘拉抬类异常交易情形（收盘阶段显著抬升价格）",
    },
    "late_dump": {
        "cn": "尾盘砸盘",
        "reg_tag": "尾盘打压股价",
        "reg_rule": "细则明确列举的尾盘打压类异常交易情形（收盘阶段显著压低价格）",
    },
}


def _tech_nodes():
    """Technique 节点行（name=中文名，code=英文 type）。"""
    return [
        {
            "name": v["cn"],
            "code": k,
            "reg_tag": v["reg_tag"],
            "reg_rule": v["reg_rule"],
            "updated_at": _now(),
        }
        for k, v in TECHNIQUES.items()
    ]


def _tech_edges(evs):
    """candle 事件 → USES_TECH 边（dst=中文名，validStart=事件日）。"""
    return [
        {
            "src": e["evt_id"],
            "dst": TECHNIQUES[e["type"]]["cn"],
            "validStart": e["date"],
            "validEnd": S.VALID_END_OPEN,
            "source": "stk_mins",
            "updated_at": _now(),
        }
        for e in evs
        if e["type"] in TECHNIQUES
    ]


def sync_techniques(cli):
    """P3-b 存量回填：Technique 节点 + 图内既有 candle 事件 USES_TECH 边。"""
    cli.upsert_nodes(S.LBL_TECHNIQUE, ["name"], _tech_nodes())
    rows = (
        cli._session()
        .run(
            "MATCH (m:MarketEvent {kind:'candle'}) "
            "WHERE NOT (m)-[:USES_TECH]->() "
            "RETURN m.evt_id AS id, m.type AS t, m.date AS d"
        )
        .data()
    )
    evs = [
        {"evt_id": r["id"], "type": r["t"], "date": r["d"]}
        for r in rows
        if r["t"] in TECHNIQUES
    ]
    n = 0
    if evs:
        edges = _tech_edges(evs)
        n = len(edges)
        cli.upsert_edge(
            S.LBL_MARKET_EVENT,
            "evt_id",
            S.LBL_TECHNIQUE,
            "name",
            S.REL_USES_TECH,
            edges,
        )
    print("technique 回填：节点 %d + USES_TECH 边 %d" % (len(TECHNIQUES), n))


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _r2(x):
    """A 股价格四舍五入到分（round half up，避开银行家舍入）。"""
    return math.floor(x * 100 + 0.5) / 100


def limit_ratio(code, name):
    """涨停幅度：ST 5% / 创业·科创 20% / 北交 30% / 主板 10%。"""
    if name and "ST" in name.upper():
        return 0.05
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.endswith(".BJ"):
        return 0.30
    return 0.10


def mins_file(tc, ym):
    return os.path.join(MINS_DIR, "%s_%s.parquet" % (tc.replace(".", "_"), ym))


def detect_day(day, pre_close, code, name):
    """单日分钟 bar → 事件列表。day: 按 trade_time 升序的单日 DataFrame。"""
    evs = []
    if pre_close is None or pre_close <= 0 or len(day) < 100:
        return evs  # 停牌/半日/数据缺失
    ratio = limit_ratio(code, name)
    lim = _r2(pre_close * (1 + ratio))
    pct = day["close"].iloc[-1] / pre_close - 1
    touch = day["high"] >= lim - 1e-6

    def base(t, **kw):
        d = day["trade_time"].iloc[0].strftime("%Y%m%d")
        return {
            "evt_id": "%s:candle:%s:%s" % (code, t, d),
            "kind": "candle",
            "type": t,
            "date": d,
            "mtime": day.loc[touch, "trade_time"].iloc[0].strftime("%H%M")
            if touch.any()
            else None,
            "pct": round(pct, 4),
            "lim": ratio,
            "source": "stk_mins",
            "updated_at": _now(),
            **kw,
        }

    sealed = day["close"].iloc[-1] >= lim - 1e-6
    if touch.any() and sealed:
        t0 = day.loc[touch, "trade_time"].iloc[0].strftime("%H:%M")
        typ = (
            "early_seal"
            if t0 <= EARLY_CUT
            else "late_seal"
            if t0 >= LATE_CUT
            else "seal"
        )
        evs.append(base(typ, n_touch=int(touch.sum())))
    elif touch.any():
        evs.append(base("broken", n_touch=int(touch.sum())))
    else:
        # 尾盘异动只在未触板日判（触板日的尾盘行为已被 seal/broken 覆盖）
        tail = day[day["trade_time"].dt.strftime("%H:%M") >= LATE_CUT]
        if len(tail) > 5:
            ref = tail["close"].iloc[0]
            mv = day["close"].iloc[-1] / ref - 1
            vshare = tail["amount"].sum() / max(day["amount"].sum(), 1)
            if vshare >= LATE_VOL_MIN and abs(mv) >= LATE_MOVE:
                t = "late_surge" if mv > 0 else "late_dump"
                evs.append(base(t, n_touch=int(touch.sum()), tail_vol=round(vshare, 3)))
    return evs


def scan_month(tc, ym, pre_map, name_map):
    """读单标的单月分钟文件 → 事件节点 + 边。"""
    f = mins_file(tc, ym)
    if not os.path.exists(f):
        return [], []
    df = pd.read_parquet(f)
    if df.empty:
        return [], []
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    name = name_map.get(tc, "")
    evs, edges = [], []
    for d, day in df.groupby(df["trade_time"].dt.strftime("%Y%m%d")):
        day = day.sort_values("trade_time")
        pre = pre_map.get((tc, d))
        for e in detect_day(day, pre, tc, name):
            evs.append(e)
            edges.append(
                {
                    "src": tc,
                    "dst": e["evt_id"],
                    "validStart": d,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "stk_mins",
                    "updated_at": _now(),
                }
            )
    return evs, edges


def _names(cli):
    return {
        r["tc"]: r["nm"]
        for r in cli._session().run(
            "MATCH (s:Security) RETURN s.ts_code AS tc, s.name AS nm"
        )
    }


def _pres(start):
    """日线池前收映射 {(tc, yyyymmdd): pre_close}。"""
    df = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "pre_close"]
    )
    df["trade_date"] = df["trade_date"].astype(str).str[:8]
    return {(r.ts_code, r.trade_date): r.pre_close for r in df.itertuples()}


def build(cli, ym=None, dryrun=False):
    yms = (
        [ym]
        if ym
        else sorted(
            {
                f.rsplit("_", 1)[1][:6]
                for f in os.listdir(MINS_DIR)
                if f.endswith(".parquet")
            }
        )
    )
    tcs = sorted(
        {
            f.split("_")[0] + "." + f.split("_")[1]
            for f in os.listdir(MINS_DIR)
            if f.endswith(".parquet")
        }
    )
    print("分钟库：%d 标的 × %d 个月（%s~%s）" % (len(tcs), len(yms), yms[0], yms[-1]))
    name_map = _names(cli)
    pre_map = _pres(yms[0][:4] + yms[0][4:] + "01")
    tot_e = tot_ed = 0
    for y in yms:
        evs, edges = [], []
        for tc in tcs:
            e, ed = scan_month(tc, y, pre_map, name_map)
            evs += e
            edges += ed
        tot_e += len(evs)
        tot_ed += len(edges)
        by = {}
        for e in evs:
            by[e["type"]] = by.get(e["type"], 0) + 1
        print("  %s: 事件 %d 条 %s" % (y, len(evs), by))
        if not dryrun and evs:
            for _e in evs:
                _e["data_source"] = "tdx_minute"  # F2-P2-b
            cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
            cli.upsert_edge(
                S.LBL_SECURITY,
                "ts_code",
                S.LBL_MARKET_EVENT,
                "evt_id",
                "HAS_CANDLE",
                edges,
            )
            # P3-b：手法对象化（Technique 节点幂等 upsert + USES_TECH 边）
            cli.upsert_nodes(S.LBL_TECHNIQUE, ["name"], _tech_nodes())
            cli.upsert_edge(
                S.LBL_MARKET_EVENT,
                "evt_id",
                S.LBL_TECHNIQUE,
                "name",
                S.REL_USES_TECH,
                _tech_edges(evs),
            )
    print("合计：%s事件 %d / 边 %d" % ("（dryrun）" if dryrun else "", tot_e, tot_ed))
    if not dryrun:
        n = (
            cli._session()
            .run("MATCH ()-[r:HAS_CANDLE]->() RETURN count(r) AS c")
            .single()["c"]
        )
        print("图谱 HAS_CANDLE 总边数：%d" % n)


def probe(cli, tc, date):
    """单日调试：打印口径中间量，人工核对。"""
    ym = date[:6]
    name_map = _names(cli)
    pre_map = _pres(date[:4] + date[4:6] + "01")
    f = mins_file(tc, ym)
    if not os.path.exists(f):
        print("无分钟文件 %s" % f)
        return
    df = pd.read_parquet(f)
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    day = df[df["trade_time"].dt.strftime("%Y%m%d") == date].sort_values("trade_time")
    if day.empty:
        print("%s %s 无分钟数据" % (tc, date))
        return
    pre = pre_map.get((tc, date))
    name = name_map.get(tc, "")
    ratio = limit_ratio(tc, name)
    lim = _r2(pre * (1 + ratio)) if pre else None
    print("=== %s %s（%s）===" % (tc, date, name))
    print("  前收 %.2f × (1+%.0f%%) → 涨停价 %.2f" % (pre, ratio * 100, lim))
    print(
        "  日内 high=%.2f close=%.2f（首根 %s ~ 末根 %s，%d 根）"
        % (
            day["high"].max(),
            day["close"].iloc[-1],
            day["trade_time"].iloc[0].strftime("%H:%M"),
            day["trade_time"].iloc[-1].strftime("%H:%M"),
            len(day),
        )
    )
    for e in detect_day(day, pre, tc, name):
        print(
            "  → 事件 %s mtime=%s pct=%.2f%% n_touch=%s %s"
            % (
                e["type"],
                e["mtime"],
                e["pct"] * 100,
                e.get("n_touch"),
                e.get("tail_vol", ""),
            )
        )


def _cur_prev_ym():
    """当月+上月（YYYYMM 集合）——对齐 TushareSync 增量重拉窗口。"""
    now = datetime.now()
    return {
        now.strftime("%Y%m"),
        (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m"),
    }


def _recent_dates(n=5):
    """仓内分钟数据实际存在的最近 n 个交易日（YYYYMMDD 升序）。

    TushareSync 每晚 23:30 才拉当日分钟（T+1 入仓），每日链 06:20 跑时
    "今日"数据必然缺席——增量口径必须锚定仓内实际日期而非自然日历
    （OPS 教训 2026-10-09：sync 只看今日 → 常态空转，全靠手动补数）。"""
    yms = _cur_prev_ym()
    dates = set()
    for f in os.listdir(MINS_DIR):
        if not f.endswith(".parquet") or f.rsplit("_", 1)[1][:6] not in yms:
            continue
        df = pd.read_parquet(os.path.join(MINS_DIR, f), columns=["trade_time"])
        if not df.empty:
            dates.update(
                pd.to_datetime(df["trade_time"]).dt.strftime("%Y%m%d").unique()
            )
    return sorted(dates)[-n:]


def sync(cli, date=None, lookback=5):
    """每日保鲜：默认回补仓内最近 lookback 个实际交易日；date=单日补历史。

    幂等重刷刻意为之——对齐 TushareSync"删当月+上月重拉"的修正语义，
    上游重拉修正过的分钟数据会被重新吸收；常态 T+1：今晨链处理昨晚
    入仓的昨日数据，断链/长假后的存量日自动补齐。"""
    _jr = linreg.JobRun("kg_daily_sync", "candle_sync").__enter__()  # X3 血缘
    dates = [date] if date else _recent_dates(lookback)
    if not dates:
        print("sync：仓内无分钟数据（%s）" % "/".join(sorted(_cur_prev_ym())))
        _jr.finish("ok")  # X3 血缘
        return
    name_map = _names(cli)
    pre_map = _pres(min(dates)[:6] + "01")
    want, yms = set(dates), sorted({d[:6] for d in dates})
    tcs = sorted(
        {
            f.split("_")[0] + "." + f.split("_")[1]
            for f in os.listdir(MINS_DIR)
            if f.endswith(".parquet")
        }
    )
    evs, edges = [], []
    for tc in tcs:
        for ym in yms:
            f = mins_file(tc, ym)
            if not os.path.exists(f):
                continue
            df = pd.read_parquet(f, columns=["trade_time", "close", "high", "amount"])
            df["trade_time"] = pd.to_datetime(df["trade_time"])
            df = df[df["trade_time"].dt.strftime("%Y%m%d").isin(want)]
            for d, day in df.groupby(df["trade_time"].dt.strftime("%Y%m%d")):
                day = day.sort_values("trade_time")
                for e in detect_day(
                    day, pre_map.get((tc, d)), tc, name_map.get(tc, "")
                ):
                    evs.append(e)
                    edges.append(
                        {
                            "src": tc,
                            "dst": e["evt_id"],
                            "validStart": d,
                            "validEnd": S.VALID_END_OPEN,
                            "source": "stk_mins",
                            "updated_at": _now(),
                        }
                    )
    by = {}
    for e in evs:
        by[e["type"]] = by.get(e["type"], 0) + 1
    print("sync %s~%s：事件 %d %s" % (dates[0], dates[-1], len(evs), by))
    if evs:
        cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
        cli.upsert_edge(
            S.LBL_SECURITY, "ts_code", S.LBL_MARKET_EVENT, "evt_id", "HAS_CANDLE", edges
        )
        # P3-b：手法对象化（每日增量同步顺带写）
        cli.upsert_nodes(S.LBL_TECHNIQUE, ["name"], _tech_nodes())
        cli.upsert_edge(
            S.LBL_MARKET_EVENT,
            "evt_id",
            S.LBL_TECHNIQUE,
            "name",
            S.REL_USES_TECH,
            _tech_edges(evs),
        )
        # X3 血缘：输入分钟库 → 输出盘口事件/手法边
        _jr.edge(
            "neo4j:HAS_CANDLE", "parquet:stk_mins", transform="candle_detect"
        )  # X3
        _jr.edge("neo4j:Technique", "parquet:stk_mins")  # X3
        _jr.rows_written = len(evs) + len(edges)  # X3
    _jr.finish("ok")  # X3 血缘（显式收尾，异常路径靠 __del__ 记 lost）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "build", "sync", "technique"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    if a.cmd == "probe":
        probe(KgClient(retry=1), a.args[0], a.args[1])
        return
    cli = KgClient(retry=1)
    try:
        if a.cmd == "build":
            ym = a.args[a.args.index("--ym") + 1] if "--ym" in a.args else None
            build(cli, ym=ym, dryrun=a.dryrun)
        elif a.cmd == "sync":
            sync(cli, a.args[0] if a.args else None)
        elif a.cmd == "technique":
            sync_techniques(cli)
    finally:
        cli.close()


from kg_client import KgClient  # noqa: E402


if __name__ == "__main__":
    main()
