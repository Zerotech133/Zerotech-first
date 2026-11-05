# -*- coding: utf-8 -*-
"""B4：db_dump_daily 压缩回归修复的守卫测试。

回归背景（2026-11-05 首次季度恢复演练）：K-STOCK-414 用
`gzip.open(dst, "wb")` 包 `subprocess.run(cmd, stdout=f)`，Windows 下子进程
直写底层 fd，GzipFile 头到 close() 才落盘 → 产物是「明文 SQL + 尾部空 gzip 头」，
严格恢复（gzip -dc / psql ON_ERROR_STOP）报错。修复后必须保证：
产物是真 gzip、sha256 正确、失败不留半成品、超时会中止。
"""
import gzip
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "ops" / "db_dump_daily.py"
_spec = importlib.util.spec_from_file_location("db_dump_daily", MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

PAYLOAD = b"-- drill dump\n" + b"x" * 350000


def _py(code):
    return [sys.executable, "-c", code]


def test_gzip_payload_streams_valid_gzip_with_sha(tmp_path):
    dst = tmp_path / "a.sql.gz"
    cmd = _py("import sys; sys.stdout.buffer.write(b'-- drill dump\\n' + b'x'*350000)")
    rc, written, sha, stderr = mod._gzip_payload(cmd, str(dst), timeout=30)
    assert rc == 0
    assert written == len(PAYLOAD)
    assert sha == hashlib.sha256(PAYLOAD).hexdigest()
    with open(dst, "rb") as f:
        assert f.read(2) == mod.GZIP_MAGIC
    with gzip.open(dst, "rb") as g:
        assert g.read() == PAYLOAD
    ok, reason = mod._validate_gzip(str(dst))
    assert ok, reason


def test_gzip_payload_keeps_nonzero_rc(tmp_path):
    dst = tmp_path / "b.sql.gz"
    rc, _written, _sha, stderr = mod._gzip_payload(_py("import sys; sys.exit(3)"),
                                                   str(dst), timeout=30)
    assert rc == 3


def test_validate_rejects_plain_sql(tmp_path):
    # 10-29 回归产物的形态：明文 SQL（没有 gzip 魔数）
    p = tmp_path / "plain.sql.gz"
    p.write_bytes(b"--\n-- PostgreSQL database cluster dump\n" + b"x" * 500)
    ok, reason = mod._validate_gzip(str(p))
    assert not ok
    assert reason == "bad_magic"


def test_validate_rejects_truncated_gzip(tmp_path):
    p = tmp_path / "truncated.sql.gz"
    with gzip.open(p, "wb") as gz:
        gz.write(b"hello" * 1000)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    ok, _reason = mod._validate_gzip(str(p))
    assert not ok


def test_dump_one_success_atomic_replace(tmp_path, monkeypatch):
    def fake_cmd(item):
        return _py("import sys; sys.stdout.buffer.write(b'-- dump\\n' + b'y'*1000)")

    monkeypatch.setattr(mod, "_dump_command", fake_cmd)
    item = {"key": "unit", "container": "x", "cmd": "x", "file": "unit.sql.gz"}
    ok, size, sha = mod.dump_one(item, str(tmp_path))
    assert ok is True
    dst = tmp_path / "unit.sql.gz"
    assert dst.exists() and size == dst.stat().st_size
    assert sha == hashlib.sha256(b"-- dump\n" + b"y" * 1000).hexdigest()
    assert not (tmp_path / "unit.sql.gz.part").exists()
    with gzip.open(dst, "rb") as g:
        assert g.read() == b"-- dump\n" + b"y" * 1000


def test_dump_one_failure_leaves_no_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_dump_command", lambda item: _py("import sys; sys.exit(2)"))
    item = {"key": "unit", "container": "x", "cmd": "x", "file": "unit.sql.gz"}
    ok, size, sha = mod.dump_one(item, str(tmp_path))
    assert (ok, size, sha) == (False, 0, "")
    assert not (tmp_path / "unit.sql.gz").exists()
    assert not (tmp_path / "unit.sql.gz.part").exists()


def test_dump_one_timeout_kills_child(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_dump_command",
                        lambda item: _py("import time; time.sleep(5)"))
    item = {"key": "unit", "container": "x", "cmd": "x", "file": "unit.sql.gz"}
    ok, size, sha = mod.dump_one(item, str(tmp_path), timeout=1)
    assert (ok, size, sha) == (False, 0, "")
    assert not (tmp_path / "unit.sql.gz").exists()
