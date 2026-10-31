# -*- coding: utf-8 -*-
"""release_sbom：release artifact SBOM 生成/校验器测试（临时 release root，不碰生产、不联网）。

覆盖：生成默认只读 pass、CycloneDX 1.5 结构/serial 确定性、文件篡改/缺 manifest/坏 schema/
缺 commit unknown、current drift、显式历史版本、--write 原子写/已存在需 --force/篡改拒绝写、
--verify 正常/缺 SBOM/坏 JSON/组件 hash 篡改/serial 篡改/spec 篡改/文件篡改/manifest 漂移/
组件集合与 dependencies 漂移、requirements 组件（purl 不猜/重复去重/漂移/缺文件）、
不联网保证、markdown、CLI 退出码 0/1/2。
"""
import importlib.util
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "release_sbom.py"
REPO = TOOL.parents[1]
VERSION = "20261031_120000_abcd1234"
COMMIT = "a" * 40
BUILT_AT = "2026-10-31T12:00:00+08:00"
DEFAULT_FILES = {
    "app.js": b"console.log('v1');\n",
    "app.css": b".a{}\n",
    "a0-mainchat.js": b"// a0\n",
    "README.md": b"# ide\n",
}


def _load():
    spec = importlib.util.spec_from_file_location("release_sbom_test", TOOL)
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


def _write_manifest(target: Path, manifest: dict) -> bytes:
    raw = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    (Path(target) / "manifest.json").write_bytes(raw)
    return raw


def _make_release(tmp_path: Path, version: str = VERSION, *, files=None,
                  commit: str = COMMIT, built_at: str = BUILT_AT,
                  schema: str = "legion-ide-release/v1",
                  with_current: bool = True, current_version: str | None = None,
                  junction_version: str | None = None,
                  make_junction: bool = True) -> dict:
    root = tmp_path / "release"
    target = root / version
    target.mkdir(parents=True, exist_ok=True)
    files = dict(files or DEFAULT_FILES)
    manifest_files = {}
    for name, data in files.items():
        (target / name).write_bytes(data)
        manifest_files[name] = {"sha256": _sha(data), "bytes": len(data)}
    manifest = {
        "schema": schema,
        "version": version,
        "commit": commit,
        "built_at": built_at,
        "source_rel": "Hermes/extensions/legion-ide",
        "hermes_baseline": {"exists": True, "ok": True, "dirty": False,
                            "head": "6d517b7", "upstream": True},
        "files": manifest_files,
    }
    raw = _write_manifest(target, manifest)
    env = {"root": root, "target": target, "version": version,
           "manifest": manifest, "manifest_raw": raw}
    if with_current:
        current_version = current_version or version
        current = {"version": current_version, "dir": str(root / current_version),
                   "promoted_at": BUILT_AT, "note": "test"}
        (root / "current.json").write_text(json.dumps(current, ensure_ascii=False),
                                           encoding="utf-8")
        if make_junction:
            junction_version = junction_version or current_version
            _make_junction(root / "current", root / junction_version)
    return env


