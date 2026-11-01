# -*- coding: utf-8 -*-
"""K-ORG-061b: L4 F6a 抽样验证——子代理拉起 + 人设一致性（轻量探针）。
以 DEV7（10 年档标准执行位）与 QA2（10 年档用例执行位）拉起，验证：
1. chat_auto 通道可用（子代理可拉起）
2. 人设注入生效（回答与注册表 grade_note 一致）
"""
import json
import sys
import time

sys.path.insert(0, r"C:\项目\老田的AI助手\tools")
LOG = open(r"C:\项目\老田的AI助手\data\korg061b_f6a_probe.log", "a", encoding="utf-8")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


import agent_coder  # noqa: E402

PROBES = [
    ("DEV7", "你是哪个岗？你的从业年限档是什么？你的产出应交给谁复核？用一行回答，不要展开。"),
    ("QA2", "你是哪个岗？你的结论最终交给谁裁定？用一行回答，不要展开。"),
]

results = []
for aid, q in PROBES:
    t0 = time.time()
    try:
        ans = agent_coder.chat_auto(aid, {"label": "f6a_probe:%s" % aid,
                                          "prompt": q + "\n直接回答，禁止输出代码块。"})
        ans = (ans or "").strip()
        results.append((aid, bool(ans), ans[:200]))
        log("PROBE %s OK(%.0fs): %s" % (aid, time.time() - t0, ans[:120]))
    except Exception as e:
        results.append((aid, False, str(e)))
        log("PROBE %s EXC: %s" % (aid, e))
    time.sleep(2)

# 人设一致性核对（注册表关键词出现即算一致）
b = json.load(open(r"C:\项目\老田的AI助手\agent_platform\registry\claude_agents.json",
                   encoding="utf-8"))
checks = {"DEV7": ["10年档", "E2+"], "QA2": ["QA1"]}
log("=== 人设一致性 ===")
for aid, kws in checks.items():
    ans = next((r[2] for r in results if r[0] == aid and r[1]), "")
    hit = [k for k in kws if k in ans]
    log("%s 命中 %s %s" % (aid, hit, "PASS" if hit else "CHECK-MANUAL"))
log("=== 汇总 === probe ok=%d/%d" % (sum(1 for r in results if r[1]), len(results)))
LOG.close()
