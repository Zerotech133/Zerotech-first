# -*- coding: utf-8 -*-
"""P1.2 测试：组合门 + kernel guard 接入 devtask.merge / merge_preflight（ADR-015）。

测试策略（不碰共享仓 git：禁止 commit/merge/reset）：
- 用临时目录伪造「任务 worktree」，把真实 tools/composition.py、tools/kernel_guard.py
  拷贝进去，构造最小 compositions/ 与 platform/kernel_v1.yaml fixture，直接调用门函数；
- 提交信息通过 monkeypatch devtask._branch_tip_commit_msg 注入（真实 git 分支 tip 语义）；
- 覆盖 fail-visible：worktree/工具缺失、输出非 JSON、工具异常一律拒（fail-closed）。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOLS = ROOT / "tools"
TID = "T-COMP-GATE"

# 最小内核清单：受保护 artifact = platform/kernel_v1.yaml（kernel_guard 自身也会自保护）
KERNEL_YAML = """\
kernel_version: 0.9.0
status: draft
contracts:
  K_test:
    artifacts:
      - path: platform/kernel_v1.yaml
        kind: gate
        status: existing
"""


class _Proc:
    """伪 subprocess.CompletedProcess（供 fake run_tool）。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _load(name, filename):
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def devtask_mod():
    return _load("devtask_under_test", "devtask.py")


@pytest.fixture(scope="module")
def preflight_mod():
    return _load("merge_preflight_under_test", "merge_preflight.py")


# ---------- fixture 构造 ----------
def _write_manifest(root, cid, version="1.0.0", lifecycle="draft", extra_spec=""):
    path = Path(root) / "compositions" / cid / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "apiVersion: zerobound/v1\nkind: Composition\n"
        "metadata:\n  id: %s\n  name: %s\n  version: %s\n  owner: A0\n"
        "  lifecycle: %s\n  tier: product\n  host: lingjie\n"
        "spec:\n  kernel: \">=0.9 <2.0\"\n%s"
        % (cid, cid, version, lifecycle, extra_spec), encoding="utf-8")
    return path


def _write_registry(root, cid, version="1.0.0", lifecycle="draft"):
    path = Path(root) / "compositions" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = {"compositions": [{
        "id": cid, "name": cid, "version": version, "owner": "A0",
        "lifecycle": lifecycle, "tier": "product", "host": "lingjie",
        "manifest": "compositions/%s/manifest.yaml" % cid,
        "status": {"phase": "shadow", "health": "unknown", "validated": False},
    }]}
    path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return path


def _mk_worktree(tmp_path, with_composition=True, with_kernel=True):
    """伪造 <tmp>/.worktrees/<TID>/：真实工具拷贝 + 最小内核清单 fixture。"""
    wt = Path(tmp_path) / ".worktrees" / TID
    (wt / "tools").mkdir(parents=True, exist_ok=True)
    if with_composition:
        shutil.copy2(TOOLS / "composition.py", wt / "tools" / "composition.py")
    if with_kernel:
        shutil.copy2(TOOLS / "kernel_guard.py", wt / "tools" / "kernel_guard.py")
    (wt / "platform").mkdir(exist_ok=True)
    (wt / "platform" / "kernel_v1.yaml").write_text(KERNEL_YAML, encoding="utf-8")
    return wt


def _point_at(tmp_path, devtask_mod, monkeypatch):
    monkeypatch.setattr(devtask_mod, "WT_DIR", str(Path(tmp_path) / ".worktrees"))


# ---------- devtask._composition_kernel_gate ----------
def test_gate_empty_changed_skips(devtask_mod):
    ok, detail = devtask_mod._composition_kernel_gate(TID, "task/" + TID, [])
    assert ok, detail
    assert "跳过" in detail


def test_gate_no_kernel_change_allows(tmp_path, devtask_mod, monkeypatch):
    _mk_worktree(tmp_path)
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(TID, "task/" + TID, ["README.md"])
    assert ok, detail
    assert "kernel_guard" in detail
    assert "composition" not in detail  # 未命中 compositions/**，不跑组合命令


def test_gate_kernel_change_without_adr_rejected(tmp_path, devtask_mod, monkeypatch):
    _mk_worktree(tmp_path)
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["platform/kernel_v1.yaml"])
    assert not ok, detail
    assert "kernel guard" in detail


def test_gate_kernel_change_with_adr_allows(tmp_path, devtask_mod, monkeypatch):
    _mk_worktree(tmp_path)
    _point_at(tmp_path, devtask_mod, monkeypatch)
    monkeypatch.setattr(devtask_mod, "_branch_tip_commit_msg",
                        lambda tid, branch="": "feat: 内核改动 ADR-015")
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["platform/kernel_v1.yaml"])
    assert ok, detail
    assert "ADR-015" in detail or "kernel_guard ✓" in detail


def test_gate_missing_worktree_rejected(tmp_path, devtask_mod, monkeypatch):
    _point_at(tmp_path, devtask_mod, monkeypatch)  # .worktrees 下无 TID 目录
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["platform/kernel_v1.yaml"])
    assert not ok, detail
    assert "worktree" in detail


def test_gate_emergency_env_skips(devtask_mod, monkeypatch):
    """应急通道与既有质量闸同口径：DEVTOOL_MERGE_GATE=0 显式跳过（须留痕）。"""
    monkeypatch.setenv("DEVTOOL_MERGE_GATE", "0")
    ok, detail = devtask_mod._composition_kernel_gate(
        "__NO_SUCH_WORKTREE__", "task/__NO_SUCH_WORKTREE__",
        ["platform/kernel_v1.yaml"])
    assert ok, detail
    assert "DEVTOOL_MERGE_GATE=0" in detail and "跳过" in detail