def _checks(report: dict) -> dict:
    return {item["id"]: item for item in report["checks"]}


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 生成（只读）
# ---------------------------------------------------------------------------
def test_generate_dry_run_pass_and_readonly(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"])
    assert report["status"] == "pass", report["gaps"]
    assert report["mode"] == "generate"
    assert report["schema"] == mod.SCHEMA
    assert report["write"] is None
    bom = report["sbom"]
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    assert bom["version"] == 1
    manifest_sha = _sha(env["manifest_raw"])
    assert bom["serialNumber"] == mod.bom_serial(VERSION, manifest_sha)
    assert bom["serialNumber"].startswith("urn:uuid:")
    root = bom["metadata"]["component"]
    assert root["name"] == "legion-ide" and root["version"] == VERSION
    assert root["hashes"] == [{"alg": "SHA-256", "content": manifest_sha}]
    assert root["bom-ref"] == "pkg:generic/legion-ide@" + VERSION
    file_components = [c for c in bom["components"] if c["type"] == "file"]
    assert {c["name"] for c in file_components} == set(DEFAULT_FILES)
    for component in file_components:
        assert component["version"] == VERSION
        assert component["hashes"] == [{"alg": "SHA-256",
                                        "content": _sha(DEFAULT_FILES[component["name"]])}]
    dependencies = bom["dependencies"][0]
    assert dependencies["ref"] == root["bom-ref"]
    assert set(dependencies["dependsOn"]) == {c["bom-ref"] for c in bom["components"]}
    # 默认只读：不写 sbom.cdx.json
    assert not (env["target"] / "sbom.cdx.json").exists()
    assert report["sbom_path"].endswith("sbom.cdx.json")
    assert report["components"]["files"] == 4 and report["components"]["libraries"] == 0


def test_generate_no_network_artifacts(tmp_path):
    """不联网保证：源码无网络库调用，BOM 内无 http(s) URL。"""
    mod = _load()
    source = TOOL.read_text(encoding="utf-8")
    for token in ("import urllib", "import requests", "import http.client", "import socket"):
        assert token not in source, token
    env = _make_release(tmp_path)
    report = mod.generate(env["root"])
    serialized = json.dumps(report["sbom"], ensure_ascii=False)
    assert "http://" not in serialized and "https://" not in serialized


def test_generate_tampered_file_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    (env["target"] / "app.js").write_bytes(b"tampered\n")
    report = mod.generate(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["artifact_files"]["status"] == "fail"
    assert _checks(report)["sbom_build"]["status"] == "fail"


def test_generate_missing_manifest_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, with_current=False)
    (env["target"] / "manifest.json").unlink()
    report = mod.generate(env["root"], version=VERSION)
    assert report["status"] == "fail"
    assert _checks(report)["manifest_readable"]["status"] == "fail"


def test_generate_bad_schema_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, schema="bogus/v9")
    report = mod.generate(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["manifest_schema"]["status"] == "fail"


def test_generate_missing_commit_unknown_and_write_refused(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, commit="")
    report = mod.generate(env["root"], write=True)
    assert report["status"] == "unknown"
    assert _checks(report)["source_commit"]["status"] == "unknown"
    assert report["write"]["written"] is False
    assert not (env["target"] / "sbom.cdx.json").exists()


def test_generate_missing_root_unknown(tmp_path):
    mod = _load()
    report = mod.generate(tmp_path / "no_such_root")
    assert report["status"] == "unknown"
    assert _checks(report)["target_resolved"]["status"] == "unknown"


def test_generate_version_escape_rejected(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], version="../escape")
    assert report["status"] == "unknown"
    assert "非法 version" in report["target"]["error"]


def test_generate_explicit_historical_version(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    older = env["root"] / "20261031_000000_00000000"
    older.mkdir()
    for name, data in DEFAULT_FILES.items():
        (older / name).write_bytes(data)
    old_manifest = dict(env["manifest"])
    old_manifest["version"] = older.name
    old_manifest["files"] = {name: {"sha256": _sha(data), "bytes": len(data)}
                             for name, data in DEFAULT_FILES.items()}
    _write_manifest(older, old_manifest)
    report = mod.generate(env["root"], version=older.name)
    assert report["status"] == "pass"
    assert "pointer_version" not in _checks(report)
    assert report["sbom"]["metadata"]["component"]["version"] == older.name


def test_generate_current_drift_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    other = env["root"] / "20261031_110000_11111111"
    other.mkdir()
    for name, data in DEFAULT_FILES.items():
        (other / name).write_bytes(data)
    manifest = dict(env["manifest"])
    manifest["version"] = other.name
    _write_manifest(other, manifest)
    os.rmdir(str(env["root"] / "current"))
    _make_junction(env["root"] / "current", other)
    report = mod.generate(env["root"])
    assert report["status"] == "fail"
    checks = _checks(report)
    assert checks["pointer_version"]["status"] == "pass"
    assert checks["junction_version"]["status"] == "fail"
    assert checks["pointer_junction_drift"]["status"] == "fail"


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------
def test_write_creates_valid_sbom(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], write=True)
    assert report["status"] == "pass", report["gaps"]
    sbom_path = env["target"] / "sbom.cdx.json"
    assert sbom_path.is_file()
    saved = _read_json(sbom_path)
    assert saved["bomFormat"] == "CycloneDX" and saved["specVersion"] == "1.5"
    assert saved["metadata"]["component"]["name"] == "legion-ide"
    assert len([c for c in saved["components"] if c["type"] == "file"]) == 4
    assert report["write"]["written"] is True
    assert report["write"]["path"].endswith("sbom.cdx.json")
    assert not (env["target"] / "sbom.cdx.json.tmp").exists()


