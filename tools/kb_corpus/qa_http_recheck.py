# -*- coding: utf-8 -*-
"""8091 问答验收复跑（限流 300/分 + 检索缓存优化后，2026-11-02）。

背景：wave38 验收（qa_http_live_acceptance_20261101）发现 multi 走 v1/strict 且 retrieval
延迟 ~2s；随后完成检索缓存优化（P95 27s→0.61s）与限流调高（retrieval 60→300/分），
并将 multi 默认切为 v4+segment+citation_retry。本模块复跑线上验收并补热缓存基准：

  ① 复用 `qa_http_live_acceptance.run_acceptance`（真实 HTTP：retrieval 3 / single 2 / multi 3 + 边界 3）；
  ② 补充：retrieval 连续 20 次（真实 HTTP）→ P50/P95/P99、错误率、rate_limit meta（期望 limit=300）；
  ③ 与 wave38 验收对比（8/8 200、3/3 400、multi v4+segment+retry、retrieval 延迟 <0.2s）；
  ④ 输出 `research/reports/qa_http_recheck_20261102.{json,md}`：前后对比 + 是否 go + 放量建议。

CLI：
  python tools/kb_corpus/qa_http_recheck.py run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1:8091"
OUT_JSON = REPO / "research" / "reports" / "qa_http_recheck_20261102.json"
OUT_MD = REPO / "research" / "reports" / "qa_http_recheck_20261102.md"
WAVE38_JSON = REPO / "research" / "reports" / "qa_http_live_acceptance_20261101.json"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
SERIAL20 = 20
RETRIEVAL_TIMEOUT = 60
EXPECT_LATENCY_P95 = 0.2
EXPECT_RETRIEVAL_LIMIT = 300


class RecheckError(RuntimeError):
    """验收复跑异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RecheckError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_recheck", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return round(vals[0], 4)
    rank = (p / 100.0) * (len(vals) - 1)
    low = int(rank)
    high = min(low + 1, len(vals) - 1)
    frac = rank - low
    return round(vals[low] * (1 - frac) + vals[high] * frac, 4)


def summarize_serial20(results: list[dict]) -> dict:
    total = len(results)
    ok = [r for r in results if r.get("status") == 200 and r.get("ok")]
    lat = [float(r.get("elapsed_seconds") or 0.0) for r in ok]
    limits = sorted({int((r.get("rate_limit") or {}).get("limit"))
                     for r in results if (r.get("rate_limit") or {}).get("limit") is not None})
    return {"n": total, "http_200": len(ok),
            "errors": total - len(ok),
            "error_rate": round((total - len(ok)) / total, 4) if total else 0.0,
            "p50": percentile(lat, 50), "p95": percentile(lat, 95),
            "p99": percentile(lat, 99), "max": round(max(lat), 4) if lat else 0.0,
            "rate_limit_limits": limits,
            "results": results}


def run_serial20(base_url: str = BASE_URL, fetch_fn=None, question_pool: list[str] | None = None) -> dict:
    """retrieval 连续 20 次（cache=0，热缓存路径），记录延迟分位与限流 meta。"""
    acc = mod("qa_http_live_acceptance")
    fetch = fetch_fn or acc.http_get
    pool = question_pool or [q["question"] for q in
                             json.loads(Path(V3_BANK).read_text(encoding="utf-8"))["questions"]]
    results = []
    for i in range(SERIAL20):
        q = pool[i % len(pool)]
        url = f"{base_url}/api/kb/qa?" + urllib.parse.urlencode(
            {"q": q, "mode": "retrieval", "topk": "3", "cache": "0"})
        fetched = fetch(url, RETRIEVAL_TIMEOUT)
        # 兼容两种注入 fetch：qa_http_live_acceptance.http_get 返回 (status, payload, elapsed)；
        # 本地增强 fetch 可返回 4 元组并附 rate_limit meta。
        if isinstance(fetched, tuple) and len(fetched) >= 4:
            status, payload, elapsed, rate_limit = fetched[0], fetched[1], fetched[2], fetched[3]
        else:
            status, payload, elapsed = fetched[0], fetched[1], fetched[2]
            rate_limit = None
        env = payload if isinstance(payload, dict) else {}
        if rate_limit is None:
            rate_limit = (env.get("meta") or {}).get("rate_limit") or {}
        results.append({"status": status, "ok": bool(env.get("ok")),
                        "elapsed_seconds": elapsed, "rate_limit": rate_limit})
    return summarize_serial20(results)


