# -*- coding: utf-8 -*-
"""九维量化选股体系（升级版）· P1 影子评分引擎（纯门户展示，不推送）。

K33 重定位（2026-10-02，V3 复评证据驱动）：总分语义反转——高分=拥挤
警报（回避位）非买入信号。依据 51 锚点/5479 样本回放：分数与前向收益
单调负相关（thinned r20：lt65 -9.2% → 85+ -71.5%，胜率 25%→0%），
高分构成=动量+机构+情绪+资金拥挤代理；≥75 入警报池跟踪（预期负收益
=警报有效），BUY_TH=90 退役仅作参照。每日 06:40 用前一晚 23:35 同步的
最近收盘数据跑两段式评分：
  ① 零 API 本地粗筛（技术结构 20 分是高分票必拿项，只滤掉拿不到的票）
  ② 候选池(≤CAND_CAP) 补齐 API 因子 → 完整评分 → 快照/入池/战绩

V2 结构修订（2026-09-27，P2 回放证据驱动；组满分不变：
质量15/盈利12/环境10/板块10/机构15/情绪8/动量12/估值10/技术8=100）：
· 移除 e3「年度预测上修」——up_q4 极稀有伪判据，其 2 分并入 e1；
· e1 EPS上调≥2次→≥1次（6→8分）、f2 融资周增≥10%→≥5%、
  m3 券商覆盖≥5篇→≥3篇（回放显示三者旧阈值下几乎恒 False）；
· f1 北向保留（此前误判停发，实为 tushare 单次 300 行上限截断，
  回放 _hsgt_series 已改分片拉取）；
· 起因：P2 两年回放 1107 样本最高 79.2，≥90 零命中（详见
  nine_dim_backtest.py / 门户 P2 回放区块）。

V3 结构修订（2026-09-27，P2 V2-final 1565 样本逐维诊断驱动；
组满分调整：质量15/盈利4/环境7/板块10/机构15/情绪8/动量4/
估值10/技术5=78，总分仍为 100×已得分/可判权重的比例制）：
· 移除 f1「北向连3日净买」——2024-08 陆股通净买停发后 tushare
  moneyflow_hsgt.north_money 语义已变成交额（恒正），live/回放同
  判恒 True 失真送分 5 分；
· 移除 v3「两市额≥万亿」（99.6% True 纯送分）、e1「EPS上调≥1次」
  （2.5% True 全员拿不到的最大稀释源）、f3「明弱实强」（4.3% True）、
  t2「量≥2倍」（80% True 与粗筛共线半送分）；
· 惩罚收紧：q_p 质量恶化>20%→>35%（51% 样本误扣）、s_p 行业
  RPS<80→<50（86% 样本误扣）；
· structural-void（sv）豁免：数据体系性缺失项（q4 现金流字段、
  s2 政策/情绪组 news 覆盖前、i2/i_p 历史大宗、f2 回放融资明细）
  判 None 时从 cov 分母同步剔除——「体系没这个维度」不是「样本
  覆盖不足」，与「None 不计分不扣分」铁律同源；个股级缺失（如
  EV/EBITDA 缺）仍压 cov。

判定语义（与 chanlun_composite「未覆盖记 None 绝不计 False」铁律一致）：
· 任一评分项不可判（无权限/无数据/近似源缺失）→ 该项 None：不计分
  不扣分，从不计分权重中剔除（cov=可判权重占比）；
· 总分 = 100 × 已得分 / 可判权重（cov≥MIN_COV 才出完整评分，否则标
  「未完整评分」不参与 ≥90 入池）。

与原规格的口径差异（页面口径说明同步标注）：
· 机构调研 research 接口无权限 → 机构行为重分配：龙虎榜10+大宗5=15；
· 情绪因子降级「新闻热度代理」：news.db 新闻量周环比 + JEV 多空占比
  + research_report 覆盖篇数（百度指数/股吧/雪球无标准 API）；
· VIX 无 A 股真源（premarket 模拟源不进分）→ 「科创50 20日已实现
  波动率的 250 日分位 ≤30%」近似，高波动档以分位 >80 近似 VIX>25；
· Q业绩超预期概率 → forecast 业绩预告「预增/略增」近似，接口不可用
  则该项 None；EPS 上调/上修依赖 report_rc，不可用则 None；
· EV/EBITDA → 总市值/EBITDA 近似（净债务简化），EBITDA 缺失回退
  「PE≤行业中位」并标注；
· 毛利率/EV 分位的行业分组用 stock_basic.industry（申万分类探权限，
  DENY 回退），行业分位在候选池内计算（有偏近似，已标注）；
· 大宗折价率=(收盘-成交价)/收盘 取正值：最深折价≤5% +5、最深>8% 扣4；
· 不复权数据 → 入池后窗口内跨除权（pre_close 缺口>0.5%）标 adj
  剔出战绩（与 chanlun_scan 同口径）。

影子纪律：只写门户数据文件，不接推送；n<10 不构成结论。
"""

import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "data", "ashare")
DATA = os.path.join(HERE, "data")
SNAP = os.path.join(DATA, "nine_dim_scores.json")
HIST = os.path.join(DATA, "nine_dim_history.jsonl")
FINA_CACHE = os.path.join(DATA, "nine_dim_fina_cache.json")
IDX_CACHE = os.path.join(DATA, "nine_dim_idx_cache.json")
BASIC_CACHE = os.path.join(DATA, "nine_dim_basic_cache.json")
EVAL_CACHE = os.path.join(DATA, "nine_dim_pool_eval_cache.json")
RC_CACHE = os.path.join(DATA, "nine_dim_rc_cache.json")
NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")
KG_FEAT = os.path.join(HERE, "data", "features", "kg_features.parquet")

BUY_TH = 90  # （退役 2026-10-02）V3 复评两仪 2 年仅 1 票，保留仅作参照展示
ALERT_TH = 75  # 拥挤警报线（K32：分数与前向收益单调负相关，反用为回避警报）
MIN_COV = 0.75  # 可判权重占比低于此 → 未完整评分，不入池
CAND_CAP = 400  # 候选池上限（RPS50 降序截断）
R5, R20 = 5, 20  # 前向收益窗口（交易日）
FINA_TTL_DAYS = 5  # 财报缓存 5 日内更新（规格：财报发布后5日内）
INDEX_CODE = "000688.SH"  # 科创50

try:
    import tdx_read as tdxr  # TDX 数据仓本地读取层（data/tdx/）
except Exception:
    tdxr = None
TDX_LOCAL = True  # live 本地仓优先；置 False 强制全走 tushare API（排查用）


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _td8(v):
    return str(v)[:10].replace("-", "")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _ts(api, **params):
    import rps_resonance as rps

    return rps._ts(api, **params)


def _atomic_append(path, rows):
    """jsonl 追加：先读旧再整体原子重写（进程内已去重）。"""
    old = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = [json.loads(l) for l in f if l.strip()]
    old.extend(rows)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old))
    os.replace(tmp, path)


# ── ⓪ 运行级历史账本（2026-10-26 OS-FIX-NINEDIM，JEV/九维审计缺口）──────
# data/nine_dim_history.jsonl 是 **append-only 混合账本**（两种行，按 schema 区分）：
#   · 运行级行（本段新增）：{"schema":"nine_dim_history/v1","generated_at",
#     "trade_date","n_alert","n_cand","n_full","bands":{band:count},
#     "history":[{code,score,band}...最多20]} —— 每轮成功算完后由 run_scan
#     出口 append_history() 追加一行；给复盘/审计回答"当天 336 候选里 alert 几个、
#     都是谁、各 band 多少"（此前 nine_dim_scores.json 只保留最新一份，无法回溯）。
#   · legacy/警报池行：{"code","dt","score","band","pool_type":"alert",...}
#     —— 既有 run_scan 入池段（_atomic_append）写入，k33/日报/门户按
#     pool_type=="alert" 读取；本段不改其语义。
# 读取纪律：运行级用 load_history()；警报池族按 pool_type 过滤（均已兼容未知行，
# update_perf 已显式跳过运行级行）。写失败只告警、不阻断主计算。
HIST_SCHEMA = "nine_dim_history/v1"
HIST_TOP_N = 20


def build_history_record(snap, rows):
    """由九维快照 + 评分行构造一行运行级历史记录（纯函数，便于单测）。"""
    bands = {}
    for r in rows or ():
        b = (r or {}).get("band") or "na"
        bands[b] = bands.get(b, 0) + 1
    history = [
        {"code": r.get("code"), "score": r.get("score"), "band": r.get("band")}
        for r in list(rows or ())[:HIST_TOP_N]
    ]
    return {
        "schema": HIST_SCHEMA,
        "generated_at": snap.get("generated_at"),
        "trade_date": snap.get("trade_date"),
        "n_alert": snap.get("n_alert"),
        "n_cand": snap.get("n_cand"),
        "n_full": snap.get("n_full"),
        "bands": bands,
        "history": history,
    }


