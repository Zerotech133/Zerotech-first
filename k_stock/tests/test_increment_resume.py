# -*- coding: utf-8 -*-
"""K-STOCK-20261020-INCREMENT：run_poc 逐只落盘 + 幂等续跑测试。
monkeypatch 隔离 OUT_DIR，不碰真 data/；run_one 全程 mock，不碰 LLM。"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))   # 仓库根（k_stock 命名空间包入口）

from k_stock import run_poc                   # noqa: E402


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(run_poc, "OUT_DIR", tmp_path / "data")
    monkeypatch.setattr(run_poc, "GUARD_LOG", tmp_path / "guard_aborts.jsonl")
    monkeypatch.setattr(run_poc, "HEARTBEAT", tmp_path / "last_run.json")
    return tmp_path


def _argv(monkeypatch, *rest):
    monkeypatch.setattr(sys, "argv", ["run_poc.py", *rest])


def _batch_file(tmp: Path, *targets: str) -> str:
    """多只目标走 --batch（stock 位置参数 nargs='?' 只收一只）。"""
    bf = tmp / "watchlist_test.txt"
    bf.write_text("\n".join(targets) + "\n", encoding="utf-8")
    return str(bf)


def _fake_result(stock: str) -> dict:
    d1 = {"action": "HOLD", "confidence": 0.5}
    if "300489" in stock:
        d1 = {"action": "SELL", "confidence": 0.65}
    return {"outputs": {"D1": d1}, "degraded": [], "stock": stock,
            "asof": "2026-10-20T12:00:00", "mock": True, "auto_context": False}


def _ledger_path() -> Path:
    return run_poc._results_log_path()


def _bulk_json(tmp: Path) -> list:
    files = sorted((tmp / "data").glob("poc_2*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_append_per_stock_immediately(tmp_path, monkeypatch):
    """逐只追加内容与时序：每只五节点一跑完立即落一行（第二只开跑时
    账本已含第一只），行含 ts/code/节点结果摘要，逐行可解析。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "--batch", _batch_file(tmp, "600519.SH 测试",
                                              "300489.SZ 宁德时代"), "--mock")
    lines_at_call = []

    def fake_run_one(stock, context="", mock=False, auto=False):
        lp = _ledger_path()                   # 时序探针：本轮开跑时账本已有几行
        lines_at_call.append(
            len(lp.read_text(encoding="utf-8").splitlines()) if lp.exists() else 0)
        return _fake_result(stock)

    monkeypatch.setattr(run_poc, "run_one", fake_run_one)
    run_poc.main()
    assert lines_at_call == [0, 1]            # 第二只开跑前第一只已落盘
    lines = _ledger_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    r1, r2 = (json.loads(x) for x in lines)
    assert r1["code"] == "600519.SH" and r2["code"] == "300489.SZ"
    assert r1["ts"] and r2["ts"]
    assert r1["outputs"]["D1"]["action"] == "HOLD"      # 节点结果摘要在账
    assert r2["outputs"]["D1"]["action"] == "SELL"
    assert r1["stock"] == "600519.SH 测试" and r1["mock"] is True


