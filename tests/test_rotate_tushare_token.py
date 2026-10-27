# -*- coding: utf-8 -*-
"""X5 Tushare token 写入路径测试（WO-X5-TOKEN-WIRE-20261027）。"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "secrets" / "rotate_tushare_token.py"
_SPEC = importlib.util.spec_from_file_location("rotate_tushare_token", str(SCRIPT))
RT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(RT)


def test_token_from_file_first_nonempty_line(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("\n  abcdefghijklmnopqrstuvwxyz012345  \nignored\n", encoding="utf-8")
    assert RT._token_from_file(str(f)) == "abcdefghijklmnopqrstuvwxyz012345"


def test_token_from_file_missing_or_empty(tmp_path):
    empty = tmp_path / "e.txt"
    empty.write_text("\n\n", encoding="utf-8")
    for p in (str(tmp_path / "nope.txt"), str(empty)):
        r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--dry-run",
                            "--token-file", p],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        # dry-run 只校验存在性：不存在 exit1；空文件存在则前置通过
        assert r.returncode in (0, 1), r.stdout + r.stderr


def test_cli_dry_run_token_file_does_not_leak(tmp_path):
    f = tmp_path / "tok.txt"
    secret = "TOKEN_SHOULD_NOT_APPEAR_1234567890"
    f.write_text(secret + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--dry-run",
                        "--token-file", str(f)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--token-file" in r.stdout or "token_file" in r.stdout
    assert secret not in (r.stdout + r.stderr)
    assert "\"steps: token-file" in r.stdout or "steps: token-file" in r.stdout


def test_cli_dry_run_missing_token_file_exit1(tmp_path):
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "--dry-run",
                        "--token-file", str(tmp_path / "missing.txt")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 1
    assert "WARN" in r.stdout
