"""check_ports.py 单测：JSON 契约 + 2026-10-26 strict 语义拆分回归。

WO-UPGRADE-20261026：strict 只对「未登记监听/端口冲突」失败；在册服务离线
不判负（旧版把 20 个按需离线口算进 strict → 合并门恒红 → 门被绕过用非
strict → 假门）。另断言 devtask 合并门端口项确实带 --strict（静态 AST）。
"""
import ast
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contextlib import redirect_stdout
from unittest.mock import patch

import check_ports


class CheckPortsJsonTests(unittest.TestCase):
    def test_tdxw_third_party_process_is_not_reported_as_unknown(self):
        argv = ['check_ports.py', '--strict', '--json']
        with patch.object(sys, 'argv', argv), \
             patch.object(check_ports, 'tcp_up', return_value=True), \
             patch.object(check_ports, 'listening_ports', return_value={14571: 51792}), \
             patch.object(check_ports, 'proc_name', return_value='TdxW'):
            output = io.StringIO()
            with redirect_stdout(output):
                rc = check_ports.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload['unknown'], [])
        self.assertIs(payload['strict_fail'], False)

    def test_strict_fail_is_boolean_in_json_output(self):
        argv = ['check_ports.py', '--strict', '--json']
        with patch.object(sys, 'argv', argv), \
             patch.object(check_ports, 'tcp_up', return_value=True), \
             patch.object(check_ports, 'listening_ports', return_value={4321: 9001}), \
             patch.object(check_ports, 'proc_name', return_value='unknown-process'):
            output = io.StringIO()
            with redirect_stdout(output):
                rc = check_ports.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(payload['strict_fail'], True)
        self.assertEqual(payload['rc'], 1)


class StrictSemanticsTests(unittest.TestCase):
    """strict 语义拆分回归（WO-UPGRADE-20261026）。"""

    @staticmethod
    def _main_json(argv, tcp_up, listening, proc='unknown-process'):
        with patch.object(sys, 'argv', argv), \
             patch.object(check_ports, 'tcp_up', return_value=tcp_up), \
             patch.object(check_ports, 'listening_ports', return_value=listening), \
             patch.object(check_ports, 'proc_name', return_value=proc):
            output = io.StringIO()
            with redirect_stdout(output):
                rc = check_ports.main()
        return rc, output.getvalue()

    def test_registered_offline_ports_do_not_fail_strict(self):
        """全部在册服务离线（含非 optional 的硬离线）时 strict 仍应 rc=0。"""
        rc, out = self._main_json(['check_ports.py', '--strict', '--json'], False, {})
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertIs(payload['strict_fail'], False)
        self.assertEqual(len(payload['offline']), len(check_ports.REGISTRY))
        self.assertTrue(payload['hard_offline'], '用例前提：确实存在非 optional 离线口')

    def test_non_strict_unknown_is_advisory_only(self):
        """非 strict：未登记监听只警示，rc=0（存量合并门行为保持不变）。"""
        rc, out = self._main_json(['check_ports.py', '--json'], True, {4321: 9001})
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(len(payload['unknown']), 1)
        self.assertEqual(payload['summary']['unregistered'], 1)

    def test_msedge_debug_port_is_whitelisted(self):
        """9335 headless Edge remote-debugging 口是浏览器客户端，不是平台服务。"""
        rc, out = self._main_json(
            ['check_ports.py', '--strict', '--json'], True, {9335: 25268},
            proc='msedge')
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload['unknown'], [])
        self.assertIs(payload['strict_fail'], False)

    def test_workbuddy_kg_api_port_is_registered(self):
        """8790 是 workbuddy 他线 KG API 的实测监听口，不是本仓服务。"""
        self.assertIn(8790, check_ports.REGISTRY)
        self.assertIn('KG API', check_ports.REGISTRY[8790])

    def test_registry_duplicate_port_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'registry_dup.py'
            src.write_text('REGISTRY = {\n 8100: "a",\n 8100: "b",\n}\n',
                           encoding='utf-8')
            self.assertEqual(check_ports.registry_duplicate_ports(str(src)), [8100])

    def test_allocation_duplicate_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            alloc = Path(tmp) / 'ports_allocated.json'
            alloc.write_text('{"a": 8101, "b": 8101, "c": 8102}', encoding='utf-8')
            self.assertEqual(check_ports.allocation_conflicts(str(alloc)),
                             [{'port': 8101, 'names': ['a', 'b']}])

    def test_conflict_fails_strict(self):
        """端口冲突进 strict 失败通道，且出现在 --json 输出里。"""
        conflict = [{'kind': 'registry_duplicate', 'port': 8100, 'detail': 'x'}]
        with patch.object(check_ports, 'port_conflicts', return_value=conflict), \
             patch.object(check_ports, 'tcp_up', return_value=True), \
             patch.object(check_ports, 'listening_ports', return_value={}):
            with patch.object(sys, 'argv', ['check_ports.py', '--strict', '--json']):
                output = io.StringIO()
                with redirect_stdout(output):
                    rc = check_ports.main()
        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(payload['strict_fail'], True)
        self.assertEqual(payload['conflicts'][0]['port'], 8100)
        self.assertEqual(payload['summary']['conflicts'], 1)

    def test_live_registry_has_no_duplicate_ports(self):
        """真实注册表必须无重复登记（否则 strict 永远红）。

        数量不断言绝对值：入册口随工单增长（AGENTS.md §1 记 51，2026-10-26
        实测 52 = 51 + 当日 8795/8103 收编），锁定具体数字会让新增端口误红。
        """
        self.assertEqual(check_ports.registry_duplicate_ports(), [])
        self.assertGreaterEqual(len(check_ports.REGISTRY), 51)


class DevtaskPortGateWireTests(unittest.TestCase):
    def test_merge_port_gate_uses_strict(self):
        src = (Path(__file__).resolve().parent / 'devtask.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        gates = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(target, 'id', '') == 'GATES' for target in node.targets):
                gates = node.value
        self.assertIsNotNone(gates, 'devtask.py 应有模块级 GATES 列表')
        port_gates = [item for item in gates.elts
                      if isinstance(item, ast.Tuple)
                      and isinstance(item.elts[0], ast.Constant)
                      and item.elts[0].value == '端口注册']
        self.assertEqual(len(port_gates), 1, '应有且只有一个端口注册门')
        cmd = port_gates[0].elts[1]
        constants = [n.value for n in ast.walk(cmd) if isinstance(n, ast.Constant)]
        self.assertIn('check_ports.py', constants)
        self.assertIn('--strict', constants,
                      '合并门端口项必须带 --strict（否则恒绿假门）')


if __name__ == '__main__':
    unittest.main()
