# -*- coding: utf-8 -*-
"""K44 统一虚拟账户：每日模拟建仓 + 净值曲线（模拟账本，非实盘）。

数据源（全部只读既有产线快照，无独立行情拉取）：
· qpzm 青萍之末  reports/rps_latest.json  res[].action=="买入"
  —— 全站唯一四市场源（CN/US/HK/CRYPTO，含虚拟币与环境闸门）
· 9dim 九维评分  data/nine_dim_scores.json  rows[].score>=80 且 full
  —— A 股，自带 industry/close
· sig4 四策略信号  positions.json（根目录）  pending[] 待买信号
  —— US/HK/CRYPTO，平仓=跟随源账本（源 open 已无该标的 → 平）
· chanlun 缠论结构  data/chanlun_paper.json  最新日新仓 type 含「买」
  —— 仅 1买/2买/3买 建多头仓（卖点是离场观察仓）；背驰/量能标记多者优先；
     平仓=跟随源账本（源 open 已无该 code → 平）
  —— K49 判例：sig4/chanlun 各自纸面账本（positions/chanlun_paper）
     与统一账户是两套视角：源账本=策略体系跟踪（大屏 books 段），
     统一子账户=资金化模拟（accounts 段），并存展示不冲突

规则（K44-B 阶段：只建仓；自动平仓 K44-C 上）：
· 每策略独立子账户，各 100 万虚拟资金，MAX_POS 等权上限
· 同一标的同账户只建一次（幂等：首日回填当前选股，之后每日增量）
· 现金不足单标的预算 → 跳过并留痕 skip_log
· 成交价 = 当日快照收盘近似；持仓每日刷新 last_px / 浮盈
· K57 价格保护三规则：初始止损 entry×0.98（方法论原口径「信号K低点-2%」
  的工程近似——虚拟账户无信号K数据）/ 移动止盈 持仓期最高收盘回撤≥5%
  / rps骤降 RPS_mix 单日降≥15 分位（环境预警细化，随闸门可重建）
  优先级：初始止损 > 移动止盈 > rps骤降 > 现有信号类；调参改顶部常量

计划任务：AI量化模拟建仓 23:50（RPS 共振 23:35 之后，全天选股齐）。
CLI：python portfolio.py          每日建仓跑批
     python portfolio.py --status 打印账本概览
     python portfolio.py --reset   清空重置（谨慎）
"""

import json
import os
import shutil
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
DATA = os.path.join(HERE, "data")
LEDGER = os.path.join(REPORTS, "portfolio.json")

# ── K57 价格保护风控参数（三止损取最严 + 环境预警退出 + 动态止盈）──
# 调参方式：直接改下面三个常量，保存后次日跑批生效（不走配置文件）。
# · STOP_PCT/TRAIL_PCT 为价格比例（0.02 即 -2%）；RPS_DROP 为 RPS_mix
#   单日下降的分位数阈值；置 0 可停用对应规则
# · 初始止损口径差异说明见 _risk_exit_of docstring
STOP_PCT = 0.02  # 初始止损：px ≤ entry_px×(1-STOP_PCT) 即离场
TRAIL_PCT = 0.05  # 移动止盈：px 自持仓期最高收盘 hi_px 回撤 ≥TRAIL_PCT 平
RPS_DROP = 15  # rps骤降：RPS_mix 单日下降 ≥RPS_DROP 分位 → 预警退出

# ── 子账户默认模板：名称/图标/选股源写死，资金与开关走配置 ──
SUB_ACCOUNTS = [
    {
        "id": "qpzm",
        "name": "青萍之末",
        "icon": "🌊",
        "capital": 1_000_000,
        "max_pos": 20,
        "markets": "CN/US/HK/CRYPTO",
    },
    {
        "id": "9dim",
        "name": "九维评分",
        "icon": "🧮",
        "capital": 1_000_000,
        "max_pos": 20,
        "markets": "CN",
    },
    {
        "id": "sig4",
        "name": "四策略信号",
        "icon": "📐",
        "capital": 1_000_000,
        "max_pos": 20,
        "markets": "US/HK/CRYPTO/CN",
    },
    {
        "id": "chanlun",
        "name": "缠论结构",
        "icon": "🧭",
        "capital": 1_000_000,
        "max_pos": 20,
        "markets": "CN",
    },
]

# ── 运行配置（data/portfolio_config.json，改完次日跑批生效）──────
# · capital 改资金：平滑生效（现金按差额注资/撤资，不动持仓）
# · start_date 开始模拟交易日：之前只刷新已有持仓、不建新仓
# · enabled=false：停用（停止新仓，存量持仓照常刷新）
CONFIG_FP = os.path.join(DATA, "portfolio_config.json")
CONFIG_DEFAULT = {
    "_doc": "K44 模拟建仓配置：改 capital 自动注资/撤资（K54 起按份额净值申赎）；start_date=开始建仓日期(YYYY-MM-DD，空=立即，账户级优先/全局回退)；enabled=false 停新仓；hold_max=持有期上限(交易日)；gate_bad_max=市场闸门连续收紧N日全平该市场仓；out_max=跌出选股池N日平仓；stop_loss_pct/take_profit_pct=单仓止损/止盈阈值(相对成本价%，账户级可覆盖)；max_drawdown_pct=净值自峰值回撤N%熔断停新仓(0=停用)；max_pos=持仓数上限；notify=跑批后推送飞书战报；——K54 机构化参数——sizing=atr(波动率目标，数据缺失回退等权)/equal(等权)；risk_pct_per_trade=单笔风险预算(净值%)；atr_stop_mult=止损距离ATR倍数；sizing_band=ATR仓位相对等权的下限比例(0.5=允许0.5~1.5倍)；be_trigger_pct/be_floor_pct=保本止损：浮盈峰值达N%后止损线上移至成本+N%；single_pos_cap_pct=单标的市值上限(账户净值%)；cross_acct_cap_pct=跨账户同一标的合计上限(四账户总净值%)；dd_warn_pct=回撤警诫线(黄标)；dd_cut_pct=深度回撤线(红标+减仓会诊清单)",
    "start_date": "",
    "hold_max": 20,  # 持有期上限（交易日，与缠论 hold20 同口径）
    "gate_bad_max": 5,  # 环境闸门连续收紧 N 个交易日 → 清该市场仓避险
    "out_max": 3,  # 跌出选股池 N 个交易日 → 平仓
    "stop_loss_pct": 8,  # 单仓浮亏达 -N% → 止损离场（价格纪律，可重建）
    "take_profit_pct": 15,  # 单仓浮盈达 +N% → 止盈离场（价格纪律，可重建）
    "max_drawdown_pct": 10,  # K51-C 净值自峰值回撤达 N% → 熔断停新仓（回升自动恢复；0=停用）
    "notify": True,  # 每日跑批后推送飞书战报
    # ── K54-B 机构化仓位与风控 ──────────────────────────────
    "sizing": "atr",  # B1 建仓 sizing：atr=波动率目标（缺数回退等权）/ equal
    "risk_pct_per_trade": 1.0,  # B1 单笔风险预算（净值 %）
    "atr_stop_mult": 2.0,  # B1 止损距离 = ATR×N（价格），下限 4%
    "sizing_band": 0.5,  # B1 ATR 仓位限幅带：等权 × [N, 1+(1-N)]
    "be_trigger_pct": 8,  # B2 保本触发：浮盈峰值达 N% 后激活（0=停用）
    "be_floor_pct": 0.5,  # B2 保本底线：止损线上移至成本 +N%
    "single_pos_cap_pct": 10,  # B3 单标的市值 ≤ 账户净值 N%（0=停用）
    "cross_acct_cap_pct": 15,  # B3 跨账户同一标的合计 ≤ 四账户总净值 N%（0=停用）
    "dd_warn_pct": 5,  # B4 回撤警诫线（战报黄标，不停建）
    "dd_cut_pct": 15,  # B4 深度回撤线（红标+减仓会诊清单，不自动减仓）
    "accounts": {
        "qpzm": {"enabled": True, "capital": 1_000_000, "max_pos": 20},
        "9dim": {"enabled": True, "capital": 1_000_000, "max_pos": 20},
        "sig4": {"enabled": True, "capital": 1_000_000, "max_pos": 20},
        "chanlun": {"enabled": True, "capital": 1_000_000, "max_pos": 20},
    },
}


def load_config():
    cfg = _load_json(CONFIG_FP)
    if not cfg.get("accounts"):
        _atomic(CONFIG_FP, CONFIG_DEFAULT)
        return CONFIG_DEFAULT
    return cfg


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return time.strftime("%Y-%m-%d")