def _history_records(path=None):
    """流式读历史账本：返回 (v1 运行级记录列表, 坏行数, 非 v1 行数)。"""
    path = path or HIST
    out, bad, other = [], 0, 0
    if not os.path.exists(path):
        return out, bad, other
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out, bad + 1, other
    with fh:
        for line in fh:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                bad += 1
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            if rec.get("schema") == HIST_SCHEMA:
                out.append(rec)
            else:
                other += 1
    return out, bad, other


def append_history(record, path=None):
    """追加一行运行级历史（O_APPEND 单行写 + fsync）；同 generated_at 幂等。

    返回 True=已追加 / False=幂等跳过或写失败（失败只告警，绝不阻断主计算）。
    """
    path = path or HIST
    try:
        gen = (record or {}).get("generated_at")
        if not gen:
            print("9DIM history WARN 缺 generated_at，拒绝追加", file=sys.stderr)
            return False
        records, _bad, _other = _history_records(path)
        if any(r.get("generated_at") == gen for r in records):
            print("9DIM history skip: 同 generated_at 已存在（%s）" % gen)
            return False
        payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "nine_dim.py", exc
            )
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)  # 单行写完即落盘；崩溃最多留一行坏行（读取跳过）
        finally:
            os.close(fd)
        return True
    except Exception as e:  # noqa: BLE001
        print("9DIM history WARN 追加失败 %r（不阻断主计算）" % e, file=sys.stderr)
        return False


def load_history(limit=None, path=None):
    """只读加载运行级历史账本（仅 nine_dim_history/v1 行）。

    · 返回 list[dict]（文件顺序；legacy 警报池行不属于本账本，跳过且不计坏行）；
    · 坏行（JSON 解析失败/非对象行）跳过并在 stderr 汇总计数；
    · limit=N（>0）只返回最近 N 条（复盘常用）；None=全部；0=空列表。
    """
    records, bad, _other = _history_records(path)
    if bad:
        sys.stderr.write("9DIM history load: 坏行=%d（已跳过）\n" % bad)
    if limit is None:
        return records
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return records
    if n <= 0:
        return []
    return records[-n:]


def record_run_history(snap, rows, path=None):
    """run_scan 成功出口：构造并追加一行运行级历史；任何异常只告警。

    返回 True=已追加 / False=幂等跳过或失败。抽成函数的目的是可单测
    （不必跑完整 run_scan 的行情/API 链路）。
    """
    try:
        return append_history(build_history_record(snap, rows), path=path)
    except Exception as e:  # noqa: BLE001
        print(
            "9DIM history WARN %r（历史账本未追加，不影响主计算结果）" % e,
            file=sys.stderr,
        )
        return False


# ── ① 本地底座：全市场行情 + RPS + 粗筛 ────────────────────
def _load_market():
    """{code: df[tail 260 行]} + 最新交易日（候选池与本地因子共用）。"""
    frames, latest = {}, ""
    for f in os.listdir(LOCAL):
        if not f.endswith(".parquet") or f.startswith("_"):
            continue
        try:
            df = pd.read_parquet(os.path.join(LOCAL, f))
            if len(df) < 30:
                continue
            df = (
                df.drop_duplicates("trade_date", keep="last")
                .sort_values("trade_date")
                .tail(260)
                .reset_index(drop=True)
            )
            code = f[:-8].replace("_", ".")
            # 读侧 schema 防御（2026-11-08）：QPZM 维护脚本补入的退市股
            # parquet 仅 6 列（trade_date/ohlc/pre_close），缺 amount/volume/
            # pct_chg——下游 5 处引用点会 KeyError。退市股无成交，补 0 语义
            # 诚实：额=0 → 粗筛"额>2亿"自然出局，不进候选池。
            for _c in ("amount", "volume", "pct_chg"):
                if _c not in df.columns:
                    df[_c] = 0.0
            frames[code] = df
            latest = max(latest, _td8(df["trade_date"].iloc[-1]))
        except Exception:
            continue
    return frames, latest


def _basic():
    """stock_basic {code: [name, industry]}，缓存 1 天。

    本地中台读层优先（ts_stock_basic > stock_basic，免 API 配额），
    读层无数据时回源 tushare 远程，远程失败回落旧缓存。"""
    if (
        os.path.exists(BASIC_CACHE)
        and time.time() - os.path.getmtime(BASIC_CACHE) < 86400
    ):
        return json.load(open(BASIC_CACHE, encoding="utf-8"))
    m = {}
    try:
        import datamart

        df = datamart.read("stock_basic")
        if df is not None and not df.empty:
            if "list_status" in df.columns:
                df = df[df["list_status"].astype(str) == "L"]
            m = {
                r["ts_code"]: [r["name"], r.get("industry") or ""]
                for r in df.to_dict("records")
                if r.get("ts_code") and r.get("name")
            }
    except Exception:
        m = {}
    if not m:
        try:
            rows, fields = _ts(
                "stock_basic", list_status="L", fields="ts_code,name,industry"
            )
            i, n, g = (
                fields.index("ts_code"),
                fields.index("name"),
                fields.index("industry"),
            )
            m = {r[i]: [r[n], r[g] or ""] for r in rows}
        except Exception:
            if os.path.exists(BASIC_CACHE):
                return json.load(open(BASIC_CACHE, encoding="utf-8"))
    if m:
        _write_json(BASIC_CACHE, m)
    return m


def _boll(close, n=20, k=2):
    if len(close) < n:
        return None, None
    ma = close[-n:].mean()
    sd = close[-n:].std(ddof=0)
    return ma + k * sd, ma  # 上轨, 中轨


def _macd(close, fast=12, slow=26, sig=9):
    """(dif, dea, 金叉距今 bar 数或 None, dif>0 持续天数)。"""
    if len(close) < slow + sig:
        return None, None, None, None
    c = pd.Series(close)
    dif = c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=sig, adjust=False).mean()
    diff = (dif - dea).to_numpy()
    cross, above = None, 0
    for j in range(len(diff) - 1, 0, -1):
        if diff[j] > 0 and diff[j - 1] <= 0:
            cross = len(diff) - 1 - j
            break
    for j in range(len(diff) - 1, -1, -1):
        if diff[j] > 0:
            above += 1
        else:
            break
    return float(dif.iloc[-1]), float(dea.iloc[-1]), cross, above


def _band(score):
    """K32 V3 复评后的分段语义：高分=拥挤警报（回避位），非买入信号。

    依据（51 锚点/5479 样本/thinned 4202，r20 单调负相关）：
    lt65 -9.2% → 65-75 -14.9% → 75-85 -27.9% → 85+ -71.5%。
    """
    if score is None:
        return "na"
    if score >= 85:
        return "alert2"  # 强警报（85+ 两仪 4 样本全亏，左尾 p5 -85.9%）
    if score >= 75:
        return "alert"  # 警报（75+ thinned 胜率 9%）
    if score >= 60:
        return "mid"  # 中段（无信号；低波期 60-65 有 beta 倾向非 alpha）
    return "low"


def coarse_filter(frames, basic, rps50):
    """零 API 粗筛：RPS50>80 + 成交额>2亿 + 量能/布林动能 + 剔ST。
    返回 (候选 code 列表(RPS50 降序), 漏斗 dict)。"""
    funnel = {"全市场": len(frames)}
    cand = []
    for code, df in frames.items():
        if len(df) < 130:
            continue
        # 仅使用当前活跃 stock_basic 中可解析的股票。历史行情文件可能
        # 保留已退市代码；主数据缺失时不能把它误当成当前候选。
        if code not in basic:
            continue
        nm = (basic.get(code) or ["", ""])[0]
        if "ST" in nm or "退" in nm:
            continue
        if rps50.get(code) is None or rps50[code] <= 80:
            continue
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        amt = df["amount"].to_numpy(dtype=float)
        if amt[-1] * 1000 < 2e8:  # tushare amount 单位千元
            continue
        up, _mid = _boll(close)
        vma = vol[-120:].mean()
        if not (vol[-1] >= 1.5 * vma or (up and close[-1] >= up)):
            continue
        cand.append(code)
    funnel["RPS50>80+额>2亿+动能"] = len(cand)
    if len(cand) > CAND_CAP:
        cand = cand[:CAND_CAP]
    funnel["候选池(≤%d)" % CAND_CAP] = len(cand)
    return cand, funnel


# ── ② 市场环境 + 动态调权 ──────────────────────────────────
def _idx_bars():
    """科创50 日线 300 根收盘（缓存当日有效）。"""
    if os.path.exists(IDX_CACHE):
        try:
            c = json.load(open(IDX_CACHE, encoding="utf-8"))
            if c.get("trade_date", "") >= (datetime.now() - timedelta(days=4)).strftime(
                "%Y%m%d"
            ):
                return c
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "nine_dim.py", exc
            )
    rows, fields = _ts("index_daily", ts_code=INDEX_CODE)
    i = {f: k for k, f in enumerate(fields)}
    bars = sorted(
        ((_td8(r[i["trade_date"]]), float(r[i["close"]])) for r in rows),
        key=lambda x: x[0],
    )
    c = {
        "trade_date": bars[-1][0] if bars else "",
        "closes": [b[1] for b in bars][-300:],
    }
    _write_json(IDX_CACHE, c)
    return c