def test_write_existing_requires_force(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    first = mod.generate(env["root"], write=True)
    assert first["write"]["written"] is True
    second = mod.generate(env["root"], write=True)
    assert second["status"] == "fail"
    assert _checks(second)["sbom_write"]["status"] == "fail"
    assert "已存在" in _checks(second)["sbom_write"]["detail"]
    forced = mod.generate(env["root"], write=True, force=True)
    assert forced["status"] == "pass" and forced["write"]["written"] is True


def test_write_refuses_tampered_artifact(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    (env["target"] / "app.css").write_bytes(b"tampered\n")
    report = mod.generate(env["root"], write=True)
    assert report["status"] == "fail"
    assert report["write"]["written"] is False
    assert not (env["target"] / "sbom.cdx.json").exists()


# ---------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------
def test_verify_ok(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    report = mod.verify(env["root"])
    assert report["status"] == "pass", report["gaps"]
    assert report["mode"] == "verify"
    assert _checks(report)["component_files"]["status"] == "pass"
    assert _checks(report)["component_hashes"]["status"] == "pass"
    assert report["components"]["files"] == 4


def test_verify_missing_sbom_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["sbom_readable"]["status"] == "fail"


def test_verify_malformed_sbom_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    (env["target"] / "sbom.cdx.json").write_text("{broken json", encoding="utf-8")
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["sbom_readable"]["status"] == "fail"


def _edit_sbom(env: dict, mutate) -> None:
    path = env["target"] / "sbom.cdx.json"
    bom = _read_json(path)
    mutate(bom)
    path.write_text(json.dumps(bom, ensure_ascii=False, indent=1), encoding="utf-8")


def test_verify_component_hash_tamper_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)

    def mutate(bom):
        for component in bom["components"]:
            if component["type"] == "file":
                component["hashes"] = [{"alg": "SHA-256", "content": "deadbeef"}]
                break

    _edit_sbom(env, mutate)
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["component_hashes"]["status"] == "fail"


def test_verify_serial_tamper_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    _edit_sbom(env, lambda bom: bom.update({"serialNumber": "urn:uuid:deadbeef"}))
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["serial_number"]["status"] == "fail"


def test_verify_spec_version_tamper_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    _edit_sbom(env, lambda bom: bom.update({"specVersion": "1.4"}))
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["bom_spec"]["status"] == "fail"


def test_verify_file_tamper_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    (env["target"] / "app.js").write_bytes(b"changed after sbom\n")
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["files_actual"]["status"] == "fail"


def test_verify_manifest_drift_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    manifest = dict(env["manifest"])
    manifest["built_at"] = "2026-11-01T00:00:00+08:00"
    _write_manifest(env["target"], manifest)
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    checks = _checks(report)
    assert checks["root_properties"]["status"] == "fail"
    assert checks["serial_number"]["status"] == "fail"


def test_verify_component_set_drift_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)

    def mutate(bom):
        bom["components"] = [c for c in bom["components"] if c["name"] != "README.md"]
        bom["dependencies"][0]["dependsOn"] = [c["bom-ref"] for c in bom["components"]]

    _edit_sbom(env, mutate)
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["component_files"]["status"] == "fail"


def test_verify_dependencies_tamper_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], write=True)
    _edit_sbom(env, lambda bom: bom["dependencies"][0].update({"dependsOn": []}))
    report = mod.verify(env["root"])
    assert report["status"] == "fail"
    assert _checks(report)["dependencies"]["status"] == "fail"


