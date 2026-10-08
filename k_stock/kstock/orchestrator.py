# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 团队编排器（Python 重实现菜鸟 ai_team_role DSL）

菜鸟蓝本：thunder-model-admin TeamTaskServiceImpl（851 行 Java）——
本文件按其数据模型（ai_team_role DDL）重实现四个核心机制：
  1. 拓扑排序：parent_node_keys 依赖图 → 执行波次（level 分层）
  2. 并行屏障：is_parallel + wait_all_complete → 同层并发、全完才流转
  3. 辩论链：debate_partner_key 互指 + max_debate_rounds + 相似度>0.8 提前收敛
  4. 降级三态：节点 LLM 失败 → 重试(2) → 降级输出(标记 degraded) → 不阻塞全链
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ChatFn = Callable[..., tuple[str, str]]  # LLMClient.chat 签名


# ---------- DSL 加载 ----------

def load_team(path: str | Path) -> list[dict[str, Any]]:
    """加载 team_poc.json（字段名对齐 ai_team_role DDL）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))["nodes"]


def topo_waves(nodes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按 parent_node_keys 依赖分波次（Kahn 分层）。无父=第 0 波。"""
    by_key = {n["node_key"]: n for n in nodes}
    indeg = {k: 0 for k in by_key}
    children: dict[str, list[str]] = {k: [] for k in by_key}
    for n in nodes:
        for p in n.get("parent_node_keys") or []:
            if p in by_key:
                children[p].append(n["node_key"])
                indeg[n["node_key"]] += 1
    wave = [k for k, d in indeg.items() if d == 0]
    waves: list[list[dict[str, Any]]] = []
    seen = 0
    while wave:
        waves.append([by_key[k] for k in sorted(wave)])
        seen += len(wave)
        nxt: list[str] = []
        for k in wave:
            for c in children[k]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        wave = nxt
    if seen != len(by_key):
        raise ValueError("依赖图存在环，无法拓扑排序")
    return waves


# ---------- 相似度（辩论收敛判据，S1 用词面 Jaccard，S2 换向量） ----------