# ---------------------------------------------------------------------------
# 前后对比 + 结论
# ---------------------------------------------------------------------------
def load_wave38(path: Path | str = WAVE38_JSON) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    s = data.get("summary") or {}
    c = data.get("conclusion") or {}
    return {"summary": s, "conclusion": c, "generated_at": data.get("generated_at"),
            "multi_prompt_versions": ((s.get("multi") or {}).get("prompt_versions")),
            "multi_guard_markers": ((s.get("multi") or {}).get("guard_markers")),
            "multi_retry_values": ((s.get("multi") or {}).get("citation_retry_values")),
            "retrieval_avg_elapsed_seconds": (s.get("retrieval") or {}).get("avg_elapsed_seconds")}


def build_recheck(acceptance: dict, serial20: dict, wave38: dict, config: dict | None = None) -> dict:
    config = config or {}
    summary = acceptance.get("summary") or {}
    conclusion = acceptance.get("conclusion") or {}
    totals = summary.get("totals") or {}
    multi = summary.get("multi") or {}
    boundary = summary.get("boundary") or {}
    checks = {
        "normal_200": totals.get("http_200") == totals.get("normal") and totals.get("errors") == 0,
        "boundary_400": boundary.get("passed") == boundary.get("n") and (boundary.get("n") or 0) >= 3,
        "multi_v4": "v4" in (multi.get("prompt_versions") or []),
        "multi_segment": "segment" in (multi.get("guard_markers") or []),
        "multi_retry": any(v is True for v in (multi.get("citation_retry_values") or [])),
        "retrieval_p95_lt_0_2": (serial20.get("p95") or 1e9) < EXPECT_LATENCY_P95,
        "rate_limit_300": EXPECT_RETRIEVAL_LIMIT in (serial20.get("rate_limit_limits") or []),
    }
    meets = all(checks.values())
    fixes, risks = [], []
    if not checks["normal_200"]:
        fixes.append(f"正常路径未全部 200：{totals.get('http_200')}/{totals.get('normal')}，errors={totals.get('errors')}")
    if not checks["boundary_400"]:
        fixes.append("边界参数未全部返回 400")
    if not checks["multi_v4"] or not checks["multi_segment"]:
        fixes.append("multi 灰度字段未生效（需 prompt=v4 / guard_marker=segment）")
    if not checks["multi_retry"]:
        fixes.append("citation_retry 未生效（multi meta.citation_retry 应含 true）")
    if not checks["retrieval_p95_lt_0_2"]:
        fixes.append(f"retrieval 热缓存 P95 {serial20.get('p95')}s 未达 <0.2s 目标")
    if not checks["rate_limit_300"]:
        fixes.append(f"retrieval 限流 meta.limit 非 300：{(serial20.get('rate_limit_limits'))}")
    w38 = wave38 or {}
    compare = {
        "wave38_generated_at": w38.get("generated_at"),
        "wave38_retrieval_avg_seconds": w38.get("retrieval_avg_elapsed_seconds"),
        "wave38_multi_prompt_versions": w38.get("multi_prompt_versions"),
        "wave38_multi_guard_markers": w38.get("multi_guard_markers"),
        "wave38_multi_retry": w38.get("multi_retry_values"),
        "wave38_can_go_live": (w38.get("conclusion") or {}).get("can_go_live"),
        "wave38_fixes_needed": (w38.get("conclusion") or {}).get("fixes_needed"),
        "this_retrieval_p95_seconds": serial20.get("p95"),
        "this_retrieval_avg_seconds": serial20.get("p50"),
        "this_multi_prompt_versions": multi.get("prompt_versions"),
        "this_multi_guard_markers": multi.get("guard_markers"),
        "this_multi_retry": multi.get("citation_retry_values"),
        "wave38_fixes_resolved": (not (w38.get("conclusion") or {}).get("fixes_needed")),
    }
    base_avg = w38.get("retrieval_avg_elapsed_seconds")
    if base_avg and serial20.get("p50"):
        compare["retrieval_speedup"] = round(base_avg / serial20["p50"], 1)
    if meets:
        verdict, can_go = ("验收复跑通过（8/8 200、3/3 400、multi v4+segment+retry 生效、"
                           "retrieval 热缓存 P95 <0.2s、限流 limit=300）→ 维持 go，可灰度放量", "go")
    elif checks["normal_200"] and checks["boundary_400"]:
        verdict, can_go = ("正常路径与边界通过，但灰度/性能指标有需修项 → 修复后再放量", "conditional")
    else:
        verdict, can_go = ("验收复跑未通过 → 暂不可放量", "no-go")
    risks += [
        "本次 multi 仅 3 次真实 LLM（控制成本），multi 通道容量/延迟需另行压测。",
        "retrieval 限流 300/分 按进程内窗口计数，多实例部署需一致性方案。",
        "热缓存 P50/P95 基于本机同机调用；生产跨网络/网关可能增加时延。",
        "single 通道（6/分）与 multi（6/分）限流未调整，高并发场景需逐通道评估。",
    ]
    scale_advice = {
        "recommended_gray_scope": "先内部灰度（并发 2–3，retrieval 放量至 ~200/分以内），观察 429 比例与 P95",
        "retrieval_limit": EXPECT_RETRIEVAL_LIMIT,
        "multi_limit": 6, "single_limit": 6,
        "next_steps": ["监控 429 比例与 P95（目标 P95 <0.2s、429 <5%）",
                       "multi/single 通道按调用量评估是否调高限流",
                       "外部放量前再跑一次 3–5 QPS 持续 5 分钟稳定性（避免高 LLM 成本）"],
    }
    return {"checks": checks, "meets": bool(meets),
            "can_go_live": can_go, "verdict": verdict,
            "fixes_needed": fixes, "risks": risks,
            "compare_wave38": compare, "scale_advice": scale_advice,
            "acceptance_conclusion": conclusion}


