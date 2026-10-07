"""离线签名工具只在工程临时目录交互，不创建真实身份。"""

from contextlib import redirect_stdout
from datetime import datetime, timedelta
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import warnings
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_operator.contracts import utc_now


class OfflineSignerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.private = self.directory / "private.pem"
        self.public = self.directory / "public.json"
        self.statement_path = self.directory / "statement.json"
        self.output = self.directory / "proof.json"

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("ai_operator.offline_review_signer"),
                             "缺少离线本人签名工具")
        return importlib.import_module("ai_operator.offline_review_signer")

    def keygen_args(self):
        return ["keygen", "--private-key", str(self.private), "--public-key", str(self.public)]

    def create_key(self, *, encrypted=True):
        key = Ed25519PrivateKey.generate()
        encryption = serialization.BestAvailableEncryption(b"engineering-password-2026") if encrypted else serialization.NoEncryption()
        self.private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption))
        return key

    def statement(self):
        now = utc_now()
        value = {"schema_version": 1, "request_id": "a" * 32, "action": "approve",
                 "capture_event_id": "b" * 64, "review_event_id": "c" * 64,
                 "target_decision_event_id": None, "reason": "工程声明，未经真实授权",
                 "issued_at": now, "expires_at": (datetime.fromisoformat(now) + timedelta(days=1)).isoformat()}
        self.statement_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return value

    def sign_args(self):
        return ["sign", "--private-key", str(self.private), "--statement", str(self.statement_path),
                "--authority-id", "fixture-authority", "--policy-id", "fixture-policy", "--output", str(self.output)]

    def invoke(self, args, *, tty=True, answer="签署", passwords=()):
        with redirect_stdout(io.StringIO()) as out, \
                patch("sys.stdin.isatty", return_value=tty), patch("sys.stdout.isatty", return_value=tty), \
                patch("builtins.input", return_value=answer), patch("getpass.getpass", side_effect=passwords), \
                patch("sys.stderr.isatty", return_value=tty):
            code = self.module().main(args)
        return code, out.getvalue()

    def test_noninteractive_keygen_refuses_without_files(self):
        code, text = self.invoke(self.keygen_args(), tty=False)
        self.assertEqual(code, 2)
        self.assertIn("交互终端", text)
        self.assertFalse(self.private.exists())
        self.assertFalse(self.public.exists())

    def test_confirmed_keygen_encrypted_private_and_public_fingerprint(self):
        code, text = self.invoke(self.keygen_args(), answer="生成", passwords=["engineering-password-2026"] * 2)
        self.assertEqual(code, 0, text)
        self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", self.private.read_bytes())
        loaded = serialization.load_pem_private_key(self.private.read_bytes(), b"engineering-password-2026")
        public = json.loads(self.public.read_text(encoding="utf-8"))
        raw = loaded.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.assertEqual(public["public_key_hex"], raw.hex())
        self.assertEqual(len(public["key_id"]), 64)
        self.assertNotIn("engineering-password-2026", text)
        self.assertNotIn("PRIVATE KEY", text)

    def test_refuses_existing_key_and_declined_confirmation(self):
        self.private.write_bytes(b"existing-owner-content")
        code, text = self.invoke(self.keygen_args(), answer="生成")
        self.assertEqual(code, 2, text)
        self.assertIn("覆盖", text)
        self.assertEqual(self.private.read_bytes(), b"existing-owner-content")
        self.assertFalse(self.public.exists())
        self.private.unlink()
        code, text = self.invoke(self.keygen_args(), answer="取消")
        self.assertEqual(code, 2, text)
        self.assertIn("本人未确认", text)
        self.assertFalse(self.private.exists())

    def test_confirmed_signature_verifies_full_canonical_message(self):
        key = self.create_key()
        statement = self.statement()
        code, text = self.invoke(self.sign_args(), passwords=["engineering-password-2026"])
        self.assertEqual(code, 0, text)
        proof = json.loads(self.output.read_text(encoding="utf-8"))
        from ai_operator.signed_review import signing_message
        key.public_key().verify(bytes.fromhex(proof["signature_hex"]), signing_message(statement, "fixture-authority", "fixture-policy"))
        self.assertEqual(proof["algorithm"], "Ed25519")
        self.assertNotIn("engineering-password-2026", text)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertIn(statement["capture_event_id"], text)
        self.assertIn(statement["review_event_id"], text)

    def test_unencrypted_key_wrong_password_and_noninteractive_sign_refused(self):
        self.statement()
        self.create_key(encrypted=False)
        code, text = self.invoke(self.sign_args(), passwords=["engineering-password-2026"])
        self.assertEqual(code, 2, text)
        self.assertIn("加密PKCS8", text)
        self.assertFalse(self.output.exists())
        self.private.unlink()
        self.create_key()
        code, text = self.invoke(self.sign_args(), passwords=["wrong-password-2026"])
        self.assertEqual(code, 2, text)
        self.assertIn("口令错误", text)
        self.assertNotIn("wrong-password-2026", text)
        self.assertFalse(self.output.exists())
        code, text = self.invoke(self.sign_args(), tty=False)
        self.assertEqual(code, 2, text)
        self.assertIn("交互终端", text)
        self.assertFalse(self.output.exists())

    def test_duplicate_statement_keys_and_existing_proof_are_not_overwritten(self):
        self.create_key()
        self.statement_path.write_text('{"action":"approve","action":"reject"}', encoding="utf-8")
        code, text = self.invoke(self.sign_args(), passwords=["engineering-password-2026"])
        self.assertEqual(code, 2, text)
        self.assertIn("重复键", text)
        self.assertFalse(self.output.exists())
        self.statement()
        self.output.write_text("existing-proof", encoding="utf-8")
        code, text = self.invoke(self.sign_args())
        self.assertEqual(code, 2, text)
        self.assertIn("拒绝覆盖", text)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "existing-proof")

    def test_private_permissions_failure_happens_before_secret_write(self):
        module = self.module()
        def deny(descriptor):
            self.assertEqual(os.fstat(descriptor).st_size, 0)
            raise ValueError("Windows私钥访问权限设置失败")
        with patch.object(module, "_restrict_private_permissions", side_effect=deny, create=True):
            code, text = self.invoke(self.keygen_args(), answer="生成", passwords=["engineering-password-2026"] * 2)
        self.assertEqual(code, 2, text)
        self.assertIn("访问权限", text)
        self.assertFalse(self.private.exists())
        self.assertFalse(self.public.exists())

    def test_failed_write_preserves_file_replaced_by_other_writer(self):
        module = self.module()
        moved = self.directory / "our-original"
        def replace_and_fail(descriptor):
            os.close(descriptor)
            self.private.rename(moved)
            self.private.write_bytes(b"other-owner-content")
            raise OSError("工程fsync失败")
        with patch.object(module.os, "fsync", side_effect=replace_and_fail):
            with self.assertRaises(OSError):
                module._write_new(self.private, b"engineering-only")
        self.assertTrue(self.private.exists(), "清理误删了另一写者的文件")
        self.assertEqual(self.private.read_bytes(), b"other-owner-content")

    def test_unc_statement_is_rejected_before_any_network_read(self):
        args = self.sign_args()
        args[args.index("--statement") + 1] = r"\\server\share\statement.json"
        with patch("pathlib.Path.open", side_effect=AssertionError("不能打开UNC路径")):
            code, text = self.invoke(args)
        self.assertEqual(code, 2, text)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == "nt", "仅Windows核对实际私钥ACL")
    def test_windows_generated_private_acl_is_protected_current_user_only(self):
        code, text = self.invoke(self.keygen_args(), answer="生成", passwords=["engineering-password-2026"] * 2)
        self.assertEqual(code, 0, text)
        command = "$ErrorActionPreference='Stop'; $a=Get-Acl -LiteralPath $env:AI_OPERATOR_ACL_FILE; " \
                  "@{protected=$a.AreAccessRulesProtected; user=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; " \
                  "sids=@($a.Access | ForEach-Object {$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value})} | ConvertTo-Json -Compress"
        result = subprocess.run(["pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                env=dict(os.environ, AI_OPERATOR_ACL_FILE=str(self.private)), capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        acl = json.loads(result.stdout)
        self.assertTrue(acl["protected"])
        self.assertEqual(acl["sids"], [acl["user"]])

    def test_password_echo_warning_refuses_without_key_material(self):
        def echo_warning(prompt):
            import getpass
            warnings.warn("工程无法禁用回显", getpass.GetPassWarning)
            return "engineering-password-2026"
        with patch("getpass.getpass", side_effect=echo_warning):
            with self.assertRaisesRegex(ValueError, "回显"):
                self.module()._password("工程口令：")
        self.assertFalse(self.private.exists())

    def test_ads_paths_rejected_before_filesystem_access(self):
        module = self.module()
        for value in (str(self.directory / "base:private"), "relative:proof"):
            with self.assertRaisesRegex(ValueError, "数据流"):
                module._new_path(value)
        args = self.sign_args()
        args[args.index("--statement") + 1] = str(self.directory / "base:statement")
        with patch.object(module, "_read_evidence", side_effect=AssertionError("不能读取ADS")):
            code, text = self.invoke(args)
        self.assertEqual(code, 2, text)
        self.assertIn("数据流", text)

    @unittest.skipUnless(os.name == "nt", "仅Windows核对私钥句柄共享")
    def test_private_unreadable_before_acl_even_to_current_user(self):
        module = self.module()
        original = module._restrict_private_permissions
        def inspect(descriptor):
            with self.assertRaises(PermissionError):
                with self.private.open("rb"):
                    self.fail("ACL收紧前取得读取句柄")
            original(descriptor)
        with patch.object(module, "_restrict_private_permissions", side_effect=inspect):
            code, text = self.invoke(self.keygen_args(), answer="生成", passwords=["engineering-password-2026"] * 2)
        self.assertEqual(code, 0, text)

    def test_public_write_failure_keeps_encrypted_private_without_path_deletion(self):
        module = self.module()
        original = module._write_new
        def fail_public(path, raw, **kwargs):
            if path == self.public:
                raise OSError("工程公钥输出失败")
            return original(path, raw, **kwargs)
        with patch.object(module, "_write_new", side_effect=fail_public), \
                patch("pathlib.Path.unlink", side_effect=AssertionError("不能按路径删除")):
            code, text = self.invoke(self.keygen_args(), answer="生成", passwords=["engineering-password-2026"] * 2)
        self.assertEqual(code, 2, text)
        self.assertTrue(self.private.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"))
        self.assertFalse(self.public.exists())


if __name__ == "__main__":
    unittest.main()
