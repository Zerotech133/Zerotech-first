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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from checkpoints import Checkpointer  # noqa: E402
from idem_hook import DualTrackHook  # noqa: E402  M0 P4 双轨幂等
from interpreter import Interpreter, Team  # noqa: E402
from llm import OllamaLLM  # noqa: E402
from memory import MemoryStore  # noqa: E402
from tools import make_context, quote_snapshot  # noqa: E402

BASKET = ["600519", "000858", "601318", "300750", "600036"]  # 茅台/五粮液/平安/宁德/招行
LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
# K-ORG-129 / T-20261008-003（B8：全篮子 HOLD 使 J2 失效）：score 基数表——
# 枚举档 × confidence 连续落账（见 parse_decision），HOLD=0 中性锚不动；
# WATCH 新档 -0.5：L4 存疑观察＝弱负证据，低于 HOLD、高于 SELL，不再与 HOLD 同分
REC_SCORE = {"STRONG_BUY": 2.0, "BUY": 1.0, "HOLD": 0.0, "WATCH": -0.5,
             "SELL": -1.0, "STRONG_SELL": -2.0}
# failed 即时告警（K-STOCK-013）：复用已验证推送链；P0 语义=不等 17:35 日报
PUSH_SCRIPT = BASE.parent / "news_collector" / "send_feishu.py"
ALERT_STATE = BASE / "data" / "shadow" / "p0_alert_state.json"


