# -*- coding: utf-8 -*-
"""single/multi 通道限流复核（task-119）：6/分窗口 vs 真实 LLM 延迟。

背景：8091 QA 已上线（WIRED=True）：retrieval 300/分、single 6/分、multi 6/分。
本工具：
  ① 真实 LLM 小样本实测（**总真实 LLM ≤6 次**：默认 single 3 + multi 3），经 8091 HTTP
     `GET /api/kb/qa?q=...&mode=single|multi&cache=0`，记录状态/耗时/引用数/限流 meta；
  ② 限流边界用**进程内 mock**复核（不消耗 LLM）：single/multi 各快速 7 连发（唯一问题），
     验证「前 6 次 200 + 第 7 次 429」；
  ③ 可达性评估：串行吞吐 = 60 / 平均延迟，与 6/分比较；给出建议值（**只建议、不改配置**）；
  ④ 输出 research/reports/qa_channel_limit_20261102.{json,md}。

用法：
  python tools/kb_corpus/qa_channel_limit.py [--single-real 3] [--multi-real 3] [--timeout 240]
约束：真实 LLM 总量 ≤6（参数超限即拒绝）；不重启服务、不改 api_kb_qa 配置。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import qa_http_loadtest as loadtest  # noqa: E402

OUT = ROOT / "research" / "reports"
OUT_JSON = OUT / "qa_channel_limit_20261102.json"
OUT_MD = OUT / "qa_channel_limit_20261102.md"
BASE_URL = loadtest.BASE_URL
CHANNELS = ("single", "multi")
CONFIG_LIMITS = {"single": 6, "multi": 6}     # 当前 8091 配置（复核只读）
V3_BANK = Path(loadtest.V3_BANK)
MAX_REAL_LLM = 6
SCHEMA_VERSION = "qa_channel_limit.v1"
EXPECTED_BOUNDARY = [200] * 6 + [429]


# ---------------------------------------------------------------------------
# 真实探测（HTTP，8091）
# ---------------------------------------------------------------------------
def build_url(question: str, mode: str, topk: int = 3) -> str:
    params = {"q": question, "mode": mode, "topk": str(topk), "cache": "0"}
    return f"{BASE_URL.rstrip('/')}/api/kb/qa?{urlencode(params)}"


def probe_real(mode: str, n: int, questions: list[str], timeout: float = 240.0,
               fetch_fn=None) -> list[dict]:
    """真实调用 n 次（计数 LLM），返回每次状态/耗时/引用/限流 meta。"""
    fetch = fetch_fn or loadtest.http_get
    out = []
    for i in range(n):
        q = questions[i % len(questions)]
        status, payload, elapsed, rate = fetch(build_url(q, mode), timeout=timeout)
        data = payload.get("data") if isinstance(payload, dict) else {}
        conf = (data or {}).get("confidence") or {}
        out.append({"mode": mode, "i": i + 1, "question": q[:40], "status": status,
                    "elapsed_seconds": elapsed,
                    "citations": len((data or {}).get("citations") or []),
                    "answer_len": len(str((data or {}).get("answer") or "")),
                    "confidence": conf.get("level"),
                    "rate_limit": rate, "error": (payload or {}).get("error")
                    if isinstance(payload, dict) else str(payload)[:160]})
    return out


# ---------------------------------------------------------------------------
# 限流边界（进程内 mock，不调 LLM、不重启）
# ---------------------------------------------------------------------------
def _load_api():
    import importlib.util
    path = ROOT / "portal" / "api_kb_qa.py"
    spec = importlib.util.spec_from_file_location("api_kb_qa_limit_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_ask(question, topk=8, timeout=300, retrieval_only=False, **_kw):
    return {"question": question, "answer": "单单元答案 [u1] p.1", "citations": [{"unit_id": "u1"}],
            "confidence": {"level": "high"}, "retrieved": [], "citation_check": {},
            "fallback": False}


def _mock_multi(question, k=8, timeout=300, **_kw):
    return {"question": question, "k": k, "clusters": [], "cluster_count": 0,
            "answer": "多单元答案 [u1] p.1 [u2] p.2",
            "citations": [{"unit_id": "u1"}, {"unit_id": "u2"}],
            "confidence": {"level": "high"}, "citation_check": {},
            "fallback": False, "refusal": False}


class _PassGuard:
    def load_kb_ids(self):
        return {"u1", "u2"}, {"b1"}

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback", **_kw):
        out = dict(result)
        out.update({"guard_passed": True, "guard_action": "pass", "guard_issues": [],
                    "citation_valid": ["u1", "u2"], "citation_invalid": []})
        return out


def simulate_boundary(mode: str, n: int = 7, api=None) -> dict:
    """进程内 7 连发（唯一问题、跳过缓存），验证前 6 次 200、第 7 次 429。"""
    api = api or _load_api()
    api.reset_rate_limit()
    api.reset_cache()
    statuses = []
    for i in range(n):
        query = {"q": [f"限流边界探测-{mode}-{i}-{time.time_ns()}"], "mode": [mode],
                 "topk": ["3"], "cache": ["0"]}
        if mode == "multi":
            status, env = api.handle("GET", "/api/kb/qa", query, ask_fn=_mock_ask,
                                     multi_fn=_mock_multi, guard_impl=_PassGuard())
        else:
            status, env = api.handle("GET", "/api/kb/qa", query, ask_fn=_mock_ask,
                                     multi_fn=_mock_multi)
        statuses.append(status)
    api.reset_rate_limit()
    api.reset_cache()
    expected = list(EXPECTED_BOUNDARY[:min(n, len(EXPECTED_BOUNDARY))])
    return {"mode": mode, "n": n, "statuses": statuses, "expected": expected,
            "matches": statuses == expected,
            "limit": CONFIG_LIMITS.get(mode),
            "window_seconds": api.RATE_LIMIT_WINDOW_SECONDS}


# ---------------------------------------------------------------------------
# 统计 / 建议
# ---------------------------------------------------------------------------
def analyze(results: list[dict]) -> dict:
    """延迟统计 + 串行可达吞吐（次/分）。"""
    ok = [r for r in results if r.get("status") == 200]
    lat = [float(r["elapsed_seconds"]) for r in ok]
    if not lat:
        return {"n": len(results), "ok": 0, "statuses": [r.get("status") for r in results],
                "mean_seconds": None, "p50_seconds": None, "p95_seconds": None,
                "min_seconds": None, "max_seconds": None,
                "serial_capacity_per_min": None}
    mean = round(statistics.fmean(lat), 2)
    p95 = round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 2)
    return {"n": len(results), "ok": len(ok),
            "statuses": [r.get("status") for r in results],
            "mean_seconds": mean, "p50_seconds": round(statistics.median(lat), 2),
            "p95_seconds": p95, "min_seconds": round(min(lat), 2),
            "max_seconds": round(max(lat), 2),
            "serial_capacity_per_min": round(60.0 / mean, 2) if mean > 0 else None}


def recommend(mode: str, stats: dict, current: int | None = None) -> dict:
    """基于实测延迟给建议限流值（不改配置）。"""
    current = current if current is not None else CONFIG_LIMITS.get(mode, 6)
    cap = stats.get("serial_capacity_per_min")
    if not cap:
        return {"channel": mode, "current": current,
                "measured_mean_seconds": stats.get("mean_seconds"),
                "serial_capacity_per_min": None, "recommended": current,
                "rationale": "无成功样本，维持现值并补测", "config_changed": False}
    # 串行容量 ≥ 当前限流 → 可达，维持现值；否则按 ×0.8 安全系数下调（夹在 2..当前-1）
    if cap >= current:
        suggested = current
        rationale = (f"实测均值 {stats['mean_seconds']}s → 串行容量 {cap} 次/分 ≥ 当前 {current}/分，"
                     f"窗口可达；**建议维持 {current}/分**（并发仍受 Ollama 串行生成限制）")
    else:
        suggested = max(2, min(current - 1, int(cap * 0.8)))
        rationale = (f"实测均值 {stats['mean_seconds']}s → 串行容量 {cap} 次/分 < 当前 {current}/分，"
                     f"6/分窗口在串行下不可达；**建议下调至 {suggested}/分**"
                     f"（或保持 {current} 但明确为突发上限）")
    return {"channel": mode, "current": current, "measured_mean_seconds": stats["mean_seconds"],
            "serial_capacity_per_min": cap, "recommended": suggested,
            "rationale": rationale, "config_changed": False}


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(real: dict[str, list[dict]], boundary: dict[str, dict],
                 analyses: dict[str, dict], recommendations: dict[str, dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "qa_channel_limit_review",
        "date_tag": "20261102",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_url": BASE_URL,
        "config_snapshot": {"rate_limit": CONFIG_LIMITS,
                            "multi_timeout_seconds": 300, "single_timeout_seconds": 120,
                            "window_seconds": 60, "note": "复核只读，未修改任何配置"},
        "real_llm_budget": {"max": MAX_REAL_LLM,
                            "used": sum(len(v) for v in real.values()),
                            "per_channel": {k: len(v) for k, v in real.items()}},
        "real": real,
        "boundary": boundary,
        "analysis": analyses,
        "recommendations": recommendations,
        "conclusion": build_conclusion(analyses, recommendations),
        "caveats": [
            "真实 LLM 采样极小（single/multi 各 3 次），延迟受本机负载/模型缓存影响，仅供量级参考；",
            "限流为滑动窗口（60s），保护的是**并发突发**；串行连续调用因单次延迟 ≥10s 往往自然落在窗口内，不一定触发 429；",
            "Ollama 单实例串行生成，多并发会排队导致整体变慢，限流值应留安全余量；",
            "本报告只给建议，未修改 portal/api_kb_qa.py 的 RATE_LIMIT；调整需另立任务+重启 8091。",
        ],
    }


def build_conclusion(analyses: dict, recs: dict) -> dict:
    feasible = {}
    for mode, stats in analyses.items():
        cap = stats.get("serial_capacity_per_min")
        limit = CONFIG_LIMITS.get(mode)
        feasible[mode] = {"serial_capacity_per_min": cap, "limit": limit,
                          "reachable": bool(cap is not None and cap >= limit)}
    return {"6_per_min_reachable": feasible,
            "recommendations": {m: r.get("recommended") for m, r in recs.items()},
            "summary": "；".join(f"{m}: {r.get('rationale')}" for m, r in recs.items())}


def render_md(rep: dict) -> str:
    cfg = rep["config_snapshot"]
    real = rep["real"]
    L = ["# single/multi 通道限流复核（6/分窗口 vs 真实 LLM 延迟）", ""]
    L.append(f"- 生成：{rep['generated_at']}；目标：{rep['base_url']}（8091 QA 已上线）")
    L.append(f"- 当前配置：{cfg['rate_limit']}（滑动窗口 {cfg['window_seconds']}s；"
             f"超时 single {cfg['single_timeout_seconds']}s / multi {cfg['multi_timeout_seconds']}s）")
    L.append(f"- 真实 LLM 预算：{rep['real_llm_budget']['used']}/{rep['real_llm_budget']['max']} 次（"
             f"single {len(real.get('single') or [])} + multi {len(real.get('multi') or [])}）")
    L.append("")
    L += ["## 一、真实延迟实测（经 8091，cache=0）", ""]
    L += ["| 通道 | # | 状态 | 耗时(s) | 引用数 | 置信 |", "|---|---|---|---|---|---|"]
    for mode in CHANNELS:
        for r in real.get(mode) or []:
            L.append(f"| {mode} | {r['i']} | {r['status']} | {r['elapsed_seconds']} | "
                     f"{r['citations']} | {r.get('confidence')} |")
    L.append("")
    L += ["## 二、限流边界（进程内 mock，7 连发，不消耗 LLM）", ""]
    L += ["| 通道 | 期望 | 实际 | 匹配 | 窗口 |", "|---|---|---|---|---|"]
    for mode, b in rep["boundary"].items():
        L.append(f"| {mode} | {b['expected']} | {b['statuses']} | {'✅' if b['matches'] else '❌'} | "
                 f"{b['window_seconds']}s |")
    L.append("")
    L += ["## 三、可达性分析（串行吞吐 = 60/均值）", ""]
    L += ["| 通道 | 成功/总数 | 均值(s) | P50 | P95 | 串行容量(次/分) | 当前限流 | 6/分可达 |",
          "|---|---|---|---|---|---|---|---|"]
    for mode, a in rep["analysis"].items():
        reach = rep["conclusion"]["6_per_min_reachable"].get(mode, {})
        L.append(f"| {mode} | {a['ok']}/{a['n']} | {a['mean_seconds']} | {a['p50_seconds']} | "
                 f"{a['p95_seconds']} | {a['serial_capacity_per_min']} | {reach.get('limit')} | "
                 f"{'✅' if reach.get('reachable') else '❌'} |")
    L.append("")
    L += ["## 四、建议（只建议，不改配置）", ""]
    L += ["| 通道 | 当前 | 实测均值(s) | 建议值 | 理由 |", "|---|---|---|---|---|"]
    for mode, r in rep["recommendations"].items():
        L.append(f"| {mode} | {r['current']} | {r.get('measured_mean_seconds')} | "
                 f"**{r['recommended']}** | {r['rationale']} |")
    L.append("")
    L += ["## 五、结论", "", f"- {rep['conclusion']['summary']}", ""]
    L += ["## 六、口径与局限", ""]
    L += [f"- {x}" for x in rep["caveats"]]
    L.append("")
    return "\n".join(L)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="single/multi 限流复核（只读，不改配置）")
    ap.add_argument("--single-real", type=int, default=3, help="single 真实调用次数")
    ap.add_argument("--multi-real", type=int, default=3, help="multi 真实调用次数")
    ap.add_argument("--timeout", type=float, default=240.0, help="单次 HTTP 超时秒")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.single_real + args.multi_real > MAX_REAL_LLM:
        print(json.dumps({"error": f"真实 LLM 总量超限：{args.single_real}+{args.multi_real} > "
                                   f"{MAX_REAL_LLM}，请调小参数"}, ensure_ascii=False))
        return 2
    questions = loadtest._question_pool(V3_BANK)
    real = {
        "single": probe_real("single", args.single_real, questions, args.timeout)
        if args.single_real > 0 else [],
        "multi": probe_real("multi", args.multi_real, questions[1:], args.timeout)
        if args.multi_real > 0 else [],
    }
    boundary = {mode: simulate_boundary(mode) for mode in CHANNELS}
    analyses = {mode: analyze(real[mode]) for mode in CHANNELS}
    recs = {mode: recommend(mode, analyses[mode]) for mode in CHANNELS}
    rep = build_report(real, boundary, analyses, recs)
    OUT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.write_text(render_md(rep), encoding="utf-8")
    print(json.dumps({"real_llm_used": rep["real_llm_budget"]["used"],
                      "analysis": {m: {"mean_s": a["mean_seconds"],
                                       "capacity_per_min": a["serial_capacity_per_min"]}
                                   for m, a in analyses.items()},
                      "boundary": {m: b["statuses"] for m, b in boundary.items()},
                      "recommendations": {m: r["recommended"] for m, r in recs.items()},
                      "outputs": {"json": str(OUT_JSON), "md": str(OUT_MD)}},
                     ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
