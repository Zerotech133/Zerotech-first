# -*- coding: utf-8 -*-
"""荀攸参谋简报器（M0 计划 B2「选股参谋只读试运行」准备件，2026-10-19）。

定位：读池 + 读证据链 + 读后验 → 本地留痕（reports/xunyou_brief_{date}.md
+ 同名 .json 结构化版）。**零推送、零写库、不触交易**——全程只读：
  - 池档：只读 reports/xunyou_pool_{date}.json（每日 17:45 盘后作业产物，
    本简报不重复池日报内容，只做三节汇总留痕）；
  - 证据链：复用 xunyou.read_evidence_state（file:?mode=ro 只读连接，
    绝不调用 transition / rebuild_state）；
  - 后验：复用 xunyou.read_posterior（只读统计输出档，不重算、不触 PG）。

纪律标注：RESEARCH_ONLY_NOT_PRODUCTION / BLOCK_CUTOVER——确认门②③未过，
本简报为研究产物，不构成交易指令、不切线上 picks、不改权重。

closed on failure：任何一节缺料/异常均降级占位（DEGRADED + 文案），
绝不抛异常中断整份简报；文件头与产出内显式标注研究性质。

用法：
    python strategy_layer/xunyou_brief.py                 # 当日
    python strategy_layer/xunyou_brief.py --date 20261017 # 历史日回看
    可选覆盖：--out-dir / --pool / --evidence-db / --posterior
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_QUANT_BT = os.path.dirname(_HERE)
if _QUANT_BT not in sys.path:
    sys.path.insert(0, _QUANT_BT)

from strategy_layer.xunyou import (
    REPORT_DIR,
    SCOPE,
    VERDICT,
    read_evidence_state,
    read_posterior,
)  # noqa: E402

AGENT_NAME = "荀攸（公达）参谋简报器"

# 降级占位文案（缺料不崩，逐节独立降级）
NO_POOL_TEXT = "池档未产出"
NO_EVIDENCE_TEXT = "证据链未启用或不可读"
NO_POSTERIOR_TEXT = "后验档未产出"


# ── 第一节：池摘要 ────────────────────────────────────────────────────
def _pool_section(trade_date: str, pool_path: str) -> dict:
    """只读池档 → 摘要（票数 / tier 分布 / 分歧票清单）。缺档降级。"""
    if not os.path.isfile(pool_path):
        return {"status": "DEGRADED", "reason": NO_POOL_TEXT, "source": pool_path}
    with open(pool_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        return {"status": "DEGRADED", "reason": "池档结构异常", "source": pool_path}
    rows = doc.get("pool") or []
    tier_dist = Counter(str(r.get("tier") or "") for r in rows)
    divergent = [
        str(r.get("symbol"))
        for r in rows
        if r.get("divergence") is True
        or r.get("divergence") == "true"
        or r.get("review_required")
    ]
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    return {
        "status": "OK",
        "source": pool_path,
        "generated_at": doc.get("generated_at", ""),
        "buy_total": int(stats.get("buy_total", len(rows))),
        "tier_dist": {
            "T1": tier_dist.get("T1", 0),
            "T2": tier_dist.get("T2", 0),
            "unrated": tier_dist.get("", 0),
        },
        "divergence_symbols": divergent,
        "divergence_total": len(divergent),
    }


# ── 第二节：证据链视角 ────────────────────────────────────────────────
def _evidence_section(trade_date: str, db_path: str | None) -> dict:
    """read_evidence_state 的 state 计数分布；DEGRADED 显式降级标注。"""
    out = read_evidence_state(trade_date=trade_date, db_path=db_path)
    if out.get("status") != "OK":
        return {
            "status": "DEGRADED",
            "reason": NO_EVIDENCE_TEXT,
            "detail": out.get("reason", ""),
        }
    states = out.get("states") or []
    counts = Counter(str(row.get("state") or "unknown") for row in states)
    anomalies = []
    if out.get("count", 0) == 0:
        anomalies.append("当日无证据记录（可能未接入记账）")
    if out.get("count", 0) >= 200:
        anomalies.append("达到单次查询上限 200，计数可能截断")
    return {
        "status": "OK",
        "count": out.get("count", 0),
        "state_counts": dict(sorted(counts.items())),
        "anomalies": anomalies,
    }


# ── 第三节：后验视角 ──────────────────────────────────────────────────
def _posterior_section(posterior_path: str | None) -> dict:
    """read_posterior 的 by_strategy / by_weight_group 关键数字；缺档降级。"""
    out = read_posterior(posterior_path)
    if out.get("status") != "OK":
        return {
            "status": "DEGRADED",
            "reason": NO_POSTERIOR_TEXT,
            "detail": out.get("reason", ""),
        }

    def _flat(block: dict) -> dict:
        # 单层块 {key: {win: {n,hit,avg,med}}} → 取 1 日窗关键数
        flat = {}
        for key, wins in (block or {}).items():
            if not isinstance(wins, dict):
                continue
            w1 = wins.get("1") or wins.get(1) or {}
            if isinstance(w1, dict):
                flat[str(key)] = {k: w1.get(k) for k in ("n", "hit", "avg", "med")}
        return flat

    def _flat2(block: dict) -> dict:
        # 两级块 by_weight_group {strategy: {group: {win: {...}}}}
        nested = {}
        for strat, groups in (block or {}).items():
            if not isinstance(groups, dict):
                continue
            sub = _flat(groups)
            if sub:
                nested[str(strat)] = sub
        return nested

    return {
        "status": "OK",
        "asof": out.get("asof", ""),
        "by_strategy": _flat(out.get("by_strategy")),
        "by_weight_group": _flat2(out.get("by_weight_group")),
    }


# ── 简报组装与落盘 ────────────────────────────────────────────────────
def _guarded(section_fn, *args, **kwargs) -> dict:
    """单节源抛异常 → 降级占位，整体继续（closed on failure 逐节隔离）。"""
    try:
        return section_fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 降级不中断整份简报
        return {
            "status": "DEGRADED",
            "reason": "单节读取异常，已降级占位",
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }


def _render_md(trade_date: str, sections: dict) -> str:
    pool, evid, post = sections["pool"], sections["evidence"], sections["posterior"]
    lines = [
        "# 荀攸参谋简报（%s）" % trade_date,
        "",
        "- 生成：%s ｜ 性质：%s ｜ %s"
        % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), SCOPE, VERDICT),
        "- 本简报为只读试运行留痕：零推送、零写库、不触交易、不切 picks；"
        "确认门②③未过，不构成交易指令",
        "",
        "## 一、池摘要",
    ]
    if pool.get("status") == "OK":
        td = pool["tier_dist"]
        lines += [
            "- 状态：OK（来源：%s）" % os.path.basename(pool["source"]),
            "- 池内 buy 候选 **%d** 只；tier 分布：T1=%d、T2=%d、未入档=%d"
            % (pool["buy_total"], td["T1"], td["T2"], td["unrated"]),
            "- 分歧/人工复核票 %d 只：%s"
            % (
                pool["divergence_total"],
                "、".join(pool["divergence_symbols"]) or "（无）",
            ),
        ]
    else:
        lines.append(
            "- 状态：DEGRADED ｜ %s（%s）"
            % (pool.get("reason", "未知"), pool.get("detail", "缺料降级"))
        )
    lines += ["", "## 二、证据链视角"]
    if evid.get("status") == "OK":
        lines.append("- 状态：OK ｜ 当日证据态 %d 条" % evid["count"])
        for st, n in evid["state_counts"].items():
            lines.append("  - %s：%d" % (st, n))
        for a in evid["anomalies"]:
            lines.append("  - 异常提示：%s" % a)
    else:
        lines.append(
            "- 状态：DEGRADED ｜ %s（%s）"
            % (evid.get("reason", "未知"), evid.get("detail", ""))
        )
    lines += ["", "## 三、后验视角"]
    if post.get("status") == "OK":
        lines.append("- 状态：OK ｜ asof=%s" % post["asof"])
        lines.append("- by_strategy（1 日窗）：")
        for key, w in post["by_strategy"].items():
            lines.append(
                "  - %s：n=%s hit=%s avg=%s med=%s"
                % (key, w.get("n"), w.get("hit"), w.get("avg"), w.get("med"))
            )
        if not post["by_strategy"]:
            lines.append("  - （无分层记录）")
        lines.append("- by_weight_group（1 日窗）：")
        for strat, groups in post["by_weight_group"].items():
            if isinstance(groups, dict) and "n" not in groups:
                # 两级：strategy → weight_group → 窗口
                for grp, w in groups.items():
                    lines.append(
                        "  - %s/%s：n=%s hit=%s avg=%s med=%s"
                        % (
                            strat,
                            grp,
                            w.get("n"),
                            w.get("hit"),
                            w.get("avg"),
                            w.get("med"),
                        )
                    )
            else:
                w = groups
                lines.append(
                    "  - %s：n=%s hit=%s avg=%s med=%s"
                    % (strat, w.get("n"), w.get("hit"), w.get("avg"), w.get("med"))
                )
        if not post["by_weight_group"]:
            lines.append("  - （无分层记录）")
    else:
        lines.append(
            "- 状态：DEGRADED ｜ %s（%s）"
            % (post.get("reason", "未知"), post.get("detail", ""))
        )
    lines += [
        "",
        "## 纪律",
        "- %s ｜ %s：研究留痕，不对外直发、不推送" % (SCOPE, VERDICT),
        "- 全程只读：未写任何数据库、未调 transition、未触发后验重算",
        "",
    ]
    return "\n".join(lines)


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def build_brief(
    trade_date: str,
    pool_path: str | None = None,
    evidence_db_path: str | None = None,
    posterior_path: str | None = None,
    out_dir: str | None = None,
) -> dict:
    """三节简报：池摘要 + 证据链视角 + 后验视角 → MD + JSON 双落盘。

    只读试运行：除本简报自身两份产物外零写入；逐节降级（closed on
    failure），任何单节缺料/异常都不中断整份简报。返回结构化摘要。
    """
    date = str(trade_date)
    out_root = out_dir or REPORT_DIR
    pool_file = pool_path or os.path.join(REPORT_DIR, "xunyou_pool_%s.json" % date)

    sections = {
        "pool": _guarded(_pool_section, date, pool_file),
        "evidence": _guarded(_evidence_section, date, evidence_db_path),
        "posterior": _guarded(_posterior_section, posterior_path),
    }
    doc = {
        "agent": AGENT_NAME,
        "trade_date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": SCOPE,
        "cutover_verdict": VERDICT,
        "sections": sections,
    }
    os.makedirs(out_root, exist_ok=True)
    json_path = os.path.join(out_root, "xunyou_brief_%s.json" % date)
    md_path = os.path.join(out_root, "xunyou_brief_%s.md" % date)
    _atomic_write(
        json_path, json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    )
    _atomic_write(md_path, _render_md(date, sections).encode("utf-8"))
    doc["brief_md_path"] = md_path
    doc["brief_json_path"] = json_path
    return doc


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        description="荀攸参谋简报器（只读试运行留痕，零推送零写库）"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="交易日 YYYYMMDD（默认今日）",
    )
    parser.add_argument(
        "--out-dir", default=None, help="产物目录（默认 quant_backtest/reports）"
    )
    parser.add_argument("--pool", default=None, help="池档路径显式覆盖")
    parser.add_argument("--evidence-db", default=None, help="证据链库路径显式覆盖")
    parser.add_argument("--posterior", default=None, help="后验统计档路径显式覆盖")
    args = parser.parse_args(argv)
    doc = build_brief(
        args.date,
        pool_path=args.pool,
        evidence_db_path=args.evidence_db,
        posterior_path=args.posterior,
        out_dir=args.out_dir,
    )
    print("[xunyou_brief] %s %s ｜ %s ｜ %s" % (AGENT_NAME, args.date, SCOPE, VERDICT))
    for name, sec in doc["sections"].items():
        print("[xunyou_brief] %-9s %s" % (name, sec["status"]))
    print("[xunyou_brief] 简报 → %s" % doc["brief_md_path"])
    print("[xunyou_brief] 结构化 → %s" % doc["brief_json_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
