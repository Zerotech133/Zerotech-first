# -*- coding: utf-8 -*-
"""市场推送矩阵发送器单测（K-ORG-197 轨道C，纯离线零触网）。

纪律：路径全量 tmp_path 注入（预览/流水/订阅），env 用 monkeypatch 清场，
发送走 fake poster——绝不读真实 C:/aios、绝不真发 HTTP、绝不触真实密钥文件。
组织风格仿 portal/test_push_matrix.py（判例）。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import push_matrix_send as ms  # noqa: E402


# ---------- 测试夹具 ----------

def _matrix():
    """测试矩阵：两市场不同 daily_cap + 静默窗 + critical 窗口 + 国灶模板。"""
    return {
        "version": 1,
        "quiet_hours": {"start": "22:00", "end": "07:30"},
        "critical_tiers": ["critical"],
        "markets": {
            "cn": {"daily_cap": 1, "windows": [
                {"name": "morning", "start": "07:40", "end": "09:15",
                 "tier": "daily", "anchor": "guojia",
                 "template": "daily_digest", "deadline": "08:00"}]},
            "us": {"daily_cap": 2, "windows": [
                {"name": "preopen", "start": "20:40", "end": "21:20",
                 "tier": "daily", "anchor": "guojia",
                 "template": "us_preopen", "deadline": "21:00"},
                {"name": "burst", "start": "12:00", "end": "13:00",
                 "tier": "critical", "anchor": "guojia",
                 "template": "market_close", "deadline": "12:30"}]},
        },
        "templates": {
            "daily_digest": {"title": "晨报",
                             "snapshot": "picks_public/data_daily.html"},
            "market_close": {"title": "收盘对账",
                             "snapshot": "picks_public/data_guojia.html"},
            "us_preopen": {"title": "美股盘前",
                           "snapshot": "picks_public/data_daily.html"},
        },
        "anchors": {},
    }


def _subs():
    """测试订阅：A 订 cn（daily）、B 订 cn+us（daily）、C 仅 critical 档。"""
    return {"version": 1, "dryrun_default": True, "groups": {
        "A": {"name": "群A", "markets": ["cn"], "tier": "daily",
              "chat_id": "oc_aaa"},
        "B": {"name": "群B", "markets": ["cn", "us"], "tier": "daily",
              "chat_id": "oc_bbb"},
        "C": {"name": "群C", "markets": ["cn"], "tier": "critical",
              "chat_id": "oc_ccc"},
    }}


WIN_MORNING = {"name": "morning", "start": "07:40", "end": "09:15",
               "tier": "daily", "template": "daily_digest"}
WIN_BURST = {"name": "burst", "start": "12:00", "end": "13:00",
             "tier": "critical", "template": "market_close"}


class _Poster:
    """fake poster：记录 (url, payload, key) 调用，绝不触网。"""

    def __init__(self, resp=None):
        self.calls = []
        self.resp = resp or {"ok": True, "via": "hub"}

    def __call__(self, url, payload, key):
        self.calls.append((url, payload, key))
        return dict(self.resp)


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """路径注入：预览/流水/订阅全部指向 tmp_path，发送相关 env 清场。"""
    monkeypatch.setattr(ms, "PREVIEW_JSON", tmp_path / "preview.json")
    monkeypatch.setattr(ms, "LOG_JSON", tmp_path / "log.json")
    monkeypatch.setattr(ms, "SUBS_JSON", tmp_path / "subs.json")
    monkeypatch.setattr(ms, "SECRETS_JSON", tmp_path / "push_secrets.json")
    monkeypatch.delenv("MATRIX_SEND_LIVE", raising=False)
    monkeypatch.delenv("PUSH_HUB_KEY", raising=False)
    return tmp_path


def _picked_pls(subs, market, window, matrix, repo):
    groups = ms.pick_groups(subs, market, window["tier"])
    pls = {gid: ms.build_payload(g, market, window, matrix, repo)
           for gid, g in groups}
    return groups, pls


def _write_cfg(iso):
    """把矩阵/订阅落成 tmp 文件（run()/CLI 注入用）。"""
    pdir = iso / "portal"
    pdir.mkdir(exist_ok=True)
    mpath = pdir / "push_matrix.json"
    mpath.write_text(json.dumps(_matrix(), ensure_ascii=False),
                     encoding="utf-8")
    spath = iso / "subs.json"
    spath.write_text(json.dumps(_subs(), ensure_ascii=False),
                     encoding="utf-8")
    return mpath, spath


# ---------- (a) 选群：市场 + tier 过滤 ----------

class TestPickGroups:
    def test_daily_filters_critical_only(self):
        s = _subs()
        assert [g for g, _ in ms.pick_groups(s, "cn", "daily")] == ["A", "B"]
        assert [g for g, _ in ms.pick_groups(s, "us", "daily")] == ["B"]

    def test_critical_sends_all(self):
        s = _subs()
        assert [g for g, _ in
                ms.pick_groups(s, "cn", "critical")] == ["A", "B", "C"]

    def test_market_mismatch_excluded(self):
        s = _subs()
        assert ms.pick_groups(s, "crypto", "daily") == []


# ---------- (b) 静默窗（critical 豁免） ----------

class TestQuiet:
    def test_daily_skip_with_reason(self, iso):
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        p = _Poster()
        now = datetime(2026, 10, 19, 23, 0)  # 静默窗 22:00-07:30 内
        res = ms.send_cards(groups, pls, live=False, now=now,
                            poster=p, matrix=m)
        assert p.calls == [] and res["sent"] == []
        assert len(res["skipped"]) == 2
        assert all("静默" in s["reason"] for s in res["skipped"])

    def test_critical_exempt(self, iso):
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_BURST, m, iso)
        now = datetime(2026, 10, 19, 23, 0)
        res = ms.send_cards(groups, pls, live=False, now=now,
                            poster=_Poster(), matrix=m)
        assert res["skipped"] == []  # critical 档豁免静默，不 skip
        assert len(res["would_send"]) == 3


# ---------- (c) 日频控 ----------

class TestDailyCap:
    def test_cap_blocks_group_after_filled(self, iso):
        m = _matrix()  # cn daily_cap=1
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        today = "2026-10-19"
        (iso / "log.json").write_text(json.dumps([
            {"date": today, "market": "cn", "window": "morning",
             "group": "A", "ts": today + " 08:00:00"}]), encoding="utf-8")
        p = _Poster()
        res = ms.send_cards(groups, pls, live=False,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert p.calls == []
        by = {s["group"]: s["reason"] for s in res["skipped"]}
        assert "日频控" in by["A"]
        assert "A" not in [w["group"] for w in res["would_send"]]
        # 频控按（日期+市场+群）计：B 未发过不受 A 占用影响
        assert "B" in [w["group"] for w in res["would_send"]]

    def test_cap_blocks_live_send(self, iso, monkeypatch):
        monkeypatch.setenv("MATRIX_SEND_LIVE", "1")
        monkeypatch.setenv("PUSH_HUB_KEY", "k-test")
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        today = "2026-10-19"
        (iso / "log.json").write_text(json.dumps([
            {"date": today, "market": "cn", "window": "morning",
             "group": gid, "ts": today + " 08:00:00"} for gid in ("A", "B")]),
            encoding="utf-8")
        p = _Poster()
        res = ms.send_cards(groups, pls, live=True,
                            now=datetime(2026, 10, 19, 8, 30),
                            poster=p, matrix=m)
        assert p.calls == []  # 双双注满 cap=1 → 全部拦截
        assert len(res["skipped"]) == 2


# ---------- (d) DRYRUN：预览落盘 / 零 HTTP / 不写 log ----------

class TestDryrun:
    def test_preview_no_http_no_log(self, iso):
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        p = _Poster()
        res = ms.send_cards(groups, pls, live=False,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert p.calls == []                      # 零 HTTP
        assert not (iso / "log.json").exists()    # 不写流水
        assert res["live"] is False and res["preview"]
        prev = json.loads((iso / "preview.json").read_text(encoding="utf-8"))
        assert set(prev) == {"generated_at", "would_send", "skipped"}
        assert [w["group"] for w in prev["would_send"]] == ["A", "B"]
        # 预览里的卡片走统一 v3 规范
        assert prev["would_send"][0]["card"]["schema"] == "2.0"
        assert "晨报 · A股" in \
            prev["would_send"][0]["card"]["header"]["title"]["content"]

    def test_subs_dryrun_default_forces_dryrun(self, iso, monkeypatch):
        # --live 意图 + env 门禁开，但订阅 dryrun_default=true → 仍强制 DRYRUN
        monkeypatch.setenv("MATRIX_SEND_LIVE", "1")
        monkeypatch.setenv("PUSH_HUB_KEY", "k-test")
        mpath, spath = _write_cfg(iso)
        p = _Poster()
        res = ms.run("cn", "morning", live=True,
                     now=datetime(2026, 10, 19, 8, 0),
                     matrix_path=mpath, subs_path=spath, poster=p)
        assert p.calls == [] and res["live"] is False
        assert (iso / "preview.json").exists() and \
            not (iso / "log.json").exists()

    def test_live_intent_env_closed_all_skipped(self, iso, monkeypatch):
        # 双门禁之一：意图 live 但 env MATRIX_SEND_LIVE 未开 → 拒绝发送
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        p = _Poster()
        res = ms.send_cards(groups, pls, live=True,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert p.calls == [] and not (iso / "log.json").exists()
        assert len(res["skipped"]) == 2
        assert all("MATRIX_SEND_LIVE" in s["reason"]
                   for s in res["skipped"])


# ---------- (e) live 发送：schema / 流水 / 失败处理 ----------

class TestLive:
    def _env(self, monkeypatch):
        monkeypatch.setenv("MATRIX_SEND_LIVE", "1")
        monkeypatch.setenv("PUSH_HUB_KEY", "k-test")

    def test_empty_chat_id_skipped(self, iso, monkeypatch):
        self._env(monkeypatch)
        s = _subs()
        s["groups"]["B"]["chat_id"] = ""
        m = _matrix()
        groups, pls = _picked_pls(s, "cn", WIN_MORNING, m, iso)
        p = _Poster()
        res = ms.send_cards(groups, pls, live=True,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert [c[1]["receive_id"] for c in p.calls] == ["oc_aaa"]
        by = {x["group"]: x["reason"] for x in res["skipped"]}
        assert "chat_id" in by["B"]

    def test_api_send_schema_and_log(self, iso, monkeypatch):
        self._env(monkeypatch)
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        p = _Poster()
        res = ms.send_cards(groups, pls, live=True,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert res["live"] is True and len(res["sent"]) == 2
        assert len(p.calls) == 2
        url, body, key = p.calls[0]
        # 通道与鉴权：8097 /api/send + X-Push-Hub-Key（env 供钥）
        assert url == ms.HUB_URL == "http://127.0.0.1:8097/api/send"
        assert key == "k-test"
        # /api/send 实际 schema（push_admin_server.py api_send L656-711）
        assert body["msg_type"] == "interactive"
        assert body["receive_id"] == "oc_aaa"
        assert body["receive_id_type"] == "chat_id"
        assert body["kind"] == "matrix:cn"
        assert body["critical"] is False
        card = json.loads(body["content"])  # content = v3 卡片 JSON 串
        assert card["schema"] == "2.0"
        assert "晨报 · A股" in card["header"]["title"]["content"]
        # 仅成功发送才追加流水
        log = json.loads((iso / "log.json").read_text(encoding="utf-8"))
        assert [e["group"] for e in log] == ["A", "B"]
        assert all(e["date"] == "2026-10-19" and e["market"] == "cn"
                   and e["window"] == "morning" for e in log)

    def test_poster_fail_no_log(self, iso, monkeypatch):
        self._env(monkeypatch)
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        p = _Poster(resp={"ok": False, "why": "silent window", "via": "hub"})
        res = ms.send_cards(groups, pls, live=True,
                            now=datetime(2026, 10, 19, 8, 0),
                            poster=p, matrix=m)
        assert len(p.calls) == 2 and res["sent"] == []
        assert not (iso / "log.json").exists()  # 失败不落流水
        assert all("发送失败" in s["reason"] for s in res["skipped"])

    def test_live_without_key_fails_visible(self, iso, monkeypatch):
        monkeypatch.setenv("MATRIX_SEND_LIVE", "1")  # 有 env 无密钥 → 显式报错
        m = _matrix()
        groups, pls = _picked_pls(_subs(), "cn", WIN_MORNING, m, iso)
        with pytest.raises(RuntimeError):
            ms.send_cards(groups, pls, live=True,
                          now=datetime(2026, 10, 19, 8, 0),
                          poster=_Poster(), matrix=m)


# ---------- (f) 国灶摘要 fail-soft ----------

class TestGuojiaDigest:
    def test_missing_summary_omitted(self, iso):
        m = _matrix()  # market_close 模板 snapshot 含 data_guojia
        pl = ms.build_payload(_subs()["groups"]["C"], "cn", WIN_BURST,
                              m, iso)
        assert pl["digest"] is None  # 缺 summary 不抛错，摘要省略
        assert pl["card"]["schema"] == "2.0"

    def test_present_and_broken_json(self, iso):
        sp = iso / "news_collector" / "data" / "guojia" / "summary.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(
            {"overall": {"hit_rate": 0.78, "n_reconciled": 132}}),
            encoding="utf-8")
        m = _matrix()
        pl = ms.build_payload(_subs()["groups"]["C"], "cn", WIN_BURST,
                              m, iso)
        assert "0.78" in pl["digest"] and "132" in pl["digest"]
        sp.write_text("{broken", encoding="utf-8")  # 坏 JSON 同样 fail-soft
        pl2 = ms.build_payload(_subs()["groups"]["C"], "cn", WIN_BURST,
                               m, iso)
        assert pl2["digest"] is None


# ---------- 订阅加载与 CLI ----------

class TestLoadAndCli:
    def test_shipped_subs_locked_dryrun(self):
        s = ms.load_subscribers(HERE / "push_subscribers.json")
        assert s["dryrun_default"] is True  # 三重门锁：出厂必 DRYRUN
        # 2026-10-19 拍板灰度首群 laotian_self 入册（K-ORG-197 遗留口径
        # 同步，随 K-ORG-199 收口）；入册群 chat_id 必须非空，live 仍受
        # env MATRIX_SEND_LIVE + chat_id 双门禁约束。
        assert s["groups"]["laotian_self"]["chat_id"]

    def test_bad_subs_visible(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{broken", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            ms.load_subscribers(p)

    def test_cli_dryrun_exit0(self, iso, capsys):
        mpath, spath = _write_cfg(iso)
        rc = ms.main(["--market", "cn", "--window", "morning",
                      "--now", "2026-10-19T08:00",
                      "--matrix", str(mpath), "--subs", str(spath)])
        assert rc == 0
        res = json.loads(capsys.readouterr().out)
        assert res["market"] == "cn" and res["window"] == "morning"
        assert res["live"] is False
        assert (iso / "preview.json").exists()

    def test_cli_bad_window_exit1(self, iso, capsys):
        mpath, spath = _write_cfg(iso)
        rc = ms.main(["--market", "cn", "--window", "nope",
                      "--now", "2026-10-19T08:00",
                      "--matrix", str(mpath), "--subs", str(spath)])
        assert rc == 1
        assert "FAIL" in capsys.readouterr().out
