# -*- coding: utf-8 -*-
"""JEV-LOGROT-20261105：日志轮转「句柄占用」三级降级回归。

故障现场
--------
``start_jev_stack.ps1`` 用 ``Start-Process -RedirectStandardError/-Output``
拉起常驻服务，``.err/.out`` 句柄被子进程持有到进程退出 → ``rotate_logs.py``
的 rename 必然 ``WinError 32`` → 旧版记硬失败 rc=1 → ``JevDriftDaily`` 的 P5
每天一条假失败（11-04 07:10 实例见 review_data/jev_drift_task.log）。

覆盖
----
1. 真子进程持句柄 → B 路 ``mode=copy_truncate``：源文件被截断、归档内容与原文一致；
2. 截断失败（monkeypatch 抛 PermissionError）→ C 路 deferred 落盘且 **rc=0**；
3. ``--dry-run`` 不改文件，但仍能预览 ``mode=copy_truncate``；
4. 下一轮运行开头先重试 deferred，成功后队列清空。

边界：不依赖真实服务，全部在 ``tmp_path`` 里造夹具；不触碰生产 logs。
"""
from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_rotate_logs():
    path = REPO / "tools" / "rotate_logs.py"
    spec = importlib.util.spec_from_file_location("rotate_logs_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


rl = _load_rotate_logs()

# 子进程：以追加模式持住句柄（等价服务的 RedirectStandardError 句柄），
# 就绪后写标记文件并等父进程发一行再退出。
_HOLDER_CODE = (
    "import sys\n"
    "fh = open(sys.argv[1], 'a', encoding='utf-8')\n"
    "fh.write('')\n"
    "fh.flush()\n"
    "open(sys.argv[2], 'w', encoding='utf-8').close()\n"
    "sys.stdin.readline()\n"
    "fh.close()\n"
)


def _make_root(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """造一个「假仓库根」并把安全边界收窄到 tmp_path（不碰真实工作区）。"""
    monkeypatch.setattr(rl, "WORKSPACE_ROOT", tmp_path)
    root = tmp_path / "repo"
    logs = root / "jev-eval" / "logs"
    logs.mkdir(parents=True)
    return root, logs


def _age(path: Path, days: float = 2.0) -> None:
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


@contextlib.contextmanager
def _holder(path: Path):
    """起真实子进程持句柄，yield 时父进程可安全做轮转。"""
    flag = path.with_name(path.name + ".holding")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", _HOLDER_CODE, str(path), str(flag)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for _ in range(200):  # 最多等 10s
            if flag.exists():
                break
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "replace")
                raise AssertionError("持句柄子进程提前退出：" + err)
            time.sleep(0.05)
        else:  # pragma: no cover - 环境异常
            raise AssertionError("持句柄子进程未就绪")
        yield
    finally:
        try:
            if proc.stdin:
                proc.stdin.write(b"\n")
                proc.stdin.flush()
        except OSError:  # pragma: no cover
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)
        try:
            flag.unlink()  # 沙箱下可能被劫持，失败不致命
        except OSError:  # pragma: no cover
            pass


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = rl.run(argv)
    return rc, buf.getvalue()


def _argv(root: Path, *extra: str) -> list[str]:
    return ["--root", str(root), "--target", "jev-eval/logs",
            "--active-guard-min", "0", "--keep-days", "14", *extra]


def _payload() -> str:
    return "".join(f"2026-11-04 07:10:0{i % 10} review line {i}\n" for i in range(200))


