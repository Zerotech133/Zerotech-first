# -*- coding: utf-8 -*-
"""断言预审器：跨条目检查 + 三级分流，为人工审核做前置过滤。

与 assertion_extract.py 的分工（不重复做同一件事）
--------------------------------------------------
抽取器管**单条**：A1-A11 硬拒（格式/枚举/覆盖率）、F1-F7 软标（语义存疑）。
预审器管**条与条之间**：重复、冲突、段落过载、时效过期。这些检查单条时看不到。

六条规则
--------
R1 重复    assertion_id 完全相同 → 合并，保留 confidence 高的，sources 相加
R2 近似    action.text 字符 Jaccard ≥ 0.8 且 condition 相似 ≥ 0.6 → 标 needs_review
R3 冲突    condition 相似 ≥ 0.85 但动作族相反（买 vs 卖） → 标 CONFLICT，**两侧都保留**
           阈值从 0.6 提到 0.85 的原因（round4 实测）：0.6 会把「在中枢上方抛出」与
           「在中枢下方接回」判成冲突，实际是配对操作（高抛低吸）。"上方/下方"字面三字重叠，
           字符 Jaccard 给到 67%，但语义是相反的两个条件，不是同一个条件。宁可漏报冲突，
           不可把配对操作当冲突——漏报只是少发现一条知识，误报会让正确知识被标成待裁决。
R4 空壳    action.text 不含任何动作动词（名词短语/状态描述） → needs_review
R5 过载    同一 unit_id 产出 > 3 条 → needs_review（疑似过度抽取）
R6 过期    kind=OBSERVATION 且 expire_at < 今天 → reject（不进图）

分级口径
--------
pass     可直接进图（无任何标记）
review   需人工看一眼（R2/R4/R5 或带 F1-F7 中的语义类标记）
conflict 需人工裁决（R3）——冲突是高价值知识，不自动消解，两侧都留
reject   不进图（R6 过期、A 类残留）

用法
----
    python assertion_precheck.py --input 95_KG/_assertions/round4.jsonl
    python assertion_precheck.py --input xxx.jsonl --out-dir 95_KG/_assertions/precheck
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

KB = Path(r"C:/项目/知识库")
DEFAULT_DIR = KB / "95_KG" / "_assertions" / "precheck"

ENTRY_VERBS = {"buy", "add_position", "open_position", "scale_in"}
EXIT_VERBS = {"sell", "reduce_position", "close_position", "scale_out"}
# 语义类标记：需要人看一眼。F1(无失效条件)/F2(无指标) 是常态不是缺陷，不计入。
# 只有 F6(DEFINITION 缺定义内容)、F7(名词冒充动作) 才是真需要人判的。
SEMANTIC_FLAGS = {"F6", "F7"}


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _jaccard(a: str, b: str) -> float:
    A, B = set(_norm(a)), set(_norm(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _cond_text(a: dict) -> str:
    c = a.get("condition")
    return ((c.get("text") or "") if isinstance(c, dict) else "").strip()


def _action_text(a: dict) -> str:
    return ((a.get("action") or {}).get("text") or "").strip()

# 动作字表：判断 action.text 是「动作」还是「名词短语/状态描述」。
# 长度不作判据——「抛出」「买入」「止损」都是 2 字，但它们是中文里最标准的动作词；
# 而「市场可能的演化路径」9 个字却完全是名词短语。判据只有一条：有没有动作动词。
ACTION_CHARS = set(
    "买卖抛接退走持等守进出加减清换调降提做看放收抓找介追抢"
    "止损盈弃避禁筛选择排控仓建补平锁移"
)
ACTION_WORDS = (
    "参与", "操作", "执行", "确认", "识别", "判定", "分类", "验证", "观察", "等待",
    "持有", "空仓", "满仓", "减仓", "加仓", "清仓", "建仓", "平仓", "补仓", "换股",
)
# 名词复合词：含动作字但不是动作（"走势"含"走"、"盘整"含"整"、"中枢"…）。
# 先剥离再判动作字，否则「第三类走势」会被"走"骗成动作。
NOUN_COMPOUNDS = (
    "走势", "趋势", "盘整", "中枢", "线段", "级别", "背驰", "风险", "路径", "需求",
    "区域", "结构", "形态", "定义", "系统", "能力", "区间", "分型", "笔", "段",
    "三类", "一类", "二类", "生命形态", "主流", "生态", "逻辑",
)


def is_shell(text: str) -> bool:
    """True = 空壳（名词短语/状态描述，不是可执行动作）。

    判据只有「有没有动作动词」，长度不参与。顺序：整词优先 → 剥离名词复合词 → 单字判定。
    """
    s = (text or "").strip()
    if not s:
        return True
    if any(w in s for w in ACTION_WORDS):
        return False
    for nc in NOUN_COMPOUNDS:
        s = s.replace(nc, "")
    return not (set(s) & ACTION_CHARS)


def _family(verb: str) -> str:
    if verb in ENTRY_VERBS:
        return "entry"
    if verb in EXIT_VERBS:
        return "exit"
    return "other"


def load(path: Path) -> list:
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] 第 {ln} 行 JSON 坏，跳过：{exc}")
    return rows


def check(rows: list, today: str) -> dict:
    marks = defaultdict(list)          # assertion_id -> [code,...]
    merged = {}                        # assertion_id -> record
    conflict_pairs = []

    # R1 合并重复 id
    for a in rows:
        aid = a.get("assertion_id") or "?"
        if aid not in merged:
            merged[aid] = dict(a, _sources=[a.get("evidence", {})])
        else:
            old = merged[aid]
            if (a.get("confidence") or 0) > (old.get("confidence") or 0):
                src = old["_sources"]
                merged[aid] = dict(a, _sources=src + [a.get("evidence", {})])
            else:
                old["_sources"].append(a.get("evidence", {}))
            marks[aid].append("R1")

    items = list(merged.values())

    # R4 空壳 / R5 过载 / R6 过期
    per_unit = Counter()
    for a in items:
        aid = a["assertion_id"]
        per_unit[(a.get("evidence") or {}).get("unit_id")] += 1
        # 空壳判据：只问「有没有动作动词」。长度完全不参与判定。
        # 两次教训：① 4 字阈值误伤「做短线」13 条 ② 3 字阈值误伤「抛出」「买入」「止损」「止盈」
        # 共 15 条。长度与「是不是动作」没有相关性。
        # 此处自判而非读抽取期 F7：F7 带 len<6 条件，会漏掉「市场可能的演化路径」这种 9 字空壳，
        # 且自判无需重跑模型（省一次 ¥0.6）。
        if is_shell(_action_text(a)):
            marks[aid].append("R4")
        flags = set(a.get("flags") or [])
        if a.get("kind") == "OBSERVATION":
            exp = (a.get("expire_at") or "")[:10]
            if exp and exp < today:
                marks[aid].append("R6")
    for a in items:
        uid = (a.get("evidence") or {}).get("unit_id")
        # 一段话里 3 条规则是正常的（缠论一段常含多个判定），>4 才算过度抽取
        if per_unit[uid] > 4:
            marks[a["assertion_id"]].append("R5")

    # R2 近似 / R3 冲突（两两比较，数据量小直接 O(n^2)）
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            same_cond = _jaccard(_cond_text(a), _cond_text(b))
            same_act = _jaccard(_action_text(a), _action_text(b))
            if same_act >= 0.8 and same_cond >= 0.6:
                marks[a["assertion_id"]].append("R2")
                marks[b["assertion_id"]].append("R2")
            fa, fb = _family((a.get("action") or {}).get("verb", "")), \
                _family((b.get("action") or {}).get("verb", ""))
            if same_cond >= 0.85 and {fa, fb} == {"entry", "exit"}:
                marks[a["assertion_id"]].append("R3")
                marks[b["assertion_id"]].append("R3")
                conflict_pairs.append((a["assertion_id"], b["assertion_id"], same_cond))

    # 分级
    buckets = {"pass": [], "review": [], "conflict": [], "reject": []}
    for a in items:
        aid = a["assertion_id"]
        codes = sorted(set(marks[aid]))
        flags = set(a.get("flags") or [])
        rec = dict(a)
        rec["precheck"] = codes

        if "R6" in codes:
            buckets["reject"].append(rec)
        elif "R3" in codes:
            buckets["conflict"].append(rec)
        elif codes or (flags & SEMANTIC_FLAGS):
            buckets["review"].append(rec)
        else:
            buckets["pass"].append(rec)

    return {"buckets": buckets, "conflict_pairs": conflict_pairs,
            "marks": {k: sorted(set(v)) for k, v in marks.items()},
            "merged_from": len(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="断言预审器（跨条目检查）")
    ap.add_argument("--input", required=True, help="抽取产物 jsonl")
    ap.add_argument("--out-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--today", default=None, help="覆盖今天日期，用于回溯测试")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"[fatal] 输入不存在: {src}")
    rows = load(src)
    today = args.today or date.today().isoformat()
    res = check(rows, today)
    b = res["buckets"]

    os.makedirs(args.out_dir, exist_ok=True)
    for name in ("pass", "review", "conflict", "reject"):
        p = Path(args.out_dir) / f"{name}.jsonl"
        with io.open(p, "w", encoding="utf-8") as f:
            for rec in b[name]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("=" * 60)
    print(f"输入           {src.name}  {len(rows)} 条")
    print(f"去重后         {len(b['pass'])+len(b['review'])+len(b['conflict'])+len(b['reject'])} 条"
          f"（R1 合并 {len(rows) - (len(b['pass'])+len(b['review'])+len(b['conflict'])+len(b['reject']))} 条）")
    print()
    print(f"  pass         {len(b['pass']):>4}  可直接进图")
    print(f"  review       {len(b['review']):>4}  需人工看一眼")
    print(f"  conflict     {len(b['conflict']):>4}  需人工裁决（两侧都留）")
    print(f"  reject       {len(b['reject']):>4}  不进图")
    print()

    codes = Counter(c for v in res["marks"].values() for c in v)
    if codes:
        print(f"标记分布  {dict(codes)}")
    if res["conflict_pairs"]:
        print(f"\n冲突对 {len(res['conflict_pairs'])} 组（相似度 ≥0.6 但动作相反）：")
        for x, y, s in res["conflict_pairs"][:5]:
            print(f"  · {x} ↔ {y}  条件相似 {s:.0%}")
    if b["review"]:
        print(f"\nreview 样例（前 3）：")
        for a in b["review"][:3]:
            print(f"  · {a['assertion_id']} {a['precheck']} "
                  f"| {_action_text(a)[:30]}")
    print("=" * 60)
    print(f"→ {args.out_dir}/{{pass,review,conflict,reject}}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
