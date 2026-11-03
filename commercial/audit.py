# -*- coding: utf-8 -*-
"""审计日志：商业动作追加 JSONL（可导出对账/合规）。

H1-6（2026-11-03）：审计 **哈希链** 防篡改。
- 每条新记录新增 `prev_hash`（上一条的 hash，首条为 ""）与 `hash`
  （sha256(prev_hash + 规范化 JSON)）；
- 历史遗留行（无 hash 字段）允许存在，但必须全部位于第一条带 hash 的记录之前；
- `verify()` 逐行校验链完整性与记录哈希；`python -m commercial.cli audit-verify`
  可输出校验结果；
- `tail/export` 行为保持不变（兼容 billing/growth/followup）。
"""
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import tenants

AUDIT_FILE = os.environ.get("COMMERCIAL_AUDIT") or os.path.join(tenants.BASE, "audit", "audit.jsonl")

GENESIS = ""


def _path(path: Optional[str] = None) -> str:
    p = path or AUDIT_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _canonical(rec: Dict[str, Any]) -> str:
    """规范化 JSON：排序键、紧凑分隔符；用于哈希计算。"""
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_record(rec: Dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256((GENESIS + _canonical(body)).encode("utf-8")).hexdigest()


def _last_hash(p: str) -> str:
    """读取最后一条非空记录的 hash；文件不存在/为空 → GENESIS。

    只读文件尾部 8KB（审计记录通常 <2KB），避免每次 append 全量扫描；
    末行解析失败时回退整体读取以给出明确错误（fail-closed）。
    """
    if not os.path.exists(p):
        return GENESIS
    last = None
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return GENESIS
            f.seek(max(0, size - 8192))
            data = f.read()
        parts = [x for x in data.split(b"\n") if x.strip()]
        if parts:
            last = parts[-1].decode("utf-8", errors="replace").strip()
    except OSError:
        last = None
    if last is None:
        # 回退：整体读取（文件异常/超长记录）
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
    if last is None:
        return GENESIS
    try:
        rec = json.loads(last)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("审计文件末行不可解析，拒绝追加（需先修复）: %s" % exc)
    return str(rec.get("hash") or GENESIS)


def append(action: str, tenant: str = "", actor: str = "system", resource: str = "",
           result: str = "ok", detail: Optional[Dict[str, Any]] = None,
           path: Optional[str] = None) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "ts": time.strftime("%F %T"),
        "tenant": tenant,
        "actor": actor,
        "action": action,
        "resource": resource,
        "result": result,
        "detail": detail or {},
    }
    p = _path(path)
    rec["prev_hash"] = _last_hash(p)
    rec["hash"] = _hash_record(rec)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def tail(n: int = 20, path: Optional[str] = None, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _path(path)
    if not os.path.exists(p):
        return []
    out: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if tenant and rec.get("tenant") != tenant:
                continue
            out.append(rec)
    return out[-n:]


def export(dest: str, path: Optional[str] = None) -> int:
    """导出全部审计为文件（企业版审计导出）。"""
    p = _path(path)
    if not os.path.exists(p):
        open(dest, "w", encoding="utf-8").close()
        return 0
    n = 0
    with open(p, "r", encoding="utf-8") as src, open(dest, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)
            n += 1
    return n


def verify(path: Optional[str] = None) -> Dict[str, Any]:
    """校验审计哈希链。

    返回：{ok, total, hashed, legacy, last_hash, error, path}
    - legacy = 第一条带 hash 记录之前的旧行数；
    - 若旧行出现在已哈希记录之后 → 链视为破坏（ok=False）。
    """
    p = path or AUDIT_FILE
    result: Dict[str, Any] = {"ok": True, "total": 0, "hashed": 0, "legacy": 0,
                              "last_hash": "", "error": None, "path": p}
    if not os.path.exists(p):
        return result
    prev_expected = GENESIS
    seen_hashed = False
    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            result["total"] += 1
            try:
                rec = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                return {**result, "ok": False, "error": "第 %d 行 JSON 解析失败: %s" % (lineno, exc)}
            if "hash" not in rec:
                if seen_hashed:
                    return {**result, "ok": False,
                            "error": "第 %d 行缺少 hash（位于已哈希记录之后）" % lineno}
                result["legacy"] += 1
                continue
            seen_hashed = True
            result["hashed"] += 1
            if rec.get("prev_hash", GENESIS) != prev_expected:
                return {**result, "ok": False,
                        "error": "第 %d 行 prev_hash 不匹配（期望 %s…，实际 %s…）" % (
                            lineno, str(prev_expected)[:12], str(rec.get("prev_hash"))[:12])}
            if _hash_record(rec) != rec.get("hash"):
                return {**result, "ok": False,
                        "error": "第 %d 行 hash 不匹配（记录被篡改）" % lineno}
            prev_expected = rec["hash"]
            result["last_hash"] = rec["hash"]
    return result
