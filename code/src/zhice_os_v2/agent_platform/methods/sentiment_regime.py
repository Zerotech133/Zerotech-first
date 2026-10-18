"""sentiment_regime 方法插件（T07）— 情绪周期判定：高潮减仓 / 冰点观望。

源文档 org-style-playbook-v0.1.md 第二节：情绪 5 特征（涨停家数、炸板率、
连板高度、成交额分位、赚钱效应）。首版实现前三特征的硬阈值判定 +
SentimentTracker 进程内 5 日滑窗（daemon 常驻自然积累；单点无历史时退化
为仅静态阈值分支）。成交额分位/红盘占比依赖全市场列表接口，静态可用。

数据获取：fetch_sentiment_snapshot()（东财 ZT/ZB 池 + 全市场列表聚合，
trust_env=False 防沙盒代理劫持，60s 进程内节流）。

投票语义（阈值全部可 params_snapshot 覆盖）：
- 高潮信号 ≥2 → SELL 0.5（reasons 列出触发的特征名）
- 冰点（zt<30 且 red_ratio<35%）→ WAIT 0.6（risk_off 语义，供 regime_veto）
- 其余 → HOLD 0.4；sentiment=None → WAIT 0.3（数据不可用）
"""
from __future__ import annotations

import logging
import time
from collections import deque

import pandas as pd

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)

logger = logging.getLogger(__name__)

# ── 默认阈值（源文档口径；全部可被 params 覆盖） ─────────────────────────────
ZT_DROP_PCT = 0.20          # 涨停家数环比前值回落 ≥20%
ZB_RATE = 0.30              # 炸板率 zb/(zt+zb) > 0.3
LB_BREAK = 2                # 连板高度较 5 日峰值断层（降 ≥2 板）
ICEPOINT_ZT = 30            # 冰点涨停家数下限
ICEPOINT_RED_RATIO = 0.35   # 冰点红盘占比下限
TRACKER_WINDOW = 5          # 滑窗天数（daemon 常驻积累）

_EASTMONEY_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_ZT_POOL_URL = (
    "https://push2ex.eastmoney.com/getTopicZTPool"
    "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
    "&Pageindex=0&pagesize=320&sort=fbt:asc&date={date}"
)
_ZB_POOL_URL = (
    "https://push2ex.eastmoney.com/getTopicZBPool"
    "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
    "&Pageindex=0&pagesize=320&sort=fbt:asc&date={date}"
)
_LIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f3,f6"
)

_FETCH_TTL_S = 60.0         # daemon 每 cycle 调用节流
_fetch_cache: dict = {"ts": 0.0, "snap": None}


class SentimentTracker:
    """进程内存 5 日滑窗（涨停家数/炸板/连板高度历史；回测由引擎预注入）。

    daemon 常驻自然积累；单点无历史时高潮判定退化为静态阈值分支。
    """

    def __init__(self, window: int = TRACKER_WINDOW) -> None:
        self.window = window
        self.zt_history: deque[float] = deque(maxlen=window)
        self.lb_history: deque[float] = deque(maxlen=window)
        self._seen_dates: set[str] = set()

    def observe(self, sentiment: dict, date_key: str) -> None:
        """记录一日快照（同日重复调用幂等跳过）。"""
        if date_key in self._seen_dates or not sentiment:
            return
        self._seen_dates.add(date_key)
        if len(self._seen_dates) > 64:      # 防 key 无限增长
            self._seen_dates = set(list(self._seen_dates)[-32:])
        zt = sentiment.get("zt_count")
        lb = sentiment.get("max_lb")
        if zt is not None:
            self.zt_history.append(float(zt))
        if lb is not None:
            self.lb_history.append(float(lb))

    def prev_zt(self) -> float | None:
        """前一值（环比回落判定）。"""
        return self.zt_history[-1] if len(self.zt_history) >= 1 else None

    def peak_lb_5d(self) -> float | None:
        """近 5 日（含当前前值序列）连板高度峰值。"""
        return max(self.lb_history) if self.lb_history else None


