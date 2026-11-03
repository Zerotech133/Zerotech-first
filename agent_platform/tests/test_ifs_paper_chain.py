# -*- coding: utf-8 -*-
"""IFS-PAPER：确认→paper 唯一入口与四 ID 链测试（WO-IFS-PAPER-20261103）。

覆盖验收口径：
1) confirmation schema v2（source_decision_id/source_handoff_id/source_watch_plan_id）
   且旧 v1 记录/旧字段/旧调用签名兼容；
2) A 路径执行成功 → paper_book 落账 + trade receipt（trade_id + 四 ID 回写），
   回执含 decision/handoff/watch_plan/confirmation + trade_id，paper_only=true；
3) 唯一 A 路径 = trading_bot_flow + /api/sim/confirm；legacy B/C 入口
   （/api/paper_order、/api/paper_direct_open）显式标 legacy、不接 D1；
4) 幂等：同 confirmation_id / report_id 不重复执行、不重复铸 trade_id；
   kill switch 生效不执行不出回执；风控 blocked / reduced 语义；
5) 只读边界：全部测试用临时数据目录；真实 paper ledger / trading_bot 数据零写入；
   不启动服务、不写 portal_api.py、不改 D1 handoff 文件。
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import trading_bot_flow as flow  # noqa: E402
from agent_platform.paper import entry_policy as ep  # noqa: E402
from agent_platform.paper import paper_book  # noqa: E402
from agent_platform.paper import trade_receipt as tr  # noqa: E402

A_PATH = "trading_bot_flow+/api/sim/confirm"
SAMPLE_D1 = "d1-20261103000000-aaaaaa"
SAMPLE_HANDOFF = "d1h-20261103000000-bbbbbb"
SAMPLE_PLAN = "wp-20261103-0001"


def _load_portal_api_sim_confirm():
    path = REPO / "portal" / "api_sim_confirm.py"
    spec = importlib.util.spec_from_file_location("api_sim_confirm_ifs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_sim_confirm = _load_portal_api_sim_confirm()


# ---------------------------------------------------------------- 夹具
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离数据目录：confirmation / receipt / audit / paper ledger 全在 tmp。"""
    data_dir = str(tmp_path / "bot")
    paths = {
        "data_dir": data_dir,
        "state": str(tmp_path / "state.json"),
        "ledger": str(tmp_path / "ledger.jsonl"),
    }
    monkeypatch.setenv("TRADING_BOT_DIR", data_dir)
    monkeypatch.delenv("PAPER_KILL", raising=False)
    monkeypatch.setattr(paper_book, "DEFAULT_LEDGER", paths["ledger"])
    monkeypatch.setattr(paper_book, "DEFAULT_STATE", paths["state"])
    monkeypatch.setattr(paper_book, "KILL_FILE", str(tmp_path / "KILL"))
    monkeypatch.setattr(tr, "DEFAULT_RECEIPTS",
                        data_dir + "/receipts.jsonl")
    return paths


def _create(env, **overrides):
    kwargs = dict(
        symbol="600519", name="贵州茅台", action="BUY", price=100.0,
        stop=92.0, confidence=0.8, reason="盯盘触发", source="WA1",
        source_report_id="RPT-IFS-1", data_dir=env["data_dir"],
        source_decision_id=SAMPLE_D1, source_handoff_id=SAMPLE_HANDOFF,
        source_watch_plan_id=SAMPLE_PLAN,
    )
    kwargs.update(overrides)
    return flow.create_confirmation(**kwargs)


def _approve(env, confirmation_id, **overrides):
    kwargs = dict(data_dir=env["data_dir"],
                  paper_state_path=env["state"],
                  paper_ledger_path=env["ledger"])
    kwargs.update(overrides)
    return flow.decide(confirmation_id, "approve", by="老田", **kwargs)


def _ledger_rows(env):
    path = Path(env["ledger"])
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _receipts(env):
    return tr.read_receipts(tr.receipts_path(env["data_dir"]))


def _confirmation_file(env, confirmation_id):
    return Path(env["data_dir"]) / "confirmations" / (confirmation_id + ".json")


def _confirmations(env):
    folder = Path(env["data_dir"]) / "confirmations"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.json"))


