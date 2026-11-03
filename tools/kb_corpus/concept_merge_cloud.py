# -*- coding: utf-8 -*-
"""概念语义归并（云端版）：DeepSeek 判定候选概念对是否同一概念，合并后重算多源数。

输入只读 knowledge_multisource_20261103.json；输出 research/reports/concept_merge_cloud_20261103.{json,md}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
SRC = R / "knowledge_multisource_20261103.json"
DATE = datetime.now().strftime("%Y%m%d")

SUFFIXES = ("判断逻辑", "逻辑", "方法", "技巧", "体系", "结构", "形态", "判断", "应用", "原则", "策略", "要点",
            "解析", "详解", "口诀", "战法", "指标", "参数", "设置", "使用", "教程", "入门", "基础", "进阶",
            "定义", "概念", "原理", "规则", "模型", "系统", "框架")
FILLER = re.compile(r"[（(].*?[）)]|版本|第[一二三四五六七八九十0-9]+[章节课]?|的|之")
PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")


def norm(s: str) -> str:
    s = PUNCT.sub("", str(s or "").lower())
    return s


def head(s: str) -> str:
    s = FILLER.sub("", str(s or ""))
    s = norm(s)
    changed = True
    while changed:
        changed = False
        for suf in SUFFIXES:
            if len(s) > len(suf) + 2 and s.endswith(suf):
                s = s[: -len(suf)]
                changed = True
    return s


def bigrams(s: str) -> set[str]:
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def jaccard(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B) if A | B else 0.0


DOMAIN_TERMS = ("中枢 背驰 分型 笔 线段 买卖点 级别 均线 均线粘合 多头排列 空头排列 金叉 死叉 背离 MACD KDJ RSI "
                "BOLL 布林 放量 缩量 量能 筹码 换手 涨停 跌停 平台 突破 缺口 箱体 支撑 阻力 趋势 回调 反弹 "
                "仓位 止损 止盈 风控 回撤 复利 资金流 过拟合 滑点 回测 因子 复盘 纪律 心态 情绪 缠论 黄金分割 "
                "斐波那契 带状线 老丫头 主升浪 强势 龙虎榜 北向 融资融券 可转债 期权 期货 波动率 量价 换手率 "
                "大盘 板块 题材 估值 业绩 财报 政策 消息 半导体 新能源 算力 机器人 电力 化工 有色 军工 医药 消费 金融").split()


def candidate_pairs(concepts: list[dict], limit: int = 400) -> list[dict]:
    """领域词桶 + 高相似度 + 跨来源优先，生成候选对。"""
    groups = defaultdict(list)
    for c in concepts:
        text = str(c.get("concept") or "")
        for term in DOMAIN_TERMS:
            if term in text:
                groups[term].append(c)
    pairs, seen = [], set()

    def srcs(c):
        return {s.get("source_key") for s in (c.get("sources") or []) if s.get("source_key")}

    def add(a, b, reason, score):
        ka, kb = a["concept_key"], b["concept_key"]
        key = tuple(sorted((ka, kb)))
        if key in seen:
            return
        seen.add(key)
        cross = bool(srcs(a) - srcs(b)) or bool(srcs(b) - srcs(a))
        pairs.append({"a": a["concept"], "b": b["concept"], "ka": ka, "kb": kb,
                      "reason": reason, "score": round(score, 3), "cross_source": cross})

    for h, cs in groups.items():
        cs = sorted(cs, key=lambda x: -int(x.get("n_sources") or 0))[:24]
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                s = jaccard(cs[i]["concept"], cs[j]["concept"])
                add(cs[i], cs[j], f"term:{h}", s)
    # 高相似但 head 不同的对
    flat = sorted(concepts, key=lambda c: len(c.get("concept") or ""))
    for i in range(len(flat)):
        ci = flat[i]
        for j in range(i + 1, min(len(flat), i + 25)):
            cj = flat[j]
            if head(ci.get("concept")) == head(cj.get("concept")):
                continue
            s = jaccard(ci["concept"], cj["concept"])
            if s >= 0.6:
                add(ci, cj, "fuzzy", s)
    pairs.sort(key=lambda p: (-int(p["cross_source"]), -p["score"]))
    return pairs[:limit]


JUDGE_SYSTEM = ("你是严格的概念归并判定器。只有 A 与 B 是**同一概念**才 same=true："
                "同义/简称/全称缩写/错别字/纯修饰词差异。以下一律 same=false："
                "A 是 B 的子集或具体化、B 比 A 更宽泛、不同对象/不同板块/不同时间周期/不同参数/不同方向、"
                "仅主题相关但含义不同。宁可 false 不可误并。只输出 JSON："
                '{"pairs":[{"i":0,"same":true}]}。不要解释。')


def judge_pairs(client, pairs: list[dict], batch: int = 10) -> list[bool]:
    results = [False] * len(pairs)
    for start in range(0, len(pairs), batch):
        chunk = pairs[start:start + batch]
        lines = [f"{i}. A={p['a']} | B={p['b']}" for i, p in enumerate(chunk)]
        msg = [{"role": "system", "content": JUDGE_SYSTEM},
               {"role": "user", "content": "\n".join(lines)}]
        raw = ""
        for _ in range(2):
            try:
                raw = client.chat(msg, temperature=0.0, max_tokens=2000,
                                  response_format={"type": "json_object"})
                obj = json.loads(raw)
                for item in obj.get("pairs") or []:
                    i = int(item.get("i", -1))
                    if 0 <= i < len(chunk):
                        results[start + i] = bool(item.get("same"))
                break
            except Exception as exc:  # noqa: BLE001
                if len(chunk) > 1:
                    chunk = chunk[:-1]
                    lines = [f"{i}. A={p['a']} | B={p['b']}" for i, p in enumerate(chunk)]
                    msg = [{"role": "system", "content": JUDGE_SYSTEM},
                           {"role": "user", "content": "\n".join(lines)}]
                    continue
                print(f"[judge batch {start}] ERR {type(exc).__name__}: {str(exc)[:100]}")
                break
    return results


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def merge(concepts: list[dict], pairs: list[dict], verdicts: list[bool]) -> dict:
    by_key = {c["concept_key"]: c for c in concepts}
    uf = UF()
    for c in concepts:
        uf.find(c["concept_key"])
    merged_pairs = []
    for p, same in zip(pairs, verdicts):
        if same:
            uf.union(p["ka"], p["kb"])
            merged_pairs.append(p)
    clusters = defaultdict(list)
    for c in concepts:
        clusters[uf.find(c["concept_key"])].append(c)
    def src_keys(cs):
        keys = set()
        for c in cs:
            for s in c.get("sources") or []:
                if s.get("source_key"):
                    keys.add(s["source_key"])
        return keys
    multi = []
    for root, cs in clusters.items():
        sk = src_keys(cs)
        if len(sk) >= 2:
            multi.append({"concept": cs[0]["concept"], "members": [c["concept"] for c in cs],
                          "n_sources": len(sk), "source_keys": sorted(sk)[:10]})
    before_multi = sum(1 for c in concepts if len({s.get("source_key") for s in (c.get("sources") or []) if s.get("source_key")}) >= 2)
    return {"concepts_before": len(concepts), "clusters_after": len(clusters),
            "merged_pairs": merged_pairs, "multi_source_before": before_multi,
            "multi_source_after": len(multi), "multi_source_clusters": multi,
            "by_key": by_key}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-judge", action="store_true", help="不调云端，仅生成候选对（测试用）")
    args = ap.parse_args(argv)
    src = json.loads(SRC.read_text(encoding="utf-8"))
    concepts = src.get("concepts") or []
    pairs = candidate_pairs(concepts, args.limit)
    print(f"concepts={len(concepts)} candidate_pairs={len(pairs)}")
    if args.dry_judge:
        verdicts = [False] * len(pairs)
    else:
        sys.path.insert(0, str(REPO / "agent_platform"))
        from engine.deepseek import DeepSeekClient  # type: ignore
        client = DeepSeekClient()
        verdicts = judge_pairs(client, pairs, args.batch)
    res = merge(concepts, pairs, verdicts)
    same_n = sum(1 for v in verdicts if v)
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "source": SRC.name, "model": "deepseek-flash",
           "candidate_pairs": len(pairs), "judged_same": same_n, **res}
    print(f"same_pairs={same_n} clusters={res['clusters_after']} multi_before={res['multi_source_before']} multi_after={res['multi_source_after']}")
    if args.write:
        R.joinpath(f"concept_merge_cloud_{DATE}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 概念语义归并（云端 DeepSeek）", "",
             f"> 生成：{out['generated_at']}｜概念 {len(concepts)}｜候选对 {len(pairs)}｜判定同一 {same_n}",
             "", "| 指标 | 归并前 | 归并后 |", "|---|---|---|",
             f"| 概念簇 | {len(concepts)} | **{res['clusters_after']}** |",
             f"| 多源概念 | **{res['multi_source_before']}** | **{res['multi_source_after']}** |", "",
             "## 多源簇（Top30）", "", "| 概念 | 成员 | 独立来源 |", "|---|---|---|"]
        for m in res["multi_source_clusters"][:30]:
            L.append(f"| {m['concept']} | {' / '.join(m['members'][:4])} | {m['n_sources']} |")
        L += ["", "## 判定合并的概念对（抽样 50）", "", "| A | B | 原因 | 相似度 |", "|---|---|---|---|"]
        for p in res["merged_pairs"][:50]:
            L.append(f"| {p['a']} | {p['b']} | {p['reason']} | {p['score']} |")
        R.joinpath(f"concept_merge_cloud_{DATE}.md").write_text("\n".join(L), encoding="utf-8")
        print("written reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
