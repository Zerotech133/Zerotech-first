# -*- coding: utf-8 -*-
"""情报队列优先级评分（P1）——标准 + 权重 + 敏感性，一次运行留痕。

背景（2026-11-11 老田批准 P1）：
  队列此前只有 tier 分层（T1/T2/T3），没有“为什么排前面”的标准与权重，
  容易把排序当结论。本工具把排序拆成三件套：
    1) 标准：影响 / 时效 / 证据强度 / 可行动性 / 成本（1–5 分）；
    2) 权重：显式权重表（默认 config/intel_scoring_weights.json，合计 1）；
    3) 敏感性：单标准权重 ±delta 后重排，报告前 N 名是否翻转。
  只输出“排序与敏感性”，不把评分当证据；权重与输入哈希全部留痕。

输入（--in）JSON 数组，示例：
  [{"code":"300750","name":"宁德时代","tier":"T1",
    "scores":{"impact":5,"timeliness":4,"evidence":4,"actionability":3,"cost":2}}]

输出（默认 reports/intel_scoring/<UTC时间>.json）：
  {generated_at, input, input_sha256, weights, scale, results:[...],
   sensitivity:{delta, top_n, flips, details:[...]}}

用法：
  python -X utf8 tools/intel_priority_score.py --in queue.json
  python -X utf8 tools/intel_priority_score.py --in queue.json --md
退出码：0=成功；2=输入/权重不合法（fail-visible）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WEIGHTS = os.path.join(ROOT, "config", "intel_scoring_weights.json")
DEFAULT_OUT = os.path.join(ROOT, "reports", "intel_scoring")


def _die(msg: str) -> "NoReturn":  # noqa: F821
    sys.stderr.write("[intel_priority_score] %s\n" % msg)
    raise SystemExit(2)


def load_weights(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        _die("权重表读取失败：%s（%s）" % (path, exc))
    criteria = data.get("criteria") or []
    if not criteria:
        _die("权重表无 criteria")
    keys = set()
    total = 0.0
    for c in criteria:
        key = str(c.get("key") or "").strip()
        if not key or key in keys:
            _die("权重表 criteria key 为空或重复：%r" % (c,))
        keys.add(key)
        try:
            w = float(c.get("weight"))
        except (TypeError, ValueError):
            _die("权重非法：%r" % (c,))
        if w <= 0:
            _die("权重必须为正：%r" % (c,))
        total += w
    if abs(total - 1.0) > 1e-6:
        _die("权重合计必须为 1，实际 %.6f" % total)
    scale = data.get("scale") or [1, 5]
    lo, hi = float(scale[0]), float(scale[1])
    if hi <= lo:
        _die("scale 非法：%r" % (scale,))
    sens = data.get("sensitivity") or {}
    return {"criteria": criteria, "keys": keys, "lo": lo, "hi": hi,
            "delta": float(sens.get("delta", 0.25)),
            "top_n": int(sens.get("top_n", 5)), "raw": data}


def _renorm(criteria: list, weights: list) -> list:
    total = sum(weights)
    if total <= 0:
        _die("权重归一化失败")
    return [w / total for w in weights]


def _score_one(item: dict, criteria: list, weights: list, lo: float,
               hi: float) -> dict:
    scores = item.get("scores") or {}
    missing = [c["key"] for c in criteria if c["key"] not in scores]
    if missing:
        _die("条目 %r 缺评分：%s" % (item.get("code"), missing))
    vals = []
    for c in criteria:
        try:
            v = float(scores[c["key"]])
        except (TypeError, ValueError):
            _die("条目 %r 的 %s 评分非数字" % (item.get("code"), c["key"]))
        if v < lo or v > hi:
            _die("条目 %r 的 %s 评分越界（%s–%s）：%s"
                 % (item.get("code"), c["key"], lo, hi, v))
        vals.append(v)
    raw = sum(w * v for w, v in zip(weights, vals))
    norm = (raw - lo) / (hi - lo) * 100.0
    return {"code": item.get("code"), "name": item.get("name"),
            "tier": item.get("tier"), "scores": dict(scores),
            "raw": round(raw, 4), "score": round(norm, 2)}


def rank(items: list, criteria: list, weights: list, lo: float, hi: float,
         top_n: int) -> list:
    rows = [_score_one(it, criteria, weights, lo, hi) for it in items]
    rows.sort(key=lambda r: (-r["score"], str(r["code"])))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def sensitivity(items: list, criteria: list, weights: list, lo: float,
                hi: float, delta: float, top_n: int) -> dict:
    base = rank(items, criteria, weights, lo, hi, top_n)
    base_top = {r["code"] for r in base[:top_n]}
    details = []
    flips = 0
    for idx, c in enumerate(criteria):
        for sign in (1, -1):
            w2 = list(weights)
            w2[idx] = max(1e-9, w2[idx] * (1.0 + sign * delta))
            w2 = _renorm(criteria, w2)
            rows = rank(items, criteria, w2, lo, hi, top_n)
            top = {r["code"] for r in rows[:top_n]}
            entered = sorted(top - base_top)
            left = sorted(base_top - top)
            if entered or left:
                flips += 1
                details.append({
                    "criterion": c["key"], "delta": sign * delta,
                    "weight": round(w2[idx], 6),
                    "entered": entered, "left": left,
                })
    return {"delta": delta, "top_n": top_n, "flips": flips,
            "details": details,
            "verdict": "sensitive" if flips else "stable"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="情报队列优先级评分（P1）")
    ap.add_argument("--in", dest="inp", required=True, help="候选 JSON 数组")
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--delta", type=float, default=None)
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--md", action="store_true", help="同时输出 Markdown 摘要")
    a = ap.parse_args(argv)

    try:
        with io.open(a.inp, "rb") as f:
            raw = f.read()
        items = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        _die("输入读取失败：%s（%s）" % (a.inp, exc))
    if not isinstance(items, list) or not items:
        _die("输入必须是非空数组")

    cfg = load_weights(a.weights)
    criteria = cfg["criteria"]
    weights = [float(c["weight"]) for c in criteria]
    delta = cfg["delta"] if a.delta is None else float(a.delta)
    top_n = cfg["top_n"] if a.top is None else int(a.top)
    if top_n <= 0:
        _die("--top 必须为正")

    base = rank(items, criteria, weights, cfg["lo"], cfg["hi"], top_n)
    sens = sensitivity(items, criteria, weights, cfg["lo"], cfg["hi"],
                       delta, top_n)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "schema": "intel_priority_score.v1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "input": os.path.abspath(a.inp),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "weights_file": os.path.abspath(a.weights),
        "weights": [{"key": c["key"], "label": c.get("label"),
                     "weight": float(c["weight"])} for c in criteria],
        "scale": [cfg["lo"], cfg["hi"]],
        "results": base,
        "sensitivity": sens,
    }
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, "%s.json" % ts)
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if a.md:
        md = os.path.join(a.out_dir, "%s.md" % ts)
        lines = ["# 情报队列优先级评分 %s" % ts, "",
                 "输入：`%s`（sha256=%s）" % (a.inp,
                                             report["input_sha256"][:16]),
                 "权重：%s" % " / ".join(
                     "%s=%.2f" % (c["key"], float(c["weight"]))
                     for c in criteria),
                 "", "| 排名 | 代码 | 名称 | 得分 | tier |",
                 "|---|---|---|---|---|"]
        for r in base:
            lines.append("| %d | %s | %s | %.2f | %s |"
                         % (r["rank"], r["code"], r.get("name") or "",
                            r["score"], r.get("tier") or ""))
        lines += ["", "敏感性（±%.0f%% 单权重）：%s，翻转 %d 次"
                  % (delta * 100, sens["verdict"], sens["flips"])]
        for d in sens["details"]:
            lines.append("- %s %+.0f%%：进入 %s，退出 %s"
                         % (d["criterion"], d["delta"] * 100, d["entered"],
                            d["left"]))
        with io.open(md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    print("ok results=%d top=%s sensitivity=%s flips=%d out=%s"
          % (len(base), [r["code"] for r in base[:top_n]], sens["verdict"],
             sens["flips"], out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
