#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨源断言冲突检测（模型级）。

为什么必须用模型
================
``assertion_graph.infer_contradicts`` / ``infer_polarity`` 都是**字面**判据：
条件 Jaccard ≥0.85、动作去否定词后严格相等。这套在单源内部够用，跨范式立刻失效。

2026-11-06 实测（缠论派 213 条 vs 量化派 55 条）：真正的分歧长这样——

  · 缠论：跌破前低就止损，结构没坏就拿着
  · 量化：每笔单子进场前先定最大亏损，到点机械砍
  · 量化：每个月最后一天全部卖掉，下个月第一个交易日满仓买入 15 只

这三条字面相似度接近 0（"跌破前低" vs "进场前定最大亏损"），但它们对
「持股周期由什么决定」给出**互斥**答案：一个由结构决定，一个由日历决定。
字符串规则永远抓不到这种，只有读懂了才能判。

降维：术语倒排 → 只判候选对
==========================
全配对是 N×M（213×55 = 11,715），模型判不起也没必要。真实冲突必然发生在
**同一议题**上，于是先用核心术语建倒排索引，只保留「跨源 + 共享 ≥2 术语」的对。
这一步几乎不损召回：两条断言如果连一个共同术语都没有，它们根本不在谈同一件事。

三类判定
========
  contradicts —— 同一议题，结论互斥，不能同时执行
  compatible  —— 同一议题，可以共存（粒度差异、适用场景不同、一个更具体）
  unrelated   —— 误配（术语撞了但话题不同）

只有 contradicts 进图。compatible 也留档，因为它能反过来证明「这些源确实在对话」。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_router as R  # noqa: E402

KB = Path(r"C:/项目/知识库")
OUT_DIR = KB / "95_KG" / "_assertions"


# ---------------------------------------------------------------- 术语表
# 只收「能界定议题」的词。泛化词（股票/市场/交易）一律不要，它们会让每一对都像在谈同一件事。
TERMS = (
    # 缠论结构
    "背驰", "中枢", "线段", "分型", "级别", "走势类型", "盘整", "趋势",
    "第一类买点", "第二类买点", "第三类买点", "一买", "二买", "三买",
    "买卖点", "区间套", "递归", "笔", "缺口", "转折",
    # 量化方法
    "因子", "回测", "选股", "调仓", "持仓周期", "换手", "动量", "均值回归",
    "多因子", "单因子", "IC", "分层", "中性化", "容量", "超额收益", "基准",
    "北交所", "涨停", "涨跌停", "股息", "估值", "市盈率", "财报", "负债",
    # 风控 / 仓位
    "止损", "止盈", "仓位", "满仓", "空仓", "半仓", "加仓", "减仓", "清仓",
    "补仓", "杠杆", "回撤", "最大回撤", "风控", "盈亏比", "胜率", "扛单",
    "爆仓", "分散", "集中", "单笔风险",
    # 技术指标
    "均线", "MACD", "KDJ", "金叉", "死叉", "零轴", "量价", "背离",
    "放量", "缩量", "成交量", "换手率",
    # 交易行为
    "追涨", "抄底", "左侧", "右侧", "做T", "波段", "短线", "长线",
    "计划", "纪律", "情绪", "执行",
)

_SRC_LABEL = re.compile(r"[（(].*?[)）]")