def test_occupied_falls_back_to_copy_truncate(tmp_path, monkeypatch):
    """A 失败（占用）→ B 成功：归档内容完整、源文件截断为 0。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.err"
    body = _payload()
    src.write_bytes(body.encode("utf-8"))
    _age(src)

    with _holder(src):
        rc, out = _run(_argv(root, "--execute", "--gzip"))

    assert rc == 0, out
    assert "mode=copy_truncate" in out
    assert src.stat().st_size == 0  # 原地截断成功

    archives = sorted(logs.glob("jev-proc.review.*.err.gz")) + \
        sorted(logs.glob("jev-proc.review.*.err"))
    assert len(archives) == 1, [p.name for p in logs.iterdir()]
    archive = archives[0]
    if archive.suffix == ".gz":
        text = gzip.decompress(archive.read_bytes()).decode("utf-8")
    else:  # pragma: no cover - --gzip 已显式传入
        text = archive.read_text(encoding="utf-8")
    assert text == body


def test_truncate_failure_goes_deferred_with_rc0(tmp_path, monkeypatch):
    """B 连截断都失败 → C 落 deferred，rc 必须仍是 0（不再让 P5 变红）。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.out"
    body = _payload()
    src.write_bytes(body.encode("utf-8"))
    _age(src)

    def _boom(_path):
        raise PermissionError(13, "拒绝访问（模拟只读/ACL 拒绝）")

    monkeypatch.setattr(rl, "truncate_in_place", _boom)

    with _holder(src):
        rc, out = _run(_argv(root, "--execute"))

    assert rc == 0, out
    assert "mode=deferred" in out
    assert src.read_bytes() == body.encode("utf-8")  # 截断失败 → 原文保留

    deferred_file = logs / "rotate_deferred.json"
    assert deferred_file.exists()
    payload = json.loads(deferred_file.read_text(encoding="utf-8"))
    assert payload["schema"] == "rotate_deferred/v1"
    assert payload["count"] == 1
    item = payload["items"][0]
    assert Path(item["path"]) == src
    assert item["reason"] == "truncate_failed"
    assert item["attempts"] == 1
    assert item["next_attempt"] == 2
    assert item["max_attempts"] == rl.DEFERRED_MAX_ATTEMPTS
    assert item["last_attempt"] and item["first_seen"]


def test_dry_run_previews_copy_truncate_without_touching_files(tmp_path, monkeypatch):
    """--dry-run：文件一字节不动，但输出里已能预览 mode=copy_truncate。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.err"
    body = _payload()
    src.write_bytes(body.encode("utf-8"))
    _age(src)
    before = src.stat().st_size

    with _holder(src):
        rc_json, out_json = _run(_argv(root, "--json"))
        rc_text, out_text = _run(_argv(root))

    assert rc_json == 0 and rc_text == 0
    assert src.stat().st_size == before
    assert src.read_bytes() == body.encode("utf-8")
    assert sorted(p.name for p in logs.iterdir()) == [src.name]  # 没生成归档
    assert not (logs / "rotate_deferred.json").exists()  # dry-run 不写 deferred

    planned = json.loads(out_json)
    rotate_rows = [a for a in planned["actions"] if a["action"] == "rotate"]
    assert rotate_rows and rotate_rows[0]["mode"] == "copy_truncate"
    assert "mode=copy_truncate" in out_text


def test_deferred_retried_at_start_of_next_run(tmp_path, monkeypatch):
    """上一轮 deferred → 下一轮开头先重试；成功后队列清空、归档仍在。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.err"
    body = _payload()
    src.write_bytes(body.encode("utf-8"))
    _age(src)

    state = {"fail": True}
    original_truncate = rl.truncate_in_place

    def _flaky(path):
        if state["fail"]:
            raise PermissionError(13, "拒绝访问")
        return original_truncate(path)

    monkeypatch.setattr(rl, "truncate_in_place", _flaky)

    with _holder(src):
        rc, out = _run(_argv(root, "--execute"))
    assert rc == 0, out
    assert json.loads((logs / "rotate_deferred.json").read_text(encoding="utf-8"))["count"] == 1

    # 服务重启/退出后句柄释放 → 下一轮开头应先重试，成功后队列归零
    state["fail"] = False
    rc2, out2 = _run(_argv(root, "--execute"))

    assert rc2 == 0, out2
    assert "mode=" in out2
    payload = json.loads((logs / "rotate_deferred.json").read_text(encoding="utf-8"))
    assert payload["count"] == 0
    # rename 成功时源文件已被移走；copy_truncate 成功时源文件为 0 字节
    assert (not src.exists()) or src.stat().st_size == 0

    archives = [p for p in logs.iterdir() if p.name.startswith("jev-proc.review.")]
    assert archives, [p.name for p in logs.iterdir()]
    texts = []
    for p in archives:
        if p.suffix == ".gz":
            texts.append(gzip.decompress(p.read_bytes()).decode("utf-8", "replace"))
        else:
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
    assert any("review line 0" in t for t in texts)


