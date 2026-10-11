# -*- coding: utf-8 -*-
"""E1 alert_fail 可观测化测试。

覆盖：BOM 嗅探三种编码 / pending→.sent 流转 / 发送日志追加格式 /
发送失败时 pending 保持不动 / err log UTF-16→UTF-8 归一 / JSON 原子写。

运行（产线解释器唯一合法路径）:
    'C:\\Program Files\\Python311\\python.exe' -X utf8 test_alert_fail.py -v

红线遵守：全程 mock + tempfile 临时目录，不触网、不对外发送、
不读写真实 outbox（wrap.OUTBOX/SEND_LOG/ALERT_FAIL_PY 全部 patch）。
"""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from alert_io import (append_ascii_log, atomic_write_json, sniff_encoding,
                      sniff_read_text)
import alert_fail_wrap as wrap

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \| task=\S+ "
                   r"\| channel=\S+ \| result=(OK|FAIL) \| reason=.+$")


class TmpDirCase(unittest.TestCase):
    """统一临时目录基建（自动清理）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="e1_test_")
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.outbox = os.path.join(self.dir, "outbox")
        self.send_log = os.path.join(self.dir, "logs", "alert_send.log")

    def wbin(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p


class BomSniffTest(TmpDirCase):
    """实伤3：BOM 嗅探三种编码（含大小端与兜底）。"""

    def test_utf16le_bom(self):
        p = self.wbin("u16le.log", "中文 tail RuntimeError".encode("utf-16"))
        self.assertEqual(sniff_encoding(p), "utf-16")
        self.assertIn("RuntimeError", sniff_read_text(p))

    def test_utf16be_bom(self):
        p = self.wbin("u16be.log", b"\xfe\xff" + "失败原因".encode("utf_16_be"))
        self.assertEqual(sniff_encoding(p), "utf-16")
        self.assertIn("失败原因", sniff_read_text(p))

    def test_utf8_sig_bom(self):
        p = self.wbin("bom.txt", b"\xef\xbb\xbf" + "成功".encode("utf-8"))
        self.assertEqual(sniff_encoding(p), "utf-8-sig")
        self.assertEqual(sniff_read_text(p), "成功")

    def test_plain_and_empty_fallback_utf8(self):
        p = self.wbin("plain.txt", "plain".encode("utf-8"))
        self.assertEqual(sniff_encoding(p), "utf-8")
        self.assertEqual(sniff_read_text(p), "plain")
        e = self.wbin("empty.txt", b"")
        self.assertEqual(sniff_encoding(e), "utf-8")
        self.assertEqual(sniff_read_text(e), "")


class NormalizeErrLogTest(TmpDirCase):
    """实伤3修复：UTF-16 err log 归一为 UTF-8 副本，utf-8 原样直通。"""

    def test_utf16_log_normalized(self):
        src = self.wbin("e.log", "raise RuntimeError(boom)".encode("utf-16"))
        dst = wrap.normalize_err_log(src)
        self.addCleanup(lambda: os.path.isfile(dst) and os.unlink(dst))
        self.assertNotEqual(dst, src)
        with open(dst, encoding="utf-8") as f:  # alert_fail 的 utf-8 读法
            self.assertIn("raise RuntimeError(boom)", f.read())

    def test_utf8_log_passthrough_and_missing(self):
        src = self.wbin("ok.log", "hello".encode("utf-8"))
        self.assertEqual(wrap.normalize_err_log(src), src)
        self.assertEqual(wrap.normalize_err_log(""), "")
        self.assertEqual(wrap.normalize_err_log(
            os.path.join(self.dir, "nope.log")), "")

    def test_utf16_log_creates_missing_outbox(self):
        src = self.wbin("missing_outbox.log", "失败原因".encode("utf-16"))
        with mock.patch.object(wrap, "OUTBOX", self.outbox):
            dst = wrap.normalize_err_log(src)
        self.addCleanup(lambda: os.path.isfile(dst) and os.unlink(dst))
        self.assertTrue(os.path.isdir(self.outbox))
        self.assertIn("失败原因", Path(dst).read_text(encoding="utf-8"))


class SendLogFormatTest(TmpDirCase):
    """实伤1修复：发送结果 ASCII 单行追加落盘。"""

    def test_log_send_line_format_ascii(self):
        with mock.patch.object(wrap, "SEND_LOG", self.send_log):
            wrap.log_send("tdx_sync", "feishu", True, "SEND_OK")
            wrap.log_send("news_review", "feishu", False,
                          "SEND_FAIL openapi 中文err")
        with open(self.send_log, encoding="ascii") as f:  # 全 ASCII 可解码
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], TS_RE)
        self.assertIn("task=tdx_sync", lines[0])
        self.assertIn("result=OK", lines[0])
        self.assertIn("reason=SEND_OK", lines[0])
        self.assertRegex(lines[1], TS_RE)
        self.assertIn("result=FAIL", lines[1])
        self.assertNotIn("中文", lines[1])  # 非 ASCII 已降级

    def test_append_ascii_log_creates_dirs(self):
        deep = os.path.join(self.dir, "a", "b", "x.log")
        append_ascii_log(deep, "line1")
        append_ascii_log(deep, "line2")
        with open(deep, encoding="ascii") as f:
            self.assertEqual(f.read().splitlines(), ["line1", "line2"])


class PendingFlowTest(TmpDirCase):
    """实伤2修复：成功→.sent 原子流转；失败→pending 原地不动。"""

    def _make_pending(self, task="e1task"):
        os.makedirs(self.outbox, exist_ok=True)
        p = os.path.join(self.outbox, "pending_alert_%s.txt" % task)
        with open(p, "w", encoding="utf-8") as f:
            f.write("[QUANT ALERT] task failed: %s" % task)
        return p

    def test_success_renames_pending_to_sent(self):
        p = self._make_pending()
        with mock.patch.object(wrap, "SEND_LOG", self.send_log):
            action = wrap.handle_send_result(p, "e1task", True, "SEND_OK")
        self.assertEqual(action, "sent")
        self.assertFalse(os.path.exists(p))
        sent = p + ".sent"
        self.assertTrue(os.path.isfile(sent))
        with open(sent, encoding="utf-8") as f:
            self.assertIn("[QUANT ALERT]", f.read())  # 审计内容保留
        with open(self.send_log, encoding="ascii") as f:
            self.assertIn("result=OK", f.read())

    def test_failure_keeps_pending_untouched(self):
        p = self._make_pending()
        with mock.patch.object(wrap, "SEND_LOG", self.send_log):
            action = wrap.handle_send_result(p, "e1task", False,
                                             "SEND_FAIL mock_down")
        self.assertEqual(action, "kept")
        self.assertTrue(os.path.exists(p))          # pending 原样保留
        self.assertFalse(os.path.exists(p + ".sent"))  # 不产生 .sent
        with open(self.send_log, encoding="ascii") as f:
            self.assertIn("result=FAIL", f.read())

    def test_success_without_pending_is_noop(self):
        p = os.path.join(self.outbox, "pending_alert_ghost.txt")
        with mock.patch.object(wrap, "SEND_LOG", self.send_log):
            self.assertEqual(
                wrap.handle_send_result(p, "ghost", True, "SEND_OK"),
                "no_pending")

    def test_sent_collision_uses_timestamp_fallback(self):
        p = self._make_pending()
        with open(p + ".sent", "w", encoding="utf-8") as f:
            f.write("old audit")
        with mock.patch.object(wrap, "SEND_LOG", self.send_log):
            action = wrap.handle_send_result(p, "e1task", True, "SEND_OK")
        self.assertEqual(action, "sent_existing")
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.isfile(p + ".sent.1") or len(
            [n for n in os.listdir(self.outbox)
             if n.startswith("pending_alert_e1task.txt.sent.")]) == 1)


class ParseSendResultTest(unittest.TestCase):
    """SEND 标记解析：OK / FAIL / 无标记兜底。"""

    def test_ok(self):
        self.assertEqual(wrap.parse_send_result("SEND_OK", 0), (True, "SEND_OK"))

    def test_fail_reason_extracted(self):
        ok, why = wrap.parse_send_result("SEND_FAIL no app creds", 1)
        self.assertFalse(ok)
        self.assertIn("no app creds", why)

    def test_no_marker(self):
        ok, why = wrap.parse_send_result("", 127)
        self.assertFalse(ok)
        self.assertIn("no_send_marker", why)


FAKE_ALERT_FAIL = r'''
# -*- coding: utf-8 -*-
"""假 alert_fail：同构复刻（写 pending + utf-8 读日志尾 + 打印 SEND 标记）。"""
import os, sys
task, code = sys.argv[1], sys.argv[2]
log = sys.argv[3] if len(sys.argv) > 3 else ""
outbox = os.environ["E1_TEST_OUTBOX"]
pending = os.path.join(outbox, "pending_alert_%s.txt" % task)
tail = ""
if log and os.path.isfile(log):
    with open(log, encoding="utf-8", errors="replace") as f:  # 同真身 utf-8 读法
        tail = "".join(f.readlines()[-5:])
with open(pending, "w", encoding="utf-8") as f:
    f.write("[FAKE QUANT ALERT] task=%s exit=%s\n%s" % (task, code, tail))
print("SEND_OK" if os.environ.get("E1_FAKE_RESULT") == "OK"
      else "SEND_FAIL mock_channel_down")
'''


class WrapEndToEndTest(TmpDirCase):
    """包装器端到端（假 alert_fail 子进程）：UTF-16 日志进 → 可读告警出。"""

    def _run(self, result):
        fake_py = self.wbin("fake_alert_fail.py", FAKE_ALERT_FAIL.encode("utf-8"))
        errlog = self.wbin("e.log", "raise RuntimeError(GET failed)".encode("utf-16"))
        os.makedirs(self.outbox, exist_ok=True)
        env = {"E1_TEST_OUTBOX": self.outbox, "E1_FAKE_RESULT": result}
        with mock.patch.object(wrap, "ALERT_FAIL_PY", fake_py), \
             mock.patch.object(wrap, "SEND_LOG", self.send_log), \
             mock.patch.object(wrap, "OUTBOX", self.outbox), \
             mock.patch.dict(os.environ, env):
            rc = wrap.main(["e1task", "1", errlog])
        pending = os.path.join(self.outbox, "pending_alert_e1task.txt")
        return rc, pending

    def test_ok_path_sends_and_flows(self):
        rc, pending = self._run("OK")
        self.assertEqual(rc, 0)                      # 观测层不反噬 rc
        self.assertFalse(os.path.exists(pending))    # pending 已流转
        self.assertTrue(os.path.isfile(pending + ".sent"))
        with open(pending + ".sent", encoding="utf-8") as f:
            body = f.read()
        self.assertIn("[FAKE QUANT ALERT]", body)
        self.assertIn("raise RuntimeError(GET failed)", body)  # 乱码已治愈
        with open(self.send_log, encoding="ascii") as f:
            line = f.read().splitlines()[-1]
        self.assertRegex(line, TS_RE)
        self.assertIn("result=OK", line)

    def test_fail_path_keeps_pending_and_logs_reason(self):
        rc, pending = self._run("FAIL")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(pending))     # 失败不流转
        self.assertFalse(os.path.exists(pending + ".sent"))
        with open(pending, encoding="utf-8") as f:
            self.assertIn("[FAKE QUANT ALERT]", f.read())
        with open(self.send_log, encoding="ascii") as f:
            line = f.read().splitlines()[-1]
        self.assertIn("result=FAIL", line)
        self.assertIn("SEND_FAIL", line)


class AtomicJsonTest(TmpDirCase):
    """状态文件原子写：内容正确且无 .tmp 残留。"""

    def test_write_and_no_tmp_leftover(self):
        fp = os.path.join(self.outbox, "state.json")
        os.makedirs(self.outbox, exist_ok=True)
        atomic_write_json(fp, {"ok": True, "名": "值"})
        with open(fp, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ok": True, "名": "值"})
        self.assertEqual([n for n in os.listdir(self.outbox)
                          if n.endswith(".tmp")], [])


class LauncherWiringTest(unittest.TestCase):
    """任务启动器必须走可观测包装器，避免失败只进旧 outbox。"""

    def test_todays_picks_uses_observable_alert_wrapper(self):
        launcher = (Path(__file__).resolve().parents[1]
                    / "portal" / "start_todays_picks.ps1")
        source = launcher.read_text(encoding="utf-8")
        self.assertIn("alert_fail_wrap.py", source)
        self.assertNotRegex(source, r"quant_backtest[\\/]alert_fail\.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
