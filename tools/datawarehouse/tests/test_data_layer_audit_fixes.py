# -*- coding: utf-8 -*-
"""数据层审计修复回归测试（2026-11-02 task-31）。

覆盖 3 个高置信修复：
1. stats_v2 合并口径 all.registered/exposed 按行标记求和（不再把 available 行一律当已登记/已暴露）；
2. CLI `sample` 未知 dataset_id → fail-visible rc=2 JSON（不再抛 KeyError traceback）；
3. registry.list(limit<0) 夹取为 0（消除 Python 负切片截尾的静默行为）。
测试全部 mock 连接/游标或临时 inventory，不连真实数据库、不写数据。
"""
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset_registry as dr  # noqa: E402
import dataset_registry_cli as cli  # noqa: E402


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursors):
        self._cursors = list(cursors)

    def cursor(self):
        return self._cursors.pop(0)

    def close(self):
        pass


def _inventory(tmp_path: Path) -> Path:
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "学习派生", "label": "t.db.t1", "kind": "sqlite_table",
         "path": "testdb.db", "registered": True, "exposed": True,
         "suggested_id": "t1_table"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return inv


def _discovery_reg(tmp_path: Path, names, pg_tables):
    disc = _FakeCursor(rows=[(n,) for n in names])
    meta = _FakeCursor(rows=[(n, "r", 1) for n in names])
    conn = _FakeConn([disc, meta])

    def connect(dsn):
        return conn

    return dr.DatasetRegistry(
        _inventory(tmp_path), allowed_roots=[tmp_path],
        pg_dsn="postgresql://fake/zhice_os_v2", pg_connect=connect,
        pg_tables=pg_tables, temp_whitelist=[],
    )


def test_stats_v2_all_flags_counted_from_row_flags(tmp_path):
    """修复 1：all.registered/exposed 只按行标记求和，不再因 available 而虚增。"""
    # 动态发现 3 张：仅 v2_a 在 wave1 声明中 exposed=True；其余为动态发现（exposed=False）
    pg_tables = ({"table": "v2_a", "owner": "ACAD1", "registered": True, "exposed": True},)
    reg = _discovery_reg(tmp_path, ["v2_a", "v2_b", "v2_c"], pg_tables)
    assert reg.connector_status == "ok"
    v1 = reg.stats()
    sv2 = reg.stats_v2()
    assert v1["exposed"] == 1
    assert sv2["pg"]["total"] == 3 and sv2["pg"]["available"] == 3
    assert sv2["pg"]["exposed"] == 1          # 声明口径：仅 v2_a
    assert sv2["all"]["total"] == v1["total"] + 3
    # 修复前：all.exposed = v1.exposed + available(3) = 4；修复后只 + 已暴露的 1
    assert sv2["all"]["exposed"] == v1["exposed"] + 1
    assert sv2["all"]["registered"] == v1["registered"] + 3  # 动态发现行均 registered=True


def test_cli_sample_unknown_dataset_is_fail_visible(capsys, monkeypatch):
    """修复 2：CLI sample 未知 dataset_id → rc=2 + JSON fail_visible（不抛 traceback）。"""

    class _MissingRegistry:
        def sample(self, dataset_id, n=3):
            raise KeyError("未知 dataset_id: %s" % dataset_id)

    monkeypatch.setattr(cli, "DatasetRegistry", lambda: _MissingRegistry())
    rc = cli.main(["--json", "sample", "--id", "no_such_dataset"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["fail_visible"] is True
    assert payload["state"] == "not_found"
    assert payload["dataset_id"] == "no_such_dataset"


def test_list_negative_limit_is_clamped_not_negative_slice(tmp_path):
    """修复 3：limit<0 必须返回空（修复前 Python 负切片会静默截掉尾部元素）。"""
    inv = tmp_path / "inv2.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "d", "label": "a", "kind": "dir", "path": "a",
         "registered": True, "exposed": True, "suggested_id": "a"},
        {"domain": "d", "label": "b", "kind": "dir", "path": "b",
         "registered": True, "exposed": False, "suggested_id": "b"},
    ]}, ensure_ascii=False), encoding="utf-8")
    reg = dr.DatasetRegistry(inv, allowed_roots=[tmp_path], pg_dsn="", temp_whitelist=[])
    assert reg.list(limit=-1) == []          # 修复前：[: -1] 会返回第 1 行（静默丢第 2 行）
    assert reg.list(limit=0) == []
    assert [d["dataset_id"] for d in reg.list(limit=1)] == ["a"]
    assert len(reg.list(limit=50)) == 2
