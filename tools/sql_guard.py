# -*- coding: utf-8 -*-
"""SQL 标识符 allowlist（防 B608：只允许显式白名单的 schema/表/列名）。

值参数仍必须走 ?/%s 占位符；本模块只处理无法参数化的标识符。
"""
from __future__ import annotations

import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(value: str, allowed: set[str] | frozenset[str] | None = None,
                    what: str = "identifier") -> str:
    """校验 SQL 标识符；非法字符或不在 allowlist 时抛 ValueError。"""
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise ValueError("非法 SQL %s: %r" % (what, value))
    if allowed is not None and value not in allowed:
        raise ValueError("SQL %s 不在 allowlist: %r" % (what, value))
    return value


def safe_qualified(schema: str, table: str,
                   allowed_schemas: set[str] | frozenset[str] | None = None,
                   allowed_tables: set[str] | frozenset[str] | None = None) -> str:
    """返回 `schema.table`；两段均校验，禁止点号/引号注入。"""
    return "%s.%s" % (
        safe_identifier(schema, allowed_schemas, "schema"),
        safe_identifier(table, allowed_tables, "table"),
    )
