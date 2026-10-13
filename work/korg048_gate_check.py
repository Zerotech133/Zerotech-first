# -*- coding: utf-8 -*-
"""K-ORG-048 冒烟：ChatResult/薄壳/last_channel/接线 行为断言（子进程执行）。

全程 mock 通道函数，零网络。特征先独立验证（044 T3 判例）。
"""
import ast as _ast
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402


def _patch_gpt6_fail():
    ac.backend = lambda: "gpt6"

    def _boom(*a, **kw):
        raise RuntimeError("HTTP 503 mocked")
    ac.chat_openai = _boom
    ac.chat_anthropic = lambda *a, **kw: "MOCK_ANTHROPIC"


# T1 降级元数据：gpt6 挂→anthropic 成功 → channel=anthropic·requested=gpt6·attempts 记 gpt6
_patch_gpt6_fail()
m = ac.chat_auto_meta("A1", "t")
assert m.channel == "anthropic", "T1 FAIL channel=%r" % m.channel
assert m.requested == "gpt6", "T1 FAIL requested=%r" % m.requested
assert m.attempts and m.attempts[0][0] == "gpt6", "T1 FAIL attempts=%r" % (m.attempts,)
assert m.content == "MOCK_ANTHROPIC" and isinstance(m.latency_s, float), "T1 FAIL body"

# T2 薄壳兼容：chat_auto 仍回 str 且与 meta.content 同值
s = ac.chat_auto("A1", "t")
assert isinstance(s, str) and s == "MOCK_ANTHROPIC", "T2 FAIL: %r" % (s,)

# T3 last_channel 取实际通道（非请求档）；commit_work 引用 last_channel（AST 级）
assert ac.last_channel() == "anthropic", "T3 FAIL last_channel=%r" % ac.last_channel()
src = open("tools/agent_coder.py", encoding="utf-8").read()
tree = _ast.parse(src)
cw = next(n for n in _ast.walk(tree)
          if isinstance(n, _ast.FunctionDef) and n.name == "commit_work")
names = {n.id for n in _ast.walk(cw) if isinstance(n, _ast.Name)}
assert "last_channel" in names, "T3 FAIL: commit_work 未引用 last_channel"
assert any(isinstance(n, _ast.Assign) or isinstance(n, _ast.If)
           for n in _ast.walk(cw)), "T3 FAIL: commit_work 结构异常"

# T4 直连干净通道：channel==requested·attempts==[]·_LAST_META 已被重置为新结果
ac.chat_openai = lambda *a, **kw: "OK_GPT6"
m2 = ac.chat_auto_meta("A1", "t")
assert m2.channel == "gpt6" and m2.requested == "gpt6" and m2.attempts == [], \
    "T4 FAIL: %r" % m2
assert ac.last_channel() == "gpt6", "T4 FAIL _LAST_META 未更新"

# T5 run() 接线：run 函数体内必须调 chat_auto_meta（AST 级）
run_fn = next(n for n in _ast.walk(tree)
              if isinstance(n, _ast.FunctionDef) and n.name == "run")
calls = {n.func.id for n in _ast.walk(run_fn)
         if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
assert "chat_auto_meta" in calls, \
    "T5 FAIL: run() 未接 chat_auto_meta, calls=%r" % (calls,)
assert "chat_auto" not in calls, "T5 FAIL: run() 仍直调 chat_auto"

# T6 py_compile 级联完好：薄壳签名与 meta 签名一致（防签名漂移）
import inspect  # noqa: E402
p1 = inspect.signature(ac.chat_auto).parameters
p2 = inspect.signature(ac.chat_auto_meta).parameters
assert list(p1) == list(p2), "T6 FAIL: 薄壳与 meta 签名不一致 %r vs %r" % (p1, p2)

# T7a ollama 兜底档也写 _LAST_META（QA1-048 观察项①·T4 未覆盖）：gpt6/anthropic/cloud
# 全挂→chat(ollama) 成功 → channel=ollama·attempts 三级失败链·_LAST_META 更新
ac.backend = lambda: "gpt6"

def _boom(*a, **kw):
    raise RuntimeError("mock down")
ac.chat_openai = _boom
ac.chat_anthropic = _boom
ac.chat_cloud = _boom
ac.chat = lambda *a, **kw: "MOCK_OLLAMA"
m3 = ac.chat_auto_meta("A1", "t")
assert m3.channel == "ollama" and m3.content == "MOCK_OLLAMA", \
    "T7a FAIL: %r" % m3
assert len(m3.attempts) == 3 and m3.attempts[0][0] == "gpt6", \
    "T7a FAIL attempts=%r" % (m3.attempts,)
assert ac.last_channel() == "ollama", "T7a FAIL _LAST_META 未写"

# T7b 非默认 timeout 双入口真实转发（T6 签名一致不能证明转发·QA1-048 观察项①）
ac.backend = lambda: "anthropic"
_cap = {}
def _cap_anthropic(agent_id, task, timeout=240):
    _cap["t"] = timeout
    return "MOCK_CAP"
ac.chat_anthropic = _cap_anthropic
ac.chat_auto_meta("A1", "t", timeout_cloud=777)
assert _cap.get("t") == 777, "T7b FAIL: meta 未转发 timeout_cloud, got %r" % _cap.get("t")
ac.chat_auto("A1", "t", timeout_cloud=888)
assert _cap.get("t") == 888, "T7b FAIL: 薄壳未转发 timeout_cloud, got %r" % _cap.get("t")

print("PASS: T1降级channel/attempts/T2薄壳str/T3 last_channel+commit_work接线/"
      "T4直连attempts空/T5 run()接chat_auto_meta/T6签名一致/T7a ollama档LAST_META/"
      "T7b非默认timeout双入口转发")
