# -*- coding: utf-8 -*-
"""榜后差额资金穿透夜批（KG-GAP P2-①）。

口径（2026-10-01 探测定案，53,509 行实证）：
· 榜后差额 gap = l_buy - l_sell（== net_amount，全表 0 误差恒等）
  = 龙虎榜买方前五合计 - 卖方前五合计，单位元；
· gap_ratio = gap / 当日总成交额 amount（失衡强度归一；预演：gap_ratio
  前 25% 组次日胜率 59.6% vs 全体 56.7%，单调有效）；
· 席位性质：ts_top_inst 明细按词表分类（优先级 youzi > quant > org >
  other；youzi 复用 kg_build_cl1.YOUZI，retail_cluster → retail）：
    quant  市场高共识量化席位（华鑫上海分 4,280 行/1,965 票、
           国信浙江互联网 3,459/781、东亚前海上海 1,123/665——
           高频复访同票，量化打板行为指纹）
    org    机构通道（机构专用/沪深股通/总部/外资行/中金上海/中信上海，
           口径对齐 kg_communities.UNREG_EXCL）
  每笔按五类净额（youzi/quant/org/retail/other）记录；
  dom_nature = |净额| 最大类；quant_ratio = quant 净额/|gap|；
  覆盖 93.8%（缺 BJ 2,772 + SH 567 行无席位明细，性质字段 null）；
· 次日穿透（K14 同口径，tdx ts_daily）：
    ret1   次日收盘收益 / oret1 次日开盘跳空（vs 上榜日收盘）
    pen    = 1 if gap 与 ret1 同向 else 0（榜后差额是否打进次日价格；
            最近一交易日事件 ret1 未到，pen=null 次日补齐）

写图（全量重算幂等，MERGE on evt_id；词表/口径更新自动回填）：
  MarketEvent(kind=topgap)  evt_id = ts_code:topgap:yyyymmdd
  Security -[HAS_TOPGAP]-> MarketEvent（新边型；MATCH 池外票静默跳过）

挂载：start_kg_sync.ps1 步骤 dt_gap（seat_winrate 后、kg_features 前，
守"图写入先于特征重算"纪律——deg_total 计入任意边型）。
展示：kg_portal 个股页 topgaps 块 + hm_profile 榜后差额段。

用法：
  python kg_dt_gap.py            # 全量重算入图
  python kg_dt_gap.py --dryrun   # 只算不写
"""

import argparse
import json
import os
import re
import time
from datetime import datetime

import pandas as pd

import kg_schema as S
import tdx_read as tdxr
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）
import logging

# ── 量化席位词表 v1（市场高共识，regex 匹配 ts_top_inst.exalter）──
QUANT_SEATS = (
    r"^华鑫证券有限责任公司上海分公司$",
    r"^国信证券股份有限公司浙江互联网分公司$",
    r"^东亚前海证券有限责任公司上海分公司$",
)
ORG_PAT = (
    "机构专用|沪股通|深股通|总部|高盛|瑞银|摩根大通|"
    "中国国际金融股份有限公司上海分公司|"
    "中信证券股份有限公司上海分公司"
)
NATURES = ("youzi", "quant", "org", "retail", "other")

