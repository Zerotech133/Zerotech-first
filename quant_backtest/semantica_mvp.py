# -*- coding: utf-8 -*-
"""Semantica 决策智能 MVP v2：缠论信号先例检索 + JEV 新闻事实图谱。

学习来源：数据强"Semantica 实战系列"（微信视频号 4 期）：
  入门 21 课（摄入/抽取/建图/管图）→ 进阶 13 课（推理引擎/时序图谱）
  → 集成 3 课（Agno 多智能体/决策智能）

结合本项目：
  · Decision Intelligence：缠论预警池入池决策按 record_decision 因果链
    模型（category/scenario/reasoning/outcome/confidence/entities）持久化，
    新信号发生时向量检索历史同型先例（增强侧面标签战绩表）
  · 事实图谱：JEV 已判定新闻（bias/conf/scope）直接建图+检索，
    公告/新闻元数据走 seed 路径（结构化数据，绕开 pattern NER 中文无效）

架构说明（v2 关键变更）：
  semantica 0.7.0 的 AgentContext 黑盒检索层对中文不可用——多处自建
  EmbeddingGenerator 硬编码英文模型（无关句 0.837>相关句 0.753），
  store(metadata) 会丢 content（空向量污染），faiss 索引不跨进程。
  故 v2 复用其数据模型与 GraphBuilder，检索层自管：
  fastembed BAAI/bge-small-zh-v1.5（512 维本地 ONNX）+ numpy 余弦。
  80 条先例毫秒级；后续可切 semantica.graph_store(Neo4j/FalkorDB)。

用法：
  python semantica_mvp.py build   # 注入缠论决策 80 条 + JEV 事实 30 条
  python semantica_mvp.py query   # 先例检索 + 事实检索演示
"""

import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
from fastembed import TextEmbedding

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "data", "semantica")
DEC_F = os.path.join(SNAP, "decisions.jsonl")
FACT_F = os.path.join(SNAP, "facts.jsonl")
PREC_CACHE_F = os.path.join(SNAP, "precedents_cache.json")
ZHEM = "BAAI/bge-small-zh-v1.5"
# 模型缓存固定到项目内稳定目录（2026-10-17 判例：默认 %TEMP%\fastembed_cache
# 会被系统清理/迁移丢弃，event_graph 嵌入 10061 实锤）；同时规避快照
# symlink 在 Windows 无权限时的静默失败（真身落在 blobs、快照空）。
FE_CACHE = os.path.join(HERE, ".models", "fastembed")


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


_emb = None


def embedder():
    global _emb
    if _emb is None:
        _emb = TextEmbedding(model_name=ZHEM, cache_dir=FE_CACHE)
    return _emb


def embed(texts):
    """批量嵌入 → L2 归一化矩阵。"""
    vecs = list(embedder().embed(texts))
    m = np.array(vecs, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return m


def topk(query_vec, mat, k=5):
    """余弦 top-k（归一化后即点积）。返回 [(idx, score)]。"""
    if mat is None or len(mat) == 0:
        return []
    s = mat @ query_vec
    idx = np.argsort(-s)[:k]
    return [(int(i), float(s[i])) for i in idx]


# ── 数据源 ───────────────────────────────────────────────────
def _pool_decisions(n=80):
    """缠论预警池 → record_decision 因果链记录（chanlun_pool.jsonl）。"""
    out = []
    with open(os.path.join(HERE, "data", "chanlun_pool.jsonl"), encoding="utf-8") as f:
        for ln in f.readlines()[-n:]:
            d = json.loads(ln)
            reasons = []
            if d.get("mbei"):
                reasons.append("段内动量衰减(mbei)")
            if d.get("bei"):
                reasons.append("背驰" + json.dumps(d["bei"], ensure_ascii=False))
            out.append(
                dict(
                    category="chanlun_signal",
                    scenario="%s %s %s %s dt=%s px=%.2f"
                    % (
                        d.get("market"),
                        d.get("name"),
                        d.get("code"),
                        d.get("type"),
                        d.get("dt"),
                        d.get("price") or 0,
                    ),
                    reasoning="；".join(reasons) or "结构信号",
                    outcome="入池",
                    confidence=0.7,
                    entities=[d.get("code") or "", d.get("type") or ""],
                    cross_system_context={
                        "code": d.get("code"),
                        "sig_type": d.get("type"),
                        "dt": d.get("dt"),
                    },
                )
            )
    return out


def _jev_facts(n=30):
    """JEV 已判定新闻 → 事实记录（source news.db，jev 列 bias/conf/scope）。"""
    db = os.path.join(HERE, "..", "news_collector", "news.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,title,ts,stocks,jev FROM news "
        "WHERE jev IS NOT NULL AND jev != '' ORDER BY rowid DESC LIMIT ?",
        (n,),
    ).fetchall()
    con.close()
    facts = []
    for r in rows:
        d = json.loads(r["jev"]) if isinstance(r["jev"], str) else (r["jev"] or {})
        text = ("%s %s" % (r["ts"] or "", r["title"] or "")).strip()
        if not text:
            continue  # 空文本无法嵌入
        facts.append(
            dict(
                text=text,
                bias=d.get("bias"),
                conf=d.get("conf"),
                scope=d.get("scope"),
                stocks=r["stocks"],
                nid=r["id"],
            )
        )
    return facts


# ── 持久化（原子写） ─────────────────────────────────────────
def _dump_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]


