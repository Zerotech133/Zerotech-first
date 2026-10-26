# -*- coding: utf-8 -*-
"""G5 基线工具：JSON 契约漂移防护（null 与键缺失区分 / 显式版本校验 / 必填项校验）。

G5 = JSON 契约漂移：null 与键缺失混淆；同 ``type`` 跨版本 schema 不同（如 jev score v2/v3）；
生成器↔消费端靠 dict 键隐式耦合无 schema 校验。

修复方向：以显式 ``version`` 字段替代「按 ``len(levels)`` 分支」这类隐式契约
（jev_oos_question_gen.py:305-322 判例）；消费端做 schema 校验；null 与缺失必须区分对待。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SchemaViolation:
    """单条契约违例。"""

    kind: str  # "missing_key" | "null_value" | "type_mismatch" | "version_mismatch"
    field: str
    detail: str = ""


def field_state(obj: dict, key: str) -> str:
    """返回 ``'missing'`` | ``'null'`` | ``'present'``（区分键缺失与值为 null，G5 核心）。"""
    if key not in obj:
        return "missing"
    if obj[key] is None:
        return "null"
    return "present"


def validate_required(obj: dict, required: list[str]) -> list[SchemaViolation]:
    """校验必填键：缺失或显式为 null 都报 ``missing_key`` / ``null_value``。"""
    violations: list[SchemaViolation] = []
    for k in required:
        st = field_state(obj, k)
        if st == "missing":
            violations.append(SchemaViolation("missing_key", k))
        elif st == "null":
            violations.append(SchemaViolation("null_value", k))
    return violations


def check_version(
    obj: dict,
    expected: str,
    field: str = "version",
) -> SchemaViolation | None:
    """显式版本校验，替代「按 ``len(levels)`` 分支」这类隐式契约（jev score v2/v3 判例）。"""
    st = field_state(obj, field)
    if st == "missing":
        return SchemaViolation("missing_key", field)
    if st == "null":
        return SchemaViolation("null_value", field)
    if obj[field] != expected:
        return SchemaViolation(
            "version_mismatch", field, f"got={obj[field]} expect={expected}"
        )
    return None
