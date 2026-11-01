# -*- coding: utf-8 -*-
"""每周评测回归（task-125）：v3 冻结基线漂移监控（不调 LLM）。

流程：
  ① 复用 eval_v3_baseline 核心逻辑，在 v3 冻结资产（102 查询）上跑当前默认 search_v2
     （soft + synonym 0.6），得到 P@5 / NDCG@5 / 覆盖率；
  ② 与冻结基线 **P@5=0.800、NDCG=0.857、覆盖率=0.893** 比较，偏差阈值 **±0.02**；
     任一指标 |Δ| > tol → 告警（写入报告/历史，不影响退出码，便于计划任务持续记录）；
  ③ 输出 research/reports/weekly_eval_20261102.{json,md}；
  ④ 追加历史 research/reports/eval_regression_history.json（保留最近 52 次）；
  ⑤ 计划任务：tools/scheduled/weekly_eval_task.ps1（UTF-8 BOM）→ ZeroBound_WeeklyEval（周一 09:00）。

运行：python tools/kb_corpus/weekly_eval_regression.py [--tag 20261102] [--tolerance 0.02]
约束：不调 LLM、不改配置、不重启服务。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_v3_baseline as base  # noqa: E402

OUT = ROOT / "research" / "reports"
OUT_JSON = OUT / "weekly_eval_20261102.json"
OUT_MD = OUT / "weekly_eval_20261102.md"
HISTORY = OUT / "eval_regression_history.json"
DATE_TAG = "20261102"
SCHEMA_VERSION = "weekly_eval.v1"
HISTORY_MAX = 52
# 冻结基线（v3 基线全量回归，eval_v3_baseline_20261101）
FROZEN_BASELINE = {"precision_at_k": 0.800, "ndcg_at_k": 0.857, "coverage": 0.893}
METRIC_LABELS = {"precision_at_k": "P@5", "ndcg_at_k": "NDCG@5", "coverage": "覆盖率"}
DEFAULT_TOLERANCE = 0.02


# ---------------------------------------------------------------------------
# 当前指标（复用 v3 基线评测，不调 LLM）
# ---------------------------------------------------------------------------
def evaluate_current() -> dict:
    asset = base.load_v3()
    units = base.load_units()
    synonyms = base.load_synonyms()
    result = base.evaluate_queries(units, asset["queries"], synonyms)
    overall = result["overall"]["v2"]
    return {"metrics": {k: overall[k] for k in FROZEN_BASELINE},
            "v1_metrics": result["overall"]["v1"],
            "set_id": asset.get("set_id"), "content_hash": asset.get("content_hash"),
            "n_queries": asset.get("n_queries"),
            "subset": {"89": result["subset_89"]["v2"],
                       "13": result["subset_13"]["v2"]}}


# ---------------------------------------------------------------------------
# 偏差判定 / 报告 / 历史
# ---------------------------------------------------------------------------
def compare_metrics(current: dict, baseline: dict | None = None,
                    tolerance: float = DEFAULT_TOLERANCE) -> dict:
    baseline = baseline or FROZEN_BASELINE
    deltas, status, alerts = {}, {}, []
    for key, expected in baseline.items():
        cur = current.get(key)
        if cur is None:
            deltas[key] = None
            status[key] = "missing"
            alerts.append(f"{METRIC_LABELS.get(key, key)} 缺失")
            continue
        delta = round(float(cur) - float(expected), 4)
        deltas[key] = delta
        if abs(delta) > tolerance:
            status[key] = "alert"
            alerts.append(f"{METRIC_LABELS.get(key, key)} 偏差 {delta:+.4f} 超过阈值 ±{tolerance}"
                          f"（当前 {cur} vs 基线 {expected}）")
        else:
            status[key] = "ok"
    return {"baseline": dict(baseline), "deltas": deltas, "status": status,
            "alerts": alerts, "tolerance": tolerance, "ok": not alerts}


def build_report(current: dict, comparison: dict, *, tag: str = DATE_TAG) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "weekly_eval_regression",
        "date_tag": tag,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "eval_set": {"set_id": current.get("set_id"),
                     "content_hash": current.get("content_hash"),
                     "n_queries": current.get("n_queries")},
        "engine": "search_v2 默认（and_mode=soft, soft_cov=0.5, synonym_weight=0.6）",
        "current": current.get("metrics"),
        "baseline_frozen": comparison["baseline"],
        "deltas": comparison["deltas"],
        "status": comparison["status"],
        "tolerance": comparison["tolerance"],
        "alerts": comparison["alerts"],
        "ok": comparison["ok"],
        "v1_metrics": current.get("v1_metrics"),
        "subset": current.get("subset"),
        "disclaimer": "研究/回归监控输出；指标为冻结评测集上的确定性计算，不调用 LLM。",
    }


def append_history(report: dict, history_path: Path | str = HISTORY,
                   max_runs: int = HISTORY_MAX) -> dict:
    path = Path(history_path)
    data = {"schema_version": "eval_regression_history.v1", "runs": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"schema_version": "eval_regression_history.v1", "runs": []}
    entry = {"run_at": report.get("generated_at"), "date_tag": report.get("date_tag"),
             "set_id": (report.get("eval_set") or {}).get("set_id"),
             "content_hash": (report.get("eval_set") or {}).get("content_hash"),
             "current": report.get("current"), "baseline": report.get("baseline_frozen"),
             "deltas": report.get("deltas"), "status": report.get("status"),
             "tolerance": report.get("tolerance"), "ok": report.get("ok"),
             "alerts": report.get("alerts") or []}
    runs = list(data.get("runs") or [])
    runs.append(entry)
    data["runs"] = runs[-max_runs:]
    data["updated_at"] = entry["run_at"]
    data["total_runs"] = len(data["runs"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def render_md(rep: dict) -> str:
    L = [f"# 每周评测回归（{rep['date_tag']}）", ""]
    L.append(f"- 生成：{rep['generated_at']}；评测集 `{rep['eval_set'].get('set_id')}`"
             f"（v3，n={rep['eval_set'].get('n_queries')}，hash `{str(rep['eval_set'].get('content_hash'))[:16]}…`）")
    L.append(f"- 引擎：{rep['engine']}；阈值 ±{rep['tolerance']}；"
             f"状态：**{'✅ 通过' if rep['ok'] else '🔴 告警'}**")
    L.append("")
    L += ["| 指标 | 当前 | 冻结基线 | 偏差 | 状态 |", "|---|---|---|---|---|"]
    for key, label in METRIC_LABELS.items():
        cur = rep["current"].get(key)
        delta = rep["deltas"].get(key)
        mark = "✅" if rep["status"].get(key) == "ok" else "🔴"
        L.append(f"| {label} | {cur} | {rep['baseline_frozen'].get(key)} | "
                 f"{delta:+.4f} | {mark} |")
    L.append("")
    L += ["## 告警", ""]
    if rep["alerts"]:
        L += [f"- {x}" for x in rep["alerts"]]
    else:
        L += ["- 无告警 ✅（指标均在冻结基线 ±{:.2f} 内）".format(rep["tolerance"])]
    L.append("")
    L += ["## 子集参考", ""]
    subset = rep.get("subset") or {}
    for k, v in subset.items():
        L.append(f"- {'v2 89 子集' if k == '89' else '新增 13 条'}：P@5 {v.get('precision_at_k')}、"
                 f"NDCG {v.get('ndcg_at_k')}、覆盖率 {v.get('coverage')}")
    L.append("")
    L += ["## 口径与边界", "",
          "- 指标在冻结评测集 v3 上确定性计算（不调 LLM）；与冻结基线偏差 > ±0.02 即告警；",
          "- 知识库/检索变更导致漂移时，需排查 search_v2/知识单元并评估是否更新基线（更新需审批）；",
          "- 历史：`research/reports/eval_regression_history.json`（保留最近 52 次）；",
          "- 计划任务：ZeroBound_WeeklyEval（每周一 09:00，日志 C:\\Temp\\zero_weekly_eval.log）。", ""]
    return "\n".join(L)


def run(tag: str = DATE_TAG, tolerance: float = DEFAULT_TOLERANCE,
        write: bool = True, history_path: Path | str = HISTORY,
        json_path: Path | str = OUT_JSON, md_path: Path | str = OUT_MD) -> dict:
    current = evaluate_current()
    comparison = compare_metrics(current["metrics"], tolerance=tolerance)
    rep = build_report(current, comparison, tag=tag)
    if write:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        Path(md_path).write_text(render_md(rep), encoding="utf-8")
        rep["history_entry"] = append_history(rep, history_path)
        rep["outputs"] = {"json": str(json_path), "md": str(md_path),
                          "history": str(history_path)}
    return rep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="每周评测回归（v3 冻结基线漂移，不调 LLM）")
    ap.add_argument("--tag", default=DATE_TAG)
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    ap.add_argument("--no-history", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rep = run(tag=args.tag, tolerance=args.tolerance,
              history_path=(Path("/dev/null") if args.no_history else HISTORY))
    print(json.dumps({"tag": rep["date_tag"], "current": rep["current"],
                      "deltas": rep["deltas"], "ok": rep["ok"],
                      "alerts": rep["alerts"], "outputs": rep.get("outputs")},
                     ensure_ascii=False, indent=1), flush=True)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
