# -*- coding: utf-8 -*-
"""术语级定义冲突检测：找"同一个概念被两套说法定义"的断言对。

为什么机械规则找不到这类冲突
--------------------------
assertion_graph 里的两条规则推断：
  · infer_contradicts —— 条件高度相似 + verb 族相反
  · infer_polarity   —— 同一动作字符串，一正一反
在北斗 + 乔木 213 条断言上实测，两者合计命中 **0**（极性版第一版命中 8 条，全是误报）。
原因很实在：跨博主的冲突不长成"同条件反向动作"，而长成
「同一个术语，两套定义」—— 字面完全不同，相似度算不出来。

实测样本（北斗 vs 乔木，都讲缠论）
    北斗：某级别走势类型中，至少三个连续次级别走势类型的重叠部分，叫该级别走势中枢
    乔木：级别由顶底分形连接构成，每地规一层级别就上升一级；级别反映行情纠缠程度而非时间长度
一个是"由走势类型递归定义级别"，一个是"由分形/笔递归定义级别"。这是**口径差异**，
不是逻辑矛盾 —— 恰恰需要模型判，规则判不了。

判据
----
1. 只取 DEFINITION 类断言（别的 kind 没有"定义"可争）；
2. 两条必须共享 ≥2 个术语词（线段/中枢/背驰/级别…），否则不是同一话题，不配；
3. 交给模型判三档：
     exclusive —— 只能一个成立，直接冲突（进 CONTRADICTS）
     partial   —— 讲的是同一概念的不同侧面/不同粒度（不建冲突边，记 complementary）
     same      —— 说的是同一件事（重复知识，去重候选）
4. 模型只判不给改，冲突不自动消解 —— 冲突是高价值知识，留人工裁定。

用法
----
    python assertion_conflict.py --in a.jsonl,b.jsonl
    python assertion_conflict.py --in a.jsonl,b.jsonl --out conflicts.jsonl --dry
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_router as R  # noqa: E402

KB = Path(r"C:/项目/知识库")
OUT_DIR = KB / "95_KG" / "_assertions"

# 缠论/技术分析的核心术语。用于筛"是否同一话题"。
TERMS = ["线段", "中枢", "背驰", "分型", "级别", "走势", "买卖点", "买点", "卖点",
         "盘整", "趋势", "笔", "区间套", "递归", "缺口", "重叠", "力度", "MACD",
         "均线", "仓位", "止损", "成交量"]

SYSTEM_PROMPT = """你是交易知识图谱的冲突审核员。给你两条**定义型**断言（都在解释某个交易概念），
判断它们之间是什么关系。

【四种关系，只能选一种】
exclusive  互斥。两条定义对同一个概念给出了**不能同时成立**的说法 —— 一个成立则另一个必错。
           例：A 说"级别由走势类型递归定义"，B 说"级别由顶底分形递归定义"，且两者都声称是
           唯一正确的分级口径 → exclusive。
partial    不同侧面/不同粒度。讲的是同一个概念，但一个讲构成、一个讲用途，或一个讲大级别
           一个讲小级别。**这是最常见的情况，不是冲突。**
same       说的是同一件事，只是措辞不同。重复知识。
unrelated  其实不是同一个概念（虽然字面有共同词）。

【判 exclusive 的门槛很高】
- 必须有明确的互相否定：一个是"必须是 X"，另一个是"不是 X 而是 Y"。
- 只是详略不同、范围不同、视角不同 → 一律 partial。
- 拿不准 → partial。**假冲突比漏冲突代价高**，假冲突要人工逐条否掉。

【输出】严格 JSON，不要 markdown，不要解释文字：
{"relation":"partial","reason":"两条都在讲级别的构成，A 从走势类型切入，B 从分形切入，不是互斥","conflict_point":""}