def market_env(amount_sum_yuan, closes=None, as_of_month=None):
    """全局环境：科创50 MA20/月涨幅/波动分位 + 两市成交额 + 财报期。
    closes=科创50收盘序列（缺省读 _idx_bars 当日缓存）；as_of_month
    供历史回放指定锚点月份（财报期档判定），缺省取当前月。"""
    out = {"idx": INDEX_CODE, "as_of": _now_iso()}
    try:
        if closes is None:
            closes = np.array(_idx_bars()["closes"], dtype=float)
        closes = np.asarray(closes, dtype=float)
        out["idx_above_ma20"] = bool(
            len(closes) >= 20 and closes[-1] > closes[-20:].mean()
        )
        out["idx_month_chg"] = (
            round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) > 21 else None
        )
        ret = np.diff(closes) / closes[:-1]
        win = pd.Series(ret).rolling(20).std().dropna().to_numpy()
        out["vol_pct"] = (
            round(float((win < win[-1]).mean() * 100), 1) if len(win) >= 60 else None
        )
    except Exception as ex:
        out["idx_err"] = str(ex)[:80]
        out["idx_above_ma20"] = out["idx_month_chg"] = out["vol_pct"] = None
    out["amount_yi"] = round(amount_sum_yuan / 1e8)  # 亿元
    out["amount_ge_wan_yi"] = bool(amount_sum_yuan >= 1e12)
    out["report_season"] = (
        datetime.now().month if as_of_month is None else as_of_month
    ) in (1, 2, 3, 4, 7, 8)
    out["idx_month_chg"] = (
        None if out.get("idx_month_chg") is None else float(out["idx_month_chg"])
    )
    out["vol_pct"] = None if out.get("vol_pct") is None else float(out["vol_pct"])
    out["idx_above_ma20"] = (
        None if out.get("idx_above_ma20") is None else bool(out["idx_above_ma20"])
    )
    out["bull"] = bool((out.get("idx_month_chg") or 0) > 10)
    out["high_vol"] = out.get("vol_pct") is not None and out["vol_pct"] > 80
    return out


def dynamic_weight(env):
    """三档调权（规格，可叠加）：牛市→板块+3情绪+2；高波动→大盘-5
    质量+5动量-4；财报期→盈利+3技术-3。"""
    d = {}
    if env.get("bull"):
        d["sector"], d["sentiment"] = 3, 2
    if env.get("high_vol"):
        d["env"] = d.get("env", 0) - 5
        d["quality"] = d.get("quality", 0) + 5
        d["momentum"] = d.get("momentum", 0) - 4
    if env.get("report_season"):
        d["eps"] = d.get("eps", 0) + 3
        d["tech"] = d.get("tech", 0) - 3
    return d


# ── ②b 知识图谱特征注入（kg_features.parquet + Neo4j 事件统计）──
# 全防御：图谱缺失/Neo4j 宕机 → 返回 {}，不影响主流程。
def _kg_enrich(codes, evt_days=30):
    """{code: {deg_product, peer_*, person_shared, …, evt_bull, evt_bear}}。"""
    out = {c: {} for c in codes}
    try:
        if os.path.exists(KG_FEAT):
            kf = pd.read_parquet(KG_FEAT)
            kf = kf[kf["ts_code"].isin(out)]
            for _, r in kf.iterrows():
                out[r["ts_code"]] = {
                    "deg_product": int(r.get("deg_product") or 0),
                    "peer_industry": int(r.get("peer_industry") or 0),
                    "peer_sw_l1": int(r.get("peer_sw_l1") or 0),
                    "person_shared": int(r.get("person_shared") or 0),
                    "peer_shared_prod": int(r.get("peer_shared_prod") or 0),
                }
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim.py", exc)
    # 近 N 天 bull/bear 事件计数（Neo4j）
    try:
        from kg_client import KgClient

        since = (datetime.now() - timedelta(days=evt_days)).strftime("%Y%m%d")
        cli = KgClient()
        with cli._session() as s:
            rows = list(
                s.run(
                    "UNWIND $codes AS c "
                    "MATCH (:Security {ts_code: c})-[:INVOLVES]->(e:Event) "
                    "WHERE e.date >= $since AND e.bias IN ['bull','bear'] "
                    "RETURN c AS code, e.bias AS bias, count(*) AS n",
                    codes=list(codes),
                    since=since,
                )
            )
        cli.close()
        for r in rows:
            out.setdefault(r["code"], {})["evt_" + r["bias"]] = r["n"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim.py", exc)
    return {c: v for c, v in out.items() if v}


# ── ③ API 因子数据 ─────────────────────────────────────────
def _paged(api, **params):
    rows, off = [], 0
    while True:
        r, _f2 = _ts(api, offset=off, limit=6000, **params)
        if not r:
            break
        rows.extend(r)
        if len(r) < 6000:
            break
        off += 6000
    return rows


def _by_trade_date(api, tday, fields_needed):
    """按交易日全市场一次拉 → ({code: dict}, ok)。"""
    try:
        rows, fields = (
            _ts(api, trade_date=tday)
            if api != "daily_basic"
            else (_paged(api, trade_date=tday), [])
        )
        if api == "daily_basic":  # _paged 不回 fields
            _r, fields = _ts(api, trade_date=tday, offset=0, limit=1)
        i = {f: k for k, f in enumerate(fields)}
        out = {}
        for r in rows:
            out[r[i["ts_code"]]] = {f: r[i[f]] for f in fields_needed if f in i}
        return out, True
    except Exception:
        return {}, False


def _tdx_day(tbl, tday, fields, codes=None):
    """TDX 本地仓单日读数 → {code: {field: val}}；未同步/异常 → None。

    fresh() 门闸：表 done 且覆盖到 tday 才读，否则 None 由调用方回退
    tushare API。仅 live 使用——回放侧有自己的 as-of 通道，不得经此
    读数（避免未来函数）。
    """
    if not (TDX_LOCAL and tdxr is not None and tdxr.fresh(tbl, tday)):
        return None
    try:
        return tdxr.day_map(tbl, tday, fields, codes=codes)
    except Exception:
        return None


def _nan2none(d):
    """本地 parquet 读出的 NaN 统一洗成 None（与 API JSON 口径对齐）。"""
    return {
        c: {
            k: (v if (v is not None and v == v) else None)
            for k, v in (dv or {}).items()
        }
        for c, dv in d.items()
    }


def _daily_basic(tday):
    """仅消费带原始 Tushare 来源回执的本地 TTM 日指标。

    旧 TDX pe 是动态口径，绝不映射成 pe_ttm。Tushare circ_mv 与本
    策略 float_mv 均为流通市值（万元）；读取全列以保留新分片字段。
    原全市场 API 回退保留，仅补本地缺码/空值，不覆盖合格本地值。
    """
    local = {}
    if TDX_LOCAL and tdxr is not None and tdxr.fresh("ts_daily_basic", tday):
        try:
            df = tdxr.read_day("ts_daily_basic", tday)
            required = {
                "ts_code",
                "trade_date",
                "pe_ttm",
                "total_mv",
                "circ_mv",
                "source_provider",
                "actual_received_at",
                "source_raw_sha256",
            }
            if required.issubset(df.columns):
                received = pd.to_datetime(
                    df["actual_received_at"], utc=True, errors="coerce"
                )
                eligible = (
                    (df["trade_date"].astype(str).map(_td8) == tday)
                    & (df["source_provider"] == "tushare.daily_basic")
                    & received.notna()
                    & (received <= pd.Timestamp.now(tz="UTC"))
                    & df["source_raw_sha256"]
                    .fillna("")
                    .astype(str)
                    .str.fullmatch(r"[0-9a-fA-F]{64}")
                )
                rows = df.loc[eligible].copy()
                rows["_received"] = received.loc[eligible]
                rows = rows.sort_values("_received").drop_duplicates(
                    "ts_code", keep="last"
                )
                if not rows.empty:
                    local = _nan2none(
                        {
                            r["ts_code"]: {
                                "pe_ttm": r["pe_ttm"],
                                "total_mv": r["total_mv"],
                                "float_mv": r["circ_mv"],
                            }
                            for r in rows.to_dict("records")
                        }
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "nine_dim.py", exc
            )
    rows, ok = _by_trade_date("daily_basic", tday, ["pe_ttm", "total_mv", "circ_mv"])
    if ok:
        remote = _nan2none(
            {
                c: {
                    "pe_ttm": r.get("pe_ttm"),
                    "total_mv": r.get("total_mv"),
                    "float_mv": r.get("circ_mv"),
                }
                for c, r in rows.items()
            }
        )
        for code, values in remote.items():
            dst = local.setdefault(code, {})
            for field, value in values.items():
                if dst.get(field) is None:
                    dst[field] = value
    return local, bool(local) or ok


def _block_from_snapshot(tday, source_snapshot):
    """从 as-of 逐码快照构造 block_trade，禁止公网回源。"""
    blk = {}
    target_day = _td8(tday)

    def _norm_day(value):
        return _td8(value) if value else ""

    for rec in source_snapshot.iter_records():
        params = rec.get("request_params") or {}
        exact_day = _norm_day(params.get("trade_date"))
        start_day = _norm_day(params.get("start_date"))
        end_day = _norm_day(params.get("end_date"))
        if exact_day and exact_day != target_day:
            continue
        if (
            not exact_day
            and (start_day or end_day)
            and not (
                (not start_day or start_day <= target_day)
                and (not end_day or target_day <= end_day)
            )
        ):
            continue
        if not exact_day and not start_day and not end_day:
            # 缺失回执没有任何 as-of 边界，不能证明 block_trade 覆盖了
            # 当前交易日；失败关闭，避免把未知数据报告成 block_ok=True。
            return {}, False
        if rec.get("err") or rec.get("source_code") != 0:
            return {}, False
        fields = rec.get("fields") or []
        rows = rec.get("rows") or []
        idx = {f: i for i, f in enumerate(fields)}
        if not {"ts_code", "trade_date", "price"}.issubset(idx):
            return {}, False
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) <= max(idx.values()):
                return {}, False
            if _norm_day(row[idx["trade_date"]]) != target_day:
                continue
            code = row[idx["ts_code"]]
            blk.setdefault(code, []).append(_f(row[idx["price"]]))
    return blk, True