# 议题表：跨范式配对用。
# 术语倒排对**同源**有效（缠论对缠论，共享"中枢/盘整/背驰"），跨范式直接归零——
# 2026-11-06 实测：缠论 213 条 vs 量化 47 条，共享术语（无"止损"字样不算）的跨范式对 **0 对**。
# 因为两派根本不用同一套词：缠论说"背驰"，量化说"因子"。
# 但它们谈的是同一批议题（止损离场、持仓周期、仓位、选股方法……），
# 所以跨范式配对必须按**议题**归并，不能按术语。
ISSUE = {
    "止损离场": r"止损|止盈|最大亏|认亏|离场|走人|割肉|砍|错了|认赔|亏损|被套|套牢",
    "持仓周期": r"持有|持股|持仓|调仓|换股|周期|拿着|长线|短线|波段|隔夜",
    "仓位管理": r"仓位|满仓|半仓|空仓|重仓|轻仓|加仓|减仓|分批|建仓|清仓|补仓|杠杆",
    "选股方法": r"选股|因子|指标|筛选|排名|评分|条件|量化|回测|模型|策略",
    "买卖点":   r"买点|卖点|入场|进场|出场|买入|卖出|抛出|接回|追",
    "趋势判断": r"趋势|走势|方向|级别|中枢|线段|盘整|背驰|背离|转折|分型",
    "纪律执行": r"纪律|执行|规则|计划|情绪|机械|系统|人性|心态",
    "风险管理": r"风险|回撤|爆仓|分散|集中|活着|生存|概率",
}
_ISSUE_RE = {k: re.compile(v) for k, v in ISSUE.items()}


def issues_of(a: dict) -> set:
    blob = " ".join([
        ((a.get("condition") or {}).get("text") or ""),
        ((a.get("action") or {}).get("text") or ""),
        ((a.get("target") or {}).get("text") or ""),
        (a.get("rationale") or ""),
        (a.get("invalidator") or ""),
    ])
    return {k for k, rx in _ISSUE_RE.items() if rx.search(blob)}


def norm_src(p: str) -> str:
    """把 project 名压成短标签，用于跨源判断与展示。"""
    s = Path(p).stem
    s = _SRC_LABEL.sub("", s)
    return s.strip() or s


def load_rows(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        f = Path(p)
        if not f.exists():
            raise SystemExit(f"[fatal] 输入不存在: {f}")
        src = norm_src(f.parent.name if f.name == "verified.jsonl" else
                       (f.stem.split("_")[0] if "_" in f.stem else f.stem))
        with io.open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                a = json.loads(line)
                a.setdefault("_src_file", f.name)
                # verified.jsonl 里没有源名，靠上层目录名带出来（_verify_xbx → 需外部映射）
                a["_src"] = a.get("_src") or a.get("project") or src
                rows.append(a)
    return rows


def merge_with_project(rows: list[dict], mapping: dict) -> None:
    """按 _src_file 的目录标记回填真实 project 名。"""
    for a in rows:
        tag = a.get("_src", "")
        a["_src"] = mapping.get(tag, mapping.get(a.get("_src_file", ""), tag))


def terms_of(a: dict) -> set:
    blob = " ".join([
        ((a.get("condition") or {}).get("text") or ""),
        ((a.get("action") or {}).get("text") or ""),
        ((a.get("target") or {}).get("text") or ""),
        (a.get("invalidator") or ""),
        (a.get("rationale") or ""),
    ])
    return {t for t in TERMS if t in blob}


def build_pairs(rows: list[dict], min_shared: int) -> list[tuple]:
    idx: dict[str, list[int]] = {}
    for i, a in enumerate(rows):
        for t in terms_of(a):
            idx.setdefault(t, []).append(i)

    cand: dict[tuple, set] = {}
    for t, ids in idx.items():
        if len(ids) < 2:
            continue
        for i, j in combinations(sorted(ids), 2):
            if rows[i]["_src"] == rows[j]["_src"]:
                continue  # 只要跨源
            cand.setdefault((i, j), set()).add(t)

    out = []
    for (i, j), shared in cand.items():
        if len(shared) < min_shared:
            continue
        out.append((i, j, sorted(shared)))
    # 共享术语越多越可能是同一议题，排前面
    out.sort(key=lambda x: (-len(x[2]), x[0], x[1]))
    return out


def build_pairs_issue(rows: list[dict], max_per_issue: int) -> list[tuple]:
    """议题内跨源配对。返回 [(i, j, tags)]，tags 是议题名列表。"""
    buckets: dict[str, list[int]] = {}
    for i, a in enumerate(rows):
        for t in issues_of(a):
            buckets.setdefault(t, []).append(i)

    out = []
    seen = set()
    for topic, ids in buckets.items():
        n = 0
        for ii, i in enumerate(ids):
            for j in ids[ii + 1:]:
                if rows[i]["_src"] == rows[j]["_src"]:
                    continue
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)
                out.append((key[0], key[1], [topic]))
                n += 1
                if n >= max_per_issue:
                    break
            if n >= max_per_issue:
                break
    out.sort(key=lambda x: x[0])
    return out


