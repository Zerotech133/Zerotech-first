# -*- coding: utf-8 -*-
"""出网脱敏工具（P0-2）：A0 desc / 诸葛 diff / 测试输出在发往云端模型前分类与脱敏。

原则：
- L3（密钥/私钥/token/cookie/password）→ 恒定脱敏；diff 场景 block=true 直接拒绝复核；
- L2（手机号/身份证/卡号线索）→ 脱敏；
- L0/L1 原样通过；
- 审计只记录命中类型/数量，不记录原文。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

AUDIT_PATH = Path(os.environ.get(
    "OUTBOUND_AUDIT_PATH",
    r"C:\项目\_vault\legion_ide\outbound_audit.jsonl"))

_PATTERNS = [
    # (kind, severity, regex, mask)
    ("private_key", "L3", re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        re.S), "[REDACTED:private_key]"),
    ("aws_key", "L3", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    ("github_token", "L3", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github_token]"),
    ("slack_token", "L3", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack_token]"),
    ("bearer", "L3", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{10,}=*"), "[REDACTED:bearer]"),
    ("cookie", "L3", re.compile(r"(?i)\b(?:set-)?cookie\s*:\s*[^\r\n]+"), "[REDACTED:cookie]"),
    ("credential_assign", "L3", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret(?:[_-]?key)?|access[_-]?key|auth[_-]?token|token|password|passwd|pwd|authorization)\b\s*[:=：]\s*[\"']?([A-Za-z0-9_\-./+=]{8,})[\"']?"),
     None),
    ("phone_cn", "L2", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[REDACTED:phone]"),
    ("id_card_cn", "L2", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])"), "[REDACTED:id_card]"),
]

_LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def scan(text: str) -> dict:
    text = str(text or "")
    hits = []
    out = text
    level = "L0"
    for kind, severity, pat, mask in _PATTERNS:
        if kind == "credential_assign":
            cands = list(pat.finditer(out))
            if not cands:
                continue
            # 只脱敏值，保留键名
            def _sub(m):
                return m.group(0)[: m.start(1) - m.start(0)] + "[REDACTED:credential]"
            new = pat.sub(_sub, out)
            if new != out:
                hits.append({"kind": kind, "severity": severity, "count": len(cands)})
                out = new
        else:
            matches = list(pat.finditer(out))
            if matches:
                hits.append({"kind": kind, "severity": severity, "count": len(matches)})
                out = pat.sub(mask, out)
        if hits and _LEVEL_RANK[severity] > _LEVEL_RANK[level]:
            level = severity
    return {"text": out, "level": level, "hits": hits,
            "blocked": any(h["severity"] == "L3" for h in hits)}


def redact(text: str, block: bool = False) -> dict:
    """返回 {text, level, hits, blocked}；block=True 时 L3 命中置 blocked=True（调用方拒绝发送）。"""
    res = scan(text)
    res["blocked"] = bool(block and res["blocked"])
    return res


def log_outbound(kind: str, identifier: str, destination: str, result: dict) -> None:
    """审计：只写命中类型/级别/数量，不写原文。失败不阻断业务。"""
    try:
        audit_path = Path(os.environ.get("OUTBOUND_AUDIT_PATH", str(AUDIT_PATH)))
        row = {
            "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "kind": kind,
            "id": str(identifier or "")[:120],
            "destination": str(destination or "")[:120],
            "level": result.get("level"),
            "hits": [{"kind": h.get("kind"), "severity": h.get("severity"),
                      "count": h.get("count")} for h in result.get("hits") or []],
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:  # 便于手工验证
    import sys
    text = " ".join(sys.argv[1:])
    if not text:
        text = sys.stdin.read()
    print(json.dumps(redact(text), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
