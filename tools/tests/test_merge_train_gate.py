# -*- coding: utf-8 -*-
"""W1/UF-09 merge train 接入测试：devtask merge 门 + merge_preflight 钩子（task-19）。

不实际执行 devtask merge（当前处于禁并期且禁止 git 写操作）：
- 直接调用 `devtask._merge_train_gate` / `merge_preflight.merge_train_preflight`，
  用 **注入的 now**（ISO 时间串）与文件清单做判定；
- 用源码顺序断言守护挂载点（质量闸后 → merge train → 组合/内核门；docs 早退后 → merge train → test_gate）；
- `devtask.py --help` 子进程验收 `--train-force` 可见。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
PY = sys.executable

NOW_WINDOW = "2026-11-02T16:45:00+08:00"      # 主窗口内
NOW_FORBIDDEN = "2026-11-02T10:00:00+08:00"   # 禁并期（09:15–15:05）
NOW_OUTSIDE = "2026-11-02T15:30:00+08:00"     # 窗外、非禁并期

DEVTASK_SRC = (TOOLS / "devtask.py").read_text(encoding="utf-8")
PREFLIGHT_SRC = (TOOLS / "merge_preflight.py").read_text(encoding="utf-8")


def _load(name, filename):
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def devtask_mod():
    return _load("devtask_under_test_mt", "devtask.py")


@pytest.fixture(scope="module")
def preflight_mod():
    return _load("merge_preflight_under_test_mt", "merge_preflight.py")


# ---------- devtask._merge_train_gate ----------
def test_devtask_gate_window_allows(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["portal/api_kg.py"], now=NOW_WINDOW)
    assert ok, detail
    assert "主窗口" in detail


def test_devtask_gate_forbidden_blocks(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["portal/api_kg.py"], now=NOW_FORBIDDEN)
    assert not ok, detail
    assert "禁并期" in detail


def test_devtask_gate_outside_blocks(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["portal/api_kg.py"], now=NOW_OUTSIDE)
    assert not ok, detail
    assert "不在合并窗口" in detail


def test_devtask_gate_docs_bypass(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["docs/plans/x.md"], now=NOW_FORBIDDEN)
    assert ok, detail
    assert "旁路" in detail


def test_devtask_gate_compositions_bypass(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["compositions/data-warehouse/manifest.yaml"], now=NOW_FORBIDDEN)
    assert ok, detail
    assert "旁路" in detail


def test_devtask_gate_force_allows(devtask_mod):
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["portal/api_kg.py"], force=True, now=NOW_FORBIDDEN)
    assert ok, detail
    assert "紧急放行" in detail


def test_devtask_gate_missing_tool_fail_visible(devtask_mod, monkeypatch):
    monkeypatch.setitem(sys.modules, "merge_train", None)
    ok, detail = devtask_mod._merge_train_gate(
        "T-MT", ["portal/api_kg.py"], now=NOW_WINDOW)
    assert not ok, detail
    assert "组件缺失" in detail


# ---------- merge_preflight.merge_train_preflight ----------
def test_preflight_window_allows(preflight_mod):
    ok, detail = preflight_mod.merge_train_preflight(
        ["portal/api_kg.py"], now=NOW_WINDOW)
    assert ok, detail
    assert "主窗口" in detail


def test_preflight_forbidden_blocks(preflight_mod):
    ok, detail = preflight_mod.merge_train_preflight(
        ["portal/api_kg.py"], now=NOW_FORBIDDEN)
    assert not ok, detail
    assert "禁并期" in detail


def test_preflight_outside_blocks(preflight_mod):
    ok, detail = preflight_mod.merge_train_preflight(
        ["portal/api_kg.py"], now=NOW_OUTSIDE)
    assert not ok, detail
    assert "不在合并窗口" in detail


def test_preflight_docs_bypass_allows(preflight_mod):
    ok, detail = preflight_mod.merge_train_preflight(
        ["compositions/acad1-legion/manifest.yaml"], now=NOW_FORBIDDEN)
    assert ok, detail
    assert "旁路" in detail


def test_preflight_missing_tool_fail_visible(preflight_mod, monkeypatch):
    monkeypatch.setitem(sys.modules, "merge_train", None)
    ok, detail = preflight_mod.merge_train_preflight(
        ["portal/api_kg.py"], now=NOW_WINDOW)
    assert not ok, detail
    assert "缺 tools/merge_train.py" in detail


# ---------- 挂载点/CLI 守护 ----------
def test_help_contains_train_force():
    p = subprocess.run([PY, "-X", "utf8", str(TOOLS / "devtask.py"), "--help"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "--train-force" in p.stdout


def test_devtask_wiring_order_and_force_trace():
    qg = DEVTASK_SRC.index("qg_ok, qg_detail = _quality_gate(tid)")
    mt = DEVTASK_SRC.index("mt_ok, mt_detail = _merge_train_gate(tid, _bundle_changed")
    ck = DEVTASK_SRC.index("ck_ok, ck_detail = _composition_kernel_gate(tid")
    assert qg < mt < ck, "挂载顺序必须：质量闸 → merge train → 组合/内核门"
    # force 显式化 + 留痕：flag 解析 / 警告 / 工单 note / approval_verdict 事件
    assert 'train_force = "--train-force" in flags' in DEVTASK_SRC
    assert "--train-force 紧急放行" in DEVTASK_SRC
    assert "_emit(\"approval_verdict\", tid, _force_note)" in DEVTASK_SRC
    assert "[W1/UF-09 merge-train force]" in DEVTASK_SRC


def test_preflight_wiring_order():
    ck = PREFLIGHT_SRC.index("_ck_ok, _ck_detail = composition_kernel_preflight(files)")
    docs = PREFLIGHT_SRC.index("纯文档/配置变更 → 放行")
    mt = PREFLIGHT_SRC.index("merge_train_preflight(files)")
    gate = PREFLIGHT_SRC.index("跑测试门：test_gate -Suite")
    assert ck < docs < mt < gate, "挂载顺序必须：组合/内核复检 → docs_only 早退 → merge train → test_gate"
    assert 'print("[merge-gate] FAIL：merge train 未过' in PREFLIGHT_SRC
