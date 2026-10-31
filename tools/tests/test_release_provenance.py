# -*- coding: utf-8 -*-
"""release_provenance：release artifact provenance 生成/校验器测试（临时 release root，不碰生产）。

覆盖：只读生成 pass、逐文件篡改 fail、manifest 缺失/坏 schema/缺 commit unknown、
--write 落盘/已存在需 --force/篡改拒绝写、--verify 正常/缺 provenance/provenance 被改/
文件篡改/manifest 漂移/version 漂移、HMAC 签名+验签/无 key unsigned/错 key/缺 key、
--source-commit 覆盖、git_dirty 检测、current drift、version 逃逸、CLI 退出码 0/1/2。
"""
import importlib.util
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "release_provenance.py"
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
    spec = importlib.util.spec_from_file_location("release_provenance_test", TOOL)
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
    """构造隔离 release root：version/artifact + manifest + current.json(+junction)。"""
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
    report = mod.generate(env["root"], no_git=True)
    assert report["status"] == "pass", report["gaps"]
    assert report["mode"] == "generate"
    assert report["schema"] == mod.REPORT_SCHEMA
    assert report["write"] is None
    prov = report["provenance"]
    assert prov["schema"] == mod.SCHEMA
    assert prov["artifact_version"] == VERSION
    assert prov["source_commit"] == COMMIT
    assert prov["source_commit_source"] == "manifest.commit"
    assert prov["build_time"] == BUILT_AT
    assert prov["manifest_sha256"] == _sha(env["manifest_raw"])
    assert prov["manifest_commit"] == COMMIT
    assert prov["files_total"] == len(DEFAULT_FILES)
    assert prov["signature"] is None
    assert prov["builder"]["python"] and prov["builder"]["platform"]
    assert report["git"]["skipped"] is True
    assert prov["git_dirty"] is None
    # 默认只读：provenance.json 未被写入
    assert not (env["target"] / "provenance.json").exists()
    assert report["provenance_path"].endswith("provenance.json")


def test_generate_tampered_file_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    (env["target"] / "app.js").write_bytes(b"tampered\n")
    report = mod.generate(env["root"], no_git=True)
    assert report["status"] == "fail"
    checks = _checks(report)
    assert checks["artifact_files"]["status"] == "fail"
    assert checks["provenance_build"]["status"] == "fail"
    assert any(item["match"] is False for item in report["files"])


def test_generate_missing_manifest_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, with_current=False)
    (env["target"] / "manifest.json").unlink()
    report = mod.generate(env["root"], version=VERSION, no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["manifest_readable"]["status"] == "fail"


def test_generate_bad_schema_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, schema="bogus/v9")
    report = mod.generate(env["root"], no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["manifest_schema"]["status"] == "fail"


def test_generate_missing_commit_unknown_and_write_refused(tmp_path):
    mod = _load()
    env = _make_release(tmp_path, commit="")
    report = mod.generate(env["root"], no_git=True, write=True)
    assert report["status"] == "unknown"
    assert _checks(report)["source_commit"]["status"] == "unknown"
    assert report["write"]["written"] is False
    assert not (env["target"] / "provenance.json").exists()


def test_generate_missing_release_root_unknown(tmp_path):
    mod = _load()
    report = mod.generate(tmp_path / "no_such_root", no_git=True)
    assert report["status"] == "unknown"
    assert _checks(report)["target_resolved"]["status"] == "unknown"


def test_generate_version_escape_rejected(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], version="../escape", no_git=True)
    assert report["status"] == "unknown"
    assert "非法 version" in report["target"]["error"]


