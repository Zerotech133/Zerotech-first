# -*- coding: utf-8 -*-
"""huangzhong_perf_signal.py 单测（tmp 夹具 + fake _http；**禁用真实网络**）。

覆盖：
  · 全达标 → status=ok（p50/max/ok_rate 计算正确，md 落 --out-dir）；
  · 延迟超 SLO → fail；个别失败（ok_rate 达标非 1.0）→ warn；多数/全部失败 → fail；
  · 可选目标（8787）不可达 → warn 不 fail；
  · --samples / --targets 覆盖；--no-write 不写盘；
  · _http 注入异常 → 记失败不抛异常；--samples/--timeout 非法 → fail-visible rc2；
  · CLI --json 与退出码 0/1/2；默认目标 URL 与 SLO 阈值（8734≤800ms / 8091≤500ms / ok_rate≥2/3）。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 22, 40, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_perf_signal", _TOOLS / "huangzhong_perf_signal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok(latency: float = 50.0, status: int = 200) -> dict:
    return {"ok": True, "status": status, "latency_ms": latency, "error": None}


def _fail(error: str = "URLError: unreachable") -> dict:
    return {"ok": False, "status": None, "latency_ms": 10.0, "error": error}


def _fake_http(responses: list):
    """按调用顺序返回脚本化响应（用尽后重复最后一条）；记录调用。"""
    calls: list[dict] = []

    def fake(url, timeout=5.0):
        calls.append({"url": url, "timeout": timeout})
        idx = min(len(calls) - 1, len(responses) - 1)
        r = responses[idx]
        return dict(r) if isinstance(r, dict) else dict(r(len(calls)))

    fake.calls = calls
    return fake


def _default_responses(portal=(-1,), events=(-1,), hermes=(-1,)) -> list:
    def expand(spec):
        if isinstance(spec, (list, tuple)):
            return [r if isinstance(r, dict) else r for r in spec]
        return [spec]
    return expand(portal) + expand(events) + expand(hermes)


def _call(tmp_path: Path, monkeypatch, responses: list, **kw):
    h = _load()
    fake = _fake_http(responses)
    monkeypatch.setattr(h, "_http", fake)
    kw.setdefault("out_dir", tmp_path / "out")
    kw.setdefault("now", NOW)
    return h, fake, h.run(**kw)


def _target(rep: dict, name: str) -> dict:
    return next(t for t in rep["targets"] if t["name"] == name)


# ------------------------------------------------------------------ 全达标

def test_all_ok(tmp_path, monkeypatch):
    # 8734: [60,120,80] p50=80；8091: [20,30,25] p50=25；8787: [30,40,35] p50=35
    responses = [_ok(60), _ok(120), _ok(80),
                 _ok(20), _ok(30), _ok(25),
                 _ok(30), _ok(40), _ok(35)]
    h, fake, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "ok"
    assert rep["ok"] is True
    assert rep["summary"] == {"total": 3, "ok": 3, "warn": 0, "fail": 0}
    assert len(fake.calls) == 9
    portal = _target(rep, "portal_8734")
    assert portal["p50_ms"] == 80.0
    assert portal["max_ms"] == 120.0
    assert portal["ok_rate"] == 1.0
    assert portal["required"] is True and portal["slo_p50_ms"] == 800.0
    assert _target(rep, "events_8091")["slo_p50_ms"] == 500.0
    assert _target(rep, "hermes_8787")["required"] is False
    assert all(t["status"] == "ok" for t in rep["targets"])
    md = Path(rep["report_md"])
    assert md.exists() and md.parent == tmp_path / "out"
    text = md.read_text(encoding="utf-8")
    assert "性能/SLO 信号" in text and "SLO 明细" in text and "逐次采样" in text


# --------------------------------------------------- 阈值：延迟 / 可用率

def test_latency_over_slo_fail(tmp_path, monkeypatch):
    responses = [_ok(900), _ok(950), _ok(1000),          # 8734 p50=950 > 800
                 _ok(20), _ok(30), _ok(25),
                 _ok(30), _ok(40), _ok(35)]
    _, _, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "fail"
    portal = _target(rep, "portal_8734")
    assert portal["status"] == "fail"
    assert any("p50" in r for r in portal["reasons"])


def test_partial_fail_warn(tmp_path, monkeypatch):
    responses = [_ok(50), _fail(), _ok(60),             # 8734: 2/3 成功 → warn
                 _ok(20), _ok(30), _ok(25),
                 _ok(30), _ok(40), _ok(35)]
    _, _, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "warn"
    portal = _target(rep, "portal_8734")
    assert portal["status"] == "warn"
    assert portal["ok_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert any("个别失败" in r for r in portal["reasons"])


def test_majority_fail_fail(tmp_path, monkeypatch):
    responses = [_ok(50), _fail(), _fail(),             # 8734: 1/3 < 2/3 → fail
                 _ok(20), _ok(30), _ok(25),
                 _ok(30), _ok(40), _ok(35)]
    _, _, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "fail"
    assert _target(rep, "portal_8734")["status"] == "fail"


def test_all_fail_fail(tmp_path, monkeypatch):
    responses = [_fail(), _fail(), _fail(),
                 _ok(20), _ok(30), _ok(25),
                 _ok(30), _ok(40), _ok(35)]
    _, _, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "fail"
    portal = _target(rep, "portal_8734")
    assert portal["ok_rate"] == 0.0 and portal["p50_ms"] is None
    assert any("全部失败" in r for r in portal["reasons"])


def test_optional_unreachable_warn_not_fail(tmp_path, monkeypatch):
    responses = [_ok(60), _ok(70), _ok(65),
                 _ok(20), _ok(25), _ok(22),
                 _fail(), _fail(), _fail()]         # hermes 不可达
    _, _, rep = _call(tmp_path, monkeypatch, responses)
    assert rep["status"] == "warn"                  # 可选目标不 fail
    hermes = _target(rep, "hermes_8787")
    assert hermes["status"] == "warn"
    assert hermes["required"] is False
    assert any("可选目标" in r for r in hermes["reasons"])


# ------------------------------------------------ --samples / --targets

def test_samples_override(tmp_path, monkeypatch):
    responses = [_ok(50)] * 20
    _, fake, rep = _call(tmp_path, monkeypatch, responses, samples=5)
    assert len(fake.calls) == 15                  # 3 目标 × 5 次
    assert all(t["attempts"].__len__() == 5 for t in rep["targets"])
    assert rep["samples"] == 5


def test_targets_override(tmp_path, monkeypatch):
    h = _load()
    targets = h.parse_targets("foo=http://127.0.0.1:9999/x,"
                              "bar=http://127.0.0.1:8091/y")
    assert [t["name"] for t in targets] == ["foo", "bar"]
    assert targets[0]["slo_p50_ms"] == 800.0      # 非 8091 → 800
    assert targets[1]["slo_p50_ms"] == 500.0      # 名字含 8091 → 500
    fake = _fake_http([_ok(30)] * 10)
    monkeypatch.setattr(h, "_http", fake)
    rep = h.run(targets=targets, samples=2, out_dir=tmp_path / "out", now=NOW)
    assert rep["status"] == "ok"
    assert [t["name"] for t in rep["targets"]] == ["foo", "bar"]
    assert len(fake.calls) == 4


def test_invalid_targets_spec():
    h = _load()
    with pytest.raises(ValueError):
        h.parse_targets("no_url_here")
    with pytest.raises(ValueError):
        h.parse_targets("foo=ftp://x")
    with pytest.raises(ValueError):
        h.parse_targets("")


# ------------------------------------------------ 注入异常 / 参数校验 / 不写盘

def test_http_injection_exception_tolerated(tmp_path, monkeypatch):
    h = _load()
    calls = {"n": 0}

    def boom(url, timeout=5.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated timeout")
        return _ok(50)

    monkeypatch.setattr(h, "_http", boom)
    rep = h.run(out_dir=tmp_path / "out", now=NOW)
    assert rep["status"] in ("warn", "ok")         # 不抛异常
    portal = _target(rep, "portal_8734")
    assert portal["attempts"][0]["ok"] is False
    assert "TimeoutError" in (portal["attempts"][0]["error"] or "")


def test_invalid_params_fail_visible(tmp_path, monkeypatch):
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http([_ok(50)]))
    rep = h.run(samples=0, out_dir=tmp_path / "out", now=NOW)
    assert rep["ok"] is False and rep["status"] == "fail"
    assert "--samples" in rep["error"]
    rep2 = h.run(timeout=0, out_dir=tmp_path / "out", now=NOW)
    assert rep2["ok"] is False and "--timeout" in rep2["error"]


def test_no_write(tmp_path, monkeypatch):
    responses = [_ok(50)] * 9
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http(responses))
    rep = h.run(out_dir=tmp_path / "out", now=NOW, write_md=False)
    assert rep["report_md"] is None
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------- CLI

def _cli(h):
    return h


def test_cli_json_ok_exit_code(tmp_path, monkeypatch, capsys):
    responses = [_ok(60)] * 9
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http(responses))
    rc = h.main(["--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "ok"
    assert data["summary"]["total"] == 3


def test_cli_fail_exit_code(tmp_path, monkeypatch, capsys):
    responses = [_fail()] * 3 + [_ok(20)] * 6
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http(responses))
    rc = h.main(["--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["status"] == "fail"


def test_cli_warn_exit_code(tmp_path, monkeypatch, capsys):
    responses = [_ok(50), _fail(), _ok(60)] + [_ok(20)] * 3 + [_ok(30)] * 3
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http(responses))
    rc = h.main(["--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "warn"


def test_cli_md_written_to_out_dir(tmp_path, monkeypatch, capsys):
    responses = [_ok(60)] * 9
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http(responses))
    out = tmp_path / "deliverables"
    rc = h.main(["--json", "--out-dir", str(out)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert Path(data["report_md"]).parent == out
    assert list(out.glob("2026-10-31_黄忠性能SLO_*.md"))


def test_cli_invalid_targets_rc2(tmp_path, monkeypatch, capsys):
    h = _load()
    monkeypatch.setattr(h, "_http", _fake_http([_ok(50)]))
    rc = h.main(["--json", "--targets", "bad-format"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False and "name=url" in data["error"]
