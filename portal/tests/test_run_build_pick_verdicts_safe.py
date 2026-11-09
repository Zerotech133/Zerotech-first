# -*- coding: utf-8 -*-
"""P2-1: build_pick_verdicts 安全运行器与分片语义单测。

覆盖：
- 生产输出保护（拒绝覆盖 portal/data/pick_verdicts.json，含 junction 别名）；
- 子进程环境注入（独立输出/分片/进度，缺省不注入）；
- 分片归档选择（信号日区间 + 尾部 N 档，区间先筛再取尾）；
- 产物校验（结构不全必须报错，不静默放过）；
- 纯函数：版本解析/命令行。
全部离线、秒级，不触达行情湖与生产 JSON。
"""
import json
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTAL = os.path.join(WT, "portal")
sys.path.insert(0, PORTAL)

import build_pick_verdicts as bpv  # noqa: E402
import run_build_pick_verdicts_safe as safe  # noqa: E402

ARCHS = [
    r"X\picks_history\todays_20261009.json",
    r"X\picks_history\todays_20261019.json",
    r"X\picks_history\todays_20261020.json",
    r"X\picks_history\todays_20261021.json",
    r"X\picks_history\todays_20261029.json",
]


class TestOutGuard(unittest.TestCase):
    def test_prod_out_rejected_by_default(self):
        with self.assertRaises(safe.ConfigError):
            safe.check_out_path(safe.PROD_OUT)
        with self.assertRaises(safe.ConfigError):
            safe.check_out_path(os.path.join(WT, "portal", "data", "pick_verdicts.json"))

    @unittest.skipUnless(os.path.exists(r"C:\aios"), "C:\\aios junction 不存在，跳过别名用例")
    def test_prod_alias_rejected(self):
        with self.assertRaises(safe.ConfigError):
            safe.check_out_path(r"C:\aios\portal\data\pick_verdicts.json")

    def test_temp_out_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "pick_verdicts_shard1.json")
            # 2026-11-08 CI 修正：check_out_path 内部走 os.path.realpath（Windows 上
            # 会把 8.3 短名展开成长名，例如 C:\Users\RUNNER~1 → ...\runneradmin）。
            # 原断言直接拿 realpath 结果比原始字符串，在本机（无短名）通过、在
            # GitHub runner（TEMP 含 RUNNER~1）必红——这是断言假设的可移植性问题，
            # 不是许可逻辑缺陷：契约是"临时目录放行"，不是"字符串逐字节相同"。
            self.assertEqual(
                os.path.normcase(safe.check_out_path(p)),
                os.path.normcase(os.path.realpath(p)),
            )

    def test_prod_allowed_with_flag(self):
        got = safe.check_out_path(safe.PROD_OUT, allow_prod=True)
        self.assertEqual(
            os.path.normcase(got), os.path.normcase(os.path.realpath(safe.PROD_OUT))
        )

    def test_non_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(safe.ConfigError):
                safe.check_out_path(os.path.join(d, "pick_verdicts.txt"))


class TestChildEnv(unittest.TestCase):
    def test_env_injects_shard_and_out(self):
        env = safe.make_child_env(
            {"PATH": "x"},
            r"C:\Temp\a.json",
            max_archives=1,
            archive_from="2026-10-19",
            archive_to="20261021",
            progress_every=10,
        )
        self.assertEqual(env["VERDICTS_OUT"], r"C:\Temp\a.json")
        self.assertEqual(env["VERDICTS_MAX_ARCHIVES"], "1")
        self.assertEqual(env["VERDICTS_ARCHIVE_FROM"], "20261019")
        self.assertEqual(env["VERDICTS_ARCHIVE_TO"], "20261021")
        self.assertEqual(env["VERDICTS_PROGRESS_EVERY"], "10")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PATH"], "x")  # 原环境保留

    def test_env_no_shard_does_not_leak(self):
        env = safe.make_child_env(
            {"VERDICTS_MAX_ARCHIVES": "9", "VERDICTS_ARCHIVE_FROM": "2026"},
            r"C:\Temp\a.json",
            max_archives=0,
            archive_from="",
            archive_to="",
            progress_every=25,
        )
        self.assertNotIn("VERDICTS_MAX_ARCHIVES", env)
        self.assertNotIn("VERDICTS_ARCHIVE_FROM", env)
        self.assertNotIn("VERDICTS_ARCHIVE_TO", env)
        self.assertEqual(env["VERDICTS_PROGRESS_EVERY"], "25")