# ---------------------------------------------------------------------------
# 主流程 / 输出
# ---------------------------------------------------------------------------
def run_recheck(base_url: str = BASE_URL, acceptance_fn=None, fetch_fn=None,
                wave38_path: Path | str = WAVE38_JSON,
                questions: list[dict] | None = None) -> dict:
    started = time.monotonic()
    acc = mod("qa_http_live_acceptance")
    if acceptance_fn is None:
        def _uncached_fetch(url, timeout=30.0):
            # 复跑要求真实链路（不吃 API 缓存）：在 URL 上补 cache=0 后走验收脚本的 http_get
            if "cache=" not in url:
                url = url + ("&" if "?" in url else "?") + "cache=0"
            return acc.http_get(url, timeout)

        acceptance = acc.run_acceptance(base_url=base_url, fetch_fn=_uncached_fetch)
    else:
        acceptance = acceptance_fn(base_url=base_url)
    serial20 = run_serial20(base_url, fetch_fn=fetch_fn)
    wave38 = load_wave38(wave38_path)
    result = build_recheck(acceptance, serial20, wave38,
                           config={"base_url": base_url, "multi_llm_calls": 3})
    return {"schema_version": 1, "kind": "qa_http_recheck",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"base_url": base_url, "serial20_n": SERIAL20,
                       "expect_latency_p95": EXPECT_LATENCY_P95,
                       "expect_retrieval_limit": EXPECT_RETRIEVAL_LIMIT,
                       "multi_llm_calls": 3,
                       "note": "复用 qa_http_live_acceptance.run_acceptance 真实 HTTP（retrieval 3/single 2/multi 3+边界 3）；真 LLM multi 3 次"},
            "acceptance": acceptance, "serial20": serial20,
            "wave38": wave38, "recheck": result,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "disclaimer": "验收复跑记录仅供上线评审参考，不构成投资建议。"}


