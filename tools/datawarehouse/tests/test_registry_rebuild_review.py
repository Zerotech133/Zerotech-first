# -*- coding: utf-8 -*-
"""registry_rebuild 审查回归测试（2026-11-02 task-77）。

覆盖：lifecycle 覆盖解析失败可见化（不静默/不落半套状态）、LIMITATIONS 文案不写死过期条数、
apply 阶段「registry 已写盘但 kg 日志补记失败」的可辨别处理（written=true + rc=WARN）。
全部使用 tempfile 夹具，不读写生产数据根。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402
import registry_rebuild as rr  # noqa: E402


def _make_env(td: Path):
    cat = {"version": 28, "updated": "2026-11-02T00:00:00+08:00", "tables": {
        "t_daily": {"source": "tushare", "freq": "D", "schema": ["trade_date", "x"],
                    "date_col": "trade_date", "code_col": "ts_code",
                    "primary_key": ["ts_code", "trade_date"]},
        "planned_x": {"source": "xueqiu", "freq": "D", "schema": ["a"],
                      "date_col": None, "primary_key": []},
    }}
    cp = td / "catalog.json"
    cp.write_text(json.dumps(cat), encoding="utf-8")
    sha = hashlib.sha256(cp.read_bytes()).hexdigest()
    reg = {"generated_at": "2026-11-01T00:00:00+08:00", "catalog_sha256": sha,
           "physical_count": 1, "display_count": 0, "display_assets": [],
           "limitations": [], "physical_tables": [
               {"asset_id": "t_daily", "physical_tables": ["t_daily"],
                "qualification_status": "unknown"}]}
    rp = td / "registry.json"
    rp.write_text(json.dumps(reg), encoding="utf-8")
    return cp, rp, cat, reg


class TestRegistryRebuildReview(unittest.TestCase):
    def test_lifecycle_failure_visible_and_no_partial_status(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, cat, reg = _make_env(Path(td))
            fake = mock.MagicMock()
            fake.status_for.side_effect = RuntimeError("lifecycle boom")
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.dict(sys.modules, {"lifecycle": fake}):
                built = rr.build_payload(cat, reg)
            stats = built["stats"]
            self.assertIn("lifecycle boom", stats["lifecycle_warning"])
            statuses = [e.get("qualification_status")
                        for e in built["registry"]["physical_tables"]]
            self.assertNotIn("planned", statuses)  # 失败时不落半套生命周期状态
            self.assertNotIn("deprecated", statuses)

    def test_lifecycle_success_and_stale_limitations_text(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, cat, reg = _make_env(Path(td))
            fake = mock.MagicMock()
            fake.status_for.side_effect = lambda t: "planned" if t == "planned_x" else "active"
            fake.reason_for.return_value = "测试规划"
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.dict(sys.modules, {"lifecycle": fake}):
                built = rr.build_payload(cat, reg)
            self.assertEqual(built["stats"]["lifecycle_warning"], "")
            self.assertEqual(built["stats"]["lifecycle_counts"], {"planned": 1})
            by_id = {e["asset_id"]: e for e in built["registry"]["physical_tables"]}
            self.assertEqual(by_id["planned_x"]["qualification_status"], "planned")
            limits = "\n".join(built["registry"]["limitations"])
            self.assertIn("条数以本文件 display_count 为准", limits)
            self.assertNotIn("当前 174 条", limits)  # 过期硬编码文案不再写入

    def test_apply_kg_log_append_failure_is_distinguishable(self):
        with tempfile.TemporaryDirectory() as td:
            cp, rp, cat, reg = _make_env(Path(td))
            kg = Path(td) / "kg_sync_task.log"
            kg.write_text("registry_rebuild date=x tables=1 sha=abc exit=0\n", encoding="utf-8")
            fake = mock.MagicMock()
            fake.status_for.return_value = "active"
            fake.reason_for.return_value = None
            buf = io.StringIO()
            with mock.patch.object(common, "CATALOG", cp), \
                    mock.patch.object(common, "REGISTRY", rp), \
                    mock.patch.object(rr, "KG_LOG", kg), \
                    mock.patch.object(rr, "find_kg_log_state",
                                      return_value={"ok": True, "last": "exit=0", "reason": ""}), \
                    mock.patch.object(rr, "append_kg_log",
                                      side_effect=OSError("disk full")), \
                    mock.patch.dict(sys.modules, {"lifecycle": fake}), \
                    contextlib.redirect_stdout(buf):
                rc = rr.run(apply_mode=True, as_json=True)
            self.assertEqual(rc, common.EXIT_WARN)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("written"))  # registry 已写盘
            self.assertIn("kg_sync_task.log 补记失败", payload.get("error", ""))
            # registry 确实已是新内容（备份存在）
            self.assertTrue(list(Path(td).glob("registry.json.bak-*")))


if __name__ == "__main__":
    unittest.main()
