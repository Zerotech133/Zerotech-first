# -*- coding: utf-8 -*-
"""订阅者分页离线单测（飞书中台 P3.9-lite · N-A 工位 2026-10-20）。

问题：``list_subscribers`` 单页钳 200 且无 offset，订阅者超 200 时客户面
列表与到期提醒都会静默漏人。本文件钉住三件事：
  1. 新增 offset 不改变旧调用的任何字段与语义（纯追加）；
  2. offset / has_more 的分页口径（含正好取满、超界、非法值归一）；
  3. ``iter_all_subscribers`` 的截断与异常都必须显式上报，绝不静默停下。

沿 test_subscription_store.py 判例：env SUBSCRIPTION_DB 锚到 tmp 临时文件，
零碰生产库（portal/work/subscription.sqlite3 不动）。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subscription_store as ss

TOTAL = 250
ROW_KEYS = {"open_id", "display_name", "plan_id", "status",
            "start", "end", "daily_quota", "used_today"}


def oid(i):
    return "ou_p%03d" % i


@pytest.fixture
def db(tmp_path, monkeypatch):
    """250 个订阅者（1 个有生效订阅，其余无订阅行）——超单页 200。"""
    p = tmp_path / "sub_paging.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    ss._connect(create=True).close()
    for i in range(TOTAL):
        ss.upsert_subscriber(oid(i), "分页%03d" % i)
    ss.grant_subscription(oid(0), "basic_monthly", 30)
    yield p


# ---------------------------------------------------------------------------
# 1. 旧行为不变（纯追加）
# ---------------------------------------------------------------------------

def test_01_default_keeps_legacy_fields_and_rows(db):
    """默认调用：既有的 8 个字段逐字段不变，只在末尾追加 offset/has_more。"""
    r = ss.list_subscribers()
    assert r["ok"] is True
    assert r["count"] == 50 == len(r["subscribers"])
    assert r["offset"] == 0
    assert r["has_more"] is True
    assert [s["open_id"] for s in r["subscribers"]] == \
        [oid(i) for i in range(50)]  # ORDER BY open_id 稳定序不变
    for i, row in enumerate(r["subscribers"]):
        assert set(row) == ROW_KEYS  # 行内字段零增零减
        assert row["open_id"] == oid(i)
        assert row["display_name"] == "分页%03d" % i
        assert row["used_today"] == 0
        if i == 0:
            assert row["plan_id"] == "basic_monthly"
            assert row["status"] == "active"
            assert row["daily_quota"] == 20
            assert len(row["start"]) == 19 and len(row["end"]) == 19
        else:  # 无订阅行 → 订阅字段诚实空（旧语义）
            assert row["plan_id"] is None and row["status"] is None
            assert row["start"] is None and row["end"] is None
            assert row["daily_quota"] is None


# ---------------------------------------------------------------------------
# 2. offset 取到尾部 / 超界
# ---------------------------------------------------------------------------

def test_02_offset_200_returns_remaining_50(db):
    """offset=200 取到剩余 50 条，尾部 has_more=False。"""
    r = ss.list_subscribers(limit=200, offset=200)
    assert r["ok"] is True and r["offset"] == 200
    assert r["count"] == 50 == len(r["subscribers"])
    assert [s["open_id"] for s in r["subscribers"]] == \
        [oid(i) for i in range(200, TOTAL)]
    assert r["has_more"] is False


def test_03_offset_beyond_end_is_empty_not_error(db):
    """offset 超界 → 空列表但 ok=True（空 ≠ 错误），has_more=False。"""
    for off in (TOTAL, 10 ** 6):
        r = ss.list_subscribers(limit=200, offset=off)
        assert r["ok"] is True and "error" not in r
        assert r["count"] == 0 and r["subscribers"] == []
        assert r["offset"] == off and r["has_more"] is False


# ---------------------------------------------------------------------------
# 3. has_more 口径（宁松不紧）
# ---------------------------------------------------------------------------

def test_04_has_more_true_on_exactly_full_page(db):
    """正好取满一页即 has_more=True（无 total 计数，不假装精确）。"""
    r = ss.list_subscribers(limit=200, offset=50)  # 50..249 恰好 200 行
    assert r["count"] == 200 and r["has_more"] is True
    r = ss.list_subscribers(limit=50, offset=200)  # 恰好最后 50 行
    assert r["count"] == 50 and r["has_more"] is True  # 满页即真，宁松不紧
    r = ss.list_subscribers(limit=51, offset=200)  # 只够 50 行 → 未满页
    assert r["count"] == 50 and r["has_more"] is False


# ---------------------------------------------------------------------------
# 4. 非法 offset 归一到 0，不抛
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [-5, "abc", None, 3.7, "50"])
def test_05_bad_offset_normalized_to_zero(db, bad):
    """负 / 非数字串 / None / 浮点 / 数字字符串 → 一律当 0，不抛异常。"""
    r = ss.list_subscribers(limit=5, offset=bad)
    assert r["ok"] is True and r["offset"] == 0
    assert [s["open_id"] for s in r["subscribers"]] == [oid(i) for i in range(5)]


# ---------------------------------------------------------------------------
# 5. limit 钳制保持原样
# ---------------------------------------------------------------------------

def test_06_limit_clamp_still_1_to_200(db):
    """加大 offset 不放宽 limit：500→200、0/-5→1、非整数→50。"""
    r = ss.list_subscribers(limit=500, offset=0)
    assert r["count"] == 200 and r["has_more"] is True
    r = ss.list_subscribers(limit=500, offset=200)  # 钳后仍按 200 取尾 50
    assert r["count"] == 50 and r["has_more"] is False
    assert ss.list_subscribers(limit=0)["count"] == 1
    assert ss.list_subscribers(limit=-5)["count"] == 1
    assert ss.list_subscribers(limit="abc")["count"] == 50


# ---------------------------------------------------------------------------
# 6. iter_all_subscribers：全量 / 截断 / 异常
# ---------------------------------------------------------------------------

def test_07_iter_all_collects_all_250(db):
    """默认参数取全 250 条：2 页、无重复无遗漏、complete=True。"""
    res = ss.iter_all_subscribers()
    assert res["ok"] is True and res["error"] is None
    assert res["count"] == TOTAL == len(res["subscribers"])
    ids = [r["open_id"] for r in res["subscribers"]]
    assert ids == [oid(i) for i in range(TOTAL)]
    assert len(set(ids)) == TOTAL
    assert res["page_size"] == 200 and res["pages"] == 2
    assert res["truncated"] is False and res["complete"] is True
    assert res["problems"] == []


def test_08_iter_truncated_when_max_pages_exhausted(db):
    """max_pages=1 → 只拿 200 条但 truncated=True（钉住不静默停下）。"""
    res = ss.iter_all_subscribers(max_pages=1)
    assert res["ok"] is True
    assert res["count"] == 200 and res["pages"] == 1
    assert res["truncated"] is True and res["complete"] is False
    assert res["subscribers"][-1]["open_id"] == oid(199)


def test_09_iter_store_exception_keeps_partial(db, monkeypatch):
    """第 2 页抛异常 → 保留第 1 页 200 条，ok=False，truncated 不得为 True。"""
    real = ss.list_subscribers

    def boom(limit=50, now=None, offset=0):
        if offset:
            raise RuntimeError("磁盘读取失败")
        return real(limit=limit, now=now, offset=offset)

    monkeypatch.setattr(ss, "list_subscribers", boom)
    res = ss.iter_all_subscribers()
    assert res["ok"] is False and res["error"] == "store_exception"
    assert res["count"] == 200 and res["pages"] == 1
    assert res["truncated"] is False and res["complete"] is False
    assert res["problems"][0]["page"] == 2
    assert "磁盘读取失败" in res["problems"][0]["detail"]


def test_10_iter_store_unavailable_is_error_not_end(db, monkeypatch):
    """ok=False 的页 → 当错误中止，不当「没有更多」。"""
    real = ss.list_subscribers

    def page2_down(limit=50, now=None, offset=0):
        if offset:
            return {"ok": False, "error": "store_unavailable"}
        return real(limit=limit, now=now, offset=offset)

    monkeypatch.setattr(ss, "list_subscribers", page2_down)
    res = ss.iter_all_subscribers()
    assert res["ok"] is False and res["error"] == "store_unavailable"
    assert res["count"] == 200 and res["complete"] is False
    assert res["truncated"] is False


def test_11_iter_missing_store_reports_incomplete(tmp_path, monkeypatch):
    """store 文件缺失 → ok=False/store_unavailable，count=0，complete=False。"""
    monkeypatch.setenv("SUBSCRIPTION_DB", str(tmp_path / "never.sqlite3"))
    res = ss.iter_all_subscribers()
    assert res["ok"] is False and res["error"] == "store_unavailable"
    assert res["count"] == 0 and res["pages"] == 0
    assert res["subscribers"] == []
    assert res["truncated"] is False and res["complete"] is False


# ---------------------------------------------------------------------------
# 7. 分页拼接 == 全集；参数钳制
# ---------------------------------------------------------------------------

def test_12_pages_partition_equals_full_set(db):
    """逐页（limit=50）拼起来 = iter 全集：无重复、无遗漏、顺序一致。"""
    collected = []
    offset = 0
    while True:
        page = ss.list_subscribers(limit=50, offset=offset)
        collected.extend(s["open_id"] for s in page["subscribers"])
        if not page["has_more"] or not page["subscribers"]:
            break
        offset += 50
    expected = [oid(i) for i in range(TOTAL)]
    assert collected == expected  # 顺序 + 条数即覆盖全量
    assert len(set(collected)) == TOTAL  # 无重复
    assert [r["open_id"] for r in ss.iter_all_subscribers()["subscribers"]] \
        == expected


def test_13_iter_page_size_and_max_pages_clamped(db):
    """page_size 钳 [1,200]，max_pages 非法回落 50、0 钳到 1。"""
    res = ss.iter_all_subscribers(page_size=500)
    assert res["page_size"] == 200 and res["pages"] == 2
    assert res["truncated"] is False
    res = ss.iter_all_subscribers(max_pages="abc")
    assert res["max_pages"] == 50 and res["truncated"] is False
    res = ss.iter_all_subscribers(max_pages=0)
    assert res["max_pages"] == 1 and res["truncated"] is True
