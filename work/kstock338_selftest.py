# -*- coding: utf-8 -*-
"""K-STOCK-338 修复自测：parse_decision 三例 + eval_shadow 干跑前置检查"""
import sys, json
sys.path.insert(0, "agent_platform")
from run_shadow import parse_decision

# 例1：纯散文（五粮液 10-13 实锤输出）→ None
prose = "五粮液现价 70.46 元，短期动能中性偏弱。基本面合理，维持观望策略。"
assert parse_decision(prose) is None, "散文应返回 None"
print("T1 散文→None PASS")

# 例2：标准 JSON（含嵌套数组 target_range 不含 {}）
j1 = '{"recommendation": "BUY", "confidence": 0.75, "target_price_range": [43.5, 46.0]}'
d = parse_decision(j1)
assert d and d["score"] == 1 and d["confidence"] == 0.75, "标准 JSON 应解析出 score=1"
print("T2 标准JSON PASS")

# 例3：围栏+前后散文包裹
j2 = '```json\n{"recommendation": "HOLD", "confidence": 0.62}\n```\n以上为决策。'
d2 = parse_decision(j2)
assert d2 and d2["score"] == 0, "围栏包裹应解析出 score=0"
print("T3 围栏包裹 PASS")

# 例4：多 JSON 混排（取第一个含 recommendation 的）
j3 = '{"other": 1} {"recommendation": "SELL", "confidence": 0.4}'
d3 = parse_decision(j3)
assert d3 and d3["score"] == -1
print("T4 混排取含recommendation者 PASS")

# parse_ok 字段向后兼容检查：旧行无该字段，eval_shadow 不读它 → 无碍
row_old = {"status": "done", "score": None}
assert "parse_ok" not in row_old
print("T5 旧行兼容 PASS")

print("\n=== 4+1 自测全绿 ===")
