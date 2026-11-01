# -*- coding: utf-8 -*-
"""S1-06 dw_snapshot 每日脚本测试：刷新成功路径 + 一致性校验失败路径 + 日志回执。

策略：不跑真 dashboard（约 27s 且会写仓库产物），用注入 runner + 临时目录
（tmp_path）模拟「dashboard 重生成 snapshot」；校验函数与日志落盘均全部走真实
代码路径。≥2 用例（实际 5 个）：成功刷新结构 / generated_at 非当日 /
catalog 版本漂移 / dashboard 失败 / lagging 上限与结构。
"""
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

import dw_snapshot_daily as daily  # noqa: E402


class _Proc:
    """subprocess.CompletedProcess 的最小替身。"""

    def __init__(self, rc=0, stdout="snapshot: x size=1\n", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _iso_now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _write_catalog(tmp: Path, version=18, tables=3):
    cat = {"version": version, "tables": {"t%d" % i: {"store": "x"} for i in range(tables)}}
    (tmp / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    return cat


def _snapshot(version=18, tables=3, generated_at=None, lagging=None):
    return {
        "generated_at": generated_at or _iso_now(),
        "catalog": {"version": version, "tables": tables},
        "lagging_daily": lagging if lagging is not None else [
            {"name": "daily", "label": "Tushare·日线", "lag": 3}],
    }


def _runner(tmp: Path, snap, rc=0, write=True, calls=None):
    """假 dashboard：把 snap 写到 tmp；返回 _Proc。"""
    def _run(python):
        if write:
            (tmp / "dw_snapshot.json").write_text(
                json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        if calls is not None:
            calls.append(python)
        return _Proc(rc=rc, stdout="snapshot: fake", stderr="")
    return _run


def _run(tmp: Path, snap, rc=0, write=True, max_lagging=None, calls=None):
    return daily.run(
        runner=_runner(tmp, snap, rc=rc, write=write, calls=calls),
        snapshot_path=tmp / "dw_snapshot.json",
        catalog_path=tmp / "catalog.json",
        log_path=tmp / "dw_snapshot_daily.log",
        max_lagging=max_lagging,
    )


def _failed(rep):
    return {c["check"] for c in rep["checks"] if not c["ok"]}


def _last_log(tmp: Path):
    lines = (tmp / "dw_snapshot_daily.log").read_text(encoding="utf-8").strip().splitlines()
    assert lines, "日志为空"
    return json.loads(lines[-1])


def test_run_success_refreshes_and_validates(tmp_path):
    """成功路径：runner 刷新 snapshot → 校验全过 → rc=0 且日志落盘。"""
    _write_catalog(tmp_path)
    snap = _snapshot()
    calls = []
    rep = _run(tmp_path, snap, calls=calls)

    assert rep["rc"] == 0 and rep["ok"] is True, rep
    assert calls, "假 runner 未被调用（注入失败）"
    assert _failed(rep) == set(), rep["checks"]
    assert rep["generated_at"] == snap["generated_at"]
    assert rep["catalog_version"] == 18
    assert rep["lagging_count"] == 1
    assert (tmp_path / "dw_snapshot.json").is_file()

    rec = _last_log(tmp_path)
    assert rec["rc"] == 0 and rec["ok"] is True
    assert rec["generated_at"] == snap["generated_at"]
    assert rec["catalog_version"] == 18
    assert {"dashboard_rc", "snapshot_parse", "generated_at_today",
            "catalog_version", "lagging_structure"} <= {
               c["check"] for c in rec["checks"]}
    # log_written 是「写日志之后」才追加的检查项，故只存在于内存报告，不回写日志
    assert any(c["check"] == "log_written" and c["ok"] for c in rep["checks"])


def test_run_fails_when_generated_at_not_today(tmp_path):
    """失败路径①：generated_at 非当日（旧快照）→ rc=1，日志如实记 FAIL。"""
    _write_catalog(tmp_path)
    yesterday = (dt.datetime.now().astimezone() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    rep = _run(tmp_path, _snapshot(generated_at=yesterday))

    assert rep["rc"] == 1 and rep["ok"] is False
    assert "generated_at_today" in _failed(rep), _failed(rep)
    rec = _last_log(tmp_path)
    assert rec["rc"] == 1
    assert any(c["check"] == "generated_at_today" and not c["ok"] for c in rec["checks"])


def test_run_fails_on_catalog_version_mismatch(tmp_path):
    """失败路径②：snapshot 的 catalog 版本与当前 catalog.json 不一致 → rc=1。"""
    _write_catalog(tmp_path, version=18)
    rep = _run(tmp_path, _snapshot(version=17))

    assert rep["rc"] == 1
    assert "catalog_version" in _failed(rep), _failed(rep)


def test_run_fails_when_dashboard_fails(tmp_path):
    """失败路径③：dashboard 退出码非 0（且未刷新文件）→ rc=1，日志仍写落。"""
    _write_catalog(tmp_path)
    (tmp_path / "dw_snapshot.json").write_text(
        json.dumps(_snapshot(), ensure_ascii=False), encoding="utf-8")
    rep = _run(tmp_path, _snapshot(), rc=1, write=False)

    assert rep["rc"] == 1
    assert rep["dashboard_rc"] == 1
    assert "dashboard_rc" in _failed(rep), _failed(rep)
    rec = _last_log(tmp_path)
    assert rec["rc"] == 1 and rec["dashboard_rc"] == 1


def test_lagging_count_limit_and_structure(tmp_path):
    """lagging 校验：未设上限只记录；设上限超限失败；结构缺损（缺 lag）失败。"""
    _write_catalog(tmp_path)
    lagging = [{"name": "a", "lag": 3}, {"name": "b", "lag": 4}, {"name": "c", "lag": 5}]
    snap = _snapshot(lagging=lagging)

    ok = _run(tmp_path, snap, max_lagging=5)
    assert ok["rc"] == 0 and ok["lagging_count"] == 3, ok

    over = _run(tmp_path, snap, max_lagging=2)
    assert over["rc"] == 1
    assert "lagging_count" in _failed(over), _failed(over)

    broken = _run(tmp_path, _snapshot(lagging=[{"name": "bad"}]))
    assert broken["rc"] == 1
    assert "lagging_structure" in _failed(broken), _failed(broken)
