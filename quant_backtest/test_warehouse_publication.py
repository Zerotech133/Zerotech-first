"""跨独立 Python 进程的 Windows 发布锁与崩溃证据。"""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd
import pytest

from test_warehouse_commit import make_run, staged_table, sha


@pytest.fixture
def commit():
    return importlib.import_module("warehouse_commit")


def child(code, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()))
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", "-B", "-c", code, *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


LOCK_CHILD = """
import sys
from pathlib import Path
from warehouse_commit import publication_guard
with publication_guard(sys.argv[1], mode=sys.argv[2]):
    Path(sys.argv[3]).write_text('acquired')
"""


def test_shared_read_blocks_exclusive_writer_until_materialization_finishes(
    commit, tmp_path
):
    marker = tmp_path / "writer-ready"
    data = tmp_path / "a.parquet"
    pd.DataFrame({"value": [1]}).to_parquet(data, index=False)
    proc = None
    try:
        with commit.publication_guard(tmp_path, mode="read"):
            proc = child(LOCK_CHILD, tmp_path, "write", marker)
            # 确认进程已启动；释放 read 前枚举和物化仍在保护中。
            time.sleep(0.8)
            assert proc.poll() is None and not marker.exists()
            files = list(tmp_path.glob("*.parquet"))
            assert pd.concat([pd.read_parquet(f) for f in files])["value"].tolist() == [
                1
            ]
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, (out, err)
        assert marker.exists()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_shared_readers_and_different_roots_do_not_block(commit, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    with commit.publication_guard(one, mode="read"):
        for root, mode in [(one, "read"), (two, "write")]:
            marker = root / ("ready-" + mode)
            proc = child(LOCK_CHILD, root, mode, marker)
            try:
                out, err = proc.communicate(timeout=15)
                assert proc.returncode == 0, (out, err)
                assert marker.exists()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


def test_crashed_multifile_publication_never_reads_mixed_version(commit, tmp_path):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    frozen = tmp_path / "arguments.json"
    frozen.write_text(
        json.dumps(dict(files=files, manifest=manifest)), encoding="utf-8"
    )
    code = """
import json, os, sys
from pathlib import Path
from warehouse_runs import RunStore
from warehouse_commit import commit_table
data = json.loads(Path(sys.argv[3]).read_text('utf-8'))
real = os.replace
def crash(src, dst):
    real(src, dst)
    if str(dst) == data['files'][0]['target']:
        os._exit(73)
os.replace = crash
commit_table(data['files'], data['manifest'], store=RunStore(sys.argv[1]),
             run_id=sys.argv[2], table='daily', now=101)
"""
    proc = child(code, store.path, run, frozen)
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 73, (out, err)
    assert pd.read_parquet(tmp_path / "a.parquet")["value"].tolist() == [10]
    assert pd.read_parquet(tmp_path / "b.parquet")["value"].tolist() == [1]
    read_code = """
import sys
from warehouse_commit import publication_guard, PublicationPending
try:
    with publication_guard(sys.argv[1], mode='read'):
        raise RuntimeError('混合版本被放行')
except PublicationPending:
    print('pending')
"""
    reader = child(read_code, tmp_path)
    out, err = reader.communicate(timeout=15)
    assert reader.returncode == 0 and out.strip() == "pending", err
    before = [sha(f["target"]) for f in files]
    assert commit.recover_commits(store, run)[0]["status"] == "unknown"
    assert [sha(f["target"]) for f in files] == before
    with pytest.raises(commit.PublicationPending):
        with commit.publication_guard(tmp_path, mode="read"):
            pytest.fail("恢复无法证明完整版本")


def test_read_pending_check_is_after_lock_acquisition(commit, tmp_path):
    store, run = make_run(tmp_path)
    marker = tmp_path / "reader-entered"
    code = """
import sys
from pathlib import Path
from warehouse_commit import publication_guard, PublicationPending
try:
    with publication_guard(sys.argv[1], mode='read'):
        Path(sys.argv[2]).write_text('unsafe')
except PublicationPending:
    print('pending')
"""
    original = store.event

    def fail(run_id, kind, payload, *, now):
        if kind == "commit_complete":
            raise OSError("完成事件失败")
        return original(run_id, kind, payload, now=now)

    store.event = fail
    # 同进程不可嵌套write；先让独立读进程等锁，直接在锁内制造持久意图标记。
    proc = None
    try:
        with commit.publication_guard(tmp_path, mode="write"):
            proc = child(code, tmp_path, marker)
            time.sleep(0.8)
            assert proc.poll() is None
            pending = tmp_path / ".warehouse-publication" / "pending"
            pending.mkdir(parents=True, exist_ok=True)
            (pending / "test.json").write_text('{"table":"daily"}', encoding="utf-8")
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0 and out.strip() == "pending", err
        assert not marker.exists()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.parametrize("mode", ["shared", "", None])
def test_invalid_mode_rejected(commit, tmp_path, mode):
    with pytest.raises(ValueError):
        with commit.publication_guard(tmp_path, mode=mode):
            pass


def test_crash_immediately_after_intent_still_blocks_reader(commit, tmp_path):
    store, run = make_run(tmp_path)
    code = """
import os, sys, pandas as pd
from warehouse_runs import RunStore
from warehouse_commit import commit_frame
store = RunStore(sys.argv[1])
original = store.event
def crash(run_id, kind, payload, *, now):
    original(run_id,kind,payload,now=now)
    if kind=='commit_intent':
        os._exit(74)
store.event = crash
commit_frame(pd.DataFrame({'code':['a'],'value':[1]}), sys.argv[3],
             store=store,run_id=sys.argv[2],unit_id='a',primary_key=['code'],
             business_columns=['value'],now=101)
"""
    proc = child(code, store.path, run, tmp_path / "daily.parquet")
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 74, (out, err)
    with pytest.raises(commit.PublicationPending):
        with commit.publication_guard(tmp_path, mode="read"):
            pytest.fail("未恢复意图被放行")


def test_multifile_crash_after_manifest_recovers_without_rewriting(commit, tmp_path):
    store, run = make_run(tmp_path, units=("a", "b"))
    files, manifest = staged_table(tmp_path)
    frozen = tmp_path / "all-new-arguments.json"
    frozen.write_text(
        json.dumps(dict(files=files, manifest=manifest)), encoding="utf-8"
    )
    code = """
import json, os, sys
from pathlib import Path
from warehouse_runs import RunStore
from warehouse_commit import commit_table
data=json.loads(Path(sys.argv[3]).read_text('utf-8'))
real=os.replace
def crash(src,dst):
    real(src,dst)
    if str(dst)==data['manifest']['target']:
        os._exit(75)
os.replace=crash
commit_table(data['files'],data['manifest'],store=RunStore(sys.argv[1]),run_id=sys.argv[2],table='daily',now=101)
"""
    proc = child(code, store.path, run, frozen)
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 75, (out, err)
    before = [sha(f["target"]) for f in files] + [sha(manifest["target"])]
    store.reconcile(lambda *args: "dead", now=300)
    assert commit.recover_commits(store, run)[0]["status"] == "recovered"
    assert [sha(f["target"]) for f in files] + [sha(manifest["target"])] == before
    with commit.publication_guard(tmp_path, mode="read"):
        actual = [pd.read_parquet(f["target"])["value"].tolist() for f in files]
        assert actual == [[10], [20]]
        assert json.loads(Path(manifest["target"]).read_text("utf-8")) == {"version": 2}
    assert store.get_run(run)["metrics"]["revised_rows"] == 2
    assert commit.recover_commits(store, run)[0]["status"] == "complete"


# ---- 锁文件 open 的瞬时 Errno 13（2026-11-07 K-ORG-20261107-LOCKOPEN）----
# 两个平台分支的加锁都是阻塞等待，但 os.open 原先写在等待之外：瞬态拒绝会把
# "等一会儿"变成"发布失败"。与 tools/devtask.py::_tasks_file_lock 的形态对齐。


def _flaky_os_open(monkeypatch, target, fail_times):
    real_open = os.open
    calls = {"n": 0}

    def flaky(path, flags, mode=0o777, **kw):
        if str(path) == str(target):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", flaky)
    return calls


def test_open_lock_descriptor_retries_transient_permission_error(commit, tmp_path,
                                                                 monkeypatch):
    lock = tmp_path / ".warehouse-publication.lock"
    calls = _flaky_os_open(monkeypatch, lock, fail_times=2)
    fd = commit._open_lock_descriptor(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert lock.exists()
    finally:
        os.close(fd)
    assert calls["n"] >= 3, calls


def test_open_lock_descriptor_gives_up_after_deadline(commit, tmp_path, monkeypatch):
    lock = tmp_path / ".warehouse-publication.lock"
    _flaky_os_open(monkeypatch, lock, fail_times=10 ** 9)
    monkeypatch.setattr(commit, "_LOCK_OPEN_TIMEOUT_S", 0.05)
    with pytest.raises(PermissionError):
        commit._open_lock_descriptor(lock, os.O_RDWR | os.O_CREAT, 0o600)


def test_publication_guard_survives_transient_open_error(commit, tmp_path, monkeypatch):
    """端到端：锁文件 open 抖两次后，发布仍然走通（不再上升为发布失败）。"""
    lock = tmp_path / ".warehouse-publication.lock"
    calls = _flaky_os_open(monkeypatch, lock, fail_times=2)
    with commit.publication_guard(tmp_path, mode="write"):
        pass
    assert calls["n"] >= 3, calls