# ---------------------------------------------------------------------------
# requirements 组件（离线，purl 不猜）
# ---------------------------------------------------------------------------
def _write_requirements(path: Path, lines) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_requirements_components_generate_and_verify(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    req = _write_requirements(tmp_path / "requirements.txt", [
        "# runtime deps",
        "requests==2.31.0",
        "flask>=3.0",
        "",
        "-r other.txt",
    ])
    report = mod.generate(env["root"], requirements=[str(req)], write=True)
    assert report["status"] == "pass", report["gaps"]
    assert report["components"]["libraries"] == 2
    saved = _read_json(env["target"] / "sbom.cdx.json")
    libraries = {c["name"]: c for c in saved["components"] if c["type"] == "library"}
    assert set(libraries) == {"requests", "flask"}
    assert libraries["requests"]["version"] == "2.31.0"
    assert libraries["requests"]["purl"] == "pkg:pypi/requests@2.31.0"
    # 非精确 pin：purl 留空不猜，原始 spec 记录在 properties
    assert "purl" not in libraries["flask"]
    props = {p["name"]: p["value"] for p in libraries["flask"]["properties"]}
    assert props["legion:requirement-spec"] == "flask>=3.0"
    # 同文件 verify → pass
    verified = mod.verify(env["root"], requirements=[str(req)])
    assert verified["status"] == "pass", verified["gaps"]
    assert _checks(verified)["requirements_components"]["status"] == "pass"
    # requirements 变化 → 漂移 fail
    req2 = _write_requirements(tmp_path / "requirements2.txt", [
        "requests==2.31.0", "flask>=3.0", "numpy==1.26.0"])
    drift = mod.verify(env["root"], requirements=[str(req2)])
    assert drift["status"] == "fail"
    assert _checks(drift)["requirements_components"]["status"] == "fail"
    # 不提供 requirements → 依赖组件自洽即 pass
    plain = mod.verify(env["root"])
    assert plain["status"] == "pass"
    assert _checks(plain)["library_components"]["status"] == "pass"


def test_requirements_duplicate_dedup(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    req_a = _write_requirements(tmp_path / "a.txt", ["requests==2.31.0"])
    req_b = _write_requirements(tmp_path / "b.txt", ["requests==2.31.0"])
    report = mod.generate(env["root"], requirements=[str(req_a), str(req_b)])
    assert report["status"] == "pass"
    assert report["components"]["libraries"] == 1
    assert any("duplicate" in item for item in report["requirements"]["skipped"])


def test_requirements_missing_file_unknown(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], requirements=[str(tmp_path / "no_req.txt")],
                          write=True)
    assert report["status"] == "unknown"
    assert _checks(report)["requirements_parse"]["status"] == "unknown"
    assert report["write"]["written"] is False
    assert not (env["target"] / "sbom.cdx.json").exists()


# ---------------------------------------------------------------------------
# 渲染 / CLI
# ---------------------------------------------------------------------------
def test_render_markdown_sections(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"])
    text = mod.render_markdown(report)
    assert "# release artifact SBOM" in text
    assert "## BOM 摘要" in text
    assert "## 判据表" in text
    assert "## 组件清单" in text
    assert "**判定：PASS**" in text
    assert "不 promote" in text


def _cli(*args, timeout=180):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def test_cli_json_pass_and_exit_0(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--json", "--release-root", str(env["root"]))
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    assert report["schema"] == "release_sbom_report/v1"


def test_cli_markdown_output(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--markdown", "--release-root", str(env["root"]))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "# release artifact SBOM" in proc.stdout
    assert "判据表" in proc.stdout


def test_cli_write_then_verify_exit_codes(tmp_path):
    env = _make_release(tmp_path)
    root = str(env["root"])
    write = _cli("--json", "--write", "--release-root", root)
    assert write.returncode == 0, write.stderr[-800:]
    assert (env["target"] / "sbom.cdx.json").is_file()
    verify = _cli("--json", "--verify", "--release-root", root)
    assert verify.returncode == 0, verify.stderr[-800:]
    # 文件篡改 → verify exit 1
    (env["target"] / "app.js").write_bytes(b"tampered\n")
    bad = _cli("--json", "--verify", "--release-root", root)
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["status"] == "fail"
    # 缺 release root → exit 2
    missing = _cli("--json", "--release-root", str(tmp_path / "no_root_here"))
    assert missing.returncode == 2


def test_cli_write_no_write_conflict_returns_2(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--json", "--write", "--no-write", "--release-root", str(env["root"]))
    assert proc.returncode == 2