class TestSelectArchives(unittest.TestCase):
    def test_tail(self):
        got = bpv.select_archives(ARCHS, max_archives=2)
        self.assertEqual(
            [os.path.basename(p) for p in got],
            ["todays_20261021.json", "todays_20261029.json"],
        )

    def test_range(self):
        got = bpv.select_archives(ARCHS, date_from="20261019", date_to="20261021")
        self.assertEqual(
            [os.path.basename(p) for p in got],
            ["todays_20261019.json", "todays_20261020.json", "todays_20261021.json"],
        )

    def test_range_with_dash(self):
        got = bpv.select_archives(ARCHS, date_from="2026-10-20", date_to="2026-10-20")
        self.assertEqual([os.path.basename(p) for p in got], ["todays_20261020.json"])

    def test_range_then_tail(self):
        # 区间先筛、再取尾部：必须命中区间内最新档
        got = bpv.select_archives(
            ARCHS, max_archives=1, date_from="20261019", date_to="20261021"
        )
        self.assertEqual([os.path.basename(p) for p in got], ["todays_20261021.json"])

    def test_empty_range(self):
        self.assertEqual(bpv.select_archives(ARCHS, date_from="20260101", date_to="20260102"), [])

    def test_full_history_when_no_filter(self):
        self.assertEqual(len(bpv.select_archives(ARCHS)), len(ARCHS))


class TestPureHelpers(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(safe.version_tuple("3.11.9"), (3, 11))
        self.assertEqual(safe.version_tuple("3.12.10"), (3, 12))
        self.assertEqual(safe.version_tuple(""), ())
        self.assertEqual(safe.version_tuple("bad"), ())

    def test_archive_day(self):
        self.assertEqual(safe.archive_day(ARCHS[1]), "20261019")

    def test_build_command(self):
        cmd = safe.build_command(r"C:\py\python.exe")
        self.assertEqual(cmd[0], r"C:\py\python.exe")
        self.assertIn("-X", cmd)
        self.assertIn("utf8", cmd)
        self.assertEqual(cmd[-1], safe.TARGET)

    def test_describe_select(self):
        self.assertEqual(safe._describe_select(0, "", ""), "全量")
        self.assertIn("尾部 2 档", safe._describe_select(2, "", ""))
        self.assertIn("20261019~20261021", safe._describe_select(0, "20261019", "20261021"))


class TestValidateOutput(unittest.TestCase):
    def _write(self, doc):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "pick_verdicts.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        return p

    def test_ok(self):
        p = self._write(
            {
                "generated_at": "2026-10-31 13:00:00",
                "summary": {
                    "asof": "20261030",
                    "archives": 1,
                    "windows": {"1": {"n": 3, "hit": 33.3, "avg": 0.1}},
                },
                "per_code": {"600540.SH": {"win": {}}},
                "select": {"archives": 1},
            }
        )
        info = safe.validate_output(p)
        self.assertEqual(info["codes"], 1)
        self.assertEqual(info["windows"]["1"]["n"], 3)
        self.assertEqual(info["select"]["archives"], 1)

    def test_missing_windows_rejected(self):
        p = self._write({"summary": {"asof": "20261030"}, "per_code": {}})
        with self.assertRaises(ValueError):
            safe.validate_output(p)

    def test_bad_json_rejected(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not-json")
        with self.assertRaises(ValueError):
            safe.validate_output(p)


if __name__ == "__main__":
    unittest.main()
