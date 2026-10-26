# -*- coding: utf-8 -*-
"""genkit —— 生成管线基因修复基线工具包（Phase 1）。

对齐「代码生成器升级·8 基因修复」工单蓝图（G1–G8）：

- timezone : G1 时区单例（Asia/Shanghai / UTC+8）
- paths    : G2 路径锚集中解析（env 可覆盖，消硬编码 / 锚不一致）
- fail_visible : G3/G6/G7 缺源 fail-visible + 截断声明 + null/缺失区分
- schema   : G5 JSON 契约漂移防护（null 与缺失区分 / 显式版本 / 必填校验）
- contract : G8 生成器契约测试脚手架（可复用断言）

本包只新增，不修改任何现有生成器；Phase 2 起各生成器按需 import 本包替换缺陷写法。
"""

from .timezone import (
    CST,
    UTC,
    now_cst,
    utcnow,
    to_cst,
    to_utc,
    format_cst,
    cst_date_str,
    cst_month_str,
)
from .paths import resolve_repo_root, resolve_path, resolve_project_root
from .fail_visible import (
    read_json_visible,
    safe_json_loads,
    truncation_note,
    list_or_empty,
)
from .schema import SchemaViolation, field_state, validate_required, check_version
from .contract import (
    assert_json_contract,
    assert_nonempty_generated,
    load_generated_json,
)

__all__ = [
    "CST",
    "UTC",
    "now_cst",
    "utcnow",
    "to_cst",
    "to_utc",
    "format_cst",
    "cst_date_str",
    "cst_month_str",
    "resolve_repo_root",
    "resolve_path",
    "resolve_project_root",
    "read_json_visible",
    "safe_json_loads",
    "truncation_note",
    "list_or_empty",
    "SchemaViolation",
    "field_state",
    "validate_required",
    "check_version",
    "assert_json_contract",
    "assert_nonempty_generated",
    "load_generated_json",
]