_BACKUP_DIR = os.path.join(os.path.dirname(LEDGER), "portfolio_backups")
_BACKUP_KEEP = 50  # 保留最近 50 份滚动备份
_BACKUP_GAP = 60  # 同分钟内多次写盘只备一次（防抖）
_last_backup_t = 0.0


def _backup_ledger():
    """K52-A 账本自动备份：任何写盘前把当前账本快照到 backups/
    （K51 判例②教训：手工 bak 会被重跑覆盖，必须有引擎侧滚动备份）。
    保留最近 _BACKUP_KEEP 份，同分钟防抖。"""
    global _last_backup_t
    now = time.time()
    if now - _last_backup_t < _BACKUP_GAP or not os.path.exists(LEDGER):
        return
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    dst = os.path.join(
        _BACKUP_DIR, "portfolio-%s.json" % time.strftime("%Y%m%d-%H%M%S")
    )
    try:
        shutil.copyfile(LEDGER, dst)
        _last_backup_t = now
        olds = sorted(f for f in os.listdir(_BACKUP_DIR) if f.startswith("portfolio-"))
        for f in olds[:-_BACKUP_KEEP]:
            os.remove(os.path.join(_BACKUP_DIR, f))
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio.py", exc)


def _atomic(fp, obj):
    if fp == LEDGER:
        _backup_ledger()  # 写坏前的最后一份好账本（回滚点）
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)


def _load_json(fp):
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_ledger():
    led = _load_json(LEDGER)
    acs = led.get("accounts")
    if not isinstance(acs, dict):
        acs = {}
    cfg = load_config()
    for tpl in SUB_ACCOUNTS:
        cc = (cfg.get("accounts") or {}).get(tpl["id"]) or {}
        if tpl["id"] not in acs:
            cap = cc.get("capital", tpl["capital"])
            acs[tpl["id"]] = {
                "name": tpl["name"],
                "icon": tpl["icon"],
                "capital": cap,
                "cash": cap,
                "shares": cap,  # K54 份额（份）
                "open": [],
                "closed": [],
                "nav_curve": [],
                "skip_log": [],
                "adjust_log": [],
            }
    led["accounts"] = acs
    return led, cfg


# ── 选股源适配器：返回 [(market, code, name, px, note)] ──────
def _pick_qpzm():
    """rps_latest res[]：action=买入（观察/回避/环境闸门一律不建）。"""
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = []
    for r in snap.get("res") or []:
        if r.get("action") == "买入" and r.get("px"):
            out.append(
                (
                    r.get("market") or "",
                    r.get("code") or "",
                    r.get("name") or "",
                    float(r["px"]),
                    "score=%s %s" % (r.get("score"), r.get("dims") or ""),
                )
            )
    return out, snap.get("ts", "")


def _pick_9dim():
    """九维 rows：full（覆盖达标）× score top10 ——模拟建仓口径。
    判例：九维分数体系当前最高 ~63，固定阈值 80 恒空仓（buy_th=90 长期 0 只），
    模拟账户用 top10 相对口径才能形成策略成绩；绝对阈值口径只用于展示页。"""
    snap = _load_json(os.path.join(DATA, "nine_dim_scores.json"))
    rows = [
        r
        for r in snap.get("rows") or []
        if r.get("full") and r.get("close") and r.get("score") is not None
    ]
    rows.sort(key=lambda r: -r["score"])
    out = []
    for r in rows[:10]:
        out.append(
            (
                "CN",
                r.get("code") or "",
                r.get("name") or "",
                float(r["close"]),
                "九维%.0f %s" % (r["score"], r.get("industry") or ""),
            )
        )
    return out, snap.get("generated_at", "")


PICKERS = {"qpzm": _pick_qpzm, "9dim": _pick_9dim}


def _pick_sig4():
    """positions.json pending[]：四策略待买信号（多市场）。
    判例：pending 内同 symbol 可能多条（如 XOM 同日 trend+boll 两信号）
    → 去重取首条（信号已足够触发建仓）。"""
    snap = _load_json(os.path.join(HERE, "positions.json"))
    out, seen = [], set()
    for r in snap.get("pending") or []:
        code = r.get("symbol") or ""
        if not code or code in seen or not r.get("last_px"):
            continue
        seen.add(code)
        out.append(
            (
                r.get("market") or "US",
                code,
                r.get("name") or "",
                float(r["last_px"]),
                "%s %s" % (r.get("strategy") or "", r.get("sig_date") or ""),
            )
        )
    return out, snap.get("ts", "")


def _chan_qkey(p):
    """缠论信号质量分：背驰/量能等标记命中数（tags 值为 True 的个数）。"""
    t = p.get("tags") or {}
    return sum(1 for v in t.values() if v)


def _pick_chanlun():
    """chanlun_paper open[]：最新 detect 日新仓 type 含「买」（仅 1买/2买/3买）。
    用户口径（K49）：卖点信号是离场观察仓不建多头；每日买点 ~30 个超容量
    → 背驰/量能标记多者优先，同分按 code 稳定排序；同 code 去重取最优。"""
    snap = _load_json(os.path.join(DATA, "chanlun_paper.json"))
    rows = [p for p in snap.get("open") or [] if p.get("entry_date")]
    if not rows:
        return [], snap.get("updated_at", "")
    latest = max(p.get("detect_date") or "" for p in rows)
    buys = [
        p
        for p in rows
        if p.get("detect_date") == latest and "买" in (p.get("type") or "")
    ]
    buys.sort(key=lambda p: (-_chan_qkey(p), p.get("code") or ""))
    out, seen = [], set()
    for p in buys:
        c = p.get("code") or ""
        px = float(p.get("last_px") or 0 or p.get("entry_px") or 0)
        if not c or c in seen or px <= 0:
            continue
        seen.add(c)
        out.append(
            (
                "CN",
                c,
                p.get("name") or "",
                px,
                "%s·标记%d" % (p.get("type") or "", _chan_qkey(p)),
            )
        )
    return out, snap.get("updated_at", "")


PICKERS.update(sig4=_pick_sig4, chanlun=_pick_chanlun)


# ── 价格刷新适配器：code -> 最新价（源内找不到则不刷） ─────
def _px_map_qpzm():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    return {
        r.get("code"): r.get("px")
        for r in snap.get("res") or []
        if r.get("code") and r.get("px")
    }


def _px_map_9dim():
    snap = _load_json(os.path.join(DATA, "nine_dim_scores.json"))
    return {
        r.get("code"): r.get("close")
        for r in snap.get("rows") or []
        if r.get("code") and r.get("close")
    }


PX_MAPS = {"qpzm": _px_map_qpzm, "9dim": _px_map_9dim}


# ── K44-C 平仓引擎：择时择势规则 ─────────────────────
# ① 环境闸门：市场闸门连续 gate_bad_max 个交易日收紧 → 清该市场仓避险
# ①.5 价格保护（K57）：初始止损 > 移动止盈 > rps骤降（先于信号类，取最严）
# ② 信号翻空：qpzm 标的 action 翻「回避」→ 立即平（九维无 action 走 ③）
# ③ 离池：跌出选股池连续 out_max 个交易日 → 平（最后已知价成交）
# ④ 持有到期：hold_days ≥ hold_max（交易日，快照日期推进，假期不空转）
def _qpzm_ctx():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    pxm, act = {}, {}
    for r in snap.get("res") or []:
        if r.get("code") and r.get("px"):
            pxm[r["code"]] = float(r["px"])
            act[r["code"]] = r.get("action") or ""
    return {
        "snap_date": str(snap.get("latest") or ""),
        "px_map": pxm,
        "action_map": act,
    }


def _9dim_ctx():
    snap = _load_json(os.path.join(DATA, "nine_dim_scores.json"))
    pxm = {
        r.get("code"): float(r["close"])
        for r in snap.get("rows") or []
        if r.get("code") and r.get("close")
    }
    # 判例：九维快照交易日字段是 trade_date（非 date，date 键不存在）
    return {
        "snap_date": str(
            snap.get("trade_date") or (snap.get("generated_at") or "")[:10]
        ),
        "px_map": pxm,
        "action_map": {},
    }


def _sig4_ctx():
    """positions.json：ts「2026-10-02 16:00」→「20261002」。
    alive=源 open ∪ pending：源「待买中」也算存活（否则按 pending 建的仓
    在源引擎未建仓时会被误判「源已平」）；源彻底放弃=从两池消失（K49）。"""
    snap = _load_json(os.path.join(HERE, "positions.json"))
    pxm = {}
    for sec in ("pending", "open", "closed"):
        for r in snap.get(sec) or []:
            if r.get("symbol") and r.get("last_px"):
                pxm[r["symbol"]] = float(r["last_px"])
    alive = {
        r.get("symbol")
        for sec in ("open", "pending")
        for r in snap.get(sec) or []
        if r.get("symbol")
    }
    return {
        "snap_date": str(snap.get("ts") or "")[:10].replace("-", ""),
        "px_map": pxm,
        "action_map": {},
        "alive": alive,
    }


