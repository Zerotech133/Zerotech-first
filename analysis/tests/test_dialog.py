# -*- coding: utf-8 -*-
"""analysis/dialog.py 离线单元测试（工单 K-DIALOG-C1-20261107）。

隔离原则：
  * 每个用例用 tmp_path 的临时 SQLite，不读写生产 analysis_work；
  * 内部分析网关用 FakeClient 注入（不发起 HTTP）；
  * 覆盖三条主链路：多轮上下文、反问生成与关闭、动作执行（watch/compare）。
"""
import os
import sys

import pytest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if WT not in sys.path:
    sys.path.insert(0, WT)

from analysis import dialog  # noqa: E402

CODE_A = "600337.SH"
CODE_B = "605258.SH"
CODE_C = "688023.SH"

REPORT_MD = """# 600337.SH 个股分析报告

## 一、结论

事件观察（91.7%）：基本面偏弱，事件驱动为主。

## 二、风险清单

- 退市风险：净资产为负，需跟踪年报审计意见。
- 负债率 87.5%，短期偿债压力较大。
"""

EVENTS = [
    {"event_id": "e1", "date": "2026-11-03", "category": "回购",
     "title": "股份回购进展公告", "reviewed": True},
    {"event_id": "e2", "date": "2026-11-01", "category": "重整",
     "title": "重整事项进展", "reviewed": True},
]


class FakeClient:
    """替换 AnalysisClient：submit 后状态可手动推进。"""

    def __init__(self):
        self.state = "analyzing"
        self.submit_calls = []
        self.job_calls = 0
        self.report_calls = 0

    def health(self):
        return {"ok": True, "db": True}

    def submit(self, code, llm=False):
        self.submit_calls.append((code, llm))
        self.state = "analyzing"
        return {"ok": True, "job_id": "job-1"}

    def job(self, job_id):
        self.job_calls += 1
        if self.state == "analyzing":
            return {"ok": True, "state": "analyzing", "job": {}}
        return {"ok": True, "state": "done", "job": {
            "run_id": "600337SH-20261105-120000",
            "name": "ST美克", "rating": "事件观察", "score": 27.5,
            "coverage": 91.7, "as_of": "2026-11-05",
            "stock_code": CODE_A,
        }}

    def report(self, run_id):
        self.report_calls += 1
        return {"ok": True, "run_id": run_id, "content": REPORT_MD}

    def events(self, code, limit=8, status="all"):
        return {"ok": True, "events": EVENTS, "stats": {"total": 2}}


@pytest.fixture()
def svc(tmp_path):
    store = dialog.Store(path=str(tmp_path / "dialog.db"))
    client = FakeClient()
    service = dialog.DialogService(customer_id="tester", store=store, client=client)
    yield service, client
    store.close()


def _ready(service, client, start_text, sid=None):
    """启动/刷新分析到完成状态（分析快照放在 submit 之后、刷新之前）。"""
    res = service.handle_message(start_text, session_id=sid)
    assert res["ok"], res
    sid = res["session"]["id"]
    client.state = "done"
    res = service.handle_message("分析好了吗", session_id=sid)
    assert res["ok"], res
    return sid, res


def test_normalize_code_variants():
    assert dialog.normalize_code("600337.SH") == "600337.SH"
    assert dialog.normalize_code("600337") == "600337.SH"
    assert dialog.normalize_code("000001") == "000001.SZ"
    assert dialog.normalize_code("300308") == "300308.SZ"
    assert dialog.normalize_code("688023") == "688023.SH"
    assert dialog.normalize_code("bj920023") == "920023.BJ"
    assert dialog.normalize_code("我觉得 600337 可以看看") == "600337.SH"
    assert dialog.normalize_code("你好") is None


def test_multi_turn_context_via_get_session(svc):
    """链路 1：提交 → 轮询完成 → 总结 → 反问 → 按选项深挖（上下文延续）。"""
    service, client = svc
    res = service.handle_message("分析 600337.SH")
    assert res["ok"] and res["session"]["state"] == "analyzing"
    sid = res["session"]["id"]
    assert client.submit_calls == [(CODE_A, False)]

    # 分析未完成：get_session 仍是 analyzing
    poll = service.get_session(sid)
    assert poll["ok"] and poll["session"]["state"] == "analyzing"

    # 完成：get_session 自动刷新并补发「总结 + 反问」
    client.state = "done"
    poll = service.get_session(sid)
    assert poll["ok"] and poll["session"]["state"] == "ready"
    intents = [(t.get("payload") or {}).get("intent") for t in poll["turns"]]
    assert "analysis_done" in intents
    assert "ask_dimension" in intents
    q = poll["question"]
    assert q and q["kind"] == "dimension"
    keys = [o["key"] for o in q["options"]]
    assert keys == ["risk", "valuation", "events", "flow", "compare", "watch"]

    # 选中「风险」→ 深挖报告风险节选；上下文（会话/代码/run_id）保持
    res = service.handle_answer(sid, q["id"], option_key="risk")
    assert res["ok"], res
    assert (res["turns"][-1]["payload"] or {}).get("intent") == "deepdive"
    assert "负债率 87.5%" in res["turns"][-1]["content"]
    sess = service.get_session(sid)["session"]
    assert sess["stock_code"] == CODE_A
    assert sess["run_id"] == "600337SH-20261105-120000"
    assert sess["depth"] == 1

    # 后续普通消息仍落在同一会话（不新建）
    res = service.handle_message("这只票估值贵吗", session_id=sid)
    assert res["ok"]
    assert res["session"]["id"] == sid


