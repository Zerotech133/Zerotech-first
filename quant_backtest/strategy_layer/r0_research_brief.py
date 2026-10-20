"""从 R0 只读探针的内存结果生成研究摘要；默认不落盘、不接收。"""
from __future__ import annotations

from pathlib import Path

from .r0_readonly_e2e import write_research_evidence


_COUNT_KEYS = ("source_events", "matched_events", "signals", "kept", "combined", "pool")


def build_research_brief(result: dict, *, output_path: str | Path | None = None) -> dict:
    """仅消费只读探针返回值；显式输出须为已有 research 目录中的新 JSON。"""
    if (not isinstance(result, dict)
            or result.get("scope") != "RESEARCH_ONLY_NOT_PRODUCTION"
            or result.get("cutover_verdict") != "BLOCK_CUTOVER"
            or not isinstance(result.get("source"), dict)
            or not isinstance(result.get("counts"), dict)):
        raise ValueError("需要 R0 只读探针的研究边界结果")

    source = result["source"]
    counts = {key: result["counts"].get(key) for key in _COUNT_KEYS}
    ready = (result.get("status") == "UNVERIFIED"
             and result.get("chain_status") == "VERIFIED_IN_MEMORY")
    combined = result.get("combined") if ready else None
    pool = result.get("pool") if ready else None
    if ready and (not isinstance(combined, list) or not isinstance(pool, dict)
                  or not isinstance(pool.get("stats"), dict)):
        raise ValueError("内存链状态与 combined/pool 不一致")

    contributors = superseded = None
    pool_summary = None
    if ready:
        contributors = superseded = 0
        for row in combined:
            contributions = row["contributions"]
            contributors += len(contributions)
            for contribution in contributions.values():
                superseded += len(contribution.get("superseded", ()))
        stats = pool["stats"]
        pool_summary = {key: stats.get(key) for key in
                        ("buy_total", "divergence_total", "review_total",
                         "tier1_total", "tier2_total")}

    failures = result.get("failures") or []
    risks = ["自声明 manifest 不构成独立冻结源身份或真实批次验证",
             "尚无简报接收回执；不得用于生产或交易判断"]
    if failures:
        risks.append("探针存在源/adapter 失败，后续段落不可按零计数")
    if result.get("chain_status") == "SELF_DECLARED_EMPTY":
        risks.append("零事件仅为自声明批次，不等于缺源或已核验零信号")
    risks.append("可选字段缺项无结构化计数；不能从来源自由文本 rationale 推断或声称字段覆盖率")
    risks.append("仅生成内存研究简报；brief_status/receipt_status 仍为 NOT_VERIFIED，未验证简报接收")

    brief = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
        "cutover_verdict": "BLOCK_CUTOVER",
        "trade_date": result.get("trade_date"),
        "source": {
            "path": source.get("path"), "manifest": source.get("manifest"),
            "sha256": source.get("sha256"),
            "business_date": source.get("business_date"),
            "observed_date": source.get("observed_date"),
            "identity_status": "UNVERIFIED",
            "identity_reason": source.get("identity_reason"),
        },
        "probe_status": result.get("status"),
        "chain_status": result.get("chain_status"),
        "pool_status": result.get("pool_status"),
        "brief_status": "NOT_VERIFIED",
        "receipt_status": "NOT_VERIFIED",
        "real_source_verdict": result.get("real_source_verdict"),
        "counts": {**counts, "brief": None, "receipt": None},
        "count_basis": {
            "source_events": "整份源池事件数；历史多日池不等于当日事件数",
            "matched_events": "匹配 trade_date 的当日事件数；未提供结构化计数时为 None",
            "signals": "参与当日转换的信号数；历史池仅含匹配当日事件",
        },
        "generation_note": "仅生成内存研究简报；不代表真实简报已核验或收到接收回执",

        "scheme_c": {
            "contributions": contributors, "superseded": superseded,
            "basis": "融合行中的 contributions/superseded；仅内存链完成时可统计",
        },
        "pool_summary": pool_summary,
        "optional_missing": {
            "observed_kept_signal_rationale": None,
            "status": "NOT_VERIFIED",
            "coverage": "无结构化缺项计数；不可从来源自由文本 rationale 推断，原始事件字段覆盖率未验证",
        },
        "failures": [{"stage": f.get("stage"), "strategy": f.get("strategy"),
                      "reason": f.get("reason")} for f in failures],
        "risks": risks,
    }
    if output_path is not None:
        write_research_evidence(brief, output_path)
    return brief