def _fetch_flow(tday, source_snapshot=None):
    """daily_basic / moneyflow / top_list(+top_inst) / block_trade。
    TDX 本地仓优先（_tdx_day fresh 门闸），未落盘表自动回退 tushare
    API；block_trade 无本地表恒走 API，传入 as-of 快照时改走快照。
    daily_basic 仅接已核验原始 Tushare 回执的新分片：旧 TDX 'pe'=动态
    市盈率（2026-09-27 单日窗口 parity=逐日等于 tushare pe，≠pe_ttm
    18.7846 vs 18.989），本策略估值口径是 pe_ttm，错映射会造成
    live/回放口径漂移，不得将旧 pe 代替 pe_ttm；无合格分片回退 API。
    moneyflow/top_list/top_inst 历史分区真实（同日单日窗口 parity
    moneyflow 14/14、top_list ratio 1.0、margin_detail 0 差异）。
    排查提醒：TDX 远端 API 忽略 trade_date/ts_code 单参过滤，只有
    start_date/end_date 生效——用 trade_date 查历史分区会恒拉到最新
    分区（2026-09-27 曾据此误判两表为快照型，单日窗口法推翻）。"""
    got = {}
    got["basic"], got["basic_ok"] = _daily_basic(tday)
    # moneyflow（大小单金额，字段名与 tushare 一致）：历史分区真实，
    # 2026-09-27 单日窗口 parity 14/14 后恢复本地优先。
    mf = _tdx_day(
        "ts_moneyflow",
        tday,
        ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"],
    )
    if mf is None:
        mf, ok = _by_trade_date(
            "moneyflow",
            tday,
            ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"],
        )
        if not mf:
            mf, ok = _by_trade_date(
                "moneyflow", tday, ["buy_lg_amount", "sell_lg_amount"]
            )
    mf = _nan2none(mf) if mf is not None else {}
    net = {}
    for c, d in mf.items():
        b = (d.get("buy_lg_amount") or 0) + (d.get("buy_elg_amount") or 0)
        s = (d.get("sell_lg_amount") or 0) + (d.get("sell_elg_amount") or 0)
        net[c] = round(b - s, 1)  # 万元
    got["main_net"], got["flow_ok"] = net, bool(mf)
    # 龙虎榜
    tl = _tdx_day("ts_top_list", tday, ["net_amount"])
    if tl is None:
        got["top"], got["top_ok"] = _by_trade_date("top_list", tday, ["net_amount"])
    else:
        got["top"], got["top_ok"] = _nan2none(tl), True
    # 机构席位：TDX 行粒度=席位（同 code 多行），须按 code 聚合
    if TDX_LOCAL and tdxr is not None and tdxr.fresh("ts_top_inst", tday):
        try:
            df = tdxr.read_day("ts_top_inst", tday, columns=["ts_code", "buy", "sell"])
            net2 = {}
            for r in df.to_dict("records"):
                c = r.get("ts_code")
                b, s = r.get("buy"), r.get("sell")
                b = b if (b is not None and b == b) else 0
                s = s if (s is not None and s == s) else 0
                net2[c] = net2.get(c, 0) + (b or 0) - (s or 0)
            got["inst_net"], got["inst_ok"] = (
                {c: round(v, 1) for c, v in net2.items()},
                True,
            )
        except Exception:
            got["inst_net"], got["inst_ok"] = None, False
    else:
        try:  # 龙虎榜机构席位净买（单位=元）
            rows, fields = _ts("top_inst", trade_date=tday)
            i = {f: k for k, f in enumerate(fields)}
            inst = {}
            for r in rows:
                c = r[i["ts_code"]]
                b = r[i["buy"]] if "buy" in i else 0
                s = r[i["sell"]] if "sell" in i else 0
                inst[c] = inst.get(c, 0) + (b or 0) - (s or 0)
            got["inst_net"], got["inst_ok"] = inst, True
        except Exception:
            got["inst_net"], got["inst_ok"] = None, False
    if source_snapshot is not None:
        got["block"], got["block_ok"] = _block_from_snapshot(tday, source_snapshot)
    else:
        try:  # 大宗成交价列表（折价率用）
            rows, fields = _ts("block_trade", trade_date=tday)
            i = {f: k for k, f in enumerate(fields)}
            blk = {}
            for r in rows:
                blk.setdefault(r[i["ts_code"]], []).append(_f(r[i["price"]]))
            got["block"], got["block_ok"] = blk, True
        except Exception:
            got["block"], got["block_ok"] = {}, False
    return got


def _fetch_margin(tday, prev_tday):
    """融资余额：个股 margin_detail 两日对比；失败回退全局 margin。
    TDX 本地仓优先（两日都覆盖才走本地，缺任一日回退 tushare API）。"""
    out = {"detail": {}, "global_growth": None, "mode": None}
    if prev_tday:
        cur = _tdx_day("ts_margin_detail", tday, ["rzye"])
        prev = _tdx_day("ts_margin_detail", prev_tday, ["rzye"])
        if cur and prev:  # 任一日为空 → 回退 API 取全
            det = {}
            for src, m in (("cur", cur), ("prev", prev)):
                for c, d in m.items():
                    v = _f((d or {}).get("rzye"))
                    v = v if (v is not None and v == v) else 0
                    det.setdefault(c, {})[src] = v
            if det:
                out["detail"], out["mode"] = det, "detail"
                return out
    if prev_tday:
        try:
            for td, key in ((tday, "cur"), (prev_tday, "prev")):
                rows, fields = _ts("margin_detail", trade_date=td)
                i = {f: k for k, f in enumerate(fields)}
                for r in rows:
                    c = r[i["ts_code"]]
                    out["detail"].setdefault(c, {})[key] = _f(r[i["rzye"]]) or 0
            if out["detail"]:
                out["mode"] = "detail"
                return out
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "nine_dim.py", exc
            )
    try:  # 全局回退：rzye 近 2 周增幅
        st = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
        rows, fields = _ts("margin", start_date=st, end_date="20991231")
        i = {f: k for k, f in enumerate(fields)}
        tot, cnt = {}, {}
        for r in rows:
            d = r[i["trade_date"]]
            tot[d] = tot.get(d, 0) + (_f(r[i["rzye"]]) or 0)
            cnt[d] = cnt.get(d, 0) + 1
        days = sorted(d for d in tot if cnt[d] >= 2)
        if len(days) >= 2:
            out["global_growth"] = round((tot[days[-1]] / tot[days[0]] - 1) * 100, 2)
            out["mode"] = "global"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim.py", exc)
    return out


def _fetch_hsgt():
    """北向近 3 日净买全为正（全球级）→ True/False；异常 None。"""
    try:
        st = (datetime.now() - timedelta(days=12)).strftime("%Y%m%d")
        rows, fields = _ts("moneyflow_hsgt", start_date=st, end_date="20991231")
        i = {f: k for k, f in enumerate(fields)}
        recs = sorted(
            (r[i["trade_date"]], _f(r[i["north_money"]]))
            for r in rows
            if _f(r[i["north_money"]]) is not None
        )
        if len(recs) < 3:
            return None
        return all(v > 0 for _d, v in recs[-3:])
    except Exception:
        return None