# ── CH-07 tushare 兜底接线（WO-QT-20261020-067；根因 WO-021 CH-06/CH-07）──
# 板块日线锚（topgap 链 → sector_flow_latest.json）断线根因在远端 TDX 采集
# 段（①②），tushare 侧 top_list 兜底数据 tushare_sync.py 步骤4b 已在本地
# （data/tushare/top_list/d_*.parquet，列名与 TDX ts_top_list 同名可直拼）。
# 本分支在锚断 >N 交易日时经 tushare 补位缺日；**兜底产物显式标注
# source=tushare_fallback，不冒充真锚**（消费侧可按 source 甄别）。
# 硬边界：开关默认关（KG_DT_GAP_TS_FALLBACK=1 或 config.json
# dt_gap.tushare_fallback=true 显式开启才启用）；token 只从环境变量
# TUSHARE_TOKEN（缺省回落仓内 .env.local XT_DATA_KEY，tushare_sync 同通道）
# 内存读取，不打印不落盘；无凭据/日历时显式降级 fallback_unavailable
# （缺日本实留在产物日期上，H5 照常可见），绝不假绿。
TS_FALLBACK_ENV = "KG_DT_GAP_TS_FALLBACK"  # 兜底开关（默认关）
TS_FALLBACK_N_ENV = "KG_DT_GAP_TS_FALLBACK_N"  # 锚断阈值 N（交易日）
TS_FALLBACK_TOKEN_ENV = "TUSHARE_TOKEN"  # 凭据（环境变量优先）
TS_FALLBACK_SRC = "tushare_fallback"  # 兜底产物来源标注
TS_FALLBACK_STALE_N = 1  # 锚断 >N 交易日才补位（默认 1：落后 ≥2 交易日即补）
TS_FALLBACK_SLEEP = 0.35  # 每日一拉间隔（沿 tushare_sync.SLEEP 口径）
TS_FALLBACK_MAX_DAYS = 7  # 单次补位缺日上限（对齐步骤4b 近 7 天窗口）
TS_TOP_COLS = [
    "ts_code",
    "trade_date",
    "name",
    "close",
    "pct_change",
    "net_amount",
    "l_buy",
    "l_sell",
    "amount",
    "reason",
]
TS_API = "https://api.tushare.pro"
TS_TOP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "tushare", "top_list"
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _r2(v):
    return None if v is None or pd.isna(v) else round(float(v), 2)


def _r4(v):
    return None if v is None or pd.isna(v) else round(float(v), 4)


def _int(v):
    return None if v is None or pd.isna(v) else int(v)


def _nature_map(seats):
    """席位名列表 → {席位: 性质}。优先级 youzi > quant > org > other。"""
    from kg_build_cl1 import YOUZI

    yz = [(re.compile(p), kind) for _, pats, kind in YOUZI for p in pats]
    rx_q = re.compile("|".join(QUANT_SEATS))
    rx_o = re.compile(ORG_PAT)
    out = {}
    for s in seats:
        nat = None
        for rx, kind in yz:
            if rx.search(s):
                nat = "retail" if kind == "retail_cluster" else "youzi"
                break
        if nat is None:
            nat = "quant" if rx_q.search(s) else ("org" if rx_o.search(s) else "other")
        out[s] = nat
    return out


# ────────────────────────────────────────────────
# CH-07 tushare 兜底（离线可测：_post/_sleep/_local/_fetch/_calendar
# 全部可注入，单测不发起真实网络请求）
# ────────────────────────────────────────────────
def ts_fallback_enabled(env=None, cfg_path=None):
    """兜底开关：env KG_DT_GAP_TS_FALLBACK∈{1,true,on} 或 config.json
    dt_gap.tushare_fallback=true，任一显式开启才启用；缺省关。"""
    e = os.environ if env is None else env
    if str(e.get(TS_FALLBACK_ENV, "")).strip().lower() in ("1", "true", "on"):
        return True
    p = cfg_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False
    return (cfg.get("dt_gap") or {}).get("tushare_fallback") is True


def ts_fallback_stale_n(env=None):
    """锚断阈值 N（缺日 >N 交易日才补位）；env 可覆盖，坏值回落默认。"""
    e = os.environ if env is None else env
    try:
        return max(0, int(e.get(TS_FALLBACK_N_ENV, TS_FALLBACK_STALE_N)))
    except (TypeError, ValueError):
        return TS_FALLBACK_STALE_N


