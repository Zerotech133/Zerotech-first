# -*- coding: utf-8 -*-
"""blocked 存量治理（方案 A：T+7 过期归档 + 簇折叠投影）单测。

治理执行（2026-10-20，老田批准 FB-G 报告三案选一=A）落地验证：
1. push_flow ok=0 行近 7 天回看（FLOW_BLOCK_WINDOW_DAYS），超窗自动出队
   ——原始账永久保留，投影零写路径；
2. 投影层同 (kind, target) 失败簇折叠为簇代表（cluster_size），解决
   重复簇挤爆 200 截断墙的可见性问题；
3. meta.blocked_archive 透出归档语义（rule / raw_blocked / cluster_count），
   消费方可对账「折叠后条数 vs 原始总量」。

monkeypatch fixture 沿 test_api_notify.py 模式（env AIOS_ROOT → tmp）。
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_notify

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW_ISO = datetime.now().isoformat(timespec="seconds")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    (tmp_path / "portal" / "work").mkdir(parents=True)
    return tmp_path


def make_flow(root, rows):
    (root / "portal" / "work" / "push_flow.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def payload(root):
    return api_notify.notify_queue_payload()


def fail_row(date, kind, target, ts=None):
    return {
        "ts": ts or (date + "T08:00:00"),
        "date": date,
        "kind": kind,
        "channel": "openapi",
        "target": target,
        "count": 1,
        "ok": 0,
        "tenant": "default",
    }


def test_same_kind_target_clustered_with_size(root):
    """同 kind+target 三条失败 → 1 条簇代表，cluster_size=3，raw=3。"""
    make_flow(
        root,
        [
            fail_row(TODAY, "daily:report", "ou_a", ts=TODAY + "T08:00:00"),
            fail_row(TODAY, "daily:report", "ou_a", ts=TODAY + "T09:00:00"),
            fail_row(TODAY, "daily:report", "ou_a", ts=TODAY + "T10:00:00"),
        ],
    )
    p = payload(root)
    fb = [
        i for i in p["items"] if i["state"] == "blocked" and i["source"] == "push_flow"
    ]
    assert len(fb) == 1
    assert fb[0]["cluster_size"] == 3
    assert "失败簇共 3 条" in fb[0]["reason"]
    # 簇代表取最新一条（T10）
    assert fb[0]["created_at"].endswith("T10:00:00")
    assert p["states"]["blocked"] == 1
    ba = p["meta"]["blocked_archive"]
    assert ba["raw_blocked"] == 3 and ba["cluster_count"] == 1


def test_different_target_not_merged(root):
    """同 kind 不同 target 是不同投递事实，各成一簇。"""
    make_flow(
        root,
        [
            fail_row(TODAY, "daily:alert", "ou_a"),
            fail_row(TODAY, "daily:alert", "ou_b"),
        ],
    )
    p = payload(root)
    fb = [i for i in p["items"] if i["state"] == "blocked"]
    assert len(fb) == 2 and p["meta"]["blocked_archive"]["raw_blocked"] == 2


def test_overdue_rows_auto_archive_t_plus_7(root):
    """T+7 核心：8 天前失败行超窗自动出队，raw_blocked 不含它。"""
    old = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    make_flow(
        root,
        [
            fail_row(TODAY, "subscribe:quote_move", "个人"),
            fail_row(old, "subscribe:quote_move", "个人"),  # 超窗
        ],
    )
    p = payload(root)
    fb = [i for i in p["items"] if i["state"] == "blocked"]
    assert len(fb) == 1 and fb[0]["cluster_size"] == 1
    assert p["meta"]["blocked_archive"]["raw_blocked"] == 1
    assert all("投递失败" in i["title"] for i in fb)


def test_gate_deny_rows_idle_and_not_clustered(root):
    """闸门策略内抑制行归 idle，不进折叠（与投递失败分开治理口径）。"""
    make_flow(
        root,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "matrix:us|deny:silent window",
                "channel": "hub-gate",
                "target": "oc_us",
                "count": 1,
                "ok": 0,
            },
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "matrix:cn|deny:quota",
                "channel": "hub-gate",
                "target": "oc_cn",
                "count": 1,
                "ok": 0,
            },
            fail_row(TODAY, "daily:report", "ou_a"),
            fail_row(TODAY, "daily:report", "ou_a"),
        ],
    )
    p = payload(root)
    idle = [i for i in p["items"] if i["state"] == "idle"]
    assert len(idle) == 2 and all(i.get("cluster_size") is None for i in idle)
    fb = [i for i in p["items"] if i["state"] == "blocked"]
    assert len(fb) == 1 and fb[0]["cluster_size"] == 2
    assert p["meta"]["blocked_archive"]["raw_blocked"] == 2


def test_archive_rule_visible_and_readonly(root):
    """归档语义透出给消费方；投影全程零写路径。"""
    make_flow(root, [fail_row(TODAY, "daily:report", "ou_a")])
    p = payload(root)
    ba = p["meta"]["blocked_archive"]
    assert "T+7" in ba["rule"] and ba["clustered_source"] == "push_flow"
    assert ba["cluster_key"] == "kind+target"
    # 零写路径：读侧折叠不改账本
    fp = root / "portal" / "work" / "push_flow.jsonl"
    before = (fp.stat().st_size, fp.stat().st_mtime_ns)
    payload(root)
    assert (fp.stat().st_size, fp.stat().st_mtime_ns) == before
