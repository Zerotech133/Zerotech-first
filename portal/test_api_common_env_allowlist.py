# -*- coding: utf-8 -*-
"""B5（2026-10-21）：_load_cainiao_env 注入面白名单化测试。

纪律：
- 全部用 tempfile 临时文件调用 _load_cainiao_env(path=...)，
  严禁读写真实 C:\\Temp\\cainiao_oauth2.env。
- 测试前保存 os.environ 相关键、测试后就地恢复（模块级替换必须
  就地恢复，防污染全进程）。
- 断言返回统计只含键名，任何 "secret" 类值字符串不得出现在返回
  dict 的任何 str 中。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api_common  # noqa: E402

# 测试中可能被注入/触碰的非前缀键（前缀键按 CAINIAO_* 动态快照）
_NON_PREFIX_KEYS = ("PORTAL_SUB_ADMIN_KEY", "OTHER_X")


class _EnvGuardMixin(object):
    """保存/恢复 os.environ 相关键：测前快照，测后就地恢复。

    快照不能只列固定键名——套件内其他测试 import portal_api 会在
    import 期触发 _load_cainiao_env() 把真实 C:\\Temp\\cainiao_oauth2.env
    的 CAINIAO_* 键注入进程环境（2026-10-21 全量混跑实测判例），
    setdefault 语义下会让「前缀键注入」用例假红。故前缀键全量动态
    快照 + 测前清空，tearDown 先清测中触碰、再按快照原样恢复。
    """

    def setUp(self):
        super(_EnvGuardMixin, self).setUp()
        self._snapshot = {
            k: v for k, v in os.environ.items()
            if k.startswith("CAINIAO_") or k in _NON_PREFIX_KEYS
        }
        for k in self._snapshot:
            os.environ.pop(k, None)

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("CAINIAO_") or k in _NON_PREFIX_KEYS:
                os.environ.pop(k, None)
        for k, v in self._snapshot.items():
            os.environ[k] = v
        super(_EnvGuardMixin, self).tearDown()


class LoadCainiaoEnvAllowlistTest(_EnvGuardMixin, unittest.TestCase):

    def _write_env(self, content):
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    # --- 1. 前缀键被注入且值正确 ---
    def test_prefixed_key_loaded_with_value(self):
        path = self._write_env("CAINIAO_CLIENT_ID=abc123\n")
        r = api_common._load_cainiao_env(path=path)
        self.assertFalse(r["missing"])
        self.assertEqual(r["loaded"], ["CAINIAO_CLIENT_ID"])
        self.assertEqual(os.environ.get("CAINIAO_CLIENT_ID"), "abc123")

    # --- 2. 非前缀键不入 environ 且进 skipped（只记键名） ---
    def test_non_prefixed_keys_skipped(self):
        path = self._write_env(
            "PORTAL_SUB_ADMIN_KEY=should-not-leak\n"
            "OTHER_X=1\n"
            "CAINIAO_TENANT_ID=t1\n"
        )
        r = api_common._load_cainiao_env(path=path)
        self.assertEqual(r["loaded"], ["CAINIAO_TENANT_ID"])
        self.assertEqual(r["skipped"], ["PORTAL_SUB_ADMIN_KEY", "OTHER_X"])
        self.assertIsNone(os.environ.get("PORTAL_SUB_ADMIN_KEY"))
        self.assertIsNone(os.environ.get("OTHER_X"))
        # 值不得泄漏进返回结构
        self.assertNotIn("should-not-leak", str(r))
        self.assertNotIn("1", [s for s in r["skipped"]])

    # --- 3. 已有环境变量优先（setdefault 语义） ---
    def test_existing_env_wins(self):
        os.environ["CAINIAO_CLIENT_ID"] = "from-env"
        path = self._write_env("CAINIAO_CLIENT_ID=from-file\n")
        r = api_common._load_cainiao_env(path=path)
        self.assertEqual(os.environ.get("CAINIAO_CLIENT_ID"), "from-env")
        # 已存在时 setdefault 不注入，键也不进 loaded
        self.assertNotIn("CAINIAO_CLIENT_ID", r["loaded"])

    # --- 4. 文件缺失：missing=True、environ 零变化、不抛异常 ---
    def test_missing_file_noop(self):
        before = dict(os.environ)
        r = api_common._load_cainiao_env(
            path=os.path.join(tempfile.gettempdir(), "no_such_env_zz9xkq.env"))
        self.assertTrue(r["missing"])
        self.assertEqual(r["loaded"], [])
        self.assertEqual(r["skipped"], [])
        self.assertEqual(dict(os.environ), before)

    # --- 5. 空行 / # 注释 / 无等号行跳过 ---
    def test_blank_comment_noeq_lines_skipped(self):
        path = self._write_env(
            "\n"
            "# CAINIAO_COMMENTED=nope\n"
            "CAINIAO_OK=1\n"
            "INVALID_LINE_NO_EQUALS\n"
        )
        r = api_common._load_cainiao_env(path=path)
        self.assertEqual(r["loaded"], ["CAINIAO_OK"])
        self.assertEqual(r["skipped"], [])

    # --- 6. 键名两侧空白归一 ---
    def test_key_whitespace_stripped(self):
        path = self._write_env("  CAINIAO_A  =val-a\n")
        r = api_common._load_cainiao_env(path=path)
        self.assertEqual(r["loaded"], ["CAINIAO_A"])
        self.assertEqual(os.environ.get("CAINIAO_A"), "val-a")

    # --- 7. 返回统计只含键名不含值 ---
    def test_return_stats_keys_only_no_values(self):
        path = self._write_env(
            "CAINIAO_CLIENT_SECRET=supersecretvalue\n"
            "PORTAL_SUB_ADMIN_KEY=adminsecretvalue\n"
        )
        r = api_common._load_cainiao_env(path=path)
        # 返回 dict 的任何 str 中不得出现 secret 类值
        for v in r.values():
            if isinstance(v, str):
                self.assertNotIn("supersecretvalue", v)
                self.assertNotIn("adminsecretvalue", v)
            elif isinstance(v, (list, tuple)):
                for item in v:
                    self.assertNotIn("supersecretvalue", item)
                    self.assertNotIn("adminsecretvalue", item)
        # 结构约束
        self.assertEqual(
            sorted(r.keys()), ["loaded", "missing", "skipped"])
        self.assertIsInstance(r["missing"], bool)
        # skipped 只记键名
        self.assertEqual(r["skipped"], ["PORTAL_SUB_ADMIN_KEY"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