def _ts_token(env=None):
    """凭据：env TUSHARE_TOKEN 优先，回落仓内 .env.local XT_DATA_KEY
    （tushare_sync 同通道）。只内存传递，不打印不落盘。"""
    e = os.environ if env is None else env
    t = (e.get(TS_FALLBACK_TOKEN_ENV) or "").strip()
    if t:
        return t
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
    try:
        with open(f, encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("XT_DATA_KEY="):
                    return ln.split("=", 1)[1].strip()
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_dt_gap.py", exc)
    return ""


def _default_post(api, params, token, timeout):
    """生产默认 HTTP 出口（懒加载，import 时不触网）。"""
    import requests

    return requests.post(
        TS_API,
        json={"api_name": api, "token": token, "params": params, "fields": ""},
        timeout=timeout,
    )


def _ts_call(api, params, token, post, retry=4, timeout=60, _sleep=None):
    """对齐 tushare_sync.ts_call 既有口径：网络异常退避 2*(i+1)；
    限频文案变体全匹配（"每分钟/频率超限/次/分钟"→61s，"每小时/
    次/小时"→301s；cb_daily 2026-09-28 教训）；其余错误直接 raise。"""
    sleep = _sleep or time.sleep
    last = ""
    for i in range(retry):
        try:
            d = post(api, params, token, timeout).json()
        except Exception as e:  # noqa: BLE001
            last = str(e)[:80]
            sleep(2 * (i + 1))
            continue
        if d.get("code") == 0:
            return d.get("data") or {}
        last = str(d.get("msg", ""))[:100]
        if "每分钟" in last or "频率超限" in last or "次/分钟" in last:
            sleep(61)
            continue
        if "每小时" in last or "次/小时" in last:
            sleep(301)
            continue
        raise RuntimeError("%s: %s" % (api, last))
    raise RuntimeError("%s %s: %s" % (api, params, last))


def fetch_remote_top_list(dates, token, _post=None, _sleep=None):
    """经 tushare Pro top_list 按日拉取补位（offset 翻页 + 页间
    sleep(TS_FALLBACK_SLEEP) 控速，防失控上限 50 万行沿 pull_paged）。
    返回 (df, got_dates)。token 只经内存传参。"""
    post = _post or _default_post
    sleep = _sleep or time.sleep
    dfs, got = [], []
    for i, d in enumerate(dates):
        if i:
            sleep(TS_FALLBACK_SLEEP)
        rows, fields, off = [], None, 0
        while True:
            data = _ts_call(
                "top_list", {"trade_date": d, "offset": off}, token, post, _sleep=sleep
            )
            if fields is None:
                fields = data.get("fields") or []
            items = data.get("items") or []
            if not items:
                break
            rows.extend(items)
            off += len(items)
            if len(items) < 1000 or len(rows) >= 500000:
                break
            sleep(TS_FALLBACK_SLEEP)
        if rows and fields:
            dfs.append(pd.DataFrame(rows, columns=fields).reindex(columns=TS_TOP_COLS))
            got.append(d)
    return (
        pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=TS_TOP_COLS)
    ), got


def load_local_top_list(dates, root=None):
    """本地兜底源（tushare_sync 步骤4b 产物 d_*.parquet，免网络优先）。
    返回 (df, got_dates)。"""
    root = root or TS_TOP_DIR
    dfs, got = [], []
    for d in dates:
        f = os.path.join(root, "d_%s.parquet" % d)
        if not os.path.exists(f):
            continue
        try:
            df = pd.read_parquet(f)
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        dfs.append(df.reindex(columns=TS_TOP_COLS))
        got.append(d)
    return (
        pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=TS_TOP_COLS)
    ), got


def _local_calendar(root=None):
    """期望交易日历：本地 top_list d_*.parquet 日期清单（步骤4b 每日
    滚动采近 7 天，独立于 TDX 采集链）。缺文件 → 空（显式降级）。"""
    root = root or TS_TOP_DIR
    try:
        fs = os.listdir(root)
    except OSError:
        return []
    return sorted(
        f[2:10]
        for f in fs
        if (f.startswith("d_") and f.endswith(".parquet") and f[2:10].isdigit())
    )


def missing_anchor_dates(tdx_dates, calendar, stale_n=TS_FALLBACK_STALE_N):
    """锚断判定。tdx_dates: TDX 已有交易日集合；calendar: 期望交易日历
    （升序）。返回 (缺日列表, 落后交易日数)：落后 = 日历中晚于 TDX 最新
    日的交易日数，> stale_n 才触发补位。"""
    cal = sorted(str(d) for d in calendar)
    tmax = max((str(d) for d in tdx_dates), default=None)
    missing = [d for d in cal if tmax is None or d > tmax]
    return (missing, len(missing)) if len(missing) > stale_n else ([], len(missing))


