# -*- coding: utf-8 -*-
"""阚泽部意图路由 smoke + 混淆矩阵（K1.1 验收工具，WO-FS-148-2）。

用法
----
    python tools/comm_router_smoke.py              # 离线：只跑正则（不连模型）
    python tools/comm_router_smoke.py --real       # 连真实 Ollama（K1.1 硬验收）
    python tools/comm_router_smoke.py --real --json # 机读输出
    python tools/comm_router_smoke.py --from-route-log  # 从真实上线留痕重跑
                                                        # （有真实客户消息后使用）

语料口径（诚实标注）
------------------
默认语料 portal/fixtures/comm_router_corpus.v1.json 为**场景拟真语料**：
来源=老田本人真实问法（src=laotian）+ 典型客户问法（src=scene）。
当前系统尚无真实客户流量（bot 只收到过指令类消息），故不冒充真实样本；
首批真实客户消息产生后，用 --from-route-log 从 comm_route_log.jsonl
抽真实消息重跑（届时语料口径才升级为 real）。

输出
----
- 每条：text 摘要 / gold / predict / channel / llm_ok
- 汇总：accuracy、逐意图 precision/recall、降级计数
- 判定：accuracy >= 0.80 且 complaint/subscription_qa recall >= 0.80 → PASS
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))

import comm_router as cr  # noqa: E402

CORPUS = ROOT / "portal" / "fixtures" / "comm_router_corpus.v1.json"
PASS_ACCURACY = 0.80
PASS_CRITICAL_RECALL = 0.80          # complaint / subscription_qa 的召回底线
CRITICAL_INTENTS = ("complaint", "subscription_qa")


def load_corpus(path=None):
    p = Path(path) if path else CORPUS
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    for r in rows:
        if r.get("gold") not in cr.INTENTS:
            raise SystemExit("语料 gold 非法：%r" % (r.get("gold"),))
    return rows


def load_from_route_log(log_path=None, limit=200):
    """从真实上线留痕抽消息（无原文，只能拿 intent+md5，故仅作统计口径）。

    真实原文不落盘是隐私红线；要重跑分类必须另有原文来源（如 bot 收到
    消息时的临时缓存）。此函数因此只返回空列表并说明——不造数据。
    """
    p = Path(log_path) if log_path else (ROOT / "portal" / "work"
                                         / "comm_route_log.jsonl")
    if not p.exists():
        return [], "no_route_log: %s" % p
    n = sum(1 for _ in open(p, encoding="utf-8"))
    return [], ("route_log_rows=%d 但原文按隐私红线未落盘，无法重跑分类；"
                "如需真实语料重跑，须在 bot 侧开启原文临时缓存（另单）" % n)


class _NoLLM:
    """离线模式哨兵：任何 chat 调用立即失败 → 走 fallback 降级路径。

    语义：offline 模式必须零模型调用（不隐式连 Ollama，判例：离线工具
    不得偷偷联网）。fallback 计数因此代表"正则覆盖不到的比例"。
    """

    def chat(self, system, user, model=None):
        raise RuntimeError("offline_mode: llm disabled")


def run(rows, real=False, log=False):
    if real:
        llm, err = cr._default_llm()
        if llm is None:
            print("FATAL: 无法获取 LLM 底座：%s" % err)
            raise SystemExit(2)
    else:
        llm = _NoLLM()
    results = []
    for r in rows:
        res = cr.route_message(r["text"], llm=llm, log=log)
        results.append({
            "text": r["text"][:34],
            "gold": r["gold"],
            "pred": res["intent"],
            "channel": res["channel"],
            "llm_ok": res["llm_ok"],
            "conf": res["confidence"],
            "src": r.get("src", "?"),
            "error": res.get("error"),
        })
    return results


def summarize(results):
    n = len(results)
    correct = sum(1 for r in results if r["gold"] == r["pred"])
    acc = correct / n if n else 0.0

    tp = Counter()
    fp = Counter()
    fn = Counter()
    for r in results:
        if r["gold"] == r["pred"]:
            tp[r["gold"]] += 1
        else:
            fn[r["gold"]] += 1
            fp[r["pred"]] += 1

    per = {}
    for it in sorted(set(r["gold"] for r in results)):
        p = tp[it] / (tp[it] + fp[it]) if (tp[it] + fp[it]) else 0.0
        rc = tp[it] / (tp[it] + fn[it]) if (tp[it] + fn[it]) else 0.0
        per[it] = {"n": tp[it] + fn[it], "precision": round(p, 3),
                   "recall": round(rc, 3)}

    channels = Counter(r["channel"] for r in results)
    degraded = sum(1 for r in results if r["channel"] == "fallback")

    critical_ok = all(
        per.get(it, {}).get("recall", 0.0) >= PASS_CRITICAL_RECALL
        for it in CRITICAL_INTENTS if per.get(it)
    )
    return {
        "n": n, "correct": correct, "accuracy": round(acc, 3),
        "per_intent": per, "channels": dict(channels),
        "degraded": degraded,
        "pass": acc >= PASS_ACCURACY and critical_ok,
        "critical_recall_ok": critical_ok,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="连真实 Ollama")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--corpus", default=None, help="自定义语料路径")
    ap.add_argument("--from-route-log", action="store_true",
                    help="从真实上线留痕重跑（需原文缓存，当前会如实报告不可用）")
    args = ap.parse_args()

    if args.from_route_log:
        rows, note = load_from_route_log()
        if not rows:
            print("NOT_AVAILABLE: %s" % note)
            raise SystemExit(3)
    else:
        rows = load_corpus(args.corpus)

    results = run(rows, real=args.real, log=False)
    summ = summarize(results)

    if args.json:
        print(json.dumps({"mode": "real" if args.real else "offline",
                          "corpus": args.corpus or str(CORPUS),
                          "summary": summ, "rows": results},
                         ensure_ascii=False, indent=1))
    else:
        print("== 逐条 ==")
        for r in results:
            mark = "OK " if r["gold"] == r["pred"] else "MISS"
            print("%-4s %-18s → %-18s ch=%-8s conf=%.2f  %s"
                  % (mark, r["gold"], r["pred"], r["channel"], r["conf"],
                     r["text"]))
            if r["error"]:
                print("       err:", r["error"])
        print("\n== 汇总（mode=%s）==" % ("real" if args.real else "offline"))
        print("accuracy = %.3f (%d/%d)" % (summ["accuracy"],
                                           summ["correct"], summ["n"]))
        print("channels =", summ["channels"], "degraded =", summ["degraded"])
        for it, m in summ["per_intent"].items():
            flag = ""
            if it in CRITICAL_INTENTS and m["recall"] < PASS_CRITICAL_RECALL:
                flag = "  ⚠ recall<0.8"
            print("  %-16s n=%d P=%.2f R=%.2f%s"
                  % (it, m["n"], m["precision"], m["recall"], flag))
        print("\nVERDICT:", "PASS" if summ["pass"] else "FAIL")
    raise SystemExit(0 if summ["pass"] else 1)


if __name__ == "__main__":
    main()
