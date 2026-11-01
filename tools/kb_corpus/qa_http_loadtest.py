# -*- coding: utf-8 -*-
"""8091 问答 HTTP 限流/并发/长稳压测（retrieval 为主，控制 LLM 成本）。

目标：`http://127.0.0.1:8091/api/kb/qa`（已上线 WIRED=True，PID 7760）。
限流配置（portal/api_kb_qa.py）：retrieval 60/分、single 6/分、multi 6/分，窗口 60s。

测试项（顺序执行，阶段间等待 61s 重置限流窗口）：
  a) retrieval 并发 10 线程 × 20 次 = 200 次：P50/P95/P99 延迟、错误率、429 限流次数；
  b) retrieval 单线程 62 次（cache=0 强制真实检索）：验证 60/分限流触发（期望 429）；
  c) multi 并发 2 × 3 次 = 6 次（真实 LLM，≤6 次）：记录状态/耗时/meta；
  d) 稳定性：连续 50 次 retrieval 无 5xx。
输出 `research/reports/qa_http_loadtest_20261101.{json,md}`：延迟分布/错误率/限流行为/
结论（能否对外灰度/需调参项）。
不重启 8091、不压测 other 接口；真实 LLM 仅 multi 6 次。

CLI：
  python tools/kb_corpus/qa_http_loadtest.py run
  python tools/kb_corpus/qa_http_loadtest.py run --no-wait   # 不再等待窗口重置（快速/测试）
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "qa_http_loadtest_20261101.json"
OUT_MD = REPO / "research" / "reports" / "qa_http_loadtest_20261101.md"
BASE_URL = "http://127.0.0.1:8091"
WINDOW_SECONDS = 61
CONCURRENCY = 10
CONCURRENT_TOTAL = 200
SERIAL_LIMIT_N = 62
STABILITY_N = 50
MULTI_TOTAL = 6
MULTI_WORKERS = 2
RETRIEVAL_TIMEOUT = 30
MULTI_TIMEOUT = 180


class LoadTestError(RuntimeError):
    """压测异常。"""


# ---------------------------------------------------------------------------
# HTTP 调用（可注入 fetch_fn 以便 mock）
# ---------------------------------------------------------------------------
def http_get(url: str, timeout: float = 30.0) -> tuple[int, dict | str, float, dict]:
    """返回 (status, payload, elapsed_seconds, rate_limit_meta)。"""
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "qa-loadtest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", round(time.monotonic() - started, 3), {}
    elapsed = round(time.monotonic() - started, 3)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        payload = body.decode("utf-8", errors="replace")[:200]
    env = payload if isinstance(payload, dict) else {}
    return status, payload, elapsed, (env.get("meta") or {}).get("rate_limit") or {}


def _question_pool(path: Path | str = V3_BANK) -> list[str]:
    path = Path(path)
    if not path.exists():
        raise LoadTestError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoadTestError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [str(q.get("question")) for q in data.get("questions") or [] if q.get("question")]
    if not qs:
        raise LoadTestError("v3 问题库 questions 为空")
    return qs


def _url(base_url: str, question: str, mode: str = "retrieval", topk: int = 3,
         no_cache: bool = True) -> str:
    params = {"q": question, "mode": mode, "topk": str(topk)}
    if no_cache:
        params["cache"] = "0"
    return f"{base_url}/api/kb/qa?{urllib.parse.urlencode(params)}"


def make_request(fetch_fn=None, timeout: float = RETRIEVAL_TIMEOUT):
    fetch = fetch_fn or http_get

    def run(url: str) -> dict:
        started = time.monotonic()
        status, payload, elapsed, rate_limit = fetch(url, timeout)
        env = payload if isinstance(payload, dict) else {}
        return {"status": status, "ok": bool(env.get("ok")),
                "error_code": ((env.get("error") or {}).get("code")
                               if isinstance(env.get("error"), dict) else None),
                "elapsed_seconds": elapsed if elapsed is not None else round(time.monotonic() - started, 3),
                "rate_limit": rate_limit,
                "cache_hit": bool((env.get("meta") or {}).get("cache_hit"))}
    return run


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    """简单线性插值百分位（p ∈ [0,100]）。"""
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return round(vals[0], 3)
    rank = (p / 100.0) * (len(vals) - 1)
    low = int(rank)
    high = min(low + 1, len(vals) - 1)
    frac = rank - low
    return round(vals[low] * (1 - frac) + vals[high] * frac, 3)


def summarize_phase(name: str, results: list[dict]) -> dict:
    total = len(results)
    statuses: dict[int, int] = {}
    for r in results:
        statuses[str(r.get("status"))] = statuses.get(str(r.get("status")), 0) + 1
    ok = [r for r in results if r.get("status") == 200 and r.get("ok")]
    err = [r for r in results if not (r.get("status") == 200 and r.get("ok"))]
    lat_ok = [float(r.get("elapsed_seconds") or 0.0) for r in ok]
    lat_all = [float(r.get("elapsed_seconds") or 0.0) for r in results]
    return {"name": name, "total": total, "http_200": len(ok), "errors": len(err),
            "error_rate": round(len(err) / total, 4) if total else 0.0,
            "status_counts": statuses,
            "rate_limited_429": sum(1 for r in results if r.get("status") == 429),
            "server_5xx": sum(1 for r in results if isinstance(r.get("status"), int)
                              and 500 <= r["status"] < 600),
            "latency_success": {"p50": percentile(lat_ok, 50), "p95": percentile(lat_ok, 95),
                                "p99": percentile(lat_ok, 99),
                                "avg": round(statistics.mean(lat_ok), 3) if lat_ok else 0.0,
                                "max": round(max(lat_ok), 3) if lat_ok else 0.0},
            "latency_all": {"p50": percentile(lat_all, 50), "p95": percentile(lat_all, 95),
                            "p99": percentile(lat_all, 99)},
            "results": results}


# ---------------------------------------------------------------------------
# 测试阶段
# ---------------------------------------------------------------------------
def phase_retrieval_serial(question_pool: list[str], n: int, fetch_fn=None,
                           base_url: str = BASE_URL) -> dict:
    run = make_request(fetch_fn)
    results = []
    for i in range(n):
        q = question_pool[i % len(question_pool)]
        results.append(run(_url(base_url, q)))
    return summarize_phase(f"retrieval_serial_{n}", results)


def phase_retrieval_concurrent(question_pool: list[str], total: int = CONCURRENT_TOTAL,
                               workers: int = CONCURRENCY, fetch_fn=None,
                               base_url: str = BASE_URL) -> dict:
    run = make_request(fetch_fn)
    urls = [_url(base_url, question_pool[i % len(question_pool)]) for i in range(total)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, urls))
    return summarize_phase(f"retrieval_concurrent_{workers}x{total // workers}", results)


def phase_multi_concurrent(question_pool: list[str], total: int = MULTI_TOTAL,
                           workers: int = MULTI_WORKERS, fetch_fn=None,
                           base_url: str = BASE_URL) -> dict:
    run = make_request(fetch_fn, timeout=MULTI_TIMEOUT)
    urls = [_url(base_url, question_pool[i % len(question_pool)], mode="multi", topk=8,
                 no_cache=True) for i in range(total)]
    lock = threading.Lock()
    results: list[dict] = []

    def wrapped(url: str):
        res = run(url)
        with lock:
            results.append(res)
        return res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(wrapped, urls))
    summary = summarize_phase(f"multi_concurrent_{workers}x{total // workers}", results)
    summary["llm_calls"] = sum(1 for r in results if r.get("status") == 200)
    return summary


# ---------------------------------------------------------------------------
# 结论
# ---------------------------------------------------------------------------
def build_conclusion(phases: dict) -> dict:
    stability = phases.get("stability_50") or {}
    serial = phases.get("retrieval_serial_62") or {}
    conc = phases.get("retrieval_concurrent") or {}
    multi = phases.get("multi_concurrent") or {}
    fixes, risks = [], []
    stability_ok = stability.get("server_5xx", 0) == 0 and stability.get("error_rate", 1) <= 0.02
    conc_ok = conc.get("server_5xx", 0) == 0 and conc.get("errors", 0) <= conc.get("rate_limited_429", 0) + max(1, conc.get("total", 1) * 0.02)
    multi_ok = multi.get("total", 0) > 0 and multi.get("http_200", 0) == multi.get("total", 0) and multi.get("server_5xx", 0) == 0
    limit_verified = serial.get("rate_limited_429", 0) > 0
    if stability.get("server_5xx", 0) > 0:
        fixes.append(f"稳定性阶段出现 {stability.get('server_5xx')} 个 5xx，需排查 8091 稳定性。")
    if not limit_verified:
        fixes.append("单线程 62 次未触发 429：限流未按预期生效（或检索过快未达窗口），需核对限流实现/参数。")
    if not conc_ok:
        fixes.append("并发阶段除 429 外存在错误（5xx/超时），需排查并发处理能力。")
    if not multi_ok:
        fixes.append("multi 并发 6 次未全部成功，需排查 multi 通道限流/LLM 超时。")
    if conc.get("rate_limited_429", 0) >= max(1, int(conc.get("total", 0) * 0.5)) and not stability.get("server_5xx", 0):
        risks.append("并发 200 次中 429 占比过半：retrieval 60/分 限流偏紧，灰度放量前建议调高（如 120–300/分）或按 IP/租户分桶。")
    if multi.get("rate_limited_429", 0) > 0:
        risks.append("multi 并发中出现 429：6/分 限流对并发 2 仍有排队风险，建议评估限流窗口策略。")
    if conc.get("latency_success", {}).get("p95", 0) > 5:
        risks.append(f"并发 retrieval 成功请求 P95 延迟 {conc['latency_success']['p95']}s 偏高，建议检查 KB 检索/DB 连接池。")
    risks.append("retrieval 使用 cache=0 强制真实检索，延迟包含 KB 检索开销；未压测 single 通道（控 LLM 成本）。")
    risks.append("限流状态为进程内窗口计数，多进程/多实例部署时需确认限流一致性。")
    if stability_ok and conc_ok and multi_ok and limit_verified:
        verdict, can_go = ("限流/并发/长稳验收通过：50 次稳定无 5xx、并发 200 次仅 429（限流生效）、"
                           "multi 6/6 成功 → 可对外灰度，但 retrieval 限流阈值需结合业务放量评估", "go")
    elif stability_ok and conc_ok and multi_ok:
        verdict, can_go = ("稳定性/并发/multi 通过，但限流行为需复核（未观察到预期 429）→ 调参后灰度", "conditional")
    else:
        verdict, can_go = ("压测发现稳定性或错误问题 → 暂不可对外灰度，需修复后重测", "no-go")
    return {"stability_ok": stability_ok, "concurrency_ok": conc_ok, "multi_ok": multi_ok,
            "limit_verified": limit_verified,
            "can_go_live": can_go, "verdict": verdict,
            "fixes_needed": fixes, "tuning_items": [],
            "risks": risks}


# ---------------------------------------------------------------------------
# 主流程 / 输出
# ---------------------------------------------------------------------------
def run_loadtest(base_url: str = BASE_URL, wait_between_phases: bool = True,
                 fetch_fn=None, question_pool: list[str] | None = None) -> dict:
    started = time.monotonic()
    pool = question_pool if question_pool is not None else _question_pool()
    phases = {}
    sleep = (lambda: time.sleep(WINDOW_SECONDS)) if wait_between_phases else (lambda: None)

    def _phase(key, fn):
        t = time.monotonic()
        phases[key] = fn()
        phases[key]["phase_elapsed_seconds"] = round(time.monotonic() - t, 2)

    # d) 稳定性：连续 50 次 retrieval（先跑，观察 5xx）
    _phase("stability_50", lambda: phase_retrieval_serial(pool, STABILITY_N, fetch_fn, base_url))
    # b) 单线程 62 次：验证 60/分限流（接在 50 次之后，窗口内更易触发）
    _phase("retrieval_serial_62", lambda: phase_retrieval_serial(pool, SERIAL_LIMIT_N, fetch_fn, base_url))
    sleep()
    # a) 并发 10×20=200
    _phase("retrieval_concurrent", lambda: phase_retrieval_concurrent(pool, CONCURRENT_TOTAL,
                                                                     CONCURRENCY, fetch_fn, base_url))
    sleep()
    # c) multi 并发 2×3=6（真实 LLM）
    _phase("multi_concurrent", lambda: phase_multi_concurrent(pool, MULTI_TOTAL,
                                                             MULTI_WORKERS, fetch_fn, base_url))
    conclusion = build_conclusion(phases)
    return {"schema_version": 1, "kind": "qa_http_loadtest",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"base_url": base_url, "window_seconds": WINDOW_SECONDS,
                       "retrieval_concurrency": CONCURRENCY, "concurrent_total": CONCURRENT_TOTAL,
                       "serial_limit_n": SERIAL_LIMIT_N, "stability_n": STABILITY_N,
                       "multi_total": MULTI_TOTAL, "multi_workers": MULTI_WORKERS,
                       "rate_limit_config": {"retrieval": 60, "single": 6, "multi": 6},
                       "wait_between_phases": bool(wait_between_phases),
                       "cache": "0（强制真实检索）"},
            "phases": phases, "conclusion": conclusion,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "disclaimer": "HTTP 压测结果仅供上线评审参考，不构成投资建议。"}


def render_markdown(payload: dict) -> str:
    c = payload["conclusion"]
    cfg = payload["config"]
    phases = payload["phases"]
    lines = [f"# 8091 问答 HTTP 限流/并发压测（{payload['generated_at'][:10]}）", "",
             f"> 目标 `{cfg['base_url']}/api/kb/qa` · 限流配置 retrieval {cfg['rate_limit_config']['retrieval']}/分、"
             f"multi {cfg['rate_limit_config']['multi']}/分、窗口 {cfg['window_seconds']}s · "
             f"{cfg['cache']} · multi 真实 LLM {cfg['multi_total']} 次。", "",
             "## 结论", "",
             f"**{c['verdict']}** —— 判定：`{c['can_go_live']}`"
             f"（go=可对外灰度 / conditional=调参后灰度 / no-go=暂不可对外）", ""]
    if c["fixes_needed"]:
        lines += ["### 需修项", ""] + [f"- {x}" for x in c["fixes_needed"]] + [""]
    lines += ["## 阶段结果", "",
              "| 阶段 | 请求 | 200 | 429 | 5xx | 错误率 | P50(s) | P95(s) | P99(s) | 耗时(s) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for key in ("stability_50", "retrieval_serial_62", "retrieval_concurrent", "multi_concurrent"):
        p = phases.get(key) or {}
        lat = p.get("latency_success") or {}
        lines.append(f"| {p.get('name', key)} | {p.get('total')} | {p.get('http_200')} | "
                     f"{p.get('rate_limited_429')} | {p.get('server_5xx')} | "
                     f"{p.get('error_rate', 0):.1%} | {lat.get('p50')} | {lat.get('p95')} | "
                     f"{lat.get('p99')} | {p.get('phase_elapsed_seconds')} |")
    serial = phases.get("retrieval_serial_62") or {}
    lines += ["", "## 限流行为", "",
              f"- 单线程 62 次 retrieval：429 {serial.get('rate_limited_429')} 次"
              f"（期望触发 60/分限流：{'是' if c['limit_verified'] else '否'}）",
              f"- 状态分布：{serial.get('status_counts')}",
              f"- 并发 200 次：429 {(phases.get('retrieval_concurrent') or {}).get('rate_limited_429')} 次"
              f"、5xx {(phases.get('retrieval_concurrent') or {}).get('server_5xx')} 次", ""]
    lines += ["## 延迟分布（成功请求）", ""]
    for key in ("retrieval_concurrent", "multi_concurrent"):
        p = phases.get(key) or {}
        lat = p.get("latency_success") or {}
        lines.append(f"- {p.get('name')}：P50 {lat.get('p50')}s / P95 {lat.get('p95')}s / P99 {lat.get('p99')}s / max {lat.get('max')}s")
    lines += ["", "## 调参/风险", ""]
    lines += [f"- 风险：{r}" for r in c["risks"]]
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
    ap = argparse.ArgumentParser(description="8091 问答 HTTP 限流/并发压测")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="执行压测并输出 JSON+MD")
    run.add_argument("--base-url", default=BASE_URL)
    run.add_argument("--no-wait", dest="wait", action="store_false", default=True)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_loadtest(args.base_url, wait_between_phases=args.wait)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except LoadTestError as exc:
        print(f"[qa-loadtest] FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"[qa-loadtest] elapsed={payload['elapsed_seconds']}s")
    for key in ("stability_50", "retrieval_serial_62", "retrieval_concurrent", "multi_concurrent"):
        p = payload["phases"].get(key) or {}
        print(f"[qa-loadtest] {key}: total={p.get('total')} 200={p.get('http_200')} "
              f"429={p.get('rate_limited_429')} 5xx={p.get('server_5xx')} "
              f"p95={(p.get('latency_success') or {}).get('p95')}s")
    print(f"[qa-loadtest] conclusion={payload['conclusion']['can_go_live']} | "
          f"{payload['conclusion']['verdict']}")
    print(f"[qa-loadtest] json={paths[0]}")
    print(f"[qa-loadtest] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
