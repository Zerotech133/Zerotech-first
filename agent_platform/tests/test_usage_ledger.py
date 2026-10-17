"""M2-P4 统一使用账本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclass 字符串注解判例
    spec.loader.exec_module(module)
    return module


ul = _load("ul", "agent_platform/adapters/usage_ledger.py")


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # fixture 仓：tmp/<base> 为 agent_platform 位，portal 在其旁
        self.base = Path(self.tmp.name) / "agent_platform"
        (self.base / "data").mkdir(parents=True)
        (self.base / "registry").mkdir()
        (self.base.parent / "portal" / "data").mkdir(parents=True)
        # org：T1→invest，D1→invest(lead)，DEV1→dev
        (self.base / "registry" / "org.json").write_text(json.dumps({
            "departments": [
                {"dept": "invest", "lead": "D1",
                 "agents": ["T1", "D1"]},
                {"dept": "dev", "lead": "ARCH1",
                 "agents": ["DEV1"]},
            ]}), encoding="utf-8")
        # usage 台账：T1 anthropic / QA1 unmapped / T1 label 带 TID
        (self.base.parent / "portal" / "data"
         / "llm_usage.jsonl").write_text("\n".join([
            json.dumps({"ts": "2026-10-17 10:00:00", "agent": "T1",
                        "channel": "anthropic", "label": "K-101 产码",
                        "in_tok": 100, "out_tok": 50, "elapsed": 2.0,
                        "chars": 0}),
            json.dumps({"ts": "2026-10-17 11:00:00", "agent": "T1",
                        "channel": "ollama", "label": "刻画",
                        "in_tok": 0, "out_tok": 0, "elapsed": 9.0,
                        "chars": 800}),
            json.dumps({"ts": "2026-10-17 12:00:00", "agent": "QA1",
                        "channel": "cloud", "label": "G3台账验收",
                        "in_tok": 10, "out_tok": 2, "elapsed": 1.0,
                        "chars": 0}),
        ]), encoding="utf-8")
        # 工具审计
        (self.base / "data" / "llm_tool_audit.jsonl").write_text("\n".join([
            json.dumps({"ts": "2026-10-17 10:00:01", "caller": "T1",
                        "endpoint": "/api/task/list", "method": "GET",
                        "ok": True}),
            json.dumps({"ts": "2026-10-17 10:00:02", "caller": "T1",
                        "endpoint": "/api/task/move", "method": "POST",
                        "ok": False}),
        ]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_dept_map(self):
        m = ul.agent_dept_map(self.base)
        self.assertEqual(m["T1"], "invest")
        self.assertEqual(m["D1"], "invest")      # lead 也入映射
        self.assertEqual(m["DEV1"], "dev")

    def test_report_domain_attribution(self):
        rep = ul.build_usage_report(self.base)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["findings"], [])
        inv = rep["by_domain"]["invest"]
        self.assertEqual(inv["calls"], 2)
        self.assertEqual(inv["in_tok"], 100)     # ollama 行 0 tok
        self.assertEqual(inv["chars"], 800)      # chars 兜底列
        self.assertEqual(rep["by_domain"]["unmapped"]["calls"], 1)
        self.assertEqual(rep["unmapped_agents"], {"QA1": 1})

    def test_report_channel_and_credits(self):
        rep = ul.build_usage_report(self.base)
        self.assertEqual(rep["by_channel"]["anthropic"]["calls"], 1)
        self.assertEqual(rep["credits_estimate"], 4.5)   # 判例估算
        self.assertEqual(rep["totals"]["api_calls"], 2)
        self.assertEqual(rep["totals"]["api_fail"], 1)

    def test_ticket_attribution(self):
        rep = ul.build_usage_report(self.base)
        self.assertIn("K-101", rep["by_ticket_top10"])
        self.assertEqual(rep["by_ticket_top10"]["K-101"]["in_tok"], 100)

    def test_missing_sources_fail_visible(self):
        import shutil
        shutil.rmtree(self.base.parent / "portal")
        (self.base / "data" / "llm_tool_audit.jsonl").unlink()
        rep = ul.build_usage_report(self.base)
        self.assertEqual(rep["verdict"], "WARN")
        self.assertEqual(len(rep["findings"]), 2)
        self.assertEqual(rep["totals"]["usage_calls"], 0)

    def test_real_repo_read_only(self):
        rep = ul.build_usage_report(ROOT / "agent_platform")
        self.assertIn(rep["verdict"], ("PASS", "WARN"))
        self.assertTrue(rep["read_only"])
        self.assertTrue(len(rep["by_domain"]) >= 1)   # 真实台账非空
        self.assertEqual(rep["findings"], [])          # 真实仓三源齐全


if __name__ == "__main__":
    unittest.main()
