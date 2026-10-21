# -*- coding: utf-8 -*-
"""阚泽部全链端到端冒烟（K1-K6，WO-FS-148-2~10）。

一条消息走完七步：意图 → 路由 → 调度 → 派单 → 画像 → 信号 → 咨询位。

用法
----
    python tools/comm_e2e_smoke.py            # 桩模式（零模型、零真投递到生产）
    python tools/comm_e2e_smoke.py --real     # 真实模型（意图/摘要/军师真调用）

安全
----
全程用临时目录（COMM_MEMORY_DIR / COMM_ROUTE_LOG / COMM_INTEREST_DB /
bridge_inbox 全指向 tmp），不碰生产 work/ 与真实 bridge_inbox。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "agent_platform"))


def main():
    real = "--real" in sys.argv
    tmp = tempfile.mkdtemp(prefix="comm_e2e_")
    os.environ.update({
        "COMM_INTENT_ENABLED": "1",
        "COMM_DISPATCH_ENABLED": "1",
        "COMM_ZHUGE_ENABLED": "1",
        "COMM_MEMORY_DIR": os.path.join(tmp, "mem"),
        "COMM_ROUTE_LOG": os.path.join(tmp, "route.jsonl"),
        "COMM_INTEREST_DB": os.path.join(tmp, "interest.db"),
    })
    from gateway import inbox_gateway as ig
    ig.set_store(Path(tmp) / "bridge_inbox")

    import comm_dispatch as cd
    import comm_interest as ci
    import comm_intake as ck
    import comm_summarize as cs

    class _Stub:
        def __init__(self, m):
            self.m = m

        def chat(self, system, user, model=None):
            return self.m, "normal"

    stub_classify = _Stub('{"intent": "smalltalk", "confidence": 0.9}')
    stub_zhuge = _Stub("结论先行：先看流动性，再谈仓位。")
    stub_sum = _Stub("今日成交 1.82 万亿，涨停 58 家。")

    def llm_for(text):
        return None if real else stub_classify

    cases = [
        ("个股咨询", "帮我看看 605100 现在能进吗", "task_request"),
        ("盯票设置", "帮我盯一下 300750，跌破 180 就提醒我", "task_request"),
        ("订阅查询", "我的订阅还剩几天", "self"),
        ("投诉", "昨天的问题到现在没人回，太失望了", "feedback"),
        ("大盘判断（军师）", "为什么今天大盘跌，我该怎么办", "zhuge"),
        ("闲聊", "早上好", "clarify"),
    ]

    print("== 全链冒烟（%s）==" % ("真实模型" if real else "桩模式"))
    print("临时目录:", tmp)
    ok_n = 0
    for i, (name, text, expect) in enumerate(cases, 1):
        mid = "E2E-%02d" % i
        r = ck.smart_intake(text, "ou_e2e", msg_id=mid, llm=llm_for(text))
        if expect == "zhuge":
            if not real:
                ck.zhuge.consult = lambda q, open_id="", **k: {
                    "text": stub_zhuge.chat("", "")[0], "mode": "local_soul",
                    "llm_ok": True, "error": "local_fallback"}
            r = ck.smart_intake(text, "ou_e2e", msg_id=mid, llm=llm_for(text))
        verdict = "OK"
        if r is None:
            verdict = "OK" if expect == "clarify" else "!! 未受理"
        elif expect == "task_request":
            if not (r.get("dispatch") or {}).get("ok"):
                verdict = "!! 派单失败"
            elif not str(r.get("dispatch", {}).get("dispatch_id", "")).startswith("lg-"):
                verdict = "!! 单号异常"
        elif expect == "feedback":
            if (r.get("dispatch") or {}).get("kind") != "feedback":
                verdict = "!! kind 非 feedback"
        elif expect == "zhuge":
            if r.get("intent") != "consult":
                verdict = "!! 未走咨询位"
        elif expect == "clarify":
            if r is not None:
                verdict = "!! 应回落澄清卡"
        if verdict == "OK":
            ok_n += 1
        print("[%d] %-14s %-6s intent=%-14s %s"
              % (i, name, verdict,
                 (r or {}).get("intent", "-"),
                 ("单号 " + (r or {}).get("dispatch", {}).get("dispatch_id", "-"))
                 if r and r.get("dispatch") else ""))

    # 画像与信号
    for _ in range(3):
        ci.record("ou_e2e", intent="stock_consult", code="605100", blocked=True)
    st = ci.stats_7d("ou_e2e")
    sigs = ci.signals(subscribed={"ou_e2e": False})
    print("\n画像: 咨询 %d 次 / 被闸 %d 次 / 高频 %s"
          % (st["consult_count"], st["blocked_count"],
             list(st["top_codes"])[:2]))
    print("信号: %s" % ([(s["level"], s["stars"]) for s in sigs] or "空态"))
    print("引导: %s" % (ci.guide_text("ou_e2e") or "空态"))

    # 摘要
    sm = cs.humanize("收盘速览", ["成交额 1.82 万亿", "涨停 58 家"],
                     llm=None if real else stub_sum, use_cache=False)
    print("摘要: llm_ok=%s | %s" % (sm["llm_ok"], sm["text"][:80]))

    # 留痕与幂等
    log = Path(os.environ["COMM_ROUTE_LOG"])
    n_log = sum(1 for _ in open(log, encoding="utf-8")) if log.exists() else 0
    dup = ck.smart_intake("帮我看看 605100 现在能进吗", "ou_e2e",
                          msg_id="E2E-01", llm=llm_for("x"))
    print("\n留痕行数: %d（不存原文: %s）" % (
        n_log, "text" not in json.loads(log.read_text(encoding="utf-8").splitlines()[0])))
    print("重推幂等: %s" % (dup["dispatch"]["replayed"] if dup.get("dispatch") else "-"))
    print("\nVERDICT: %d/%d 用例符合预期" % (ok_n, len(cases)))
    return 0 if ok_n == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
