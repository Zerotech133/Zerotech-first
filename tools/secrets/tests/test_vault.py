# -*- coding: utf-8 -*-
"""tools/secrets 单元测试：加密→解密 round-trip、MANIFEST 校验、fail-closed。

运行::

    python -m unittest discover -s tools/secrets/tests -v

测试全部在临时目录内构造保险库，绝不碰真实 ``C:\\项目\\_vault``。
默认覆盖 cryptography AES-GCM 兜底后端；本机若存在便携 age 二进制，另加 age 往返用例。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SECRETS_DIR = HERE.parents[1]  # <repo>/tools/secrets
VAULT_PY = SECRETS_DIR / "vault.py"
sys.path.insert(0, str(SECRETS_DIR))

import vault  # noqa: E402

PLAINTEXT_A = "API_KEY=abc123\nDB_PASSWORD=p@ss w0rd\n"
PLAINTEXT_B = b"\x00\x01binary\xff payload"


class VaultTestBase(unittest.TestCase):
    backend = "aesgcm"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="secvault-test-"))
        self.home = self.tmp / "vault"
        self.key = self.home / "keys" / "age.key"
        self.secrets = self.home / "secrets"
        (self.secrets / "nested").mkdir(parents=True)
        (self.secrets / "a.env").write_bytes(PLAINTEXT_A.encode("utf-8"))
        (self.secrets / "nested" / "b.bin").write_bytes(PLAINTEXT_B)
        (self.secrets / "empty.txt").write_bytes(b"")
        # Windows 上 write_text 会做 \n -> \r\n 转换；round-trip 断言一律用磁盘真实字节
        self.plain_a = (self.secrets / "a.env").read_bytes()
        self.init()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def init(self, backend=None, force=False):
        return vault.init_vault(self.home, self.key, backend=backend or self.backend, force=force)

    def encrypt_all(self):
        return vault.encrypt_paths(self.home, self.key, [str(self.secrets)], recursive=True)

    def cli(self, *args, **kwargs):
        """跑 vault.py 子进程；返回 CompletedProcess（text=False）。"""
        argv = [sys.executable, str(VAULT_PY), *args]
        env = dict(os.environ)
        env.pop("SECVAULT_HOME", None)
        env.pop("SECVAULT_KEY_FILE", None)
        return subprocess.run(argv, capture_output=True, env=env, timeout=300, **kwargs)


class TestFallbackRoundTrip(VaultTestBase):
    def test_init_creates_aesgcm_key_and_is_idempotent(self):
        first = self.key.read_bytes()
        self.assertIn(vault.AESGCM_KEY_HEADER.encode(), first)
        summary = self.init()  # 幂等
        self.assertEqual(summary["status"], "exists-skip")
        self.assertEqual(summary["backend"], "aesgcm")
        self.assertEqual(self.key.read_bytes(), first)

    def test_encrypt_decrypt_roundtrip(self):
        summary = self.encrypt_all()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["file_count"], 3)
        self.assertEqual(summary["encrypted"], 3)
        for rel, expected in (
            ("a.env", self.plain_a),
            ("nested/b.bin", PLAINTEXT_B),
            ("empty.txt", b""),
        ):
            enc = self.home / vault.ENC_DIRNAME / (rel + ".age")
            self.assertTrue(enc.is_file(), rel)
            self.assertEqual(vault.decrypt_path(self.home, self.key, rel), expected, rel)

    def test_cipher_contains_no_plaintext(self):
        self.encrypt_all()
        blob = (self.home / vault.ENC_DIRNAME / "a.env.age").read_bytes()
        self.assertTrue(blob.startswith(vault.AESGCM_MAGIC))
        self.assertNotIn(b"abc123", blob)
        self.assertNotIn(b"p@ss w0rd", blob)

    def test_skip_unchanged_reuses_ciphertext(self):
        self.encrypt_all()
        again = self.encrypt_all()
        self.assertEqual(again["encrypted"], 0)
        self.assertEqual(again["reused"], 3)

    def test_manifest_fields(self):
        self.encrypt_all()
        mpath = self.home / vault.ENC_DIRNAME / vault.MANIFEST_NAME
        data = json.loads(mpath.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], vault.MANIFEST_SCHEMA)
        self.assertEqual(data["file_count"], 3)
        self.assertEqual(data["backend"], "aesgcm")
        entry = data["files"]["a.env"]
        for key in (
            "path",
            "sha256_plain",
            "sha256_cipher",
            "bytes",
            "encrypted_at",
            "tool_version",
        ):
            self.assertIn(key, entry)
        self.assertEqual(entry["sha256_plain"], hashlib.sha256(self.plain_a).hexdigest())
        self.assertEqual(entry["bytes"], len(self.plain_a))
        cipher_file = self.home / vault.ENC_DIRNAME / "a.env.age"
        self.assertEqual(entry["sha256_cipher"], hashlib.sha256(cipher_file.read_bytes()).hexdigest())


class TestVerifyFailClosed(VaultTestBase):
    def test_verify_ok(self):
        self.encrypt_all()
        result = vault.verify_manifest(self.home)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["failed"], 0)

    def test_verify_detects_tamper(self):
        self.encrypt_all()
        enc = self.home / vault.ENC_DIRNAME / "a.env.age"
        blob = bytearray(enc.read_bytes())
        blob[-1] ^= 0xFF
        enc.write_bytes(bytes(blob))
        result = vault.verify_manifest(self.home)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failures"][0]["reason"], "cipher-sha256-mismatch")

    def test_verify_detects_missing_cipher(self):
        self.encrypt_all()
        (self.home / vault.ENC_DIRNAME / "nested" / "b.bin.age").unlink()
        result = vault.verify_manifest(self.home)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failures"][0]["reason"], "cipher-missing")

    def test_verify_without_manifest_fail_closed(self):
        with self.assertRaises(vault.VaultError):
            vault.verify_manifest(self.home)
        res = self.cli("--home", str(self.home), "verify", "--json")
        self.assertEqual(res.returncode, 2)
        self.assertIn(b"FAIL-CLOSED", res.stderr)

    def test_encrypt_without_key_fail_closed(self):
        home2 = self.tmp / "no-key-vault"
        with self.assertRaises(vault.VaultError):
            vault.encrypt_paths(home2, home2 / "keys" / "age.key", [str(self.secrets)], recursive=True)

    def test_decrypt_without_key_fail_closed(self):
        self.encrypt_all()
        enc = self.home / vault.ENC_DIRNAME / "a.env.age"
        missing = self.tmp / "nokey" / "age.key"
        with self.assertRaises(vault.VaultError):
            vault.decrypt_bytes(self.home, missing, enc.read_bytes())

    def test_decrypt_with_wrong_key_fail_closed(self):
        self.encrypt_all()
        enc = self.home / vault.ENC_DIRNAME / "a.env.age"
        other_home = self.tmp / "other"
        other_key = other_home / "keys" / "age.key"
        vault.init_vault(other_home, other_key, backend="aesgcm")
        with self.assertRaises(vault.VaultError):
            vault.decrypt_bytes(other_home, other_key, enc.read_bytes())

    def test_init_refuses_unknown_existing_key(self):
        home2 = self.tmp / "weird"
        key = home2 / "keys" / "age.key"
        key.parent.mkdir(parents=True)
        key.write_text("not a key\n", encoding="utf-8")
        with self.assertRaises(vault.VaultError):
            vault.init_vault(home2, key, backend="aesgcm")


class TestExecEnv(VaultTestBase):
    def _write_env(self, text: str) -> str:
        (self.secrets / "app.env").write_text(text, encoding="utf-8")
        return "app.env"

    def test_exec_env_injects_variables(self):
        rel = self._write_env("# comment\nexport FOO='bar baz'\nBAZ=qux\n")
        self.encrypt_all()
        checker = (
            "import os,sys;sys.exit(0 if os.environ.get('FOO')=='bar baz' "
            "and os.environ.get('BAZ')=='qux' else 1)"
        )
        rc, out, err = vault.run_exec_env(
            self.home, self.key, rel, [sys.executable, "-c", checker], capture=True
        )
        self.assertEqual(rc, 0, err.decode("utf-8", "replace"))
        self.assertEqual(out, b"")

    def test_exec_env_passes_child_rc(self):
        rel = self._write_env("FOO=1\n")
        self.encrypt_all()
        rc, _out, _err = vault.run_exec_env(
            self.home, self.key, rel, [sys.executable, "-c", "raise SystemExit(7)"], capture=True
        )
        self.assertEqual(rc, 7)

    def test_exec_env_parse_error_fail_closed(self):
        rel = self._write_env("THIS IS NOT KEY VALUE\n")
        self.encrypt_all()
        with self.assertRaises(vault.VaultError):
            vault.run_exec_env(self.home, self.key, rel, [sys.executable, "-c", "pass"])

    def test_parse_env_text(self):
        env = vault.parse_env_text("# c\n\nA=1\nexport B='two words'\nC=\"three\"\n")
        self.assertEqual(env, {"A": "1", "B": "two words", "C": "three"})


class TestCLI(VaultTestBase):
    def test_cli_init_encrypt_verify_decrypt(self):
        res = self.cli("--home", str(self.home), "init", "--backend", "aesgcm")
        self.assertEqual(res.returncode, 0, res.stderr)
        res = self.cli("--home", str(self.home), "encrypt", str(self.secrets), "--recursive")
        self.assertEqual(res.returncode, 0, res.stderr)
        summary = json.loads(res.stdout.decode("utf-8"))
        self.assertEqual(summary["file_count"], 3)
        res = self.cli("--home", str(self.home), "verify", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(json.loads(res.stdout.decode("utf-8"))["ok"])
        res = self.cli("--home", str(self.home), "decrypt", "a.env")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, self.plain_a)

    def test_cli_encrypt_dir_requires_recursive(self):
        res = self.cli("--home", str(self.home), "encrypt", str(self.secrets))
        self.assertEqual(res.returncode, 2)
        self.assertIn(b"FAIL-CLOSED", res.stderr)


@unittest.skipUnless(
    vault.find_age_bin("age") is not None and vault.find_age_bin("age-keygen") is not None,
    "本机无便携 age/age-keygen，跳过 age 后端用例（AES-GCM 兜底用例仍覆盖）",
)
class TestAgeBackend(VaultTestBase):
    backend = "age"

    def test_init_creates_age_identity_and_recipients(self):
        recipients = vault.resolve_recipients_file(self.home)
        self.assertTrue(recipients.is_file())
        self.assertRegex(recipients.read_text(encoding="utf-8").strip(), r"^age1[0-9a-z]+$")
        self.assertIn("AGE-SECRET-KEY-1", self.key.read_text(encoding="utf-8"))

    def test_age_roundtrip(self):
        summary = self.encrypt_all()
        self.assertEqual(summary["encrypted"], 3)
        self.assertEqual(summary["backend"], ["age"])
        self.assertEqual(vault.decrypt_path(self.home, self.key, "nested/b.bin"), PLAINTEXT_B)
        self.assertEqual(vault.decrypt_path(self.home, self.key, "a.env"), self.plain_a)
        result = vault.verify_manifest(self.home)
        self.assertTrue(result["ok"])

    def test_age_encrypt_without_recipients_derives_from_key(self):
        (vault.resolve_recipients_file(self.home)).unlink()
        summary = self.encrypt_all()
        self.assertEqual(summary["encrypted"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