# ── 先例检索（基于 chanlun_paper.json closed[] 持仓结算数据） ──
_PAPER_CACHE = None
_PAPER_MAT = None
_PAPER_TEXTS = None


def _load_paper():
    """加载 chanlun_paper.json closed[] 持仓，构建先例文本+矩阵（进程缓存）。

    影子文件 chanlun_shadow_1sell_tbe.jsonl 是 1卖+趋✓ 的专项副本，
    与 closed[] 的 1卖+趋✓ 子集（n=84, ret mean=0.0590）完全一致；
    故以 closed[] 为统一先例库，覆盖全部 6 类信号 × tbe/bei/mbei/vv/reg 多标签。
    """
    global _PAPER_CACHE, _PAPER_MAT, _PAPER_TEXTS
    if _PAPER_CACHE is not None:
        return _PAPER_CACHE, _PAPER_MAT, _PAPER_TEXTS
    path = os.path.join(HERE, "data", "chanlun_paper.json")
    if not os.path.exists(path):
        return [], None, []
    d = json.load(open(path, encoding="utf-8"))
    rows = d.get("closed", [])
    texts = []
    for r in rows:
        tags = r.get("tags") or {}
        tbe = tags.get("tbe")
        parts = ["%s %s %s" % (r.get("code"), r.get("name"), r.get("type"))]
        if tbe is True:
            parts.append("趋势背驰")
        if tags.get("bei"):
            parts.append("背驰")
        if tags.get("mbei"):
            parts.append("段内动量衰减")
        parts.append(
            "entry=%.2f exit=%.2f ret%+.4f"
            % (r.get("entry_px") or 0, r.get("exit_px") or 0, r.get("ret") or 0)
        )
        texts.append(" ".join(parts))
    mat = embed(texts) if texts else None
    _PAPER_CACHE, _PAPER_MAT, _PAPER_TEXTS = rows, mat, texts
    return rows, mat, texts


def precedents(code, sig_type, tbe=None, k=8):
    """检索同型先例，返回战绩统计 + top-k 相似样本。

    Args:
        code: 标的代码（仅用于查询嵌入，不参与过滤）
        sig_type: 信号类型（1买/1卖/2买/2卖/3买/3卖）
        tbe: 趋势背驰过滤 True/False/None(不过滤)
        k: 返回 top-k 相似样本数

    Returns:
        dict: {n, win_rate, avg_return, median_return,
               sample: [{code,name,type,sig_dt,ret,tbe,score}]}
    """
    rows, mat, _ = _load_paper()
    if not rows or mat is None:
        return {
            "n": 0,
            "win_rate": 0,
            "avg_return": 0,
            "median_return": 0,
            "sample": [],
        }
    # 按 sig_type + tbe 过滤候选集
    cands = []
    for i, r in enumerate(rows):
        if r.get("type") != sig_type:
            continue
        rt = (r.get("tags") or {}).get("tbe")
        if tbe is True and rt is not True:
            continue
        if tbe is False and rt is not False:
            continue
        cands.append(i)
    if not cands:
        return {
            "n": 0,
            "win_rate": 0,
            "avg_return": 0,
            "median_return": 0,
            "sample": [],
        }
    # 全候选战绩统计
    all_rets = [
        rows[i].get("ret") or 0 for i in cands if rows[i].get("ret") is not None
    ]
    win = sum(1 for v in all_rets if v > 0)
    avg = statistics.mean(all_rets) if all_rets else 0
    med = statistics.median(all_rets) if all_rets else 0
    # 向量检索 top-k 相似先例
    q_text = "%s %s %s" % (code, sig_type, "趋势背驰" if tbe is True else "结构信号")
    qv = embed([q_text])[0]
    sub_mat = mat[cands]
    scores = sub_mat @ qv
    order = np.argsort(-scores)[:k]
    sample = []
    for idx in order:
        r = rows[cands[int(idx)]]
        sample.append(
            {
                "code": r.get("code"),
                "name": r.get("name"),
                "type": r.get("type"),
                "sig_dt": r.get("sig_dt"),
                "ret": r.get("ret"),
                "tbe": (r.get("tags") or {}).get("tbe"),
                "score": float(scores[int(idx)]),
            }
        )
    return {
        "n": len(cands),
        "win_rate": win * 1.0 / len(all_rets) if all_rets else 0,
        "avg_return": avg,
        "median_return": med,
        "sample": sample,
    }


