# -*- coding: utf-8 -*-
"""K-ORG-036 F5 冒烟：编码轨迹落盘 + 事件新类（不调模型）。"""
import json
import os
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402
import events  # noqa: E402

# 0) 幂等：清掉上一轮自身残留（trace 为追加写）
_day = os.path.join(ac.TRACE_DIR, __import__("time").strftime("%Y-%m-%d"))
_old = os.path.join(_day, "K-TEST-F5.jsonl")
if os.path.exists(_old):
    os.remove(_old)

# 1) 轨迹按单归档：写 3 条 → 读回可解析
ac.trace_log("K-TEST-F5", "generate", "probe by OPS1 via cloud")
ac.trace_log("K-TEST-F5", "edit", "mode=sr")
ac.trace_log("K-TEST-F5", "commit", "abc1234 probe")
day_dir = os.path.join(ac.TRACE_DIR, __import__("time").strftime("%Y-%m-%d"))
fn = os.path.join(day_dir, "K-TEST-F5.jsonl")
assert os.path.exists(fn), fn
lines = [json.loads(l) for l in open(fn, encoding="utf-8")]
assert [r["kind"] for r in lines] == ["generate", "edit", "commit"], lines
print("1 trace-jsonl-roundtrip PASS:", fn)

# 2) 事件新类：code_action/code_observation 可发，未知类仍拒
r1 = events.emit("code_action", task="K-TEST-F5", agent="OPS1",
                 detail="generate model_route_v2 via claude_glm")
r2 = events.emit("code_observation", task="K-TEST-F5", agent="OPS1",
                 detail="lint PASS mode=sr")
assert r1.get("ok") and r2.get("ok"), (r1, r2)
r3 = events.emit("not_a_kind", task="x")
assert r3.get("ok") is False, r3  # emit 静默设计：失败返回 ok=False 不抛
print("2 events-new-kinds PASS")

# 3) 匿名 k_id 不落盘（防轨迹垃圾）
n_before = len(os.listdir(day_dir))
ac.trace_log(None, "generate", "anon")
assert len(os.listdir(day_dir)) == n_before
print("3 anon-no-trace PASS")

print("F5 TRACE SMOKE ALL PASS")
