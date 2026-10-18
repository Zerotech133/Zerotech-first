# -*- coding: utf-8 -*-
"""scan_secrets 只读扫描器测试。

注意：本测试源码自身必须不被扫描器命中（防自误报），因此所有疑似
凭据样例均以字符串拼接构造，源码中不出现任何完整命中形态的字面量。
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCANNER = Path(__file__).resolve().parent.parent / "tools" / "scan_secrets.py"
PYTHON = sys.executable


def run_scan(args, cwd=None):
    return subprocess.run(
        [PYTHON, str(SCANNER)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
    )


def make_file(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---- 疑似凭据样例（拼接构造，避免本文件被自扫描命中） ----
Q = '"'
KEYWORD_LINE = "api_key = " + Q + "B" * 12 + Q
SK_VALUE = "sk-" + "A" * 18
SK_SEP_VALUE = "sk-" + "s" + "t0lcu-elelc-0g7d6l9w"
AWS_VALUE = "AKIA" + "2" * 16
PEM_LINE = "-----BEGIN " + "RSA " + "PRIVATE KEY-----"
BEARER_LINE = "bearer " + "C" * 24

CATEGORY_CASES = [
    (KEYWORD_LINE, "KEYWORD_ASSIGN"),
    (SK_VALUE, "SK_KEY"),
    (SK_SEP_VALUE, "SK_KEY_SEP"),
    (AWS_VALUE, "AWS_AKID"),
    (PEM_LINE, "PRIVATE_KEY"),
    (BEARER_LINE, "BEARER_TOKEN"),
]


@pytest.fixture()
def tmpdir_factory_():
    d = Path(tempfile.mkdtemp(prefix="scan_secrets_test_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("line,category", CATEGORY_CASES)
def test_each_pattern_hits(tmpdir_factory_, line, category):
    f = make_file(tmpdir_factory_, "case.py", line + "\n")
    r = run_scan(["--paths", str(f)])
    assert r.returncode == 0, r.stderr
    assert category in r.stdout
    assert f"{f}:1 | {category}" in r.stdout  # 扫描器原样回显传入路径


def test_clean_file_no_false_positive(tmpdir_factory_):
    f = make_file(
        tmpdir_factory_, "clean.py",
        "import os\n"
        "x = settings.glm_api_key\n"  # 变量传递，非明文赋值
        "api_key = os.environ.get('LLM_API_KEY')\n"  # 环境变量读取，无内联值
        "max_tokens = 1024\n"
        "authorization = 'Bearer '\n",
    )
    r = run_scan(["--paths", str(f)])
    assert r.returncode == 0
    assert "命中 0 处" in r.stdout


def test_mask_first4_last2(tmpdir_factory_):
    f = make_file(tmpdir_factory_, "mask.py", SK_VALUE + "\n")
    r = run_scan(["--paths", str(f)])
    # SK_VALUE = sk- + A*18，脱敏后：前 4 字符 sk-A + *** + 后 2 字符 AA
    assert "sk-A***AA" in r.stdout
    # 完整值绝不能出现在输出中
    assert SK_VALUE not in r.stdout


def test_large_file_skipped(tmpdir_factory_):
    f = tmpdir_factory_ / "big.txt"
    f.write_bytes((b"x" * 1024) * 1100)  # 约 1.07MB > 1MB
    with f.open("ab") as fh:
        fh.write((SK_VALUE + "\n").encode())
    r = run_scan(["--paths", str(f)])
    assert r.returncode == 0
    assert "too_large" in r.stdout
    assert "SK_KEY" not in r.stdout


def test_binary_file_skipped(tmpdir_factory_):
    f = tmpdir_factory_ / "bin.dat"
    f.write_bytes(b"\x00\x01\x02" + SK_VALUE.encode() + b"\x00")
    r = run_scan(["--paths", str(f)])
    assert r.returncode == 0
    assert "binary" in r.stdout
    assert "SK_KEY" not in r.stdout


def test_strict_exit_code(tmpdir_factory_):
    f = make_file(tmpdir_factory_, "hit.py", SK_VALUE + "\n")
    r = run_scan(["--paths", str(f), "--strict"])
    assert r.returncode == 1
    clean = make_file(tmpdir_factory_, "ok.py", "nothing here\n")
    r2 = run_scan(["--paths", str(clean), "--strict"])
    assert r2.returncode == 0


def test_empty_dir_neutral(tmpdir_factory_):
    empty = tmpdir_factory_ / "empty"
    empty.mkdir()
    r = run_scan(["--paths", str(empty)])
    assert r.returncode == 0
    assert "命中 0 处" in r.stdout


def test_scanner_and_test_source_self_clean():
    """扫描器与测试源码自身必须零命中（防自误报）。"""
    r = run_scan(["--paths", str(SCANNER), str(Path(__file__).resolve())])
    assert r.returncode == 0, r.stdout
    assert "命中 0 处" in r.stdout


def test_git_status_mode(tmpdir_factory_):
    """在临时 git 仓内验证 --git-status 默认模式能取到 untracked 文件。"""
    repo = tmpdir_factory_ / "repo"
    repo.mkdir()
    assert subprocess.run(["git", "init", "-q"], cwd=repo).returncode == 0
    secret_file = repo / "leak.py"
    secret_file.write_text(SK_VALUE + "\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("hello\n", encoding="utf-8")
    r = run_scan(["--git-status"], cwd=str(repo))
    assert r.returncode == 0
    assert "leak.py:1 | SK_KEY" in r.stdout
    assert "unrelated.txt" not in r.stdout


def test_no_repo_no_paths_refused(tmpdir_factory_):
    """显式 root 纪律：不在 git 仓内且未给 --paths 时拒绝运行（退出码 2）。"""
    bare = tmpdir_factory_ / "bare"
    bare.mkdir()
    r = run_scan([], cwd=str(bare))
    assert r.returncode == 2
    assert "git" in (r.stderr + r.stdout).lower()
