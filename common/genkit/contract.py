# -*- coding: utf-8 -*-
"""G8 基线工具：生成器契约测试脚手架（最低可用断言，供 Phase 2 各生成器复用）。

G8 = 「读多源 → 落盘 JSON/HTML」生成器普遍无 pytest 回归，修复后极易回退。
本模块提供可在各生成器 ``test_*.py`` 中直接调用的断言助手，降低写契约测试的门槛。

用法示例（Phase 2 某生成器 test）::

    from common.genkit import assert_json_contract, assert_nonempty_generated
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert_json_contract(payload, required=["meta", "summary", "generated"])
    assert_nonempty_generated(payload, "generated")   # 回归 G1：必须带时区
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import validate_required


def load_generated_json(path: str | Path) -> dict:
    """读取生成器产物 JSON（测试入口）。文件缺失 / 解析失败直接抛，便于测试快速失败。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"生成产物缺失: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def assert_json_contract(payload: dict, required: list[str]) -> None:
    """断言 JSON 产物满足必填契约（缺键 / 空值即抛 ``AssertionError``）。"""
    violations = validate_required(payload, required)
    if violations:
        raise AssertionError(
            "契约缺失: " + "; ".join(f"{v.field}:{v.kind}" for v in violations)
        )


def assert_nonempty_generated(payload: dict, generated_key: str = "generated") -> None:
    """断言生成产物带有时区感知的 ``generated`` 时间戳（G1 回归）。

    旧代码多写 ``datetime.now().isoformat()``（naive，无偏移），本断言强制产物带
    ``+08:00`` / ``Z`` / ``T`` 之一，防止 G1 回退。
    """
    assert generated_key in payload, f"缺 {generated_key} 时间戳"
    val = payload[generated_key]
    assert isinstance(val, str), f"{generated_key} 应为字符串，实际: {val!r}"
    try:
        parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        raise AssertionError(f"{generated_key} 不是合法 ISO 时间串: {val!r}")
    assert parsed.tzinfo is not None, (
        f"{generated_key} 应为带时区的 ISO 串（缺 tzinfo），实际: {val!r}"
    )
