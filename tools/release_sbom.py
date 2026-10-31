# -*- coding: utf-8 -*-
"""release artifact SBOM 生成/校验器（CycloneDX 1.5 JSON，离线、零外部依赖）。

背景：``tools/release_legion_ide.py`` 产出的 artifact 只有 manifest；本工具在
**不联网、不 promote、不改 release_legion_ide.py** 前提下，为已构建 artifact 生成
``sbom.cdx.json``（CycloneDX 1.5 物料清单），并支持离线校验 tamper/缺失/漂移。

用法：::

  # 默认只读演练：解析当前 artifact，生成 BOM 并打印校验结果（不写任何文件）
  python -X utf8 tools/release_sbom.py
  python -X utf8 tools/release_sbom.py --json
  python -X utf8 tools/release_sbom.py --version 20261031_143832_bfd3d5ec --json

  # 写入 sbom.cdx.json（仅目标 version 目录；已存在需 --force；原子写）
  python -X utf8 tools/release_sbom.py --write
  python -X utf8 tools/release_sbom.py --write --force
  python -X utf8 tools/release_sbom.py --write --requirements requirements.txt

  # 校验已有 SBOM（tamper/缺文件/漂移 → fail）
  python -X utf8 tools/release_sbom.py --verify --json
  python -X utf8 tools/release_sbom.py --verify --requirements requirements.txt

BOM 内容（CycloneDX 1.5）：
  - ``bomFormat=CycloneDX`` / ``specVersion=1.5`` / ``serialNumber=urn:uuid:<deterministic>``
  - ``metadata``：timestamp、tool（release_sbom/版本）、root component
    （type=application，name=legion-ide，version=artifact 版本，hashes=SHA-256(manifest.json)，
    properties=artifact-version/manifest-sha256/source-commit/built-at/release-dir）
  - ``components``：artifact 逐个文件（type=file，name/version=artifact 版本、SHA-256、
    bytes 属性）；``--requirements`` 时把依赖包列为 type=library（purl 仅在可确定
    ``name==version`` 时生成，其余留空并在 properties 记录原始 requirement，不猜）
  - ``dependencies``：root dependsOn 全部组件 bom-ref

判定：
  - 生成（默认/--write）：target/current 单元一致、manifest 可读/schema/version、
    逐文件 sha256+bytes 与 manifest/实际一致 → 全 pass 才允许 --write（fail-closed）。
  - --verify：BOM 可读/格式/spec/version/serial 与 manifest+version 重算一致、
    root component hash/属性、components 文件集合+hash+bytes、dependencies、
    实际文件一致；提供 ``--requirements`` 时依赖组件集合漂移 → fail。
  - 环境性缺失（root/current 解析失败、BOM/requirements 不可读）→ unknown，不伪造。

退出码：``pass``→0；``fail``→1；``unknown``→2（含参数错误）。
安全边界（代码强制）：不联网（无网络库调用）、不 promote、不切 junction、不重启服务；
默认不写文件；``--write`` 只写目标 version 目录内的 ``sbom.cdx.json``。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

SCHEMA = "release_sbom_report/v1"
BOM_FORMAT = "CycloneDX"
SPEC_VERSION = "1.5"
TOOL_NAME = "release_sbom"
TOOL_VERSION = "1.0.0"
HASH_ALG = "SHA-256"
DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
CURRENT_JSON_NAME = "current.json"
CURRENT_LINK_NAME = "current"
MANIFEST_NAME = "manifest.json"
SBOM_NAME = "sbom.cdx.json"
ROOT_COMPONENT_NAME = "legion-ide"
KNOWN_MANIFEST_SCHEMAS = ("legion-ide-release/v1",)
VERSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# 固定命名空间：serialNumber = uuid5(NAMESPACE, "legion-ide/<version>/<manifest_sha256>")
SERIAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "legion-ide/release-sbom/v1")
REQUIREMENT_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")
EXACT_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9._+!-]*)$")

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
NOTES = (
    "默认只读演练：不加 --write 只打印报告与 BOM，不写 sbom.cdx.json、不改 manifest/artifact/current",
    "--write 仅写目标 version 目录内的 sbom.cdx.json（已存在需 --force；tmp+replace 原子写），不 promote/不切 junction/不重启",
    "离线：不访问网络/包仓库；purl 仅在 requirements 行可确定为 name==version 时生成，其余留空不猜",
    "不改 tools/release_legion_ide.py（本工具独立实现 manifest/文件校验）",
    "current artifact 解析优先 current.json 指针、其次 current junction；两者不一致=drift（fail，拒绝 --write）",
    "BOM serialNumber 由 version+manifest_sha256 确定性生成，--verify 会重算以此检出 BOM 被篡改",
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


def _read_json(path: Path) -> tuple[dict | None, str]:
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8", "replace"))
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


def _norm_path(value) -> str:
    return os.path.normcase(_display_target(os.path.normpath(str(value or ""))))


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


def _valid_version_name(version: str) -> bool:
    return bool(version) and bool(VERSION_NAME_RE.match(version))


# ---------------------------------------------------------------------------
# 目标 / manifest / 文件（只读）
# ---------------------------------------------------------------------------
def resolve_target(release_root, version: str | None = None) -> dict:
    """解析目标 artifact 目录：显式 version 或 current（指针优先、junction 兜底）。"""
    root = Path(release_root)
    meta = {
        "ok": False, "error": "", "release_root": str(root), "version": "", "dir": "",
        "resolved_by": "", "root_exists": root.is_dir(),
        "current": {"pointer_version": "", "pointer_dir": "", "junction_target": "",
                    "junction_version": "", "junction_exists": False,
                    "junction_target_exists": False, "drift": False, "drift_detail": ""},
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
    result = {"ok": False, "files": [], "errors": [], "files_total": 0, "files_matched": 0}
    if not isinstance(files, dict) or not files:
        result["errors"].append("manifest.files 为空或非对象")
        return result
    for name in sorted(files):
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
    result["files_total"] = len(result["files"])
    result["files_matched"] = sum(1 for item in result["files"] if item["match"])
    result["ok"] = not result["errors"] and result["files_matched"] == result["files_total"]
    return result


# ---------------------------------------------------------------------------
# requirements 解析（离线；purl 仅在可确定时生成，不猜）
# ---------------------------------------------------------------------------
def _normalize_pypi_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


def parse_requirements_text(text: str, source: str = "") -> dict:
    """解析 requirements 文本为 CycloneDX library components（离线，无包仓库查询）。"""
    components: list[dict] = []
    skipped: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "-c ", "-e ", "--")):
            skipped.append(raw.strip())
            continue
        line = re.split(r"\s+#", line, 1)[0].strip()
        if not line:
            continue
        spec = line.split(";", 1)[0].strip()
        match = REQUIREMENT_LINE_RE.match(spec)
        if not match:
            skipped.append(raw.strip())
            continue
        name = match.group(1)
        purl = ""
        version = ""
        exact = EXACT_PIN_RE.match(line)
        if exact:
            version = exact.group(2).strip()
            purl = "pkg:pypi/%s@%s" % (_normalize_pypi_name(name), version)
        component = {
            "type": "library",
            "bom-ref": purl or ("req:pypi/" + _normalize_pypi_name(name)),
            "name": name,
        }
        if version:
            component["version"] = version
        if purl:
            component["purl"] = purl
        component["properties"] = [
            {"name": "legion:requirement-spec", "value": spec or name},
            {"name": "legion:requirement-source", "value": source or "requirements"},
        ]
        components.append(component)
    # 去重（同 bom-ref 保留首个）+ 稳定排序
    seen = set()
    unique = []
    for component in sorted(components, key=lambda item: (item["name"].lower(), item["bom-ref"])):
        if component["bom-ref"] in seen:
            skipped.append("duplicate: %s" % component["name"])
            continue
        seen.add(component["bom-ref"])
        unique.append(component)
    return {"components": unique, "skipped": skipped[:40],
            "files": [], "error": ""}


def collect_requirements(paths) -> dict:
    """读取一个或多个 requirements 文件（只读）；任一不可读 → error（不伪造）。"""
    result = {"components": [], "skipped": [], "files": [], "error": ""}
    if not paths:
        return result
    merged: list[dict] = []
    skipped: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        result["files"].append(str(path))
        if not path.is_file():
            result["error"] = "requirements 文件不可读（不存在）：%s" % path
            return result
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            result["error"] = "requirements 文件不可读：%s: %s" % (
                type(exc).__name__, str(exc)[:160])
            return result
        parsed = parse_requirements_text(text, source=path.name)
        merged.extend(parsed["components"])
        skipped.extend(parsed["skipped"])
    seen = set()
    components = []
    for component in sorted(merged, key=lambda item: (item["name"].lower(), item["bom-ref"])):
        if component["bom-ref"] in seen:
            skipped.append("duplicate: %s" % component["name"])
            continue
        seen.add(component["bom-ref"])
        components.append(component)
    result.update({"components": components, "skipped": skipped[:40]})
    return result


# ---------------------------------------------------------------------------
# BOM 生成
# ---------------------------------------------------------------------------
def bom_serial(version: str, manifest_sha: str) -> str:
    return "urn:uuid:%s" % uuid.uuid5(SERIAL_NAMESPACE, "legion-ide/%s/%s"
                                     % (version, manifest_sha))


def root_bom_ref(version: str) -> str:
    return "pkg:generic/%s@%s" % (ROOT_COMPONENT_NAME, version)


def build_bom(target: dict, manifest: dict, manifest_sha: str, artifact: dict,
              requirements: dict, generated_at: str | None = None) -> dict:
    """构造 CycloneDX 1.5 BOM（确定性：serial 由 version+manifest_sha 推导）。"""
    version = str(target.get("version") or manifest.get("version") or "")
    commit = str(manifest.get("commit") or "")
    built_at = str(manifest.get("built_at") or "")
    files = []
    for entry in artifact.get("files") or []:
        if not entry.get("match"):
            continue
        files.append({
            "type": "file",
            "bom-ref": "file:" + entry["name"],
            "name": entry["name"],
            "version": version,
            "hashes": [{"alg": HASH_ALG, "content": entry["actual_sha256"]}],
            "properties": [
                {"name": "legion:bytes", "value": str(entry["actual_bytes"])},
                {"name": "legion:artifact-version", "value": version},
            ],
        })
    libraries = list((requirements or {}).get("components") or [])
    components = files + libraries
    metadata_properties = []
    requirements_files = (requirements or {}).get("files") or []
    if requirements_files:
        metadata_properties.append({"name": "legion:requirements-files",
                                    "value": ";".join(requirements_files)})
    root_component = {
        "type": "application",
        "bom-ref": root_bom_ref(version),
        "name": ROOT_COMPONENT_NAME,
        "version": version,
        "hashes": [{"alg": HASH_ALG, "content": manifest_sha}],
        "properties": [
            {"name": "legion:artifact-version", "value": version},
            {"name": "legion:manifest-sha256", "value": manifest_sha},
            {"name": "legion:source-commit", "value": commit},
            {"name": "legion:built-at", "value": built_at},
            {"name": "legion:release-dir", "value": str(target.get("dir") or "")},
        ],
    }
    return {
        "bomFormat": BOM_FORMAT,
        "specVersion": SPEC_VERSION,
        "serialNumber": bom_serial(version, manifest_sha),
        "version": 1,
        "metadata": {
            "timestamp": generated_at or _now(),
            "tools": [{"vendor": "zerobound", "name": TOOL_NAME, "version": TOOL_VERSION}],
            "component": root_component,
            "properties": metadata_properties,
        },
        "components": components,
        "dependencies": [{
            "ref": root_bom_ref(version),
            "dependsOn": sorted(component["bom-ref"] for component in components),
        }],
    }


# ---------------------------------------------------------------------------
# BOM 校验
# ---------------------------------------------------------------------------
def _hash_map(component: dict) -> dict:
    hashes = component.get("hashes") if isinstance(component, dict) else None
    out = {}
    for item in hashes or []:
        if isinstance(item, dict) and str(item.get("alg") or "").upper() in (
                "SHA-256", "SHA256"):
            out["sha256"] = str(item.get("content") or "").lower()
    return out


def _component_properties(component: dict) -> dict:
    props = {}
    for item in (component or {}).get("properties") or []:
        if isinstance(item, dict) and item.get("name"):
            props[str(item["name"])] = str(item.get("value") or "")
    return props


def verify_bom_against_manifest(bom: dict, target: dict, manifest: dict, manifest_sha: str,
                                artifact: dict, requirements: dict,
                                requirements_given: bool) -> tuple[list[dict], dict]:
    """BOM ↔ manifest ↔ 实际文件 一致性判据；返回 (checks, component_counts)。"""
    checks: list[dict] = []
    version = str(target.get("version") or "")
    components = bom.get("components") if isinstance(bom.get("components"), list) else []
    metadata = bom.get("metadata") if isinstance(bom.get("metadata"), dict) else {}
    root = metadata.get("component") if isinstance(metadata.get("component"), dict) else {}
    hashes = _hash_map(root)
    props = _component_properties(root)
    checks.append(_crit("bom_format", "bomFormat=CycloneDX", "pass"
                        if str(bom.get("bomFormat") or "") == BOM_FORMAT else "fail",
                        "bomFormat=%r 期望=%r" % (bom.get("bomFormat"), BOM_FORMAT)))
    checks.append(_crit("bom_spec", "specVersion=1.5", "pass"
                        if str(bom.get("specVersion") or "") == SPEC_VERSION else "fail",
                        "specVersion=%r 期望=%r" % (bom.get("specVersion"), SPEC_VERSION)))
    checks.append(_crit("bom_version", "BOM version=1（本工具口径）", "pass"
                        if bom.get("version") == 1 else "fail",
                        "version=%r" % (bom.get("version"),)))
    expected_serial = bom_serial(version, manifest_sha)
    checks.append(_crit("serial_number", "serialNumber 与 version+manifest_sha256 重算一致",
                        "pass" if str(bom.get("serialNumber") or "") == expected_serial
                        else "fail",
                        "serial=%r 期望=%r（不一致=BOM 被篡改或 artifact 漂移）"
                        % (bom.get("serialNumber"), expected_serial)))
    checks.append(_crit("root_component", "root component name/version/hash 正确",
                        "pass" if (str(root.get("name") or "") == ROOT_COMPONENT_NAME
                                   and str(root.get("version") or "") == version
                                   and hashes.get("sha256") == manifest_sha.lower())
                        else "fail",
                        "name=%r version=%r sha256=%s manifest=%s"
                        % (root.get("name"), root.get("version"),
                           str(hashes.get("sha256") or "-")[:16], manifest_sha[:16])))
    checks.append(_crit("root_properties",
                        "root properties source-commit/built-at/release-dir 与 manifest 一致",
                        "pass" if (props.get("legion:source-commit", "")
                                   == str(manifest.get("commit") or "")
                                   and props.get("legion:built-at", "")
                                   == str(manifest.get("built_at") or "")
                                   and _norm_path(props.get("legion:release-dir", ""))
                                   == _norm_path(target.get("dir") or ""))
                        else "fail",
                        "commit=%r built_at=%r release_dir=%r"
                        % (props.get("legion:source-commit"),
                           props.get("legion:built-at"),
                           props.get("legion:release-dir"))))

    file_components = {}
    library_components = []
    malformed = []
    for component in components:
        if not isinstance(component, dict):
            malformed.append(str(component))
            continue
        if str(component.get("type") or "") == "file":
            file_components[str(component.get("name") or "")] = component
        elif str(component.get("type") or "") == "library":
            library_components.append(component)
        else:
            malformed.append("%s:%s" % (component.get("type"), component.get("name")))
    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    expected_names = set(manifest_files)
    actual_names = set(file_components)
    names_ok = expected_names == actual_names
    checks.append(_crit("component_files", "文件 components 集合与 manifest.files 一致",
                        "pass" if names_ok else "fail",
                        "bom=%s manifest=%s missing=%s extra=%s"
                        % (len(actual_names), len(expected_names),
                           sorted(expected_names - actual_names)[:4],
                           sorted(actual_names - expected_names)[:4])))
    hash_mismatch = []
    if names_ok:
        artifact_by_name = {entry["name"]: entry for entry in artifact.get("files") or []}
        for name in sorted(expected_names):
            component = file_components[name]
            comp_hashes = _hash_map(component)
            comp_props = _component_properties(component)
            manifest_sha_expected = str((manifest_files.get(name) or {}).get("sha256") or "").lower()
            artifact_entry = artifact_by_name.get(name) or {}
            if comp_hashes.get("sha256") != manifest_sha_expected:
                hash_mismatch.append("%s:hash" % name)
            if comp_props.get("legion:bytes") != str(artifact_entry.get("actual_bytes", "")):
                hash_mismatch.append("%s:bytes" % name)
            if str(comp_props.get("legion:artifact-version") or "") != version:
                hash_mismatch.append("%s:version" % name)
    checks.append(_crit("component_hashes", "文件 components hash/bytes/version 正确",
                        "pass" if not hash_mismatch else "fail",
                        "不一致=%s" % (hash_mismatch[:5] or "无")))
    expected_file_refs = {"file:" + name for name in expected_names}
    dependencies = bom.get("dependencies") if isinstance(bom.get("dependencies"), list) else []
    depends_on = set()
    for item in dependencies:
        if isinstance(item, dict) and str(item.get("ref") or "") == root_bom_ref(version):
            depends_on = {str(ref) for ref in item.get("dependsOn") or []}
    all_refs = expected_file_refs | {library.get("bom-ref") for library in library_components}
    checks.append(_crit("dependencies", "dependencies: root dependsOn 覆盖全部组件 bom-ref",
                        "pass" if depends_on == all_refs else "fail",
                        "dependsOn=%s 期望=%s"
                        % (len(depends_on), len(all_refs))))
    checks.append(_crit("component_types", "components 仅允许 file/library 类型",
                        "pass" if not malformed else "fail",
                        "非法组件=%s" % (malformed[:5] or "无")))
    library_ok = True
    library_problem = ""
    seen_refs = set()
    for library in library_components:
        ref = str(library.get("bom-ref") or "")
        name = str(library.get("name") or "")
        if not name or not ref:
            library_ok = False
            library_problem = "library 组件缺 name/bom-ref"
            break
        if ref in seen_refs:
            library_ok = False
            library_problem = "library bom-ref 重复：%s" % ref
            break
        seen_refs.add(ref)
        purl = str(library.get("purl") or "")
        if purl and not purl.startswith("pkg:pypi/"):
            library_ok = False
            library_problem = "library purl 非 pkg:pypi：%s" % purl
            break
    checks.append(_crit("library_components", "依赖 library components 自洽（purl 仅 pkg:pypi）",
                        "pass" if library_ok else "fail", library_problem or
                        "library=%s（离线生成，purl 可留空不猜）" % len(library_components)))
    if requirements_given:
        expected = _norm_library_components((requirements or {}).get("components") or [])
        recorded = _norm_library_components(library_components)
        drift = expected != recorded
        checks.append(_crit("requirements_components",
                            "依赖 components 与 --requirements 一致（漂移检测）",
                            "fail" if drift else "pass",
                            "requirements=%s bom=%s" % (len(expected), len(recorded))
                            if not drift else "依赖组件与 requirements 不一致（expected %s / bom %s）"
                            % (len(expected), len(recorded))))
    return checks, {"files": len(file_components), "libraries": len(library_components)}


def _norm_library_components(components: list) -> list:
    out = []
    for component in components or []:
        if not isinstance(component, dict):
            continue
        purl = str(component.get("purl") or "")
        out.append((str(component.get("name") or ""),
                    str(component.get("version") or ""),
                    purl,
                    _component_properties(component).get("legion:requirement-spec", "")))
    return sorted(out)


def _requirements_components_match(requirements: dict, bom_library_components: list) -> bool:
    return (_norm_library_components((requirements or {}).get("components") or [])
            == _norm_library_components(bom_library_components))


# ---------------------------------------------------------------------------
# 生成（只读 / --write）
# ---------------------------------------------------------------------------
def _current_unit_checks(current: dict, expect_version: str) -> list[dict]:
    pointer = str(current.get("pointer_version") or "")
    junction = str(current.get("junction_version") or "")
    return [
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


def generate(release_root, version=None, *, requirements=None,
             write: bool = False, force: bool = False,
             timestamp: str | None = None) -> dict:
    """生成 SBOM 报告；``write=True`` 时（校验全 pass 才）原子写 sbom.cdx.json。"""
    started = time.perf_counter()
    target = resolve_target(release_root, version)
    req_paths = list(requirements or [])
    report = {
        "schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "mode": "write" if write else "generate",
        "generated_at": timestamp or _now(), "release_root": str(Path(release_root)),
        "target": target, "current": target.get("current") or {},
        "manifest": {}, "sbom": None, "sbom_path": "", "components": {},
        "requirements": {"files": req_paths, "components": [], "skipped": [], "error": ""},
        "files": [], "checks": [], "gaps": [], "counts": {}, "write": None,
        "status": "unknown", "duration_ms": 0, "errors": [], "notes": list(NOTES),
    }
    checks: list[dict] = []
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
            checks.append(_crit("source_commit", "manifest.commit 存在（BOM 溯源用）",
                                "pass" if str(manifest.get("commit") or "").strip()
                                else "unknown",
                                "commit=%r" % (manifest.get("commit"),)))
            checks.append(_crit("build_time", "manifest.built_at 存在（BOM timestamp 锚点）",
                                "pass" if str(manifest.get("built_at") or "").strip()
                                else "unknown",
                                "built_at=%r" % (manifest.get("built_at"),)))
            if manifest_meta.get("ok"):
                artifact = inspect_artifact(Path(target["dir"]), manifest)
                report["files"] = artifact["files"]
                checks.append(_crit("artifact_files", "artifact 逐文件 sha256+bytes 一致",
                                    "pass" if artifact["ok"] else "fail",
                                    "matched=%s/%s errors=%s"
                                    % (artifact["files_matched"], artifact["files_total"],
                                       artifact["errors"][:3] or "无")))
                requirements = collect_requirements(req_paths)
                report["requirements"].update({
                    "components": requirements["components"],
                    "skipped": requirements["skipped"],
                    "error": requirements["error"],
                    "components_total": len(requirements["components"]),
                })
                if requirements["error"]:
                    checks.append(_crit("requirements_parse", "requirements 可读/可解析",
                                        "unknown", requirements["error"]))
                elif req_paths:
                    checks.append(_crit("requirements_parse", "requirements 可读/可解析",
                                        "pass",
                                        "files=%s components=%s skipped=%s"
                                        % (len(req_paths), len(requirements["components"]),
                                           len(requirements["skipped"]))))
                else:
                    checks.append(_crit("requirements_parse", "requirements（可选）",
                                        "pass", "未提供 --requirements（BOM 只含 artifact 文件）",
                                        advisory=True))
                if artifact["ok"]:
                    bom = build_bom(target, manifest, str(manifest_meta.get("sha256") or ""),
                                    artifact, requirements, report["generated_at"])
                    report["sbom"] = bom
                    report["sbom_path"] = str(Path(target["dir"]) / SBOM_NAME)
                    report["components"] = {
                        "files": sum(1 for c in bom["components"] if c.get("type") == "file"),
                        "libraries": sum(1 for c in bom["components"]
                                         if c.get("type") == "library"),
                        "total": len(bom["components"]),
                    }
                    checks.append(_crit("sbom_build", "BOM 可生成", "pass",
                                        "components=%s files=%s libraries=%s serial=%s"
                                        % (report["components"]["total"],
                                           report["components"]["files"],
                                           report["components"]["libraries"],
                                           bom["serialNumber"])))
                else:
                    checks.append(_crit("sbom_build", "BOM 可生成", "fail",
                                        "artifact 未通过完整性校验，拒绝生成/写入"))
    except Exception as exc:  # noqa: BLE001 - 单点异常转 unknown，不伪造
        checks.append(_crit("generate_exception", "生成流程异常", "unknown",
                            "%s: %s" % (type(exc).__name__, str(exc)[:240])))
    status = _status_from_checks(checks)
    if write:
        write_info = {"requested": True, "written": False, "path": "", "reason": "",
                      "forced": bool(force)}
        if status != "pass":
            write_info["reason"] = "生成判据未全部通过（%s），拒绝写入 SBOM" % status
            checks.append(_crit("sbom_write", "写入 sbom.cdx.json",
                                "fail" if status == "fail" else "unknown",
                                write_info["reason"]))
        else:
            sbom_path = Path(report["sbom_path"])
            write_info["path"] = str(sbom_path)
            if sbom_path.exists() and not force:
                write_info["reason"] = "sbom.cdx.json 已存在（需 --force 覆盖）"
                checks.append(_crit("sbom_write", "写入 sbom.cdx.json", "fail",
                                    write_info["reason"]))
            else:
                try:
                    payload = dict(report["sbom"] or {})
                    payload["metadata"] = dict(payload.get("metadata") or {})
                    payload["metadata"]["timestamp"] = _now()
                    tmp = sbom_path.with_name(SBOM_NAME + ".tmp")
                    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
                    os.replace(str(tmp), str(sbom_path))
                    report["sbom"] = payload
                    write_info.update({"written": True, "reason": ""})
                    checks.append(_crit("sbom_write", "写入 sbom.cdx.json", "pass",
                                        "已原子写入 %s" % sbom_path))
                except OSError as exc:
                    write_info["reason"] = "写入失败：%s" % str(exc)[:200]
                    checks.append(_crit("sbom_write", "写入 sbom.cdx.json", "fail",
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
def verify(release_root, version=None, *, requirements=None) -> dict:
    """校验已有 sbom.cdx.json 与 manifest/实际文件/current 的一致性。"""
    started = time.perf_counter()
    target = resolve_target(release_root, version)
    req_paths = list(requirements or [])
    report = {
        "schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "mode": "verify", "generated_at": _now(), "release_root": str(Path(release_root)),
        "target": target, "current": target.get("current") or {},
        "manifest": {}, "sbom": None, "sbom_path": "", "components": {},
        "requirements": {"files": req_paths, "components": [], "skipped": [], "error": ""},
        "files": [], "checks": [], "gaps": [], "counts": {}, "write": None,
        "status": "unknown", "duration_ms": 0, "errors": [], "notes": list(NOTES),
    }
    checks: list[dict] = []
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
            sbom_path = Path(target["dir"]) / SBOM_NAME
            report["sbom_path"] = str(sbom_path)
            bom, sbom_error = _read_json(sbom_path) if sbom_path.is_file() else (
                None, "sbom.cdx.json 不存在：%s" % sbom_path)
            report["sbom"] = bom
            checks.append(_crit("sbom_readable", "sbom.cdx.json 可读且为 JSON 对象",
                                "pass" if bom is not None else "fail",
                                sbom_error or "已读取 %s" % sbom_path))
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
            checks.append(_crit("manifest_version", "manifest.version 与目标一致",
                                "pass" if str(manifest.get("version") or "")
                                == target["version"] else "fail",
                                "manifest.version=%r target=%r"
                                % (manifest.get("version"), target.get("version"))))
            if manifest_meta.get("ok"):
                artifact = inspect_artifact(Path(target["dir"]), manifest)
                report["files"] = artifact["files"]
                checks.append(_crit("files_actual", "实际文件与 manifest 一致（tamper/缺失检查）",
                                    "pass" if artifact["ok"] else "fail",
                                    "matched=%s/%s errors=%s"
                                    % (artifact["files_matched"], artifact["files_total"],
                                       artifact["errors"][:3] or "无")))
                requirements = collect_requirements(req_paths)
                report["requirements"].update({
                    "components": requirements["components"],
                    "skipped": requirements["skipped"],
                    "error": requirements["error"],
                    "components_total": len(requirements["components"]),
                })
                if requirements["error"]:
                    checks.append(_crit("requirements_parse", "requirements 可读/可解析",
                                        "unknown", requirements["error"]))
                if artifact["ok"] and isinstance(bom, dict):
                    bom_checks, component_counts = verify_bom_against_manifest(
                        bom, target, manifest, str(manifest_meta.get("sha256") or ""),
                        artifact, requirements, bool(req_paths))
                    checks.extend(bom_checks)
                    report["components"] = component_counts
                elif not isinstance(bom, dict):
                    checks.append(_crit("bom_consistency", "BOM 与 manifest/实际文件一致",
                                        "unknown", "BOM 不可读/非对象，无法对账"))
    except Exception as exc:  # noqa: BLE001 - 单点异常转 unknown，不伪造
        checks.append(_crit("verify_exception", "校验流程异常", "unknown",
                            "%s: %s" % (type(exc).__name__, str(exc)[:240])))
    report["checks"] = checks
    report["status"] = _status_from_checks(checks)
    report["counts"] = _counts(checks)
    report["gaps"] = [{"id": c["id"], "status": c["status"], "required": c["required"],
                       "detail": c["detail"]} for c in checks if c["status"] != "pass"]
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    report["errors"] = [g["detail"] for g in report["gaps"] if g["required"]]
    return report


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
    bom = report.get("sbom") or {}
    components = report.get("components") or {}
    counts = report.get("counts") or {}
    metadata = bom.get("metadata") if isinstance(bom, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    root = metadata.get("component") if isinstance(metadata.get("component"), dict) else {}
    root_hashes = _hash_map(root)
    lines = [
        "# release artifact SBOM（CycloneDX 1.5，离线）",
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
        "## BOM 摘要",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| bomFormat / specVersion | %s / %s |" % (
            _md_cell(bom.get("bomFormat")), _md_cell(bom.get("specVersion"))),
        "| serialNumber | %s |" % _md_cell(bom.get("serialNumber")),
        "| root component | %s @ %s（sha256 %s）|" % (
            _md_cell(root.get("name")), _md_cell(root.get("version")),
            _md_cell(str(root_hashes.get("sha256") or "")[:16])),
        "| components | files=%s libraries=%s total=%s |" % (
            components.get("files"), components.get("libraries"), components.get("total")),
        "| manifest schema/version/commit | %s / %s / %s |" % (
            _md_cell(manifest.get("schema")), _md_cell(manifest.get("version")),
            _md_cell(str(manifest.get("commit") or "")[:16])),
        "| requirements | files=%s components=%s |" % (
            len((report.get("requirements") or {}).get("files") or []),
            len((report.get("requirements") or {}).get("components") or [])),
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
    bom_components = bom.get("components") if isinstance(bom, dict) else None
    if isinstance(bom_components, list) and bom_components:
        lines += ["", "## 组件清单", "",
                  "| 类型 | 名称 | 版本 | SHA-256 / purl |", "|---|---|---|---|"]
        for component in bom_components:
            if not isinstance(component, dict):
                continue
            hashes = _hash_map(component)
            lines.append("| %s | %s | %s | %s |" % (
                _md_cell(component.get("type")), _md_cell(component.get("name")),
                _md_cell(component.get("version")),
                _md_cell(hashes.get("sha256") or component.get("purl") or "-")))
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
        lines += ["", "## current 发布单元（只读）", "",
                  "- pointer_version=%r · junction_version=%r · drift=%r"
                  % (current.get("pointer_version"), current.get("junction_version"),
                     current.get("drift"))]
        if current.get("drift_detail"):
            lines.append("- detail：%s" % current.get("drift_detail"))
    write_info = report.get("write")
    if write_info:
        lines += ["", "## 写入", "",
                  "- 请求写入：%s · 已写入：%s · path=%s%s" % (
                      write_info.get("requested"), write_info.get("written"),
                      write_info.get("path") or "-",
                      (" · 强制覆盖" if write_info.get("forced") else ""))]
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
        description="release artifact SBOM 生成/校验（CycloneDX 1.5，默认只读；--write 才写 sbom.cdx.json）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="生成并写入 <version>/sbom.cdx.json（已存在需 --force；默认只打印）")
    mode.add_argument("--verify", action="store_true",
                      help="校验已有 sbom.cdx.json（tamper/缺文件/漂移；不写任何文件）")
    parser.add_argument("--no-write", action="store_true", help="显式只读（默认行为；与 --write 互斥）")
    parser.add_argument("--force", action="store_true",
                        help="--write 时允许覆盖已存在 sbom.cdx.json")
    parser.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT),
                        help="release root（默认生产 root %s）" % DEFAULT_RELEASE_ROOT)
    parser.add_argument("--version", default="", help="目标 version 目录名（缺省跟随 current）")
    parser.add_argument("--requirements", action="append", default=[],
                        help="可选 requirements 文件（可多次；离线解析为 library components，purl 不猜）")
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
            report = verify(args.release_root, version=(args.version or None),
                            requirements=args.requirements)
        else:
            report = generate(args.release_root, version=(args.version or None),
                              requirements=args.requirements,
                              write=bool(args.write), force=bool(args.force))
    except Exception as exc:  # noqa: BLE001 - 环境异常也给出结构化 unknown
        report = {
            "schema": SCHEMA, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
            "mode": "verify" if args.verify else "generate",
            "generated_at": _now(), "status": "unknown",
            "release_root": args.release_root, "target": {}, "sbom": None,
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
