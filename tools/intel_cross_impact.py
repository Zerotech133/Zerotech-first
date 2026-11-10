# -*- coding: utf-8 -*-
"""重大判断的交叉影响矩阵（P2）——脚本级情景推演，不做平台模块。

定位（2026-11-11 老田批准 P2）：
  只对“多事件强耦合、数据少、后果重”的重大判断启用；日常队列不用。
  方法按交叉影响分析法四步走：
    (1) 确定影响关系（谁影响谁、方向）
    (2) 评定影响程度（0–1 强度；来源须标注）
    (3) 确定影响值（本脚本用一阶迭代公式）
    (4) 蒙特卡罗模拟（输出情景分布，不输出单点“精确概率”）

公式（写死并留痕，避免黑箱）：
  p_i <- clip( p_i + Σ_j dir_ji * strength_ji * p_j * (1 - p_i), 0.01, 0.99 )
  迭代 5 轮至收敛（|Δ|<1e-6）或上限；只用于把“连锁反应”摆上桌面。

输入 JSON 示例：
  {"title":"某重大判断","horizon":"6-12 个月",
   "events":[{"id":"A","label":"事件A","base_prob":0.4},
             {"id":"B","label":"事件B","base_prob":0.3}],
   "influences":[{"from":"A","to":"B","direction":1,"strength":0.3,
                  "source":"专家估计 2026-11-11"}]}

产出（--out-dir，默认 reports/intel_cross_impact/<ts>/）：
  summary.md      结论、调整后概率区间、最敏感影响、局限
  matrix.csv      事件×影响明细（方向/强度/贡献）
  scenarios.csv   情景组合分布（Top 10）

用法：
  python -X utf8 tools/intel_cross_impact.py --template x.json
  python -X utf8 tools/intel_cross_impact.py --in x.json --samples 5000 --seed 42
退出码：0=成功；2=输入不合法。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "reports", "intel_cross_impact")
MAX_ITER = 5


def _die(msg: str) -> "NoReturn":  # noqa: F821
    sys.stderr.write("[intel_cross_impact] %s\n" % msg)
    raise SystemExit(2)


def _clip(p: float) -> float:
    return max(0.01, min(0.99, p))


def load_case(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as f:
            case = json.load(f)
    except Exception as exc:  # noqa: BLE001
        _die("输入读取失败：%s（%s）" % (path, exc))
    events = case.get("events") or []
    if not events:
        _die("events 为空")
    ids = set()
    for e in events:
        eid = str(e.get("id") or "").strip()
        if not eid or eid in ids:
            _die("event id 为空或重复：%r" % (e,))
        ids.add(eid)
        try:
            p = float(e.get("base_prob"))
        except (TypeError, ValueError):
            _die("event %s base_prob 非数字" % eid)
        if not (0.0 <= p <= 1.0):
            _die("event %s base_prob 越界：%s" % (eid, p))
    for inf in case.get("influences") or []:
        src, dst = str(inf.get("from")), str(inf.get("to"))
        if src not in ids or dst not in ids:
            _die("influence 端点不存在：%s→%s" % (src, dst))
        try:
            s = float(inf.get("strength"))
        except (TypeError, ValueError):
            _die("influence %s→%s strength 非数字" % (src, dst))
        if not (-1.0 <= s <= 1.0):
            _die("influence %s→%s strength 越界" % (src, dst))
        if int(inf.get("direction", 1)) not in (-1, 1):
            _die("influence %s→%s direction 必须 ±1" % (src, dst))
    return case


def adjust(case: dict) -> dict:
    """一阶迭代调整概率，返回 {id: 调整后概率}。"""
    p = {e["id"]: float(e["base_prob"]) for e in case["events"]}
    infl = case.get("influences") or []
    for _ in range(MAX_ITER):
        nxt = {}
        for e in case["events"]:
            eid = e["id"]
            delta = 0.0
            for inf in infl:
                if str(inf.get("to")) != eid:
                    continue
                src = str(inf.get("from"))
                delta += (int(inf.get("direction", 1)) *
                          float(inf.get("strength")) *
                          p[src] * (1.0 - p[eid]))
            nxt[eid] = _clip(p[eid] + delta)
        diff = max(abs(nxt[k] - p[k]) for k in p)
        p = nxt
        if diff < 1e-6:
            break
    return p


def contributions(case: dict, adjusted: dict) -> list:
    rows = []
    base = {e["id"]: float(e["base_prob"]) for e in case["events"]}
    for inf in case.get("influences") or []:
        src, dst = str(inf.get("from")), str(inf.get("to"))
        s = float(inf.get("strength"))
        d = int(inf.get("direction", 1))
        rows.append({
            "from": src, "to": dst, "direction": d, "strength": s,
            "base_from": base[src], "base_to": base[dst],
            "adjusted_to": adjusted[dst],
            "delta_to": round(adjusted[dst] - base[dst], 6),
        })
    return rows


def monte_carlo(case: dict, adjusted: dict, samples: int, seed: int) -> dict:
    rng = random.Random(seed)
    ids = [e["id"] for e in case["events"]]
    counts = {}
    hits = {eid: 0 for eid in ids}
    for _ in range(samples):
        combo = tuple(eid for eid in ids if rng.random() < adjusted[eid])
        hits_this = len(combo)
        counts[combo] = counts.get(combo, 0) + 1
        for eid in combo:
            hits[eid] += 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "samples": samples,
        "seed": seed,
        "marginals": {eid: round(hits[eid] / samples, 4) for eid in ids},
        "top_scenarios": [{"events": list(k), "count": v}
                          for k, v in top],
        "scenario_count": len(counts),
    }


def sensitivity(case: dict) -> list:
    """单条影响强度 ±50% 后，目标事件概率的最大变化。"""
    base = adjust(case)
    out = []
    for i, inf in enumerate(case.get("influences") or []):
        worst = 0.0
        for factor in (0.5, 1.5):
            c2 = json.loads(json.dumps(case))
            c2["influences"][i]["strength"] = \
                float(inf.get("strength")) * factor
            p2 = adjust(c2)
            dst = str(inf.get("to"))
            worst = max(worst, abs(p2[dst] - base[dst]))
        out.append({"from": str(inf.get("from")), "to": str(inf.get("to")),
                    "strength": float(inf.get("strength")),
                    "max_delta": round(worst, 6)})
    out.sort(key=lambda r: -r["max_delta"])
    return out[:5]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交叉影响矩阵（P2，脚本级）")
    ap.add_argument("--in", dest="inp", help="案例 JSON")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--samples", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--template", help="写一份输入模板后退出")
    a = ap.parse_args(argv)

    if a.template:
        tpl = {
            "title": "示例：某重大判断",
            "horizon": "6-12 个月",
            "events": [
                {"id": "A", "label": "事件A（政策落地）", "base_prob": 0.4},
                {"id": "B", "label": "事件B（需求爆发）", "base_prob": 0.3},
            ],
            "influences": [
                {"from": "A", "to": "B", "direction": 1,
                 "strength": 0.3, "source": "专家估计（须写明来源）"},
            ],
        }
        with io.open(a.template, "w", encoding="utf-8") as f:
            json.dump(tpl, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("template written: %s" % a.template)
        return 0

    if not a.inp:
        _die("需要 --in 或 --template")
    case = load_case(a.inp)
    if a.samples <= 0:
        _die("--samples 必须为正")
    adjusted = adjust(case)
    contrib = contributions(case, adjusted)
    mc = monte_carlo(case, adjusted, a.samples, a.seed)
    sens = sensitivity(case)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(a.out_dir, ts)
    os.makedirs(out_dir, exist_ok=True)

    with io.open(os.path.join(out_dir, "matrix.csv"), "w",
                 encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(contrib[0].keys())
                           if contrib else ["from", "to"])
        w.writeheader()
        for r in contrib:
            w.writerow(r)
    with io.open(os.path.join(out_dir, "scenarios.csv"), "w",
                 encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "count", "share"])
        for s in mc["top_scenarios"]:
            w.writerow(["+".join(s["events"]) or "(none)", s["count"],
                        round(s["count"] / mc["samples"], 4)])

    lines = [
        "# 交叉影响情景推演 · %s" % (case.get("title") or "未命名"),
        "",
        "- 时间窗：%s" % (case.get("horizon") or "未填"),
        "- 生成：%s，蒙特卡罗 %d 次（seed=%d）"
        % (dt.datetime.now().isoformat(timespec="seconds"),
           mc["samples"], mc["seed"]),
        "- 定性：情景推演，不给单点“精确概率”；主观强度须标注来源。",
        "",
        "## 调整后概率（基准 → 调整）",
        "| 事件 | 基准 | 调整后 | 蒙特卡罗频率 |",
        "|---|---|---|---|",
    ]
    for e in case["events"]:
        eid = e["id"]
        lines.append("| %s %s | %.3f | %.3f | %.3f |"
                     % (eid, e.get("label") or "", float(e["base_prob"]),
                        adjusted[eid], mc["marginals"][eid]))
    lines += ["", "## 最敏感影响（强度 ±50% 后目标概率最大变化）"]
    if sens:
        for s in sens:
            lines.append("- %s→%s（strength %.2f）：Δp≤%.3f"
                         % (s["from"], s["to"], s["strength"],
                            s["max_delta"]))
    else:
        lines.append("- 无影响关系")
    lines += ["", "## 情景分布（Top）"]
    for s in mc["top_scenarios"][:5]:
        lines.append("- %s：%d 次（%.1f%%）"
                     % ("+".join(s["events"]) or "(无事件)",
                        s["count"], 100.0 * s["count"] / mc["samples"]))
    lines += ["", "## 局限（必须随结论一起看）",
              "1. 初始概率与影响强度均为主观估计，模拟不消除不确定性；",
              "2. 本公式是一阶近似，不能替代因果识别与证据验证；",
              "3. 样本独立抽样，未建模事件间的高阶相关；",
              "4. 结论只能用于排情景与找敏感变量，不能当预测。"]

    with io.open(os.path.join(out_dir, "summary.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("ok events=%d influences=%d out=%s"
          % (len(case["events"]), len(case.get("influences") or []), out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
