# -*- coding: utf-8 -*-
"""xq_status_classified 标签口径漂移探针（P0-2）。只读分析，不改任何表。

背景（实测实锤）：
  xq_status_classified 三代 prompt 并存且标签口径系统性漂移——
  v1.2-re(1687条) 广告推广 3% vs v1.3(2406条) 9%（3倍差）、宏观政策 2% vs 5%；
  v1.3-rule(196条) 100% 广告 = 纯规则通道。跨版本统计情绪分布不分层即假信号。

判定规则：
  · 任一 (prompt_version 对, event_type) 分布占比差 >5pp 或 >2σ → 该对记 ALERT
  · 级别：>10pp 或 >3σ → CRITICAL，其余 → WARN
  · verdict：有 CRITICAL → "DRIFT-CRITICAL"；只有 WARN → "DRIFT-WARN"；无 → "STABLE"
  · σ = 该 event_type 占比在全部 LLM 版本间的总体标准差（缺该标签的版本按 0 计），
    sigma 字段 = |占比差| / σ_pp（σ_pp 为 0 时记 0.0，只走 pp 规则）
  · v*-rule 后缀 = 规则通道，排除在版本对拍之外（不算 LLM 口径）
  · 最小样本量门槛：LLM 版本总样本 n < MIN_SAMPLES(30) 不参与对拍，
    单独列入 excluded_versions（{pv: {n, reason: "too_few_samples"}}），
    防止极小样本（如 n=2 占比 100%）污染 verdict 产生假 CRITICAL

用法（automation 契约，07:40 数据健康复盘 automation id 5cede65c 消费）：
  python tools/xq_label_drift.py --json
    stdout 输出稳定 JSON 结构（字段名勿改，只允许追加）：
      {
        "generated_at": "<ISO8601>",
        "versions":      ["v1.2-re", "v1.3", ...],      # 参与 LLM 对拍的版本
        "rule_versions": ["v1.3-rule", ...],            # 规则通道（不参与对拍）
        "excluded_versions": {"v1.2": {"n": 2, "reason": "too_few_samples"}},
        "pairs": [{"a": "...", "b": "...",
                   "alerts": [{"event_type": "...", "pct_a": 3.0, "pct_b": 9.0,
                               "diff_pp": 6.0, "sigma": 2.0, "level": "WARN"}]}],
        "verdict": "STABLE" | "DRIFT-WARN" | "DRIFT-CRITICAL"
      }

  python tools/xq_label_drift.py --report [PATH]
    写 Markdown 报告，默认 C:/aios/automation_runs/xq_data_review/label_drift_YYYY-MM-DD.md
    （目录不存在自动创建）

铁律：
  · 只读连接 file:...?mode=ro，绝不写库
  · 不对 created_at/labeled_at 做 CAST/strftime unixepoch（本项目判例，本脚本不做时间运算）
  · 报告只写 C:/aios/automation_runs/xq_data_review/（可用 --report 显式覆盖，测试用 tmp_path）
"""
import argparse
import itertools
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEFAULT_DB = HERE / "data" / "xueqiu" / "xueqiu_info.db"
DEFAULT_REPORT_DIR = Path(r"C:/aios/automation_runs/xq_data_review")

# 判定阈值（百分比/百分点、σ 倍数）
ALERT_DIFF_PP = 5.0    # 占比差 > 5pp 触发 ALERT
ALERT_SIGMA = 2.0      # 差异 > 2σ 触发 ALERT
CRIT_DIFF_PP = 10.0    # 占比差 > 10pp 升级 CRITICAL
CRIT_SIGMA = 3.0       # 差异 > 3σ 升级 CRITICAL

RULE_SUFFIX = "-rule"  # 规则通道后缀，排除在 LLM 版本对拍之外

MIN_SAMPLES = 30       # LLM 版本最小样本量门槛：n < 30 不参与版本对拍


