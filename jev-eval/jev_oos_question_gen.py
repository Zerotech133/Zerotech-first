#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_oos_question_gen — OOS 题集生成器（W5，2026-10-14，只读产线数据）。

目的：现评估 30 题×3 遍 93.3% 是 in-sample（同一批手写题反复用）。
本脚本从**近期未用于评估的**候选池事件自动抽题，生成
oos_question_set_v1.json，题目格式与 jev_eval.py 的 questions.json 完全兼容
（noul/choice/score 三类，字段 id/type/state/question/answer*）。

"未用于评估"的定义（防评估过拟合的核心规则）：
  排除 ① review_data/triples.jsonl 出现过的 candidate_id（影子门校准用过）
       ② replay/replay-20260922-*.jsonl 出现过的 (ts_code, pool_date)
         （v2 影子决策设计用过，见 signal_review_gate.py L378-383）
  剩余事件即 fresh 池（2026-07-09 起全历史中未被任何评估消费的部分）。

抽题规则（分层抽样）：
  - 层 = 市场域 × 策略评分段；评分段沿用任务口径 low<0.5 / mid 0.5-0.7 / high≥0.7
  - score=0 视为缺失（HK v1 通道无分），不参与抽题（留痕）
  - 市场域配额按 sqrt(层内样本量) 分配（比按比例更保小域样本），每域至少 4
  - 固定随机种子 20261014，可复现

出题规则（答案全部由数据确定性推导，无人工标注）：
  - 每事件 2 题：
    ① noul：对 state 中事实的真/假陈述（评分值或市场域，4 循环保证答案均衡）
    ② choice（市场域四选一，正确项位置轮转）或 score（评分档位三档）交替
  - state 为事件事实的改写（池日期/市场/评分/信号理由），不含任何答案之外的提示
  - 每题携带 src 字段（candidate_id/pool_date/market/score）供留痕，
    jev_eval.py 按键取值，未知字段不影响评测

⚠ 纪律：本脚本只读产线 DB（SELECT）；生成题集后**不跑评估**
  （评估要消耗 AI 调用，何时跑由老田拍板）。跑评估时需将 jev_eval.py 的
  QUESTIONS_FILE 指向本文件（或届时加 --questions 参数），本脚本不代劳。

用法：
  "C:/Program Files/Python311/python.exe" jev_oos_question_gen.py
输出：
  oos_question_set_v1.json + stdout 抽样摘要
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from jev_common import db_rows  # noqa: E402

TRIPLES = HERE / "review_data" / "triples.jsonl"
REPLAYS = [HERE / "replay" / "replay-20260922-020710.jsonl",
           HERE / "replay" / "replay-20260922-030434.jsonl"]
OUT = HERE / "oos_question_set_v1.json"

