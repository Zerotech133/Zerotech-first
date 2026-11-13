# -*- coding: utf-8 -*-
"""模拟盘（paper trading）v0.1 —— 策略调校与训练数据积累。

用户规则（2026-11-13 老田拍板，原文转译）：
  · 单策略上限 1000 万（模拟资金）；
  · 每只首仓 ≤5%；盘中出现加仓点且在买点上 → 可加仓，单票封顶 30%；
  · 不达预期直接卖掉（v0.1 口径：持有次日收盘浮亏即卖，参数可调）；
  · 每日收盘后筛「不在趋势上、次日可能出问题」的票，结合次日盘前
    数据卖出空仓；
  · 产出：数据（trades/equity 流水）+ 曲线图 + 未来训练模型的数据集。

三个产出物：
  data/paper/account.json   账户状态（现金/持仓/挂单）——原子写
  data/paper/trades.jsonl   交易流水（每笔带信号快照=训练样本；平仓回填收益）
  data/paper/equity.jsonl   每日净值（收盘后记一条）

审计（JUDGMENT_AUDIT 台账纪律）：
  assumptions: ①成交价=当日开盘价（L1 快照可得，模拟盘无滑点）；
               ②「不达预期」=-阈值浮亏（用户原话的量化，待调校）；
               ③「不在趋势上」= pool_state 失守/下离枢组 或 trend_dir<=0。
  falsifier:  实盘对照滑点>1%、或止损口径过松/过紧（后验胜率<基准）。
  cost_if_wrong: 模拟盘无真金损失，代价=训练数据被污染（口径变更必须
                 在 trades 里记 rules_version，防止混样本训练）。

用法：
  python paper_trade.py open            # 开盘：执行隔夜卖出单+买入单
  python paper_trade.py review          # 盘后：止损判定+生成隔夜卖出清单
  python paper_trade.py equity          # 收盘净值入账+曲线数据
  python paper_trade.py status          # 账户概览
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

# 交易日间隔工具（冷却期判定）：借 strong_ts_window_research 的日历
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def R_cal_after(d0, d1, n):
    """d0 之后第 n 个交易日是否已到 d1（None=日历不可用，调用方放行）。"""
    try:
        import strong_ts_window_research as _R
        cal = _R.trade_calendar()
        return _R.after_days(cal, d0, n) if cal else None
    except Exception:
        return None


def is_trading_day(day):
    """交易日历门：返回 (是否交易日, 日历来源)。

    用 ts_trade_cal（SSE，覆盖 2020→2027，含未来日期与节假日），
    而不是 strong_ts_window_research.trade_calendar()——后者由历史日线
    生成、只到最新数据日，会把所有未来日误判成非交易日、永久停摆。
    表不可读时 fail-open 回退「周一~周五」，绝不因日历缺失阻断交易。
    """
    try:
        import pandas as pd
        d = pd.read_parquet(os.path.join(HERE, "data", "tdx",
                                        "ts_trade_cal", "part_0000.parquet"))
        s = d["cal_date"].astype(str).str.replace("-", "", regex=False)
        r = d[(s == day) & (d["exchange"] == "SSE")]
        if r.empty:
            r = d[s == day]
        if not r.empty:
            return float(r.iloc[0]["is_open"]) == 1.0, "ts_trade_cal"
    except Exception:
        pass
    w = dt.date(int(day[:4]), int(day[4:6]), int(day[6:8])).weekday()
    return w < 5, "weekday_fallback"

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PAPER_DIR = os.path.join(HERE, "data", "paper")
ACC = os.path.join(PAPER_DIR, "account.json")
TRADES = os.path.join(PAPER_DIR, "trades.jsonl")
EQUITY = os.path.join(PAPER_DIR, "equity.jsonl")

# ── 决策时点定价 / 抑制令（v2）────────────────────────────────────────
# 分钟行情：每根 K 的 time 是该分钟起点，close 要等这一分钟走完才可见。
# 因此「决策时刻 T 可见」= time < T 的最后一根，绝不能取 time == T。
MINUTE_ROOT = os.path.join(HERE, "data", "tdx", "ts_rt_min_roll")
DELIVERABLES = os.environ.get("ZB_DELIVERABLES") or os.path.abspath(
    os.path.join(HERE, "..", "..", "deliverables"))
SUP_CONSUMER_ID = "quant_backtest/paper_trade.py"
SUP_SCHEMA = "preopen_suppressed/v1"
# 交易时段：不在时段内一律拒绝成交（封死「盘后补录开盘单」这条回填路径）
SESSIONS = (("09:30", "11:30"), ("13:00", "15:00"))


# ── 规则参数（rules_v2：三层仓位架构，2026-11-13 老田仓位管理拍板）─────
# v1→v2 变更（全部入 trades rules_version=v2，旧样本不混）：
#   ① 三层仓位：核心仓 ≤60% / 机动仓(加仓专用) ≤30% / 现金垫 ≥10%
#      ——解「卖A的钱买B」与「当天买当天坏」的 T+1 死锁；
#   ② 加仓纪律：亏损不加（现价<成本不出加仓单）、金字塔每次≤5%、
#      同票加仓冷却 2 交易日；
#   ③ 盘中降级：当日票浮亏 ≤-4% → 封存等额现金垫 + 次日卖单最优先。
# v2.1（2026-11-13 老田拍板，一个月回放回测后的三条调校）：
#   ④ 降级线 -5%→-4%（回放中今日 6 只持仓 4 只触线，-5% 偏松）；
#   ⑤ 除权口径：avg_cost 保持原始成交价（现金记账用），判定改走
#      后复权收益率 _ret_of()，除权日不产生假浮亏/假降级；
#   ⑥ T2 通道收口：挂单必须有有效 chanlun 买点，剔除 strong/qpzm
#      单独抬层票（回算该通道 -7.61%、胜率 16%，最大毒源）。
# v2.2（2026-11-13 首次真实 open 前就绪性 dry-run 抓到的缺陷修复，
#       非策略参数变更；样本单独分档）：
#   ⑦ 换股护栏：同一次 open 里卖出的票不再从隔夜挂单买回；
#   ⑧ 早盘触发早于 09:30 时明确报错（当天无可见分钟价），不再静默零成交；
#   ⑨ 交易日历门（ts_trade_cal，覆盖未来）：周末/节假日 SKIP，
#      不判定、不写账户、不写流水，防止非交易日净值点污染训练样本。
# v2.3（2026-11-14 名单时序倒挂 A+B，非策略参数变更；样本单独分档）：
#   ⑩ 挂单逐单带 list_generated_at / list_basis_date，生成逻辑统一到
#      build_buy_orders()（review 与存量重建共用）；
#   ⑪ open 执行前按「基准日」硬判倒挂（缺时点/基准落后 → 拒绝成交），
#      周末重跑刷新 generated_at 但基准不变，不误杀；
#   ⑫ signal 快照只读挂单自带时点，不再读 live 名单，杜绝归因错标。
RULES = {
    "version": "v2.3",
    "capital": 10_000_000.0,        # 单策略上限 1000 万
    "init_pos_pct": 0.05,           # 首仓 5%
    "max_pos_pct": 0.30,            # 加仓后单票上限 30%
    "add_on_signal": True,          # 新买点加仓（用户：「加仓点且在买点上」）
    "stop_loss_pct": None,          # 硬止损（未设；用户口径=浮亏即卖）
    "lose_next_day_sell": True,     # 持有次日收盘浮亏 → 卖（不达预期）
    # ── v2 三层仓位 ──
    "core_cap_pct": 0.60,           # 核心仓上限 60%（首仓建仓总闸）
    "addon_budget_pct": 0.30,       # 机动仓：加仓专用额度（累计）
    "cash_floor_pct": 0.10,         # 现金垫：永不动用的保命钱
    "add_step_pct": 0.05,           # 金字塔：每次加仓 ≤5%
    "add_no_averaging_down": True,  # 亏损不加仓（现价<成本 → 拒绝加仓单）
    "add_cooldown_days": 2,         # 同票加仓冷却 2 交易日
    "intraday_mark_pct": -0.04,     # 当日票浮亏 -4% → 降级标记（v2.1，原 -5%）
    "require_chanlun_buy": True,    # v2.1 T2 通道收口：挂单必带 chanlun 买点
    "audit": {
        "assumptions": "成交价=严格早于决策时刻的最后一分钟收盘价；"
                       "浮亏即卖=不达预期；失守/下离枢=不在趋势上；"
                       "三层占比以 capital 为分母静态核算；"
                       "复权仅用于信号判定（后复权每股成本），现金账保持"
                       "原始成交口径；除权送股不调整 shares（模拟盘简化），"
                       "故除权日账面 pnl 与复权收益率可能不一致，卖出决策"
                       "以后者为准；复权因子取 <=day 最后可得日，缺数据回退"
                       "原始口径（fail-open）；卖出与买入须隔夜分离——"
                       "同一次 open 卖出的票当日不买回（换股护栏）；"
                       "隔夜挂单与 signal 快照必须来自同一份名单（基准日）",
        "falsifier": "实盘滑点>1%；口径过松/紧（后验胜率<基准）；"
                     "现金垫被击穿（连续弱势日卖单>现金垫）；"
                     "除权日出现假浮亏卖单（查 trades 的 adj_asof 字段）；"
                     "同一 open 内出现同票 SELL 后 BUY（查 rebound_blocked_last）；"
                     "BUY 的 signal.picks_generated 与挂单来源档不一致"
                     "（查 list_basis_date / pending_buys_meta）",
        "cost_if_wrong": "训练数据污染——rules_version 必须随档",
    },
}


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_account():
    if os.path.exists(ACC):
        return json.load(open(ACC, encoding="utf-8"))
    acc = {
        "created": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules": RULES,
        "cash": RULES["capital"],
        "positions": {},        # code -> {shares, avg_cost, opened, last_signal, snapshot}
        "pending_buys": [],     # [{code, name, tier, sources...}]
        "pending_sells": [],    # [{code, why}]
        "realized_pnl": 0.0,
    }
    _atomic_write_json(ACC, acc)
    return acc


def _now_hhmm():
    return dt.datetime.now().strftime("%H:%M")


def _resolve_day(day=None):
    return day or dt.date.today().strftime("%Y%m%d")


def _assert_session(decision_time):
    """决策时刻必须落在交易时段内；否则直接阻断，不许补录。"""
    if len(decision_time) != 5 or decision_time[2] != ":":
        raise RuntimeError("decision_time 必须是 HH:MM，实际=%s" % decision_time)
    if not any(lo <= decision_time <= hi for lo, hi in SESSIONS):
        raise RuntimeError("决策时刻 %s 不在交易时段内，拒绝成交定价" % decision_time)


def resolve_visible_prices(codes, day, decision_time):
    """返回决策时点「已可见」的成交价，绝不回填更早或更晚的价格。

    口径：分钟行情里 time 严格小于 decision_time 的最后一根 close。
      · 无可见分钟数据的票直接不成交（不回退 open、不回退实时快照）；
      · 同一 (day, decision_time) 永远得到同一结果 —— 可复算；
      · 返回值带 price_visible_at，训练样本可独立审计 I1 时点单调。
    """
    import glob
    import pandas as pd

    _assert_session(decision_time)
    iso = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    cut = "%s %s:00" % (iso, decision_time)
    parts = sorted(glob.glob(os.path.join(MINUTE_ROOT, "d_" + day,
                                          "part_*.parquet")))
    if not parts or not codes:
        return {}
    m = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    m["time"] = m["time"].astype(str)
    m = m[m["time"].str.startswith(iso)]
    m = m[m["ts_code"].isin(set(codes))]
    if m.empty:
        return {}
    m = m.drop_duplicates(["ts_code", "time"]).sort_values(["ts_code", "time"])

    # 09:30 集合竞价：开盘价在 09:30:00 即已确定，是此刻唯一可见价。
    # 取当日第一根 K 的 open 并显式标注，不构成「用更早价格回填」。
    if decision_time == "09:30":
        auc = m[m["time"] == iso + " 09:30:00"]
        out = {}
        for c in codes:
            rows = auc[auc["ts_code"] == c]
            if rows.empty:
                continue
            p = float(rows.iloc[0]["open"])
            if p > 0:
                out[c] = {"price": p, "name": "",
                          "price_source": "auction_open_0930",
                          "price_visible_at": iso + " 09:30:00"}
        return out

    # 盘中：取 time 严格小于决策时刻的最后一根 close。
    m = m[(m["time"] >= iso + " 09:30:00") & (m["time"] < cut)]
    if m.empty:
        return {}
    g = m.groupby("ts_code", sort=False)
    last_px = g["close"].last().to_dict()
    last_t = g["time"].last().to_dict()
    out = {}
    for c in codes:
        p = float(last_px.get(c, 0) or 0)
        if p > 0:
            out[c] = {"price": p, "name": "", "price_source": "minute_last_close",
                      "price_visible_at": last_t[c]}
    return out


def _audit_meta(quote, day, decision_time):
    """成交流水里必须留下的时点证据（训练样本可独立复核）。"""
    return {
        "decision_time": "%s-%s-%s %s:00" % (day[:4], day[4:6], day[6:],
                                            decision_time),
        "price_source": quote.get("price_source", "UNKNOWN"),
        "price_visible_at": quote.get("price_visible_at", ""),
        "exec_basis": "visible_minute_close_before_decision",
    }


def load_suppression(day):
    """读人工抑制令。字段不符一律抛错阻断（失败关闭），绝不静默当「今日无抑制」。"""
    p = os.path.join(DELIVERABLES, "preopen_suppressed_%s.json" % day)
    if not os.path.isfile(p):
        return None
    j = json.load(open(p, encoding="utf-8"))
    if j.get("schema") != SUP_SCHEMA:
        raise RuntimeError("抑制令 schema 不符：%s" % j.get("schema"))
    action = j.get("action")
    if action not in ("suppress", "allow"):
        raise RuntimeError("抑制令 action 无法识别：%s" % action)
    if str(j.get("exec_day")) != str(day):
        raise RuntimeError("抑制令 exec_day=%s 与执行日 %s 不符" % (j.get("exec_day"), day))
    consumers = j.get("consumers")
    if not isinstance(consumers, list) or SUP_CONSUMER_ID not in consumers:
        raise RuntimeError("抑制令未登记本消费方 %s，失败关闭" % SUP_CONSUMER_ID)
    return j if action == "suppress" else None


def _today_open_prices(codes):
    """[DEPRECATED / 仅展示] 新浪 L1 快照取当日开盘价。

    禁止用于成交定价：无论何时调用都返回当日 09:30 开盘价，
    盘中或盘后调用即形成「用更早价格回填成交」的前视。
    成交一律走 resolve_visible_prices(codes, day, decision_time)。
    """
    root = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from quote_snapshot import fetch_quotes  # noqa: E402

    q = {}
    for i in range(0, len(codes), 200):
        for x in fetch_quotes(codes[i:i + 200]) or []:
            q[x["code"]] = x
    out = {}
    for c in codes:
        sc = ("sh" if c.endswith(".SH") else "bj" if c.endswith(".BJ")
              else "sz") + c[:6]
        x = q.get(sc)
        if x and x.get("open"):
            out[c] = {"open": float(x["open"]), "close": float(x.get("price")
                                                             or x["open"]),
                      "name": x.get("name", "")}
    return out


def _to_sina(code):
    pre = "sh" if code.endswith(".SH") else ("bj" if code.endswith(".BJ")
                                             else "sz")
    return pre + code[:6]


def _cur_price_map(codes):
    """当日最新价（盘中=实时，盘后=收盘）。"""
    root = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from quote_snapshot import fetch_quotes  # noqa: E402

    out = {}
    for i in range(0, len(codes), 200):
        for x in fetch_quotes(codes[i:i + 200]) or []:
            c = x["code"]
            code = c[2:] + (".SH" if c.startswith("sh")
                            else ".BJ" if c.startswith("bj") else ".SZ")
            out[code] = float(x.get("price") or 0)
    return out


# ── 除权口径（v2.1）──────────────────────────────────────────────
# 回放回测抓到的坑：买入成本若与复权现价不在同一量纲，浮亏会被系统性
# 低估（002623 除权因子 1.1164，浮亏少算 11%）。生产代码两边原本都是
# 原始价、当下无错，但**持仓跨除权日**时价格跳空 → 假浮亏 → 误卖/误降级。
# 修法：持仓记「后复权每股成本」cost_adj，判定统一走 _pos_ret()。
# 只有买入基准 af_buy 与当日 af_now 都可取时才用复权口径，否则 fail-open
# 回退原始价比较 —— 绝不因为缺复权因子就阻断交易或凭空改变判定。
ADJ_ROOT = os.path.join(HERE, "data", "tdx", "ts_adj_factor")


def adj_factors(codes, day):
    """{code: (af, asof)}：<= day 的最后一个后复权因子。

    一次遍历全部分片、谓词下推只取目标票（6 只票 ~几千行，不动全表）。
    表缺失/读失败 → 返回空 dict，调用方 fail-open 回退原始口径。
    """
    import glob
    try:
        import pandas as pd
    except Exception:
        return {}
    parts = sorted(glob.glob(os.path.join(ADJ_ROOT, "part_*.parquet")))
    if not parts or not codes:
        return {}
    want = set(codes)
    best = {}
    d8 = str(day)[:8]
    for p in parts:
        try:
            t = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"],
                                filters=[("ts_code", "in", tuple(want))])
        except Exception:
            # 老 parquet 不支持谓词下推时退回全读再过滤
            try:
                t = pd.read_parquet(p, columns=["ts_code", "trade_date",
                                                "adj_factor"])
                t = t[t["ts_code"].isin(want)]
            except Exception:
                continue
        if t.empty:
            continue
        for c, d, f in zip(t["ts_code"].astype(str),
                           t["trade_date"].astype(str).str[:8],
                           t["adj_factor"].astype(float)):
            if d > d8 or not (f > 0):
                continue
            if c not in best or d > best[c][1]:
                best[c] = (f, d)
    return best


def _pos_ret(pos, code, price, adjm):
    """持仓浮动收益率 + 口径标签。返回 (ret, basis, asof)。

    basis='adj'：后复权口径（cost_adj 与 af_now 齐备）；
    basis='raw'：回退原始价比较（新建仓当天/因子表缺数据）。
    """
    cost_adj = pos.get("cost_adj")
    item = adjm.get(code)
    if cost_adj and cost_adj > 0 and item and item[0] > 0:
        return price * item[0] / cost_adj - 1, "adj", item[1]
    return price / pos["avg_cost"] - 1, "raw", None


def _adj_cost_on_buy(price, af_buy):
    """建仓时的后复权每股成本；因子取不到则 None（判定回退 raw）。"""
    return price * af_buy if af_buy and af_buy > 0 else None


# T2 通道闸门（独立模块，便于 portal 生成侧与金测复用）
from t2_channel_gate import gate_t2, gate_report, has_chanlun_buy  # noqa: E402


# ── 开盘：执行买卖挂单 ─────────────────────────────────────────────
def cmd_open(day=None, decision_time=None):
    day = _resolve_day(day)
    decision_time = decision_time or _now_hhmm()
    if decision_time < "09:30":
        raise RuntimeError(
            "决策时刻 %s 早于开盘 09:30：当日分钟行情尚未产生、无可见价，"
            "本次 open 不成交。请把 PaperTrade 早盘触发改到 09:31 之后。"
            % decision_time)
    _assert_session(decision_time)

    # 抑制令优先于一切：不建仓、不写账户、不写流水（按规则跳过，exit 0）
    sup = load_suppression(day)
    if sup:
        print("[SKIP] %s 盘前已人工抑制：本链不建仓、不写流水、不生成挂单" % day)
        print("       理由：%s" % str(sup.get("reason", ""))[:100])
        return 0

    acc = load_account()
    try:
        picks = _load_live_picks()
    except Exception as e:  # noqa: BLE001
        print("[open] 警告：live 名单不可读（%s），跳过倒挂硬判；"
              "挂单时点仍逐单留痕" % str(e)[:60])
        picks = {}
    # 1) 执行隔夜卖出单（价=决策时点可见价）
    sells = [s["code"] for s in acc.get("pending_sells") or []]
    orders = acc.get("pending_buys") or []
    # ── v2.3 名单时点契约：防名单时序倒挂复发 ────────────────────────
    # 11-13 实锤：存量 12 单来自 11-12 名单，open 却用 11-13 live 名单
    # 写 signal 快照 → 票与快照不是同一份名单，训练样本归因错。
    live_g, live_basis, live_src = _picks_meta(picks)
    if orders:
        missing = [b["code"] for b in orders if not b.get("list_basis_date")]
        if missing:
            raise RuntimeError(
                "[BLOCK] %d 单挂单缺 list_basis_date（旧口径/人工注入），"
                "无法证明名单未倒挂：%s；请重跑 review 重建挂单"
                % (len(missing), "、".join(missing[:5])))
        if live_basis:
            # 只在 live 基准可信（来自 trade_basis 或本身是交易日）时硬判；
            # 周末重跑/行情湖不可达会让 generated_at 漂移，不能据此误杀。
            live_ok = (live_src == "trade_basis"
                       or is_trading_day(live_basis)[0])
            if live_ok:
                stale = sorted({b["list_basis_date"] for b in orders
                                if b["list_basis_date"] < live_basis})
                if stale:
                    raise RuntimeError(
                        "[BLOCK] 挂单基准日 %s 落后于当前名单 %s（名单时序倒挂），"
                        "拒绝成交；请重跑 review 生成挂单"
                        % ("、".join(stale), live_basis))
            else:
                print("[open] 名单基准 %s 非交易日/回退口径，跳过倒挂硬判（只告警）"
                      % live_basis)
        if any(b.get("list_generated_at") != live_g for b in orders):
            print("[open] 提示：live 名单时点 %s 与挂单不同，但基准日一致，"
                  "按同基准放行；signal 快照一律按挂单自带时点记账" % live_g)
    # v2.1 T2 通道收口（open 侧二次护栏）：挂单是昨晚生成的，可能来自
    # 旧口径/人工注入，执行前再过一次闸，绝不把无买点抬层票买进来。
    if RULES["require_chanlun_buy"] and orders:
        orders, rej = gate_t2(orders)
        if rej:
            print("[open] T2 通道收口：剔除 %d 单（%s）"
                  % (len(rej), "、".join(r["why"] for r in rej[:3])))
            acc["t2_gate_last"] = gate_report(orders, rej)
    # ── v2.2 换股护栏：当日卖出的票，当日绝不买回 ──────────────────
    # 11-13 就绪性 dry-run 实锤：卖出段 del positions[c] 后，同一只票
    # 若仍在隔夜买入挂单里会被当作「首仓」重新建回 —— 走坏降级卖出的
    # 4 只票当天又全数买回，规则⓪形同虚设。
    # 边界：只拦「同一次 open 卖出→买回」。卖出后的冷却天数属策略参数，
    # 交老田拍板，不在代码里擅自设定。
    sold_today = set(sells)
    rebound = [b["code"] for b in orders if b["code"] in sold_today]
    if rebound:
        orders = [b for b in orders if b["code"] not in sold_today]
        acc["rebound_blocked_last"] = {"day": day, "codes": rebound,
                                       "why": "same_day_sell_then_buy"}
        print("[open] 换股护栏：当日已卖出 %d 只，不再买回（%s）"
              % (len(rebound), "、".join(rebound)))
    buy_codes = {b["code"] for b in orders}
    all_codes = sorted(set(sells) | buy_codes | set(acc["positions"]))
    adjm = adj_factors(all_codes, day)   # 后复权因子（判定层用，缺数据 fail-open）
    px = resolve_visible_prices(sorted(set(sells) | buy_codes), day,
                                decision_time)
    for s in acc.get("pending_sells") or []:
        c = s["code"]
        pos = acc["positions"].get(c)
        quote = px.get(c)
        if not pos or not quote:
            continue
        price = quote["price"]
        proceeds = pos["shares"] * price
        acc["cash"] += proceeds
        pnl = (price - pos["avg_cost"]) * pos["shares"]
        acc["realized_pnl"] += pnl
        del acc["positions"][c]
        _append_jsonl(TRADES, {
            "day": day, "action": "SELL", "code": c,
            "name": pos.get("name", ""), "price": round(price, 3),
            "shares": pos["shares"], "pnl": round(pnl, 0),
            "why": s.get("why", ""), "rules": RULES["version"],
            # 除权留痕：判定口径与现金账口径分开记，后验可识别除权日假亏
            "pnl_basis": "raw_cash", "cost_adj": pos.get("cost_adj"),
            "adj_asof": (adjm.get(c) or (None, None))[1],
            **_audit_meta(quote, day, decision_time),
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
        })
        print("[open] 卖出 %s %s @%.2f 盈亏 %+.0f（%s）"
              % (c, pos.get("name", ""), price, pnl, s.get("why", "")))
    acc["pending_sells"] = []

    # 2) 执行买入挂单（T2 名单，首仓 5%；已持仓且信号在→加仓至≤30%）
    budget = acc["cash"]
    for b in orders:
        c = b["code"]
        quote = px.get(c)
        if not quote:
            print("[open] %s 决策时点无可见价，不成交" % c)
            continue
        price = quote["price"]
        pos = acc["positions"].get(c)
        cap_total = RULES["capital"] * RULES["max_pos_pct"]
        if pos:
            if not RULES["add_on_signal"]:
                continue
            # ── v2 加仓纪律（三层仓位架构）────────────────────────
            # ①亏损不加：复权收益率 <0 → 拒绝（摊平是纪律禁区）
            ret_now, _basis, _asof = _pos_ret(pos, c, price, adjm)
            if RULES["add_no_averaging_down"] and ret_now < 0:
                print("[open] 加仓拒绝 %s：复权浮亏 %.1f%%（亏损不加）"
                      % (c, ret_now * 100))
                continue
            # ②冷却期：距上次加仓 <2 交易日 → 拒绝（防同信号重复加）
            last_add = pos.get("last_add_day")
            if last_add:
                try:
                    gap_t = R_cal_after(last_add, day, RULES["add_cooldown_days"])
                    if gap_t is None or gap_t <= 0:
                        print("[open] 加仓拒绝 %s：冷却期内（上次 %s）"
                              % (c, last_add))
                        continue
                except Exception:
                    pass
            cur_val = pos["shares"] * price
            # ③金字塔步长 + 单票上限 + 机动仓余额三重封顶
            addon_used = acc.get("addon_used", 0.0)
            addon_left = RULES["capital"] * RULES["addon_budget_pct"] - addon_used
            add_val = min(RULES["capital"] * RULES["add_step_pct"],
                          cap_total - cur_val, addon_left, budget)
            if add_val < price * 100:
                continue
            shares = int(add_val / price / 100) * 100
            if shares <= 0:
                continue
            old_shares = pos["shares"]
            # 复权成本同步加权（旧仓无 cost_adj 时保持 None→判定回退 raw）
            af_now = (adjm.get(c) or (None, None))[0]
            if pos.get("cost_adj") and af_now:
                pos["cost_adj"] = (pos["cost_adj"] * old_shares
                                   + price * af_now * shares) \
                    / (old_shares + shares)
            pos["avg_cost"] = (pos["avg_cost"] * old_shares + price * shares) \
                / (old_shares + shares)
            pos["shares"] += shares
            pos["last_add_day"] = day
            acc["addon_used"] = addon_used + shares * price
            acc["cash"] -= shares * price
            # 现金垫不可击穿
            if acc["cash"] < RULES["capital"] * RULES["cash_floor_pct"]:
                print("[open] 警告：现金垫接近下限（%.0f 万）"
                      % (acc["cash"] / 1e4))
            budget = acc["cash"]
            _append_jsonl(TRADES, {
                "day": day, "action": "ADD", "code": c, "price": round(price, 3),
                "shares": shares, "rules": RULES["version"],
                "why": "买点加仓", "signal": b.get("sources"),
                **_audit_meta(quote, day, decision_time),
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            })
            print("[open] 加仓 %s +%d 股 @%.2f" % (c, shares, price))
        else:
            # ── v2 核心仓总闸：首仓名义总市值 ≤60% capital ──
            core_used = sum(
                p["shares"] * p.get("avg_cost", 0)
                for p in acc["positions"].values())
            core_left = RULES["capital"] * RULES["core_cap_pct"] - core_used
            val = min(RULES["capital"] * RULES["init_pos_pct"],
                      core_left, budget)
            shares = int(val / price / 100) * 100
            if shares <= 0:
                print("[open] 现金不足/核心仓满，跳过 %s" % c)
                continue
            # v2.3：signal 快照一律读「挂单自带的名单时点」，绝不读 live。
            # 否则名单时序倒挂时会出现「票来自 A 名单、快照标 B 名单」，
            # 字段齐全看着自洽、内容却是错的（最隐蔽的训练数据污染）。
            ometa = acc.get("pending_buys_meta") or {}
            snapshot = {
                "tier": b.get("tier"),
                "sources": b.get("sources"),
                "risk_gate": ometa.get("risk_gate"),
                "picks_generated": b.get("list_generated_at"),
                "basis": ometa.get("trade_basis") or b.get("list_basis_date"),
                "list_basis_date": b.get("list_basis_date"),
                "list_basis_source": b.get("list_basis_source"),
                "chanlun_buy": has_chanlun_buy(b),   # v2.1 通道闸门留痕
            }
            af_buy, af_asof = adjm.get(c) or (None, None)
            acc["positions"][c] = {
                "name": b.get("name", quote.get("name", "")),
                "shares": shares, "avg_cost": price,
                "opened": day, "snapshot": snapshot,
                # 后复权每股成本（判定口径）；因子缺失→None→fail-open 回退 raw
                "cost_adj": _adj_cost_on_buy(price, af_buy),
                "cost_adj_asof": af_asof,
            }
            acc["cash"] -= shares * price
            budget = acc["cash"]
            _append_jsonl(TRADES, {
                "day": day, "action": "BUY", "code": c,
                "name": b.get("name", ""), "price": round(price, 3),
                "shares": shares, "rules": RULES["version"],
                "signal": snapshot,      # 训练样本：入场时的完整信号快照
                **_audit_meta(quote, day, decision_time),
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            })
            print("[open] 买入 %s %s %d 股 @%.2f（首仓 %.0f 万）"
                  % (c, b.get("name", ""), shares, price,
                     shares * price / 1e4))
    acc["pending_buys"] = []
    _atomic_write_json(ACC, acc)
    print("现金余 %.0f 万，持仓 %d 只"
          % (acc["cash"] / 1e4, len(acc["positions"])))


# ── 盘后评审：不达预期判定 + 明日卖出清单 + 次日买入挂单 ─────────────
# ── v2.3 名单时点契约（名单时序倒挂防复发）────────────────────────────
# 倒挂定义：隔夜挂单来自「旧基准日」的名单，open 却拿实时 live 名单写
# signal 快照 → 票来自 A 名单、快照标 B 名单，字段齐全但归因错。
# 判据用 trade_basis 的基准日（周末重跑不变），不用 generated_at 时间戳：
# TodaysPicks 周末照跑会刷新 generated_at，但基准日仍是上个交易日。
def _picks_meta(picks):
    """抽名单 (generated_at, basis_date, basis_source)。

    basis_date 优先取 trade_basis 里的 YYYY-MM-DD（行情湖基准日）；
    行情湖不可达时 trade_basis 无日期，才回退 generated_at 的日期，
    并标 source=generated_at_fallback，供 open 决定是否硬判倒挂。
    """
    g = str(picks.get("generated_at") or "")
    b = str(picks.get("trade_basis") or "")
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", b)
    if m:
        return g, "".join(m.groups()), "trade_basis"
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", g)
    return (g, "".join(m.groups()) if m else "", "generated_at_fallback")


def _load_live_picks():
    """当前 live 名单（open 做倒挂校验用，不用于写 signal 快照）。"""
    return json.load(open(os.path.join(HERE, "..", "portal", "data",
                                      "todays_picks.json"), encoding="utf-8"))


def build_buy_orders(picks, acc, day):
    """从名单构造隔夜买入挂单（首仓 + 加仓），逐单带名单时点契约。

    cmd_review 与「存量挂单重建」共用同一份实现，杜绝两套口径。
    返回 (orders, gate, meta)；meta 落 acc["pending_buys_meta"]。
    """
    g, basis, bsrc = _picks_meta(picks)
    t2 = [s for s in picks.get("stocks") or [] if s.get("tier") == "T2"]
    if RULES["require_chanlun_buy"]:
        t2, rejected = gate_t2(t2)
        gate = gate_report(t2, rejected)
    else:
        rejected, gate = [], {"applied": False}
    if rejected:
        print("[review] T2 通道收口：%d→%d 单（剔除 %d：%s）"
              % (gate["t2_in"], gate["t2_kept"], len(rejected),
                 "、".join(r["why"] for r in rejected[:3])))
    meta = {
        "generated_at": g, "basis_date": basis, "basis_source": bsrc,
        "trade_basis": str(picks.get("trade_basis") or ""),
        "risk_gate": picks.get("risk_gate"),
        "built_for_day": day,
    }
    pb, add_sigs = [], []
    for s in t2:
        sig = {
            "code": s["code"], "name": s.get("name"), "tier": "T2",
            "sources": [{"s": x.get("s"), "tag": x.get("tag"),
                         "dt": x.get("dt")}
                        for x in s.get("sources") or []
                        if isinstance(x, dict)],
            "list_generated_at": g,
            "list_basis_date": basis,
            "list_basis_source": bsrc,
        }
        if s["code"] in acc["positions"]:
            add_sigs.append(sig)   # 加仓挂单：open 的 pos 分支消费
        else:
            pb.append(sig)
    rg = picks.get("risk_gate") or {}
    meta["weak_day"] = bool(rg.get("weak_day"))
    if meta["weak_day"]:
        print("[review] 弱势日：明日建仓名单仅保留前 6 只（半仓试错）")
        pb = pb[:6]
    sup = load_suppression(day)
    meta["suppressed"] = bool(sup)
    if sup:
        print("[review] 抑制日：不生成买入挂单（止损卖出判定保留，只减敞口不造信号）")
        pb, add_sigs = [], []
    meta["n_orders"] = len(pb) + len(add_sigs)
    return pb + add_sigs, gate, meta


def cmd_review(day=None):
    acc = load_account()
    day = _resolve_day(day)
    codes = list(acc["positions"])
    if not codes:
        print("无持仓")
    px = _cur_price_map(codes) if codes else {}
    adjm = adj_factors(codes, day) if codes else {}   # 判定层后复权因子

    pending_sells = []
    intraday_marks = []      # v2 盘中降级：当日票浮亏超阈（明日最优先卖）
    basis_stat = {"adj": 0, "raw": 0}
    for c, pos in acc["positions"].items():
        price = px.get(c)
        if not price:
            continue
        # v2.1：统一后复权口径收益率（除权日不产生假浮亏 → 不误卖/误降级）
        ret, basis, af_asof = _pos_ret(pos, c, price, adjm)
        basis_stat[basis] += 1
        pos["last_ret_basis"] = basis
        pos["last_adj_asof"] = af_asof
        # ── v2 规则⓪：当日开仓票盘中降级标记（T+1 不能卖，但记账+排序）──
        if pos.get("opened") == day:
            if ret <= RULES["intraday_mark_pct"]:
                pos["degraded"] = day
                intraday_marks.append((c, ret))
                # v2.1 时序修复：打标的**同一晚**就排进明早卖单。
                # 原实现只打标、等「次日 review」再见 degraded 排单，
                # 结果今日走坏的票要到后日开盘才卖，迟整整一个交易日。
                # T+1 合规：今日买入不可卖，明日开盘卖出（持仓日≥1）合法。
                pending_sells.insert(0, {"code": c, "why":
                    "盘中降级（当日浮亏 %.1f%% ≤ %.0f%%，明日最优先卖）"
                    % (ret * 100, RULES["intraday_mark_pct"] * 100)})
            continue                           # 当日票不参与①②（T+1）
        # 规则①：持有次日收盘浮亏 → 不达预期，直接卖
        # （昨日已被标记 degraded 的票无条件最优先——规则⓪的次日执行）
        if pos.get("degraded") and pos.get("degraded") != day:
            pending_sells.insert(0, {"code": c, "why":
                                     "盘中降级（%s 浮亏触发）" % pos["degraded"]})
            continue
        if RULES["lose_next_day_sell"] and pos.get("opened") != day and ret < 0:
            pending_sells.append({"code": c, "why":
                                  "不达预期（浮亏 %.1f%%）" % (ret * 100)})
            continue
        # 规则②：不在趋势上（盘后结构筛选——失守/下离枢）
        # 数据源：当天 pool_live 最新快照 groups
        # T+1 防护：当天开仓的票不进隔夜卖单（当日买入不可卖），
        # 留到次日 review 再判——首版漏判例（2026-11-13 老田 T+1 检查）。
        if pos.get("opened") == day:
            continue
        try:
            import glob
            snaps = sorted(glob.glob(os.path.join(
                HERE, "reports", "pool_live_%s_*.json" % day)))
            bad_groups = ("买点失守", "三买失守", "三买失守", "下离枢")
            flag = None
            if snaps:
                lv = json.load(open(snaps[-1], encoding="utf-8"))
                for gname, items in (lv.get("groups") or {}).items():
                    if any(gname.startswith(bg) for bg in bad_groups):
                        if any(it.get("code") == c for it in items or []):
                            flag = gname
                            break
            if flag:
                pending_sells.append({"code": c, "why": "不在趋势上（%s）" % flag})
        except Exception as e:  # noqa: BLE001
            print("[review] 结构筛选失败（软）：%s" % str(e)[:60])

    acc["pending_sells"] = pending_sells

    # 次日买入挂单：来源是「今晚 18:20 生成的新名单」（18:25 review 晚于
    # 名单生成，名单时序倒挂的调度层已收口）。v2.3 起逐单带名单时点契约，
    # 生成逻辑与「存量挂单重建」共用 build_buy_orders()，杜绝两套口径。
    try:
        picks = _load_live_picks()
        orders, gate, ometa = build_buy_orders(picks, acc, day)
        acc["t2_gate_last"] = gate
        acc["pending_buys"] = orders
        acc["pending_buys_meta"] = ometa
        print("[review] 隔夜挂单 %d 单（名单 %s / 基准日 %s）"
              % (len(orders), ometa["generated_at"], ometa["basis_date"]))
    except Exception as e:  # noqa: BLE001
        print("[review] 名单读取失败（保留空挂单）：%s" % str(e)[:60])

    _atomic_write_json(ACC, acc)
    for c, r in intraday_marks:
        print("[review] 盘中降级标记 %s（今日浮亏 %.1f%% ≤ %.0f%%，明日最优先卖）"
              % (c, r * 100, RULES["intraday_mark_pct"] * 100))
    print("[review] 判定口径：后复权 %d / 原始 %d（缺复权因子回退，不阻断）"
          % (basis_stat["adj"], basis_stat["raw"]))
    print("[review] 明日卖出 %d 单：%s" % (len(pending_sells),
          "、".join(s["code"] for s in pending_sells) or "无"))


# ── 收盘净值 ───────────────────────────────────────────────────────
def cmd_equity(day=None):
    acc = load_account()
    day = _resolve_day(day)
    codes = list(acc["positions"])
    px = _cur_price_map(codes) if codes else {}
    mv = sum(pos["shares"] * px.get(c, pos["avg_cost"])
             for c, pos in acc["positions"].items())
    total = acc["cash"] + mv
    _append_jsonl(EQUITY, {
        "day": day, "cash": round(acc["cash"], 0),
        "market_value": round(mv, 0), "total": round(total, 0),
        "nav": round(total / RULES["capital"], 4),
        "n_pos": len(acc["positions"]),
        "suppressed": bool(load_suppression(day)),
        "realized_pnl": round(acc["realized_pnl"], 0),
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
    })
    print("[equity] %s 总资产 %.0f 万（nav %.4f）持仓 %d 只"
          % (day, total / 1e4, total / RULES["capital"],
             len(acc["positions"])))


def cmd_status():
    acc = load_account()
    codes = list(acc["positions"])
    px = _cur_price_map(codes) if codes else {}
    day = _resolve_day()
    adjm = adj_factors(codes, day) if codes else {}
    mv = sum(pos["shares"] * px.get(c, pos["avg_cost"])
             for c, pos in acc["positions"].items())
    total = acc["cash"] + mv
    print("模拟账户 %s（账户 rules %s / 代码 rules %s）"
          % (acc.get("created"), acc["rules"]["version"],
             RULES["version"]))
    print("现金 %.0f 万 | 市值 %.0f 万 | 总资产 %.0f 万（nav %.4f）| 已实现盈亏 %+.0f"
          % (acc["cash"] / 1e4, mv / 1e4, total / 1e4,
             total / RULES["capital"], acc["realized_pnl"]))
    for c, pos in acc["positions"].items():
        p = px.get(c)
        if p:
            ret, basis, _ = _pos_ret(pos, c, p, adjm)
            ret *= 100
        else:
            ret, basis = 0, "n/a"
        print("  %-11s %-8s %d股 成本%.2f 现价%.2f 浮动%+.1f%%[%s]（%s入）"
              % (c, pos.get("name", ""), pos["shares"], pos["avg_cost"],
                 p or 0, ret, basis, pos.get("opened")))
    for s in acc.get("pending_sells") or []:
        print("  [明日卖] %s %s" % (s["code"], s["why"]))


def main():
    ap = argparse.ArgumentParser(description="模拟盘 v2（决策时点定价+抑制令感知）")
    ap.add_argument("cmd", choices=["open", "review", "equity", "status"])
    ap.add_argument("--date", default=None,
                    help="数据日 YYYYMMDD；复算历史必须同时给 --decision-time")
    ap.add_argument("--decision-time", default=None,
                    help="成交决策时刻 HH:MM；决定「哪些价格已经可见」")
    a = ap.parse_args()
    if a.date and a.date != dt.date.today().strftime("%Y%m%d") \
            and not a.decision_time:
        print("[BLOCK] 复算历史日必须显式给 --decision-time，"
              "禁止用当前时刻冒充历史决策时刻")
        return 2
    if a.decision_time and a.cmd != "open":
        print("[BLOCK] --decision-time 只对 open 有意义")
        return 2
    # ── 交易日历门（v2.2）：周末/节假日不判定、不写账户、不写流水 ──
    # 计划任务全年每日触发。缺了这道门，周末会拿周五收盘价做卖出判定、
    # 生成隔夜挂单，并往 equity.jsonl 写一条非交易日净值点（污染样本）。
    # 业务跳过 exit 0，与「程序崩溃 exit 非 0」严格区分。
    if a.cmd in ("open", "review", "equity"):
        day = _resolve_day(a.date)
        ok, src = is_trading_day(day)
        if not ok:
            print("[SKIP] %s 非交易日（日历=%s）：不判定、不写账户、不写流水"
                  % (day, src))
            return 0
    try:
        return {"open": lambda: cmd_open(a.date, a.decision_time),
                "review": lambda: cmd_review(a.date),
                "equity": lambda: cmd_equity(a.date),
                "status": cmd_status}[a.cmd]() or 0
    except RuntimeError as e:
        print("[BLOCK] %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
