# -*- coding: utf-8 -*-
"""05:30 每日检查 + 部门派单的行为测试。

日期字面量一律用码点构造（项目铁律：Write/Edit 与 stdout 通道都会串味数字串）。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

D = lambda *codes: "".join(chr(c) for c in codes)  # noqa: E731
DAY_T = D(50, 48, 50, 54, 49, 49, 49, 50)   # 目标数据日
DAY_X = D(50, 48, 50, 54, 49, 49, 49, 51)   # 执行日
DAY_P = D(50, 48, 50, 54, 49, 49, 49, 49)   # 前一交易日
DAY_O = D(50, 48, 50, 54, 49, 49, 49, 48)

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "tools" / "preopen" / "daily_check_dispatch.py"
ROUTES = ROOT / "tools" / "preopen" / "daily_check_routes.json"


def _load():
    spec = importlib.util.spec_from_file_location("daily_check_dispatch_t", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def m():
    return _load()


@pytest.fixture
def routes(m):
    return m.load_routes(ROUTES)


def _args(**kw):
    base = dict(date=DAY_T, routes=None, dry=True, no_feishu=True,
                no_aux=False, no_ledger=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _fake_res(problems=None, warns=None, states=None, verdict=None):
    problems = problems or []
    warns = warns or []
    states = states or {}
    return {
        "checked_at": "2026-11-13T05:30:00+08:00", "target": DAY_T,
        "target_source": "cli",
        "sources": [
            {"id": "ts_daily", "name": "个股日线", "role": "must", "state": states.get("ts_daily", "ok"),
             "max_date": DAY_T, "lag_trading_days": 0, "note": "已到目标日"},
            {"id": "ts_top_inst", "name": "龙虎榜", "role": "soft",
             "state": states.get("ts_top_inst", "warn"), "max_date": DAY_P,
             "lag_trading_days": 1, "note": "能力降级"},
            {"id": "limit_list_d", "name": "涨跌停名单", "role": "t_plus_1",
             "state": states.get("limit_list_d", "pending"), "max_date": DAY_P,
             "lag_trading_days": 1, "note": "按频率属正常未到"},
        ],
        "structure": {"rows": 5573, "nan_close": 0, "bad_high": 0, "bad_low": 0,
                      "pct_extreme": 0, "zero_vol": 0, "pre_close_gap": 6},
        "limit": {"up": 37, "dn": 6, "cross_check": {"day": DAY_T, "is_target_day": True,
                                                      "lag_days": 0, "official_up": 37,
                                                      "mine_up": 37, "official_only": 0,
                                                      "mine_only": 0}},
        "consensus": {"pkg": DAY_O, "asof": DAY_O, "n": 82},
        "problems": problems, "warns": warns,
        "verdict": verdict or ("fail" if problems else "warn" if warns else "ok"),
    }


# ---------------------------------------------------------------------------
def test_resolve_target_uses_calendar_not_subject_max(m):
    cal = [DAY_O, DAY_P, DAY_T, DAY_X]
    assert m.resolve_target(None, cal, DAY_X) == (DAY_T, "trade_cal")
    assert m.resolve_target(DAY_O, cal, DAY_X) == (DAY_O, "cli")
    assert m.resolve_target(None, [], DAY_X)[0] is None
    assert m.resolve_target(None, cal, DAY_O) == (None, "no_prior_session")


def test_classify_by_source_then_keyword_then_fallback(m, routes):
    assert m.classify("龙虎榜：最新可用 " + DAY_P, routes)[0] == "data"
    assert m.classify("涨跌停名单：落后 2 日", routes)[0] == "proj_data"
    assert m.classify("共识池最新包 asof=" + DAY_O, routes)[0] == "invest"
    assert m.classify("3 只最高价低于开/收（价格自相矛盾）", routes)[0] == "quality"
    dept, how = m.classify("某个没登记过的新问题", routes)
    assert dept == routes["fallback"] and how == "fallback"


def test_build_issues_separates_blocker_warning_and_pending(m, routes):
    res = _fake_res(
        problems=["个股日线：应到 " + DAY_T + "，实际停在 " + DAY_O],
        warns=["龙虎榜：最新可用 " + DAY_P, "共识池最新包 asof=" + DAY_O],
    )
    items = m.build_issues(res, routes, None)
    assert len(items) == 3, "pending（T+1 正常）不得生成问题条目"
    assert items[0]["severity"] == m.BLOCKER
    assert all(i["severity"] == m.WARNING for i in items[1:])
    assert items[0]["dept"] == "data" and items[0]["matched_by"].startswith("source:")
    assert items[0]["evidence"]["state"] == "ok", "证据必须来自结构化源状态"
    groups = m.group_by_dept(items, routes)
    assert set(groups) == {"data", "invest"}


def test_aux_chain_is_independent_and_marked_not_downtime(m, routes):
    aux = {"verdict": "zero_collect", "summary": {"n_tables": 7, "zero_collect": 7},
           "sets": [{"table": "x", "verdict": "zero_collect"}]}
    items = m.build_issues(_fake_res(warns=["龙虎榜：落后"]), routes, aux)
    aux_items = [i for i in items if i["matched_by"] == "aux_chain"]
    assert len(aux_items) == 1
    assert aux_items[0]["dept"] == "proj_data"
    assert "不得直接判上游断供" in aux_items[0]["text"]
    assert aux_items[0]["severity"] == m.WARNING


def test_letter_has_sla_reply_and_three_causes_split(m, routes):
    issues = [{"severity": m.BLOCKER, "text": "个股日线：应到未到",
               "dept": "data", "matched_by": "source:ts_daily", "evidence": {}}]
    title, body = m.build_letter("data", issues,
                                 {"target": DAY_T, "exec_day": DAY_X, "verdict_cn": "不通过"},
                                 routes)
    assert "→DATA1" in title and "阻断" in title
    for kw in ("立即处理", "08:10", "回执", "上游给空 / 本地漏跑 / 架构停更",
               "T+1 源落后 1 日属正常"):
        assert kw in body, "信件缺关键约束：" + kw
    assert len(body) <= 4000 and len(title) <= 120


def test_dedupe_key_stable_and_content_sensitive(m, routes):
    a = [{"text": "甲"}, {"text": "乙"}]
    k1 = m.dedupe_key(DAY_T, "data", a)
    k2 = m.dedupe_key(DAY_T, "data", a)
    k3 = m.dedupe_key(DAY_T, "data", [{"text": "甲"}, {"text": "丙"}])
    k4 = m.dedupe_key(DAY_X, "data", a)
    assert k1 == k2 and re.match(r"^[A-Za-z0-9_-]{1,64}$", k1)
    assert k1 != k3 and k1 != k4


def test_run_dry_does_not_emit_and_feishu_only_on_fail(m, monkeypatch):
    sent = []
    monkeypatch.setattr(m.internal_notify, "emit",
                        lambda **kw: sent.append(kw) or {"ok": True, "data": {"id": "x"}})
    monkeypatch.setattr(m, "run_data_health",
                        lambda t, o: {"verdict": "ok", "sets": [], "summary": {}})
    fired = []
    monkeypatch.setattr(m, "notify_people", lambda r, t: fired.append((r, t)) or
                        {"attempted": True, "ok": True})
    monkeypatch.setattr(m.ap, "trade_cal", lambda: [DAY_O, DAY_P, DAY_T, DAY_X])
    monkeypatch.setattr(m.ap, "audit", lambda t: _fake_res(warns=["共识池最新包 asof=" + DAY_O]))

    rec = m.run(_args(dry=True))
    assert sent == [], "--dry 绝不能真投信"
    assert fired == [], "非阻断级不发飞书"
    assert rec["state"] == "CHECKED" and rec["verdict"] == "warn"
    assert rec["letters"] and rec["letters"][0]["action"] == "DRY_RUN"

    # 告警级即便实跑也只进部门工单，不发红告警（告警疲劳比漏报更危险）
    rec_warn = m.run(_args(dry=False, no_feishu=False))
    assert sent and fired == [], "warn 级不得发达人红告警"
    assert rec_warn["state"] == "CHECKED"

    sent.clear()
    fired.clear()
    # 阻断级实跑：派单 + 红告警到人
    monkeypatch.setattr(m.ap, "audit",
                        lambda t: _fake_res(problems=["个股日线：应到 " + DAY_T + "，实际停在 " + DAY_O]))
    rec2 = m.run(_args(dry=False, no_feishu=False))
    assert sent and fired, "阻断级必须派单并红告警到人"
    assert all(x["created_at"] == DAY_T + "T05:30:00+0800" for x in sent), \
        "created_at 必须固定，否则同日重跑会被判 idempotency_conflict"
    assert rec2["letters"][0]["action"] in ("SENT", "REPLAYED")


def test_data_not_ready_is_not_clean(m, monkeypatch):
    monkeypatch.setattr(m.ap, "trade_cal", lambda: [DAY_O, DAY_P, DAY_T])
    monkeypatch.setattr(m.ap, "audit",
                        lambda t: _fake_res(problems=["连个股日线都没有，无法确定目标日"],
                                            verdict="fail"))
    monkeypatch.setattr(m, "run_data_health", lambda t, o: {"verdict": "ok", "sets": []})
    rec = m.run(_args(dry=True))
    assert rec["state"] == "DATA_NOT_READY" and rec["state"] != "CLEAN"
    assert rec["n_issues"] >= 1, "数据未就绪也必须派单，不能静默"

    # 连目标日都定不了：BLOCKED，且不是「无事可做」
    monkeypatch.setattr(m.ap, "trade_cal", lambda: [])
    rec2 = m.run(_args(date=None))
    assert rec2["state"] == "BLOCKED" and rec2["letters"] == []


def test_internal_notify_fixed_created_at_is_idempotent(m, tmp_path):
    """同 key 二次投递必须是 replayed，不能是 idempotency_conflict。"""
    from gateway import inbox_gateway, internal_notify
    old = inbox_gateway._DEFAULT_STORE
    inbox_gateway.set_store(tmp_path)
    try:
        kw = dict(target="DATA1", title="幂等探针", body="同一封催办信",
                  kind="task_request", source="preopen/daily_check",
                  dedupe_key="probe-fixed-key", created_at=DAY_T + "T05:30:00+0800")
        a = internal_notify.emit(**kw)
        b = internal_notify.emit(**kw)
        assert a.get("ok") and b.get("ok")
        assert (b.get("data") or {}).get("replayed") is True
        assert not (b.get("error") or {}).get("code")
        # 不能只靠「两次调用恰好在同一秒」碰巧幂等：落盘内容必须是固定戳
        rec = json.loads((tmp_path / "inbox" / (a["data"]["id"] + ".json"))
                         .read_text(encoding="utf-8"))
        assert rec["payload"]["created_at"] == DAY_T + "T05:30:00+0800"
    finally:
        inbox_gateway._DEFAULT_STORE = old


def test_routes_schema_and_targets(m, routes):
    assert routes["schema"] == "preopen/daily_check_routes/v1"
    assert routes["sla"]["blocker"] and routes["sla"]["warning"]
    for key, cfg in routes["departments"].items():
        assert re.match(r"^[A-Za-z0-9_]{1,32}$", cfg["target"]), key
    org = json.loads((ROOT / "agent_platform" / "registry" / "org.json")
                     .read_text(encoding="utf-8-sig"))
    leads = {d["dept"]: d.get("lead") for d in org["departments"]}
    for key, cfg in routes["departments"].items():
        if key in ("proj_data",):
            assert leads.get(key) == cfg["target"], "映射表席码必须与组织真源一致"
    assert all(v in routes["departments"] for v in routes["by_source"].values())


def test_no_forbidden_public_surface_words_in_new_files(m):
    """新增文件不能自我命中四类公开面关键词（整文件扫描，普通叙述也算）。"""
    spec = importlib.util.spec_from_file_location("cps_t", ROOT / "tools" / "check_public_surface.py")
    cps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cps)
    files = [MOD_PATH, ROUTES,
             ROOT / "tools" / "scheduled" / "daily_check_task.ps1",
             ROOT / "tools" / "scheduled" / "install_daily_check_task.ps1"]
    for f in files:
        t = io.open(f, encoding="utf-8", errors="replace").read()
        hits = {k: len(re.findall(v, t)) for k, v in cps.RULES.items() if re.findall(v, t)}
        assert not hits, "%s 命中 %s" % (f.name, hits)


def test_main_exit_code_is_completion_not_business_verdict(m, monkeypatch):
    """LastTaskResult 问的是「跑完了吗」；业务 warn 不得让计划任务报失败。"""
    monkeypatch.setattr(sys, "argv", ["daily_check_dispatch.py", "--date", DAY_T,
                                      "--dry", "--no-aux", "--no-ledger", "--no-feishu"])
    monkeypatch.setattr(m.ap, "trade_cal", lambda: [DAY_O, DAY_P, DAY_T, DAY_X])
    monkeypatch.setattr(m.ap, "audit",
                        lambda t: _fake_res(warns=["共识池最新包 asof=" + DAY_O]))
    monkeypatch.setattr(m, "run_data_health", lambda t, o: {"verdict": "ok", "sets": []})
    assert m.main() == 0, "业务 warn 时任务仍应报完成"

    # 投递失败必须 fail-visible（同族：数据未就绪被说成无事可做）
    monkeypatch.setattr(sys, "argv", ["daily_check_dispatch.py", "--date", DAY_T,
                                      "--no-aux", "--no-ledger", "--no-feishu"])
    monkeypatch.setattr(m.internal_notify, "emit",
                        lambda **kw: {"ok": False, "error": {"code": "probe_fail"}})
    assert m.main() == 3, "信件没投出去不能报成功"
