# -*- coding: utf-8 -*-
"""采集即服务平台 · PII 合规（检测与脱敏）

开源情报采集的对象是公开社媒/论坛帖，但公开帖里常夹带作者手机号、邮箱、身份证号。
本模块在"入湖即脱敏"的治理点上提供：
  - scan_pii(text)        ：检测三类 PII，返回 (类型, 命中) 列表（命中仅用于计数，不打印明文）
  - redact_text(text)     ：脱敏（保留末位，其余打码），返回 (脱敏文本, 命中数)
  - redact_observation()  ：递归脱敏 payload 中的字符串字段，返回新 Observation（原快照不动）
  - scan_store_pii()      ：统计各源 PII 命中数，供面板合规栏呈现

安全优先：本模块任何函数都不打印 PII 明文；脱敏后明文不落库。
仅覆盖常见三类；非穷尽，真实接入时按合规要求扩展模式库。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Optional

from .contracts import Observation


# ---- 检测模式（常见三类；按合规要求扩展）----
_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}


def scan_pii(text: str) -> list[tuple[str, str]]:
    """返回 (类型, 命中片段) 列表。命中片段仅用于计数/脱敏，调用方不得打印。"""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    for ptype, pat in _PATTERNS.items():
        for m in pat.finditer(text):
            out.append((ptype, m.group(0)))
    return out


def redact_text(text: str) -> tuple[str, int]:
    """对文本做 PII 脱敏：每类命中替换为打码串（保留末位便于核对非明文），返回 (脱敏文本, 命中数)。"""
    if not text:
        return text, 0
    total = 0
    result = text
    for ptype, pat in _PATTERNS.items():
        def _mask(m: re.Match) -> str:
            s = m.group(0)
            if len(s) <= 1:
                return "*"
            return s[0] + "*" * (len(s) - 2) + s[-1]

        result, n = pat.subn(_mask, result)
        total += n
    return result, total


def _redact_value(v: Any) -> tuple[Any, int]:
    """递归脱敏：字符串脱敏；容器逐元素；其余原样。返回 (新值, 命中数)。"""
    count = 0
    if isinstance(v, str):
        new, n = redact_text(v)
        return new, n
    if isinstance(v, dict):
        new = {}
        for k, val in v.items():
            nv, n = _redact_value(val)
            new[k] = nv
            count += n
        return new, count
    if isinstance(v, list):
        new = []
        for item in v:
            ni, n = _redact_value(item)
            new.append(ni)
            count += n
        return new, count
    return v, 0


def redact_observation(obs: Observation) -> Observation:
    """返回一份 payload 已脱敏的新 Observation；原 obs 不被修改（不可变契约）。"""
    new_payload, _ = _redact_value(deepcopy(obs.payload or {}))
    return Observation(
        source_id=obs.source_id, snapshot_id=obs.snapshot_id,
        entity_type=obs.entity_type, entity_id=obs.entity_id,
        clock=obs.clock, status=obs.status, completeness=obs.completeness,
        payload=new_payload, raw_sha256=obs.raw_sha256,
        source_payload_sha256=obs.source_payload_sha256,
        allowed_use_cases=obs.allowed_use_cases, source_field=obs.source_field,
    )


def scan_store_pii(observations: list[Observation]) -> dict[str, int]:
    """统计各源 payload 中的 PII 命中数（用于面板合规栏）。仅返回计数，不含明文。"""
    out: dict[str, int] = {}
    for o in observations:
        count = 0
        _, n = _redact_value(deepcopy(o.payload or {}))
        count += n
        if count:
            out[o.source_id] = out.get(o.source_id, 0) + count
    return out
