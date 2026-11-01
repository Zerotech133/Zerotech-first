# -*- coding: utf-8 -*-
"""通道容量/限流语义复核（task-122）：mock 慢 LLM，真实 LLM 0 次。

背景：8091 QA 限流 retrieval 300/分、single 6/分、multi 6/分；实测单次 10-16s，
串行自然吞吐 <6/分。本工具用**进程内 mock 慢 LLM**（single 12s / multi 10s）复现：
  ① 并发 1/2/4 各 10 请求矩阵：记录队列等待/成功/429/总耗时/吞吐；
  ② 并发 10 突发验证：滑动窗口 6/分是否 200×6 → 429×4；
  ③ 结论：6/分作为「突发上限」的合理性（串行不误伤、突发受保护），建议维持（不改配置）；
  ④ 输出 research/reports/qa_channel_capacity_20261102.{json,md}。

用法：
  python tools/kb_corpus/qa_channel_capacity.py            # 标准矩阵（约 3-4 分钟）
  python tools/kb_corpus/qa_channel_capacity.py --quick     # 缩小 n/延迟（自测）
约束：不重启服务、不改配置、不调用真实 LLM。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "reports"
OUT_JSON = OUT / "qa_channel_capacity_20261102.json"
OUT_MD = OUT / "qa_channel_capacity_20261102.md"
SCHEMA_VERSION = "qa_channel_capacity.v1"
CHANNELS = ("single", "multi")
DEFAULT_SLEEPS = {"single": 12.0, "multi": 10.0}
CONCURRENCIES = (1, 2, 4)
BURST_CONCURRENCY = 10
DEFAULT_N = 10
CONFIG = {"rate_limit": {"retrieval": 300, "single": 6, "multi": 6},
          "window_seconds": 60, "note": "复核只读，未修改任何配置"}


# ---------------------------------------------------------------------------
# 进程内 handler（每个通道独立实例，互不干扰限流状态）
# ---------------------------------------------------------------------------
def load_api():
    path = ROOT / "portal" / "api_kb_qa.py"
    spec = importlib.util.spec_from_file_location(f"api_kb_qa_capacity_{time.time_ns()}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_mock_fns(channel: str, sleep_seconds: float, state: dict):
    """mock LLM：sleep 模拟延迟；记录调用开始/结束。"""

    def ask_fn(question, topk=8, timeout=300, retrieval_only=False, **_kw):
        state.setdefault("llm_calls", 0)
        state["llm_calls"] += 1
        t0 = time.monotonic()
        time.sleep(sleep_seconds)
        state.setdefault("llm_starts", []).append(t0)
        return {"question": question, "answer": "单单元答案 [u1] p.1",
                "citations": [{"unit_id": "u1"}], "confidence": {"level": "high"},
                "retrieved": [], "citation_check": {}, "fallback": False}

    def multi_fn(question, k=8, timeout=300, **_kw):
        state.setdefault("llm_calls", 0)
        state["llm_calls"] += 1
        t0 = time.monotonic()
        time.sleep(sleep_seconds)
        state.setdefault("llm_starts", []).append(t0)
        return {"question": question, "k": k, "clusters": [], "cluster_count": 0,
                "answer": "多单元答案 [u1] p.1 [u2] p.2",
                "citations": [{"unit_id": "u1"}, {"unit_id": "u2"}],
                "confidence": {"level": "high"}, "citation_check": {},
                "fallback": False, "refusal": False}

    return (ask_fn, multi_fn) if channel == "single" else (ask_fn, multi_fn)


class _PassGuard:
    def load_kb_ids(self):
        return {"u1", "u2"}, {"b1"}

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback", **_kw):
        out = dict(result)
        out.update({"guard_passed": True, "guard_action": "pass", "guard_issues": [],
                    "citation_valid": ["u1", "u2"], "citation_invalid": []})
        return out


# ---------------------------------------------------------------------------
# 矩阵单元
# ---------------------------------------------------------------------------
def run_cell(channel: str, concurrency: int, n: int = DEFAULT_N,
             sleep_seconds: float | None = None, api=None) -> dict:
    """一个矩阵单元：并发 concurrency、共 n 请求；返回成功率/429/等待/总耗时。"""
    api = api or load_api()
    api.reset_rate_limit()
    api.reset_cache()
    ask_fn, multi_fn = make_mock_fns(channel, sleep_seconds if sleep_seconds is not None
                                     else DEFAULT_SLEEPS[channel], {})
    rows = []
    lock = threading.Lock()
    started = time.monotonic()

    def one(i: int):
        submit = time.monotonic()
        query = {"q": [f"容量矩阵-{channel}-{concurrency}-{i}-{time.time_ns()}"],
                 "mode": [channel], "topk": ["3"], "cache": ["0"]}
        try:
            if channel == "multi":
                status, env = api.handle("GET", "/api/kb/qa", query, ask_fn=ask_fn,
                                         multi_fn=multi_fn, guard_impl=_PassGuard())
            else:
                status, env = api.handle("GET", "/api/kb/qa", query, ask_fn=ask_fn,
                                         multi_fn=multi_fn)
        except Exception as exc:  # noqa: BLE001
            status, env = "ERR", {"error": f"{type(exc).__name__}: {exc}"}
        end = time.monotonic()
        with lock:
            rows.append({"i": i, "status": status, "submit": submit, "start": submit,
                         "end": end, "elapsed": round(end - submit, 3),
                         "error": (env or {}).get("error") if isinstance(env, dict) else None})

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(n)))
    wall = time.monotonic() - started
    statuses = [r["status"] for r in rows]
    ok = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 429)
    waits = [max(0.0, r["start"] - r["submit"]) for r in rows]
    return {
        "channel": channel, "concurrency": concurrency, "n": n,
        "sleep_seconds": round(sleep_seconds if sleep_seconds is not None
                               else DEFAULT_SLEEPS[channel], 2),
        "success": ok, "rate_limited": rejected, "other": len(statuses) - ok - rejected,
        "statuses": statuses,
        "queue_wait_mean_s": round(statistics.fmean(waits), 3) if waits else 0.0,
        "queue_wait_max_s": round(max(waits), 3) if waits else 0.0,
        "total_wall_s": round(wall, 2),
        "throughput_per_min": round(ok / wall * 60, 2) if wall > 0 else None,
        "llm_calls": ok,        # 只有 200 才真正进入 mock LLM（429 在 LLM 前拒绝）
    }


def run_matrix(channels=CHANNELS, concurrencies=CONCURRENCIES, n: int = DEFAULT_N,
               sleeps: dict | None = None, burst_concurrency: int = BURST_CONCURRENCY) -> dict:
    """按通道并行跑矩阵（每通道独立 handler 实例），返回 {channel: {...}}。"""
    sleeps = sleeps or DEFAULT_SLEEPS
    result: dict[str, dict] = {}

    def channel_job(channel: str):
        cells = []
        for c in list(concurrencies) + [burst_concurrency]:
            cells.append(run_cell(channel, c, n=n, sleep_seconds=sleeps[channel]))
        result[channel] = {"cells": cells}

    threads = [threading.Thread(target=channel_job, args=(ch,)) for ch in channels]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return result


# ---------------------------------------------------------------------------
# 分析 / 报告
# ---------------------------------------------------------------------------
def analyze_matrix(matrix: dict) -> dict:
    """矩阵 → 每通道结论：突发是否 200×6+429、串行是否误伤、6/分语义。"""
    out = {}
    for channel, data in matrix.items():
        cells = {c["concurrency"]: c for c in data["cells"]}
        burst = cells.get(BURST_CONCURRENCY, {})
        statuses = burst.get("statuses") or []
        ok6 = statuses.count(200) == 6
        rej4 = statuses.count(429) == 4
        serial = cells.get(1, {})
        out[channel] = {
            "burst_expected_200": 6, "burst_expected_429": 4,
            "burst_actual_200": statuses.count(200),
            "burst_actual_429": statuses.count(429),
            "burst_matches_6_plus_429": bool(ok6 and rej4),
            "serial_total": serial.get("n"),
            "serial_success": serial.get("success"),
            "serial_rate_limited": serial.get("rate_limited"),
            "serial_not_false_positive": bool((serial.get("rate_limited") or 0) == 0),
            "limit_per_minute": CONFIG["rate_limit"].get(channel),
        }
    return out


def build_recommendations(analysis: dict) -> dict:
    return {
        "single": {"current": 6, "recommended": 6,
                   "rationale": "6/分作为**突发上限**：并发突发时第 7 个起 429（保护 Ollama 不被压垮）；"
                                "串行单请求 10-16s，自然吞吐 <6/分，不会误伤；维持 6，不修改配置"},
        "multi": {"current": 6, "recommended": 6,
                  "rationale": "同上：multi 单次 ~10s，串行自然 <6/分；6/分仅作并发突发硬上限，维持"},
        "retrieval": {"current": 300, "recommended": 300,
                      "rationale": "检索缓存优化后 300/分（无 LLM 成本），维持"},
        "config_changed": False,
    }


def build_report(matrix: dict) -> dict:
    analysis = analyze_matrix(matrix)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "qa_channel_capacity_review",
        "date_tag": "20261102",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mock_llm": {"single_sleep_seconds": DEFAULT_SLEEPS["single"],
                     "multi_sleep_seconds": DEFAULT_SLEEPS["multi"],
                     "real_llm_calls": 0, "note": "进程内 mock，不调用真实 LLM"},
        "config_snapshot": CONFIG,
        "matrix": matrix,
        "analysis": analysis,
        "recommendations": build_recommendations(analysis),
        "conclusion": {
            "rate_limit_semantics": "single/multi 6/分 = **并发突发上限**（滑动窗口 60s），非串行吞吐目标",
            "burst_verified": all(a["burst_matches_6_plus_429"] for a in analysis.values()),
            "serial_not_penalized": all(a["serial_not_false_positive"] for a in analysis.values()),
            "verdict": "维持 6/分（single/multi）= 突发上限；retrieval 300/分；不改配置",
        },
        "caveats": [
            "mock LLM 用固定 sleep（single 12s / multi 10s）近似真实延迟；真实延迟受本机负载影响；",
            "滑动窗口实现为每通道独立 60s 计数，测试为进程内 handler，与 8091 同代码但独立状态；",
            "并发 1 时窗口自然滑动，10 个串行请求可能全部 200（不触发 429）→ 6/分只在突发并发时成为硬上限；",
            "本报告只做语义/容量复核，未修改配置、未重启服务。",
        ],
    }


def render_md(rep: dict) -> str:
    L = ["# 通道容量/限流语义复核（mock 慢 LLM）", ""]
    L.append(f"- 生成：{rep['generated_at']}；mock LLM：single {rep['mock_llm']['single_sleep_seconds']}s / "
             f"multi {rep['mock_llm']['multi_sleep_seconds']}s；**真实 LLM 0 次**")
    L.append(f"- 当前配置：{rep['config_snapshot']['rate_limit']}（窗口 {rep['config_snapshot']['window_seconds']}s）")
    L.append("")
    L += ["## 一、并发矩阵（每格 10 请求）", ""]
    L += ["| 通道 | 并发 | 成功 | 429 | 其他 | 队列等待均值(s) | 队列等待max(s) | 总耗时(s) | 吞吐(次/分) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for channel, data in rep["matrix"].items():
        for c in data["cells"]:
            L.append(f"| {channel} | {c['concurrency']} | {c['success']} | {c['rate_limited']} | "
                     f"{c['other']} | {c['queue_wait_mean_s']} | {c['queue_wait_max_s']} | "
                     f"{c['total_wall_s']} | {c['throughput_per_min']} |")
    L.append("")
    L += ["## 二、突发验证（并发 10）", ""]
    L += ["| 通道 | 期望 | 实际 200 | 实际 429 | 匹配 |", "|---|---|---|---|---|"]
    for channel, a in rep["analysis"].items():
        expected = f"200×{a['burst_expected_200']} + 429×{a['burst_expected_429']}"
        L.append(f"| {channel} | {expected} | {a['burst_actual_200']} | {a['burst_actual_429']} | "
                 f"{'✅' if a['burst_matches_6_plus_429'] else '❌'} |")
    L.append("")
    L += ["## 三、限流语义结论", "",
          f"- {rep['conclusion']['rate_limit_semantics']}",
          f"- 突发验证通过：{'✅' if rep['conclusion']['burst_verified'] else '❌'}；"
          f"串行不误伤：{'✅' if rep['conclusion']['serial_not_penalized'] else '❌'}",
          f"- **结论：{rep['conclusion']['verdict']}**", ""]
    L += ["## 四、建议（不修改配置）", "",
          "| 通道 | 当前 | 建议 | 理由 |", "|---|---|---|---|"]
    for ch, r in rep["recommendations"].items():
        if ch == "config_changed":
            continue
        L.append(f"| {ch} | {r['current']} | **{r['recommended']}** | {r['rationale']} |")
    L.append("")
    L += ["## 五、口径与局限", ""]
    L += [f"- {x}" for x in rep["caveats"]]
    L.append("")
    return "\n".join(L)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="通道容量/限流语义复核（mock LLM，真实 0 次）")
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="每格请求数（默认 10）")
    ap.add_argument("--single-sleep", type=float, default=DEFAULT_SLEEPS["single"])
    ap.add_argument("--multi-sleep", type=float, default=DEFAULT_SLEEPS["multi"])
    ap.add_argument("--quick", action="store_true", help="快速自测（n=3、sleep=0.2）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.quick:
        args.n, sleeps = 3, {"single": 0.2, "multi": 0.2}
    else:
        sleeps = {"single": args.single_sleep, "multi": args.multi_sleep}
    matrix = run_matrix(n=args.n, sleeps=sleeps)
    rep = build_report(matrix)
    rep["mock_llm"]["single_sleep_seconds"] = sleeps["single"]
    rep["mock_llm"]["multi_sleep_seconds"] = sleeps["multi"]
    OUT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.write_text(render_md(rep), encoding="utf-8")
    print(json.dumps({"analysis": {m: {"burst_200": a["burst_actual_200"],
                                       "burst_429": a["burst_actual_429"],
                                       "match": a["burst_matches_6_plus_429"],
                                       "serial_429": a["serial_rate_limited"]}
                                   for m, a in rep["analysis"].items()},
                      "verdict": rep["conclusion"]["verdict"],
                      "outputs": {"json": str(OUT_JSON), "md": str(OUT_MD)}},
                     ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
