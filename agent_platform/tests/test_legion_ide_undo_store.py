# -*- coding: utf-8 -*-
"""LDE undo content-addressed 持久化测试（K-QUANT-20261030-IDEDEEP2）。"""
import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from legion_ide import undo_store as U  # noqa: E402


def test_blob_is_content_addressed_and_survives_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGION_IDE_STATE_DIR", str(tmp_path))
    U.reset_for_tests()
    old = "old = 1\n"
    U.save("T123", "agent_platform/a.py", old, "newsha", "owner")
    sha = hashlib.sha256(old.encode()).hexdigest()
    assert (tmp_path / "undo" / "blobs" / sha).read_text(encoding="utf-8") == old

    # 模拟 8103 重启：内存清空，仅剩磁盘
    U.reset_for_tests()
    rec = U.get("T123", "agent_platform/a.py")
    assert rec and rec["old_content"] == old
    assert rec["new_sha"] == "newsha"
    U.pop("T123", "agent_platform/a.py")
    assert U.get("T123", "agent_platform/a.py") is None


def test_memory_fallback_without_state_dir(monkeypatch):
    monkeypatch.delenv("LEGION_IDE_STATE_DIR", raising=False)
    U.reset_for_tests()
    U.save("T1", "p", "x", "n", "o")
    assert U.get("T1", "p")["old_content"] == "x"
