# -*- coding: utf-8 -*-
"""fresh worktree 缺 gitignored IDE bundle 的引导/契约回归（2026-10-31 WO-QUAL-BUNDLE-20261031）。

背景
----
``portal/agent_ide_bundle.json`` 是 .gitignore 忽略的 nginx 静态产物；fresh worktree
检出没有它 → ``test_hermes_orchestrator`` 2 例 + ``test_ide_enrich`` 1 例假红（实测
均因读不到 bundle，非代码/测试坏）。修复在 gate 侧：``tools/test_gate.ps1`` 在
**worktree** 且缺件时，用本 checkout 受控源（registry/prompts →
``tools/build_agent_ide_bundle.py``）确定性生成到该 worktree，不入 git、fail-open。

本文件锁定
----------
A. builder 受控生成：``--out`` 生成到指定路径，产物满足 bundle 契约
   （agents/departments/stats/name_collisions 等），且不覆写仓内正式 bundle；
B. “缺 bundle 不误红”：仓内 bundle 存在则校验其契约（gate 引导后形态）；
   不存在则用 ``--out`` 生成到 tmp 校验同一契约（等价 bootstrap 可救回声明的形态）；
C. ``--out`` 缺值返回 2（用法错误显式，不静默写错位置）。

离线纪律：只读 registry/prompts 等受控源 + tmp 写盘；零网络、不启服务、
不写主仓 bundle。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "tools" / "build_agent_ide_bundle.py"
REPO_BUNDLE = ROOT / "portal" / "agent_ide_bundle.json"


def _run_builder(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(BUILDER)] + list(args),
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)


def _assert_bundle_contract(path):
    """bundle 消费契约（test_ide_enrich / hermes_orchestrator 的最小依赖面）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(doc.get("agents"), dict) and doc["agents"], "agents 必须非空"
    assert "A0" in doc["agents"], "组织顶点 A0 必须在 bundle"
    assert isinstance(doc.get("departments"), list) and doc["departments"]
    stats = doc.get("stats") or {}
    for key in ("agents", "departments", "total_cards", "name_collisions"):
        assert key in stats, "stats 缺契约键 %s" % key
    assert stats["name_collisions"] == {}, "同名席位冲突必须在 builder 侧清零"
    return doc


def test_builder_generates_bundle_to_out(tmp_path):
    """builder --out：受控源确定性生成，产物满足契约（gate bootstrap 同源）。"""
    out = tmp_path / "nested" / "agent_ide_bundle.json"
    r = _run_builder("--out", str(out))
    assert r.returncode == 0, (r.stdout + r.stderr)[-800:]
    assert out.is_file()
    doc = _assert_bundle_contract(out)
    assert doc["generated"] and doc["generator"]


def test_builder_out_does_not_touch_repo_bundle(tmp_path):
    """--out 只写指定路径：不得覆写仓内正式 bundle（防测试污染生产静态产物）。"""
    before = REPO_BUNDLE.read_bytes() if REPO_BUNDLE.is_file() else None
    out = tmp_path / "b.json"
    r = _run_builder("--out", str(out))
    assert r.returncode == 0
    after = REPO_BUNDLE.read_bytes() if REPO_BUNDLE.is_file() else None
    assert after == before, "--out 生成不应改动仓内 portal/agent_ide_bundle.json"


def test_missing_bundle_is_recoverable_not_red(tmp_path):
    """fresh worktree 缺 bundle 时：gate bootstrap 可救回（契约与存在时等价）。

    * gate 内跑：bootstrap 已先生成 bundle → 走“存在且契约 OK”分支；
    * fresh worktree 直跑 pytest：bundle 可能仍缺 → 用 --out 生成后校验同一契约，
      证明“缺件”不是测试逻辑坏，可被 gate 引导恢复。
    """
    if REPO_BUNDLE.is_file():
        _assert_bundle_contract(REPO_BUNDLE)
    else:
        out = tmp_path / "generated.json"
        r = _run_builder("--out", str(out))
        assert r.returncode == 0, r.stderr[-400:]
        _assert_bundle_contract(out)


def test_builder_out_missing_value_rc2():
    r = _run_builder("--out")
    assert r.returncode == 2
    assert "用法" in (r.stdout + r.stderr)
