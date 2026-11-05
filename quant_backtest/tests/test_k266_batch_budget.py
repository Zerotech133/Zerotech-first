# -*- coding: utf-8 -*-
"""K266 合批预算不变式（2026-11-04 交互延迟优化配套护栏）。

背景：np=1 下请求串行，batch 越长，人类聊天插队等得越久。故 BATCH_K 6→3。
本测试锁住三条不变式，防止后人调参时引入"截断/爆 ctx/预算不足"：

1. 字数上限能落在 K 条内：CHAR_CAP <= K * ITEM_TRUNC
2. ask_ollama 的 trunc 必须覆盖组正文最坏长度：trunc >= CHAR_CAP + ITEM_TRUNC
3. num_predict 必须覆盖 K 条预期输出（单条实测 ~250tok，留 15% 余量）

第 3 条是最容易出事的：K=8 时 1800 曾实测截断翻车（尾部条目被砍 → 回退单判）。
跑法：python -m pytest quant_backtest/tests/test_k266_batch_budget.py -q
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import k266_m3_structure as k266

# 探针实测：单条新闻的 JSON（doc_type/title/companies/events/direction/
# direction_why/key_numbers/tags）约 250 token。见 k266_m3_structure.py 常量段注释。
PER_ITEM_OUT_TOK = 250
SAFETY = 1.15


def _batch_num_predict():
    """从源码取合批调用传入的 num_predict 字面量（取最大，即合批口径）。"""
    src = Path(k266.__file__).read_text(encoding="utf-8")
    vals = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ask_ollama":
            for kw in node.keywords:
                if kw.arg == "num_predict" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, int):
                        vals.append(kw.value.value)
    assert vals, "未找到 ask_ollama 的 num_predict 字面量"
    return max(vals)


def test_batch_k_is_three():
    """交互延迟基线：K=3（单请求 ~23s，人类插队等待减半）。"""
    assert k266.BATCH_K == 3


def test_char_cap_fits_within_k():
    """字数上限必须能在 K 条内收住，否则 buffered 条数会超出预期。"""
    assert 0 < k266.BATCH_CHAR_CAP <= k266.BATCH_K * k266.BATCH_ITEM_TRUNC


def test_trunc_covers_worst_case_group():
    """ask_ollama 的 trunc = CHAR_CAP + ITEM_TRUNC + 64，须覆盖组正文最坏长度。"""
    worst = k266.BATCH_CHAR_CAP + k266.BATCH_ITEM_TRUNC
    budget = k266.BATCH_CHAR_CAP + k266.BATCH_ITEM_TRUNC + 64
    assert budget >= worst
    assert budget - worst == 64


def test_num_predict_covers_expected_output():
    """合批输出预算须覆盖 K 条 × 单条 ~250tok × 余量，否则尾部条目被截断。"""
    need = k266.BATCH_K * PER_ITEM_OUT_TOK * SAFETY
    have = _batch_num_predict()
    assert have >= need, "num_predict=%d 不足以覆盖 %d 条输出（需 >= %.0f）" % (
        have,
        k266.BATCH_K,
        need,
    )


def test_num_predict_not_grossly_oversized():
    """反向护栏：预算不应远超需要（多出的部分会变成病态长生成的等待）。"""
    need = k266.BATCH_K * PER_ITEM_OUT_TOK * SAFETY
    have = _batch_num_predict()
    assert have <= need * 1.6, "num_predict=%d 相对需求 %.0f 过于宽裕" % (have, need)
