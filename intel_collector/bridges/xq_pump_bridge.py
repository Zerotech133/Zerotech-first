# -*- coding: utf-8 -*-
"""⑤ 第三层：调度/存储接线——xq_pump 抽取结果桥接进 intel_collector 治理体系。

收敛口径（2026-11-01 归档决策 ⑤ 第三层）：
  - **数据面**（行级增量落库）由 xq_pump 负责（xq_pump.db / xueqiu_info.db，
    幂等 upsert + seen 去重 + 游标断点）；
  - **治理面**（Observation 观测、RightsGate 用途绑定、SLA/PII/反馈环）由
    intel_collector 负责；
  - 本桥 = 两个世界的接线器：实现 CronRunner 的 CollectDispatcher 协议，
    一次 collect_source("xueqiu") 同时完成：
      1) 调 xq_pump 抽取器（可注入 session，测试用 FakeSession，生产用
         xq_pump.LiveSession——传输真源）；
      2) 把本次运行的**页级摘要**包装成 Observation 落 ObservationStore
         （entity_type=xq_feed/xq_comments_page；payload 含 new/requests/
         per_symbol 分账；失败可见：exception→INCOMPLETE）；
      3) 返回 {ok, n, reason} 供 Scheduler.mark_collected 推进调度。

安全：本桥不绕 RightsGate——Observation.allowed_use_cases 只写源 spec 中
已授权且闸门通过的用途；不触网（session 由调用方注入，armed 守卫在
xq_pump/xueqiu_live 各自持有）。
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.contracts import Observation, SourceClock, Status
from core.rights_gate import RightsGate
from registry.source_registry import SourceRegistry
from store.obs_store import ObservationStore

_XQ_PUMP_LOADED = False


def _load_xq_pump():
    """惰性加载 xq_pump（兄弟目录 quant_backtest/ 挂 sys.path）。"""
    global _XQ_PUMP_LOADED
    qb = Path(__file__).resolve().parents[2] / "quant_backtest"
    if str(qb) not in sys.path:
        sys.path.insert(0, str(qb))
    _XQ_PUMP_LOADED = True
    from xq_pump import extractors
    from xq_pump import cursor as xq_cursor
    from xq_pump import store as xq_store
    return extractors, xq_cursor, xq_store


class XqPumpDispatcher:
    """CollectDispatcher 实现：驱动 xq_pump 抽取 + 产出治理 Observation。

    参数：
      registry/store/gate：intel_collector 三件套（源档/观测库/权利闸）。
      session：xq_pump 会话（XqSession 协议）。None=默认 FakeSession（演示/测试，
               空页零新增但不崩）；生产应注入 xq_pump.core.LiveSession。
      db_path/cursor_root：xq_pump 数据根（缺省用 xq_pump 默认，见其 store.py）。
      extractor_types：本次派发要跑的抽取类型，默认 ("posts",)。
      max_new/max_pages：透传 xq_pump 抽取器。
    """

    def __init__(
        self,
        registry: SourceRegistry,
        store: ObservationStore,
        gate: Optional[RightsGate] = None,
        *,
        session=None,
        db_path: Optional[str] = None,
        cursor_root: Optional[str] = None,
        extractor_types: tuple = ("posts",),
        max_new: int = 500,
        max_pages: int = 30,
        categories: tuple = (102,),
        symbols: tuple = ("SH600519",),
    ):
        self.registry = registry
        self.store = store
        self.gate = gate or RightsGate()
        self.session = session
        self.db_path = db_path
        self.cursor_root = cursor_root
        self.extractor_types = tuple(extractor_types)
        self.max_new = max_new
        self.max_pages = max_pages
        self.categories = tuple(categories)
        self.symbols = tuple(symbols)

    # ---- CollectDispatcher 协议 ----

    def collect_source(self, source_id: str, now: datetime) -> dict:
        if source_id != "xueqiu":
            return {"ok": False, "n": 0, "reason": f"bridge 只接 xueqiu，收到 {source_id}"}
        spec = self.registry.get("xueqiu")
        if spec is None:
            return {"ok": False, "n": 0, "reason": "xueqiu 未登记"}
        if not spec.enabled:
            return {"ok": False, "n": 0, "reason": "xueqiu 已禁用（熔断/复核）"}

        # 用途绑定（闸门通过的才写入 Observation）
        bound = [
            uc for uc in ("internal_retention", "cleaning", "controlled_query")
            if self.gate.check(spec, uc).allowed
        ]

        extractors, xq_cursor, xq_store = _load_xq_pump()
        if self.session is None:
            from xq_pump.core import FakeSession
            session = FakeSession()  # 默认演示态：空页零新增（不触网、不静默）
        else:
            session = self.session

        results: dict[str, dict] = {}
        total_new = 0
        ok = True
        err = ""
        try:
            if self.db_path or self.cursor_root:
                store = xq_store.Store(self.db_path) if self.db_path else xq_store.Store()
                cursor = xq_cursor.CursorStore(self.cursor_root) if self.cursor_root else xq_cursor.CursorStore("xq_pump_cursors_default")
            else:
                import tempfile, os
                tmp = tempfile.mkdtemp(prefix="xq_pump_bridge_")
                store = xq_store.Store(os.path.join(tmp, "bridge.db"))
                cursor = xq_cursor.CursorStore(os.path.join(tmp, "cursors"))
            try:
                for etype in self.extractor_types:
                    if etype == "posts":
                        r = extractors.collect_posts(
                            session, store, cursor,
                            categories=self.categories,
                            max_new=self.max_new, max_pages_per_cat=self.max_pages)
                    elif etype == "hot":
                        r = extractors.collect_hot(
                            session, store, cursor,
                            max_new=self.max_new, max_pages=self.max_pages)
                    elif etype == "comments":
                        r = extractors.collect_comments(
                            session, store, cursor,
                            symbols=list(self.symbols),
                            max_pages=self.max_pages, max_new=self.max_new)
                    else:
                        continue
                    results[etype] = r
                    total_new += int(r.get("new", 0))
                    store.close()
                    # 每类型独立 store/cursor：重新开（简化，避免共享连接状态）
                    if etype != self.extractor_types[-1]:
                        import os as _os, tempfile as _tf
                        tmp2 = _tf.mkdtemp(prefix="xq_pump_bridge_")
                        store = xq_store.Store(_os.path.join(tmp2, "bridge.db"))
                        cursor = xq_cursor.CursorStore(_os.path.join(tmp2, "cursors"))
            finally:
                try:
                    store.close()
                except Exception:
                    pass
        except Exception as e:  # 失败可见
            ok = False
            err = f"{type(e).__name__}: {e}"
            results = {"error": {"trace": traceback.format_exc()[-400:]}}

        # 治理面 Observation：页级运行摘要（不做逐行镜像——行级归 xq_pump 数据湖）
        collected_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "extractor_types": list(self.extractor_types),
            "total_new": total_new,
            "results": {k: {kk: vv for kk, vv in v.items() if kk != "trace"}
                        for k, v in results.items() if k != "error"},
            "error": err or None,
        }
        status = Status.COMPLETE if ok and total_new >= 0 else Status.INCOMPLETE
        if not ok:
            status = Status.INCOMPLETE
        elif total_new == 0:
            status = Status.EMPTY
        obs = Observation(
            source_id="xueqiu",
            snapshot_id=f"bridge:{collected_at}:{'+'.join(self.extractor_types)}",
            entity_type="xq_feed",
            entity_id=f"run:{collected_at}",
            clock=SourceClock(collected_at=collected_at),
            status=status,
            completeness=None,
            allowed_use_cases=tuple(bound),
            source_field="post",
            payload=payload,
        )
        self.store.put(obs)
        self.registry.update_health("xueqiu", ok=ok, detail=err[:200] if err else "ok")
        return {"ok": ok, "n": total_new, "reason": err or "ok",
                "snapshot_id": obs.snapshot_id}