VERB_FAM = {
    "entry": "entry", "buy": "entry", "open_position": "entry", "add_position": "entry",
    "scale_in": "entry",
    "exit": "exit", "sell": "exit", "close_position": "exit", "stop_loss": "exit",
    "reduce_position": "exit", "scale_out": "exit", "take_profit": "exit",
    "hold": "hold", "wait": "hold",
    "avoid": "forbid", "prohibit": "forbid",
    "size_position": "size", "identify": "judge", "classify": "judge",
    "validate": "judge", "confirm": "judge",
}


def build_pairs_risk(rows: list[dict], topics: list[str], cap: int) -> list[tuple]:
    """风控议题内的跨源两两配对。

    为什么按「两两配 + 单对判」而不是「议题列表一起判」
    --------------------------------------------------
    ``--mode topic`` 把一侧 16 条塞给 14B 让它自己找配对，实测 9 条"冲突"里 7 条误报，
    且模型在 why 里把 B 条记成了另一条（编号串了）。同样的模型、同样的 prompt，
    **一次只判一对**时准确率是 7/8 = 88%（见 ``assertion_conflict_fixture.py``）。

    结论：14B 能做判断，不能做检索。检索交给规则（议题 + verb 族），判断交给模型。

    排序（先判可能性高的）
    --------------------
    1. verb 族相反（一个进一个出）—— 最可能对撞
    2. verb 族不同（一个判一个做）
    3. 同族，但有条件

    **每条断言最多出场 2 次**。不加这个限制时，一条"17.62 没跌破就持有"会跟异源侧
    所有止损规则各配一次，24 对里 6 对都是它——真正有信息量的对撞被稀释了。
    """
    out = []
    for topic in topics:
        a_side = [r for r in rows if topic in issues_of(r)]
        for i in range(len(a_side)):
            for j in range(i + 1, len(a_side)):
                x, y = a_side[i], a_side[j]
                if x["_src"] == y["_src"]:
                    continue
                fx = VERB_FAM.get(x["action"].get("verb"), "other")
                fy = VERB_FAM.get(y["action"].get("verb"), "other")
                opp = {fx, fy} in ({"entry", "exit"}, {"entry", "forbid"}, {"exit", "hold"},
                                   {"hold", "exit"}, {"forbid", "entry"})
                has_c = bool((x.get("condition") or {}).get("text")) and \
                        bool((y.get("condition") or {}).get("text"))
                rank = 0 if opp else (1 if fx != fy else 2)
                out.append((rank, topic, x, y, has_c))
    out.sort(key=lambda t: (t[0], not t[4]))
    picked = []
    used: dict[str, int] = {}
    for rank, topic, x, y, _ in out:
        kx, ky = x.get("assertion_id"), y.get("assertion_id")
        if used.get(kx, 0) >= 2 or used.get(ky, 0) >= 2:
            continue
        used[kx] = used.get(kx, 0) + 1
        used[ky] = used.get(ky, 0) + 1
        picked.append((topic, x, y))
        if len(picked) >= cap:
            break
    return picked


