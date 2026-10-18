"""缠论 v2 决策层 — 严格按《智策A-OS_40 缠论策略规则书》实现（纯函数，不依赖 czsc）。

自 V1 modules.candidate_engine.strategies.chan.rules_v2 移植（语义原样）。
结构层输入为 engine.extract_bi_list 产出的笔字典列表 + 日K DataFrame。
所有条款号（R2.x/R3.x/R4.x）指向规则书对应规则；这里是"笔级别中枢"自定义体系（D1），
不冒称原文日线中枢。

信号优先级: b3 > b2 > b1（检出即返回，二三买重合在 b2 内加成）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan.engine import (
    compute_macd_hist,
    compute_vol_price_sync,
)

logger = logging.getLogger(__name__)

# ─── 参数表（规则书 §6） ─────────────────────────────────────────────────────
DIV_AREA_RATIO = 0.8          # R3.1-3 离开段面积 < 进入段×0.8
ZERO_PULLBACK_RATIO = 0.3     # R3.1-2 中枢期 DIF 回收至进入段极值的 30% 以内（回收≥70%）
B3_MAX_DIST = 0.15            # R4.3-5 回试低点距 ZG 超 15% 视为"飞三买"
ZS_MAX_BIS = 9                # R2.6 九段升级：核心笔数 >9 的中枢不作本级别三买依据
B3_2ND_PIVOT_PENALTY = 0.6    # R4.3-5 第2个上移中枢降权；第3个起禁用


def _is_up(bi: dict) -> bool:
    return "up" in bi.get("direction", "").lower() or "向上" in bi.get("direction", "")


def _is_down(bi: dict) -> bool:
    return "down" in bi.get("direction", "").lower() or "向下" in bi.get("direction", "")


# ─── R2.7 中枢分组（复刻 czsc get_zs_seq 语义，附带笔索引） ────────────────


def group_pivots(bi_list: list[dict]) -> list[dict]:
    """按 czsc get_zs_seq 语义把笔序列切成中枢组：一笔完全脱离 [ZD,ZG] 才开新组。

    注意语义细节：向上离开笔（低点仍在中枢内）留在原组末尾；其后完全高于 ZG 的
    向下回试笔才开新组——因此"离开段 = 组末笔、回试段 = 新组首笔"（R2.7）。
    返回组字典: idxs/zg/zd/gg/dd/bi_count/valid。
    """
    groups: list[dict] = []
    cur: list[int] = []
    for i, bi in enumerate(bi_list):
        if not cur:
            cur = [i]
            continue
        first3 = [bi_list[j] for j in cur[:3]]
        zg = min(float(b["high"]) for b in first3)
        zd = max(float(b["low"]) for b in first3)
        h, low = float(bi["high"]), float(bi["low"])
        if (_is_up(bi) and h < zd) or (_is_down(bi) and low > zg):
            groups.append(_finalize_group(cur, bi_list))
            cur = [i]
        else:
            cur.append(i)
    if cur:
        groups.append(_finalize_group(cur, bi_list))
    return groups


def _finalize_group(idxs: list[int], bi_list: list[dict]) -> dict:
    bis = [bi_list[i] for i in idxs]
    first3 = bis[:3]
    zg = min(float(b["high"]) for b in first3)
    zd = max(float(b["low"]) for b in first3)
    valid = (
        len(bis) >= 3 and zg > zd
        and all(float(b["low"]) <= zg and float(b["high"]) >= zd for b in bis)  # R2.5 每笔与区间有交集
    )
    return {
        "idxs": idxs,
        "zg": zg, "zd": zd,
        "gg": max(float(b["high"]) for b in bis),
        "dd": min(float(b["low"]) for b in bis),
        "bi_count": len(bis),
        "valid": valid,
    }


def clean_pivots(bi_list: list[dict], groups: list[dict], exclude_tail: int = 1) -> list[dict]:
    """"净中枢"视图：剔除组末离开笔与序列尾部 exclude_tail 根笔后重算极值。

    czsc 分组语义下离开笔留在原组内，会把组的 GG/DD 拖到下一中枢区域，
    直接用组极值判"波动区间不接触"（R2.8 趋势）或"创新低"会系统性失败。
    非末组的末笔必为离开段（下一组首笔完全脱离区间 ⇒ 前一笔已走出）——剔除；
    序列尾部的当下笔（背驰段 C / 一买回抽等）不参与中枢统计——剔除。
    """
    cutoff = len(bi_list) - exclude_tail
    out = []
    for gi, g in enumerate(groups):
        idxs = [i for i in g["idxs"] if i < cutoff]
        if (gi < len(groups) - 1 and idxs and idxs[-1] == g["idxs"][-1]
                and len(idxs) > 1):
            idxs = idxs[:-1]          # 非末组末笔 = 离开段
        if len(idxs) >= 3:
            out.append(_finalize_group(idxs, bi_list))
    return [g for g in out if g["valid"]]


# ─── R3 背驰工具（MACD 面积法） ─────────────────────────────────────────────


def compute_macd_dif(closes: pd.Series) -> pd.Series:
    """DIF = EMA12 − EMA26（黄白线之"白线"，零轴回拉判定用）。"""
    return closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()


def _window(df: pd.DataFrame, start_dt: str, end_dt: str) -> pd.DataFrame:
    """截取 [start_dt, end_dt] 闭区间的K线。

    以 pd.to_datetime 做时间戳比较，避免 astype(str) 字典序坑：pandas 3.x 下
    午夜日线 dt.astype(str)="2024-01-05" 与笔端点 "2024-01-05 00:00:00" 字典序
    比较会误丢首根（笔分型端点/局部极值）→ 污染背驰面积与买卖点判定。
    """
    start = pd.to_datetime(start_dt)
    end = pd.to_datetime(end_dt)
    dts = pd.to_datetime(df["dt"])
    return df[(dts >= start) & (dts <= end)]


def _green_area(df2: pd.DataFrame) -> float:
    """窗口内绿柱面积（负值求和的绝对值），R3.3 同色柱逐bar求和。"""
    return float(abs(df2["_hist"].clip(upper=0).sum()))


def _red_area(df2: pd.DataFrame) -> float:
    return float(df2["_hist"].clip(lower=0).sum())


def _prep_macd(df: pd.DataFrame) -> pd.DataFrame:
    closes = df["close"].astype(float)
    return df.assign(_hist=compute_macd_hist(closes).to_numpy(),
                     _dif=compute_macd_dif(closes).to_numpy())


def trend_divergence_down(
    bi_list: list[dict], groups: list[dict], df2: pd.DataFrame,
) -> dict | None:
    """R3.1 下跌趋势背驰四要件。成立返回 {conf, area_ratio, pivot}，否则 None。

    结构: 末两个有效中枢依次下移且波动区间不接触（R2.8）；
    A = 进入末中枢的向下笔，C = 末笔（须创新低），B = 末中枢期间。
    """
    last = bi_list[-1]
    if not _is_down(last):
        return None
    valid = clean_pivots(bi_list, groups, exclude_tail=1)
    if len(valid) < 2:
        return None
    p2, p1 = valid[-1], valid[-2]
    if p1["dd"] <= p2["gg"]:          # 中心定理二：前中枢整体高于后中枢才是下跌趋势
        return None
    if float(last["low"]) >= p2["dd"]:  # 新低硬条件（37课）
        return None

    a = bi_list[p2["idxs"][0]]
    if not _is_down(a):
        i = p2["idxs"][0] - 1
        if i < 0:
            return None
        a = bi_list[i]

    a_win = _window(df2, a["start_dt"], a["end_dt"])
    c_win = _window(df2, last["start_dt"], last["end_dt"])
    b_start = bi_list[p2["idxs"][0]]["start_dt"]
    b_end = bi_list[p2["idxs"][-1]]["end_dt"]
    b_win = _window(df2, b_start, b_end)
    if a_win.empty or c_win.empty or b_win.empty:
        return None

    # 要件2 零轴回拉：中枢期 DIF 回收到进入段 DIF 极小值的 30% 以内
    dif_a_min = float(a_win["_dif"].min())
    if dif_a_min >= 0:
        return None
    if float(b_win["_dif"].max()) < ZERO_PULLBACK_RATIO * dif_a_min:
        return None

    # 要件3 面积：C 绿柱面积 < A × 0.8
    area_a, area_c = _green_area(a_win), _green_area(c_win)
    if area_a <= 0 or area_c >= DIV_AREA_RATIO * area_a:
        return None

    # 辅助双信号（27课"或"）：黄白线不创新低 → 双确认 HIGH
    dif_no_new_low = float(c_win["_dif"].min()) > dif_a_min
    return {
        "conf": "HIGH" if dif_no_new_low else "MID",
        "area_ratio": round(area_c / area_a, 3),
        "pivot": p2,
    }


def range_divergence_down(prev_down: dict, this_down: dict, df2: pd.DataFrame) -> bool:
    """R3.2 盘整背驰（向下）：两同向段绿柱面积缩小即成立（不问趋势结构）。"""
    a_win = _window(df2, prev_down["start_dt"], prev_down["end_dt"])
    c_win = _window(df2, this_down["start_dt"], this_down["end_dt"])
    if a_win.empty or c_win.empty:
        return False
    area_a, area_c = _green_area(a_win), _green_area(c_win)
    return area_a > 0 and area_c < DIV_AREA_RATIO * area_a


# ─── R4 三类买点 ─────────────────────────────────────────────────────────────


def detect_b3(bi_list: list[dict], groups: list[dict], df2: pd.DataFrame) -> dict | None:
    """R4.3 三买：紧邻中枢 + 真离开（破组内前高）+ 第一次回试 > ZG + 新鲜度纪律。"""
    if len(groups) < 2:
        return None
    n, c = groups[-1], groups[-2]
    if not c["valid"]:
        return None
    c_bis = [bi_list[i] for i in c["idxs"]]
    dep = c_bis[-1]                       # 离开段 = 组末笔（分组语义，R2.7）
    if not _is_up(dep):
        return None
    core = c_bis[:-1]
    if not (3 <= len(core) <= ZS_MAX_BIS):   # R2.6 九段升级
        return None
    core_gg = max(float(b["high"]) for b in core)
    if float(dep["high"]) <= core_gg:     # 收紧：突破中枢此前极值才算真离开
        return None

    n_bis = [bi_list[i] for i in n["idxs"]]
    if len(n_bis) > 2:                    # 回试后又走了多笔 → 信号过期（第一次回试已不在当下）
        return None
    r = n_bis[0]
    if not _is_down(r):
        return None
    r_low, zg = float(r["low"]), c["zg"]
    if r_low <= zg:                       # 回试跌回中枢（防御，分组语义已保证）
        return None
    dist = (r_low - zg) / zg
    if dist > B3_MAX_DIST:                # R4.3-5 飞三买过滤
        return None

    # R4.3-5 中枢计数纪律（22课）：此前的同向上移有效中枢（净视图：剔除各组离开笔）
    prior = []
    for g in groups[:-2]:
        p_idxs = g["idxs"][:-1] if len(g["idxs"]) > 1 else g["idxs"]
        if len(p_idxs) >= 3:
            prior.append(_finalize_group(p_idxs, bi_list))
    prior_ups = [g for g in prior if g["valid"] and g["gg"] < c["dd"]]
    if len(prior_ups) >= 2:
        return None
    penalty = B3_2ND_PIVOT_PENALTY if len(prior_ups) == 1 else 1.0

    # 背驰质量：离开段力度未较进入段衰竭（非盘整背驰）→ 强离开 HIGH（107课趋势语境）
    entry_up = next((b for b in reversed(core) if _is_up(b)), None)
    strong_leave = True
    if entry_up is not None:
        a_win = _window(df2, entry_up["start_dt"], entry_up["end_dt"])
        d_win = _window(df2, dep["start_dt"], dep["end_dt"])
        if not a_win.empty and not d_win.empty:
            strong_leave = _red_area(d_win) >= DIV_AREA_RATIO * _red_area(a_win)

    return {
        "point": "b3",
        "conf": "HIGH" if strong_leave else "MID",
        "pivot": c,
        "pivot_bis": len(core),
        "dist_to_zg": round(dist, 4),
        "penalty": penalty,
        "is_second_pivot": len(prior_ups) == 1,
    }


def detect_b2(bi_list: list[dict], groups: list[dict], df2: pd.DataFrame) -> dict | None:
    """R4.2 二买：一买语境（末中枢下方背驰新低）→ 首个向上笔 → 回抽笔结束点。

    弱二买（低于一买）直接放弃；回抽不破末中枢 ZG → 与三买重合（最强）。
    """
    if len(bi_list) < 3:
        return None
    r, u, d1 = bi_list[-1], bi_list[-2], bi_list[-3]
    if not (_is_down(r) and _is_up(u) and _is_down(d1)):
        return None
    valid = clean_pivots(bi_list, groups, exclude_tail=3)
    if not valid:
        return None
    p2 = valid[-1]
    d1_low = float(d1["low"])
    if d1_low >= p2["dd"]:               # d1 未跌破末中枢 → 无一买语境
        return None
    if float(r["low"]) < d1_low:         # 弱二买放弃（量化取稳）
        return None

    # d1 须对进入末中枢的 A 段至少构成盘整背驰，否则一买语境不成立
    a = bi_list[p2["idxs"][0]]
    if not _is_down(a):
        i = p2["idxs"][0] - 1
        if i < 0:
            return None
        a = bi_list[i]
    if not range_divergence_down(a, d1, df2):
        return None

    b2b3 = float(r["low"]) > p2["zg"]    # R4.5 二三买重合 = 最强
    # 回抽笔自身对 d1 的盘整背驰 → 确认加分
    pullback_div = range_divergence_down(d1, r, df2)
    return {
        "point": "b2",
        "conf": "HIGH" if (b2b3 or pullback_div) else "MID",
        "pivot": p2,
        "pivot_bis": p2["bi_count"],
        "b2b3_overlap": b2b3,
        "b1_low": d1_low,
    }


def detect_b1(bi_list: list[dict], groups: list[dict], df2: pd.DataFrame) -> dict | None:
    """R4.1 一买：下跌趋势背驰点（四要件全部成立）。"""
    div = trend_divergence_down(bi_list, groups, df2)
    if div is None:
        return None
    return {
        "point": "b1",
        "conf": div["conf"],
        "pivot": div["pivot"],
        "pivot_bis": div["pivot"]["bi_count"],
        "area_ratio": div["area_ratio"],
    }


# ─── R4.4 卖点（对称，供持仓退出；先输出检测，接入为后续步骤） ─────────────


def detect_sell_point(bi_list: list[dict], groups: list[dict], df2: pd.DataFrame) -> str | None:
    """s3: 向下离开中枢后第一次回抽高点 < ZD；s1: 上涨趋势顶背驰。"""
    if len(bi_list) < 2:
        return None
    # s3（与 b3 对称）
    if len(groups) >= 2:
        n, c = groups[-1], groups[-2]
        if c["valid"]:
            c_bis = [bi_list[i] for i in c["idxs"]]
            dep = c_bis[-1]
            core = c_bis[:-1]
            if (_is_down(dep) and len(core) >= 3
                    and float(dep["low"]) < min(float(b["low"]) for b in core)):
                n_bis = [bi_list[i] for i in n["idxs"]]
                if len(n_bis) <= 2 and _is_up(n_bis[0]) and float(n_bis[0]["high"]) < c["zd"]:
                    return "s3"
    # s1（上涨趋势顶背驰，与 trend_divergence_down 镜像）
    last = bi_list[-1]
    if _is_up(last):
        valid = clean_pivots(bi_list, groups, exclude_tail=1)
        if len(valid) >= 2:
            p2, p1 = valid[-1], valid[-2]
            if p1["gg"] < p2["dd"] and float(last["high"]) > p2["gg"]:
                a = bi_list[p2["idxs"][0]]
                if not _is_up(a) and p2["idxs"][0] >= 1:
                    a = bi_list[p2["idxs"][0] - 1]
                if _is_up(a):
                    a_win = _window(df2, a["start_dt"], a["end_dt"])
                    c_win = _window(df2, last["start_dt"], last["end_dt"])
                    b_win = _window(
                        df2,
                        bi_list[p2["idxs"][0]]["start_dt"],
                        bi_list[p2["idxs"][-1]]["end_dt"],
                    )
                    if not (a_win.empty or c_win.empty or b_win.empty):
                        dif_a_max = float(a_win["_dif"].max())
                        area_a, area_c = _red_area(a_win), _red_area(c_win)
                        if (dif_a_max > 0
                                and float(b_win["_dif"].min()) <= ZERO_PULLBACK_RATIO * dif_a_max
                                and area_a > 0 and area_c < DIV_AREA_RATIO * area_a):
                            return "s1"
    return None


# ─── 汇总入口 ────────────────────────────────────────────────────────────────


@dataclass
class ChanV2Signal:
    """v2 信号快照。"""
    symbol: str
    bar_count: int = 0
    signal: dict | None = None       # detect_b1/b2/b3 的返回
    sell_point: str | None = None
    vol_price_sync: bool = False
    groups: list = field(default_factory=list)


def evaluate(symbol: str, bi_list: list[dict], df: pd.DataFrame) -> ChanV2Signal:
    """对笔序列执行 v2 规则评估（买点优先级 b3 > b2 > b1）。"""
    snap = ChanV2Signal(symbol=symbol, bar_count=len(df) if df is not None else 0)
    if not bi_list or len(bi_list) < 5 or df is None or len(df) < 60:
        return snap
    try:
        groups = group_pivots(bi_list)
        df2 = _prep_macd(df)
        snap.groups = groups
        snap.signal = (
            detect_b3(bi_list, groups, df2)
            or detect_b2(bi_list, groups, df2)
            or detect_b1(bi_list, groups, df2)
        )
        snap.sell_point = detect_sell_point(bi_list, groups, df2)
        snap.vol_price_sync = compute_vol_price_sync(df)
    except Exception as e:
        logger.warning("chan_v2.evaluate_failed symbol=%s error=%s", symbol, e)
    return snap