def push_failed_alert(failed_rows, today, parse_fail_rows=None):
    """篮子跑完有 failed 时立即推飞书简讯；同日只推一次（节流）。任何异常不中断主流程。
    K-STOCK-338：parse_fail_rows（done 但决策解析失败）并入同条告警——failed 是 P0、
    parse 失败是样本缺失（模型偶发不守格式），后者此前完全静默。"""
    if not failed_rows and not parse_fail_rows:
        return
    try:
        st = json.loads(ALERT_STATE.read_text(encoding="utf-8")) if ALERT_STATE.exists() else {}
        if st.get("date") == today and st.get("pushed"):
            print("[alert] 今日 failed 告警已推过（节流），跳过")
            return
        lines = ["**[P0] 影子运行 %d 笔失败（需处置）**" % len(failed_rows)]
        for r in failed_rows:
            lines.append("- %s：%s" % (r["symbol"], r.get("error", "?")[:100]))
        if parse_fail_rows:
            lines.append("**[P1] %d 笔 done 但 D1 决策解析失败（评估样本缺失·不阻塞）**"
                         % len(parse_fail_rows))
            for r in parse_fail_rows:
                lines.append("- %s：latency %.0fs" % (r["symbol"], r.get("latency_s", 0)))
        tmp = BASE / "data" / "shadow" / "_p0_alert_tmp.md"
        tmp.write_text("\n".join(lines), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(PUSH_SCRIPT), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = (r.stdout or r.stderr or "").strip()
        ok = r.returncode == 0 and "SEND_OK" in out
        print("[alert] failed 即时告警推送：%s" % (out.splitlines()[-1] if out else "无输出"))
        tmp.unlink(missing_ok=True)
        if ok:
            ALERT_STATE.write_text(json.dumps({"date": today, "pushed": True}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[alert] 告警推送异常（不中断主流程）：%s" % e)


def _balanced_objects(text):
    r"""花括号计数法提取 text 中所有配平的 JSON 对象块（K-ORG-137）。

    旧实现 `\{[^{}]*recommendation[^{}]*\}` 禁止对象内出现花括号——D1
    rationale 含嵌套 { } 时失配 → parse_ok=false 样本静默缩水。计数法
    逐块配平，支持嵌套，语义不变（仍只认含 recommendation 的对象）。"""
    objs = []
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:  # 配平才收录；未配平（截断输出）跳过
            objs.append(text[i:j + 1])
        i = j + 1
    return objs


def parse_decision(text):
    """从 D1 输出提取 JSON 决策；失败返回 None（评估时计为解析失败样本）"""
    try:
        for block in _balanced_objects(text):
            j = json.loads(block)
            if "recommendation" not in j:
                continue  # 非决策对象（如解释段里的 JSON 引用）跳过
            # K-ORG-129：score 连续化＝枚举基数 × confidence——
            # 此前纯枚举（HOLD 恒 0、18/25 并列零）致 J2 Spearman 无分辨（B8）；
            # HOLD 基数 0 保持中性（0×c 仍 0），confidence 解析失败按 0 容错不毒化
            base = REC_SCORE.get(str(j.get("recommendation", "")).upper(), 0.0)
            try:
                conf = float(j.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            j["score"] = round(base * conf, 3)
            return j
    except json.JSONDecodeError:
        return None
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


def open_failed_task(failed_rows, today, store=None):
    """failed 自动开单给研发部（K-ORG-013 / P2-3）：P0 直入 doing；同日同因节流。异常不中断主流程。
    store 参数供测试注入临时库（默认真库）。"""
    if not failed_rows:
        return None
    try:
        from tasks import TaskStore  # noqa: E402  engine path 已在顶部注入
        ts = store or TaskStore(BASE / "data" / "org" / "tasks")
        title = "影子运行 failed 处置（%s）" % today
        dup = [t for t in ts.ls(dept="dev") if t["title"] == title]
        if dup:
            print("[task] 当日 failed 开单已存在（%s），跳过" % dup[0]["task_id"])
            return dup[0]["task_id"]
        body = "\n".join("- %s：%s" % (r["symbol"], r.get("error", "?")[:120])
                         for r in failed_rows)
        tid = ts.open("dev", title, body, "P0",
                      opened_by="system/monitor", p0=True)
        print("[task] failed 自动开单：%s（P0 直入 doing，待研发部处置）" % tid)
        return tid
    except Exception as e:  # noqa: BLE001
        print("[task] 自动开单异常（不中断主流程）：%s" % e)
        return None


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
    failed_rows = []
    parse_fail_rows = []  # K-STOCK-338：done 但决策解析失败（评估样本缺失，此前静默）
    # M0 P4 双轨幂等：date+symbol+done JSONL 仍是权威；并行写统一账本
    # （scope=shadow_daily，死信窗 1h——单票耗时分钟级，超 1h 视为进程已死）。
    hook = DualTrackHook("shadow_daily", stale_seconds=3600)
    for symbol in BASKET:
        if already_done(today, symbol):
            print("[skip] %s 今日已入账" % symbol)
            continue
        claim = hook.claim("daily_decision", "%s|%s" % (today, symbol),
                           {"date": today, "symbol": symbol, "strategy": "basket_v2"})
        attempt_id = claim.get("attempt_id", "")
        if claim["outcome"] == "completed":
            print("[skip] %s 幂等账本已完成（result_ref=%s）"
                  % (symbol, claim.get("result_ref", "")))
            continue
        if claim["outcome"] == "in_progress":
            print("[skip] %s 幂等账本执行中（另一进程在跑）" % symbol)
            continue
        if claim["outcome"] == "conflict":
            print("[warn] %s 幂等账本冲突（跳过）：%s"
                  % (symbol, claim.get("conflict_reason", "?")))
            continue
        if claim["outcome"] == "unavailable":
            print("[idem] 账本不可用（fail-open 继续旧链）：%s"
                  % claim.get("error", "?"))
        row = None
        for attempt in (1, 2):  # 脚本内重试一次（Ollama 偶发超时；幂等不受影响，done 才跳过）
            try:
                data_ctx = make_context(symbol)
                t0 = time.time()
                result = interp.run(symbol, data_ctx=data_ctx)
                dec = parse_decision(result["decision"])
                parse_ok = dec is not None  # K-STOCK-338：解析失败显式记录，不再静默吞成 {}
                dec = dec or {}
                price = quote_snapshot(symbol)["price"]
                row = {
                    "date": today, "ts": time.time(), "symbol": symbol,
                    "name": data_ctx.get("name", ""), "status": "done",
                    "run_id": result["run_id"], "price_at_decision": price,
                    "recommendation": dec.get("recommendation"),
                    "confidence": dec.get("confidence"),
                    "score": dec.get("score"), "target_range": dec.get("target_price_range"),
                    "parse_ok": parse_ok,
                    "degraded": any(v != "normal" for v in result["status"].values()),
                    "latency_s": round(time.time() - t0, 1),
                }
                if not parse_ok:
                    print("[warn] %s D1 决策解析失败（输出无 recommendation JSON·已入账 parse_ok=false）"
                          % symbol)
                break
            except Exception as e:  # noqa: BLE001  无人干预要求：失败入账不中断
                row = {"date": today, "ts": time.time(), "symbol": symbol,
                       "status": "failed", "error": str(e)[:200]}
                if attempt == 1:
                    print("[retry] %s 首次失败，3s 后重试：%s" % (symbol, str(e)[:100]))
                    time.sleep(3)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if attempt_id:  # 双轨回写：done→completed；failed→允许下次死信后重试
            hook.complete(attempt_id, ok=(row["status"] == "done"),
                          result_ref=str(row.get("run_id", "")),
                          error=str(row.get("error", ""))[:200])
        print("[%s] %s %s %s" % (row["status"], symbol, row.get("recommendation", ""),
                                 row.get("run_id", row.get("error", ""))))
        summary.append(row["status"])
        if row["status"] == "failed":
            failed_rows.append(row)
        elif row.get("parse_ok") is False:
            parse_fail_rows.append(row)

    print("shadow day %s done: %s" % (today, dict((s, summary.count(s)) for s in set(summary))))
    if parse_fail_rows:
        print("parse_fail: %d（%s）" % (len(parse_fail_rows),
                                        ",".join(r["symbol"] for r in parse_fail_rows)))
    push_failed_alert(failed_rows, today, parse_fail_rows)
    open_failed_task(failed_rows, today)


if __name__ == "__main__":
    main()
