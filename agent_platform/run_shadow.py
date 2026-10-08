# -*- coding: utf-8 -*-
"""5 日影子运行账本（K-STOCK-004 蓝图 v2 判据的数据采集端）

每日 17:10 计划任务触发（周一~五，避开 03:00 备份 / 15:30 SubPushEod 窗口）：
  股票篮子逐票跑 live 全链路 → 决策快照写 data/shadow/shadow_ledger.jsonl
  lineage：run_id 可回查 data/checkpoints.db 的辩论 trace 与节点 checkpoint。

设计纪律：
  - 幂等：同日同票已入账则跳过（防手动重跑污染 5 日样本）
  - 健壮：单票失败记 failed 行，不中断整篮（无人干预要求）
  - 落文件：stdout/stderr 由计划任务重定向 data/shadow/last_run.log（长跑不依赖管道）
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from checkpoints import Checkpointer  # noqa: E402
from interpreter import Interpreter, Team  # noqa: E402
from llm import OllamaLLM  # noqa: E402
from memory import MemoryStore  # noqa: E402
from tools import make_context, quote_snapshot  # noqa: E402

BASKET = ["600519", "000858", "601318", "300750", "600036"]  # 茅台/五粮液/平安/宁德/招行
LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
REC_SCORE = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2}


def parse_decision(text):
    """从 D1 输出提取 JSON 决策；失败返回 None（评估时计为解析失败样本）"""
    m = re.search(r"\{[^{}]*recommendation[^{}]*\}", text, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
        j["score"] = REC_SCORE.get(str(j.get("recommendation", "")).upper(), 0)
        return j
    except json.JSONDecodeError:
        return None


def already_done(today, symbol):
    if not LEDGER.exists():
        return False
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("date") == today and r.get("symbol") == symbol and r.get("status") == "done":
                return True
        except json.JSONDecodeError:
            continue
    return False


def main():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not sys.stdout.isatty():
        # 计划任务无终端：输出自重定向落文件（长跑纪律，不依赖管道）
        log_f = open(LEDGER.parent / "last_run.log", "a", encoding="utf-8")
        sys.stdout = log_f
        sys.stderr = log_f
        print("===== shadow run start %s =====" % datetime.now())
    today = datetime.now().strftime("%Y-%m-%d")
    team = Team(BASE / "registry" / "team_invest.json")
    grants = {a["agent_id"]: a["memory_grants"] for a in team.agents}
    memory = MemoryStore(BASE / "data" / "memory", grants_by_agent=grants, enforce=True)
    cp = Checkpointer(BASE / "data" / "checkpoints.db")
    llm = OllamaLLM()
    interp = Interpreter(team, llm, memory, cp)

    summary = []
    for symbol in BASKET:
        if already_done(today, symbol):
            print("[skip] %s 今日已入账" % symbol)
            continue
        try:
            data_ctx = make_context(symbol)
            t0 = time.time()
            result = interp.run(symbol, data_ctx=data_ctx)
            dec = parse_decision(result["decision"]) or {}
            price = quote_snapshot(symbol)["price"]
            row = {
                "date": today, "ts": time.time(), "symbol": symbol,
                "name": data_ctx.get("name", ""), "status": "done",
                "run_id": result["run_id"], "price_at_decision": price,
                "recommendation": dec.get("recommendation"),
                "confidence": dec.get("confidence"),
                "score": dec.get("score"), "target_range": dec.get("target_price_range"),
                "degraded": any(v != "normal" for v in result["status"].values()),
                "latency_s": round(time.time() - t0, 1),
            }
        except Exception as e:  # noqa: BLE001  无人干预要求：失败入账不中断
            row = {"date": today, "ts": time.time(), "symbol": symbol,
                   "status": "failed", "error": str(e)[:200]}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("[%s] %s %s %s" % (row["status"], symbol, row.get("recommendation", ""),
                                 row.get("run_id", row.get("error", ""))))
        summary.append(row["status"])

    print("shadow day %s done: %s" % (today, dict((s, summary.count(s)) for s in set(summary))))


if __name__ == "__main__":
    main()