def render_markdown(payload: dict) -> str:
    rc = payload["recheck"]
    s = payload["acceptance"]["summary"]
    serial = payload["serial20"]
    cmp = rc["compare_wave38"]
    cfg = payload["config"]
    lines = [f"# 8091 问答验收复跑（限流 300/分 + 检索缓存优化后，{payload['generated_at'][:10]}）", "",
             f"> 真实 HTTP `{cfg['base_url']}/api/kb/qa` · retrieval 3 / single 2 / multi 3 + 边界 3 · "
             f"补 retrieval 热缓存连续 {cfg['serial20_n']} 次 · multi 真 LLM 3 次。", "",
             "## 结论", "",
             f"**{rc['verdict']}** —— 判定：`{rc['can_go_live']}`", ""]
    if rc["fixes_needed"]:
        lines += ["### 需修项", ""] + [f"- {x}" for x in rc["fixes_needed"]] + [""]
    lines += ["## 验收检查项", "",
              "| 检查 | 结果 |", "|---|---|",
              f"| 正常路径 8/8 200 | {'✅' if rc['checks']['normal_200'] else '❌'} |",
              f"| 边界 3/3 返回 400 | {'✅' if rc['checks']['boundary_400'] else '❌'} |",
              f"| multi prompt=v4 | {'✅' if rc['checks']['multi_v4'] else '❌'} |",
              f"| multi guard_marker=segment | {'✅' if rc['checks']['multi_segment'] else '❌'} |",
              f"| multi citation_retry=true | {'✅' if rc['checks']['multi_retry'] else '❌'} |",
              f"| retrieval 热缓存 P95 <0.2s | {'✅' if rc['checks']['retrieval_p95_lt_0_2'] else '❌'}（{serial['p95']}s） |",
              f"| retrieval 限流 meta.limit=300 | {'✅' if rc['checks']['rate_limit_300'] else '❌'}（{serial['rate_limit_limits']}） |", "",
              "## 与 wave38 验收对比", "",
              "| 指标 | wave38（2026-11-01） | 本次复跑（2026-11-02） |", "|---|---|---|",
              f"| retrieval 平均耗时 | {cmp['wave38_retrieval_avg_seconds']}s | **P50 {cmp['this_retrieval_avg_seconds']}s / P95 {cmp['this_retrieval_p95_seconds']}s** |",
              f"| multi prompt | {cmp['wave38_multi_prompt_versions']} | **{cmp['this_multi_prompt_versions']}** |",
              f"| multi guard marker | {cmp['wave38_multi_guard_markers']} | **{cmp['this_multi_guard_markers']}** |",
              f"| multi citation_retry | {cmp['wave38_multi_retry']} | **{cmp['this_multi_retry']}** |",
              f"| go-live 判定 | {cmp['wave38_can_go_live']} | **{rc['can_go_live']}** |",
              f"| 需修项 | {cmp['wave38_fixes_needed']} | **{rc['fixes_needed'] or '无'}** |", "",
              "## 热缓存 retrieval 基准（连续 20 次）", "",
              f"- P50 **{serial['p50']}s** / P95 **{serial['p95']}s** / P99 **{serial['p99']}s** / max {serial['max']}s；"
              f"错误率 {serial['error_rate']:.1%}；限流 meta limit={serial['rate_limit_limits']}",
              f"- 对比 wave38 平均值 {cmp['wave38_retrieval_avg_seconds']}s：提速约 **{cmp.get('retrieval_speedup', '—')}x**", "",
              "## 验收矩阵（本次）", "",
              "| 通道 | 请求 | 200 | 平均耗时 | 平均引用 | guard 通过 |", "|---|---|---|---|---|---|",
              f"| retrieval | {s['retrieval']['n']} | {s['retrieval']['http_200']} | {s['retrieval']['avg_elapsed_seconds']}s | — | — |",
              f"| single | {s['single']['n']} | {s['single']['http_200']} | {s['single']['avg_elapsed_seconds']}s | {s['single']['avg_citations']} | — |",
              f"| multi | {s['multi']['n']} | {s['multi']['http_200']} | {s['multi']['avg_elapsed_seconds']}s | {s['multi']['avg_citations']} | {s['multi']['guard_passed']} |", "",
              "## 放量建议", "",
              f"- {rc['scale_advice']['recommended_gray_scope']}",
              f"- retrieval 限流 {rc['scale_advice']['retrieval_limit']}/分；multi/single 各 {rc['scale_advice']['multi_limit']}/{rc['scale_advice']['single_limit']}/分（未调整）"]
    lines += [f"- 下一步：{x}" for x in rc["scale_advice"]["next_steps"]]
    lines += ["", "## 风险", ""]
    lines += [f"- 风险：{r}" for r in rc["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 总耗时 {payload['elapsed_seconds']}s"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON,
                  out_md: Path | str = OUT_MD) -> tuple[Path, Path]:
    out_json, out_md = Path(out_json), Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="8091 问答验收复跑（限流/性能优化后）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="真实 HTTP 复跑验收并输出 JSON+MD")
    run.add_argument("--base-url", default=BASE_URL)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_recheck(args.base_url)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except RecheckError as exc:
        print(f"[qa-http-recheck] FAILED: {exc}", file=sys.stderr)
        return 1
    rc = payload["recheck"]
    print(f"[qa-http-recheck] checks={rc['checks']}")
    print(f"[qa-http-recheck] serial20 p50={payload['serial20']['p50']}s p95={payload['serial20']['p95']}s "
          f"limits={payload['serial20']['rate_limit_limits']}")
    print(f"[qa-http-recheck] can_go_live={rc['can_go_live']} | {rc['verdict']}")
    print(f"[qa-http-recheck] json={paths[0]}")
    print(f"[qa-http-recheck] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
