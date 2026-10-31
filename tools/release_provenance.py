# -*- coding: utf-8 -*-
"""release artifact provenance 生成/校验器（P0 发布供应链，SLSA 启发，零外部依赖）。

背景：``tools/release_legion_ide.py`` 的 artifact 只有 manifest（commit/version/逐文件
sha256），缺少「谁/何时/用哪份源码/工作树是否 dirty」的 provenance 证据。本工具在
**不改 release_legion_ide.py、不 promote、不重启**的前提下，为已构建 artifact 生成并
校验 ``provenance.json``。

用法：::

  # 默认只读演练：解析当前 artifact，打印 provenance + 校验结果（不写任何文件）
  python -X utf8 tools/release_provenance.py
  python -X utf8 tools/release_provenance.py --json
  python -X utf8 tools/release_provenance.py --version 20261031_143832_bfd3d5ec --json

  # 生成并写入 provenance.json（仅目标 version 目录；已存在需 --force）
  python -X utf8 tools/release_provenance.py --write
  python -X utf8 tools/release_provenance.py --write --force
  python -X utf8 tools/release_provenance.py --write --key-file <hmac-key>

  # 校验已有 provenance（tamper/缺失/漂移 → fail；已签名需同一 key 才能验签）
  python -X utf8 tools/release_provenance.py --verify --json
  python -X utf8 tools/release_provenance.py --verify --key-file <hmac-key> --require-signature

provenance 内容：
  artifact_version / release_dir / source_commit（manifest.commit 或 --source-commit）/
  git_dirty（生成时刻工作树状态，basis 注明）/ builder（python/platform/host）/
  build_time（manifest.built_at）/ manifest_sha256 / 逐文件 sha256+bytes /
  schema / tool / generated_at / signature（可选 HMAC-SHA256，无 key 则 null=unsigned）。

校验判定：
  - 生成（默认/--write）：current.json↔current junction↔目标目录一致性、manifest 可读、
    manifest schema/version/commit/built_at、逐文件 sha256+bytes 与实际文件一致 → 全 pass
    才允许 --write；任何 fail → 拒绝写（fail-closed）。
  - --verify：provenance 可读/schema、artifact_version/release_dir/manifest_sha256/
    build_time/manifest_commit 与 manifest 对齐、provenance.files 与 manifest 对齐、
    实际文件与 manifest 对齐（tamper/缺失）、签名（若有 key）→ 任一 fail 即 fail。
  - current 解析失败/缺 manifest/无 key 验签等环境性缺失 → unknown（不伪造 pass/fail）。

状态与退出码：``pass``→0；``fail``→1；``unknown``→2（含参数/环境错误）。
安全边界（代码强制）：不改 release_legion_ide.py、不 promote、不切 junction、不重启服务；
默认不写任何文件；``--write`` 只写目标 version 目录内的 ``provenance.json``。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "legion-ide-release-provenance/v1"
REPORT_SCHEMA = "release_provenance_report/v1"
TOOL_NAME = "release_provenance"
TOOL_VERSION = "1.0.0"
DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
CURRENT_JSON_NAME = "current.json"
CURRENT_LINK_NAME = "current"
MANIFEST_NAME = "manifest.json"
PROVENANCE_NAME = "provenance.json"
SIGN_ALGORITHM = "hmac-sha256"
KNOWN_MANIFEST_SCHEMAS = ("legion-ide-release/v1",)
VERSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# M9（K-ORG-20261101-BUNDLEPROV）：8103 实际消费的 _vault bundle 不在 artifact manifest；
# 作为 **auxiliary（辅助、非必需）** 段记录 path/exists/bytes/sha256/mtime，与 artifact 分开。
BUNDLE_NAME = "agent_ide_bundle.json"
DEFAULT_BUNDLE_PATH = Path(r"C:\项目\_vault\legion_ide\agent_ide_bundle.json")
BUNDLE_ENV = "DEVTOOL_IDE_BUNDLE_DEPLOY"
AUXILIARY_SCHEMA = "legion-ide-release-auxiliary/v1"

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
NOTES = (
    "默认只读演练：不加 --write 只打印报告，不写 provenance.json、不改 manifest/artifact/current",
    "--write 仅写目标 version 目录内的 provenance.json（已存在需 --force），不 promote、不切 junction、不重启",
    "不改 tools/release_legion_ide.py（本工具独立实现 manifest/文件校验）",
    "无 --key-file 时 provenance 标 unsigned（HMAC 只做来源签名，不替代完整性校验）",
    "source_commit 默认取 manifest.commit（构建 commit）；--source-commit 可显式覆盖并记录 override",
    "git_dirty 是 provenance 生成时刻的工作树状态（basis=provenance-generation-time），非构建时刻快照",
    "current artifact 解析优先 current.json 指针、其次 current junction；两者不一致=drift（fail，拒绝 --write）",
    "bundle 是 auxiliary（辅助证据）：记录 _vault/legion_ide/agent_ide_bundle.json 的 path/bytes/sha256/mtime；"
    "不写入 artifact manifest.files，也不参与 provenance 整体 pass/fail 判定（生成物会变，漂移/缺失只告警）",
)


# ---------------------------------------------------------------------------
# 基础工具（只读）
# ---------------------------------------------------------------------------
def ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 非标准流
            pass


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _read_json(path: Path) -> tuple[dict | None, str]:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8", "replace"))
    except OSError as exc:
        return None, "不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
    except ValueError as exc:
        return None, "非 JSON：%s" % str(exc)[:160]
    if not isinstance(data, dict):
        return None, "非 JSON 对象（%s）" % type(data).__name__
    return data, ""


def _display_target(value) -> str:
    text = str(value or "")
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def _path_present(path: Path) -> bool:
    return os.path.lexists(str(path)) or Path(path).exists()


def _link_target(path: Path) -> str:
    try:
        return os.readlink(str(path))
    except (OSError, ValueError):
        return ""


def _is_dir_link(path: Path) -> bool:
    try:
        if getattr(path, "is_junction", None) and path.is_junction():
            return True
    except OSError:
        return False
    try:
        os.readlink(str(path))
        return True
    except (OSError, ValueError):
        return False


def _norm_path(value) -> str:
    text = _display_target(os.path.normpath(str(value or "")))
    return os.path.normcase(text)


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child_key = _norm_path(child)
        parent_key = _norm_path(parent)
        return child_key == parent_key or child_key.startswith(parent_key.rstrip("\\/") + os.sep)
    except (OSError, ValueError):  # pragma: no cover - 极端非法路径
        return False


def _norm_status(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("pass", "ok", "true"):
        return "pass"
    if text in ("fail", "failed", "false"):
        return "fail"
    return "unknown"


def _crit(cid: str, title: str, status: str, detail: str, *,
          required: bool = True, evidence: dict | None = None, advisory: bool = False) -> dict:
    return {"id": cid, "title": title, "status": _norm_status(status),
            "detail": str(detail or ""), "required": bool(required),
            "advisory": bool(advisory), "evidence": evidence or {}}


def _status_from_checks(checks: list[dict]) -> str:
    hard = [c for c in checks if c.get("required") and not c.get("advisory")]
    if any(c["status"] == "fail" for c in hard):
        return "fail"
    if any(c["status"] == "unknown" for c in hard):
        return "unknown"
    return "pass"


def _counts(checks: list[dict]) -> dict:
    return {
        "pass": sum(1 for c in checks if c["status"] == "pass"),
        "fail": sum(1 for c in checks if c["status"] == "fail"),
        "unknown": sum(1 for c in checks if c["status"] == "unknown"),
        "total": len(checks),
    }


# ---------------------------------------------------------------------------
# 目标解析（release version 目录 / current）
# ---------------------------------------------------------------------------
def _valid_version_name(version: str) -> bool:
    return bool(version) and bool(VERSION_NAME_RE.match(version))


def resolve_target(release_root, version: str | None = None) -> dict:
    """解析目标 artifact 目录：显式 version 或 current（指针优先、junction 兜底）。"""
    root = Path(release_root)
    meta = {
        "ok": False, "error": "", "release_root": str(root), "version": "", "dir": "",
        "resolved_by": "", "root_exists": root.is_dir(),
        "current": {"pointer_version": "", "pointer_dir": "", "junction_target": "",
                    "junction_version": "", "junction_exists": False,
                    "junction_target_exists": False, "drift": False,
                    "drift_detail": ""},
    }
    if not root.is_dir():
        meta["error"] = "release root 不存在：%s" % root
        return meta
    current = meta["current"]
    current_json = root / CURRENT_JSON_NAME
    if current_json.is_file():
        data, err = _read_json(current_json)
        if err:
            meta["error"] = "current.json 不可读：%s" % err
            return meta
        current["pointer_version"] = str(data.get("version") or "")
        current["pointer_dir"] = str(data.get("dir") or "")
    link = root / CURRENT_LINK_NAME
    current["junction_exists"] = _path_present(link)
    if current["junction_exists"]:
        target = _display_target(_link_target(link))
        current["junction_target"] = target
        current["junction_version"] = Path(target).name if target else ""
        current["junction_target_exists"] = bool(target) and Path(target).is_dir()
    pointer_dir = current["pointer_dir"]
    junction_target = current["junction_target"]
    if pointer_dir and junction_target:
        current["drift"] = _norm_path(pointer_dir) != _norm_path(junction_target)
        current["drift_detail"] = ("current.json.dir=%s vs junction=%s"
                                   % (pointer_dir, junction_target))
    elif current["pointer_version"] and current["junction_version"]:
        current["drift"] = current["pointer_version"] != current["junction_version"]
        current["drift_detail"] = ("current.json.version=%s vs junction=%s"
                                   % (current["pointer_version"], current["junction_version"]))
    if version:
        name = str(version).strip()
        if not _valid_version_name(name):
            meta["error"] = "非法 version（只允许单层目录名 %r）" % name
            return meta
        target_dir = root / name
        if not target_dir.is_dir():
            meta["error"] = "目标 artifact 目录不存在：%s" % target_dir
            return meta
        meta.update({"ok": True, "version": name, "dir": str(target_dir),
                     "resolved_by": "explicit"})
        return meta
    if current["pointer_version"] and (root / current["pointer_version"]).is_dir():
        name = current["pointer_version"]
        resolved_by = "current.json"
    elif current["junction_version"] and (root / current["junction_version"]).is_dir():
        name = current["junction_version"]
        resolved_by = "current-junction"
    else:
        meta["error"] = "无法解析当前 artifact（current.json/current junction 均无效）"
        return meta
    meta.update({"ok": True, "version": name, "dir": str(root / name),
                 "resolved_by": resolved_by})
    return meta


# ---------------------------------------------------------------------------
# manifest / artifact / git / builder
# ---------------------------------------------------------------------------
def load_manifest(target_dir: Path) -> dict:
    path = Path(target_dir) / MANIFEST_NAME
    meta = {"ok": False, "error": "", "path": str(path), "manifest": None,
            "sha256": "", "bytes": 0}
    if not path.is_file():
        meta["error"] = "manifest.json 不存在：%s" % path
        return meta
    try:
        raw = path.read_bytes()
    except OSError as exc:
        meta["error"] = "manifest.json 不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        meta["error"] = "manifest.json 非 JSON：%s" % str(exc)[:160]
        return meta
    if not isinstance(data, dict):
        meta["error"] = "manifest.json 非 JSON 对象（%s）" % type(data).__name__
        return meta
    meta.update({"ok": True, "manifest": data, "sha256": _sha256_bytes(raw),
                 "bytes": len(raw)})
    return meta


def inspect_artifact(target_dir: Path, manifest: dict) -> dict:
    """逐文件校验 sha256/bytes（只读，路径不得逃出 artifact 目录）。"""
    target_dir = Path(target_dir)
    files = manifest.get("files")
    result = {"ok": False, "files": [], "errors": [], "extra_files": [],
              "files_total": 0, "files_matched": 0}
    if not isinstance(files, dict) or not files:
        result["errors"].append("manifest.files 为空或非对象")
        return result
    manifest_names = set()
    for name in sorted(files):
        manifest_names.add(str(name))
        meta = files[name]
        entry = {"name": str(name), "manifest_sha256": "", "manifest_bytes": None,
                 "actual_sha256": "", "actual_bytes": None, "exists": False,
                 "match": False, "error": ""}
        if not isinstance(meta, dict):
            entry["error"] = "manifest 元数据非对象"
            result["errors"].append("%s：manifest 元数据非对象" % name)
            result["files"].append(entry)
            continue
        expected_sha = str(meta.get("sha256") or "")
        expected_bytes = meta.get("bytes")
        entry["manifest_sha256"] = expected_sha
        entry["manifest_bytes"] = expected_bytes
        if not expected_sha:
            entry["error"] = "manifest 缺 sha256"
            result["errors"].append("%s：manifest 缺 sha256" % name)
            result["files"].append(entry)
            continue
        path = target_dir / str(name)
        if not _is_under(path, target_dir):
            entry["error"] = "路径逃出 artifact 目录"
            result["errors"].append("%s：路径逃出 artifact 目录" % name)
            result["files"].append(entry)
            continue
        if not path.is_file():
            entry["error"] = "文件缺失"
            result["errors"].append("%s：文件缺失" % name)
            result["files"].append(entry)
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            entry["error"] = "文件不可读：%s" % type(exc).__name__
            result["errors"].append("%s：文件不可读 %s" % (name, type(exc).__name__))
            result["files"].append(entry)
            continue
        actual_sha = _sha256_bytes(data)
        entry.update({"exists": True, "actual_sha256": actual_sha, "actual_bytes": len(data)})
        bytes_ok = True
        if expected_bytes is not None:
            try:
                bytes_ok = int(expected_bytes) == len(data)
            except (TypeError, ValueError):
                bytes_ok = False
        entry["match"] = actual_sha == expected_sha and bytes_ok
        if not entry["match"]:
            entry["error"] = "hash/bytes 不符（manifest=%s/%s actual=%s/%s）" % (
                expected_sha[:12], expected_bytes, actual_sha[:12], len(data))
            result["errors"].append("%s：hash/bytes 不符" % name)
        result["files"].append(entry)
    try:
        result["extra_files"] = sorted(
            p.name for p in target_dir.iterdir()
            if p.is_file() and p.name not in manifest_names
            and p.name not in (MANIFEST_NAME, PROVENANCE_NAME))
    except OSError:
        result["extra_files"] = []
    result["files_total"] = len(result["files"])
    result["files_matched"] = sum(1 for item in result["files"] if item["match"])
    result["ok"] = not result["errors"] and result["files_matched"] == result["files_total"]
    return result


def git_state(repo, enabled: bool = True) -> dict:
    """只读 git 状态：HEAD / 工作树是否 dirty（--no-git 时跳过）。"""
    meta = {"enabled": bool(enabled), "ok": False, "skipped": not enabled,
            "head": "", "dirty": None, "status_count": None, "status_sample": [],
            "error": ""}
    if not enabled:
        meta["error"] = "--no-git：跳过 git 状态采集"
        return meta
    repo = Path(repo)
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        meta["error"] = "git 不可用：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    if head.returncode != 0:
        meta["error"] = "git 不可用：%s" % ((head.stderr or "").strip()[:160] or "rc=%s" % head.returncode)
        return meta
    meta["head"] = (head.stdout or "").strip()
    try:
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        meta["error"] = "git status 不可用：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    if status.returncode != 0:
        meta["error"] = "git status 不可用：%s" % ((status.stderr or "").strip()[:160] or "rc")
        return meta
    lines = [line for line in (status.stdout or "").splitlines() if line.strip()]
    meta.update({"ok": True, "dirty": bool(lines), "status_count": len(lines),
                 "status_sample": lines[:5]})
    return meta


def builder_info() -> dict:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "host": socket.gethostname(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }


def resolve_source_commit(manifest: dict, override: str, git: dict) -> tuple[str, str]:
    commit = str(manifest.get("commit") or "").strip()
    source = "manifest.commit" if commit else ""
    if override:
        commit = str(override).strip()
        source = "user-override"
    if not commit and git.get("head"):
        commit = str(git["head"])
        source = "git-head-fallback"
    return commit, source


# ---------------------------------------------------------------------------
# HMAC 签名
# ---------------------------------------------------------------------------
def _canonical_payload(payload: dict) -> bytes:
    body = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _key_fingerprint(key: bytes) -> str:
    return hmac.new(key, b"legion-ide-provenance-key-id", hashlib.sha256).hexdigest()[:16]


def _read_key(key_file) -> tuple[bytes, str]:
    if not key_file:
        return b"", ""
    path = Path(key_file)
    try:
        key = path.read_bytes().strip()
    except OSError as exc:
        return b"", "key file 不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
    if not key:
        return b"", "key file 为空：%s" % path
    return key, ""


def _sign_payload(payload: dict, key: bytes) -> dict:
    value = hmac.new(key, _canonical_payload(payload), hashlib.sha256).hexdigest()
    return {"algorithm": SIGN_ALGORITHM, "value": value,
            "key_fingerprint": _key_fingerprint(key)}


def _verify_signature(payload: dict, signature: dict, key: bytes) -> bool:
    value = str((signature or {}).get("value") or "")
    expected = hmac.new(key, _canonical_payload(payload), hashlib.sha256).hexdigest()
    return bool(value) and hmac.compare_digest(expected, value)


# ---------------------------------------------------------------------------
# 生成（只读 / --write）
# ---------------------------------------------------------------------------
def resolve_bundle_path(bundle_path=None) -> Path:
    """bundle 路径：显式参数 > 环境变量 > 默认 _vault 部署副本。"""
    if bundle_path:
        return Path(bundle_path)
    env = os.environ.get(BUNDLE_ENV) or ""
    return Path(env) if env else DEFAULT_BUNDLE_PATH


def inspect_bundle(bundle_path=None) -> dict:
    """只读采集 bundle 辅助证据（不进 manifest.files；不参与必需判定）。"""
    path = resolve_bundle_path(bundle_path)
    info = {"path": str(path), "exists": False, "bytes": 0, "sha256": "",
            "mtime": "", "status": "unknown", "detail": ""}
    try:
        if not path.is_file():
            info["detail"] = "bundle 缺失（8103 回落仓内生成物；重建=rebake_ide_bundle.ps1）"
            return info
        st = path.stat()
        info.update(exists=True, bytes=st.st_size,
                    mtime=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)))
        if st.st_size <= 0:
            info.update(status="fail",
                        detail="bundle 零字节/损坏（8103 会读到空数据；需 rebake）")
        else:
            info.update(sha256=_sha256_file(path), status="pass",
                        detail="bundle exists bytes=%d sha256=%s"
                        % (st.st_size, _sha256_file(path)[:16]))
    except OSError as exc:
        info["detail"] = "bundle 读取失败：%s: %s" % (type(exc).__name__, str(exc)[:160])
    return info


def _bundle_checks(recorded: dict | None, bundle_path=None) -> tuple[dict, list[dict]]:
    """生成/校验共用的 bundle 辅助判据（全部 required=False/advisory，不影响 status）。"""
    current = inspect_bundle(bundle_path)
    checks: list[dict] = [ _crit(
        "bundle_current", "当前 bundle 可读且非空（辅助/advisory）",
        current["status"], "%s（path=%s）" % (current["detail"], current["path"]),
        required=False, advisory=True,
        evidence={"bundle": current}) ]
    rec = recorded if isinstance(recorded, dict) else None
    if not rec:
        checks.append(_crit(
            "bundle_recorded", "provenance.bundle 辅助段（兼容）", "pass",
            "旧 provenance 无 bundle 段（兼容跳过；新 --write 会记录）",
            required=False, advisory=True))
        checks.append(_crit(
            "bundle_drift", "bundle 与 provenance 记录一致（辅助/advisory）", "unknown",
            "无记录可比（旧 provenance）", required=False, advisory=True))
    else:
        checks.append(_crit(
            "bundle_recorded", "provenance.bundle 辅助段（兼容）", "pass",
            "记录 bytes=%r sha256=%s mtime=%r"
            % (rec.get("bytes"), str(rec.get("sha256") or "")[:16], rec.get("mtime")),
            required=False, advisory=True, evidence={"recorded": rec}))
        rec_sha = str(rec.get("sha256") or "")
        if not current["exists"]:
            checks.append(_crit(
                "bundle_drift", "bundle 与 provenance 记录一致（辅助/advisory）", "unknown",
                "bundle 现存缺失（provenance 记录 bytes=%r sha256=%s；8103 回落仓内）"
                % (rec.get("bytes"), rec_sha[:16]), required=False, advisory=True))
        elif not rec_sha:
            checks.append(_crit(
                "bundle_drift", "bundle 与 provenance 记录一致（辅助/advisory）", "unknown",
                "provenance 未记录 sha256，无法比对漂移", required=False, advisory=True))
        elif rec_sha != current["sha256"]:
            checks.append(_crit(
                "bundle_drift", "bundle 与 provenance 记录一致（辅助/advisory）", "unknown",
                "bundle 漂移：recorded=%s actual=%s（生成物会变；需 rebake/复核，不阻断 artifact）"
                % (rec_sha[:16], current["sha256"][:16]), required=False, advisory=True))
        else:
            checks.append(_crit(
                "bundle_drift", "bundle 与 provenance 记录一致（辅助/advisory）", "pass",
                "bundle sha256 与 provenance 记录一致（%s）" % rec_sha[:16],
                required=False, advisory=True))
    return current, checks


def build_provenance_payload(target: dict, manifest: dict, manifest_sha: str,
                             artifact: dict, git: dict, commit: str,
                             commit_source: str, bundle: dict | None = None) -> dict:
    files = {}
    for entry in artifact["files"]:
        if entry["match"]:
            files[entry["name"]] = {"sha256": entry["actual_sha256"],
                                    "bytes": entry["actual_bytes"]}
    baseline = manifest.get("hermes_baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    return {
        "schema": SCHEMA,
        "artifact_version": str(target.get("version") or manifest.get("version") or ""),
        "release_dir": str(target.get("dir") or ""),
        "manifest_schema": str(manifest.get("schema") or ""),
        "manifest_sha256": manifest_sha,
        "source_commit": commit,
        "source_commit_source": commit_source,
        "manifest_commit": str(manifest.get("commit") or ""),
        "build_time": str(manifest.get("built_at") or ""),
        "source_rel": manifest.get("source_rel"),
        "hermes_baseline": {"head": baseline.get("head"),
                            "dirty": baseline.get("dirty")},
        "git_dirty": git.get("dirty"),
        "git_head": str(git.get("head") or ""),
        "git_dirty_basis": ("not-collected" if not git.get("enabled")
                            else "provenance-generation-time"),
        "builder": builder_info(),
        "files": files,
        "files_total": len(files),
        # M9：bundle 辅助段（独立于 artifact files；不参与 manifest/整链判定）
        "auxiliary": {"schema": AUXILIARY_SCHEMA, "bundle": bundle or {}},
        "generated_at": _now(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "signature": None,
    }


def _current_unit_checks(current: dict, expect_version: str) -> list[dict]:
    pointer = str(current.get("pointer_version") or "")
    junction = str(current.get("junction_version") or "")
    checks = [
        _crit("pointer_version", "current.json 指针指向目标版本",
              "pass" if pointer == expect_version else "fail",
              "pointer_version=%r 期望=%r" % (pointer, expect_version)),
        _crit("junction_version", "current junction 指向目标版本",
              "pass" if junction == expect_version else "fail",
              "junction.version=%r 期望=%r（exists=%r）"
              % (junction, expect_version, current.get("junction_exists"))),
        _crit("pointer_junction_drift", "指针与 junction 无漂移",
              "fail" if current.get("drift") else "pass",
              current.get("drift_detail") or "pointer/junction 一致（drift=false）"),
    ]
    return checks


def _signing_check(key: bytes, key_error: str, requested: bool) -> dict:
    if not requested:
        return _crit("signing", "HMAC 签名（可选）", "pass",
                     "unsigned：未提供 --key-file（签名不参与完整性判定）", advisory=True)
    if key_error:
        return _crit("signing", "HMAC 签名（可选）", "unknown", key_error)
    return _crit("signing", "HMAC 签名（可选）", "pass",
                 "已用 %s 签名（key_fingerprint=%s）" % (SIGN_ALGORITHM, _key_fingerprint(key)))


def generate(release_root, version=None, *, repo=None, source_commit="", key_file="",
             no_git: bool = False, write: bool = False, force: bool = False,
             bundle_path="") -> dict:
    """生成 provenance 报告；``write=True`` 时（校验全 pass 才）写 provenance.json。"""
    started = time.perf_counter()
    repo = Path(repo or Path(__file__).resolve().parents[1])
    target = resolve_target(release_root, version)
    report = {
        "schema": REPORT_SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "mode": "write" if write else "generate",
        "generated_at": _now(), "release_root": str(Path(release_root)),
        "target": target, "current": target.get("current") or {},
        "manifest": {}, "provenance": None, "provenance_path": "",
        "files": [], "auxiliary": {"schema": AUXILIARY_SCHEMA, "bundle": {}},
        "signature": {"status": "unsigned", "required": False},
        "git": {}, "checks": [], "gaps": [], "write": None, "errors": [],
        "notes": list(NOTES),
    }
    checks: list[dict] = []
    git = git_state(repo, enabled=not no_git)
    report["git"] = git
    bundle = inspect_bundle(bundle_path or None)
    report["auxiliary"]["bundle"] = bundle
    key_bytes, key_error = _read_key(key_file) if key_file else (b"", "")
    requested_key = bool(key_file)
    payload = None
    manifest_meta = {"ok": False, "manifest": None, "sha256": "", "error": target.get("error") or ""}
    artifact: dict = {"ok": False, "files": [], "errors": [], "files_total": 0,
                      "files_matched": 0}
    try:
        checks.append(_crit("target_resolved", "解析目标 artifact",
                            "pass" if target.get("ok") else "unknown",
                            "version=%r dir=%s resolved_by=%s"
                            % (target.get("version"), target.get("dir"),
                               target.get("resolved_by"))
                            if target.get("ok") else (target.get("error") or "解析失败")))
        if target.get("ok"):
            if target.get("resolved_by") != "explicit":
                checks.extend(_current_unit_checks(report["current"], target["version"]))
            manifest_meta = load_manifest(Path(target["dir"]))
            manifest = manifest_meta.get("manifest") or {}
            report["manifest"] = {
                "path": manifest_meta.get("path"), "sha256": manifest_meta.get("sha256"),
                "bytes": manifest_meta.get("bytes"),
                "schema": manifest.get("schema"), "version": manifest.get("version"),
                "commit": manifest.get("commit"), "built_at": manifest.get("built_at"),
                "files_total": len(manifest.get("files") or {})
                if isinstance(manifest.get("files"), dict) else 0,
            }
            checks.append(_crit("manifest_readable", "manifest.json 可读且为 JSON 对象",
                                "pass" if manifest_meta.get("ok") else "fail",
                                manifest_meta.get("error") or "manifest sha256=%s"
                                % str(manifest_meta.get("sha256"))[:16]))
            manifest_schema = str(manifest.get("schema") or "")
            checks.append(_crit("manifest_schema", "manifest schema 受支持",
                                "pass" if manifest_schema in KNOWN_MANIFEST_SCHEMAS else "fail",
                                "schema=%r 期望 in %s"
                                % (manifest_schema, list(KNOWN_MANIFEST_SCHEMAS))))
            manifest_version = str(manifest.get("version") or "")
            checks.append(_crit("manifest_version", "manifest.version 与目标一致",
                                "pass" if manifest_version == target["version"] else "fail",
                                "manifest.version=%r target=%r"
                                % (manifest_version, target["version"])))
            if manifest_meta.get("ok"):
                artifact = inspect_artifact(Path(target["dir"]), manifest)
                report["files"] = artifact["files"]
                checks.append(_crit("artifact_files", "artifact 逐文件 sha256+bytes 一致",
                                    "pass" if artifact["ok"] else "fail",
                                    "matched=%s/%s errors=%s"
                                    % (artifact["files_matched"], artifact["files_total"],
                                       artifact["errors"][:3] or "无")))
            else:
                checks.append(_crit("artifact_files", "artifact 逐文件 sha256+bytes 一致",
                                    "unknown", "manifest 不可读，无法校验文件"))
            commit, commit_source = resolve_source_commit(manifest, source_commit, git)
            checks.append(_crit("source_commit", "source_commit 可判定",
                                "pass" if commit else "unknown",
                                "commit=%r（来源 %s）" % (commit, commit_source or "-")))
            build_time = str(manifest.get("built_at") or "").strip()
            checks.append(_crit("build_time", "build_time（manifest.built_at）存在",
                                "pass" if build_time else "unknown",
                                "built_at=%r" % build_time))
            if manifest_meta.get("ok"):
                payload = build_provenance_payload(
                    target, manifest, str(manifest_meta.get("sha256") or ""),
                    artifact, git, commit, commit_source, bundle=bundle)
                report["provenance"] = payload
                report["provenance_path"] = str(Path(target["dir"]) / PROVENANCE_NAME)
                if not artifact["ok"]:
                    checks.append(_crit("provenance_build", "provenance 可生成",
                                        "fail", "artifact 未通过完整性校验，拒绝生成/写入"))
                else:
                    checks.append(_crit("provenance_build", "provenance 可生成", "pass",
                                        "files=%s manifest_sha256=%s"
                                        % (payload["files_total"],
                                           payload["manifest_sha256"][:16])))
                # 签名（可选）
                if requested_key and not key_error and artifact["ok"]:
                    payload["signature"] = _sign_payload(payload, key_bytes)
                    report["signature"] = {
                        "status": "signed", "algorithm": SIGN_ALGORITHM,
                        "key_fingerprint": _key_fingerprint(key_bytes),
                        "present": True, "required": False,
                        "detail": "已用 HMAC-SHA256 签名",
                    }
                elif requested_key and key_error:
                    report["signature"] = {"status": "unverifiable", "present": False,
                                           "required": False, "detail": key_error}
                checks.append(_signing_check(key_bytes, key_error, requested_key))
                # M9：bundle 辅助判据（advisory，不影响生成/写入判定）
                checks.append(_crit(
                    "bundle_recorded", "bundle 辅助证据已记录（advisory）",
                    bundle["status"], "%s（path=%s）" % (bundle["detail"], bundle["path"]),
                    required=False, advisory=True, evidence={"bundle": bundle}))
                # git 状态为 advisory（不影响生成/写入判定，但如实记录）
                checks.append(_crit("git_state", "git 状态采集（advisory）",
                                    "pass" if git.get("ok") else "unknown",
                                    git.get("error") or "HEAD=%s dirty=%r status_count=%s"
                                    % (str(git.get("head"))[:12], git.get("dirty"),
                                       git.get("status_count")),
                                    required=False, advisory=True))
    except Exception as exc:  # noqa: BLE001 - 单点异常转 unknown，不伪造
        checks.append(_crit("generate_exception", "生成流程异常", "unknown",
                            "%s: %s" % (type(exc).__name__, str(exc)[:240])))
    status = _status_from_checks(checks)
    # --write：全 pass 且无 key 错误才落盘；已存在需 --force
    if write:
        write_info = {"requested": True, "written": False, "path": "", "reason": "",
                      "forced": bool(force)}
        if status != "pass":
            write_info["reason"] = "生成判据未全部通过（%s），拒绝写入 provenance" % status
            checks.append(_crit("provenance_write", "写入 provenance.json",
                                "fail" if status == "fail" else "unknown",
                                write_info["reason"]))
        elif not target.get("ok"):
            write_info["reason"] = "目标 artifact 未解析"
            checks.append(_crit("provenance_write", "写入 provenance.json", "fail",
                                write_info["reason"]))
        else:
            provenance_path = Path(target["dir"]) / PROVENANCE_NAME
            write_info["path"] = str(provenance_path)
            if provenance_path.exists() and not force:
                write_info["reason"] = "provenance.json 已存在（需 --force 覆盖）"
                checks.append(_crit("provenance_write", "写入 provenance.json", "fail",
                                    write_info["reason"]))
            else:
                try:
                    payload["signature"] = (report["signature"].get("present")
                                            and payload.get("signature")) or None
                    if requested_key and report["signature"].get("status") != "signed":
                        raise RuntimeError("签名未完成，拒绝写入")
                    tmp = provenance_path.with_name(PROVENANCE_NAME + ".tmp")
                    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
                    os.replace(str(tmp), str(provenance_path))
                    write_info.update({"written": True, "reason": ""})
                    checks.append(_crit("provenance_write", "写入 provenance.json", "pass",
                                        "已写入 %s" % provenance_path))
                except (OSError, RuntimeError) as exc:
                    write_info["reason"] = "写入失败：%s" % str(exc)[:200]
                    checks.append(_crit("provenance_write", "写入 provenance.json", "fail",
                                        write_info["reason"]))
        report["write"] = write_info
        status = _status_from_checks(checks)
    report["checks"] = checks
    report["counts"] = _counts(checks)
    report["status"] = status
    report["gaps"] = [{"id": c["id"], "status": c["status"], "required": c["required"],
                       "detail": c["detail"]} for c in checks if c["status"] != "pass"]
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    report["errors"] = [g["detail"] for g in report["gaps"] if g["required"]]
    return report


# ---------------------------------------------------------------------------
# 校验（--verify）
# ---------------------------------------------------------------------------
def verify(release_root, version=None, *, repo=None, source_commit="", key_file="",
           no_git: bool = False, require_signature: bool = False,
           bundle_path="") -> dict:
    """校验已有 provenance.json 与 manifest/实际文件/current 的一致性。"""
    started = time.perf_counter()
    repo = Path(repo or Path(__file__).resolve().parents[1])
    target = resolve_target(release_root, version)
    report = {
        "schema": REPORT_SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "mode": "verify", "generated_at": _now(),
        "release_root": str(Path(release_root)), "target": target,
        "current": target.get("current") or {}, "manifest": {}, "provenance": None,
        "provenance_path": "", "files": [],
        "auxiliary": {"schema": AUXILIARY_SCHEMA, "bundle": {}, "recorded": {}},
        "signature": {"status": "unsigned", "required": bool(require_signature)},
        "git": {}, "checks": [], "gaps": [], "write": None, "errors": [],
        "notes": list(NOTES),
    }
    checks: list[dict] = []
    git = git_state(repo, enabled=not no_git)
    report["git"] = git
    bundle_current = inspect_bundle(bundle_path or None)
    report["auxiliary"]["bundle"] = bundle_current
    key_bytes, key_error = _read_key(key_file) if key_file else (b"", "")
    target_ok = bool(target.get("ok"))
    checks.append(_crit("target_resolved", "解析目标 artifact",
                        "pass" if target_ok else "unknown",
                        "version=%r dir=%s resolved_by=%s"
                        % (target.get("version"), target.get("dir"), target.get("resolved_by"))
                        if target_ok else (target.get("error") or "解析失败")))
    provenance_payload = None
    manifest_meta = {"ok": False, "manifest": None, "sha256": "", "error": ""}
    artifact: dict = {"ok": False, "files": [], "errors": [], "files_total": 0,
                      "files_matched": 0}
    try:
        if target_ok:
            if target.get("resolved_by") != "explicit":
                checks.extend(_current_unit_checks(report["current"], target["version"]))
            provenance_path = Path(target["dir"]) / PROVENANCE_NAME
            report["provenance_path"] = str(provenance_path)
            provenance_meta = {"ok": False, "error": ""}
            if not provenance_path.is_file():
                provenance_meta["error"] = "provenance.json 不存在：%s" % provenance_path
            else:
                raw, err = _read_json(provenance_path)
                if err:
                    provenance_meta["error"] = "provenance.json 不可读：%s" % err
                else:
                    provenance_meta.update({"ok": True})
                    provenance_payload = raw
            report["provenance"] = provenance_payload
            checks.append(_crit("provenance_readable", "provenance.json 可读且为 JSON 对象",
                                "pass" if provenance_meta.get("ok") else "fail",
                                provenance_meta.get("error") or "已读取 %s" % provenance_path))
            manifest_meta = load_manifest(Path(target["dir"]))
            manifest = manifest_meta.get("manifest") or {}
            report["manifest"] = {
                "path": manifest_meta.get("path"), "sha256": manifest_meta.get("sha256"),
                "bytes": manifest_meta.get("bytes"),
                "schema": manifest.get("schema"), "version": manifest.get("version"),
                "commit": manifest.get("commit"), "built_at": manifest.get("built_at"),
                "files_total": len(manifest.get("files") or {})
                if isinstance(manifest.get("files"), dict) else 0,
            }
            checks.append(_crit("manifest_readable", "manifest.json 可读且为 JSON 对象",
                                "pass" if manifest_meta.get("ok") else "fail",
                                manifest_meta.get("error") or "manifest sha256=%s"
                                % str(manifest_meta.get("sha256"))[:16]))
            if manifest_meta.get("ok"):
                artifact = inspect_artifact(Path(target["dir"]), manifest)
                report["files"] = artifact["files"]
            prov = provenance_payload or {}
            checks.append(_crit("provenance_schema", "provenance schema 匹配",
                                "pass" if str(prov.get("schema") or "") == SCHEMA else "fail",
                                "schema=%r 期望=%r" % (prov.get("schema"), SCHEMA)))
            missing = [key for key in ("artifact_version", "release_dir", "source_commit",
                                       "manifest_sha256", "build_time", "files")
                       if not prov.get(key) and prov.get(key) != {}]
            checks.append(_crit("provenance_fields", "provenance 必需字段齐备",
                                "pass" if not missing else "fail",
                                "缺字段=%s" % (missing or "无")))
            checks.append(_crit("artifact_version",
                                "provenance.artifact_version 与目标一致",
                                "pass" if str(prov.get("artifact_version") or "")
                                == target.get("version") else "fail",
                                "provenance=%r target=%r（不一致=漂移）"
                                % (prov.get("artifact_version"), target.get("version"))))
            checks.append(_crit("release_dir", "provenance.release_dir 与目标目录一致",
                                "pass" if _norm_path(prov.get("release_dir"))
                                == _norm_path(target.get("dir")) else "fail",
                                "provenance=%r target=%r"
                                % (prov.get("release_dir"), target.get("dir"))))
            checks.append(_crit(
                "manifest_sha256", "manifest_sha256 与当前 manifest 一致",
                "pass" if str(prov.get("manifest_sha256") or "")
                == str(manifest_meta.get("sha256") or "") else "fail",
                "provenance=%s actual=%s（不一致=manifest 漂移/被改）"
                % (str(prov.get("manifest_sha256") or "-")[:16],
                   str(manifest_meta.get("sha256") or "-")[:16])))
            checks.append(_crit(
                "manifest_version", "manifest.version 与 provenance/目标一致",
                "pass" if (str(manifest.get("version") or "") == target.get("version")
                           == str(prov.get("artifact_version") or "")) else "fail",
                "manifest=%r provenance=%r target=%r"
                % (manifest.get("version"), prov.get("artifact_version"),
                   target.get("version"))))
            checks.append(_crit(
                "manifest_commit", "manifest.commit 与 provenance.manifest_commit 一致",
                "pass" if str(prov.get("manifest_commit") or "")
                == str(manifest.get("commit") or "") else "fail",
                "provenance=%r manifest=%r"
                % (prov.get("manifest_commit"), manifest.get("commit"))))
            checks.append(_crit(
                "build_time", "build_time 与 manifest.built_at 一致",
                "pass" if str(prov.get("build_time") or "")
                == str(manifest.get("built_at") or "") else "fail",
                "provenance=%r manifest=%r"
                % (prov.get("build_time"), manifest.get("built_at"))))
            prov_files = prov.get("files") if isinstance(prov.get("files"), dict) else {}
            manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
            declared_match = _files_map_matches(prov_files, manifest_files)
            checks.append(_crit("files_declared", "provenance.files 与 manifest.files 一致",
                                "pass" if declared_match else "fail",
                                "provenance=%s manifest=%s（不一致=漂移）"
                                % (len(prov_files), len(manifest_files))))
            checks.append(_crit("files_actual", "实际文件与 manifest 一致（tamper/缺失检查）",
                                "pass" if artifact["ok"] else "fail",
                                "matched=%s/%s errors=%s"
                                % (artifact["files_matched"], artifact["files_total"],
                                   artifact["errors"][:3] or "无")))
            # M9：bundle 辅助段比对（recorded vs 当前 _vault 文件；全部 advisory）
            recorded = ((prov.get("auxiliary") or {}) if isinstance(prov.get("auxiliary"), dict)
                        else {}).get("bundle")
            if not isinstance(recorded, dict) or not recorded:
                recorded = prov.get("bundle") if isinstance(prov.get("bundle"), dict) else None
            report["auxiliary"]["recorded"] = recorded or {}
            _cur, _bchecks = _bundle_checks(recorded, bundle_path or None)
            report["auxiliary"]["bundle"] = _cur
            checks.extend(_bchecks)
            signature = prov.get("signature")
            signature = signature if isinstance(signature, dict) else None
            if signature and signature.get("value"):
                if key_error or not key_bytes:
                    report["signature"] = {
                        "status": "unverifiable", "algorithm": signature.get("algorithm"),
                        "key_fingerprint": signature.get("key_fingerprint"),
                        "present": True, "required": bool(require_signature),
                        "detail": key_error or "provenance 已签名，但未提供 --key-file，无法验签",
                    }
                    checks.append(_crit("signature", "HMAC 签名校验", "unknown",
                                        report["signature"]["detail"]))
                elif _verify_signature(prov, signature, key_bytes):
                    report["signature"] = {
                        "status": "verified", "algorithm": SIGN_ALGORITHM,
                        "key_fingerprint": signature.get("key_fingerprint"),
                        "present": True, "required": bool(require_signature),
                        "detail": "HMAC-SHA256 验签通过",
                    }
                    checks.append(_crit("signature", "HMAC 签名校验", "pass",
                                        "验签通过（key_fingerprint=%s）"
                                        % signature.get("key_fingerprint")))
                else:
                    report["signature"] = {
                        "status": "invalid", "algorithm": signature.get("algorithm"),
                        "key_fingerprint": signature.get("key_fingerprint"),
                        "present": True, "required": bool(require_signature),
                        "detail": "验签失败（内容被改或 key 不匹配）",
                    }
                    checks.append(_crit("signature", "HMAC 签名校验", "fail",
                                        report["signature"]["detail"]))
            else:
                report["signature"] = {
                    "status": "unsigned", "present": False,
                    "required": bool(require_signature),
                    "detail": ("provenance 未签名，但 --require-signature 要求签名"
                               if require_signature else "unsigned（未签名，不参与完整性判定）"),
                }
                checks.append(_crit("signature", "HMAC 签名校验",
                                    "fail" if require_signature else "pass",
                                    report["signature"]["detail"]))
    except Exception as exc:  # noqa: BLE001 - 单点异常转 unknown，不伪造
        checks.append(_crit("verify_exception", "校验流程异常", "unknown",
                            "%s: %s" % (type(exc).__name__, str(exc)[:240])))
    report["checks"] = checks
    status = _status_from_checks(checks)
    report["status"] = status
    report["counts"] = _counts(checks)
    report["gaps"] = [{"id": c["id"], "status": c["status"], "required": c["required"],
                       "detail": c["detail"]} for c in checks if c["status"] != "pass"]
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    report["errors"] = [g["detail"] for g in report["gaps"] if g["required"]]
    return report


def _norm_files_map(files: dict) -> dict:
    out = {}
    for name, meta in files.items():
        if not isinstance(meta, dict):
            out[str(name)] = {"sha256": "", "bytes": None}
            continue
        raw_bytes = meta.get("bytes")
        try:
            raw_bytes = int(raw_bytes) if raw_bytes is not None else None
        except (TypeError, ValueError):
            raw_bytes = None
        out[str(name)] = {"sha256": str(meta.get("sha256") or ""), "bytes": raw_bytes}
    return out


def _files_map_matches(prov_files: dict, manifest_files: dict) -> bool:
    return _norm_files_map(prov_files) == _norm_files_map(manifest_files)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _label(status) -> str:
    return STATUS_LABEL.get(str(status), "UNKNOWN")


def render_markdown(report: dict) -> str:
    target = report.get("target") or {}
    manifest = report.get("manifest") or {}
    provenance = report.get("provenance") or {}
    signature = report.get("signature") or {}
    counts = report.get("counts") or {}
    lines = [
        "# release artifact provenance（P0 发布供应链）",
        "",
        "- 模式：%s · 生成时间：%s · 耗时：%sms" % (
            report.get("mode"), report.get("generated_at"), report.get("duration_ms")),
        "- release root：`%s`" % report.get("release_root"),
        "- 目标 artifact：%s（version=%r，解析方式=%s）" % (
            "已解析" if target.get("ok") else "未解析（%s）" % (target.get("error") or "-"),
            target.get("version"), target.get("resolved_by") or "-"),
        "",
        "**判定：%s**（通过 %s / 失败 %s / 未知 %s，共 %s 项）" % (
            _label(report.get("status")), counts.get("pass"), counts.get("fail"),
            counts.get("unknown"), counts.get("total")),
        "",
        "## provenance 摘要",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| artifact_version | %s |" % _md_cell(provenance.get("artifact_version")),
        "| source_commit | %s（%s）|" % (
            _md_cell(provenance.get("source_commit")),
            _md_cell(provenance.get("source_commit_source"))),
        "| build_time | %s |" % _md_cell(provenance.get("build_time")),
        "| manifest_sha256 | %s |" % _md_cell(provenance.get("manifest_sha256")),
        "| git_dirty / git_head | %s / %s |" % (
            _md_cell(provenance.get("git_dirty")),
            _md_cell(str(provenance.get("git_head") or "")[:16])),
        "| manifest schema/version/commit | %s / %s / %s |" % (
            _md_cell(manifest.get("schema")), _md_cell(manifest.get("version")),
            _md_cell(str(manifest.get("commit") or "")[:16])),
        "| files | %s |" % _md_cell(
            "%s 个" % len(provenance.get("files") or {})
            if isinstance(provenance.get("files"), dict) else "-"),
        "| signature | %s %s |" % (
            _md_cell(signature.get("status")),
            _md_cell(signature.get("key_fingerprint") or "")),
        "",
        "## 判据表",
        "",
        "| # | 判据 | 必需 | 状态 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(report.get("checks") or [], 1):
        required = "是" if item.get("required") else ("警告" if item.get("advisory") else "否")
        lines.append("| %d | %s | %s | %s | %s |" % (
            index, _md_cell(item.get("title")), required, _label(item.get("status")),
            _md_cell(item.get("detail"))))
    files = report.get("files") or []
    if files:
        lines += ["", "## 逐文件校验", "", "| 文件 | manifest sha256 | 实际 sha256 | 结果 |",
                  "|---|---|---|---|"]
        for item in files:
            lines.append("| %s | %s | %s | %s |" % (
                _md_cell(item.get("name")),
                _md_cell(str(item.get("manifest_sha256") or "")[:16]),
                _md_cell(str(item.get("actual_sha256") or "")[:16]),
                "PASS" if item.get("match") else "FAIL"))
    if report.get("current"):
        current = report["current"]
        lines += ["", "## current 发布单元（只读）", ""]
        lines.append("- pointer_version=%r · junction_version=%r · drift=%r"
                     % (current.get("pointer_version"), current.get("junction_version"),
                        current.get("drift")))
        if current.get("drift_detail"):
            lines.append("- detail：%s" % current.get("drift_detail"))
    auxiliary = report.get("auxiliary") or {}
    bundle = auxiliary.get("bundle") if isinstance(auxiliary.get("bundle"), dict) else {}
    if bundle:
        lines += ["", "## bundle 辅助证据（advisory；不参与 artifact 判定）", "",
                  "- path：`%s`" % _md_cell(bundle.get("path")),
                  "- exists=%s · bytes=%s · mtime=%s" % (
                      bundle.get("exists"), bundle.get("bytes"), bundle.get("mtime") or "-"),
                  "- sha256：`%s` · status=%s" % (
                      _md_cell(bundle.get("sha256") or "-"), _label(bundle.get("status"))),
                  "- detail：%s" % _md_cell(bundle.get("detail"))]
        if auxiliary.get("recorded"):
            lines.append("- provenance 记录：bytes=%r sha256=`%s`"
                         % ((auxiliary.get("recorded") or {}).get("bytes"),
                            _md_cell(str((auxiliary.get("recorded") or {}).get("sha256") or "")[:16])))
    write_info = report.get("write")
    if write_info:
        lines += ["", "## 写入", "",
                  "- 请求写入：%s · 已写入：%s · path=%s%s" % (
                      write_info.get("requested"), write_info.get("written"),
                      write_info.get("path") or "-",
                      (" · 强制覆盖" if write_info.get("forced") else "")),
                  ]
        if write_info.get("reason"):
            lines.append("- 原因：%s" % write_info.get("reason"))
    lines += ["", "## 缺口清单（%d）" % len(report.get("gaps") or []), ""]
    gaps = report.get("gaps") or []
    if gaps:
        lines += ["| # | 判据 | 必需 | 状态 | 说明 |", "|---|---|---|---|---|"]
        for index, gap in enumerate(gaps, 1):
            lines.append("| %d | %s | %s | %s | %s |" % (
                index, _md_cell(gap.get("id")), "是" if gap.get("required") else "否",
                _label(gap.get("status")), _md_cell(gap.get("detail"))))
    else:
        lines.append("无缺口（全部判据 PASS）。")
    lines += ["", "## 说明", ""]
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="release artifact provenance 生成/校验（默认只读演练；--write 才写 provenance.json）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="生成并写入 <version>/provenance.json（已存在需 --force；默认只打印）")
    mode.add_argument("--verify", action="store_true", help="校验已有 provenance.json（不写任何文件）")
    parser.add_argument("--no-write", action="store_true",
                        help="显式只读（默认行为；与 --write 互斥）")
    parser.add_argument("--force", action="store_true", help="--write 时允许覆盖已存在 provenance.json")
    parser.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT),
                        help="release root（默认生产 root %s）" % DEFAULT_RELEASE_ROOT)
    parser.add_argument("--version", default="", help="目标 version 目录名（缺省跟随 current）")
    parser.add_argument("--source-commit", default="",
                        help="显式覆盖 source_commit（默认取 manifest.commit）")
    parser.add_argument("--key-file", default="", help="可选 HMAC key 文件（无则 unsigned）")
    parser.add_argument("--require-signature", action="store_true",
                        help="--verify 时要求 provenance 必须已签名（unsigned → fail）")
    parser.add_argument("--repo", default="", help="git 仓库根（默认工具所在仓库；仅用于 git_dirty）")
    parser.add_argument("--no-git", action="store_true", help="跳过 git 状态采集（离线/mock）")
    parser.add_argument("--bundle-path", default="",
                        help="bundle 辅助证据路径（默认 env %s 或 %s）"
                        % (BUNDLE_ENV, DEFAULT_BUNDLE_PATH))
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="stdout 输出 JSON（稳定机器契约）")
    fmt.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    if args.no_write and args.write:
        print("参数错误：--write 与 --no-write 互斥", file=sys.stderr)
        return 2
    try:
        if args.verify:
            report = verify(
                args.release_root, version=(args.version or None),
                repo=(args.repo or None), source_commit=args.source_commit,
                key_file=args.key_file, no_git=args.no_git,
                require_signature=args.require_signature,
                bundle_path=args.bundle_path)
        else:
            report = generate(
                args.release_root, version=(args.version or None),
                repo=(args.repo or None), source_commit=args.source_commit,
                key_file=args.key_file, no_git=args.no_git,
                write=bool(args.write), force=bool(args.force),
                bundle_path=args.bundle_path)
    except Exception as exc:  # noqa: BLE001 - 环境异常也给出结构化 unknown
        report = {
            "schema": REPORT_SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
            "mode": "verify" if args.verify else "generate",
            "generated_at": _now(), "status": "unknown",
            "release_root": args.release_root, "target": {}, "provenance": None,
            "checks": [], "gaps": [], "counts": {}, "errors": [],
            "notes": list(NOTES),
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
        }
        report["errors"] = [report["error"]]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(report))
    status = str(report.get("status") or "unknown")
    return 0 if status == "pass" else (1 if status == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