TOPIC_SYSTEM = """你是交易知识库的冲突裁定员。下面给你一个**议题**下两组来自不同博主的知识断言，A 组是缠论/结构派，B 组是量化/风控/短线派。

任务：找出 A 组和 B 组之间**互斥**的组合。

【互斥的唯一检验方法】
问自己：**有个人同时信这两条，在某个具体行情里，他会不会做不到 / 自相矛盾？**

必须能写出这样一句话才算冲突：
「当〔具体行情〕发生时，A 要求〔做什么〕，B 要求〔做什么〕，这两件事同一次操作里做不出来。」

写不出这句话的，一律不是冲突。

【不算冲突，绝对不要报 —— 第一版把下面这些都报成冲突了】
1. **判断依据不同**：A 用结构（背驰/中枢）判断，B 用 MACD/均线判断。
   → 两套工具可以同时用，一个确认另一个。不报。
2. **指标不同**：A 看三类买卖点，B 看金叉。两个不同体系的技术信号，可以互相印证。
   → 不报。
3. **体系不同**：A 讲缠论结构，B 讲因子选股。根本不在一个环节。
   → 不报。
4. **粒度不同 / 场景不同 / 一个更具体**：不报。
5. **一个更严一个更松**：不算冲突，松的只是覆盖更少情况。
6. **用词不同意思一样**：不报。

【真冲突只有四种形态 —— 请只在这里找】
1. **同一动作，一个要求做，一个要求不做或明确反对。**
   例：A「跌破这个低点才走，没跌破就拿着」 vs B「亏损到 2% 就机械砍，不看结构」。
   同一笔浮亏，一个让留一个让走 → 冲突。
2. **同一决策，依据互斥到同一时刻只能选一个。**
   例：A「持股周期由背驰/结构决定」 vs B「每个月最后一天清仓，下个月第一个交易日重新满仓买入」。
   同一天，一个说看结构、一个说日历到了必须换 → 冲突。
3. **同一现象，给出相反的解释或相反的操作含义。**
   例：A「背驰 = 要转折了，该走」 vs B「背驰只是速度放慢，方向没变，不该走」。
4. **同一指标/方法，一个说有效必用，一个说无效/有害。**

【数量】
大多数议题下**应该只报 0-1 条**。有一个真冲突就报一个。宁可漏报，不要凑数。
两侧主要在讲不同环节、只是工具不同 —— 报空数组，这是完全正常的答案，不会被扣分。
报错了要人工逐条核查，代价远高于漏报。

【最后一道自检 —— 每条都要过】
报之前再问一遍：**一个交易者同时遵守 A 和 B，会不会真的做不到？**
- 「A 看结构离场，B 看均线离场」→ 两个信号可以同时看，先到先走。**能做到，不是冲突。**
- 「A 说亏到结构位走（可能 -15%），B 说亏到 8% 无条件走」→ 亏 8% 而结构位还在 -15% 时，
  B 要求立刻走，A 要求继续拿。**做不到，是真冲突。**
差别在于：前者是两个**可叠加的信号**，后者是同一时刻**两个相反的指令**。

严格输出 JSON，不要 markdown 代码块，不要解释文字：
{"conflicts":[{"a":3,"b":7,"why":"当持仓浮亏 3% 而结构位还没破时，A 要求继续持有等结构破坏，B 要求到点即砍，同一次操作做不到两件事"}],"note":"该议题下两侧主要在讲不同环节"}

字段：
  a     A 组的编号（整数）
  b     B 组的编号（整数）
  why   **必须**是「当…发生时，A 要求…，B 要求…，做不到两件事」这个句式，40-70 字。
        写成「两者判断标准不同」这种话的，视为无效，不要输出。
  note  10-30 字，说明这个议题整体上两边是不是在谈同一件事"""


def _brief(a: dict) -> str:
    c = (a.get("condition") or {}).get("text") or ""
    parts = []
    if c:
        parts.append(f"条件「{c[:40]}」")
    parts.append(f"动作「{(a['action'].get('text') or '')[:44]}」")
    r = (a.get("rationale") or "").strip()
    if r:
        parts.append(f"理由「{r[:50]}」")
    return "，".join(parts)


def _pick(rows: list[dict], cap: int) -> list[dict]:
    """挑代表：有条件、tone 明确、动作不空壳的优先。"""
    def score(a):
        s = 0
        if (a.get("condition") or {}).get("text"):
            s += 2
        if a.get("kind") == "RULE":
            s += 1
        if (a.get("rationale") or "").strip():
            s += 1
        return -s
    return sorted(rows, key=score)[:cap]


