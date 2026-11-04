# -*- coding: utf-8 -*-
"""P1 组合层工具测试：composition.py（validate/plan/diff/retire）+ kernel_guard.py。

全部用例只读：不写 manifest / registry / 运行时文件（retire 用 dry-run 并断言字节不变）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def _load_composition_module():
    path = ROOT / "tools" / "composition.py"
    spec = importlib.util.spec_from_file_location("composition_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def comp_mod():
    return _load_composition_module()


# ---------- validate / manifest 基线 ----------
def test_manifests_schema_pass():
    rc, out, err = _run(["tools/composition.py", "validate", "--all"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "7 个组合" in out or "组合" in out


def test_templates_schema_pass():
    for name in ("legion", "product", "workflow", "data"):
        path = ROOT / "compositions" / "_templates" / f"template-{name}.yaml"
        assert path.exists(), f"missing {path}"
        rc, out, err = _run(["tools/composition.py", "validate", str(path)])
        assert rc == 0, f"{name}: rc={rc}\n{out}\n{err}"


def test_registry_consistent():
    reg = json.loads((ROOT / "compositions" / "registry.json").read_text(encoding="utf-8"))
    ids = {c["id"] for c in reg["compositions"]}
    dirs = {p.parent.name for p in (ROOT / "compositions").glob("*/manifest.yaml")}
    assert ids == dirs


def test_validate_all_json_is_pure_json():
    rc, out, err = _run(["tools/composition.py", "validate", "--all", "--json"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    rows = json.loads(out)  # stdout 必须是可解析 JSON（人类汇总走 stderr）
    assert len(rows) == 7  # 2026-11-03 dev-legion 入册（6→7）
    assert all(r["ok"] for r in rows)
    assert "汇总" in err


# ---------- kernel_guard ----------
def test_kernel_guard_blocks_kernel_without_adr():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml"])
    assert rc == 1, f"rc={rc}\n{out}\n{err}"
    assert "BLOCK" in out


def test_kernel_guard_allows_kernel_with_adr():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml", "--adr", "ADR-015"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "OK" in out


def test_kernel_guard_ignores_non_kernel():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "README.md"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"


def test_kernel_guard_commit_msg_adr_allows():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "tools/composition.py",
                         "--commit-msg", "feat(P1): plan/diff/retire + kernel guard 收尾 ADR-015"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "ADR-015" in out
    assert "--commit-msg" in out  # 来源可见，便于排查


def test_kernel_guard_commit_msg_without_adr_blocks():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "tools/composition.py",
                         "--commit-msg", "feat: 顺手改内核但没写 ADR 号"])
    assert rc == 1, f"rc={rc}\n{out}\n{err}"
    assert "BLOCK" in out
    assert "--commit-msg" in out  # 提示两条解决路径之一


def test_kernel_guard_list_shows_artifacts():
    rc, out, err = _run(["tools/kernel_guard.py", "--list"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "K0_composition" in out
    assert "platform/kernel_v1.yaml" in out


# ---------- plan ----------
def test_plan_clean_repo():
    rc, out, err = _run(["tools/composition.py", "plan"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "无漂移" in out
    assert "errors=0" in out


def test_plan_json_clean():
    rc, out, err = _run(["tools/composition.py", "plan", "--json"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["strict"] is False
    assert payload["findings"] == []
    assert payload["summary"]["errors"] == 0
    assert payload["registry_count"] == 7  # 2026-11-03 dev-legion 入册（6→7）
    assert payload["manifest_count"] == 7


def test_plan_detects_drift(tmp_path, comp_mod):
    comp = tmp_path / "compositions"
    (comp / "alpha").mkdir(parents=True)
    (comp / "ghost").mkdir(parents=True)
    reg = {"compositions": [
        {"id": "alpha", "name": "A", "version": "1.0.0", "owner": "A0",
         "lifecycle": "draft", "tier": "product", "host": "lingjie",
         "manifest": "compositions/alpha/manifest.yaml"},
        # beta 只在 registry 登记，manifest 缺失（error）
        {"id": "beta", "name": "B", "version": "2.0.0", "owner": "A0",
         "lifecycle": "active", "tier": "product", "host": "lingjie",
         "manifest": "compositions/beta/manifest.yaml"},
    ]}
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    (comp / "alpha" / "manifest.yaml").write_text(
        "apiVersion: zerobound/v1\nkind: Composition\n"
        "metadata:\n  id: alpha\n  name: A\n  version: 1.0.1\n  owner: A0\n"
        "  lifecycle: retired\n  tier: product\n  host: lingjie\nspec: {}\n",
        encoding="utf-8")
    (comp / "ghost" / "manifest.yaml").write_text(
        "apiVersion: zerobound/v1\nkind: Composition\n"
        "metadata:\n  id: ghost\n  name: G\n  version: 0.1.0\n  owner: D1\n"
        "  lifecycle: draft\n  tier: workflow\n  host: lingjie\nspec: {}\n",
        encoding="utf-8")

    findings = comp_mod.plan_drift(reg_path=reg_path, comp_dir=comp)
    kinds = {f["kind"] for f in findings}
    assert {"version_changed", "lifecycle_changed",
            "missing_manifest", "missing_registry"} <= kinds
    levels = {f["kind"]: f["level"] for f in findings}
    assert levels["missing_manifest"] == "error"
    assert levels["missing_registry"] == "warning"
    # 只读：函数不写两个输入文件之外的任何东西（registry/manifest 字节不变）
    assert json.loads(reg_path.read_text(encoding="utf-8"))["compositions"][0]["version"] == "1.0.0"


def test_plan_strict_promotes_warnings(comp_mod):
    warn_only = [{"kind": "version_changed", "id": "x", "level": "warning"}]
    normal = comp_mod.summarize_findings(warn_only, strict=False)
    strict = comp_mod.summarize_findings(warn_only, strict=True)
    assert normal["rc"] == 0 and normal["warnings"] == 1
    assert strict["rc"] == 1 and strict["errors"] == 1 and strict["warnings"] == 0
    hard_err = [{"kind": "missing_manifest", "id": "x", "level": "error"}]
    assert comp_mod.summarize_findings(hard_err, strict=False)["rc"] == 1


# ---------- diff ----------
def test_diff_same_manifest_no_difference():
    path = "compositions/acad1-legion/manifest.yaml"
    rc, out, err = _run(["tools/composition.py", "diff", path, path])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "无差异" in out


def test_diff_registry_snapshot_clean():
    rc, out, err = _run(["tools/composition.py", "diff",
                         "compositions/acad1-legion/manifest.yaml", "--registry"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "无差异" in out
    assert "registry.json#acad1-legion" in out


def test_diff_two_manifests_strict():
    left = "compositions/acad1-legion/manifest.yaml"
    right = "compositions/data-warehouse/manifest.yaml"
    rc, out, _ = _run(["tools/composition.py", "diff", left, right])
    assert rc == 0, f"非 strict 差异只报告 rc={rc}\n{out}"
    assert "metadata.id" in out
    rc, out, err = _run(["tools/composition.py", "diff", left, right, "--strict"])
    assert rc == 1, f"strict 差异应 rc=1，实际 {rc}\n{out}\n{err}"
    assert "差异=" in out


def test_diff_json_shape():
    rc, out, err = _run(["tools/composition.py", "diff",
                         "compositions/acad1-legion/manifest.yaml",
                         "compositions/data-warehouse/manifest.yaml", "--json"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    payload = json.loads(out)
    assert payload["command"] == "diff"
    assert payload["mode"] == "manifest"
    assert payload["left"].endswith("acad1-legion/manifest.yaml")
    assert payload["differences"]
    assert any(d["path"] == "metadata.id" for d in payload["differences"])
    assert payload["summary"]["differences"] == len(payload["differences"])


# ---------- retire ----------
def test_retire_dry_run_does_not_write():
    manifest = ROOT / "compositions" / "acad1-legion" / "manifest.yaml"
    before = manifest.read_bytes()
    rc, out, err = _run(["tools/composition.py", "retire", "acad1-legion", "--dry-run"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "retired" in out
    assert "metadata.lifecycle" in out
    assert "未写盘" in out
    assert manifest.read_bytes() == before, "retire --dry-run 不得写 manifest"


def test_retire_requires_dry_run():
    manifest = ROOT / "compositions" / "acad1-legion" / "manifest.yaml"
    before = manifest.read_bytes()
    rc, out, err = _run(["tools/composition.py", "retire", "acad1-legion"])
    assert rc == 2, f"未带 --dry-run 必须 rc=2，实际 {rc}\n{out}\n{err}"
    assert "dry-run" in (out + err)
    assert manifest.read_bytes() == before, "retire 无 dry-run 时也不得写 manifest"


def test_retire_json():
    rc, out, err = _run(["tools/composition.py", "retire",
                         "compositions/acad1-legion/manifest.yaml", "--dry-run", "--json"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    payload = json.loads(out)
    assert payload["command"] == "retire"
    assert payload["dry_run"] is True
    plan = payload["plan"]
    assert plan["id"] == "acad1-legion"
    assert plan["planned_lifecycle"] == "retired"
    assert any(c["path"] == "metadata.lifecycle" and c["to"] == "retired"
               for c in plan["changes"])
    assert any("lifecycle" in c["path"] for c in plan["registry_patch"])


def test_retire_missing_target_rc2():
    rc, out, err = _run(["tools/composition.py", "retire", "no-such-composition", "--dry-run"])
    assert rc == 2, f"rc={rc}\n{out}\n{err}"
    assert "找不到" in (out + err)


# ---------- 子命令 help 可用 ----------
def test_new_commands_help():
    for cmd in ("plan", "diff", "retire"):
        rc, out, err = _run(["tools/composition.py", cmd, "--help"])
        assert rc == 0, f"{cmd} --help rc={rc}\n{out}\n{err}"
        assert "usage" in out.lower()


# ---------- 审计修复回归（task-30） ----------
def test_validate_repo_external_manifest_no_crash(tmp_path):
    """修复 A：validate 文本模式对仓外 manifest 不应崩溃（旧代码 f.relative_to(ROOT) 抛 ValueError）。"""
    src = ROOT / "compositions" / "_templates" / "template-product.yaml"
    dst = tmp_path / "manifest.yaml"
    dst.write_bytes(src.read_bytes())
    rc, out, err = _run(["tools/composition.py", "validate", str(dst)])
    assert "Traceback" not in (out + err), (out + err)
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "[PASS]" in out


def test_kernel_guard_absolute_path_blocks_without_adr():
    """修复 B：绝对路径改动不得 fail-open 绕过内核门（旧 _norm 不归一绝对路径）。"""
    abs_path = (ROOT / "platform" / "kernel_v1.yaml").resolve()
    rc, out, err = _run(["tools/kernel_guard.py", "--files", str(abs_path)])
    assert rc == 1, f"绝对路径应 BLOCK，实际 rc={rc}\n{out}\n{err}"
    assert "BLOCK" in out
    rc2, out2, err2 = _run(["tools/kernel_guard.py", "--files", str(abs_path),
                            "--adr", "ADR-015"])
    assert rc2 == 0, f"带 ADR 应放行，实际 rc={rc2}\n{out2}\n{err2}"
    assert "ADR-015" in out2
