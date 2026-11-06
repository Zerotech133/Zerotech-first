#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断言图构建器（P0-C）：把 assertion jsonschema 的四要素落到图上，验证新谓词能建、能查。

设计约束
--------
1. **只建影子库**，不写 `95_KG/kg/graph_cache.db`。生产图合并需单独授权。
2. 表结构与生产图**完全兼容**（nodes / edges 同列、同 node_id 前缀约定），
   便于将来 `ATTACH` 或 INSERT ... SELECT 合并，无需改下游查询。
3. 每个新谓词都记 `derived` 标记：`model` 表示模型直接产出，`rule` 表示本脚本按规则推断。
   **推断出来的边不得冒充模型产出**——这条是血统要求，与 source_split 的 is_authoritative 同源。

节点类型（新增 4 类）
--------------------
Assertion   一条断言本体。ID = `Assertion:{assertion_id}`
Condition   触发条件。ID = `Condition:{sha1(去标点小写文本)[:12]}`，**跨断言内容寻址去重**
Action      动作。     ID = `Action:{verb}:{sha1(text)[:12]}`
Target      标的。     ID = `Target:{scope}`（枚举值，天然收敛）

Signal 节点复用 v1 命名空间但另开 `assert:` 源，避免与 yt 语料的 Signal 混淆。

边（7 条）
----------
ASSERTION_HAS_CONDITION   Assertion -> Condition
ASSERTION_HAS_ACTION      Assertion -> Action
ASSERTION_APPLIES_TO      Assertion -> Target
CONDITION_TRIGGERS_ACTION Condition -> Action      ← 核心：跨断言汇聚
ASSERTION_CONTRADICTS     Assertion -> Assertion   ← 对称，只建一条
ASSERTION_REFERS_TO       Assertion -> Signal
ASSERTION_REQUIRES        Assertion -> Assertion   ← 规则推断，derived=rule

用法
----
python assertion_graph.py build --input _assertions/precheck/pass.jsonl --db 95_KG/kg/_assertion_graph.db
python assertion_graph.py query --db 95_KG/kg/_assertion_graph.db
python assertion_graph.py query --db ... --q Q1 --arg "跌破"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id    TEXT PRIMARY KEY,
    node_type  TEXT NOT NULL,
    label      TEXT,
    language   TEXT,
    confidence REAL,
    extra      TEXT
);
CREATE TABLE IF NOT EXISTS edges (
    edge_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_table TEXT NOT NULL,
    rel        TEXT NOT NULL,
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    extra      TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel);
CREATE TABLE IF NOT EXISTS build_meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

# 动作族：verb → family。与 assertion_schema.json 的 verbs 分族保持一致。
VERB_FAMILY = {
    "buy": "entry", "add_position": "entry", "open_position": "entry", "scale_in": "entry",
    "sell": "exit", "reduce_position": "exit", "close_position": "exit", "scale_out": "exit",
    "stop_loss": "protect", "take_profit": "protect", "hedge": "protect",
    "hold": "wait", "wait": "wait", "no_action": "wait",
    "screen": "select", "filter": "select", "rank": "select",
    "size_position": "size", "cap_exposure": "size",
    "avoid": "forbid", "prohibit": "forbid",
    "identify": "judge", "confirm": "judge", "classify": "judge", "validate": "judge",
    "other": "other",
}

# 前提语标记：出现这些词的条件，视为依赖另一条断言（规则推断 REQUIRES）
# 前提语：只收**真正表示逻辑前置**的说法。
# 第一版收了「先」「之前」「需要」，结果 11 条 REQUIRES 全部指向同一条断言：
# 因为 scope 大多是 any，任何含「先」「之前」的条件（"先抓确定的结构规律"、
# "进场之前"、"先看是否愿意长期持有"）都落到同一个池子里，池里 action 最长的
# 那条被反复挂上，跟话题完全无关。收紧词表 + 加相关性约束两处一起改。
PREREQ_RE = re.compile(r"前提|基础上|才谈得上|才可以|必须先|必须已经|成立之后|之后才能|先决条件")
PUNCT_RE = re.compile(r"[\s，。、；：！？,.;:!?\"'（）()《》【】\[\]—\-~·]+")

