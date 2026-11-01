# -*- coding: utf-8 -*-
"""问答链路端到端验收（CLI / HTTP 预接线 / 门户）：三段验收 + 上线结论。

验收内容：
  ① CLI 端到端：v3 问题库选 N 条（默认 10），真实调用
     `kb_qa.ask(mode="multi", guard=True, on_guard_fail="fallback")`，记录每条答案长度、
     引用数、guard 字段、耗时、状态（success/no_evidence/fallback）与质量（引用可解析率/支撑率/推断比例）；
  ② HTTP 预接线（**进程内**，不启动服务）：调用 `portal/api_kb_qa.handle` 模拟
     retrieval / single / multi / guard=0 / guard=2 五种请求，记录状态码与 meta（LLM 用注入 mock，不真调）；
  ③ 门户核查：`/kb_reports.html` 与 `/static/kb_qa_samples.json` HTTP 200 + 内容标记；
  ④ 汇总：CLI/HTTP/门户三段结论、是否可上线（go / conditional / no-go）、需修项、风险。

输出：`research/reports/qa_acceptance_20261101.{json,md}`。
约束：不启动/重启 8091；不修改 api_kb_qa/guard 代码；HTTP 用例用注入 mock，无额外 LLM 成本。

CLI：
  python tools/kb_corpus/qa_acceptance.py run --n 10
  python tools/kb_corpus/qa_acceptance.py run --n 8 --deadline 300   # LLM 慢时减条
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
API_PATH = REPO / "portal" / "api_kb_qa.py"
OUT_JSON = REPO / "research" / "reports" / "qa_acceptance_20261101.json"
OUT_MD = REPO / "research" / "reports" / "qa_acceptance_20261101.md"
DEFAULT_N = 10
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 540
PORTAL = "http://127.0.0.1:8734"


class AcceptanceError(RuntimeError):
    """验收流程异常。"""


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_acceptance", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AcceptanceError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# ① CLI 端到端
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = V3_BANK, n: int = DEFAULT_N) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise AcceptanceError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    questions = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not questions:
        raise AcceptanceError(f"v3 问题库 questions 为空：{path}")
    return questions[:max(1, int(n))]


def _classify_status(payload: dict) -> str:
    answer = str(payload.get("answer") or "")
    confidence = payload.get("confidence") or {}
    if payload.get("fallback"):
        return "fallback"
    if payload.get("refusal") or confidence.get("level") == "none" or "没有足够依据" in answer:
        return "no_evidence"
    return "success" if answer else "no_evidence"


def run_cli_checks(questions: list[dict], ask_fn=None, model: str = DEFAULT_MODEL,
                   timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                   clock=time.monotonic) -> dict:
    """真实 CLI 链路（可注入 ask_fn mock）；逐条记录 + 汇总。"""
    started = clock()
    fn = ask_fn or (lambda q, **kw: _load_kb_qa().ask(q, **kw))
    rows = []
    skipped = 0
    for q in questions:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        item_started = clock()
        try:
            payload = fn(q["question"], topk=8, model=model, timeout=max(5.0, min(timeout, remaining)),
                         mode="multi", guard=True, on_guard_fail="fallback")
            error = None
        except Exception as exc:  # noqa: BLE001
            payload = {"answer": None, "citations": [], "confidence": {"level": "none"},
                       "fallback": True, "refusal": False, "mode": "multi"}
            error = f"{type(exc).__name__}: {exc}"
        status = _classify_status(payload)
        rows.append({
            "id": q.get("id"), "question": q["question"], "category": q.get("category"),
            "answer_length": len(str(payload.get("answer") or "")),
            "answer_preview": str(payload.get("answer") or "")[:160],
            "citations": len(payload.get("citations") or []),
            "mode": payload.get("mode", "multi"),
            "status": status,
            "guard_enabled": payload.get("guard_enabled"),
            "guard_passed": payload.get("guard_passed"),
            "guard_action": payload.get("guard_action"),
            "guard_issues": payload.get("guard_issues") or [],
            "citation_resolvable_rate": payload.get("citation_resolvable_rate"),
            "support_rate": payload.get("support_rate"),
            "inference_ratio": payload.get("inference_ratio"),
            "distinct_cited_units": payload.get("distinct_cited_units"),
            "fallback": bool(payload.get("fallback")),
            "elapsed_seconds": round(clock() - item_started, 2),
            "error": error,
        })
    total = len(rows)
    summary = {
        "requested": len(questions), "actual": total, "skipped": skipped,
        "success": sum(1 for r in rows if r["status"] == "success"),
        "no_evidence": sum(1 for r in rows if r["status"] == "no_evidence"),
        "fallback": sum(1 for r in rows if r["status"] == "fallback"),
        "guard_passed": sum(1 for r in rows if r.get("guard_passed") is True),
        "guard_failed": sum(1 for r in rows if r.get("guard_passed") is False),
        "avg_citations": round(sum(r["citations"] for r in rows) / total, 2) if total else 0,
        "avg_elapsed_seconds": round(sum(r["elapsed_seconds"] for r in rows) / total, 2) if total else 0,
        "errors": [r["error"] for r in rows if r.get("error")],
        "elapsed_seconds": round(clock() - started, 2),
    }
    summary["pass"] = (total > 0 and not summary["errors"]
                       and all(r["mode"] == "multi" for r in rows)
                       and all(r.get("guard_enabled") for r in rows))
    return {"rows": rows, "summary": summary}


# ---------------------------------------------------------------------------
# ② HTTP 预接线进程内验证
# ---------------------------------------------------------------------------
def _load_api_module():
    path = API_PATH
    if not path.exists():
        raise AcceptanceError(f"api_kb_qa.py 不存在：{path}")
    spec = importlib.util.spec_from_file_location("api_kb_qa_for_acceptance", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AcceptanceError(f"无法加载 api_kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_ask(question, **kw):
    return {"question": question, "answer": "HTTP 验收 mock 答案 [u1] p.1",
            "citations": [{"unit_id": "u1", "page": 1, "text": "mock"}],
            "confidence": {"level": "high"}, "retrieved": [{"unit_id": "u1"}],
            "citation_check": {}, "fallback": False, "refusal": False,
            "mode": "single", "elapsed_seconds": 0.01}


def _fake_multi(question, **kw):
    return {"question": question, "answer": "【直接事实】HTTP 验收 mock multi [u1] p.1",
            "citations": [{"unit_id": "u1", "page": 1, "text": "mock"}],
            "confidence": {"level": "high", "reasons": ["1 条可核验引用"]},
            "retrieved": [{"unit_id": "u1"}], "citation_check": {},
            "fallback": False, "refusal": False, "cluster_count": 2,
            "clusters": [{"label": "c1", "units": 2}],
            "direct_fact_markers": 1, "inference_markers": 1,
            "guard_passed": True, "guard_issues": [],
            "citation_valid": ["u1"], "citation_invalid": [],
            "citation_resolvable_rate": 1.0, "distinct_cited_units": 1,
            "inference_ratio": 0.5,
            "generated_at": "2026-11-01T00:00:00+08:00", "elapsed_seconds": 0.01}


class _FakeGuard:
    def load_kb_ids(self):
        return ({"u1"}, set())

    def apply_guard(self, result, unit_ids, book_ids, on_fail="fallback"):
        out = dict(result)
        out.update({"guard_passed": True, "guard_issues": [], "guard_action": "pass",
                    "citation_valid": ["u1"], "citation_invalid": [],
                    "citation_resolvable_rate": 1.0, "distinct_cited_units": 1,
                    "inference_ratio": 0.5})
        return out


def run_http_checks(api=None) -> dict:
    """进程内调用 handle 模拟 5 类请求；不启动服务、不真调 LLM（注入 mock）。"""
    api = api or _load_api_module()
    q = {"q": ["M9 级覆铜板对 PCB 价值量的影响？"], "topk": ["3"], "cache": ["0"]}
    cases = []

    def call(name, params, **kw):
        merged = {k: list(v) for k, v in q.items()}
        for k, v in params.items():
            merged[k] = [v]
        status, env = api.handle("GET", "/api/kb/qa", merged, None, **kw)
        meta = env.get("meta") or {}
        cases.append({"case": name, "params": params, "status": status,
                      "ok": bool(env.get("ok")), "error": env.get("error"),
                      "meta": {k: meta.get(k) for k in
                               ("mode", "k", "clusters", "llm_called", "cache_hit")} | {
                          "guard": meta.get("guard")}})
        return status, env, meta

    # 1) retrieval（只读检索，不调 LLM；真实 kb_qa retrieval）
    try:
        call("retrieval", {"mode": "retrieval"})
    except Exception as exc:  # noqa: BLE001
        cases.append({"case": "retrieval", "status": 0, "ok": False,
                      "error": f"{type(exc).__name__}: {exc}", "meta": {}})
    # 2) single（注入 ask_fn mock）
    call("single", {"mode": "single"}, ask_fn=_fake_ask)
    # 3) multi（注入 multi_fn + guard_impl mock）
    call("multi", {"mode": "multi"}, multi_fn=_fake_multi, guard_impl=_FakeGuard())
    # 4) guard=0（multi 跳过守卫）
    call("multi_guard0", {"mode": "multi", "guard": "0"}, multi_fn=_fake_multi,
         guard_impl=_FakeGuard())
    # 5) guard=2（非法参数，应 400）
    call("guard_invalid", {"mode": "multi", "guard": "2"}, multi_fn=_fake_multi,
         guard_impl=_FakeGuard())

    expected = {"retrieval": 200, "single": 200, "multi": 200, "multi_guard0": 200,
                "guard_invalid": 400}
    checks = [{"case": c["case"], "status": c["status"],
               "expected": expected.get(c["case"]), "pass": c["status"] == expected.get(c["case"]),
               "error": c.get("error")} for c in cases]
    summary = {"cases": len(cases), "passed": sum(1 for x in checks if x["pass"]),
               "failed": sum(1 for x in checks if not x["pass"]),
               "wired": bool(getattr(api, "WIRED", False)),
               "checks": checks,
               "pass": all(x["pass"] for x in checks)}
    return {"rows": cases, "summary": summary}


# ---------------------------------------------------------------------------
# ③ 门户核查
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "qa-acceptance/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc).encode("utf-8", errors="replace")


def run_portal_checks(base: str = PORTAL, fetch_fn=None) -> dict:
    """门户核查：问答示例页面 200 + 静态样本 JSON 200/schema v2。"""
    fetch = fetch_fn or _http_get
    checks = []
    page_status, page_body = fetch(base + "/kb_reports.html")
    try:
        page_text = page_body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        page_text = ""
    checks.append({"item": "kb_reports.html", "url": base + "/kb_reports.html",
                   "status": page_status, "bytes": len(page_body),
                   "has_qa_block": "问答示例" in page_text,
                   "has_guard_marker": ("守卫通过" in page_text or "守卫未通过" in page_text),
                   "pass": page_status == 200 and "问答示例" in page_text})
    json_status, json_body = fetch(base + "/static/kb_qa_samples.json")
    schema = mode = None
    samples = 0
    guard_enabled = None
    if json_status == 200:
        try:
            data = json.loads(json_body.decode("utf-8", errors="replace"))
            schema = data.get("schema_version")
            mode = data.get("mode")
            samples = len(data.get("samples") or [])
            guard_enabled = bool((data.get("guard") or {}).get("enabled"))
        except (ValueError, UnicodeDecodeError):
            pass
    checks.append({"item": "kb_qa_samples.json", "url": base + "/static/kb_qa_samples.json",
                   "status": json_status, "bytes": len(json_body),
                   "schema_version": schema, "mode": mode, "samples": samples,
                   "guard_enabled": guard_enabled,
                   "pass": json_status == 200 and schema == 2 and samples >= 10})
    return {"rows": checks, "summary": {"checks": len(checks),
                                        "passed": sum(1 for x in checks if x["pass"]),
                                        "pass": all(x["pass"] for x in checks)}}


# ---------------------------------------------------------------------------
# ④ 结论 + 主流程
# ---------------------------------------------------------------------------
def build_conclusion(cli: dict, http: dict, portal: dict) -> dict:
    cli_pass = bool((cli.get("summary") or {}).get("pass"))
    http_pass = bool((http.get("summary") or {}).get("pass"))
    portal_pass = bool((portal.get("summary") or {}).get("pass"))
    fixes = []
    if not cli_pass:
        fixes.append("CLI 链路未通过：检查 kb_qa.ask(mode=multi, guard=True) 的 guard 字段/状态或错误。")
    if not http_pass:
        for check in (http.get("summary") or {}).get("checks") or []:
            if not check["pass"]:
                fixes.append(f"HTTP 预接线用例 {check['case']} 状态={check['status']}"
                             f"（期望 {check['expected']}），需修 portal/api_kb_qa.py。")
    if not portal_pass:
        fixes.append("门户静态页/样本 JSON 未达 200 或 schema v2，需检查 nginx 静态服务与样本刷新。")
    if cli_pass and http_pass and portal_pass:
        verdict, can_go = "三段验收全部通过，可进入 8091 接线 L2 审批", "go"
    elif cli_pass and portal_pass:
        verdict, can_go = ("CLI 与门户通过；HTTP 预接线存在需修项，修复后重新验收再提交 L2 审批",
                           "conditional")
    elif cli_pass:
        verdict, can_go = "CLI 通过；HTTP/门户存在需修项，暂不可上线", "no-go"
    else:
        verdict, can_go = "CLI 链路未通过，不可上线", "no-go"
    risks = [
        "HTTP 验收为进程内 handle 调用（未启动 8091），不代表真实网关/鉴权/超时链路已就绪。",
        "HTTP 用例中 single/multi 使用注入 mock（不真调 LLM），仅验证参数/状态码/信封契约。",
        "CLI 验收条数受 LLM 时间预算限制（默认 10 条），结论为 effective 子集。",
        "multi+guard 的 fallback 答案信息密度低于原答案，上线前需继续人工抽查。",
        "WIRED=False：本轮不注册、不重启 8091；正式接线需 L2 五要素审批。",
    ]
    return {"cli_pass": cli_pass, "http_pass": http_pass, "portal_pass": portal_pass,
            "can_go_live": can_go, "verdict": verdict,
            "fixes_needed": fixes, "risks": risks}


def run_acceptance(n: int = DEFAULT_N, model: str = DEFAULT_MODEL,
                   timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                   ask_fn=None, api=None, portal_base: str = PORTAL,
                   fetch_fn=None, clock=time.monotonic) -> dict:
    started = clock()
    questions = load_questions(V3_BANK, n)
    cli = run_cli_checks(questions, ask_fn=ask_fn, model=model, timeout=timeout,
                         deadline_seconds=deadline_seconds, clock=clock)
    http = run_http_checks(api=api)
    portal = run_portal_checks(base=portal_base, fetch_fn=fetch_fn)
    conclusion = build_conclusion(cli, http, portal)
    return {"schema_version": 1, "kind": "qa_acceptance",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": cli["summary"]["actual"],
                       "model": model, "mode": "multi", "guard": True,
                       "on_guard_fail": "fallback", "timeout_seconds": timeout,
                       "deadline_seconds": deadline_seconds, "portal": portal_base},
            "cli": cli, "http": http, "portal": portal,
            "conclusion": conclusion,
            "disclaimer": "验收记录仅供研究参考，不构成投资建议；HTTP 验收不启动服务。",
            "elapsed_seconds": round(clock() - started, 2)}


def render_markdown(payload: dict) -> str:
    cli, http, portal = payload["cli"], payload["http"], payload["portal"]
    csum, hsum = cli["summary"], http["summary"]
    concl = payload["conclusion"]
    lines = [f"# 问答链路端到端验收（{payload['generated_at'][:10]}）", "",
             f"> CLI：v3 前 {payload['config']['n_actual']} 条 · `kb_qa.ask(mode=multi, guard=True, "
             f"on_guard_fail=fallback)` 真实调用 · HTTP：`portal/api_kb_qa.handle` **进程内**验证"
             f"（不启动服务、注入 mock）· 门户：{payload['config']['portal']}", "",
             "## 验收结论", "",
             f"**{concl['verdict']}**（三段：CLI={'通过' if concl['cli_pass'] else '未通过'} / "
             f"HTTP={'通过' if concl['http_pass'] else '需修'} / 门户={'通过' if concl['portal_pass'] else '未通过'}）", "",
             f"- 上线判定：**{concl['can_go_live']}**", ""]
    if concl["fixes_needed"]:
        lines += ["### 需修项", ""] + [f"- {x}" for x in concl["fixes_needed"]] + [""]
    lines += ["## 一、CLI 端到端", "",
              f"- 请求 {csum['requested']} 条，实际 {csum['actual']} 条，跳过 {csum['skipped']} 条；"
              f"pass={csum['pass']}",
              f"- 状态：success **{csum['success']}** / no_evidence {csum['no_evidence']} / "
              f"fallback {csum['fallback']}",
              f"- guard：passed {csum['guard_passed']} / failed {csum['guard_failed']}；"
              f"平均引用 {csum['avg_citations']} 条；平均耗时 {csum['avg_elapsed_seconds']}s", "",
              "| # | 类别 | 问题 | 状态 | guard | 引用 | 可解析率 | 支撑率 | 推断比 | 耗时 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(cli["rows"], 1):
        lines.append(f"| {i} | {r.get('category')} | {str(r.get('question'))[:40]} | {r['status']} | "
                     f"{r.get('guard_passed')}/{r.get('guard_action')} | {r['citations']} | "
                     f"{r.get('citation_resolvable_rate')} | {r.get('support_rate')} | "
                     f"{r.get('inference_ratio')} | {r['elapsed_seconds']}s |")
    lines += ["", "## 二、HTTP 预接线（进程内）", "",
              f"- WIRED=`{hsum['wired']}`（未注册到运行中的 8091）；用例 {hsum['passed']}/{hsum['cases']} 通过", "",
              "| 用例 | 参数 | 状态码 | 期望 | 结果 | meta |", "|---|---|---|---|---|---|"]
    for idx, row in enumerate(http["rows"]):
        check = hsum["checks"][idx]
        meta = row.get("meta") or {}
        lines.append(f"| {row['case']} | {json.dumps(row.get('params'), ensure_ascii=False)} | "
                     f"{row['status']} | {check['expected']} | {'✅' if check['pass'] else '❌'} | "
                     f"mode={meta.get('mode')}/llm={meta.get('llm_called')}/guard={json.dumps(meta.get('guard'), ensure_ascii=False)[:60]} |")
    lines += ["", "## 三、门户核查", "",
              "| 项 | URL | 状态码 | 内容 | 结果 |", "|---|---|---|---|---|"]
    for row in portal["rows"]:
        content = (f"bytes={row.get('bytes')}"
                   + (f" schema={row.get('schema_version')} samples={row.get('samples')}" if "schema_version" in row else "")
                   + (" 含问答区块" if row.get("has_qa_block") else ""))
        lines.append(f"| {row['item']} | {row['url']} | {row['status']} | {content} | "
                     f"{'✅' if row['pass'] else '❌'} |")
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in concl["risks"]]
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
    ap = argparse.ArgumentParser(description="问答链路端到端验收（CLI/HTTP 预接线/门户）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑三段验收并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"CLI 验收条数（默认 {DEFAULT_N}）")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--portal", default=PORTAL)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_acceptance(n=args.n, model=args.model, timeout=args.timeout,
                                 deadline_seconds=args.deadline, portal_base=args.portal)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except AcceptanceError as exc:
        print(f"[qa_acceptance] FAILED: {exc}", file=sys.stderr)
        return 1
    cli = payload["cli"]["summary"]
    http = payload["http"]["summary"]
    portal = payload["portal"]["summary"]
    print(f"[qa_acceptance] CLI: actual={cli['actual']} success={cli['success']} "
          f"no_evidence={cli['no_evidence']} fallback={cli['fallback']} pass={cli['pass']}")
    print(f"[qa_acceptance] HTTP: {http['passed']}/{http['cases']} pass, wired={http['wired']}")
    print(f"[qa_acceptance] PORTAL: {portal['passed']}/{portal['checks']} pass")
    print(f"[qa_acceptance] conclusion: {payload['conclusion']['verdict']} "
          f"can_go_live={payload['conclusion']['can_go_live']}")
    print(f"[qa_acceptance] json={paths[0]}")
    print(f"[qa_acceptance] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
