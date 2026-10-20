"""WO-QT-20261020-044：可选共振字段覆盖统计（R0 计划 B 项）。

纯内存只读统计：对调用方提供的信号池原始行（dict 列表）逐字段分列
True/False/None(不可判)/缺键/异常取值，coverage 仅在样本 > 0 时计算，
零样本不造覆盖率。只读统计，不改评分/融合/生产输入，不触文件/PG/网络；
统计结果不构成 READY、单日冻结或任何生产放行证据（返回值内嵌
scope 字段随数据走）。

字段表注意：_RESONANCE_FIELDS 为 chan_monitor 字段表的**人工镜像**，
测试侧有同步守卫；chan_monitor 字段表变更时必须同步本文件，
否则会静默漏统（结构性失真且无告警）。
"""
from __future__ import annotations

_RESONANCE_FIELDS = ("mbei", "bei", "bei2", "vv")
_SCOPE = "STATISTICS_ONLY_NOT_EVIDENCE"


def _rejected(reason: str) -> dict:
    return {"status": "REJECTED", "scope": _SCOPE, "reasons": [reason],
            "zero_sample": None, "total_rows": None, "fields": None,
            "anomalies": None}


def coverage_report(rows) -> dict:
    """统计四个可选共振字段的覆盖结构。

    rows：调用方提供的原始行 dict 列表（如信号池 jsonl 逐行 json.loads 结果）。
    返回逐字段 present_true/present_false/present_null/present_missing/
    present_other 计数与 coverage=(true+false)/total；异常取值逐条留痕。
    """
    if type(rows) is not list:
        return _rejected("输入必须是行 dict 列表")
    for index, row in enumerate(rows):
        if type(row) is not dict:
            return _rejected(f"第 {index} 行不是 dict，拒绝静默跳过")

    total = len(rows)
    anomalies: list[dict] = []
    fields: dict[str, dict] = {}
    for field in _RESONANCE_FIELDS:
        counts = {"present_true": 0, "present_false": 0, "present_null": 0,
                  "present_missing": 0, "present_other": 0}
        for index, row in enumerate(rows):
            if field not in row:
                counts["present_missing"] += 1
                continue
            value = row[field]
            if value is True:
                counts["present_true"] += 1
            elif value is False:
                counts["present_false"] += 1
            elif value is None:
                counts["present_null"] += 1
            else:
                counts["present_other"] += 1
                anomalies.append({
                    "row_index": index,
                    "symbol": row.get("code") if type(row.get("code")) is str else None,
                    "field": field,
                    "value_type": type(value).__name__,
                })
        observable = counts["present_true"] + counts["present_false"]
        fields[field] = {
            **counts,
            "total": total,
            # coverage 只衡量可观测（True/False 有明确取值）的占比；
            # None（不可判）与缺键按评分契约贡献为 0，属信息缺口不入分子。
            "coverage": observable / total if total > 0 else None,
        }

    return {
        "status": "OK_WITH_ANOMALIES" if anomalies else "OK",
        "scope": _SCOPE,
        "reasons": [],
        "zero_sample": total == 0,
        "total_rows": total,
        "fields": fields,
        "anomalies": anomalies,
    }