def apply_tushare_fallback(
    tl,
    verbose=True,
    env=None,
    stale_n=None,
    _local=None,
    _fetch=None,
    _calendar=None,
    _token=None,
):
    """板块锚断 >N 交易日 → 经 tushare 补位缺日（本地 parquet 优先，
    缺口经 API 拉取）。返回 (tl, fb_keys, status)：
      fb_keys  兜底行 (ts_code, trade_date) 集合 → 事件/边显式标
               source=tushare_fallback（不冒充真锚，消费侧可甄别）；
      status   disabled / fresh / ok / fallback_unavailable（锚断但
               无凭据/无日历/拉取失败——显式降级非假绿，缺日本实
               留在产物日期上，H5 照常判红）。
    """
    if not ts_fallback_enabled(env):
        return tl, set(), "disabled"
    n = ts_fallback_stale_n(env) if stale_n is None else stale_n
    calendar = (_calendar or _local_calendar)()
    if not calendar:
        if verbose:
            print(
                "  [ts_fallback] fallback_unavailable：交易日历缺失"
                "（data/tushare/top_list/d_*.parquet 无件，先跑 tushare_sync）"
            )
        return tl, set(), "fallback_unavailable"
    missing, stale = missing_anchor_dates(
        set(tl["trade_date"].astype(str)), calendar, n
    )
    if not missing:
        return tl, set(), "fresh"
    missing = missing[-TS_FALLBACK_MAX_DAYS:]  # 单次补位上限（近 7 日窗）
    if verbose:
        print(
            "  [ts_fallback] 锚断 %d 交易日（>%d）→ 缺日 %s"
            % (stale, n, ",".join(missing))
        )
    # TDX 与 tushare trade_date 混拼前统一 str，防 min/max 混型
    tl = tl.copy()
    tl["trade_date"] = tl["trade_date"].astype(str)
    local, got = (_local or load_local_top_list)(missing)
    rest = [d for d in missing if d not in got]
    fb = local
    if rest:
        token = (_token or _ts_token)(env)
        if not token:
            if verbose:
                print(
                    "  [ts_fallback] fallback_unavailable：缺 %s 且无凭据"
                    "（TUSHARE_TOKEN / .env.local XT_DATA_KEY 均未配置）"
                    % ",".join(rest)
                )
            return tl, set(), "fallback_unavailable"
        try:
            rdf, rgot = (_fetch or fetch_remote_top_list)(rest, token)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print("  [ts_fallback] 远端拉取失败（%s），缺日保留断口" % str(e)[:100])
            rdf, rgot = pd.DataFrame(columns=TS_TOP_COLS), []
        if len(rdf):
            fb = pd.concat([fb, rdf], ignore_index=True) if len(fb) else rdf
            got = got + rgot
    if not len(fb):
        return tl, set(), "fallback_unavailable"
    fb_keys = set(zip(fb["ts_code"], fb["trade_date"].astype(str)))
    if verbose:
        print(
            "  [ts_fallback] 补位 %d 日 %d 行（source=%s）"
            % (len(got), len(fb), TS_FALLBACK_SRC)
        )
    return pd.concat([tl, fb], ignore_index=True), fb_keys, "ok"


