# -*- coding: utf-8 -*-
"""D1 置信度校准（P1，2026-11-01 WO-D1-CALIB-20261101）。

回答一个问题：D1 说 0.8 的时候，到底对不对。「准不准」不能只看命中率，要看
置信度和实际命中率是否对齐（校准），所以按置信度分五桶看：

  0-0.2 / 0.2-0.4 / 0.4-0.6 / 0.6-0.8 / 0.8-1.0（左闭右开，最后一桶含 1.0）

接口：
  · bucket_of(conf)             → 桶标签；非法/越界返回 None（不裁剪、不猜）；
  · brier_score(rows, outcomes=None) → Brier（均方误差，越低越好）；无实测样本 → None；
  · reliability(rows, outcomes=None) → 每桶 count / mean_confidence / win_rate / brier；
  · calibration_report(ledger=None, outcomes=None)
        → dict（measured/unmeasured/overall_brier/reliability/status）；
  · CLI：``python -m agent_platform.d1_calibration --json [--ledger 账本] [--outcomes sidecar]``。

数据纪律（本单红线）：
  · **只读**：d1_decisions.jsonl 与 outcome sidecar 一律不写，不生成交易单、不接实盘；
  · **缺 outcome 不硬填**：没有 outcome 的行计入 unmeasured，绝不按 0/1 猜；
  · **坏行不猜**：置信度非数字 / NaN / 越界计 invalid，不进任何桶；
  · **未测量不粉饰**：measured=0 → status="unmeasured"、overall_brier=None；
    measured 少于 MIN_MEASURED（30，与 tools/d1_calibration.py 的 CONF_FLOOR_N
    同口径）→ status="insufficient"，只报数不下结论；样本够了才 status="measured"。

与 tools/d1_calibration.py 的分工：那边是**扩席门**（Brier/ECE + n≥30 → can_expand），
这里只做**只读分桶报告**，不阻断、不改账。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from agent_platform import d1_decision as D
except ImportError:  # 直接以脚本路径运行时把同目录加入 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import d1_decision as D

SCHEMA = "d1_calibration/v1"
# (下界, 上界, 标签)：左闭右开；最后一桶含 1.0（概率上限必须能落桶）
BUCKETS = (
    (0.0, 0.2, "0-0.2"),
    (0.2, 0.4, "0.2-0.4"),
    (0.4, 0.6, "0.4-0.6"),
    (0.6, 0.8, "0.6-0.8"),
    (0.8, 1.0, "0.8-1.0"),
)
BUCKET_LABELS = tuple(b[2] for b in BUCKETS)
# outcome 取值键：本模块统一称 outcome，兼容 outcome sidecar 的 hit 字段
OUTCOME_KEYS = ("outcome", "hit")
# outcomes 映射的关联键（按顺序优先）
KEY_FIELDS = ("decision_id", "code", "symbol")
SCORE_DIGITS = 6
RATE_DIGITS = 4
MIN_MEASURED = 30
STATUS_UNMEASURED = "unmeasured"
STATUS_INSUFFICIENT = "insufficient"
STATUS_MEASURED = "measured"


# ---------------------------------------------------------------- 基础解析
def _as_prob(value):
    """置信度 → [0, 1] 内的 float；非数字 / NaN / 越界 / bool → None（不裁剪）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        prob = float(value)
    except (TypeError, ValueError):
        return None
    if prob != prob or prob < 0.0 or prob > 1.0:  # NaN / 越界
        return None
    return prob


