# -*- coding: utf-8 -*-
"""deadline_watchdog 单测（K-ORG-197 轨道B，纯离线零触网零真实发送）。

手法仿 portal/test_push_matrix.py 的 TestAnchor：往矩阵 dict 注入测试锚
（enabled=True、path 指向 tmp_path 相对路径），用 os.utime 控制 mtime；
旗标/日志路径经 monkeypatch 指到 tmp_path；sender 一律注入 fake，
全程零真实发送，绝不读真实 C:/aios。
"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "portal")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import deadline_watchdog as dw  # noqa: E402
import push_matrix as pm  # noqa: E402

# NOW 落在测试窗判定带内：deadline 08:00 <= 08:05 <= end 09:15 + 30min
NOW = datetime(2026, 10, 19, 8, 5)
ANCHOR_REL = "tmp_anchor/x.json"


def _make_matrix(deadline="08:00", start="07:40", end="09:15",
                 anchor="t", anchors=None):
    """裁剪真实矩阵为单市场单窗口，并注入测试锚（仿 TestAnchor 手法）。"""
    m = copy.deepcopy(
        pm.load_matrix(HERE.parent / "portal" / "push_matrix.json"))
    m["markets"] = {"cn": {"tz": "Asia/Shanghai", "daily_cap": 2, "windows": [
        {"name": "morning", "start": start, "end": end, "tier": "daily",
         "anchor": anchor, "template": "daily_digest", "deadline": deadline}]}}
    m["anchors"] = {"t": {"path": ANCHOR_REL, "rule": "mtime_today",
                          "min_mtime": "07:25", "enabled": True}}
    if anchors:
        m["anchors"].update(anchors)
    return m


def _touch(p: Path, ts: datetime):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    import os
    os.utime(p, (ts.timestamp(), ts.timestamp()))


def _fake_sender(calls):
    def sender(title, template, md):
        calls.append({"title": title, "template": template, "md": md})
    return sender


@pytest.fixture(autouse=True)
def _iso_env(tmp_path, monkeypatch):
    """每用例把日志/旗标模块路径隔离到 tmp_path，测试不写 worktree 仓库目录。"""
    monkeypatch.setattr(dw, "LOG_PATH", str(tmp_path / "deadline_watch.log"))
    monkeypatch.setattr(dw, "FLAG_PATH",
                        str(tmp_path / "deadline_pending.json"))


class TestRunCheck:
    def test_missing_then_stale_pending(self, tmp_path):
        m = _make_matrix()
        r = dw.run_check(m, tmp_path, NOW)  # 未 touch 锚文件 → missing
        assert r["checked"] == 1 and r["skipped"] == 0
        it = r["pending"][0]
        assert (it["market"], it["window"], it["status"],
                it["deadline"]) == ("cn", "morning", "missing", "08:00")

        _touch(tmp_path / ANCHOR_REL, datetime(2026, 10, 19, 7, 0))
        r = dw.run_check(m, tmp_path, NOW)  # 今日 mtime 但早于 07:25 → stale
        assert r["pending"][0]["status"] == "stale"
        assert "07:25" in r["pending"][0]["detail"]

    def test_disabled_and_unknown_never_pending(self, tmp_path):
        m = _make_matrix(anchors={"t": {
            "path": ANCHOR_REL, "rule": "mtime_today",
            "min_mtime": "07:25", "enabled": False}})
        r = dw.run_check(m, tmp_path, NOW)
        assert r["pending"] == [] and r["checked"] == 0 and r["skipped"] == 1

        m2 = _make_matrix(anchor="nope")  # 矩阵未注册 → unknown
        r2 = dw.run_check(m2, tmp_path, NOW)
        assert r2["pending"] == [] and r2["checked"] == 0 and r2["skipped"] == 1

    def test_before_deadline_not_judged(self, tmp_path):
        r = dw.run_check(_make_matrix(), tmp_path,
                         datetime(2026, 10, 19, 7, 50))
        assert r["checked"] == 0 and r["pending"] == []

    def test_after_grace_band_not_judged(self, tmp_path):
        # end 09:15 + 30min 宽限 = 09:45，09:46 起出带不再判
        r = dw.run_check(_make_matrix(), tmp_path,
                         datetime(2026, 10, 19, 9, 46))
        assert r["checked"] == 0 and r["pending"] == []


class TestRunCycle:
    def test_pending_writes_flag_rc2_no_card(self, tmp_path):
        calls = []
        rc, rep = dw.run_cycle(matrix=_make_matrix(), repo=tmp_path, now=NOW,
                               sender=_fake_sender(calls))
        assert rc == 2 and len(rep["pending"]) == 1
        flag = json.loads(Path(dw.FLAG_PATH).read_text(encoding="utf-8"))
        assert flag["attempts"] == {"cn|morning": 1}
        assert flag["pending"][0]["escalated"] is False
        assert calls == []  # 仅 pending 未升级：只更新旗标留痕，不发卡

    def test_twice_escalates_red_card(self, tmp_path):
        calls = []
        m = _make_matrix()
        dw.run_cycle(matrix=m, repo=tmp_path, now=NOW,
                     sender=_fake_sender(calls))
        rc, rep = dw.run_cycle(matrix=m, repo=tmp_path, now=NOW,
                               sender=_fake_sender(calls))
        assert rc == 2
        assert rep["pending"][0]["attempts"] == 2
        assert rep["pending"][0]["escalated"] is True
        assert len(calls) == 1 and calls[0]["template"] == "red"
        assert "cn" in calls[0]["md"] and "morning" in calls[0]["md"]
        flag = json.loads(Path(dw.FLAG_PATH).read_text(encoding="utf-8"))
        assert flag["attempts"] == {"cn|morning": 2}  # 红卡同时账本已推进

    def test_recover_green_card_and_clear(self, tmp_path):
        calls = []
        m = _make_matrix()
        dw.run_cycle(matrix=m, repo=tmp_path, now=NOW,
                     sender=_fake_sender(calls))
        assert Path(dw.FLAG_PATH).exists()
        _touch(tmp_path / ANCHOR_REL, datetime(2026, 10, 19, 7, 30))  # ok
        rc, rep = dw.run_cycle(matrix=m, repo=tmp_path, now=NOW,
                               sender=_fake_sender(calls))
        assert rc == 0 and rep["pending"] == []
        assert len(calls) == 1 and calls[0]["template"] == "green"
        assert "已恢复" in calls[0]["title"]
        assert not Path(dw.FLAG_PATH).exists()  # 旗标已清除

    def test_out_of_band_keeps_flag_no_fake_recovery(self, tmp_path):
        """P1-2 回归：锚持续缺数出判定带后，不得假恢复销账。

        原 bug：出带项不在 report["pending"] → 误发绿卡+清旗标+attempts
        归零。修复后：旗标与 attempts 保留、不发绿卡、exit=2。
        """
        calls = []
        m = _make_matrix()
        dw.run_cycle(matrix=m, repo=tmp_path, now=NOW,
                     sender=_fake_sender(calls))
        assert json.loads(
            Path(dw.FLAG_PATH).read_text(encoding="utf-8"))[
                "attempts"] == {"cn|morning": 1}
        # 下一轮 now 已出宽限带（end 09:15+30min=09:45 之后）：锚仍未就绪
        later = datetime(2026, 10, 19, 11, 0)
        rc, rep = dw.run_cycle(matrix=m, repo=tmp_path, now=later,
                               sender=_fake_sender(calls))
        assert rc == 2  # 不冒充恢复
        assert calls == []  # 无绿卡无红卡（出带项不在巡检面，不骚扰）
        flag = json.loads(Path(dw.FLAG_PATH).read_text(encoding="utf-8"))
        assert flag["pending"][0]["market"] == "cn"  # 旗标保留
        assert flag["attempts"] == {"cn|morning": 1}  # 账本不归零
        # 再下一日重新入带且仍未就绪：attempts 从保留值继续累计
        next_day = datetime(2026, 10, 20, 8, 5)
        rc, rep = dw.run_cycle(matrix=m, repo=tmp_path, now=next_day,
                               sender=_fake_sender(calls))
        assert rc == 2 and rep["pending"][0]["attempts"] == 2

    def test_ok_without_flag_no_card(self, tmp_path):
        calls = []
        _touch(tmp_path / ANCHOR_REL, datetime(2026, 10, 19, 7, 30))
        rc, rep = dw.run_cycle(matrix=_make_matrix(), repo=tmp_path, now=NOW,
                               sender=_fake_sender(calls))
        assert rc == 0 and rep["pending"] == []
        assert calls == [] and not Path(dw.FLAG_PATH).exists()

    def test_dry_run_writes_nothing(self, tmp_path):
        calls = []
        rc, _ = dw.run_cycle(matrix=_make_matrix(), repo=tmp_path, now=NOW,
                             sender=_fake_sender(calls), dry_run=True)
        assert rc == 2
        assert not Path(dw.FLAG_PATH).exists() and calls == []

    def test_corrupt_flag_rebuilt_visible(self, tmp_path):
        Path(dw.FLAG_PATH).write_text("{broken", encoding="utf-8")
        calls = []
        rc, rep = dw.run_cycle(matrix=_make_matrix(), repo=tmp_path, now=NOW,
                               sender=_fake_sender(calls))
        assert rc == 2 and rep.get("flag_error")  # 坏旗标不吞，report 留痕
        flag = json.loads(Path(dw.FLAG_PATH).read_text(encoding="utf-8"))
        assert flag["attempts"] == {"cn|morning": 1}  # 按空账重建，从 1 起算


class TestCli:
    def test_main_rc2_and_flag(self, tmp_path, monkeypatch):
        # monkeypatch 模块路径：矩阵 JSON / 仓库根全部指到 tmp，绝不触 C:/aios
        mjson = tmp_path / "portal" / "push_matrix.json"
        mjson.parent.mkdir(parents=True, exist_ok=True)
        mjson.write_text(json.dumps(_make_matrix(), ensure_ascii=False),
                         encoding="utf-8")
        monkeypatch.setattr(pm, "MATRIX_JSON", mjson)
        monkeypatch.setattr(pm, "REPO", tmp_path)
        rc = dw.main(["--now", "2026-10-19 08:05"])  # missing 首轮不发卡，零发送
        assert rc == 2
        flag = json.loads(Path(dw.FLAG_PATH).read_text(encoding="utf-8"))
        assert flag["pending"][0]["status"] == "missing"
        assert flag["attempts"] == {"cn|morning": 1}