def similarity(a: str, b: str) -> float:
    sa = set(re.findall(r"[\u4e00-\u9fff\w]+", a.lower()))
    sb = set(re.findall(r"[\u4e00-\u9fff\w]+", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------- 编排器 ----------

class Orchestrator:
    def __init__(self, nodes: list[dict[str, Any]], chat: ChatFn,
                 max_workers: int = 3, converge_threshold: float = 0.8):
        self.nodes = {n["node_key"]: n for n in nodes}
        self.waves = topo_waves(nodes)
        self.chat = chat
        self.max_workers = max_workers
        self.converge_threshold = converge_threshold
        self.trace: list[dict[str, Any]] = []  # lineage：每步留痕

    def run(self, stock: str, context: str = "") -> dict[str, Any]:
        """执行全团队。返回 {outputs: {node_key: parsed_json}, trace, degraded}。"""
        outputs: dict[str, dict[str, Any]] = {}
        raw_outputs: dict[str, str] = {}
        degraded: list[str] = []
        for wave in self.waves:
            # 辩论对手互指的节点（is_debate_node）先配对辩论
            done_keys: set[str] = set()
            debate_pairs = self._pair_debates(wave)
            for a_key, b_key in debate_pairs:
                self._debate(a_key, b_key, stock, context, outputs, raw_outputs, degraded)
                done_keys.update({a_key, b_key})
            remain = [n for n in wave if n["node_key"] not in done_keys]
            if len(remain) == 1 or (len(remain) > 1 and not remain[0].get("is_parallel")):
                for n in remain:
                    self._single(n, stock, context, outputs, raw_outputs, degraded)
            else:  # 并行屏障
                with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                    futs = {ex.submit(self._single, n, stock, context, outputs,
                                      raw_outputs, degraded): n["node_key"]
                            for n in remain}
                    for f in as_completed(futs):
                        f.result()
        return {"outputs": outputs, "raw": raw_outputs,
                "trace": self.trace, "degraded": degraded}

    # ----- 辩论配对（debate_partner_key 互指） -----
    def _pair_debates(self, wave: list[dict[str, Any]]) -> list[tuple[str, str]]:
        pairs, seen = [], set()
        for n in wave:
            if not n.get("is_debate_node"):
                continue
            k = n["node_key"]
            partner = n.get("debate_partner_key")
            if partner in self.nodes and partner not in seen:
                pairs.append((k, partner))
                seen.update({k, partner})
        return pairs

    # ----- 辩论执行：max_debate_rounds 轮，相似度超阈值提前收敛 -----
    def _debate(self, a_key, b_key, stock, context, outputs, raw_outputs, degraded):
        a, b = self.nodes[a_key], self.nodes[b_key]
        msg_a = msg_b = ""
        rounds = int(a.get("max_debate_rounds") or 2)
        rd = 0
        for rd in range(1, rounds + 1):
            msg_a = self._call(a, stock, context, outputs, opponent_last=msg_b,
                               round_no=rd, raw_out=raw_outputs, degraded=degraded)
            # 对手已降级则不再向其追问（无意义辩论），提前收敛
            if '"degraded": true' in msg_b and rd > 1:
                self.trace.append({"event": "debate_aborted", "round": rd,
                                   "why": f"{b_key} degraded", "ts": datetime.now().isoformat(timespec="seconds")})
                break
            msg_b = self._call(b, stock, context, outputs, opponent_last=msg_a,
                               round_no=rd, raw_out=raw_outputs, degraded=degraded)
            if '"degraded": true' in msg_a and rd > 1:
                self.trace.append({"event": "debate_aborted", "round": rd,
                                   "why": f"{a_key} degraded", "ts": datetime.now().isoformat(timespec="seconds")})
                break
            if similarity(msg_a, msg_b) >= self.converge_threshold:
                self.trace.append({"event": "debate_converged", "round": rd,
                                   "pair": [a_key, b_key], "sim": round(similarity(msg_a, msg_b), 3),
                                   "ts": datetime.now().isoformat(timespec="seconds")})
                break
        outputs[a_key] = self._parse(msg_a, degraded, a_key)
        outputs[b_key] = self._parse(msg_b, degraded, b_key)
        self.trace.append({"event": "debate_done", "pair": [a_key, b_key],
                           "rounds": max(rd, 1), "ts": datetime.now().isoformat(timespec="seconds")})

    # ----- 单节点执行：重试 2 次 → 降级 -----
    def _single(self, node, stock, context, outputs, raw_outputs, degraded):
        msg = self._call(node, stock, context, outputs, raw_out=raw_outputs, degraded=degraded)
        outputs[node["node_key"]] = self._parse(msg, degraded, node["node_key"])
        self.trace.append({"event": "node_done", "node": node["node_key"],
                           "analysis_type": node.get("analysis_type"),
                           "ts": datetime.now().isoformat(timespec="seconds")})

    # ----- 单次 LLM 调用（含降级三态的执行面） -----
    def _call(self, node, stock, context, outputs, opponent_last="", round_no=1,
              raw_out=None, degraded=None):
        from .nodes import build_messages
        msgs = build_messages(node, stock, context, outputs, opponent_last, round_no)
        attempts = int(node.get("retry") or 2)
        for i in range(attempts + 1):
            try:
                text, provider = self.chat(msgs)
                if text and text.strip():  # 空输出（Ollama 负载下偶发）视同失败重试
                    if raw_out is not None:
                        raw_out[node["node_key"]] = text
                    return text
                raise ValueError("空输出")
            except Exception as e:
                self.trace.append({"event": "node_retry", "node": node["node_key"],
                                   "attempt": i + 1, "err": str(e)[:120],
                                   "ts": datetime.now().isoformat(timespec="seconds")})
                if i < attempts:
                    time.sleep(2 + 2 * i)  # 退避：负载高峰连发空输出时给 Ollama 喘息
        # 全败：降级输出（不阻塞全链）
        if degraded is not None:
            degraded.append(node["node_key"])
        if raw_out is not None:
            raw_out[node["node_key"]] = '{"degraded": true}'
        return '{"degraded": true}'

    @staticmethod
    def _parse(text: str, degraded: list[str], key: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
            if key not in degraded:
                degraded.append(key + ":parse")
            return {"degraded": True, "raw": text[:200]}