def _chanlun_ctx():
    """chanlun_paper：updated_at ISO →「20261002」。alive 同 sig4 跟随源口径
    （源 open 仍持有该 code 任意类型仓即算存活——源体系自己会按卖点离场）。"""
    snap = _load_json(os.path.join(DATA, "chanlun_paper.json"))
    pxm, alive = {}, set()
    for p in snap.get("open") or []:
        c = p.get("code")
        if not c:
            continue
        if p.get("last_px"):
            pxm[c] = float(p["last_px"])
        alive.add(c)
    return {
        "snap_date": str(snap.get("updated_at") or "")[:10].replace("-", ""),
        "px_map": pxm,
        "action_map": {},
        "alive": alive,
    }


CTXS = {
    "qpzm": _qpzm_ctx,
    "9dim": _9dim_ctx,
    "sig4": _sig4_ctx,
    "chanlun": _chanlun_ctx,
}


def _envs_safe():
    """rps_latest envs → {market: safe}（四市场环境闸门）。
    S6 三态: True/False=闸门结论；None=数据缺失（缺维降权，
    不触发一票否决、不累计闸门坏日）。"""
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for e in snap.get("envs") or []:
        lb = e.get("label") or ""
        mkt = (
            "CN"
            if lb.startswith("A股")
            else "US"
            if lb.startswith("美股")
            else "HK"
            if lb.startswith("港股")
            else "CRYPTO"
            if lb.startswith("加密")
            else None
        )
        if mkt:
            sv = e.get("safe")
            out[mkt] = None if sv is None else bool(sv)  # S6: None 透传
    return out


def _rps_score_map():
    """rps_latest res[] → {code: score}（RPS_mix 当日快照，骤降检测源）。"""
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for r in snap.get("res") or []:
        c, s = r.get("code"), r.get("score")
        if c and isinstance(s, (int, float)):
            out[c] = float(s)
    return out


def _risk_exit_of(p, pxm, rps_map):
    """K57 单仓价格保护三规则：返回 (reason, rebuild) 或 None。
    优先级从高到低（同日多触发取最严，上游只出一条退出记录）：
    ① 初始止损 px ≤ entry×(1-STOP_PCT)：方法论原口径为「信号K低点-2%」，
       虚拟账户无信号K数据 → 以 entry-2% 工程近似（口径差异如实声明）；
       价格保护触发=信号已坏 → rebuild=False 不重建
    ② 移动止盈 px 自 hi_px 回撤 ≥TRAIL_PCT：hi_px=持仓期最高收盘
      （跑批 step3 每日刷新）；老仓缺 hi_px 字段时用 max(entry, 现价)
       兜底初始化（无历史即无可度量回撤，下行由①初始止损兜底）；rebuild=False
    ③ rps骤降 RPS_mix 单日降 ≥RPS_DROP：标的自身信号恶化预警（环境预警
       细化），沿用环境闸门 rebuild=True 语义（环境恢复可重建）；
       副作用：res[] 有该标的分值时，无论是否触发都刷新 p["last_rps"]
       作为次日对比基线（老仓缺字段当日只初始化不触发，防上线首日误杀）。"""
    px_now = pxm.get(p["code"])
    if px_now:
        hi = p.get("hi_px") or max(p["entry_px"], px_now)  # 老仓回填兼容
        if px_now <= p["entry_px"] * (1 - STOP_PCT):
            return "初始止损(-%g%%)" % (STOP_PCT * 100), False
        if px_now <= hi * (1 - TRAIL_PCT):
            return "移动止盈(回撤%g%%)" % (TRAIL_PCT * 100), False
    cur = rps_map.get(p["code"])
    if cur is not None:
        prev = p.get("last_rps")
        p["last_rps"] = cur  # 每日刷新对比基线（缺失即初始化）
        if prev is not None and prev - cur >= RPS_DROP:
            return "rps骤降", True  # 环境预警类：随闸门语义，可重建
    return None


def _close(ac, p, px, today, reason, exits, rebuild=False):
    px = px or p["last_px"]
    proceeds = round(p["qty"] * px, 2)
    pnl = round(proceeds - p["cost"], 2)
    ac["cash"] = round(ac["cash"] + proceeds, 2)
    ac["open"].remove(p)
    rec = dict(p)
    rec.update(
        {
            "exit_px": px,
            "exit_date": today,
            "exit_pnl": pnl,
            "exit_pnl_pct": round((px - p["entry_px"]) / p["entry_px"] * 100, 2),
            "reason": reason,
        }
    )
    if rebuild:
        rec["rebuild"] = True  # 闸门避险/持有到期：环境或信号恢复后允许重建
    ac["closed"].append(rec)
    ac["closed"] = ac["closed"][-500:]
    exits.append("%s %s %+.2f%%(%s)" % (p["name"], reason, rec["exit_pnl_pct"], today))


# ── K54-A1 份额净值口径 ────────────────────────────────
def _curve_ps(c):
    """曲线点 ps 兜底：新点带 ps 字段；老点（份额法上线前）按 ret 换算。"""
    return c.get("ps") if c.get("ps") else 1.0 + (c.get("ret") or 0) / 100.0


def _push_curve(ac, today, nav):
    """份额法写曲线：ps = nav / shares（申赎只动 shares 不动 ps）。
    ret 改为份额累计收益 (ps-1)*100——capital 未变时与旧口径数值连续，
    且资金调整当日不再跳变（注资=申购份额，不虚增收益）。
    老账本无 shares 缺省 = capital（向后兼容，无迁移成本）。"""
    sh = ac.get("shares") or ac.get("capital") or 1_000_000
    ps = nav / sh if sh > 0 else 1.0
    curve = [c for c in ac["nav_curve"] if c["d"] != today]
    curve.append(
        {"d": today, "nav": nav, "ps": round(ps, 4), "ret": round((ps - 1) * 100, 3)}
    )
    ac["nav_curve"] = curve[-400:]
    return curve[-1]


# ── K54-B1 ATR 波动率 ─────────────────────────────────
ATR_TABLES = {
    "CN": "ts_daily",
    "CRYPTO": "crypto_daily",
    "US": "us_daily",
    "HK": "hk_daily",
}


def _atr_of(market, code):
    """ATR14（datamart 近 16 根日K H/L/C，SMA 平滑）。任何失败返回
    None → 建仓回退等权（数据缺失不阻断跑批）。"""
    try:
        import datamart as dm

        df = dm.read(
            ATR_TABLES.get(market, ""),
            codes=[code],
            fields=["high", "low", "close"],
            limit=40,
        )
        if df is None or df.empty or len(df) < 15:
            return None
        dcol = next((k for k in ("trade_date", "date") if k in df.columns), None)
        if dcol:
            df = df.sort_values(dcol)
        rows = df.tail(16).to_dict("records")
        trs, prev_c = [], None
        for r in rows:
            h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
            trs.append(
                max(h - l, abs(h - prev_c), abs(l - prev_c))
                if prev_c is not None
                else h - l
            )
            prev_c = c
        return sum(trs[-14:]) / 14.0
    except Exception:
        return None


# ── K54-B4 多级回撤墙 ─────────────────────────────────
def _dd_level(dd, warn, fuse, cut):
    """回撤分级：0 正常 / 1 警诫(黄标) / 2 熔断停建 / 3 深回撤(红标+会诊)。
    熔断沿用 K51-C 阈值（max_drawdown_pct）；warn/cut 为 K54 新增上下沿。"""
    if cut > 0 and dd >= cut:
        return 3
    if fuse > 0 and dd >= fuse:
        return 2
    if warn > 0 and dd >= warn:
        return 1
    return 0


