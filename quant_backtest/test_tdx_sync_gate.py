# -*- coding: utf-8 -*-
"""A3 治本闸门单测（20261020 ws-a3g，工位 A3 第一步）。

覆盖 sync_parquet_table 盖 done 前的后置校验闸门与表级 fail 聚合链：
- 全量空页假 done 被拦 / 全量正常完成 / 增量零新行不误杀 / 实拉 vs 广告差异超阈
- 远端广告 rows>0 实拉 0 行判失败 / 失败不盖 done 且 manifest 不变
- 空页重探一次后成功 / 表级聚合非零退出 / 零分片落盘判失败 / total<=0 跳过比对

约定：全部 mock 远端（monkeypatch tdx_sync._get），OUT/MANIFEST 注入 tmp_path，
禁连真实远端、禁写真实 data/tdx/。
"""
import copy
from collections import deque

import pytest

import tdx_sync as ts

COLS = ["trade_date", "close"]


def _rows(n, day=1):
    """n 行两列假数据，日期从 2026-01-DD 递增（保证增量去重过滤可控）。"""
    out = []
    for i in range(n):
        d = day + i // 28
        out.append(["2026-01-%02d" % max(d, 1), 1.0 + i])
    return out


class FakeRemote:
    """按 offset 排队的假 _get：队列耗尽或无队列时返回空页（模拟对端真空）。"""

    def __init__(self):
        self.queues = {}
        self.calls = []

    def set(self, offset, *responses):
        self.queues.setdefault(offset, deque(responses))

    def __call__(self, path, params=None, timeout=60):
        p = dict(params or {})
        self.calls.append((path, p))
        q = self.queues.get(p.get("offset", 0))
        if not q:
            return {"rows": [], "columns": [], "total": 0}
        return q.popleft()


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = tmp_path / "tdx"
    out.mkdir()
    monkeypatch.setattr(ts, "OUT", str(out))
    monkeypatch.setattr(ts, "MANIFEST", str(out / "_manifest.json"))
    monkeypatch.setattr(ts, "SLEEP", 0)
    monkeypatch.setattr(ts, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(ts, "_date_col_of", lambda tbl: "trade_date")
    monkeypatch.setattr(ts, "_parquet_latest_date", lambda tbl: None)
    fake = FakeRemote()
    monkeypatch.setattr(ts, "_get", fake)
    return fake, out


def _run(tbl, total, m):
    ts.sync_parquet_table(tbl, {"rows": total}, m)


# 1. 全量空页假 done 被拦：远端广告 1000 行、实拉 0 行 → raise，不盖 done
def test_full_empty_page_blocked(env):
    fake, out = env
    fake.set(0, {"rows": [], "columns": [], "total": 1000})
    m = {}
    with pytest.raises(RuntimeError, match="假完成拦截"):
        _run("t1", 1000, m)
    assert m["t1"]["done"] is False
    assert m["t1"]["rows"] == 0


# 2. 全量正常完成：一次页拉满 1000 行 → done=True、分片落盘
def test_full_normal_complete(env):
    fake, out = env
    fake.set(0, {"rows": _rows(1000), "columns": COLS, "total": 1000})
    m = {}
    _run("t2", 1000, m)
    assert m["t2"]["done"] is True
    assert m["t2"]["rows"] == 1000
    assert (out / "t2").exists() and list((out / "t2").glob("part_*.parquet"))


# 3. 增量 0 新行正常完成：去重后空 break 不是失败，done 不得被误杀/回退
def test_incremental_zero_new_rows_ok(env, monkeypatch):
    fake, out = env
    monkeypatch.setattr(ts, "_parquet_latest_date", lambda tbl: "2026-01-01")
    fake.set(0, {"rows": _rows(5), "columns": COLS, "total": 5000})
    st = {"offset": 0, "part": 2, "rows": 5000, "done": True}
    m = {"t3": st}
    _run("t3", 5000, m)               # 全部行日期 <= latest，去重后零新行
    assert m["t3"]["done"] is True
    assert st["rows"] == 5000          # 本次实拉 0，累计不变
    assert st["offset"] == 0


# 4. 实拉与广告差异超阈：广告 100000、实拉 1000 后截断 → 完整性闸门拦下
def test_full_truncated_diff_blocked(env):
    fake, out = env
    fake.set(0, {"rows": _rows(1000), "columns": COLS, "total": 100000})
    m = {}
    with pytest.raises(RuntimeError, match="完整性闸门"):
        _run("t4", 100000, m)
    assert m["t4"]["done"] is False


# 5. 远端 rows>0 实拉 0 行判失败：不写 manifest 文件（无落盘记账）
def test_full_zero_pulled_no_manifest_write(env):
    fake, out = env
    fake.set(0, {"rows": [], "columns": [], "total": 500})
    m = {}
    with pytest.raises(RuntimeError, match="实拉 0 行"):
        _run("t5", 500, m)
    assert not (out / "_manifest.json").exists()


# 6. 失败不盖 done 且 manifest 记账不变：异常路径无副作用
def test_failure_manifest_unchanged(env):
    fake, out = env
    fake.set(0, {"rows": [], "columns": [], "total": 1000})
    m = {"t6": {"offset": 0, "part": 0, "rows": 0, "done": False}}
    before = copy.deepcopy(m)
    with pytest.raises(RuntimeError):
        _run("t6", 1000, m)
    assert m == before


# 7. 空页重探一次后成功：首次空页触发重探拿到数据 → 正常 done
def test_empty_page_reprobe_recovers(env):
    fake, out = env
    fake.set(0,
             {"rows": [], "columns": [], "total": 100},
             {"rows": _rows(100), "columns": COLS, "total": 100})
    m = {}
    _run("t7", 100, m)
    assert m["t7"]["done"] is True
    assert m["t7"]["rows"] == 100
    assert len(fake.calls) == 2        # 首拉 + 重探各一次


# 8. 表级 fail 聚合链不破坏：一好一坏 → _run_sync 返回 1（非零退出链）
def test_run_sync_fail_aggregation(env, monkeypatch):
    fake, out = env
    fake.set(0,
             {"rows": _rows(10), "columns": COLS, "total": 10},   # ok 表
             {"rows": [], "columns": [], "total": 1000})          # bad 表空页
    m = {}
    monkeypatch.setattr(ts, "_load_manifest", lambda: m)
    remote = {"bad": {"rows": 1000}, "ok": {"rows": 10}}
    a = type("A", (), {"tables": "", "include_rtmin": False})()
    rc = ts._run_sync(a, remote)
    assert rc == 1
    assert m["ok"]["done"] is True
    assert m["bad"]["done"] is False


# 9. total<=0（远端元数据异常如 -1）跳过比对：全量空页不误杀
def test_total_nonpositive_skips_gate(env):
    fake, out = env
    fake.set(0, {"rows": [], "columns": [], "total": -1})
    m = {}
    _run("t9", -1, m)
    assert m["t9"]["done"] is True


# 10. 零分片落盘判失败：实拉 1000 行但写盘被吞 → 闸门拦下
def test_full_zero_parts_blocked(env, monkeypatch):
    fake, out = env
    fake.set(0, {"rows": _rows(1000), "columns": COLS, "total": 1000})
    monkeypatch.setattr(ts.pd.DataFrame, "to_parquet",
                        lambda self, path, index=False: None)
    m = {}
    with pytest.raises(RuntimeError, match="零分片落盘"):
        _run("t10", 1000, m)
    assert m["t10"]["done"] is False


# 11. 增量有新行正常完成：不误杀且 offset 语义还原为 0
def test_incremental_new_rows_complete(env, monkeypatch):
    fake, out = env
    monkeypatch.setattr(ts, "_parquet_latest_date", lambda tbl: "2026-01-01")
    new = [["2026-01-02", 9.9], ["2026-01-03", 9.8]]
    fake.set(0, {"rows": new, "columns": COLS, "total": 5000})
    st = {"offset": 0, "part": 2, "rows": 5000, "done": True}
    m = {"t11": st}
    _run("t11", 5000, m)
    assert m["t11"]["done"] is True
    assert st["rows"] == 5002          # 实拉 2 行入账
    assert st["offset"] == 0


# 12. 差异在阈值缓冲内不拦：广告 1000、实拉 991（差 9 ≤ max(1%,1000)）→ 正常 done
def test_diff_within_threshold_ok(env):
    fake, out = env
    fake.set(0, {"rows": _rows(991), "columns": COLS, "total": 1000})
    m = {}
    _run("t12", 1000, m)
    assert m["t12"]["done"] is True
