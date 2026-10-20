"""工位X测试：BaseAgent 跨进程文件锁原语 file_lock。

全部落盘 tmp_path，零真实网络；平台分支经 monkeypatch 模块标记隔离。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import roles.base_agent as base_agent
from roles.base_agent import file_lock


def test_file_lock_acquire_and_release(tmp_path):
    """a) 正常加锁解锁：with 内锁文件存在，退出后可再次进入（证明已释放）。"""
    path = tmp_path / "lock.lock"
    with file_lock(path):
        assert path.exists()
    with file_lock(path):  # 能再次进入 => 上一把已释放
        pass
    assert base_agent._HELD_LOCK_PATHS == {}


def test_file_lock_releases_on_exception(tmp_path):
    """b) with 内抛异常：锁仍释放、重入登记清空、句柄关闭（文件可删除佐证）。"""
    path = tmp_path / "lock.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with file_lock(path):
            raise RuntimeError("boom")
    assert base_agent._HELD_LOCK_PATHS == {}
    with file_lock(path):  # 可重新加锁 => 异常退出时已解锁
        pass
    path.unlink()  # Windows 下句柄未关闭会导致删除失败


def test_file_lock_creates_parent_dirs(tmp_path):
    """c) 锁文件父目录不存在时自动创建（多层 parents）。"""
    path = tmp_path / "a" / "b" / "c" / "x.lock"
    assert not path.parent.exists()
    with file_lock(path):
        assert path.exists()
    assert path.parent.is_dir()


def test_file_lock_reentry_rejected(tmp_path):
    """d) 同进程重入：按 docstring 语义显式抛 RuntimeError；外层锁不受影响。"""
    path = tmp_path / "lock.lock"
    with file_lock(path):
        with pytest.raises(RuntimeError, match="重入"):
            with file_lock(path):
                pass  # pragma: no cover - 不应到达
        # 重入被拒后外层锁仍正常持有
    assert base_agent._HELD_LOCK_PATHS == {}
    with file_lock(path):  # 退出后仍可正常重新进入
        pass


def test_platform_branch_isolation(tmp_path, monkeypatch, caplog):
    """e) 平台分支隔离：Windows 上把平台标记改为非 Windows 后，
    Windows 分支（msvcrt spy）不触发、真实 fcntl 路径不触发（fcntl 为
    None，未发生任何 flock 调用/AttributeError），退化为无锁 + warning。"""
    spy = ModuleType("msvcrt_spy")
    spy_calls = []
    spy.locking = lambda *args, **kwargs: spy_calls.append(args)
    monkeypatch.setattr(base_agent, "IS_WINDOWS", False)
    monkeypatch.setattr(base_agent, "msvcrt", spy)
    assert base_agent.fcntl is None  # Windows 上 POSIX 模块本就不可用
    with caplog.at_level(logging.WARNING, logger="os-legion"):
        with file_lock(tmp_path / "lock.lock"):
            pass
    assert spy_calls == []  # Windows 分支未触发
    assert not getattr(base_agent.fcntl, "flock", None)  # fcntl 路径未触发
    assert any("无锁" in r.message for r in caplog.records)


def test_fallback_no_lock_when_modules_missing(tmp_path, monkeypatch, caplog):
    """f) msvcrt/fcntl 均不可用：无锁 fallback + warning；
    closed on failure——退出时句柄一律关闭（文件可删除佐证）。"""
    monkeypatch.setattr(base_agent, "IS_WINDOWS", False)
    monkeypatch.setattr(base_agent, "fcntl", None)
    monkeypatch.setattr(base_agent, "msvcrt", None)
    path = tmp_path / "lock.lock"
    with caplog.at_level(logging.WARNING, logger="os-legion"):
        with file_lock(path):
            path.write_text("critical", encoding="utf-8")  # 无锁仍可写
        assert any("无锁" in r.message for r in caplog.records)
    path.unlink()  # 句柄已关闭（Windows 下句柄未关则删除抛 PermissionError）


CHILD_LOCK_PROBE = r"""
import msvcrt
import sys

path = sys.argv[1]
fh = open(path, "a")
try:
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    print("ACQUIRED")
except OSError:
    print("LOCKED_BY_OTHER")
finally:
    fh.close()
