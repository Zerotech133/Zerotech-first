# -*- coding: utf-8 -*-
"""merge_preflight.merge_head_commit_msg 回退链单测（2026-11-03）。

工单：WO-MERGE-HOOK-MERGEHEAD-20261103
背景：Git 2.55/Win 在 pre-merge-commit 时 MERGE_HEAD/MERGE_MSG 均尚未落盘，
原实现返回空串 → kernel_guard 误报"内核契约改动无 ADR"。本测覆盖：
  1) 正常：优先 MERGE_HEAD；
  2) 回退：MERGE_HEAD 读不到时读 `--git-path MERGE_MSG` 文件；
  3) 兜底：两者皆无时用 GITHEAD_<40hex> 环境变量取待合入 tip sha 的 commit msg；
  4) 全空：返回空串（由 kernel_guard fail-closed 判拒）。
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import merge_preflight as MP  # noqa: E402


def _boom(*_a, **_k):
    raise AssertionError("不应走到该回退分支")


def test_priority_merge_head(monkeypatch):
    """正常合并状态：优先用 MERGE_HEAD 的提交信息。"""
    calls = []

    def fake_git(args, timeout=30):
        calls.append(list(args))
        assert args[-1] == "MERGE_HEAD"
        return 0, "merge task/WO-X: feat [ADR-020]"

    monkeypatch.setattr(MP, "_git_stdout", fake_git)
    monkeypatch.setattr(MP, "_read_git_path_file", _boom)
    monkeypatch.setattr(MP, "_merged_head_sha_from_env", _boom)
    assert MP.merge_head_commit_msg() == "merge task/WO-X: feat [ADR-020]"
    assert len(calls) == 1


def test_fallback_merge_msg_file(monkeypatch):
    """MERGE_HEAD 读不到 → 回退读 `--git-path MERGE_MSG` 文件。"""
    monkeypatch.setattr(MP, "_git_stdout", lambda args, timeout=30: (128, ""))
    monkeypatch.setattr(MP, "_read_git_path_file",
                        lambda name: "merge task/WO-X: [ADR-020]（MERGE_MSG）"
                        if name == "MERGE_MSG" else "")
    monkeypatch.setattr(MP, "_merged_head_sha_from_env", _boom)
    assert MP.merge_head_commit_msg() == "merge task/WO-X: [ADR-020]（MERGE_MSG）"


def test_fallback_githead_env_sha(monkeypatch):
    """MERGE_HEAD/MERGE_MSG 皆无 → 用 GITHEAD_<40hex> 的 tip sha 取 commit msg（实测兜底）。"""
    sha = "9DFCD3F5D7617D3E86C5616CFA293344AF80190A"

    def fake_git(args, timeout=30):
        if args[-1] == "MERGE_HEAD":
            return 128, ""
        if args[-1] == sha:
            return 0, "merge task/WO-X: [ADR-020]（GITHEAD）"
        return 128, ""

    monkeypatch.setattr(MP, "_git_stdout", fake_git)
    monkeypatch.setattr(MP, "_read_git_path_file", lambda name: "")
    out = MP.merge_head_commit_msg(
        environ={"GITHEAD_%s" % sha: "task/WO-X", "PATH": "x"})
    assert out == "merge task/WO-X: [ADR-020]（GITHEAD）"


def test_githead_env_ignores_non_sha_keys(monkeypatch):
    """GITHEAD_ 值不是 40hex sha 时忽略（不猜分支名）。"""
    monkeypatch.setattr(MP, "_git_stdout", lambda args, timeout=30: (128, ""))
    monkeypatch.setattr(MP, "_read_git_path_file", lambda name: "")
    assert MP.merge_head_commit_msg(
        environ={"GITHEAD_short": "task/WO-X", "OTHER": "x"}) == ""


def test_empty_when_all_missing(monkeypatch):
    """三者皆无 → 空串（门禁侧判拒，不静默放行）。"""
    monkeypatch.setattr(MP, "_git_stdout", lambda args, timeout=30: (128, ""))
    monkeypatch.setattr(MP, "_read_git_path_file", lambda name: "")
    monkeypatch.setattr(MP, "_merged_head_sha_from_env", lambda environ=None: "")
    assert MP.merge_head_commit_msg() == ""


def test_read_git_path_file_uses_git_path(monkeypatch, tmp_path):
    """`_read_git_path_file` 用 `git rev-parse --git-path` 定位并读取（相对路径按 REPO 解析）。"""
    msg = tmp_path / "MERGE_MSG"
    msg.write_text("merge task/WO-X: [ADR-020]\n", encoding="utf-8")
    seen = []

    def fake_git(args, timeout=30):
        seen.append(list(args))
        if list(args[:2]) == ["rev-parse", "--git-path"]:
            return 0, str(msg)
        return 128, ""

    monkeypatch.setattr(MP, "_git_stdout", fake_git)
    assert MP._read_git_path_file("MERGE_MSG") == "merge task/WO-X: [ADR-020]"
    assert seen and seen[0][:2] == ["rev-parse", "--git-path"]