class FakeHandler:
    def __init__(self, path, body=b"{}"):
        self.path = path
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.sent = None

    def _json(self, obj, code=200):
        self.sent = (code, obj)


def _call_post(path, body):
    handler = FakeHandler(path, body)
    api_sim_confirm.handle_post(handler)
    return handler.sent


# ---------------------------------------------------------------- 1. schema v2
class TestConfirmationV2:
    def test_v2_schema_records_four_id_chain(self, env):
        rec = _create(env)
        assert rec["schema"] == "trading_bot_confirmation/v2"
        assert rec["schema_version"] == 2
        assert rec["source_decision_id"] == SAMPLE_D1
        assert rec["source_handoff_id"] == SAMPLE_HANDOFF
        assert rec["source_watch_plan_id"] == SAMPLE_PLAN
        # 旧字段兼容：source_report_id/profile/audience/human_gate 全保留
        assert rec["source_report_id"] == "RPT-IFS-1"
        assert rec["profile"] == "balanced" and rec["human_gate"] is True
        assert rec["trade_id"] is None
        assert rec["entry_path"] == A_PATH
        assert rec["paper_only"] is True
        assert rec["trading_permitted"] is False and rec["mode"] == "paper"
        # 落盘文件即 v2
        saved = json.loads(_confirmation_file(env, rec["confirmation_id"])
                           .read_text(encoding="utf-8"))
        assert saved["schema"] == "trading_bot_confirmation/v2"

    def test_legacy_v1_record_read_and_migrate(self, env):
        """旧 v1 记录（无 source_*）能读、能执行，并升级为 v2 落盘。"""
        cid = "CFM-V1LEGACY0001"
        v1 = {
            "schema": "trading_bot_confirmation/v1",
            "confirmation_id": cid,
            "status": "pending",
            "audience": "external_user",
            "human_gate": True,
            "symbol": "000858",
            "name": "五粮液",
            "action": "BUY",
            "price": 50.0,
            "stop": None,
            "target": None,
            "confidence": 0.6,
            "profile": "balanced",
            "profile_label": "平衡",
            "reason": "v1 旧记录",
            "source": "WA1",
            "source_report_id": "RPT-V1-1",
            "created_at": "2026-11-01T00:00:00+08:00",
            "decided_at": None,
            "decided_by": None,
            "decision": None,
            "execution": None,
            "trading_permitted": False,
            "mode": "paper",
        }
        folder = Path(env["data_dir"]) / "confirmations"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (cid + ".json")).write_text(
            json.dumps(v1, ensure_ascii=False), encoding="utf-8")
        loaded = flow.get_confirmation(cid, env["data_dir"])
        assert loaded["schema"] == "trading_bot_confirmation/v2"
        assert loaded["source_decision_id"] == ""
        assert loaded["source_report_id"] == "RPT-V1-1"
        out = _approve(env, cid)
        assert out["status"] == "executed"
        saved = json.loads((folder / (cid + ".json")).read_text(encoding="utf-8"))
        assert saved["schema"] == "trading_bot_confirmation/v2"
        assert saved["source_report_id"] == "RPT-V1-1"
        # v1 无上游 ID → 回执四 ID 中上游三项显式 null（不造数）
        receipt = _receipts(env)[0]
        assert receipt["decision_id"] is None
        assert receipt["handoff_id"] is None
        assert receipt["watch_plan_id"] is None
        assert receipt["confirmation_id"] == cid

    def test_legacy_call_signature_compatible(self, env):
        rec = flow.create_confirmation(
            "600519", "茅台", "BUY", 100.0, 92.0, None, 0.8, "盯盘触发",
            "WA1", "RPT-OLD-CALL", "balanced", "external_user",
            env["data_dir"])
        assert rec["confirmation_id"].startswith("CFM-")
        assert rec["source_decision_id"] == ""

    def test_trigger_from_watch_extracts_ids(self, env):
        report = {"symbol": "600519", "name": "茅台", "action": "BUY",
                  "price": 100.0, "report_id": "RPT-WATCH-1",
                  "decision_id": SAMPLE_D1, "handoff_id": SAMPLE_HANDOFF,
                  "watch_plan_id": SAMPLE_PLAN}
        rec = flow.trigger_from_watch(report, data_dir=env["data_dir"])
        assert rec["source_decision_id"] == SAMPLE_D1
        assert rec["source_handoff_id"] == SAMPLE_HANDOFF
        assert rec["source_watch_plan_id"] == SAMPLE_PLAN


