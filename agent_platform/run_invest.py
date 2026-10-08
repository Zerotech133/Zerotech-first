# -*- coding: utf-8 -*-
"""投研部执行入口（K-STOCK-004）

用法：
    python run_invest.py --stock 600519            # Mock 冒烟（默认，零 GPU 占用）
    python run_invest.py --stock 600519 --live     # 真调 Ollama（qwen3.5:9b）
    python run_invest.py --stock 600519 --live --model qwen3.5:4b
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from checkpoints import Checkpointer  # noqa: E402
from interpreter import Interpreter, Team  # noqa: E402
from llm import MockLLM, OllamaLLM  # noqa: E402
from memory import MemoryStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="投研部 alpha 团队选股执行")
    ap.add_argument("--stock", required=True, help="标的代码，如 600519")
    ap.add_argument("--live", action="store_true", help="真调 Ollama（默认 Mock 冒烟）")
    ap.add_argument("--model", default=None, help="覆盖注册表 llm_binding（如 qwen3.5:4b）")
    ap.add_argument("--registry", default=str(BASE / "registry" / "team_invest.json"))
    ap.add_argument("--data", default=str(BASE / "data"))
    args = ap.parse_args()

    team = Team(args.registry)
    grants = {a["agent_id"]: a["memory_grants"] for a in team.agents}
    llm = OllamaLLM() if args.live else MockLLM()
    if args.model and args.live:
        for a in team.agents:
            a["llm_binding"] = "ollama/" + args.model
    memory = MemoryStore(Path(args.data) / "memory", grants_by_agent=grants, enforce=True)
    cp = Checkpointer(Path(args.data) / "checkpoints.db")

    interp = Interpreter(team, llm, memory, cp)
    result = interp.run(args.stock)

    print("=" * 62)
    print("投研部运行简报  run_id=%s  stock=%s" % (result["run_id"], result["stock"]))
    print("=" * 62)
    for key in sorted(result["statements"]):
        agent = team.by_node[key]["agent_id"]
        print("[%s] %s" % (agent, result["status"].get(key, "?")))
        print("   " + result["statements"][key][:120].replace("\n", " "))
    print("-" * 62)
    print("[最终决策]")
    print(result["decision"])
    print("-" * 62)
    states = cp.get_run_states(result["run_id"])
    print("checkpoint 节点数: %d / %d" % (len(states), len(team.agents)))
    return result


if __name__ == "__main__":
    main()
