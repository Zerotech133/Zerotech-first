#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-04：预算 / 运行成本只读模型 v1。

复用 M2-P4 `adapters.usage_ledger.build_usage_report()`（只读聚合），
不建第二账本、不改模型路由。

预算阈值（可用环境变量覆盖）：
- HQ_TOKEN_BUDGET（默认 1_000_000）
- HQ_CREDIT_BUDGET（默认 100）
用法：python -X utf8 tools/hq_budget.py [--days 7] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
sys.path.insert(0, str(AP))
from adapters.usage_ledger import build_usage_report  # noqa: E402


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except Exception:  # noqa: BLE001
        return default


def build_budget(days: int | None = None) -> dict:
    rep = build_usage_report(days=days)
    totals = rep.get("totals") or {}
    tokens = int(totals.get("in_tok", 0)) + int(totals.get("out_tok", 0))
    token_budget = _int_env("HQ_TOKEN_BUDGET", 1_000_000)
    credit_budget = _int_env("HQ_CREDIT_BUDGET", 100)
    credits = float(rep.get("credits_estimate") or 0.0)
    risks = []
    if token_budget and tokens > token_budget:
        risks.append(f"token 超预算：{tokens} > {token_budget}")
    if credit_budget and credits > credit_budget:
        risks.append(f"credit 超预算：{credits} > {credit_budget}")
    if int(totals.get("api_fail", 0)) > 0:
        risks.append(f"API 失败 {totals.get('api_fail')} 次（fail-visible）")
    if int(totals.get("usage_calls", 0)) == 0:
        risks.append("usage 台账无调用记录（可能只落 api 审计）")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "window_days": rep.get("window_days"),
        "totals": totals,
        "credits_estimate": credits,
        "budget": {
            "token_budget": token_budget,
            "credit_budget": credit_budget,
            "token_utilization": round(tokens / token_budget, 4) if token_budget else None,
            "credit_utilization": round(credits / credit_budget, 4) if credit_budget else None,
        },
        "by_domain": rep.get("by_domain") or {},
        "by_agent_top": sorted((rep.get("by_agent") or {}).items(), key=lambda kv: -(kv[1].get("calls", 0)))[:10],
        "by_channel": rep.get("by_channel") or {},
        "findings": rep.get("findings") or [],
        "risks": risks,
    }


def render_md(b: dict) -> str:
    t = b["totals"]
    u = b["budget"]
    lines = [
        "# 零界 HQ · 预算/运行成本 v1",
        "",
        f"- 生成：{b['generated_at']}｜窗口：{b['window_days']} 天｜只读：是",
        f"- 调用：usage **{t.get('usage_calls', 0)}** / API **{t.get('api_calls', 0)}**（失败 {t.get('api_fail', 0)}）",
        f"- Token：in {t.get('in_tok', 0)} / out {t.get('out_tok', 0)}｜预算 {u['token_budget']}｜利用率 {u['token_utilization']}",
        f"- Credits 估算：{b['credits_estimate']}｜预算 {u['credit_budget']}｜利用率 {u['credit_utilization']}",
        "",
        "## 域分布",
        "",
        "| 域 | calls | in_tok | out_tok |",
        "|---|---:|---:|---:|",
    ]
    for k, v in sorted(b["by_domain"].items(), key=lambda kv: -kv[1].get("calls", 0)):
        lines.append(f"| {k} | {v.get('calls', 0)} | {v.get('in_tok', 0)} | {v.get('out_tok', 0)} |")
    lines += ["", "## 席位 Top", ""]
    for k, v in b["by_agent_top"]:
        lines.append(f"- {k}：calls={v.get('calls', 0)} in={v.get('in_tok', 0)} out={v.get('out_tok', 0)}")
    lines += ["", "## 风险", ""]
    lines += [f"- {x}" for x in b["risks"]] or ["- 无"]
    lines += ["", "> 只读聚合；预算阈值可用 HQ_TOKEN_BUDGET/HQ_CREDIT_BUDGET 覆盖，不写账本。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-04 预算/运行成本只读模型")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    b = build_budget(days=args.days)
    print(json.dumps(b, ensure_ascii=False, indent=1) if args.json else render_md(b), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
