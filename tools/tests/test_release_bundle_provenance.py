# -*- coding: utf-8 -*-
"""K-ORG-20261101-BUNDLEPROV（审计 M9）：bundle 纳入 release provenance / guard 辅助段。

背景：release manifest 仅 app.js/app.css/a0-mainchat.js/README.md；8103 实际消费
`_vault/legion_ide/agent_ide_bundle.json`（~808KB 且会变），不在 manifest/provenance；
guard PASS 不代表 bundle 一致。修复：bundle 作为 **auxiliary（advisory）** 段记录并校验，
**不写入 artifact manifest.files、不改变 artifact 判定**（生成物会变；漂移/缺失只告警）。

覆盖：
- provenance generate/data：bundle 存在→pass 记录 path/bytes/sha256/mtime；缺失→unknown；
  零字节→fail；不污染 manifest.files；auxiliary.bundle 独立字段；
- provenance --write + --verify：一致 pass；旧 provenance 无 bundle 段兼容；漂移/缺失 advisory；
- env `DEVTOOL_IDE_BUNDLE_DEPLOY` 覆盖；CLI `--bundle-path`；
- guard `--check --with-bundle`：存在/缺失/零字节；auxiliary 不改 ok/exit_code；与 provenance
  记录 sha 不同 → drift 告警；只读零写入。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
PROV_TOOL = TOOLS / "release_provenance.py"
GUARD_TOOL = TOOLS / "release_artifact_guard.py"
VERSION = "20261031_120000_abcd1234"
COMMIT = "a" * 40
BUILT_AT = "2026-10-31T12:00:00+08:00"
DEFAULT_FILES = {
    "app.js": b"console.log('v1');\n",
    "app.css": b".a{}\n",
    "a0-mainchat.js": b"// a0\n",
    "README.md": b"# ide\n",
}
BUNDLE_DATA = b'{"v": 1, "bundle": "' + b"x" * 800 + b'"}\n'


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        assert proc.returncode == 0, proc.stderr
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def _make_release(tmp_path: Path) -> dict:
    """构造隔离 release root：version/artifact + manifest + current.json + junction。"""
    root = tmp_path / "release"
    target = root / VERSION
    target.mkdir(parents=True, exist_ok=True)
    manifest_files = {}
    for name, data in DEFAULT_FILES.items():
        (target / name).write_bytes(data)
        manifest_files[name] = {"sha256": _sha(data), "bytes": len(data)}
    manifest = {"schema": "legion-ide-release/v1", "version": VERSION,
                "commit": COMMIT, "built_at": BUILT_AT,
                "source_rel": "Hermes/extensions/legion-ide",
                "files": manifest_files}
    raw = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    (target / "manifest.json").write_bytes(raw)
    (root / "current.json").write_text(json.dumps(
        {"version": VERSION, "dir": str(target), "promoted_at": BUILT_AT},
        ensure_ascii=False), encoding="utf-8")
    _make_junction(root / "current", target)
    return {"root": root, "target": target, "manifest": manifest, "manifest_raw": raw}


def _bundle(tmp_path: Path, data: bytes = BUNDLE_DATA, name="bundle.json") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _checks(report: dict) -> dict:
    return {c["id"]: c for c in report["checks"]}


# ── provenance：generate ──────────────────────────────────────────────────

def test_generate_records_bundle_auxiliary_not_manifest(tmp_path):
    mod = _load("prov_t1", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    report = mod.generate(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass", report["gaps"]
    aux = report["auxiliary"]
    assert aux["schema"] == mod.AUXILIARY_SCHEMA
    b = aux["bundle"]
    assert b["path"] == str(bundle) and b["exists"] is True
    assert b["bytes"] == len(BUNDLE_DATA) and b["sha256"] == _sha(BUNDLE_DATA)
    assert b["mtime"] and b["status"] == "pass"
    payload = report["provenance"]
    assert payload["auxiliary"]["bundle"]["sha256"] == _sha(BUNDLE_DATA)
    # bundle 不进 artifact files / manifest.files
    assert "agent_ide_bundle.json" not in payload["files"]
    assert "agent_ide_bundle.json" not in env["manifest"]["files"]
    assert payload["files_total"] == len(DEFAULT_FILES)
    # 辅助判据 advisory 且不影响整体状态
    chk = _checks(report)["bundle_recorded"]
    assert chk["required"] is False and chk["advisory"] is True and chk["status"] == "pass"


def test_generate_missing_bundle_is_advisory_unknown(tmp_path):
    mod = _load("prov_t2", PROV_TOOL)
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], no_git=True,
                          bundle_path=str(tmp_path / "nope" / "bundle.json"))
    assert report["status"] == "pass"          # 辅助项不影响整体
    b = report["auxiliary"]["bundle"]
    assert b["exists"] is False and b["status"] == "unknown"
    assert "缺失" in b["detail"]
    assert _checks(report)["bundle_recorded"]["status"] == "unknown"


def test_generate_zero_byte_bundle_visible_fail_but_aux(tmp_path):
    mod = _load("prov_t3", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path, b"")
    report = mod.generate(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass"          # advisory：不阻断 artifact
    b = report["auxiliary"]["bundle"]
    assert b["exists"] is True and b["bytes"] == 0 and b["status"] == "fail"
    assert "零字节" in b["detail"]


def test_env_override_and_cli_bundle_path(tmp_path, monkeypatch):
    mod = _load("prov_t4", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    monkeypatch.setenv(mod.BUNDLE_ENV, str(bundle))
    assert mod.inspect_bundle()["sha256"] == _sha(BUNDLE_DATA)
    # CLI 显式 --bundle-path 优先于 env
    other = _bundle(tmp_path, b'{"v": 2}\n', name="other.json")
    rc = mod.main(["--json", "--no-git", "--release-root", str(env["root"]),
                   "--bundle-path", str(other)])
    assert rc == 0


# ── provenance：--write + --verify ────────────────────────────────────────

def _write_prov(tmp_path, mod, env, bundle):
    rep = mod.generate(env["root"], no_git=True, write=True, bundle_path=str(bundle))
    assert rep["write"]["written"] is True, rep.get("write")
    return env["target"] / "provenance.json"


def test_verify_bundle_consistent_pass(tmp_path):
    mod = _load("prov_t5", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    _write_prov(tmp_path, mod, env, bundle)
    report = mod.verify(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass", report["gaps"]
    chk = _checks(report)
    assert chk["bundle_current"]["status"] == "pass"
    assert chk["bundle_drift"]["status"] == "pass"
    assert chk["bundle_current"]["required"] is False


def test_verify_bundle_drift_is_advisory(tmp_path):
    mod = _load("prov_t6", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    _write_prov(tmp_path, mod, env, bundle)
    bundle.write_bytes(b'{"v": 999}\n')       # bundle 被重新生成 → 漂移
    report = mod.verify(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass"          # 不阻断 artifact
    chk = _checks(report)
    assert chk["bundle_drift"]["status"] == "unknown"
    assert "漂移" in chk["bundle_drift"]["detail"]


def test_verify_bundle_missing_after_write_is_advisory(tmp_path):
    mod = _load("prov_t7", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    _write_prov(tmp_path, mod, env, bundle)
    bundle.unlink()
    report = mod.verify(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass"
    chk = _checks(report)
    assert chk["bundle_current"]["status"] == "unknown"
    assert chk["bundle_drift"]["status"] == "unknown"


def test_verify_legacy_provenance_without_bundle_section(tmp_path):
    mod = _load("prov_t8", PROV_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    prov_path = _write_prov(tmp_path, mod, env, bundle)
    payload = json.loads(prov_path.read_text(encoding="utf-8"))
    payload.pop("auxiliary", None)             # 模拟旧版 provenance
    prov_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    report = mod.verify(env["root"], no_git=True, bundle_path=str(bundle))
    assert report["status"] == "pass"
    chk = _checks(report)
    assert chk["bundle_recorded"]["status"] == "pass"
    assert "旧 provenance" in chk["bundle_recorded"]["detail"]
    # 旧版兼容：bundle 现值仍被报告
    assert report["auxiliary"]["bundle"]["sha256"] == _sha(BUNDLE_DATA)


# ── guard --with-bundle ───────────────────────────────────────────────────

def test_guard_default_check_has_no_auxiliary(tmp_path):
    mod = _load("guard_t1", GUARD_TOOL)
    env = _make_release(tmp_path)
    res = mod.check(env["root"])
    assert res["ok"] is True and "auxiliary" not in res


def test_guard_with_bundle_pass(tmp_path):
    mod = _load("guard_t2", GUARD_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    res = mod.check(env["root"], with_bundle=True, bundle_path=str(bundle))
    assert res["ok"] is True and res["exit_code"] == 0
    aux = res["auxiliary"]
    assert aux["advisory"] is True
    assert aux["bundle"]["status"] == "pass"
    assert aux["bundle"]["sha256"] == _sha(BUNDLE_DATA)
    assert aux["drift"] is None


def test_guard_with_bundle_missing_is_advisory(tmp_path):
    mod = _load("guard_t3", GUARD_TOOL)
    env = _make_release(tmp_path)
    res = mod.check(env["root"], with_bundle=True,
                    bundle_path=str(tmp_path / "nope.json"))
    assert res["ok"] is True and res["exit_code"] == 0   # artifact 判定不变
    assert res["auxiliary"]["bundle"]["status"] == "unknown"
    assert any("bundle_missing" in w for w in res["warnings"])


def test_guard_with_bundle_zero_is_advisory_warn(tmp_path):
    mod = _load("guard_t4", GUARD_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path, b"")
    res = mod.check(env["root"], with_bundle=True, bundle_path=str(bundle))
    assert res["ok"] is True and res["exit_code"] == 0
    assert res["auxiliary"]["bundle"]["status"] == "fail"
    assert any("bundle_bad" in w for w in res["warnings"])


def test_guard_with_bundle_drift_vs_provenance(tmp_path):
    mod = _load("guard_t5", GUARD_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    (env["target"] / "provenance.json").write_text(json.dumps({
        "schema": "legion-ide-release-provenance/v1",
        "auxiliary": {"bundle": {"sha256": "deadbeef" * 8, "bytes": 1}},
    }), encoding="utf-8")
    res = mod.check(env["root"], with_bundle=True, bundle_path=str(bundle))
    assert res["ok"] is True
    assert res["auxiliary"]["drift"] is True
    assert any("bundle_drift" in w for w in res["warnings"])


def test_guard_with_bundle_is_read_only(tmp_path):
    mod = _load("guard_t6", GUARD_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    before = (bundle.read_bytes(), bundle.stat().st_mtime_ns)
    mod.check(env["root"], with_bundle=True, bundle_path=str(bundle))
    assert (bundle.read_bytes(), bundle.stat().st_mtime_ns) == before


def test_guard_cli_with_bundle_json(tmp_path, capsys):
    mod = _load("guard_t7", GUARD_TOOL)
    env = _make_release(tmp_path)
    bundle = _bundle(tmp_path)
    rc = mod.main(["--check", "--json", "--with-bundle",
                   "--release-root", str(env["root"]),
                   "--bundle-path", str(bundle)])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["mode"] == "check" and res["ok"] is True
    assert res["auxiliary"]["bundle"]["sha256"] == _sha(BUNDLE_DATA)
