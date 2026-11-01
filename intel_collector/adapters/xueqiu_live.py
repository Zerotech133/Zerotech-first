# -*- coding: utf-8 -*-
"""雪球真实 fetcher（闸内代码就绪，默认禁用）——⑤ 第二层收敛：传输委托 xq_pump

收敛口径（2026-11-01 归档决策 ⑤ 第二层）：
  - **HTTP 传输栈单一真源**：会话构建/头/UA/代理/匿名 token 预热/健壮 GET
    全部委托 `xq_pump.core.LiveSession`（其底层复用 xueqiu_common.xq_get），
    intel_collector 不再自建 requests 栈——消除传输层双写；
  - 本模块保留**治理语义**（不可委托）：
      1) RightsGate 语境下的 arming 守卫（XQ_LIVE_ARMED=1，默认禁用绝不误触网）；
      2) manifest 状态分账（ok/empty/failed/ip_blocked → 失败可见，被封≠删除）；
      3) 403 连续熔断（阈值 3、冷却 600s）——xq_pump 透传 XqIpBlocked，本层捕获计数；
      4) >=2s 限速（throttle，与 XqAdapter.THROTTLE 对齐）。

合规底线：只读 GET、不登录写操作、失败可见；端点/cookie 均来自环境变量或入参，
不硬编码任何真实凭据。未对真实接口做连通性冒烟（待凭据后由真实环境验证）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

# 默认端点（可被环境变量覆盖）。与 xq_pump 端点常量对齐，但允许 env 覆盖做 A/B。
_DEFAULT_TIMELINE = "https://xueqiu.com/v4/statuses/public_timeline_by_category.json"
_DEFAULT_COMMENTS = "https://xueqiu.com/v4/statuses/search.json"
_CIRCUIT_403_THRESHOLD = 3          # 连续 403 达到此数即熔断
_CIRCUIT_COOLDOWN_S = 600           # 熔断后冷却时长（秒）


class CircuitOpenError(RuntimeError):
    """雪球 403 连续熔断：需冷却后由人工/调度重试。"""


def _load_xq_pump():
    """惰性加载 xq_pump（兄弟目录 quant_backtest/ 挂 sys.path）。"""
    qb = Path(__file__).resolve().parents[2] / "quant_backtest"
    if str(qb) not in sys.path:
        sys.path.insert(0, str(qb))
    from xq_pump import core as xq_core
    return xq_core


class LiveXqFetcher:
    """真实雪球 fetcher：传输委托 xq_pump.LiveSession，本层只做治理壳。

    构造即要求 arming 检查；未 armed 直接抛 RuntimeError，确保任何代码路径
    都无法"顺手"触网。armed 且有 cookie 时才构建底层 LiveSession。
    """

    def __init__(
        self,
        *,
        cookie: Optional[str] = None,
        proxy: Optional[str] = None,
        timeline_url: Optional[str] = None,
        comments_url: Optional[str] = None,
        throttle: float = 2.0,
        session=None,          # 测试注入（FakeSession）；生产走 xq_pump.LiveSession
    ):
        # arming 守卫：必须由显式环境变量开启，避免误用
        if os.environ.get("XQ_LIVE_ARMED") != "1":
            raise RuntimeError(
                "未授权：真实雪球 fetcher 默认禁用。需 RightsGate 审批通过且设置 "
                "XQ_LIVE_ARMED=1 后方可启用。"
            )
        self.cookie = cookie or os.environ.get("XQ_COOKIE") or ""
        self.proxy = proxy or os.environ.get("XQ_PROXY")
        self.timeline_url = timeline_url or os.environ.get("XQ_TIMELINE_URL", _DEFAULT_TIMELINE)
        self.comments_url = comments_url or os.environ.get("XQ_COMMENTS_URL", _DEFAULT_COMMENTS)
        self.throttle = throttle
        self._consecutive_403 = 0
        self._circuit_opened_at: Optional[float] = None
        if not self.cookie:
            raise RuntimeError("缺少 XQ_COOKIE（雪球会话 cookie），真实 fetcher 无法鉴权。")
        # 传输委托：session 注入优先（测试），否则构建 xq_pump.LiveSession
        if session is not None:
            self._session = session
        else:
            xq_core = _load_xq_pump()
            self._session = xq_core.LiveSession(cookie=self.cookie, proxy=self.proxy or "")

    @classmethod
    def check_armed(cls) -> bool:
        return os.environ.get("XQ_LIVE_ARMED") == "1"

    # ---- 熔断（治理语义，本层持有） ----

    def _circuit_guard(self) -> None:
        if self._circuit_opened_at is not None:
            waited = time.time() - self._circuit_opened_at
            if waited < _CIRCUIT_COOLDOWN_S:
                raise CircuitOpenError(
                    f"雪球 403 熔断冷却中（剩余 {int(_CIRCUIT_COOLDOWN_S - waited)}s），"
                    f"连续 403 已达 {_CIRCUIT_403_THRESHOLD} 次。"
                )
            # 冷却结束，复位
            self._circuit_opened_at = None
            self._consecutive_403 = 0

    def _note_403(self) -> None:
        self._consecutive_403 += 1
        if self._consecutive_403 >= _CIRCUIT_403_THRESHOLD:
            self._circuit_opened_at = time.time()

    # ---- 传输（委托 xq_pump session） ----

    def _call(self, url: str, params: dict[str, Any]) -> dict:
        self._circuit_guard()
        if self._consecutive_403:
            # 熔断计数已在途（未达阈值）：403 压力下仍让底层尽力，失败可见
            pass
        try:
            data = self._session.get_json(url, params)
        except Exception as e:  # 含 xq_pump 透传的 XqIpBlocked → 计数熔断
            name = type(e).__name__
            if "IpBlocked" in name or "403" in str(e):
                self._note_403()
                if self._circuit_opened_at is not None:
                    return {"status": "ip_blocked", "total": 0, "list": [],
                            "_error": f"circuit_open:403x{self._consecutive_403}"}
                return {"status": "ip_blocked", "total": 0, "list": [],
                        "_error": f"403 (consecutive {self._consecutive_403})"}
            return {"status": "failed", "total": 0, "list": [], "_error": f"net:{e}"}
        if data is None:
            return {"status": "failed", "total": 0, "list": [], "_error": "no_data"}
        # 雪球返回顶层即 list/items/分页字段；适配 XqAdapter 期望的 {"status","total","list"}
        items = (data or {}).get("list") or (data or {}).get("items") or \
            (data or {}).get("statuses") or []
        total = (data or {}).get("total") or len(items)
        return {"status": "ok", "total": total, "list": items}

    def __call__(
        self,
        target: str,
        *,
        mode: str = "timeline",
        category: int = 102,
        page: int = 1,
        symbols: Optional[list[str]] = None,
    ) -> dict:
        # 限速（与 XqAdapter.THROTTLE 对齐；委托 session 的 jitter 之外再显式 throttle）
        if self.throttle and self.throttle > 0:
            time.sleep(self.throttle)
        if mode == "comments":
            url = self.comments_url
            params = {"symbol": target, "count": 20, "page": page,
                      "source": "user", "sort": "time"}
        else:
            url = self.timeline_url
            params = {
                "category": category,
                "count": 20,
                "page": 1,
                "source": "user",
                "max_id": None,
            }
            params.pop("max_id", None) if params.get("max_id") is None else None
            if symbols:
                params["symbol"] = symbols[0]
        return self._call(url, params)


def build_live_fetcher() -> Callable[..., dict]:
    """工厂：返回已 armed 的真实 fetcher（供编排层在闸门通过后注入 XqAdapter）。

    调用前编排层须已通过 RightsGate.check(source_spec, uc) 对绑定用途授权。
    未 armed 时本函数直接抛 RuntimeError。
    """
    return LiveXqFetcher()
