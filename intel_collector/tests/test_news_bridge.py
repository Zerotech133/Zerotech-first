# -*- coding: utf-8 -*-
"""P2 通讯线数据桥测试：news.db rowid 游标增量 / 批级摘要 / 只读性质。

夹具：tmp_path 构造最小 news.db（真实 schema DDL），验证：
  1) 首采批级 Observation（COMPLETE、new 计数、来源分布、时间跨度、用途绑定）；
  2) 游标续读：二次采集无新增 n=0（EMPTY），追加后三采只读增量；
  3) 空库/缺库→EMPTY；
  4) 只读性质：桥运行后 news.db 字节不变；
  5) 拒未知源；
  6) SLA 覆盖只在内存生效（sources.json 字节不变、spec.sla 不被变异）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from bridges.news_bridge import NewsBridge, DISPATCH_KEY
from core.contracts import Status
from registry.source_registry import SourceRegistry, SourceSpec
from store.obs_store import ObservationStore

NOW = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)

NEWS_DDL = """
CREATE TABLE news (
      id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, content TEXT,
      hit INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0, digested INTEGER DEFAULT 0,
      created_at TEXT, stocks TEXT, jev TEXT, model TEXT, judged_at TEXT,
      attempt INTEGER NOT NULL DEFAULT 0, last_error TEXT)
