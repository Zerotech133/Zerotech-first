# -*- coding: utf-8 -*-
"""xq_pump 核心：会话抽象 + 常量 + 时间工具。

定位：剥掉原 xq_* 体系的治理外壳（动态代理池 / 浏览器登录 / 请求闸 /
intel_collector RightsGate），只保留「能拉到数据的最小集」：token 注入、
2s+抖动限速、403 熔断冷却、健壮 GET（沿用 xueqiu_common.xq_get）。

真实抓取需在用户侧有网络与雪球 cookie/token；本模块本身不触网、不做授权。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)  # quant_backtest/
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

# ── 端点（公开只读 GET） ──────────────────────────────────────
URL_TIMELINE = "https://xueqiu.com/v4/statuses/public_timeline_by_category.json"
URL_HOT = "https://xueqiu.com/statuses/hot/listV2.json"
URL_TIMELINE_USER = "https://xueqiu.com/statuses/user_timeline.json"

# ── 常量 ─────────────────────────────────────────────────────
THROTTLE_BASE = 2.0          # 秒/请求（与 xueqiu_common 对齐）
COUNT = 20                   # 每页条数
CATEGORIES = (102, 104, 105, 111)  # 沪深 / 港股 / 美股 / 基金

log = logging.getLogger("xq_pump")


def ms_to_dt(ms) -> str:
    """雪球 created_at 可能是毫秒时间戳或 ISO 串；统一成 'YYYY-MM-DD HH:MM:SS'（UTC）。"""
    if ms is None:
        return ""
    if isinstance(ms, (int, float)):
        try:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            return str(ms)
    s = str(ms)
    return s[:19] if len(s) >= 19 else s


class XqSession:
    """会话基类：定义增量抽取所需的最小接口。

    子类：
      - LiveSession：复用 xueqiu_common 的 token/限速/健壮 GET + pysnowball 行情；
      - FakeSession：测试用，不触网，按预设返回合成数据。
    """

    def get_json(self, url: str, params: dict) -> Optional[dict]:
        raise NotImplementedError

    def quotec(self, symbol: str) -> Optional[dict]:
        raise NotImplementedError

    def kline(self, symbol: str, period: str = "day", count: int = 240) -> Optional[dict]:
        raise NotImplementedError

    def sleep(self, base: float = THROTTLE_BASE) -> None:
        time.sleep(base)


class FakeSession(XqSession):
    """测试用：不触网，按预设页序列返回合成数据。可注入 quotes/klines。"""

    def __init__(self, pages: list = None, quotes: dict = None, klines: dict = None):
        self.pages = list(pages or [])
        self._i = 0
        self.quotes = quotes or {}
        self.klines = klines or {}
        self.calls = []  # 记录 (url, params)

    def get_json(self, url: str, params: dict) -> Optional[dict]:
        self.calls.append((url, dict(params)))
        if self._i < len(self.pages):
            p = self.pages[self._i]
            self._i += 1
            return p
        # 页取尽：返回空列表 + 无游标（触发到尽头）
        return {"list": [], "next_max_id": None}

    def quotec(self, symbol: str) -> Optional[dict]:
        return self.quotes.get(symbol, {"data": {"items": []}})

    def kline(self, symbol: str, period: str = "day", count: int = 240) -> Optional[dict]:
        return self.klines.get(symbol, {"data": {"column": [], "item": []}})

    def sleep(self, base: float = THROTTLE_BASE) -> None:
        pass  # 测试态不真实限流


class LiveSession(XqSession):
    """真实会话：复用 xueqiu_common 的 token/限速/健壮 GET + pysnowball 行情。

    构造即 bootstrap token；缺 cookie 且匿名被 WAF 拦时抛 RuntimeError（不静默）。
    注意：本会话不接入动态代理池 / 请求闸 —— 用户已裁定「不需要授权」，只抓数据。
    403 连续熔断由 xueqiu_common.xq_get 透传 XqIpBlocked，由抽取层捕获后停轮冷却。
    """

    def __init__(self, cookie: str = "", proxy: str = ""):
        self.cookie = cookie or os.environ.get("XQ_COOKIE") or ""
        self.proxy = proxy or os.environ.get("XQ_PROXY") or ""
        self._xq = None
        self._sess = None
        self._ensure_xueqiu_common()
        self._bootstrap()

    def _ensure_xueqiu_common(self) -> None:
        try:
            import xueqiu_common as xq  # 延迟 import（未装则报 err）
            self._xq = xq
        except Exception as e:  # pragma: no cover - 依赖未装
            raise RuntimeError(
                "运行真实抓取需 xueqiu_common（在 quant_backtest/ 下）；导入失败：%s" % e
            )

    def _bootstrap(self) -> None:
        import requests

        xq = self._xq
        s = requests.Session()
        if self.proxy:
            s.proxies.update({"http": self.proxy, "https": self.proxy})
        ua = getattr(xq, "UA", None) or "Mozilla/5.0 (compatible; xq_pump/0.1)"
        s.headers.update(
            {
                "User-Agent": ua,
                "Accept": "application/json",
                "Referer": "https://xueqiu.com/",
            }
        )
        if self.cookie:
            s.headers["Cookie"] = self.cookie
        # 匿名预热取 xq_a_token（已带 cookie 则跳过）
        if not self.cookie:
            try:
                tok = xq.warm_session(s)
                if tok:
                    s.cookies.set("xq_a_token", tok)
            except Exception as e:  # pragma: no cover
                log.warning("warm_session 失败（匿名通路可能失效，需 cookie）：%s", str(e)[:80])
        self._sess = s

    def get_json(self, url: str, params: dict) -> Optional[dict]:
        try:
            return self._xq.xq_get(self._sess, url, params)
        except self._xq.XqIpBlocked:
            raise  # 透传，由抽取层熔断

    def quotec(self, symbol: str) -> Optional[dict]:
        import pysnowball as ball  # 延迟 import

        return ball.quotec(symbol)

    def kline(self, symbol: str, period: str = "day", count: int = 240) -> Optional[dict]:
        import pysnowball as ball  # 延迟 import

        return ball.kline(symbol, period=period, count=count)

    def sleep(self, base: float = THROTTLE_BASE) -> None:
        self._xq.jitter_sleep(base)
