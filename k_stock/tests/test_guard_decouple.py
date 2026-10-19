# -*- coding: utf-8 -*-
"""K-STOCK-20261019-GUARDDECOUPLE：守卫与主链解耦测试。
默认路径 Ollama 信号降级（GLM 主链可达放行 / 不可达中止），
KSTOCK_FORCE_OLLAMA=1 恢复硬守卫。monkeypatch 隔离，不碰真 data/、不发真网络请求。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))   # 仓库根（k_stock 命名空间包入口）

from k_stock import run_poc                   # noqa: E402


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(run_poc, "GUARD_LOG", tmp_path / "guard_aborts.jsonl")
    monkeypatch.setattr(run_poc, "HEARTBEAT", tmp_path / "last_run.json")
    monkeypatch.setattr(run_poc, "OUT_DIR", tmp_path / "data")
    monkeypatch.delenv("KSTOCK_FORCE_OLLAMA", raising=False)   # 默认路径防环境干扰
    monkeypatch.setattr(run_poc, "run_one",
                        lambda stock, context="", mock=False, auto=False:
                        {"outputs": {"D1": {"action": "BUY", "confidence": 0.5}},
                         "degraded": [], "stock": stock})
    return tmp_path


def _argv(monkeypatch, *rest):
    monkeypatch.setattr(sys, "argv", ["run_poc.py", *rest])


def _guard_recs(tmp_path):
    p = tmp_path / "guard_aborts.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def _boom():
    raise ConnectionError("connection refused")


def test_glm_ok_ollama_down_degrades_pass(tmp_path, monkeypatch):
    """GLM 可达 + Ollama 不可达 → WARN 放行（不 exit）+
    reason=ollama_unreachable_degraded 留痕，跑批正常完成心跳 rc=0。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "600519.SH 测试", "--out", str(tmp / "out.json"))
    monkeypatch.setattr(run_poc, "load_guard", _boom)
    monkeypatch.setattr(run_poc, "_glm_probe", lambda: (True, "glm-5.3-flash ok"))
    run_poc.main()                                # 不抛 SystemExit 即放行
    recs = _guard_recs(tmp)
    assert len(recs) == 1
    assert recs[0]["phase"] == "probe_unreachable_degraded"
    assert recs[0]["reason"] == "ollama_unreachable_degraded"
    assert recs[0]["ollama_alive"] is False and recs[0]["glm_ok"] is True
    assert "connection refused" in recs[0]["detail"]
    hb = json.loads((tmp / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 0 and hb["summary"]["ok"] == 1 and hb["summary"]["targets"] == 1
    assert (tmp / "out.json").exists()            # 产物落盘=整批真跑完


def test_glm_down_ollama_down_aborts(tmp_path, monkeypatch):
    """GLM 主链亦不可达 → exit 3 + glm_unreachable 留痕（防全链 LLMUnavailable 级联）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "600519.SH 测试")
    monkeypatch.setattr(run_poc, "load_guard", _boom)
    monkeypatch.setattr(run_poc, "_glm_probe", lambda: (False, "GLM_API_KEY 未配置"))
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3
    recs = _guard_recs(tmp)
    assert len(recs) == 2
    assert recs[0]["reason"] == "ollama_unreachable_degraded"   # 降级留痕先行
    assert recs[1]["phase"] == "glm_unreachable" and recs[1]["latency_s"] is None
    assert "GLM 主链不可达" in recs[1]["reason"]
    hb = json.loads((tmp / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 3 and hb["summary"]["phase"] == "glm_unreachable"


def test_force_ollama_keeps_hard_guard_on_unreachable(tmp_path, monkeypatch):
    """KSTOCK_FORCE_OLLAMA=1 + Ollama 不可达 → 维持 exit 3 + probe_unreachable 留痕，
    不触发降级路径（_glm_probe 不被调用）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KSTOCK_FORCE_OLLAMA", "1")
    _argv(monkeypatch, "600519.SH 测试")
    monkeypatch.setattr(run_poc, "load_guard", _boom)
    monkeypatch.setattr(run_poc, "_glm_probe",
                        lambda: (_ for _ in ()).throw(AssertionError("不应探测 GLM")))
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3
    recs = _guard_recs(tmp)
    assert len(recs) == 1 and recs[0]["phase"] == "probe_unreachable"
    assert recs[0]["ollama_alive"] is False and "connection refused" in recs[0]["reason"]


def test_hot_over_degrades_pass_by_default(tmp_path, monkeypatch):
    """默认路径热态超阈（Ollama 可达但挤压）→ WARN 放行 + probe_hot_degraded 留痕，
    不再 exit 3（主链 GLM，负载信号与之无关）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "600519.SH 测试", "--out", str(tmp / "out.json"))
    monkeypatch.setattr(run_poc, "load_guard", lambda: (5.0, 9.2))
    monkeypatch.setattr(run_poc, "_competitor_note", lambda: "")
    run_poc.main()
    hb = json.loads((tmp / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 0
    recs = _guard_recs(tmp)
    assert len(recs) == 1 and recs[0]["phase"] == "probe_hot_degraded"
    assert recs[0]["reason"] == "ollama_hot_over_threshold_degraded"
    assert recs[0]["latency_s"] == 9.2


def test_force_ollama_keeps_hard_guard_on_hot(tmp_path, monkeypatch):
    """KSTOCK_FORCE_OLLAMA=1 + 热态超阈 → 维持 exit 3 + probe_hot 留痕（现状语义）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KSTOCK_FORCE_OLLAMA", "1")
    _argv(monkeypatch, "600519.SH 测试")
    monkeypatch.setattr(run_poc, "load_guard", lambda: (5.0, 9.2))
    monkeypatch.setattr(run_poc, "_competitor_note", lambda: "")
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3
    recs = _guard_recs(tmp)
    assert len(recs) == 1 and recs[0]["phase"] == "probe_hot"
    assert recs[0]["ollama_alive"] is True and recs[0]["threshold_s"] == 6.0


def test_glm_probe_no_key_fails_closed(tmp_path, monkeypatch):
    """_glm_probe 判定口径：GLM_API_KEY/ZHIPU_API_KEY 均缺 → (False, 说明)，不抛。"""
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    ok, note = run_poc._glm_probe()
    assert ok is False and "GLM_API_KEY" in note