def judge_topic(topic: str, side_a: list[dict], side_b: list[dict]) -> tuple[list, dict]:
    la = "\n".join(f"  A{n}（{a['_src']}）{_brief(a)}" for n, a in enumerate(side_a, 1))
    lb = "\n".join(f"  B{n}（{b['_src']}）{_brief(b)}" for n, b in enumerate(side_b, 1))
    user = f"议题：{topic}\n\nA 组（缠论/结构派）\n{la}\n\nB 组（量化/风控/短线派）\n{lb}"
    stat = {"cost": 0.0, "error": None}
    try:
        r = R.generate(user, system=TOPIC_SYSTEM, temperature=0.0,
                       max_tokens=900, purpose="assertion_conflict_topic")
        stat["cost"] = r.get("cost_cny") or 0.0
        if not r.get("ok"):
            stat["error"] = "llm_failed"
            return [], stat
        t = (r.get("text") or "").strip()
        if t.startswith("```"):
            t = re.sub(r"^```[A-Za-z]*\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            stat["error"] = "no_json"
            return [], stat
        payload = json.loads(t[i:j + 1])
        out = []
        for it in payload.get("conflicts") or []:
            try:
                ai, bi = int(it["a"]), int(it["b"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (1 <= ai <= len(side_a) and 1 <= bi <= len(side_b)):
                continue
            out.append((side_a[ai - 1], side_b[bi - 1],
                        (it.get("why") or "").strip()[:200]))
        stat["note"] = (payload.get("note") or "").strip()[:80]
        return out, stat
    except Exception as exc:  # noqa: BLE001
        stat["error"] = f"exc:{exc}"
        return [], stat


SYSTEM_PROMPT = """你是交易知识库的冲突裁定员。给你两条来自**不同博主**的交易知识断言，判断它们是否互斥。

只判断一件事：**这两条能不能同时执行**。

【判 contradicts 的情形】同一议题上给出相反的结论
- 一个说必须做、一个说不要做（止损、满仓、追涨、扛单……）
- 对同一个量给出相反的方向解释（背驰意味着转折 / 背驰只是速度减慢）
- 持股周期由什么决定，一个说结构、一个说日历，这是互斥的：同一时刻不可能既按结构走又等下个月第一个交易日
- 一个说"选股看 A 指标"，一个说"A 指标没用"

【判 compatible 的情形】大部分情况都属于这里
- 粒度不同：一个说宏观原则，一个说具体步骤
- 场景不同：一个讲买点，一个讲仓位（不同维度，不冲突）
- 一个更具体：粗规则 + 细化的执行细则
- 说法不同意思相同：同义词、口语 vs 术语

【判 unrelated 的情形】
- 术语碰巧相同但谈的不是一回事（都提到"级别"，一个是缠论级别，一个是仓位级别）
- 一条讲选股、一条讲卖出，两个环节，没有对撞

【极重要】
- 拿不准就判 compatible。误报一个冲突，就要多花人力去核一条假知识。
- 不要因为"语气不同""用词不同"就判冲突。
- 不要因为"一条更激进"就判冲突，激进程度不是互斥。

严格输出 JSON，不要 markdown 代码块，不要任何解释文字：
{"pairs":[{"idx":1,"verdict":"contradicts","topic":"持股周期由什么决定","why":"A 按结构决定，B 按日历决定，同一时刻只能选一个"}]}

字段：
  idx      配对编号，从 1 开始，对应输入里的 [1] [2]
  verdict  contradicts / compatible / unrelated
  topic    8-20 字，说明这两条在争什么
  why      20-50 字，说清为什么互斥或为什么不互斥
每一对都必须给 verdict，数量要一致。"""


def build_user(items: list[tuple]) -> str:
    parts = []
    for n, (a, b, shared) in enumerate(items, 1):
        ca = (a.get("condition") or {}).get("text") or "（无）"
        cb = (b.get("condition") or {}).get("text") or "（无）"
        parts.append(
            f"[{n}] 关联（共同术语或共同议题）：{'、'.join(shared)}\n"
            f"  A（{a['_src']}）\n"
            f"    条件：{ca}\n"
            f"    动作：{a['action'].get('text','')}\n"
            f"    理由：{(a.get('rationale') or '')[:120]}\n"
            f"  B（{b['_src']}）\n"
            f"    条件：{cb}\n"
            f"    动作：{b['action'].get('text','')}\n"
            f"    理由：{(b.get('rationale') or '')[:120]}"
        )
    return "\n\n".join(parts)


def parse_pairs(text: str, n: int) -> tuple[dict, str | None]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}, "no_json_object"
    try:
        payload = json.loads(t[i:j + 1])
    except Exception as exc:  # noqa: BLE001
        return {}, f"json_error:{exc}"
    items = payload.get("pairs")
    if not isinstance(items, list):
        return {}, "pairs_not_list"
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("idx"))
        except (TypeError, ValueError):
            continue
        v = (it.get("verdict") or "").strip().lower()
        if v not in ("contradicts", "compatible", "unrelated"):
            continue
        out[idx] = {
            "verdict": v,
            "topic": (it.get("topic") or "").strip()[:40],
            "why": (it.get("why") or "").strip()[:160],
        }
    missing = set(range(1, n + 1)) - set(out)
    return out, (f"missing_idx:{sorted(missing)}" if missing else None)


def judge(batch: list[tuple], retries: int = 2) -> tuple[dict, dict]:
    user = build_user(batch)
    stat = {"cost": 0.0, "error": None, "tier": None}
    for attempt in range(retries + 1):
        try:
            r = R.generate(user, system=SYSTEM_PROMPT, temperature=0.0,
                           max_tokens=400 + 160 * len(batch), purpose="assertion_conflict")
            stat["cost"] += r.get("cost_cny") or 0.0
            stat["tier"] = r.get("tier")
            if not r.get("ok"):
                stat["error"] = "llm_failed"
                continue
            res, err = parse_pairs(r.get("text") or "", len(batch))
            if res and not err:
                return res, stat
            stat["error"] = err or "empty"
        except Exception as exc:  # noqa: BLE001
            stat["error"] = f"exc:{exc}"
        time.sleep(1.0 + attempt)
    return {}, stat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跨源断言冲突检测（模型级）")
    ap.add_argument("--in", dest="inp", required=True,
                    help="逗号分隔的 jsonl；每个文件一条断言行")
    ap.add_argument("--src-map", default="",
                    help='可选，"文件名前缀=源名" 逗号分隔，用于回填 _src')
    ap.add_argument("--min-shared", type=int, default=2, help="term 模式：最少共享术语数，默认 2")
    ap.add_argument("--mode", default="term",
                    choices=("term", "issue", "topic", "pair", "both"),
                    help="term=术语倒排（同源）；issue=议题两两配对；topic=议题列表一起判"
                         "（14B 会串号，别用）；pair=议题两两配+单对判（推荐）；both=term+issue")
    ap.add_argument("--pair-cap", type=int, default=80,
                    help="pair 模式：总共最多判多少对（已按对撞可能性排序），默认 80")
    ap.add_argument("--group-a", default="北斗,乔木", help="topic 模式：A 组源名，逗号分隔")
    ap.add_argument("--group-b", default="", help="topic 模式：B 组源名，逗号分隔；空=除 A 组外全部")
    ap.add_argument("--cap", type=int, default=18, help="topic 模式：每侧最多送多少条断言，默认 18")
    ap.add_argument("--topics", default="",
                    help="topic 模式：只跑这些议题（逗号分隔）。空=全部。"
                         "建议只跑风控类（止损离场/持仓周期/仓位管理/风险管理）——"
                         "技术判断类（买卖点/趋势判断/选股方法）各派工具不同但可叠加，硬配必误报。")
    ap.add_argument("--max-per-issue", type=int, default=60,
                    help="issue 模式：每个议题最多取多少对，默认 60")
    ap.add_argument("--batch", type=int, default=6, help="每次调模型判几对，默认 6")
    ap.add_argument("--limit", type=int, default=0, help="最多判多少对，0=全部")
    ap.add_argument("--out", default=str(OUT_DIR / "cross_conflicts.jsonl"))
    ap.add_argument("--dry", action="store_true", help="只列候选对，不调模型")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args(argv)

    rows: list[dict] = []
    for p in args.inp.split(","):
        p = p.strip()
        if not p:
            continue
        f = Path(p)
        with io.open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    a = json.loads(line)
                    a["_src"] = a.get("_src") or a.get("project") or f.stem
                    rows.append(a)
    if args.src_map:
        mp = {}
        for kv in args.src_map.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                mp[k.strip()] = v.strip()
        for a in rows:
            a["_src"] = mp.get(a["_src"], a["_src"])

    # 去掉 verify 判死的
    rows = [a for a in rows
            if (a.get("verify") or {}).get("verdict") in (None, "ok", "fix")]
    srcs = sorted({a["_src"] for a in rows})
    print(f"[data] 断言 {len(rows)} 条，源 {len(srcs)} 个：{srcs}")

    # ---------------- pair 模式：议题内两两配 + 单对判（推荐）
    if args.mode == "pair":
        topics = ([t.strip() for t in args.topics.split(",") if t.strip()]
                  if args.topics else list(ISSUE))
        cand = build_pairs_risk(rows, topics, args.pair_cap)
        print(f"[pair] 议题 {topics} 候选 {len(cand)} 对（已排序，单对判）")
        from concurrent.futures import ThreadPoolExecutor

        def one(item):
            topic, x, y = item
            hits, st = judge_topic(topic, [x], [y])
            return topic, x, y, hits, st

        results = []
        total = 0.0
        hit_n = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for k, (topic, x, y, hits, st) in enumerate(ex.map(one, cand), 1):
                total += st["cost"]
                if hits:
                    hit_n += 1
                    results.append({
                        "topic": topic, "verdict": "contradicts", "why": hits[0][2],
                        "shared": [topic],
                        "a_id": x.get("assertion_id"), "a_src": x["_src"],
                        "a_cond": ((x.get("condition") or {}).get("text") or ""),
                        "a_action": x["action"].get("text"), "a_kind": x.get("kind"),
                        "b_id": y.get("assertion_id"), "b_src": y["_src"],
                        "b_cond": ((y.get("condition") or {}).get("text") or ""),
                        "b_action": y["action"].get("text"), "b_kind": y.get("kind"),
                    })
                if k % 20 == 0 or k == len(cand):
                    print(f"  [{k}/{len(cand)}] 冲突 {hit_n}　累计 ¥{total:.3f}")
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with io.open(outp, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print()
        print("=" * 62)
        print(f"判 {len(cand)} 对，报冲突 {len(results)} 对（{len(results)/max(len(cand),1)*100:.1f}%）"
              f"　成本 ¥{total:.4f}")
        print(f"→ {outp}")
        for r in results:
            print(f"  · [{r['topic']}] {r['why'][:110]}")
            print(f"      A（{r['a_src']}）{r['a_cond'][:26]} → {r['a_action'][:38]}")
            print(f"      B（{r['b_src']}）{r['b_cond'][:26]} → {r['b_action'][:38]}")
        return 0

    # ---------------- topic 模式：议题级对撞
    if args.mode == "topic":
        ga = {s.strip() for s in args.group_a.split(",") if s.strip()}
        gb = ({s.strip() for s in args.group_b.split(",") if s.strip()}
              if args.group_b else set(srcs) - ga)
        print(f"[group] A={sorted(ga)}  B={sorted(gb)}  cap={args.cap}")
        topics = ([t.strip() for t in args.topics.split(",") if t.strip()]
                  if args.topics else list(ISSUE))
        results = []
        total = 0.0
        for topic in topics:
            sa = _pick([a for a in rows if a["_src"] in ga and topic in issues_of(a)], args.cap)
            sb = _pick([a for a in rows if a["_src"] in gb and topic in issues_of(a)], args.cap)
            if len(sa) < 1 or len(sb) < 1:
                print(f"  {topic:<6} A{len(sa)}/B{len(sb)}  跳过（一侧为空）")
                continue
            hits, st = judge_topic(topic, sa, sb)
            total += st["cost"]
            print(f"  {topic:<6} A{len(sa)}/B{len(sb)}  → 冲突 {len(hits)} 条"
                  f"　{st.get('note','')}{'　[ERR]' if st['error'] else ''}")
            for a, b, why in hits:
                results.append({
                    "topic": topic, "verdict": "contradicts", "why": why, "shared": [topic],
                    "a_id": a.get("assertion_id"), "a_src": a["_src"],
                    "a_cond": ((a.get("condition") or {}).get("text") or ""),
                    "a_action": a["action"].get("text"), "a_kind": a.get("kind"),
                    "b_id": b.get("assertion_id"), "b_src": b["_src"],
                    "b_cond": ((b.get("condition") or {}).get("text") or ""),
                    "b_action": b["action"].get("text"), "b_kind": b.get("kind"),
                })
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with io.open(outp, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print()
        print("=" * 62)
        print(f"冲突 {len(results)} 条　成本 ¥{total:.4f}")
        print(f"→ {outp}")
        for r in results:
            print(f"  · [{r['topic']}] {r['why']}")
            print(f"      A（{r['a_src']}）{r['a_cond']} → {r['a_action'][:40]}")
            print(f"      B（{r['b_src']}）{r['b_cond']} → {r['b_action'][:40]}")
        return 0

    pairs: list[tuple] = []
    if args.mode in ("term", "both"):
        pairs += build_pairs(rows, args.min_shared)
    if args.mode in ("issue", "both"):
        have = {(min(i, j), max(i, j)) for i, j, _ in pairs}
        for i, j, tags in build_pairs_issue(rows, args.max_per_issue):
            if (i, j) not in have:
                pairs.append((i, j, tags))
    if args.limit:
        pairs = pairs[:args.limit]
    print(f"[pair] 跨源候选 {len(pairs)} 对（mode={args.mode}）")
    if args.dry:
        for n, (i, j, sh) in enumerate(pairs[:25], 1):
            a, b = rows[i], rows[j]
            print(f"  [{n}] {'、'.join(sh)}")
            print(f"      {a['_src']}: {(a['action'].get('text') or '')[:38]}")
            print(f"      {b['_src']}: {(b['action'].get('text') or '')[:38]}")
        return 0
    if not pairs:
        print("[warn] 无候选对，检查术语表或 --min-shared")
        return 0

    from concurrent.futures import ThreadPoolExecutor
    batches = [pairs[k:k + args.batch] for k in range(0, len(pairs), args.batch)]
    results: list[dict] = []
    total_cost = 0.0
    fails = 0

    def work(b):
        v, st = judge(b)
        return b, v, st

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for b, v, st in ex.map(work, batches):
            total_cost += st["cost"]
            done += len(b)
            if not v:
                fails += 1
            for n, (i, j, sh) in enumerate(b, 1):
                r = v.get(n)
                if not r:
                    continue
                a, b2 = rows[i], rows[j]
                results.append({
                    "topic": r["topic"], "verdict": r["verdict"], "why": r["why"],
                    "shared": sh,
                    "a_id": a.get("assertion_id"), "a_src": a["_src"],
                    "a_cond": ((a.get("condition") or {}).get("text") or ""),
                    "a_action": a["action"].get("text"), "a_kind": a.get("kind"),
                    "b_id": b2.get("assertion_id"), "b_src": b2["_src"],
                    "b_cond": ((b2.get("condition") or {}).get("text") or ""),
                    "b_action": b2["action"].get("text"), "b_kind": b2.get("kind"),
                })
            print(f"  [{min(done,len(pairs))}/{len(pairs)}] 累计判定 {len(results)} 条，"
                  f"冲突 {sum(1 for x in results if x['verdict']=='contradicts')}")

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with io.open(outp, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    c = Counter(r["verdict"] for r in results)
    print()
    print("=" * 62)
    print(f"判定 {len(results)} 对 / 批次失败 {fails}")
    print(f"  contradicts {c.get('contradicts',0)} / compatible {c.get('compatible',0)} / "
          f"unrelated {c.get('unrelated',0)}")
    print(f"总成本 ¥{total_cost:.4f}")
    print(f"→ {outp}")
    print()
    print("冲突明细：")
    for r in results:
        if r["verdict"] == "contradicts":
            print(f"  · {r['topic']}")
            print(f"      [{r['a_src']}] {r['a_action'][:44]}")
            print(f"      [{r['b_src']}] {r['b_action'][:44]}")
            print(f"      {r['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