def test_gate_missing_tool_rejected(tmp_path, devtask_mod, monkeypatch):
    _mk_worktree(tmp_path, with_composition=False, with_kernel=False)
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["compositions/demo/manifest.yaml"])
    assert not ok, detail
    assert "缺 tools/composition.py" in detail


def test_gate_manifest_errors_rejected(tmp_path, devtask_mod, monkeypatch):
    wt = _mk_worktree(tmp_path)
    _write_registry(wt, "demo")
    # 硬引用错误：agent 未登记（validate 必报 error）
    _write_manifest(wt, "demo", extra_spec="  seats:\n    - ref: agent/NOT_REGISTERED\n")
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["compositions/demo/manifest.yaml"])
    assert not ok, detail
    assert "composition validate" in detail
    assert "agent 未登记" in detail


def test_gate_manifest_warnings_allows(tmp_path, devtask_mod, monkeypatch):
    wt = _mk_worktree(tmp_path)
    _write_registry(wt, "demo")
    # 软引用（skill）只产生 warning，不得阻断
    _write_manifest(wt, "demo", extra_spec="  skills:\n    - ref: skill/NOT_REGISTERED\n")
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["compositions/demo/manifest.yaml"])
    assert ok, detail
    assert "composition validate" in detail


def test_gate_plan_drift_rejected(tmp_path, devtask_mod, monkeypatch):
    wt = _mk_worktree(tmp_path)
    _write_registry(wt, "demo", version="1.0.0")
    _write_manifest(wt, "demo", version="1.0.1")  # registry×manifest 版本漂移
    _point_at(tmp_path, devtask_mod, monkeypatch)
    ok, detail = devtask_mod._composition_kernel_gate(
        TID, "task/" + TID, ["compositions/demo/manifest.yaml"])
    assert not ok, detail
    assert "composition plan" in detail
    assert "version_changed" in detail


# ---------- merge_preflight.composition_kernel_check ----------
def test_preflight_no_kernel_change_allows(preflight_mod):
    calls = []

    def fake_run(tool_rel, args):
        calls.append((tool_rel, tuple(args)))
        return _Proc(0, ""), ""

    ok, detail = preflight_mod.composition_kernel_check(["README.md"], fake_run, "")
    assert ok, detail
    assert calls and calls[0][0] == "kernel_guard.py"
    assert calls[0][1][0] == "--files"


def test_preflight_kernel_block_rejected(preflight_mod):
    def fake_run(tool_rel, args):
        return _Proc(1, "[kernel_guard] BLOCK: 未提供 ADR 号"), ""

    ok, detail = preflight_mod.composition_kernel_check(
        ["platform/kernel_v1.yaml"], fake_run, "feat: 没写 ADR")
    assert not ok, detail
    assert "kernel guard" in detail


def test_preflight_manifest_errors_rejected(preflight_mod):
    def fake_run(tool_rel, args):
        if args and args[0] == "plan":
            return _Proc(0, json.dumps({"ok": True, "findings": []})), ""
        return _Proc(1, json.dumps([{"ok": False, "errors": ["schema: boom"]}])), ""

    ok, detail = preflight_mod.composition_kernel_check(
        ["compositions/demo/manifest.yaml"], fake_run, "x ADR-015")
    assert not ok, detail
    assert "composition validate" in detail


def test_preflight_plan_drift_with_ok_flag_rejected(preflight_mod):
    """防语义漂移：即使 ok=true，non-empty findings 也必须拒。"""
    def fake_run(tool_rel, args):
        if tool_rel == "composition.py":
            return _Proc(0, json.dumps({"ok": True, "findings": [
                {"kind": "version_changed", "id": "demo"}]})), ""
        return _Proc(0, ""), ""

    ok, detail = preflight_mod.composition_kernel_check(
        ["compositions/demo/manifest.yaml"], fake_run, "")
    assert not ok, detail
    assert "version_changed" in detail


def test_preflight_bad_json_rejected(preflight_mod):
    def fake_run(tool_rel, args):
        return _Proc(0, "NOT JSON"), ""

    ok, detail = preflight_mod.composition_kernel_check(
        ["compositions/demo/manifest.yaml"], fake_run, "")
    assert not ok, detail
    assert "非 JSON" in detail


def test_preflight_tool_exception_rejected(preflight_mod):
    def fake_run(tool_rel, args):
        return None, "运行 tools/composition.py 异常：TimeoutExpired"

    ok, detail = preflight_mod.composition_kernel_check(
        ["compositions/demo/manifest.yaml"], fake_run, "")
    assert not ok, detail
    assert "异常" in detail


def test_preflight_run_repo_tool_missing_fail_visible(preflight_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(preflight_mod, "HERE", Path(tmp_path))
    proc, err = preflight_mod.run_repo_tool("composition.py", ["plan"])
    assert proc is None
    assert "缺 tools/composition.py" in err


def test_preflight_merge_head_msg_safe(preflight_mod):
    # 无 merge 上下文时返回空串而不是抛异常（由 kernel_guard 判拒）
    msg = preflight_mod.merge_head_commit_msg()
    assert isinstance(msg, str)


# ---------- CLI 冒烟 ----------
def test_devtask_help_works():
    p = subprocess.run([PY, "-X", "utf8", str(TOOLS / "devtask.py"), "--help"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "devtask" in p.stdout
    assert "merge <TID>" in p.stdout
