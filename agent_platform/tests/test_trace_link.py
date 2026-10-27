# -*- coding: utf-8 -*-
"""M4-P1 trace_link 映射层测试。

覆盖：环锚点规范化 / 全链登记与回放 / 幂等重放 / 同键异链拒绝 /
残缺锚点拒绝 / 影子账本只读收集（done 过滤+坏行容忍）/ 环缺失显式
missing / 空账本 NOT_FOUND / tmp 隔离（不触真仓）。
"""

import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from adapters.trace_link import (  # noqa: E402
    LinkStore,
    SEG_REQUIRED,
    collect_shadow_anchors,
    link_id_of,
    link_key,
    replay_trace,
    validate_chain,
)

FULL_CHAIN = {
    "shadow": {
        "run_id": "76cf6d30358f",
        "symbol": "300750",
        "trade_date": "2026-10-14",
    },
    "paper": {"trace_id": "tr-abc123"},
    "execution": {"order_id": "ord-001"},
    "perf": {"perf_key": "300750|2026-10-14|tr-abc123"},
}


class LinkKeyTests(unittest.TestCase):
    def test_link_key_normalization(self):
        k = link_key(
            "shadow", {"run_id": " a ", "symbol": "300750", "trade_date": "2026-10-14"}
        )
        self.assertEqual(k, "shadow:a|300750|2026-10-14")

    def test_link_key_missing_field_rejected(self):
        with self.assertRaises(ValueError):
            link_key("paper", {})  # 缺 trace_id
        with self.assertRaises(ValueError):
            link_key("bogus", {"x": "1"})  # 未知环节

    def test_validate_chain_findings(self):
        self.assertEqual(validate_chain(FULL_CHAIN), [])
        fs = validate_chain({"bogus": {}, "paper": {}})
        self.assertEqual(len(fs), 2)  # 未知环节 + 缺锚点

    def test_link_id_deterministic_and_order_insensitive(self):
        chain = {k: FULL_CHAIN[k] for k in ("perf", "execution", "paper", "shadow")}
        self.assertEqual(link_id_of(FULL_CHAIN), link_id_of(chain))


class StoreTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="trace_link_t_"))
        self.store = LinkStore(self.tmp / "links.jsonl")

    def test_record_and_replay(self):
        r1 = self.store.record(
            FULL_CHAIN, source="t", declared_at="2026-10-17T19:20:00"
        )
        self.assertEqual(r1["status"], "recorded")
        r2 = self.store.record(FULL_CHAIN, source="t")
        self.assertEqual(r2["status"], "replayed")  # 幂等重放零副作用
        # 重放不追加行
        self.assertEqual(
            len(
                self.tmp.joinpath("links.jsonl")
                .read_text(encoding="utf-8")
                .strip()
                .splitlines()
            ),
            1,
        )

    def test_same_key_different_chain_rejected(self):
        self.store.record(FULL_CHAIN)
        clash = json.loads(json.dumps(FULL_CHAIN))
        clash["execution"]["order_id"] = "ord-999"  # 换 order 但四元组 hash 相同链位
        # link_id 由全部非空环节键决定——order 变化即新链，应可登记
        r = self.store.record(clash)
        self.assertEqual(r["status"], "recorded")
        # 真正的同键异值：篡改已登记行的 chain 内容再重放
        lid = link_id_of(FULL_CHAIN)
        self.store._index[lid]["chain"]["shadow"]["symbol"] = "600036"  # 模拟外部篡改
        with self.assertRaises(ValueError):
            self.store.record(FULL_CHAIN)

    def test_invalid_chain_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record({"paper": {}})  # 缺 trace_id
        with self.assertRaises(ValueError):
            self.store.record({})  # 空链

    def test_partial_chain_and_missing_segments(self):
        self.store.record(
            {"shadow": FULL_CHAIN["shadow"], "paper": FULL_CHAIN["paper"]}
        )
        r = replay_trace(self.store, "tr-abc123")
        self.assertEqual(r["verdict"], "OK")
        m = r["matches"][0]
        self.assertEqual(m["missing_segments"], ["execution", "perf"])

    def test_not_found(self):
        r = replay_trace(self.store, "no-such-key")
        self.assertEqual(r["verdict"], "NOT_FOUND")
        self.assertTrue(r["findings"])

    def test_stats(self):
        self.store.record(FULL_CHAIN)
        s = self.store.stats()
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["by_segment"]["shadow"], 1)


class ShadowCollectTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="trace_link_sc_"))

    def _write(self, rows):
        p = self.tmp / "shadow_ledger.jsonl"
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )
        return p

    def test_done_filter_and_fields(self):
        p = self._write(
            [
                {
                    "date": "2026-10-14",
                    "status": "done",
                    "run_id": "r1",
                    "symbol": "300750",
                    "score": 1,
                    "parse_ok": True,
                },
                {
                    "date": "2026-10-14",
                    "status": "failed",
                    "run_id": "r2",
                    "symbol": "600519",
                    "error": "x",
                },
                {
                    "date": "2026-10-14",
                    "status": "done",
                    "run_id": None,
                    "symbol": "601318",
                },
            ]
        )
        anchors = collect_shadow_anchors(p)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["run_id"], "r1")
        self.assertEqual(anchors[0]["trade_date"], "2026-10-14")

    def test_bad_lines_tolerated(self):
        p = self.tmp / "shadow_ledger.jsonl"
        p.write_text(
            '{"status":"done","run_id":"r9","symbol":"x","date":"d"}\n'
            "NOT JSON AT ALL\n",
            encoding="utf-8",
        )
        self.assertEqual(len(collect_shadow_anchors(p)), 1)

    def test_missing_ledger_empty(self):
        self.assertEqual(collect_shadow_anchors(self.tmp / "none.jsonl"), [])

    def test_seg_required_shape(self):
        self.assertEqual(SEG_REQUIRED["perf"], ("perf_key",))


if __name__ == "__main__":
    unittest.main()