def test_deferred_retry_limit_moves_to_dead(tmp_path, monkeypatch):
    """同一文件累计失败满 3 次 → 转 dead 文件、WARN 一次、不再无限重试。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.err"
    src.write_bytes(_payload().encode("utf-8"))
    _age(src)

    deferred_file = logs / "rotate_deferred.json"
    deferred_file.write_text(json.dumps({
        "schema": "rotate_deferred/v1",
        "max_attempts": rl.DEFERRED_MAX_ATTEMPTS,
        "items": [{
            "path": str(src), "rel": "jev-eval/logs/jev-proc.review.err",
            "reason": "truncate_failed", "detail": "前两轮失败",
            "attempts": 2, "next_attempt": 3,
            "max_attempts": rl.DEFERRED_MAX_ATTEMPTS,
        }],
    }, ensure_ascii=False), encoding="utf-8")

    def _boom(_path):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(rl, "truncate_in_place", _boom)
    with _holder(src):
        rc, out = _run(_argv(root, "--execute"))

    assert rc == 0, out
    assert "[WARN]" in out and "转入" in out
    assert json.loads(deferred_file.read_text(encoding="utf-8"))["count"] == 0

    dead_file = logs / "rotate_deferred_dead.json"
    assert dead_file.exists()
    dead = json.loads(dead_file.read_text(encoding="utf-8"))
    assert dead["count"] == 1
    assert dead["items"][0]["attempts"] == 3
    assert dead["items"][0]["result"] == "dead"


def test_retried_path_not_copied_twice_in_same_run(tmp_path, monkeypatch):
    """本轮已重试仍占用：扫描阶段不得再复制一遍（避免同日两份归档）。"""
    root, logs = _make_root(tmp_path, monkeypatch)
    src = logs / "jev-proc.review.out"
    src.write_bytes(_payload().encode("utf-8"))
    _age(src)

    deferred_file = logs / "rotate_deferred.json"
    deferred_file.write_text(json.dumps({
        "schema": "rotate_deferred/v1",
        "items": [{"path": str(src), "rel": "jev-eval/logs/jev-proc.review.out",
                   "reason": "truncate_failed", "attempts": 1, "next_attempt": 2}],
    }, ensure_ascii=False), encoding="utf-8")

    def _boom(_path):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(rl, "truncate_in_place", _boom)
    with _holder(src):
        rc, out = _run(_argv(root, "--execute"))

    assert rc == 0, out
    payload = json.loads(deferred_file.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["items"][0]["attempts"] == 2
    assert payload["items"][0]["next_attempt"] == 3
    archives = [p for p in logs.iterdir()
                if p.name.startswith("jev-proc.review.") and p != src]
    assert len(archives) == 1, [p.name for p in logs.iterdir()]


@pytest.mark.parametrize("winerror,errno_value,expected", [
    (32, None, True),
    (33, None, True),
    (None, 13, True),
    (None, 5, False),
])
def test_is_lock_error_matrix(winerror, errno_value, expected):
    """占用判定矩阵：WinError 32/33 与 EACCES 才算「可降级」，其余仍走失败。"""
    exc = OSError()
    if winerror is not None:
        exc.winerror = winerror
    if errno_value is not None:
        exc.errno = errno_value
    assert rl.is_lock_error(exc) is expected