"""


@pytest.mark.skipif(not base_agent.IS_WINDOWS,
                    reason="子进程探针用 msvcrt，仅 Windows")
def test_cross_process_mutual_exclusion(tmp_path):
    """g) 跨进程互斥：本进程持锁期间子进程非阻塞加锁失败；释放后子进程可获取。"""
    path = tmp_path / "lock.lock"
    with file_lock(path):
        probe = subprocess.run(
            [sys.executable, "-c", CHILD_LOCK_PROBE, str(path)],
            capture_output=True, text=True, timeout=30)
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.strip() == "LOCKED_BY_OTHER"
    probe2 = subprocess.run(
        [sys.executable, "-c", CHILD_LOCK_PROBE, str(path)],
        capture_output=True, text=True, timeout=30)
    assert probe2.returncode == 0, probe2.stderr
    assert probe2.stdout.strip() == "ACQUIRED"


# ---------------- h/i/j. 锁句柄打开重试（WO-LEGION-S13-01） ----------------
#
# 背景：Windows 上多进程几乎同时首次创建同一锁文件时，CreateFile 偶发
# ERROR_ACCESS_DENIED（CRT 映射为 EACCES），实测 pytest 下 4 进程 × 8 次
# 并发 submit 失败率 ≈27%。产品侧改为有限次退避重试。以下三例分别承重
# 「会重试」「耗尽仍抛而不降级」「重试面不扩大到别的 OSError」。


def _patch_lock_open(monkeypatch, target, script):
    """把 Path.open 换成按脚本失败的可数版本；返回调用计数。

    script(n) -> 异常实例或 None（None 表示本次放行）。仅对 target 生效，
    其余路径走真实 open，避免污染 tmp 目录下的其它 I/O。
    """
    real_open = Path.open
    calls = {"n": 0}

    def patched(self, *a, **kw):
        if self == target:
            calls["n"] += 1
            exc = script(calls["n"])
            if exc is not None:
                raise exc
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", patched)
    return calls


def test_lock_handle_retries_on_transient_eacces(tmp_path, monkeypatch, caplog):
    """h) 前 2 次 EACCES、第 3 次成功：能进入临界区，且重试次数留痕。

    这条证明"瞬时竞争被吸收"——若把 _open_lock_handle 退回裸 open，
    calls 会停在 1 且直接抛错，本用例按预期变红。
    """
    path = tmp_path / "lock.lock"
    calls = _patch_lock_open(
        monkeypatch, path,
        lambda n: PermissionError(13, "Permission denied", str(path))
        if n <= 2 else None)

    with caplog.at_level(logging.WARNING, logger=base_agent.LOG.name):
        with file_lock(path):
            pass

    assert calls["n"] == 3                      # 精确等于"失败两次后成功"
    assert any("重试 2 次后成功" in rec.getMessage()
               for rec in caplog.records), caplog.text
    assert base_agent._HELD_LOCK_PATHS == {}


def test_lock_handle_raises_when_eacces_persists(tmp_path, monkeypatch, caplog):
    """i) EACCES 持续：用尽额度后抛原异常，绝不进临界区（不降级无锁）。

    这条是正确性闸门——若耗尽后返回无锁句柄/降级放行，entered 会变 True，
    本用例按预期变红。
    """
    path = tmp_path / "lock.lock"
    calls = _patch_lock_open(
        monkeypatch, path,
        lambda n: PermissionError(13, "Permission denied", str(path)))

    entered = False
    with caplog.at_level(logging.ERROR, logger=base_agent.LOG.name):
        with pytest.raises(PermissionError) as ei:
            with file_lock(path):
                entered = True

    assert entered is False, "EACCES 耗尽后不得进入临界区（等于无锁放行）"
    assert calls["n"] == base_agent.LOCK_OPEN_RETRIES
    assert ei.value.errno == 13                  # 原异常原样上抛，不包装成 Unknown
    assert base_agent._HELD_LOCK_PATHS == {}     # 失败路径也清登记，不留假持锁
    assert any("不降级为无锁" in rec.getMessage()
               for rec in caplog.records), caplog.text


def test_lock_handle_does_not_retry_other_oserror(tmp_path, monkeypatch):
    """j) 非 EACCES 的 OSError 立即上抛，只尝试 1 次。

    这条防"重试面扩大"——若把 except 写成 OSError，真故障（如路径非法）
    会被重试 5 次才报，故障定位被拖慢，本用例按预期变红。
    """
    path = tmp_path / "lock.lock"
    calls = _patch_lock_open(
        monkeypatch, path,
        lambda n: FileNotFoundError(2, "No such file or directory", str(path)))

    with pytest.raises(FileNotFoundError):
        with file_lock(path):
            pass

    assert calls["n"] == 1
    assert base_agent._HELD_LOCK_PATHS == {}
