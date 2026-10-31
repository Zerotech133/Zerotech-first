# -*- coding: utf-8 -*-
"""L4 established 的 artifact_ref 必需校验（2026-10-31 WO-L4-M4-HARDEN-20261031）。

背景（7d 审计 P0）：`agent_platform/data/xsec/l4_status.json` 的
`status=established` 此前只凭状态字段即可被 `l4_provider` / `evidence_bundle`
当研究级 L4 消费，**未校验其是否有可核验的 Research Artifact** —— 任何
"L4=定性成立"式文本/占位记录都可能被误读为研究级 L4，进而解除 D1 的
WATCH 压制。7d_治理跟踪 v1 §3.2 D1-c/L5 判据要求：`established` 必须带
`artifact_ref`，且 artifact 带冻结数据 sha / as_of / 内容指纹。

本模块口径（最小、只读、无副作用）：
1. `artifact_ref` 必须为非空字符串；外链 URL / `..` 越界 / 非 .json 一律拒绝；
2. 相对 `agent_platform/data/academy/artifacts/`（或仓库相对路径）解析后
   文件必须真实存在；
3. JSON 可解析、`schema == research_artifact/v1`、`artifact_id` 非空；
4. 冻结证据：`data_version` 内含 64 位 sha256，或顶层 `data_sha`/`sha256`
   为 64 位十六进制；
5. 时间锚点：`as_of` 或 `created` 至少一个非空。

**不自定政策**（列入报告「需拍板点」，本模块不判）：
- artifact 内 `l4.status` 是否必须同样为 established 才允许引用；
- L4 状态由纯文本启发式（`evidence_bundle._detect_l4`）推断出的 established
  是否一律禁止（当前仍按文本推断，未加 artifact 门）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
PLATFORM_ROOT = ENGINE_DIR.parent
REPO_ROOT = PLATFORM_ROOT.parent
ARTIFACT_ROOT = PLATFORM_ROOT / "data" / "academy" / "artifacts"
SCHEMA = "research_artifact/v1"
_SHA256 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def _locate(ref, root):
    """把 artifact_ref 解析为候选 Path；返回 (path|None, reason)。"""
    ref = str(ref or "").strip()
    if not ref:
        return None, "missing"
    if ref.lower().startswith(("http://", "https://", "file://")):
        return None, "url_not_local"
    path = Path(ref)
    if ".." in path.parts:
        return None, "path_traversal"
    if path.suffix.lower() != ".json":
        return None, "not_json"
    if path.is_absolute():
        return path, ""
    if path.parts and path.parts[0] == "agent_platform":
        return REPO_ROOT / path, ""  # 仓库相对路径（兼容生产写法）
    return Path(root) / path, ""


def _has_freeze_evidence(doc):
    """冻结证据：sha256 至少一处（data_version 内嵌 / 顶层字段）。"""
    patterns = []
    for key in ("data_version", "data_sha", "sha256", "content_hash"):
        value = doc.get(key)
        if isinstance(value, str):
            patterns.append(value)
    for freeze in (doc.get("freeze_receipt"), doc.get("freeze")):
        if isinstance(freeze, str):
            patterns.append(freeze)
    return any(_SHA256.search(text) for text in patterns)


def _has_time_anchor(doc):
    return any(str(doc.get(key) or "").strip()
               for key in ("as_of", "created", "generated_at"))


def validate_artifact_ref(ref, root=None):
    """校验 L4 established 的 artifact 引用。

    返回 ``(ok, reason, detail)``：ok=True 时 reason=""，detail=解析到的文件
    路径；ok=False 时 reason 为机器可读短码（missing/url_not_local/
    path_traversal/not_json/not_found/invalid_json/schema_mismatch/
    missing_artifact_id/missing_freeze_evidence/missing_time_anchor）。
    """
    base = Path(root or ARTIFACT_ROOT)
    candidate, reason = _locate(ref, base)
    if candidate is None:
        return False, reason, ""
    try:
        candidate = candidate.resolve()
    except OSError:
        return False, "path_unresolvable", str(candidate)
    if not candidate.is_file():
        # 兼容：数据目录不入 git（worktree 检出无 data/）时，回落到显式传入的
        # artifact_root 下同名文件；两处都不存在才判 not_found。
        fallback = base / candidate.name
        if fallback.is_file():
            candidate = fallback.resolve()
    if not candidate.is_file():
        return False, "not_found", str(candidate)
    try:
        doc = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, "invalid_json", "%s: %s" % (candidate, exc)
    if not isinstance(doc, dict):
        return False, "invalid_json", "root is not an object: %s" % candidate
    if str(doc.get("schema") or "").strip() != SCHEMA:
        return False, "schema_mismatch", str(doc.get("schema") or "")
    if not str(doc.get("artifact_id") or "").strip():
        return False, "missing_artifact_id", ""
    if not _has_freeze_evidence(doc):
        return False, "missing_freeze_evidence", ""
    if not _has_time_anchor(doc):
        return False, "missing_time_anchor", ""
    return True, "", str(candidate)
