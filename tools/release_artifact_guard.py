# -*- coding: utf-8 -*-
"""artifact 完整性 guard + 只读锁工具（K-ORG-20261031-ARTLOCK）。

事故背景（2026-10-31 14:12）
---------------------------
另一并行进程绕过 `tools/release_legion_ide.py`，直接把
`_vault/legion_ide/release/legion-ide/current/app.js` 从 81,060B 改成 83,125B，
manifest 未变 → `/api/ide/version all_match=false`（从构建 commit 02fc06f8 的 git blob 恢复）。

本工具三件事（只读校验 + 可逆写保护；**不自动修复、不发布、不重启、不删文件**）：
- ``--check``  ：校验 current.json / current junction / manifest / 每个文件的 sha256+bytes；
                 JSON 输出；exit 0=一致，1=完整性失败（篡改/缺文件/drift/坏 manifest），
                 2=环境/配置错误（release root/current.json 不可读）。
- ``--lock``   ：先 --check，一致才把 **current 指向的 artifact 目录内文件**（manifest.json
                 + manifest.files 列出的文件）设为只读；**不锁 current.json、不锁 junction**；
                 写 lock manifest（文件清单+sha+原 mode+时间）；幂等（重复 lock 安全）。
- ``--unlock`` ：只解除 **lock manifest 记录的文件**；幂等；只恢复为记录的原 mode；
                 不删除任何文件（lock manifest 改成 state=unlocked 保留）。
                 若记录的文件 sha 已变（被改过），保持锁定并在结果里报 changed（exit 1，可见）。

合约
----
- `--json`：唯一稳定机器契约（schema=release_artifact_guard/v1）。
- `--version`：打印工具名/schema/版本。
- 默认 release root：``C:\\项目\\_vault\\legion_ide\\release\\legion-ide``；
  测试/演练用 ``--release-root`` + ``--lock-manifest`` 注入 tmp 路径，**绝不碰真实 _vault**。
- 不写任何 artifact 内容；`--lock/--unlock` 只改文件只读属性 + lock manifest。

用法
----
    python -X utf8 tools/release_artifact_guard.py --check --json
    python -X utf8 tools/release_artifact_guard.py --lock --json
    python -X utf8 tools/release_artifact_guard.py --unlock --json
    python -X utf8 tools/release_artifact_guard.py --version
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

SCHEMA = "release_artifact_guard/v1"
TOOL_NAME = "release_artifact_guard"
TOOL_VERSION = "1.0.0"
DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
CURRENT_LINK_NAME = "current"
CURRENT_JSON_NAME = "current.json"
MANIFEST_NAME = "manifest.json"
LOCK_MANIFEST_NAME = ".artifact_lock.json"
# M9（K-ORG-20261101-BUNDLEPROV）：8103 实际消费的 _vault bundle 不属 artifact manifest；
# `--check --with-bundle` 只读附带 bundle 证据（auxiliary，advisory，不改 ok/exit_code）。
BUNDLE_NAME = "agent_ide_bundle.json"
DEFAULT_BUNDLE_PATH = Path(r"C:\项目\_vault\legion_ide\agent_ide_bundle.json")
BUNDLE_ENV = "DEVTOOL_IDE_BUNDLE_DEPLOY"


# ──────────────────────────────────────────────────────────────────────────
# 基础工具（只读）
# ──────────────────────────────────────────────────────────────────────────
def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(p: Path) -> tuple[dict | None, str]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, "not_a_json_object"
        return data, ""
    except OSError as e:
        return None, "read_error: %s" % e
    except ValueError as e:
        return None, "json_error: %s" % e


def _sha256(p: Path) -> str:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _resolve_bundle_path(bundle_path=None) -> Path:
    if bundle_path:
        return Path(bundle_path)
    env = os.environ.get(BUNDLE_ENV) or ""
    return Path(env) if env else DEFAULT_BUNDLE_PATH


def _inspect_bundle(bundle_path=None) -> dict:
    """只读采集 bundle 辅助证据：pass/unknown(缺失)/fail(零字节/读取失败)。"""
    path = _resolve_bundle_path(bundle_path)
    info = {"path": str(path), "exists": False, "bytes": 0, "sha256": "",
            "mtime": "", "status": "unknown", "detail": ""}
    try:
        if not path.is_file():
            info["detail"] = "bundle 缺失（8103 回落仓内生成物；重建=rebake_ide_bundle.ps1）"
            return info
        st = path.stat()
        info.update(exists=True, bytes=st.st_size,
                    mtime=dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(
                        timespec="seconds"))
        if st.st_size <= 0:
            info.update(status="fail",
                        detail="bundle 零字节/损坏（8103 会读到空数据；需 rebake）")
        else:
            sha = _sha256(path)
            info.update(sha256=sha, status="pass",
                        detail="bundle exists bytes=%d sha256=%s" % (st.st_size, sha[:16]))
    except OSError as exc:
        info.update(status="fail",
                    detail="bundle 读取失败：%s: %s"
                    % (type(exc).__name__, str(exc)[:160]))
    return info


def _file_present(p: Path) -> bool:
    return os.path.lexists(str(p)) or p.exists()


def _link_target(p: Path) -> str:
    try:
        return str(os.readlink(str(p)))
    except (OSError, ValueError):
        return ""


def _display(p: str) -> str:
    s = str(p or "")
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[len("\\\\?\\UNC\\"):]
    if s.startswith("\\\\?\\"):
        return s[len("\\\\?\\"):]
    return s


def _norm(p: str) -> str:
    s = _display(p)
    try:
        s = os.path.normpath(s)
    except (OSError, ValueError):
        pass
    return os.path.normcase(s)


def _mode(p: Path) -> int:
    try:
        return stat.S_IMODE(p.stat().st_mode)
    except OSError:
        return 0


def _is_readonly(p: Path) -> bool:
    return not bool(_mode(p) & 0o200)


def _set_mode(p: Path, target: int) -> tuple[bool, str]:
    try:
        os.chmod(p, target)
        return _mode(p) == target, ""
    except OSError as e:
        return False, "%s: %s" % (type(e).__name__, e)


def _write_json_atomic(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(p))


# ──────────────────────────────────────────────────────────────────────────
# --check
# ──────────────────────────────────────────────────────────────────────────
def check(release_root: Path | str = DEFAULT_RELEASE_ROOT, *,
          with_bundle: bool = False, bundle_path=None) -> dict:
    """只读完整性校验；不写任何文件。exit_code: 0 ok / 1 完整性失败 / 2 环境错误。

    ``with_bundle=True`` 时在 ``res["auxiliary"]["bundle"]`` 附带 _vault bundle 证据
    （path/exists/bytes/sha256/mtime/status）：**advisory，不改变 ok/exit_code**；
    bundle 缺失=unknown、零字节/读取失败=fail、正常=pass；若 artifact 目录有
    provenance.json 的 auxiliary.bundle 记录且 sha 不同 → drift=True（warn）。
    """
    root = Path(release_root)
    res: dict = {
        "schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "mode": "check", "ok": False, "exit_code": 2,
        "release_root": str(root), "checked_at": _now(),
        "pointer": {"current_json": str(root / CURRENT_JSON_NAME), "exists": False,
                    "version": "", "dir": ""},
        "junction": {"path": str(root / CURRENT_LINK_NAME), "exists": False,
                     "target": "", "drift": None},
        "manifest": {"path": "", "exists": False, "version": "", "commit": "", "files_count": 0},
        "files": {}, "summary": {"files_total": 0, "files_ok": 0, "files_bad": 0,
                                 "readonly_files": 0},
        "warnings": [], "errors": [],
    }
    if not root.is_dir():
        res["errors"].append("release_root_missing: %s" % root)
        return res

    # 1) current.json
    cj_path = root / CURRENT_JSON_NAME
    res["pointer"]["exists"] = cj_path.is_file()
    if not cj_path.is_file():
        res["errors"].append("current_json_missing: %s" % cj_path)
        return res
    cj, err = _read_json(cj_path)
    if cj is None:
        res["errors"].append("current_json_invalid: %s" % err)
        return res
    pointer_dir_raw = str(cj.get("dir") or "")
    version = str(cj.get("version") or "")
    if not pointer_dir_raw:
        res["errors"].append("current_json_dir_missing")
        return res
    pointer_dir = Path(pointer_dir_raw)
    res["pointer"].update({"version": version, "dir": pointer_dir_raw,
                           "promoted_at": str(cj.get("promoted_at") or "")})

    # 2) junction（current → ?）
    link = root / CURRENT_LINK_NAME
    if not _file_present(link):
        res["errors"].append("junction_missing: %s" % link)
        res["junction"]["drift"] = True
        res["exit_code"] = 1
        return res
    target = _link_target(link)
    res["junction"].update({"exists": True, "target": _display(target),
                            "drift": _norm(target) != _norm(str(pointer_dir))})
    if not pointer_dir.is_dir():
        res["errors"].append("pointer_dir_missing: %s" % pointer_dir)

    # 3) manifest
    mf_path = pointer_dir / MANIFEST_NAME
    res["manifest"]["path"] = str(mf_path)
    res["manifest"]["exists"] = mf_path.is_file()
    if not mf_path.is_file():
        res["errors"].append("manifest_missing: %s" % mf_path)
        res["exit_code"] = 1
        return res
    mf, err = _read_json(mf_path)
    if mf is None:
        res["errors"].append("manifest_invalid: %s" % err)
        res["exit_code"] = 1
        return res
    res["manifest"].update({"version": str(mf.get("version") or ""),
                            "commit": str(mf.get("commit") or "")})
    files = mf.get("files")
    if not isinstance(files, dict) or not files:
        res["errors"].append("manifest_files_empty_or_invalid")
        res["exit_code"] = 1
        return res
    res["manifest"]["files_count"] = len(files)

    # 4) 逐文件 sha/bytes
    for name, meta in files.items():
        expected_sha = str((meta or {}).get("sha256") or "")
        expected_bytes = (meta or {}).get("bytes")
        fp = pointer_dir / str(name)
        info = {"expected_sha256": expected_sha, "expected_bytes": expected_bytes,
                "exists": fp.is_file(), "bytes": 0, "sha256": "", "match": False,
                "readonly": False}
        if not expected_sha:
            info["error"] = "manifest_sha_missing"
        elif fp.is_file():
            info["bytes"] = fp.stat().st_size
            info["sha256"] = _sha256(fp)
            info["readonly"] = _is_readonly(fp)
            size_ok = (expected_bytes is None) or (info["bytes"] == expected_bytes)
            info["size_match"] = size_ok
            info["match"] = (info["sha256"] == expected_sha) and size_ok
        else:
            info["error"] = "file_missing"
        res["files"][str(name)] = info

    total = len(res["files"])
    ok_n = sum(1 for v in res["files"].values() if v.get("match"))
    bad = [k for k, v in res["files"].items() if not v.get("match")]
    res["summary"].update({"files_total": total, "files_ok": ok_n,
                           "files_bad": len(bad),
                           "readonly_files": sum(1 for v in res["files"].values()
                                                 if v.get("readonly"))})
    if res["junction"]["drift"]:
        res["errors"].append("junction_drift: pointer=%s junction=%s"
                             % (_display(str(pointer_dir)), res["junction"]["target"]))
    if bad:
        res["errors"].append("files_mismatch: %s" % ", ".join(sorted(bad)))
    # 版本一致性提示（不阻断）
    if res["manifest"]["version"] and version and res["manifest"]["version"] != version:
        res["warnings"].append("manifest_version_vs_pointer: %s != %s"
                               % (res["manifest"]["version"], version))

    res["ok"] = (not res["errors"]) and res["exit_code"] == 2  # errors 为空即一致性 ok
    res["exit_code"] = 0 if res["ok"] else 1

    # M9：bundle 辅助段（advisory；不改 ok/exit_code）
    if with_bundle:
        blob = _inspect_bundle(bundle_path)
        recorded = None
        prov_path = pointer_dir / "provenance.json"
        prov_meta = {"path": str(prov_path), "exists": prov_path.is_file(),
                     "recorded": None}
        if prov_meta["exists"]:
            payload, _err = _read_json(prov_path)
            if isinstance(payload, dict):
                rec = ((payload.get("auxiliary") or {})
                       if isinstance(payload.get("auxiliary"), dict) else {}).get("bundle")
                if not isinstance(rec, dict) or not rec:
                    rec = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else None
                recorded = rec
                prov_meta["recorded"] = rec
        drift = None
        if isinstance(recorded, dict) and recorded.get("sha256") and blob.get("sha256"):
            drift = str(recorded.get("sha256")) != str(blob.get("sha256"))
        res["auxiliary"] = {"schema": SCHEMA + "/auxiliary/v1",
                            "bundle": blob, "provenance": prov_meta,
                            "drift": drift,
                            "advisory": True,
                            "note": "bundle 是 auxiliary 证据：不改变 artifact ok/exit_code"}
        if blob["status"] == "unknown":
            res["warnings"].append("bundle_missing_or_unreadable: %s" % blob["detail"])
        elif blob["status"] == "fail":
            res["warnings"].append("bundle_bad: %s" % blob["detail"])
        if drift:
            res["warnings"].append(
                "bundle_drift: provenance 记录 sha=%s 实际 sha=%s（生成物会变，仅告警）"
                % (str((recorded or {}).get("sha256"))[:16], str(blob.get("sha256"))[:16]))
    return res


# ──────────────────────────────────────────────────────────────────────────
# --lock
# ──────────────────────────────────────────────────────────────────────────
def _lock_targets(pointer_dir: Path, files: dict) -> list[str]:
    names = [MANIFEST_NAME] + sorted(str(n) for n in files)
    return names


def lock(release_root: Path | str = DEFAULT_RELEASE_ROOT,
         lock_manifest: Path | str | None = None) -> dict:
    """--check 一致后把 artifact 文件设只读（不锁 current.json/junction）；幂等。"""
    root = Path(release_root)
    lock_path = Path(lock_manifest) if lock_manifest else root / LOCK_MANIFEST_NAME
    res = {"schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
           "mode": "lock", "ok": False, "exit_code": 1, "release_root": str(root),
           "lock_manifest": str(lock_path), "locked_at": _now(),
           "lock_manifest_existed": lock_path.is_file(),
           "files": {}, "summary": {"total": 0, "locked": 0, "already_locked": 0,
                                    "failed": 0},
           "warnings": [], "errors": []}
    chk = check(root)
    res["check"] = {"ok": chk["ok"], "exit_code": chk["exit_code"],
                    "errors": chk["errors"], "junction_drift": chk["junction"]["drift"]}
    if not chk["ok"]:
        res["errors"].append("lock_refused_artifact_not_consistent")
        res["exit_code"] = 1
        return res

    pointer_dir = Path(chk["pointer"]["dir"])
    files = chk["files"]
    targets = _lock_targets(pointer_dir, files)
    original_modes = {}
    applied = []
    for name in targets:
        fp = pointer_dir / name
        if not fp.is_file():
            res["warnings"].append("target_missing: %s" % name)
            continue
        mode = _mode(fp)
        expected_sha = (files.get(name) or {}).get("sha256") or (
            _sha256(pointer_dir / MANIFEST_NAME) if name == MANIFEST_NAME else "")
        rec = {"name": name, "path": str(fp), "bytes": fp.stat().st_size,
               "sha256": _sha256(fp), "original_mode": mode}
        res["files"][name] = rec
        if not bool(mode & 0o200):
            rec["already_locked"] = True
            res["summary"]["already_locked"] += 1
            continue
        ok, err = _set_mode(fp, mode & ~0o222)
        if ok:
            rec["locked"] = True
            original_modes[name] = mode
            applied.append(fp)
            res["summary"]["locked"] += 1
        else:
            rec["locked"] = False
            rec["error"] = err
            res["summary"]["failed"] += 1
            res["errors"].append("lock_failed: %s (%s)" % (name, err))

    if res["summary"]["failed"]:
        # 失败可见 + 回滚已加锁文件，避免半锁状态
        for fp in applied:
            mode = _mode(fp)
            _set_mode(fp, mode | 0o200)
        res["errors"].append("lock_aborted_and_reverted")
        res["exit_code"] = 1
        return res

    manifest = {
        "schema": SCHEMA + "/lock_manifest",
        "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "state": "locked", "locked_at": res["locked_at"],
        "release_root": str(root), "pointer_dir": str(pointer_dir),
        "version": chk["pointer"]["version"],
        "files": [res["files"][n] for n in res["files"]],
        "note": "只读锁；仅本工具 --unlock 可恢复；不锁 current.json/junction，不删任何文件",
    }
    _write_json_atomic(lock_path, manifest)
    res["summary"]["total"] = len(res["files"])
    res["ok"] = True
    res["exit_code"] = 0
    return res


# ──────────────────────────────────────────────────────────────────────────
# --unlock
# ──────────────────────────────────────────────────────────────────────────
def unlock(release_root: Path | str = DEFAULT_RELEASE_ROOT,
           lock_manifest: Path | str | None = None) -> dict:
    """只解除 lock manifest 记录的文件；幂等；不删文件；sha 变化则保持锁定并报 changed。"""
    root = Path(release_root)
    lock_path = Path(lock_manifest) if lock_manifest else root / LOCK_MANIFEST_NAME
    res = {"schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
           "mode": "unlock", "ok": False, "exit_code": 1, "release_root": str(root),
           "lock_manifest": str(lock_path), "unlocked_at": _now(),
           "files": {}, "changed": [], "summary": {"total": 0, "unlocked": 0,
                                                   "already_writable": 0,
                                                   "changed_kept_locked": 0,
                                                   "missing": 0},
           "warnings": [], "errors": []}
    if not lock_path.is_file():
        res.update({"ok": True, "exit_code": 0, "note": "no_lock_manifest"})
        return res
    manifest, err = _read_json(lock_path)
    if manifest is None:
        res["errors"].append("lock_manifest_invalid: %s" % err)
        res["exit_code"] = 2
        return res

    records = manifest.get("files") or []
    if not isinstance(records, list):
        res["errors"].append("lock_manifest_files_invalid")
        res["exit_code"] = 2
        return res

    all_writable = True
    for rec in records:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or "")
        fp = Path(str(rec.get("path") or (Path(manifest.get("pointer_dir") or "") / name)))
        info = {"name": name, "path": str(fp), "recorded_sha256": rec.get("sha256"),
                "state": ""}
        res["summary"]["total"] += 1
        if not fp.is_file():
            info["state"] = "missing"
            res["summary"]["missing"] += 1
            res["warnings"].append("recorded_file_missing: %s" % name)
            continue
        cur_sha = _sha256(fp)
        if rec.get("sha256") and cur_sha != rec.get("sha256"):
            info["state"] = "changed_kept_locked"
            info["current_sha256"] = cur_sha
            res["changed"].append(name)
            res["summary"]["changed_kept_locked"] += 1
            all_writable = False
            res["files"][name] = info
            continue
        target_mode = int(rec.get("original_mode") or 0o644)
        if _mode(fp) & 0o200:
            info["state"] = "already_writable"
            res["summary"]["already_writable"] += 1
        else:
            ok, err2 = _set_mode(fp, target_mode)
            info["state"] = "unlocked" if ok else "unlock_failed"
            if ok:
                res["summary"]["unlocked"] += 1
            else:
                all_writable = False
                info["error"] = err2
                res["errors"].append("unlock_failed: %s (%s)" % (name, err2))
        res["files"][name] = info

    manifest["state"] = "unlocked" if all_writable else "partially_locked"
    manifest["unlocked_at"] = res["unlocked_at"]
    manifest["changed_since_lock"] = list(res["changed"])
    try:
        _write_json_atomic(lock_path, manifest)
    except OSError as e:
        res["warnings"].append("lock_manifest_update_failed: %s" % e)

    res["ok"] = all_writable and not res["errors"]
    res["exit_code"] = 0 if res["ok"] else 1
    return res


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def _version_payload() -> dict:
    return {"schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
            "modes": ["--check", "--lock", "--unlock"],
            "check_flags": ["--with-bundle"],
            "bundle_default": str(DEFAULT_BUNDLE_PATH),
            "lock_manifest_default": LOCK_MANIFEST_NAME,
            "note": "只读校验 + 只读锁；不自动修复/不发布/不重启/不删文件"}


def render_human(res: dict) -> str:
    mode = res.get("mode", "")
    lines = ["== release artifact guard [%s] ==" % mode,
             "release_root: %s" % res.get("release_root", "")]
    if mode == "check":
        p, j, m = res["pointer"], res["junction"], res["manifest"]
        s = res["summary"]
        lines += [
            "pointer: version=%s dir=%s" % (p.get("version"), _display(p.get("dir", ""))),
            "junction: target=%s drift=%s" % (j.get("target"), j.get("drift")),
            "manifest: files=%s version=%s" % (m.get("files_count"), m.get("version")),
            "files: total=%s ok=%s bad=%s readonly=%s" % (
                s["files_total"], s["files_ok"], s["files_bad"], s["readonly_files"]),
        ]
        for name, info in sorted(res["files"].items()):
            mark = "OK " if info.get("match") else "BAD"
            lines.append("  [%s] %-18s bytes=%s expected=%s sha=%s" % (
                mark, name, info.get("bytes"), info.get("expected_bytes"),
                (info.get("sha256") or "")[:16]))
        aux = res.get("auxiliary") or {}
        bundle = aux.get("bundle") if isinstance(aux.get("bundle"), dict) else None
        if bundle:
            lines += [
                "auxiliary bundle（advisory，不影响 ok）: %s" % bundle.get("path"),
                "  exists=%s bytes=%s status=%s sha256=%s" % (
                    bundle.get("exists"), bundle.get("bytes"), bundle.get("status"),
                    str(bundle.get("sha256") or "")[:16]),
                "  detail: %s" % bundle.get("detail"),
            ]
            if aux.get("drift") is not None:
                lines.append("  provenance drift: %s" % aux.get("drift"))
    else:
        lines.append("ok=%s exit_code=%s" % (res.get("ok"), res.get("exit_code")))
        lines.append("summary: %s" % json.dumps(res.get("summary") or {}, ensure_ascii=False))
        if res.get("lock_manifest"):
            lines.append("lock_manifest: %s" % res["lock_manifest"])
    for w in res.get("warnings") or []:
        lines.append("WARN: %s" % w)
    for e in res.get("errors") or []:
        lines.append("ERROR: %s" % e)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="release artifact 完整性 guard + 只读锁（只读校验/可逆写保护；不修复/不发布）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="校验 current.json/junction/manifest/文件 sha")
    g.add_argument("--lock", action="store_true", help="把 artifact 文件设只读并记录 lock manifest")
    g.add_argument("--unlock", action="store_true", help="只解除 lock manifest 记录的文件")
    g.add_argument("--version", action="store_true", help="打印工具版本/schema")
    ap.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT))
    ap.add_argument("--lock-manifest", default="")
    ap.add_argument("--with-bundle", action="store_true",
                    help="--check 时附带 _vault bundle 证据（auxiliary/advisory，不改 ok/exit_code）")
    ap.add_argument("--bundle-path", default="",
                    help="bundle 路径（默认 env %s 或 %s）" % (BUNDLE_ENV, DEFAULT_BUNDLE_PATH))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        payload = _version_payload()
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    root = Path(args.release_root)
    lock_path = Path(args.lock_manifest) if args.lock_manifest else None
    if args.check:
        res = check(root, with_bundle=bool(args.with_bundle),
                    bundle_path=args.bundle_path or None)
    elif args.lock:
        res = lock(root, lock_path)
    else:
        res = unlock(root, lock_path)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif not args.quiet:
        print(render_human(res))
    return int(res.get("exit_code", 2))


if __name__ == "__main__":
    sys.exit(main())
