# -*- coding: utf-8 -*-
"""研报评审面板 v2：页码校验 + 多模型交叉 + 候选降维（TradingAgents 思路强化版）。

在 v1（tools/research/report_panel.py，不改动）基础上强化三点：
  ① 页码校验：每个角色 key_facts 的 page 必须落在该研报 knowledge_units 的
    page_start..page_end 并集内（容差 ±1）；越界/缺页码标记 page_invalid，
    该角色页码有效率 < 50% 时其 pass 票降权为 neutral（计入 effective_aggregate）。
  ② 多模型交叉：每篇用主模型 qwen3.5:9b + 交叉模型 qwen3.6:35b-a3b 各跑一次三角色；
    交叉模型失败/超时自动降级 qwen3.5:4b（标记 fallback_of）。计算：
    - verdict 一致率（角色级匹配率 + 模型级 verdict 是否一致）；
    - key_facts 重合度（仅用页码校验通过的事实，difflib 相似度 >= 0.45 贪心匹配）。
  ③ 候选降维：一篇进候选必须同时满足
      (a) pass 的模型数 >= 2（两模型都要 pass；降级模型同样计为一次模型跑分）；
      (b) 主模型 effective pass 角色数 >= 2；
      (c) 页码校验通过（该篇全部事实页码有效，无 page_invalid / page_missing）。
输出：research/reports/report_panel_v2_<date>.json / .md。

复用 v1 的函数：选篇、单元读取、prompt 构建、JSON 容错解析、投票聚合、规则式降级、Ollama 调用。
CLI：
  python tools/research/report_panel_v2.py run --reports 3 \
      --primary qwen3.5:9b --cross qwen3.6:35b-a3b --cross-fallback qwen3.5:4b
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "research" / "reports"
V1_JSON = OUT_DIR / "report_panel_20261101.json"
DEFAULT_MODELS = {
    "primary": "qwen3.5:9b",
    "cross": "qwen3.6:35b-a3b",
    "cross_fallback": "qwen3.5:4b",
}
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 600
PAGE_TOLERANCE = 1
FACT_SIM_THRESHOLD = 0.45
PAGE_FACT_VALID_RATIO = 0.5


def _load_v1():
    """按文件路径加载 v1 模块（复用且不修改 v1）。"""
    path = Path(__file__).with_name("report_panel.py")
    spec = importlib.util.spec_from_file_location("report_panel_v1", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 v1：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v1 = _load_v1()
ROLES = v1.ROLES
VERDICTS = v1.VERDICTS


# ---------------------------------------------------------------------------
# ① 页码校验
# ---------------------------------------------------------------------------
def allowed_pages(units: list[dict], max_span: int = 400) -> set[int]:
    """知识单元 page_start..page_end 的页码并集（防御异常反向/超长区间）。"""
    pages: set[int] = set()
    for u in units or []:
        start = u.get("page_start")
        if not isinstance(start, int) or isinstance(start, bool):
            continue
        end = u.get("page_end")
        if not isinstance(end, int) or isinstance(end, bool):
            end = start
        if end < start:
            start, end = end, start
        pages.update(range(start, min(end, start + max_span) + 1))
    return pages


def _in_allowed(page, allowed: set[int], tolerance: int = PAGE_TOLERANCE) -> bool:
    if not isinstance(page, int) or isinstance(page, bool):
        return False
    return any((page + d) in allowed for d in range(-tolerance, tolerance + 1))


def validate_role_facts(role_payload: dict, allowed: set[int],
                        tolerance: int = PAGE_TOLERANCE) -> dict:
    """逐条校验角色 key_facts 页码；返回校验明细（不修改原 payload）。"""
    facts = [f for f in (role_payload.get("key_facts") or []) if isinstance(f, dict)]
    valid, invalid, missing = [], [], []
    for f in facts:
        page = f.get("page")
        if page is None:
            missing.append({"fact": v1._clip(f.get("fact"), 120), "page": None})
        elif _in_allowed(page, allowed, tolerance):
            valid.append(f)
        else:
            invalid.append({"fact": v1._clip(f.get("fact"), 120), "page": page})
    total = len(facts)
    ratio = round(len(valid) / total, 3) if total else 1.0
    return {
        "role": role_payload.get("role"),
        "total": total,
        "valid": len(valid),
        "invalid": len(invalid),
        "missing": len(missing),
        "valid_ratio": ratio,
        "downweighted": total > 0 and ratio < PAGE_FACT_VALID_RATIO,
        "invalid_details": invalid,
        "missing_details": missing,
    }


def apply_page_validation(roles: list[dict], units: list[dict],
                          tolerance: int = PAGE_TOLERANCE) -> tuple[list[dict], dict]:
    """给每个角色加 page_check，并汇总全篇页码校验结果。"""
    allowed = allowed_pages(units)
    checked: list[dict] = []
    total = invalid = missing = 0
    details: list[dict] = []
    for role in roles:
        check = validate_role_facts(role, allowed, tolerance)
        total += check["total"]
        invalid += check["invalid"]
        missing += check["missing"]
        details.extend({"role": check["role"], **d} for d in check["invalid_details"])
        item = dict(role)
        item["page_check"] = check
        checked.append(item)
    summary = {
        "allowed_pages": sorted(allowed),
        "allowed_pages_count": len(allowed),
        "tolerance": tolerance,
        "total_facts": total,
        "invalid_facts": invalid,
        "missing_pages": missing,
        "invalid_details": details[:20],
        "valid_ratio": round((total - invalid - missing) / total, 3) if total else 1.0,
        "pass": total > 0 and invalid == 0 and missing == 0,
        "note": None if total else "无 key_facts，页码校验无从验证（判定不通过）",
    }
    return checked, summary


def effective_roles(roles: list[dict]) -> list[dict]:
    """页码有效率不足的角色，其 pass 票降权为 neutral（avoid/neutral 不变）。"""
    out = []
    for role in roles:
        check = role.get("page_check") or {}
        item = dict(role)
        if role.get("verdict") == "pass" and check.get("downweighted"):
            item["verdict"] = "neutral"
            item["downweighted_from"] = "pass"
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# ② 多模型交叉
# ---------------------------------------------------------------------------
def _normalize_fact(text: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"'’‘·…—\-]+", "", str(text or ""))


def match_key_facts(facts_a: list[dict], facts_b: list[dict],
                    threshold: float = FACT_SIM_THRESHOLD) -> dict:
    """两模型 key_facts 贪心相似匹配，返回重合度。"""
    norm_a = [(_normalize_fact(f.get("fact")), f) for f in facts_a]
    norm_b = [(_normalize_fact(f.get("fact")), f) for f in facts_b]
    used_b: set[int] = set()
    matched: list[dict] = []
    for ta, fa in norm_a:
        best_i, best_ratio = -1, 0.0
        for i, (tb, fb) in enumerate(norm_b):
            if i in used_b or not ta or not tb:
                continue
            ratio = SequenceMatcher(None, ta, tb).ratio()
            if ratio > best_ratio:
                best_i, best_ratio = i, ratio
        if best_i >= 0 and best_ratio >= threshold:
            used_b.add(best_i)
            matched.append({"model_a_fact": v1._clip(fa.get("fact"), 80), "page_a": fa.get("page"),
                            "model_b_fact": v1._clip(norm_b[best_i][1].get("fact"), 80),
                            "page_b": norm_b[best_i][1].get("page"),
                            "similarity": round(best_ratio, 3)})
    denom = max(len(norm_a), len(norm_b), 1)
    return {"matched_count": len(matched), "model_a_facts": len(norm_a),
            "model_b_facts": len(norm_b), "overlap_ratio": round(len(matched) / denom, 3),
            "matched_pairs": matched[:5], "threshold": threshold}


def _valid_facts(roles: list[dict]) -> list[dict]:
    out: list[dict] = []
    for role in roles:
        check = role.get("page_check") or {}
        if not check.get("invalid") and not check.get("missing"):
            out.extend(f for f in role.get("key_facts") or [] if isinstance(f, dict))
    return out


def compare_models(run_a: dict, run_b: dict) -> dict:
    """比较两次模型跑分：verdict 一致率 + key_facts 重合度。"""
    roles_a = {r.get("role"): r for r in run_a.get("roles") or []}
    roles_b = {r.get("role"): r for r in run_b.get("roles") or []}
    common = [role for role in ROLES if role in roles_a and role in roles_b]
    matches = [role for role in common
               if roles_a[role].get("verdict") == roles_b[role].get("verdict")]
    rate = round(len(matches) / len(common), 3) if common else 0.0
    agg_a = (run_a.get("aggregate") or {}).get("verdict")
    agg_b = (run_b.get("aggregate") or {}).get("verdict")
    return {
        "role_verdicts": {run["model"]: {role: roles[role].get("verdict") for role in ROLES if role in roles}
                          for run, roles in ((run_a, roles_a), (run_b, roles_b))},
        "model_verdicts": {run_a.get("model"): agg_a, run_b.get("model"): agg_b},
        "role_agreement_rate": rate,
        "matched_roles": matches,
        "verdict_agreement": bool(agg_a and agg_a == agg_b),
        "key_facts_overlap": match_key_facts(_valid_facts(run_a.get("roles") or []),
                                             _valid_facts(run_b.get("roles") or [])),
        "both_pass": agg_a == "pass" and agg_b == "pass",
    }


# ---------------------------------------------------------------------------
# 单模型 / 单篇跑分
# ---------------------------------------------------------------------------
def _call_role(role: str, report: dict, units: list[dict], model: str, chat_fn,
               timeout: float, deadline_at: float) -> tuple[dict, str | None]:
    remaining = deadline_at - time.monotonic()
    if remaining <= 1:
        return v1.rule_based_role(role, report, units, reason="deadline_exceeded"), "deadline_exceeded"
    prompt = v1.build_role_prompt(role, report, units)
    try:
        raw = chat_fn(prompt, model=model, timeout=max(5.0, min(timeout, remaining)))
        parsed = v1.parse_role_json(raw, role)
        parsed["source"] = "llm"
        parsed["fallback_reason"] = None
        return parsed, None
    except v1.PanelParseError as exc:
        return v1.rule_based_role(role, report, units, reason="parse_error"), f"parse_error: {v1._clip(exc, 140)}"
    except (v1.PanelError, TimeoutError, OSError, ValueError) as exc:
        return (v1.rule_based_role(role, report, units, reason="llm_error"),
                f"llm_error: {v1._clip(exc, 140)}")


def run_model_panel(report: dict, units: list[dict], model: str, chat_fn,
                    timeout: float, deadline_at: float, max_workers: int = 3) -> dict:
    """单模型三角色跑分（chat_fn 可注入 mock）。"""
    started = time.monotonic()
    errors: list[str] = []

    def _one(role: str):
        payload, err = _call_role(role, report, units, model, chat_fn, timeout, deadline_at)
        if err:
            errors.append(f"{role}@{model}: {err}")
        return payload

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(ROLES))) as pool:
            roles = list(pool.map(_one, ROLES))
    else:
        roles = [_one(role) for role in ROLES]
    return {"model": model, "roles": roles,
            "aggregate": v1.aggregate_roles(roles),
            "llm_ok_roles": sum(1 for r in roles if r.get("source") == "llm"),
            "fallback_roles": sum(1 for r in roles if r.get("source") == "rule_fallback"),
            "errors": errors, "elapsed_seconds": round(time.monotonic() - started, 1)}


def _model_run_with_page_check(report: dict, units: list[dict], model: str, chat_fn,
                               timeout: float, deadline_at: float, max_workers: int,
                               role: str = "primary", fallback_of: str | None = None) -> dict:
    run = run_model_panel(report, units, model, chat_fn, timeout, deadline_at, max_workers)
    checked, page_summary = apply_page_validation(run["roles"], units)
    run["roles"] = checked
    run["page_validation"] = page_summary
    eff = effective_roles(checked)
    run["effective_roles"] = [{"role": r.get("role"), "verdict": r.get("verdict"),
                               "downweighted_from": r.get("downweighted_from")} for r in eff]
    run["effective_aggregate"] = v1.aggregate_roles(eff)
    run["role"] = role
    run["fallback_of"] = fallback_of
    run["is_fallback"] = bool(fallback_of)
    run["model_pass"] = run["effective_aggregate"]["verdict"] == "pass"
    return run


def evaluate_report_v2(report: dict, units: list[dict], models: dict, chat_fn,
                       timeout: float, deadline_at: float, max_workers: int = 3) -> dict:
    """单篇 v2 评审：主模型 + 交叉模型（失败降级）+ 页码校验 + 交叉对比 + 候选判定。"""
    title = v1.report_title(report, units)
    fallback_notes: list[str] = []
    runs = [_model_run_with_page_check(report, units, models["primary"], chat_fn, timeout,
                                       deadline_at, max_workers, role="primary")]
    cross = _model_run_with_page_check(report, units, models["cross"], chat_fn, timeout,
                                       deadline_at, max_workers, role="cross")
    if cross["llm_ok_roles"] == 0:  # 交叉模型整体失败（被占满/超时/全降级）→ 换轻量模型
        fallback_notes.append(f"交叉模型 {models['cross']} 全部角色失败，降级 {models['cross_fallback']}")
        cross = _model_run_with_page_check(report, units, models["cross_fallback"], chat_fn, timeout,
                                           deadline_at, max_workers, role="cross",
                                           fallback_of=models["cross"])
    runs.append(cross)
    primary = runs[0]
    cross_model = compare_models(primary, runs[1]) if len(runs) > 1 else {}
    page_ok = all(r["page_validation"]["pass"] for r in runs)
    page_issue_count = sum(r["page_validation"]["invalid_facts"] + r["page_validation"]["missing_pages"]
                           for r in runs)
    models_pass = [r["model"] for r in runs if r["model_pass"]]
    primary_pass_roles = primary["effective_aggregate"]["votes"]["pass"]
    conditions = {
        "models_pass_ge_2": len(models_pass) >= 2,
        "primary_pass_roles_ge_2": primary_pass_roles >= 2,
        "page_validation_pass": page_ok,
    }
    candidate = all(conditions.values())
    reasons = []
    reasons.append(f"pass 模型 {len(models_pass)}/2" + (f"（{', '.join(models_pass)}）" if models_pass else ""))
    reasons.append(f"主模型 effective pass 角色数 {primary_pass_roles}/3")
    reasons.append(f"页码校验 {'通过' if page_ok else f'不通过（问题 {page_issue_count} 处）'}")
    return {
        "report_id": report.get("report_id"), "title": title, "date": report.get("date"),
        "pages": report.get("pages"), "units_total": report.get("units_total", len(units)),
        "units_used": len(units),
        "models": runs,
        "cross_model": cross_model,
        "page_validation": {
            "pass": page_ok,
            "issue_count": page_issue_count,
            "per_model": {r["model"]: {"invalid_facts": r["page_validation"]["invalid_facts"],
                                       "missing_pages": r["page_validation"]["missing_pages"],
                                       "allowed_pages_count": r["page_validation"]["allowed_pages_count"],
                                       "invalid_details": r["page_validation"]["invalid_details"]}
                          for r in runs},
        },
        "candidate": candidate,
        "candidate_decision": {"conditions": conditions,
                               "model_passes": models_pass,
                               "primary_pass_roles": primary_pass_roles,
                               "reasons": reasons},
        "fallback_notes": fallback_notes,
        "errors": [e for r in runs for e in r.get("errors") or []],
    }


# ---------------------------------------------------------------------------
# 主流程 + 输出
# ---------------------------------------------------------------------------
def load_v1_candidates(path: Path = V1_JSON) -> dict:
    """读取 v1 结果用于候选数对比（缺文件时优雅降级）。"""
    if not path.exists():
        return {"v1_file": str(path), "available": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"v1_file": str(path), "available": False}
    candidates = [c.get("report_id") for c in data.get("candidates") or []]
    return {"v1_file": str(path), "available": True,
            "v1_candidates_total": len(candidates),
            "v1_candidate_ids": candidates,
            "v1_candidate_verdicts": {p.get("report_id"): (p.get("aggregate") or {}).get("verdict")
                                      for p in data.get("panel") or []}}


def run_panel_v2(reports_n: int = 3, models: dict | None = None, chat_fn=None,
                 timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                 max_workers: int = 3, max_units: int = v1.DEFAULT_MAX_UNITS,
                 date_tag: str | None = None, root: Path | str = v1.DEFAULT_ROOT,
                 v1_json: Path = V1_JSON) -> dict:
    models = dict(DEFAULT_MODELS if models is None else models)
    chat_fn = chat_fn or v1.call_ollama
    started = time.monotonic()
    deadline_at = started + max(1.0, float(deadline_seconds))
    catalog = v1.load_report_catalog(root)
    selected = v1.select_reports(catalog, reports_n)
    panel: list[dict] = []
    reports_meta: list[dict] = []
    notes: list[str] = []
    llm_calls_ok = llm_calls_fallback = attempted = 0
    model_fallbacks = 0
    for rec in selected:
        units = v1.load_report_units(rec["path"], limit=max_units)
        report = dict(rec)
        report["units_total"] = rec.get("units_total", len(units))
        reports_meta.append({**{k: rec.get(k) for k in ("report_id", "date", "pages", "units_total", "path")},
                             "title": v1.report_title(report, units), "units_used": len(units)})
        item = evaluate_report_v2(report, units, models, chat_fn, timeout, deadline_at, max_workers)
        panel.append(item)
        for run in item["models"]:
            attempted += len(run["roles"])
            llm_calls_ok += run["llm_ok_roles"]
            llm_calls_fallback += run["fallback_roles"]
            model_fallbacks += 1 if run.get("is_fallback") else 0
        notes.extend(f"{item['report_id']}: {n}" for n in item["fallback_notes"])
    candidates = []
    for item in panel:
        if not item["candidate"]:
            continue
        candidates.append({
            "report_id": item["report_id"], "title": item["title"], "date": item.get("date"),
            "pages": item.get("pages"),
            "pass_models": item["candidate_decision"]["model_passes"],
            "primary_pass_roles": item["candidate_decision"]["primary_pass_roles"],
            "model_verdicts": {r["model"]: r["effective_aggregate"]["verdict"] for r in item["models"]},
            "role_agreement_rate": (item.get("cross_model") or {}).get("role_agreement_rate"),
            "page_issue_count": item["page_validation"]["issue_count"],
        })
    candidates.sort(key=lambda c: (-c["primary_pass_roles"], -(c.get("pages") or 0), c["report_id"]))
    v1_info = load_v1_candidates(v1_json)
    v1_scope = [r["report_id"] for r in reports_meta]
    v1_candidates_in_scope = [i for i in v1_info.get("v1_candidate_ids") or [] if i in v1_scope]
    comparison = {
        "v1_file": v1_info.get("v1_file"), "v1_available": v1_info.get("available", False),
        "v1_candidates_total": v1_info.get("v1_candidates_total"),
        "v1_candidates_in_scope": v1_candidates_in_scope,
        "v2_candidates": [c["report_id"] for c in candidates],
        "delta_in_scope": len(candidates) - len(v1_candidates_in_scope),
        "note": "v1=单模型 qwen3.5:9b、pass 票≥2 即进；v2=双模型 ≥2 模型 pass + 主模型 ≥2 角色 pass + 页码校验通过，候选显著降维",
    }
    if llm_calls_fallback:
        notes.append(f"{llm_calls_fallback} 个角色评审降级为规则式摘要（LLM 超时/JSON 截断不可解析/超 deadline），"
                     f"已在 panel[].models[].roles[].source=rule_fallback 与 errors 中标注；候选判定使用降级后的 effective 票。")
    if model_fallbacks:
        notes.append(f"{model_fallbacks} 个模型跑分使用了交叉模型降级（{models['cross']} → {models['cross_fallback']}），已标记 fallback_of。")
    if not catalog:
        notes.append(f"未在 {root} 找到已入库研报，面板为空。")
    elapsed = round(time.monotonic() - started, 1)
    if elapsed > deadline_seconds:
        notes.append(f"本次耗时 {elapsed}s 超出 deadline {deadline_seconds}s：超时角色已按规则式降级。")
    return {
        "schema_version": 2,
        "kind": "report_panel_v2",
        "approach": "TradingAgents 多智能体评审 + 页码校验 + 双模型交叉 + 候选降维",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_tag": date_tag or datetime.now().strftime("%Y%m%d"),
        "models": models,
        "llm": {"endpoint": v1.OLLAMA_ENDPOINT, "think": False, "timeout_seconds": timeout,
                "deadline_seconds": deadline_seconds,
                "role_calls_attempted": attempted, "llm_ok_roles": llm_calls_ok,
                "rule_fallback_roles": llm_calls_fallback,
                "model_fallbacks": model_fallbacks},
        "selection": {"reports_requested": reports_n, "reports_selected": len(selected),
                      "catalog_size": len(catalog),
                      "rule": "报告日期最新优先，同日按知识单元数/页数降序；零知识单元研报跳过",
                      "skipped_no_units": sum(1 for c in catalog if int(c.get("units_total") or 0) == 0)},
        "reports": reports_meta,
        "panel": panel,
        "candidates": candidates,
        "comparison_v1": comparison,
        "fallback": {"used": bool(llm_calls_fallback or model_fallbacks),
                     "rule_fallback_roles": llm_calls_fallback,
                     "model_fallbacks": model_fallbacks,
                     "notes": notes},
        "elapsed_seconds": elapsed,
    }


def render_markdown_v2(payload: dict) -> str:
    p = payload
    llm = p.get("llm") or {}
    sel = p.get("selection") or {}
    cmp = p.get("comparison_v1") or {}
    lines = [
        f"# 研报评审面板 v2（{p.get('date_tag', '')}）· 页码校验 + 双模型交叉 + 候选降维", "",
        "> 每篇研报由 3 角色（" + " / ".join(ROLES) + "）在 **2 个模型**上独立评审；"
        "进候选需：≥2 模型 pass + 主模型 ≥2 角色 pass + 页码校验通过。", "",
        "## 运行概览", "",
        f"- 生成时间：{p.get('generated_at')}",
        f"- 模型：主 `{(p.get('models') or {}).get('primary')}` / 交叉 `{(p.get('models') or {}).get('cross')}`"
        f"（失败降级 `{(p.get('models') or {}).get('cross_fallback')}`）",
        f"- 选篇：请求 {sel.get('reports_requested')} 篇，实际 {sel.get('reports_selected')} 篇"
        f"（候选目录 {sel.get('catalog_size')} 篇，跳过零单元 {sel.get('skipped_no_units')} 篇）",
        f"- 角色调用：{llm.get('llm_ok_roles')}/{llm.get('role_calls_attempted')} 走 LLM，"
        f"{llm.get('rule_fallback_roles')} 个规则降级，{llm.get('model_fallbacks')} 次模型降级；"
        f"耗时 {p.get('elapsed_seconds')}s",
        f"- **v1 对比**：v1 候选 {cmp.get('v1_candidates_total')} 篇（同范围 {len(cmp.get('v1_candidates_in_scope') or [])} 篇）"
        f"→ v2 候选 **{len(p.get('candidates') or [])} 篇**（本范围内 Δ{cmp.get('delta_in_scope')}）", "",
    ]
    if p.get("candidates"):
        lines += ["## v2 候选池", "",
                  "| # | 研报 | 日期 | 页数 | pass 模型 | 主模型 pass 角色 | 角色 verdict 一致率 | 页码问题 |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, c in enumerate(p["candidates"], 1):
            lines.append(f"| {i} | `{c['report_id']}` {c.get('title') or ''} | {c.get('date')} | "
                         f"{c.get('pages')} | {len(c.get('pass_models') or [])}/2 | "
                         f"{c.get('primary_pass_roles')}/3 | {c.get('role_agreement_rate')} | "
                         f"{c.get('page_issue_count')} |")
        lines.append("")
    else:
        lines += ["## v2 候选池", "", "无研报同时满足三项降维条件，候选池为空。", ""]
    lines += ["## 每篇面板", ""]
    for item in p.get("panel") or []:
        cross = item.get("cross_model") or {}
        overlap = cross.get("key_facts_overlap") or {}
        page = item.get("page_validation") or {}
        decision = item.get("candidate_decision") or {}
        cond = decision.get("conditions") or {}
        lines += [f"### `{item.get('report_id')}` {item.get('title') or ''}", "",
                  f"- 日期：{item.get('date')}；页数：{item.get('pages')}；"
                  f"单元：{item.get('units_used')}/{item.get('units_total')}",
                  f"- **候选判定：{'进候选池' if item.get('candidate') else '不进候选'}**；条件："
                  f"≥2 模型 pass={cond.get('models_pass_ge_2')}，"
                  f"主模型 ≥2 角色 pass={cond.get('primary_pass_roles_ge_2')}，"
                  f"页码校验通过={cond.get('page_validation_pass')}；"
                  f"{'；'.join(decision.get('reasons') or [])}",
                  f"- 跨模型：verdict 一致={cross.get('verdict_agreement')}；角色 verdict 一致率="
                  f"{cross.get('role_agreement_rate')}（匹配 {', '.join(cross.get('matched_roles') or []) or '无'}）；"
                  f"key_facts 重合度={overlap.get('overlap_ratio')}"
                  f"（{overlap.get('matched_count')}/{max(overlap.get('model_a_facts', 0), overlap.get('model_b_facts', 0), 1)}）",
                  f"- 页码校验：{'通过' if page.get('pass') else '不通过'}，问题 {page.get('issue_count')} 处"
                  f"（容差 ±{page.get('tolerance', PAGE_TOLERANCE)}）", ""]
        for run in item.get("models") or []:
            tag = f"{run.get('model')}" + (f"（降级自 {run.get('fallback_of')}）" if run.get("is_fallback") else "")
            eff = run.get("effective_aggregate") or {}
            v = eff.get("votes") or {}
            pv = run.get("page_validation") or {}
            lines.append(f"- **模型 {tag}** · aggregate=`{eff.get('verdict')}` "
                         f"(pass {v.get('pass', 0)} / avoid {v.get('avoid', 0)} / neutral {v.get('neutral', 0)})"
                         f" · 页码问题 {pv.get('invalid_facts', 0) + pv.get('missing_pages', 0)}"
                         f" · LLM 角色 {run.get('llm_ok_roles')}/3")
            for role in run.get("roles") or []:
                check = role.get("page_check") or {}
                down = f"（页码有效率 {check.get('valid_ratio')}，pass 票降权）" \
                    if check.get("downweighted") else ""
                src = "LLM" if role.get("source") == "llm" else f"规则降级({role.get('fallback_reason')})"
                lines.append(f"  - {role.get('role')} · `{role.get('verdict')}`{down} · {src}")
                for fact in role.get("key_facts") or []:
                    page_no = fact.get("page")
                    lines.append(f"    - 事实：{v1._clip(fact.get('fact'), 160)}（p.{page_no if page_no is not None else '?'}）")
                for risk in role.get("risks") or []:
                    lines.append(f"    - 风险：{v1._clip(risk, 160)}")
            if pv.get("invalid_details"):
                for d in pv["invalid_details"][:5]:
                    lines.append(f"    - ⚠ 页码越界：{d.get('role')} · {d.get('fact')}（p.{d.get('page')}）")
            if pv.get("missing_pages"):
                lines.append(f"    - ⚠ 缺页码事实 {pv.get('missing_pages')} 条")
        if item.get("errors"):
            lines.append(f"- 降级/异常：{'；'.join(item['errors'][:6])}")
        if item.get("fallback_notes"):
            lines.append(f"- 模型降级：{'；'.join(item['fallback_notes'])}")
        lines.append("")
    lines += ["## 局限与说明", ""]
    fb = p.get("fallback") or {}
    if fb.get("notes"):
        lines += [f"- {n}" for n in fb["notes"]]
    elif fb.get("used"):
        lines.append("- 存在角色/模型降级但未记录明细，详见 JSON panel[].errors。")
    else:
        lines.append("- 无 fallback：全部角色均由 LLM 完成，交叉模型未降级。")
    lines += [
        f"- 页码校验口径：page 需落在该篇知识单元 page_start..page_end 并集内（容差 ±{PAGE_TOLERANCE}）；"
        f"事实页码有效率 < {int(PAGE_FACT_VALID_RATIO * 100)}% 的角色其 pass 票降权为 neutral，但仍保留原文供人工复核。",
    ]
    pv_sum = p.get("page_validation_summary") or {}
    if pv_sum:
        lines.append(f"- 页码校验汇总：共 {pv_sum.get('total_facts')} 条事实，越界 {pv_sum.get('invalid_facts')} 条、"
                     f"缺页码 {pv_sum.get('missing_pages')} 条，问题研报 {pv_sum.get('reports_with_issues')} 篇；"
                     f"本次选中研报的单条知识单元页区间较宽（并集覆盖多页），±1 容差下校验拦截力有限，属口径局限。")
    lines += [
        "- 多模型交叉只反映「两个模型是否读同一份材料得出一致结论」，不证明结论正确；35b-a3b 为 MoE 交叉验证模型，"
        "若被占满会自动降级 4b 并在 JSON 标记 fallback_of。",
        "- key_facts 重合度用去标点后的 difflib 相似度（阈值 0.45）贪心匹配，中文转述差异会导致重合度偏低，仅作参考。",
        f"- v1 对比口径：{cmp.get('note')}",
        "- 每篇仅投喂知识单元摘要（claim+evidence 截断），非全文；本 MVP 不构成投资建议。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs_v2(payload: dict, json_path: Path | str, md_path: Path | str) -> tuple[Path, Path]:
    json_path, md_path = Path(json_path), Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path.write_text(render_markdown_v2(payload), encoding="utf-8")
    return json_path, md_path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研报评审面板 v2：页码校验+多模型交叉+候选降维")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 v2 面板并输出 JSON+MD")
    run.add_argument("--reports", type=int, default=3, help="评审研报数量（默认 3）")
    run.add_argument("--primary", default=DEFAULT_MODELS["primary"], help="主模型")
    run.add_argument("--cross", default=DEFAULT_MODELS["cross"], help="交叉模型")
    run.add_argument("--cross-fallback", default=DEFAULT_MODELS["cross_fallback"], help="交叉模型降级模型")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次 LLM 超时秒数")
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE, help="整体 deadline 秒数")
    run.add_argument("--workers", type=int, default=3, help="每模型角色并行数")
    run.add_argument("--max-units", type=int, default=v1.DEFAULT_MAX_UNITS, help="每篇最多投喂单元数")
    run.add_argument("--root", default=str(v1.DEFAULT_ROOT), help="研报入库根目录")
    run.add_argument("--date", default=None, help="输出文件名日期标签（默认今天）")
    run.add_argument("--out-json", default=None, help="JSON 输出路径")
    run.add_argument("--out-md", default=None, help="Markdown 输出路径")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    date_tag = args.date or datetime.now().strftime("%Y%m%d")
    out_json = Path(args.out_json) if args.out_json else OUT_DIR / f"report_panel_v2_{date_tag}.json"
    out_md = Path(args.out_md) if args.out_md else OUT_DIR / f"report_panel_v2_{date_tag}.md"
    models = {"primary": args.primary, "cross": args.cross, "cross_fallback": args.cross_fallback}
    payload = run_panel_v2(reports_n=args.reports, models=models, timeout=args.timeout,
                           deadline_seconds=args.deadline, max_workers=args.workers,
                           max_units=args.max_units, date_tag=date_tag, root=args.root)
    jp, mp = write_outputs_v2(payload, out_json, out_md)
    print(f"[report_panel_v2] reports={len(payload['panel'])} candidates={len(payload['candidates'])} "
          f"llm_ok={payload['llm']['llm_ok_roles']}/{payload['llm']['role_calls_attempted']} "
          f"rule_fallback={payload['llm']['rule_fallback_roles']} model_fallback={payload['llm']['model_fallbacks']} "
          f"elapsed={payload['elapsed_seconds']}s")
    print(f"[report_panel_v2] json={jp}")
    print(f"[report_panel_v2] md={mp}")
    return 0 if payload["panel"] else 1


if __name__ == "__main__":
    sys.exit(main())