def test_explicit_historical_version_has_no_current_unit_checks(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    older = env["root"] / "20261031_000000_00000000"
    older.mkdir()
    files = dict(DEFAULT_FILES)
    for name, data in files.items():
        (older / name).write_bytes(data)
    old_manifest = dict(env["manifest"])
    old_manifest["version"] = older.name
    old_manifest["files"] = {name: {"sha256": _sha(data), "bytes": len(data)}
                             for name, data in files.items()}
    _write_manifest(older, old_manifest)
    report = mod.generate(env["root"], version=older.name, no_git=True)
    assert report["status"] == "pass"
    ids = set(_checks(report))
    assert "pointer_version" not in ids and "current_unit" not in ids
    assert report["provenance"]["artifact_version"] == older.name


def test_current_drift_detected(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    other = env["root"] / "20261031_110000_11111111"
    other.mkdir()
    for name, data in DEFAULT_FILES.items():
        (other / name).write_bytes(data)
    manifest = dict(env["manifest"])
    manifest["version"] = other.name
    _write_manifest(other, manifest)
    # current.json 仍指 A、junction 改指 B → drift
    os.rmdir(str(env["root"] / "current"))
    _make_junction(env["root"] / "current", other)
    # 显式 version 不校验 current 单元 → pass；current 漂移信息仍被记录
    explicit = mod.generate(env["root"], version=env["version"], no_git=True)
    assert explicit["status"] == "pass"
    assert explicit["current"]["drift"] is True
    # 跟随 current 解析：指针指 A、junction 指 B → drift fail
    report_current = mod.generate(env["root"], no_git=True)
    assert report_current["status"] == "fail"
    checks = _checks(report_current)
    assert checks["pointer_version"]["status"] == "pass"
    assert checks["junction_version"]["status"] == "fail"
    assert checks["pointer_junction_drift"]["status"] == "fail"


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------
def test_write_creates_unsigned_provenance(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], no_git=True, write=True)
    assert report["status"] == "pass", report["gaps"]
    prov_path = env["target"] / "provenance.json"
    assert prov_path.is_file()
    saved = _read_json(prov_path)
    assert saved["schema"] == mod.SCHEMA
    assert saved["signature"] is None
    assert saved["artifact_version"] == VERSION
    assert set(saved["files"]) == set(DEFAULT_FILES)
    assert report["write"]["written"] is True
    assert report["signature"]["status"] == "unsigned"
    assert "unsigned" in _checks(report)["signing"]["detail"]


def test_write_existing_requires_force(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    first = mod.generate(env["root"], no_git=True, write=True)
    assert first["write"]["written"] is True
    second = mod.generate(env["root"], no_git=True, write=True)
    assert second["status"] == "fail"
    assert _checks(second)["provenance_write"]["status"] == "fail"
    assert "已存在" in _checks(second)["provenance_write"]["detail"]
    forced = mod.generate(env["root"], no_git=True, write=True, force=True)
    assert forced["status"] == "pass" and forced["write"]["written"] is True


def test_write_refuses_tampered_artifact(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    (env["target"] / "app.css").write_bytes(b"tampered\n")
    report = mod.generate(env["root"], no_git=True, write=True)
    assert report["status"] == "fail"
    assert report["write"]["written"] is False
    assert not (env["target"] / "provenance.json").exists()


# ---------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------
def test_verify_ok_unsigned(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "pass", report["gaps"]
    assert report["mode"] == "verify"
    assert _checks(report)["files_actual"]["status"] == "pass"
    assert _checks(report)["signature"]["status"] == "pass"
    assert report["signature"]["status"] == "unsigned"


def test_verify_missing_provenance_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["provenance_readable"]["status"] == "fail"


def test_verify_provenance_tampered_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    prov_path = env["target"] / "provenance.json"
    saved = _read_json(prov_path)
    saved["manifest_sha256"] = "deadbeef"
    prov_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["manifest_sha256"]["status"] == "fail"


def test_verify_file_tampered_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    (env["target"] / "app.js").write_bytes(b"changed after provenance\n")
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["files_actual"]["status"] == "fail"


def test_verify_manifest_drift_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    manifest = dict(env["manifest"])
    manifest["built_at"] = "2026-11-01T00:00:00+08:00"
    _write_manifest(env["target"], manifest)
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "fail"
    checks = _checks(report)
    assert checks["manifest_sha256"]["status"] == "fail"
    assert checks["build_time"]["status"] == "fail"


def test_verify_artifact_version_drift_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    prov_path = env["target"] / "provenance.json"
    saved = _read_json(prov_path)
    saved["artifact_version"] = "99999999_999999_ffffffff"
    prov_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    report = mod.verify(env["root"], no_git=True)
    assert report["status"] == "fail"
    assert _checks(report)["artifact_version"]["status"] == "fail"


# ---------------------------------------------------------------------------
# HMAC 签名
# ---------------------------------------------------------------------------
def test_hmac_sign_and_verify(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    key_file = tmp_path / "hmac.key"
    key_file.write_bytes(b"unit-test-secret-key\n")
    report = mod.generate(env["root"], no_git=True, write=True, key_file=str(key_file))
    assert report["status"] == "pass", report["gaps"]
    assert report["signature"]["status"] == "signed"
    saved = _read_json(env["target"] / "provenance.json")
    assert isinstance(saved["signature"], dict)
    assert saved["signature"]["algorithm"] == "hmac-sha256"
    assert saved["signature"]["value"]
    verified = mod.verify(env["root"], no_git=True, key_file=str(key_file))
    assert verified["status"] == "pass", verified["gaps"]
    assert verified["signature"]["status"] == "verified"
    # 无 key 校验：完整性可过，但签名 unverifiable → unknown
    no_key = mod.verify(env["root"], no_git=True)
    assert no_key["status"] == "unknown"
    assert no_key["signature"]["status"] == "unverifiable"
    # 错 key → fail
    wrong = tmp_path / "wrong.key"
    wrong.write_bytes(b"wrong-secret\n")
    bad = mod.verify(env["root"], no_git=True, key_file=str(wrong))
    assert bad["status"] == "fail"
    assert bad["signature"]["status"] == "invalid"


def test_verify_require_signature_on_unsigned_fail(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    mod.generate(env["root"], no_git=True, write=True)
    report = mod.verify(env["root"], no_git=True, require_signature=True)
    assert report["status"] == "fail"
    assert _checks(report)["signature"]["status"] == "fail"


def test_key_file_missing_unknown(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], no_git=True, write=True,
                          key_file=str(tmp_path / "no_key.bin"))
    assert report["status"] == "unknown"
    assert report["write"]["written"] is False
    assert not (env["target"] / "provenance.json").exists()


# ---------------------------------------------------------------------------
# source_commit / git_dirty
# ---------------------------------------------------------------------------
def test_source_commit_override(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], no_git=True, source_commit="deadbeef" * 5)
    assert report["status"] == "pass"
    prov = report["provenance"]
    assert prov["source_commit"] == "deadbeef" * 5
    assert prov["source_commit_source"] == "user-override"
    assert prov["manifest_commit"] == COMMIT


def test_git_dirty_detection(tmp_path):
    mod = _load()
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    env = dict(os.environ)
    for cmd in (["git", "init", "-q"],):
        subprocess.run(cmd, cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo),
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo),
                   capture_output=True)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo),
                   capture_output=True)
    clean = mod.git_state(repo)
    assert clean["ok"] is True and clean["dirty"] is False and clean["head"]
    release = _make_release(tmp_path / "rel_clean")
    report = mod.generate(release["root"], repo=repo)
    assert report["status"] == "pass"
    assert report["provenance"]["git_dirty"] is False
    assert report["provenance"]["git_head"] == clean["head"]
    (repo / "new_untracked.txt").write_text("y", encoding="utf-8")
    dirty = mod.git_state(repo)
    assert dirty["ok"] is True and dirty["dirty"] is True