def _as_outcome(value):
    """outcome → 0 / 1；其余（None / 非 0-1 数字 / 无法识别）→ None（不硬填）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    if num == 0.0:
        return 0
    if num == 1.0:
        return 1
    return None


def _pick_outcome(row):
    """行内取 outcome（outcome 优先，兼容 hit）；取不到有效值 → None。"""
    for key in OUTCOME_KEYS:
        if key in row:
            got = _as_outcome(row.get(key))
            if got is not None:
                return got
    return None


def _row_key(row):
    """行关联键：decision_id 优先，其次 code / symbol；都没有 → None。"""
    for field in KEY_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def _outcome_map(outcomes):
    """outcomes → {关联键: 0/1}；None 表示「只看行内 outcome」。"""
    if outcomes is None:
        return None
    if isinstance(outcomes, dict):
        return {str(k): _as_outcome(v) for k, v in outcomes.items()}
    mapping = {}
    for item in outcomes or ():
        if isinstance(item, dict):
            key = _row_key(item)
            if key is not None:
                mapping[key] = _pick_outcome(item)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            mapping[str(item[0])] = _as_outcome(item[1])
    return mapping


def _resolve_outcome(row, mapping=None):
    """行 → outcome；给了映射先查映射，查不到再退回行内字段，仍无 → None。"""
    if mapping is None:
        return _pick_outcome(row)
    for field in KEY_FIELDS:
        key = row.get(field)
        if key in (None, ""):
            continue
        got = mapping.get(str(key))
        if got is not None:
            return got
    return _pick_outcome(row)


def _rows(rows):
    """归一成可迭代列表；None → []，单行 dict → [dict]，不可迭代 → []。"""
    if rows is None:
        return []
    if isinstance(rows, dict):
        return [rows]
    try:
        return list(rows)
    except TypeError:
        return []


def _measurable(rows, outcomes=None):
    """切样本：返回 (可测 (conf, outcome) 列表, unmeasured 数, invalid 数)。"""
    mapping = _outcome_map(outcomes)
    pairs = []
    unmeasured = 0
    invalid = 0
    for row in _rows(rows):
        if not isinstance(row, dict):
            invalid += 1
            continue
        conf = _as_prob(row.get("confidence"))
        if conf is None:
            invalid += 1
            continue
        got = _resolve_outcome(row, mapping)
        if got is None:
            unmeasured += 1
            continue
        pairs.append((conf, got))
    return pairs, unmeasured, invalid


# ---------------------------------------------------------------- 指标
def bucket_of(conf):
    """置信度 → 桶标签（左闭右开，1.0 落 0.8-1.0 桶）；非法/越界 → None。"""
    prob = _as_prob(conf)
    if prob is None:
        return None
    if prob >= 1.0:
        return BUCKET_LABELS[-1]
    for low, high, label in BUCKETS:
        if low <= prob < high:
            return label
    return None


def _brier(pairs):
    if not pairs:
        return None
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), SCORE_DIGITS)


def brier_score(rows, outcomes=None):
    """Brier 分数（均方误差，越低越好）。

    rows 为 d1_decisions 行（读自 read_decisions 或直接传入）；outcomes 可为
    ``{decision_id: 0/1}`` 映射，或带 decision_id/outcome(或 hit) 的行列表。
    缺 outcome 的行跳过（计入 unmeasured，绝不硬填）；无可测样本 → None。
    """
    pairs, _, _ = _measurable(rows, outcomes)
    return _brier(pairs)


def _reliability(pairs):
    grouped = {label: [] for label in BUCKET_LABELS}
    for prob, outcome in pairs:
        label = bucket_of(prob)
        if label is None:
            continue
        grouped[label].append((prob, outcome))
    out = []
    for low, high, label in BUCKETS:
        items = grouped[label]
        count = len(items)
        out.append({
            "bucket": label,
            "lo": low,
            "hi": high,
            "count": count,
            "mean_confidence": (round(sum(p for p, _ in items) / count, RATE_DIGITS)
                                if count else None),
            "win_rate": (round(sum(y for _, y in items) / count, RATE_DIGITS)
                         if count else None),
            "brier": _brier(items),
        })
    return out


def reliability(rows, outcomes=None):
    """每桶校准表：count / mean_confidence / win_rate / brier（可测时）。

    五桶固定返回（无样本的桶 count=0、指标为 None），保证报告结构稳定；
    统计只覆盖「置信度有效且 outcome 可测」的样本。
    """
    pairs, _, _ = _measurable(rows, outcomes)
    return _reliability(pairs)


# ---------------------------------------------------------------- 报告
def calibration_report(ledger=None, outcomes=None):
    """只读生成校准报告 dict（不写账、不生成交易单）。

    ledger 缺省读生产 d1_decisions.jsonl（只读）；outcomes 缺省只看行内 outcome。
    measured=0 时 status="unmeasured" 且 overall_brier=None——不假装校准成功。
    """
    rows = D.read_decisions(ledger)
    pairs, unmeasured, invalid = _measurable(rows, outcomes)
    measured = len(pairs)
    if measured == 0:
        status = STATUS_UNMEASURED
    elif measured < MIN_MEASURED:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_MEASURED
    return {
        "schema": SCHEMA,
        "ledger": str(Path(ledger or D.DEFAULT_LEDGER)),
        "readonly": True,
        "total": len(rows),
        "measured": measured,
        "unmeasured": unmeasured,
        "invalid": invalid,
        "min_measured": MIN_MEASURED,
        "overall_brier": _brier(pairs),
        "reliability": _reliability(pairs),
        "status": status,
    }


def render_text(report):
    """人读版报告（CLI 默认输出；不写盘）。"""
    lines = ["D1 置信度校准（只读）  账本：%s" % report.get("ledger")]
    lines.append("status=%s  measured=%d  unmeasured=%d  invalid=%d  总体Brier=%s" % (
        report.get("status"), report.get("measured") or 0,
        report.get("unmeasured") or 0, report.get("invalid") or 0,
        "—" if report.get("overall_brier") is None else "%.4f" % report["overall_brier"]))
    lines.append("")
    lines.append("| 置信度桶 | n | 平均置信度 | 命中率 | Brier |")
    lines.append("|---|---:|---:|---:|---:|")
    for bucket in report.get("reliability") or []:
        lines.append("| %s | %d | %s | %s | %s |" % (
            bucket["bucket"], bucket["count"],
            "—" if bucket["mean_confidence"] is None else "%.3f" % bucket["mean_confidence"],
            "—" if bucket["win_rate"] is None else "%.1f%%" % (bucket["win_rate"] * 100),
            "—" if bucket["brier"] is None else "%.4f" % bucket["brier"]))
    if report.get("status") == STATUS_UNMEASURED:
        lines.append("")
        lines.append("**未测量**：账内没有可对齐的「置信度 + outcome」样本，本报告不做校准结论。")
    return "\n".join(lines)


def read_outcomes(path):
    """只读 outcome sidecar（JSONL）：坏行跳过，不写回。"""
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="D1 置信度校准报告（只读，不写账）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--ledger", default=None, help="d1_decisions.jsonl（只读，默认生产账）")
    parser.add_argument("--outcomes", default=None,
                        help="outcome sidecar JSONL（只读；缺省只看行内 outcome）")
    args = parser.parse_args(argv)
    outcomes = read_outcomes(args.outcomes) if args.outcomes else None
    report = calibration_report(args.ledger, outcomes)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
