# -*- coding: utf-8 -*-
"""候选重提炼：从知识单元抽取「术语短语概念」，过滤元数据/口语，生成 candidates v2。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
CARDS = REPO / "portal/static/kb_learning_cards.json"
DEFAULT_PID = "视频号学习_8博主"
DATE = datetime.now().strftime("%Y%m%d")

MD_RE = re.compile(r"[*`_|#~>\[\]]+")
BULLET_RE = re.compile(r"^[\s\-–—•·●○▪◦*\d.、)）]+")
META_RE = re.compile(r"^(共\s*\d+\s*条|由\s*faster|文件\s*[:：]|时长\s*[:：]|字数\s*[:：]|来源\s*[:：]|转写|—+\s*$|\+\+\+)")
FILLER = re.compile(r"^(看|说|再|那|这|今天|首先|然后|接下来|如果|所以|但是|其实|因为|我|你|他|她|大家|给|让|把|被|是|有|会|能|要|想|可以|知道|觉得|比如|例如|第一|第二|第三|最后|总之|简单|直接|具体|下面|上面|之前|之后|同时|而且|并且|就是|不是|还是|只是|真的|非常|特别|所有|只有|一个|这种|这些|那些|什么|怎么|如何|为什么|系统|后台|专业|传统|明明|明明只|回看|你写|你数|这块|那根|这条|这根|这个|那个|各位|兄弟们|朋友们|大家好|欢迎)")
DOMAIN = ("量化 AI 模型 算法 因子 回测 数据 机器学习 Python 代码 编程 策略 指标 公式 均线 MACD KDJ RSI 布林 金叉 死叉 背离 "
          "缠论 中枢 背驰 笔 线段 买卖点 级别 结构 趋势 突破 支撑 阻力 形态 平台 缺口 波浪 箱体 新高 回调 反弹 主升浪 "
          "仓位 止损 止盈 风控 回撤 杠杆 资金管理 复利 风险 心态 纪律 情绪 贪婪 恐惧 执行 复盘 耐心 认知 "
          "业绩 估值 财报 政策 消息 题材 板块 资金流 成交量 换手 量能 收盘 开盘 分时 盘口 通达信 同花顺 选股 涨停 "
          "斐波那契 带状线 老丫头 架拖 三均线 均线粘合 DIF DEA SKDJ QMT 集群算力 套利 动量因子 价值因子 筹码 "
          "主力 洗盘 出货 建仓 试盘 拉升 打压 涨停板 跌停 龙虎榜 北向 融资融券 可转债 期权 期货 波动率 "
          "正态分布 高斯 卡尔曼 粒子滤波 马尔可夫 神经网络 LSTM Transformer 强化学习 大模型 LLM 因子挖掘 "
          "信号 过拟合 未来函数 幸存者偏差 滑点 手续费 撮合 复盘 均线系统 量价 背离 金叉 死叉 多头排列 空头排列 "
          "缺口 平台 箱体 支撑位 阻力位 颈线 趋势线 通道 级别 中枢 背驰 买卖点 分型 笔 线段").split()


def clean_text(s) -> str:
    s = MD_RE.sub("", str(s or ""))
    s = s.replace("\u3000", " ")
    return re.sub(r"\s{2,}", " ", s).strip()


def clean_concept(s) -> str:
    c = clean_text(s)
    c = BULLET_RE.sub("", c)
    c = re.sub(r"^(所有的|专业的|只有|一个|这种|这些|那些|接下来|下面|上面)", "", c)
    return c.strip("。：:；;，,、！!？?\"'“”‘’「」『』()（）[]【】 ")


def norm(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (s or "").lower())


def has_domain(s: str) -> int:
    return sum(1 for k in DOMAIN if k in s)


def concept_from_claim(claim: str) -> str | None:
    claim = clean_text(claim)
    probe = BULLET_RE.sub("", claim)
    if META_RE.search(probe) or ".mp4" in probe or "faster-whisper" in probe:
        return None
    # 段落切成短句/短语
    clauses = [c.strip() for c in re.split(r"[，。；！？、,;!?]+", claim) if c.strip()]
    best, best_score = None, 0
    for c in clauses:
        c = clean_concept(c)
        if not (4 <= len(c) <= 16):
            continue
        if FILLER.match(c):
            continue
        if re.search(r"(吗|呢|吧|啊|呀|哦|嘛|到底|是不是|能不能|为什么|如何|怎么|要不要)$", c):
            continue
        if re.search(r"[，,。；;！!？?·\s]", c) or "." in c:
            continue
        if re.search(r"(文件|时长|字数|视频|mp4|转写|条$)", c):
            continue
        d = has_domain(c)
        if d == 0:
            continue
        score = d * 3 + (2 if 6 <= len(c) <= 12 else 0) - (1 if c.endswith("的") else 0)
        if score > best_score:
            best, best_score = c, score
    if not best:
        # 兜底：仅当段落里出现“单个完整领域词”作为短语（如 止损/均线/中枢）
        for k in DOMAIN:
            if len(k) >= 2 and k in claim:
                frag = clean_concept(k)
                if 2 <= len(frag) <= 8:
                    return frag
        return None
    return best


def truncate_answer(claim: str, concept: str = "", limit: int = 300) -> str:
    a = clean_text(claim)
    if len(a) <= limit:
        return a
    pos = a.find(concept) if concept else -1
    if pos >= 0:
        start = max(0, pos - 90)
        seg = a[start:start + limit]
        # 尽量从句子边界开始/结束
        for sep in ("。", "！", "？", "；"):
            j = seg.find(sep)
            if 0 <= j < 40:
                seg = seg[j + 1:]
                break
        cut = seg
    else:
        cut = a[:limit]
    for sep in ("。", "！", "？", "；", "."):
        idx = cut.rfind(sep)
        if idx > 80:
            return cut[:idx + 1]
    return cut[:limit]


def run(pid: str, write: bool) -> dict:
    units_p = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    units = [json.loads(line) for line in units_p.open(encoding="utf-8") if line.strip()]
    cards_doc = json.loads(CARDS.read_text(encoding="utf-8"))
    existing = {norm(c.get("concept")) for c in cards_doc.get("cards") or []}
    stats = {"units": len(units), "skip_meta": 0, "skip_no_concept": 0,
             "skip_duplicate": 0, "skip_short_answer": 0, "candidates": 0}
    out, batch = [], set()
    for u in units:
        claim = str(u.get("claim") or "")
        concept = concept_from_claim(claim)
        if not concept:
            if META_RE.search(clean_text(claim)):
                stats["skip_meta"] += 1
            else:
                stats["skip_no_concept"] += 1
            continue
        answer = truncate_answer(claim, concept)
        if len(answer) < 20:
            stats["skip_short_answer"] += 1
            continue
        key = norm(concept)
        if key in existing or key in batch:
            stats["skip_duplicate"] += 1
            continue
        batch.add(key)
        stats["candidates"] += 1
        ev = u.get("evidence") or {}
        out.append({
            "card_id": f"RX-{stats['candidates']:04d}",
            "status": "pending_review", "round": "reextract_v2",
            "concept": concept, "q": f"什么是「{concept}」？", "a": answer,
            "source": f"{pid} · {ev.get('file','')} · L{ev.get('line',0)}",
            "evidence": f"{ev.get('file','')} L{ev.get('line',0)}",
            "theme": "其他", "topic": "其他",
            "project_id": pid, "unit_id": u.get("unit_id"),
            "evidence_pointer": {"file": ev.get("file"), "line": ev.get("line")},
            "quality_score": 0, "traceable": True,
            "review": {"checkboxes": {"pass": False, "revise": False, "reject": False},
                       "reviewer": "", "notes": ""},
        })
    result = {"schema_version": "card_candidates.v2", "date": DATE,
              "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "generator": "card_reextract.py", "pid": pid,
              "sources": {"units": units_p.name}, "stats": stats,
              "cards_unchanged": True, "candidates": out}
    if write:
        p = REPO / "research/reports" / f"card_candidates_{pid}_v2_{DATE}.json"
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        s = REPO / "portal/static" / f"card_candidates_{pid}_v2.json"
        s.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("written:", p.name, "candidates:", stats["candidates"])
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default=DEFAULT_PID)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    r = run(args.pid, args.write)
    print(json.dumps(r["stats"], ensure_ascii=False))
    for c in r["candidates"][:12]:
        print("-", c["concept"], "|", c["a"][:40])
    return 0


if __name__ == "__main__":
    sys.exit(main())
