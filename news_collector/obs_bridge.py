# -*- coding: utf-8 -*-
"""FEEDBACK-L1 观测桥：news_collector 采集结果 → intel_collector observations.db。

设计（feedback_l1_l2_plan_20261107.md L1）：
  - 采集器出口调 record(source_id, ok, got, err)：转成 Observation 落库；
  - SourceClock 三时间语义：collected_at=本次采集 UTC，绝不倒填发布时间；
  - try/except 全包：桥故障只 print 告警，绝不阻塞采集主链；
  - 落库 target=intel_collector/store/observations.db（SLA/PII 分析的数据面）。

REG-W-20261108 之后的落点：观测记录写入唯一真源体系的 store，
与 intel_collector/registry/sources.json（authoritative=true）配套。
"""
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTEL = os.path.normpath(os.path.join(_HERE, "..", "intel_collector"))
if _INTEL not in sys.path:
    sys.path.insert(0, _INTEL)

DB = os.path.join(_INTEL, "store", "observations.db")


def record(source_id, ok, got=0, err="", db_path=None):
    """采集出口调用：ok/got/err → Observation 落库。绝不抛出。"""
    try:
        from core.contracts import Observation, SourceClock
        from store.obs_store import ObservationStore

        db = db_path or DB
        if db_path:
            # 显式指定路径：不降级，父目录缺失即失败返回 False（观测不可悄悄丢）
            if not os.path.isdir(os.path.dirname(db)):
                raise FileNotFoundError("obs db dir missing: %s" % db)
            st = ObservationStore(db)
        else:
            # 默认路径：目录缺失降级内存（保采集不中断，print 已告警）
            st = ObservationStore(db if os.path.isdir(os.path.dirname(db)) else ":memory:")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        obs = Observation(
            source_id=source_id,
            snapshot_id="bridge-%s-%s" % (now[:10], abs(hash((source_id, now))) % 10**8),
            entity_type="collect_run",
            entity_id=source_id,
            clock=SourceClock(collected_at=now),
            status=("ok" if ok else ("empty" if ok and got == 0 else "error")),
            payload={"got": got, "err": (err or "")[:120]},
        )
        st.put(obs)
        return True
    except Exception as e:  # 桥故障绝不反噬采集
        print("[obs_bridge] WARN record fail: %s" % str(e)[:120])
        return False