"""


def _make_reg(tmp_path):
    reg_path = tmp_path / "sources.json"
    import dataclasses
    specs = {
        "news": SourceSpec(source_id="news", name="新闻聚合",
                           category="open_source_intel",
                           owner="intel(军情司·INTEL0)",
                           rights={"internal_retention": True, "cleaning": True,
                                   "controlled_query": True},
                           content_types=["post"],
                           sla={"freshness_minutes": 30, "max_failures": 5})
    }
    reg_path.write_text(json.dumps(
        {sid: dataclasses.asdict(s) for sid, s in specs.items()}, ensure_ascii=False),
        encoding="utf-8")
    return SourceRegistry(str(reg_path))


def _make_db(tmp_path, rows=()):
    db = tmp_path / "news_collector" / "news.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(NEWS_DDL)
    con.executemany(
        "INSERT INTO news(id, source, ts, title, created_at) VALUES(?,?,?,?,?)",
        rows)
    con.commit()
    con.close()
    return db


def _r(i, source="sina", ts="2026-11-03 09:00", created="2026-11-03 09:05"):
    return (f"n{i}", source, ts, f"标题{i}", created)


class TestNewsBridge:
    def test_first_batch_summary(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [
            _r(1, "sina", "2026-11-03 08:00", "2026-11-03 08:05"),
            _r(2, "sina", "2026-11-03 09:00", "2026-11-03 09:05"),
            _r(3, "eastmoney", "2026-11-03 10:00", "2026-11-03 10:05"),
        ])
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors")
        res = b.collect_source(DISPATCH_KEY, NOW)
        assert res["ok"] and res["n"] == 3 and res["snapshot_id"].startswith("news:")
        no = [o for o in store.all() if o.source_id == "news"]
        assert len(no) == 1
        o = no[0]
        assert o.status == Status.COMPLETE
        assert o.entity_type == "news_item"
        assert o.entity_id.startswith("batch:")
        assert o.source_field == "title"
        assert o.clock.collected_at
        p = o.payload
        assert p["new"] == 3
        assert p["source_distribution"] == {"sina": 2, "eastmoney": 1}
        # payload 经 JSON 落盘回读，tuple 序列化为 list
        assert p["time_span"]["ts"] == ["2026-11-03 08:00", "2026-11-03 10:00"]
        assert p["time_span"]["created_at"] == ["2026-11-03 08:05", "2026-11-03 10:05"]
        assert p["cursor_rowid_before"] == 0 and p["cursor_rowid_after"] == 3
        assert "internal_retention" in o.allowed_use_cases
        assert "cleaning" in o.allowed_use_cases
        assert "controlled_query" in o.allowed_use_cases

    def test_cursor_incremental_no_reread(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [_r(i) for i in range(1, 4)])
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors")
        r1 = b.collect_source(DISPATCH_KEY, NOW)
        assert r1["n"] == 3
        r2 = b.collect_source(DISPATCH_KEY, NOW)  # 无新增 → EMPTY、游标不动
        assert r2["ok"] and r2["n"] == 0
        empty_obs = [o for o in store.all()
                     if o.source_id == "news" and o.status == Status.EMPTY]
        assert len(empty_obs) == 1
        # 追加 2 条（rowid 续位）
        con = sqlite3.connect(db)
        con.executemany(
            "INSERT INTO news(id, source, ts, title, created_at) VALUES(?,?,?,?,?)",
            [_r(10), _r(11)])
        con.commit()
        con.close()
        r3 = b.collect_source(DISPATCH_KEY, NOW)
        assert r3["n"] == 2  # 只读增量
        last = [o for o in store.all() if o.source_id == "news"][-1]
        assert last.payload["cursor_rowid_before"] == 3
        assert last.payload["cursor_rowid_after"] == 5

    def test_max_rows_batching(self, tmp_path):
        """max_rows 限批：一次只取 2 行，分两批读完，计数不丢不重。"""
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [_r(i) for i in range(1, 5)])
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors",
                       max_rows=2)
        assert b.collect_source(DISPATCH_KEY, NOW)["n"] == 2
        assert b.collect_source(DISPATCH_KEY, NOW)["n"] == 2
        assert b.collect_source(DISPATCH_KEY, NOW)["n"] == 0  # 读完

    def test_empty_and_missing_db(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path)  # 空表
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors")
        res = b.collect_source(DISPATCH_KEY, NOW)
        assert res["ok"] and res["n"] == 0
        assert [o for o in store.all()
                if o.source_id == "news"][0].status == Status.EMPTY
        # 缺库同样 EMPTY（不崩溃）
        b2 = NewsBridge(reg, store, db_path=tmp_path / "nope" / "news.db",
                        cursor_dir=tmp_path / "cursors2")
        res2 = b2.collect_source(DISPATCH_KEY, NOW)
        assert res2["ok"] and res2["n"] == 0

    def test_readonly_bytes_unchanged(self, tmp_path):
        """只读性质：桥运行前后 news.db 字节不变。"""
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [_r(i) for i in range(1, 4)])
        before = db.read_bytes()
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors")
        b.collect_source(DISPATCH_KEY, NOW)
        assert db.read_bytes() == before

    def test_rejects_unknown_source(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        b = NewsBridge(reg, store, db_path=_make_db(tmp_path),
                       cursor_dir=tmp_path / "cursors")
        res = b.collect_source("xueqiu", NOW)
        assert res["ok"] is False and "news_db" in res["reason"]

    def test_sla_override_memory_only(self, tmp_path):
        """SLA 覆盖只读生效：spec.sla 不变异、sources.json 字节不变。"""
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [_r(1)])
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors",
                       sla_freshness_minutes=1440)
        assert b.effective_freshness_minutes == 1440
        res = b.collect_source(DISPATCH_KEY, NOW)
        assert res["ok"]
        o = [x for x in store.all() if x.source_id == "news"][0]
        assert o.payload["sla_freshness_minutes"] == 1440
        assert o.payload["sla_overridden"] is True
        # 内存 spec.sla 不变异；update_health 合法重写文件后，
        # 持久化的 sla.freshness_minutes 仍是 30（覆盖绝不落 sources.json）
        spec = reg.get("news")
        assert spec.sla["freshness_minutes"] == 30
        persisted = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
        assert persisted["news"]["sla"]["freshness_minutes"] == 30

    def test_no_sla_override_uses_spec(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        db = _make_db(tmp_path, [_r(1)])
        b = NewsBridge(reg, store, db_path=db, cursor_dir=tmp_path / "cursors")
        assert b.effective_freshness_minutes == 30
        b.collect_source(DISPATCH_KEY, NOW)
        o = [x for x in store.all() if x.source_id == "news"][0]
        assert o.payload["sla_freshness_minutes"] == 30
        assert o.payload["sla_overridden"] is False
