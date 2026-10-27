"""执行反馈账本 v0 最小测试：合成数据 + 临时目录，不碰 portal/data。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import risk_feedback_ledger as rfl  # noqa: E402


def _rec(**kw):
    base = {
        "ts": "2026-10-15 09:35:00",
        "date": "20261015",
        "strategy": "chanlun",
        "symbol": "600519.SH",
        "action": "blocked",
        "rule": "R2-liquidity-shock",
        "score": 2.31,
        "decision_price": 12.34,
        "holding_days": None,
        "fwd5": None,
        "fwd20": None,
        "note": "vol/vol_ma20=2.31 >= 上限2.00; mode=shadow",
    }
    base.update(kw)
    return base


class TestRiskFeedbackLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rfl_test_")
        self.path = os.path.join(self.tmp, "sub", "ledger.jsonl")

    def test_append_creates_dir_and_jsonl(self):
        row = rfl.append(_rec(), path=self.path)
        self.assertTrue(os.path.isfile(self.path))
        with open(self.path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["symbol"], "600519.SH")
        self.assertEqual(row, lines[0])

    def test_append_whitelist_and_note_trunc(self):
        rec = _rec(big_object={"news": "x" * 9999}, note="长" * 500)
        row = rfl.append(rec, path=self.path)
        self.assertNotIn("big_object", row)  # 白名单丢弃大对象
        self.assertLessEqual(len(row["note"]), 200)  # note 截断

    def test_append_rejects_bad_action_and_missing(self):
        with self.assertRaises(rfl.LedgerError):
            rfl.append(_rec(action="killed"), path=self.path)
        with self.assertRaises(rfl.LedgerError):
            rfl.append(_rec(symbol=""), path=self.path)

    def test_query_filters(self):
        for kw in (
            _rec(),
            _rec(symbol="000001.SZ", action="allowed", rule="pass", score=None),
            _rec(strategy="double_knife"),
        ):
            rfl.append(kw, path=self.path)
        self.assertEqual(len(rfl.query(path=self.path)), 3)
        self.assertEqual(len(rfl.query(strategy="chanlun", path=self.path)), 2)
        self.assertEqual(len(rfl.query(action="blocked", path=self.path)), 2)
        got = rfl.query(strategy="chanlun", action="allowed", path=self.path)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["symbol"], "000001.SZ")

    def test_query_skips_bad_lines_and_empty(self):
        self.assertEqual(rfl.query(path=os.path.join(self.tmp, "no.jsonl")), [])
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken json\n")
            f.write(json.dumps(_rec(), ensure_ascii=False) + "\n")
        rows = rfl.query(path=self.path)
        self.assertEqual(len(rows), 1)  # 坏行跳过不抛

    def test_chinese_not_escaped(self):
        rfl.append(_rec(note="中文归因·换手率正常"), path=self.path)
        raw = open(self.path, encoding="utf-8").read()
        self.assertIn("中文归因", raw)  # ensure_ascii=False


if __name__ == "__main__":
    unittest.main()
