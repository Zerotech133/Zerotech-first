# -*- coding: utf-8 -*-
"""concept 合并影响模拟（只在内存/临时副本合并，绝不写真实数据）。

背景：wave13 AL 审计出 11 个重复 concept 组，其中 2 组（CG-003 东盟 MSME、CG-004 跨境电商四大挑战，
跨研报 claim 全同）判为可合并；wave12 AG 发现问答 @1 miss 根因是「同 concept 不同 unit」。

做法：
  1. 读 portal/static/kb_units_all.json + research/reports/concept_dedup_20261101.json 的 alias_map；
  2. **仅在内存**把 action=merge 组按「保留主单元、剔除被合并单元」合并（被合并单元 alias 指向主单元）；
  3. 用合并前/后单元集合分别跑 kb_qa 检索链路（32 问题自检 @1/@5/平均排名）：
     - baseline：原始单元集；
     - merged_alias：合并后单元集 + 问题 source_unit_id 按 alias 重映射（公平口径，模拟真实合并后更新引用）；
     - merged_raw：合并后单元集 + 原始 source_unit_id（风险口径：若问题库不重映射会丢召回）；
  4. 输出 research/reports/merge_sim_20261101.{json,md}：对比、受影响问题逐条、结论与「是否真实合并」建议。

CLI::

    python tools/kb_corpus/merge_sim.py sim [--write] [--json] [--topk 5]

安全：不写 kb_units_all.json / 不写任何 portal 静态 JSON；只写本任务报告（--write 时）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
UNITS_ALL = STATIC / "kb_units_all.json"
DEDUP = REPO / "research" / "reports" / "concept_dedup_20261101.json"
QUESTIONS = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
TOPK = 5
GENERATOR = "tools/kb_corpus/merge_sim.py"
DISCLAIMER = "本模拟只评估 concept 合并对检索命中的影响，不修改任何真实数据；结论供是否合并审批参考，不构成投资建议。"


# ---------------------------------------------------------------------------
# 模块加载（复用既有检索链，避免重写口径）
# ---------------------------------------------------------------------------
def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_stack():
    """加载 kb_read_api / kb_qa / kb_qa_selfcheck（只读复用）。"""
    api = _load_module("merge_sim_kb_read_api", "kb_read_api.py")
    qa = _load_module("merge_sim_kb_qa", "kb_qa.py")
    selfcheck = _load_module("merge_sim_selfcheck", "kb_qa_selfcheck.py")
    return api, qa, selfcheck


# ---------------------------------------------------------------------------
# 纯函数：合并计划 / 单元索引 / 问题重映射
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def retrieval_unit_id(unit: dict, api=None) -> str:
    """按 kb_read_api 口径计算检索单元 ID：sha1(book|chapter|page_start|claim[:80])[:12]。"""
    if api is not None:
        return api.unit_id({"book": unit.get("book"), "chapter": unit.get("chapter") or "",
                            "page_start": unit.get("page_start", unit.get("p1")),
                            "claim": unit.get("claim") or ""})
    raw = f"{unit.get('book')}|{unit.get('chapter') or ''}|{unit.get('page_start', unit.get('p1'))}|{str(unit.get('claim') or '')[:80]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def plan_merge(dedup: dict, units_all: list[dict], api=None) -> dict:
    """按 AL alias_map 生成合并计划（纯函数）：应保留主单元 / 剔除单元 / alias 重映射。"""
    dedup = dedup if isinstance(dedup, dict) else {}
    groups = []
    keep_drop: dict[str, str] = {}   # dropped retrieval_id -> primary retrieval_id
    all_dropped: list[str] = []
    for group in dedup.get("groups") or []:
        if not isinstance(group, dict) or group.get("action") != "merge":
            continue
        units = [u for u in (group.get("units") or []) if isinstance(u, dict)]
        if len(units) < 2:
            continue
        primary = next((u for u in units if u.get("is_primary")), units[0])
        # AL 的 unit_id 与检索 ID 口径不同：统一改用 kb_read_api 口径
        mapped = {u.get("unit_id"): retrieval_unit_id(u, api) for u in units}
        primary_id = mapped.get(primary.get("unit_id"), retrieval_unit_id(primary, api))
        dropped = [mapped[u.get("unit_id")] for u in units
                   if u.get("unit_id") != primary.get("unit_id") and u.get("unit_id") in mapped]
        for d in dropped:
            keep_drop[d] = primary_id
            all_dropped.append(d)
        groups.append({
            "group_id": group.get("group_id"),
            "concept_forms": group.get("concept_forms") or [],
            "al_primary_unit_id": group.get("primary_unit_id"),
            "primary_unit_id": primary_id,
            "dropped_unit_ids": dropped,
            "count_before": len(units),
            "count_after": 1,
            "action": "merge",
        })
    return {
        "groups": groups,
        "alias_remap": keep_drop,
        "dropped_ids": all_dropped,
        "merge_groups": len(groups),
        "units_before": len(units_all),
        "units_after": len(units_all) - len(all_dropped),
    }


def build_index(units: list[dict]) -> dict[str, dict]:
    """检索索引（内存副本）：unit_id → unit；用于校验合并后索引不含被剔除单元。"""
    return {str(u.get("id") or ""): u for u in units if isinstance(u, dict) and u.get("id")}


def apply_merge_to_index(units: list[dict], plan: dict) -> list[dict]:
    """在内存副本上执行合并：保留原顺序，剔除 dropped 单元。"""
    dropped = set(plan.get("dropped_ids") or [])
    return [u for u in units if isinstance(u, dict) and u.get("id") not in dropped]


def remap_questions(questions: list[dict], alias_remap: dict) -> tuple[list[dict], list[dict]]:
    """按 alias 重映射问题 source_unit_id（返回 新问题列表 + 映射记录）。"""
    remapped, records = [], []
    for q in questions:
        if not isinstance(q, dict):
            continue
        new_q = dict(q)
        src = str(q.get("source_unit_id") or "")
        if src in alias_remap:
            new_q["source_unit_id"] = alias_remap[src]
            new_q["_original_source_unit_id"] = src
            records.append({"question_id": q.get("id"), "from": src, "to": alias_remap[src]})
        remapped.append(new_q)
    return remapped, records


# ---------------------------------------------------------------------------
# 评测（复用 kb_qa_selfcheck 的逐条判定/汇总口径）
# ---------------------------------------------------------------------------
def run_eval(questions: list[dict], search_fn, selfcheck, topk: int = TOPK) -> dict:
    """对问题列表跑 retrieval-only 评测（@1/@5/平均排名）。"""
    items = []
    started = time.monotonic()
    for q in questions:
        try:
            hits = search_fn(q.get("question") or "", topk) or []
        except Exception as exc:  # noqa: BLE001
            hits = []
            items.append({"id": q.get("id"), "question": q.get("question"),
                          "source_unit_id": q.get("source_unit_id"), "search_error": f"{exc}"})
            continue
        items.append(selfcheck.evaluate_question(q, hits, topk))
    return {
        "summary": selfcheck.summarize(items),
        "items": items,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def compare_runs(baseline: dict, merged_alias: dict, merged_raw: dict, plan: dict) -> dict:
    """对比三次评测：@1/@5/平均排名 delta + 受影响问题逐条变化。"""
    def summary(eval_result: dict) -> dict:
        return eval_result.get("summary") or {}

    base_s, alias_s, raw_s = summary(baseline), summary(merged_alias), summary(merged_raw)
    by_id = {x.get("id"): x for x in merged_alias.get("items") or [] if isinstance(x, dict)}
    affected = []
    for item in baseline.get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = item.get("id")
        after = by_id.get(qid) or {}
        if (item.get("top1_is_source") != after.get("top1_is_source")
                or item.get("hit_source") != after.get("hit_source")
                or item.get("source_rank") != after.get("source_rank")):
            affected.append({
                "question_id": qid,
                "question": item.get("question"),
                "concept": item.get("concept"),
                "source_unit_id": item.get("source_unit_id"),
                "before": {"hit_at_1": item.get("top1_is_source"), "hit": item.get("hit_source"),
                           "source_rank": item.get("source_rank")},
                "after": {"hit_at_1": after.get("top1_is_source"), "hit": after.get("hit_source"),
                          "source_rank": after.get("source_rank")},
                "delta": "improved" if (after.get("source_rank") or 999) < (item.get("source_rank") or 999)
                         else "regressed" if (after.get("source_rank") or 999) > (item.get("source_rank") or 999)
                         else "changed",
            })
    def delta(key: str, digits: int = 4) -> dict:
        return {
            "baseline": base_s.get(key),
            "merged_alias": alias_s.get(key),
            "merged_raw": raw_s.get(key),
            "delta_alias": round((alias_s.get(key) or 0) - (base_s.get(key) or 0), digits),
            "delta_raw": round((raw_s.get(key) or 0) - (base_s.get(key) or 0), digits),
        }
    return {
        "deltas": {
            "hit_at_1": delta("hit_at_1"),
            "hit_at_5": delta("hit_at_5"),
            "hit_rate_at_1": delta("hit_rate_at_1", 4),
            "hit_rate_at_5": delta("hit_rate_at_5", 4),
            "avg_source_rank": delta("avg_source_rank", 2),
        },
        "affected_questions": affected,
        "alias_remap_count": len([g for g in plan.get("alias_remap") or {}]),
        "dropped_unit_ids": plan.get("dropped_ids") or [],
        "merge_groups": plan.get("groups") or [],
    }


# ---------------------------------------------------------------------------
# 报告 / CLI
# ---------------------------------------------------------------------------
def build_report(*, units_all_meta: dict, questions_path: str, plan: dict, baseline: dict,
                 merged_alias: dict, merged_raw: dict, aliases: list[dict],
                 generated_at: str | None = None, with_corpus: bool = False) -> dict:
    comparison = compare_runs(baseline, merged_alias, merged_raw, plan)
    d = comparison["deltas"]
    hit1_delta = d["hit_at_1"]["delta_alias"]
    hit5_delta = d["hit_at_5"]["delta_alias"]
    raw_hit5_delta = d["hit_at_5"]["delta_raw"]
    if hit5_delta >= 0 and hit1_delta >= 0:
        verdict = "worth_merging"
        conclusion = (f"合并后（alias 重映射口径）@1 {d['hit_at_1']['baseline']}→{d['hit_at_1']['merged_alias']}"
                      f"（Δ{hit1_delta:+d}）、@5 {d['hit_at_5']['baseline']}→{d['hit_at_5']['merged_alias']}"
                      f"（Δ{hit5_delta:+d}），检索命中不降；可考虑执行真实合并（需人工审批+备份+更新引用/重建索引）。")
        if hit1_delta == 0 and hit5_delta == 0:
            conclusion = ("合并后（alias 重映射口径）@1/@5 与合并前持平：去重收益主要是「知识库更干净」，"
                          "对当前 32 问题检索无直接命中收益；可合并但优先级不高（需人工审批+备份）。")
    else:
        verdict = "not_worth_merging_now"
        conclusion = (f"合并后（alias 重映射口径）命中下降：@1 Δ{hit1_delta:+d}、@5 Δ{hit5_delta:+d}；"
                      "不建议直接合并，应先修正问题库/引用映射或改进检索排序。")
    return {
        "schema_version": 1,
        "date": DATE_TAG,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "in_memory_simulation",
        "source": {
            "units_all": units_all_meta,
            "dedup": str(DEDUP),
            "questions": questions_path,
        },
        "policy": {
            "merge_source": "concept_dedup_20261101.json groups[action=merge]+alias_map",
            "merge_strategy": "保留主单元、剔除被合并单元；被合并单元 alias 重映射到主单元",
            "modifies_real_data": False,
            "topk": TOPK,
            "eval_chain": "kb_qa.search（unit-side 检索；corpus chunk 支路默认关闭以隔离单元合并影响、避免 99 次语料全量扫描）",
            "corpus_enabled": bool(with_corpus),
            "views": {
                "baseline": "原始单元集 + 原始问题 source_unit_id",
                "merged_alias": "合并后单元集 + 问题 source_unit_id 按 alias 重映射（公平口径）",
                "merged_raw": "合并后单元集 + 原始问题 source_unit_id（风险口径）",
            },
        },
        "merge_plan": plan,
        "alias_remap_records": aliases,
        "results": {
            "baseline": {k: v for k, v in baseline.items() if k != "items"},
            "merged_alias": {k: v for k, v in merged_alias.items() if k != "items"},
            "merged_raw": {k: v for k, v in merged_raw.items() if k != "items"},
        },
        "comparison": comparison,
        "conclusion": conclusion,
        "verdict": verdict,
        "recommendation": {
            "action": "有条件可合并" if verdict == "worth_merging" else "暂缓合并",
            "preconditions": [
                "人工确认 2 组跨研报重复内容确为同一知识（claim 全同）",
                "备份 kb_units_all.json 与知识单元源 jsonl（写前快照）",
                "合并后更新问题库 source_unit_id（按 alias_remap）或保留 alias 解析层",
                "重建检索索引/清缓存（kb_read_api 每次全量加载，无需额外重建，但门户缓存需刷新）",
                "合并/重建属数据变更，按仓库纪律审批后执行（本工具不执行）",
            ],
            "risk": (f"若不做 alias 重映射（merged_raw 口径），@5 变化 {raw_hit5_delta:+d}："
                     "问题库会丢失被剔除单元来源，出现假 miss；必须同步更新引用。"),
        },
        "optional_89_eval": {"run": False,
                             "reason": "本次以 32 问题自检为主；89 查询冻结评测需 search_v2 评测器口径，作为后续可选步骤（接口预留 --with-89）。"},
        "disclaimer": DISCLAIMER,
    }


def render_markdown(report: dict) -> str:
    cmp = report["comparison"]
    d = cmp["deltas"]
    res = report["results"]
    plan = report["merge_plan"]
    lines = [
        "# concept 合并影响模拟（2026-11-01）",
        "",
        f"> 模式：**内存模拟（不改真实数据）** · 生成于 {report['generated_at']}",
        f"> 合并计划：**{plan['merge_groups']} 组**（{plan['units_before']} → {plan['units_after']} 单元，"
        f"剔除 {len(plan['dropped_ids'])} 个被合并单元）· 评测：32 问题 retrational-only（top-{report['policy']['topk']}）",
        "",
        "## 一、合并计划",
        "",
        "| 组ID | concept | 主单元(保留) | 被合并单元(剔除→alias) |",
        "|---|---|---|---|",
    ]
    for g in plan["groups"]:
        lines.append(f"| {g['group_id']} | {' / '.join(g['concept_forms'])} | `{g['primary_unit_id']}` | "
                     f"{', '.join('`' + x + '`' for x in g['dropped_unit_ids']) or '—'} |")
    lines += ["", "## 二、检索命中对比（@1 / @5 / 平均来源排名）", "",
              "| 口径 | @1 | @5 | @1 率 | @5 率 | 平均来源排名 | 说明 |", "|---|---|---|---|---|---|---|"]
    for key, label in (("baseline", "合并前"), ("merged_alias", "合并后·alias重映射（公平）"),
                       ("merged_raw", "合并后·原始引用（风险）")):
        s = res[key]["summary"]
        lines.append(f"| {label} | {s['hit_at_1']}/{s['total']} | {s['hit_at_5']}/{s['total']} | "
                     f"{s['hit_rate_at_1']:.0%} | {s['hit_rate_at_5']:.0%} | "
                     f"{s['avg_source_rank'] if s['avg_source_rank'] is not None else '—'} | {res[key]['elapsed_seconds']}s |")
    lines += ["", "### Delta（合并后 − 合并前）", "",
              "| 指标 | 合并前 | 合并后(alias) | Δ(alias) | 合并后(原始引用) | Δ(原始) |",
              "|---|---|---|---|---|---|"]
    for key, label in (("hit_at_1", "@1"), ("hit_at_5", "@5"),
                       ("hit_rate_at_1", "@1 率"), ("hit_rate_at_5", "@5 率"),
                       ("avg_source_rank", "平均来源排名")):
        x = d[key]
        lines.append(f"| {label} | {x['baseline']} | {x['merged_alias']} | {x['delta_alias']:+} | "
                     f"{x['merged_raw']} | {x['delta_raw']:+} |")
    lines += ["", "## 三、受影响问题（逐条）", ""]
    aff = cmp["affected_questions"]
    if aff:
        lines += ["| # | 问题ID | concept | 合并前(@1/命中/排名) | 合并后(@1/命中/排名) | 变化 |",
                  "|---|---|---|---|---|---|"]
        for i, a in enumerate(aff, 1):
            b, af = a["before"], a["after"]
            lines.append(f"| {i} | `{a['question_id']}` | {a.get('concept') or '—'} | "
                         f"{'✓' if b['hit_at_1'] else '—'}/{'✓' if b['hit'] else '✗'}/{b['source_rank'] or '—'} | "
                         f"{'✓' if af['hit_at_1'] else '—'}/{'✓' if af['hit'] else '✗'}/{af['source_rank'] or '—'} | "
                         f"{a['delta']} |")
    else:
        lines.append("- 无受影响问题（@1/@5/来源排名均不变）")
    lines += ["", "## 四、结论与建议", "",
              f"**结论**：{report['conclusion']}", "",
              f"**判定**：`{report['verdict']}` → **{report['recommendation']['action']}**", "",
              "### 前置条件（若执行真实合并）", ""]
    lines += [f"{i}. {x}" for i, x in enumerate(report["recommendation"]["preconditions"], 1)]
    lines += ["", "### 风险", "", report["recommendation"]["risk"],
              "", "## 五、可选 89 查询评测", "",
              f"- 未运行：{report['optional_89_eval']['reason']}",
              "", "## 六、说明与边界", "",
              "1. 全程**内存模拟**：不写 kb_units_all.json、不写任何 portal/static JSON；只写本报告。",
              "2. 检索链路复用 `kb_qa.search`（units + corpus 融合，retrieval-only，不调 LLM）；对比差异来自单元集变化。",
              "3. 被合并单元的 id 不进入检索索引；`merged_alias` 先把问题库 source_unit_id 按 alias 重映射，模拟真实合并后的引用更新。",
              "4. 结论仅针对 32 条候选问题；不同问题集可能得到不同收益，执行合并前建议扩样复核。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


def run(*, units_all_path: str | Path = UNITS_ALL, dedup_path: str | Path = DEDUP,
        questions_path: str | Path = QUESTIONS, out_dir: str | Path = OUT_DIR,
        topk: int = TOPK, write: bool = False, limit: int | None = None,
        search_fn=None, generated_at: str | None = None, with_corpus: bool = False) -> dict:
    """执行内存合并模拟（默认 dry-run：不写盘）。"""
    api, qa, selfcheck = load_stack()
    try:
        qa._kb = api  # 让 kb_qa.kb_api() 复用本模块的 kb_read_api 实例（合并 patch 才生效）
    except Exception:  # noqa: BLE001
        pass
    units_json, units_err = read_json_tolerant(units_all_path)
    dedup, dedup_err = read_json_tolerant(dedup_path)
    questions_json, q_err = read_json_tolerant(questions_path)
    units_all = (units_json or {}).get("units") if isinstance(units_json, dict) else []
    questions = (questions_json or {}).get("questions") if isinstance(questions_json, dict) else []
    questions = [q for q in (questions or []) if isinstance(q, dict) and q.get("question")]
    if limit:
        questions = questions[:max(1, int(limit))]

    plan = plan_merge(dedup or {}, units_all or [], api)
    original_units = api.load_units()  # 检索链真实单元集（含 id）
    before_index = build_index(original_units)
    merged_units = apply_merge_to_index(original_units, plan)
    after_index = build_index(merged_units)
    # 一致性校验：合并组的主单元必须都在索引中；被剔除单元不得存在
    plan_issues = []
    for g in plan["groups"]:
        if g["primary_unit_id"] not in before_index:
            plan_issues.append(f"{g['group_id']}: 主单元 {g['primary_unit_id']} 不在检索索引")
        if g["primary_unit_id"] not in after_index:
            plan_issues.append(f"{g['group_id']}: 主单元丢失于合并后索引")
    remaining_dropped = [d for d in plan["dropped_ids"] if d in after_index]
    if remaining_dropped:
        plan_issues.append(f"合并后索引仍含被剔除单元: {remaining_dropped}")

    def default_search(question: str, k: int):
        return qa.search(question, topk=k)

    fn = search_fn or default_search
    remapped_questions, alias_records = remap_questions(questions, plan["alias_remap"])
    # 合并后：临时 patch kb_read_api.load_units（仅进程内，结束恢复）
    original_loader = api.load_units
    # 单元文件加载较重：baseline 用进程内缓存（kb_read_api.cmd_search 每条查询都会调用 load_units）
    api.load_units = lru_cache(maxsize=1)(original_loader)  # type: ignore[assignment]
    try:
        baseline = run_eval(questions, fn, selfcheck, topk)
        try:
            api.load_units = lambda: list(merged_units)  # type: ignore[assignment]
            merged_alias = run_eval(remapped_questions, fn, selfcheck, topk)
            merged_raw = run_eval(questions, fn, selfcheck, topk)
        finally:
            pass
    finally:
        api.load_units = original_loader  # type: ignore[assignment]

    units_meta = {
        "path": str(units_all_path), "exists": Path(units_all_path).is_file(),
        "error": units_err, "declared_total": (units_json or {}).get("total") if isinstance(units_json, dict) else None,
        "loaded_units": len(units_all or []), "retrieval_units": len(original_units),
        "sha1_before": hashlib.sha1(Path(units_all_path).read_bytes()).hexdigest()
        if Path(units_all_path).is_file() else None,
    }
    report = build_report(units_all_meta=units_meta, questions_path=str(questions_path),
                          plan=plan, baseline=baseline, merged_alias=merged_alias,
                          merged_raw=merged_raw, aliases=alias_records, generated_at=generated_at,
                          with_corpus=with_corpus)
    report["plan_issues"] = plan_issues
    report["questions_meta"] = {"path": str(questions_path), "error": q_err, "count": len(questions)}
    report["dedup_meta"] = {"path": str(dedup_path), "error": dedup_err,
                            "sha1": hashlib.sha1(Path(dedup_path).read_bytes()).hexdigest()
                            if Path(dedup_path).is_file() else None}
    report["index_rebuild"] = {
        "before": len(before_index), "after": len(after_index),
        "dropped_present_after": remaining_dropped,
        "ok": not plan_issues,
    }
    # 写前自证：真实数据哈希未变（先计算再写报告，确保 JSON 内含该字段）
    sha_after = hashlib.sha1(Path(units_all_path).read_bytes()).hexdigest() if Path(units_all_path).is_file() else None
    report["real_data_unchanged"] = (sha_after == units_meta["sha1_before"])
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"merge_sim_{DATE_TAG}.json"
        mp = out / f"merge_sim_{DATE_TAG}.md"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(report), encoding="utf-8")
        report["_outputs"] = {"json": str(jp), "md": str(mp)}
    return report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="concept 合并影响模拟（内存副本，不改真实数据）")
    parser.add_argument("command", nargs="?", default="sim", choices=["sim"], help="sim=模拟（默认）")
    parser.add_argument("--units-all", default=str(UNITS_ALL))
    parser.add_argument("--dedup", default=str(DEDUP))
    parser.add_argument("--questions", default=str(QUESTIONS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--topk", type=int, default=TOPK)
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 题（0=全部）")
    parser.add_argument("--write", action="store_true", help="写 research/reports/merge_sim_20261101.{json,md}")
    parser.add_argument("--with-corpus", action="store_true", help="检索时启用 corpus chunk 支路（默认关闭以隔离单元合并影响）")
    parser.add_argument("--with-89", action="store_true", dest="with_89", help="可选：89 查询评测（预留，本次未实现）")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(units_all_path=args.units_all, dedup_path=args.dedup, questions_path=args.questions,
                 out_dir=args.out_dir, topk=args.topk, write=args.write,
                 limit=args.limit or None)
    s = report["results"]
    b, a, r = s["baseline"]["summary"], s["merged_alias"]["summary"], s["merged_raw"]["summary"]
    if args.json:
        out = {k: v for k, v in report.items() if k != "_outputs"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[merge_sim] 合并 {report['merge_plan']['merge_groups']} 组（{report['merge_plan']['units_before']}"
              f"→{report['merge_plan']['units_after']} 单元）| 32 问题 top-{args.topk}")
        print(f"  @1: baseline {b['hit_at_1']}/{b['total']} → alias {a['hit_at_1']}/{a['total']} "
              f"(Δ{a['hit_at_1'] - b['hit_at_1']:+d}) | raw {r['hit_at_1']}/{r['total']}")
        print(f"  @5: baseline {b['hit_at_5']}/{b['total']} → alias {a['hit_at_5']}/{a['total']} "
              f"(Δ{a['hit_at_5'] - b['hit_at_5']:+d}) | raw {r['hit_at_5']}/{r['total']}")
        print(f"  平均来源排名: {b['avg_source_rank']} → alias {a['avg_source_rank']} | raw {r['avg_source_rank']}")
        print(f"  受影响问题: {len(report['comparison']['affected_questions'])} 条 | 结论: {report['verdict']}")
        print(f"  {report['conclusion']}")
        print(f"  真实数据未修改: {report.get('real_data_unchanged', '（dry-run 未写报告）')}")
        for name, path in (report.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
    if args.with_89:
        print("  提示：89 查询评测为可选步骤，本次未实现（见报告 optional_89_eval）。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
