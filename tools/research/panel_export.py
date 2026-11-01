# -*- coding: utf-8 -*-
"""研报面板门户导出器：v3 面板 JSON → portal 静态 JSON（供 selection.html 展示）。

数据流：
  research/reports/report_panel_v3_<date>.json  （v3 多智能体面板产物）
      └─ tools/research/panel_export.py export
             └─ portal/static/report_panel.json   （门户静态数据，含免责声明）
                    └─ portal/selection.html「研报候选（多智能体）」区块 fetch 展示

导出的门户 JSON 字段（v1）：
  generated_at / exported_at / source / panel_version / date_tag / models
  disclaimer（短声明，必须含「仅供研究」「不构成投资建议」）
  summary    概览：报告数/候选数/被拦截数/页码越界/规则降级/耗时/结论
  candidates 候选池：研报 ID/标题/日期/页数/pass 模型/主模型 pass 角色/一致率/页码问题
  reports    逐篇：候选判定、拒绝原因、角色×模型 verdict、页码问题、一致率、key_facts 重合度
  rejected   被拦截研报（同结构，附拒绝原因，供页面「被拦截」页签）
  review     人工复核清单摘要：清单文件、覆盖篇数、事实/意见条数、勾选位统计
  page_issues 精确页码越界明细（研报/模型/角色/事实/页码/允许页码）
  disclaimer_full 完整免责声明

CLI：
  python tools/research/panel_export.py export           # 生成 portal/static/report_panel.json
  python tools/research/panel_export.py export --check   # 生成后校验 schema/声明字段
  python tools/research/panel_export.py check            # 仅校验现有门户 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / "research" / "reports" / "report_panel_v3_20261101.json"
DEFAULT_OUT = REPO / "portal" / "static" / "report_panel.json"
REVIEW_FILE = REPO / "research" / "reports" / "report_panel_v3_review.md"
DISCLAIMER = "仅供研究，不构成投资建议"
DISCLAIMER_FULL = ("本面板由本地 LLM 多智能体评审（基本面分析师/量化研究员/风控官 × 双模型）"
                   "自动生成，仅供研究参考，不构成任何投资建议；候选需经人工复核后方可使用。")
CHECKBOX = "☐"

REQUIRED_TOP = ("generated_at", "exported_at", "source", "panel_version", "disclaimer",
                "disclaimer_full", "summary", "candidates", "reports", "rejected",
                "review", "page_issues")


class PanelExportError(RuntimeError):
    """导出流程异常（输入缺失/结构不合法等）。"""


# ---------------------------------------------------------------------------
# 构建门户 payload
# ---------------------------------------------------------------------------
def _role_verdicts(item: dict) -> list[dict]:
    out: list[dict] = []
    for run in item.get("models") or []:
        model = run.get("model")
        for role in run.get("roles") or []:
            check = role.get("page_check") or {}
            out.append({"model": model, "model_role": run.get("role_kind"),
                        "role": role.get("role"), "verdict": role.get("verdict"),
                        "source": role.get("source"),
                        "page_valid_ratio": check.get("valid_ratio"),
                        "downweighted": bool(check.get("downweighted")),
                        "retried": bool(role.get("retried"))})
    return out


def _page_issues(item: dict) -> list[dict]:
    issues: list[dict] = []
    for model, pv in (item.get("page_validation") or {}).get("per_model", {}).items():
        for detail in pv.get("invalid_details") or []:
            issues.append({"report_id": item.get("report_id"), "model": model,
                           "role": detail.get("role"), "fact": detail.get("fact"),
                           "page": detail.get("page"),
                           "allowed_pages": pv.get("allowed_pages")})
    return issues


def _report_row(item: dict) -> dict:
    cross = item.get("cross_model") or {}
    overlap = cross.get("key_facts_overlap") or {}
    decision = item.get("candidate_decision") or {}
    page = item.get("page_validation") or {}
    return {
        "report_id": item.get("report_id"), "title": item.get("title"),
        "date": item.get("date"), "pages": item.get("pages"),
        "candidate": bool(item.get("candidate")),
        "verdicts": _role_verdicts(item),
        "model_verdicts": {r.get("model"): (r.get("effective_aggregate") or {}).get("verdict")
                           for r in item.get("models") or []},
        "role_agreement_rate": cross.get("role_agreement_rate"),
        "verdict_agreement": cross.get("verdict_agreement"),
        "key_facts_overlap": overlap.get("overlap_ratio"),
        "page_issue_count": page.get("issue_count", 0),
        "legacy_union_issue_count": page.get("legacy_union_issue_count", 0),
        "conditions": decision.get("conditions") or {},
        "reasons": decision.get("reasons") or [],
    }


def build_portal_payload(v3: dict, source_path: Path | str = DEFAULT_INPUT) -> dict:
    """把 v3 面板 JSON 压缩为门户展示 payload（缺字段容错）。"""
    if not isinstance(v3, dict):
        raise PanelExportError("v3 面板数据不是 JSON 对象")
    panel = v3.get("panel") or []
    candidates = [c for c in v3.get("candidates") or [] if isinstance(c, dict)]
    reports = [_report_row(item) for item in panel if isinstance(item, dict)]
    rejected = [row for row in reports if not row["candidate"]]
    page_issues = [issue for item in panel if isinstance(item, dict) for issue in _page_issues(item)]
    page_sum = v3.get("page_validation_summary") or {}
    llm = v3.get("llm") or {}
    conclusion = v3.get("conclusion") or {}
    checklist = [c for c in v3.get("review_checklist") or [] if isinstance(c, dict)]
    review_items = []
    for item in checklist:
        facts = item.get("facts") or []
        opinions = item.get("opinions") or []
        review_items.append({
            "report_id": item.get("report_id"), "title": item.get("title"),
            "is_candidate": bool(item.get("is_candidate")),
            "page_issue_count": item.get("page_issue_count", 0),
            "facts_total": len(facts),
            "facts_invalid": sum(1 for f in facts if not f.get("valid")),
            "opinions_total": len(opinions),
            "open_checkboxes": len(facts) + len(opinions) + 4,
            "reject_reasons": item.get("reject_reasons") or [],
        })
    models = v3.get("models") or {}
    return {
        "generated_at": v3.get("generated_at"),
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source_path),
        "panel_version": v3.get("schema_version", 3),
        "date_tag": v3.get("date_tag"),
        "models": {"primary": models.get("primary"), "cross": models.get("cross"),
                   "cross_fallback": models.get("cross_fallback")},
        "disclaimer": DISCLAIMER,
        "disclaimer_full": DISCLAIMER_FULL,
        "summary": {
            "reports": len(reports),
            "candidates": len(candidates),
            "rejected": len(rejected),
            "page_invalid_facts": page_sum.get("invalid_facts", len(page_issues)),
            "legacy_union_invalid_facts": page_sum.get("legacy_union_invalid_facts", 0),
            "reports_with_page_issues": page_sum.get("reports_with_issues", 0),
            "rule_fallback_roles": llm.get("rule_fallback_roles", 0),
            "role_calls_attempted": llm.get("role_calls_attempted", 0),
            "retries": llm.get("retries", 0),
            "elapsed_seconds": v3.get("elapsed_seconds"),
            "conclusion": {"verdict": conclusion.get("verdict"),
                           "passed": conclusion.get("passed"),
                           "total": conclusion.get("total"),
                           "met": conclusion.get("met")},
        },
        "candidates": candidates,
        "reports": reports,
        "rejected": rejected,
        "review": {
            "file": str(REVIEW_FILE),
            "note": "人工复核清单为必过环节：勾选确认页码/事实/角色意见后方可进入下游。",
            "items": review_items,
            "facts_total": sum(i["facts_total"] for i in review_items),
            "facts_invalid": sum(i["facts_invalid"] for i in review_items),
            "open_checkboxes": sum(i["open_checkboxes"] for i in review_items),
        },
        "page_issues": page_issues,
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_portal_payload(payload: dict) -> list[str]:
    """校验门户 JSON schema/声明字段；返回错误列表（空=通过）。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["门户 JSON 顶层不是对象"]
    for key in REQUIRED_TOP:
        if key not in payload:
            errors.append(f"缺少字段：{key}")
    disclaimer = str(payload.get("disclaimer") or "")
    for phrase in ("仅供研究", "不构成投资建议"):
        if phrase not in disclaimer:
            errors.append(f"disclaimer 缺少必需声明：{phrase}")
    if "不构成任何投资建议" not in str(payload.get("disclaimer_full") or ""):
        errors.append("disclaimer_full 缺少「不构成任何投资建议」")
    if not payload.get("generated_at"):
        errors.append("generated_at 为空")
    if not isinstance(payload.get("candidates"), list):
        errors.append("candidates 不是数组")
    if not isinstance(payload.get("reports"), list) or not payload.get("reports"):
        errors.append("reports 为空或不是数组")
    if not isinstance(payload.get("rejected"), list):
        errors.append("rejected 不是数组")
    for row in payload.get("reports") or []:
        if not isinstance(row, dict) or not row.get("report_id"):
            errors.append("reports 行缺少 report_id")
            break
        if not isinstance(row.get("verdicts"), list) or not row.get("verdicts"):
            errors.append(f"reports[{row.get('report_id')}] 缺角色 verdict")
            break
    review = payload.get("review")
    if not isinstance(review, dict) or "items" not in review:
        errors.append("review 缺少 items")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary 不是对象")
    elif summary.get("candidates") != len(payload.get("candidates") or []):
        errors.append("summary.candidates 与 candidates 长度不一致")
    return errors


