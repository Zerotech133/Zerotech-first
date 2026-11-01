# -*- coding: utf-8 -*-
"""问答质量每日抽检 + 漂移告警（真实 HTTP multi 1-2 条/日，低成本）。

流程：
  ① 经 8091 `/api/kb/qa?mode=multi&cache=0` 真实调用 1-2 条（<2 次 LLM），复用
     qa_live_quality_review 的解析/重算（答案/引用/可解析/推断/guard/fallback/耗时）；
  ② 追加到 `research/reports/qa_quality_history.json`（按日期累积，含 rolling 7d 统计）；
  ③ 漂移告警：当日 可答率<80% / guard 通过率<80% / fallback>20%，
     以及相对 rolling 7d 明显下滑（可答率 -15pp 以上）→ 告警（默认打印，`--send` 发飞书）；
  ④ 输出 `research/reports/qa_quality_<YYYYMMDD>.{json,md}`（当日指标 + 历史趋势）。

飞书：`--send` 时调用 news_collector/send_feishu.send_text 发文本告警（失败降级为打印，不抛异常）。

CLI：
  python tools/kb_corpus/qa_quality_daily.py run                 # 抽检 2 条 + 打印告警
  python tools/kb_corpus/qa_quality_daily.py run --n 1 --send   # 抽检 1 条 + 飞书告警
  python tools/kb_corpus/qa_quality_daily.py status              # 只读历史/rolling 7d
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HISTORY = REPO / "research" / "reports" / "qa_quality_history.json"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
BASE_URL = "http://127.0.0.1:8091"
DEFAULT_N = 2
ROLLING_WINDOW = 7
THRESHOLDS = {"answerable_min": 0.80, "guard_min": 0.80, "fallback_max": 0.20,
              "trend_drop_max": 0.15}


class QualityError(RuntimeError):
    """每日质量抽检异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise QualityError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mod(name: str):
    key = f"{name}_for_daily_quality"
    if key not in sys.modules:
        sys.modules[key] = _load_module(key, f"{name}.py")
    return sys.modules[key]


# ---------------------------------------------------------------------------
# 历史（累积 + rolling 7d）
# ---------------------------------------------------------------------------
def load_history(path: Path | str = HISTORY) -> dict:
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "kind": "qa_quality_history",
                "entries": [], "rolling_7d": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "kind": "qa_quality_history",
                "entries": [], "rolling_7d": {}}
    if not isinstance(data, dict):
        return {"schema_version": 1, "kind": "qa_quality_history",
                "entries": [], "rolling_7d": {}}
    data.setdefault("entries", [])
    data.setdefault("rolling_7d", {})
    return data


def rolling_stats(entries: list[dict], window: int = ROLLING_WINDOW) -> dict:
    recent = entries[-max(1, window):]
    if not recent:
        return {"n_days": 0, "answerable_rate": None, "guard_pass_rate": None,
                "fallback_rate": None, "avg_citations": None, "avg_elapsed_seconds": None,
                "window": window}
    n = len(recent)

    def _avg(key):
        vals = [float(e.get(key) or 0.0) for e in recent]
        return round(sum(vals) / n, 4)

    return {"n_days": n, "answerable_rate": _avg("answerable_rate"),
            "guard_pass_rate": _avg("guard_pass_rate"),
            "fallback_rate": _avg("fallback_rate"),
            "avg_citations": _avg("avg_citations"),
            "avg_elapsed_seconds": _avg("avg_elapsed_seconds"),
            "window": window,
            "date_from": recent[0].get("date"), "date_to": recent[-1].get("date")}


