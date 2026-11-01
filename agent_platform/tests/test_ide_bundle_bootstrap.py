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
   **2026-11-01 STRAT2FIX**：同名不同 id 必须显式消歧——``stats.name_collisions`` 清空，
   raw 冲突留档 ``name_collisions_raw``、别名映射留档 ``name_aliases``，显示名唯一可审计；
B. “缺 bundle 不误红”：仓内 bundle 存在则校验其契约（gate 引导后形态）；
   不存在则用 ``--out`` 生成到 tmp 校验同一契约（等价 bootstrap 可救回声明的形态）；
C. ``--out`` 缺值返回 2（用法错误显式，不静默写错位置）。

离线纪律：只读 registry/prompts 等受控源 + tmp 写盘；零网络、不启服务、
不写主仓 bundle。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "tools" / "build_agent_ide_bundle.py"
REPO_BUNDLE = ROOT / "portal" / "agent_ide_bundle.json"
NAME_CHECKER = ROOT / "tools" / "check_name_uniqueness.py"


def _run_builder(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(BUILDER)] + list(args),
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)


def _display_names(doc):
    return [c.get("name") for c in doc["agents"].values() if c.get("name")]


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
    names = _display_names(doc)
    assert len(names) == len(set(names)), "bundle 显示名必须唯一（避免按名聚合误认席）"
    return doc


def _assert_name_disambiguation(doc):
    """同名冲突显式消歧契约：collisions 清空 → raw 留证 → alias 可审计，不静默吞。"""
    stats = doc["stats"]
    agents = doc["agents"]
    assert stats["name_collisions"] == {}
    assert "name_collisions_raw" in stats and "name_aliases" in stats
    raw = stats["name_collisions_raw"]
    aliases = stats["name_aliases"]
    names = _display_names(doc)
    assert len(names) == len(set(names))
    for raw_name, ids in raw.items():
        assert len(ids) > 1, (raw_name, ids)
        kept = [aid for aid in ids if aid not in aliases]
        assert len(kept) == 1, "同名组必须恰好保留 1 个原名：%r -> kept=%r" % (ids, kept)
        assert agents[kept[0]].get("name") == raw_name
    for aid, meta in aliases.items():
        card = agents[aid]
        assert card.get("name") == meta["alias"], (aid, card.get("name"), meta)
        assert aid in meta["alias"], "别名必须含 agent_id 以可审计"
        assert card.get("cn_name_raw") == meta["cn_name"]
        assert meta["kept"] in agents
    if "STRAT2" in agents and "T1" in agents:
        # 2026-11-01：STRAT2（庞统·策略）与 T1（庞统）必须可区分；若未来仍同名则走 alias。
        s2 = agents["STRAT2"].get("name") or ""
        t1 = agents["T1"].get("name") or ""
        assert s2 != t1, "STRAT2/T1 显示名不得相同（按名聚合会误认席）"
        if "STRAT2" in aliases:
            assert "STRAT2" in s2
        else:
            assert "策略" in s2, "源名应带显式限定词消歧（庞统·策略）"


def test_builder_generates_bundle_to_out(tmp_path):
    """builder --out：受控源确定性生成，产物满足契约（gate bootstrap 同源）。"""
    out = tmp_path / "nested" / "agent_ide_bundle.json"
    r = _run_builder("--out", str(out))
    assert r.returncode == 0, (r.stdout + r.stderr)[-800:]
    assert out.is_file()
    doc = _assert_bundle_contract(out)
    _assert_name_disambiguation(doc)
    assert doc["generated"] and doc["generator"]
    # 确定性：同源重跑 name raw/aliases 与显示名集合不漂移
    out2 = tmp_path / "again" / "agent_ide_bundle.json"
    r2 = _run_builder("--out", str(out2))
    assert r2.returncode == 0, (r2.stdout + r2.stderr)[-800:]
    doc2 = json.loads(out2.read_text(encoding="utf-8"))
    assert doc2["stats"]["name_collisions_raw"] == doc["stats"]["name_collisions_raw"]
    assert doc2["stats"]["name_aliases"] == doc["stats"]["name_aliases"]
    assert sorted(_display_names(doc2)) == sorted(_display_names(doc))


def test_disambiguate_names_unit():
    """同名消歧函数单测（真实数据源名已消歧；此处锁死未来新增冲突的别名策略）。"""
    mod = _load_builder()
    agents = {
        "T1": {"cn_name": "庞统", "name": "庞统"},
        "STRAT2": {"cn_name": "庞统", "name": "庞统"},
    }
    org = {"departments": [{"formal_agents": ["T1"], "liaison_agents": [],
                            "agent_profiles": [{"agent_id": "T1"}]}]}
    raw, aliases = mod.disambiguate_names(agents, org)
    assert raw == {"庞统": ["STRAT2", "T1"]}
    assert agents["T1"]["cn_name"] == "庞统", "org.json 已注册席位保留原名"
    assert agents["STRAT2"]["cn_name"] == "庞统（STRAT2）"
    assert agents["STRAT2"]["cn_name_raw"] == "庞统"
    assert aliases["STRAT2"] == {"cn_name": "庞统", "alias": "庞统（STRAT2）",
                                 "kept": "T1"}
    assert agents["STRAT2"]["name_alias"]["kept"] == "T1"
    unchanged = {"A": {"cn_name": "甲"}}
    assert mod.disambiguate_names(unchanged, {}) == ({}, {})


def test_generated_bundle_passes_name_uniqueness_check(tmp_path):
    """生成产物过仓内名称唯一性检查器（R1 编内唯一 / R3 HR 名册唯一）。"""
    root = tmp_path / "root"
    (root / "portal").mkdir(parents=True)
    shutil.copytree(ROOT / "agent_platform" / "registry",
                    root / "agent_platform" / "registry")
    shutil.copytree(ROOT / "agent_platform" / "hr", root / "agent_platform" / "hr")
    out = root / "portal" / "agent_ide_bundle.json"
    r = _run_builder("--out", str(out))
    assert r.returncode == 0, (r.stdout + r.stderr)[-800:]
    spec = importlib.util.spec_from_file_location(
        "name_uniqueness_under_test", NAME_CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report = checker.check(root)
    assert report.get("ok") is True, report.get("violations")


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "bundle_builder_under_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
