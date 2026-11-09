#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jev 中文财经场景评测脚本（零依赖，纯标准库）。

评测协议（对齐 36kr 实测方法）：
  1. 每题问 3 遍，看答案是否稳定（自一致性）；
  2. choice/noul 以概率最高项为答案；score 以分值最高（或居中最近）档位为答案；
  3. 统计：准确率 / 三遍一致率 / 平均延迟 / token 成本 / 校准桶（高把握答错）；
  4. 结果写 result-<时间戳>.json，控制台打印汇总表。

用法：
  python jev_eval.py --dry-run            # 不调 API，仅校验题目库与请求构造
  python jev_eval.py --repeats 3          # 正式评测（默认 3 遍）
  python jev_eval.py --repeats 1          # 快速跑一遍
环境变量：
  JEV_API_KEY   TypeSafe key（https://console.typesafe.ai/keys）
  JEV_BASE_URL  可选，默认 https://api.typesafe.ai/v1
  JEV_MODEL     可选，默认 jev-latest
"""

from __future__ import annotations

import json
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Windows 控制台默认 GBK，中文/符号打印报错 → 强制 UTF-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
QUESTIONS_FILE = HERE / "questions.json"
RESULTS_DIR = HERE / "results"

API_KEY = os.environ.get("JEV_API_KEY", "")
BASE_URL = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
TIMEOUT = 30
# 可选代理（2026-11-05）：typesafe.ai 对本机直连出口返回 451（地区法律屏蔽），
# 生产侧早已切到本地 ollama（jev-gate.env.ps1: JEV_BACKEND=ollama）。评测若要
# 继续对 jev-latest 打分，必须显式给出口代理，例如
#   $env:JEV_PROXY='http://127.0.0.1:7890'
# 不设则维持 urllib 默认（读环境代理，任务环境里等于直连）。
PROXY = os.environ.get("JEV_PROXY", "").strip()


# ── 请求构造 ────────────────────────────────────────────────
def build_request(q: dict) -> dict:
    """把单道题构造成 Jev API 请求体（每次请求只含一题，避免状态串扰）。"""
    questions = {}
    if q["type"] == "noul":
        questions["main"] = {"type": "noul", "instructions": q["question"]}
    elif q["type"] == "choice":
        # 官方 schema：criteria 为 {key: 选项描述} 字典；响应 choice 返回 key
        # 用短 key（opt_N）而非长中文文本作 key，规避长文本引用不稳的问题
        criteria = {f"opt_{i}": text for i, text in enumerate(q["options"])}
        questions["main"] = {
            "type": "choice",
            "instructions": q["question"],
            "criteria": criteria,
        }
    elif q["type"] == "score":
        # 官方 schema：criteria 为有序档位描述数组；响应 probabilities 按索引给概率
        questions["main"] = {
            "type": "score",
            "instructions": q["question"],
            "criteria": q["levels"],
        }
    else:
        raise ValueError(f"未知题型: {q['type']}")
    return {"model": MODEL, "state": q["state"], "questions": questions}


def call_jev(body: dict) -> dict:
    """调一次 Jev API，返回 (answers, usage, latency_ms)。失败抛异常。"""
    req = urllib.request.Request(
        BASE_URL + "/systemone",  # 实测端点（2026-09-21 验证）
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    op = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        )
        if PROXY
        else urllib.request.build_opener()
    )
    with op.open(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000
    return data, latency


def probe_endpoint() -> int:
    """端点体检：只问 1 题，确认「网络通 / 密钥活 / schema 对」。

    为什么要有：月度评测在计划任务里无人值守跑，端点被 451 屏蔽时会静默
    产出 30 题全失败的「正式结果」，污染基线并触发假漂移告警。
    退出码：0 通 / 4 HTTP 错误（含 401/451）/ 5 网络不可达。
    """
    body = {
        "model": MODEL,
        "state": "贵州茅台今日公告称，2026 年第三季度营业收入同比增长 8%。",
        "questions": {"main": {"type": "noul", "instructions": "这段材料提到营业收入增长了。"}},
    }
    print(f"endpoint {BASE_URL}/systemone  model={MODEL}  proxy={PROXY or '(env/default)'}")
    try:
        resp, lat = call_jev(body)
    except urllib.error.HTTPError as e:
        _body = e.read()[:160].decode("utf-8", errors="replace")
        if re.search(r"key|token|sk-", _body, re.IGNORECASE):
            _body = "[REDACTED]"
        print(f"PROBE FAIL HTTP {e.code} {_body!r}")
        if e.code == 451:
            print("诊断：地区屏蔽。要么给出口代理（$env:JEV_PROXY='http://127.0.0.1:7890'），"
                  "要么按生产口径改评测后端（JEV_BACKEND=ollama）")
        elif e.code in (401, 403):
            print("诊断：密钥无效/无权限 → 换 JEV_API_KEY")
        return 4
    except Exception as e:  # noqa: BLE001
        print(f"PROBE FAIL {type(e).__name__}: {str(e)[:160]}")
        print("诊断：网络不可达（DNS/代理/防火墙）")
        return 5
    ans = (resp.get("answers") or {}).get("main")
    print(f"PROBE OK model={resp.get('model')} latency={lat:.0f}ms answer={ans}")
    return 0


# ── 答案解析 ────────────────────────────────────────────────
def parse_answer(q: dict, resp: dict):
    """从响应里抽出本题的预测答案与置信度。返回 (pred, confidence)。"""
    ans = resp.get("answers", {}).get("main", {})
    t = ans.get("type", q["type"])
    if t == "noul":
        p = ans.get("noul", 0.0)
        # 置信度 = 对所给答案的把握：判真取 p，判假取 1-p
        return p >= 0.5, (p if p >= 0.5 else 1.0 - p)
    if t == "choice":
        # 响应 choice 返回 criteria 的 key（opt_N），转回索引
        choice_key = ans.get("choice", "")
        probs = ans.get("probabilities") or {}
        try:
            idx = int(str(choice_key).replace("opt_", ""))
        except ValueError:
            idx = -1
        conf = probs.get(choice_key, ans.get("confidence", 0.0))
        return idx, conf
    if t == "score":
        # 响应 probabilities 按档位索引给概率，取 argmax
        probs = ans.get("probabilities") or {}
        if probs:
            best = max(probs.items(), key=lambda kv: kv[1])
            return int(best[0]), best[1]
        score = ans.get("score", 0.0)  # 兜底：0..1 连续值映射到档位
        n = len(q["levels"])
        idx = min(n - 1, max(0, round(score * (n - 1))))
        return idx, ans.get("confidence", 0.0)
    raise ValueError(f"响应中未知类型: {t}")


def ground_truth(q: dict):
    if q["type"] == "noul":
        return q["answer"]
    if q["type"] == "choice":
        return q["answer_index"]
    return q["answer_level"]


# ── 评测主流程 ──────────────────────────────────────────────
def run(repeats: int, dry_run: bool) -> None:
    bank = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    qs = bank["questions"]
    rng = random.Random(42)

    # 题型分布统计 + 答案分布自检
    types = {}
    for q in qs:
        types[q["type"]] = types.get(q["type"], 0) + 1
    noul_answers = [q["answer"] for q in qs if q["type"] == "noul"]
    print(f"题目库：{len(qs)} 题  分布={types}")
    print(f"  noul true/false = {noul_answers.count(True)}/{noul_answers.count(False)}")
    bad = [
        q["id"]
        for q in qs
        if q["type"] == "choice" and q["answer_index"] >= len(q["options"])
    ]
    bad += [
        q["id"]
        for q in qs
        if q["type"] == "score" and q["answer_level"] >= len(q["levels"])
    ]
    if bad:
        sys.exit(f"题目库错误，答案越界: {bad}")

    if dry_run:
        print("\n--dry-run：请求构造预览（前 2 题）--")
        for q in qs[:2]:
            print(json.dumps(build_request(q), ensure_ascii=False, indent=2))
        print("dry-run 通过。设置 JEV_API_KEY 后正式运行。")
        return

    if not API_KEY:
        sys.exit("缺少 JEV_API_KEY 环境变量（https://console.typesafe.ai/keys）")

    RESULTS_DIR.mkdir(exist_ok=True)
    records = []
    total_tokens = 0
    latencies = []

    for i, q in enumerate(qs, 1):
        body = build_request(q)
        preds, confs = [], []
        for r in range(repeats):
            # 题间插入随机间隔，避免命中同一缓存/批量窗口
            time.sleep(rng.uniform(0.2, 0.6))
            try:
                resp, latency = call_jev(body)
            except urllib.error.HTTPError as e:
                # 审计修复（2026-10-14）：响应体只截前 80 字符，且含
                # key/token/sk- 字样的片段不打印（防凭据进日志）
                _body = e.read()[:80].decode("utf-8", errors="replace")
                if re.search(r"key|token|sk-", _body, re.IGNORECASE):
                    _body = "[REDACTED：响应体含凭据字样，不打印]"
                print(f"[{i}/{len(qs)}] {q['id']} 第{r + 1}遍 HTTP {e.code}: {_body!r}")
                preds.append(None)
                confs.append(None)
                continue
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(qs)}] {q['id']} 第{r + 1}遍 失败: {e}")
                preds.append(None)
                confs.append(None)
                continue
            latencies.append(latency)
            usage = resp.get("usage", {}) or {}
            total_tokens += (usage.get("input_tokens", 0) or 0) + (
                usage.get("output_tokens", 0) or 0
            )
            pred, conf = parse_answer(q, resp)
            preds.append(pred)
            confs.append(conf)

        valid = [p for p in preds if p is not None]
        consistent = len(set(map(json.dumps, valid))) == 1 if valid else False
        gt = ground_truth(q)
        correct = all(p == gt for p in valid) if valid else False
        mean_conf = (
            statistics.mean([c for c in confs if c is not None])
            if any(c is not None for c in confs)
            else None
        )
        records.append(
            {
                "id": q["id"],
                "type": q["type"],
                "repeats_preds": [str(p) for p in preds],
                "ground_truth": str(gt),
                "correct": correct,
                "consistent": consistent,
                "mean_confidence": mean_conf,
                "errors": preds.count(None),
            }
        )
        flag = "OK " if correct else ("~  " if valid else "ERR")
        conf_s = f"{mean_conf:.2f}" if mean_conf is not None else "N/A"
        print(
            f"[{i:>2}/{len(qs)}] {q['id']} {flag} 预测={preds} 真值={gt} 置信={conf_s} 一致={consistent}"
        )

    # ── 汇总 ────────────────────────────────────────────────
    n = len(records)
    acc = sum(r["correct"] for r in records) / n
    cons = sum(r["consistent"] for r in records) / n
    err = sum(r["errors"] for r in records) / (n * repeats)
    lat_med = statistics.median(latencies) if latencies else 0
    lat_avg = statistics.mean(latencies) if latencies else 0

    # 校准桶：置信 ≥0.8 却答错（自信勾错，比胡编更难察觉）
    high_conf_wrong = [
        r
        for r in records
        if r["correct"] is False and (r["mean_confidence"] or 0) >= 0.8
    ]
    low_conf_right = [
        r for r in records if r["correct"] is True and (r["mean_confidence"] or 1) < 0.5
    ]

    # 分题型准确率
    by_type = {}
    for q, r in zip(qs, records):
        t = q["type"]
        by_type.setdefault(t, []).append(r["correct"])

    summary = {
        "run_at": datetime.now().isoformat(),
        "model": MODEL,
        "base_url": BASE_URL,
        "repeats": repeats,
        "total_questions": n,
        "accuracy": round(acc, 4),
        "self_consistency": round(cons, 4),
        "error_rate": round(err, 4),
        "latency_ms_median": round(lat_med),
        "latency_ms_avg": round(lat_avg),
        "total_tokens": total_tokens,
        "high_conf_wrong_ids": [r["id"] for r in high_conf_wrong],
        "low_conf_right_ids": [r["id"] for r in low_conf_right],
        "by_type_accuracy": {t: round(sum(v) / len(v), 4) for t, v in by_type.items()},
    }
    out = RESULTS_DIR / f"result-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps(
            {"summary": summary, "records": records}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    print("\n════════ 汇总 ════════")
    print(f"准确率      : {acc:.1%}  ({sum(r['correct'] for r in records)}/{n})")
    print(f"三遍一致率  : {cons:.1%}")
    print(f"请求失败率  : {err:.1%}")
    print(f"延迟 中位/均: {lat_med:.0f}ms / {lat_avg:.0f}ms")
    print(f"token 总量  : {total_tokens}")
    print(f"分题型准确率: {summary['by_type_accuracy']}")
    if high_conf_wrong:
        print(
            f"⚠ 高置信答错({len(high_conf_wrong)}题): {summary['high_conf_wrong_ids']}"
        )
    if low_conf_right:
        print(
            f"⚠ 答对但低置信({len(low_conf_right)}题): {summary['low_conf_right_ids']}"
        )
    print(f"\n结果已写入: {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--probe-endpoint" in args:
        sys.exit(probe_endpoint())
    dry = "--dry-run" in args
    reps = 1
    if "--repeats" in args:
        reps = int(args[args.index("--repeats") + 1])
    run(repeats=reps, dry_run=dry)
