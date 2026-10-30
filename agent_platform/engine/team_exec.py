# -*- coding: utf-8 -*-
"""团队执行账与结果摘要（2026-10-30 WO-INV-TEAM-ENGINE2）。

对齐菜鸟 `TeamTaskServiceImpl`：
- node_exec：normal/debate/degraded、duration、retry、structured、debate_log；
- createResultSummary：五档/置信/风险/目标/周期 + 执行统计，落 run summary 文件。

只追加、只读；路径可由 TEAM_EXEC_DIR 覆盖（测试/隔离）。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = os.path.join(REPO, "agent_platform", "data", "team_exec")
SUMMARY_FILE = "result_summaries.jsonl"


def _dir():
    return os.environ.get("TEAM_EXEC_DIR") or DEFAULT_DIR


def reset_for_tests(path=None):
    """仅测试用：设置目录（不删除文件）。"""
    if path:
        os.environ["TEAM_EXEC_DIR"] = str(path)


def run_path(run_id):
    return os.path.join(_dir(), str(run_id) + ".jsonl")


def summary_path(run_id):
    return os.path.join(_dir(), str(run_id) + "_summary.json")


def record(run_id, node, phase, execution_mode, status, duration_ms,
           input_text="", output_text="", structured=None, debate_log=None,
           retry=0):
    """追加一条节点执行账；返回记录。"""
    rec = {
        "run_id": str(run_id),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "node_key": node.get("node_key"),
        "agent_id": node.get("agent_id"),
        "role": node.get("role_profile"),
        "analysis_type": node.get("analysis_type"),
        "phase": phase,
        "execution_mode": execution_mode,
        "status": status,
        "retry": int(retry or 0),
        "duration_ms": round(float(duration_ms or 0), 1),
        "input": str(input_text or "")[:2000],
        "output": str(output_text or "")[:4000],
        "structured": structured or {},
        "debate_log": debate_log or [],
    }
    os.makedirs(_dir(), exist_ok=True)
    with io.open(run_path(run_id), "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_run(run_id):
    out = []
    p = run_path(run_id)
    if not os.path.exists(p):
        return out
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def parse_decision_text(text):
    """从 D1 输出提取 JSON 决策（兼容前缀/围栏）；失败返回 {}。"""
    import re
    if not text:
        return {}
    m = re.search(r"\{[\s\S]*\}", str(text))
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def build_summary(decision, records, stock=None):
    """createResultSummary：结果契约 + 执行统计；缺字段不臆造。"""
    d = decision if isinstance(decision, dict) else {}
    conf = d.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    if conf is not None and conf > 1:
        conf = conf / 100.0
    rec = str(d.get("recommendation") or "").upper()
    risk = d.get("risk_level")
    if not risk:
        if not rec and conf is None:
            risk = "UNKNOWN"  # 无决策/无置信时不冒充 LOW
        elif rec in ("SELL", "STRONG_SELL") or (conf is not None and conf < 0.5):
            risk = "HIGH"
        elif conf is not None and conf < 0.7:
            risk = "MEDIUM"
        else:
            risk = "LOW"
    modes = {}
    retries = 0
    total_ms = 0.0
    for r in records or []:
        modes[r.get("execution_mode") or "?"] = modes.get(
            r.get("execution_mode") or "?", 0) + 1
        retries += int(r.get("retry") or 0)
        total_ms += float(r.get("duration_ms") or 0)
    return {
        "schema": "team_result_summary/v1",
        "stock": stock,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "recommendation": d.get("recommendation"),
        "confidence": conf,
        "risk_level": risk,
        "target_price": d.get("target_price") or d.get("target_price_range"),
        "time_horizon": d.get("time_horizon"),
        "rationale": d.get("rationale"),
        "l1_basis": d.get("l1_basis"),
        "node_count": len(records or []),
        "execution_modes": modes,
        "retry_total": retries,
        "duration_ms_total": round(total_ms, 1),
        "degraded": modes.get("degraded", 0) > 0,
    }


def write_summary(run_id, summary):
    os.makedirs(_dir(), exist_ok=True)
    p = summary_path(run_id)
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    with io.open(os.path.join(_dir(), SUMMARY_FILE), "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps({"run_id": str(run_id), **summary}, ensure_ascii=False) + "\n")
    return p
