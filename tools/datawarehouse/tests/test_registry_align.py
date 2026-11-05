# -*- coding: utf-8 -*-
"""registry_align 单测（2026-11-03 task-81）：全部 tmp 目录，不读/写生产数据根。

覆盖：一致 OK、catalog 升版/registry 旧（sha+缺表）不一致、--check 零写入、坏 JSON fail-visible、
--apply 备份+重建+metadata PASS+kg 日志、锁占用/陈旧锁回收、写后校验失败自动回滚、kg 前置阻断。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402
import registry_align as ra  # noqa: E402


def _make_env(td: Path, *, stale: bool = False, tables=("t1", "t2")):
    """构造 catalog + registry(一致/旧)；表 store 真实存在以过 metadata_consistency。"""
    data = td / "data"
    for t in tables:
        (data / t).mkdir(parents=True, exist_ok=True)
    cat = {"version": 35, "updated": "2026-11-03T06:25:30+08:00",
           "tables": {t: {"source": "tdx", "layout": "part", "store": str(data / t),
                          "freq": "D", "schema": ["trade_date", "x"],
                          "date_col": "trade_date", "code_col": "ts_code",
                          "primary_key": ["ts_code", "trade_date"],
                          "manifest": str(data / "_manifest.json"),
                          "manifest_status": "closed"}
                      for t in tables}}
    cp = td / "catalog.json"
    cp.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    sha = hashlib.sha256(cp.read_bytes()).hexdigest()
    if stale:
        # 模拟「catalog 已升版、registry 旧」：旧 sha + 只覆盖 t1 + 计数旧
        reg_tables = ["t1"]
        reg = {"generated_at": "2026-11-03T05:07:35.920109+08:00",
               "catalog_sha256": "0" * 64, "physical_count": 99, "display_count": 0,
               "display_assets": [], "limitations": [],
               "physical_tables": [{"asset_id": "t1", "physical_tables": ["t1"],
                                    "qualification_status": "unknown"}]}
    else:
        reg_tables = list(tables)
        reg = {"generated_at": "2026-11-03T06:30:00+08:00",
               "catalog_sha256": sha, "physical_count": len(reg_tables),
               "display_count": 0, "display_assets": [], "limitations": [],
               "physical_tables": [{"asset_id": t, "physical_tables": [t],
                                    "qualification_status": "unknown"}
                                   for t in reg_tables]}
    rp = td / "registry.json"
    rp.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    kg = td / "kg_sync_task.log"
    kg.write_text("catalog_build exit=0 2026-11-03T06:25:31\n", encoding="utf-8")
    return cp, rp, kg


class TestRegistryAlign(unittest.TestCase):
    def test_check_aligned_ok_and_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td))
            before = rp.read_text(encoding="utf-8")
            mtime = rp.stat().st_mtime_ns
            res = ra.run("check", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertTrue(res["match_before"])
            self.assertEqual(res["diffs_before"], [])
            self.assertFalse((Path(td) / "lock").exists())  # check 不取锁
            self.assertEqual(rp.read_text(encoding="utf-8"), before)  # 零写入
            self.assertEqual(rp.stat().st_mtime_ns, mtime)
            self.assertEqual(kg.read_text(encoding="utf-8").count("registry_align"), 0)

    def test_check_detects_catalog_upgraded_registry_stale(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            res = ra.run("check", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 1, res)
            self.assertFalse(res["match_before"])
            kinds = {d["kind"] for d in res["diffs_before"]}
            self.assertIn("catalog_sha256", kinds)
            self.assertIn("catalog_tables_missing_from_registry", kinds)
            self.assertIn("physical_count_self_inconsistent", kinds)

    def test_check_bad_json_fail_visible(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td))
            rp.write_text("{not valid json", encoding="utf-8")
            res = ra.run("check", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 2, res)
            self.assertIn("不可读", res["error"])
            res2 = ra.run("check", lock_path=Path(td) / "lock", kg_log=kg,
                          catalog_path=Path(td) / "nope_catalog.json", registry_path=rp)
            self.assertEqual(res2["exit_code"], 2)
            self.assertIn("不存在", res2["error"])

    def test_apply_aligns_backup_kg_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            lock = Path(td) / "registry_align.lock"
            res = ra.run("apply", lock_path=lock, kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertTrue(res["match_after"])
            self.assertTrue(res["rebuild"]["performed"])
            self.assertTrue((Path(td) / "registry.json.bak-").parent.exists())
            backups = list(Path(td).glob("registry.json.bak-*"))
            self.assertEqual(len(backups), 1)
            data = json.loads(rp.read_text(encoding="utf-8"))
            self.assertEqual(data["catalog_sha256"], res["catalog"]["sha256"])
            self.assertEqual(data["physical_count"], 2)
            self.assertFalse(lock.exists())  # 锁已释放
            meta = res["metadata"]
            self.assertEqual(meta["code"], 0, meta)
            self.assertEqual(meta["physical_count"], 2)
            log = kg.read_text(encoding="utf-8")
            self.assertIn("registry_align date=", log)
            self.assertIn("exit=0", log)

    def test_apply_lock_busy(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            lock = Path(td) / "lock"
            lock.write_text(json.dumps({"tool": "registry_align", "pid": 1,
                                        "ts": time.time()}), encoding="utf-8")
            before = rp.read_text(encoding="utf-8")
            res = ra.run("apply", lock_path=lock, kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 2, res)
            self.assertIn("锁占用", res["error"])
            self.assertEqual(rp.read_text(encoding="utf-8"), before)  # 未写盘

    def test_apply_stale_lock_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            lock = Path(td) / "lock"
            lock.write_text(json.dumps({"tool": "registry_align", "pid": 1,
                                        "ts": time.time() - 3 * 3600}), encoding="utf-8")
            res = ra.run("apply", lock_path=lock, kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertFalse(lock.exists())

    def test_apply_validation_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            original = rp.read_text(encoding="utf-8")
            bad_meta = {"tool": "metadata_consistency", "code": 1,
                        "issues": ["simulated"], "warnings": []}
            with mock.patch.object(ra.mc, "check", return_value=bad_meta):
                res = ra.run("apply", lock_path=Path(td) / "lock", kg_log=kg,
                             catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 1, res)
            self.assertTrue(res["rolled_back"])
            self.assertEqual(rp.read_text(encoding="utf-8"), original)  # 已回滚
            self.assertTrue(list(Path(td).glob("registry.json.bak-*")))
            self.assertIn("已回滚", res["error"])

    def test_apply_succeeds_with_retained_orphan_entries(self):
        """registry_rebuild 保守保留孤儿条目 → physical_count > catalog 表数时仍应成功。

        回归 2026-11-05：原实现把 catalog 表数当期望 physical_count，导致
        factor_list 登记后 apply 永远回滚（aligned=True/verify=True/metadata=0）。
        """
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            reg = json.loads(rp.read_text(encoding="utf-8"))
            reg["physical_tables"].append({
                "asset_id": "renamed_old_asset", "physical_tables": ["t1"],
                "qualification_status": "unknown"})
            reg["physical_count"] = len(reg["physical_tables"])  # 2（t1 + 孤儿）
            rp.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
            res = ra.run("apply", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertTrue(res["match_after"])
            data = json.loads(rp.read_text(encoding="utf-8"))
            self.assertEqual(data["physical_count"], 3)  # t1 保留 + t2 新增 + 孤儿
            ids = {e.get("asset_id") for e in data["physical_tables"]}
            self.assertIn("renamed_old_asset", ids)  # 孤儿未被丢弃
            self.assertEqual(data["catalog_sha256"], res["catalog"]["sha256"])
            self.assertEqual(res["metadata"]["code"], 0)
            self.assertIn("registry_vs_catalog_table_count_diff:3/2",
                          res["metadata"]["warnings"])

    def test_apply_kg_log_precondition_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td), stale=True)
            kg.write_text("kg_sync ABORT at kg_features\n", encoding="utf-8")
            before = rp.read_text(encoding="utf-8")
            res = ra.run("apply", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 2, res)
            self.assertIn("kg_sync_task.log 前置检查", res["error"])
            self.assertEqual(rp.read_text(encoding="utf-8"), before)

    def test_apply_already_aligned_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, kg = _make_env(Path(td))
            res = ra.run("apply", lock_path=Path(td) / "lock", kg_log=kg,
                         catalog_path=cp, registry_path=rp)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertFalse(res["rebuild"]["performed"])
            self.assertEqual(res["rebuild"]["reason"], "already_aligned")
            self.assertFalse(list(Path(td).glob("registry.json.bak-*")))  # 无备份=未写
            self.assertEqual(res["metadata"]["code"], 0)


if __name__ == "__main__":
    unittest.main()
