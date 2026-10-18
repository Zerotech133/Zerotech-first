# -*- coding: utf-8 -*-
"""verify_strategy_ledger 三方对账工具测试（军团-M2 验收判据 1 闭环）。

临时目录构造小样本账本（对齐 test_monday_checkpoint_strategy_observe.py
的隔离根构造法），真仓零接触。覆盖：
  1. 全对账 PASS（三项全过 + CLI 退出码 0 + 纯只读字节自证）；
  2. verdict_key 手编辑篡改 → 对账项① FAIL；
  3. run_id 缺失（空值 / 影子无命中 / 影子账本缺失）→ 对账项② FAIL；
  4. 证据覆盖缺口 → 对账项③ FAIL（键集合同源缺陷修复后，证据侧
     从 evidence.db 实体提取：实体存在→PASS / 缺实体→FAIL /
     证据仓缺失→FAIL 且零建库写副作用）；
  5. 空账本中性 PASS（空=中性非通过，对齐周末 PENDING 口径）；
  6. CLI 显式 root 纪律：无 --data-root 拒绝运行（退出码 2）。
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "tools"))

import verify_strategy_ledger as vsl  # noqa: E402
from contracts import Evidence  # noqa: E402
from engine.evidence import EvidenceStore  # noqa: E402
from engine.strategy_registry import verdict_key  # noqa: E402

DAY = "2026-10-19"
RUN_A = "a1b2c3d4e5f6"
RUN_B = "b2c3d4e5f6a1"
RUN_C = "c3d4e5f6a1b2"  # 只在影子侧（合法 orphan，不计 FAIL）


def _verdict_row(sid, symbol, run_id, trade_date=DAY, **over):
    """按 strategy_registry.record_verdicts 落账 schema 构造判决行。"""
    row = {
        "verdict_key": verdict_key(sid, symbol, trade_date),
        "strategy_id": sid, "symbol": symbol,
        "recommendation": "buy", "confidence": 0.7, "stance": "bull",
        "score": 1.2, "rationale": "ma20 上方", "trace_id": "trc-" + sid,
        "run_id": run_id, "trade_date": trade_date,
        "recorded_at": "2026-10-19T17:30:00",
    }
    row.update(over)
    return row


def _shadow_row(symbol, run_id, trade_date=DAY):
    """按 run_shadow.build_ledger_row m5.v1 口径构造影子 done 行（最小键集）。"""
    return {"date": trade_date, "ts": 1.0, "symbol": symbol, "name": symbol,
            "status": "done", "schema_version": "m5.v1", "parse_ok": True,
            "recommendation": "buy", "confidence": 0.8, "score": 1.0,
            "degraded": False, "run_id": run_id}


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


def _evidence_row(vkey, run_id, trade_date=DAY):
    """按 evidence_collect._collect_strategy 落仓 schema 构造 strategy 证据行。"""
    return Evidence(
        source="strategy.runs",
        source_ref="strategy:%s" % vkey,
        statement="strategy=s_sma20 symbol=600510 rec=buy stance=bull "
                  "run_id=%s" % run_id,
        payload={"domain": "strategy",
                 "kind": "strategy_verdict_fingerprint",
                 "idempotency_key": vkey, "trade_date": trade_date})


class _Base(unittest.TestCase):
    """公用脚手架：临时隔离根 + stdout 捕获。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vsl_"))
        self._buf = io.StringIO()

    def build_ledger(self, verdict_rows, shadow_rows=None, shadow_missing=False):
        _write_jsonl(self.root / "strategy" / "strategy_runs.jsonl",
                     verdict_rows)
        if not shadow_missing and shadow_rows is not None:
            _write_jsonl(self.root / "shadow" / "shadow_ledger.jsonl",
                         shadow_rows)

    def build_evidence(self, pairs):
        """构造证据仓（evidence.db）：pairs = [(verdict_key, run_id), ...]。"""
        store = EvidenceStore(self.root / "evidence" / "evidence.db")
        try:
            for vkey, run_id in pairs:
                store.record(_evidence_row(vkey, run_id))
        finally:
            store.close()

    def run_cli(self, *argv):
        with redirect_stdout(self._buf):
            rc = vsl.main(list(argv))
        return rc

    def tearDown(self):
        pass


