# -*- coding: utf-8 -*-
"""L2 Object View 装配引擎（2026-10-09）。

Palantir Object Views 的本地版：一对象一枢纽页。stock() 数据层
（13 扇区查询）保持不动，本模块在其上声明式装配「扇区体检」：
每个扇区 = 一行配置 {title, rel, data, date_cols, freq}，
引擎自动产出徽章——观测时点（防前视锚）/滞后天数/新鲜档/
信源信任档（从 L1 ontology 注册表下探到扇区级）。

新增一个分析视角 = SECTORS 加一行 + stock() 补一查（不写 HTML）。

档位口径（阈值与 kg_source_rating 同源，不另立标准）：
  grade: ok=新鲜(绿) / stale=连续型超阈值断流(红) / empty=无数据(灰) /
         static=静态名录(免检)
  freq : D/M/Q=连续型节奏（滞后超阈报红）；E=触发式（有数据即绿，
         没上榜≠断流）；static=免检
  lag  : 交易日口径（K267）——fresh 之后到期望日（最近已收盘交易
         日，<17 点当日未收盘，trade_cal 权威）之间的开市日数；
         长假/周末不累计（2026 国庆 8 天假不再误报红）。
         trade_cal 不可达时回退自然日（lag_unit=cal）。
         阈值：td 档 STALE_TD（D=5 交易日≈1 周；Q=90 交易日≈自然日 130 天，
              含季报披露空窗 10-31/04-30，0630/0930 快照不误报红）；
              cal 回退沿用 G7.STALE_DAYS。
  trust: Admiralty A-F，取 L1 ontology.EDGE_TYPES[rel] 的实证档；
         非图扇区（特征湖直读）就地声明并注明来源。

company_360（六扇区 G6 图）为后续扩展点：其扇区本体声明可并入
本注册表（frm/to 元数据已在 L1），装配输出 nodes/edges 而非行集。

用法：
  python kg_objectview.py 002743.SZ        # CLI 自测
  kg_portal.api("objectview", code, days)  # 门户/agent 消费
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as OT                    # L1：信任档唯一权威
import kg_source_rating as G7            # 滞后阈值唯一权威

# 交易日口径阈值（G7.STALE_DAYS 自然日阈值 × 250/365 换算：D 5→5 放宽一周、Q 130→90）
STALE_TD = {"D": 5, "Q": 90}


# ── 扇区声明注册表（stock() 结果键 → 视图扇区）───────────────
# data   = stock() 返回 dict 的键（行集或单 dict）
# rel    = 驱动边型（trust/source 从 L1 注册表解析；非图扇区 rel=None）
# date_cols = 观测时间列（按序尝试，取行内最大；空=该扇区无行级锚）
# freq   = 预期节奏：D/M/Q 对 STALE_DAYS 阈值（连续型，滞后超阈=断流红）；
#          E=触发式（龙虎榜/涨跌停类：有数据即绿、无数据灰、不报红——
#          没上榜不是断流）；static=名录免检
SECTORS = {
    "basic": dict(title="基本面", rel=None, data="basic",
                  date_cols=[], freq="static",
                  trust="B", source="xtcloud_local+tushare_pro",
                  note="tdx∪tushare 静态名录"),
    "structures": dict(title="结构事件（缠论）", rel="HAS_STRUCTURE",
                       data="structures", date_cols=["date"], freq="E"),
    "vol_events": dict(title="量能事件", rel="HAS_VOL_EVENT",
                       data="vol_events", date_cols=["last"], freq="E"),
    "hotmoney": dict(title="游资痕迹", rel="TRADED_ON",
                     data="hotmoney", date_cols=["last"], freq="E",
                     note="龙虎榜仅上榜日披露，空≠断流"),
    "candles": dict(title="盘口事件", rel="HAS_CANDLE",
                    data="candles", date_cols=["date"], freq="E",
                    note="分钟库覆盖边界见 candle_cov"),
    "limits": dict(title="涨跌停", rel="HAS_LIMIT",
                   data="limits", date_cols=["date"], freq="E",
                   note="触发式事件，空=窗口无涨跌停"),
    "topgaps": dict(title="榜后差额", rel="HAS_TOPGAP",
                    data="topgaps", date_cols=["date"], freq="E"),
    "holders": dict(title="股东持股", rel="HOLDS",
                    data="holders", date_cols=["period"], freq="Q",
                    note="季频披露（报告期快照），Q 空窗=未披露"),
    "similar": dict(title="K线相似", rel="SIMILAR_K",
                    data="similar", date_cols=["we"], freq="D",
                    note="60 日 DTW 窗口末端为锚"),
    "simseats": dict(title="相似同席位", rel=None,
                     data="simseats", date_cols=["d1"], freq="E",
                     note="派生扇区（SIMILAR_K×TRADED_ON 交）",
                     trust="B", source="cooccur_stat"),
    "cyq": dict(title="筹码分布", rel=None,
                data="cyq", date_cols=["date"], freq="D", single=True,
                note="特征湖 parquet 直读（不入图）",
                trust="B", source="kg_features 特征湖"),
    "drive_groups": dict(title="驱动事件组", rel="DRIVE",
                         data="drive_groups", date_cols=["last"], freq="E",
                         note="M2 共现统计（不判因果）"),
}


def _rows_of(st, cfg):
    """取扇区行集（single=True 为单 dict，统一成 list）。"""
    v = st.get(cfg["data"])
    if v is None:
        return []
    return [v] if cfg.get("single") else v


def _max_date(rows, cols):
    """行级观测锚：取声明列中的最大 8 位日期（空列集/无值 → None）。"""
    best = ""
    for r in rows:
        for c in cols:
            v = str(r.get(c) or "")
            if len(v) >= 8 and v[:8].isdigit() and v[:8] > best:
                best = v[:8]
    return best or None


def _lag_days(fresh):
    if not fresh:
        return None
    try:
        d = datetime.strptime(fresh, "%Y%m%d")
    except ValueError:
        return None
    return (datetime.now() - d).days


def _trade_days(n_days=400):
    """trade_cal 权威开市日序列（复用 datamart 缓存 1h；失败 []）。"""
    try:
        import datamart as dm
        return dm._recent_trade_days(n_days)
    except Exception:
        return []


def _lag_of(fresh, trade_days):
    """滞后计算（K267 交易日口径）。

    有 trade_cal：lag = fresh 之后到期望日（最近已收盘交易日，
    <17 点当日未收盘）之间的开市日数——周末/长假不累计。
    无 trade_cal：回退自然日（与旧行为一致）。
    返回 (lag, unit)；unit ∈ {"td", "cal"}。
    """
    if not fresh:
        return None, None
    if trade_days:
        try:
            import datamart as dm
            exp = dm._expected_trade_day(trade_days)
        except Exception:
            exp = None
        if exp:
            if fresh >= exp:
                return 0, "td"
            return (sum(1 for d in trade_days if fresh < d <= exp), "td")
    return _lag_days(fresh), "cal"


def _grade(fresh, lag, freq, unit="td"):
    if freq == "static":
        return "static"
    if freq == "E":                       # 触发式：有数据即绿，不报红
        return "ok" if fresh else "empty"
    if fresh is None or lag is None:
        return "empty"
    thr = (STALE_TD if unit == "td" else G7.STALE_DAYS).get(freq)
    if thr is None:
        return "empty"
    return "ok" if lag <= thr else "stale"


def _trust_of(cfg):
    """信任档：图扇区走 L1（边型→源族→实证档），非图扇区就地声明。"""
    if cfg.get("rel"):
        m = OT.EDGE_TYPES[cfg["rel"]]
        return m["trust"], m["source"]
    return cfg.get("trust"), cfg.get("source")


def assemble(st, trade_days=None):
    """把 stock() 结果装配为扇区体检视图（portal 已取 st 时零额外查询）。

    入参须为 stock() 的成功返回（ok=True）；徽章全离线计算不碰图。
    trade_days=None → 自动取 trade_cal（K267 交易日口径）；
    显式传 [] → 强制回退自然日口径（离线测试用）。
    """
    if trade_days is None:
        trade_days = _trade_days()
    sectors = []
    for sid, cfg in SECTORS.items():
        rows = _rows_of(st, cfg)
        fresh = _max_date(rows, cfg["date_cols"])
        lag, unit = _lag_of(fresh, trade_days)
        grade = _grade(fresh, lag, cfg["freq"], unit)
        trust, source = _trust_of(cfg)
        sectors.append({
            "id": sid, "title": cfg["title"], "n": len(rows),
            "fresh": fresh, "lag": lag, "lag_unit": unit,
            "grade": grade,
            "freq": cfg["freq"], "trust": trust, "source": source,
            "note": cfg.get("note", ""),
        })
    # 数据层直通键（agent/页面二跳消费；不重复装配）
    return {"ok": True, "code": st.get("code"),
            "name": (st.get("basic") or {}).get("name"),
            "window_days": st.get("window_days"),
            "candle_cov": st.get("candle_cov"),
            "sectors": sectors, "_stock": st}


def view(code, days=120):
    """装配个股对象视图：stock() 数据 × 扇区声明 × L1 徽章。"""
    import kg_portal as KP
    st = KP.stock(code, days)
    if not st.get("ok"):
        return st
    return assemble(st)


if __name__ == "__main__":
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else "002743.SZ"
    r = view(code)
    r.pop("_stock", None)                    # CLI 打印省去原始行集
    print(json.dumps(r, ensure_ascii=False, indent=1))
