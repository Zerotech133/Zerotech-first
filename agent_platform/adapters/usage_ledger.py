"""M2-P4 统一模型使用账本（全只读聚合器）。

定位：M5 Token 预算的前置数据面。只聚合、不改账本、不路由——
- 模型路由本身不动（Hermes/agent_coder 内部路由是既有纪律红线）；
- 成本归集复用既有台账，不建第二账本。

数据源（全部只读）：
- portal/data/llm_usage.jsonl：token/时长台账（agent_coder._log_usage 落账，
  字段 ts/agent/channel/label/in_tok/out_tok/elapsed/chars）；
- agent_platform/data/llm_tool_audit.jsonl：平台 API 调用审计
  （ts/caller/endpoint/method/ok）；
- agent_platform/registry/org.json：agent→dept 权威映射（departments[].agents）。

分账口径：
- by_domain：agent 经 org.json 映射到业务域；映射不到的记 unmapped
  （fail-visible，不猜测——无源 ID 不伪造判例）；
- by_agent / by_channel：直列；
- credits：智谱积分估算（anthropic 通道单次 ≈4.5 积分，K-ORG-038 判例
  取中），显式标注 estimate；
- 归因到工单复用 tools/cost_report._TID_RE 口径（label 含 TID）。

CLI：``python adapters/usage_ledger.py report [--days 7]``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_ADAPTERS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _ADAPTERS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

ROOT = _AGENT_PLATFORM.parent
USAGE_JSONL = ROOT / "portal" / "data" / "llm_usage.jsonl"
TOOL_AUDIT_JSONL = _AGENT_PLATFORM / "data" / "llm_tool_audit.jsonl"
ORG_JSON = _AGENT_PLATFORM / "registry" / "org.json"

_ANTHROPIC_CREDIT_PER_CALL = 4.5  # K-ORG-038 判例取中（估算）
_TID_RE = re.compile(r"K-[\w-]+")


def _resolve(root: Optional[Path] = None):
    """路径统一走函数（P2/P3 判例：模块级常量即测试债务）。

    base = agent_platform 目录（root 缺省即本平台）；usage 台账在
    仓根 portal/data/ 下，故取 base.parent。
    """
    base = Path(root) if root else _AGENT_PLATFORM
    return {
        "usage": base.parent / "portal" / "data" / "llm_usage.jsonl",
        "audit": base / "data" / "llm_tool_audit.jsonl",
        "org": base / "registry" / "org.json",
    }


def agent_dept_map(root: Optional[Path] = None) -> Dict[str, str]:
    """org.json 权威映射：agent_id → dept（含 lead）。"""
    paths = _resolve(root)
    if not paths["org"].exists():
        return {}
    org = json.loads(paths["org"].read_text(encoding="utf-8"))
    mapping = {}
    for d in org.get("departments", []):
        for a in d.get("agents", []):
            mapping.setdefault(a, d["dept"])
        if d.get("lead"):
            mapping.setdefault(d["lead"], d["dept"])
    return mapping


def _read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _in_window(ts: str, since: Optional[datetime]) -> bool:
    if since is None:
        return True
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") >= since
    except (ValueError, TypeError):
        return True  # 坏时间戳保留（宁多勿丢）


def build_usage_report(root: Optional[Path] = None, days: Optional[int] = None) -> Dict:
    """统一使用账本报告（全只读，fail-open 单源缺失）。"""
    paths = _resolve(root)
    since = None
    if days:
        since = datetime.now() - timedelta(days=days)

    amap = agent_dept_map(root)
    usage = [
        r for r in _read_jsonl(paths["usage"]) if _in_window(r.get("ts", ""), since)
    ]
    audit = [
        r for r in _read_jsonl(paths["audit"]) if _in_window(r.get("ts", ""), since)
    ]

    findings = []
    for name, p in (
        ("llm_usage", paths["usage"]),
        ("llm_tool_audit", paths["audit"]),
        ("org", paths["org"]),
    ):
        if not p.exists():
            findings.append("%s 缺失：%s（该源计 0，不伪造）" % (name, p.name))
    if not amap:
        findings.append("org.json 无 departments 映射，by_domain 全落 unmapped")

    by_domain = defaultdict(
        lambda: {"calls": 0, "in_tok": 0, "out_tok": 0, "elapsed": 0.0, "chars": 0}
    )
    by_agent = defaultdict(lambda: {"calls": 0, "in_tok": 0, "out_tok": 0})
    by_channel = defaultdict(lambda: {"calls": 0, "in_tok": 0, "out_tok": 0})
    unmapped_agents = defaultdict(int)
    by_ticket = defaultdict(lambda: {"calls": 0, "in_tok": 0, "out_tok": 0})

    for r in usage:
        agent = r.get("agent") or "unknown"
        ch = r.get("channel", "?")
        it = int(r.get("in_tok") or 0)
        ot = int(r.get("out_tok") or 0)
        for agg in (by_agent[agent], by_channel[ch]):
            agg["calls"] += 1
            agg["in_tok"] += it
            agg["out_tok"] += ot
        dom = amap.get(agent)
        if dom is None:
            unmapped_agents[agent] += 1
            dom = "unmapped"
        d = by_domain[dom]
        d["calls"] += 1
        d["in_tok"] += it
        d["out_tok"] += ot
        d["elapsed"] += float(r.get("elapsed") or 0.0)
        d["chars"] += int(r.get("chars") or 0)
        m = _TID_RE.search(str(r.get("label", "")))
        if m:
            t = by_ticket[m.group(0)]
            t["calls"] += 1
            t["in_tok"] += it
            t["out_tok"] += ot

    totals = {
        "usage_calls": len(usage),
        "in_tok": sum(v["in_tok"] for v in by_channel.values()),
        "out_tok": sum(v["out_tok"] for v in by_channel.values()),
        "api_calls": len(audit),
        "api_fail": sum(1 for r in audit if r.get("ok") is False),
    }
    credits = (
        by_channel.get("anthropic", {}).get("calls", 0) * _ANTHROPIC_CREDIT_PER_CALL
    )
    verdict = "PASS" if not findings else "WARN"
    return {
        "verdict": verdict,
        "window_days": days or 0,  # 0=全量
        "totals": totals,
        "credits_estimate": round(credits, 1),
        "by_domain": {k: dict(v) for k, v in sorted(by_domain.items())},
        "by_agent": {k: dict(v) for k, v in sorted(by_agent.items())},
        "by_channel": {k: dict(v) for k, v in sorted(by_channel.items())},
        "by_ticket_top10": dict(
            sorted(by_ticket.items(), key=lambda kv: -kv[1]["in_tok"])[:10]
        ),
        "unmapped_agents": dict(unmapped_agents),
        "findings": findings,
        "read_only": True,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2-P4 统一使用账本（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_r = sub.add_parser("report")
    p_r.add_argument("--root", default=".")
    p_r.add_argument("--days", type=int, default=0)
    args = ap.parse_args(argv)
    rep = build_usage_report(
        Path(args.root) if args.root != "." else None, days=args.days or None
    )
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
