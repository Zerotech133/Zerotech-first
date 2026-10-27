# -*- coding: utf-8 -*-
"""策略工单后台 worker（W43 环 3）：解析→回测→前视→影子/拒绝。

用法：python strategy_worker.py <order_id> <d0> <d1>
- 独立进程跑回测引擎（8091 不加载行情数据）；
- 全流程写入 work/strategy_worker/<order>.log；
- 闸门：回测失败/收益≤0 → rejected；正收益 → shadowing（30 天）。
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "quant_backtest"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

# double_knife 依赖 agent_platform.engine；仓库名为 namespace 包，会被已安装
# 的 zhice 同名包抢占 → 显式 shim（与 A/B 脚本一致）。
import types  # noqa: E402

if "agent_platform" not in sys.modules:
    _pkg = types.ModuleType("agent_platform")
    _pkg.__path__ = [os.path.join(ROOT, "agent_platform")]
    sys.modules["agent_platform"] = _pkg

os.environ["STRATEGY_BACKTEST_ENABLED"] = "1"
import strategy_ring as sr  # noqa: E402


def main():
    if len(sys.argv) < 4:
        print("usage: strategy_worker.py <order_id> <d0> <d1>")
        return 2
    oid, d0, d1 = sys.argv[1], sys.argv[2], sys.argv[3]
    log_path = os.path.join(HERE, "work", "strategy_worker", oid + ".log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def log(msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("START order=%s window=%s-%s" % (oid, d0, d1))
    row = sr._last_order(oid)
    if not row:
        log("ORDER MISSING")
        return 1
    sr.draft(oid)
    log("drafted")
    bt = sr.backtest(oid, execute=True, d0=d0, d1=d1)
    b = bt.get("backtest") or {}
    log("backtest status=%s why=%s" % (b.get("status"), (b.get("why") or "")[:200]))
    if b.get("status") != "done":
        sr._update(
            oid,
            status="rejected",
            reason="回测失败：%s" % (b.get("why") or b.get("status"))[:100],
        )
        log("REJECTED (backtest not done)")
        return 0
    sm = (b.get("result") or {}).get("summary") or {}
    log("summary=%s" % json.dumps(sm, ensure_ascii=False)[:300])
    la = sr.lookahead_check(oid)
    log("lookahead pass=%s" % ((la.get("lookahead") or {}).get("pass")))
    sh = sr.shadow_start(oid)
    if sh.get("ok"):
        log("SHADOWING until %s" % ((sh.get("shadow") or {}).get("end")))
    else:
        log("NOT SHADOW: %s" % sh.get("error"))
    final = sr._last_order(oid) or {}
    log("DONE status=%s" % final.get("status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
