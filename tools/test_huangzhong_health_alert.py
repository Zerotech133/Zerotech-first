# -*- coding: utf-8 -*-
"""huangzhong_health_alert.py 单测（tmp 夹具 + fake _run；不跑真实 health、不写生产 alerts）。

覆盖：
  · dry-run 默认零写盘（healthy / 首失败未达阈值 / 达到阈值只展示将写 flag）；
  · --apply 首次失败只写 state（atomic，无 tmp 残留）；连续 2 次达阈值 → 写 flag；
  · 去重窗口内不重复；去重窗口过后可再次告警；成功/warn 清零；
  · 演练前缀（HUANGZHONG_DRILL=1 → 【演练】）；
  · 坏 JSON / 超时 → fail-visible（rc2）且不吞掉失败计数；state 坏 JSON 自愈；
  · 写盘失败 → rc2；--dry-run + --apply 冲突 / 非法参数 → rc2 零写入；
  · 调用参数（cmd/cwd/timeout=180）；CLI --json 与退出码。
"""
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 1, 7, 10, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_health_alert", _TOOLS / "huangzhong_health_alert.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _health(status: str = "ok") -> dict:
    if status == "fail":
        checks = [
            {"id": "acceptance_latest", "name": "最新验收单 age",
             "status": "fail", "detail": "age=200.0 min > 90 min"},
            {"id": "events_freshness", "name": "events.jsonl 最近写入 age",
             "status": "ok", "detail": "age=10.0 min"},
        ]
        summary = {"ok": 1, "warn": 0, "fail": 1, "skipped": 0, "total": 2}
    elif status == "warn":
        checks = [{"id": "review_latest", "name": "最新复核报告 age",
                   "status": "warn", "detail": "age=30h"}]
        summary = {"ok": 0, "warn": 1, "fail": 0, "skipped": 0, "total": 1}
    else:
        checks = [{"id": "acceptance_latest", "name": "最新验收单 age",
                   "status": "ok", "detail": "age=10.0 min"}]
        summary = {"ok": 1, "warn": 0, "fail": 0, "skipped": 0, "total": 1}
    return {"ok": status != "fail", "tool": "huangzhong_health.py",
            "status": status, "generated_at": _iso(NOW), "checks": checks,
            "summary": summary, "warnings": []}


def _make_run(rc=0, stdout=None, stderr=""):
    calls = []

    def fake(cmd, cwd=None, timeout=180.0):
        calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
        return {"ok": rc == 0, "rc": rc,
                "stdout": stdout if stdout is not None else json.dumps(
                    _health(), ensure_ascii=False),
                "stderr": stderr, "timeout": False}

    fake.calls = calls
    return fake


DEFAULT_TIMEOUT = 180.0


def _setup(tmp_path: Path, *, rc=0, stdout=None, stderr="", state=None,
           health_payload=None) -> dict:
    if stdout is None and health_payload is None:
        stdout = json.dumps(_health(), ensure_ascii=False)
    root = tmp_path
    work = root / "work" / "huangzhong"
    work.mkdir(parents=True, exist_ok=True)
    if state is not None:
        _write(work / "health_state.json", state)
    return {"root": root, "work": work, "alerts": root / "alerts",
            "rc": rc, "stdout": stdout, "stderr": stderr}


def _call(tmp_path, monkeypatch, *, rc=0, stdout=None, stderr="", state=None,
          **kw):
    fx = _setup(tmp_path, rc=rc, stdout=stdout, stderr=stderr, state=state)
    h = _load()
    fake = _make_run(rc=fx["rc"], stdout=fx["stdout"], stderr=fx["stderr"])
    monkeypatch.setattr(h, "_run", fake)
    kw.setdefault("repo_root", fx["root"])
    kw.setdefault("work_dir", fx["work"])
    kw.setdefault("alerts_dir", fx["alerts"])
    kw.setdefault("now", NOW)
    rep = h.run(**kw)
    return h, fake, rep, fx


