# -*- coding: utf-8 -*-
"""multi 扩样 50 条复跑：v3 全 50 题 single vs multi，复用已有 single/multi 基线。

背景：wave17 AW 只测 v3 前 30 条（single 33.3% → multi 80%，McNemar p=0.000122，回退 0），
结论建议扩到 50 条验证 multi 默认稳定性。本模块（不改动 kb_qa_multi_ab.py / 既有测试）：

  ① 复用已有基线减少 LLM 调用：
     - 前 30 条：直接复用 AW（wave17）已跑的 single + multi 结果（both arms，零新增调用）；
     - 后 20 条：现场跑 single（`kb_qa.ask(mode="single")`）+ multi（`kb_qa.ask(mode="multi")`）；
  ② 统计：可答率、配对 McNemar、回退/改善、平均引用、平均耗时、按模板类别/主题；
  ③ 与 AW n=30 结果对比，判定 multi 默认结论是否在 50 条上**复现**；
  ④ 输出 `research/reports/kb_qa_multi_ab50_20261101.{json,md}`。

CLI：
  python tools/kb_corpus/kb_qa_multi_ab50.py run --n 50 --k 8
  python tools/kb_corpus/kb_qa_multi_ab50.py run --n 40 --deadline 420   # LLM 慢时缩样并注明
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
AW_JSON = REPO / "research" / "reports" / "kb_qa_multi_ab_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_multi_ab50_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_multi_ab50_20261101.md"
MAX_N = 50
DEFAULT_N = 50
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480
REUSE_N = 30  # AW 已覆盖的题数（v3 前 30）


class Ab50Error(RuntimeError):
    """扩样 A/B 异常（基线文件缺失等）。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise Ab50Error(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ab = None
_kb_qa = None


def ab():
    global _ab
    if _ab is None:
        _ab = _load_module("kb_qa_multi_ab_for_ab50", "kb_qa_multi_ab.py")
    return _ab


def kb_qa():
    global _kb_qa
    if _kb_qa is None:
        _kb_qa = _load_module("kb_qa_for_ab50", "kb_qa.py")
    return _kb_qa


# ---------------------------------------------------------------------------
# ① 基线加载
# ---------------------------------------------------------------------------
def load_v3_questions(path: Path | str = V3_BANK) -> list[dict]:
    try:
        bank = ab().load_v3_bank(path)
    except Exception as exc:  # noqa: BLE001
        raise Ab50Error(f"v3 问题库加载失败：{exc}") from exc
    return bank["questions"]


def load_aw_rows(path: Path | str = AW_JSON) -> dict:
    """读 AW（n=30）A/B 结果：返回 {id: row}（含 single/multi arms）。"""
    path = Path(path)
    if not path.exists():
        raise Ab50Error(f"AW 结果文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Ab50Error(f"AW 结果读取失败：{path}（{exc}）") from exc
    rows = [r for r in data.get("comparison") or [] if isinstance(r, dict) and r.get("id")]
    if not rows:
        raise Ab50Error(f"AW 结果缺少 comparison[]：{path}")
    return {r["id"]: r for r in rows}, data.get("summary") or {}


# ---------------------------------------------------------------------------
# ② 组成 50 条配对行（前 30 复用 AW，后 20 现场跑）
# ---------------------------------------------------------------------------
def _arm_from_aw(arm: dict) -> dict:
    out = copy.deepcopy(arm or {})
    out.setdefault("status", "success" if out.get("success") else "no_evidence")
    out.setdefault("success", out.get("status") == "success")
    out.setdefault("refusal", out.get("status") == "no_evidence")
    out.setdefault("fallback", out.get("status") == "fallback")
    out.setdefault("citations", 0)
    out.setdefault("elapsed_seconds", 0.0)
    out["reused"] = True
    out["reuse_source"] = "aw_n30"
    return out


def compose_rows(questions: list[dict], aw_rows: dict, single_fn=None, multi_fn=None,
                 k: int = DEFAULT_K, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                 deadline_seconds: float = DEFAULT_DEADLINE, clock=time.monotonic) -> tuple[list[dict], dict]:
    """构造 50 条配对 rows；前 REUSE_N 条复用 AW，其余现场跑。"""
    started = clock()
    rows: list[dict] = []
    new_single_calls = new_multi_calls = 0
    skipped = 0
    single_call = single_fn or (lambda q, **kw: kb_qa().ask(q, **kw))
    multi_call = multi_fn or (lambda q, **kw: kb_qa().ask(q, **kw))
    for idx, question in enumerate(questions):
        qid = question.get("id")
        row = {"id": qid, "question": question.get("question"),
               "category": question.get("category"), "theme": question.get("theme")}
        aw_row = aw_rows.get(qid)
        if aw_row is not None and idx < REUSE_N:
            row["single"] = _arm_from_aw(aw_row.get("single") or {})
            row["multi"] = _arm_from_aw(aw_row.get("multi") or {})
            row["reused_from_aw"] = True
        else:
            remaining = deadline_seconds - (clock() - started)
            if remaining <= 1:
                skipped += 1
                row.update({"single": {"status": "skipped_deadline", "success": False,
                                       "citations": 0, "elapsed_seconds": 0.0, "refusal": False,
                                       "fallback": False},
                            "multi": {"status": "skipped_deadline", "success": False,
                                      "citations": 0, "elapsed_seconds": 0.0, "refusal": False,
                                      "fallback": False}})
                rows.append(row)
                continue
            try:
                res_s = single_call(question["question"], topk=k, model=model, timeout=min(timeout, remaining),
                                    mode="single")
            except Exception:  # noqa: BLE001
                res_s = {"answer": None, "citations": [], "confidence": {"level": "none"},
                         "fallback": True, "elapsed_seconds": 0.0}
            cls_s = ab().classify_single(res_s)
            cls_s["reused"] = False
            cls_s["reuse_source"] = "live"
            row["single"] = cls_s
            new_single_calls += 1
            remaining = deadline_seconds - (clock() - started)
            if remaining <= 1:
                skipped += 1
                row["multi"] = {"status": "skipped_deadline", "success": False, "citations": 0,
                                "elapsed_seconds": 0.0, "refusal": False, "fallback": False}
                rows.append(row)
                continue
            try:
                res_m = multi_call(question["question"], topk=k, model=model, timeout=min(timeout, remaining),
                                   mode="multi")
            except Exception:  # noqa: BLE001
                res_m = {"answer": None, "citations": [], "confidence": {"level": "none"},
                         "fallback": True, "elapsed_seconds": 0.0}
            row["multi"] = ab().classify_multi(res_m)
            row["multi"]["reuse_source"] = "live"
            new_multi_calls += 1
            row["reused_from_aw"] = False
        row["improved"] = (not row["single"].get("success")) and bool(row["multi"].get("success"))
        row["regressed"] = bool(row["single"].get("success")) and not bool(row["multi"].get("success"))
        rows.append(row)
    meta = {"new_single_calls": new_single_calls, "new_multi_calls": new_multi_calls,
            "reused_rows": sum(1 for r in rows if r.get("reused_from_aw")),
            "skipped": skipped, "n_actual": len(rows),
            "llm_calls_total": new_single_calls + new_multi_calls}
    return rows, meta


# ---------------------------------------------------------------------------
# ③ 统计 / n=30 对比 / 复现判定
# ---------------------------------------------------------------------------
def compare_n30(summary: dict, decision: dict, aw_summary: dict, aw_decision: dict) -> dict:
    aw_single = (aw_summary.get("single") or {}).get("rate")
    aw_multi = (aw_summary.get("multi") or {}).get("rate")
    aw_delta = (aw_summary.get("delta") or {}).get("answerable_rate")
    aw_paired = aw_summary.get("paired") or {}
    aw_regressed = len(aw_summary.get("regressed_ids") or [])
    aw_default = (aw_decision or {}).get("default_multi")
    new_delta = summary["delta"]["answerable_rate"]
    new_regressed = len(summary["regressed_ids"])
    new_p = summary["paired"]["mcnemar_p"]
    new_default = decision.get("default_multi")
    delta_diff = round(new_delta - (aw_delta or 0), 4)
    reproduced = bool(new_default == aw_default and new_default
                      and abs(delta_diff) <= 0.15 and new_p < 0.05
                      and new_regressed <= max(1, int(round(len(summary["improved_ids"]) * 0.1))))
    return {
        "aw_n30": {"n": (aw_summary.get("total") or 0),
                   "single_rate": aw_single, "multi_rate": aw_multi, "delta": aw_delta,
                   "mcnemar_p": aw_paired.get("mcnemar_p"), "regressions": aw_regressed,
                   "default_multi": aw_default,
                   "verdict": (aw_decision or {}).get("verdict")},
        "new_n50": {"n": summary["total"],
                    "single_rate": summary["single"]["rate"], "multi_rate": summary["multi"]["rate"],
                    "delta": new_delta, "mcnemar_p": new_p, "regressions": new_regressed,
                    "default_multi": new_default, "verdict": decision.get("verdict")},
        "delta_diff": delta_diff,
        "reproduced": reproduced,
        "reproduced_note": ("multi 默认结论在 50 条上复现（Δ 差异 ≤15pp、McNemar 显著、回退可控）"
                            if reproduced else "结论未完全复现，需复核样本/耗时/类别差异"),
    }


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab50(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
             timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
             single_fn=None, multi_fn=None, v3_path: Path | str = V3_BANK,
             aw_path: Path | str = AW_JSON, clock=time.monotonic) -> dict:
    started = clock()
    n = max(1, min(MAX_N, int(n)))
    questions = load_v3_questions(v3_path)[:n]
    aw_rows, aw_summary = load_aw_rows(aw_path)
    aw_data = {"summary": aw_summary}
    try:
        aw_full = json.loads(Path(aw_path).read_text(encoding="utf-8"))
        aw_data["decision"] = aw_full.get("decision") or {}
    except (OSError, json.JSONDecodeError):
        aw_data["decision"] = {}
    rows, meta = compose_rows(questions, aw_rows, single_fn=single_fn, multi_fn=multi_fn,
                              k=k, model=model, timeout=timeout,
                              deadline_seconds=deadline_seconds, clock=clock)
    summary = ab().summarize_ab(rows)
    decision = ab().decide(summary, len(rows))
    n30 = compare_n30(summary, decision, aw_summary, aw_data["decision"])
    return {
        "schema_version": 1, "kind": "kb_qa_multi_ab50",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {"v3_bank": str(v3_path), "aw_n30": str(aw_path)},
        "config": {"n_requested": n, "n_actual": len(rows), "k": max(5, min(8, int(k))),
                   "model": model, "timeout_seconds": timeout,
                   "deadline_seconds": deadline_seconds, "reuse_n": REUSE_N},
        "run_meta": meta,
        "note": "前 30 条复用 wave17 AW 的 single+multi 结果（零新增调用）；后 20 条现场 "
                "single（kb_qa.ask mode=single）+ multi（mode=multi）串行生成",
        "summary": summary, "decision": decision,
        "comparison_n30": n30, "comparison": rows,
        "disclaimer": "扩样 A/B 结果仅供研究参考，不构成投资建议；multi 允许推断，引用/事实需人工复核。",
        "elapsed_seconds": round(clock() - started, 2),
    }


def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    cfg = payload["config"]
    meta = payload["run_meta"]
    dec = payload["decision"]
    n30 = payload["comparison_n30"]
    lines = [f"# multi 扩样 50 条 A/B 复跑（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库 **{cfg['n_actual']}** 条（申请 {cfg['n_requested']}）· k={cfg['k']} · "
             f"复用 AW 前 {meta['reused_rows']} 条 single+multi 基线 · 新增 LLM 调用 "
             f"{meta['llm_calls_total']} 次（single {meta['new_single_calls']} + multi {meta['new_multi_calls']}）"
             f" · 跳过 {meta['skipped']} 条", "",
             "## 结论", "",
             f"**{'multi 默认结论复现' if n30['reproduced'] else '结论未完全复现'}** —— "
             f"{'建议维持 multi 为问答默认模式' if dec['default_multi'] else '暂不维持 multi 默认'}（{dec['verdict']}）", "",
             f"- {n30['reproduced_note']}",
             f"- 可答率：single {s['single']['rate']:.0%}（{s['single']['success']}/{s['total']}）→ "
             f"multi {s['multi']['rate']:.0%}（{s['multi']['success']}/{s['total']}），Δ{s['delta']['answerable_rate']:+.0%}。",
             f"- 配对：改善 {len(s['improved_ids'])} 条 / 回退 {len(s['regressed_ids'])} 条；"
             f"McNemar p={s['paired']['mcnemar_p']}。",
             f"- 平均引用 {s['single']['avg_citations']} → {s['multi']['avg_citations']}；"
             f"单条平均耗时 {s['single']['avg_elapsed_seconds']}s → {s['multi']['avg_elapsed_seconds']}s"
             f"（比值 {s['delta']['latency_ratio']}）。", "",
             "## 与 AW n=30 对比", "",
             "| 指标 | AW n=30 | 本次 n={} | Δ |".format(n30["new_n50"]["n"]),
             "|---|---|---|---|",
             f"| single 可答率 | {n30['aw_n30']['single_rate']:.0%} | {n30['new_n50']['single_rate']:.0%} | "
             f"{n30['new_n50']['single_rate'] - n30['aw_n30']['single_rate']:+.0%} |",
             f"| multi 可答率 | {n30['aw_n30']['multi_rate']:.0%} | {n30['new_n50']['multi_rate']:.0%} | "
             f"{n30['new_n50']['multi_rate'] - n30['aw_n30']['multi_rate']:+.0%} |",
             f"| 可答率 Δ | {n30['aw_n30']['delta']:+.0%} | {n30['new_n50']['delta']:+.0%} | "
             f"{n30['delta_diff']:+.0%} |",
             f"| McNemar p | {n30['aw_n30']['mcnemar_p']} | {n30['new_n50']['mcnemar_p']} | — |",
             f"| 回退条数 | {n30['aw_n30']['regressions']} | {n30['new_n50']['regressions']} | — |",
             f"| 结论 | {'默认 multi' if n30['aw_n30']['default_multi'] else '不设默认'} | "
             f"{'默认 multi' if n30['new_n50']['default_multi'] else '不设默认'} | {'复现' if n30['reproduced'] else '未复现'} |",
             "", "## 判定阈值", "", "| 判据 | 阈值 | 实测 | 结果 |", "|---|---|---|---|"]
    for c in dec["criteria"]:
        lines.append(f"| {c['name']} | {c['threshold']} | {c['actual']} | {'✅' if c['pass'] else '❌'} |")
    lines += ["", "## 按模板类别", "", "| 类别 | 条数 | single | multi | Δ |", "|---|---|---|---|---|"]
    for g in s["by_category"]:
        lines.append(f"| {g['category']} | {g['total']} | {g['single_rate']:.0%} | {g['multi_rate']:.0%} | {g['delta']:+.0%} |")
    if s["improved_ids"]:
        lines += ["", f"改善样本（{len(s['improved_ids'])} 条）：" + "、".join(s["improved_ids"][:20])]
    if s["regressed_ids"]:
        lines += ["", f"回退样本（{len(s['regressed_ids'])} 条）：" + "、".join(s["regressed_ids"][:20])]
    lines += ["", "## 逐条对比", "",
              "| # | 类别 | 问题 | single 状态/引用 | multi 状态/引用 | 来源 |",
              "|---|---|---|---|---|---|"]
    for i, row in enumerate(payload["comparison"], 1):
        si = row.get("single") or {}
        mu = row.get("multi") or {}
        src = "AW复用" if row.get("reused_from_aw") else "本次实跑"
        lines.append(f"| {i} | {row.get('category')} | {str(row.get('question'))[:40]} | "
                     f"{si.get('status')}/{si.get('citations')} | {mu.get('status')}/{mu.get('citations')} | {src} |")
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in dec["risks"]]
    lines += [f"- 下一步：{r}" for r in dec["next_actions"]]
    lines += ["", f"- {payload['disclaimer']}", f"- {payload['note']}",
              f"- 耗时 {payload['elapsed_seconds']}s（含检索/LLM）"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON,
                  out_md: Path | str = OUT_MD) -> tuple[Path, Path]:
    out_json, out_md = Path(out_json), Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="multi 扩样 50 条 A/B 复跑（复用 AW n=30 基线）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 50 条（或指定 n）single vs multi 对比")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本数（默认 {DEFAULT_N}，上限 {MAX_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--v3", default=str(V3_BANK), help="v3 问题库路径")
    run.add_argument("--aw", default=str(AW_JSON), help="AW n=30 结果路径")
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab50(args.n, k=args.k, model=args.model, timeout=args.timeout,
                           deadline_seconds=args.deadline, v3_path=args.v3, aw_path=args.aw)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except Ab50Error as exc:
        print(f"[kb_qa_multi_ab50] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["summary"]
    n30 = payload["comparison_n30"]
    print(f"[kb_qa_multi_ab50] n={payload['config']['n_actual']} reused_rows={payload['run_meta']['reused_rows']} "
          f"llm_calls={payload['run_meta']['llm_calls_total']} skipped={payload['run_meta']['skipped']}")
    print(f"[kb_qa_multi_ab50] single rate={s['single']['rate']:.0%} ({s['single']['success']}/{s['total']}) "
          f"multi rate={s['multi']['rate']:.0%} ({s['multi']['success']}/{s['total']}) "
          f"delta={s['delta']['answerable_rate']:+.0%}")
    print(f"[kb_qa_multi_ab50] paired={json.dumps(s['paired'], ensure_ascii=False)} "
          f"regressions={len(s['regressed_ids'])}")
    print(f"[kb_qa_multi_ab50] decision={payload['decision']['verdict']} "
          f"default_multi={payload['decision']['default_multi']} reproduced={n30['reproduced']}")
    print(f"[kb_qa_multi_ab50] json={paths[0]}")
    print(f"[kb_qa_multi_ab50] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
