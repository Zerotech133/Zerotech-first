# -*- coding: utf-8 -*-
"""防穿越断言（WO-LEGION-S23 · 进化层 Z5）。

Harness² 三重断言（assert_no_leak_paths / assert_no_grader_verdicts /
assert_tree_has_no_grader_verdicts）的量化语境移植：喂给回测/研究/进化
上下文的列名、路径或载荷中若出现「未来数据」「评分裁决」「验证集标记」，
一律拦截——不让评分标准与未来答案流进改进上下文。

设计原则（与 Harness² 同源）：
  1. 规则表版本化（RULES_VERSION），可审计、可回滚；
  2. fail-visible：命中即抛 LeakageDetected，绝不静默放行；
  3. 精确优先：能精确匹配的用集合（grade/verdict），避免宽正则误杀；
  4. 只读：本模块不做任何 IO 写入，不碰生产参数（params.yaml 属
     zerobound-kg 生产，接入需另单评审）。

挂接方式（下一步，需单）：回测/研究入口在构建上下文后调用
``assert_clean(columns=..., paths=..., stage="backtest")``；
本单只交付守卫本体与测试，不改任何生产链。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Iterable

RULES_VERSION = "1.0"

# 未来数据列：强前缀 + 精确集 + 后缀，三通道。精确优先避免误杀业务字段
# （判例：label_free_flag / target_price_high 是合法业务列，不得拦截）
FUTURE_COL_PREFIXES = (
    "future_",
    "fwd_",
    "forward_",
    "next_",
    "ret_fwd_",
    "ret_next_",
    "pct_chg_next_",
)
FUTURE_COL_EXACT = frozenset(
    {
        "label",
        "label_name",
        "label_y",
        "target",
        "target_y",
        "y_true",
        "y_pred",
        "future_return",
        "forward_return",
        "ret_fwd",
        "ret_next",
        "pct_chg_next",
        "close_next",
        "open_next",
        "high_next",
        "low_next",
        "vwap_next",
    }
)
FUTURE_COL_SUFFIX_RE = re.compile(r"_(t\+\d+|next|fwd|future|forward)$")

# 评分/裁决键：精确集合（防误杀：不含 score_card 之类业务字段）
GRADER_KEYS = frozenset(
    {
        "score",
        "grade",
        "verdict",
        "grader",
        "judge",
        "judge_verdict",
        "rank_final",
        "total_score",
        "final_score",
        "pass_fail",
    }
)

# 验证集 / holdout 标记：精确集合
SPLIT_KEYS = frozenset(
    {
        "valid_set",
        "test_set",
        "holdout",
        "oof",
        "oof_flag",
        "is_valid",
        "is_test",
        "in_holdout",
    }
)

# 泄露路径：评分器目录、答案文件、holdout/future 目录
LEAK_PATH_RES = (
    re.compile(r"(^|/)\.grader(/|$)"),
    re.compile(
        r"(^|/)(answers?|ground_truth|solution)s?\.(csv|json|jsonl|parquet|txt|xlsx)$"
    ),
    re.compile(r"(^|/)(future|holdout|valid_set|test_set)(/|$)"),
    re.compile(r"(^|/)labels?\.(csv|parquet)$"),
)

STAGES = ("backtest", "research", "evolve")


@dataclass
class Finding:
    """单条泄露命中。category: future_column / grader_key / split_key / leak_path"""

    category: str
    target: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"category": self.category, "target": self.target, "detail": self.detail}


class LeakageDetected(RuntimeError):
    """上下文含穿越/裁决泄露；禁止继续。"""

    def __init__(self, findings: list[Finding], stage: str):
        self.findings = findings
        self.stage = stage
        detail = "; ".join("%s:%s" % (f.category, f.target) for f in findings)
        super().__init__("lookahead leakage detected at stage=%s: %s" % (stage, detail))


def _norm(name: str) -> str:
    return str(name).strip().lower()


def scan_columns(columns: Iterable[str]) -> list[Finding]:
    out: list[Finding] = []
    for col in columns or []:
        n = _norm(col)
        if n in FUTURE_COL_EXACT:
            out.append(Finding("future_column", str(col), "future-data column (exact)"))
        elif any(n.startswith(p) for p in FUTURE_COL_PREFIXES):
            out.append(Finding("future_column", str(col), "future-data column prefix"))
        elif FUTURE_COL_SUFFIX_RE.search(n):
            out.append(Finding("future_column", str(col), "future-data column suffix"))
        elif n in GRADER_KEYS:
            out.append(Finding("grader_key", str(col), "grader/verdict key in context"))
        elif n in SPLIT_KEYS:
            out.append(Finding("split_key", str(col), "validation/holdout marker"))
    return out


def scan_paths(paths: Iterable[str]) -> list[Finding]:
    out: list[Finding] = []
    for p in paths or []:
        s = str(p).replace("\\", "/").lower()
        for rx in LEAK_PATH_RES:
            if rx.search(s):
                out.append(Finding("leak_path", str(p), "leak-prone path pattern"))
                break
    return out


def scan_payload(obj, _depth: int = 0) -> list[Finding]:
    """递归扫描 dict/list 的键名（不读值，避免误判正文内容）。"""
    out: list[Finding] = []
    if _depth > 6:
        return out
    if isinstance(obj, dict):
        out.extend(scan_columns(obj.keys()))
        for v in obj.values():
            out.extend(scan_payload(v, _depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(scan_payload(v, _depth + 1))
    return out


def scan(columns=None, paths=None, payload=None, stage="backtest") -> list[Finding]:
    """汇总裁荷；stage 仅用于错误信息与审计（不做分档差异化）。"""
    findings: list[Finding] = []
    findings.extend(scan_columns(columns or []))
    findings.extend(scan_paths(paths or []))
    if payload is not None:
        findings.extend(scan_payload(payload))
    return findings


def assert_clean(columns=None, paths=None, payload=None, stage="backtest") -> None:
    """fail-visible 断言：有命中即抛 LeakageDetected；干净则静默返回。"""
    if stage not in STAGES:
        raise ValueError("unknown stage: %s" % stage)
    findings = scan(columns=columns, paths=paths, payload=payload, stage=stage)
    if findings:
        raise LeakageDetected(findings, stage)


def _selftest() -> int:
    """CLI 自检：注入未来列/裁决键/泄露路径必须被拦，干净上下文必须放行。"""
    cases = [
        ("future col", dict(columns=["code", "close", "forward_return"]), True),
        ("t+1 suffix", dict(columns=["close_t+1"]), True),
        ("label col", dict(columns=["label"]), True),
        ("grader key", dict(payload={"verdict": "BUY", "note": "ok"}), True),
        ("split key", dict(payload={"holdout": True}), True),
        ("leak path", dict(paths=["C:/data/.grader/rules.py"]), True),
        ("answers file", dict(paths=["/tmp/answers.csv"]), True),  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
        (
            "clean",
            dict(columns=["code", "close", "volume"], paths=["/data/daily.csv"]),
            False,
        ),
        (
            "clean nested",
            dict(payload={"candidates": [{"code": "600000", "close": 10.1}]}),
            False,
        ),
    ]
    bad = 0
    for name, kwargs, expect_block in cases:
        try:
            assert_clean(**kwargs)
            blocked = False
        except LeakageDetected:
            blocked = True
        ok = blocked == expect_block
        print(
            "%-14s expect=%-5s got=%-5s %s"
            % (name, expect_block, blocked, "PASS" if ok else "FAIL")
        )
        bad += 0 if ok else 1
    print(
        "selftest:", "PASS" if bad == 0 else "%d FAIL" % bad, "| rules", RULES_VERSION
    )
    return 0 if bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="lookahead guard (Z5)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--columns", help="逗号分隔列名")
    ap.add_argument("--paths", help="逗号分隔路径")
    ap.add_argument("--stage", default="backtest", choices=STAGES)
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    cols = [c for c in (args.columns or "").split(",") if c]
    paths = [p for p in (args.paths or "").split(",") if p]
    try:
        assert_clean(columns=cols, paths=paths, stage=args.stage)
    except LeakageDetected as exc:
        print("BLOCKED:", exc)
        return 2
    print("CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