def append_entry(history: dict, entry: dict, window: int = ROLLING_WINDOW) -> dict:
    entries = list(history.get("entries") or [])
    # 同日重跑：覆盖当日条目（避免重复累积）
    entries = [e for e in entries if e.get("date") != entry.get("date")]
    entries.append(entry)
    entries.sort(key=lambda e: str(e.get("date")))
    history["entries"] = entries
    history["rolling_7d"] = rolling_stats(entries, window)
    history["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    history["kind"] = "qa_quality_history"
    history["schema_version"] = 1
    return history


# ---------------------------------------------------------------------------
# 漂移判定 + 告警
# ---------------------------------------------------------------------------
def drift_check(day: dict, rolling: dict, thresholds: dict | None = None) -> list[dict]:
    t = thresholds or THRESHOLDS
    alerts = []
    ans = float(day.get("answerable_rate") or 0.0)
    guard = float(day.get("guard_pass_rate") or 0.0)
    fb = float(day.get("fallback_rate") or 0.0)
    if ans < t["answerable_min"]:
        alerts.append({"code": "ANSWERABLE_LOW", "level": "warn",
                       "message": f"可答率 {ans:.0%} < {t['answerable_min']:.0%} 阈值"})
    if guard < t["guard_min"]:
        alerts.append({"code": "GUARD_LOW", "level": "warn",
                       "message": f"guard 通过率 {guard:.0%} < {t['guard_min']:.0%} 阈值"})
    if fb > t["fallback_max"]:
        alerts.append({"code": "FALLBACK_HIGH", "level": "warn",
                       "message": f"fallback 率 {fb:.0%} > {t['fallback_max']:.0%} 阈值"})
    base = rolling.get("answerable_rate")
    if base is not None and ans < float(base) - t["trend_drop_max"]:
        alerts.append({"code": "TREND_DOWN", "level": "warn",
                       "message": f"可答率较 rolling 7d 均值 {float(base):.0%} 下滑 "
                                  f"{float(base) - ans:.0%}（>{t['trend_drop_max']:.0%}）"})
    return alerts


def format_alert_text(date: str, day: dict, alerts: list[dict], rolling: dict) -> str:
    if not alerts:
        return f"【问答质量日报 {date}】正常：可答率 {day.get('answerable_rate', 0):.0%}、" \
               f"guard {day.get('guard_pass_rate', 0):.0%}、fallback {day.get('fallback_rate', 0):.0%}"
    lines = [f"【问答质量告警 {date}】发现 {len(alerts)} 项漂移："]
    for a in alerts:
        lines.append(f"- [{a['code']}] {a['message']}")
    lines.append(f"当日：可答率 {day.get('answerable_rate', 0):.0%}、guard {day.get('guard_pass_rate', 0):.0%}、"
                 f"fallback {day.get('fallback_rate', 0):.0%}、样本 {day.get('n')} 条")
    if rolling.get("answerable_rate") is not None:
        lines.append(f"rolling 7d：日均可答率 {rolling['answerable_rate']:.0%}、"
                     f"guard {rolling['guard_pass_rate']:.0%}、fallback {rolling['fallback_rate']:.0%}"
                     f"（{rolling.get('n_days')} 天）")
    return "\n".join(lines)


def send_alert(text: str) -> dict:
    """--send：调用 news_collector/send_feishu.send_text；失败返回 error，不抛出。"""
    try:
        nc = REPO / "news_collector"
        if nc.exists() and str(nc) not in sys.path:
            sys.path.insert(0, str(nc))
        from send_feishu import send_text  # noqa: PLC0415

        ok = bool(send_text(text, kind="qa_quality_alert"))
        return {"sent": ok, "error": None}
    except Exception as exc:  # noqa: BLE001 - 告警失败降级为打印
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 抽检（真实 HTTP multi）
# ---------------------------------------------------------------------------
def pick_questions(n: int = DEFAULT_N, path: Path | str = V3_BANK,
                   history: dict | None = None) -> list[dict]:
    """按历史轮换选样（尽量覆盖不同主题，避免每天重复同一批）。"""
    path = Path(path)
    if not path.exists():
        raise QualityError(f"v3 问题库不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise QualityError("v3 问题库 questions 为空")
    used = set()
    for e in (history or {}).get("entries") or []:
        used.update(e.get("sample_ids") or [])
    fresh = [q for q in qs if q.get("id") not in used] or qs
    by_theme: dict[str, list[dict]] = {}
    for q in fresh:
        by_theme.setdefault(str(q.get("theme") or "其他"), []).append(q)
    picked, i = [], 0
    themes = list(by_theme)
    while len(picked) < n and themes:
        pool = by_theme[themes[i % len(themes)]]
        if pool:
            picked.append(pool.pop(0))
        i += 1
        if not any(by_theme.values()):
            break
    return picked[:n]


def run_samples(questions: list[dict], base_url: str = BASE_URL, fetch_fn=None,
                unit_ids: set[str] | None = None) -> list[dict]:
    lq = mod("qa_live_quality_review")
    fetch = fetch_fn or lq.http_get
    ids = unit_ids if unit_ids is not None else lq.load_unit_ids()
    samples = []
    for q in questions:
        url = (f"{base_url}/api/kb/qa?" +
               "q=" + __import__("urllib.parse", fromlist=["parse"]).quote(q["question"]) +
               "&mode=multi&topk=8&cache=0")
        status, payload, elapsed = fetch(url, 180)
        samples.append(lq.build_sample(q, status, payload, elapsed, ids))
    return samples


def summarize_day(samples: list[dict]) -> dict:
    total = len(samples)
    ok = [s for s in samples if s.get("http_status") == 200]
    success = [s for s in ok if s.get("status") == "success"]
    fallback = [s for s in ok if s.get("status") == "fallback"]
    guard_pass = [s for s in ok if s.get("guard_passed_recomputed")]

    def _avg(items, key):
        return round(sum(float(i.get(key) or 0) for i in items) / len(items), 4) if items else 0.0

    return {"n": total, "http_200": len(ok), "success": len(success),
            "fallback": len(fallback),
            "answerable_rate": round(len(success) / total, 4) if total else 0.0,
            "guard_pass_rate": round(len(guard_pass) / total, 4) if total else 0.0,
            "fallback_rate": round(len(fallback) / total, 4) if total else 0.0,
            "avg_citations": _avg(ok, "citation_count"),
            "avg_citation_resolvable_rate": _avg(ok, "citation_resolvable_rate"),
            "avg_inference_ratio": _avg(ok, "inference_ratio"),
            "avg_elapsed_seconds": _avg(ok, "elapsed_seconds"),
            "avg_completeness_score": _avg(ok, "completeness_score")}


def run_daily(n: int = DEFAULT_N, base_url: str = BASE_URL, send: bool = False,
              history_path: Path | str = HISTORY, fetch_fn=None,
              unit_ids: set[str] | None = None, clock=time.monotonic) -> dict:
    started = clock()
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    history = load_history(history_path)
    questions = pick_questions(n, history=history)
    samples = run_samples(questions, base_url=base_url, fetch_fn=fetch_fn, unit_ids=unit_ids)
    day = summarize_day(samples)
    rolling_before = dict(history.get("rolling_7d") or {})
    alerts = drift_check(day, rolling_before)
    entry = {"date": date, **{k: day[k] for k in
                              ("n", "answerable_rate", "guard_pass_rate", "fallback_rate",
                               "avg_citations", "avg_citation_resolvable_rate",
                               "avg_inference_ratio", "avg_elapsed_seconds",
                               "avg_completeness_score")},
             "guard_pass_count": day["success"],
             "sample_ids": [q.get("id") for q in questions],
             "alerts": [a["code"] for a in alerts]}
    history = append_entry(history, entry)
    rolling = history["rolling_7d"]
    alert_text = format_alert_text(date, day, alerts, rolling)
    send_result = {"sent": False, "error": None, "attempted": False}
    if send and alerts:
        send_result = {"attempted": True, **send_alert(alert_text)}
    out_json = REPO / "research" / "reports" / f"qa_quality_{date.replace('-', '')}.json"
    return {"schema_version": 1, "kind": "qa_quality_daily",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "date": date,
            "config": {"base_url": base_url, "n": len(samples), "llm_calls": len(samples),
                       "history_path": str(history_path),
                       "thresholds": dict(THRESHOLDS), "rolling_window": ROLLING_WINDOW,
                       "send_requested": bool(send)},
            "day": day, "alerts": alerts, "alert_text": alert_text,
            "rolling_7d": rolling, "history_entries": len(history["entries"]),
            "history_snapshot": history,
            "samples": samples,
            "history_date": str(history_path),
            "send": send_result,
            "out_json_expected": str(out_json),
            "elapsed_seconds": round(clock() - started, 2),
            "disclaimer": "每日质量抽检仅供运维监控参考，不构成投资建议。"}


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def write_outputs(payload: dict, out_json: Path | str | None = None,
                  out_md: Path | str | None = None) -> tuple[Path, Path]:
    date_tag = payload["date"].replace("-", "")
    out_json = Path(out_json) if out_json else REPO / "research" / "reports" / f"qa_quality_{date_tag}.json"
    out_md = Path(out_md) if out_md else REPO / "research" / "reports" / f"qa_quality_{date_tag}.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


def render_markdown(payload: dict) -> str:
    day, alerts, rolling = payload["day"], payload["alerts"], payload["rolling_7d"]
    cfg = payload["config"]
    lines = [f"# 问答质量日报（{payload['date']}）", "",
             f"> 真实 HTTP multi 抽检 {cfg['n']} 条（cache=0，LLM {cfg['llm_calls']} 次）· "
             f"rolling {cfg['rolling_window']}d · 阈值 可答率≥80% / guard≥80% / fallback≤20%。", "",
             "## 当日结论", ""]
    status = "⚠️ 漂移告警" if alerts else "✅ 正常"
    lines += [f"**{status}**：可答率 {day['answerable_rate']:.0%}、guard {day['guard_pass_rate']:.0%}、"
              f"fallback {day['fallback_rate']:.0%}、平均引用 {day['avg_citations']}、"
              f"可解析率 {day['avg_citation_resolvable_rate']:.0%}、推断 {day['avg_inference_ratio']}、"
              f"平均耗时 {day['avg_elapsed_seconds']}s、完整度 {day['avg_completeness_score']}/5", ""]
    if alerts:
        lines += ["### 告警明细", ""] + [f"- [{a['code']}] {a['message']}" for a in alerts] + [""]
    lines += ["## rolling 7d 趋势", ""]
    if rolling.get("answerable_rate") is not None:
        lines += [f"- {rolling.get('date_from')} ~ {rolling.get('date_to')}（{rolling.get('n_days')} 天）："
                  f"日均可答率 {rolling['answerable_rate']:.0%}、guard {rolling['guard_pass_rate']:.0%}、"
                  f"fallback {rolling['fallback_rate']:.0%}、平均引用 {rolling['avg_citations']}", ""]
    else:
        lines += ["- 历史不足（首次运行）", ""]
    lines += ["## 告警文本（--send 时发送）", "", "```", payload["alert_text"], "```", ""]
    if payload.get("send", {}).get("attempted"):
        lines += [f"- 发送结果：sent={payload['send'].get('sent')} error={payload['send'].get('error')}", ""]
    lines += ["## 抽检样本", "",
              "| # | 问题 | 状态 | 引用 | 可解析 | 推断 | guard | 耗时 | 完整度 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(payload["samples"], 1):
        lines.append(f"| {i} | {str(s.get('question'))[:36]} | {s.get('status')} | "
                     f"{s.get('citation_count')} | {float(s.get('citation_resolvable_rate') or 0):.0%} | "
                     f"{s.get('inference_ratio')} | {s.get('guard_passed_recomputed')} | "
                     f"{s.get('elapsed_seconds')}s | {s.get('completeness_score')}/5 |")
    lines += ["", f"- 历史文件：`{payload['history_date']}`（累计 {payload['history_entries']} 天）",
              "", f"- {payload['disclaimer']}", f"- 总耗时 {payload['elapsed_seconds']}s"]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答质量每日抽检 + 漂移告警")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="真实 HTTP 抽检并追加历史/告警")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"抽检条数（默认 {DEFAULT_N}，≤2 控 LLM 成本）")
    run.add_argument("--base-url", default=BASE_URL)
    run.add_argument("--send", action="store_true", help="有告警时发送飞书（默认打印）")
    run.add_argument("--history", default=str(HISTORY))
    run.add_argument("--out-json", default=None)
    run.add_argument("--out-md", default=None)
    status = sub.add_parser("status", help="只读历史与 rolling 7d（不调 LLM）")
    status.add_argument("--history", default=str(HISTORY))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "status":
            history = load_history(args.history)
            rolling = rolling_stats(history.get("entries") or [])
            print(json.dumps({"entries": len(history.get("entries") or []),
                              "rolling_7d": rolling,
                              "last": (history.get("entries") or [{}])[-1]},
                             ensure_ascii=False, indent=1))
            return 0
        payload = run_daily(args.n, base_url=args.base_url, send=args.send,
                            history_path=args.history)
        paths = write_outputs(payload, args.out_json, args.out_md)
        history = payload.get("history_snapshot") or load_history(args.history)
        history["rolling_7d"] = payload["rolling_7d"]
        history_path = Path(args.history)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n",
                                encoding="utf-8")
    except QualityError as exc:
        print(f"[qa-quality-daily] FAILED: {exc}", file=sys.stderr)
        return 1
    day = payload["day"]
    print(f"[qa-quality-daily] date={payload['date']} n={day['n']} "
          f"answerable={day['answerable_rate']:.0%} guard={day['guard_pass_rate']:.0%} "
          f"fallback={day['fallback_rate']:.0%} alerts={len(payload['alerts'])}")
    print(payload["alert_text"])
    print(f"[qa-quality-daily] json={paths[0]}")
    print(f"[qa-quality-daily] md={paths[1]}")
    print(f"[qa-quality-daily] history={args.history} entries={payload['history_entries']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