# ---------------------------------------------------------------------------
# 渲染 / CLI
# ---------------------------------------------------------------------------
def test_render_markdown_sections(tmp_path):
    mod = _load()
    env = _make_release(tmp_path)
    report = mod.generate(env["root"], no_git=True)
    text = mod.render_markdown(report)
    assert "# release artifact provenance" in text
    assert "## provenance 摘要" in text
    assert "## 判据表" in text
    assert "## 逐文件校验" in text
    assert "**判定：PASS**" in text
    assert "不 promote" in text


def _cli(*args, timeout=180):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def test_cli_json_pass_and_exit_0(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--json", "--no-git", "--release-root", str(env["root"]))
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["status"] == "pass"
    assert report["schema"] == "release_provenance_report/v1"


def test_cli_markdown_output(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--markdown", "--no-git", "--release-root", str(env["root"]))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "# release artifact provenance" in proc.stdout
    assert "判据表" in proc.stdout


def test_cli_write_then_verify_exit_codes(tmp_path):
    env = _make_release(tmp_path)
    root = str(env["root"])
    write = _cli("--json", "--no-git", "--write", "--release-root", root)
    assert write.returncode == 0, write.stderr[-800:]
    assert (env["target"] / "provenance.json").is_file()
    verify = _cli("--json", "--no-git", "--verify", "--release-root", root)
    assert verify.returncode == 0, verify.stderr[-800:]
    # 篡改文件 → verify exit 1
    (env["target"] / "app.js").write_bytes(b"tampered\n")
    bad = _cli("--json", "--no-git", "--verify", "--release-root", root)
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["status"] == "fail"
    # 缺 provenance/缺 root → exit 1 / 2
    empty = _make_release(tmp_path / "no_prov", with_current=True)
    missing_prov = _cli("--json", "--no-git", "--verify", "--release-root",
                        str(empty["root"]))
    assert missing_prov.returncode == 1
    missing_root = _cli("--json", "--no-git", "--release-root",
                        str(tmp_path / "no_root_here"))
    assert missing_root.returncode == 2


def test_cli_write_no_write_conflict_returns_2(tmp_path):
    env = _make_release(tmp_path)
    proc = _cli("--json", "--no-git", "--write", "--no-write",
                "--release-root", str(env["root"]))
    assert proc.returncode == 2
