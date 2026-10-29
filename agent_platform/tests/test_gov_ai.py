"""GOVAI-1 地基测试（WO-LEGION-GOVAI1-20261026）。

全离线：假 transport/假客户端，不联网、不读真实密钥、不写生产目录。
覆盖：输入构建、数字/名称接地、字段白名单、id 确定性、Z5、run 成功/失败/
跳过/强制/dry-run、GOVAI=off、case_draft 证据包、健康卡输入、金标回放。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
for _p in (str(_PLATFORM), str(_PLATFORM / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gov_ai as G  # noqa: E402
from engine import deepseek as DS  # noqa: E402

_PAYLOAD = {
    "as_of": "2026-10-26T09:56:00+08:00",
    "hours": 24.0,
    "counts": {
        "total_scanned": 46,
        "ok": 38,
        "failed": 0,
        "upstream_hold": 1,
        "disabled": 3,
        "never_run": 2,
    },
    "failures": [],
    "upstream": [
        {
            "name": "AlertWatch",
            "last_result": 3,
            "exit_semantics": "上游不可信",
            "last_run": "2026-10-26 09:45:00",
        }
    ],
    "cancelled_tasks": [
        {
            "name": "AgentMondayCheckpoint",
            "last_result": 3221225786,
            "exit_semantics": "被取消",
            "last_run": "2026-10-19 17:25:00",
        }
    ],
    "terminated_tasks": [
        {
            "name": "NoticeBackfill",
            "last_result": 267014,
            "exit_semantics": "被终止",
            "last_run": "2026-10-25 06:45:01",
        }
    ],
    "window": {
        "window_hours": 24.0,
        "new_failures": [],
        "persisting_failures": [],
        "fixed_in_window": ["HermesBriefPush"],
    },
    "known_tasks": [
        "AlertWatch",
        "AgentMondayCheckpoint",
        "NoticeBackfill",
        "HermesBriefPush",
    ],
}

_VALID_ITEM = {
    "title": "AgentMondayCheckpoint 取消被误计",
    "hypothesis": "3221225786 属任务被取消/强杀，被当失败；应与 267014 同列非失败",
    "evidence": ["task:AgentMondayCheckpoint", "exit:3221225786", "exit:267014"],
    "confidence": 0.8,
    "suggested_check": "检查 task_exit_audit 的 NON_FAILURE_RC 是否覆盖该码",
}


class FakeClient:
    model = "fake-model"

    def __init__(self, items=None, content=None, error=None):
        self.items = [_VALID_ITEM] if items is None else items
        self.content = content
        self.error = error
        self.calls = []

    def chat_full(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if self.error:
            raise self.error
        body = (
            self.content
            if self.content is not None
            else json.dumps({"items": self.items}, ensure_ascii=False)
        )
        return {
            "content": body,
            "usage": {"total_tokens": 123},
            "model": self.model,
            "finish_reason": "stop",
        }


# ---------------------------------------------------------------- 校验器


def test_validate_accepts_grounded_item():
    r = G.validate_items("anomaly_digest", [dict(_VALID_ITEM)], _PAYLOAD)
    assert r["ok"] is True and r["schema_ok"] and r["grounding_ok"] and r["z5_ok"]
    assert r["items"][0]["id"].startswith("det-")


def test_validate_rejects_fabricated_task_name():
    item = dict(_VALID_ITEM)
    item["evidence"] = ["task:NotARealTask"]
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["ok"] is False and r["grounding_ok"] is False
    assert any("任务名不在输入中" in n for n in r["notes"])


def test_validate_rejects_ungrounded_number():
    item = dict(_VALID_ITEM)
    item["hypothesis"] = "疑似有 999 个任务失败"
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["ok"] is False and r["grounding_ok"] is False
    assert any("数字未接地" in n for n in r["notes"])


def test_validate_rejects_too_many_items():
    items = [dict(_VALID_ITEM) for _ in range(G.MAX_ITEMS + 1)]
    r = G.validate_items("anomaly_digest", items, _PAYLOAD)
    assert r["schema_ok"] is False


def test_validate_rejects_no_evidence_with_high_confidence():
    item = dict(_VALID_ITEM)
    item["evidence"] = []
    item["confidence"] = 0.9
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["grounding_ok"] is False


def test_validate_rejects_missing_required_field():
    item = dict(_VALID_ITEM)
    item.pop("hypothesis")
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["schema_ok"] is False


def test_validate_id_is_deterministic_and_content_bound():
    a = G.validate_items("anomaly_digest", [dict(_VALID_ITEM)], _PAYLOAD)["items"][0][
        "id"
    ]
    b = G.validate_items("anomaly_digest", [dict(_VALID_ITEM)], _PAYLOAD)["items"][0][
        "id"
    ]
    other = dict(_VALID_ITEM)
    other["title"] = "另一个标题"
    c = G.validate_items("anomaly_digest", [other], _PAYLOAD)["items"][0]["id"]
    assert a == b and a != c


def test_z5_blocks_leak_payload():
    ok, notes = G._z5_ok({"items": [], "verdict": "BUY"})
    assert ok is False and notes


# ---------------------------------------------------------------- 工具函数


def test_strip_fences_and_num_tokens():
    assert G._strip_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert G._num_tokens("4.0 和 24h 和 3221225786") == {"4", "24", "3221225786"}


def test_input_digest_stable():
    assert G.input_digest(_PAYLOAD) == G.input_digest(dict(_PAYLOAD))
    assert G.input_digest(_PAYLOAD) != G.input_digest({**_PAYLOAD, "hours": 12.0})


# ---------------------------------------------------------------- 输入构建


def test_build_ledger_input(monkeypatch):
    monkeypatch.setattr(
        G.ta,
        "summary_only",
        lambda: {
            "generated_at": "2026-10-26T09:56:00+08:00",
            "total_scanned": 46,
            "ok": 38,
            "failed": 0,
            "upstream_hold": 1,
            "disabled": 3,
            "never_run": 2,
            "failures": [],
            "upstream": [{"name": "AlertWatch", "last_result": 3}],
            "cancelled_tasks": [],
            "terminated_tasks": [],
            "launcher_refused_tasks": [],
            "running_tasks": [],
        },
    )
    monkeypatch.setattr(
        G.ta,
        "window_diff",
        lambda hours=24.0: {
            "window_hours": hours,
            "new_failures": [],
            "persisting_failures": [],
            "fixed_in_window": ["HermesBriefPush"],
        },
    )
    payload = G.build_ledger_input(hours=12)
    assert payload["hours"] == 12
    assert "AlertWatch" in payload["known_tasks"]
    assert "HermesBriefPush" in payload["known_tasks"]


def test_build_ledger_input_error_raises(monkeypatch):
    monkeypatch.setattr(G.ta, "summary_only", lambda: {"error": "no summary"})
    with pytest.raises(G.GovAiError):
        G.build_ledger_input()


def test_build_health_input_parses_checks_structure(monkeypatch):
    """健康卡真实结构是 checks[{id,level,detail}]（GOVAI-1 首跑曾误读 items 导致空输入）。"""
    card = {
        "generated_at": "2026-10-26T11:00:00+08:00",
        "status": "red",
        "exit_code": 1,
        "checks": [
            {"id": "backup.catchup", "level": "red", "detail": "30.4h"},
            {"id": "ops.alert_watch_liveness", "level": "green", "detail": ""},
        ],
    }

    class FakeProc:
        returncode = 1
        stdout = json.dumps(card, ensure_ascii=False)
        stderr = ""

    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: FakeProc())
    payload = G.build_health_input()
    assert payload["counts"]["red"] == 1 and payload["counts"]["green"] == 1
    assert payload["status"] == "red" and payload["exit_code"] == 1
    assert "backup.catchup" in payload["known_items"]


def test_build_health_input_items_fallback(monkeypatch):
    card = {"items": [{"id": "x", "state": "yellow", "detail": ""}]}

    class FakeProc:
        returncode = 0
        stdout = json.dumps(card)
        stderr = ""

    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: FakeProc())
    payload = G.build_health_input()
    assert payload["counts"]["yellow"] == 1 and payload["known_items"] == ["x"]


def test_priority_requires_evidence():
    payload = {
        "items": [{"id": "health.a", "state": "red"}],
        "known_items": ["health.a"],
        "counts": {"red": 1},
    }
    item = {
        "title": "x",
        "why": "health.a 为红",
        "suggested_action": "查",
        "confidence": 0.6,
    }
    r = G.validate_items("priority_advice", [item], payload)
    assert r["schema_ok"] is False


def test_priority_accepts_health_evidence():
    payload = {
        "items": [{"id": "health.a", "state": "red"}],
        "known_items": ["health.a"],
        "counts": {"red": 1},
    }
    item = {
        "title": "x",
        "why": "health.a 为红",
        "suggested_action": "查",
        "evidence": ["health:health.a"],
        "confidence": 0.6,
    }
    r = G.validate_items("priority_advice", [item], payload)
    assert r["ok"] is True and r["items"][0]["evidence"] == ["health:health.a"]


def test_build_case_input_requires_schema(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    with pytest.raises(G.GovAiError):
        G.build_case_input(str(p))


def test_build_case_input_ok(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(
        json.dumps(
            {
                "schema": "governance_case_bundle/v1",
                "title": "t",
                "timeline": [{"id": "E1", "what": "x"}],
            }
        ),
        encoding="utf-8",
    )
    payload = G.build_case_input(str(p))
    assert payload["known_bundle_ids"] == ["E1"]


# ---------------------------------------------------------------- run 编排


def _patch_payload(monkeypatch, payload=None):
    pay = dict(payload or _PAYLOAD)
    monkeypatch.setattr(G, "build_ledger_input", lambda hours=24.0: pay)
    monkeypatch.setattr(G, "build_health_input", lambda now=None: pay)
    return pay


def test_run_success_writes_draft(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    out = G.run(
        "anomaly_digest", client=FakeClient(), output_root=tmp_path, if_changed=False
    )
    assert out["ok"] is True and out["status"] == "draft"
    assert out["usage"]["total_tokens"] == 123
    path = Path(out["path"])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == G.SCHEMA and data["validator"]["grounding_ok"] is True
    assert data["items"][0]["id"].startswith("det-")


def test_run_skips_unchanged(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    first = G.run("anomaly_digest", client=FakeClient(), output_root=tmp_path)
    second = G.run("anomaly_digest", client=FakeClient(), output_root=tmp_path)
    assert first["ok"] and not first.get("skipped")
    assert second["skipped"] is True and second["reason"] == "unchanged"
    assert len(list((tmp_path / "advice").rglob("anomaly_digest.json"))) == 1


def test_run_force_recalls(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    c = FakeClient()
    G.run("anomaly_digest", client=c, output_root=tmp_path)
    G.run("anomaly_digest", client=c, output_root=tmp_path, force=True)
    assert len(c.calls) == 2


def test_run_failure_is_fail_visible(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    out = G.run(
        "anomaly_digest",
        client=FakeClient(error=DS.DeepSeekError("boom")),
        output_root=tmp_path,
        if_changed=False,
    )
    assert out["ok"] is False and out["status"] == "failed"
    data = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    assert data["status"] == "failed" and data["validator"]["notes"]


def test_run_invalid_model_output_fails(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    out = G.run(
        "anomaly_digest",
        client=FakeClient(content="not json"),
        output_root=tmp_path,
        if_changed=False,
    )
    assert out["ok"] is False and any("非 JSON" in n for n in out["validator"]["notes"])


def test_run_dry_run_writes_nothing(tmp_path, monkeypatch):
    _patch_payload(monkeypatch)
    out = G.run(
        "anomaly_digest", client=FakeClient(), output_root=tmp_path, dry_run=True
    )
    assert out["dry_run"] is True and out["would_call"] is True
    assert not (tmp_path / "advice").exists()


def test_run_govai_off(tmp_path, monkeypatch):
    monkeypatch.setenv(G.OFF_ENV, "off")
    out = G.run("anomaly_digest", client=FakeClient(), output_root=tmp_path)
    assert out["skipped"] is True and "off" in out["reason"]


def test_case_draft_bundle_flow(tmp_path, monkeypatch):
    bundle = tmp_path / "case.json"
    bundle.write_text(
        json.dumps(
            {
                "schema": "governance_case_bundle/v1",
                "title": "取消码误计事件",
                "timeline": [
                    {"id": "E1", "what": "3221225786 被计为失败"},
                    {"id": "E2", "what": "修复 NON_FAILURE_RC"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    item = {
        "cue": "取消码被计失败",
        "lesson": "非失败码必须单列",
        "evidence": ["bundle:E1", "bundle:E2"],
        "confidence": 0.7,
    }
    client = FakeClient(items=[item])
    out = G.run(
        "case_draft",
        bundle=str(bundle),
        client=client,
        output_root=tmp_path / "out",
        if_changed=False,
    )
    assert out["ok"] is True and out["items"][0]["id"].startswith("det-")


def test_case_draft_unknown_bundle_ref_fails(tmp_path):
    bundle = tmp_path / "case.json"
    bundle.write_text(
        json.dumps({"schema": "governance_case_bundle/v1", "timeline": [{"id": "E1"}]}),
        encoding="utf-8",
    )
    item = {"cue": "c", "lesson": "l", "evidence": ["bundle:NOPE"], "confidence": 0.6}
    out = G.run(
        "case_draft",
        bundle=str(bundle),
        client=FakeClient(items=[item]),
        output_root=tmp_path / "out",
        if_changed=False,
    )
    assert out["ok"] is False


# ---------------------------------------------------------------- 金标回放


def test_replay_golden_fixture():
    fixture = Path(__file__).resolve().parent / "fixtures" / "govai_golden"
    # 同一 item 需在两个 fixture 上都接地：只引用两边都存在的任务，且不含数字
    item = {
        "title": "AlertWatch 上游拒跑",
        "hypothesis": "上游不可信主动拒跑，不属于失败（待验证）",
        "evidence": ["task:AlertWatch"],
        "confidence": 0.6,
    }
    result = G.replay(fixture, client=FakeClient(items=[item]))
    assert result["cases"] >= 2
    assert result["schema_ok"] == result["cases"]
    assert result["grounding_ok"] == result["cases"]
    assert (result["recall"] or 0) > 0


# ---------------- GOVAI-2：健康判级 / 保留策略 / 批量动作 ----------------


def _write_advice(
    root, day: str, action: str, status: str, generated_at: str, items: int = 1
):
    d = root / "advice" / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.json" % action)
    p.write_text(
        json.dumps(
            {
                "status": status,
                "generated_at": generated_at,
                "items": [{"id": "x"}] * items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_health_status_no_files_is_yellow_not_red(tmp_path):
    r = G.health_status(tmp_path)
    assert r["level"] == "yellow" and "尚未运行" in r["detail"]


def test_health_status_fresh_success_is_green(tmp_path):
    import time

    now = time.time()
    _write_advice(
        tmp_path,
        G.dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d"),
        "anomaly_digest",
        "draft",
        G._iso(now - 3600),
        items=2,
    )
    r = G.health_status(tmp_path, now=now)
    assert r["level"] == "green" and r["age_hours"] is not None and r["age_hours"] <= 26


def test_health_status_stale_success_is_yellow(tmp_path):
    import time

    now = time.time()
    _write_advice(
        tmp_path, "2026-10-25", "anomaly_digest", "draft", G._iso(now - 30 * 3600)
    )
    assert G.health_status(tmp_path, now=now)["level"] == "yellow"


def test_health_status_very_stale_success_is_red(tmp_path):
    import time

    now = time.time()
    _write_advice(
        tmp_path, "2026-10-23", "anomaly_digest", "draft", G._iso(now - 60 * 3600)
    )
    assert G.health_status(tmp_path, now=now)["level"] == "red"


def test_health_status_consecutive_failures_is_red(tmp_path):
    import time

    now = time.time()
    _write_advice(
        tmp_path, "2026-10-26", "anomaly_digest", "failed", G._iso(now - 3600)
    )
    _write_advice(
        tmp_path, "2026-10-26", "priority_advice", "failed", G._iso(now - 3500)
    )
    r = G.health_status(tmp_path, now=now)
    assert r["level"] == "red" and r["consecutive_failures"] >= 2


def test_prune_advice_moves_old_days(tmp_path):
    import time

    now = time.time()
    old_day = (G.dt.datetime.fromtimestamp(now) - G.dt.timedelta(days=120)).strftime(
        "%Y-%m-%d"
    )
    new_day = G.dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    _write_advice(
        tmp_path, old_day, "anomaly_digest", "draft", G._iso(now - 120 * 86400)
    )
    _write_advice(tmp_path, new_day, "anomaly_digest", "draft", G._iso(now - 3600))
    moved = G.prune_advice(tmp_path, keep_days=90, now=now)
    assert moved == 1
    assert (tmp_path / "_expired" / old_day).is_dir()
    assert (tmp_path / "advice" / new_day).is_dir()
    assert not (tmp_path / "advice" / old_day).exists()


def test_run_all_aggregates_actions(monkeypatch, tmp_path):
    calls = []

    def fake_run(action, **kw):
        calls.append(action)
        return {
            "ok": True,
            "status": "draft",
            "items": [{"id": "x"}],
            "usage": {"total_tokens": 1},
            "path": "p",
            "validator": {"schema_ok": True, "grounding_ok": True},
        }

    monkeypatch.setattr(G, "run", fake_run)
    out = G.run_all(output_root=tmp_path)
    assert out["ok"] is True
    assert calls == ["anomaly_digest", "priority_advice"]
    assert [a["action"] for a in out["actions"]] == calls
    assert out["health"]["level"] in ("yellow", "green")


# ---------------- GOVAI2FIX：BOM 容忍 / evidence 字符串归一 ----------------


def test_build_case_input_tolerates_bom(tmp_path):
    bundle = tmp_path / "b.json"
    data = {"schema": "governance_case_bundle/v1", "timeline": [{"id": "E1"}]}
    bundle.write_text("\ufeff" + json.dumps(data), encoding="utf-8")
    payload = G.build_case_input(str(bundle))
    assert payload["known_bundle_ids"] == ["E1"]


def test_validate_normalizes_string_evidence():
    payload = {"known_tasks": ["AlertWatch"]}
    item = {
        "title": "x",
        "hypothesis": "y",
        "evidence": "task:AlertWatch",
        "confidence": 0.6,
    }
    r = G.validate_items("anomaly_digest", [item], payload)
    assert r["ok"] is True
    assert r["items"][0]["evidence"] == ["task:AlertWatch"]
    assert any("归一为数组" in n for n in r["notes"])


def test_validate_string_evidence_still_grounded():
    payload = {"known_tasks": ["AlertWatch"]}
    item = {
        "title": "x",
        "hypothesis": "y",
        "evidence": "task:NotARealTask",
        "confidence": 0.6,
    }
    r = G.validate_items("anomaly_digest", [item], payload)
    assert r["ok"] is False and r["grounding_ok"] is False


def test_validate_normalizes_confidence_label():
    item = dict(_VALID_ITEM)
    item["confidence"] = "high"
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["ok"] is True
    assert r["items"][0]["confidence"] == 0.9
    assert any("confidence 标签" in n for n in r["notes"])


def test_validate_rejects_unknown_confidence_label():
    item = dict(_VALID_ITEM)
    item["confidence"] = "certain"
    r = G.validate_items("anomaly_digest", [item], _PAYLOAD)
    assert r["ok"] is False
    assert any("confidence 非数值" in n for n in r["notes"])