class SentimentRegimeMethod(WatchMethod):
    """情绪周期方法：高潮（≥2 特征）→ SELL；冰点 → WAIT(risk_off)；其余 HOLD。"""

    method_id = "sentiment_regime"
    display_name = "情绪周期"
    weight = 1.0

    def __init__(self, tracker: SentimentTracker | None = None) -> None:
        self.tracker = tracker or SentimentTracker()
        self.p = {
            "zt_drop_pct": ZT_DROP_PCT,
            "zb_rate": ZB_RATE,
            "lb_break": LB_BREAK,
            "icepoint_zt": ICEPOINT_ZT,
            "icepoint_red_ratio": ICEPOINT_RED_RATIO,
        }

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        sent = ctx.sentiment
        if not sent:
            return MethodVote(self.method_id, "WAIT", 0.3,
                              ["情绪数据不可用（None）"], params_snapshot=self.params())

        # 先读取历史基线，再记入当前快照；否则当前值会污染环比/峰值比较。
        # 同 ctx 当日重复评估由 tracker.observe() 幂等处理。
        prev_zt = self.tracker.prev_zt()
        peak_lb = self.tracker.peak_lb_5d()
        date_key = self._date_key(ctx)
        if date_key:
            self.tracker.observe(sent, date_key)

        zt = _as_float(sent.get("zt_count"))
        zb = _as_float(sent.get("zb_count"))
        max_lb = _as_float(sent.get("max_lb"))
        red = _as_float(sent.get("red_count"))
        # 全市场总数（近似）：红盘 + 平/绿盘；缺 red 时用 zt+zb 下限不可靠 → None
        red_ratio = None
        if red is not None and red >= 0 and sent.get("total_count"):
            red_ratio = red / float(sent["total_count"])

        reasons: list[str] = []

        # 高潮特征 ①：涨停家数环比前值回落 ≥ zt_drop_pct
        if zt is not None and prev_zt is not None and prev_zt > 0:
            if (prev_zt - zt) / prev_zt >= self.p["zt_drop_pct"]:
                reasons.append(f"涨停家数回落{((prev_zt - zt) / prev_zt):.0%}"
                               f"（{prev_zt:.0f}→{zt:.0f}）")

        # 高潮特征 ②：炸板率 > zb_rate
        if zt is not None and zb is not None and (zt + zb) > 0:
            rate = zb / (zt + zb)
            if rate > self.p["zb_rate"]:
                reasons.append(f"炸板率{rate:.0%}>{self.p['zb_rate']:.0%}"
                               f"（zb={zb:.0f}/zt={zt:.0f}）")

        # 高潮特征 ③：连板高度较 5 日峰值断层 ≥ lb_break 板
        if max_lb is not None and peak_lb is not None:
            if (peak_lb - max_lb) >= self.p["lb_break"]:
                reasons.append(f"连板高度断层{peak_lb - max_lb:.0f}板"
                               f"（峰值{peak_lb:.0f}→现{max_lb:.0f}）")

        # TODO(成交额分位): 全市场成交额近 N 日分位需要日级历史序列，首版只
        # 透传当日总额（sent['amount']），分位特征待 tracker 增量积累后接入。
        if reasons and len(reasons) >= 2:
            return MethodVote(self.method_id, "SELL", 0.5, reasons,
                              params_snapshot=self.params())

        # 冰点：zt < icepoint_zt 且 red_ratio < icepoint_red_ratio
        if (zt is not None and zt < self.p["icepoint_zt"]
                and red_ratio is not None and red_ratio < self.p["icepoint_red_ratio"]):
            return MethodVote(
                self.method_id, "WAIT", 0.6,
                [f"情绪冰点：涨停{zt:.0f}<{self.p['icepoint_zt']}"
                 f" 且红盘占比{red_ratio:.0%}<{self.p['icepoint_red_ratio']:.0%}"
                 "（risk_off 语义）"],
                params_snapshot=self.params())

        return MethodVote(self.method_id, "HOLD", 0.4,
                          ["情绪中性（高潮特征不足 2 项且非冰点）"],
                          params_snapshot=self.params())

    def _date_key(self, ctx: WatchContext) -> str:
        df = ctx.bars_df
        if df is not None and len(df) and "dt" in df.columns:
            return str(pd.Timestamp(df["dt"].iloc[-1]).date())
        return ""

    def params(self) -> dict:
        return {**super().params(), **self.p, "window": self.tracker.window}


