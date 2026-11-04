# -*- coding: utf-8 -*-
"""P4 共振 v4 基建接线——resonance_store 事件库桥接进 intel_collector 治理体系。

职责划分（对齐 xq_pump_bridge 的"数据面/治理面"收敛口径）：
  - **数据面**（gap_daily/events/runs 行级增量落库）由 quant_backtest/tools/resonance_store.py
    负责（INSERT OR REPLACE 幂等 + fwd* 回填 + runs 留痕）；
  - **治理面**（Observation 观测、SLA/失败可见）由 intel_collector 负责；
  - 本桥 = 接线器：实现 CronRunner 的 CollectDispatcher 协议，
    collect_source("resonance_store") 一次完成：
      1) 调 resonance_store 当日更新路径（v1 = backfill_fwd + stats 快照；
         v3 脚本计算函数与自然日窗常量强耦合，先不 import，B1-b 后再接日频增量）；
      2) 把本次运行摘要包装成 Observation 落 ObservationStore
         （entity_type=resonance_store；payload 带 stats() 与 backfill 明细；
         失败可见：exception→INCOMPLETE）；
      3) 返回 {ok, n, reason} 供 Scheduler.mark_collected 推进调度。

源登记说明（工单口径，如实）：
  - sources.json **无** resonance 源；构造参数传 spec 不可用；
  - Observation 的 source_id 仍写 "resonance_store"（治理面可检索）；
  - registry 若无此源则跳过 update_health 并在返回 reason 中说明；
  - 不私自改 sources.json（登记待工单另批）。
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

SOURCE_ID = "resonance_store"

_RES_STORE_LOADED = False


def _load_resonance_store():
    """惰性加载 quant_backtest/tools/resonance_store.py（挂 sys.path，不包名 import）。"""
    global _RES_STORE_LOADED
    tools = Path(__file__).resolve().parents[2] / "quant_backtest" / "tools"
    for p in (str(tools), str(tools.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)
    _RES_STORE_LOADED = True
    import importlib
    mod = importlib.import_module("resonance_store")
    return mod


class ResonanceStoreDispatcher:
    """CollectDispatcher 实现：驱动 resonance_store 当日路径 + 产出治理 Observation。

    参数：
      registry/store/gate：intel_collector 三件套（源档/观测库/权利闸）。
      db_path/index_parquet：resonance_store 数据根（None=用其默认真库路径）。
    """

    def __init__(
        self,
        registry: SourceRegistry,
        store: ObservationStore,
        gate: Optional[RightsGate] = None,
        *,
        db_path: Optional[str] = None,
        index_parquet: Optional[str] = None,
    ):
        self.registry = registry
        self.store = store
        self.gate = gate or RightsGate()
        self.db_path = db_path
        self.index_parquet = index_parquet

    # ---- CollectDispatcher 协议 ----

    def collect_source(self, source_id: str, now: datetime) -> dict:
        if source_id != SOURCE_ID:
            return {"ok": False, "n": 0,
                    "reason": f"bridge 只接 {SOURCE_ID}，收到 {source_id}"}
        spec = self.registry.get(SOURCE_ID)  # 允许未登记（见模块 docstring）

        # 用途绑定：未登记 → 无源档可闸 → 空（治理面如实记录，不伪造授权）
        if spec is not None:
            bound = [uc for uc in ("internal_retention", "cleaning", "controlled_query")
                     if self.gate.check(spec, uc).allowed]
        else:
            bound = []

        mod = _load_resonance_store()
        ok = True
        err = ""
        backfill_res: dict = {}
        stats_res: dict = {}
        try:
            kw = {}
            if self.db_path:
                kw["db_path"] = self.db_path
            if self.index_parquet:
                backfill_res = mod.backfill_fwd(self.index_parquet, **kw)
            else:
                backfill_res = mod.backfill_fwd(**kw)
            stats_res = mod.stats(**kw)
        except Exception as e:  # 失败可见
            ok = False
            err = f"{type(e).__name__}: {e}"
            backfill_res = {"error": {"trace": traceback.format_exc()[-400:]}}

        n = len(backfill_res.get("filled") or []) if ok else 0
        collected_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "job": "backfill+stats",
            "backfill": {
                "filled": backfill_res.get("filled") if ok else [],
                "pending": backfill_res.get("pending") if ok else [],
                "index_last_date": backfill_res.get("index_last_date") if ok else None,
            },
            "stats": stats_res if stats_res else None,
            "error": err or None,
            "registered_source": spec is not None,
        }
        status = Status.COMPLETE if ok else Status.INCOMPLETE
        obs = Observation(
            source_id=SOURCE_ID,
            snapshot_id=f"bridge:{collected_at}:backfill",
            entity_type=SOURCE_ID,
            entity_id=f"run:{collected_at}",
            clock=SourceClock(collected_at=collected_at),
            status=status,
            completeness=None,
            allowed_use_cases=tuple(bound),
            source_field=None,
            payload=payload,
        )
        self.store.put(obs)
        if spec is not None:
            self.registry.update_health(SOURCE_ID, ok=ok, detail=err[:200] if err else "ok")
            reason = err or "ok"
        else:
            reason = err or ("ok（源未登记 sources.json：跳过 update_health，"
                             "不私自改登记，待工单另批）")
        return {"ok": ok, "n": n, "reason": reason, "snapshot_id": obs.snapshot_id}
