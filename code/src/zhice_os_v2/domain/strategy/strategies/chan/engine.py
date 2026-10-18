"""缠论结构引擎 — czsc 封装 + 中枢/买卖点/背驰纯函数。

自 V1 modules.candidate_engine.strategies.chan.engine 移植（语义原样保留），
仅替换 logging 与内部包引用为本项目路径。
czsc 以纯 Python 实现运行（未安装 rs_czsc 时 czsc.core 自动回退 czsc.py 实现）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

MIN_BARS_REQUIRED = 60          # 至少60根日K才做结构分析
VOL_PRICE_SYNC_N = 5            # 量价同步检测窗口
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9


# ─── 中枢与买卖点（移植：缠论全闭环系统 czsc_adapter，纯函数） ─────────────


def build_zs_list(bi_objects: list) -> list[dict]:
    """从 czsc BI 对象列表构建中枢序列（保留有效中枢，至少3笔）。"""
    if not bi_objects or len(bi_objects) < 3:
        return []
    try:
        from czsc.utils.sig import get_zs_seq

        result = []
        for zs in get_zs_seq(bi_objects):
            if not zs.is_valid or len(zs.bis) < 3:
                continue
            result.append({
                "zg": float(zs.zg), "zd": float(zs.zd),
                "gg": float(zs.gg), "dd": float(zs.dd),
                "start_dt": str(zs.sdt), "end_dt": str(zs.edt),
                "bi_count": len(zs.bis),
            })
        return result
    except Exception as e:
        logger.warning("chan.zs_build_failed: %s", e)
        return []


def find_prev_zs(zs_list: list[dict], bi_start_dt: str) -> dict | None:
    """找最后一个在本笔开始前已完成的中枢。"""
    if not zs_list:
        return None
    for zs in reversed(zs_list):
        end_dt = zs.get("end_dt", "")
        if end_dt and bi_start_dt and end_dt < bi_start_dt:
            return zs
    return zs_list[-1]


def detect_bs_point(bi_list: list[dict], zs_list: list[dict]) -> str | None:
    """几何法买卖点：看最后一笔起点相对前中枢 [zd, zg] 的位置。

    最后一笔向上: low < zd → b1 | zd <= low < zg → b2 | low >= zg → b3
    最后一笔向下: high > zg → s1 | zd <= high <= zg → s2 | high < zd → s3
    """
    if not bi_list:
        return None
    last_bi = bi_list[-1]
    direction = last_bi.get("direction", "")

    if "向上" in direction:
        bi_low = float(last_bi.get("low", 0))
        prev_zs = find_prev_zs(zs_list, last_bi.get("start_dt", ""))
        if not prev_zs or float(prev_zs.get("zg", 0)) == 0:
            return "b1"
        if bi_low >= float(prev_zs["zg"]):
            return "b3"
        if bi_low >= float(prev_zs["zd"]):
            return "b2"
        return "b1"

    if "向下" in direction:
        bi_high = float(last_bi.get("high", 0))
        prev_zs = find_prev_zs(zs_list, last_bi.get("start_dt", ""))
        if not prev_zs or float(prev_zs.get("zg", 0)) == 0:
            return "s1"
        if bi_high > float(prev_zs["zg"]):
            return "s1"
        if bi_high >= float(prev_zs["zd"]):
            return "s2"
        return "s3"

    return None


def compute_divergence_strength(bi_list: list[dict], df: pd.DataFrame | None = None) -> str:
    """振幅+量能背驰（移植原实现）：bi[-1] 与 bi[-3] 同向比较。

    振幅比 <0.70 且量能比 <0.80 → strong；满足其一（振幅<0.90 或量缩）→ medium；否则 weak。
    """
    if len(bi_list) < 3:
        return "weak"
    last_bi, prev_bi = bi_list[-1], bi_list[-3]
    last_power = float(last_bi.get("high", 0)) - float(last_bi.get("low", 0))
    prev_power = float(prev_bi.get("high", 0)) - float(prev_bi.get("low", 0))
    if prev_power <= 0:
        return "weak"

    price_ratio = last_power / prev_power
    price_strong = price_ratio < 0.70
    price_any = price_ratio < 0.90

    vol_div = False
    if df is not None and len(df) >= 4:
        try:
            last_vols = _bi_slice(df, last_bi)["volume"]
            prev_vols = _bi_slice(df, prev_bi)["volume"]
            if len(last_vols) and len(prev_vols):
                vol_div = (last_vols.mean() / prev_vols.mean()) < 0.80
        except Exception:
            pass

    if price_strong and vol_div:
        return "strong"
    if price_any or vol_div:
        return "medium"
    return "weak"


def _bi_slice(df: pd.DataFrame, bi: dict) -> pd.DataFrame:
    """截取一笔时间区间[start_dt, end_dt]内的K线（闭区间）。

    以 pd.to_datetime 做时间戳比较，避免 astype(str) 字典序坑：pandas 3.x 下
    午夜日线 dt.astype(str) 产出 "2024-01-05"(10字符)，而笔端点 start_dt=
    str(Timestamp)="2024-01-05 00:00:00"(19字符)，字典序 "2024-01-05" >=
    "2024-01-05 00:00:00" 为 False → 首根(笔分型端点/局部极值)被误丢。
    """
    start = pd.to_datetime(bi.get("start_dt"))
    end = pd.to_datetime(bi.get("end_dt"))
    dts = pd.to_datetime(df["dt"])
    return df[(dts >= start) & (dts <= end)]


def compute_vol_price_sync(df: pd.DataFrame, n: int = VOL_PRICE_SYNC_N) -> bool:
    """最近N根量价同步（价涨量增/价跌量缩比例≥60%）。"""
    if len(df) < n + 1:
        return False
    recent = df.tail(n + 1)
    closes = recent["close"].to_numpy()
    vols = recent["volume"].to_numpy()
    agree = sum(
        1 for i in range(1, len(closes))
        if (closes[i] > closes[i - 1] and vols[i] > vols[i - 1])
        or (closes[i] < closes[i - 1] and vols[i] < vols[i - 1])
    )
    return agree >= max(1, int(n * 0.6))


# ─── MACD 面积背驰（V1 项目补齐，三处原实现均缺失） ──────────────────────────


def compute_macd_hist(closes: pd.Series) -> pd.Series:
    """标准 MACD 柱（DIF-DEA）×2。"""
    ema_fast = closes.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = closes.ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return (dif - dea) * 2


def macd_bottom_divergence(bi_list: list[dict], df: pd.DataFrame) -> bool:
    """真 MACD 底背驰：最近两个下行笔，价格创新低（或近似）但 MACD 绿柱面积明显收缩。

    条件：
      1. 最近两个向下笔 down[-1] 的低点 <= down[-2] 低点 × 1.02（新低或双底附近）
      2. down[-1] 期间 MACD 负柱面积绝对值 < down[-2] 的 0.8 倍（动能衰竭）
    """
    downs = [b for b in bi_list if "向下" in b.get("direction", "")]
    if len(downs) < 2 or df is None or len(df) < MACD_SLOW + MACD_SIGNAL:
        return False
    d_last, d_prev = downs[-1], downs[-2]
    if float(d_last.get("low", 0)) > float(d_prev.get("low", 0)) * 1.02:
        return False
    try:
        hist = compute_macd_hist(df["close"].astype(float))
        df2 = df.assign(_hist=hist.to_numpy())
        area_last = _bi_slice(df2, d_last)["_hist"].clip(upper=0).sum()
        area_prev = _bi_slice(df2, d_prev)["_hist"].clip(upper=0).sum()
        if area_prev >= 0:  # 前一下行笔没有绿柱，无从比较
            return False
        return bool(abs(area_last) < abs(area_prev) * 0.8)
    except Exception as e:
        logger.debug("chan.macd_div_failed: %s", e)
        return False


# ─── 多级别联立：周线大级别方向过滤（"大级别定方向"） ─────────────────────────

WEEKLY_MIN_BARS = 30            # 周线不足30根 → 放行（早期历史不足不误拦）


def _weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """日K→周K(以周五收盘定周) OHLCV 聚合。df 需含 dt/open/high/low/close/volume(升序)。"""
    d = df[["dt", "open", "high", "low", "close", "volume"]].copy()
    d["dt"] = pd.to_datetime(d["dt"])
    wk = (d.set_index("dt")
            .resample("W-FRI")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["close"]))
    return wk.reset_index()


def weekly_trend_permits_long(df: pd.DataFrame) -> bool:
    """大级别(周线)方向门：周线处于下跌趋势时禁止做多，其余放行。

    缠论"大级别定方向、小级别找买点"——日线三类买点若逆周线大势，胜率显著下降。
    经典稳健判定（阴跌/加速跌都能覆盖，避免只认加速下跌漏掉慢熊）：
      禁买 ⇔ 周线收盘 < 周线EMA(慢) 且 周线EMA(慢)本身在下行（近5周走低）。
    周线历史 < WEEKLY_MIN_BARS 根 → 放行（不因缺数据误拦）。
    """
    wk = _weekly_bars(df)
    if len(wk) < WEEKLY_MIN_BARS:
        return True
    closes = wk["close"].astype(float)
    ema_slow = closes.ewm(span=MACD_SLOW, adjust=False).mean()
    below_ema = closes.iloc[-1] < ema_slow.iloc[-1]
    ema_falling = ema_slow.iloc[-1] < ema_slow.iloc[-5]
    return not (below_ema and ema_falling)


# ─── czsc 结构计算 ──────────────────────────────────────────────────────────


@dataclass
class ChanSnapshot:
    """单只股票的缠论结构快照。"""
    symbol: str
    bar_count: int = 0
    bi_list: list = field(default_factory=list)
    zs_list: list = field(default_factory=list)
    bs_point: str | None = None
    last_bi_direction: str | None = None
    divergence: str = "weak"
    macd_divergence: bool = False
    vol_price_sync: bool = False
    prev_zs: dict | None = None


def extract_bi_list(symbol: str, df: pd.DataFrame) -> tuple[list[dict], object]:
    """czsc 笔提取（结构层公共入口，v1/v2 共用）。

    df 必需列: dt(datetime), open, high, low, close, volume, amount（时间升序）。
    返回 (笔字典列表, CZSC对象)；笔字典: direction/high/low/start_dt/end_dt。
    """
    from czsc import CZSC, Freq, RawBar

    raw_bars = [
        RawBar(
            symbol=symbol, id=i, freq=Freq.D, dt=r.dt,
            open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            vol=float(r.volume), amount=float(r.amount),
        )
        for i, r in enumerate(df.itertuples())
    ]
    c = CZSC(raw_bars)
    bi_list = []
    for bi in c.bi_list:
        try:
            bi_list.append({
                "direction": str(bi.direction),
                "high": float(bi.high), "low": float(bi.low),
                "start_dt": str(bi.fx_a.dt), "end_dt": str(bi.fx_b.dt),
            })
        except Exception:
            continue
    return bi_list, c


def analyze(symbol: str, df: pd.DataFrame) -> ChanSnapshot:
    """对单只股票的日K序列做缠论结构分析。

    df 必需列: dt(datetime), open, high, low, close, volume, amount（时间升序）
    """
    if df is None or len(df) < MIN_BARS_REQUIRED:
        return ChanSnapshot(symbol=symbol, bar_count=0 if df is None else len(df))

    try:
        bi_list, c = extract_bi_list(symbol, df)
        zs_list = build_zs_list(c.bi_list)  # type: ignore[attr-defined]
        del c

        bs_point = detect_bs_point(bi_list, zs_list)
        prev_zs = find_prev_zs(zs_list, bi_list[-1].get("start_dt", "")) if bi_list else None

        return ChanSnapshot(
            symbol=symbol,
            bar_count=len(df),
            bi_list=bi_list,
            zs_list=zs_list,
            bs_point=bs_point,
            last_bi_direction=bi_list[-1].get("direction") if bi_list else None,
            divergence=compute_divergence_strength(bi_list, df),
            macd_divergence=macd_bottom_divergence(bi_list, df),
            vol_price_sync=compute_vol_price_sync(df),
            prev_zs=prev_zs,
        )
    except Exception as e:
        logger.warning("chan.analyze_failed symbol=%s error=%s", symbol, e)
        return ChanSnapshot(symbol=symbol, bar_count=len(df))
