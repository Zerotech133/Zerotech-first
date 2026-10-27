# -*- coding: utf-8 -*-
"""diff_baseline 账本基线对比测试。

tmp_path 式隔离目录构造（对齐 test_check_observation_point.py 的隔离根
构造法），真仓零接触、只读零写面。覆盖六类场景：
  1. 正常新增行：append-only 正常增长，退出码 0，新增行按 run_id 列出；
  2. 缺失行：退出码 2（append-only 账本缺失=篡改/回滚嫌疑，最高级别）；
  3. 完全一致：退出码 0，sha256 一致，status IDENTICAL；
  4. 二进制指纹级：checkpoints.db 只比 sha256+大小，指纹差异不拉高退出码；
  5. 文件缺失：中性退出码 1，如实标注；
  6. 空账本：两侧空文件完全一致，退出码 0。
  附：退出码优先级 2 > 1（既有缺失行又有文件缺失时取 2）；
      CLI 显式传参 + 目录不存在 fail-visible。
jsonl 行构造按真账本取证 schema（shadow_ledger 的 run_id/ts 键、
trace_links 的 link_id、perf_ledger 的 perf_key、registrations 的
submit_id），非凭空捏造。
"""

import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "tools"))

import diff_baseline as db  # noqa: E402

# 隔离测试用的账本对（覆盖 checkpoint binary + 三种 jsonl 身份键形态）
TEST_PAIRS = (
    (
        "shadow_ledger",
        ("shadow", "shadow_ledger.jsonl"),
        "shadow_ledger.jsonl.baseline",
        "jsonl",
    ),
    (
        "trace_links",
        ("trace", "trace_links.jsonl"),
        "trace_links.jsonl.baseline",
        "jsonl",
    ),
    ("checkpoints_db", ("checkpoints.db",), "checkpoints.db.baseline", "binary"),
)


def _shadow_row(symbol, run_id, ts=1791442784.86, status="done"):
    """按 run_shadow.py 落账 schema 构造影子行（真仓键结构取证）。"""
    return {
        "date": "2026-10-08",
        "ts": ts,
        "symbol": symbol,
        "name": "样本" + symbol,
        "status": status,
        "run_id": run_id,
        "price_at_decision": 10.0,
        "recommendation": "HOLD",
        "confidence": 0.55,
        "score": 0,
        "target_range": [9.0, 11.0],
        "degraded": False,
        "latency_s": 100.0,
    }


def _trace_row(link_id, run_id):
    """按 adapters/trace_link.py record() schema 构造链关联行。"""
    return {
        "schema_version": "m4p1.v1",
        "link_id": link_id,
        "chain": {
            "shadow": {"run_id": run_id, "symbol": "600519", "trade_date": "2026-10-08"}
        },
        "source": "m4p1-bootstrap",
        "declared_at": "2026-10-17T19:30:00",
    }


def _dump_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


class DiffBaselineBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="diff_baseline_test_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.baseline = Path(self._tmp) / "baseline"
        self.data = Path(self._tmp) / "data"
        self.baseline.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self._orig_pairs = db.LEDGER_PAIRS
        db.LEDGER_PAIRS = TEST_PAIRS
        self.addCleanup(setattr, db, "LEDGER_PAIRS", self._orig_pairs)
        # 未被用例触碰的账本对默认两侧一致（避免无关 FILE_MISSING 中性
        # 条目干扰退出码断言）；用例自行覆盖或删除。
        for name, rel, baseline_name, kind in TEST_PAIRS:
            if kind == "jsonl":
                (self.baseline / baseline_name).parent.mkdir(
                    parents=True, exist_ok=True
                )
                (self.baseline / baseline_name).write_text("", encoding="utf-8")
                src = self.data.joinpath(*rel)
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text("", encoding="utf-8")
            else:
                self._binary(self.baseline / baseline_name, self._DEFAULT_DB)
                self._binary(self.data.joinpath(*rel), self._DEFAULT_DB)

    _DEFAULT_DB = b"SQLite format 3\x00" + b"default" * 8

    # ---- 构造工具 ----
    def _baseline_jsonl(self, name, rows):
        baseline_name = dict((p[0], p[2]) for p in TEST_PAIRS)[name]
        _dump_jsonl(self.baseline / baseline_name, rows)

    def _source_jsonl(self, rel, rows):
        _dump_jsonl(self.data.joinpath(*rel), rows)

    def _binary(self, path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _run(self):
        rep = db.run_diff(self.baseline, self.data)
        buf = StringIO()
        with redirect_stdout(buf):
            db._print_report(rep)
        return rep, buf.getvalue()

    def _item(self, rep, name):
        return next(it for it in rep["items"] if it["name"] == name)


class TestNormalGrowth(DiffBaselineBase):
    """场景 1：正常新增行 → 退出码 0，新增按 run_id 列出。"""

    def test_growth_exit0(self):
        old_rows = [
            _shadow_row("600519", "run_old_1"),
            _shadow_row("000858", "run_old_2"),
        ]
        new_rows = old_rows + [_shadow_row("601318", "run_new_1", ts=1791443000.0)]
        self._baseline_jsonl("shadow_ledger", old_rows)
        self._source_jsonl(TEST_PAIRS[0][1], new_rows)
        rep, out = self._run()
        self.assertEqual(rep["exit_code"], 0)
        it = self._item(rep, "shadow_ledger")
        self.assertEqual(it["status"], db.GREW)
        self.assertEqual((it["old_lines"], it["new_lines"]), (2, 3))
        self.assertEqual(len(it["added"]), 1)
        self.assertEqual(it["missing"], [])
        self.assertFalse(it["sha_equal"])
        self.assertIn("run_id=run_new_1", out)
        self.assertIn("[新增]", out)
        self.assertNotIn("[缺失]", out)


class TestMissingLine(DiffBaselineBase):
    """场景 2：缺失行 → 退出码 2，篡改/回滚嫌疑最高级别。"""

    def test_missing_exit2_and_red_mark(self):
        old_rows = [
            _shadow_row("600519", "run_old_1"),
            _shadow_row("000858", "run_old_2"),
        ]
        new_rows = [old_rows[1]]  # 回滚掉了第一行
        self._baseline_jsonl("shadow_ledger", old_rows)
        self._source_jsonl(TEST_PAIRS[0][1], new_rows)
        rep, out = self._run()
        self.assertEqual(rep["exit_code"], 2)
        it = self._item(rep, "shadow_ledger")
        self.assertEqual(it["status"], db.MISSING_LINES)
        self.assertEqual(len(it["missing"]), 1)
        self.assertIn("run_id=run_old_1", out)
        self.assertIn("[缺失]", out)

    def test_missing_dominates_file_missing(self):
        """优先级 2 > 1：既有缺失行又有文件缺失 → 取 2。"""
        old_rows = [_trace_row("lid_1", "run_a")]
        self._baseline_jsonl("trace_links", old_rows)
        self._source_jsonl(TEST_PAIRS[1][1], [])  # 源清空 = 整册缺失
        # shadow_ledger 两侧都删文件 → FILE_MISSING
        (self.baseline / "shadow_ledger.jsonl.baseline").unlink()
        (self.data / "shadow" / "shadow_ledger.jsonl").unlink()
        rep, _ = self._run()
        self.assertEqual(rep["exit_code"], 2)
        self.assertEqual(self._item(rep, "trace_links")["status"], db.MISSING_LINES)
        self.assertEqual(self._item(rep, "shadow_ledger")["status"], db.FILE_MISSING)


class TestIdentical(DiffBaselineBase):
    """场景 3：完全一致 → 退出码 0，IDENTICAL。"""

    def test_identical_exit0(self):
        rows = [_trace_row("lid_1", "run_a"), _trace_row("lid_2", "run_b")]
        self._baseline_jsonl("trace_links", rows)
        self._source_jsonl(TEST_PAIRS[1][1], rows)
        rep, out = self._run()
        self.assertEqual(rep["exit_code"], 0)
        it = self._item(rep, "trace_links")
        self.assertEqual(it["status"], db.IDENTICAL)
        self.assertTrue(it["sha_equal"])
        self.assertEqual(it["added"], [])
        self.assertEqual(it["missing"], [])
        self.assertIn("行数 2→2", it["evidence"])


class TestBinaryFingerprint(DiffBaselineBase):
    """场景 4：checkpoints.db 仅 sha256+大小指纹级对比。"""

    BASE_DB = b"SQLite format 3\x00" + b"x" * 64

    def test_fingerprint_diff_does_not_raise_exit_code(self):
        self._binary(self.baseline / "checkpoints.db.baseline", self.BASE_DB)
        self._binary(self.data / "checkpoints.db", self.BASE_DB + b"more bytes")
        rep, out = self._run()
        self.assertEqual(rep["exit_code"], 0)  # 指纹差异不拉高退出码
        it = self._item(rep, "checkpoints_db")
        self.assertEqual(it["status"], db.FINGERPRINT_DIFF)
        self.assertFalse(it["sha_equal"])
        self.assertNotEqual(it["old_size"], it["new_size"])
        self.assertIn("指纹级", out)
        self.assertEqual(it["added"], [])  # 不做行级

    def test_fingerprint_match(self):
        self._binary(self.baseline / "checkpoints.db.baseline", self.BASE_DB)
        self._binary(self.data / "checkpoints.db", self.BASE_DB)
        rep, _ = self._run()
        self.assertEqual(rep["exit_code"], 0)
        self.assertEqual(
            self._item(rep, "checkpoints_db")["status"], db.FINGERPRINT_MATCH
        )
        self.assertTrue(self._item(rep, "checkpoints_db")["sha_equal"])


class TestFileMissing(DiffBaselineBase):
    """场景 5：文件缺失 → 中性退出码 1，如实标注。"""

    def test_source_missing_neutral_exit1(self):
        self._baseline_jsonl("shadow_ledger", [_shadow_row("600519", "run_old_1")])
        # 删源文件：data/shadow/shadow_ledger.jsonl 不存在
        (self.data / "shadow" / "shadow_ledger.jsonl").unlink()
        rep, out = self._run()
        self.assertEqual(rep["exit_code"], 1)
        it = self._item(rep, "shadow_ledger")
        self.assertEqual(it["status"], db.FILE_MISSING)
        self.assertIn("源缺失", it["evidence"])
        self.assertIn("中性", out)

    def test_baseline_missing_neutral_exit1(self):
        self._source_jsonl(TEST_PAIRS[0][1], [_shadow_row("600519", "run_new_1")])
        # 删基线文件：shadow_ledger.jsonl.baseline 不存在
        (self.baseline / "shadow_ledger.jsonl.baseline").unlink()
        rep, _ = self._run()
        self.assertEqual(rep["exit_code"], 1)
        it = self._item(rep, "shadow_ledger")
        self.assertEqual(it["status"], db.FILE_MISSING)
        self.assertIn("基线缺失", it["evidence"])


class TestEmptyLedger(DiffBaselineBase):
    """场景 6：两侧空账本 → 完全一致，退出码 0。"""

    def test_empty_identical_exit0(self):
        self._baseline_jsonl("shadow_ledger", [])
        self._source_jsonl(TEST_PAIRS[0][1], [])
        rep, _ = self._run()
        self.assertEqual(rep["exit_code"], 0)
        it = self._item(rep, "shadow_ledger")
        self.assertEqual(it["status"], db.IDENTICAL)
        self.assertEqual((it["old_lines"], it["new_lines"]), (0, 0))
        self.assertTrue(it["sha_equal"])


class TestCli(unittest.TestCase):
    """CLI 显式传参纪律 + 目录不存在 fail-visible。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="diff_baseline_cli_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_main_missing_dir_fails_visible(self):
        out_buf, err_buf = StringIO(), StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = db.main(
                [
                    "--baseline-dir",
                    str(Path(self._tmp) / "nope"),
                    "--data-root",
                    self._tmp,
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("不存在", err_buf.getvalue())

    def test_main_end_to_end(self):
        base = Path(self._tmp) / "base"
        data = Path(self._tmp) / "data"
        base.mkdir()
        data.mkdir()
        row = {"run_id": "r1", "ts": 1.0, "symbol": "600519"}
        (base / "shadow_ledger.jsonl.baseline").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (data / "shadow").mkdir()
        (data / "shadow" / "shadow_ledger.jsonl").write_text(
            json.dumps(row, ensure_ascii=False)
            + "\n"
            + json.dumps(
                {"run_id": "r2", "ts": 2.0, "symbol": "000858"}, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
        # CLI 需要处理全部真实账本对；其余账本两侧均缺失 → 中性
        orig = db.LEDGER_PAIRS
        db.LEDGER_PAIRS = TEST_PAIRS
        try:
            buf = StringIO()
            with redirect_stdout(buf):
                code = db.main(["--baseline-dir", str(base), "--data-root", str(data)])
            out = buf.getvalue()
        finally:
            db.LEDGER_PAIRS = orig
        self.assertEqual(code, 1)  # trace/checkpoint 两侧缺失 → 中性
        self.assertIn("run_id=r2", out)
        self.assertIn("账本基线对比", out)


if __name__ == "__main__":
    unittest.main()