# ---------------------------------------------------------------- 2. 执行 + 回执
class TestExecuteAndReceipt:
    def test_approve_executes_and_writes_four_id_receipt(self, env):
        rec = _create(env)
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "executed"
        rows = _ledger_rows(env)
        assert len(rows) == 1 and rows[0]["symbol"] == "600519"
        assert rows[0]["trading_permitted"] is False
        receipt = _receipts(env)
        assert len(receipt) == 1
        item = receipt[0]
        assert item["schema"] == "paper_trade_receipt/v1"
        assert item["trade_id"] == out["trade_id"]
        assert item["trade_id"].startswith("pt-")
        assert item["confirmation_id"] == rec["confirmation_id"]
        assert item["decision_id"] == SAMPLE_D1
        assert item["handoff_id"] == SAMPLE_HANDOFF
        assert item["watch_plan_id"] == SAMPLE_PLAN
        assert item["ids"]["confirmation_id"] == rec["confirmation_id"]
        assert item["ids"]["trade_id"] == item["trade_id"]
        assert set(item["ids"]) == {"decision_id", "handoff_id",
                                    "watch_plan_id", "confirmation_id",
                                    "trade_id"}
        assert item["entry_path"] == A_PATH
        assert item["paper_only"] is True and item["not_a_trade"] is True
        assert item["trading_permitted"] is False
        assert item["ledger_hash"] == rows[0]["hash"]
        assert item["action"] == "buy" and item["verdict"] == "pass"
        # 回执双落：jsonl 日志 + 单笔 json 文件
        per_trade = Path(env["data_dir"]) / "receipts" / (
            out["trade_id"] + ".json")
        assert per_trade.is_file()
        assert json.loads(per_trade.read_text(encoding="utf-8"))[
            "trade_id"] == out["trade_id"]
        # confirmation 回写：trade_id + 五 ID 链
        saved = json.loads(_confirmation_file(env, rec["confirmation_id"])
                           .read_text(encoding="utf-8"))
        assert saved["trade_id"] == out["trade_id"]
        assert saved["ids"] == out["ids"]
        assert saved["execution"]["trade_id"] == out["trade_id"]
        assert saved["execution"]["ids"]["decision_id"] == SAMPLE_D1
        assert saved["paper_only"] is True

    def test_receipt_upstream_ids_null_when_absent(self, env):
        rec = _create(env, source_decision_id="", source_handoff_id="",
                      source_watch_plan_id="")
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "executed"
        item = _receipts(env)[0]
        assert item["decision_id"] is None
        assert item["handoff_id"] is None
        assert item["watch_plan_id"] is None
        assert item["confirmation_id"] == rec["confirmation_id"]
        assert item["trade_id"] == out["trade_id"]

    def test_reject_does_not_execute_or_receipt(self, env):
        rec = _create(env)
        out = flow.decide(rec["confirmation_id"], "reject", by="老田",
                          data_dir=env["data_dir"])
        assert out["status"] == "rejected" and out["trade_id"] is None
        assert not Path(env["ledger"]).exists()
        assert _receipts(env) == []

    def test_price_missing_blocked_without_receipt(self, env):
        rec = _create(env, price=None)
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "blocked" and out["trade_id"] is None
        assert not Path(env["ledger"]).exists()
        assert _receipts(env) == []

    def test_four_ids_helpers(self, env):
        rec = _create(env)
        four = flow.four_ids(rec)
        assert four == {"decision_id": SAMPLE_D1,
                        "handoff_id": SAMPLE_HANDOFF,
                        "watch_plan_id": SAMPLE_PLAN,
                        "confirmation_id": rec["confirmation_id"]}
        chain = flow.id_chain(rec)
        assert chain["trade_id"] is None and set(chain) == set(four) | {"trade_id"}
        assert tr.four_ids({"decision_id": "D", "handoff_id": "H",
                            "watch_plan_id": "W", "confirmation_id": "C"}) == {
            "decision_id": "D", "handoff_id": "H",
            "watch_plan_id": "W", "confirmation_id": "C"}