def _as_float(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 数据获取（东财三接口聚合；requests trust_env=False） ─────────────────────
def _http_get_json(url: str, timeout: float = 10.0):
    import requests

    resp = requests.get(url, timeout=timeout, headers={"User-Agent": _EASTMONEY_UA},
                        trust_env=False, proxies={"http": None, "https": None})
    resp.raise_for_status()
    return resp.json()


def _fetch_pool(url_tpl: str, date_s: str) -> tuple[float | None, float | None]:
    """ZT/ZB 池 → (家数, 连板高度峰值)。失败返 (None, None)。"""
    try:
        data = _http_get_json(url_tpl.format(date=date_s))
        payload = (data or {}).get("data") or {}
        tc = _as_float(payload.get("tc"))
        pool = payload.get("pool") or []
        lbc = [_as_float(p.get("lbc")) for p in pool if p.get("lbc") is not None]
        max_lb = max(lbc) if lbc else None
        return tc, max_lb
    except Exception as exc:
        logger.warning("东财池接口失败 %s: %s", url_tpl.split("?")[0], exc)
        return None, None


def _fetch_market_overview() -> tuple[float | None, float | None]:
    """全市场列表 → (红盘家数, 总成交额)。失败返 (None, None)。"""
    try:
        data = _http_get_json(_LIST_URL)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        red = 0
        amount = 0.0
        n = 0
        for row in diff:
            f3 = row.get("f3")
            if f3 is None or f3 == "-":
                continue
            n += 1
            try:
                f3f = float(f3)
                if f3f > 0:
                    red += 1
                f6 = row.get("f6")
                if f6 not in (None, "-"):
                    amount += float(f6)
            except (TypeError, ValueError):
                continue
        return (float(red), amount) if n else (None, None)
    except Exception as exc:
        logger.warning("东财全市场接口失败: %s", exc)
        return None, None


def fetch_sentiment_snapshot(now=None, *, force: bool = False) -> dict | None:
    """聚合三接口 → {zt_count, zb_count, max_lb, red_count, amount, total_count, date}。

    60s 进程内节流（daemon 每 cycle 调用）；任一核心接口（ZT 池）失败返 None
    调用方降级；ZB/列表接口失败仅缺对应键。date 键用当日（东财口径 YYYYMMDD）。
    """
    ts = time.monotonic()
    if not force and _fetch_cache["snap"] is not None and \
            (ts - _fetch_cache["ts"]) < _FETCH_TTL_S:
        return _fetch_cache["snap"]

    from datetime import UTC, datetime

    now = now or datetime.now(UTC)
    date_s = f"{now:%Y%m%d}"

    zt, max_lb = _fetch_pool(_ZT_POOL_URL, date_s)
    if zt is None:                      # 核心接口失败 → 整体不可用
        return None
    zb, _ = _fetch_pool(_ZB_POOL_URL, date_s)
    red, amount = _fetch_market_overview()

    snap = {
        "zt_count": zt,
        "zb_count": zb if zb is not None else 0.0,
        "max_lb": max_lb if max_lb is not None else 0.0,
        "red_count": red,
        "amount": amount,
        "date": date_s,
        "synthetic": False,
    }
    _fetch_cache["ts"] = ts
    _fetch_cache["snap"] = snap
    return snap