# 否定词：极性对立检测专用。"暂时/先"这类程度词一并归入，因为
# "暂时不止损" 与 "止损" 的对立正是靠识别"暂时不"这个整体来判的。
NEG_RE = re.compile(r"(暂时不|暂时先不|先不|不需要|不用|不要|不能|不会|不宜|不该|不必|"
                    r"不|别|勿|禁止|避免|切勿|无须|无需)")

# 动作语义提示：极性对立两侧都必须命中，用来排除"术语 vs 识别术语"这种假冲突。
# 第一版没加这条，"盘整背驰" 对上 "识别盘整背驰" 也被判成了冲突。
ACT_HINT = re.compile(
    r"(买|卖|减|加|持|抛|止|建|清|换|接|等|看|做|走|"
    r"识别|判定|分类|确认|验证|避免|禁止|止损|止盈|持仓|减仓|加仓)")


def norm_key(text: str) -> str:
    """条件去重键：去标点、去空白、小写。语义相同的条件收敛到同一节点。"""
    return PUNCT_RE.sub("", (text or "").strip().lower())


def same_source(a: dict, b: dict) -> bool:
    """同段 / 同证据跳过：同一句原文抽出的两条断言不构成跨断言冲突或依赖。

    依据（2026-11-06 全量 2715 条复核）：规则推断 8 条全假阳性，其中 3 条
    CONTRADICTS 的根因是**同一句原文被不同 unit 重复引用** ——
    「空单使劲干」vs「空单」(cond_sim=1.0) 是重复抽取，不是知识冲突；
    「第一类买点出现时买入」vs「仍想等安全信号」是同一段里正确做法与错误
    倾向的正反表述，也不是冲突。实测同段内近似重复仅 1 对，重复发生在
    **跨段同 quote**，故单靠 unit_id 不够，必须比归一化 quote。

    9 条 human-confirmed 冲突均为跨博主/跨证据，不受本判据影响（且它们由
    外部注入，不走 infer_*）。宁可漏报不可误报。
    """
    ua, ub = (a.get("unit_id") or "").strip(), (b.get("unit_id") or "").strip()
    if ua and ua == ub:
        return True
    qa, qb = a.get("quote") or "", b.get("quote") or ""
    return bool(qa and qa == qb)


def cond_node_id(text: str) -> str:
    return "Condition:" + hashlib.sha1(norm_key(text).encode("utf-8")).hexdigest()[:12]


def action_node_id(verb: str, text: str) -> str:
    h = hashlib.sha1(norm_key(text).encode("utf-8")).hexdigest()[:12]
    return f"Action:{(verb or 'other').strip()}:{h}"


def signal_node_id(name: str) -> str:
    return "Signal:assert:" + hashlib.sha1(norm_key(name).encode("utf-8")).hexdigest()[:12]


_UNIT_RE = re.compile(r"^learn_(.+)_([0-9a-f]{10})(?:_(\d+))?$")


def project_of(unit_id: str) -> str:
    """从单元 ID 取来源项目名。解析规则与 units_v2 的 ID 契约一致。

    只允许 ``^learn_(.+)_([0-9a-f]{10})(?:_(\\d+))?$``：pid 与 video_key 本身都含 ``_``，
    禁止 ``rsplit('_', 1)``（遇到 ``_2`` 后缀会把序号当哈希）。
    """
    m = _UNIT_RE.match(unit_id or "")
    return m.group(1) if m else ""


class GraphBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA_SQL)
        self.n_nodes = 0
        self.n_edges = 0
        self.edge_counts: Dict[str, int] = {}

    # ---- 基础写入 ----
    def add_node(self, node_id: str, node_type: str, label: str = "",
                 confidence: Optional[float] = None, extra: Optional[dict] = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO nodes(node_id,node_type,label,language,confidence,extra)"
            " VALUES(?,?,?,?,?,?)",
            (node_id, node_type, (label or "")[:400], "zh", confidence,
             json.dumps(extra, ensure_ascii=False) if extra else None),
        )
        self.n_nodes += 1

    def add_edge(self, edge_table: str, rel: str, src: str, dst: str,
                 extra: Optional[dict] = None) -> None:
        self.conn.execute(
            "INSERT INTO edges(edge_table,rel,src,dst,extra) VALUES(?,?,?,?,?)",
            (edge_table, rel, src, dst,
             json.dumps(extra, ensure_ascii=False) if extra else None),
        )
        self.n_edges += 1
        self.edge_counts[rel] = self.edge_counts.get(rel, 0) + 1

    # ---- 一条断言 ----
    def add_assertion(self, a: dict) -> Dict[str, Any]:
        aid = a.get("assertion_id") or ("Assertion:" + hashlib.sha1(
            json.dumps(a, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16])
        nid = aid if aid.startswith("Assertion:") else "Assertion:" + aid

        cond = a.get("condition") or {}
        cond_text = (cond.get("text") if isinstance(cond, dict) else "") or ""
        act = a.get("action") or {}
        verb = (act.get("verb") or "other").strip()
        atext = (act.get("text") or "").strip()
        tgt = a.get("target") or {}
        scope = (tgt.get("scope") or "unknown").strip()
        ttext = (tgt.get("text") or "").strip()
        ev = a.get("evidence") or {}

        # 断言节点：label 用人读的一句话，extra 存全字段供回溯
        label = f"{(cond_text + ' → ') if cond_text else ''}{atext}"
        self.add_node(nid, "Assertion", label, confidence=a.get("confidence"),
                      extra={"kind": a.get("kind"), "verb": verb,
                             "condition": cond_text, "action": atext,
                             "target": ttext, "scope": scope,
                             "rationale": a.get("rationale"),
                             "invalidator": a.get("invalidator"),
                             "unit_id": ev.get("unit_id"),
                             "video_key": ev.get("video_key"),
                             "quote": (ev.get("quote") or "")[:300],
                             "flags": a.get("flags") or [],
                             "precheck": a.get("precheck")})

        created = {"condition": None, "action": None, "target": None, "signals": 0}

        # 条件 → Condition 节点（内容寻址去重，跨断言复用）
        if cond_text:
            cid = cond_node_id(cond_text)
            self.add_node(cid, "Condition", cond_text)
            self.add_edge("ASSERTION_HAS_CONDITION", "HAS_CONDITION", nid, cid,
                          {"derived": "model"})
            created["condition"] = cid
            # 核心边：Condition -TRIGGERS-> Action（同一条件被多条断言共享时自然汇聚）
            if atext:
                a_id = action_node_id(verb, atext)
                self.add_edge("CONDITION_TRIGGERS_ACTION", "TRIGGERS", cid, a_id,
                              {"derived": "model", "from_assertion": nid})

        # 动作 → Action 节点
        if atext:
            a_id = action_node_id(verb, atext)
            fam = VERB_FAMILY.get(verb, "other")
            self.add_node(a_id, "Action", atext, extra={"verb": verb, "family": fam})
            self.add_edge("ASSERTION_HAS_ACTION", "HAS_ACTION", nid, a_id,
                          {"derived": "model"})
            created["action"] = a_id

        # 标的 → Target 节点（枚举收敛）
        tid = f"Target:{scope}"
        self.add_node(tid, "Target", scope)
        self.add_edge("ASSERTION_APPLIES_TO", "APPLIES_TO", nid, tid,
                      {"derived": "model", "target_text": ttext})

        # signals → Signal 节点
        for s in (a.get("signals") or []):
            if not isinstance(s, str) or not s.strip():
                continue
            sid = signal_node_id(s)
            self.add_node(sid, "Signal", s.strip())
            self.add_edge("ASSERTION_REFERS_TO", "REFERS_TO", nid, sid, {"derived": "model"})
            created["signals"] += 1

        # 视频节点：生产图的 Video 是 YouTube ID（--c-sczDq58），中文语料是「日期_时间」
        # （20260829_1637），两套命名空间不相交，直接挂不到生产图上。
        # 故断言图自带 Video 节点、自成完整子图，将来整体 ATTACH 即可，不依赖生产图已有哪些视频。
        # node_id 必须带来源前缀：多个博主都用「日期_时间」做 video_key，裸 key 会串源
        # （乔木 20260829_1637 与北斗 20260829_1637 是两个视频）。label 仍用裸 key 供人读。
        vk = (ev.get("video_key") or "").strip()
        if vk:
            pid = project_of(ev.get("unit_id"))
            vid = f"Video:{pid}:{vk}" if pid else f"Video:{vk}"
            self.add_node(vid, "Video", vk,
                          extra={"project": pid, "video_key": vk})
            self.add_edge("VIDEO_HAS_ASSERTION", "HAS_ASSERTION", vid, nid,
                          {"derived": "model", "unit_id": ev.get("unit_id")})
            created["video"] = vid

        return {"id": nid, "cond_text": cond_text, "action_text": atext,
                "target_text": ttext, "verb": verb, "scope": scope, "created": created,
                # 同段/同证据标记：供 infer_* 跳过「同一句原文被重复引用」的伪冲突。
                # quote 用 norm_key 去标点空白，#1 那种只差逗号的两段原文即可命中。
                "unit_id": (ev.get("unit_id") or "").strip(),
                "quote": norm_key((ev.get("quote") or "")[:300])}

    # ---- 跨断言规则推断 ----
    def infer_requires(self, records: List[dict]) -> int:
        """条件里带前提语（先/前提/基础上…）的断言，挂到同标的的『无条件断言』上。

        这是**规则推断**，边带 derived=rule，不得与模型产出混计。
        推断逻辑：找同 scope 下 condition 为空的断言作为可依赖前提，最多挂 1 条（最相关优先）。
        """
        by_scope: Dict[str, List[dict]] = {}
        for r in records:
            by_scope.setdefault(r["scope"], []).append(r)

        n = 0
        for r in records:
            if not PREREQ_RE.search(r["cond_text"] or ""):
                continue
            pool = [x for x in by_scope.get(r["scope"], [])
                    if x["id"] != r["id"] and not x["cond_text"] and x["action_text"]
                    and not same_source(x, r)]
            # 相关性约束：被依赖方必须与本条**有共同二字组**（谈同一件事）。
            # 不加这条，scope=any 的池子会把一条讲「二类卖点」的断言挂到讲「HBM 封装」的断言上。
            key = _bigrams(r["action_text"] + r["target_text"])
            pool = [x for x in pool
                    if key & _bigrams(x["action_text"] + x["target_text"])]
            if not pool:
                continue
            # 选理由字数最长的那条（信息量最大）
            tgt = max(pool, key=lambda x: len(x["action_text"]))
            self.add_edge("ASSERTION_REQUIRES", "REQUIRES", r["id"], tgt["id"],
                          {"derived": "rule", "why": "condition 含前提语，挂同标的下无条件断言"})
            n += 1
        return n

    def infer_contradicts(self, records: List[dict], sim_th: float = 0.85) -> int:
        """冲突检测：条件高度相似但动作族相反（entry vs exit / forbid vs entry）。

        阈值 0.85 是从预审器 R3 的教训来的：0.6 会把「中枢上方抛出」vs「中枢下方接回」
        这种配对操作误判成冲突。**宁可漏报不可误报。**
        """
        def fam(r): return VERB_FAMILY.get(r["verb"], "other")
        OPPOSITE = {("entry", "exit"), ("exit", "entry"), ("forbid", "entry"), ("entry", "forbid")}

        n = 0
        for i in range(len(records)):
            for j in range(i + 1, len(records)):
                a, b = records[i], records[j]
                if same_source(a, b):
                    continue
                if not a["cond_text"] or not b["cond_text"]:
                    continue
                if (fam(a), fam(b)) not in OPPOSITE:
                    continue
                same = _jaccard(a["cond_text"], b["cond_text"])
                if same >= sim_th:
                    self.add_edge("ASSERTION_CONTRADICTS", "CONTRADICTS",
                                  a["id"], b["id"],
                                  {"derived": "rule", "cond_sim": round(same, 3),
                                   "note": "对称边，只建一条；冲突是高价值知识，不自动消解"})
                    n += 1
        return n

    def infer_polarity(self, records: List[dict]) -> int:
        """极性对立：**同一个动作，一个正说一个反说** → 硬冲突。

        为什么需要这一条（单靠 infer_contradicts 抓不到）
        ----------------------------------------------
        infer_contradicts 要求「条件高度相似 + verb 族相反」。但真实的知识冲突
        常常是同一个动作被一个博主肯定、另一个博主否定：
          · 「暂时不止损」 vs 「跌破就走，必须止损」
        这两条条件不同、verb 可能都是 hold/sell，字面相似度算不出来。
        能算的是**动作字符串本身**：去掉否定词后等价，一个带否定一个不带。

        判据（第一版宽松过一次，8 条全是误报，据此收紧）
        ----------------------------------------------
        1. 两侧动作去否定词后必须**严格相等**。第一版允许"一方包含另一方且长度差
           ≤3"，结果 "线段" vs "构不成线段"（去'不'→'构成线段'）被判冲突 —— 那是
           一个术语定义和一个判定标准，不是冲突。
        2. 两侧都必须含**动作语义**（ACT_HINT）。纯名词节点（"盘整背驰""三类买卖点"）
           直接排除，它们的对撞是"术语 vs 识别术语"，不是矛盾。
        3. 必须一正一反（否定词的有无不同）。
        4. 条件相似度 ≥0.5，或至少一方是无条件断言。
        收紧后宁可漏掉边界案例，也不让假冲突污染冲突表 —— 冲突是高价值知识，
        一条假冲突要花人工去否掉，比漏掉一条真冲突成本高。
        """
        def neg(s: str) -> bool:
            return bool(NEG_RE.search(s or ""))

        n = 0
        for i in range(len(records)):
            for j in range(i + 1, len(records)):
                a, b = records[i], records[j]
                if same_source(a, b):
                    continue
                ta, tb = norm_key(a["action_text"]), norm_key(b["action_text"])
                if not ta or not tb:
                    continue
                fa, fb = NEG_RE.sub("", ta), NEG_RE.sub("", tb)
                if len(fa) < 2 or fa != fb:
                    continue
                if neg(ta) == neg(tb):
                    continue
                if not (ACT_HINT.search(fa) and ACT_HINT.search(fb)):
                    continue
                ca, cb = a["cond_text"], b["cond_text"]
                if ca and cb:
                    sim = _jaccard(ca, cb)
                    if sim < 0.5:
                        continue
                    why = f"条件相似 {sim:.2f}"
                else:
                    why = "一方为无条件原则"
                self.add_edge("ASSERTION_CONTRADICTS", "CONTRADICTS", a["id"], b["id"],
                              {"derived": "rule", "polarity": True, "why": why,
                               "left": a["action_text"], "right": b["action_text"],
                               "note": "极性对立，规则推断；冲突不自动消解，留人工裁定"})
                n += 1
        return n

    def finalize(self, src_file: str, n_input: int) -> None:
        meta = {
            "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_file": os.path.abspath(src_file),
            "n_input_assertions": n_input,
            "n_nodes": self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            "n_edges": self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "note": "影子库，未写入生产 graph_cache.db",
        }
        for k, v in meta.items():
            self.conn.execute("INSERT OR REPLACE INTO build_meta(k,v) VALUES(?,?)",
                              (k, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()
        return meta


def _bigrams(s: str) -> set:
    """字符 bigram 集合（不做 Jaccard，只判「有没有共同二字组」）。"""
    s = norm_key(s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _jaccard(a: str, b: str) -> float:
    """字符 bigram Jaccard。短中文串上比字符集重合更稳。"""
    def grams(s):
        s = norm_key(s)
        return {s[i:i + 2] for i in range(len(s) - 1)} or {s}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ============================ 查询能力验证 ============================

QUERIES = {
    "Q1": ("给定条件关键词，查它触发哪些动作（TRIGGERS）", """
        SELECT c.label AS 条件, act.label AS 动作, act.extra AS 动作元数据, e.extra AS 边元数据
        FROM edges e
        JOIN nodes c   ON c.node_id  = e.src AND c.node_type  = 'Condition'
        JOIN nodes act ON act.node_id = e.dst AND act.node_type = 'Action'
        WHERE e.rel='TRIGGERS' AND c.label LIKE ?
        ORDER BY c.label LIMIT 30
    """),
    "Q2": ("给定标的，查所有规则（APPLIES_TO + HAS_ACTION）", """
        SELECT a.label, a.extra
        FROM edges e
        JOIN nodes a ON a.node_id = e.src AND a.node_type='Assertion'
        WHERE e.rel='APPLIES_TO' AND e.dst = ?
        LIMIT 40
    """),
    "Q3": ("给定动作族，查全部断言（HAS_ACTION + family）", """
        SELECT act.extra AS 动作元数据, a.label
        FROM edges e
        JOIN nodes act ON act.node_id = e.dst AND act.node_type='Action'
        JOIN nodes a   ON a.node_id   = e.src
        WHERE e.rel='HAS_ACTION' AND json_extract(act.extra,'$.family') = ?
        LIMIT 40
    """),
    "Q4": ("给定指标名，查引用它的断言（REFERS_TO）", """
        SELECT s.label, a.label
        FROM edges e
        JOIN nodes s ON s.node_id = e.dst AND s.node_type='Signal'
        JOIN nodes a ON a.node_id = e.src
        WHERE e.rel='REFERS_TO' AND s.label LIKE ?
        LIMIT 30
    """),
    "Q5": ("查全部冲突对（CONTRADICTS）", """
        SELECT a.label, b.label, e.extra
        FROM edges e
        JOIN nodes a ON a.node_id=e.src
        JOIN nodes b ON b.node_id=e.dst
        WHERE e.rel='CONTRADICTS' LIMIT 30
    """),
    "Q6": ("条件被多条断言复用（知识收敛度，无参数）", """
        SELECT c.label AS 条件, COUNT(*) AS 复用次数
        FROM edges e
        JOIN nodes c ON c.node_id=e.src AND c.node_type='Condition'
        WHERE e.rel='TRIGGERS'
        GROUP BY c.node_id HAVING COUNT(*) > 1
        ORDER BY 2 DESC LIMIT 20
    """),
    "Q7": ("查前提依赖链（REQUIRES）", """
        SELECT a.label AS 后件, b.label AS 前提, e.extra
        FROM edges e JOIN nodes a ON a.node_id=e.src JOIN nodes b ON b.node_id=e.dst
        WHERE e.rel='REQUIRES' LIMIT 20
    """),
    "Q8": ("终态查询：条件含 X 且标的为 Y → 做什么（联合筛）", """
        SELECT c.label AS 条件, act.label AS 动作, t.label AS 标的, a.label AS 断言
        FROM edges ec
        JOIN nodes c ON c.node_id=ec.dst AND c.node_type='Condition'
        JOIN nodes a ON a.node_id=ec.src
        JOIN edges ea ON ea.src=a.node_id AND ea.rel='HAS_ACTION'
        JOIN nodes act ON act.node_id=ea.dst
        JOIN edges et ON et.src=a.node_id AND et.rel='APPLIES_TO'
        JOIN nodes t  ON t.node_id=et.dst
        WHERE ec.rel='HAS_CONDITION' AND c.label LIKE ? AND t.node_id = ?
        LIMIT 30
    """),
    "Q9": ("给定视频，查它产出的断言（VIDEO_HAS_ASSERTION）", """
        SELECT COALESCE(json_extract(v.extra, '$.project'), '') || ' ' || v.label AS 视频,
               COUNT(*) AS 断言数, GROUP_CONCAT(a.label, ' ｜ ') AS 断言
        FROM edges e
        JOIN nodes v ON v.node_id=e.src AND v.node_type='Video'
        JOIN nodes a ON a.node_id=e.dst
        WHERE e.rel='HAS_ASSERTION'
        GROUP BY v.node_id ORDER BY 2 DESC LIMIT 10
    """),
}

# 参数按实测数据选：北斗 44 条断言的 scope 全为 any（模型对市场品种保守），
# Signal 里高频的是「中枢/级别/成交量」而非「均线」，故查询参数据此调整。
Q_ARGS = {
    "Q1": ["%跌破%"], "Q2": ["Target:any"], "Q3": ["exit"],
    "Q4": ["%级别%"], "Q5": [], "Q6": [], "Q7": [], "Q8": ["%中枢%", "Target:any"],
    "Q9": [],
}


def run_queries(db: str, only: Optional[str] = None, args: Optional[List[str]] = None) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total_ok = 0
    for qid in (QUERIES if not only else [only]):
        desc, sql = QUERIES[qid]
        params = args if args is not None else Q_ARGS.get(qid, [])
        print(f"\n{'=' * 72}\n{qid}  {desc}\n参数 {params}")
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as e:
            print(f"  [SQL ERROR] {e}")
            continue
        if not rows:
            print("  （0 行）")
            continue
        total_ok += 1
        for r in rows[:8]:
            cells = [str(x)[:60] for x in r]
            print("  · " + " | ".join(cells))
        if len(rows) > 8:
            print(f"  … 共 {len(rows)} 行")
    return total_ok


# ============================ 主流程 ============================

def cmd_build(args) -> int:
    """建图。``--input`` 支持逗号分隔多个 jsonl —— 跨博主冲突检测必须一次建，
    分两次建会得到两张互不相连的子图，CONTRADICTS 永远为 0。"""
    rows = []
    srcs = [x.strip() for x in args.input.split(",") if x.strip()]
    for p in srcs:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    if not rows:
        print("[fail] 输入为空", file=sys.stderr)
        return 1
    print(f"[in] {len(srcs)} 个文件，断言 {len(rows)} 条")

    if os.path.exists(args.db):
        os.remove(args.db)
        print(f"[init] 已清除旧影子库 {args.db}")

    gb = GraphBuilder(args.db)
    recs = []
    for a in rows:
        try:
            recs.append(gb.add_assertion(a))
        except Exception as e:  # noqa: BLE001 单条失败不拖垮整批
            print(f"[warn] 断言建图失败 {a.get('assertion_id')}: {e}", file=sys.stderr)

    # REQUIRES 规则推断默认关闭：上轮 11 条 + 本轮 3 条经人工复核 0/14 成立，
    # 保留 --enable-requires 开关仅供复现/研究，默认不写边。详见交付报告 v6 第 6 节。
    n_req = gb.infer_requires(recs) if getattr(args, "enable_requires", False) else 0
    n_con = gb.infer_contradicts(recs)
    n_pol = gb.infer_polarity(recs)
    meta = gb.finalize(",".join(srcs), len(rows))

    if not getattr(args, "enable_requires", False):
        print("  [gate] REQUIRES 规则推断默认关闭（0/14 命中率，--enable-requires 可启用）")
    print(f"\n=== 建图完成：{args.db} ===")
    for k, v in meta.items():
        print(f"  {k:<22} {v}")
    print("\n=== 边类型计数 ===")
    for rel, n in sorted(gb.edge_counts.items(), key=lambda x: -x[1]):
        print(f"  {rel:<26} {n:>6}")
    print(f"\n  规则推断 REQUIRES {n_req} 条 / CONTRADICTS {n_con} 条"
          f"（+极性对立 {n_pol} 条）（derived=rule，不计入模型产出）")

    print("\n=== 节点类型计数 ===")
    for t, n in gb.conn.execute(
            "SELECT node_type, COUNT(*) FROM nodes GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {t:<14} {n:>6}")
    gb.conn.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="断言图构建器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="从断言 jsonl 建影子图")
    b.add_argument("--input", required=True)
    b.add_argument("--db", required=True)
    b.add_argument("--enable-requires", action="store_true",
                   help="启用规则推断 REQUIRES（默认关闭：两轮人工复核 0/14 成立，只产假边）")
    b.set_defaults(func=cmd_build)

    q = sub.add_parser("query", help="跑能力查询验证")
    q.add_argument("--db", required=True)
    q.add_argument("--q", default=None, help="只跑某一条，如 Q1")
    q.add_argument("--arg", nargs="*", default=None, help="覆盖查询参数")
    q.set_defaults(func=lambda a: 0 if run_queries(a.db, a.q, a.arg) else 1)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