# ---------------------------------------------------------------- 3. 幂等
class TestIdempotency:
    def test_repeat_approve_no_duplicate_execution(self, env):
        rec = _create(env)
        first = _approve(env, rec["confirmation_id"])
        second = _approve(env, rec["confirmation_id"])
        assert first["trade_id"] == second["trade_id"]
        assert len(_ledger_rows(env)) == 1
        assert len(_receipts(env)) == 1

    def test_duplicate_report_id_returns_same_confirmation(self, env):
        a = _create(env, source_report_id="RPT-DUP")
        b = _create(env, source_report_id="RPT-DUP")
        assert a["confirmation_id"] == b["confirmation_id"]
        assert len(_confirmations(env)) == 1
        _approve(env, a["confirmation_id"])
        assert len(_ledger_rows(env)) == 1
        assert len(_receipts(env)) == 1

    def test_crash_recovery_reconciles_existing_receipt(self, env):
        """模拟「账本+回执已落、confirmation 未保存」崩溃：重放不重复执行。"""
        rec = _create(env)
        first = _approve(env, rec["confirmation_id"])
        cid = rec["confirmation_id"]
        path = _confirmation_file(env, cid)
        broken = json.loads(path.read_text(encoding="utf-8"))
        broken.update({"status": "pending", "decision": None,
                       "decided_by": None, "decided_at": None,
                       "trade_id": None, "ids": {}, "execution": None})
        path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        again = _approve(env, cid)
        assert again["status"] == "executed"
        assert again["trade_id"] == first["trade_id"]
        assert len(_ledger_rows(env)) == 1
        assert len(_receipts(env)) == 1

    def test_source_id_conflict_fails_closed(self, env):
        rec = _create(env)
        with pytest.raises(ValueError, match="source id 冲突"):
            _approve(env, rec["confirmation_id"],
                     source_decision_id="d1-OTHER-000000")
        loaded = flow.get_confirmation(rec["confirmation_id"], env["data_dir"])
        assert loaded["status"] == "pending"
        assert not Path(env["ledger"]).exists()


# ---------------------------------------------------------------- 4. kill / 风控
class TestKillAndRiskGate:
    def test_kill_switch_env_blocks_execution(self, env, monkeypatch):
        rec = _create(env)
        monkeypatch.setenv("PAPER_KILL", "1")
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "killed" and out["trade_id"] is None
        assert not Path(env["ledger"]).exists()
        assert _receipts(env) == []

    def test_kill_switch_file_blocks_execution(self, env):
        rec = _create(env)
        Path(paper_book.KILL_FILE).write_text("kill", encoding="utf-8")
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "killed"
        assert not Path(env["ledger"]).exists()
        assert _receipts(env) == []

    def test_risk_gate_blocked_no_receipt(self, env):
        Path(env["state"]).write_text(json.dumps({
            "positions": {"600519": {"weight": 0.10}},
            "cash_weight": 0.9, "nav": 1.0,
        }), encoding="utf-8")
        rec = _create(env)  # BUY 600519，单票已满 → block
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "blocked"
        assert out["gate"]["verdict"] == "block"
        assert out["trade_id"] is None
        assert not Path(env["ledger"]).exists()
        assert _receipts(env) == []

    def test_risk_gate_reduced_executes_and_receipt_records_verdict(self, env):
        rec = _create(env, confidence=0.3)  # < min_confidence 0.5 → 仓位减半
        out = _approve(env, rec["confirmation_id"])
        assert out["status"] == "executed"
        assert out["gate"]["verdict"] == "reduce"
        assert out["gate"]["target_weight"] == pytest.approx(0.025)
        rows = _ledger_rows(env)
        assert len(rows) == 1
        assert rows[0]["target_weight"] == pytest.approx(0.025)
        receipt = _receipts(env)[0]
        assert receipt["verdict"] == "reduce"
        assert receipt["target_weight"] == pytest.approx(0.025)
        assert receipt["confirmation_id"] == rec["confirmation_id"]


