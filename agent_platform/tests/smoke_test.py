# -*- coding: utf-8 -*-
"""冒烟测试（K-STOCK-004）：MockLLM 全链路 + 断言，临时目录不污染正式 data

覆盖：
  1. 拓扑校验（合法注册表通过；辩论对手不互指时报错）
  2. 全链路：T1⇄T2 辩论 + N1/F1 并行 + D1 汇总
  3. checkpoint 5 节点全落库
  4. 辩论轮次 ≤ max_debate_rounds（2）
  5. D1 输出含 recommendation 字段
  6. 记忆文件生成 + RBAC 越权拒绝（N1 写 dept/invest/decision 必须 PermissionDenied）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / "engine"))

from checkpoints import Checkpointer  # noqa: E402
from interpreter import Interpreter, Team, TopologyError  # noqa: E402
from llm import MockLLM  # noqa: E402
from memory import MemoryStore, PermissionDenied  # noqa: E402

REG = BASE / "registry" / "team_invest.json"
FAILED = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (tag, name, detail))
    if not cond:
        FAILED.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="invest_smoke_"))
    try:
        team = Team(REG)
        check("拓扑校验通过", len(team.agents) == 5)

        grants = {a["agent_id"]: a["memory_grants"] for a in team.agents}
        memory = MemoryStore(tmp / "memory", grants_by_agent=grants, enforce=True)
        cp = Checkpointer(tmp / "checkpoints.db")
        interp = Interpreter(team, MockLLM(), memory, cp)

        result = interp.run("600519")
        check("全链路完成", result["run_id"] and result["decision"])

        states = cp.get_run_states(result["run_id"])
        expect_nodes = {"t1_market", "t2_market", "n1_news", "f1_fundamental", "d1_decision"}
        check("checkpoint 5 节点全落库", expect_nodes <= set(states),
              "got=%s" % sorted(states))

        traces = cp.conn.execute(
            "SELECT COUNT(*) FROM traces WHERE run_id=? AND phase='debate_rebuttal'",
            (result["run_id"],)).fetchone()[0]
        check("辩论轮次≤2（每轮2条trace）", traces <= 4, "rebuttal_traces=%d" % traces)

        d1 = result["statements"].get("d1_decision", "")
        try:
            j = json.loads(d1[d1.index("{"): d1.rindex("}") + 1])
            has_rec = "recommendation" in j
        except Exception:  # noqa: BLE001
            has_rec = False
        check("D1 输出含 recommendation", has_rec)

        mem_files = list((tmp / "memory").glob("*.jsonl"))
        check("记忆文件生成", len(mem_files) >= 3, "files=%d" % len(mem_files))

        try:
            memory.write("N1", "dept/invest/decision", {"x": 1})
            check("RBAC 越权拒绝", False, "N1 竟然写穿了！")
        except PermissionDenied:
            check("RBAC 越权拒绝", True)

        # 拓扑负向用例：辩论对手不互指 → 必须报错
        bad = json.loads(REG.read_text(encoding="utf-8"))
        bad["agents"][1]["debate_partner_key"] = "t1_market_wrong"
        badp = tmp / "bad.json"
        badp.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        try:
            Team(badp)
            check("负向拓扑拦截", False, "不互指竟然通过！")
        except TopologyError:
            check("负向拓扑拦截", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 62)
    if FAILED:
        print("SMOKE FAILED: %d 项 -> %s" % (len(FAILED), FAILED))
        sys.exit(1)
    print("SMOKE ALL PASS")


if __name__ == "__main__":
    main()
