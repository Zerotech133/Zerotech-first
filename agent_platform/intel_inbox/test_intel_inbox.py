# -*- coding: utf-8 -*-
"""满宠每日情报收件箱受理席测试（全临时目录，零触碰真实 inbox/ledger）。

覆盖（2026-10-19 工单·侧3）：
  · 成对正常受理：ACK 齐全（received_utc/哈希/文件清单）+ ledger 入账
  · 重复投递幂等：同内容哈希再投 → duplicate 记录，不重复入账
  · 缺对拒绝 / 哈希篡改拒绝 / meta 缺失拒绝：reject.json + 移入
    processed/rejected/ + ledger 记 reject（fail-visible 不静默）
  · ledger append-only：多次运行只追加，既有行逐字节不变
纪律：零网络；不触碰 registry/ 与 onboard_agent.py。
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intel_inbox as inbox_mod  # noqa: E402

DATE = "2026-10-19"
STEM = "manchong_brief_%s" % DATE
MD_BODY = "# 满宠每日情报汇报 %s\n\n留痕不外发\n" % DATE
JSON_BODY = (
    json.dumps({"report": "manchong_daily_brief", "date": DATE}, ensure_ascii=False)
    + "\n"
)


def _sha(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class InboxFixture(unittest.TestCase):
    """公共夹具：临时 inbox/processed/ledger，投递构造器。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inbox_dir = os.path.join(self.tmp.name, "inbox")
        self.processed_dir = os.path.join(self.tmp.name, "processed")
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")
        os.makedirs(self.inbox_dir)

    def deliver(
        self,
        md_body=MD_BODY,
        json_body=JSON_BODY,
        stem=STEM,
        with_meta=True,
        with_json=True,
        with_md=True,
        tamper=None,
    ):
        """按满宠侧 meta 契约向临时 inbox/ 投递一份（默认 md+json+meta）。"""
        names = []
        if with_md:
            names.append((stem + ".md", md_body))
        if with_json:
            names.append((stem + ".json", json_body))
        payloads = {name: body for name, body in names}
        if with_meta:
            sha_map = {n: _sha(b) for n, b in payloads.items()}
            if tamper:  # 篡改：meta 记错哈希
                sha_map[tamper] = "0" * 64
            meta = {
                "delivered_utc": "2026-10-19T01:00:00Z",
                "sha256": sha_map,
                "source": {n: "C:/fake/source/" + n for n in payloads},
            }
            with open(
                os.path.join(self.inbox_dir, stem + ".meta.json"),
                "w",
                encoding="utf-8",
                newline="\n",
            ) as f:
                f.write(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
        for name, body in payloads.items():
            with open(
                os.path.join(self.inbox_dir, name), "w", encoding="utf-8", newline="\n"
            ) as f:
                f.write(body)
        return sorted(os.listdir(self.inbox_dir))

    def run_inbox(self):
        return inbox_mod.process_inbox(self.inbox_dir, self.processed_dir, self.ledger)


class TestNormalAcceptance(InboxFixture):
    """成对正常受理：ACK + 入账 + 文件归位。"""

    def test_paired_delivery_accepted(self):
        self.deliver()
        results, warnings = self.run_inbox()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["kind"], "accept")
        self.assertEqual(warnings, [])
        # ACK：received_utc / 哈希 / 文件清单
        ack_path = os.path.join(self.processed_dir, STEM + ".ack.json")
        with open(ack_path, encoding="utf-8") as f:
            ack = json.load(f)
        self.assertEqual(ack["ack"], "received")
        self.assertEqual(ack["stem"], STEM)
        self.assertTrue(ack["received_utc"].endswith("Z"))
        self.assertFalse(ack["duplicate"])
        self.assertEqual(ack["sha256"]["md"], _sha(MD_BODY))
        self.assertEqual(ack["sha256"]["json"], _sha(JSON_BODY))
        self.assertEqual(ack["files"], [STEM + ".md", STEM + ".json"])
        # 原文件（md/json/meta）移入 processed/，inbox 清空
        self.assertEqual(os.listdir(self.inbox_dir), [])
        for name in (STEM + ".md", STEM + ".json", STEM + ".meta.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.processed_dir, name)))
        # ledger：恰一条 accept，哈希与文件一致
        with open(self.ledger, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["kind"], "accept")
        self.assertEqual(recs[0]["md_sha256"], _sha(MD_BODY))
        self.assertEqual(recs[0]["json_sha256"], _sha(JSON_BODY))
        self.assertEqual(recs[0]["stem"], STEM)

    def test_second_inbox_run_is_noop(self):
        """空跑第二遍：无新结果，ledger 不增长（幂等扫描）。"""
        self.deliver()
        self.run_inbox()
        with open(self.ledger, encoding="utf-8") as f:
            before = f.read()
        results, _ = self.run_inbox()
        self.assertEqual(results, [])
        with open(self.ledger, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)


class TestDuplicateDelivery(InboxFixture):
    """重复投递幂等：同哈希记 duplicate，不重复入账。"""

    def test_same_content_redelivery_marked_duplicate(self):
        self.deliver()
        r1, _ = self.run_inbox()
        self.assertEqual(r1[0]["kind"], "accept")
        # 再投一份同内容（文件名不同日期亦按内容哈希判重）
        stem2 = "manchong_brief_2026-10-19_resend"
        self.deliver(stem=stem2)
        r2, _ = self.run_inbox()
        self.assertEqual(r2[0]["kind"], "duplicate")
        ack_path = os.path.join(self.processed_dir, stem2 + ".ack.json")
        with open(ack_path, encoding="utf-8") as f:
            ack = json.load(f)
        self.assertTrue(ack["duplicate"])
        self.assertEqual(ack["prior_stem"], STEM)
        # ledger：accept 一条 + duplicate 一条；accept 不重复
        with open(self.ledger, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        kinds = [r["kind"] for r in recs]
        self.assertEqual(kinds.count("accept"), 1)
        self.assertEqual(kinds.count("duplicate"), 1)
        self.assertEqual(recs[1]["stem"], stem2)


class TestRejectPaths(InboxFixture):
    """拒绝路径：缺对 / 哈希篡改 / meta 缺失，全部 fail-visible。"""

    def _assert_rejected(self, results, reason):
        self.assertEqual(results[0]["kind"], "reject")
        self.assertEqual(results[0]["reason"], reason)
        rej = os.path.join(self.processed_dir, "rejected")
        with open(os.path.join(rej, STEM + ".reject.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["reason"], reason)
        self.assertTrue(doc["detail"])
        self.assertTrue(doc["rejected_utc"].endswith("Z"))
        # 问题文件移入 rejected/，inbox 清空，无 ACK 无 accept 入账
        self.assertEqual(os.listdir(self.inbox_dir), [])
        for name in results[0]["files"]:
            self.assertTrue(os.path.isfile(os.path.join(rej, name)))
        with open(self.ledger, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        self.assertEqual([r["kind"] for r in recs], ["reject"])
        self.assertEqual(recs[0]["reason"], reason)
        self.assertFalse(
            os.path.isfile(os.path.join(self.processed_dir, STEM + ".ack.json"))
        )

    def test_missing_pair_rejected(self):
        self.deliver(with_json=False)
        results, _ = self.run_inbox()
        self._assert_rejected(results, inbox_mod.R_MISSING_PAIR)

    def test_meta_missing_rejected(self):
        self.deliver(with_meta=False)
        results, _ = self.run_inbox()
        self._assert_rejected(results, inbox_mod.R_META_MISSING)

    def test_tampered_md_rejected(self):
        self.deliver(tamper=STEM + ".md")
        results, _ = self.run_inbox()
        self._assert_rejected(results, inbox_mod.R_HASH_MISMATCH)

    def test_tampered_json_rejected(self):
        self.deliver(tamper=STEM + ".json")
        results, _ = self.run_inbox()
        self._assert_rejected(results, inbox_mod.R_HASH_MISMATCH)

    def test_meta_invalid_rejected(self):
        self.deliver()
        with open(
            os.path.join(self.inbox_dir, STEM + ".meta.json"), "w", encoding="utf-8"
        ) as f:
            f.write("not json {{{")
        results, _ = self.run_inbox()
        self._assert_rejected(results, inbox_mod.R_META_INVALID)

    def test_reject_then_good_delivery_both_recorded(self):
        """先拒后收：拒绝不阻断后续正常受理，ledger 两种记录齐备。"""
        self.deliver(with_meta=False)
        r1, _ = self.run_inbox()
        self.assertEqual(r1[0]["kind"], "reject")
        stem2 = "manchong_brief_2026-10-20"
        self.deliver(
            stem=stem2,
            md_body="# 满宠每日情报汇报 2026-10-20\n",
            json_body='{"date": "2026-10-20"}\n',
        )
        r2, _ = self.run_inbox()
        self.assertEqual(r2[0]["kind"], "accept")
        with open(self.ledger, encoding="utf-8") as f:
            kinds = [json.loads(l)["kind"] for l in f if l.strip()]
        self.assertEqual(kinds, ["reject", "accept"])


class TestLedgerAppendOnly(InboxFixture):
    """ledger append-only：只追加，既有行逐字节不变。"""

    def test_ledger_only_appends(self):
        self.deliver()
        self.run_inbox()
        with open(self.ledger, "rb") as f:
            before = f.read()
        # 再投新内容 + 一份重复 → ledger 只在尾部追加
        self.deliver(
            stem="manchong_brief_2026-10-20",
            md_body="# 满宠每日情报汇报 2026-10-20\n",
            json_body='{"date": "2026-10-20"}\n',
        )
        self.deliver(
            stem="manchong_brief_2026-10-21",
            md_body="# 满宠每日情报汇报 2026-10-21\n",
            json_body='{"date": "2026-10-21"}\n',
        )
        self.run_inbox()
        with open(self.ledger, "rb") as f:
            after = f.read()
        self.assertTrue(after.startswith(before))  # 既有行原样保留
        self.assertGreater(len(after), len(before))
        lines = after.decode("utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)  # 1 accept + 2 accept

    def test_corrupt_ledger_line_skipped_not_fatal(self):
        """台账既有坏行：如实告警，不阻断新投递受理。"""
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write("CORRUPT GARBAGE LINE\n")
        self.deliver()
        results, warnings = self.run_inbox()
        self.assertEqual(results[0]["kind"], "accept")
        self.assertTrue(any("不可解析" in w for w in warnings))


class TestCli(InboxFixture):
    """CLI 退出码：拒绝存在 → 1；干净 → 0。"""

    def test_exit_code_clean(self):
        self.deliver()
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = inbox_mod.main(
                [
                    "--inbox",
                    self.inbox_dir,
                    "--processed",
                    self.processed_dir,
                    "--ledger",
                    self.ledger,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("accept=1", buf.getvalue())

    def test_exit_code_reject(self):
        self.deliver(with_json=False)
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = inbox_mod.main(
                [
                    "--inbox",
                    self.inbox_dir,
                    "--processed",
                    self.processed_dir,
                    "--ledger",
                    self.ledger,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("REJECT", buf.getvalue())
        self.assertIn(inbox_mod.R_MISSING_PAIR, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