# ---------------------------------------------------------------- 5. 入口策略
class TestEntryPolicy:
    def test_unique_a_path_and_legacy_entries(self):
        assert ep.UNIQUE_A_PATH == A_PATH
        a = ep.entry_for("/api/sim/confirm")
        assert a["path"] == "A" and a["legacy"] is False and a["d1_chain"] is True
        assert tuple(a["id_chain"]) == ("decision_id", "handoff_id",
                                        "watch_plan_id", "confirmation_id",
                                        "trade_id")
        for legacy_path, expected in (("/api/paper_order", "B"),
                                      ("/api/paper_direct_open", "C")):
            entry = ep.entry_for(legacy_path)
            assert entry["path"] == expected
            assert entry["legacy"] is True and entry["d1_chain"] is False
            assert entry["creates_confirmation"] is False
            assert entry["id_chain"] == ()
            assert entry["replacement"] == A_PATH
            assert ep.is_legacy(legacy_path) is True
            assert ep.is_unique_a_path(legacy_path) is False
        assert ep.entry_for("/api/unknown") is None
        assert ep.is_legacy("/api/unknown") is False
        assert ep.describe()["unique_a_path"] == A_PATH
        assert ep.describe()["paper_only"] is True

    def test_annotate_and_headers(self):
        payload = {"ok": True, "count": 1}
        legacy = ep.annotate(payload, "/api/paper_order")
        assert payload == {"ok": True, "count": 1}  # 不改入参
        assert legacy["entry"]["legacy"] is True
        assert legacy["legacy"] is True
        assert legacy["replacement"] == A_PATH
        assert "legacy 直连" in legacy["entry_note"]
        a_payload = ep.annotate(payload, "/api/sim/confirm")
        assert a_payload["entry"]["legacy"] is False
        assert a_payload["legacy"] is False
        assert ep.entry_headers("/api/paper_order") == {
            ep.ENTRY_HEADER: ep.ENTRY_HEADER_LEGACY}
        assert ep.entry_headers("/api/sim/confirm") == {
            ep.ENTRY_HEADER: ep.ENTRY_HEADER_A}
        assert ep.entry_headers("/api/unknown") == {}
        assert ep.legacy_marker("/api/sim/confirm") is None
        assert ep.legacy_marker("/api/paper_order")["entry"] == "legacy"

    def test_legacy_entries_do_not_enter_d1_chain(self):
        for path in ep.LEGACY_ENTRIES:
            entry = ep.entry_for(path)
            marker = ep.legacy_marker(path)
            assert marker["d1_chain"] is False and marker["id_chain"] == []
            assert marker["replacement"] == A_PATH
            # legacy 入口不经过 trading_bot_flow 执行链（只指向替换入口）
            assert "trading_bot_flow" not in entry["module"]
            assert "trading_bot_flow" not in entry["execute"]


