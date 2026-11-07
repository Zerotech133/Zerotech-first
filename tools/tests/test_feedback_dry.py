# -*- coding: utf-8 -*-
"""FEEDBACK-L0 单测：feedback_dry.run() 干跑纯只读、输出完整。

不连生产、不触网；fixture 用临时 sources.json + 临时 observations.db。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
INTEL = os.path.normpath(os.path.join(HERE, "..", "..", "intel_collector"))
if INTEL not in sys.path:
    sys.path.insert(0, INTEL)

from examples.feedback_dry import run  # noqa: E402
from store.obs_store import ObservationStore  # noqa: E402
import examples.feedback_dry as fd  # noqa: E402


def _mk_obs(source_id, snapshot_id, collected_at, status="ok"):
    from core.contracts import Observation, SourceClock
    return Observation(
        source_id=source_id,
        snapshot_id=snapshot_id,
        entity_type="demo",
        entity_id="demo",
        clock=SourceClock(collected_at=collected_at),
        status=status,
    )


class TestFeedbackDry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "sources.json")
        self.db = os.path.join(self.tmp.name, "observations.db")
        # 2 源 fixture：guba 新鲜 / xueqiu 违约
        json.dump(
            {
                "guba": {"enabled": True, "sla": {"freshness_minutes": 120, "max_failures": 3}},
                "xueqiu": {"enabled": True, "sla": {"freshness_minutes": 60, "max_failures": 3}},
            },
            open(self.src, "w", encoding="utf-8"),
            ensure_ascii=False,
        )
        st = ObservationStore(self.db)
        now = datetime.now(timezone.utc)
        st.put(_mk_obs("guba", "s1", now.strftime("%Y-%m-%dT%H:%M:%SZ")))
        st.put(_mk_obs("xueqiu", "s1", (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_outputs_and_zero_write(self):
        out = run(sources_path=self.src, obs_db=self.db)
        # 2 源状态
        self.assertEqual(out["n_sources"], 2)
        self.assertEqual(out["n_obs"], 2)
        self.assertEqual(out["sla"]["guba"]["state"], "ok")
        self.assertEqual(out["sla"]["xueqiu"]["state"], "breach")
        # 反馈动作：xueqiu breach → expedite_retry
        ids = [a["source_id"] for a in out["actions"]]
        self.assertIn("xueqiu", ids)
        self.assertTrue(all(a["action"] == "expedite_retry" for a in out["actions"]))
        # 零写入实证：哈希前后一致（run 内部已断言，这里再验返回值非空）
        self.assertTrue(out["hashes"]["sources"] and out["hashes"]["obs_db"])
        # 输出含关键行
        self.assertIn("不代表反馈环已闭环", out["text"])
        self.assertIn("若 apply 将发生", out["text"])

    def test_empty_registry_no_crash(self):
        json.dump({}, open(self.src, "w", encoding="utf-8"))
        out = run(sources_path=self.src, obs_db=self.db)
        self.assertEqual(out["n_sources"], 0)
        self.assertEqual(out["actions"], [])

    def test_missing_db_falls_back_memory(self):
        out = run(sources_path=self.src, obs_db=os.path.join(self.tmp.name, "none.db"))
        self.assertEqual(out["n_obs"], 0)
        # 2 源均无观测 → no_data，动作含 expedite_retry（无观测加速补采）
        self.assertEqual(out["sla"]["guba"]["state"], "no_data")


if __name__ == "__main__":
    unittest.main()