def test_resume_skips_done_and_backfills_bulk_json(tmp_path, monkeypatch):
    """续跑跳过已有：账本已有 600519.SH（含一条坏行+空行也不影响判定），
    重跑只补 300489.SZ，整盘 json 回填账本记录仍含全部标的。"""
    tmp = _isolate(tmp_path, monkeypatch)
    (tmp / "data").mkdir(parents=True)
    done_rec = {"ts": "2026-10-20T07:30:00", "code": "600519.SH",
                "stock": "600519.SH 测试", "outputs": {"D1": {"action": "SELL",
                "confidence": 0.9}}, "degraded": [], "mock": False}
    lp = tmp / "data" / f"poc_results_{datetime.now():%Y%m%d}.jsonl"
    lp.write_text("not-a-json-line\n\n"            # 坏行/空行：宁放过不误跳
                  + json.dumps(done_rec, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    _argv(monkeypatch, "--batch", _batch_file(tmp, "600519.SH 测试",
                                              "300489.SZ 宁德时代"), "--mock")
    called = []
    monkeypatch.setattr(run_poc, "run_one",
                        lambda stock, **kw: (called.append(stock),
                                             _fake_result(stock))[1])
    run_poc.main()
    assert called == ["300489.SZ 宁德时代"]   # 已有完整结果的不重跑
    bulk = _bulk_json(tmp)
    assert len(bulk) == 2
    assert bulk[0]["code"] == "600519.SH"     # 账本记录回填整盘 json
    assert bulk[0]["outputs"]["D1"]["action"] == "SELL"
    assert bulk[1]["stock"].startswith("300489.SZ") and "error" not in bulk[1]
    lines = lp.read_text(encoding="utf-8").splitlines()   # 坏行+空行+账本行+新行
    assert len(lines) == 4 and json.loads(lines[3])["code"] == "300489.SZ"


def test_force_reruns_all(tmp_path, monkeypatch):
    """--force 不跳过：账本已有 600519.SH 仍全量重跑，账本同 code 追加多行
    （读侧取最后一条，后写为准）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    (tmp / "data").mkdir(parents=True)
    lp = tmp / "data" / f"poc_results_{datetime.now():%Y%m%d}.jsonl"
    lp.write_text(json.dumps({"ts": "2026-10-20T07:30:00", "code": "600519.SH",
                              "stock": "600519.SH 测试", "outputs": {},
                              "degraded": []}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    _argv(monkeypatch, "600519.SH 测试", "--mock", "--force")
    called = []
    monkeypatch.setattr(run_poc, "run_one",
                        lambda stock, **kw: (called.append(stock),
                                             _fake_result(stock))[1])
    run_poc.main()
    assert called == ["600519.SH 测试"]       # force 下不跳过
    lines = lp.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["outputs"]["D1"]["action"] == "HOLD"


def test_flush_failure_degrades_not_crash(tmp_path, monkeypatch):
    """落盘失败降级不崩：账本路径被目录占位导致 os.open 必炸，主流程继续、
    rc=0、整盘 json 照常落盘、退出码语义不变。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "600519.SH 测试", "--mock")
    monkeypatch.setattr(run_poc, "run_one",
                        lambda stock, **kw: _fake_result(stock))
    # 先让 _results_log_path 落在已存在的目录上（占位）→ 追加必失败
    blocker = tmp / "data" / f"poc_results_{datetime.now():%Y%m%d}.jsonl"
    blocker.mkdir(parents=True)
    run_poc.main()                            # 不抛即 rc=0
    bulk = _bulk_json(tmp)
    assert len(bulk) == 1 and bulk[0]["stock"] == "600519.SH 测试"
    hb = json.loads((tmp / "last_run.json").read_text(encoding="utf-8"))
    assert hb["rc"] == 0 and hb["summary"]["ok"] == 1


def test_fail_stock_not_in_ledger_retries_on_resume(tmp_path, monkeypatch):
    """FAIL 不记账：五节点没跑完的标的不进账本，续跑会重试（宁重跑不永久跳过）。"""
    tmp = _isolate(tmp_path, monkeypatch)
    _argv(monkeypatch, "--batch", _batch_file(tmp, "600519.SH 测试",
                                              "300489.SZ 宁德时代"), "--mock")

    def fake_run_one(stock, context="", mock=False, auto=False):
        if "600519" in stock:
            raise RuntimeError("GLM unavailable")
        return _fake_result(stock)

    monkeypatch.setattr(run_poc, "run_one", fake_run_one)
    run_poc.main()
    lines = _ledger_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["code"] == "300489.SZ"
    bulk = _bulk_json(tmp)                    # 整盘 json 仍记 FAIL（原行为不变）
    assert bulk[0].get("error") == "GLM unavailable"
    # 续跑：FAIL 过的 600519.SH 会被重试
    monkeypatch.setattr(sys, "argv", ["run_poc.py", "600519.SH 测试", "--mock"])
    monkeypatch.setattr(run_poc, "run_one",
                        lambda stock, **kw: _fake_result(stock))
    run_poc.main()
    lines = _ledger_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["code"] == "600519.SH"
