# -*- coding: utf-8 -*-
"""run_shadow 单节点最小链路 smoke（只测模型/绑定，不写账本）。

用途：A/B 前先确认某个节点（T1/T2/N1/F1/D1）能在预算内返回，
避免把“模型慢/排队”误判成 run_shadow 或情报链路故障。

用法：
  python tools/shadow_node_probe.py --node T1 --timeout 60
  python tools/shadow_node_probe.py --node T1 --node D1 --json
  python tools/shadow_node_probe.py --all --timeout 120

纪律：
  · 只调用 LLM，不读写 news.db、不写 shadow ledger、不联网抓行情；
  · 超时/异常 fail-visible，退出码 1；
  · 不是质量评估，只证明节点可响应。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "agent_platform" / "engine"
for p in (str(ROOT), str(ENGINE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from llm import OllamaLLM  # noqa: E402

REGISTRY = ROOT / "agent_platform" / "registry" / "team_invest.json"
DEFAULT_PROMPT = "只回复两个字：OK"
ALIASES = {
    "T1": "t1_market", "T2": "t2_market", "N1": "n1_news",
    "F1": "f1_fundamental", "D1": "d1_decision",
}


def _agents():
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("读取 team_invest.json 失败：%s" % exc)
    return {a.get("node_key"): a for a in data.get("agents") or []}


def main(argv=None):
    parser = argparse.ArgumentParser(description="run_shadow 单节点 smoke")
    parser.add_argument("--node", action="append", default=[],
                        help="节点 key，可重复；默认 T1")
    parser.add_argument("--all", action="store_true", help="全部 T1/T2/N1/F1/D1")
    parser.add_argument("--timeout", type=int, default=60,
                        help="单节点硬超时秒数（默认 60）")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    agents = _agents()
    wanted = [ALIASES.get(str(n), str(n)) for n in (list(agents) if args.all else (args.node or ["T1"]))]
    unknown = [n for n in wanted if n not in agents]
    if unknown:
        print("[ERR] 未知节点：%s（可选 %s）" % (unknown, ",".join(agents)),
              file=sys.stderr)
        return 2

    llm = OllamaLLM(timeout=max(1, int(args.timeout)))
    results = []
    for node_key in wanted:
        node = agents[node_key] or {}
        model = str(node.get("llm_binding") or "")
        system = "[%s]\n%s" % (node.get("role_profile") or node_key,
                               node.get("prompt") or "只输出模型可用性确认。")
        t0 = time.time()
        try:
            text, status = llm.chat(system, args.prompt, model)
            ok = bool(str(text or "").strip())
            row = {"node": node_key, "model": model, "ok": ok,
                   "status": status, "elapsed_s": round(time.time() - t0, 3),
                   "chars": len(str(text or ""))}
        except Exception as exc:  # noqa: BLE001 fail-visible
            row = {"node": node_key, "model": model, "ok": False,
                   "status": "error", "elapsed_s": round(time.time() - t0, 3),
                   "error": "%s: %s" % (type(exc).__name__, exc)}
        results.append(row)
        if not args.json:
            print("[probe] %s model=%s ok=%s status=%s elapsed=%.3fs%s" % (
                row["node"], row["model"], row["ok"], row["status"],
                row["elapsed_s"], (" error=" + row["error"]) if row.get("error") else ""))

    if args.json:
        print(json.dumps({"ok": all(r["ok"] for r in results),
                          "timeout": args.timeout, "results": results},
                         ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