# ---------------------------------------------------------------- 6. API v2
class TestApiSimConfirmV2:
    def test_post_approve_returns_trade_and_four_ids(self, env):
        rec = _create(env)
        body = json.dumps({"confirmation_id": rec["confirmation_id"],
                           "action": "approve", "by": "老田"}).encode("utf-8")
        code, payload = _call_post("/api/sim/confirm", body)
        assert code == 200 and payload["ok"] is True
        assert payload["mode"] == "paper" and payload["paper_only"] is True
        assert payload["trading_permitted"] is False
        assert payload["entry"]["path"] == "A"
        assert payload["legacy"] is False
        assert payload["trade_id"].startswith("pt-")
        assert payload["ids"]["decision_id"] == SAMPLE_D1
        assert payload["ids"]["handoff_id"] == SAMPLE_HANDOFF
        assert payload["ids"]["watch_plan_id"] == SAMPLE_PLAN
        assert payload["ids"]["confirmation_id"] == rec["confirmation_id"]
        assert payload["item"]["status"] == "executed"
        assert len(_ledger_rows(env)) == 1 and len(_receipts(env)) == 1

    def test_post_can_fill_source_ids_at_approve(self, env):
        rec = _create(env, source_decision_id="", source_handoff_id="",
                      source_watch_plan_id="")
        body = json.dumps({
            "confirmation_id": rec["confirmation_id"], "action": "approve",
            "source_decision_id": SAMPLE_D1,
            "source_handoff_id": SAMPLE_HANDOFF,
            "source_watch_plan_id": SAMPLE_PLAN,
        }).encode("utf-8")
        code, payload = _call_post("/api/sim/confirm", body)
        assert code == 200
        saved = flow.get_confirmation(rec["confirmation_id"], env["data_dir"])
        assert saved["source_decision_id"] == SAMPLE_D1
        assert saved["source_handoff_id"] == SAMPLE_HANDOFF
        assert saved["source_watch_plan_id"] == SAMPLE_PLAN
        assert payload["ids"]["decision_id"] == SAMPLE_D1

    def test_post_reject_no_trade_id(self, env):
        rec = _create(env)
        body = json.dumps({"confirmation_id": rec["confirmation_id"],
                           "action": "reject"}).encode("utf-8")
        code, payload = _call_post("/api/sim/confirm", body)
        assert code == 200 and payload["ok"] is True
        assert payload["executed"] is False and payload["trade_id"] is None
        assert _ledger_rows(env) == []

    @pytest.mark.parametrize("body,expected", [
        (b"not-json", 400),
        (json.dumps({"action": "approve"}).encode(), 400),
        (json.dumps({"confirmation_id": "CFM-X", "action": "hold"}).encode(), 400),
        (json.dumps({"confirmation_id": "CFM-NOT-EXIST",
                     "action": "approve"}).encode(), 400),
    ])
    def test_post_bad_requests(self, env, body, expected):
        code, payload = _call_post("/api/sim/confirm", body)
        assert code == expected and payload["ok"] is False
        assert payload["entry"]["path"] == "A"

    def test_post_unknown_path_404(self, env):
        code, payload = _call_post("/api/other", b"{}")
        assert code == 404 and payload["error"] == "not_found"

    def test_list_payload_v2(self, env):
        executed = _create(env, source_report_id="RPT-L1")
        _approve(env, executed["confirmation_id"])
        pending = _create(env, source_report_id="RPT-L2",
                          source_decision_id="")
        payload = api_sim_confirm.list_payload("pending")
        assert payload["ok"] is True and payload["count"] == 1
        assert payload["items"][0]["confirmation_id"] == pending["confirmation_id"]
        assert payload["items"][0]["entry_path"] == A_PATH
        assert payload["entry"]["path"] == "A"
        assert payload["receipts"]["total"] == 1
        done = api_sim_confirm.list_payload("executed")
        assert done["count"] == 1
        assert done["items"][0]["ids"]["trade_id"].startswith("pt-")

    def test_handle_get_list(self, env):
        rec = _create(env)
        _approve(env, rec["confirmation_id"])
        handler = FakeHandler("/api/sim/confirmations?status=executed")
        api_sim_confirm.handle_get(handler, "status=executed")
        code, payload = handler.sent
        assert code == 200 and payload["count"] == 1
        handler2 = FakeHandler("/api/sim/other")
        api_sim_confirm.handle_get(handler2, "")
        assert handler2.sent[0] == 404

    def test_receipts_payload_readonly(self, env):
        rec = _create(env)
        out = _approve(env, rec["confirmation_id"])
        payload = api_sim_confirm.receipts_payload(out["trade_id"])
        assert payload["ok"] is True and payload["readonly"] is True
        assert payload["receipt"]["trade_id"] == out["trade_id"]
        missing = api_sim_confirm.receipts_payload("pt-does-not-exist")
        assert missing["ok"] is False and missing["receipt"] is None

    def test_legacy_annotate_helpers(self):
        assert api_sim_confirm.legacy_marker("/api/paper_order")["legacy"] is True
        assert api_sim_confirm.legacy_marker("/api/sim/confirm") is None
        annotated = api_sim_confirm.annotate_legacy(
            {"ok": True}, "/api/paper_direct_open")
        assert annotated["legacy"] is True
        assert annotated["entry"]["path"] == "C"
        assert api_sim_confirm.entry_headers("/api/paper_order") == {
            ep.ENTRY_HEADER: ep.ENTRY_HEADER_LEGACY}
        assert api_sim_confirm.describe()["entry"]["unique_a_path"] == A_PATH


# ---------------------------------------------------------------- 7. 隔离/只读
REAL_PATHS = (
    REPO / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl",
    REPO / "agent_platform" / "data" / "paper" / "state.json",
    REPO / "agent_platform" / "data" / "paper" / "KILL",
    REPO / "agent_platform" / "data" / "trading_bot",
    REPO / "agent_platform" / "data" / "d1_handoffs.jsonl",
)


