# -*- coding: utf-8 -*-
"""8091 问答 HTTP 端到端上线验收（真实 HTTP，不再进程内 mock）。

对已上线的 `http://127.0.0.1:8091/api/kb/qa`（WIRED=True，PID 7760）做真实验收：

  ① 正常路径：
     - retrieval × 3（只读检索，不调 LLM）；
     - single × 2（单单元生成，真实 LLM）；
     - multi × 3（**默认 prompt=v4 + guard_marker=segment + citation_retry=1**，真实 LLM 串行，
       单条客户端超时 180s）；
  ② 记录：HTTP 状态、耗时、meta（mode/k/clusters/llm_called/guard/prompt_version/
     guard_marker/citation_retry/citation_retry_info）、答案长度/引用数、guard 结果、是否 fallback、错误码；
  ③ 边界：topk=0 → 400、q 空 → 400、非法 mode → 400；
  ④ 输出 `research/reports/qa_http_live_acceptance_20261101.{json,md}`：
     逐条记录 + 统计 + 结论（能否对外 / 需修项 / 风险）。
  不重启 8091、不压测。

CLI：
  python tools/kb_corpus/qa_http_live_acceptance.py run
  python tools/kb_corpus/qa_http_live_acceptance.py run --multi-n 2   # LLM 慢时减少
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "qa_http_live_acceptance_20261101.json"
OUT_MD = REPO / "research" / "reports" / "qa_http_live_acceptance_20261101.md"
BASE_URL = "http://127.0.0.1:8091"
MULTI_TIMEOUT = 180
RETRIEVAL_TIMEOUT = 40
SINGLE_TIMEOUT = 120


class AcceptanceError(RuntimeError):
    """HTTP 验收异常。"""


# ---------------------------------------------------------------------------
# 选样（v3 问题库：3 条 retrieval + 2 条 single + 3 条 multi，多主题）
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = V3_BANK) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise AcceptanceError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise AcceptanceError("v3 问题库 questions 为空")
    return qs


def pick_questions(all_qs: list[dict], retrieval_n: int = 3, single_n: int = 2,
                   multi_n: int = 3) -> dict:
    """按主题轮转选题（不重复），返回 {"retrieval": [...], "single": [...], "multi": [...]}"""
    by_theme: dict[str, list[dict]] = {}
    for q in all_qs:
        by_theme.setdefault(str(q.get("theme") or "其他"), []).append(q)
    themes = list(by_theme)
    picked = {"retrieval": [], "single": [], "multi": []}
    idx = 0
    for mode, n in (("retrieval", retrieval_n), ("single", single_n), ("multi", multi_n)):
        while len(picked[mode]) < n and themes:
            theme = themes[idx % len(themes)]
            idx += 1
            pool = by_theme.get(theme) or []
            if pool:
                picked[mode].append(pool.pop(0))
            if not any(by_theme.values()):
                break
    return picked


# ---------------------------------------------------------------------------
# HTTP 调用（可注入 http_get 以便测试）
# ---------------------------------------------------------------------------
def http_get(url: str, timeout: float = 30.0) -> tuple[int, dict | str, float]:
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "qa-http-acceptance/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", round(time.monotonic() - started, 2)
    elapsed = round(time.monotonic() - started, 2)
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        parsed = body.decode("utf-8", errors="replace")[:500]
    return status, parsed, elapsed


def build_query(params: dict) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def _call(base_url: str, params: dict, timeout: float, fetch_fn=None) -> dict:
    url = f"{base_url}/api/kb/qa?{build_query(params)}"
    fetch = fetch_fn or http_get
    status, payload, elapsed = fetch(url, timeout)
    env = payload if isinstance(payload, dict) else {}
    meta = env.get("meta") or {}
    data = env.get("data") or {}
    guard = meta.get("guard") or {}
    citations = data.get("citations") or []
    return {"url": url, "params": params, "status": status, "ok": bool(env.get("ok")),
            "error": env.get("error"), "elapsed_seconds": elapsed,
            "meta": {k: meta.get(k) for k in
                     ("mode", "k", "clusters", "topk", "cache_hit", "llm_called",
                      "prompt_version", "guard_marker", "citation_retry", "citation_retry_info")},
            "guard": guard,
            "data": {"answer_length": len(str(data.get("answer") or "")),
                     "citations": len(citations),
                     "refusal": bool(data.get("refusal")),
                     "fallback": bool(data.get("fallback")),
                     "confidence": (data.get("confidence") or {}).get("level")},
            "raw_error": None if isinstance(payload, dict) else str(payload)[:300]}


# ---------------------------------------------------------------------------
# 验收用例
# ---------------------------------------------------------------------------
def run_case_matrix(base_url: str, picked: dict, fetch_fn=None,
                    multi_timeout: float = MULTI_TIMEOUT) -> dict:
    cases = []
    for q in picked.get("retrieval") or []:
        cases.append(_call(base_url, {"q": q["question"], "mode": "retrieval", "topk": "3"},
                           RETRIEVAL_TIMEOUT, fetch_fn))
    for q in picked.get("single") or []:
        cases.append(_call(base_url, {"q": q["question"], "mode": "single", "topk": "5"},
                           SINGLE_TIMEOUT, fetch_fn))
    for q in picked.get("multi") or []:
        cases.append(_call(base_url, {"q": q["question"], "mode": "multi", "topk": "8"},
                           multi_timeout, fetch_fn))
    return {"normal": cases}


def run_boundary_checks(base_url: str, fetch_fn=None) -> list[dict]:
    boundary = [
        ("topk_zero", {"q": "M9 级覆铜板对 PCB 的价值量有什么影响？", "mode": "retrieval",
                       "topk": "0"}, 400),
        ("empty_q", {"mode": "retrieval"}, 400),
        ("invalid_mode", {"q": "任意问题", "mode": "bogus"}, 400),
    ]
    out = []
    for name, params, expected in boundary:
        rec = _call(base_url, params, 30.0, fetch_fn)
        rec["case"] = name
        rec["expected_status"] = expected
        rec["pass"] = rec["status"] == expected
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 统计 / 结论
# ---------------------------------------------------------------------------
def summarize(normal: list[dict], boundary: list[dict]) -> dict:
    def bucket(mode: str) -> list[dict]:
        return [c for c in normal if (c.get("params") or {}).get("mode") == mode]

    retr = bucket("retrieval")
    single = bucket("single")
    multi = bucket("multi")
    multi_ok = [c for c in multi if c["status"] == 200 and c["ok"]]
    multi_meta = [c.get("meta") or {} for c in multi_ok]
    multi_guard = [c.get("guard") or {} for c in multi]
    multi_prompt = sorted({str(m.get("prompt_version")) for m in multi_meta if m.get("prompt_version")})
    multi_marker = sorted({str(m.get("guard_marker")) for m in multi_meta if m.get("guard_marker")})
    retry_flags = [c.get("meta", {}).get("citation_retry") for c in multi]
    llm_calls = sum(1 for c in normal if (c.get("meta") or {}).get("llm_called"))
    return {
        "totals": {"normal": len(normal), "boundary": len(boundary),
                   "http_200": sum(1 for c in normal if c["status"] == 200),
                   "errors": sum(1 for c in normal if c["status"] != 200 or not c["ok"])},
        "retrieval": {"n": len(retr), "http_200": sum(1 for c in retr if c["status"] == 200),
                      "llm_called": any((c.get("meta") or {}).get("llm_called") for c in retr),
                      "avg_elapsed_seconds": _avg(c["elapsed_seconds"] for c in retr)},
        "single": {"n": len(single), "http_200": sum(1 for c in single if c["status"] == 200),
                   "llm_called": sum(1 for c in single if (c.get("meta") or {}).get("llm_called")),
                   "avg_elapsed_seconds": _avg(c["elapsed_seconds"] for c in single),
                   "avg_citations": _avg((c["data"] or {}).get("citations") or 0 for c in single)},
        "multi": {"n": len(multi), "http_200": len(multi_ok),
                  "llm_called": sum(1 for c in multi if (c.get("meta") or {}).get("llm_called")),
                  "avg_elapsed_seconds": _avg(c["elapsed_seconds"] for c in multi),
                  "avg_citations": _avg((c["data"] or {}).get("citations") or 0 for c in multi),
                  "guard_passed": sum(1 for g in multi_guard if g.get("passed") is True),
                  "fallback": sum(1 for c in multi if (c["data"] or {}).get("fallback")),
                  "prompt_versions": multi_prompt, "guard_markers": multi_marker,
                  "citation_retry_values": retry_flags},
        "boundary": {"n": len(boundary), "passed": sum(1 for b in boundary if b["pass"]),
                     "cases": [{"case": b["case"], "status": b["status"],
                                "expected": b["expected_status"], "pass": b["pass"]} for b in boundary]},
        "llm_calls": llm_calls,
    }


def _avg(values) -> float:
    vals = [float(v) for v in values]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def build_conclusion(summary: dict) -> dict:
    t = summary["totals"]
    fixes, risks = [], []
    normal_pass = (t["errors"] == 0 and t["http_200"] == t["normal"])
    retr_pass = summary["retrieval"]["http_200"] == summary["retrieval"]["n"]
    single_pass = summary["single"]["http_200"] == summary["single"]["n"]
    multi_pass = summary["multi"]["http_200"] == summary["multi"]["n"]
    boundary_pass = summary["boundary"]["passed"] == summary["boundary"]["n"]
    if not retr_pass:
        fixes.append("retrieval 通道存在非 200 响应，检查 8091 服务/检索依赖。")
    if not single_pass:
        fixes.append("single 生成通道存在非 200 响应，检查 LLM/超时/退避逻辑。")
    if not multi_pass:
        fixes.append("multi 生成通道存在非 200 响应，检查 LLM 超时/守卫兼容。")
    if not boundary_pass:
        fixes.append("边界参数（topk=0/q 空/非法 mode）未全部返回 400，需修参数校验。")
    m = summary["multi"]
    if "v4" not in (m.get("prompt_versions") or []):
        fixes.append("multi 通道 meta.prompt_version 未显示 v4，灰度可能未生效（需查 api 默认）。")
    if "segment" not in (m.get("guard_markers") or []):
        fixes.append("multi 通道 meta.guard_marker 未显示 segment，守卫分段模式可能未生效。")
    if not any(v is True for v in (m.get("citation_retry_values") or [])):
        risks.append("multi meta.citation_retry 未返回 true，引用自检重试可能未生效（灰度确认）。")
    if summary["retrieval"]["llm_called"]:
        risks.append("retrieval 通道 llm_called=true，存在误调 LLM 的成本风险。")
    if not normal_pass:
        risks.append("存在非 200 的正常路径请求，端到端链路尚不完整。")
    normal_paths_pass = retr_pass and single_pass and multi_pass
    gray_ok = not any(("v4" in x) or ("segment" in x) for x in fixes)
    if normal_paths_pass and boundary_pass and gray_ok and t["errors"] == 0:
        verdict, can_go = ("8091 /api/kb/qa 端到端验收通过（retrieval/single/multi 正常、边界 400 正确、"
                           "multi 走 v4+segment+retry）→ 可对外灰度发布", "go")
    elif normal_paths_pass and not t["errors"]:
        verdict, can_go = ("正常路径可用，但存在需修项（边界校验/灰度字段）→ 修复后重新验收再对外", "conditional")
    else:
        verdict, can_go = ("端到端验收未通过 → 暂不可对外", "no-go")
    risks.append("本次为低频串行验收（retrieval 3/single 2/multi 3），未做限流/并发/长稳压测。")
    risks.append("multi 含真实 LLM，单条耗时可受模型负载影响；本验收未触发限流。")
    return {"normal_pass": normal_pass, "retrieval_pass": retr_pass, "single_pass": single_pass,
            "multi_pass": multi_pass, "boundary_pass": boundary_pass,
            "can_go_live": can_go, "verdict": verdict,
            "fixes_needed": fixes, "risks": risks}


# ---------------------------------------------------------------------------
# 主流程 / 输出
# ---------------------------------------------------------------------------
def run_acceptance(base_url: str = BASE_URL, retrieval_n: int = 3, single_n: int = 2,
                   multi_n: int = 3, fetch_fn=None, questions: list[dict] | None = None,
                   clock=time.monotonic) -> dict:
    started = clock()
    all_qs = questions if questions is not None else load_questions()
    picked = pick_questions(all_qs, retrieval_n, single_n, multi_n)
    matrix = run_case_matrix(base_url, picked, fetch_fn=fetch_fn)
    boundary = run_boundary_checks(base_url, fetch_fn=fetch_fn)
    summary = summarize(matrix["normal"], boundary)
    conclusion = build_conclusion(summary)
    return {"schema_version": 1, "kind": "qa_http_live_acceptance",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"base_url": base_url, "retrieval_n": len(picked["retrieval"]),
                       "single_n": len(picked["single"]), "multi_n": len(picked["multi"]),
                       "multi_timeout_seconds": MULTI_TIMEOUT, "wired": True,
                       "gotcha": "不重启 8091、不压测；multi 默认 v4+segment+citation_retry"},
            "cases": {"normal": matrix["normal"], "boundary": boundary},
            "summary": summary, "conclusion": conclusion,
            "elapsed_seconds": round(clock() - started, 2),
            "disclaimer": "HTTP 端到端验收记录仅供上线评审参考，不构成投资建议。"}


def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    c = payload["conclusion"]
    cfg = payload["config"]
    lines = [f"# 8091 问答 HTTP 端到端上线验收（{payload['generated_at'][:10]}）", "",
             f"> 真实 HTTP：`{cfg['base_url']}/api/kb/qa` · retrieval {cfg['retrieval_n']} / single "
             f"{cfg['single_n']} / multi {cfg['multi_n']}（multi 默认 v4+segment+citation_retry，"
             f"真实 LLM 串行，单条 180s）· 边界 3 例。不重启 8091、不压测。", "",
             "## 结论", "",
             f"**{c['verdict']}** —— 判定：`{c['can_go_live']}`"
             f"（go=可对外灰度 / conditional=修复后 / no-go=不可对外）", ""]
    if c["fixes_needed"]:
        lines += ["### 需修项", ""] + [f"- {x}" for x in c["fixes_needed"]] + [""]
    lines += ["## 验收矩阵", "",
              "| 通道 | 请求数 | HTTP 200 | LLM 调用 | 平均耗时 | 平均引用 | guard 通过 | fallback |",
              "|---|---|---|---|---|---|---|---|",
              f"| retrieval | {s['retrieval']['n']} | {s['retrieval']['http_200']} | "
              f"{'是' if s['retrieval']['llm_called'] else '否'} | {s['retrieval']['avg_elapsed_seconds']}s | — | — | — |",
              f"| single | {s['single']['n']} | {s['single']['http_200']} | {s['single']['llm_called']} | "
              f"{s['single']['avg_elapsed_seconds']}s | {s['single']['avg_citations']} | — | — |",
              f"| multi | {s['multi']['n']} | {s['multi']['http_200']} | {s['multi']['llm_called']} | "
              f"{s['multi']['avg_elapsed_seconds']}s | {s['multi']['avg_citations']} | "
              f"{s['multi']['guard_passed']} | {s['multi']['fallback']} |", "",
              "## multi 灰度字段校验", "",
              f"- prompt_versions：{s['multi']['prompt_versions']}",
              f"- guard_markers：{s['multi']['guard_markers']}",
              f"- citation_retry：{s['multi']['citation_retry_values']}", "",
              "## 边界参数（期望 400）", "",
              "| 用例 | 状态 | 期望 | 结果 |", "|---|---|---|---|"]
    for b in s["boundary"]["cases"]:
        lines.append(f"| {b['case']} | {b['status']} | {b['expected']} | {'✅' if b['pass'] else '❌'} |")
    lines += ["", "## 逐条记录", "",
              "| # | mode | 问题 | 状态 | 耗时 | 引用 | prompt | marker | retry | guard passed | fallback |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, case in enumerate(payload["cases"]["normal"], 1):
        params = case.get("params") or {}
        meta = case.get("meta") or {}
        guard = case.get("guard") or {}
        data = case.get("data") or {}
        lines.append(f"| {i} | {params.get('mode')} | {str(params.get('q'))[:32]} | {case['status']} | "
                     f"{case['elapsed_seconds']}s | {data.get('citations')} | {meta.get('prompt_version')} | "
                     f"{meta.get('guard_marker')} | {meta.get('citation_retry')} | {guard.get('passed')} | "
                     f"{data.get('fallback')} |")
    lines += ["", "## 风险与说明", ""]
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
    ap = argparse.ArgumentParser(description="8091 问答 HTTP 端到端上线验收")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="真实 HTTP 验收并输出 JSON+MD")
    run.add_argument("--base-url", default=BASE_URL)
    run.add_argument("--retrieval-n", type=int, default=3)
    run.add_argument("--single-n", type=int, default=2)
    run.add_argument("--multi-n", type=int, default=3)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_acceptance(args.base_url, args.retrieval_n, args.single_n, args.multi_n)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except AcceptanceError as exc:
        print(f"[qa-http-acceptance] FAILED: {exc}", file=sys.stderr)
        return 1
    s, c = payload["summary"], payload["conclusion"]
    print(f"[qa-http-acceptance] normal={s['totals']['normal']} http200={s['totals']['http_200']} "
          f"errors={s['totals']['errors']} boundary={s['boundary']['passed']}/{s['boundary']['n']}")
    print(f"[qa-http-acceptance] multi prompt={s['multi']['prompt_versions']} "
          f"markers={s['multi']['guard_markers']} retry={s['multi']['citation_retry_values']}")
    print(f"[qa-http-acceptance] conclusion={c['can_go_live']} | {c['verdict']}")
    if c["fixes_needed"]:
        print(f"[qa-http-acceptance] fixes={c['fixes_needed']}")
    print(f"[qa-http-acceptance] json={paths[0]}")
    print(f"[qa-http-acceptance] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
