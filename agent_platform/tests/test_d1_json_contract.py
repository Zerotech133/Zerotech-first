# -*- coding: utf-8 -*-
"""决策节点 JSON 契约 + LLM 生成元信息留存（10-17 隔离预演驱动的收口）。

两个真实缺陷，均由「周一 run 路径隔离预演」暴露：

1. **D1 输出契约自相矛盾**：`_build_user` 给所有节点追加「3 句以内结论」，
   而 D1 的 system 要求「输出 JSON」。模型采信 user 侧时只写散文，
   `parse_decision` 拿不到 JSON → `parse_ok=false`。预演实测 600519
   产出 123 字散文（无任何花括号）即为该路径。
2. **done_reason 被丢弃**：`llm.chat` 旧实现只要 content，于是
   「被 num_predict 截断」与「模型没守格式」两类故障在证据上无法区分
   （排障时只能靠外部探针反推）。截断必须显式降级，不得当正常样本。
"""

import io
import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import interpreter as interp_mod  # noqa: E402
import llm as llm_mod  # noqa: E402

D1_NODE = {
    "agent_id": "D1",
    "node_key": "d1_decision",
    "role_profile": "d1_decision",
    "analysis_type": "final_decision",
}
T1_NODE = {
    "agent_id": "T1",
    "node_key": "t1_market",
    "role_profile": "t1_bull",
    "analysis_type": "market_technical",
}
CTX = {"stock": "600519", "data": {"final_decision": "上游攻防结论摘要"}}


# ---------------------------------------------------------------- 契约 1


def test_decision_node_asks_for_json_only():
    u = interp_mod.Interpreter._build_user(None, D1_NODE, CTX)
    assert "JSON" in u
    # 与 system 冲突的旧尾句必须消失
    assert "3 句以内" not in u


def test_non_decision_node_uses_schema_instruction_or_brief_tail():
    """现行契约（2026-10 菜鸟团队分析移植后）：

    - 有 analysis_type JSON Schema 指令的非决策节点 → 追加「先正文后 ```json```」指令；
    - 无 schema 指令的类型 → 保留旧「3 句以内」简要结论尾句；
    - 两者都不得出现决策节点专用的「严格只输出一个 JSON」。
    """
    u_analyst = interp_mod.Interpreter._build_user(None, T1_NODE, CTX)  # market_technical
    assert "输出格式要求" in u_analyst and "JSON" in u_analyst
    assert "严格只输出一个 JSON" not in u_analyst

    plain_node = dict(T1_NODE, analysis_type="unknown_type_xyz")
    u_plain = interp_mod.Interpreter._build_user(None, plain_node, CTX)
    assert "3 句以内" in u_plain
    assert "严格只输出一个 JSON" not in u_plain


# ---------------------------------------------------------------- 契约 2


class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, payload):
        self.payload = payload

    def open(self, req, timeout=None):
        return _FakeResp(self.payload)


def _patch_ollama(monkeypatch, payload):
    monkeypatch.setattr(
        llm_mod.urllib.request, "build_opener", lambda *a, **k: _FakeOpener(payload)
    )


def test_truncated_generation_is_degraded(monkeypatch):
    """done_reason=length（被 num_predict 截断）→ status=degraded，且元信息留存。"""
    _patch_ollama(
        monkeypatch,
        {"message": {"content": "半截"}, "done_reason": "length", "eval_count": 400},
    )
    text, status = llm_mod.OllamaLLM().chat("s", "u")
    assert text == "半截"
    assert status == "degraded"


def test_normal_generation_records_done_reason(monkeypatch):
    _patch_ollama(
        monkeypatch,
        {"message": {"content": "完整"}, "done_reason": "stop", "eval_count": 85},
    )
    llm = llm_mod.OllamaLLM()
    text, status = llm.chat("s", "u")
    assert (text, status) == ("完整", "normal")
    assert llm.last_meta["done_reason"] == "stop"
    assert llm.last_meta["eval_count"] == 85


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