class AllPassTests(_Base):
    def test_full_pass(self):
        """三项全过 + 退出码 0（证据仓实体齐备）。"""
        rows = [
            _verdict_row("s_sma20", "600510", RUN_A),
            _verdict_row("s_dual_ma", "600511", RUN_B),
        ]
        self.build_ledger(rows, [_shadow_row("600510", RUN_A),
                                 _shadow_row("600511", RUN_B),
                                 _shadow_row("600512", RUN_C)])
        self.build_evidence([(r["verdict_key"], r["run_id"]) for r in rows])
        rep = vsl.verify(self.root)
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["neutral"])
        self.assertEqual(rep["rows"], 2)
        self.assertEqual(rep["evidence_keys"], 2)
        self.assertTrue(all(c["ok"] for c in rep["checks"]), rep["checks"])
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 0)
        self.assertIn("全部 PASS", self._buf.getvalue())

    def test_read_only_bytes_unchanged(self):
        """纯只读契约：对账前后三账本（strategy/shadow/evidence.db）字节零变化。"""
        rows = [_verdict_row("s_sma20", "600510", RUN_A)]
        self.build_ledger(rows, [_shadow_row("600510", RUN_A)])
        self.build_evidence([(rows[0]["verdict_key"], RUN_A)])
        sp = self.root / "strategy" / "strategy_runs.jsonl"
        hp = self.root / "shadow" / "shadow_ledger.jsonl"
        ep = self.root / "evidence" / "evidence.db"
        s_before, h_before, e_before = (sp.read_bytes(), hp.read_bytes(),
                                        ep.read_bytes())
        vsl.verify(self.root)
        self.assertEqual(sp.read_bytes(), s_before)
        self.assertEqual(hp.read_bytes(), h_before)
        self.assertEqual(ep.read_bytes(), e_before)


class KeyTamperTests(_Base):
    def test_tampered_verdict_key_detected(self):
        """对账项①：verdict_key 手编辑篡改必检出。"""
        good = _verdict_row("s_sma20", "600510", RUN_A)
        bad = _verdict_row("s_dual_ma", "600511", RUN_B,
                           verdict_key="0" * 16)  # 合法形态的错误键
        self.build_ledger([good, bad], [_shadow_row("600510", RUN_A),
                                        _shadow_row("600511", RUN_B)])
        rep = vsl.verify(self.root)
        self.assertFalse(rep["ok"])
        c1 = rep["checks"][0]
        self.assertEqual(c1["item"], "①verdict_key 重算一致")
        self.assertFalse(c1["ok"])
        self.assertIn("0" * 16, "".join(c1["diffs"]))
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 1)
        self.assertIn("存在 FAIL", self._buf.getvalue())

    def test_missing_key_fields_detected(self):
        """对账项①：strategy_id/symbol/trade_date 缺失 → 无法重算 → FAIL。"""
        row = _verdict_row("s_sma20", "600510", RUN_A)
        del row["trade_date"]
        self.build_ledger([row], [_shadow_row("600510", RUN_A)])
        rep = vsl.verify(self.root)
        self.assertFalse(rep["checks"][0]["ok"])
        self.assertIn("缺 trade_date", "".join(rep["checks"][0]["diffs"]))


class RunIdLinkTests(_Base):
    def _rows(self):
        return [_verdict_row("s_sma20", "600510", RUN_A)]

    def test_empty_run_id_detected(self):
        row = _verdict_row("s_sma20", "600510", "")
        self.build_ledger([row], [_shadow_row("600510", RUN_A)])
        rep = vsl.verify(self.root)
        c2 = rep["checks"][1]
        self.assertEqual(c2["item"], "②run_id 双向可查")
        self.assertFalse(c2["ok"])
        self.assertIn("run_id 为空", "".join(c2["diffs"]))
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 1)

    def test_run_id_not_in_shadow_detected(self):
        self.build_ledger(self._rows(), [_shadow_row("600599", RUN_B)])
        rep = vsl.verify(self.root)
        c2 = rep["checks"][1]
        self.assertFalse(c2["ok"])
        self.assertIn(RUN_A, "".join(c2["diffs"]))
        self.assertIn("未命中", "".join(c2["diffs"]))

    def test_shadow_ledger_missing_detected(self):
        self.build_ledger(self._rows(), shadow_missing=True)
        rep = vsl.verify(self.root)
        self.assertFalse(rep["checks"][1]["ok"])
        self.assertIn("影子账本不存在", "".join(rep["checks"][1]["diffs"]))