def _fina(codes):
    """fina_indicator 季更缓存（5 日 TTL）→ {code: {cur, prev}}。
    不接 TDX 本地仓：ts_fina_indicator 缺 or_yoy/netprofit_yoy/
    ocf_to_or/ebitda 四字段，本地读会压质量组 cov，维持 tushare API。"""
    cache = {}
    if os.path.exists(FINA_CACHE):
        try:
            cache = json.load(open(FINA_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    need = [
        c
        for c in codes
        if not cache.get(c)
        or (
            datetime.now()
            - datetime.strptime(
                cache[c].get("fetched_at", "2000-01-01"), "%Y-%m-%d %H:%M:%S"
            )
        ).days
        >= FINA_TTL_DAYS
    ]
    flds = (
        "ts_code,ann_date,end_date,or_yoy,netprofit_yoy,"
        "grossprofit_margin,roe,ocf_to_or,ebitda,debt_to_assets"
    )
    for k, c in enumerate(need):
        rec = {"fetched_at": _now_iso()}
        try:
            rows, fields = _ts("fina_indicator", ts_code=c, limit=2, fields=flds)
            i = {f: j for j, f in enumerate(fields)}
            per = {}
            for r in sorted(
                rows,
                key=lambda r: _td8(r[i["ann_date"]]) if r[i["ann_date"]] else "",
                reverse=True,
            ):
                per.setdefault(_td8(r[i["end_date"]]), r)  # 同期取最新公告
            eds = sorted(per, reverse=True)
            if eds:
                rec["cur"] = {f: per[eds[0]][i[f]] for f in fields if f != "ts_code"}
            if len(eds) > 1:
                rec["prev"] = {f: per[eds[1]][i[f]] for f in fields if f != "ts_code"}
        except Exception:
            rec["err"] = 1
        cache[c] = rec
        time.sleep(0.12)
        if (k + 1) % 50 == 0:
            _write_json(FINA_CACHE, cache)  # 中途落盘防中断
    _write_json(FINA_CACHE, cache)
    return cache


def _rc_stats(rows, fields):
    """report_rc 明细 → {n_rep, up, down, up_q4}
    （n_rep=券商覆盖篇数去重(日期+机构)；up=同机构同季度 EPS 上调次数；
    down=下调次数；up_q4=年度(Q4)上修次数）。rows 须已按时间窗过滤。"""
    i = {f: k for k, f in enumerate(fields)}
    cov, events = set(), []
    for r in rows:
        d, org = r[i["report_date"]], r[i["org_name"]] or ""
        q = str(r[i["quarter"]] or "")
        eps = _f(r[i["eps"]])
        cov.add((d, org))
        if d and q and eps is not None:
            events.append((d, org, q, eps))
    rec = {"n_rep": len(cov) if rows else 0, "up": 0, "down": 0, "up_q4": 0}
    seq = {}
    for d, org, q, eps in events:
        seq.setdefault((org, q), []).append((d, eps))
    for (org, q), lst in seq.items():
        lst.sort()
        last = None
        for d, eps in lst:
            if last is not None and eps != last:
                if eps > last:
                    rec["up"] += 1
                    if q.endswith("Q4"):
                        rec["up_q4"] += 1
                else:
                    rec["down"] += 1
            last = eps
    return rec


def _report_rc(codes):
    """report_rc 近 30 日盈利预测明细 → {code: {n_rep, up, down, up_q4}}。
    全市场皆空→None 项。
    不接 TDX stock_report：东财研报 ≠ tushare report_rc（盈利预测明细），
    up/down 口径漂移会改 _j_ep 扣分判定，保持 API 原口径。"""
    out = {}
    st = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    ed = datetime.now().strftime("%Y%m%d")
    for c in codes:
        rec = {"n_rep": None, "up": None, "down": None, "up_q4": None}
        try:
            rows, fields = _ts(
                "report_rc", ts_code=c, start_date=st, end_date=ed, limit=300
            )
            rec.update(_rc_stats(rows, fields))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "nine_dim.py", exc
            )
        out[c] = rec
        time.sleep(0.1)
    return out


def _fc_pick(rows, fields):
    """业绩预告明细 → 最近一条 True(预增/略增/扭亏)/False(预减/略减/
    首亏)/None（无预告）。按 announce_date 降序取首个可判项。"""
    i = {f: k for k, f in enumerate(fields)}
    dk = next((k for k in ("announce_date", "ann_date") if k in i), None)
    if dk:
        pts = sorted(((str(r[i[dk]] or ""), r) for r in rows), reverse=True)
    else:
        pts = [("", r) for r in rows]  # 无日期字段保持 API 原序
    for _d, r in pts:
        t = str(r[i["type"]] or "")
        if "预增" in t or "略增" in t or "扭亏" in t:
            return True
        if "预减" in t or "略减" in t or "首亏" in t:
            return False
    return None


def _forecast_flags(codes):
    """forecast 业绩预告近 120 日 {code: True(预增/扭亏)/False/None}。"""
    out = {}
    st = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
    ed = datetime.now().strftime("%Y%m%d")
    for c in codes:
        try:
            rows, fields = _ts(
                "forecast", ts_code=c, start_date=st, end_date=ed, limit=5
            )
            out[c] = _fc_pick(rows, fields)
        except Exception:
            out[c] = None
        time.sleep(0.1)
    return out


def _news_stats(codes, basic):
    """news.db 近 14 日个股新闻：周环比 + JEV 多空占比。"""
    stats = {c: {"n7": 0, "n_prev7": 0, "bull": 0, "bear": 0, "n": 0} for c in codes}
    nm = {c: (basic.get(c) or ["", ""])[0] for c in codes}
    nm = {c: n for c, n in nm.items() if n}
    if not os.path.exists(NEWS_DB) or not nm:
        return stats
    since = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
    cut = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        for title, j, ca in db.execute(
            "SELECT title, jev, created_at FROM news "
            "WHERE jev IS NOT NULL AND created_at>=? "
            "ORDER BY id DESC LIMIT 8000",
            (since,),
        ):
            if not title:
                continue
            for c, n in nm.items():
                if n in title:
                    s = stats.get(c)
                    if s is None:
                        continue
                    s["n"] += 1
                    if ca >= cut:
                        s["n7"] += 1
                    else:
                        s["n_prev7"] += 1
                    try:
                        b = json.loads(j).get("bias")
                    except Exception:
                        b = None
                    if b == "bull":
                        s["bull"] += 1
                    elif b == "bear":
                        s["bear"] += 1
        db.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim.py", exc)
    return stats


# ── ④ 评分引擎（声明式九维表 + 惩罚项） ────────────────────
def _j_q1(s):
    v = _f(s["fin"].get("cur", {}).get("or_yoy"))
    return None if v is None else v >= 25


def _j_q2(s):
    v = _f(s["fin"].get("cur", {}).get("grossprofit_margin"))
    th = s["ctx"].get("gm_p70", {}).get(s.get("industry") or "")
    return None if v is None or th is None else v >= th


def _j_q3(s):
    cur, prev = (
        _f(s["fin"].get("cur", {}).get("roe")),
        _f(s["fin"].get("prev", {}).get("roe")),
    )
    return None if cur is None or prev is None else cur > prev


def _j_q4(s):
    v = _f(s["fin"].get("cur", {}).get("ocf_to_or"))
    return None if v is None else v > 0


def _j_qp(s):  # 营收增速/ROE 恶化>35%（V3收紧）
    cur, prev = s["fin"].get("cur", {}), s["fin"].get("prev", {})
    for k in ("or_yoy", "roe"):
        a, b = _f(cur.get(k)), _f(prev.get(k))
        if a is None or b is None or b <= 0:
            continue
        if (b - a) / b > 0.35:
            return True
    return False


def _j_e2(s):  # 超预期→业绩预告预增近似
    return s.get("fc_flag")


def _j_ep(s):  # 盈利预测下调扣2
    rc = s.get("rc") or {}
    return None if rc.get("down") is None else rc["down"] >= 1


def _j_v1(s):
    return s["env"].get("idx_above_ma20")


def _j_v2(s):  # VIX 降级：波动分位≤30
    v = s["env"].get("vol_pct")
    return None if v is None else v <= 30


def _j_s1(s):
    top = s["ctx"].get("ind_top3")
    return None if top is None else s.get("industry") in top


def _j_s2(s):
    return s.get("policy_hit")


def _j_sp(s):  # 行业RPS<50 扣3（V3收紧）
    v = s["ctx"].get("ind_rps", {}).get(s.get("industry") or "")
    return None if v is None else v < 50


def _j_i1(s):  # 机构净买≥5000万（10分）
    # top_inst/top_list 金额单位=元（2026-09-27 验证：机构专用 buy
    # 2167万元=2.16e7 元），5000万 = 5e7 元；旧阈值 5000 实际只挡
    # 0.5 万元，判定形同虚设（pre-existing，2026-09-27 修复）。
    v = s.get("inst_net")
    if v is None:
        v = s.get("top_net")  # 回退：龙虎榜净买额代理
    return None if v is None else v >= 5e7


def _j_i2(s):  # 大宗最深折价≤5%
    return s.get("block_disc_lo")


def _j_ip(s):  # 大宗折价>8% 扣4
    return s.get("block_disc_hi")


def _j_m1(s):  # 新闻量周环比≥100%
    n, p = s["news"].get("n7", 0), s["news"].get("n_prev7", 0)
    return None if (n == 0 and p == 0) else (n >= 2 * p and n >= 2)


def _j_m2(s):  # JEV 利多占比≥60%
    n = s["news"].get("n", 0)
    return None if n == 0 else s["news"]["bull"] * 100.0 / n >= 60


def _j_m3(s):  # 券商覆盖≥3篇（report_rc）
    v = (s.get("rc") or {}).get("n_rep")
    return None if v is None else v >= 3


def _j_mp(s):  # JEV 负面>30% 扣3
    n = s["news"].get("n", 0)
    return None if n == 0 else s["news"]["bear"] * 100.0 / n > 30


def _j_f2(s):  # 融资余额周增≥5%（个股）
    d = s.get("margin_detail") or {}
    if d.get("cur") and d.get("prev") and d["prev"] > 0:
        return (d["cur"] / d["prev"] - 1) * 100 >= 5
    return None  # 个股缺 → 不用全局替代给分


def _j_fp(s):  # 主力净流出>5%流通市值
    mn, fv = s.get("main_net"), s.get("float_mv")
    return None if mn is None or not fv else (-mn / fv) > 0.05


def _j_g1(s):  # PEG≤1.0（净利负增长=False）
    pe, g = s.get("pe_ttm"), _f(s["fin"].get("cur", {}).get("netprofit_yoy"))
    if pe is None or g is None:
        return None
    if g <= 0:
        return False
    return pe / g <= 1.0


def _j_g2(s):  # EV/EBITDA≤行业均值（近似）
    ev = s.get("ev_ebitda")
    if ev is not None:
        th = s["ctx"].get("ev_mean", {}).get(s.get("industry") or "")
        return None if th is None else ev <= th
    pe = s.get("pe_ttm")  # 回退：PE≤行业中位
    th2 = s["ctx"].get("pe_med", {}).get(s.get("industry") or "")
    return None if pe is None or th2 is None else pe <= th2


def _j_gp(s):  # PEG>1.5 扣3
    pe, g = s.get("pe_ttm"), _f(s["fin"].get("cur", {}).get("netprofit_yoy"))
    if pe is None or g is None or g <= 0:
        return None
    return pe / g > 1.5


def _j_t1(s):
    return s.get("boll_break")


def _j_t3(s):
    return s.get("macd_gold_zero")


def _j_tp(s):  # 假突破：近3日破上轨后收中轨下
    return s.get("fake_break")


# V3：sv=True=structural-void——数据体系性缺失（历史未收集/接口停发/
# 字段不存在）判 None 时从 cov 分母同步剔除；个股级缺失不标 sv 仍压 cov。
FACTORS = [
    {
        "id": "quality",
        "name": "公司质量",
        "max": 15,
        "items": [
            {"id": "q1", "label": "营收增速≥25%", "pts": 6, "j": _j_q1},
            {"id": "q2", "label": "毛利率行业TOP30%", "pts": 4, "j": _j_q2},
            {"id": "q3", "label": "ROE环比提升", "pts": 3, "j": _j_q3},
            {"id": "q4", "label": "经营现金流为正", "pts": 2, "j": _j_q4, "sv": True},
        ],
    },
    {
        "id": "eps",
        "name": "盈利预期",
        "max": 4,
        "items": [
            {"id": "e2", "label": "业绩预告预增(超预期近似)", "pts": 4, "j": _j_e2}
        ],
    },
    {
        "id": "env",
        "name": "大盘环境",
        "max": 7,
        "items": [
            {"id": "v1", "label": "科创50站上20日线", "pts": 4, "j": _j_v1},
            {"id": "v2", "label": "波动率分位≤30%(VIX降级)", "pts": 3, "j": _j_v2},
        ],
    },
    {
        "id": "sector",
        "name": "板块协同",
        "max": 10,
        "items": [
            {"id": "s1", "label": "行业5日涨幅前3", "pts": 6, "j": _j_s1},
            {
                "id": "s2",
                "label": "政策利好(新闻关键词)",
                "pts": 4,
                "j": _j_s2,
                "sv": True,
            },
        ],
    },
    {
        "id": "inst",
        "name": "机构行为",
        "max": 15,
        "items": [
            {"id": "i1", "label": "龙虎榜机构净买≥5000万", "pts": 10, "j": _j_i1},
            {"id": "i2", "label": "大宗最深折价≤5%", "pts": 5, "j": _j_i2, "sv": True},
        ],
    },
    {
        "id": "sentiment",
        "name": "市场情绪",
        "max": 8,
        "items": [
            {
                "id": "m1",
                "label": "新闻量周环比≥100%",
                "pts": 4,
                "j": _j_m1,
                "sv": True,
            },
            {"id": "m2", "label": "JEV利多占比≥60%", "pts": 3, "j": _j_m2, "sv": True},
            {"id": "m3", "label": "券商覆盖≥3篇", "pts": 1, "j": _j_m3},
        ],
    },
    {
        "id": "momentum",
        "name": "动量资金",
        "max": 4,
        "items": [
            {"id": "f2", "label": "融资余额周增≥5%", "pts": 4, "j": _j_f2, "sv": True}
        ],
    },
    {
        "id": "val",
        "name": "估值安全",
        "max": 10,
        "items": [
            {"id": "g1", "label": "PEG≤1.0", "pts": 6, "j": _j_g1},
            {"id": "g2", "label": "EV/EBITDA≤行业均值", "pts": 4, "j": _j_g2},
        ],
    },
    {
        "id": "tech",
        "name": "技术结构",
        "max": 5,
        "items": [
            {"id": "t1", "label": "放量突破布林上轨", "pts": 4, "j": _j_t1},
            {"id": "t3", "label": "MACD零轴上金叉", "pts": 1, "j": _j_t3},
        ],
    },
]

PENALTIES = [
    {"fid": "quality", "id": "q_p", "label": "质量指标恶化>35%", "pts": 3, "j": _j_qp},
    {"fid": "sector", "id": "s_p", "label": "行业RPS<50", "pts": 3, "j": _j_sp},
    {"fid": "inst", "id": "i_p", "label": "大宗折价>8%", "pts": 4, "j": _j_ip},
    {"fid": "sentiment", "id": "m_p", "label": "JEV负面>30%", "pts": 3, "j": _j_mp},
    {
        "fid": "momentum",
        "id": "f_p",
        "label": "主力净流出>5%流通盘",
        "pts": 2,
        "j": _j_fp,
    },
    {"fid": "val", "id": "g_p", "label": "PEG>1.5", "pts": 3, "j": _j_gp},
    {"fid": "tech", "id": "t_p", "label": "假突破", "pts": 4, "j": _j_tp},
]


def score_one(s, wdelta):
    """单票评分 → {score, cov, detail}。score=100×已得分/可判权重；
    cov=可判权重/(体系权重−sv不可判权重)——sv（体系性缺失）项判 None
    时从 cov 分母同步剔除，个股级缺失仍压 cov。cov≥MIN_COV 才有效。"""
    detail, total_max, judg_max, earned = [], 0.0, 0.0, 0.0
    pens = {p["fid"]: p for p in PENALTIES}
    for fa in FACTORS:
        adj = fa["max"] + wdelta.get(fa["id"], 0)
        f_earned, f_judg, f_items, f_svvoid = 0.0, 0.0, [], 0.0
        for it in fa["items"]:
            v = it["j"](s)
            got = it["pts"] if v is True else 0.0
            if v is not None:
                f_judg += it["pts"]
            elif it.get("sv"):
                f_svvoid += it["pts"]  # 体系性缺失 → cov 分母剔除
            f_earned += got
            f_items.append(
                {
                    "id": it["id"],
                    "label": it["label"],
                    "state": (None if v is None else bool(v)),
                    "pts": it["pts"],
                    "got": got,
                }
            )
        p = pens.get(fa["id"])
        if p:
            v = p["j"](s)
            f_items.append(
                {
                    "id": p["id"],
                    "label": p["label"],
                    "state": (None if v is None else bool(v)),
                    "pts": -p["pts"],
                    "got": (-p["pts"]) if v is True else 0,
                }
            )
            if v is True:
                f_earned -= p["pts"]
        detail.append(
            {
                "id": fa["id"],
                "name": fa["name"],
                "max": adj,
                "earned": round(f_earned, 1),
                "cov": round(f_judg / adj, 2) if adj else 0,
                "items": f_items,
            }
        )
        total_max += max(adj - f_svvoid, f_judg)
        judg_max += f_judg
        earned += f_earned
    cov = judg_max / total_max if total_max else 0
    full = cov >= MIN_COV
    return {
        "score": round(100.0 * earned / judg_max, 1) if (judg_max and full) else None,
        "raw": round(earned, 1),
        "cov": round(cov, 2),
        "full": full,
        "detail": detail,
    }


# ── ⑤ 编排 ────────────────────────────────────────────────
def _build_stock(
    code,
    frames,
    basic,
    rps50,
    flow,
    marg,
    north,
    fina,
    rc,
    fc_flags,
    nstats,
    pol_titles,
    ctx,
    env,
    kg=None,
):
    df = frames[code]
    bi = basic.get(code) or ["", ""]
    cl = df["close"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    up, mid = _boll(cl)
    vma = vol[-120:].mean() if len(vol) >= 120 else None
    dif, dea, cross, above = _macd(cl)
    fin = fina.get(code, {})
    btd = flow["block"].get(code) or []
    disc_lo = disc_hi = None
    if btd and cl[-1]:
        discs = [(cl[-1] - p) / cl[-1] for p in btd if p]
        if discs:
            disc_lo = min(discs) <= 0.05
            disc_hi = max(discs) > 0.08
    db = flow["basic"].get(code) or {}
    pe, tmv, fmv = _f(db.get("pe_ttm")), _f(db.get("total_mv")), _f(db.get("float_mv"))
    ebd = _f(fin.get("cur", {}).get("ebitda"))
    ev = round(tmv * 1e4 / (ebd * 1e8), 2) if (tmv and ebd and ebd > 0) else None
    return {
        "code": code,
        "name": bi[0],
        "industry": bi[1],
        "env": env,
        "ctx": ctx,
        "fin": fin,
        "pe_ttm": pe,
        "ev_ebitda": ev,
        "float_mv": fmv,
        "main_net": flow["main_net"].get(code),
        "inst_net": flow["inst_net"].get(code) if flow["inst_ok"] else None,
        "top_net": _f((flow["top"].get(code) or {}).get("net_amount")),
        "block_disc_lo": disc_lo,
        "block_disc_hi": disc_hi,
        "margin_detail": marg["detail"].get(code),
        "boll_break": bool(up and cl[-1] >= up and vma and vol[-1] >= 1.5 * vma),
        "fake_break": bool(
            up and mid and any(cl[-j] >= up for j in (2, 3, 4)) and cl[-1] < mid
        ),
        "vol_ratio": round(float(vol[-1] / vma), 2) if vma else None,
        "macd_gold_zero": bool(cross is not None and cross <= 3 and dif and dif > 0),
        "pct_chg": float(df["pct_chg"].iloc[-1] or 0),
        "rc": rc.get(code),
        "fc_flag": fc_flags.get(code),
        "news": nstats.get(code, {}),
        "policy_hit": (
            None
            if pol_titles is None
            else any(
                (bi[0] and bi[0] in t) or (bi[1] and bi[1] in t) for t in pol_titles
            )
        ),
        "kg": (kg or {}).get(code) or None,
    }


def run_scan(dryrun=False):
    import rps_resonance as rps

    t0 = time.time()
    try:  # 数据时点提示（校对：数据停在几号 vs 期望几号；失败不阻塞）
        import datamart as _dm

        print(
            _dm.freshness_banner(
                ["daily", "moneyflow", "margin", "news", "notice", "report"]
            )
        )
    except Exception as ex:
        print("[数据时点] 探测跳过: %s" % ex)
    frames, latest = _load_market()
    if not frames or not latest:
        print("9DIM no local data")
        return
    basic = _basic()
    closes = {c: df["close"].to_numpy(dtype=float) for c, df in frames.items()}
    rdf = rps.batch_rps(closes)
    rps50 = rdf["rps_50"] if "rps_50" in rdf.columns else pd.Series(dtype=float)
    rps50 = {k: float(v) for k, v in rps50.items()}
    cand, funnel = coarse_filter(frames, basic, rps50)
    print("funnel: %s" % funnel)
    if not cand:
        print("9DIM no candidates")
        return

    # 行业聚合（全市场本地：5日涨幅/RPS） + 两市成交额
    ind_of = {c: (basic.get(c) or ["", ""])[1] for c in frames}
    buf5, bufr, amt_sum = {}, {}, 0.0
    for c, df in frames.items():
        amt_sum += float(df["amount"].iloc[-1] or 0) * 1000
        g = ind_of.get(c) or ""
        if not g or len(df) < 10:
            continue
        cl = df["close"].to_numpy(dtype=float)
        if len(cl) >= 6 and cl[-6] > 0:
            buf5.setdefault(g, []).append(cl[-1] / cl[-6] - 1)
        if rps50.get(c) is not None:
            bufr.setdefault(g, []).append(rps50[c])
    ind_ret5 = {g: float(np.median(v)) for g, v in buf5.items()}
    ind_rps50 = {g: round(float(np.median(v)), 1) for g, v in bufr.items()}
    ind_top3 = set(sorted(ind_ret5, key=lambda g: -ind_ret5[g])[:3])

    env = market_env(amt_sum)
    wdelta = dynamic_weight(env)
    print(
        "env: %s"
        % {
            k: env.get(k)
            for k in (
                "idx_above_ma20",
                "idx_month_chg",
                "vol_pct",
                "amount_yi",
                "bull",
                "high_vol",
                "report_season",
            )
        }
    )
    print("wdelta: %s" % wdelta)

    # API 数据
    prev8 = None
    try:
        rows, fields = _ts(
            "trade_cal",
            exchange="SSE",
            start_date=(
                datetime.strptime(latest, "%Y%m%d") - timedelta(days=20)
            ).strftime("%Y%m%d"),
            end_date=latest,
        )
        i_o, i_c = fields.index("is_open"), fields.index("cal_date")
        days = sorted(r[i_c] for r in rows if r[i_o] == 1)
        if len(days) >= 6:
            prev8 = days[-6]  # 5 个交易日前
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim.py", exc)
    flow = _fetch_flow(latest)
    marg = _fetch_margin(latest, prev8)
    north = _fetch_hsgt()
    fina = _fina(cand)
    # report_rc/forecast 当日缓存（慢接口，日内复跑不重拉）
    rc_cache = {}
    if os.path.exists(RC_CACHE):
        try:
            rc_cache = json.load(open(RC_CACHE, encoding="utf-8"))
        except Exception:
            rc_cache = {}
    if rc_cache.get("date") == latest and rc_cache.get("codes") == cand:
        rc, fc_flags = rc_cache["rc"], rc_cache["fc"]
    else:
        rc = _report_rc(cand)
        fc_flags = _forecast_flags(cand)
        _write_json(RC_CACHE, {"date": latest, "codes": cand, "rc": rc, "fc": fc_flags})
    nstats = _news_stats(cand, basic)
    kg = _kg_enrich(cand)  # 知识图谱特征（图特征+事件统计，缺失降级）

    # 政策利好：候选股名字/行业命中近3日政策关键词新闻
    # None=news 源不可用（诚实不可判，s2 豁免 cov）；set=已覆盖
    pol_kw = (
        "政策",
        "部委",
        "规划",
        "央行",
        "监管",
        "降准",
        "降息",
        "补贴",
        "试点",
        "条例",
        "意见",
    )
    pol_titles = set()
    try:
        since = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        db = sqlite3.connect(NEWS_DB)
        for (t,) in db.execute(
            "SELECT title FROM news WHERE created_at>=? ORDER BY id DESC LIMIT 3000",
            (since,),
        ):
            if t and any(k in t for k in pol_kw):
                pol_titles.add(t)
        db.close()
    except Exception:
        pol_titles = None

    # 行业分位上下文（候选池内近似：毛利率 p70 / EV 均值 / PE 中位）
    gm_by, ev_by, pe_by = {}, {}, {}
    for code in cand:
        g = (basic.get(code) or ["", ""])[1] or ""
        fcur = fina.get(code, {}).get("cur", {})
        gm = _f(fcur.get("grossprofit_margin"))
        if gm is not None:
            gm_by.setdefault(g, []).append(gm)
        pe = _f((flow["basic"].get(code) or {}).get("pe_ttm"))
        if pe:
            pe_by.setdefault(g, []).append(pe)
        ebd = _f(fcur.get("ebitda"))
        tmv = _f((flow["basic"].get(code) or {}).get("total_mv"))
        if ebd and ebd > 0 and tmv:
            ev_by.setdefault(g, []).append(tmv * 1e4 / (ebd * 1e8))
    ctx = {
        "gm_p70": {g: round(float(np.percentile(v, 70)), 2) for g, v in gm_by.items()},
        "ev_mean": {g: round(float(np.mean(v)), 2) for g, v in ev_by.items()},
        "pe_med": {g: round(float(np.median(v)), 2) for g, v in pe_by.items()},
        "ind_top3": sorted(ind_top3),
        "ind_rps": ind_rps50,
        "north_buy3": north,
        "margin_global_growth": marg.get("global_growth"),
    }

    # 评分
    rows_out = []
    for code in cand:
        s = _build_stock(
            code,
            frames,
            basic,
            rps50,
            flow,
            marg,
            north,
            fina,
            rc,
            fc_flags,
            nstats,
            pol_titles,
            ctx,
            env,
            kg,
        )
        res = score_one(s, wdelta)
        df = frames[code]
        rows_out.append(
            {
                "code": code,
                "name": s["name"],
                "industry": s["industry"],
                "rps50": rps50.get(code),
                "close": round(float(df["close"].to_numpy(dtype=float)[-1]), 2),
                "pct_chg": s["pct_chg"],
                "score": res["score"],
                "cov": res["cov"],
                "full": res["full"],
                "band": _band(res["score"]),
                "kg": s["kg"],
                "detail": res["detail"],
            }
        )
    rows_out.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
    n_full = sum(1 for r in rows_out if r["full"])
    n_buy = sum(1 for r in rows_out if (r["score"] or 0) >= BUY_TH)
    n_alert = sum(1 for r in rows_out if r["band"] in ("alert", "alert2"))
    # `generated_at` 必须是**带时区偏移的 ISO**：/api/alerts 的 source_health
    # （portal/api_common.py producer_time）只认 `...+08:00` 或 `...Z`，
    # 裸本地串会被判 unproven_completion，进而使 AlertWatch 拒跑。
    # 2026-10-22 之前这里用 _now_iso()（"%Y-%m-%d %H:%M:%S"，无时区）。
    # 只改这一处生成时间，不动 _now_iso 本身——它还被别处用着，
    # 全局改格式会波及其它消费方对旧格式的假设。
    snap = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trade_date": latest,
        "env": env,
        "wdelta": wdelta,
        "funnel": funnel,
        "n_cand": len(cand),
        "n_full": n_full,
        "n_buy": n_buy,
        "n_alert": n_alert,
        "alert_th": ALERT_TH,
        "buy_th": BUY_TH,
        "min_cov": MIN_COV,
        "rows": rows_out,
        "note": "影子评分不推送 · cov=可判权重占比 · 未完整评分"
        "(cov<%.2f)不出分 · 规格降级口径见工作台说明 · "
        "K33 重定位：高分=拥挤警报（回避位）非买入信号" % MIN_COV,
    }
    print(
        "scored: cand=%d full=%d alert(>=%d)=%d  %.1fs"
        % (len(cand), n_full, ALERT_TH, n_alert, time.time() - t0)
    )

    if dryrun:
        for r in rows_out[:20]:
            print(
                "  %s %-10s score=%s cov=%s ind=%s rps=%s"
                % (
                    r["code"],
                    r["name"],
                    r["score"],
                    r["cov"],
                    r["industry"],
                    r["rps50"],
                )
            )
        return snap

    _write_json(SNAP, snap)
    # OS-FIX-NINEDIM-20261026：运行级历史账本（append-only；失败只告警不阻断）。
    # 位置=「成功算出分数/警报后的出口」：dryrun 不写快照也不写历史，保持一致。
    record_run_history(snap, rows_out)
    # 入池（K33 反转语义：≥75 拥挤警报池，跟踪验证负收益即警报有效；
    # 原 ≥90 买入池退役——V3 复评两仪 2 年 1 票且 r20 -34%）
    have = set()
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    have.add((r["code"], r["dt"]))
                except Exception:
                    continue
    new = [
        {
            "code": r["code"],
            "name": r["name"],
            "industry": r["industry"],
            "dt": latest,
            "score": r["score"],
            "cov": r["cov"],
            "band": r["band"],
            "pool_type": "alert",
            "entry_px": r["close"],
            "first_seen": _now_iso(),
        }
        for r in rows_out
        if r["band"] in ("alert", "alert2")
        and r["full"]
        and (r["code"], latest) not in have
    ]
    if new:
        _atomic_append(HIST, new)
    update_perf()
    print("9DIM done: snap written, pool +%d" % len(new))
    return snap


# ── ⑥ 战绩（入池日收盘基准 r5/r20，跨除权剔除） ────────────
def _bars(code):
    p = os.path.join(LOCAL, code.replace(".", "_") + ".parquet")
    if not os.path.exists(p):
        return []
    try:
        df = pd.read_parquet(p)
        # K169 断源显式检查（对齐 chanlun_scan.issubset 强范式）：
        # trade_date/close/pre_close 任一缺列 loud WARN 返回空，
        # 不再裸吞 AttributeError 静默降级（写端=rps_resonance 固定 11 列）
        miss = {"trade_date", "close", "pre_close"} - set(df.columns)
        if miss:
            print("9DIM _bars WARN %s 源列缺失: %s" % (code, sorted(miss)))
            return []
        df = df.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
        return [
            {
                "dt": _td8(r.trade_date),
                "c": float(r.close),
                "pc": float(r.pre_close) if r.pre_close else None,
            }
            for r in df.itertuples()
        ]
    except Exception as e:
        print("9DIM _bars WARN %s 读取失败: %r" % (code, e))
        return []


def _exdiv(bars, ei, xi):
    for j in range(max(ei + 1, 1), xi + 1):
        pc, prev = bars[j].get("pc"), bars[j - 1]["c"]
        if pc and prev and abs(pc - prev) > 0.005 * prev:
            return True
    return False


def update_perf():
    """逐条 history 计算 r5/r20（成熟实现 / 未成熟盯市），聚合缓存。

    OS-FIX-NINEDIM：nine_dim_history.jsonl 现为混合 append-only 账本，运行级
    记录（schema=nine_dim_history/v1）与坏行必须跳过，否则本函数 r["code"]
    会 KeyError；文件只有运行级行时提前返回，不覆盖既有战绩缓存。
    """
    if not os.path.exists(HIST):
        print("perf: no history yet")
        return
    rows, skipped = [], 0
    with open(HIST, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                skipped += 1
                continue
            if not isinstance(r, dict):
                skipped += 1
                continue
            if r.get("schema"):  # 运行级记录（有 schema 字段）不进战绩
                skipped += 1
                continue
            if "code" not in r or "dt" not in r:
                skipped += 1  # 字段不全/未知行：当坏行跳过
                continue
            rows.append(r)
    if not rows:
        print("perf: no pool rows yet（history 跳过 %d 行运行级/坏行）" % skipped)
        return
    cache, out = {}, []
    for r in rows:
        code = r["code"]
        if code not in cache:
            cache[code] = _bars(code)
        bars = cache[code]
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        ei = idx.get(r["dt"])
        rec = dict(r)
        rec["r5"] = rec["r20"] = None
        rec["mature5"] = rec["mature20"] = False
        rec["adj"] = False
        if ei is not None:
            px = r.get("entry_px") or bars[ei]["c"]
            for w, key, mat in ((R5, "r5", "mature5"), (R20, "r20", "mature20")):
                j = ei + w
                if j < len(bars):
                    rec[key] = round((bars[j]["c"] / px - 1) * 100, 2)
                    rec[mat] = True
                    if _exdiv(bars, ei, j):
                        rec["adj"] = True
                elif len(bars):
                    rec[key] = round((bars[-1]["c"] / px - 1) * 100, 2)
        out.append(rec)

    def _stats(vals):
        if not vals:
            return None
        n = len(vals)
        return {
            "n": n,
            "win": round(sum(1 for x in vals if x > 0) * 100.0 / n, 1),
            "avg": round(sum(vals) * 100.0 / n, 2),
        }

    bands = {}
    for w, mat in (("r5", "mature5"), ("r20", "mature20")):
        clean = [r[w] for r in out if r[w] is not None and not r["adj"] and r[mat]]
        bands[w] = {
            "all": _stats(clean),
            "ge95": _stats(
                [
                    r[w]
                    for r in out
                    if r[w] is not None and not r["adj"] and r[mat] and r["score"] >= 95
                ]
            ),
        }
    ev = {
        "generated_at": _now_iso(),
        "n_pool": len(out),
        "r5": bands["r5"],
        "r20": bands["r20"],
        "note": "入池日收盘价基准 · 交易日窗口 · 跨除权(adj)>0.5%剔除 · "
        "未成熟窗口取盯市不入统计 · n<10 不构成结论 · "
        "影子观察不推送",
    }
    _write_json(EVAL_CACHE, ev)
    print(
        "perf: pool=%d r5=%s r20=%s"
        % (len(out), bands["r5"]["all"], bands["r20"]["all"])
    )
    return ev


def main():
    if "--perf" in sys.argv:
        update_perf()
    else:
        run_scan(dryrun="--dryrun" in sys.argv)


if __name__ == "__main__":
    main()
