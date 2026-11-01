# -*- coding: utf-8 -*-
"""研报评审面板 v3：修复截断/超时 + 精确页码校验 + 人工复核清单。

相对 v2 的三项修复（v1/v2 文件均不改动，函数复用）：
  ① LLM 稳定性：num_predict 从 800 提到 1600（≥1200），单次 timeout 300s、
    整体 deadline 默认 900s（≥900s）；角色 JSON 解析失败自动「修复重试」一次
    （把原始输出+错误喂回模型，要求只输出严格 JSON），统计重试/成功次数。
  ② 页码精确口径：key_facts 的 page 必须落在其来源知识单元的 page_start ±1
    （替代 v2 的 page_start..page_end 宽并集）；同时保留 v2 宽并集口径做对照，
    精确口径越界逐条标记 page_invalid，角色事实有效率 <50% 时 pass 票降权。
  ③ 人工复核清单：research/reports/report_panel_v3_review.md，逐篇候选（含被
    精确页码拦截的研报）列出关键事实/页码/角色意见 + ☐ 勾选位。
  ④ 输出 v3 JSON/MD 及与 v2 的对比（候选数/fallback 次数/页码越界数/耗时），
    并自动给出「候选初筛 + 人工复核」达标结论。

CLI：
  python tools/research/report_panel_v3.py run --reports 3 \
      --primary qwen3.5:9b --cross qwen3.6:35b-a3b --cross-fallback qwen3.5:4b
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
OUT_DIR = REPO / "research" / "reports"
V2_JSON = OUT_DIR / "report_panel_v2_20261101.json"
DEFAULT_MODELS = {"primary": "qwen3.5:9b", "cross": "qwen3.6:35b-a3b",
                  "cross_fallback": "qwen3.5:4b"}
NUM_PREDICT = 1600            # ≥1200：修复 v2 num_predict=800 截断
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 900        # ≥900s：修复 v2 480s deadline 超时
DEFAULT_RETRIES = 1
PAGE_TOLERANCE = 1            # 精确口径：来源单元 page_start ±1
PAGE_FACT_VALID_RATIO = 0.5
REVIEW_CONCLUSION = "候选初筛 + 人工复核"


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v2 = _load_module("report_panel_v2_for_v3", "report_panel_v2.py")
v1 = v2.v1
ROLES = v1.ROLES


# ---------------------------------------------------------------------------
# ① LLM 调用（num_predict 调大）+ 解析失败重试
# ---------------------------------------------------------------------------
def call_ollama_v3(prompt: str, model: str = DEFAULT_MODELS["primary"],
                   timeout: float = DEFAULT_TIMEOUT, num_predict: int = NUM_PREDICT) -> str:
    """调 Ollama /api/chat，num_predict=1600；失败抛 v1.PanelError。"""
    import urllib.error
    import urllib.request
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": int(num_predict)}}
    req = urllib.request.Request(v1.OLLAMA_ENDPOINT, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise v1.PanelError(f"Ollama 调用失败：{exc}") from exc
    content = ((payload.get("message") or {}).get("content") or "").strip()
    if not content:
        raise v1.PanelError("Ollama 返回空内容")
    return content


def build_retry_prompt(role: str, report: dict, raw_output: str) -> str:
    """解析失败后的一次性修复 prompt：明确要求只输出严格 JSON。"""
    return (
        f"你上一轮以{role}身份评审研报 {report.get('report_id')} 时，输出无法被解析为 JSON。\n"
        f"上一轮输出片段：\n{raw_output[:1200]}\n\n"
        f"请重新只输出一个严格 JSON 对象（不要 markdown、不要解释），字段："
        f'{{"role":"{role}","verdict":"pass|avoid|neutral","reasons":["2-3条"],'
        f'"key_facts":[{{"fact":"事实","page":整数页码}}],"risks":["1-2条"]}}。\n'
        f"注意：key_facts 的 page 必须使用知识单元的起始页码（即摘要中 [p.X-Y] 里的 X）±1 范围内。\n"
    )


def build_role_prompt_v3(role: str, report: dict, units: list[dict]) -> str:
    """v1 prompt + 精确页码硬约束（page 用来源单元 page_start ±1）。"""
    base = v1.build_role_prompt(role, report, units)
    page_starts = sorted({u.get("page_start") for u in units if isinstance(u.get("page_start"), int)})
    pages = "、".join(str(p) for p in page_starts) or "（无）"
    return base + (
        f"\n【页码硬约束（v3）】key_facts 的 page 必须是该事实来源知识单元的起始页码（{pages}）"
        f"±{PAGE_TOLERANCE} 范围内的整数，禁止编造其它页码；无法确定页码时省略该事实。"
        f"输出 JSON 必须完整闭合，避免被截断。\n"
    )


def _call_role_v3(role: str, report: dict, units: list[dict], model: str, chat_fn,
                  timeout: float, deadline_at: float, num_predict: int,
                  retries: int, stats: dict) -> tuple[dict, str | None]:
    remaining = deadline_at - time.monotonic()
    if remaining <= 1:
        return v1.rule_based_role(role, report, units, reason="deadline_exceeded"), "deadline_exceeded"
    prompt = build_role_prompt_v3(role, report, units)
    try:
        raw = chat_fn(prompt, model=model, timeout=max(5.0, min(timeout, remaining)),
                      num_predict=num_predict)
        return _parse_with_retry(role, report, units, model, chat_fn, raw, timeout,
                                 deadline_at, num_predict, retries, stats, prompt), None
    except v1.PanelParseError as exc:  # pragma: no cover - 由 _parse_with_retry 内部处理
        return v1.rule_based_role(role, report, units, reason="parse_error"), f"parse_error: {v1._clip(exc, 140)}"
    except (v1.PanelError, TimeoutError, OSError, ValueError) as exc:
        return (v1.rule_based_role(role, report, units, reason="llm_error"),
                f"llm_error: {v1._clip(exc, 140)}")


def _parse_with_retry(role, report, units, model, chat_fn, raw, timeout, deadline_at,
                      num_predict, retries, stats, base_prompt) -> dict:
    """解析 LLM 输出；失败时用修复 prompt 重试（最多 retries 次）。"""
    try:
        parsed = v1.parse_role_json(raw, role)
        parsed["source"] = "llm"
        parsed["retried"] = False
        parsed["fallback_reason"] = None
        return parsed
    except v1.PanelParseError:
        stats["parse_errors"] += 1
        if retries <= 0 or deadline_at - time.monotonic() <= 5:
            raise
        for attempt in range(1, retries + 1):
            remaining = deadline_at - time.monotonic()
            if remaining <= 5:
                raise
            stats["retries"] += 1
            repair = build_retry_prompt(role, report, raw)
            try:
                raw2 = chat_fn(repair, model=model, timeout=max(5.0, min(timeout, remaining)),
                               num_predict=num_predict)
                parsed = v1.parse_role_json(raw2, role)
                parsed["source"] = "llm_retry"
                parsed["retried"] = True
                parsed["retry_attempts"] = attempt
                parsed["fallback_reason"] = None
                stats["retries_succeeded"] += 1
                return parsed
            except (v1.PanelParseError, v1.PanelError, TimeoutError, OSError, ValueError) as exc2:
                stats["parse_errors"] += 1
                if attempt >= retries:
                    raise v1.PanelParseError(str(exc2)) from exc2
        raise


# ---------------------------------------------------------------------------
# ② 精确页码校验（来源单元 page_start ±1）
# ---------------------------------------------------------------------------
def precise_allowed_pages(units: list[dict], tolerance: int = PAGE_TOLERANCE) -> set[int]:
    """精确口径：每条知识单元取 page_start，允许 ±tolerance（替代宽并集）。"""
    starts = {u.get("page_start") for u in units or [] if isinstance(u.get("page_start"), int)}
    allowed: set[int] = set()
    for start in starts:
        allowed.update(range(start - tolerance, start + tolerance + 1))
    return allowed


def legacy_allowed_pages(units: list[dict]) -> set[int]:
    """v2 宽并集口径（page_start..page_end），仅用于对照。"""
    return v2.allowed_pages(units)


def apply_page_validation_v3(roles: list[dict], units: list[dict]) -> tuple[list[dict], dict]:
    """精确页码校验：逐条 key_fact 校验 + 角色降权 + v2 宽口径对照。"""
    allowed = precise_allowed_pages(units)
    legacy = legacy_allowed_pages(units)
    checked: list[dict] = []
    total = invalid = missing = legacy_invalid = 0
    details: list[dict] = []
    for role in roles:
        check = v2.validate_role_facts(role, allowed, tolerance=0)
        legacy_check = v2.validate_role_facts(role, legacy, tolerance=1)
        total += check["total"]
        invalid += check["invalid"]
        missing += check["missing"]
        legacy_invalid += legacy_check["invalid"] + legacy_check["missing"]
        details.extend({"role": check["role"], **d} for d in check["invalid_details"])
        item = dict(role)
        item["page_check"] = check
        item["page_check"]["mode"] = "precise_unit_page_start_+-1"
        item["page_check"]["legacy_invalid"] = legacy_check["invalid"] + legacy_check["missing"]
        checked.append(item)
    summary = {
        "mode": "precise: 来源知识单元 page_start ±1（替代 v2 宽并集）",
        "source_page_starts": sorted({u.get("page_start") for u in units
                                      if isinstance(u.get("page_start"), int)}),
        "allowed_pages": sorted(allowed),
        "allowed_pages_count": len(allowed),
        "tolerance": PAGE_TOLERANCE,
        "total_facts": total,
        "invalid_facts": invalid,
        "missing_pages": missing,
        "legacy_union_invalid_facts": legacy_invalid,
        "invalid_details": details[:30],
        "valid_ratio": round((total - invalid - missing) / total, 3) if total else 1.0,
        "pass": total > 0 and invalid == 0 and missing == 0,
        "note": None if total else "无 key_facts，页码校验无从验证（判定不通过）",
    }
    return checked, summary


# ---------------------------------------------------------------------------
# 单模型 / 单篇跑分（复核 v2 结构，改用 v3 调用与校验）
# ---------------------------------------------------------------------------
def run_model_panel_v3(report: dict, units: list[dict], model: str, chat_fn,
                       timeout: float, deadline_at: float, num_predict: int = NUM_PREDICT,
                       retries: int = DEFAULT_RETRIES, max_workers: int = 3,
                       role_kind: str = "primary", fallback_of: str | None = None) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    started = time.monotonic()
    errors: list[str] = []
    stats = {"retries": 0, "retries_succeeded": 0, "parse_errors": 0}

    def _one(role: str):
        payload, err = _call_role_v3(role, report, units, model, chat_fn, timeout,
                                     deadline_at, num_predict, retries, stats)
        if err:
            errors.append(f"{role}@{model}: {err}")
        return payload

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(ROLES))) as pool:
            roles = list(pool.map(_one, ROLES))
    else:
        roles = [_one(role) for role in ROLES]
    checked, page_summary = apply_page_validation_v3(roles, units)
    eff = v2.effective_roles(checked)
    return {"model": model, "role_kind": role_kind, "fallback_of": fallback_of,
            "is_fallback": bool(fallback_of), "roles": checked,
            "aggregate": v1.aggregate_roles(roles), "page_validation": page_summary,
            "effective_roles": [{"role": r.get("role"), "verdict": r.get("verdict"),
                                 "downweighted_from": r.get("downweighted_from")} for r in eff],
            "effective_aggregate": v1.aggregate_roles(eff),
            "llm_ok_roles": sum(1 for r in roles if str(r.get("source", "")).startswith("llm")),
            "rule_fallback_roles": sum(1 for r in roles if r.get("source") == "rule_fallback"),
            "retried_roles": sum(1 for r in roles if r.get("retried")),
            "retry_stats": stats, "errors": errors,
            "elapsed_seconds": round(time.monotonic() - started, 1)}


def evaluate_report_v3(report: dict, units: list[dict], models: dict, chat_fn,
                       timeout: float, deadline_at: float, num_predict: int,
                       retries: int, max_workers: int = 3) -> dict:
    title = v1.report_title(report, units)
    fallback_notes: list[str] = []
    runs = [run_model_panel_v3(report, units, models["primary"], chat_fn, timeout, deadline_at,
                               num_predict, retries, max_workers, role_kind="primary")]
    cross = run_model_panel_v3(report, units, models["cross"], chat_fn, timeout, deadline_at,
                               num_predict, retries, max_workers, role_kind="cross")
    if cross["llm_ok_roles"] == 0:
        fallback_notes.append(f"交叉模型 {models['cross']} 全部角色失败，降级 {models['cross_fallback']}")
        cross = run_model_panel_v3(report, units, models["cross_fallback"], chat_fn, timeout,
                                   deadline_at, num_predict, retries, max_workers,
                                   role_kind="cross", fallback_of=models["cross"])
    runs.append(cross)
    cross_model = v2.compare_models(runs[0], runs[1])
    page_ok = all(r["page_validation"]["pass"] for r in runs)
    page_issue_count = sum(r["page_validation"]["invalid_facts"] + r["page_validation"]["missing_pages"]
                           for r in runs)
    legacy_issue_count = sum(r["page_validation"]["legacy_union_invalid_facts"] for r in runs)
    models_pass = [r["model"] for r in runs if r["effective_aggregate"]["verdict"] == "pass"]
    primary_pass_roles = runs[0]["effective_aggregate"]["votes"]["pass"]
    conditions = {"models_pass_ge_2": len(models_pass) >= 2,
                  "primary_pass_roles_ge_2": primary_pass_roles >= 2,
                  "page_validation_pass": page_ok}
    candidate = all(conditions.values())
    reasons = [f"pass 模型 {len(models_pass)}/2" + (f"（{', '.join(models_pass)}）" if models_pass else ""),
               f"主模型 effective pass 角色数 {primary_pass_roles}/3",
               f"精确页码校验 {'通过' if page_ok else f'不通过（越界 {page_issue_count} 处，v2 宽口径 {legacy_issue_count} 处）'}"]
    return {
        "report_id": report.get("report_id"), "title": title, "date": report.get("date"),
        "pages": report.get("pages"), "units_total": report.get("units_total", len(units)),
        "units_used": len(units),
        "models": runs, "cross_model": cross_model,
        "page_validation": {"pass": page_ok, "issue_count": page_issue_count,
                            "legacy_union_issue_count": legacy_issue_count,
                            "mode": "precise_unit_page_start_+-1",
                            "per_model": {r["model"]: {
                                "invalid_facts": r["page_validation"]["invalid_facts"],
                                "missing_pages": r["page_validation"]["missing_pages"],
                                "legacy_union_invalid_facts": r["page_validation"]["legacy_union_invalid_facts"],
                                "allowed_pages": r["page_validation"]["allowed_pages"],
                                "invalid_details": r["page_validation"]["invalid_details"]}
                                for r in runs}},
        "candidate": candidate,
        "candidate_decision": {"conditions": conditions, "model_passes": models_pass,
                               "primary_pass_roles": primary_pass_roles, "reasons": reasons},
        "fallback_notes": fallback_notes,
        "errors": [e for r in runs for e in r.get("errors") or []],
    }


# ---------------------------------------------------------------------------
# ④ v2 对比 / ⑤ 达标结论
# ---------------------------------------------------------------------------
def load_v2_reference(path: Path = V2_JSON) -> dict:
    if not path.exists():
        return {"v2_file": str(path), "available": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"v2_file": str(path), "available": False}
    return {"v2_file": str(path), "available": True,
            "v2_candidates": [c.get("report_id") for c in data.get("candidates") or []],
            "v2_rule_fallback_roles": (data.get("llm") or {}).get("rule_fallback_roles"),
            "v2_model_fallbacks": (data.get("llm") or {}).get("model_fallbacks"),
            "v2_page_issue_count": (data.get("page_validation_summary") or {}).get("invalid_facts"),
            "v2_elapsed_seconds": data.get("elapsed_seconds"),
            "v2_reports": [r.get("report_id") for r in data.get("reports") or []]}


def build_comparison_v2(payload: dict, v2_ref: dict) -> dict:
    scope = [r.get("report_id") for r in payload.get("reports") or []]
    v2_scope_candidates = [i for i in v2_ref.get("v2_candidates") or [] if i in scope]
    fallback_total = (payload.get("llm") or {}).get("rule_fallback_roles", 0) + \
                     (payload.get("llm") or {}).get("model_fallbacks", 0)
    return {
        "v2_file": v2_ref.get("v2_file"), "v2_available": v2_ref.get("available", False),
        "scope_report_ids": scope,
        "candidates": {"v2": len(v2_scope_candidates), "v2_scope_ids": v2_scope_candidates,
                       "v3": len(payload.get("candidates") or []),
                       "v3_ids": [c.get("report_id") for c in payload.get("candidates") or []],
                       "delta": len(payload.get("candidates") or []) - len(v2_scope_candidates)},
        "fallback": {"v2_rule_fallback_roles": v2_ref.get("v2_rule_fallback_roles"),
                     "v2_model_fallbacks": v2_ref.get("v2_model_fallbacks"),
                     "v3_rule_fallback_roles": (payload.get("llm") or {}).get("rule_fallback_roles"),
                     "v3_model_fallbacks": (payload.get("llm") or {}).get("model_fallbacks"),
                     "v3_total": fallback_total},
        "page_invalid": {"v2": v2_ref.get("v2_page_issue_count"),
                         "v3_precise": payload.get("page_validation_summary", {}).get("invalid_facts"),
                         "v3_legacy_union": payload.get("page_validation_summary", {}).get("legacy_union_invalid_facts")},
        "elapsed_seconds": {"v2": v2_ref.get("v2_elapsed_seconds"),
                            "v3": payload.get("elapsed_seconds")},
    }


def build_conclusion(payload: dict) -> dict:
    """自动判定是否达到「候选初筛 + 人工复核」标准。"""
    llm = payload.get("llm") or {}
    comparison = payload.get("comparison_v2") or {}
    reports_n = len(payload.get("panel") or [])
    rule_fb = llm.get("rule_fallback_roles", 0) or 0
    attempted = llm.get("role_calls_attempted", 1) or 1
    page_sum = payload.get("page_validation_summary") or {}
    precision = payload.get("page_precision") or {}
    criteria = [
        {"name": "覆盖研报数≥2（不足 3 篇已注明 fallback）",
         "pass": reports_n >= 2,
         "detail": f"本次评审 {reports_n} 篇"},
        {"name": "截断/超时修复：规则降级比例 ≤ 1/3",
         "pass": rule_fb / attempted <= 1 / 3,
         "detail": f"规则降级 {rule_fb}/{attempted}（v2: {comparison.get('fallback', {}).get('v2_rule_fallback_roles')}）"},
        {"name": "精确页码校验已生效（可识别越界并逐条标记）",
         "pass": precision.get("mode") == "precise_unit_page_start_+-1" and page_sum.get("total_facts", 0) > 0,
         "detail": f"越界 {page_sum.get('invalid_facts')} 处（v2 宽并集口径为 {page_sum.get('legacy_union_invalid_facts')} 处）"},
        {"name": "候选降维有效（候选数≤2 且均附人工复核清单）",
         "pass": len(payload.get("candidates") or []) <= 2 and bool(payload.get("review_checklist")),
         "detail": f"候选 {len(payload.get('candidates') or [])} 篇，复核清单 {len(payload.get('review_checklist') or [])} 篇"},
        {"name": "交叉模型一致率可量化",
         "pass": all((p.get("cross_model") or {}).get("role_agreement_rate") is not None
                     for p in payload.get("panel") or []),
         "detail": "逐篇输出角色 verdict 一致率与 key_facts 重合度"},
    ]
    passed = sum(1 for c in criteria if c["pass"])
    return {"standard": REVIEW_CONCLUSION + "标准（MVP）", "passed": passed,
            "total": len(criteria), "met": passed == len(criteria),
            "criteria": criteria,
            "verdict": "达到" if passed == len(criteria) else "部分达到",
            "note": "面板可支撑候选初筛并输出人工复核清单；页码精确校验暴露的问题需在入库/抽取阶段修数据后再收紧候选门槛。"}


# ---------------------------------------------------------------------------
# ③ 人工复核清单
# ---------------------------------------------------------------------------
def build_review_checklist(payload: dict) -> list[dict]:
    """候选（含被页码拦截的研报）的关键事实/页码/角色意见 + 勾选位。"""
    checklist: list[dict] = []
    candidates = {c.get("report_id") for c in payload.get("candidates") or []}
    for item in payload.get("panel") or []:
        is_candidate = item.get("report_id") in candidates
        facts: list[dict] = []
        opinions: list[dict] = []
        seen = set()
        for run in item.get("models") or []:
            for role in run.get("roles") or []:
                opinions.append({
                    "model": run.get("model"), "model_pass": run.get("model_pass"),
                    "role": role.get("role"), "verdict": role.get("verdict"),
                    "page_valid_ratio": (role.get("page_check") or {}).get("valid_ratio"),
                    "downweighted": (role.get("page_check") or {}).get("downweighted"),
                    "reasons": role.get("reasons") or [],
                    "risks": role.get("risks") or []})
                for fact in role.get("key_facts") or []:
                    key = (fact.get("fact"), fact.get("page"))
                    if key in seen:
                        continue
                    seen.add(key)
                    valid = v2._in_allowed(fact.get("page"),
                                           set(run.get("page_validation", {}).get("allowed_pages") or []), 0)
                    facts.append({"fact": fact.get("fact"), "page": fact.get("page"),
                                  "valid": valid, "model": run.get("model"), "role": role.get("role")})
        page = item.get("page_validation") or {}
        checklist.append({
            "report_id": item.get("report_id"), "title": item.get("title"),
            "date": item.get("date"), "is_candidate": is_candidate,
            "candidate": item.get("candidate"),
            "reject_reasons": [] if item.get("candidate") else (item.get("candidate_decision") or {}).get("reasons"),
            "page_issue_count": page.get("issue_count"),
            "legacy_union_issue_count": page.get("legacy_union_issue_count"),
            "facts": facts, "opinions": opinions,
        })
    return checklist


def render_review_md(payload: dict) -> str:
    checklist = payload.get("review_checklist") or []
    lines = [f"# 研报面板 v3 · 人工复核清单（{payload.get('date_tag', '')}）", "",
             "> 用途：对面板输出的候选/被拦截研报逐项人工复核，勾选 ☑ 表示已核对通过；"
             "未勾选项即待确认，不得直接进入下游。", "",
             f"- 生成时间：{payload.get('generated_at')}",
             f"- 模型：主 `{(payload.get('models') or {}).get('primary')}` / "
             f"交叉 `{(payload.get('models') or {}).get('cross')}`；"
             f"精确页码口径：来源知识单元 page_start ±1",
             f"- 候选 {len(payload.get('candidates') or [])} 篇 / 复核清单 {len(checklist)} 篇", ""]
    if not checklist:
        lines += ["本期无研报产出面板，复核清单为空。", ""]
    for i, item in enumerate(checklist, 1):
        tag = "**进候选池**" if item.get("is_candidate") else "未进候选"
        lines += [f"## {i}. `{item.get('report_id')}` {item.get('title') or ''}（{tag}）", "",
                  f"- 日期：{item.get('date')}；页码问题：{item.get('page_issue_count')} 处"
                  f"（v2 宽并集口径 {item.get('legacy_union_issue_count')} 处）",
                  f"- 候选判定：{'进候选池' if item.get('candidate') else '不进候选'}"
                  + (f"；原因：{'；'.join(item.get('reject_reasons') or [])}" if item.get('reject_reasons') else ""),
                  "", "### 关键事实核对（页码必须为来源单元起始页 ±1）", "",
                  "| ☐ | # | 事实 | 页码 | 来源模型/角色 | 页码校验 |", "|---|---|---|---|---|---|"]
        for j, fact in enumerate(item.get("facts") or [], 1):
            mark = "✅" if fact.get("valid") else "❌ 越界"
            lines.append(f"| ☐ | {j} | {v1._clip(fact.get('fact'), 90)} | p.{fact.get('page')} | "
                         f"{fact.get('model')} / {fact.get('role')} | {mark} |")
        lines += ["", "### 角色意见核对（☐ 逐条确认 verdict/理由/风险）", ""]
        for op in item.get("opinions") or []:
            down = "（pass 已降权）" if op.get("downweighted") else ""
            lines.append(f"- ☐ **{op.get('model')} / {op.get('role')}** → `{op.get('verdict')}`{down}，"
                         f"页码有效率 {op.get('page_valid_ratio')}")
            for reason in (op.get("reasons") or [])[:3]:
                lines.append(f"  - ☐ 理由：{v1._clip(reason, 140)}")
            for risk in (op.get("risks") or [])[:2]:
                lines.append(f"  - ☐ 风险：{v1._clip(risk, 120)}")
        lines += ["", "### 复核结论（人工填写）", "",
                  "- ☐ 页码已逐条对照原文核对（越界项已剔除/更正）",
                  "- ☐ 关键事实与原始研报一致，无模型幻觉",
                  "- ☐ 角色意见分歧已评估，风险已纳入考虑",
                  "- ☐ 结论：同意进候选池 / 退回 / 需补充数据（人工勾选并签名）", ""]
    lines += ["## 使用说明", "",
              "- 精确页码校验以「知识单元 page_start ±1」为口径；本批入库单元 page_start 粒度较粗（同篇多条单元共享起始页），"
              "模型引用正文后段页码会被判越界，这正是需要人工复核触发而非直接采信的点。",
              "- 复核通过的候选才可进入下游（如策略/模拟盘）；本清单不构成投资建议。"]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_panel_v3(reports_n: int = 3, models: dict | None = None, chat_fn=None,
                 timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                 num_predict: int = NUM_PREDICT, retries: int = DEFAULT_RETRIES,
                 max_workers: int = 3, max_units: int = v1.DEFAULT_MAX_UNITS,
                 date_tag: str | None = None, root: Path | str = v1.DEFAULT_ROOT,
                 v2_json: Path = V2_JSON) -> dict:
    models = dict(DEFAULT_MODELS if models is None else models)
    chat_fn = chat_fn or call_ollama_v3
    started = time.monotonic()
    deadline_at = started + max(1.0, float(deadline_seconds))
    catalog = v1.load_report_catalog(root)
    selected = v1.select_reports(catalog, reports_n)
    panel: list[dict] = []
    reports_meta: list[dict] = []
    notes: list[str] = []
    attempted = llm_ok = rule_fb = model_fb = retries_total = retries_ok = 0
    for rec in selected:
        units = v1.load_report_units(rec["path"], limit=max_units)
        report = dict(rec)
        report["units_total"] = rec.get("units_total", len(units))
        reports_meta.append({**{k: rec.get(k) for k in ("report_id", "date", "pages", "units_total", "path")},
                             "title": v1.report_title(report, units), "units_used": len(units)})
        item = evaluate_report_v3(report, units, models, chat_fn, timeout, deadline_at,
                                  num_predict, retries, max_workers)
        panel.append(item)
        for run in item["models"]:
            attempted += len(run["roles"])
            llm_ok += run["llm_ok_roles"]
            rule_fb += run["rule_fallback_roles"]
            model_fb += 1 if run.get("is_fallback") else 0
            retries_total += (run.get("retry_stats") or {}).get("retries", 0)
            retries_ok += (run.get("retry_stats") or {}).get("retries_succeeded", 0)
        notes.extend(f"{item['report_id']}: {n}" for n in item["fallback_notes"])
    candidates = [{"report_id": p["report_id"], "title": p["title"], "date": p.get("date"),
                   "pages": p.get("pages"),
                   "pass_models": p["candidate_decision"]["model_passes"],
                   "primary_pass_roles": p["candidate_decision"]["primary_pass_roles"],
                   "model_verdicts": {r["model"]: r["effective_aggregate"]["verdict"] for r in p["models"]},
                   "role_agreement_rate": (p.get("cross_model") or {}).get("role_agreement_rate"),
                   "page_issue_count": p["page_validation"]["issue_count"]}
                  for p in panel if p["candidate"]]
    candidates.sort(key=lambda c: (-c["primary_pass_roles"], -(c.get("pages") or 0), c["report_id"]))
    page_summary = {
        "mode": "precise_unit_page_start_+-1",
        "total_facts": sum(r["page_validation"]["total_facts"] for p in panel for r in p["models"]),
        "invalid_facts": sum(r["page_validation"]["invalid_facts"] for p in panel for r in p["models"]),
        "missing_pages": sum(r["page_validation"]["missing_pages"] for p in panel for r in p["models"]),
        "legacy_union_invalid_facts": sum(r["page_validation"]["legacy_union_invalid_facts"]
                                          for p in panel for r in p["models"]),
        "reports_with_issues": sum(1 for p in panel if p["page_validation"]["issue_count"] > 0),
        "tolerance": PAGE_TOLERANCE,
    }
    if retries_total:
        notes.append(f"解析失败重试 {retries_total} 次，成功 {retries_ok} 次"
                     f"（num_predict={num_predict}，缓解截断）。")
    if rule_fb:
        notes.append(f"{rule_fb}/{attempted} 个角色评审降级为规则式摘要，已在 "
                     f"panel[].models[].roles[].source=rule_fallback 与 errors 标注。")
    if model_fb:
        notes.append(f"{model_fb} 个模型跑分使用交叉模型降级（{models['cross']} → {models['cross_fallback']}）。")
    elapsed = round(time.monotonic() - started, 1)
    if elapsed > deadline_seconds:
        notes.append(f"本次耗时 {elapsed}s 超出 deadline {deadline_seconds}s：超时角色已按规则式降级。")
    if not catalog:
        notes.append(f"未在 {root} 找到已入库研报，面板为空。")
    payload = {
        "schema_version": 3, "kind": "report_panel_v3",
        "approach": "多智能体评审 + 大 num_predict/重试 + 精确页码校验 + 人工复核清单",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_tag": date_tag or datetime.now().strftime("%Y%m%d"),
        "models": models,
        "llm": {"endpoint": v1.OLLAMA_ENDPOINT, "think": False, "num_predict": num_predict,
                "timeout_seconds": timeout, "deadline_seconds": deadline_seconds,
                "retries_per_role": retries, "role_calls_attempted": attempted,
                "llm_ok_roles": llm_ok, "rule_fallback_roles": rule_fb,
                "model_fallbacks": model_fb, "retries": retries_total,
                "retries_succeeded": retries_ok},
        "selection": {"reports_requested": reports_n, "reports_selected": len(selected),
                      "catalog_size": len(catalog),
                      "rule": "报告日期最新优先，同日按知识单元数/页数降序；零知识单元研报跳过",
                      "skipped_no_units": sum(1 for c in catalog if int(c.get("units_total") or 0) == 0)},
        "reports": reports_meta, "panel": panel, "candidates": candidates,
        "page_validation_summary": page_summary,
        "page_precision": {"mode": "precise_unit_page_start_+-1", "tolerance": PAGE_TOLERANCE,
                           "statement": "key_facts.page 必须落在其来源知识单元的 page_start ±1 内；"
                                        "v2 宽并集口径（page_start..page_end）保留为对照指标"},
        "fallback": {"used": bool(rule_fb or model_fb), "rule_fallback_roles": rule_fb,
                     "model_fallbacks": model_fb, "notes": notes},
        "elapsed_seconds": elapsed,
    }
    payload["review_checklist"] = build_review_checklist(payload)
    v2_ref = load_v2_reference(v2_json)
    payload["comparison_v2"] = build_comparison_v2(payload, v2_ref)
    payload["conclusion"] = build_conclusion(payload)
    return payload


def render_markdown_v3(payload: dict) -> str:
    p = payload
    llm = p.get("llm") or {}
    cmp_v2 = p.get("comparison_v2") or {}
    concl = p.get("conclusion") or {}
    sel = p.get("selection") or {}
    lines = [f"# 研报评审面板 v3（{p.get('date_tag', '')}）· 截断/超时修复 + 精确页码 + 人工复核", "",
             "> 每篇研报由 3 角色 × 2 模型评审；进候选需 ≥2 模型 pass + 主模型 ≥2 角色 pass + "
             "精确页码校验通过（key_facts.page ∈ 来源单元 page_start ±1）。", "",
             "## 运行概览", "",
             f"- 生成时间：{p.get('generated_at')}",
             f"- 模型：主 `{(p.get('models') or {}).get('primary')}` / 交叉 `{(p.get('models') or {}).get('cross')}`，"
             f"num_predict={llm.get('num_predict')}、timeout={llm.get('timeout_seconds')}s、"
             f"deadline={llm.get('deadline_seconds')}s、每角色重试={llm.get('retries_per_role')}",
             f"- 选篇：请求 {sel.get('reports_requested')} 篇，实际 {sel.get('reports_selected')} 篇"
             f"（目录 {sel.get('catalog_size')} 篇，跳过零单元 {sel.get('skipped_no_units')} 篇）",
             f"- 角色调用：{llm.get('llm_ok_roles')}/{llm.get('role_calls_attempted')} LLM 成功，"
             f"规则降级 {llm.get('rule_fallback_roles')}，模型降级 {llm.get('model_fallbacks')}，"
             f"解析重试 {llm.get('retries')}（成功 {llm.get('retries_succeeded')}）；耗时 {p.get('elapsed_seconds')}s", ""]
    if p.get("candidates"):
        lines += ["## v3 候选池", "",
                  "| # | 研报 | 日期 | 页数 | pass 模型 | 主模型 pass 角色 | 一致率 | 页码越界 |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, c in enumerate(p["candidates"], 1):
            lines.append(f"| {i} | `{c['report_id']}` {c.get('title') or ''} | {c.get('date')} | {c.get('pages')} | "
                         f"{len(c.get('pass_models') or [])}/2 | {c.get('primary_pass_roles')}/3 | "
                         f"{c.get('role_agreement_rate')} | {c.get('page_issue_count')} |")
        lines.append("")
    else:
        lines += ["## v3 候选池", "", "本期无研报同时满足三项条件（精确页码校验为新增门槛），候选池为空。", ""]
    lines += ["## 每篇面板", ""]
    for item in p.get("panel") or []:
        page = item.get("page_validation") or {}
        decision = item.get("candidate_decision") or {}
        cross = item.get("cross_model") or {}
        overlap = cross.get("key_facts_overlap") or {}
        lines += [f"### `{item.get('report_id')}` {item.get('title') or ''}", "",
                  f"- 候选判定：**{'进候选池' if item.get('candidate') else '不进候选'}**；"
                  f"{'；'.join(decision.get('reasons') or [])}",
                  f"- 跨模型：verdict 一致={cross.get('verdict_agreement')}，角色一致率="
                  f"{cross.get('role_agreement_rate')}，key_facts 重合度={overlap.get('overlap_ratio')}",
                  f"- 精确页码校验：{'通过' if page.get('pass') else '不通过'}，越界 {page.get('issue_count')} 处"
                  f"（v2 宽并集口径 {page.get('legacy_union_issue_count')} 处）", ""]
        for run in item.get("models") or []:
            tag = f"{run.get('model')}" + (f"（降级自 {run.get('fallback_of')}）" if run.get("is_fallback") else "")
            eff = run.get("effective_aggregate") or {}
            v = eff.get("votes") or {}
            pv = run.get("page_validation") or {}
            lines.append(f"- **模型 {tag}** · aggregate=`{eff.get('verdict')}` "
                         f"(pass {v.get('pass', 0)} / avoid {v.get('avoid', 0)} / neutral {v.get('neutral', 0)})"
                         f" · 精确页码越界 {pv.get('invalid_facts', 0)} + 缺页码 {pv.get('missing_pages', 0)}"
                         f" · LLM {run.get('llm_ok_roles')}/3 · 重试 {run.get('retried_roles')}")
            for role in run.get("roles") or []:
                check = role.get("page_check") or {}
                down = f"（页码有效率 {check.get('valid_ratio')}，pass 票降权）" if check.get("downweighted") else ""
                src = role.get("source") or ""
                lines.append(f"  - {role.get('role')} · `{role.get('verdict')}`{down} · {src}")
                for fact in (role.get("key_facts") or [])[:3]:
                    lines.append(f"    - 事实：{v1._clip(fact.get('fact'), 150)}（p.{fact.get('page')}）")
            if pv.get("invalid_details"):
                for d in pv["invalid_details"][:6]:
                    lines.append(f"    - ⚠ 页码越界：{d.get('role')} · {v1._clip(d.get('fact'), 90)}（p.{d.get('page')}）")
        if item.get("errors"):
            lines.append(f"- 降级/异常：{'；'.join(item['errors'][:6])}")
        lines.append("")
    lines += ["## 与 v2 对比", "",
              f"- 候选数：v2 同范围 {cmp_v2.get('candidates', {}).get('v2')} 篇 → v3 "
              f"{cmp_v2.get('candidates', {}).get('v3')} 篇（Δ{cmp_v2.get('candidates', {}).get('delta')}）",
              f"- fallback：v2 规则降级 {cmp_v2.get('fallback', {}).get('v2_rule_fallback_roles')} + "
              f"模型降级 {cmp_v2.get('fallback', {}).get('v2_model_fallbacks')} → v3 规则降级 "
              f"{cmp_v2.get('fallback', {}).get('v3_rule_fallback_roles')} + 模型降级 "
              f"{cmp_v2.get('fallback', {}).get('v3_model_fallbacks')}",
              f"- 页码越界：v2 宽并集口径 {cmp_v2.get('page_invalid', {}).get('v2')} 处 → v3 精确口径 "
              f"{cmp_v2.get('page_invalid', {}).get('v3_precise')} 处（同数据宽口径 "
              f"{cmp_v2.get('page_invalid', {}).get('v3_legacy_union')} 处）",
              f"- 耗时：v2 {cmp_v2.get('elapsed_seconds', {}).get('v2')}s → v3 "
              f"{cmp_v2.get('elapsed_seconds', {}).get('v3')}s", ""]
    lines += ["## 结论：是否达到「候选初筛 + 人工复核」标准", "",
              f"**{concl.get('verdict')}**（{concl.get('passed')}/{concl.get('total')} 项达标）——{concl.get('note')}", ""]
    for c in concl.get("criteria") or []:
        lines.append(f"- {'✅' if c.get('pass') else '❌'} {c.get('name')}：{c.get('detail')}")
    lines += ["", "## 局限与说明", ""]
    fb = p.get("fallback") or {}
    if fb.get("notes"):
        lines += [f"- {n}" for n in fb["notes"]]
    else:
        lines.append("- 无 fallback：全部角色由 LLM 完成。")
    lines += [
        "- 精确页码口径（page_start ±1）：本批知识单元 page_start 粒度较粗（同篇多条单元共享起始页），"
        "模型引用正文后段页码会被判越界，需人工复核或改进入库抽取的页码锚定。",
        "- num_predict 提升到 1600 + 解析失败修复重试，降低了 JSON 截断不可解析率，但长输出仍可能超时。",
        "- 面板结论仅供研究参考，不构成投资建议；人工复核清单为必过环节。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs_v3(payload: dict, json_path: Path | str, md_path: Path | str,
                     review_path: Path | str) -> tuple[Path, Path, Path]:
    paths = [Path(json_path), Path(md_path), Path(review_path)]
    for pth in paths:
        pth.parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    paths[1].write_text(render_markdown_v3(payload), encoding="utf-8")
    paths[2].write_text(render_review_md(payload), encoding="utf-8")
    return paths[0], paths[1], paths[2]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研报评审面板 v3：修复截断/超时 + 精确页码 + 人工复核清单")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 v3 面板并输出 JSON+MD+复核清单")
    run.add_argument("--reports", type=int, default=3, help="评审研报数量（默认 3）")
    run.add_argument("--primary", default=DEFAULT_MODELS["primary"], help="主模型")
    run.add_argument("--cross", default=DEFAULT_MODELS["cross"], help="交叉模型")
    run.add_argument("--cross-fallback", default=DEFAULT_MODELS["cross_fallback"], help="交叉模型降级模型")
    run.add_argument("--num-predict", type=int, default=NUM_PREDICT, help=f"num_predict（默认 {NUM_PREDICT}，≥1200）")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次 LLM 超时秒数")
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE, help="整体 deadline 秒数（默认 900）")
    run.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="解析失败重试次数（默认 1）")
    run.add_argument("--workers", type=int, default=3, help="每模型角色并行数")
    run.add_argument("--max-units", type=int, default=v1.DEFAULT_MAX_UNITS, help="每篇最多投喂单元数")
    run.add_argument("--root", default=str(v1.DEFAULT_ROOT), help="研报入库根目录")
    run.add_argument("--date", default=None, help="输出文件名日期标签（默认今天）")
    run.add_argument("--out-json", default=None, help="JSON 输出路径")
    run.add_argument("--out-md", default=None, help="Markdown 输出路径")
    run.add_argument("--out-review", default=None, help="人工复核清单输出路径")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    date_tag = args.date or datetime.now().strftime("%Y%m%d")
    out_json = Path(args.out_json) if args.out_json else OUT_DIR / f"report_panel_v3_{date_tag}.json"
    out_md = Path(args.out_md) if args.out_md else OUT_DIR / f"report_panel_v3_{date_tag}.md"
    out_review = Path(args.out_review) if args.out_review else OUT_DIR / "report_panel_v3_review.md"
    models = {"primary": args.primary, "cross": args.cross, "cross_fallback": args.cross_fallback}
    payload = run_panel_v3(reports_n=args.reports, models=models, timeout=args.timeout,
                           deadline_seconds=args.deadline, num_predict=args.num_predict,
                           retries=args.retries, max_workers=args.workers,
                           max_units=args.max_units, date_tag=date_tag, root=args.root)
    jp, mp, rp = write_outputs_v3(payload, out_json, out_md, out_review)
    print(f"[report_panel_v3] reports={len(payload['panel'])} candidates={len(payload['candidates'])} "
          f"llm_ok={payload['llm']['llm_ok_roles']}/{payload['llm']['role_calls_attempted']} "
          f"rule_fallback={payload['llm']['rule_fallback_roles']} model_fallback={payload['llm']['model_fallbacks']} "
          f"retries={payload['llm']['retries']} page_invalid={payload['page_validation_summary']['invalid_facts']} "
          f"elapsed={payload['elapsed_seconds']}s conclusion={payload['conclusion']['verdict']}")
    print(f"[report_panel_v3] json={jp}")
    print(f"[report_panel_v3] md={mp}")
    print(f"[report_panel_v3] review={rp}")
    return 0 if payload["panel"] else 1


if __name__ == "__main__":
    sys.exit(main())
