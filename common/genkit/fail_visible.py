# -*- coding: utf-8 -*-
"""G3/G6/G7 基线工具：fail-visible 缺源处理 + 截断声明 + null/缺失安全取用。

G3 = 生成器侧缺 fail-visible（缺源即崩 / 静默吞且仍报原池大小）；
G6 = 静默吞错（``except: pass`` / 关键副作用 fail-open）；
G7 = 低频淹没 / 截断未声明 / 阈值未量化。

设计原则（沿 portal fail-visible 判例）：缺源返回 ``exists=false`` 合法观测态，
**绝不**在 import 期抛 ``FileNotFoundError``，**绝不**编造；所有截断必须显式声明。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_visible(path: str | Path) -> tuple[Any, bool, str]:
    """读取 JSON 文件并返回 ``(data, exists, reason)``。

    - 文件缺失 → ``(None, False, "source_missing:<path>")``
    - 解析失败 → ``(None, False, "parse_error:<msg>")``
    - 成功     → ``(data, True, "")``

    永不抛异常、永不编造。生成器应以此替代 ``json.load(open(path))``（G3 修复：
    根治 chanlun_daily_ledger.py:37 / build_bt2_prototype.py:9-10 的 import 期崩溃）。
    """
    p = Path(path)
    if not p.exists():
        return None, False, f"source_missing:{p}"
    try:
        return json.loads(p.read_text(encoding="utf-8")), True, ""
    except Exception as exc:  # noqa: BLE001 - 此处需捕获一切以降级为可见观测态
        return None, False, f"parse_error:{exc}"


def safe_json_loads(text: str) -> tuple[Any, bool, str]:
    """同 :func:`read_json_visible`，但入参为字符串。"""
    try:
        return json.loads(text), True, ""
    except Exception as exc:  # noqa: BLE001
        return None, False, f"parse_error:{exc}"


def truncation_note(kept: int, total: int, what: str = "") -> str:
    """生成截断声明串，强制调用方在输出中暴露「已截断 N/共 M」。

    用法：``out += f"...{truncation_note(len(kept), total, '走查报告')}"``
    替代 morning_brief.py:90/119/160、sec2_review FILE_MAX 等**静默**截断（G7 修复）。
    ``kept >= total`` 时返回空串（无需声明）。
    """
    if kept >= total:
        return ""
    tag = f" {what}" if what else ""
    return f"（已截断 {kept}/{total}{tag}）"


def list_or_empty(obj: dict, key: str) -> list:
    """安全取列表字段：键缺失或值为 null 都降级为空列表（不抛、不编造）。

    若需区分「缺失」与「null」，请配合 :func:`common.genkit.schema.field_state`。
    """
    val = obj.get(key)
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