def calc(verbose=True):
    """返回 (evs, edges, stats)，不写库。"""
    tl = tdxr.read_range("ts_top_list")
    if tl.empty:
        # CH-07：TDX 空表时仍走兜底分支（全量缺日形态），补不齐再退出
        tl = pd.DataFrame(columns=TS_TOP_COLS)
    tl, fb_keys, fb_status = apply_tushare_fallback(tl, verbose=verbose)
    if tl.empty:
        raise SystemExit(
            "ts_top_list 为空且 tushare 兜底未补齐（status=%s）："
            "先跑 TdxSync，或 KG_DT_GAP_TS_FALLBACK=1 显式开启兜底"
            "（WO-021 CH-07）" % fb_status
        )
    ti = tdxr.read_range(
        "ts_top_inst", columns=["ts_code", "trade_date", "exalter", "net_buy"]
    )
    if verbose:
        print(
            "  ts_top_list %d 行（%s~%s）· ts_top_inst %d 行"
            % (len(tl), tl["trade_date"].min(), tl["trade_date"].max(), len(ti))
        )

    # ── 席位性质分类 + (票,日) 五类净额 ──
    nmap = _nature_map(sorted(ti["exalter"].unique()))
    ti["nat"] = ti["exalter"].map(nmap)
    agg = (
        ti.groupby(["ts_code", "trade_date", "nat"])["net_buy"]
        .sum()
        .unstack("nat", fill_value=0.0)
    )
    for c in NATURES:
        if c not in agg.columns:
            agg[c] = 0.0
    agg = agg[list(NATURES)].reset_index()
    na = agg[list(NATURES)].abs()
    agg["dom_nature"] = na.idxmax(axis=1).where(na.sum(axis=1) > 0, None)
    if verbose:
        dist = pd.Series(nmap).value_counts().to_dict()
        print("  席位分类 %d 个（%s）" % (len(nmap), dist))

    # ── 榜单合并：gap / gap_ratio / 性质字段 ──
    tl["trade_date"] = tl["trade_date"].astype(str)
    m = tl.merge(agg, on=["ts_code", "trade_date"], how="left")
    m["gap"] = m["net_amount"]
    m["gap_ratio"] = (m["gap"] / m["amount"]).where(m["amount"] > 0)
    m["quant_ratio"] = (m["quant"] / m["gap"].abs()).where(m["gap"] != 0)

    # ── 次日穿透（ts_daily，K14 同口径）──
    dd = tdxr.read_range(
        "ts_daily",
        codes=set(tl["ts_code"]),
        columns=["ts_code", "trade_date", "close", "open"],
    )
    dd["trade_date"] = dd["trade_date"].astype(str)
    dd = dd.sort_values(["ts_code", "trade_date"])
    g = dd.groupby("ts_code")
    dd["ret1"] = g["close"].shift(-1) / dd["close"] - 1.0
    dd["oret1"] = g["open"].shift(-1) / dd["close"] - 1.0
    m = m.merge(
        dd[["ts_code", "trade_date", "ret1", "oret1"]],
        on=["ts_code", "trade_date"],
        how="left",
    )
    ok = m["ret1"].notna() & m["gap"].notna() & (m["gap"] != 0)
    m["pen"] = None
    m.loc[ok, "pen"] = ((m.loc[ok, "gap"] > 0) == (m.loc[ok, "ret1"] > 0)).astype(int)
    if verbose:
        v = m[ok]
        hi = v[v["gap_ratio"] >= v["gap_ratio"].quantile(0.75)]
        print(
            "  穿透：有效 %d 笔 · pen 胜率 %.1f%% · gap_ratio 前25%%组 "
            "%.1f%% · 主导分布 %s"
            % (
                len(v),
                100.0 * v["pen"].astype(float).mean(),
                100.0 * hi["pen"].astype(float).mean(),
                v["dom_nature"].value_counts().head(4).to_dict(),
            )
        )

    # ── 事件行（to_dict 防止 itertuples 改名列陷阱，kg_bclass 判例）──
    # CH-07：兜底行显式标 source=tushare_fallback，不冒充真锚
    evs, edges = [], []
    ts = _now()
    for r in m.to_dict("records"):
        tc, d = r["ts_code"], r["trade_date"]
        src = TS_FALLBACK_SRC if (tc, d) in fb_keys else "ts_top_list"
        eid = "%s:topgap:%s" % (tc, d)
        reason = (r.get("reason") or "").strip()
        evs.append(
            {
                "evt_id": eid,
                "kind": "topgap",
                "date": d,
                "name": r.get("name"),
                "close": _r2(r.get("close")),
                "pct_chg": _r2(r.get("pct_change")),
                "amount": _r2(r.get("amount")),
                "gap": _r2(r.get("gap")),
                "l_buy": _r2(r.get("l_buy")),
                "l_sell": _r2(r.get("l_sell")),
                "gap_ratio": _r4(r.get("gap_ratio")),
                "reason": reason[:64] if reason else None,
                "yz_net": _r2(r.get("youzi")),
                "qt_net": _r2(r.get("quant")),
                "og_net": _r2(r.get("org")),
                "rt_net": _r2(r.get("retail")),
                "ot_net": _r2(r.get("other")),
                "dom_nature": r.get("dom_nature"),
                "quant_ratio": _r4(r.get("quant_ratio")),
                "ret1": _r4(r.get("ret1")),
                "oret1": _r4(r.get("oret1")),
                "pen": _int(r.get("pen")),
                "source": src,
                "updated_at": ts,
            }
        )
        edges.append(
            {
                "src": tc,
                "dst": eid,
                "validStart": d,
                "validEnd": S.VALID_END_OPEN,
                "source": src,
                "updated_at": ts,
            }
        )

    v = m[ok]
    hi = v[v["gap_ratio"] >= v["gap_ratio"].quantile(0.75)]
    stats = (
        "topgap %d 事件/边（%s~%s）· 性质覆盖 %.1f%% · pen 有效 %d "
        "胜率 %.1f%% · gap_ratio 前25%%组 %.1f%% · 量化主导 %d 笔"
        % (
            len(evs),
            tl["trade_date"].min(),
            tl["trade_date"].max(),
            100.0 * m["dom_nature"].notna().mean(),
            len(v),
            100.0 * v["pen"].astype(float).mean(),
            100.0 * hi["pen"].astype(float).mean(),
            int((v["dom_nature"] == "quant").sum()),
        )
    )
    # CH-07：兜底情况入 stats（fallback_unavailable 显式可见，不假绿）
    if fb_keys:
        stats += " · tushare兜底 %d 行 source=%s" % (len(fb_keys), TS_FALLBACK_SRC)
    elif fb_status == "fallback_unavailable":
        stats += " · tushare兜底 fallback_unavailable（缺日未补，断口可见）"
    return evs, edges, stats


