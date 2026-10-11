# -*- coding: utf-8 -*-
"""K-STOCK-137g P2 · 经理实体加固回归（被测：k_stock/paper/manager.py）

覆盖：hire 正常路径/查重/非 live 拒/模型不在目录拒（137c fail-closed）/
models.json 损坏拒/mid·source·note 非法拒/fire 周期/tmp 账本原子写无残留。
全部 monkeypatch 临时目录，真账本与真档案零触碰。
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAPER = HERE / ".." / "paper"
sys.path.insert(0, str(PAPER))

import manager  # noqa: E402
from manager import Reject, fire, hire, hires_list, load_profile  # noqa: E402


def _setup(tmp: Path):
    """monkeypatch 账本/档案目录/ models.json 根到临时区，造一名 live 经理。"""
    manager.HIRES_PATH = tmp / "manager_hires.json"
    mdir = tmp / "managers"
    mdir.mkdir()
    manager.MANAGERS_DIR = mdir
    manager.HERE = tmp  # _hire_price 读 HERE/"models.json"
    (tmp / "models.json").write_text(json.dumps(
        {"models": [{"model": "GLM-5.2", "coef_vs_glm52": 1.0}]}), encoding="utf-8")
    _profile(tmp, "bull_desk", "live")


def _profile(tmp: Path, mid: str, status: str, model: str = "GLM-5.2",
             base: int = 99):
    (manager.MANAGERS_DIR / f"{mid}.json").write_text(json.dumps(
        {"id": mid, "name": mid, "status": status, "model": model,
         "hire_base_price_cny": base}), encoding="utf-8")


def _rejects(fn, why: str):
    try:
        fn()
        raise AssertionError(why)
    except Reject:
        pass


def test_hire_normal_path_price_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        # coef=0.5 → 聘请价 = 99 × 0.5 = 50（快照落账本）
        (Path(tmp) / "models.json").write_text(json.dumps(
            {"models": [{"model": "GLM-5.2", "coef_vs_glm52": 0.5}]}),
            encoding="utf-8")
        rec = hire("bull_desk", "qd", note="首单")
        assert rec["status"] == "active"
        assert rec["hire_price_cny"] == 50
        rows = hires_list()
        assert len(rows) == 1 and rows[0]["hire_price_cny"] == 50
        assert json.loads(manager.HIRES_PATH.read_text(encoding="utf-8")
                          )["hires"][0]["status"] == "active"
    print("PASS test_hire_normal_path_price_snapshot")


def test_hire_duplicate_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        hire("bull_desk", "qd")
        _rejects(lambda: hire("bull_desk", "qd"), "同 mid+source 重复雇佣应拒")
        hire("bull_desk", "qpzm")  # 同人可被多源雇
        fire("bull_desk", "qd")
        hire("bull_desk", "qd")    # fire 后可再雇
    print("PASS test_hire_duplicate_rejected")


def test_hire_rejects_non_live():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _setup(tmp)
        for st in ("retired", "training", "design"):
            _profile(tmp, "bull_desk", st)
            _rejects(lambda: hire("bull_desk", "qd"), f"status={st} 应拒")
    print("PASS test_hire_rejects_non_live")


def test_hire_rejects_model_not_in_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _setup(tmp)
        _profile(tmp, "bull_desk", "live", model="evil-model")
        _rejects(lambda: hire("bull_desk", "qd"), "模型不在目录应拒（137c fail-closed）")
        _rejects(lambda: load_profile("bull_desk"), "load_profile 计价同样应拒")
    print("PASS test_hire_rejects_model_not_in_catalog")


def test_hire_rejects_broken_models_json():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _setup(tmp)
        (tmp / "models.json").write_text("{bad json!!", encoding="utf-8")
        _rejects(lambda: hire("bull_desk", "qd"), "models.json 损坏应拒（不静默 coef=1.0）")
    print("PASS test_hire_rejects_broken_models_json")


def test_hire_rejects_bad_mid():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        for bad in ("../evil", "UPPER", "a" * 33, "", "has space", None):
            _rejects(lambda b=bad: hire(b, "qd"), f"非法 mid {bad!r} 应拒")
    print("PASS test_hire_rejects_bad_mid")


def test_hire_rejects_bad_source_and_note():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        _rejects(lambda: hire("bull_desk", "bad source!"), "非法 source 应拒")
        _rejects(lambda: hire("bull_desk", "a" * 33), "超长 source 应拒")
        _rejects(lambda: hire("bull_desk", "qd", note="x" * 201), "超长 note 应拒")
        _rejects(lambda: hire("bull_desk", "qd", note=123), "非字符串 note 应拒")
    print("PASS test_hire_rejects_bad_source_and_note")


def test_fire_cycle_and_no_active():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        hire("bull_desk", "qd")
        rec = fire("bull_desk", "qd")
        assert rec["status"] == "ended" and rec.get("fired_at")
        rows = hires_list()
        assert len(rows) == 1 and rows[0]["status"] == "ended"  # 历史保留
        _rejects(lambda: fire("bull_desk", "qd"), "二次 fire 应拒")
        _rejects(lambda: fire("bull_desk", "ai_team"), "无 active 应拒")
    print("PASS test_fire_cycle_and_no_active")


def test_ledger_atomic_write_no_tmp_leftover():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _setup(tmp)
        hire("bull_desk", "qd")
        hire("bull_desk", "qpzm")
        fire("bull_desk", "qd")
        assert manager.HIRES_PATH.exists()
        leftovers = [p.name for p in tmp.iterdir() if ".tmp." in p.name]
        assert leftovers == [], f"写后目录不应残留 tmp 文件: {leftovers}"
        d = json.loads(manager.HIRES_PATH.read_text(encoding="utf-8"))
        assert len(d["hires"]) == 2
    print("PASS test_ledger_atomic_write_no_tmp_leftover")


if __name__ == "__main__":
    test_hire_normal_path_price_snapshot()
    test_hire_duplicate_rejected()
    test_hire_rejects_non_live()
    test_hire_rejects_model_not_in_catalog()
    test_hire_rejects_broken_models_json()
    test_hire_rejects_bad_mid()
    test_hire_rejects_bad_source_and_note()
    test_fire_cycle_and_no_active()
    test_ledger_atomic_write_no_tmp_leftover()
    print("9/9 PASS")
