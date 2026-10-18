# -*- coding: utf-8 -*-
"""check_observation_point 周一观察点一键巡检测试。

临时目录构造隔离 root（对齐 test_verify_strategy_ledger.py 的隔离根
构造法），真仓零接触。覆盖四类场景：
  1. 全绿：五判据全 PASS，退出码 0（含纯只读字节自证）；
  2. 缺日志：空 root 全 PENDING，退出码 1（中性——首跑/巡检未到）；
  3. 空账本中性：strategy_runs 空文件 → b/e 双 PENDING 不拉 FAIL；
  4. 账本篡改：verdict_key 手编辑 → 判据 e FAIL，退出码 2；
  附：CLI 显式 root 纪律——无 --data-root 拒绝运行（退出码 2）。
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "tools"))

import check_observation_point as cop  # noqa: E402
from contracts import Evidence  # noqa: E402
from engine.evidence import EvidenceStore  # noqa: E402
from engine.strategy_registry import verdict_key  # noqa: E402

DAY = "2026-10-19"
RUN_A = "a1b2c3d4e5f6"


def _verdict_row(sid, symbol, run_id, trade_date=DAY, **over):
    """按 strategy_registry.record_verdicts 落账 schema 构造判决行。"""
    row = {
        "verdict_key": verdict_key(sid, symbol, trade_date),
        "strategy_id": sid, "symbol": symbol,
        "recommendation": "buy", "confidence": 0.7, "stance": "bull",
        "run_id": run_id, "trade_date": trade_date,
        "recorded_at": "2026-10-19T17:30:00",
    }
    row.update(over)
    return row


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


class CheckObservationPointBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cop_test_")
        self.root = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _make_green(self):
        """构造五判据全绿的最小数据面。"""
        (self.root / "shadow").mkdir(parents=True, exist_ok=True)
        (self.root / "shadow" / "last_run.log").write_text(
            "[strategy] codex 桥接插件 +1 (skip 0)\n"
            "[strategy] 600519 产出 4 条 recorded=4 replayed=0\n"
            "[done] 600519 HOLD %s\n" % RUN_A, encoding="utf-8")
        _write_jsonl(self.root / "strategy" / "strategy_runs.jsonl",
                     [_verdict_row("codex_sma20_rsi14", "600519", RUN_A)])
        _write_jsonl(self.root / "shadow" / "shadow_ledger.jsonl",
                     [{"schema_version": "m5.v1", "run_id": RUN_A,
                       "symbol": "600519"}])
        (self.root / "shadow" / "monday_checkpoint.log").write_text(
            "[observe] strategy_runs：1 行 / 带 run_id 1 行——观察项\n"
            "[observe] codex_registrations：1 行 / strategy_id: "
            "codex_sma20_rsi14——观察项\n", encoding="utf-8")
        # 判据 e 全绿还需证据仓有对应 strategy.runs 实体——对账项③
        # 修复后真实读取 evidence.db（同源恒真缺陷 B2），缺实体即 FAIL
        row = _verdict_row("codex_sma20_rsi14", "600519", RUN_A)
        store = EvidenceStore(self.root / "evidence" / "evidence.db")
        try:
            store.record(Evidence(
                source="strategy.runs",
                source_ref="strategy:%s" % row["verdict_key"],
                statement="strategy=codex_sma20_rsi14 symbol=600519 rec=buy "
                          "stance=bull run_id=%s" % RUN_A,
                payload={"domain": "strategy",
                         "kind": "strategy_verdict_fingerprint",
                         "idempotency_key": row["verdict_key"],
                         "trade_date": DAY}))
        finally:
            store.close()

    def _run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cop.main(list(argv))
        return code, out.getvalue()


class TestAllGreen(CheckObservationPointBase):
    """场景 1：全绿 → 退出码 0。"""

    def test_all_pass_exit_0(self):
        self._make_green()
        code, out = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 0)
        rep = cop.run_checks(self.root)
        self.assertEqual([i["status"] for i in rep["items"]],
                         ["PASS"] * 5)
        self.assertIn("PASS] a_last_run_marker", out)
        self.assertIn("PASS] e_verdict_key_reconcile", out)

    def test_readonly_no_write(self):
        """纯只读自证：巡检前后字节快照一致（含 mtime 不新增文件）。

        豁免 evidence.db-shm/-wal：mode=ro 打开 WAL 库时 SQLite 惰性
        创建的读路径侧文件（engine/evidence.py 只读投影判例同款，
        真仓常驻存在）；evidence.db 本体字节仍须零变化。
        """
        self._make_green()
        skip = ("evidence.db-shm", "evidence.db-wal")
        before = sorted(
            (str(p.relative_to(self.root)), p.stat().st_size, p.stat().st_mtime_ns)
            for p in self.root.rglob("*") if p.is_file() and p.name not in skip)
        db_before = (self.root / "evidence" / "evidence.db").read_bytes()
        cop.run_checks(self.root)
        after = sorted(
            (str(p.relative_to(self.root)), p.stat().st_size, p.stat().st_mtime_ns)
            for p in self.root.rglob("*") if p.is_file() and p.name not in skip)
        self.assertEqual(before, after)
        self.assertEqual((self.root / "evidence" / "evidence.db").read_bytes(),
                         db_before)


class TestMissingLogs(CheckObservationPointBase):
    """场景 2：缺日志（空 root）→ 全 PENDING，退出码 1。"""

    def test_empty_root_all_pending_exit_1(self):
        code, out = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 1)
        rep = cop.run_checks(self.root)
        self.assertEqual(rep["fail"], 0)
        self.assertEqual(rep["pass"], 0)
        self.assertEqual(rep["pending"], 5)
        for i in rep["items"]:
            self.assertEqual(i["status"], "PENDING")
        self.assertIn("全 PENDING", out)


class TestEmptyLedgerNeutral(CheckObservationPointBase):
    """场景 3：空账本中性——零好行不拉 FAIL。"""

    def test_empty_strategy_runs_pending_not_fail(self):
        self._make_green()
        (self.root / "strategy" / "strategy_runs.jsonl").write_text(
            "", encoding="utf-8")
        code, _ = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 0)  # b/e 双 PENDING，其余 PASS，无 FAIL
        rep = cop.run_checks(self.root)
        st = {i["name"]: i["status"] for i in rep["items"]}
        self.assertEqual(st["b_strategy_runs_anchored"], "PENDING")
        self.assertEqual(st["e_verdict_key_reconcile"], "PENDING")
        self.assertEqual(rep["fail"], 0)


class TestTamperedLedger(CheckObservationPointBase):
    """场景 4：账本篡改 → 判据 e FAIL，退出码 2。"""

    def test_tampered_verdict_key_exit_2(self):
        self._make_green()
        rows = [_verdict_row("codex_sma20_rsi14", "600519", RUN_A)]
        rows[0]["verdict_key"] = "0" * 16  # 手编辑篡改幂等键
        _write_jsonl(self.root / "strategy" / "strategy_runs.jsonl", rows)
        code, out = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 2)
        rep = cop.run_checks(self.root)
        self.assertEqual(rep["fail"], 1)
        e_item = next(i for i in rep["items"] if i["name"] == "e_verdict_key_reconcile")
        self.assertEqual(e_item["status"], "FAIL")
        self.assertIn("FAIL] e_verdict_key_reconcile", out)

    def test_anchor_missing_run_id_exit_2(self):
        """好行全部不带 run_id（契约缺失）→ 判据 b FAIL，退出码 2。"""
        self._make_green()
        rows = [_verdict_row("codex_sma20_rsi14", "600519", RUN_A)]
        rows[0].pop("run_id")
        _write_jsonl(self.root / "strategy" / "strategy_runs.jsonl", rows)
        code, _ = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 2)
        rep = cop.run_checks(self.root)
        b_item = next(i for i in rep["items"] if i["name"] == "b_strategy_runs_anchored")
        self.assertEqual(b_item["status"], "FAIL")


class TestCliRootDiscipline(CheckObservationPointBase):
    """显式 root 纪律：无 --data-root 拒绝运行。"""

    def test_missing_data_root_rejected(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                cop.main([])
        self.assertEqual(cm.exception.code, 2)  # argparse 缺必填参数


class TestCheckpointLogSemantics(CheckObservationPointBase):
    """判据 c 三态：全有 PASS / 恰缺一 FAIL / 全无 PENDING。"""

    def test_only_one_mark_is_fail(self):
        self._make_green()
        (self.root / "shadow" / "monday_checkpoint.log").write_text(
            "[observe] strategy_runs：1 行 / 带 run_id 1 行——观察项\n",
            encoding="utf-8")
        code, _ = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 2)
        rep = cop.run_checks(self.root)
        c_item = next(i for i in rep["items"] if i["name"] == "c_checkpoint_observe_sections")
        self.assertEqual(c_item["status"], "FAIL")

    def test_stale_log_without_both_marks_is_pending(self):
        """真仓实证形态：历史 log 只有旧版 tasks.json 段 → PENDING 非 FAIL。"""
        self._make_green()
        (self.root / "shadow" / "monday_checkpoint.log").write_text(
            "[observe] tasks.json schema：228 工单 / ERROR 0 / WARN 13\n",
            encoding="utf-8")
        code, _ = self._run_cli("--data-root", str(self.root))
        self.assertEqual(code, 0)
        rep = cop.run_checks(self.root)
        c_item = next(i for i in rep["items"] if i["name"] == "c_checkpoint_observe_sections")
        self.assertEqual(c_item["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