def load_json(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        raise PanelExportError(f"输入文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PanelExportError(f"输入文件读取/解析失败：{path} ({exc})") from exc
    if not isinstance(data, dict):
        raise PanelExportError(f"输入文件顶层不是 JSON 对象：{path}")
    return data


def export_panel(input_path: Path | str = DEFAULT_INPUT, out_path: Path | str = DEFAULT_OUT,
                 check: bool = False) -> dict:
    """读 v3 面板 JSON → 写门户静态 JSON；check=True 时写后校验。"""
    input_path, out_path = Path(input_path), Path(out_path)
    v3 = load_json(input_path)
    payload = build_portal_payload(v3, source_path=input_path)
    errors = validate_portal_payload(payload)
    if errors:
        raise PanelExportError("门户 payload 校验失败：" + "；".join(errors))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if check:
        errors = validate_portal_payload(load_json(out_path))
        if errors:
            raise PanelExportError("落盘后校验失败：" + "；".join(errors))
    return payload


def check_portal_json(out_path: Path | str = DEFAULT_OUT) -> list[str]:
    """校验已落盘的门户 JSON；返回错误列表。"""
    try:
        payload = load_json(out_path)
    except PanelExportError as exc:
        return [str(exc)]
    return validate_portal_payload(payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研报面板 → 门户静态 JSON 导出器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="导出 portal/static/report_panel.json")
    exp.add_argument("--input", default=str(DEFAULT_INPUT), help="v3 面板 JSON 路径")
    exp.add_argument("--out", default=str(DEFAULT_OUT), help="门户静态 JSON 输出路径")
    exp.add_argument("--check", action="store_true", help="写出后校验 schema/声明字段")
    chk = sub.add_parser("check", help="仅校验现有门户 JSON")
    chk.add_argument("--out", default=str(DEFAULT_OUT), help="门户静态 JSON 路径")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "export":
        try:
            payload = export_panel(args.input, args.out, check=args.check)
        except PanelExportError as exc:
            print(f"[panel_export] FAILED: {exc}", file=sys.stderr)
            return 1
        summary = payload["summary"]
        print(f"[panel_export] ok src={args.input}")
        print(f"[panel_export] out={args.out}")
        print(f"[panel_export] reports={summary['reports']} candidates={summary['candidates']} "
              f"rejected={summary['rejected']} page_issues={summary['page_invalid_facts']} "
              f"conclusion={summary['conclusion']['verdict']} "
              f"generated_at={payload['generated_at']}")
        print(f"[panel_export] disclaimer={payload['disclaimer']}")
        return 0
    errors = check_portal_json(args.out)
    if errors:
        print(f"[panel_export] check FAILED ({len(errors)}): " + "；".join(errors), file=sys.stderr)
        return 1
    print(f"[panel_export] check OK: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