relation       exclusive / partial / same / unrelated
reason         一句话，30 字以内
conflict_point 只有 exclusive 才填：冲突在哪一点上（15 字以内），否则填 ""
只输出一个 JSON 对象。
"""

_REL = {"exclusive", "partial", "same", "unrelated"}


def load_jsonl(path: str) -> list:
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def _text(a: dict) -> str:
    cond = a.get("condition")
    ct = (cond.get("text") or "") if isinstance(cond, dict) else ""
    return " ".join([ct, (a.get("action") or {}).get("text") or "",
                     (a.get("rationale") or "") or "",
                     (a.get("target") or {}).get("text") or ""])


def shared_terms(a: dict, b: dict) -> list:
    ta, tb = _text(a), _text(b)
    return [t for t in TERMS if t in ta and t in tb]


def build_pairs(rows: list) -> list:
    """只配对：DEFINITION 类 + 共享 ≥2 个术语 + 不是同一条。"""
    defs = [a for a in rows if a.get("kind") == "DEFINITION"]
    pairs = []
    for i in range(len(defs)):
        for j in range(i + 1, len(defs)):
            a, b = defs[i], defs[j]
            if a.get("assertion_id") == b.get("assertion_id"):
                continue
            sh = shared_terms(a, b)
            if len(sh) < 2:
                continue
            pairs.append((a, b, sh))
    return defs, pairs


def build_user(a: dict, b: dict, shared: list) -> str:
    def side(tag, x):
        return (f"[{tag}] kind={x.get('kind')}\n"
                f"    动作/术语：{(x.get('action') or {}).get('text')}\n"
                f"    标的：{(x.get('target') or {}).get('text')}\n"
                f"    定义内容：{x.get('rationale') or '（无）'}\n"
                f"    出处：{(x.get('evidence') or {}).get('unit_id', '')[:48]}")
    return ("请判断这两条定义的关系。\n\n" + side("A", a) + "\n\n" + side("B", b)
            + f"\n\n二者共同涉及的术语：{'、'.join(shared)}")


def _parse(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    dec = json.JSONDecoder()
    i = 0
    while True:
        j = t.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(t, j)
        except ValueError:
            i = j + 1
            continue
        if isinstance(obj, dict) and "relation" in obj:
            rel = str(obj.get("relation") or "").strip().lower()
            return {"relation": rel if rel in _REL else "partial",
                    "reason": (obj.get("reason") or "").strip()[:200],
                    "conflict_point": (obj.get("conflict_point") or "").strip()[:60]}
        i = end
    return {}


def judge(a: dict, b: dict, shared: list) -> dict:
    out = {"a": a.get("assertion_id"), "b": b.get("assertion_id"),
           "shared": shared, "relation": None, "reason": "", "conflict_point": "",
           "cost": 0.0, "error": None}
    t0 = time.monotonic()
    for _ in range(2):
        try:
            r = R.generate(build_user(a, b, shared), system=SYSTEM_PROMPT,
                           temperature=0.0, max_tokens=400, purpose="assertion_conflict")
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"llm_exception:{exc}"
            break
        out["cost"] += r.get("cost_cny") or 0.0
        if not r.get("ok"):
            out["error"] = "llm_failed"
            continue
        p = _parse(r.get("text") or "")
        if p:
            out.update(p)
            out["error"] = None
            break
        out["error"] = "parse_failed"
    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="术语级定义冲突检测")
    ap.add_argument("--in", dest="inp", required=True, help="逗号分隔多个 jsonl")
    ap.add_argument("--out", default=str(OUT_DIR / "conflicts.jsonl"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    rows = []
    for p in [x.strip() for x in args.inp.split(",") if x.strip()]:
        rows.extend(load_jsonl(p))
    defs, pairs = build_pairs(rows)
    print(f"[in] 断言 {len(rows)} 条，其中 DEFINITION {len(defs)} 条")
    print(f"[pair] 共享 ≥2 术语的定义对 {len(pairs)} 对")
    if args.dry:
        for a, b, sh in pairs:
            print(f"  · [{a['assertion_id']}] {a['action']['text'][:18]}"
                  f" ↔ [{b['assertion_id']}] {b['action']['text'][:18]}  共享={sh}")
        print("[dry] 未调模型")
        return 0
    if not pairs:
        print("[done] 没有需要判断的定义对")
        return 0

    t0 = time.monotonic()
    res = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(judge, a, b, sh) for a, b, sh in pairs]
        for fu in as_completed(futs):
            res.append(fu.result())

    # 回填两侧内容，便于人读
    idx = {r.get("assertion_id"): r for r in rows}
    for r in res:
        for side in ("a", "b"):
            x = idx.get(r[side]) or {}
            r[side + "_text"] = (x.get("action") or {}).get("text")
            r[side + "_def"] = (x.get("rationale") or "")[:160]
            uid = (x.get("evidence") or {}).get("unit_id") or ""
            m = re.match(r"^learn_(.+)_[0-9a-f]{10}", uid)
            r[side + "_src"] = m.group(1) if m else ""

    from collections import Counter
    Counter(r["relation"] for r in res)
    cost = sum(r["cost"] for r in res)
    print()
    print("=" * 64)
    print(f"判定 {len(res)} 对　{Counter(r['relation'] for r in res)}")
    print(f"成本 ¥{cost:.4f}　耗时 {time.monotonic()-t0:.1f}s")
    for rel in ("exclusive", "same", "unrelated", "partial"):
        xs = [r for r in res if r["relation"] == rel]
        if not xs or rel == "partial":
            continue
        print(f"\n--- {rel}（{len(xs)} 对）")
        for r in xs[:10]:
            print(f"  · [{r['a_src']}] {r['a_text']}")
            print(f"    [{r['b_src']}] {r['b_text']}")
            print(f"    判定：{r['conflict_point'] or ''} {r['reason']}")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8") as f:
        for r in res:
            r["judged_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