# ── K54-B4 BAC 月度资金再平衡提案 ──────────────────────
def _bac_proposal(led, today):
    """按近 30 日份额收益 Sharpe 排序：最优账户建议注资、最弱建议减资。
    只出提案（存 led["bac"]），人工 --set capital 确认——LLM/机器不碰资金。"""
    try:
        import perf_metrics as pm

        rows = []
        for sid, ac in (led.get("accounts") or {}).items():
            curve = [
                {"d": c["d"], "nav": _curve_ps(c)} for c in (ac.get("nav_curve") or [])
            ][-30:]
            if len(curve) < 5:
                continue
            m = pm.compute(curve)
            if m["sharpe"] is None:
                continue
            rows.append(
                {
                    "sid": sid,
                    "name": ac.get("name"),
                    "ret": m["total_ret"],
                    "sharpe": m["sharpe"],
                }
            )
        if len(rows) < 2:
            return None
        rows.sort(key=lambda r: r["sharpe"], reverse=True)
        top, bot = rows[0], rows[-1]
        return {
            "d": today,
            "top": "%s(SR %.2f, 30日%+.2f%%)"
            % (top["name"], top["sharpe"], top["ret"]),
            "bot": "%s(SR %.2f, 30日%+.2f%%)"
            % (bot["name"], bot["sharpe"], bot["ret"]),
            "hint": (
                "月度 BAC 提案：建议对 %s 注资 10%%、%s 减资 10%%。"
                "人工确认：python portfolio.py --set %s.capital=N / %s.capital=N"
                % (top["name"], bot["name"], top["sid"], bot["sid"])
            ),
        }
    except Exception:
        return None


def _run_exits(led, ac, sid, today, cfg_all):
    """按 闸门→翻空→止盈止损→离池→到期 顺序判定平仓，返回 (exits, ctx)。"""
    exits = []
    ctx = CTXS[sid]()
    pxm, act = ctx["px_map"], ctx["action_map"]
    safe = _envs_safe()
    rps_map = _rps_score_map()  # K57 RPS_mix 快照（每账户跑批只读一次盘）
    gate_bad_max = int(cfg_all.get("gate_bad_max", 5))
    out_max = int(cfg_all.get("out_max", 3))
    hold_max = int(cfg_all.get("hold_max", 20))
    # K50：价格维度纪律（账户级优先/全局回退；相对成本价）
    ccfg = (cfg_all.get("accounts") or {}).get(sid) or {}
    sl = float(ccfg.get("stop_loss_pct", cfg_all.get("stop_loss_pct", 8)))
    tp = float(ccfg.get("take_profit_pct", cfg_all.get("take_profit_pct", 15)))

    # 交易日推进：源快照日期变化才算新交易日（假期跑批不空转）
    new_day = bool(ctx["snap_date"]) and ac.get("last_snap") != ctx["snap_date"]
    if new_day:
        ac["last_snap"] = ctx["snap_date"]
        for p in ac["open"]:
            p["hold_days"] = p.get("hold_days", 0) + 1
            p["out_days"] = 0 if p["code"] in pxm else p.get("out_days", 0) + 1

    # 环境闸门计数（全局按市场；K49 修复：多账户同跑批只计一次——
    # led 级 gate_days 共享而 new_day 是账户级判定，若不去重，
    # 四账户同日各 +1 会虚增闸门提前触发清仓）
    if new_day and led.get("gate_mark") != today:
        led["gate_mark"] = today
        gates = led.setdefault("gate_days", {})
        for mkt, ok in safe.items():
            if ok is None:  # S6: 数据缺失→不计坏日也不清零
                continue  # （闸门状态未知，既不推进也不赦免）
            gates[mkt] = 0 if ok else gates.get(mkt, 0) + 1
    gates = led.get("gate_days") or {}

    closed_mkt = set()
    for p in list(ac["open"]):
        mkt = p.get("market")
        # ① 环境闸门避险：该市场闸门连续收紧达阈值 → 清仓（环境恢复可重建）
        if mkt in closed_mkt or gates.get(mkt, 0) >= gate_bad_max:
            closed_mkt.add(mkt)
            _close(
                ac,
                p,
                pxm.get(p["code"]),
                today,
                "环境闸门x%d" % gates.get(mkt, 0),
                exits,
                rebuild=True,
            )
            continue
        # ①.5 价格保护三规则（K57）：初始止损 > 移动止盈 > rps骤降，
        #     同日多触发取最严一条；命中即短路 ②信号翻空/K50价格纪律/离池/到期
        hit = _risk_exit_of(p, pxm, rps_map)
        if hit:
            _close(ac, p, pxm.get(p["code"]), today, hit[0], exits, rebuild=hit[1])
            continue
        # ② 信号翻空：action 翻回避 → 立即平（仅 qpzm 源，信号已坏不重建）
        if "回避" in act.get(p["code"], ""):
            _close(ac, p, pxm.get(p["code"]), today, "信号翻空", exits)
            continue
        # ②.5 止盈止损（K50 价格维度纪律）：源快照有价才判定；
        #     止损/止盈是价格纪律离场而非信号转坏 → rebuild=True 允许重建
        #     K54-B2 保本止损插在最前：浮盈峰值达 be_trigger 后激活，
        #     跌破成本+be_floor 即离场（锁住 "曾到手的浮盈"，让利润不回吐成本）
        px_now = pxm.get(p["code"])
        if px_now:
            be_t = float(ccfg.get("be_trigger_pct", cfg_all.get("be_trigger_pct", 8)))
            be_f = float(ccfg.get("be_floor_pct", cfg_all.get("be_floor_pct", 0.5)))
            if be_t > 0:
                hi = p.get("hi_px") or p["entry_px"]
                if (hi / p["entry_px"] - 1) * 100 >= be_t and px_now <= p[
                    "entry_px"
                ] * (1 + be_f / 100):
                    _close(
                        ac,
                        p,
                        px_now,
                        today,
                        "保本止损(峰%+.1f%%)" % ((hi / p["entry_px"] - 1) * 100),
                        exits,
                        rebuild=True,
                    )
                    continue
            if sl > 0:
                chg = (px_now - p["entry_px"]) / p["entry_px"] * 100
                if chg <= -sl:
                    _close(
                        ac, p, px_now, today, "止损%.1f%%" % chg, exits, rebuild=True
                    )
                    continue
                if tp > 0 and chg >= tp:
                    _close(
                        ac, p, px_now, today, "止盈%.1f%%" % chg, exits, rebuild=True
                    )
                    continue
        # ③ 离池 / 跟随源（两种口径二选一，由 ctx 决定）：
        #    qpzm/9dim：连续 out_max 日跌出选股池 → 平（最后已知价，不重建）
        #    sig4/chanlun：源账本已平该标的 → 平（跟随策略离场，不重建）。
        #      源 open 仍有仓说明策略体系继续持有，盈亏与源同步自然表达
        alive = ctx.get("alive")
        if alive is not None:
            if p["code"] not in alive:
                # rebuild=True：源体系日后重新出信号时允许跟随重建
                # （同跑批不会立即回建——源平仓意味着已不在本快照 picks 里）
                _close(
                    ac, p, pxm.get(p["code"]), today, "源账本已平", exits, rebuild=True
                )
                continue
        elif p.get("out_days", 0) >= out_max:
            _close(ac, p, None, today, "离池%d日" % p["out_days"], exits)
            continue
        # ④ 持有到期：信号未坏，到期止盈止损离场（可开新一轮）
        if p.get("hold_days", 0) >= hold_max:
            _close(
                ac,
                p,
                pxm.get(p["code"]),
                today,
                "持有%d日" % p["hold_days"],
                exits,
                rebuild=True,
            )
    return exits, ctx


