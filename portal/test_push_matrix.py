# -*- coding: utf-8 -*-
"""市场推送矩阵解析器单测（K-ORG-197 轨道A，纯离线零触网）。"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import push_matrix as pm  # noqa: E402


class TestDst:
    def test_summer_winter(self):
        assert pm.us_dst_active(date(2026, 7, 15)) is True
        assert pm.us_dst_active(date(2026, 1, 15)) is False
        assert pm.us_dst_active(date(2027, 1, 15)) is False

    def test_boundaries(self):
        # 2026-03-08 = 3 月第 2 个周日（夏令时起始日，当日生效）
        assert pm.us_dst_active(date(2026, 3, 8)) is True
        assert pm.us_dst_active(date(2026, 3, 7)) is False
        # 2026-11-01 = 11 月第 1 个周日（切回冬令时，当日已不生效）
        assert pm.us_dst_active(date(2026, 11, 1)) is False
        assert pm.us_dst_active(date(2026, 10, 31)) is True


class TestResolve:
    def test_winter_shift_us(self):
        cfg = {"dst": "us", "winter_shift_minutes": 60,
               "windows": [{"name": "preopen", "start": "20:40",
                            "end": "21:20", "deadline": "21:00"}]}
        summer = pm.resolve_windows(cfg, date(2026, 7, 15))[0]
        winter = pm.resolve_windows(cfg, date(2026, 1, 15))[0]
        assert (summer["start"], summer["deadline"]) == ("20:40", "21:00")
        assert winter["start"] == "21:40" and winter["deadline"] == "22:00"
        assert winter["_shift_min"] == 60 and summer["_shift_min"] == 0

    def test_cn_no_shift(self):
        cfg = {"windows": [{"name": "morning", "start": "07:40",
                            "end": "09:15", "deadline": "08:00"}]}
        w = pm.resolve_windows(cfg, date(2026, 1, 15))[0]
        assert w["start"] == "07:40" and w["_shift_min"] == 0


class TestInWindow:
    def setup_method(self):
        self.m = pm.load_matrix(HERE / "push_matrix.json")

    def test_hit(self):
        mkt, w = pm.in_window(datetime(2026, 10, 19, 8, 0), self.m)
        assert mkt == "cn" and w["name"] == "morning"

    def test_us_winter_hit(self):
        # 冬令时 1 月：preopen 21:40-22:20 → 22:00 应命中（并越过静默线，仅判窗口）
        mkt, w = pm.in_window(datetime(2026, 1, 15, 22, 0), self.m)
        assert mkt == "us" and w["name"] == "preopen"

    def test_miss(self):
        mkt, w = pm.in_window(datetime(2026, 10, 19, 12, 0), self.m)
        assert mkt is None and w is None


class TestNextWindow:
    def setup_method(self):
        self.m = pm.load_matrix(HERE / "push_matrix.json")

    def test_same_day(self):
        # 17:00 在全部收盘窗开始之后 → 今日下一个为加密晚窗 20:10（最早）
        mkt, w, st = pm.next_window(datetime(2026, 10, 19, 17, 0), self.m)
        assert mkt == "crypto" and w["name"] == "rhythm_pm"
        assert st == datetime(2026, 10, 19, 20, 10)

    def test_cross_day(self):
        mkt, w, st = pm.next_window(datetime(2026, 10, 19, 21, 0), self.m)
        assert mkt == "us" and st.date() == date(2026, 10, 20) \
            and st.hour == 5 and st.minute == 40


class TestQuiet:
    def setup_method(self):
        self.m = pm.load_matrix(HERE / "push_matrix.json")

    def test_quiet_daily(self):
        assert pm.is_quiet(datetime(2026, 10, 19, 23, 0), self.m, "daily")
        assert pm.is_quiet(datetime(2026, 10, 19, 3, 0), self.m, "daily")

    def test_daytime_not_quiet(self):
        assert not pm.is_quiet(datetime(2026, 10, 19, 12, 0), self.m, "daily")
        assert not pm.is_quiet(datetime(2026, 10, 19, 21, 59), self.m, "daily")

    def test_critical_bypass(self):
        assert not pm.is_quiet(datetime(2026, 10, 19, 23, 0), self.m,
                               "critical")


def _touch(p: Path, ts: datetime):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    import os
    os.utime(p, (ts.timestamp(), ts.timestamp()))


class TestAnchor:
    def setup_method(self):
        self.m = pm.load_matrix(HERE / "push_matrix.json")
        self.m["anchors"]["t"] = {"path": "tmp_anchor/x.json",
                                  "rule": "mtime_today",
                                  "min_mtime": "07:25", "enabled": True}

    def _repo(self, tmp: Path) -> Path:
        return tmp

    def test_ok(self, tmp_path):
        _touch(tmp_path / "tmp_anchor" / "x.json",
               datetime(2026, 10, 19, 7, 30))
        r = pm.anchor_status("t", self.m, datetime(2026, 10, 19, 8, 0),
                             repo=tmp_path)
        assert r["status"] == "ok"

    def test_missing_and_disabled_and_unknown(self, tmp_path):
        r = pm.anchor_status("t", self.m, repo=tmp_path)
        assert r["status"] == "missing"
        self.m["anchors"]["t"]["enabled"] = False
        assert pm.anchor_status("t", self.m, repo=tmp_path)["status"] == \
            "disabled"
        assert pm.anchor_status("nope", self.m)["status"] == "unknown"

    def test_stale_yesterday_and_early(self, tmp_path):
        _touch(tmp_path / "tmp_anchor" / "x.json",
               datetime(2026, 10, 18, 9, 0))
        r = pm.anchor_status("t", self.m, datetime(2026, 10, 19, 8, 0),
                             repo=tmp_path)
        assert r["status"] == "stale"
        _touch(tmp_path / "tmp_anchor" / "x.json",
               datetime(2026, 10, 19, 7, 0))
        r = pm.anchor_status("t", self.m, datetime(2026, 10, 19, 8, 0),
                             repo=tmp_path)
        assert r["status"] == "stale" and "07:25" in r["detail"]


class TestLoad:
    def test_shipped_config_valid(self):
        m = pm.load_matrix(HERE / "push_matrix.json")
        # 六市场全覆盖，锚引用闭合（每个窗口的 anchor/templates 有注册）
        assert set(m["markets"]) == {"cn", "hk", "us", "commodity", "crypto",
                                     "fx"}
        for mkt, cfg in m["markets"].items():
            for w in cfg["windows"]:
                assert w["anchor"] in m["anchors"], (mkt, w["name"])
                assert w["template"] in m["templates"], (mkt, w["name"])

    def test_bad_json_visible(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("{broken", encoding="utf-8")
        try:
            pm.load_matrix(p)
            assert False, "should raise"
        except json.JSONDecodeError:
            pass