def enrich_signal(code, sig_type, dt, tbe=None):
    """信号触发时调用，返回先例摘要文本（用于飞书推送/门户展示）。

    形如：
      📋 同型先例 1卖 趋✓: n=84 win=63.1% avg=+5.90%
        · 2026-07-07 南方泵业 ✗ ret-17.23%
        · 2026-04-21 西测测试 ✓ ret+22.53%
        · 2026-04-13 好利科技 ✗ ret+0.00%
    """
    p = precedents(code, sig_type, tbe=tbe, k=8)
    if p["n"] == 0:
        return "📋 同型先例 %s: 暂无历史样本" % sig_type
    tag = " 趋✓" if tbe is True else ""
    head = "📋 同型先例 %s%s: n=%d win=%.1f%% avg%+.2f%%" % (
        sig_type,
        tag,
        p["n"],
        p["win_rate"] * 100,
        p["avg_return"] * 100,
    )
    lines = []
    for s in p["sample"][:3]:
        mark = "✓" if (s["ret"] or 0) > 0 else "✗"
        lines.append(
            "  · %s %s %s ret%+.2f%%"
            % (s["sig_dt"], s["name"] or s["code"], mark, (s["ret"] or 0) * 100)
        )
    return head + "\n" + "\n".join(lines)


# ── 预计算缓存（供门户读，避免 HTTP 请求触发 fastembed 加载） ──
def dump_precedents_cache():
    """预计算所有 sig_type × tbe 组合的先例统计，写 JSON 缓存。

    键约定："<type>" 全型 / "<type>_tbe" 趋势背驰子集 / "<type>_notbe" 非趋势背驰。
    每日 chanlun_scan 任务后调用，刷新统计。
    直接读 chanlun_paper.json，不触发 fastembed 加载，可在扫描任务末尾安全调用。
    """
    path = os.path.join(HERE, "data", "chanlun_paper.json")
    if not os.path.exists(path):
        print("dump_precedents_cache: no paper, skip")
        return
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = d.get("closed", [])
    if not rows:
        print("dump_precedents_cache: empty closed, skip")
        return
    buckets = defaultdict(list)
    for r in rows:
        t = r.get("type")
        ret = r.get("ret")
        if not t or ret is None:
            continue
        buckets[t].append(ret)
        tbe = (r.get("tags") or {}).get("tbe")
        if tbe is True:
            buckets["%s_tbe" % t].append(ret)
        elif tbe is False:
            buckets["%s_notbe" % t].append(ret)
    out = {"updated_at": _now_iso(), "by_key": {}}
    for k, rets in buckets.items():
        n = len(rets)
        out["by_key"][k] = {
            "n": n,
            "win_rate": sum(1 for v in rets if v > 0) * 1.0 / n,
            "avg_return": statistics.mean(rets),
            "median_return": statistics.median(rets),
        }
    os.makedirs(SNAP, exist_ok=True)
    tmp = PREC_CACHE_F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, PREC_CACHE_F)
    print("dump_precedents_cache: %d keys → %s" % (len(out["by_key"]), PREC_CACHE_F))