SEED = 20261014
N_EVENTS = 36          # 36 事件 × 2 题 = 72 题（≥60）
MIN_PER_MARKET = 4
BANDS = (("low", 0.0, 0.5), ("mid", 0.5, 0.7), ("high", 0.7, 1.01))
MARKETS = ("CN", "US", "HK", "CRYPTO")
MARKET_LABEL = {"CN": "CN（A股）", "US": "US（美股）", "HK": "HK（港股）",
                "CRYPTO": "CRYPTO（加密货币）"}

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def band_of(score: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= score < hi:
            return name
    return "high"  # score=1.0 边界兜底


def used_keys() -> tuple[set, set]:
    tri_ids, rep_pairs = set(), set()
    if TRIPLES.exists():
        for line in TRIPLES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                tri_ids.add(json.loads(line).get("candidate_id"))
    for f in REPLAYS:
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    rep_pairs.add((str(r.get("ts_code")), str(r.get("pool_date"))))
    return tri_ids, rep_pairs


def fetch_fresh() -> list[dict]:
    tri_ids, rep_pairs = used_keys()
    rows = db_rows(
        "SELECT a.candidate_id, a.ts_code, a.market, "
        "coalesce(a.candidate_score,0), coalesce(a.candidate_reason,''), "
        "p.pool_date, coalesce(p.pool_type,'') "
        "FROM v2_candidate_asset a JOIN v2_candidate_pool p ON a.pool_id = p.pool_id "
        "ORDER BY p.pool_date DESC, a.candidate_score DESC NULLS LAST")
    fresh, dropped_missing = [], 0
    for cid, code, mkt, score, reason, pd_, ptype in rows:
        if cid in tri_ids or (str(code), str(pd_)) in rep_pairs:
            continue
        score = float(score or 0)
        if score <= 0:  # 评分缺失（HK v1 通道）不参与分层
            dropped_missing += 1
            continue
        fresh.append({"candidate_id": cid, "ts_code": str(code),
                      "market": str(mkt or "?"), "score": score,
                      "reason": str(reason or ""), "pool_date": str(pd_),
                      "pool_type": str(ptype)})
    print(f"fresh 池：{len(fresh)} 事件（另排除 score=0 缺失 {dropped_missing} 条）")
    return fresh


def allocate(fresh: list[dict]) -> dict[str, list[dict]]:
    """市场域配额 ∝ sqrt(域样本)，每域≥MIN_PER_MARKET；域内按评分段比例分层。"""
    by_mkt: dict[str, list[dict]] = defaultdict(list)
    for r in fresh:
        by_mkt[r["market"]].append(r)
    totals = {m: len(by_mkt[m]) for m in MARKETS if by_mkt.get(m)}
    sq = {m: max(MIN_PER_MARKET, n ** 0.5) for m, n in totals.items()}
    quota = {m: max(MIN_PER_MARKET, round(N_EVENTS * v / sum(sq.values())))
             for m, v in sq.items()}
    # 配额不得超过域内可用量
    quota = {m: min(q, totals[m]) for m, q in quota.items()}
    rng = random.Random(SEED)
    picked: dict[str, list[dict]] = {}
    for m, q in sorted(quota.items()):
        bands: dict[str, list[dict]] = defaultdict(list)
        for r in by_mkt[m]:
            bands[band_of(r["score"])].append(r)
        # 域内按段比例分配（大段优先吃满，小段有多少给多少，余量回补大段）
        band_quota: dict[str, int] = {}
        remain = q
        for name, _, _ in sorted(BANDS, key=lambda b: len(bands[b[0]])):
            avail = len(bands[name])
            want = round(remain * avail / max(1, sum(len(bands[b[0]])
                                                     for b in BANDS
                                                     if bands.get(b[0]))))
            take = min(avail, want)
            band_quota[name] = take
            remain -= take
        for name in band_quota:  # 余量回补（按段样本量降序）
            if remain <= 0:
                break
            add = min(remain, len(bands[name]) - band_quota[name])
            if add > 0:
                band_quota[name] += add
                remain -= add
        sel = []
        for name, _, _ in BANDS:
            pool = sorted(bands.get(name, []), key=lambda r: r["candidate_id"])
            sel += rng.sample(pool, min(band_quota.get(name, 0), len(pool)))
        picked[m] = sel
        print(f"  {m:<7} 配额 {q}（域样本 {totals[m]:>4}）→ 实抽 {len(sel)} "
              f"按段 {dict(Counter(band_of(r['score']) for r in sel))}")
    return picked


def make_questions(picked: dict[str, list[dict]]) -> list[dict]:
    qs: list[dict] = []
    n = c = s = 0
    noul_ans = Counter()
    idx = 0
    for m in MARKETS:
        for r in picked.get(m, []):
            src = {"candidate_id": r["candidate_id"], "pool_date": r["pool_date"],
                   "market": r["market"], "score": r["score"]}
            state = (f"候选信号事件（{r['pool_date']}，{MARKET_LABEL.get(r['market'], r['market'])}，"
                     f"池类型 {r['pool_type'] or '未标注'}）：标的 {r['ts_code']}，"
                     f"策略评分 {r['score']:.4f}。信号理由：{r['reason'][:200] or '（无理由文本）'}")
            # ① noul：真/假事实陈述，4 循环（评分真/评分假/市场真/市场假）
            kind = idx % 4
            if kind in (0, 1):
                shown = r["score"]
                if kind == 1:  # 扰动 ±0.05 且夹到 [0,1]，保证与真值不同
                    shown = round(min(1.0, max(0.0, r["score"] + 0.05)), 4)
                    if shown == round(r["score"], 4):
                        shown = round(min(1.0, max(0.0, r["score"] - 0.05)), 4)
                ans = kind == 0
                qs.append({"id": f"oos-n{n + 1:03d}", "type": "noul", "state": state,
                           "question": f"该候选的策略评分为 {shown:.4f}。",
                           "answer": ans, "src": src})
                noul_ans[ans] += 1
                n += 1
            else:
                wrong = next(x for x in MARKETS if x != r["market"])
                ans = kind == 2
                claimed = r["market"] if ans else wrong
                qs.append({"id": f"oos-n{n + 1:03d}", "type": "noul", "state": state,
                           "question": f"该候选信号属于 {MARKET_LABEL[claimed]} 市场域。",
                           "answer": ans, "src": src})
                noul_ans[ans] += 1
                n += 1
            # ② choice（市场域）/ score（评分档位）交替；choice 正确项位置轮转
            if idx % 2 == 0:
                opts = list(MARKETS)
                rot = idx % 4
                opts = opts[rot:] + opts[:rot]
                ai = opts.index(r["market"])
                qs.append({"id": f"oos-c{c + 1:03d}", "type": "choice", "state": state,
                           "question": "该候选信号属于哪个市场域？",
                           "options": [MARKET_LABEL[x] for x in opts],
                           "answer_index": ai, "src": src})
                c += 1
            else:
                lv = band_of(r["score"])
                qs.append({"id": f"oos-s{s + 1:03d}", "type": "score", "state": state,
                           "question": "该候选策略评分所处的档位是？",
                           "levels": ["低（0-0.5）", "中（0.5-0.7）", "高（0.7-1）"],
                           "answer_level": ("low", "mid", "high").index(lv),
                           "src": src})
                s += 1
            idx += 1
    print(f"出题：noul {n}（真/假 {noul_ans[True]}/{noul_ans[False]}）+ "
          f"choice {c} + score {s} = {len(qs)} 题")
    return qs


def main() -> None:
    fresh = fetch_fresh()
    by_band = Counter(band_of(r["score"]) for r in fresh)
    by_mkt = Counter(r["market"] for r in fresh)
    print(f"fresh 分层底数：市场域 {dict(by_mkt)}；评分段 {dict(by_band)}")
    picked = allocate(fresh)
    qs = make_questions(picked)

    bank = {
        "meta": {
            "name": "jev-oos-question-set",
            "version": "v1",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "jev_oos_question_gen.py",
            "seed": SEED,
            "purpose": "OOS 防过拟合题集：全部题目来自未参与过任何评估的候选池事件",
            "exclusion_rules": [
                "排除 review_data/triples.jsonl 中的 candidate_id（影子门校准已用）",
                "排除 replay/replay-20260922-*.jsonl 中的 (ts_code, pool_date)"
                "（v2 影子决策设计已用）",
                "排除 candidate_score<=0（评分缺失，HK v1 通道）",
            ],
            "sampling_rules": [
                "层 = 市场域×评分段（low<0.5 / mid 0.5-0.7 / high>=0.7）",
                f"市场域配额 ∝ sqrt(域样本量)，每域至少 {MIN_PER_MARKET}；"
                "域内按评分段比例分层，小段不足给多少算多少、余量回补大段",
                f"目标 {N_EVENTS} 事件 × 2 题 = {len(qs)} 题",
                "固定种子可复现；事件内 candidate_id 排序后抽样，防 DB 返回序漂移",
            ],
            "question_rules": [
                "每事件 2 题：noul（对 state 事实真/假陈述，4 循环均衡答案）+ "
                "choice（市场域四选一，正确项位置轮转）或 score（评分三档）交替",
                "答案全部由源数据确定性推导，无人工标注；每题 src 字段可回溯事件",
                "state 不含题目答案之外的评价性内容（防泄题）",
            ],
            "stratum_base": {"by_market": dict(by_mkt), "by_band": dict(by_band)},
            "answer_distribution": {
                "noul_true_false": [sum(1 for q in qs if q["type"] == "noul" and q["answer"]),
                                    sum(1 for q in qs if q["type"] == "noul" and not q["answer"])],
                "choice_answer_index": dict(Counter(q["answer_index"] for q in qs
                                                    if q["type"] == "choice")),
                "score_answer_level": dict(Counter(q["answer_level"] for q in qs
                                                   if q["type"] == "score")),
            },
            "known_limits": [
                "mid 段（0.5-0.7）在 fresh 池内天然稀薄，score 题 answer_level 分布"
                "随数据偏置（已在 answer_distribution 披露），判读时注意",
                "加密域事件少，配额下限保证其存在但样本代表性有限",
            ],
            "how_to_run": "评估执行需将 jev_eval.py QUESTIONS_FILE 指向本文件；"
                          "是否跑、何时跑由老田拍板（本生成不消耗 AI 调用）",
        },
        "questions": qs,
    }
    OUT.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写 {OUT}（{len(qs)} 题）")


if __name__ == "__main__":
    main()
