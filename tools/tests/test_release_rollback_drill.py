# -*- coding: utf-8 -*-
"""release_rollback_drill：W1 发布回滚演练工具测试（隔离根，不碰生产）。

覆盖：
  * 成功链路 A→B→回滚 A（drift=false、内容经 current junction 可读、可重复）；
  * 坏制品 fail-closed（空 manifest / hash 不符 / 缺 manifest / 缺文件）；
  * 指针/junction drift 检出与修复（含反向漂移）；
  * 中途失败恢复（junction 已切/指针未写、junction 丢失）；
  * 隔离安全：拒绝生产 release root、写入仅限演练根、生产快照不变；
  * CLI --json/--markdown 输出、退出码 0/1/2、temp 根自动清理与 --keep/--cleanup。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "release_rollback_drill.py"
REPO = TOOL.parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("release_rollback_drill_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _legion(mod):
    return mod.load_legion_module()


def _scenario(report, sid):
    return next(s for s in report["scenarios"] if s["id"] == sid)


def _checks(report, sid):
    return {c["name"]: c for c in _scenario(report, sid)["checks"]}


def _assert_checks(report, sid, names):
    checks = _checks(report, sid)
    for name in names:
        assert name in checks, "缺检查项 %s（场景 %s）" % (name, sid)
        assert checks[name]["ok"] is True, "%s 失败：%s" % (name, checks[name]["detail"])


@pytest.fixture(scope="module")
def drill(tmp_path_factory):
    """整体演练一次（显式隔离根 + 保留现场），供各断言复用，避免重复跑 junction 操作。"""
    mod = _load()
    legion = _legion(mod)
    before = mod.production_snapshot(legion)
    root = tmp_path_factory.mktemp("drill_full")
    report = mod.run_drill(release_root=root / "release", keep=True)
    after = mod.production_snapshot(legion)
    return mod, report, before, after


def _cli(*args, timeout=180):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


# ---------------------------------------------------------------------------
# 整体演练与逐场景断言
# ---------------------------------------------------------------------------
def test_full_drill_passes_all_scenarios(drill):
    mod, report, _before, _after = drill
    assert report["ok"] is True, report.get("error")
    assert report["schema"] == mod.SCHEMA
    assert [s["id"] for s in report["scenarios"]] == list(mod.DEFAULT_SCENARIOS) + ["isolation_safety"]
    assert report["summary"]["scenarios_total"] == 5
    assert report["summary"]["scenarios_passed"] == 5
    assert report["summary"]["scenarios_failed"] == 0
    assert report["summary"]["checks_failed"] == 0
    assert report["summary"]["checks_total"] >= 40
    assert report["platform"]["link_kind"] == ("junction" if os.name == "nt" else "symlink")
    assert report["duration_ms"] >= 0


def test_happy_path_ab_rollback(drill):
    _mod, report, _before, _after = drill
    _assert_checks(report, "happy_ab_rollback", [
        "promote_a.junction_switched",
        "promote_a.pointer_version",
        "promote_a.junction_version",
        "promote_a.drift_false",
        "promote_a.current_content",
        "promote_b.pointer_version",
        "promote_b.junction_version",
        "promote_b.drift_false",
        "promote_b.current_content",
        "promote_b.prev_junction_kept",
        "rollback_a.pointer_version",
        "rollback_a.junction_version",
        "rollback_a.drift_false",
        "rollback_a.current_content",
        "repeat_rollback_a.drift_false",
        "repeat_rollback_a.current_content",
        "no_orphan_temp_junction",
    ])


def test_bad_artifact_fail_closed(drill):
    _mod, report, _before, _after = drill
    for variant in ("empty_manifest", "hash_mismatch", "missing_manifest", "missing_file"):
        _assert_checks(report, "bad_artifact_rejected", [
            variant + ".rejected",
            variant + ".fail_closed",
            variant + ".pointer_only_rejected",
        ])
    _assert_checks(report, "bad_artifact_rejected", [
        "switch_current_link.rejected",
        "recover_after_bad.drift_false",
        "no_orphan_temp_junction",
    ])


def test_pointer_junction_drift_detected_and_repaired(drill):
    _mod, report, _before, _after = drill
    _assert_checks(report, "drift_detected", [
        "pointer_only.pointer_moved",
        "pointer_only.junction_not_moved",
        "pointer_only.drift_detected",
        "pointer_only.current_content_still_a",
        "repair_by_promote.drift_false",
        "repair_by_promote.pointer_version",
        "junction_only.pointer_still_b",
        "junction_only.junction_moved",
        "junction_only.drift_detected",
        "repair_by_rollback.drift_false",
        "repair_by_rollback.current_content",
    ])


def test_mid_failure_recovery(drill):
    _mod, report, _before, _after = drill
    _assert_checks(report, "mid_failure_recovery", [
        "crash_after_switch.pointer_old",
        "crash_after_switch.junction_new",
        "crash_after_switch.drift_detected",
        "recover_from_crash.drift_false",
        "recover_from_crash.current_content",
        "junction_missing.simulated",
        "junction_missing.detected",
        "recover_missing_junction.drift_false",
        "recover_missing_junction.current_content",
        "post_fault_publish_b.drift_false",
        "post_fault_rollback_a.drift_false",
        "post_fault_rollback_a.current_content",
    ])


def test_isolation_safety_and_production_untouched(drill):
    mod, report, before, after = drill
    _assert_checks(report, "isolation_safety", [
        "release_root_isolated",
        "writes_confined",
        "production_untouched",
    ])
    prod = Path(report["production"]["release_root"])
    assert prod == Path(_legion(mod).DEFAULT_RELEASE_ROOT)
    # 演练前后生产 current.json/junction 快照零变化（只读校验）
    assert before == after
    assert report["safety"]["isolated_from_production"] is True
    assert report["safety"]["writes_confined_to_drill_root"] is True
    assert report["safety"]["production_untouched"] is True
    # 演练根与生产根无路径重叠
    assert not mod.paths_overlap(Path(report["release_root"]), prod)


def test_explicit_root_kept_by_default(drill):
    _mod, report, _before, _after = drill
    assert report["release_root_explicit"] is True
    assert report["cleanup"]["requested"] is False
    assert report["cleanup"]["exists_after"] is True
    assert Path(report["release_root"]).is_dir()


# ---------------------------------------------------------------------------
# 安全边界：拒绝生产 root / 仓库 / 非空显式根
# ---------------------------------------------------------------------------
def test_rejects_production_release_root(tmp_path):
    mod = _load()
    prod = Path(_legion(mod).DEFAULT_RELEASE_ROOT)
    # 等价路径、子目录、父目录都视为重叠
    for candidate in (prod, prod / "__drill_should_refuse", prod.parent):
        with pytest.raises(RuntimeError):
            mod.assert_isolated(candidate, prod)
    # 仓库工作树同样拒绝（避免脏化 git）
    with pytest.raises(RuntimeError):
        mod.assert_isolated(REPO / "_drill_should_refuse", prod)
    # run_drill 直接拒跑，不落任何文件
    target = prod / "__drill_should_refuse"
    report = mod.run_drill(release_root=target, cleanup=True)
    assert report["ok"] is False
    assert "拒绝" in report["error"]
    assert report["scenarios"] == []
    assert not mod._path_lexists(target)


def test_rejects_nonempty_explicit_root(tmp_path):
    mod = _load()
    root = tmp_path / "nonempty"
    root.mkdir()
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    report = mod.run_drill(release_root=root)
    assert report["ok"] is False
    assert "非空" in report["error"]
    # 现场原封不动，未开始演练
    assert (root / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (root / "happy_ab_rollback").exists()


# ---------------------------------------------------------------------------
# 自动 temp 根、子集选择、清理语义
# ---------------------------------------------------------------------------
def test_auto_temp_root_cleaned_with_scenario_subset():
    mod = _load()
    report = mod.run_drill(scenarios=["drift_detected"])
    assert report["ok"] is True
    assert report["release_root_explicit"] is False
    assert report["release_root_under_temp"] is True
    assert [s["id"] for s in report["scenarios"]] == ["drift_detected", "isolation_safety"]
    assert report["cleanup"]["requested"] is True
    assert report["cleanup"]["removed"] is True
    assert not Path(report["release_root"]).exists()


def test_keep_temp_root_then_cleanup():
    mod = _load()
    report = mod.run_drill(keep=True, scenarios=["happy_ab_rollback"])
    root = Path(report["release_root"])
    assert report["ok"] is True
    assert root.is_dir()
    assert report["cleanup"]["requested"] is False
    info = mod.cleanup_root(root)
    assert info["removed"] is True
    assert not root.exists()


def test_select_scenarios_contract():
    mod = _load()
    assert mod._select_scenarios("") == list(mod.DEFAULT_SCENARIOS)
    # 子集按固定顺序输出（与传入顺序无关），且不受 isolation_safety 影响
    assert mod._select_scenarios("drift_detected,happy_ab_rollback") == [
        "happy_ab_rollback", "drift_detected"]
    with pytest.raises(ValueError):
        mod._select_scenarios("nope")
    assert "isolation_safety" not in mod.DEFAULT_SCENARIOS


# ---------------------------------------------------------------------------
# CLI：--json / --markdown / 退出码 / 清理
# ---------------------------------------------------------------------------
def test_cli_json_default_temp_cleanup():
    mod = _load()
    before = mod.production_snapshot(_legion(mod))
    proc = _cli("--json")
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["schema"] == mod.SCHEMA
    assert report["cleanup"]["removed"] is True
    assert not Path(report["release_root"]).exists()
    assert mod.production_snapshot(_legion(mod)) == before


def test_cli_markdown_report(tmp_path):
    mod = _load()
    root = tmp_path / "md"
    proc = _cli("--markdown", "--release-root", str(root))
    assert proc.returncode == 0, proc.stderr[-800:]
    text = proc.stdout
    assert "# W1 发布回滚演练报告" in text
    assert "**结果：PASS**" in text
    for sid in mod.DEFAULT_SCENARIOS:
        assert sid in text
    assert "生产 current.json/junction 快照未变" in text
    assert "不重启 8787/8103" in text
    # 显式根默认保留现场
    assert root.is_dir()


def test_cli_explicit_root_cleanup_flag(tmp_path):
    root = tmp_path / "clean"
    proc = _cli("--json", "--release-root", str(root), "--cleanup")
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["cleanup"]["requested"] is True
    assert report["cleanup"]["removed"] is True
    assert not root.exists()


def test_cli_refuses_production_root():
    mod = _load()
    target = Path(_legion(mod).DEFAULT_RELEASE_ROOT) / "__drill_cli_refuse"
    proc = _cli("--json", "--release-root", str(target))
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert "拒绝" in report["error"]
    assert not mod._path_lexists(target)


def test_cli_unknown_scenario_returns_usage_error():
    proc = _cli("--scenario", "nope", "--json")
    assert proc.returncode == 2
    assert "未知场景" in proc.stderr