def _real_state():
    state = {}
    for path in REAL_PATHS:
        if path.is_file():
            state[str(path)] = ("file", path.read_bytes())
        elif path.is_dir():
            state[str(path)] = ("dir", sorted(
                (p.name, p.stat().st_size) for p in path.rglob("*")
                if p.is_file()))
        else:
            state[str(path)] = ("absent", None)
    return state


class TestIsolationAndReadonly:
    def test_flow_does_not_touch_real_data(self, env):
        before = _real_state()
        rec = _create(env)
        _approve(env, rec["confirmation_id"])
        body = json.dumps({"confirmation_id": rec["confirmation_id"],
                           "action": "approve"}).encode("utf-8")
        _call_post("/api/sim/confirm", body)
        api_sim_confirm.list_payload("executed")
        assert _real_state() == before, "不得写真实 paper ledger / trading_bot 数据"

    def test_receipt_module_is_append_only_and_idempotent(self, env):
        rec_a = _create(env, source_report_id="RPT-RA")
        out_a = _approve(env, rec_a["confirmation_id"])
        receipt_a = _receipts(env)[0]
        again = tr.write_receipt(dict(receipt_a),
                                 path=tr.receipts_path(env["data_dir"]))
        assert again["duplicate"] is True
        assert again["receipt"]["trade_id"] == out_a["trade_id"]
        assert len(tr.read_receipts(tr.receipts_path(env["data_dir"]))) == 1
        rec_b = _create(env, symbol="000858", source_report_id="RPT-RB",
                        source_decision_id="")
        _approve(env, rec_b["confirmation_id"])
        assert len(tr.read_receipts(tr.receipts_path(env["data_dir"]))) == 2
        summary = tr.summary(tr.receipts_path(env["data_dir"]))
        assert summary["total"] == 2 and summary["paper_only"] is True

    def test_new_modules_no_forbidden_imports_or_service(self):
        import ast

        checks = {
            REPO / "agent_platform" / "paper" / "entry_policy.py":
                {"__future__"},
            REPO / "agent_platform" / "paper" / "trade_receipt.py":
                {"__future__", "io", "json", "os", "secrets", "datetime",
                 "pathlib", "sys", "", "agent_platform.paper"},
            REPO / "portal" / "api_sim_confirm.py":
                {"__future__", "json", "sys", "pathlib",
                 "agent_platform", "agent_platform.paper",
                 "agent_platform.paper.entry_policy",
                 "agent_platform.paper.trade_receipt",
                 "urllib.parse"},
        }
        for path, allowed in checks.items():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
            assert imported <= allowed, (path.name, imported - allowed)
            for banned in ("subprocess", "http.server", "socket", "requests"):
                assert banned not in source, (path.name, banned)
            for token in ("serve_forever", "HTTPServer", "listen(", "bind(",
                          "import portal_api", "from portal_api"):
                assert token not in source, (path.name, token)

    def test_trading_bot_flow_import_boundary(self):
        import ast

        source = (REPO / "agent_platform" / "trading_bot_flow.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
        assert "uuid" in imported and "json" in imported
        for banned in ("subprocess", "socket", "requests", "portal_api",
                       "d1_handoff"):
            assert all(banned not in item for item in imported), (banned, imported)
        assert "trade_receipt" in source and "entry_policy" in source
        assert "portal_api" not in source


# ---------------------------------------------------------------- 8. 旧回归兼容
def test_existing_flow_behaviour_still_works(env):
    """旧 WO-BOT-CONFIRM 主流程不回归：pending→approve→executed→replay 幂等。"""
    rec = _create(env, source_report_id="")
    assert rec["status"] == "pending" and rec["human_gate"] is True
    out = _approve(env, rec["confirmation_id"])
    assert out["status"] == "executed"
    again = _approve(env, rec["confirmation_id"])
    assert again["status"] == "executed"
    assert len(_ledger_rows(env)) == 1
    assert flow.list_confirmations("executed", data_dir=env["data_dir"])[0][
        "confirmation_id"] == rec["confirmation_id"]
