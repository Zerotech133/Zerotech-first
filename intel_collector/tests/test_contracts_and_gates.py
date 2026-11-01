# -*- coding: utf-8 -*-
"""合成夹具测试：契约 / 权利 / 存储 / 时间语义 / 适配器。不触网、不接真实数据根。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.contracts import Observation, SourceClock, Status, UseCase
from core.rights_gate import RightsGate
from registry.source_registry import SourceRegistry, SourceSpec
from store.obs_store import ObservationStore
from adapters.guba_adapter import GubaAdapter, _default_synthetic_fetcher
from adapters.youtube_envelope_adapter import YouTubeEnvelopeAdapter
from adapters.xueqiu_adapter import XqAdapter


class ContractTests(unittest.TestCase):
    def test_observation_auto_hash(self):
        o = Observation(source_id="s1", snapshot_id="x", entity_type="t", entity_id="i",
                        clock=SourceClock(collected_at="2026-01-01T00:00:00Z"), status="complete",
                        payload={"a": 1})
        self.assertIsNotNone(o.raw_sha256)
        o2 = Observation(source_id="s1", snapshot_id="y", entity_type="t", entity_id="i",
                         clock=SourceClock(collected_at="2026-01-01T00:00:00Z"), status="complete",
                         payload={"a": 1})
        self.assertEqual(o.raw_sha256, o2.raw_sha256)

    def test_rights_gate_default_deny(self):
        g = RightsGate()
        spec = SourceSpec(source_id="yt", name="yt", category="media", owner="tian_feng")
        spec.grant(UseCase.CONTROLLED_QUERY.value)
        self.assertFalse(g.check(spec, UseCase.TRAINING.value).allowed)
        self.assertTrue(g.check(spec, UseCase.CONTROLLED_QUERY.value).allowed)

    def test_high_risk_requires_explicit(self):
        g = RightsGate()
        spec = SourceSpec(source_id="yt", name="yt", category="media", owner="tian_feng")
        spec.rights[UseCase.TRAINING.value] = True  # 但没有 explicit_approval
        self.assertFalse(g.check(spec, UseCase.TRAINING.value).allowed)
        spec.grant(UseCase.TRAINING.value, explicit=True)
        self.assertTrue(g.check(spec, UseCase.TRAINING.value).allowed)

    def test_registry_rejects_unowned(self):
        reg = SourceRegistry()
        with self.assertRaises(ValueError):
            reg.register(SourceSpec(source_id="x", name="x", category="media", owner="UNOWNED"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ObservationStore(":memory:")

    def test_latest_failed_not_rolled_back(self):
        s, et, eid = "s1", "t", "i"
        ok1 = Observation(source_id=s, snapshot_id="a", entity_type=et, entity_id=eid,
                          clock=SourceClock(collected_at="2026-01-01T00:00:00Z"), status="complete",
                          allowed_use_cases=("controlled_query",), payload={"v": 1})
        fail = Observation(source_id=s, snapshot_id="b", entity_type=et, entity_id=eid,
                           clock=SourceClock(collected_at="2026-01-02T00:00:00Z"), status="unavailable",
                           allowed_use_cases=("controlled_query",), payload={"v": 2})
        self.store.put(ok1)
        self.store.put(fail)
        latest = self.store.latest(s, et, eid)
        self.assertEqual(latest.status, "unavailable")  # 最新失败快照不回退伪装为旧 success
        aof = self.store.as_of(s, et, eid, "2026-01-01T12:00:00Z")
        self.assertEqual(aof.status, "complete")       # as-of 取旧成功

    def test_store_roundtrip_preserves_source_field_and_rights(self):
        o = Observation(source_id="s1", snapshot_id="x", entity_type="xq_feed", entity_id="i",
                        clock=SourceClock(collected_at="2026-01-01T00:00:00Z"), status="complete",
                        payload={"k": 1}, source_field="post",
                        allowed_use_cases=("controlled_query",))
        self.store.put(o)
        got = self.store.latest("s1", "xq_feed", "i")
        self.assertEqual(got.source_field, "post")
        self.assertEqual(got.allowed_use_cases, ("controlled_query",))
        self.assertIn("controlled_query", [u for u in got.allowed_use_cases])


class AdapterTests(unittest.TestCase):
    def test_guba_synthetic_produces_page_snapshots(self):
        ad = GubaAdapter("guba_eastmoney", fetcher=_default_synthetic_fetcher)
        obs = ad.collect(["000338.SZ"], pages=2)
        self.assertTrue(obs)
        self.assertTrue(all(o.entity_type == "guba_page" for o in obs))
        self.assertTrue(all(o.source_id == "guba_eastmoney" for o in obs))
        empty = GubaAdapter("guba_eastmoney", fetcher=lambda c, p: [])
        self.assertEqual(len(empty.collect(["000338.SZ"])), 1)
        self.assertEqual(empty.collect(["000338.SZ"])[0].status, "empty")

    def test_youtube_envelope_preserves_provenance(self):
        ad = YouTubeEnvelopeAdapter("youtube_quant_public_v1")
        env = {
            "snapshot_id": "snap-orig-1", "entity_type": "video_metadata",
            "entity_id": "vid1", "collected_at": "2026-10-19T03:57:00Z",
            "status": "complete", "completeness": "exhausted",
            "payload": {"info": {"title": "合成视频", "published_at": "2026-05-01T00:00:00Z"}},
            "raw_sha256": "abc", "source_payload_sha256": "def",
        }
        o = ad.collect([env])[0]
        self.assertEqual(o.snapshot_id, "snap-orig-1")
        self.assertEqual(o.raw_sha256, "abc")
        self.assertNotIn(UseCase.TRAINING.value, o.allowed_use_cases)

    def test_xueqiu_nested_data_normalized(self):
        def f(target, **kw):
            return {"status": "ok", "total": 1, "list": [
                {"data": {"id": "post-nested-1", "created_at": "2026-10-22T08:00:00Z",
                          "desc": "嵌套帖", "user": {"id": "u-1", "screen_name": "A"}}}
            ]}

        ad = XqAdapter("xueqiu_public_v1", fetcher=f)
        obs = ad.collect(["600519.SH"], mode="timeline", max_pages=1)
        self.assertEqual(len(obs), 1)
        items = obs[0].payload["items"]
        self.assertEqual(items[0]["status_id"], "post-nested-1")  # 来自内层 data
        self.assertEqual(obs[0].entity_type, "xq_feed")
        self.assertNotIn(UseCase.TRAINING.value, obs[0].allowed_use_cases)

    def test_xueqiu_flat_compat(self):
        def f(target, **kw):
            return {"status": "ok", "total": 1, "list": [
                {"id": "post-flat-1", "created_at": "2026-10-22T08:00:00Z", "desc": "扁平帖"}
            ]}

        ad = XqAdapter("xueqiu_public_v1", fetcher=f)
        obs = ad.collect(["600519.SH"], max_pages=1)
        self.assertEqual(obs[0].payload["items"][0]["status_id"], "post-flat-1")

    def test_xueqiu_status_mapping(self):
        def f(target, **kw):
            return {"status": "ip_blocked", "total": 0, "list": []}

        ad = XqAdapter("xueqiu_public_v1", fetcher=f)
        obs = ad.collect(["600519.SH"], max_pages=1)
        self.assertEqual(obs[0].status, "unavailable")  # 被封 != 删除（失败可见）


if __name__ == "__main__":
    unittest.main(verbosity=2)