def main():
    _jr = linreg.JobRun("kg_daily_sync", "dt_gap").__enter__()  # X3 血缘
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    print("[dt_gap] 榜后差额资金穿透重算（ts_top_list × top_inst × ts_daily）")
    evs, edges, stats = calc()
    if a.dryrun:
        print("  dryrun：跳过写库")
        print("  " + stats)
        _jr.finish("ok")  # X3 血缘（显式收尾）
        return
    cli = KgClient()
    try:
        # OPS-1004 降噪：只保留图内 Security 存在的标的。可转债/B股/
        # 北交所/未入图次新上榜时 upsert_edge 的 src 匹配不到 → 边被
        # 静默跳过 → 孤儿事件天天触发 kg_check orphan_evt 红项
        # （消费端 kg_portal/sector_flow 均经 Security 关联，缺边事件
        # 本就不可见，过滤不丢信息；次新在 Security 补建后随窗口补入）。
        with cli._session() as s:
            have = set(
                x["c"]
                for x in s.run("MATCH (sec:Security) RETURN sec.ts_code AS c").data()
            )
        n0 = len(evs)
        evs = [e for e in evs if e["evt_id"].split(":")[0] in have]
        edges = [e for e in edges if e["src"] in have]
        if n0 - len(evs):
            print(
                "  过滤非 Security 标的 %d 条（转债/B股/北交所/次新）" % (n0 - len(evs))
            )
        for _e in evs:
            _e["data_source"] = "ts_top_list"  # F2-P2-b
        n1 = cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
        n2 = cli.upsert_edge(
            S.LBL_SECURITY,
            "ts_code",
            S.LBL_MARKET_EVENT,
            "evt_id",
            S.REL_HAS_TOPGAP,
            edges,
        )
        with cli._session() as s:
            n = s.run(
                "MATCH ()-[r:%s]->() RETURN count(r) AS c" % S.REL_HAS_TOPGAP
            ).single()["c"]
    finally:
        cli.close()
    print("  写入 MarketEvent %d / HAS_TOPGAP 边 %d（图内现存 %d）" % (n1, n2, n))
    print("  " + stats)
    # X3 血缘：榜后差额穿透 → HAS_TOPGAP 边
    _jr.edge("neo4j:HAS_TOPGAP", "parquet:ts_top_list", transform="penetration")  # X3
    _jr.rows_written = n1 + n2  # X3
    _jr.finish("ok")  # X3 血缘（显式收尾）


if __name__ == "__main__":
    main()