def is_rule_version(prompt_version: str) -> bool:
    """v*-rule 后缀视为规则通道，不算 LLM 口径。"""
    return str(prompt_version).endswith(RULE_SUFFIX)


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读 URI 连接，绝不写库。"""
    uri = "file:%s?mode=ro" % Path(db_path).resolve().as_posix()
    return sqlite3.connect(uri, uri=True)


def collect(cur: sqlite3.Cursor) -> dict:
    """SELECT prompt_version, event_type, COUNT(*) GROUP BY → {pv: {et: n}}。

    NULL event_type / prompt_version 归一为 "unknown"，保证 JSON 可序列化。
    """
    rows = cur.execute(
        "SELECT prompt_version, event_type, COUNT(*) "
        "FROM xq_status_classified GROUP BY prompt_version, event_type"
    ).fetchall()
    dist: dict = {}
    for pv, et, n in rows:
        pv_key = "unknown" if pv is None else str(pv)
        et_key = "unknown" if et is None else str(et)
        dist.setdefault(pv_key, {})[et_key] = dist.setdefault(pv_key, {}).get(et_key, 0) + n
    return dist


def _version_counts(dist: dict) -> dict:
    """各版本总样本量 {pv: n}。"""
    return {pv: sum(bucket.values()) for pv, bucket in dist.items()}


def _shares(dist: dict, pv: str) -> dict:
    """单版本内各 event_type 占比（%），总行数为 0 返回空 dict。"""
    bucket = dist.get(pv) or {}
    total = sum(bucket.values())
    if total <= 0:
        return {}
    return {et: n * 100.0 / total for et, n in bucket.items()}


def drift_matrix(dist: dict) -> list:
    """两两 LLM 版本对拍各 event_type，产出 [{a, b, alerts: [...]}]。

    · v*-rule 排除在版本对拍之外
    · 总样本 n < MIN_SAMPLES(30) 的版本排除在对拍（及 σ 序列）之外，
      防止极小样本占比（如 n=2 → 100%）污染 verdict 产生假 CRITICAL
    · σ 跨全体有效 LLM 版本计算（缺该标签的版本按占比 0 计入）
    """
    llm_versions = sorted(pv for pv in dist if not is_rule_version(pv))
    counts = _version_counts(dist)
    shares = {pv: _shares(dist, pv) for pv in llm_versions}
    valid = [pv for pv in llm_versions if counts[pv] >= MIN_SAMPLES and shares[pv]]

    all_ets = sorted({et for pv in valid for et in shares[pv]})

    pairs = []
    for a, b in itertools.combinations(valid, 2):
        alerts = []
        for et in all_ets:
            pct_a = shares[a].get(et, 0.0)
            pct_b = shares[b].get(et, 0.0)
            diff_pp = abs(pct_a - pct_b)
            # σ：该 event_type 占比在全体 LLM 版本间的总体标准差（缺标签按 0）
            series = [shares[pv].get(et, 0.0) for pv in valid]
            sigma_pp = statistics.pstdev(series) if len(series) > 1 else 0.0
            sigma_mult = (diff_pp / sigma_pp) if sigma_pp > 0 else 0.0

            if diff_pp > ALERT_DIFF_PP or sigma_mult > ALERT_SIGMA:
                critical = diff_pp > CRIT_DIFF_PP or sigma_mult > CRIT_SIGMA
                alerts.append({
                    "event_type": et,
                    "pct_a": round(pct_a, 2),
                    "pct_b": round(pct_b, 2),
                    "diff_pp": round(diff_pp, 2),
                    "sigma": round(sigma_mult, 2),
                    "level": "CRITICAL" if critical else "WARN",
                })
        pairs.append({"a": a, "b": b, "alerts": alerts})
    return pairs


def compute_verdict(pairs: list) -> str:
    """有 CRITICAL → DRIFT-CRITICAL；只有 WARN → DRIFT-WARN；无 → STABLE。"""
    levels = {al["level"] for pair in pairs for al in pair["alerts"]}
    if "CRITICAL" in levels:
        return "DRIFT-CRITICAL"
    if "WARN" in levels:
        return "DRIFT-WARN"
    return "STABLE"


def run(cur: sqlite3.Cursor) -> dict:
    """算矩阵并产出 automation 消费的稳定 JSON 结构。"""
    dist = collect(cur)
    counts = _version_counts(dist)
    llm_versions = [pv for pv in sorted(dist)
                    if not is_rule_version(pv) and counts[pv] >= MIN_SAMPLES]
    rule_versions = sorted(pv for pv in dist if is_rule_version(pv))
    excluded_versions = {
        pv: {"n": counts[pv], "reason": "too_few_samples"}
        for pv in sorted(dist)
        if not is_rule_version(pv) and counts[pv] < MIN_SAMPLES
    }
    pairs = drift_matrix(dist)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "versions": llm_versions,
        "rule_versions": rule_versions,
        "excluded_versions": excluded_versions,
        "pairs": pairs,
        "verdict": compute_verdict(pairs),
    }


def write_report(payload: dict, path: Path) -> Path:
    """把 verdict 结果写成 Markdown 报告（目录不存在自动创建）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# 雪球标签口径漂移报告（P0-2）",
        "",
        "- generated_at: %s" % payload["generated_at"],
        "- LLM 版本: %s" % (", ".join(payload["versions"]) or "(无)"),
        "- 规则通道(不参与对拍): %s" % (", ".join(payload.get("rule_versions", [])) or "(无)"),
        "- verdict: **%s**" % payload["verdict"],
        "",
    ]
    excluded = payload.get("excluded_versions") or {}
    if excluded:
        lines.append("## 因样本量不足排除的版本（n < %d，不参与对拍）" % MIN_SAMPLES)
        lines.append("")
        lines.append("| prompt_version | n | reason |")
        lines.append("|---|---|---|")
        for pv, info in excluded.items():
            lines.append("| %s | %d | %s |" % (pv, info["n"], info["reason"]))
        lines.append("")
    if not payload["pairs"]:
        lines.append("无可对拍版本对（LLM 版本不足 2 个）。")
    for pair in payload["pairs"]:
        lines.append("## %s vs %s" % (pair["a"], pair["b"]))
        if not pair["alerts"]:
            lines.append("")
            lines.append("无 ALERT（分布一致）。")
            continue
        lines.append("")
        lines.append("| event_type | pct_a(%) | pct_b(%) | diff_pp | sigma | level |")
        lines.append("|---|---|---|---|---|---|")
        for al in pair["alerts"]:
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (al["event_type"], al["pct_a"], al["pct_b"],
                   al["diff_pp"], al["sigma"], al["level"])
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def default_report_path() -> Path:
    """默认报告路径：label_drift_YYYY-MM-DD.md。"""
    return DEFAULT_REPORT_DIR / ("label_drift_%s.md" % datetime.now().strftime("%Y-%m-%d"))


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="xq_status_classified 标签口径漂移探针（只读）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 路径（默认 data/xueqiu/xueqiu_info.db）")
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON（供 automation 消费）")
    parser.add_argument("--report", nargs="?", const="", default=None,
                        help="写 MD 报告（无参数值则用默认路径 label_drift_YYYY-MM-DD.md）")
    args = parser.parse_args(argv)

    conn = connect_ro(args.db)
    try:
        payload = run(conn.cursor())
    finally:
        conn.close()

    exit_code = 0
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    if args.report is not None:
        report_path = Path(args.report) if args.report else default_report_path()
        write_report(payload, report_path)
        print("report written: %s" % report_path, file=sys.stderr)
    if payload["verdict"] == "DRIFT-CRITICAL":
        exit_code = 2
    elif payload["verdict"] == "DRIFT-WARN":
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
