# -*- coding: utf-8 -*-
"""cleanup_plaintext_secrets.scan 单元测试（工位 #371 · 门 N6）。

约定：把 tools/ 目录注入 sys.path，直接 `import cleanup_plaintext_secrets`。
关键红线：全程不得删除任何文件；scan 仅 dry-run 列出。
"""

import os
import sys
import tempfile

from pathlib import Path

# --- sys.path 注入（参考 tools/tests 约定） ---
QBT = Path(__file__).resolve().parents[1]  # tools/tests -> tools/
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))

from cleanup_plaintext_secrets import (  # noqa: E402
    scan,
    KNOWN_PLAINTEXT_RISK_PATHS,
)


def test_scan_reports_existing_temp_file():
    # 临时造一个存在的『明文』文件，但【不】混入 KNOWN 常量；
    # 直接传给 scan(paths=[...]) 断言返回包含该路径。
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("this-is-a-synthetic-plaintext-token-do-not-use\n")
        result = scan(paths=[path], dry_run=True)
        assert path in result
    finally:
        # 无论如何都清理（测试自身的临时文件），不属于『扫描删除』动作。
        if os.path.exists(path):
            os.unlink(path)


def test_scan_dry_run_keeps_file():
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("synthetic-plaintext-should-survive-scan\n")
        # dry_run=True（默认）扫描后，临时文件必须依旧存在。
        scan(paths=[path], dry_run=True)
        assert os.path.exists(path), "dry-run 扫描不得删除文件"
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_scan_skips_missing_path():
    missing = "C:/nonexistent_dir_371/definitely_missing_token.txt"
    # 对不存在路径不得报错；返回结果不应包含它。
    result = scan(paths=[missing], dry_run=True)
    assert missing not in result


def test_known_paths_is_documentation_tuple():
    # KNOWN 常量仅为风险登记，结构正确即可（不对其文件做操作）。
    assert isinstance(KNOWN_PLAINTEXT_RISK_PATHS, tuple)
    assert "config/tushare_token.txt" in KNOWN_PLAINTEXT_RISK_PATHS
    assert ".env.local" in KNOWN_PLAINTEXT_RISK_PATHS