def read_precedents_cache():
    """门户读层：返回 precedents_cache.json（缺失返回 {}）。"""
    if not os.path.exists(PREC_CACHE_F):
        return {}
    try:
        with open(PREC_CACHE_F, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── build / query ────────────────────────────────────────────
def build():
    dec = _pool_decisions()
    facts = _jev_facts()
    _dump_jsonl(DEC_F, dec)
    _dump_jsonl(FACT_F, facts)
    print(
        "build 完成: decisions=%d facts=%d\n  %s\n  %s"
        % (len(dec), len(facts), DEC_F, FACT_F)
    )


def query():
    dec = _load_jsonl(DEC_F)
    facts = _load_jsonl(FACT_F)
    if not dec or not facts:
        print("库为空，先跑 build")
        return

    qv = embed(["1卖 趋势背驰 强预警 顶背离"])[0]
    dtexts = [d["scenario"] + " " + d["reasoning"] for d in dec]
    dmat = embed(dtexts)

    print("=== 先例检索：新信号「1卖 趋势背驰」同型决策 top5 ===")
    for i, s in topk(qv, dmat, 5):
        d = dec[i]
        print(
            " · [%.3f] %s | %s | %s"
            % (
                s,
                d["scenario"][:46],
                d["reasoning"][:20],
                d["cross_system_context"]["dt"],
            )
        )

    print()
    qv2 = embed(["储能 电网 中标 订单"])[0]
    ftexts = [f["text"] for f in facts]
    fmat = embed(ftexts)
    print("=== 事实检索：JEV 已判定新闻「储能/电网/订单」top5 ===")
    for i, s in topk(qv2, fmat, 5):
        f = facts[i]
        print(
            " · [%.3f] %s bias=%s conf=%s" % (s, f["text"][:44], f["bias"], f["conf"])
        )

    print()
    print("=== 先例统计（同型信号近 80 条内分布） ===")
    from collections import Counter

    cnt = Counter(d["cross_system_context"]["sig_type"] for d in dec)
    print(" ", dict(cnt))

    # 先例检索演示：1卖+趋✓（首个达标显著有效因子）
    print()
    print("=== 先例检索：1卖+趋✓ 同型战绩 ===")
    p1 = precedents("000001.SH", "1卖", tbe=True, k=5)
    print(
        "  n=%d win=%.1f%% avg=%+.2f%% median=%+.2f%%"
        % (
            p1["n"],
            p1["win_rate"] * 100,
            p1["avg_return"] * 100,
            p1["median_return"] * 100,
        )
    )
    for s in p1["sample"]:
        mark = "✓" if (s["ret"] or 0) > 0 else "✗"
        print(
            "    [%.3f] %s %s %s ret%+.2f%%"
            % (
                s["score"],
                s["sig_dt"],
                s["name"] or s["code"],
                mark,
                (s["ret"] or 0) * 100,
            )
        )

    # 对照组：1卖 不带趋✓（基线）
    print()
    print("=== 对照：1卖 不带趋✓ 基线战绩 ===")
    p2 = precedents("000001.SH", "1卖", tbe=False, k=5)
    print(
        "  n=%d win=%.1f%% avg=%+.2f%% median=%+.2f%%"
        % (
            p2["n"],
            p2["win_rate"] * 100,
            p2["avg_return"] * 100,
            p2["median_return"] * 100,
        )
    )

    # enrich_signal 摘要文本（飞书推送用）
    print()
    print("=== enrich_signal 摘要（飞书推送示例） ===")
    print(enrich_signal("000001.SH", "1卖", "2026-09-26", tbe=True))


if __name__ == "__main__":

    def _main_precedents():
        from pprint import pprint

        pprint(precedents("000001.SH", "1卖", tbe=True, k=5))

    def _main_enrich():
        code = sys.argv[2] if len(sys.argv) > 2 else "000001.SH"
        sig = sys.argv[3] if len(sys.argv) > 3 else "1卖"
        dt = sys.argv[4] if len(sys.argv) > 4 else "2026-09-26"
        print(enrich_signal(code, sig, dt, tbe=True))

    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {
        "build": build,
        "query": query,
        "dump_cache": dump_precedents_cache,
        "precedents": _main_precedents,
        "enrich": _main_enrich,
    }[cmd]()
