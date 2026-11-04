# -*- coding: utf-8 -*-
"""W2 自动 arm 测试（WO-WATCH-AUTOARM-20261104）。

全部离线：tmp handoff/plan/status 路径；不触发/不建确认/不写生产。
覆盖：新 handoff→armed、重复幂等、过期回收（含 expire_due）、dry-run 零写、
坏/非法 handoff fail-visible、plan_id 冲突、状态原子写、env 路径、无执行字段。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_plan_autoarm as A  # noqa: E402
from agent_platform import watch_plan_consumer as consumer  # noqa: E402

FUTURE = "2099-01-01T00:00:00+08:00"
PAST = "2000-01-01T00:00:00+08:00"
SYMBOL = "600519.SH"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


def _payload(receipt="d1h-auto-001", plan_id="wp-auto-001", expires=FUTURE,
             conditions=None):
    return {
        "schema": "d1_handoff/v2",
        "receipt_id": receipt,
        "code": SYMBOL,
        "name": "贵州茅台",
        "decision_id": "d1-20261104000000-auto",
        "target": "WA1",
        "action": "watch",
        "rationale": "W2 自动 arm 测试",
        "paper_only": True,
        "watch_plans": [{
            "plan_id": plan_id,
            "seat": "WA1",
            "method": "trend_pullback",
            "conditions": conditions or {"symbol": SYMBOL, "action": "BUY", "price": 1500.0},
            "expires_at": expires,
        }],
    }


def _env(tmp_path):
    return {
        "handoff": tmp_path / "d1_handoffs.jsonl",
        "plans": tmp_path / "watch_plans.jsonl",
        "status": tmp_path / "watch_engine" / "autoarm_status.json",
    }


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def _plan_rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def _assert_no_execution_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in EXECUTION_KEYS, "执行字段不得出现在输出: %r" % key
            _assert_no_execution_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_execution_keys(item)


# ── 1. 基本 arm / 幂等 / 空账本 ─────────────────────────────────────
class TestAutoarmBasics:
    def test_empty_handoff_ledger_ok(self, tmp_path):
        env = _env(tmp_path)
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["ok"] is True
        assert status["handoffs_scanned"] == 0
        assert status["created"] == 0 and status["armed"] == 0
        assert status["errors"] == 0
        assert not env["plans"].exists()

    def test_new_handoff_arms_plan(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["ok"] is True and status["degraded"] is False
        assert status["handoffs_scanned"] == 1
        assert status["handoffs_with_plans"] == 1
        assert status["plans_seen"] == 1
        assert status["created"] == 1 and status["armed"] == 1
        plan = consumer.get_plan("wp-auto-001", path=env["plans"])
        assert plan["status"] == "armed"
        assert plan["source"]["receipt_id"] == "d1h-auto-001"
        assert plan["paper_only"] is True

    def test_repeat_run_idempotent(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        first = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert first["created"] == 1
        before = _sha(env["plans"])
        second = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert second["created"] == 0 and second["armed"] == 0
        assert second["duplicates"] == 1
        assert second["ok"] is True and second["errors"] == 0
        assert len(_plan_rows(env["plans"])) == 1
        assert _sha(env["plans"]) == before  # 幂等：内容不变

    def test_handoff_without_watch_plans_ignored(self, tmp_path):
        env = _env(tmp_path)
        row = _payload()
        row.pop("watch_plans")
        row["schema"] = "d1_handoff/v1"
        H.append_handoff(row, ledger=env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["handoffs_scanned"] == 1
        assert status["handoffs_with_plans"] == 0
        assert status["created"] == 0
        assert not env["plans"].exists()

    def test_missing_paths_fail_visible(self, tmp_path, monkeypatch):
        monkeypatch.delenv(A.HANDOFF_LEDGER_ENV, raising=False)
        monkeypatch.delenv(A.PLAN_LEDGER_ENV, raising=False)
        status = A.run()
        assert status["ok"] is False
        assert status["error"] == "handoff_ledger_required"
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        status2 = A.run(handoff_ledger=env["handoff"])
        assert status2["ok"] is False
        assert status2["error"] == "plan_ledger_required"


# ── 2. dry-run 零写 / 过期回收 ──────────────────────────────────────
class TestDryRunAndExpiry:
    def test_dry_run_zero_write_preview(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        before = _sha(env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"],
                       dry_run=True, status_path=env["status"])
        assert status["ok"] is True and status["dry_run"] is True
        assert status["would_create"] == 1 and status["would_arm"] == 1
        assert status["created"] == 0 and status["armed"] == 0
        assert not env["plans"].exists()
        assert not env["status"].exists()
        assert status["status_written"] is False
        assert status["status_write_skipped"] == "dry_run"
        assert _sha(env["handoff"]) == before

    def test_expired_plan_created_expired(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(expires=PAST), ledger=env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["created"] == 1 and status["expired"] == 1
        assert status["armed"] == 0
        assert consumer.get_plan("wp-auto-001", path=env["plans"])["status"] == "expired"

    def test_expire_due_reclaims_armed(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        first = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert first["armed"] == 1
        # 模拟「arm 之后到期」：把 plan 账本 expires_at 改成过去
        rows = _plan_rows(env["plans"])
        rows[0]["expires_at"] = PAST
        env["plans"].write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["expired"] >= 1
        assert consumer.get_plan("wp-auto-001", path=env["plans"])["status"] == "expired"

    def test_dry_run_previews_expire_due(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        rows = _plan_rows(env["plans"])
        rows[0]["expires_at"] = PAST
        env["plans"].write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        before = _sha(env["plans"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"],
                       dry_run=True)
        assert status["would_expire"] >= 1
        assert _sha(env["plans"]) == before  # 预览不写


# ── 3. fail-visible：坏账本 / 非法行 / 冲突 ─────────────────────────
class TestFailVisible:
    def test_corrupt_handoff_ledger_fail_visible(self, tmp_path):
        env = _env(tmp_path)
        env["handoff"].write_text("{bad json\n", encoding="utf-8")
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["ok"] is False
        assert status["error"] == "handoff_ledger_corrupt"
        assert not env["plans"].exists()

    def test_invalid_handoff_row_fail_visible(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        bad = _payload(receipt="d1h-auto-bad", plan_id="wp-bad")
        bad["target"] = "TR1"  # watch 计划配 TR 目标 → handoff_invalid
        with env["handoff"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(bad, ensure_ascii=False) + "\n")
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["ok"] is True
        assert status["degraded"] is True
        assert status["errors"] == 1
        assert status["error_details"][0]["code"] == "handoff_invalid"
        assert status["created"] == 1  # 合法行仍被 arm

    def test_plan_id_conflict_fail_visible(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(receipt="d1h-auto-a"), ledger=env["handoff"])
        H.append_handoff(_payload(receipt="d1h-auto-b"), ledger=env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"])
        assert status["created"] == 1
        assert status["errors"] == 1
        assert status["error_details"][0]["code"] == "plan_id_conflict"
        plan = consumer.get_plan("wp-auto-001", path=env["plans"])
        assert plan["source"]["receipt_id"] == "d1h-auto-a"  # 不覆盖
        assert len(_plan_rows(env["plans"])) == 1


# ── 4. status 原子写 / env 路径 / CLI / 无执行字段 ──────────────────
class TestStatusAndCli:
    def test_status_atomic_write(self, tmp_path):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        status = A.run(handoff_ledger=env["handoff"], plan_ledger=env["plans"],
                       status_path=env["status"])
        assert status["status_written"] is True
        assert env["status"].is_file()
        assert not env["status"].with_suffix(env["status"].suffix + ".tmp").exists()
        saved = json.loads(env["status"].read_text(encoding="utf-8"))
        assert saved["schema"] == A.SCHEMA
        assert saved["run_id"].startswith("ar-")
        assert saved["created"] == 1 and saved["armed"] == 1
        assert saved["paper_only"] is True and saved["executes"] is False
        assert saved["duplicates"] == 0
        _assert_no_execution_keys(saved)

    def test_env_paths_used(self, tmp_path, monkeypatch):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        monkeypatch.setenv(A.HANDOFF_LEDGER_ENV, str(env["handoff"]))
        monkeypatch.setenv(A.PLAN_LEDGER_ENV, str(env["plans"]))
        status = A.run()
        assert status["created"] == 1
        assert env["plans"].is_file()

    def test_cli_json_and_rc(self, tmp_path, capsys):
        env = _env(tmp_path)
        H.append_handoff(_payload(), ledger=env["handoff"])
        rc = A.main(["--once", "--ledger", str(env["handoff"]),
                     "--plans", str(env["plans"]), "--status", str(env["status"]),
                     "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["created"] == 1 and payload["armed"] == 1
        # 重复 run：duplicates 且 rc 仍 0（无 item 错误）
        rc2 = A.main(["--once", "--ledger", str(env["handoff"]),
                      "--plans", str(env["plans"]), "--json"])
        assert rc2 == 0
        payload2 = json.loads(capsys.readouterr().out)
        assert payload2["duplicates"] == 1 and payload2["created"] == 0

    def test_cli_missing_paths_rc1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(A.HANDOFF_LEDGER_ENV, raising=False)
        monkeypatch.delenv(A.PLAN_LEDGER_ENV, raising=False)
        rc = A.main(["--once", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["error"] == "handoff_ledger_required"
