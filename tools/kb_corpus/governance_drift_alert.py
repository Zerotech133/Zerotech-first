# -*- coding: utf-8 -*-
"""W0-4 治理漂移告警：读最新知识治理报告 + 基线，按阈值判定漂移并告警。

输入（只读，不改 knowledge_governance.py / learning_gate.py）：
  - research/reports/knowledge_governance_<date>.json（最新治理报告：metrics/drift/authenticity）
  - research/reports/governance_baseline_<date>.json（基线快照）
阈值（任务 W0-4）：
  - 核验率下降 > 5pp        → VERIFIED_DROP
  - 重复率上升 > 5pp        → DUP_RISE
  - 多源概念数下降 > 10     → MULTI_SOURCE_DROP
  - 冲突数 > 5              → CONFLICT_HIGH
行为：默认 dry-run（只生成告警文本/报告）；`--send` 时调用
news_collector/send_feishu.send_text 发飞书文本（失败降级记录，不抛异常）。
输出：research/reports/governance_drift_alert_<YYYYMMDD>.json

CLI：
  python tools/kb_corpus/governance_drift_alert.py run                # dry-run
  python tools/kb_corpus/governance_drift_alert.py run --send         # 发飞书
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "research" / "reports"
THRESHOLDS = {"verified_drop_pp": 5.0, "dup_rise_pp": 5.0,
              "multi_source_drop": 10, "conflicts_max": 5}
METRIC_KEYS = ("units", "knowledge_units", "verified", "verified_pct", "unverifiable",
               "exact_dup_units", "near_dup_pairs", "dup_ratio_pct", "card_concept_dup",
               "candidate_pool", "multi_source_concepts", "conflicts")


class DriftAlertError(RuntimeError):
    """治理漂移告警异常。"""


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_file(pattern: str, reports_dir: Path = REPORTS) -> Path | None:
    files = sorted(Path(reports_dir).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _metrics(data: dict | None) -> dict:
    if not data:
        return {}
    m = data.get("metrics") or {}
    return {k: m.get(k) for k in METRIC_KEYS}


def extract_governance(gov: dict, baseline_doc: dict | None) -> tuple[dict, dict, dict]:
    """返回 (current_metrics, baseline_metrics, delta)。

    优先使用治理报告内嵌 drift.baseline（上一基线）/ drift.delta；
    缺失时用 governance_baseline 文件 metrics 对比。
    """
    current = _metrics(gov)
    drift = gov.get("drift") or {}
    embedded = drift.get("baseline") or {}
    if embedded:
        baseline = {k: embedded.get(k) for k in METRIC_KEYS}
        baseline.setdefault("verified_pct", embedded.get("verified_pct"))
        delta = dict(drift.get("delta") or {})
    else:
        baseline = _metrics(baseline_doc)
        delta = {}
    if not delta:
        delta = {}
        for key in ("verified_pct", "dup_ratio_pct", "multi_source_concepts", "conflicts"):
            if current.get(key) is not None and baseline.get(key) is not None:
                delta[key] = round(float(current[key]) - float(baseline[key]), 2)
    return current, baseline, delta


def evaluate_drift(current: dict, baseline: dict, delta: dict,
                   thresholds: dict | None = None) -> list[dict]:
    t = thresholds or THRESHOLDS
    drifts = []
    d_verified = float(delta.get("verified_pct") or 0.0)
    d_dup = float(delta.get("dup_ratio_pct") or 0.0)
    d_multi = float(delta.get("multi_source_concepts") or 0.0)
    conflicts = int(current.get("conflicts") or 0)
    if d_verified < -float(t["verified_drop_pp"]):
        drifts.append({"code": "VERIFIED_DROP", "level": "warn",
                       "message": f"核验率下降 {-d_verified:.1f}pp（> {t['verified_drop_pp']:.0f}pp 阈值）",
                       "current": current.get("verified_pct"), "baseline": baseline.get("verified_pct"),
                       "delta": d_verified})
    if d_dup > float(t["dup_rise_pp"]):
        drifts.append({"code": "DUP_RISE", "level": "warn",
                       "message": f"重复率上升 {d_dup:.1f}pp（> {t['dup_rise_pp']:.0f}pp 阈值）",
                       "current": current.get("dup_ratio_pct"), "baseline": baseline.get("dup_ratio_pct"),
                       "delta": d_dup})
    if d_multi < -float(t["multi_source_drop"]):
        drifts.append({"code": "MULTI_SOURCE_DROP", "level": "warn",
                       "message": f"多源概念数下降 {-d_multi:.0f}（> {t['multi_source_drop']} 阈值）",
                       "current": current.get("multi_source_concepts"),
                       "baseline": baseline.get("multi_source_concepts"), "delta": d_multi})
    if conflicts > int(t["conflicts_max"]):
        drifts.append({"code": "CONFLICT_HIGH", "level": "warn",
                       "message": f"冲突数 {conflicts} > {t['conflicts_max']} 阈值",
                       "current": conflicts, "baseline": baseline.get("conflicts"),
                       "delta": int(delta.get("conflicts") or 0)})
    return drifts


def format_alert_text(date: str, drifts: list[dict], current: dict, baseline: dict) -> str:
    if not drifts:
        return (f"【知识治理漂移日报 {date}】正常：核验率 {current.get('verified_pct')}%、"
                f"重复率 {current.get('dup_ratio_pct')}%、多源概念 {current.get('multi_source_concepts')}、"
                f"冲突 {current.get('conflicts')}")
    lines = [f"【知识治理漂移告警 {date}】发现 {len(drifts)} 项漂移："]
    for d in drifts:
        lines.append(f"- [{d['code']}] {d['message']}")
    lines.append(f"当前：核验率 {current.get('verified_pct')}%、重复率 {current.get('dup_ratio_pct')}%、"
                 f"多源 {current.get('multi_source_concepts')}、冲突 {current.get('conflicts')}；"
                 f"基线：核验率 {baseline.get('verified_pct')}%、重复率 {baseline.get('dup_ratio_pct')}%、"
                 f"多源 {baseline.get('multi_source_concepts')}、冲突 {baseline.get('conflicts')}")
    return "\n".join(lines)


def send_alert(text: str) -> dict:
    """--send：调用 news_collector/send_feishu.send_text；失败返回 error 不抛出。"""
    try:
        nc = REPO / "news_collector"
        if nc.exists() and str(nc) not in sys.path:
            sys.path.insert(0, str(nc))
        from send_feishu import send_text  # noqa: PLC0415

        ok = bool(send_text(text, kind="governance_drift_alert"))
        return {"sent": ok, "error": None}
    except Exception as exc:  # noqa: BLE001 - 告警失败降级
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


def build_payload(gov: dict, baseline_doc: dict | None, *, send: bool = False,
                  send_fn=None, report_path: str | None = None,
                  baseline_path: str | None = None,
                  thresholds: dict | None = None) -> dict:
    current, baseline, delta = extract_governance(gov, baseline_doc)
    drifts = evaluate_drift(current, baseline, delta, thresholds)
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    alert_text = format_alert_text(date, drifts, current, baseline)
    send_result = {"attempted": False, "sent": False, "error": None}
    if send and drifts:
        fn = send_fn or send_alert
        try:
            sent = fn(alert_text) or {}
            send_result = {"attempted": True, "sent": bool(sent.get("sent")),
                           "error": sent.get("error")}
        except Exception as exc:  # noqa: BLE001 - 发送失败降级（不阻断报告）
            send_result = {"attempted": True, "sent": False,
                           "error": f"{type(exc).__name__}: {exc}"}
    return {
        "schema_version": 1, "kind": "governance_drift_alert",
        "date": date, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dry_run": not send,
        "thresholds": dict(thresholds or THRESHOLDS),
        "sources": {"report": report_path, "baseline": baseline_path},
        "current": current, "baseline": baseline, "delta": delta,
        "drifts": drifts, "alert_count": len(drifts), "alert_text": alert_text,
        "send": send_result,
        "note": "只读治理报告/基线；默认 dry-run，--send 才发飞书；不改 knowledge_governance/learning_gate。",
        "disclaimer": "治理漂移告警仅供运维监控参考，不构成投资建议。",
    }


def run(report_path: str | None = None, baseline_path: str | None = None,
        out_dir: Path | str = REPORTS, send: bool = False, send_fn=None,
        thresholds: dict | None = None) -> dict:
    gov_path = Path(report_path) if report_path else latest_file("knowledge_governance_*.json")
    base_path = Path(baseline_path) if baseline_path else latest_file("governance_baseline_*.json")
    if not gov_path or not gov_path.exists():
        raise DriftAlertError("缺少最新知识治理报告 knowledge_governance_*.json")
    gov = load_json(gov_path)
    if not gov:
        raise DriftAlertError(f"治理报告读取失败：{gov_path}")
    baseline_doc = load_json(base_path) if base_path and base_path.exists() else None
    payload = build_payload(gov, baseline_doc, send=send, send_fn=send_fn,
                            report_path=str(gov_path), baseline_path=str(base_path) if base_path else None,
                            thresholds=thresholds)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"governance_drift_alert_{payload['date'].replace('-', '')}.json"
    payload["out_json"] = str(out_path)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="知识治理漂移告警（W0-4）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="读最新治理报告/基线 → 判定漂移 → 输出 JSON")
    r.add_argument("--report", default=None, help="knowledge_governance_*.json 路径（默认最新）")
    r.add_argument("--baseline", default=None, help="governance_baseline_*.json 路径（默认最新）")
    r.add_argument("--out-dir", default=str(REPORTS))
    r.add_argument("--send", action="store_true", help="有漂移时发飞书（默认 dry-run）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run(args.report, args.baseline, args.out_dir, send=args.send)
    except DriftAlertError as exc:
        print(f"[governance-drift] FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"[governance-drift] date={payload['date']} dry_run={payload['dry_run']} "
          f"alerts={payload['alert_count']} drifts={[d['code'] for d in payload['drifts']]}")
    print(payload["alert_text"])
    print(f"[governance-drift] json={payload['out_json']}")
    if payload["send"]["attempted"]:
        print(f"[governance-drift] send={payload['send']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
