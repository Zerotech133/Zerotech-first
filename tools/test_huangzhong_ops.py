# -*- coding: utf-8 -*-
"""黄忠运维 CLI 单测（tmp 夹具，绝不触碰真实 work/ 或 dev_events）。

覆盖 P2 要求：
  · push on/off/status 往返 + 原子写/回读校验 + code_action 审计（含 fail-open）；
  · events-stats 总数/近 24h/verdict 计数/最近 ts/坏行（只读）；
  · events-rotate dry-run 不改文件；apply 后归档正确、坏行原样保留、幂等；
  · env HUANGZHONG_WORK_DIR / HUANGZHONG_REPO_ROOT 覆盖生效；
  · CLI --json / 退出码 / 参数互斥 / 错误 fail-visible。
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("huangzhong_ops",
                                                  _TOOLS / "huangzhong_ops.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup_env(tmp_path, monkeypatch):
    """隔离环境：work 目录/repo 根都指向 tmp；清掉会影响开关的环境变量。"""
    work = tmp_path / "work"
    repo = tmp_path / "repo"
    monkeypatch.setenv("HUANGZHONG_WORK_DIR", str(work))
    monkeypatch.setenv("HUANGZHONG_REPO_ROOT", str(repo))
    monkeypatch.delenv("HUANGZHONG_EVENT_PUSH", raising=False)
    monkeypatch.delenv("AGENT_NAME", raising=False)
    monkeypatch.delenv("DEVTOOL_AGENT", raising=False)
    return work


def _recorder():
    calls = []

    def fn(**kw):
        calls.append(kw)
        return {"ok": True}

    return calls, fn


def _line(ts, verdict="PASS", commit="abcdef123456"):
    return json.dumps({"ts": ts, "kind": "acceptance", "verdict": verdict,
                       "commit": commit, "reasons": []}, ensure_ascii=False)


def _write_events(work: Path, lines) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    p = work / "events.jsonl"
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return p


# --------------------------------------------------------------- push 开关

def test_push_status_default_off_no_file(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    rep = h.push_status()
    assert rep["ok"] is True
    assert rep["enabled"] is False and rep["source"] == "default"
    assert rep["path"] == str(work / "push_config.json")
    assert not (work / "push_config.json").exists()


def test_push_on_off_roundtrip_with_audit(tmp_path, monkeypatch):
    """push on→status on→push off→status off；每次落 code_action 审计。"""
    work = _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_NAME", "DSH-Lead")
    h = _load()
    calls, audit = _recorder()

    rep_on = h.push_set(True, audit_fn=audit)
    assert rep_on["ok"] is True and rep_on["readback_ok"] is True
    assert rep_on["enabled"] is True
    cfg = json.loads((work / "push_config.json").read_text(encoding="utf-8"))
    assert cfg["enabled"] is True and cfg["by"] == "DSH-Lead"
    datetime.fromisoformat(cfg["updated_at"])  # updated_at 是合法 ISO

    st = h.push_status()
    assert st["enabled"] is True and st["source"] == "config"
    assert st["by"] == "DSH-Lead"

    rep_off = h.push_set(False, audit_fn=audit)
    assert rep_off["ok"] is True and h.push_status()["enabled"] is False

    assert len(calls) == 2
    assert all(c["kind"] == "code_action" for c in calls)
    assert all(c["task"] == "HUANGZHONG-OPS" for c in calls)
    assert "push-on" in calls[0]["detail"] and "enabled=true" in calls[0]["detail"]
    assert "push-off" in calls[1]["detail"] and "enabled=false" in calls[1]["detail"]
    # 原子写不残留临时文件
    assert not list(work.glob("*.tmp")) and not list(work.glob(".*.tmp"))


def test_push_operator_override(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    h = _load()
    _, audit = _recorder()
    rep = h.push_set(True, operator="老田", audit_fn=audit)
    assert rep["ok"] is True and rep["config"]["by"] == "老田"


def test_push_status_env_overrides_config(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    h = _load()
    _, audit = _recorder()
    h.push_set(False, audit_fn=audit)
    monkeypatch.setenv("HUANGZHONG_EVENT_PUSH", "1")
    st = h.push_status()
    assert st["enabled"] is True and st["source"] == "env"
    monkeypatch.setenv("HUANGZHONG_EVENT_PUSH", "off")
    st = h.push_status()
    assert st["enabled"] is False and st["source"] == "env"
    # 不可识别的 env → 回落配置文件
    monkeypatch.setenv("HUANGZHONG_EVENT_PUSH", "maybe")
    st = h.push_status()
    assert st["source"] == "config" and st["enabled"] is False


def test_push_status_bad_config_default_off(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    work.mkdir(parents=True, exist_ok=True)
    (work / "push_config.json").write_text("{broken", encoding="utf-8")
    h = _load()
    st = h.push_status()
    assert st["ok"] is True and st["enabled"] is False
    assert st["source"] == "default" and "config_error" in st


def test_push_readback_mismatch_fail_visible(tmp_path, monkeypatch):
    """写盘后回读不一致 → ok=False（fail-visible），审计仍记录 readback=FAIL。"""
    _setup_env(tmp_path, monkeypatch)
    h = _load()
    monkeypatch.setattr(h, "_atomic_write_json", lambda p, d: None)  # 模拟未落盘
    calls, audit = _recorder()
    rep = h.push_set(True, audit_fn=audit)
    assert rep["ok"] is False and rep["readback_ok"] is False
    assert "回读" in rep["error"]
    assert calls and "readback=FAIL" in calls[0]["detail"]


def test_push_write_fail_visible(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_text("not a dir", encoding="utf-8")  # work 被文件占位 → 无法建目录
    h = _load()
    calls, audit = _recorder()
    rep = h.push_set(True, audit_fn=audit)
    assert rep["ok"] is False and "error" in rep
    assert calls == []


def test_push_audit_fail_open(tmp_path, monkeypatch):
    """审计抛异常不得影响开关写入与返回（fail-open）。"""
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()

    def boom(**kw):
        raise RuntimeError("events down")

    rep = h.push_set(True, audit_fn=boom)
    assert rep["ok"] is True and rep["readback_ok"] is True
    assert (work / "push_config.json").is_file()


def test_audit_fallback_writes_dev_events(tmp_path, monkeypatch):
    """events.py 不可用时兜底直写 dev_events（同 schema，落 env 指定 repo）。"""
    _setup_env(tmp_path, monkeypatch)
    h = _load()
    monkeypatch.setitem(sys.modules, "events", None)  # 让 import events 失败
    rep = h._audit("push-on enabled=true by=DSH-Lead")
    assert rep["ok"] is True and "fallback" in rep
    p = tmp_path / "repo" / "portal" / "data" / "dev_events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "code_action" and rec["task"] == "HUANGZHONG-OPS"
    assert "push-on enabled=true" in rec["detail"]


# --------------------------------------------------------------- 事件统计

def test_events_stats_counts_and_bad_lines(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    old = (now - timedelta(hours=30)).isoformat(timespec="seconds")
    _write_events(work, [
        _line(fresh, "PASS"),
        _line(fresh, "PASS"),
        _line(fresh, "REJECT"),
        _line(old, "OBSERVE"),
        '{"not": "valid json"',
        '{"ts": "not-a-time", "verdict": "PASS"}',  # ts 不可解析 → 坏行
        "",
    ])
    rep = h.events_stats()
    assert rep["ok"] is True and rep["exists"] is True
    assert rep["total"] == 6
    assert rep["bad_lines"] == 2
    assert rep["recent_24h"] == 3
    assert rep["verdicts"] == {"PASS": 3, "REJECT": 1, "OBSERVE": 1}
    latest = datetime.fromisoformat(rep["latest_ts"])
    assert abs((latest - now).total_seconds()) < 2 * 3600


def test_events_stats_missing_file(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)  # work 目录不存在
    h = _load()
    rep = h.events_stats()
    assert rep["ok"] is True and rep["exists"] is False and rep["total"] == 0


def test_events_stats_unreadable_fail_visible(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    work.mkdir(parents=True, exist_ok=True)
    (work / "events.jsonl").mkdir()  # 目录冒充文件 → 读取失败
    h = _load()
    rep = h.events_stats()
    assert rep["ok"] is False and "error" in rep
    assert h.main(["events-stats", "--json"]) == 1


# --------------------------------------------------------------- 事件轮转

def test_rotate_dry_run_does_not_touch_file(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=31)).isoformat(timespec="seconds")
    fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")
    p = _write_events(work, [_line(old), _line(fresh), '{"bad": 1}'])
    before = p.read_bytes()
    rep = h.events_rotate(days=30, apply=False, now=now)
    assert rep["ok"] is True and rep["dry_run"] is True and rep["applied"] is False
    assert rep["total"] == 3 and rep["archive_count"] == 1 and rep["keep_count"] == 2
    assert rep["bad_lines"] == 1
    assert p.read_bytes() == before
    assert not (work / "archive").exists()


def test_rotate_apply_archives_and_is_idempotent(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    old1 = "2026-10-01T00:00:00+00:00"   # 30.5 天 → 归档
    old2 = "2026-09-30T00:00:00+00:00"   # 31.5 天 → 归档
    fresh = "2026-10-31T10:00:00+00:00"
    p = _write_events(work, [_line(old1), _line(old2, "REJECT"), _line(fresh)])
    rep = h.events_rotate(days=30, apply=True, now=now)
    assert rep["ok"] is True and rep["applied"] is True
    assert rep["archive_written"] is True and rep["readback_ok"] is True
    assert rep["archive_count"] == 2 and rep["keep_count"] == 1
    archive = work / "archive" / "events_20261031.jsonl"
    assert archive.is_file()
    arc_lines = archive.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["ts"] for x in arc_lines] == [old1, old2]
    kept = p.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["ts"] for x in kept] == [fresh]
    # 幂等：再跑 --apply 无变化、不重复归档
    main_before, arc_before = p.read_bytes(), archive.read_bytes()
    rep2 = h.events_rotate(days=30, apply=True, now=now)
    assert rep2["ok"] is True and rep2["applied"] is False
    assert rep2["archive_count"] == 0
    assert p.read_bytes() == main_before and archive.read_bytes() == arc_before


def test_rotate_keeps_bad_lines_verbatim(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    bad = '{"broken": '
    no_ts = '{"verdict": "PASS"}'
    p = _write_events(work, [_line("2026-10-01T00:00:00+00:00"), bad, no_ts])
    rep = h.events_rotate(days=30, apply=True, now=now)
    assert rep["ok"] is True
    assert rep["bad_lines"] == 2 and rep["archive_count"] == 1
    kept = p.read_text(encoding="utf-8").splitlines()
    assert bad in kept and no_ts in kept


def test_rotate_respects_days_threshold(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    _write_events(work, [
        _line("2026-09-30T12:00:00+00:00"),   # 31 天 → 归档
        _line("2026-10-02T12:00:00+00:00"),   # 29 天 → 保留
    ])
    rep = h.events_rotate(days=30, apply=True, now=now)
    assert rep["archive_count"] == 1 and rep["keep_count"] == 1


def test_rotate_missing_file_ok(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    h = _load()
    rep = h.events_rotate(days=30, apply=True)
    assert rep["ok"] is True and rep["exists"] is False
    assert rep["archive_count"] == 0 and rep["applied"] is False


# ------------------------------------------------------------ env/CLI 行为

def test_env_override_paths(tmp_path, monkeypatch):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    assert h._work_dir() == work
    assert h._push_path() == work / "push_config.json"
    assert h._events_path() == work / "events.jsonl"
    monkeypatch.delenv("HUANGZHONG_WORK_DIR")
    assert h._work_dir() == tmp_path / "repo" / "work" / "huangzhong"
    monkeypatch.delenv("HUANGZHONG_REPO_ROOT")
    assert h._work_dir() == h.ROOT / "work" / "huangzhong"


def test_cli_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    work = _setup_env(tmp_path, monkeypatch)
    h = _load()
    calls = []

    def fake_audit(detail, audit_fn=None):
        calls.append(detail)
        return {"ok": True}

    monkeypatch.setattr(h, "_audit", fake_audit)

    rc = h.main(["push-on", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["enabled"] is True
    assert calls and "push-on" in calls[-1]

    rc = h.main(["push-status", "--json"])
    assert rc == 0
    st = json.loads(capsys.readouterr().out)
    assert st["enabled"] is True and st["source"] == "config"

    rc = h.main(["push-off", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    _write_events(work, [_line("2026-10-31T11:00:00+00:00", "PASS")])
    rc = h.main(["events-stats", "--json"])
    assert rc == 0
    st = json.loads(capsys.readouterr().out)
    assert st["total"] == 1 and st["verdicts"] == {"PASS": 1}

    rc = h.main(["events-rotate", "--days", "30", "--dry-run", "--json"])
    assert rc == 0
    rot = json.loads(capsys.readouterr().out)
    assert rot["dry_run"] is True and rot["applied"] is False

    # 人类可读输出 + 默认即 dry-run
    rc = h.main(["events-rotate", "--days", "30"])
    assert rc == 0 and "dry-run" in capsys.readouterr().out

    # 参数错误 fail-visible（argparse exit 2）
    with pytest.raises(SystemExit):
        h.main([])
    with pytest.raises(SystemExit):
        h.main(["events-rotate", "--dry-run", "--apply"])
