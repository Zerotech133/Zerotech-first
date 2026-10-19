# -*- coding: utf-8 -*-
"""K-STOCK-20261019-GUARDLOG：守卫中止落盘 + 跑批心跳测试。
monkeypatch 隔离 GUARD_LOG/HEARTBEAT/OUT_DIR，不碰真 data/。"""
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
    return tmp_path


def _argv(monkeypatch, *rest):
    monkeypatch.setattr(sys, "argv", ["run_poc.py", *rest])


def test_hot_abort_writes_jsonl_and_keeps_exit3(tmp_path, monkeypatch):
    """热态超阈：exit 3 不变 + probe_hot 结构化留痕 + 心跳 rc=3。
    （K-STOCK-20261019-GUARDDECOUPLE 后硬守卫需显式 KSTOCK_FORCE_OLLAMA=1。）"""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KSTOCK_FORCE_OLLAMA", "1")
    _argv(monkeypatch, "600519.SH 测试")
    monkeypatch.setattr(run_poc, "load_guard", lambda: (5.0, 9.2))
    monkeypatch.setattr(run_poc, "_competitor_note", lambda: "")
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3
    lines = (tmp_path / "guard_aborts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    r = json.loads(lines[0])
    assert r["phase"] == "probe_hot" and r["latency_s"] == 9.2
    assert r["t_cold_s"] == 5.0 and r["threshold_s"] == 6.0
    assert r["ollama_alive"] is True and r["watchlist_size"] == 1
    assert "热态超6s" in r["reason"] and "ts" in r
    hb = json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 3 and hb["summary"]["phase"] == "probe_hot"


def test_unreachable_writes_probe_unreachable(tmp_path, monkeypatch):
    """Ollama 不可达：exit 3 不变 + probe_unreachable 留痕（ollama_alive=False）。
    （K-STOCK-20261019-GUARDDECOUPLE 后硬守卫需显式 KSTOCK_FORCE_OLLAMA=1。）"""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KSTOCK_FORCE_OLLAMA", "1")
    _argv(monkeypatch, "600519.SH 测试")
    def _boom():
        raise ConnectionError("connection refused")
    monkeypatch.setattr(run_poc, "load_guard", _boom)
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3
    r = json.loads((tmp_path / "guard_aborts.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert r["phase"] == "probe_unreachable" and r["ollama_alive"] is False
    assert r["latency_s"] is None and "connection refused" in r["reason"]
    hb = json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 3


def test_log_failure_degrades_silently(tmp_path, monkeypatch):
    """落盘失败静默降级：GUARD_LOG 路径被文件占位导致 mkdir 失败，exit 3 不变。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")     # 文件占位 → mkdir(parents) 必炸
    monkeypatch.setattr(run_poc, "GUARD_LOG", blocker / "sub" / "guard_aborts.jsonl")
    monkeypatch.setattr(run_poc, "HEARTBEAT", blocker / "sub" / "last_run.json")
    monkeypatch.setenv("KSTOCK_FORCE_OLLAMA", "1")  # 维持 exit 3 原意，避免降级放行真跑主链
    _argv(monkeypatch, "600519.SH 测试")
    monkeypatch.setattr(run_poc, "load_guard", lambda: (5.0, 9.2))
    monkeypatch.setattr(run_poc, "_competitor_note", lambda: "")
    with pytest.raises(SystemExit) as ei:
        run_poc.main()
    assert ei.value.code == 3                     # 日志失败不改变退出码


def test_append_is_line_atomic(tmp_path, monkeypatch):
    """O_APPEND 单次 os.write：连续两条各占一行、逐行可解析。"""
    _isolate(tmp_path, monkeypatch)
    run_poc._append_guard_log({"phase": "probe_hot", "latency_s": 8.0})
    run_poc._append_guard_log({"phase": "probe_unreachable", "latency_s": None})
    lines = (tmp_path / "guard_aborts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["phase"] == "probe_hot"
    assert json.loads(lines[1])["phase"] == "probe_unreachable"


def test_mock_run_heartbeat_rc0(tmp_path, monkeypatch):
    """mock 正常跑批：心跳 rc=0，摘要含 ok/fail，产物落隔离 OUT_DIR。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "600519.SH 测试", "--mock")
    run_poc.main()                                # 不抛即 rc=0
    hb = json.loads((tmp / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 0
    assert hb["summary"]["targets"] == 1 and hb["summary"]["ok"] == 1
    assert hb["summary"]["fail"] == 0 and hb["summary"]["mock"] is True
    assert hb["elapsed_s"] >= 0 and "ts" in hb
    assert not list((tmp / "guard_aborts.jsonl").glob("*")) if \
        (tmp / "guard_aborts.jsonl").exists() else True
