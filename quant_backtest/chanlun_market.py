# -*- coding: utf-8 -*-
"""大盘（上证指数）多级别缠论研判 · 共享口径单点。

消费方：
  - /bt-chanlun 级别联动表 + 综合研判卡（portal_api._cw_stage_rows）
  - 盘前晨报大盘研判节（morning_report._sec_market）

口径（2026-10-05 与门户综合研判卡同步定型，改判定规则只改这里）：
  - 走势 = 最近两中枢位置关系（B.zd>A.zg=上升 / B.zg<A.zd=下降 / 否则盘整）
  - 现价位置 = zd<px 过滤取最近中枢（同扫描链）→ 上方/内/下方
  - 判定矩阵 = 日线走势 × 30m 位置 三×三合成
  - 观察语义 = 下方→反抽不回ZD=三卖观察·收复=重回中枢 /
               内→上下沿定方向 / 上方→回踩不破ZG=三买观察
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CODE = "000001.SH"
PERIODS = ("1d", "30m", "5m")
LABELS = {"1d": "日线", "30m": "30分钟", "5m": "5分钟"}
VERDICT_COLOR = {"up": "#f85149", "warn": "#d29922", "down": "#3fb950"}
# A 股配色惯例（同门户信号清单）：多头/上升=红，空头/下降=绿
REPORTS = os.path.join(HERE, "reports")


def levels(code=CODE, periods=PERIODS, limit=500):
    """多级别引擎读数 → [{per,label,trend,pos,piv,tip,zg,zd,px,sig}]。

    单级别失败跳过；全部失败返回 []。sig 为最近信号 dict（可能 None）。"""
    try:
        from chanlun_data import load_kline
        from chanlun_signal import analyze
    except Exception:
        return []
    out = []
    for per in periods:
        try:
            ri = load_kline(code, per, limit)
            bars = ri.get("bars") or []
            if not bars:
                continue
            st = analyze(bars)
            allp = [(float(p["zg"]), float(p["zd"])) for p in (st.get("pivots") or [])]
            px = float(bars[-1]["c"])
            trend = "盘整"
            if len(allp) >= 2:
                (a_zg, a_zd), (b_zg, b_zd) = allp[-2], allp[-1]
                if b_zd > a_zg:
                    trend = "上升"
                elif b_zg < a_zd:
                    trend = "下降"
            below = [p for p in allp if p[1] < px]
            cur = below[-1] if below else (allp[-1] if allp else None)
            pos, zg, zd, tip = "—", None, None, "—"
            if cur:
                zg, zd = cur
                pos = "中枢上方" if px > zg else "中枢下方" if px < zd else "中枢内"
                if px > zg:
                    tip = "回踩不破 %.0f=三买观察" % zg
                elif px < zd:
                    tip = "反抽不回 %.0f=三卖观察；收复=重回中枢" % zd
                else:
                    tip = "上沿 %.0f 突破 / 下沿 %.0f 守稳定方向" % (zg, zd)
            sig = (st.get("signals") or [None])[-1]
            out.append(
                {
                    "per": per,
                    "label": LABELS.get(per, per),
                    "trend": trend,
                    "pos": pos,
                    "piv": ("[%.0f, %.0f]" % (zd, zg)) if cur else "—",
                    "tip": tip,
                    "zg": zg,
                    "zd": zd,
                    "px": px,
                    "sig": sig,
                }
            )
        except Exception:
            continue
    return out


def verdict(brief):
    """判定矩阵（日线走势 × 30m 位置）→ (判定, 颜色键, 操作语义)。

    颜色键 ∈ up/warn/down（映射 VERDICT_COLOR）。"""
    d = next((b for b in brief if b["per"] == "1d"), None)
    m30 = next((b for b in brief if b["per"] == "30m"), None)
    if not d or not m30:
        return ("读数不足", "warn", "日线/30m 数据不全，研判降级")
    dt, t30, p30 = d["trend"], m30["trend"], m30["pos"]
    if dt == "上升":
        if t30 == "上升" or "上方" in p30:
            return (
                "多头共振 · 顺势",
                "up",
                "大级别与中级别同向——回调看回踩不破关键位的三买机会",
            )
        if t30 == "下降" or "下方" in p30:
            return (
                "升势 · 回踩确认期",
                "warn",
                "大级别多、中级别调整——放量收复节奏线=调整结束信号，"
                "破日线中枢下沿=升势存疑",
            )
        return (
            "升势 · 中级别蓄势",
            "warn",
            "大级别多头未破、中级别中枢震荡待选方向——突破定节奏",
        )
    if dt == "下降":
        if t30 == "下降" or "下方" in p30:
            return (
                "空头共振 · 规避",
                "down",
                "大级别与中级别同向向下——反弹不接、破位不抄",
            )
        if t30 == "上升" or "上方" in p30:
            return (
                "跌势 · 反抽观察期",
                "warn",
                "大级别空、中级别反弹——反弹至承压位关注三卖风险，收复才有反转戏",
            )
        return (
            "跌势 · 中级别整理",
            "warn",
            "大级别空头未破、中级别中枢震荡——下沿失守续跌、上沿突破修复",
        )
    return ("日线区间震荡", "warn", "方向未选——按日线中枢上下沿观察突破方向")


def key_levels(brief):
    """多空分界关键位（纯文本列表）：日线中枢上下沿 + 30m 节奏线
    （随 30m 现价位置自适应）。"""
    d = next((b for b in brief if b["per"] == "1d"), None)
    m30 = next((b for b in brief if b["per"] == "30m"), None)
    keys = []
    if d and d["zg"] and d["zd"]:
        keys.append("上 %.0f 日线中枢上沿（收复=确认）" % d["zg"])
        keys.append("下 %.0f 日线中枢下沿（破位=存疑）" % d["zd"])
    if m30:
        p30 = m30["pos"] or ""
        if "下方" in p30 and m30["zd"]:
            keys.append("节奏 %.0f 30m中枢下沿（放量收复=重回中枢）" % m30["zd"])
        elif "上方" in p30 and m30["zg"]:
            keys.append("节奏 %.0f 30m中枢上沿（回踩不破=三买观察）" % m30["zg"])
        elif m30["zg"] and m30["zd"]:
            keys.append(
                "节奏 %.0f / %.0f 30m中枢上下沿（突破定方向）" % (m30["zg"], m30["zd"])
            )
    return keys


def action_hint(ck):
    """判定颜色键 → 今晨操作倾向（判定矩阵派生文本，零幻觉；
    研究视角呈现，不构成推送触发/拦截口径）。"""
    return {
        "up": "顺势 · 可开新仓，回调看三买",
        "warn": "观察 · 不追不砍，收复节奏线再定",
        "down": "防守 · 不接反弹，破位不抄",
    }.get(ck, "观望——读数不足")


# 仓位档位表（策略配置参数，非引擎读数；键=(判定颜色键, A股闸门safe)。
# 三元组=(档名, 分批说明, 档位上限pct)——pct 供动态调仓量化对比。
# 数字为个人系统风格化配置，按风险偏好调整只改这里。
POS_SIZE = {
    ("up", True): ("标准仓 60~80%", "首笔≤40%，回踩三买确认后加满", 80),
    ("up", False): ("半仓 ≤40%", "首笔≤20%，站稳节奏线后再加", 40),
    ("warn", True): ("轻仓 ≤30%", "首笔≤15%，收复节奏线确认后加", 30),
    ("warn", False): ("底仓 ≤15%", "只持有不新开，收复节奏线+闸门放行再补", 15),
    ("down", True): ("防守 ≤10%", "反弹减仓，不接飞刀", 10),
    ("down", False): ("空仓观望", "反弹不接、破位不抄，等右侧修复", 0),
}

# 动态调仓影子账本（状态文件：目标档位+调仓动作历史，原子写；
# 只计算/记录/提示，不下单、不改推送触发/拦截口径）
POS_STATE = os.path.join(HERE, "data", "chanlun_pos_state.json")

# 仓位管理表（策略配置参数，非引擎读数；键与 POS_SIZE 同=(判定颜色键,
# A股闸门safe)。steps=分批比例阶梯 / cap=单票+总仓上限 /
# rebalance=调仓触发链（升档/降档条件）。调整只改这里。
POS_MGMT = {
    ("up", True): {
        "steps": "首笔 40% → 回踩三买确认 60% → 趋势延续加满 80%",
        "cap": "单票 ≤20% · 总仓 ≤80%",
        "rebalance": "升档：回踩三买确认逐级加；降档：30m 失守 ZD 减半、"
        "日线破 ZD 清仓复盘",
    },
    ("up", False): {
        "steps": "首笔 20% → 站稳节奏线 30% → 闸门放行后补至 40%",
        "cap": "单票 ≤15% · 总仓 ≤40%",
        "rebalance": "升档：闸门放行即补满；降档：30m 失守 ZD 减半",
    },
    ("warn", True): {
        "steps": "首笔 15% → 放量收复节奏线确认 25% → 站稳日线上沿 30%",
        "cap": "单票 ≤10% · 总仓 ≤30%",
        "rebalance": "升档：收复节奏线+站稳日线上沿逐级加；降档：三卖确认"
        "止损首笔、日线破 ZD 降至底仓",
    },
    ("warn", False): {
        "steps": "存量持有 → 闸门放行+收复节奏线后新开 ≤10% → 站稳日线上沿 ≤15%",
        "cap": "单票 ≤10% · 总仓 ≤15%",
        "rebalance": "升档：闸门放行+收复节奏线 → 轻仓档 ≤30%；降档："
        "日线破 ZD → 空仓观望",
    },
    ("down", True): {
        "steps": "首笔 0 · 存量 ≤10% 只减不加",
        "cap": "单票 ≤10% · 总仓 ≤10%",
        "rebalance": "降档：反弹承压继续减；升档：仅日线收复 ZG/日线三买后重评",
    },
    ("down", False): {
        "steps": "首笔 0 · 存量 0（空仓观望）",
        "cap": "总仓 0",
        "rebalance": "升档：等日线收复 ZG 或日线三买后重评；期间反弹不接、破位不抄",
    },
}


def _gate_cn():
    """A股 envs 项；不可用 None。"""
    try:
        with open(os.path.join(REPORTS, "rps_latest.json"), encoding="utf-8") as f:
            envs = json.load(f).get("envs") or []
        return next((e for e in envs if "A股" in str(e.get("label", ""))), None)
    except Exception:
        return None


def gate_safe():
    """A股闸门 safe bool；不可用 None（消费方按 False 保守处理）。"""
    cn = _gate_cn()
    return None if cn is None else bool(cn.get("safe"))


def gate_line():
    """A股环境闸门一行（rps_latest.json envs，与门户环境闸门卡同源
    快照）；不可用返回 ''——只呈现不否决。"""
    cn = _gate_cn()
    if not cn:
        return ""
    st = str(cn.get("state", "")).replace("— 闸门收紧", "").strip()
    tag = "✅ 放行" if cn.get("safe") else "⛔ 收紧"
    return "闸门 %s · %s" % (tag, st)


def trade_plan(brief):
    """💼 操作建议：买卖点位（引擎关键位派生，缺位跳过对应行）+
    仓位档位（判定×闸门查 POS_SIZE——百分比为策略配置参数非引擎
    计算）。返回 dict(size, ladder, buy, sell, gate) 或 None（读数
    不足）。仅为研究视角呈现，不构成推送触发/拦截口径。"""
    d = next((b for b in brief if b["per"] == "1d"), None)
    m30 = next((b for b in brief if b["per"] == "30m"), None)
    m5 = next((b for b in brief if b["per"] == "5m"), None)
    if not d or not m30:
        return None
    ck = verdict(brief)[1]  # 颜色键 up/warn/down（[0]是判定文本）
    gkey = bool(gate_safe())
    _slot = POS_SIZE.get((ck, gkey))
    if not _slot:
        _slot = POS_SIZE[("warn", False)]
    size, ladder, _pct = _slot
    dzg, dzd, p30g, p30d = d["zg"], d["zd"], m30["zg"], m30["zd"]
    m5g = m5["zg"] if m5 else None
    m5d = m5["zd"] if m5 else None
    buy, sell = [], []
    if ck == "up":
        if dzg:
            buy.append("回踩不破 %.0f（日线三买观察）" % dzg)
        if m5g:
            buy.append("日内 %.0f 上沿放量突破（结构确认）" % m5g)
        if p30d:
            sell.append("30m 失守 %.0f（结构转弱）" % p30d)
        if dzd:
            sell.append("失守 %.0f（日线破位·结构失效）" % dzd)
    elif ck == "warn" and d["trend"] == "上升":
        # 回踩确认期/蓄势：收复节奏线试多，站稳上沿加仓，破位降档
        if p30d:
            buy.append("放量收复 %.0f（30m 重回中枢）" % p30d)
        if dzg:
            buy.append("站稳 %.0f（日线收复=回踩确认）" % dzg)
        if m5g and m5d:
            buy.append("日内 %.0f 上沿突破（破 %.0f 结构失效）" % (m5g, m5d))
        if p30d and m5d:
            sell.append("反抽不回 %.0f 且失守 %.0f（三卖确认）" % (p30d, m5d))
        if dzd:
            sell.append("失守 %.0f（日线破位=升势存疑）" % dzd)
    elif ck == "warn" and d["trend"] == "下降":
        if p30g:
            sell.append("反抽承压 %.0f（30m中枢上沿）→ 风险位" % p30g)
        if dzg:
            buy.append("收复 %.0f（日线中枢上沿=右侧结构成立条件）" % dzg)
        if dzd:
            sell.append("失守 %.0f → 下一中枢寻找支撑" % dzd)
    elif ck == "warn":  # 日线盘整
        if dzd:
            buy.append("回踩 %.0f（日线中枢下沿）企稳（区间结构）" % dzd)
        if dzg:
            sell.append("承压 %.0f（日线中枢上沿·区间上沿）" % dzg)
        if dzd:
            sell.append("失守 %.0f（区间破坏）" % dzd)
    else:  # down
        if dzg:
            buy.append("收复 %.0f（日线中枢上沿）或日线三买（右侧条件）" % dzg)
        if p30g:
            sell.append("反抽 %.0f（30m 中枢上沿承压）" % p30g)
        if dzd:
            sell.append("失守 %.0f（加速段）" % dzd)
    if not gkey and buy:
        buy.append("（闸门收紧期以上为触发观察位，非开仓许可）")
    mgmt = POS_MGMT.get((ck, gkey)) or POS_MGMT[("warn", False)]
    return {
        "size": size,
        "ladder": ladder,
        "buy": buy,
        "sell": sell,
        "gate": gkey,
        "mgmt": mgmt,
    }


def dynamic_pos(brief, write=True):
    """🔄 动态调仓（影子账本）：实时档位（引擎三级别读数+闸门快照）
    对比上次状态 → 自动产出调仓动作（升档/降档/维持）。

    - 目标档位 = trade_plan 同源 POS_SIZE 查表（pct 为档位上限数字）
    - 动作判定 = 与 data/chanlun_pos_state.json 上次 pct 数值对比
    - 仅在档位变化/首建时原子写盘（tempfile+os.replace，防 churn）
    - 只计算/记录/提示，不下单、不改推送触发/拦截口径

    返回 dict(ck, gate, name, pct, action, prev_name, prev_pct, at)。"""
    ck = verdict(brief)[1]
    gkey = bool(gate_safe())
    _slot = POS_SIZE.get((ck, gkey))
    if not _slot:
        _slot = POS_SIZE[("warn", False)]
    name, _ladder, pct = _slot
    prev = None
    try:
        with open(POS_STATE, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = None
    prev_name = (prev or {}).get("name")
    prev_pct = (prev or {}).get("pct")
    if prev_name is None:
        action = "初始建档"
    elif pct > (prev_pct if prev_pct is not None else 0):
        action = "升档 %s → %s" % (prev_name, name)
    elif pct < (prev_pct if prev_pct is not None else 0):
        action = "降档 %s → %s" % (prev_name, name)
    else:
        action = "维持"
    out = {
        "ck": ck,
        "gate": gkey,
        "name": name,
        "pct": pct,
        "action": action,
        "prev_name": prev_name,
        "prev_pct": prev_pct,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    changed = prev is None or prev_name != name or prev_pct != pct
    if write and changed:
        try:
            os.makedirs(os.path.dirname(POS_STATE), exist_ok=True)
            hist = list((prev or {}).get("history") or [])
            if prev_name is not None:
                hist.append(
                    {"at": out["at"], "from": prev_name, "to": name, "pct": pct}
                )
            out["history"] = hist[-30:]
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(POS_STATE), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
            os.replace(tmp, POS_STATE)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_market.py", exc)
    return out


# ── 个股研判（四市场通用） ────────────────────────────────
_LV_CACHE = {}  # (code, periods, limit) → (ts, rows) 60s
_MB_CACHE = {}  # 大盘默认三级别 → (ts, rows) 60s


def levels_cached(code=CODE, periods=PERIODS, limit=500, ttl=60):
    """levels() 的模块级 TTL 缓存包装——个股页等高频消费方防重算。"""
    import time as _t

    k = (code, tuple(periods), limit)
    _n = _t.time()
    c = _LV_CACHE.get(k)
    if c and _n - c[0] < ttl:
        return c[1]
    out = levels(code, periods, limit)
    _LV_CACHE[k] = (_n, out)
    return out


def market_brief(ttl=60):
    """大盘默认三级别读数（60s 缓存）——晨报/门户大盘页/个股页共享，
    判定矩阵与级别联动表同源。"""
    import time as _t

    _n = _t.time()
    c = _MB_CACHE.get("mb")
    if c and _n - c[0] < ttl:
        return c[1]
    b = levels()
    _MB_CACHE["mb"] = (_n, b)
    return b


def stock_view(code):
    """个股缠论日线结构速览（四市场通用，load_kline 原生多形态代码）。

    复用 levels() 单级别口径（trend 两中枢关系 / pos zd<px 过滤 /
    tip 操作语义 / sig 最近信号），另附 sig_txt 文本。60s 缓存。
    数据不足返回 {"ok": False}（调用方不渲染）。"""
    rows = levels_cached(code, ("1d",), 500)
    if not rows:
        return {"ok": False}
    r = dict(rows[0])
    sig = r.get("sig")
    r["sig_txt"] = (
        "%s(%s)" % (sig.get("type"), (str(sig.get("dt")) or "")[5:])
        if isinstance(sig, dict) and sig.get("type")
        else None
    )
    r["ok"] = True
    return r