class EvidenceCoverageTests(_Base):
    """对账项③：证据侧键集合从 evidence.db 实体提取（异源），覆盖判定真实。"""

    def _row(self):
        return _verdict_row("s_sma20", "600510", RUN_A)

    def test_evidence_entity_present_pass(self):
        """证据仓有对应 strategy.runs 实体 → 对账项③ PASS。"""
        row = self._row()
        self.build_ledger([row], [_shadow_row("600510", RUN_A)])
        self.build_evidence([(row["verdict_key"], RUN_A)])
        rep = vsl.verify(self.root)
        c3 = rep["checks"][2]
        self.assertEqual(c3["item"], "③证据 idempotency_key 覆盖")
        self.assertTrue(c3["ok"], c3["diffs"])
        self.assertEqual(c3["diffs"], [])
        self.assertEqual(rep["evidence_keys"], 1)

    def test_evidence_entity_missing_fail(self):
        """账本 run 的 verdict_key 无对应证据实体（未收编/漂移）→ FAIL。"""
        row_a = _verdict_row("s_sma20", "600510", RUN_A)
        row_b = _verdict_row("s_dual_ma", "600511", RUN_B)
        self.build_ledger([row_a, row_b],
                          [_shadow_row("600510", RUN_A),
                           _shadow_row("600511", RUN_B)])
        # 证据仓只收编了 RUN_A 的判决，RUN_B 无证据实体
        self.build_evidence([(row_a["verdict_key"], RUN_A)])
        rep = vsl.verify(self.root)
        c3 = rep["checks"][2]
        self.assertFalse(c3["ok"])
        joined = "".join(c3["diffs"])
        self.assertIn(row_b["verdict_key"], joined)
        self.assertIn("未被证据仓", joined)
        self.assertNotIn(row_a["verdict_key"], joined)
        self.assertFalse(rep["ok"])
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 1)
        self.assertIn("存在 FAIL", self._buf.getvalue())

    def test_evidence_db_missing_fail_without_creation(self):
        """证据仓缺失 → FAIL（有判决行即有覆盖缺口）；零建库写副作用。"""
        row = self._row()
        self.build_ledger([row], [_shadow_row("600510", RUN_A)])
        rep = vsl.verify(self.root)
        c3 = rep["checks"][2]
        self.assertFalse(c3["ok"])
        self.assertIn("证据仓不存在", "".join(c3["diffs"]))
        self.assertFalse(rep["ok"])
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 1)
        # 只读红线：工具不创建 evidence.db
        self.assertFalse((self.root / "evidence" / "evidence.db").exists())

    def test_gap_detected(self):
        """对账项③：verdict_key 缺失行在证据侧坍缩成空键 → 覆盖缺口 FAIL。"""
        row = self._row()
        del row["verdict_key"]
        self.build_ledger([row], [_shadow_row("600510", RUN_A)])
        # 证据仓非空（含他键实体），空键行依然无处落 → 覆盖缺口
        self.build_evidence([("f" * 16, RUN_A)])
        rep = vsl.verify(self.root)
        c3 = rep["checks"][2]
        self.assertEqual(c3["item"], "③证据 idempotency_key 覆盖")
        self.assertFalse(c3["ok"])
        self.assertIn("覆盖缺口", "".join(c3["diffs"]))
        self.assertFalse(rep["ok"])
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 1)


class EmptyNeutralTests(_Base):
    def test_missing_file_is_neutral_pass(self):
        """空账本（文件缺失）= 中性非通过，退出码 0。"""
        rep = vsl.verify(self.root)
        self.assertTrue(rep["neutral"])
        self.assertTrue(rep["ok"])
        self.assertTrue(all(c["ok"] for c in rep["checks"]))
        self.assertEqual(self.run_cli("--data-root", str(self.root)), 0)
        self.assertIn("中性", self._buf.getvalue())
        self.assertIn("PENDING", self._buf.getvalue())

    def test_zero_good_rows_is_neutral_pass(self):
        """文件存在但零好行（全坏行）同样中性——首跑前不得误报。"""
        p = self.root / "strategy" / "strategy_runs.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not-json\n", encoding="utf-8")
        rep = vsl.verify(self.root)
        self.assertTrue(rep["neutral"])
        self.assertTrue(rep["ok"])


class CliDisciplineTests(_Base):
    def test_no_root_refused(self):
        """显式 root 纪律：无 --data-root 拒绝运行（argparse exit 2）。"""
        with self.assertRaises(SystemExit) as cm:
            vsl.main([])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    sys.exit(unittest.main())
