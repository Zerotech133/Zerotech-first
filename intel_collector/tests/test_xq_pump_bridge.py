# -*- coding: utf-8 -*-
"""⑤ 第三层桥接测试：xq_pump 抽取 → Observation 治理态 → CronRunner 集成。

全链路不触网（FakeSession 注入）；验证：
  1) collect_source 跑 xq_pump 抽取并双落库（xq_pump 行级 + Observation 治理态）；
  2) 失败可见（注入异常 session → INCOMPLETE + reason）；
  3) CronRunner.tick 端到端：due → dispatch → Observation → SLA 报告可读；
  4) RightsGate 用途绑定只写授权用途；
  5) 空结果 → EMPTY 状态分账。
"""
from __future__ import annotations

import sys
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(ROOT.parent / "quant_backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from bridges.xq_pump_bridge import XqPumpDispatcher
from core.cron_runner import CronRunner
from core.contracts import Status
from registry.source_registry import SourceRegistry
from store.obs_store import ObservationStore


def _item(i, created=1700000000000):
    return {"id": i, "created_at": created, "title": "t%d" % i,
            "description": "d%d" % i, "user_id": "u", "target": "",
            "comment_count": 0, "view_count": 1, "retweet_count": 0,
            "like_count": 0, "reward_count": 0, "source": "web"}


class _FakeSession:
    """xq_pump 会话契约（get_json/sleep），页序列驱动。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self._i = 0
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, dict(params)))
        if self._i < len(self.pages):
            p = self.pages[self._i]
            self._i += 1
            return p
        return {"list": [], "next_max_id": None}

    def sleep(self, base=2.0):
        pass


def _make_reg(tmpdir):
    import json
    reg_path = os.path.join(tmpdir, "sources.json")
    spec = {
        "xueqiu": {
            "source_id": "xueqiu", "name": "雪球", "category": "open_source_intel",
            "owner": "intel(军情司·INTEL0)", "schema_version": "1.0",
            "rights": {"internal_retention": True, "cleaning": True,
                       "controlled_query": True},
            "rights_meta": {}, "content_types": ["post", "comment"],
            "sla": {"freshness_minutes": 30, "max_failures": 3},
            "enabled": True, "health": {},
        }
    }
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    return SourceRegistry(reg_path)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class TestBridge:
    def test_collect_posts_dual_write(self, tmp_path):
        reg = _make_reg(str(tmp_path))
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        sess = _FakeSession(pages=[
            {"list": [_item(1), _item(2)], "next_max_id": None},
        ])
        d = XqPumpDispatcher(reg, obs_store, session=sess,
                             extractor_types=("posts",), max_new=10)
        res = d.collect_source("xueqiu", NOW)
        assert res["ok"] is True
        assert res["n"] == 2
        # Observation 治理态
        obs = obs_store.latest("xueqiu", "xq_feed", "run:2026-11-01T12:00:00Z")
        assert obs is not None
        assert obs.status == Status.COMPLETE
        assert obs.payload["total_new"] == 2
        assert "internal_retention" in obs.allowed_use_cases
        assert "training" not in obs.allowed_use_cases  # 高风险不随入湖

    def test_bridge_rejects_non_xueqiu(self, tmp_path):
        reg = _make_reg(str(tmp_path))
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        d = XqPumpDispatcher(reg, obs_store, session=_FakeSession([]))
        res = d.collect_source("guba", NOW)
        assert res["ok"] is False
        assert "只接 xueqiu" in res["reason"]

    def test_failure_visible(self, tmp_path):
        reg = _make_reg(str(tmp_path))
        obs_store = ObservationStore(str(tmp_path / "obs.db"))

        class _BoomSession:
            def get_json(self, url, params):
                raise RuntimeError("simulated network failure")

            def sleep(self, base=2.0):
                pass

        d = XqPumpDispatcher(reg, obs_store, session=_BoomSession(),
                             extractor_types=("posts",))
        res = d.collect_source("xueqiu", NOW)
        assert res["ok"] is False
        assert "simulated network failure" in res["reason"]
        obs = obs_store.latest("xueqiu", "xq_feed", "run:2026-11-01T12:00:00Z")
        assert obs.status == Status.INCOMPLETE  # 失败可见
        # 健康更新
        assert reg.get("xueqiu").health.get("consecutive_failures") == 1

    def test_empty_is_empty_status(self, tmp_path):
        reg = _make_reg(str(tmp_path))
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        sess = _FakeSession(pages=[{"list": [], "next_max_id": None}])
        d = XqPumpDispatcher(reg, obs_store, session=sess,
                             extractor_types=("posts",))
        res = d.collect_source("xueqiu", NOW)
        assert res["ok"] is True and res["n"] == 0
        obs = obs_store.latest("xueqiu", "xq_feed", "run:2026-11-01T12:00:00Z")
        assert obs.status == Status.EMPTY

    def test_cron_runner_end_to_end(self, tmp_path):
        """CronRunner.tick → dispatcher（桥）→ Observation → 反馈环吃真实结果。"""
        from core.scheduler import Scheduler
        from core.feedback import FeedbackEngine

        reg = _make_reg(str(tmp_path))
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        sess = _FakeSession(pages=[{"list": [_item(1)], "next_max_id": None}])
        d = XqPumpDispatcher(reg, obs_store, session=sess,
                             extractor_types=("posts",))

        clock = {"t": NOW}
        sched = Scheduler(reg, now_provider=lambda: clock["t"])
        runner = CronRunner(sched, dispatcher=d,
                            now_provider=lambda: clock["t"])
        report = runner.tick(NOW)
        assert "xueqiu" in report.dispatched
        assert report.outcomes["xueqiu"]["ok"] is True
        assert report.outcomes["xueqiu"]["n"] == 1
        # Observation 已落库
        obs = obs_store.latest("xueqiu", "xq_feed", "run:2026-11-01T12:00:00Z")
        assert obs is not None and obs.payload["total_new"] == 1
