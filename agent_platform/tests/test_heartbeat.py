# -*- coding: utf-8 -*-
"""Paper 引擎心跳适配器测试（Paperclip 心跳对接第一刀，B 路）。

覆盖：NullProbe/FileProbe 行为、幂等键（同分钟二次不重复/跨分钟可再入）、
安全阀（无 root 拒写且不留半文件）、坏状态文件 fail-visible、
SHADOW_DATA_ROOT 隔离根继承、真仓 data/ 字节不变断言。
本测试绝不写 agent_platform/data/（全部落在 tempfile 隔离根）。
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "agent_platform" / "data"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hb = _load("adapters_heartbeat", "agent_platform/adapters/heartbeat.py")


def _snapshot(root):
    """目录快照（真仓零副作用断言用）。

    SQLite 主库及其 -wal/-shm 侧车字节会被前序测试遗留连接异步刷写
    （全量跑实测 artifacts.db-shm/checkpoints.db 自变），字节比对会产生
    与本模块无关的假阳性——故 DB 类文件只断言「路径集合不变」（新增/
    删除仍会暴露），非 DB 文件保持逐字节比对。"""
    root = Path(root)
    if not root.exists():
        return {}
    # P0 测试卫生：不再逐字节读真仓（855 文件/20MB，27 个用例 ×2 次 → teardown
    # 卡死在 dict diff）。改为 (size, mtime_ns) 元数据快照：仍能发现新增/删除/
    # 修改，但 I/O 从 GB 级降到毫秒级；DB 侧车仍只断言路径集合（异步刷写）。
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            volatile = (p.name.endswith(".db") or p.name.endswith("-wal")
                        or p.name.endswith("-shm"))
            if volatile:
                out[str(p)] = None
            else:
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def _sample(source="paper_engine", alive=True, detail="ok",
            checked_at="2026-10-18T07:05"):
    return {"source": source, "alive": alive, "detail": detail,
            "checked_at": checked_at}


def _probe_semantics(probe_result):
    """断言探针产出四字段齐全且 checked_at 合法。"""
    hb.validate_checked_at(probe_result["checked_at"])
    return probe_result


class _RealRepoSafe(unittest.TestCase):
    """每个测试前后断言真仓 agent_platform/data/ 字节不变。"""

    def setUp(self):
        self._snap = _snapshot(DATA)

    def tearDown(self):
        self.assertFalse((DATA / "heartbeat").exists(),
                         "真仓 agent_platform/data/heartbeat 被测试创建！")
        self.assertEqual(_snapshot(DATA), self._snap,
                         "真仓 agent_platform/data/ 被测试污染！")


class NullProbeTests(_RealRepoSafe):
    def test_null_probe_not_configured_semantics(self):
        r = hb.NullProbe().probe()
        self.assertFalse(r["alive"])
        self.assertEqual(r["detail"], "not_configured")
        self.assertEqual(r["source"], "null")
        _probe_semantics(r)

    def test_null_probe_checked_at_minute_format(self):
        r = hb.NullProbe().probe()
        self.assertEqual(r["checked_at"], hb.now_minute())

    def test_null_probe_sample_recordable_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            rep = hb.record_heartbeat(hb.NullProbe().probe(), td)
            self.assertEqual(rep["recorded"], 1)
            rows = json.loads(
                (Path(td) / "heartbeat" / "heartbeat.jsonl")
                .read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rows["schema_version"], "hb.v1")
            self.assertEqual(rows["source"], "null")
            self.assertFalse(rows["alive"])
            self.assertEqual(rows["detail"], "not_configured")


class FileProbeTests(_RealRepoSafe):
    def _write_state(self, td, raw):
        p = Path(td) / "state.json"
        p.write_text(raw, encoding="utf-8")
        return p

    def test_file_probe_reads_valid_state(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_state(td, json.dumps(
                {"alive": True, "detail": "engine up"}))
            r = hb.FileProbe(p).probe()
            self.assertTrue(r["alive"])
            self.assertEqual(r["detail"], "engine up")
            self.assertEqual(r["source"], "paperclip_org_shell")

    def test_file_probe_uses_state_checked_at(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_state(td, json.dumps(
                {"alive": False, "checked_at": "2026-10-18T06:59"}))
            r = hb.FileProbe(p).probe()
            self.assertEqual(r["checked_at"], "2026-10-18T06:59")

    def test_file_probe_missing_file_fails_visible(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(hb.HeartbeatError):
                hb.FileProbe(Path(td) / "nope.json").probe()

    def test_file_probe_bad_json_fails_visible(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_state(td, "{broken json")
            with self.assertRaises(hb.HeartbeatError):
                hb.FileProbe(p).probe()

    def test_file_probe_non_object_fails_visible(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_state(td, "[1, 2]")
            with self.assertRaises(hb.HeartbeatError):
                hb.FileProbe(p).probe()

    def test_file_probe_missing_alive_fails_visible(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_state(td, json.dumps({"detail": "ok"}))
            with self.assertRaises(hb.HeartbeatError):
                hb.FileProbe(p).probe()

    def test_file_probe_requires_explicit_state_file(self):
        with self.assertRaises(hb.HeartbeatError):
            hb.FileProbe(None)


class IdempotencyTests(_RealRepoSafe):
    def test_key_stable_shape(self):
        k1 = hb.heartbeat_key("paper_engine", "2026-10-18T07:05")
        k2 = hb.heartbeat_key("paper_engine", "2026-10-18T07:05")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 16)
        self.assertNotEqual(k1, hb.heartbeat_key("null", "2026-10-18T07:05"))
        self.assertNotEqual(k1, hb.heartbeat_key("paper_engine",
                                                 "2026-10-18T07:06"))

    def test_same_minute_replay_not_duplicated(self):
        # 同分钟同源二次采集 → 同键跳过（replayed），账本仍 1 行
        with tempfile.TemporaryDirectory() as td:
            rep1 = hb.record_heartbeat(_sample(), td)
            rep2 = hb.record_heartbeat(_sample(), td)
            self.assertEqual((rep1["recorded"], rep1["replayed"]), (1, 0))
            self.assertEqual((rep2["recorded"], rep2["replayed"]), (0, 1))
            p = Path(td) / "heartbeat" / "heartbeat.jsonl"
            self.assertEqual(len(p.read_text(encoding="utf-8")
                                 .splitlines()), 1)

    def test_cross_minute_recordable_again(self):
        # 幂等键含分钟维度：跨分钟必须可再入账
        with tempfile.TemporaryDirectory() as td:
            hb.record_heartbeat(_sample(checked_at="2026-10-18T07:05"), td)
            rep = hb.record_heartbeat(_sample(checked_at="2026-10-18T07:06"),
                                      td)
            self.assertEqual(rep["recorded"], 1)
            p = Path(td) / "heartbeat" / "heartbeat.jsonl"
            self.assertEqual(len(p.read_text(encoding="utf-8")
                                 .splitlines()), 2)

    def test_same_minute_different_source_both_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            hb.record_heartbeat(_sample(source="paper_engine"), td)
            rep = hb.record_heartbeat(_sample(source="bridge"), td)
            self.assertEqual(rep["recorded"], 1)
            p = Path(td) / "heartbeat" / "heartbeat.jsonl"
            self.assertEqual(len(p.read_text(encoding="utf-8")
                                 .splitlines()), 2)

    def test_existing_bad_lines_tolerated_on_key_scan(self):
        # 读侧坏行容忍（对齐 record_verdicts）：坏行不影响幂等判定
        with tempfile.TemporaryDirectory() as td:
            hb.record_heartbeat(_sample(), td)
            p = Path(td) / "heartbeat" / "heartbeat.jsonl"
            with open(p, "a", encoding="utf-8") as f:
                f.write("{broken\n")
            rep = hb.record_heartbeat(_sample(), td)
            self.assertEqual((rep["recorded"], rep["replayed"]), (0, 1))


class SafetyValveTests(_RealRepoSafe):
    def test_no_root_rejected(self):
        # M5 刀2 判例平移：无显式 root 拒绝落盘（fail-visible）
        with self.assertRaises(hb.HeartbeatError):
            hb.record_heartbeat(_sample(), None)

    def test_no_root_no_half_file_created(self):
        # 拒写零副作用：错误隔离根下不建目录不留半文件
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(hb.HeartbeatError):
                hb.record_heartbeat(_sample(), None)
            self.assertFalse((Path(td) / "heartbeat").exists())

    def test_bad_sample_rejected_before_write_no_dir(self):
        # 先全量校验后开写：坏样本不建目录不留半文件
        with tempfile.TemporaryDirectory() as td:
            bad = _sample(alive="yes")  # 非 bool
            with self.assertRaises(hb.HeartbeatError):
                hb.record_heartbeat(bad, td)
            self.assertFalse((Path(td) / "heartbeat").exists())

    def test_bad_checked_at_rejected(self):
        for bad in ("2026-10-18 07:05", "2026-10-18T07:05:00",
                    "2026-13-18T07:05", 20261018):
            with tempfile.TemporaryDirectory() as td:
                with self.assertRaises(hb.HeartbeatError):
                    hb.record_heartbeat(_sample(checked_at=bad), td)
                self.assertFalse((Path(td) / "heartbeat").exists())

    def test_resolve_root_explicit_wins_over_env(self):
        with tempfile.TemporaryDirectory() as td_env, \
                tempfile.TemporaryDirectory() as td_explicit:
            old = os.environ.get("SHADOW_DATA_ROOT")
            os.environ["SHADOW_DATA_ROOT"] = td_env
            try:
                self.assertEqual(hb.resolve_root(td_explicit),
                                 Path(td_explicit))
                self.assertEqual(hb.resolve_root(None), Path(td_env))
            finally:
                if old is None:
                    os.environ.pop("SHADOW_DATA_ROOT", None)
                else:
                    os.environ["SHADOW_DATA_ROOT"] = old

    def test_resolve_root_none_without_env(self):
        old = os.environ.pop("SHADOW_DATA_ROOT", None)
        try:
            self.assertIsNone(hb.resolve_root(None))
        finally:
            if old is not None:
                os.environ["SHADOW_DATA_ROOT"] = old


class IsolationRootTests(_RealRepoSafe):
    def test_env_root_inherited_by_collect_and_record(self):
        # 挂 SHADOW_DATA_ROOT 自动继承隔离根（run_shadow 同款）
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("SHADOW_DATA_ROOT")
            os.environ["SHADOW_DATA_ROOT"] = td
            try:
                rep = hb.collect_and_record(hb.NullProbe())
                self.assertEqual(rep["recorded"], 1)
                self.assertTrue(
                    (Path(td) / "heartbeat" / "heartbeat.jsonl").exists())
            finally:
                if old is None:
                    os.environ.pop("SHADOW_DATA_ROOT", None)
                else:
                    os.environ["SHADOW_DATA_ROOT"] = old

    def test_collect_without_root_or_env_rejected(self):
        old = os.environ.pop("SHADOW_DATA_ROOT", None)
        try:
            with self.assertRaises(hb.HeartbeatError):
                hb.collect_and_record(hb.NullProbe())
        finally:
            if old is not None:
                os.environ["SHADOW_DATA_ROOT"] = old


class CliTests(_RealRepoSafe):
    def test_cli_dry_run_prints_without_persist(self):
        # 无 --persist-root：打印心跳 JSON、退出码 0、stderr 提示 dry-run
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hb.main(["--probe", "null"])
        self.assertEqual(rc, 0)
        j = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(j["schema_version"], "hb.v1")
        self.assertEqual(j["source"], "null")
        self.assertIn("dry-run", err.getvalue())
        self.assertFalse((DATA / "heartbeat").exists())

    def test_cli_file_probe_persist_to_tmp_root(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text(json.dumps({"alive": True, "detail": "up"}),
                             encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = hb.main(["--probe", "file", "--state-file", str(state),
                              "--persist-root", td])
            self.assertEqual(rc, 0)
            rep = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(rep["recorded"], 1)
            self.assertTrue(
                (Path(td) / "heartbeat" / "heartbeat.jsonl").exists())

    def test_cli_bad_state_file_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "broken.json"
            state.write_text("{broken", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = hb.main(["--probe", "file", "--state-file",
                              str(state)])
            self.assertEqual(rc, 2)
            self.assertIn("FATAL", err.getvalue())

    def test_cli_file_probe_without_state_file_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hb.main(["--probe", "file"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