def test_followup_generation_and_disable(svc):
    """链路 2：反问生成 → 「不用问了」关闭 → 新分析不再反问 → 可恢复。"""
    service, client = svc
    sid1, _ = _ready(service, client, "600337.SH")
    assert service.get_session(sid1)["question"] is not None

    res = service.handle_message("不用问了", session_id=sid1)
    assert res["ok"]
    assert (res["turns"][-1]["payload"] or {}).get("intent") == "stop_followup"
    assert service.store.get_profile("tester")["followup_enabled"] is False

    # 关闭后新分析完成：不再生成反问
    client.state = "analyzing"
    sid2, _ = _ready(service, client, "605258.SH")
    poll = service.get_session(sid2)
    assert poll["question"] is None
    intents = [(t.get("payload") or {}).get("intent") for t in poll["turns"]]
    assert "ask_dimension" not in intents

    # 恢复反问 → 再分析 → 反问回来
    res = service.handle_message("开启反问", session_id=sid2)
    assert (res["turns"][-1]["payload"] or {}).get("intent") == "enable_followup"
    assert service.store.get_profile("tester")["followup_enabled"] is True
    client.state = "analyzing"
    sid3, _ = _ready(service, client, "688023.SH")
    assert service.get_session(sid3)["question"] is not None


def test_action_execution_watch_and_compare(svc):
    """链路 3：watch（订阅+动作）与 compare（C1 登记、待 C2）两个动作闭环。"""
    service, client = svc
    sid, _ = _ready(service, client, "600337.SH")
    q = service.get_session(sid)["question"]

    # 深挖风险 → 产生 watch_confirm 反问
    res = service.handle_answer(sid, q["id"], option_key="risk")
    wq = res["question"]
    assert wq and wq["kind"] == "watch_confirm"
    assert [o["key"] for o in wq["options"]] == ["watch_yes", "no"]

    # 回答「帮我盯着」→ 订阅建立 + watch.add 动作
    res = service.handle_answer(sid, wq["id"], option_key="watch_yes")
    assert res["ok"], res
    assert (res["turns"][-1]["payload"] or {}).get("intent") == "watch_added"
    subs = service.store.list_subscriptions("tester")
    assert [s["code"] for s in subs] == [CODE_A]
    actions = [dict(r) for r in service.store.con.execute(
        "SELECT * FROM actions WHERE session_id=?", (sid,)).fetchall()]
    assert any(a["type"] == "watch.add" for a in actions)

    # 再分析另一只票，选「对比」→ 反问第二只代码 → 回复代码 → C1 登记 pending_c2
    client.state = "analyzing"
    sid2, _ = _ready(service, client, "605258.SH")
    q2 = service.get_session(sid2)["question"]
    res = service.handle_answer(sid2, q2["id"], option_key="compare")
    cq = res["question"]
    assert cq and cq["kind"] == "compare_ask" and cq["options"] == []

    res = service.handle_message("000001.SZ", session_id=sid2)
    assert res["ok"], res
    payload = res["turns"][-1]["payload"] or {}
    assert payload.get("intent") == "compare_pending"
    assert payload.get("a") == CODE_B and payload.get("b") == "000001.SZ"
    assert service.store.pending_question(sid2) is None


def test_store_persists_profile_and_session(tmp_path):
    path = str(tmp_path / "dialog.db")
    s1 = dialog.Store(path=path)
    sid = s1.create_session("tester", "pc", "分析 600337.SH", stock_code=CODE_A)["id"]
    s1.set_profile("tester", followup_enabled=False)
    s1.close()

    s2 = dialog.Store(path=path)
    assert s2.get_session(sid)["stock_code"] == CODE_A
    assert s2.get_profile("tester")["followup_enabled"] is False
    s2.close()
