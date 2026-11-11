# -*- coding: utf-8 -*-
"""P1 修正版 D0（ADR-025）：devtask GOV_MODE 四档行为矩阵回归。

覆盖：
  ① off：达上限硬拒（原语义一字不改）；
  ② shadow：不拒、不 print，只写 %TEMP%/gov_observe/gov_shadow.log；
  ③ warn：不拒，stderr 输出 [gov-warn] would-block；
  ④ enforce：硬拒（复用原语义）；
  ⑤ _worktree_cap / _ttl_hours 在 mode!=off 时读 gov_state，off 档忽略；
  ⑥ env GOV_MODE 优先于状态文件；
  ⑦ --explore：不调用 cap 闸、不建 worktree、登记 status=explore/write_scope；
  ⑧ audit-worktrees 不把 explore 单算违规。
"""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402


@pytest.fixture
def gov(tmp_path, monkeypatch):
    """隔离治理状态与影子日志；默认无状态文件、无 GOV_MODE。"""
    state = tmp_path / "gov_state.json"
    shadow = tmp_path / "gov_observe" / "gov_shadow.log"
    monkeypatch.setattr(devtask, "GOV_STATE_PATH", str(state))
    monkeypatch.setattr(devtask, "GOV_SHADOW_LOG", str(shadow))
    monkeypatch.delenv(devtask.GOV_STATE_ENV, raising=False)
    return {"state": state, "shadow": shadow}


def _write_state(path, **kw):
    base = {"mode": "off", "cap": None, "target": 15, "ttl": None}
    base.update(kw)
    path.write_text(json.dumps(base), encoding="utf-8")


def test_off_mode_default_and_block(gov, monkeypatch):
    assert devtask._gov_state()["mode"] == "off"
    _write_state(gov["state"], mode="off", cap=10)
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda: 99)
    monkeypatch.setattr(devtask, "_worktree_cap", lambda: 80)
    with pytest.raises(SystemExit) as ei:
        devtask._enforce_worktree_cap()
    assert "已达上限" in str(ei.value)


def test_shadow_never_raises_and_writes_log(gov, monkeypatch):
    _write_state(gov["state"], mode="shadow", cap=10)
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda: 99)
    monkeypatch.setattr(devtask, "_worktree_cap", lambda: 10)
    devtask._enforce_worktree_cap()  # 不抛
    text = gov["shadow"].read_text(encoding="utf-8")
    assert "mode=shadow" in text and "verdict=block" in text


def test_warn_never_raises_but_prints(gov, monkeypatch, capsys):
    _write_state(gov["state"], mode="warn", cap=10)
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda: 99)
    monkeypatch.setattr(devtask, "_worktree_cap", lambda: 10)
    devtask._enforce_worktree_cap()
    err = capsys.readouterr().err
    assert "[gov-warn] would-block" in err


def test_enforce_raises(gov, monkeypatch):
    _write_state(gov["state"], mode="enforce", cap=10)
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda: 99)
    monkeypatch.setattr(devtask, "_worktree_cap", lambda: 10)
    with pytest.raises(SystemExit) as ei:
        devtask._enforce_worktree_cap()
    assert "已达上限" in str(ei.value)


def test_env_mode_overrides_state(gov, monkeypatch):
    _write_state(gov["state"], mode="shadow", cap=10)
    monkeypatch.setenv(devtask.GOV_STATE_ENV, "off")
    assert devtask._gov_state()["mode"] == "off"


def test_cap_reads_state_only_when_mode_not_off(gov, monkeypatch):
    _write_state(gov["state"], mode="shadow", cap=42)
    monkeypatch.delenv(devtask.MAX_WORKTREES_ENV, raising=False)
    assert devtask._worktree_cap() == 42
    _write_state(gov["state"], mode="off", cap=42)
    assert devtask._worktree_cap() == devtask.MAX_WORKTREES_DEFAULT


def test_ttl_reads_state_only_when_mode_not_off(gov):
    _write_state(gov["state"], mode="warn", ttl=24)
    assert devtask._ttl_hours() == 24.0
    _write_state(gov["state"], mode="off", ttl=24)
    assert devtask._ttl_hours() == devtask.WT_TTL_HOURS_DEFAULT


def test_explore_skips_cap_and_registers_scope(gov, monkeypatch, capsys):
    called = {"cap": 0, "save": 0}

    def _boom():
        called["cap"] += 1
        raise SystemExit("cap 不应被调用")

    monkeypatch.setattr(devtask, "_enforce_worktree_cap", _boom)
    monkeypatch.setattr(devtask, "_tasks", lambda: {})
    monkeypatch.setattr(devtask, "_ev_precheck", lambda desc, tasks: [])
    monkeypatch.setattr(devtask, "_ears_lint", lambda desc: [])
    monkeypatch.setattr(devtask, "_owner_check", lambda owner: (True, {}, 0))
    monkeypatch.setattr(devtask, "_save", lambda tasks, changed=None: called.__setitem__("save", called["save"] + 1))
    monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
    monkeypatch.setattr(devtask, "_emit", lambda *a, **k: None)

    rc = devtask.cmd_new("EXPLORE-TEST-1", "探针", "--explore", "--write-scope", "runs/exp1/")
    assert rc == 0
    assert called["cap"] == 0
    out = capsys.readouterr().out
    assert "已开（explore）" in out
    assert "runs/exp1/" in out


def test_audit_skips_explore_tasks():
    tasks = {"E1": {"status": "explore", "explore": True}}
    out = devtask._worktree_violations(tasks, r"C:\none", set(), set())
    assert out == []