# ── 每日跑批 ────────────────────────────────────────────
def daily_run(notify=False):
    led, cfg_all = load_ledger()
    today = _today()
    summary = []
    for tpl in SUB_ACCOUNTS:
        sid = tpl["id"]
        ac = led["accounts"][sid]
        cc = (cfg_all.get("accounts") or {}).get(sid) or {}
        enabled = cc.get("enabled", True)
        capital = int(cc.get("capital", tpl["capital"]))  # 资金可设定（50万/100万…）
        max_pos = int(cc.get("max_pos", tpl["max_pos"]))
        # start_date 闸门：未到开始模拟日 → 只刷新已有持仓，不建新仓
        # （K45：账户级 start_date 优先，回退全局——每策略卡片可独立设定建仓时间）
        start_gate = cc.get("start_date") or cfg_all.get("start_date") or ""
        can_open = (not start_gate) or today >= start_gate

        # 0) 资金变更平滑生效：差额注资/撤资（不动持仓，adjust_log 留痕）
        #    K49 修复：撤资超现金（如资金上调后当日已补建新仓、随后又下调）
        #    → 按建仓时间倒序平最新仓筹资（真实交易语义），现金不再为负
        #    K54-A1 申赎：份额按当日净值 ps0 变动（注资=申购/撤资=赎回），
        #    ps 不动 → 曲线平滑，资金调整不再污染收益口径
        delta = capital - ac.get("capital", 0)
        if delta:
            sh0 = ac.get("shares") or ac.get("capital") or capital
            nav0 = ac.get("cash", capital) + sum(
                p["qty"] * p["last_px"] for p in ac["open"]
            )
            ps0 = nav0 / sh0 if sh0 > 0 and nav0 > 0 else 1.0
            ac["capital"] = capital
            if delta < 0 and ac.get("cash", 0) + delta < 0:
                need = -(ac.get("cash", 0) + delta)
                fund_log = []
                # 判例：sorted(reverse=True) 对同 entry_date 是稳定排序（保原序）
                # LIFO 会失效平错仓；建仓序=append 序 → 直接 reversed 才是真 LIFO
                for p in list(reversed(ac["open"])):
                    if need <= 0:
                        break
                    _close(ac, p, p["last_px"], today, "撤资筹资", fund_log)
                    need -= round(p["qty"] * p["last_px"], 2)
                for e in fund_log:
                    print("EXIT %s[%s] %s" % (tpl["icon"], sid, e))
            ac["cash"] = round(ac.get("cash", capital) + delta, 2)
            ac["shares"] = round(max(sh0 + delta / ps0, 1.0), 2)
            ac.setdefault("adjust_log", []).append(
                {
                    "d": today,
                    "delta": delta,
                    "ps0": round(ps0, 4),
                    "shares": ac["shares"],
                    "note": "注资" if delta > 0 else "撤资",
                }
            )
            ac["adjust_log"] = ac["adjust_log"][-50:]

        picks, src_ts = PICKERS[sid]()
        budget = capital // max_pos  # 等权预算（B1 限幅基准）

        # 1) 平仓先行（K44-C 三规则：闸门/翻空/离池/到期），回收现金再建仓
        exits, ctx = _run_exits(led, ac, sid, today, cfg_all)
        for e in exits:
            print("EXIT %s[%s] %s" % (tpl["icon"], sid, e))

        # 2) 建仓（幂等：同账户同 code 已持仓/已平仓过的不重复建；
        #    rebuild=True 的平仓记录（闸门避险/持有到期）不挡重建；
        #    市场闸门收紧达阈值 → 该市场停建新仓=择时的建仓侧闸门）
        opened = 0
        if enabled and can_open:
            gate_bad_max = int(cfg_all.get("gate_bad_max", 5))
            gates_now = led.get("gate_days") or {}
            gate_mkt = {m for m, n in gates_now.items() if n >= gate_bad_max}
            # K51-C 回撤熔断 → K54-B4 升级三级回撤墙：
            #   L1 警戒(黄标) / L2 熔断停建(原 K51 口径) / L3 深回撤(红标+会诊)
            mdd = float(cc.get("max_drawdown_pct", cfg_all.get("max_drawdown_pct", 10)))
            dd_warn = float(cc.get("dd_warn_pct", cfg_all.get("dd_warn_pct", 5)))
            dd_cut = float(cc.get("dd_cut_pct", cfg_all.get("dd_cut_pct", 15)))
            nav_now = ac["cash"] + sum(p["qty"] * p["last_px"] for p in ac["open"])
            peak = max(
                [c["nav"] for c in ac.get("nav_curve") or []]
                + [ac.get("capital", 0), nav_now]
            )
            dd = (peak - nav_now) / peak * 100 if peak > 0 else 0.0
            fused = mdd > 0 and dd >= mdd
            dd_lv = _dd_level(dd, dd_warn, mdd, dd_cut)
            if dd_lv == 3:  # 深回撤：减仓会诊清单（只列弱仓，不自动减）
                ac["dd_review"] = {
                    "d": today,
                    "dd": round(dd, 2),
                    "worst": [
                        {
                            "code": p["code"],
                            "name": p["name"],
                            "pnl_pct": p.get("pnl_pct", 0),
                            "hold": p.get("hold_days", 0),
                        }
                        for p in sorted(ac["open"], key=lambda x: x.get("pnl_pct", 0))[
                            :5
                        ]
                    ],
                }
            # K54-B1/B3 sizing 参数 + 四账户总净值（跨账户上限基数）
            sizing = str(cfg_all.get("sizing", "atr")).lower()
            risk_pct = float(cfg_all.get("risk_pct_per_trade", 1.0))
            atr_mult = float(cfg_all.get("atr_stop_mult", 2.0))
            band = min(max(float(cfg_all.get("sizing_band", 0.5)), 0.1), 0.9)
            cap1 = float(
                cc.get("single_pos_cap_pct", cfg_all.get("single_pos_cap_pct", 10))
            )
            capx = float(
                cc.get("cross_acct_cap_pct", cfg_all.get("cross_acct_cap_pct", 15))
            )
            total_nav = sum(
                (o.get("cash") or 0)
                + sum(p["qty"] * p["last_px"] for p in o.get("open") or [])
                for o in led["accounts"].values()
            )
            held = {p["code"] for p in ac["open"]}
            done = {c["code"] for c in ac["closed"] if not c.get("rebuild")}
            for market, code, name, px, note in picks:
                if not code or code in held or code in done:
                    continue
                if fused:
                    ac["skip_log"].append(
                        {"d": today, "code": code, "why": "回撤熔断%.1f%%" % dd}
                    )
                    ac["skip_log"] = ac["skip_log"][-200:]
                    break
                if market in gate_mkt:
                    ac["skip_log"].append(
                        {"d": today, "code": code, "why": "市场闸门收紧"}
                    )
                    ac["skip_log"] = ac["skip_log"][-200:]
                    continue
                # K54-B1 波动率目标 sizing：仓位 = 净值×风险预算 ÷ 止损距离；
                #   止损距离 = max(ATR×N/px, 4%)，限幅 [等权×band, 等权×(2-band)]；
                #   ATR 缺数回退等权（数据缺失不阻断跑批）
                amt = float(budget)
                if sizing == "atr":
                    atr = _atr_of(market, code)
                    if atr:
                        stop_dist = max(atr * atr_mult / px, 0.04)
                        risk_amt = nav_now * risk_pct / 100.0
                        amt = min(
                            max(risk_amt / stop_dist, budget * band),
                            budget * (2 - band),
                        )
                # K54-B3 组合约束（只约束自动建仓；手动建仓用户意志优先）
                if cap1 > 0:
                    amt = min(amt, nav_now * cap1 / 100.0)
                if capx > 0:
                    others = sum(
                        p["qty"] * p["last_px"]
                        for o_sid, o in led["accounts"].items()
                        if o_sid != sid
                        for p in o.get("open") or []
                        if p["code"] == code
                    )
                    room = total_nav * capx / 100.0 - others
                    if amt > room:
                        amt = room
                if amt < 1000:
                    ac["skip_log"].append(
                        {"d": today, "code": code, "why": "B3约束预算不足%.0f" % amt}
                    )
                    ac["skip_log"] = ac["skip_log"][-200:]
                    continue
                if ac["cash"] < amt:
                    ac["skip_log"].append(
                        {
                            "d": today,
                            "code": code,
                            "why": "现金不足 %d/%d" % (len(ac["open"]), max_pos),
                        }
                    )
                    ac["skip_log"] = ac["skip_log"][-200:]
                    break
                qty = round(amt / px, 4)
                ac["open"].append(
                    {
                        "market": market,
                        "code": code,
                        "name": name,
                        "qty": qty,
                        "entry_px": px,
                        "entry_date": today,
                        "cost": round(amt, 2),
                        "last_px": px,
                        "last_date": today,
                        "note": note,
                        "hi_px": px,  # K54-B2 浮盈峰值（保本止损用）
                        "hold_days": 0,
                        "out_days": 0,
                    }
                )
                ac["cash"] = round(ac["cash"] - amt, 2)
                held.add(code)
                opened += 1

        # 3) 刷新持仓最新价/浮盈/浮盈峰值（复用平仓引擎已读的源快照）
        pxm = ctx["px_map"]
        mkt_val = 0.0
        for p in ac["open"]:
            px = pxm.get(p["code"])
            if px:
                p["last_px"], p["last_date"] = float(px), today
            p["hi_px"] = round(
                max(p.get("hi_px") or p["entry_px"], p["last_px"]), 6
            )  # K54-B2 峰值追踪
            mkt_val += p["qty"] * p["last_px"]
            p["pnl"] = round(p["qty"] * p["last_px"] - p["cost"], 2)
            p["pnl_pct"] = round(
                (p["last_px"] - p["entry_px"]) / p["entry_px"] * 100, 2
            )

        # 4) 净值曲线（K54-A1 份额法：ps=nav/shares，申赎只动份额不动 ps）
        nav = round(ac["cash"] + mkt_val, 2)
        last_pt = _push_curve(ac, today, nav)
        gate = (
            ""
            if (enabled and can_open)
            else (" [停用]" if not enabled else " [未到起始日 %s]" % start_gate)
        )
        if enabled and can_open and dd_lv >= 1:
            gate = {1: " [🟡回撤%.1f%%]", 2: " [🔒熔断%.1f%%]", 3: " [🔴深回撤%.1f%%]"}[
                dd_lv
            ] % dd
        summary.append(
            "%s[%s] pick=%d open=%d(+%d) closed=%d(-%d) "
            "cash=%.0f nav=%.0f(ps %.4f,%+.2f%%)%s"
            % (
                tpl["icon"],
                sid,
                len(picks),
                len(ac["open"]),
                opened,
                len(ac["closed"]),
                len(exits),
                ac["cash"],
                nav,
                last_pt["ps"],
                last_pt["ret"],
                gate,
            )
        )
    # K54-B4 BAC 月度资金再平衡提案：每月 1-5 日跑批生成一次（只提案，
    # 存 led["bac"] 供战报/大屏展示，资金动作人工 --set 确认）
    if today[8:10] <= "05" and led.get("bac_mark") != today[:7]:
        prop = _bac_proposal(led, today)
        if prop:
            led["bac_mark"] = today[:7]
            led["bac"] = prop
            print("BAC %s" % prop["hint"])
    led["ts"] = _now()
    _atomic(LEDGER, led)
    for s in summary:
        print("PORTFOLIO " + s)
    if notify and cfg_all.get("notify", True):
        try:
            daily_report(led, cfg_all)
        except Exception as e:  # 推送失败不阻断跑批
            print("REPORT-ERR %s" % e)
    return led