def _valid_state(consecutive: int = 0, last_alert_at=None,
                 last_status: str = "fail") -> dict:
    return {"consecutive_fail": consecutive, "last_alert_at": last_alert_at,
            "last_status": last_status,
            "updated_at": _iso(NOW - timedelta(hours=1))}


def _flags(fx: dict) -> list[Path]:
    return sorted(Path(fx["alerts"]).glob("*.flag")) if Path(
        fx["alerts"]).exists() else []


# --------------------------------------------------------- dry-run 零写入

def test_default_dry_run_healthy_zero_write(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(tmp_path, monkeypatch)
    assert rep["dry_run"] is True and rep["apply"] is False
    assert rep["exit_code"] == 0 and rep["ok"] is True
    assert rep["evaluation"]["healthy"] is True
    assert rep["evaluation"]["would_alert"] is False
    assert rep["health"]["rc"] == 0 and rep["health"]["status"] == "ok"
    assert not (fx["work"] / "health_state.json").exists()
    assert not Path(fx["alerts"]).exists()
    # 调用参数：repo/tools/huangzhong_health.py --json，cwd=repo，timeout=180
    call = fake.calls[0]
    assert call["cmd"][-1] == "--json"
    assert call["cmd"][1].endswith("huangzhong_health.py")
    assert Path(call["cwd"]) == fx["root"]
    assert call["timeout"] == 180.0


def test_dry_run_first_fail_no_alert(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(tmp_path, monkeypatch, rc=2,
                             stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["evaluation"]["failure"] is True
    assert rep["evaluation"]["consecutive_fail"] == 1
    assert rep["evaluation"]["would_alert"] is False     # 未达阈值 2
    assert rep["exit_code"] == 0
    assert not (fx["work"] / "health_state.json").exists()
    assert _flags(fx) == []


def test_dry_run_threshold_reached_rc1_and_shows_flag(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, rc=2, state=_valid_state(consecutive=1),
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["evaluation"]["consecutive_fail"] == 2
    assert rep["evaluation"]["would_alert"] is True
    assert rep["exit_code"] == 1                         # 达到阈值但 dry-run
    assert rep["flag"]["content"] and "黄忠工具链健康告警" in rep["flag"]["content"]
    assert rep["flag"]["written"] is False
    # dry-run 不更新已存在的 state（保持 consecutive=1/旧 updated_at）
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 1
    assert state["updated_at"] == _iso(NOW - timedelta(hours=1))
    assert _flags(fx) == []
    assert rep["state_after"]["consecutive_fail"] == 2   # 仅「假如触发」展示


# ------------------------------------------------------- apply：state / flag

def test_apply_first_fail_updates_state_only(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=2,
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["exit_code"] == 0 and rep["ok"] is True
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 1
    assert state["last_status"] == "fail"
    assert state["last_alert_at"] is None
    assert state["updated_at"] == _iso(NOW)
    assert _flags(fx) == []
    assert set(state) == {"consecutive_fail", "last_alert_at", "last_status",
                          "updated_at"}


def test_apply_second_fail_writes_flag(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=2,
        state=_valid_state(consecutive=1),
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["exit_code"] == 0 and rep["flag"]["written"] is True
    flags = _flags(fx)
    assert len(flags) == 1
    name = flags[0].name
    assert re.match(r"^huangzhong_health_fail_\d{8}_\d{6}\.flag$", name)
    content = flags[0].read_text(encoding="utf-8")
    for token in ("黄忠工具链健康告警", "status=fail", "consecutive_fail=2/2",
                  "失败检查项", "acceptance_latest", "时间=", "查看：http"):
        assert token in content, token
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 2
    assert state["last_alert_at"] == _iso(NOW)
    assert rep["flag"]["path"] == str(flags[0])


def test_dedupe_window_suppresses_repeat(tmp_path, monkeypatch):
    last = _iso(NOW - timedelta(hours=1))
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=2,
        state=_valid_state(consecutive=1, last_alert_at=last),
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["evaluation"]["dedupe_suppressed"] is True
    assert rep["evaluation"]["would_alert"] is False
    assert _flags(fx) == []
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 2
    assert state["last_alert_at"] == last          # 去重窗口内不更新
    assert rep["exit_code"] == 0


def test_dedupe_expired_alerts_again(tmp_path, monkeypatch):
    last = _iso(NOW - timedelta(hours=7))
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=2,
        state=_valid_state(consecutive=1, last_alert_at=last),
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["evaluation"]["would_alert"] is True
    assert len(_flags(fx)) == 1
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["last_alert_at"] == _iso(NOW)


def test_healthy_resets_and_no_flag(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(tmp_path, monkeypatch, apply=True, rc=0,
                             state=_valid_state(consecutive=3))
    assert rep["evaluation"]["healthy"] is True
    assert rep["evaluation"]["consecutive_fail"] == 0
    assert rep["flag"]["written"] is False
    assert _flags(fx) == []
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 0
    assert state["last_status"] == "ok"


def test_warn_is_healthy_resets(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=1,
        stdout=json.dumps(_health("warn"), ensure_ascii=False),
        state=_valid_state(consecutive=2))
    assert rep["evaluation"]["healthy"] is True
    assert rep["evaluation"]["consecutive_fail"] == 0
    assert _flags(fx) == []


def test_drill_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HUANGZHONG_DRILL", "1")
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=2,
        state=_valid_state(consecutive=1),
        stdout=json.dumps(_health("fail"), ensure_ascii=False))
    assert rep["flag"]["written"] is True
    content = _flags(fx)[0].read_text(encoding="utf-8")
    assert content.startswith("【演练】")
    assert "drill=true" in content


# ----------------------------------------------- fail-visible：坏 JSON/超时

def test_bad_json_stdout_fail_visible(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(tmp_path, monkeypatch, rc=0, stdout="not-json-{{")
    assert rep["ok"] is False
    assert rep["exit_code"] == 2
    assert "不可解析" in rep["error"]
    assert rep["health"]["failure"] is True
    assert not (fx["work"] / "health_state.json").exists()
    assert _flags(fx) == []


def test_timeout_fail_visible_and_counts(tmp_path, monkeypatch):
    h, fake, rep, fx = _call(
        tmp_path, monkeypatch, apply=True, rc=None, stdout="",
        stderr="TimeoutExpired: >180s", state=_valid_state(consecutive=1))
    assert rep["ok"] is False and rep["exit_code"] == 2
    assert "执行失败" in rep["error"]
    # 执行失败也计一次失败（不吞掉），达到阈值 → flag 仍写出
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 2
    flags = _flags(fx)
    assert len(flags) == 1
    content = flags[0].read_text(encoding="utf-8")
    assert "执行错误" in content


def test_state_bad_json_self_heal(tmp_path, monkeypatch):
    fx = _setup(tmp_path, rc=0)
    (fx["work"] / "health_state.json").write_text("{oops", encoding="utf-8")
    h = _load()
    fake = _make_run(rc=0)
    monkeypatch.setattr(h, "_run", fake)
    rep = h.run(apply=True, repo_root=fx["root"], work_dir=fx["work"],
                alerts_dir=fx["alerts"], now=NOW)
    assert rep["exit_code"] == 0
    assert any("不可读" in w for w in rep["warnings"])
    state = json.loads((fx["work"] / "health_state.json").read_text(
        encoding="utf-8"))
    assert state["consecutive_fail"] == 0
    assert set(state) == {"consecutive_fail", "last_alert_at", "last_status",
                          "updated_at"}


def test_atomic_write_no_tmp_left(tmp_path, monkeypatch):
    fx = _setup(tmp_path, rc=2, stdout=json.dumps(_health("fail"),
                                                  ensure_ascii=False))
    _write(fx["work"] / "health_state.json", _valid_state(consecutive=1))
    h = _load()
    monkeypatch.setattr(h, "_run",
                        _make_run(rc=2, stdout=json.dumps(_health("fail"),
                                                          ensure_ascii=False)))
    rep = h.run(apply=True, repo_root=fx["root"], work_dir=fx["work"],
                alerts_dir=fx["alerts"], now=NOW)
    assert rep["flag"]["written"] is True
    leftovers = list(Path(fx["work"]).glob("*.tmp")) + \
        list(Path(fx["alerts"]).glob("*.tmp"))
    assert leftovers == []


def test_flag_write_failure_rc2(tmp_path, monkeypatch):
    fx = _setup(tmp_path, rc=2, stdout=json.dumps(_health("fail"),
                                                  ensure_ascii=False))
    _write(fx["work"] / "health_state.json", _valid_state(consecutive=1))
    blocker = tmp_path / "alerts_blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    h = _load()
    monkeypatch.setattr(h, "_run",
                        _make_run(rc=2, stdout=json.dumps(_health("fail"),
                                                          ensure_ascii=False)))
    rep = h.run(apply=True, repo_root=fx["root"], work_dir=fx["work"],
                alerts_dir=blocker, now=NOW)
    assert rep["ok"] is False and rep["exit_code"] == 2
    assert "flag 写入失败" in rep["error"]


# -------------------------------------------------- 参数冲突 / 非法参数 / CLI

def test_conflicting_flags_zero_write(tmp_path, monkeypatch):
    fx = _setup(tmp_path)
    h = _load()
    monkeypatch.setattr(h, "_run", _make_run(rc=0))
    rep = h.run(apply=True, dry_run_flag=True, repo_root=fx["root"],
                work_dir=fx["work"], alerts_dir=fx["alerts"], now=NOW)
    assert rep["ok"] is False and rep["exit_code"] == 2
    assert "同时" in rep["error"]
    assert not (fx["work"] / "health_state.json").exists()


def test_invalid_params_zero_write(tmp_path, monkeypatch):
    fx = _setup(tmp_path)
    h = _load()
    monkeypatch.setattr(h, "_run", _make_run(rc=0))
    rep = h.run(threshold=0, repo_root=fx["root"], work_dir=fx["work"],
                alerts_dir=fx["alerts"], now=NOW)
    assert rep["ok"] is False and rep["exit_code"] == 2
    assert "threshold" in rep["error"]
    rep2 = h.run(dedupe_hours=-1, repo_root=fx["root"], work_dir=fx["work"],
                 alerts_dir=fx["alerts"], now=NOW)
    assert rep2["ok"] is False and rep2["exit_code"] == 2
    assert not (fx["work"] / "health_state.json").exists()


def test_cli_json_dry_run_default(tmp_path, monkeypatch, capsys):
    fx = _setup(tmp_path)
    h = _load()
    monkeypatch.setattr(h, "_run", _make_run(rc=0))
    rc = h.main(["--json", "--repo-root", str(fx["root"]),
                 "--work-dir", str(fx["work"]),
                 "--alerts-dir", str(fx["alerts"])])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["dry_run"] is True and data["exit_code"] == 0
    assert not (fx["work"] / "health_state.json").exists()


def test_cli_apply_and_dry_run_conflict(tmp_path, monkeypatch, capsys):
    fx = _setup(tmp_path)
    h = _load()
    monkeypatch.setattr(h, "_run", _make_run(rc=0))
    rc = h.main(["--json", "--apply", "--dry-run",
                 "--repo-root", str(fx["root"]),
                 "--work-dir", str(fx["work"]),
                 "--alerts-dir", str(fx["alerts"])])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2 and data["ok"] is False


def test_cli_threshold_dedupe_args(tmp_path, monkeypatch, capsys):
    fx = _setup(tmp_path, rc=2,
                stdout=json.dumps(_health("fail"), ensure_ascii=False))
    h = _load()
    monkeypatch.setattr(h, "_run", _make_run(rc=2, stdout=json.dumps(
        _health("fail"), ensure_ascii=False)))
    rc = h.main(["--json", "--dry-run", "--threshold", "3",
                 "--dedupe-hours", "1",
                 "--repo-root", str(fx["root"]),
                 "--work-dir", str(fx["work"]),
                 "--alerts-dir", str(fx["alerts"])])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0                      # 1 < 3 未达阈值
    assert data["threshold"] == 3
    assert data["dedupe_hours"] == 1.0
    assert data["evaluation"]["consecutive_fail"] == 1
