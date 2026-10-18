# -*- coding: utf-8 -*-
"""B 路追加观察项测试：monday_checkpoint 的 strategy 域首跑可见性（2026-10-18）。

铁律：只增不改——门 3 的 11 项判据与输出格式冻结。本文件只锁两件事：
  1. 两个新观察项（strategy_runs / codex_registrations）自身的契约：
     只读、缺文件=PENDING 中性、坏行跳过计数、异常 fail-visible、
     永不进入 RESULTS / 不影响退出码；
  2. 既有判据输出零变化的回归自证：RESULTS 与 [PASS]/[FAIL] 行一一对应，
     观察项行不改变判据计数。
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "engine"))

import monday_checkpoint as mc  # noqa: E402

DAY = "2026-10-20"  # 周二（非周一，等待循环必不触发）


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


class _Capture(unittest.TestCase):
    """公用脚手架：捕获 stdout、快照 RESULTS。"""

    def setUp(self):
        import io
        import tempfile
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mcobs_"))
        self._old_out = sys.stdout
        self._old_results = list(mc.RESULTS)
        self._buf = io.StringIO()
        sys.stdout = self._buf
        # 快照后清空：先跑的测试（如 log 文件的 check("样例检查")）会向全局
        # RESULTS 塞条目且不清理——不清空则全量/单跑顺序不同导致计数漂移。
        del mc.RESULTS[:]

    def tearDown(self):
        sys.stdout = self._old_out
        del mc.RESULTS[:]
        mc.RESULTS.extend(self._old_results)

    @property
    def out(self):
        # main() 内 _tee_log() 会把 sys.stdout 换成 _Tee（内层仍是本 buffer），
        # 所以必须持有 buffer 直接引用，而非读当前 sys.stdout。
        return self._buf.getvalue()


# ---------------------------------------------------------------- 常量契约

class ObservePathContractTests(_Capture):
    def test_strategy_runs_path_is_agent_platform_own_data(self):
        """root 语义与现有账本观察面一致：agent_platform 自身 data/ 下。"""
        self.assertTrue(mc.STRATEGY_RUNS.as_posix().endswith(
            "data/strategy/strategy_runs.jsonl"), mc.STRATEGY_RUNS)
        self.assertTrue(mc.CODEX_REGS.as_posix().endswith(
            "data/codex_bridge/registrations.jsonl"), mc.CODEX_REGS)


# ------------------------------------------------------- strategy_runs 观察项

class StrategyRunsObserveTests(_Capture):
    def test_missing_file_is_pending_neutral(self):
        p = self.tmpdir / "strategy_runs.jsonl"  # 不存在
        mc._strategy_runs_observe(p)
        self.assertIn("PENDING 中性", self.out)
        self.assertIn("周一 17:10 首跑后应存在", self.out)
        self.assertIn("不计入退出码", self.out)

    def test_present_reports_row_and_m5_counts(self):
        p = self.tmpdir / "strategy_runs.jsonl"
        _write_jsonl(p, [
            {"strategy_id": "s1", "schema_version": "m5.v1"},
            {"strategy_id": "s2", "schema_version": "m5.v1"},
            {"strategy_id": "s3", "schema_version": "v0"},
        ])
        mc._strategy_runs_observe(p)
        self.assertIn("strategy_runs：3 行 / m5.v1 2 行", self.out)

    def test_bad_line_counted_and_skipped(self):
        p = self.tmpdir / "strategy_runs.jsonl"
        _write_jsonl(p, [{"strategy_id": "s1", "schema_version": "m5.v1"}])
        with p.open("a", encoding="utf-8") as f:
            f.write('{"strategy_id": "broken",\n')  # 半行
        mc._strategy_runs_observe(p)
        self.assertIn("有 1 行损坏", self.out), "坏行必须显式计数（fail-visible）"
        self.assertIn("strategy_runs：1 行 / m5.v1 1 行", self.out)

    def test_unreadable_path_fails_visible_not_raises(self):
        d = self.tmpdir / "not_a_file"
        d.mkdir()  # 目录：read_text 必抛
        mc._strategy_runs_observe(d)
        self.assertIn("不可读", self.out)
        self.assertIn("不计入退出码", self.out)


# -------------------------------------------------- codex_registrations 观察项

class CodexRegsObserveTests(_Capture):
    def test_missing_file_is_pending_neutral(self):
        p = self.tmpdir / "registrations.jsonl"
        mc._codex_regs_observe(p)
        self.assertIn("codex_registrations：文件不存在（PENDING 中性）", self.out)

    def test_present_reports_rows_and_strategy_ids(self):
        p = self.tmpdir / "registrations.jsonl"
        _write_jsonl(p, [
            {"submit_id": "a", "strategy_id": "codex_sma20_rsi14"},
            {"submit_id": "b", "strategy_id": "codex_dual_ma"},
        ])
        mc._codex_regs_observe(p)
        self.assertIn("codex_registrations：2 行", self.out)
        self.assertIn("codex_sma20_rsi14", self.out)
        self.assertIn("codex_dual_ma", self.out)

    def test_bad_line_counted_good_rows_still_parsed(self):
        p = self.tmpdir / "registrations.jsonl"
        _write_jsonl(p, [{"strategy_id": "codex_sma20_rsi14"}])
        with p.open("a", encoding="utf-8") as f:
            f.write("not-json-at-all\n")
        mc._codex_regs_observe(p)
        self.assertIn("有 1 行损坏", self.out)
        self.assertIn("codex_registrations：1 行", self.out)


# ------------------------------------------------- 只读 + 退出码不变量

class ReadOnlyAndResultsInvariantTests(_Capture):
    def test_observe_items_are_read_only(self):
        """调用前后账本字节零变化（纯只读契约）。"""
        sp = self.tmpdir / "strategy_runs.jsonl"
        cp = self.tmpdir / "registrations.jsonl"
        _write_jsonl(sp, [{"strategy_id": "s1", "schema_version": "m5.v1"}])
        _write_jsonl(cp, [{"strategy_id": "c1"}])
        s_before = sp.read_bytes()
        c_before = cp.read_bytes()
        mc._strategy_runs_observe(sp)
        mc._codex_regs_observe(cp)
        self.assertEqual(sp.read_bytes(), s_before)
        self.assertEqual(cp.read_bytes(), c_before)

    def test_observe_items_never_touch_results(self):
        """观察项永不 append 进 RESULTS（退出码与判据计数冻结的根据）。"""
        n0 = len(mc.RESULTS)
        sp = self.tmpdir / "missing_strategy.jsonl"   # 缺失分支
        cp = self.tmpdir / "regs.jsonl"               # 存在分支
        _write_jsonl(cp, [{"strategy_id": "c1"}])
        mc._strategy_runs_observe(sp)
        mc._codex_regs_observe(cp)
        self.assertEqual(len(mc.RESULTS), n0)


# ------------------------------------------------- main() 集成回归

class MainIntegrationTests(_Capture):
    def test_main_emits_observes_without_changing_gate_semantics(self):
        """★回归自证：真跑 main()——

        · 两个观察项行出现在输出且标注「不计入退出码」；
        · [PASS]/[FAIL] 行数与汇总「结论: X/Y PASS」中的 Y 严格相等——
          观察项行不改变判据计数（11 项门判据零变化的结构性证明）。
        """
        import datetime as _dt

        ledger = self.tmpdir / "shadow_ledger.jsonl"
        _write_jsonl(ledger, [
            {"date": DAY, "symbol": "60051%d" % i, "status": "done",
             "schema_version": "m5.v1",
             **{f: "x" for f in mc.DK_FIELDS}}
            for i in range(mc.BASKET_N)])

        class _Tuesday(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date(2026, 10, 20)

        saved = (mc.LEDGER, mc.PERF, mc.LOG, mc._LOG_HANDLE,
                 mc.STRATEGY_RUNS, mc.CODEX_REGS, mc.date)
        try:
            mc.LEDGER = ledger
            mc.PERF = self.tmpdir / "perf.jsonl"
            mc.LOG = self.tmpdir / "cp.log"
            mc._LOG_HANDLE = None
            sp = self.tmpdir / "strategy_runs.jsonl"
            _write_jsonl(sp, [{"schema_version": "m5.v1"}])
            mc.STRATEGY_RUNS = sp
            mc.CODEX_REGS = self.tmpdir / "missing_regs.jsonl"
            mc.date = _Tuesday
            rc = mc.main()
        finally:
            mc._close_log()
            (mc.LEDGER, mc.PERF, mc.LOG, mc._LOG_HANDLE,
             mc.STRATEGY_RUNS, mc.CODEX_REGS, mc.date) = saved

        out = self.out
        self.assertIn("[observe] strategy_runs：1 行 / m5.v1 1 行", out)
        self.assertIn("[observe] codex_registrations：文件不存在（PENDING 中性）", out)
        n_gate_lines = sum(
            1 for ln in out.splitlines()
            if ln.startswith("[PASS]") or ln.startswith("[FAIL]"))
        n_results = len(mc.RESULTS)
        self.assertEqual(n_gate_lines, n_results,
                         "判据行与 RESULTS 必须一一对应（观察项不在内）")
        self.assertIn("结论: %d/%d PASS" % (n_results - sum(
            1 for _, ok, _ in mc.RESULTS if not ok), n_results), out)
        self.assertIn(rc, (0, 1))


if __name__ == "__main__":
    sys.exit(unittest.main())
