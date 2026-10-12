import io
import json
import sys
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


if __name__ == '__main__':
    unittest.main()
