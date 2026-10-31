# -*- coding: utf-8 -*-
"""patchset_journal 单测（P0 事务崩溃一致性，WO-LEGION-PATCHSET-JOURNAL-20261031）。

覆盖：
- begin：原子落盘 intent/备份、created/old_sha256/new_sha256 口径、失败不留半成品；
- 参数/路径围栏：坏 patchset_id、重复 path、`..`/绝对路径/journal 目录、坏 content；
- 正常提交流：record_applied（含磁盘校验/幂等）→ complete（未全 applied 拒绝）→ recover 无动作；
- 崩溃恢复：部分写后回滚、新建文件删除、旧文件按备份恢复、「写了但没记账」按 sha 识别；
- abort：回滚 + 幂等；已完成事务不可 abort；
- resume：全部文件已是新内容 → 前滚 completed；不匹配 → 回落回滚；
- 冲突：第三方修改保留现状（不覆盖）；备份指针被篡改 → backup_missing 不越界；
- 坏 journal 隔离为 <id>.corrupt，重复 recover 幂等；空目录/缺失根目录不崩；
- prune：只清终态/隔离目录、未完成保留、dry_run 不删；status：汇总与单条明细；
- 只写 journal 目录 / 无 tmp 残留。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]  # agent_platform
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from legion_ide import patchset_journal as J  # noqa: E402

A = "agent_platform/pkg/a.py"
B = "agent_platform/pkg/b.py"
EMPTY = "agent_platform/pkg/empty.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / "agent_platform" / "pkg").mkdir(parents=True)
    (wt / A).write_text("x = 1\n", encoding="utf-8", newline="\n")
    return wt


def _files():
    return [
        {"path": A, "content": "x = 2\n"},
        {"path": B, "content": "y = 3\n"},
    ]


def _intent(wt: Path, pid: str) -> dict:
    path = wt / J.JOURNAL_DIR / pid / J.INTENT_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def _write_intent(wt: Path, pid: str, doc: dict) -> Path:
    jdir = wt / J.JOURNAL_DIR / pid
    jdir.mkdir(parents=True, exist_ok=True)
    path = jdir / J.INTENT_FILE
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _apply_and_record(wt: Path, pid: str, rel: str, content: str) -> dict:
    target = wt / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return J.record_applied(wt, pid, rel)


def _read(wt: Path, rel: str) -> str:
    return (wt / rel).read_text(encoding="utf-8")


def _only_outside_journal(wt: Path) -> list[str]:
    return sorted(
        path.relative_to(wt).as_posix()
        for path in wt.rglob("*")
        if path.is_file() and J.JOURNAL_DIR not in path.parts
    )


# ---------------------------------------------------------------------------
# 1) begin：原子落盘 + 旧内容备份
# ---------------------------------------------------------------------------
def test_begin_writes_intent_and_backup(tmp_path):
    wt = _worktree(tmp_path)
    result = J.begin(wt, "ps-1", _files(), task="T-BOOT", owner="DEV1")
    assert result["ok"] is True
    assert result["state"] == "open"
    assert result["patchset_id"] == "ps-1"

    doc = _intent(wt, "ps-1")
    assert doc["schema"] == J.SCHEMA
    assert doc["task"] == "T-BOOT" and doc["owner"] == "DEV1"
    assert [entry["path"] for entry in doc["files"]] == [A, B]
    entry_a, entry_b = doc["files"]
    assert entry_a["created"] is False and entry_a["applied"] is False
    assert entry_a["old_sha256"] == hashlib.sha256(b"x = 1\n").hexdigest()
    assert entry_a["new_sha256"] == hashlib.sha256(b"x = 2\n").hexdigest()
    assert entry_b["created"] is True
    assert entry_b["old_sha256"] == hashlib.sha256(b"").hexdigest()

    backup = wt / J.JOURNAL_DIR / "ps-1" / entry_a["backup"]
    assert backup.read_bytes() == b"x = 1\n"
    assert entry_b["backup"] == ""

    # journal 目录之外只有原有文件 A（begin 不碰业务文件）
    assert _only_outside_journal(wt) == [A]
    # 原子写不留 tmp
    assert not list((wt / J.JOURNAL_DIR / "ps-1").glob("*.tmp-*"))


def test_begin_is_not_silently_overwriting(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-dup", [{"path": A, "content": "x = 2\n"}])
    with pytest.raises(J.JournalError) as exc_info:
        J.begin(wt, "ps-dup", [{"path": A, "content": "x = 3\n"}])
    assert "已存在" in str(exc_info.value)


def test_begin_requires_worktree(tmp_path):
    with pytest.raises(J.JournalError):
        J.begin(tmp_path / "missing-wt", "ps-x", [{"path": "a.txt", "content": "1"}])
    assert not (tmp_path / "missing-wt").exists()


@pytest.mark.parametrize("files,needle", [
    ([], "1–20"),
    ([{"path": A, "content": "1"}, {"path": A, "content": "2"}], "重复"),
    ([{"path": "../escape.txt", "content": "1"}], "越界"),
    ([{"path": "/abs/escape.txt", "content": "1"}], "非法"),
    ([{"path": J.JOURNAL_DIR + "/self.json", "content": "1"}], "journal 目录"),
    ([{"path": A, "content": 123}], "字符串"),
])
def test_begin_rejects_bad_files(tmp_path, files, needle):
    wt = _worktree(tmp_path)
    with pytest.raises(J.JournalError) as exc_info:
        J.begin(wt, "ps-bad", files)
    assert needle in str(exc_info.value)
    assert not (wt / J.JOURNAL_DIR / "ps-bad").exists()  # 失败不留半成品


def test_begin_rejects_bad_patchset_id(tmp_path):
    wt = _worktree(tmp_path)
    for pid in ("", "a/b", "../x", "x" * 81):
        with pytest.raises(J.JournalError):
            J.begin(wt, pid, [{"path": A, "content": "x = 2\n"}])


def test_operations_on_missing_journal_raise(tmp_path):
    wt = _worktree(tmp_path)
    with pytest.raises(J.JournalError):
        J.record_applied(wt, "ps-none", A)
    with pytest.raises(J.JournalError):
        J.complete(wt, "ps-none")
    with pytest.raises(J.JournalError):
        J.abort(wt, "ps-none")
    with pytest.raises(J.JournalError):
        J.status(wt, "ps-none")


# ---------------------------------------------------------------------------
# 2) record_applied：磁盘校验 + 幂等
# ---------------------------------------------------------------------------
def test_record_applied_verifies_disk_content(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-v", [{"path": A, "content": "x = 2\n"}])
    with pytest.raises(J.JournalError):  # 还没写盘就记账 → 拒
        J.record_applied(wt, "ps-v", A)
    with pytest.raises(J.JournalError):  # 传入错误 sha256 → 拒
        J.record_applied(wt, "ps-v", A, sha256="0" * 64)

    (wt / A).write_text("x = 2\n", encoding="utf-8", newline="\n")
    res = J.record_applied(wt, "ps-v", A)
    assert res["state"] == "applying"
    assert res["applied_count"] == 1 and res["total"] == 1
    again = J.record_applied(wt, "ps-v", A)
    assert again["already"] is True
    assert _intent(wt, "ps-v")["state"] == "applying"


def test_record_applied_rejects_unknown_path(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-v2", [{"path": A, "content": "x = 2\n"}])
    with pytest.raises(J.JournalError):
        J.record_applied(wt, "ps-v2", B)


# ---------------------------------------------------------------------------
# 3) 成功提交流 + status/recover
# ---------------------------------------------------------------------------
def test_success_flow_complete_and_recover_noop(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-ok", _files(), task="T1", owner="DEV1")
    _apply_and_record(wt, "ps-ok", A, "x = 2\n")
    res = _apply_and_record(wt, "ps-ok", B, "y = 3\n")
    assert res["applied_count"] == 2

    done = J.complete(wt, "ps-ok")
    assert done["state"] == "completed" and done["already"] is False
    assert sorted(done["files"]) == sorted([A, B])
    assert J.complete(wt, "ps-ok")["already"] is True  # 幂等

    assert _read(wt, A) == "x = 2\n"
    assert _read(wt, B) == "y = 3\n"
    recovered = J.recover(wt)
    assert recovered["actions"] == []
    assert recovered["counts"]["skipped"] == 1
    assert J.status(wt)["counts"] == {"completed": 1}
    with pytest.raises(J.JournalError):  # 终态不允许再 record_applied
        J.record_applied(wt, "ps-ok", A)
    assert not list((wt / J.JOURNAL_DIR).rglob("*.tmp-*"))


def test_complete_requires_all_files_applied(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-c", _files())
    _apply_and_record(wt, "ps-c", A, "x = 2\n")
    with pytest.raises(J.JournalError) as exc_info:
        J.complete(wt, "ps-c")
    assert "未 applied" in str(exc_info.value)
    assert _intent(wt, "ps-c")["state"] == "applying"


# ---------------------------------------------------------------------------
# 4) 崩溃恢复：部分写 / 新建删除 / 旧文件恢复 / 写后未记账
# ---------------------------------------------------------------------------
def test_crash_after_partial_apply_rolls_back(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-crash", _files())
    _apply_and_record(wt, "ps-crash", A, "x = 2\n")  # A 已写且记账；B 未写
    assert _read(wt, A) == "x = 2\n"

    recovered = J.recover(wt)
    actions = {(item["action"], item["path"]) for item in recovered["actions"]}
    assert ("restored", A) in actions
    assert _read(wt, A) == "x = 1\n"  # 旧内容按备份恢复
    assert not (wt / B).exists()
    doc = _intent(wt, "ps-crash")
    assert doc["state"] == "recovered"
    assert doc["recover_mode"] == "rollback"
    assert recovered["counts"]["scanned"] == 1
    assert recovered["counts"]["recovered"] == 1
    # 幂等：再 recover 无动作
    assert J.recover(wt)["actions"] == []


def test_crash_deletes_created_file_written_without_record(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-new", _files())
    # 模拟崩溃窗口：新建文件已写盘，但 record_applied 尚未执行
    (wt / B).write_text("y = 3\n", encoding="utf-8", newline="\n")
    recovered = J.recover(wt)
    assert any(item["action"] == "deleted_created" and item["path"] == B
               for item in recovered["actions"])
    assert not (wt / B).exists()
    assert _read(wt, A) == "x = 1\n"


def test_crash_restores_modified_file_written_without_record(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-mod", [{"path": A, "content": "x = 9\n"}])
    (wt / A).write_text("x = 9\n", encoding="utf-8", newline="\n")
    recovered = J.recover(wt)
    assert any(item["action"] == "restored" and item["path"] == A
               for item in recovered["actions"])
    assert _read(wt, A) == "x = 1\n"


def test_recover_restores_empty_old_file(tmp_path):
    wt = _worktree(tmp_path)
    empty = wt / EMPTY
    empty.write_bytes(b"")
    J.begin(wt, "ps-empty", [{"path": EMPTY, "content": "z = 1\n"}])
    empty.write_text("z = 1\n", encoding="utf-8", newline="\n")
    J.recover(wt)
    assert empty.read_bytes() == b""


def test_recover_conflict_keeps_external_change(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-cf", [{"path": A, "content": "x = 2\n"}])
    (wt / A).write_text("external\n", encoding="utf-8", newline="\n")  # 第三方修改
    recovered = J.recover(wt)
    assert any(item["action"] == "conflict" and item["path"] == A
               for item in recovered["actions"])
    assert _read(wt, A) == "external\n"  # 不覆盖第三方内容


def test_recover_tampered_backup_pointer_is_safe(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-tamper", [{"path": A, "content": "x = 2\n"}])
    doc = _intent(wt, "ps-tamper")
    doc["files"][0]["backup"] = "../../evil.bak"  # 磁盘 intent 被篡改
    _write_intent(wt, "ps-tamper", doc)
    _apply_and_record(wt, "ps-tamper", A, "x = 2\n")
    recovered = J.recover(wt)
    assert any(item["action"] == "backup_missing" for item in recovered["actions"])
    assert _read(wt, A) == "x = 2\n"  # 无备份可恢复 → 保留现状，不越界写


def test_recover_never_writes_outside_worktree(tmp_path):
    wt = _worktree(tmp_path)
    doc = {
        "schema": J.SCHEMA,
        "patchset_id": "ps-evil",
        "state": "applying",
        "files": [{
            "path": "../escape.txt",
            "created": True,
            "old_sha256": hashlib.sha256(b"").hexdigest(),
            "new_sha256": hashlib.sha256(b"evil\n").hexdigest(),
            "backup": "",
            "applied": True,
        }],
    }
    _write_intent(wt, "ps-evil", doc)
    recovered = J.recover(wt)
    assert any(item["action"] == "unsafe_path" for item in recovered["actions"])
    assert not (tmp_path / "escape.txt").exists()  # 绝不越出 worktree 写
    assert _intent(wt, "ps-evil")["state"] == "recovered"


# ---------------------------------------------------------------------------
# 5) abort
# ---------------------------------------------------------------------------
def test_abort_rolls_back_and_is_idempotent(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-ab", _files())
    _apply_and_record(wt, "ps-ab", A, "x = 2\n")
    _apply_and_record(wt, "ps-ab", B, "y = 3\n")
    result = J.abort(wt, "ps-ab", reason="测试放弃")
    assert result["state"] == "aborted"
    assert _read(wt, A) == "x = 1\n"
    assert not (wt / B).exists()
    doc = _intent(wt, "ps-ab")
    assert doc["abort_reason"] == "测试放弃"
    assert doc["state"] == "aborted"
    again = J.abort(wt, "ps-ab")
    assert again["already"] is True
    assert J.recover(wt)["actions"] == []


def test_abort_completed_raises(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-cb", [{"path": A, "content": "x = 2\n"}])
    _apply_and_record(wt, "ps-cb", A, "x = 2\n")
    J.complete(wt, "ps-cb")
    with pytest.raises(J.JournalError):
        J.abort(wt, "ps-cb")
    assert _read(wt, A) == "x = 2\n"  # 已完成事务不被回滚


# ---------------------------------------------------------------------------
# 6) resume 前滚 / 回落
# ---------------------------------------------------------------------------
def test_recover_resume_commits_when_all_files_match(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-rs", _files())
    _apply_and_record(wt, "ps-rs", A, "x = 2\n")
    _apply_and_record(wt, "ps-rs", B, "y = 3\n")
    assert _intent(wt, "ps-rs")["state"] == "applying"  # 模拟崩溃在 complete 前

    recovered = J.recover(wt, resume=True)
    assert any(item["action"] == "committed_forward" for item in recovered["actions"])
    assert _intent(wt, "ps-rs")["state"] == "completed"
    assert _read(wt, A) == "x = 2\n" and _read(wt, B) == "y = 3\n"
    assert J.recover(wt)["actions"] == []  # 终态后幂等


def test_recover_default_rolls_back_even_if_all_applied(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-rb", _files())
    _apply_and_record(wt, "ps-rb", A, "x = 2\n")
    _apply_and_record(wt, "ps-rb", B, "y = 3\n")
    recovered = J.recover(wt)  # resume=False：原子性优先，未 complete 一律回滚
    assert _intent(wt, "ps-rb")["state"] == "recovered"
    assert _read(wt, A) == "x = 1\n"
    assert not (wt / B).exists()
    assert recovered["counts"]["recovered"] == 1


def test_recover_resume_falls_back_to_rollback_on_mismatch(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-fb", _files())
    _apply_and_record(wt, "ps-fb", A, "x = 2\n")  # B 未写 → 全量不匹
    recovered = J.recover(wt, resume=True)
    assert recovered["actions"], recovered
    doc = _intent(wt, "ps-fb")
    assert doc["state"] == "recovered"
    assert doc["recover_mode"] == "resume_fallback_rollback"


# ---------------------------------------------------------------------------
# 7) 坏 journal / 空目录
# ---------------------------------------------------------------------------
def test_recover_corrupt_journal_quarantined_and_idempotent(tmp_path):
    wt = _worktree(tmp_path)
    jdir = wt / J.JOURNAL_DIR / "ps-bad"
    jdir.mkdir(parents=True)
    (jdir / J.INTENT_FILE).write_text("{not json", encoding="utf-8")
    recovered = J.recover(wt)
    assert any(item["action"] == "corrupt" for item in recovered["actions"])
    assert recovered["counts"]["scanned"] == 1
    assert recovered["counts"]["corrupt"] == 1
    assert (wt / J.JOURNAL_DIR / ("ps-bad" + J.CORRUPT_SUFFIX)).is_dir()
    again = J.recover(wt)
    assert again["actions"] == []  # 已隔离 → 幂等
    assert again["counts"]["scanned"] == 0  # 隔离目录不再算活跃 journal
    assert again["counts"]["corrupt"] == 0
    assert again["corrupt"] == ["ps-bad" + J.CORRUPT_SUFFIX]  # 仅报告存在


def test_recover_orphan_dir_without_intent_is_quarantined(tmp_path):
    wt = _worktree(tmp_path)
    (wt / J.JOURNAL_DIR / "ps-orphan" / "backup").mkdir(parents=True)
    recovered = J.recover(wt)
    assert any(item["action"] == "corrupt" for item in recovered["actions"])
    assert (wt / J.JOURNAL_DIR / ("ps-orphan" + J.CORRUPT_SUFFIX)).is_dir()


def test_recover_and_status_on_empty_worktree(tmp_path):
    wt = tmp_path / "empty-wt"
    wt.mkdir()
    recovered = J.recover(wt)
    assert recovered["ok"] is True
    assert recovered["actions"] == []
    assert recovered["counts"]["scanned"] == 0
    st = J.status(wt)
    assert st["counts"] == {} and st["journals"] == [] and st["corrupt"] == []
    assert J.prune(wt)["removed"] == []


# ---------------------------------------------------------------------------
# 8) prune / status
# ---------------------------------------------------------------------------
def test_prune_removes_finished_keeps_unfinished(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-done", [{"path": A, "content": "x = 2\n"}])
    _apply_and_record(wt, "ps-done", A, "x = 2\n")
    J.complete(wt, "ps-done")
    J.begin(wt, "ps-ab", [{"path": A, "content": "x = 3\n"}])
    J.abort(wt, "ps-ab")
    J.begin(wt, "ps-open", [{"path": A, "content": "x = 4\n"}])

    dry = J.prune(wt, dry_run=True)
    assert {item["journal"] for item in dry["removed"]} == {"ps-done", "ps-ab"}
    assert (wt / J.JOURNAL_DIR / "ps-done").is_dir()  # dry_run 不删

    recent = J.prune(wt, keep_seconds=3600)  # 刚完成的 journal 在保留窗口内
    assert recent["removed"] == []
    assert recent["counts"]["kept"] == 3  # 2 个终态 + 1 个未完成

    result = J.prune(wt)
    assert {item["journal"] for item in result["removed"]} == {"ps-done", "ps-ab"}
    assert not (wt / J.JOURNAL_DIR / "ps-done").exists()
    assert not (wt / J.JOURNAL_DIR / "ps-ab").exists()
    assert (wt / J.JOURNAL_DIR / "ps-open").is_dir()  # 未完成永不 prune
    assert result["counts"]["kept"] == 1


def test_prune_removes_corrupt_quarantine(tmp_path):
    wt = _worktree(tmp_path)
    jdir = wt / J.JOURNAL_DIR / "ps-bad"
    jdir.mkdir(parents=True)
    (jdir / J.INTENT_FILE).write_text("[]", encoding="utf-8")
    J.recover(wt)  # 隔离为 ps-bad.corrupt
    result = J.prune(wt)
    assert {item["journal"] for item in result["removed"]} == {"ps-bad" + J.CORRUPT_SUFFIX}
    assert not (wt / J.JOURNAL_DIR / ("ps-bad" + J.CORRUPT_SUFFIX)).exists()


def test_status_lists_states_and_single_detail(tmp_path):
    wt = _worktree(tmp_path)
    J.begin(wt, "ps-one", _files(), task="T1", owner="DEV1")
    J.begin(wt, "ps-two", [{"path": A, "content": "x = 5\n"}])
    (wt / A).write_text("x = 2\n", encoding="utf-8", newline="\n")
    J.record_applied(wt, "ps-one", A)
    st = J.status(wt)
    assert st["counts"] == {"open": 1, "applying": 1}
    single = J.status(wt, "ps-one")["journal"]
    assert single["patchset_id"] == "ps-one"
    assert single["task"] == "T1" and single["owner"] == "DEV1"
    assert single["files"] == 2 and single["applied"] == 1
    assert len(single["file_entries"]) == 2
    assert single["file_entries"][0]["path"] == A
