# -*- coding: utf-8 -*-
"""run 路径隔离根与预演阀契约测试（M5 缺口收口）。

背景（10-17 实测）：run_shadow 原先**没有隔离根**——LEDGER / MemoryStore /
Checkpointer / DualTrackHook / p0_alert_state / TaskStore 六处全指向真仓，
因此周一的 run 路径无法预演（跑一次就会把 5 行假决策写进真账本）。
本组锁死收口后的三条契约：

1. **账本在哪，附属存储就在哪**：`_data_root()` 运行时读 LEDGER 模块属性，
   monkeypatch LEDGER 后六处存储整体重定向（不是只改账本一半）；
2. **预演默认拒绝**：REHEARSAL 下数据根解析为真仓 data/ 必须在写任何字节前拒绝；
3. **预演禁外部副作用**：飞书推送一律不发（真实推送无法撤回）。
"""

import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))

import run_shadow  # noqa: E402


# ---------------------------------------------------------------- 数据根反推


def test_data_root_follows_ledger_patch(tmp_path, monkeypatch):
    """契约 1：patch LEDGER 后附属存储根同步迁移（六处不裂）。"""
    iso = tmp_path / "iso"
    monkeypatch.setattr(run_shadow, "LEDGER", iso / "shadow" / "shadow_ledger.jsonl")
    assert run_shadow._data_root() == iso.resolve()
    # 由根派生的关键附属路径必须全部落在隔离根内
    assert run_shadow._data_root() / "memory" != (PLATFORM / "data" / "memory")


def test_data_root_default_is_real_data(monkeypatch):
    """默认（未注入）仍指向真仓 data/ —— 生产语义不变。"""
    monkeypatch.undo()
    assert run_shadow._data_root() == (PLATFORM / "data").resolve()


# ---------------------------------------------------------------- 预演阀


def test_rehearsal_refuses_real_root(monkeypatch):
    """契约 2：预演 + 真仓根 → 拒绝（返回 False，调用方 exit 2）。"""
    monkeypatch.setattr(run_shadow, "REHEARSAL", True)
    monkeypatch.setattr(
        run_shadow, "LEDGER", PLATFORM / "data" / "shadow" / "shadow_ledger.jsonl"
    )
    assert run_shadow._assert_not_real_root(PLATFORM / "data") is False


def test_rehearsal_allows_isolated_root(tmp_path, monkeypatch):
    """预演 + 隔离根 → 放行。"""
    monkeypatch.setattr(run_shadow, "REHEARSAL", True)
    assert run_shadow._assert_not_real_root(tmp_path) is True


def test_production_mode_not_blocked(monkeypatch):
    """非预演（计划任务真实跑）→ 真仓根照常放行，不得误伤。"""
    monkeypatch.setattr(run_shadow, "REHEARSAL", False)
    assert run_shadow._assert_not_real_root(PLATFORM / "data") is True


# ---------------------------------------------------------------- 外部副作用


def test_rehearsal_skips_feishu_push(monkeypatch):
    """契约 3：预演模式绝不触发飞书推送（外部副作用不可撤回）。"""
    called = []
    monkeypatch.setattr(run_shadow, "REHEARSAL", True)
    monkeypatch.setattr(
        run_shadow.subprocess,
        "run",
        lambda *a, **k: called.append(a) or pytest.fail("预演不应调推送"),
    )
    run_shadow.push_failed_alert([{"symbol": "600519", "error": "boom"}], "2026-10-19")
    assert called == []


# ---------------------------------------------------------------- CLI 解析


def test_cli_opt_reads_root():
    """--root <dir> 形态解析；缺失值返回 None（不越界）。"""
    assert (
        run_shadow._cli_opt("--root", ["--rehearsal", "--root", "D:/tmp/iso"])
        == "D:/tmp/iso"
    )
    assert run_shadow._cli_opt("--root", ["--rehearsal"]) is None
    assert run_shadow._cli_opt("--root", []) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
