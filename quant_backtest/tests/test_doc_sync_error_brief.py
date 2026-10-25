# -*- coding: utf-8 -*-
"""DOC_SYNC 错误摘要回归（K-ORG-20261025-DOCSYNC）。

背景：2026-10-25 排查"盘前飞书文档镜像更新失败"时，原日志只打印
`out[:120]` 原文，把两种完全不同的病因截成同一副样子：
  · {"type":"authentication","subtype":"token_missing"}      ← user 未登录
  · {"type":"authorization","subtype":"app_scope_not_applied"} ← 应用缺权限域
导致误判为"内容/参数问题"，白查一轮。本测试锁住 `_cli_error_brief`
的摘要口径：必须保留 type/subtype/code/message 四要素。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QBT = ROOT / "quant_backtest"
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))
os.chdir(QBT)          # premarket 的模块级路径按 cwd 解析

import premarket  # noqa: E402


def test_token_missing_is_labelled_authentication():
    payload = json.dumps({
        "ok": False, "identity": "user",
        "error": {"type": "authentication", "subtype": "token_missing",
                  "message": "need_user_authorization (user: )"},
    })
    brief = premarket._cli_error_brief(payload)
    assert "authentication/token_missing" in brief
    assert "need_user_authorization" in brief


def test_scope_error_keeps_code():
    payload = json.dumps({
        "ok": False, "identity": "bot",
        "error": {"type": "authorization", "subtype": "app_scope_not_applied",
                  "code": 99991672,
                  "message": "access denied: app cli_x has not applied for the required scope(s)"},
    })
    brief = premarket._cli_error_brief(payload)
    assert "authorization/app_scope_not_applied" in brief
    assert "99991672" in brief


def test_non_json_falls_back_to_text():
    brief = premarket._cli_error_brief("Traceback: boom\nline2")
    assert "boom" in brief and "\n" not in brief


def test_afterhours_has_same_helper():
    import afterhours
    assert hasattr(afterhours, "_cli_error_brief")
    assert callable(afterhours._cli_error_brief)