def close_manual(sid, code=None):
    """K50-D 手动平仓：code=None 清仓整个账户。按源快照最新价成交。"""
    led, cfg_all = load_ledger()
    ac = (led.get("accounts") or {}).get(sid)
    if not ac:
        print("账户不存在：%s" % sid)
        return
    ctx = CTXS[sid]()
    today = _today()
    targets = [p for p in ac["open"] if code in (None, p["code"])]
    if not targets:
        print("无可平持仓：%s %s" % (sid, code or "(全部)"))
        return
    log = []
    for p in targets:
        _close(ac, p, ctx["px_map"].get(p["code"]), today, "手动平仓", log)
    nav = round(ac["cash"] + sum(p["qty"] * p["last_px"] for p in ac["open"]), 2)
    _push_curve(ac, today, nav)  # K54-A1 份额法
    led["ts"] = _now()
    _atomic(LEDGER, led)
    for e in log:
        print("EXIT 手动[%s] %s" % (sid, e))


def adjust_position(sid, code, act, pct):
    """K51-A 手动加/减仓：act=add 按现价加仓（规模=持仓市值×pct%），
    act=trim 卖出持仓数量 pct%。部分减仓只记 trades 流水（不动 closed，
    保住胜率统计口径）；减仓 pct≥100 视为全平（走 _close，reason=手动减仓）。"""
    led, cfg_all = load_ledger()
    ac = (led.get("accounts") or {}).get(sid)
    if not ac:
        print("账户不存在：%s" % sid)
        return
    p = next((x for x in ac["open"] if x["code"] == code), None)
    if not p:
        print("无可调持仓：%s %s" % (sid, code))
        return
    pct = max(1.0, min(float(pct), 100.0))
    ctx = CTXS[sid]()
    px = ctx["px_map"].get(code) or p["last_px"]
    today = _today()
    mkt_val = round(p["qty"] * px, 2)
    if act == "add":
        amount = round(mkt_val * pct / 100, 2)
        if ac["cash"] < amount:
            print("现金不足：需 %.0f 现金 %.0f" % (amount, ac["cash"]))
            return
        old_cost = p["cost"]
        p["qty"] = round(p["qty"] + amount / px, 4)
        p["cost"] = round(old_cost + amount, 2)
        p["entry_px"] = round(p["cost"] / p["qty"], 6)  # 加权平均成本
        ac["cash"] = round(ac["cash"] - amount, 2)
        ac.setdefault("trades", []).append(
            {
                "d": today,
                "code": code,
                "name": p["name"],
                "act": "加仓",
                "pct": pct,
                "px": px,
                "amount": amount,
            }
        )
        print(
            "ADJUST %s[%s] %s 加仓%.0f%% px=%.2f amount=%.0f"
            % (
                next(t["icon"] for t in SUB_ACCOUNTS if t["id"] == sid),
                sid,
                p["name"],
                pct,
                px,
                amount,
            )
        )
    else:  # act == "trim"
        sell_qty = round(p["qty"] * pct / 100, 4)
        if sell_qty >= p["qty"] or pct >= 100:
            log = []
            _close(ac, p, px, today, "手动减仓", log)
            for e in log:
                print("EXIT 手动减仓[%s] %s" % (sid, e))
        else:
            proceeds = round(sell_qty * px, 2)
            keep = 1 - pct / 100
            p["qty"] = round(p["qty"] - sell_qty, 4)
            p["cost"] = round(p["cost"] * keep, 2)  # 成本按比例结转
            ac["cash"] = round(ac["cash"] + proceeds, 2)
            ac.setdefault("trades", []).append(
                {
                    "d": today,
                    "code": code,
                    "name": p["name"],
                    "act": "减仓",
                    "pct": pct,
                    "px": px,
                    "amount": proceeds,
                }
            )
            print(
                "ADJUST %s[%s] %s 减仓%.0f%% px=%.2f 回笼=%.0f"
                % (
                    next(t["icon"] for t in SUB_ACCOUNTS if t["id"] == sid),
                    sid,
                    p["name"],
                    pct,
                    px,
                    proceeds,
                )
            )
    ac["trades"] = ac.get("trades", [])[-500:]
    nav = round(ac["cash"] + sum(x["qty"] * x["last_px"] for x in ac["open"]), 2)
    _push_curve(ac, today, nav)  # K54-A1 份额法
    led["ts"] = _now()
    _atomic(LEDGER, led)


def _market_all():
    """K51-D 手动建仓寻价：合并四账户 PICKERS+px_map（跨策略口径），
    返回 {code: (market, name, px)}——picks 带 name/market 优先，px_map 兜底。"""
    table = {}
    for sid in CTXS:
        try:
            picks, _ = PICKERS[sid]()  # 返回 (picks, src_ts) 二元组
            for market, code, name, px, _n in picks:
                if code and px:
                    table.setdefault(code, (market, name, float(px)))
            ctx = CTXS[sid]()
            for code, px in (ctx.get("px_map") or {}).items():
                if code and px:
                    table.setdefault(code, ("", "", float(px)))
        except Exception:
            continue
    return table


def _infer_mkt(code):
    """code 后缀推断市场（px_map 兜底路径无 market 时用）。"""
    if code.endswith((".SS", ".SZ")):
        return "CN"
    if code.endswith("-USD"):
        return "CRYPTO"
    if ".HK" in code or code[:1].isdigit():
        return "HK"
    return ""


def market_search(q):
    """K52-D 标的搜索：_market_all 全表按 code/name 过滤（全量返回，
    截断由调用方做——API 端拉一次全表缓存后内存过滤）。"""
    q = (q or "").strip().lower()
    table = _market_all()
    hits = [
        {"code": c, "market": _infer_mkt(c) or v[0], "name": v[1], "px": v[2]}
        for c, v in table.items()
        if not q or q in c.lower() or q in (v[1] or "").lower()
    ]
    hits.sort(key=lambda x: x["code"])
    return hits


def manual_open(sid, code, amount=None):
    """K51-D 手动建仓：标的按跨策略快照最新价成交入仓。用户意志优先——
    不受闸门/熔断/起始日/done 拉黑限制（手动建仓即解除该标的冷宫），
    仅守现金与 max_pos；amount 默认等权口径 capital//max_pos。"""
    led, cfg_all = load_ledger()
    ac = (led.get("accounts") or {}).get(sid)
    if not ac:
        print("账户不存在：%s" % sid)
        return
    if any(p["code"] == code for p in ac["open"]):
        print("已持仓 %s（如需增持请用加仓）" % code)
        return
    cc = (cfg_all.get("accounts") or {}).get(sid) or {}
    tpl = next(t for t in SUB_ACCOUNTS if t["id"] == sid)
    max_pos = int(cc.get("max_pos", tpl["max_pos"]))
    if len(ac["open"]) >= max_pos:
        print("持仓已满 %d/%d（可先调大持仓上限）" % (len(ac["open"]), max_pos))
        return
    hit = _market_all().get(code)
    if not hit:
        print("找不到价格：%s 不在任何策略源快照中" % code)
        return
    market, name, px = hit
    market = market or _infer_mkt(code)
    name = name or code
    capital = int(cc.get("capital", tpl["capital"]))
    amount = round(float(amount), 2) if amount else capital // max_pos
    if ac["cash"] < amount:
        print("现金不足：需 %.0f 现金 %.0f" % (amount, ac["cash"]))
        return
    today = _today()
    ac["open"].append(
        {
            "market": market,
            "code": code,
            "name": name,
            "qty": round(amount / px, 4),
            "entry_px": px,
            "entry_date": today,
            "cost": amount,
            "last_px": px,
            "last_date": today,
            "note": "手动建仓",
            "hi_px": px,
            "hold_days": 0,
            "out_days": 0,
        }
    )
    ac["cash"] = round(ac["cash"] - amount, 2)
    ac.setdefault("trades", []).append(
        {
            "d": today,
            "code": code,
            "name": name,
            "act": "建仓",
            "pct": 0,
            "px": px,
            "amount": amount,
        }
    )
    ac["trades"] = ac["trades"][-500:]
    nav = round(ac["cash"] + sum(x["qty"] * x["last_px"] for x in ac["open"]), 2)
    _push_curve(ac, today, nav)  # K54-A1 份额法
    led["ts"] = _now()
    _atomic(LEDGER, led)
    print("OPEN 手动[%s] %s %s px=%.2f amount=%.0f" % (sid, market, name, px, amount))


def daily_report(led, cfg_all=None):
    """K50-B 飞书每日战报：四账户净值/当日动作/胜率摘要卡。
    K52-A 扩展：🔒熔断标记（重算，与跑批同口径）+ 调仓计数（当日 trades）。"""
    if cfg_all is None:
        cfg_all = load_ledger()[1]
    rows = []
    for tpl in SUB_ACCOUNTS:
        ac = (led.get("accounts") or {}).get(tpl["id"]) or {}
        cc = (cfg_all.get("accounts") or {}).get(tpl["id"]) or {}
        curve = ac.get("nav_curve") or []
        ret = curve[-1]["ret"] if curve else 0.0
        prev = curve[-2]["ret"] if len(curve) > 1 else 0.0
        closed = ac.get("closed") or []
        wins = len([c for c in closed if (c.get("exit_pnl_pct") or 0) > 0])
        wr = ("%.0f%%" % (wins * 100 / len(closed))) if closed else "—"
        # 当日动作：entry_date/exit_date 为今天的仓
        today = _today()
        n_in = len([p for p in ac.get("open") or [] if p.get("entry_date") == today])
        outs = [c for c in closed if c.get("exit_date") == today]
        out_s = (
            "、".join(
                "%s%s%+.1f%%"
                % (c["name"], c.get("reason", ""), c.get("exit_pnl_pct") or 0)
                for c in outs[:4]
            )
            or "—"
        )
        # K52 熔断重算（与 daily_run step2 同口径）→ K54-B4 三级回撤墙
        nav_now = (ac.get("cash") or 0) + sum(
            p["qty"] * (p.get("last_px") or p.get("entry_px") or 0)
            for p in (ac.get("open") or [])
        )
        peak = max([c["nav"] for c in curve] + [ac.get("capital") or 0, nav_now])
        mdd = float(cc.get("max_drawdown_pct", cfg_all.get("max_drawdown_pct", 10)))
        dd = (peak - nav_now) / peak * 100 if peak > 0 else 0.0
        lv = _dd_level(
            dd,
            float(cc.get("dd_warn_pct", cfg_all.get("dd_warn_pct", 5))),
            mdd,
            float(cc.get("dd_cut_pct", cfg_all.get("dd_cut_pct", 15))),
        )
        n_adj = len(
            [
                t
                for t in (ac.get("trades") or [])
                if t.get("d") == today and t.get("act") in ("加仓", "减仓")
            ]
        )
        # K54-A2 机构指标（份额收益口径：ps 序列，申赎不污染曲线）
        m = {"sharpe": None, "max_dd_pct": None}
        try:
            import perf_metrics as pm

            if len(curve) >= 2:
                m = pm.compute([{"d": c["d"], "nav": _curve_ps(c)} for c in curve])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio.py", exc)
        rows.append(
            {
                "icon": tpl["icon"],
                "name": tpl["name"],
                "nav": curve[-1]["nav"] if curve else ac.get("capital", 0),
                "ps": _curve_ps(curve[-1]) if curve else 1.0,
                "ret": ret,
                "day": round(ret - prev, 2),
                "sharpe": m["sharpe"],
                "maxdd": m["max_dd_pct"],
                "n_open": len(ac.get("open") or []),
                "n_in": n_in,
                "n_out": len(outs),
                "wr": wr,
                "out_s": out_s,
                "lv": lv,
                "dd": dd,
                "n_adj": n_adj,
                "review": ac.get("dd_review"),
            }
        )
    # 收益榜排序（今日战报内按累计收益排）
    rows.sort(key=lambda r: r["ret"], reverse=True)
    medal = ["🥇", "🥈", "🥉", " 4 "]
    lines = []
    for i, r in enumerate(rows):
        cum, day = round(r["ret"], 2) + 0.0, r["day"] + 0.0
        # +0.0 将 round 产生的 -0.0 归一为 +0.0，避免 "-0.00%" 负号配涨色；
        # 配色与大屏同口径：中式红涨绿跌（screen.html --color-up=红）
        adj_s = " ｜调 %d" % r["n_adj"] if r["n_adj"] else ""
        dd_s = (
            (
                {
                    1: " ｜<font color='grey'>🟡回撤%.1f%%</font>",
                    2: " ｜<font color='grey'>🔒熔断%.1f%%</font>",
                    3: " ｜<font color='grey'>🔴深回撤%.1f%%</font>",
                }[r["lv"]]
                % r["dd"]
            )
            if r["lv"]
            else ""
        )
        lines.append(
            "**%s %s %s**｜净值 %.0f 万｜ps %.4f｜累计 <font color='%s'>%+.2f%%</font>｜"
            "今日 <font color='%s'>%+.2f%%</font>｜SR %s｜MaxDD %s\n"
            "持仓 %d ｜建 %d ｜平 %d%s ｜胜率 %s%s%s%s"
            % (
                medal[i] if i < 4 else "",
                r["icon"],
                r["name"],
                r["nav"] / 10000,
                r["ps"],
                "red" if cum >= 0 else "green",
                cum,
                "red" if day >= 0 else "green",
                day,
                "%.2f" % r["sharpe"] if r["sharpe"] is not None else "—",
                "%.1f%%" % r["maxdd"] if r["maxdd"] is not None else "—",
                r["n_open"],
                r["n_in"],
                r["n_out"],
                adj_s,
                r["wr"],
                dd_s,
                (
                    "\n　会诊：%s"
                    % "、".join(
                        "%s(%+.1f%%)" % (w["name"], w["pnl_pct"])
                        for w in r["review"]["worst"][:3]
                    )
                )
                if r["lv"] == 3 and r.get("review")
                else "",
                ("\n　平仓：%s" % r["out_s"]) if r["out_s"] != "—" else "",
            )
        )
    # K54-A3 母基金行（四账户份额加权）+ B4 BAC 月度提案展示
    tail_s = ""
    try:
        import perf_metrics as pm

        curves, shs = {}, {}
        for sid, ac in (led.get("accounts") or {}).items():
            c = ac.get("nav_curve") or []
            if c:
                curves[sid] = [{"d": x["d"], "ps": _curve_ps(x)} for x in c]
                shs[sid] = ac.get("shares") or ac.get("capital") or 1_000_000
        if len(curves) >= 2:
            ps_c = pm.fof_merge(curves, shs)
            fm = pm.compute([{"d": x["d"], "nav": x["ps"]} for x in ps_c])
            tail_s += (
                "\n\n**🏦 母基金（份额加权）**｜ps %.4f｜累计 %+.2f%%｜"
                "SR %s｜MaxDD %s"
                % (
                    ps_c[-1]["ps"],
                    fm["total_ret"],
                    "%.2f" % fm["sharpe"] if fm["sharpe"] is not None else "—",
                    "%.1f%%" % fm["max_dd_pct"]
                    if fm["max_dd_pct"] is not None
                    else "—",
                )
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio.py", exc)
    if led.get("bac"):
        tail_s += "\n\n**📋 BAC 月度提案（%s）**\n%s" % (
            led["bac"]["d"],
            led["bac"]["hint"],
        )
    # K86 战报接六本账：♠ 操盘锦标赛摘要段（观察层，读 eval json，
    # 异常静默——锦标赛故障永不波及四账户战报本体）
    try:
        _ev_fp = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "reports",
            "trader_sim_eval.json",
        )
        with open(_ev_fp, encoding="utf-8") as _f:
            _ev = json.load(_f)
        _aggs = _ev.get("books") or {}
        if _aggs:
            _tot = sum(a.get("n", 0) for a in _aggs.values())
            if _tot:
                _cup = ["🥇", "🥈", "🥉", " 4 ", " 5 ", " 6 "]
                _rk = sorted(
                    _aggs.items(),
                    key=lambda kv: kv[1].get("avg_contrib") or 0,
                    reverse=True,
                )
                _sl = [
                    "%s %s｜%d票 胜率%.0f%%｜均贡献 <font color='%s'>"
                    "%+.2f%%</font>｜α %+.2f%%"
                    % (
                        _cup[i] if i < len(_cup) else "   ",
                        bk.replace("|", "·"),
                        a.get("n", 0),
                        a.get("winrate") or 0.0,
                        "red" if (a.get("avg_contrib") or 0) >= 0 else "green",
                        a.get("avg_contrib") or 0.0,
                        a.get("avg_alpha") or 0.0,
                    )
                    for i, (bk, a) in enumerate(_rk)
                ]
                for _t in ((_ev.get("decisions") or {}).get("tips") or [])[:2]:
                    _sl.append("<font color='grey'>⚠ %s</font>" % _t)
            else:  # 空态：尚无完赛票（立案票次一交易日开盘才建仓）
                _sl = [
                    "<font color='grey'>暂无完赛票（立案票次一交易"
                    "日开盘建仓，日K落盘后计成绩）</font>"
                ]
            _pend = _ev.get("pending") or {}
            if _pend:
                _names = [
                    v.get("name") or v.get("code") for v in list(_pend.values())[:4]
                ]
                _pend_s = "\n待入场 %d 票：%s%s" % (
                    len(_pend),
                    "、".join(_names),
                    " 等" if len(_pend) > 4 else "",
                )
            else:
                _pend_s = "\n待入场 0 票"
            tail_s += (
                "\n\n**♠ 操盘锦标赛（投委会立案票 × 操盘手/盘算六账推演）**"
                "\n%s%s" % ("\n".join(_sl), _pend_s)
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio.py", exc)
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📊 模拟建仓每日战报（四策略虚拟账户）",
            },
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n\n".join(lines) + tail_s},
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "择时择势：环境闸门/信号翻空/止盈止损/离池/到期 · %s"
                        % led.get("ts", ""),
                    }
                ],
            },
        ],
    }
    ok = push_feishu(json.dumps(card, ensure_ascii=False), card=True)
    print("REPORT sent (feishu)" if ok else "REPORT send FAILED (feishu)")


def push_feishu(text, card=False):
    """复用 news_collector 推送链路（与 runner.push_feishu 同口径，避免 import
    runner 触发其模块级副作用）。"""
    import subprocess

    tmp = os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")),
        "news_collector",
        "outbox",
        "pending_portfolio_card.json" if card else "pending_portfolio.txt",
    )
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    args = [
        sys.executable,
        os.path.join(os.path.dirname(tmp), "..", "send_feishu.py"),
        tmp,
    ]
    if card:
        args.append("interactive")
    r = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    return (r.stdout or "").strip().startswith("SEND_OK")


def _spawn_council():
    """K55-C1：跑批后 detached 启动逐票投委会（影子模式）。
    观点层故障永不波及跑批——子进程 + 全兜底，stdout 落
    reports/council_run.log。"""
    try:
        import subprocess

        here = os.path.dirname(os.path.abspath(__file__))
        log = os.path.join(here, "reports", "council_run.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a", encoding="utf-8") as lf:
            lf.write("\n===== %s =====\n" % _now())
            subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(here, "stock_council.py")],
                creationflags=0x00000008 | 0x00000200,
                stdout=lf,
                stderr=subprocess.STDOUT,
                cwd=here,
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio.py", exc)


def print_status():
    led = _load_json(LEDGER)
    cfg_all = load_config()
    print(
        "config: %s  start_date=%s" % (CONFIG_FP, cfg_all.get("start_date") or "(立即)")
    )
    for sid, cc in (cfg_all.get("accounts") or {}).items():
        print(
            "  %s: enabled=%s capital=%d max_pos=%d"
            % (sid, cc.get("enabled", True), cc.get("capital", 0), cc.get("max_pos", 0))
        )
    if not led.get("accounts"):
        print("账本为空，先跑 python portfolio.py")
        return
    print("ts=%s" % led.get("ts"))
    for cfg in SUB_ACCOUNTS:
        ac = led["accounts"].get(cfg["id"]) or {}
        curve = ac.get("nav_curve") or []
        ret = curve[-1]["ret"] if curve else 0.0
        print(
            "%s %s: open=%d closed=%d cash=%.0f nav=%.0f ret=%+.2f%%"
            % (
                ac.get("icon"),
                ac.get("name"),
                len(ac.get("open") or []),
                len(ac.get("closed") or []),
                ac.get("cash", 0),
                curve[-1]["nav"] if curve else 0,
                ret,
            )
        )
        for p in (ac.get("open") or [])[:5]:
            print(
                "  %s %s %s qty=%.4f entry=%.2f last=%.2f pnl=%+.2f%%"
                % (
                    p["market"],
                    p["code"],
                    p["name"],
                    p["qty"],
                    p["entry_px"],
                    p["last_px"],
                    p.get("pnl_pct", 0),
                )
            )


if __name__ == "__main__":
    if "--status" in sys.argv:
        print_status()
    elif "--reset" in sys.argv:
        if os.path.exists(LEDGER):
            os.remove(LEDGER)
        print("RESET done")
    elif "--set" in sys.argv:
        # 用法：python portfolio.py --set qpzm.capital=500000  9dim.enabled=false
        cfg = load_config()
        for a in sys.argv[sys.argv.index("--set") + 1 :]:
            if "." not in a or "=" not in a:
                print("格式错误（应为 sid.key=value）：%s" % a)
                continue
            k, v = a.split("=", 1)
            sid, key = k.split(".", 1)
            if key in ("capital", "max_pos"):
                v = int(v)
            elif v.lower() in ("true", "false"):
                v = v.lower() == "true"
            cfg.setdefault("accounts", {}).setdefault(
                sid, {"enabled": True, "capital": 1_000_000, "max_pos": 20}
            )
            cfg["accounts"][sid][key] = v
            print("SET %s.%s = %s" % (sid, key, v))
        _atomic(CONFIG_FP, cfg)
        print("配置已写入 %s（次日跑批生效，capital 变更自动注资/撤资）" % CONFIG_FP)
    elif "--close" in sys.argv:
        # 用法：python portfolio.py --close qpzm            （清仓整个账户）
        #       python portfolio.py --close qpzm.SOL-USD    （平单只）
        a = sys.argv[sys.argv.index("--close") + 1]
        sid, code = (a.split(".", 1) + [None])[:2] if "." in a else (a, None)
        close_manual(sid, code)
    elif "--adjust" in sys.argv:
        # K51-A：python portfolio.py --adjust qpzm.SOL/USDT,add,50  （加仓50%）
        #         python portfolio.py --adjust qpzm.000915.SZ,trim,50（减仓50%）
        a = sys.argv[sys.argv.index("--adjust") + 1]
        sid, rest = a.split(".", 1)
        code, act, pct = rest.rsplit(",", 2)
        adjust_position(sid, code, act, float(pct))
    elif "--open" in sys.argv:
        # K51-D：python portfolio.py --open qpzm.000915.SZ,50000（金额可省=等权）
        a = sys.argv[sys.argv.index("--open") + 1]
        sid, rest = a.split(".", 1)
        parts = rest.rsplit(",", 1)
        manual_open(sid, parts[0], float(parts[1]) if len(parts) > 1 else None)
    elif "--market" in sys.argv:  # K52-D 标的搜索（空关键词=全量前50）
        q = (
            sys.argv[sys.argv.index("--market") + 1]
            if len(sys.argv) > sys.argv.index("--market") + 1
            else ""
        )
        print(json.dumps(market_search(q), ensure_ascii=False))
    elif "--report" in sys.argv:
        _led, _cfg = load_ledger()
        daily_report(_led, _cfg)
    else:
        try:
            daily_run(notify=True)  # 计划任务入口：跑批+战报
            _spawn_council()  # K55-C1 影子投委会（detached，故障不影响跑批）
        except Exception:  # K52-B 无人值守跑批失败 → 飞书告警
            import traceback

            tail = traceback.format_exc().strip().splitlines()[-1][:200]
            try:
                push_feishu(
                    "🚨 模拟建仓跑批失败：%s\n%s\n账本写盘为原子替换，"
                    "历史快照在 reports/portfolio_backups/" % (_now(), tail)
                )
                print("ALERT sent (feishu)")
            except Exception:
                print("ALERT send FAILED")
            raise SystemExit(1)
