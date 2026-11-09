# -*- coding: utf-8 -*-
"""JEV 知识图谱关系层（Step 2 / 2026-11-09）。

把 Step 1 在 `jev_intel.html` 里已验证的 `compileRelations()` 下沉到服务端：
四谓词把散落在 6 处列表里的证据编译成统一 Relation，UI 才有真正「可走的关系」。

## 职责边界（老田 2026-11-08 裁定，跨会话有效）

JEV 不是爬虫，是数据的使用方。本模块：

* **零网络调用**：不 import `xtcloud_client` / `requests` / `urllib` / `socket`，
  不调 `jev_graph.build()`。输入只有两类：① 已算好的 graph 响应 dict（纯内存）
  ② 本地快照文件。采集频率/源/反爬都不进这里。
* **只读、无状态、可整体替换**。采集工位（`tools/*_fetch.py`）负责产出快照，
  本层只回答「拿到了说明什么」。
* **全量成分从快照读**，不受 `network.nodes` 的 `NODE_MAX=60` 截断。截断只是
  视图限制，不是数据限制；题材「电力」218 只成员必须 218/218 都有 IN_BOARD。
* 与 `jev_graph.py` 的物理隔离靠惰性 import：`load_members()` 只借它的
  `load_board()`（纯本地 parquet），绝不触碰带 xtcloud 兜底的 `build()`。

## 四谓词

    IN_BOARD   个股 ∈ 板块 / 事件 → 跨板块共振（双口径，题材允许重叠）
    AT_TIME    一切有时刻证据 → t:HH:MM（priced=false 可查但不参与 Δmin）
    ACTED_VIA  席位 / 资金 / 社交主体 → 个股（净额与买入额严格区分）
    PRICE_MSG  消息 ↔ 价格（lead/hit/echo/cross 归一，delta_min 带符号）

每条 Relation 带 `day / board_kind / board / subj / pred / obj / via / t /
delta_min / ts_kind / priced / evidence{src,key}`；`priced=false` 的证据
（盘前批量公告、社交、收盘态）可查但不参与 Δmin（判例 #25）。

## 跨日纪律（Step 2 验收口径之二）

索引按 `(day, pred)` 复合键，反查默认锁定 KG.day。来源若自带 `day` 且与请求
day 不符，**整源隔离**（不进关系集，只留 warning），杜绝跨日快照串味。
`assert_clean()` 校验每条关系的 day / board_kind 一致、端点零断链、逐源零漏项。
"""
from __future__ import annotations

import json
import os
import re
import threading

PREDS = {
    "IN_BOARD": {"zh": "板块归属 / 共振", "color": "#4bc8e8"},
    "AT_TIME": {"zh": "时空定位", "color": "#a98bff"},
    "ACTED_VIA": {"zh": "席位 / 资金 / 社交", "color": "#f0b64b"},
    "PRICE_MSG": {"zh": "消息 ↔ 价格", "color": "#ff5459"},
}
PRED_KEYS = tuple(PREDS)

ACTOR_LABEL = {
    "flow:main_net": "主力资金 · 全板块", "flow:list_net": "龙虎榜净额",
    "flow:relay": "连板接力梯队", "flow:board_rank": "板块资金排名",
    "flow:divergence": "资金背离样本", "flow:board_ladder": "板块内梯队",
    "social:guba": "股吧人气榜", "social:xq": "雪球人气榜",
    "social:board": "板块社交样本", "social:post_author": "股吧发帖",
    "social:market_top": "全市场热榜",
}

# 快照目录：采集工位将来把 graph 响应落到这里，接口即可完全离线（src=snapshot）。
STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare", "_jev_kg")

_C6_RE = re.compile(r"^[0-9]{6}$")
_LOCK = threading.Lock()
_CACHE = {}
CACHE_TTL_S = 60


# ── 小工具（与 jev_intel.html 的 JS 版逐一对齐） ──────────────────
def c6(x) -> str:
    """600900.SH → 600900；已经是 6 位则原样。"""
    return str(x or "").split(".")[0]


def _hm(s):
    p = str(s or "").split(":")
    if len(p) != 2:
        return None
    try:
        return int(p[0]) * 60 + int(p[1])
    except (TypeError, ValueError):
        return None


def priced_t(hm) -> bool:
    """盘中可定价窗口 09:15..15:05。盘前批量 / 社交 / 收盘态一律 false。"""
    m = _hm(hm)
    return m is not None and 555 <= m <= 905


def num(v, d=2) -> str:
    try:
        if v is None or v == "":
            return "--"
        return ("%.*f" % (d, float(v)))
    except (TypeError, ValueError):
        return "--"


def norm_day(x) -> str:
    """只接受 8 位数字交易日；带横杠的日期一律拒绝（判例 #28 的 `_d8()` 坑）。"""
    s = str(x or "").strip()
    if not re.fullmatch(r"[0-9]{8}", s):
        raise ValueError("day 须为 8 位数字 YYYYMMDD，收到 %r" % (s,))
    return s


def scan_foreign_days(obj, day, path="root", out=None, depth=0):
    """递归收集所有 key=='day' 且与请求 day 不符的路径（跨日脏数据台账）。

    不修改数据：隔离由 `_src()` 对真正的证据源执行，这里只负责把「响应里
    混着别的交易日」这件事显性化，避免静默串味。深度上限防自引用结构。
    """
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        v = obj.get("day")
        if v not in (None, "", day) and str(v) != day:
            out.append("%s.day=%r" % (path, v))
        for k, v2 in obj.items():
            if isinstance(v2, (dict, list)):
                scan_foreign_days(v2, day, path + "." + str(k), out, depth + 1)
    elif isinstance(obj, list):
        for i, v2 in enumerate(obj[:50]):
            if isinstance(v2, (dict, list)):
                scan_foreign_days(v2, day, "%s[%d]" % (path, i), out, depth + 1)
    return out


def _src(obj, name, day, warnings):
    """来源自带 day 且与请求 day 不符 → 整源隔离，只留 warning。"""
    if isinstance(obj, dict):
        d = obj.get("day")
        if d not in (None, "", day) and str(d) != day:
            warnings.append("跨日快照已隔离: %s.day=%r ≠ %s" % (name, d, day))
            return {}
    return obj or {}


def snap_path(day: str, board: str, kind: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", str(board or "auto"))
    return os.path.join(SNAP_DIR, "%s_%s_%s.json" % (norm_day(day), safe, kind))


def load_members(day: str, board: str, kind: str) -> list:
    """板块**全量成分**（只读本地快照，不联网）。

    theme    → `jev_theme.load(day)["by_theme"][board]`（开盘啦概念，允许重叠）
    industry → `jev_graph.load_board(day,"industry")["rows"]` 按 industry 过滤

    这两条路径都只读 parquet / JSON 快照；`load_board` 内部用的是 `_dm_read_retry`
    （本地 datamart），不带 xtcloud 兜底。返回 6 位码、去重、保序。
    快照缺失 / 板块不存在一律返回空列表，由调用方如实标注，不静默编造成员。
    """
    day = norm_day(day)
    if kind not in ("industry", "theme"):
        raise ValueError("kind 只能是 industry / theme")
    if not board:
        return []
    if kind == "theme":
        import jev_theme
        t = jev_theme.load(day)
        if not t.get("has"):
            return []
        out = list(t["by_theme"].get(board) or [])
    else:
        import jev_graph
        B = jev_graph.load_board(day, "industry")
        out = [z["c6"] for z in B["rows"] if z.get("industry") == board]
    seen, res = set(), []
    for c in out:
        k = str(c)[:6]
        if k and k not in seen:
            seen.add(k)
            res.append(k)
    return res


# ── 编译器 ─────────────────────────────────────────────────────
class _Compiler:
    def __init__(self, data, day, board_kind, members):
        self.d = data or {}
        self.day = day
        self.bk = board_kind
        self.members = list(members or [])
        self.warnings = []
        self.rels, self.ents, self.hits = [], {}, {}
        self.by_pred, self.by_code, self.by_time = {}, {}, {}
        self.by_src, self.by_ref, self.by_id = {}, {}, {}
        self.seq = 0
        self._pull()

    def _pull(self):
        """把 graph 响应拆成各来源；跨日源就地隔离。"""
        d = self.d
        top_day = d.get("day")
        if top_day is None:
            raise ValueError("graph 响应缺 day 字段")
        if str(top_day) != self.day:
            raise ValueError("graph 响应 day=%r ≠ 请求 day=%r（拒绝跨日编译）"
                             % (top_day, self.day))
        real_kind = d.get("board_kind") or self.bk
        if str(real_kind) != str(self.bk):
            self.warnings.append(
                "口径回落: 请求 %s，graph 实际 %s（快照缺失时不静默替换）"
                % (self.bk, real_kind))
            self.bk = str(real_kind)

        f = d.get("focus") or {}
        net = d.get("network") or {}
        self.f, self.net = f, net
        self.board = f.get("name") or d.get("board") or ""
        self.nodes = net.get("nodes") or []
        self.edges = net.get("edges") or []
        self.stocks = [n for n in self.nodes if n.get("type") == "stock"]
        self.news = [n for n in self.nodes if n.get("type") == "news"]
        bn = next((n for n in self.nodes if n.get("type") == "board"), None)
        self.board_ref = ((bn or {}).get("id")
                         or ("board:" + self.board))
        self.cross = net.get("cross_summary") or []

        self.timeline = f.get("timeline") or []
        self.ev_items = (f.get("events") or {}).get("items") or []
        self.disc = (f.get("disclosures") or {}).get("items") or []
        self.fl = _src(f.get("flow"), "focus.flow", self.day, self.warnings) or {}

        ch = _src(d.get("chain"), "chain", self.day, self.warnings) or {}
        so = _src(d.get("social"), "social", self.day, self.warnings) or {}
        self.ladder = ch.get("ladder") or []
        self.dist = ch.get("distribution") or []
        self.board_items = (ch.get("board") or {}).get("items") or []
        self.hot = so.get("hot") or []
        self.posts = so.get("posts") or []
        self.mtop = so.get("market_top") or []
        for p in scan_foreign_days(d, self.day):
            if p != "root.day":
                self.warnings.append("跨日字段: " + p)

    # ── 原语 ──
    def ent(self, rid, typ, label=None, extra=None):
        if not rid:
            return None
        k = str(rid)
        if k not in self.ents:
            self.ents[k] = {"id": k, "type": typ,
                            "label": label or k, "extra": extra or {}}
        elif label and self.ents[k]["label"] == k:
            self.ents[k]["label"] = label
        return k

    def hit(self, src, key):
        self.hits.setdefault(src, set()).add(str(key))

    def rel(self, o):
        self.seq += 1
        r = {
            "id": "r%d" % self.seq, "day": self.day, "board_kind": self.bk,
            "board": self.board, "subj": None, "subj_type": "entity",
            "pred": "", "obj": None, "obj_type": "entity", "via": "",
            "t": None, "delta_min": None, "ts_kind": None, "priced": True,
            "label": "", "weight": 1, "scope": "focus",
            "evidence": {"src": "", "key": ""},
        }
        r.update(o)
        r["refs"] = [str(x) for x in (r["subj"], r["obj"]) if x]
        if r["subj"]:
            self.ent(r["subj"], r["subj_type"], ACTOR_LABEL.get(r["subj"]))
        if r["obj"]:
            self.ent(r["obj"], r["obj_type"], ACTOR_LABEL.get(r["obj"]))
        self.rels.append(r)
        self.by_id[r["id"]] = r
        self.by_pred.setdefault((self.day, r["pred"]), []).append(r["id"])
        self.by_src.setdefault((self.day, r["evidence"]["src"]), []).append(r["id"])
        if r["t"]:
            self.by_time.setdefault((self.day, str(r["t"])), []).append(r["id"])
        for k in r["refs"]:
            self.by_ref.setdefault((self.day, k), []).append(r["id"])
            if _C6_RE.match(c6(k)):
                self.by_code.setdefault((self.day, c6(k)), []).append(r["id"])
        self.hit(r["evidence"]["src"], r["evidence"]["key"])
        return r

    def nm(self, code, fallback=None):
        c = c6(code)
        for n in self.stocks:
            if c6(n.get("id")) == c:
                return n.get("label")
        return fallback or c

    # ── 主流程 ──
    def compile(self) -> "KG":
        self.ent(self.board_ref, "board", self.board,
                 {"n": self.f.get("n"), "med": self.f.get("med")})
        for n in self.stocks:
            self.ent(n.get("id"), "stock", n.get("label"),
                     {"pct": n.get("pct"), "limit_up": n.get("limit_up")})
        for n in self.news:
            self.ent(n.get("id"), "event", n.get("label"),
                     {"hm": n.get("hm"), "ts_kind": n.get("ts_kind")})

        self._p1_in_board()
        self._p2_at_time()
        self._p3_acted_via()
        self._p4_price_msg()
        cover = self._coverage()
        return KG(self.day, self.board, self.bk, self.rels, self.ents,
                  {"by_pred": self.by_pred, "by_code": self.by_code,
                   "by_time": self.by_time, "by_src": self.by_src,
                   "by_ref": self.by_ref, "by_id": self.by_id},
                  cover, self.warnings,
                  self.members_stat)

    def _p1_in_board(self):
        for n in self.stocks:
            self.rel({
                "subj": n.get("id"), "subj_type": "stock", "pred": "IN_BOARD",
                "obj": self.board_ref, "obj_type": "board", "via": "member",
                "label": "%s ∈ %s" % (n.get("label"), self.board),
                "evidence": {"src": "network.nodes.stock", "key": n.get("id")},
            })
        for x in self.cross:
            eid = "event:" + str(x.get("event_id") or x.get("hm"))
            for b in (x.get("boards") or []):
                self.ent("board:" + b, "board", b)
                self.ent(eid, "event", "跨板块事件 %s" % x.get("hm"))
                self.rel({
                    "subj": eid, "subj_type": "event", "pred": "IN_BOARD",
                    "obj": "board:" + b, "obj_type": "board",
                    "via": "cross_event", "t": x.get("hm"),
                    "ts_kind": "intraday", "label": "共振板块 %s" % b,
                    "evidence": {"src": "network.cross_summary",
                                 "key": x.get("event_id") or x.get("hm")},
                })

        # ── 全量成分补齐：NODE_MAX=60 只是视图截断，不能当数据边界 ──
        node_c6 = {c6(n.get("id")) for n in self.stocks}
        in_nodes = member_only = 0
        for raw in self.members:
            c = str(raw)[:6]
            if not c:
                continue
            if c in node_c6:
                in_nodes += 1       # 已由 network.nodes.stock 生成 IN_BOARD
                continue
            member_only += 1
            self.ent(c, "stock", self.nm(c), {"member_only": True})
            self.rel({
                "subj": c, "subj_type": "stock", "pred": "IN_BOARD",
                "obj": self.board_ref, "obj_type": "board",
                "via": "member_only", "scope": "member_only",
                "label": "%s ∈ %s（全量成分，无行情节点）" % (self.nm(c), self.board),
                "evidence": {"src": "snapshot.members", "key": c},
            })
        self.members_stat = {
            "total": len(self.members), "in_nodes": in_nodes,
            "member_only": member_only,
            "covered": in_nodes + member_only,
            "snapshot_total": len(self.members),
            "graph_focus_n": self.f.get("n"),
            "view_nodes": len(node_c6),
            "truncated_by_view": member_only > 0,
        }

    def _p2_at_time(self):
        for i, x in enumerate(self.timeline):
            t = x.get("t")
            self.ent("t:" + str(t), "time", t)
            self.rel({
                "subj": x.get("code"), "subj_type": "stock", "pred": "AT_TIME",
                "obj": "t:" + str(t), "obj_type": "time",
                "via": "timeline_" + str(x.get("kind") or "price"),
                "t": t, "ts_kind": "price" if x.get("kind") == "price" else "intraday",
                "priced": True, "label": "%s · %s" % (x.get("name"), x.get("text")),
                "evidence": {"src": "focus.timeline",
                             "key": "%s@%s#%d" % (x.get("code"), t, i)},
            })
        for n in self.stocks:
            st = n.get("seal_time")
            if not st:
                continue
            self.ent("t:" + str(st), "time", st)
            self.rel({
                "subj": n.get("id"), "subj_type": "stock", "pred": "AT_TIME",
                "obj": "t:" + str(st), "obj_type": "time", "via": "seal",
                "t": st, "ts_kind": "price", "priced": True,
                "label": "%s 封板" % n.get("label"),
                "evidence": {"src": "network.nodes.seal", "key": n.get("id")},
            })
        for x in self.ev_items:
            tk = x.get("ts_kind") or "batch"
            px = tk == "intraday" and priced_t(x.get("hm"))
            self.ent(x.get("id"), "event", str(x.get("title") or "")[:20],
                     {"ts_kind": tk})
            self.ent("t:" + str(x.get("hm")), "time", x.get("hm"))
            self.rel({
                "subj": x.get("id"), "subj_type": "event", "pred": "AT_TIME",
                "obj": "t:" + str(x.get("hm")), "obj_type": "time",
                "via": "event_" + tk, "t": x.get("hm"), "ts_kind": tk,
                "priced": px,
                "label": ("盘中 " if px else "盘前批量 / 不参与 Δmin ")
                         + str(x.get("title") or "")[:24],
                "evidence": {"src": "focus.events.items", "key": x.get("id")},
            })
        for i, x in enumerate(self.disc):
            eid = "disc:%s:%d" % (c6(x.get("code")), i)
            self.ent(eid, "event", str(x.get("title") or "")[:20],
                     {"kind": x.get("kind"), "url": x.get("url")})
            self.ent("t:00:00", "time", "00:00")
            self.rel({
                "subj": eid, "subj_type": "event", "pred": "AT_TIME",
                "obj": "t:00:00", "obj_type": "time",
                "via": x.get("kind") or "notice", "t": "00:00",
                "ts_kind": "batch", "priced": False,
                "label": "%s · %s" % (x.get("name") or c6(x.get("code")),
                                      str(x.get("title") or "")[:24]),
                "evidence": {"src": "focus.disclosures.items", "key": eid},
            })
            self.rel({
                "subj": eid, "subj_type": "event", "pred": "ACTED_VIA",
                "obj": x.get("code"), "obj_type": "stock", "via": "disclosure",
                "ts_kind": "batch", "priced": False,
                "label": "披露主体 %s" % (x.get("name") or c6(x.get("code"))),
                "evidence": {"src": "focus.disclosures.items", "key": eid},
            })
        for i, x in enumerate(self.posts):
            t = str(x.get("ts") or "")[11:16]
            eid = "post:%s:%d" % (c6(x.get("code")), i)
            self.ent(eid, "event", str(x.get("title") or "")[:20])
            self.ent("t:" + t, "time", t)
            self.rel({
                "subj": eid, "subj_type": "event", "pred": "AT_TIME",
                "obj": "t:" + t, "obj_type": "time", "via": "post",
                "t": t or None, "ts_kind": "social", "priced": False,
                "label": "%s 吧 · %s" % (x.get("name") or c6(x.get("code")),
                                        str(x.get("title") or "")[:22]),
                "evidence": {"src": "social.posts", "key": eid},
            })
        for n in self.news:
            self.ent("t:" + str(n.get("hm")), "time", n.get("hm"))
            self.rel({
                "subj": n.get("id"), "subj_type": "event", "pred": "AT_TIME",
                "obj": "t:" + str(n.get("hm")), "obj_type": "time",
                "via": "news_node_" + str(n.get("ts_kind") or "batch"),
                "t": n.get("hm"), "ts_kind": n.get("ts_kind") or "batch",
                "priced": (n.get("ts_kind") == "intraday"
                           and priced_t(n.get("hm"))),
                "label": str(n.get("label") or "")[:26],
                "evidence": {"src": "network.nodes.news", "key": n.get("id")},
            })

    def _p3_acted_via(self):
        code_of = lambda x: x.get("code") or x.get("c6") or ""
        for x in self.ladder:
            cd = code_of(x)
            self.ent(cd, "stock", x.get("name"))
            if x.get("top_buyer"):
                self.ent("seat:" + str(x["top_buyer"]), "seat", x["top_buyer"])
                self.rel({
                    "subj": "seat:" + str(x["top_buyer"]), "subj_type": "seat",
                    "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                    "via": "top_buyer", "t": "15:00", "ts_kind": "close",
                    "priced": False, "weight": x.get("top_buyer_amt"),
                    "label": "买方席位 %s %s 万" % (
                        x["top_buyer"], num(x.get("top_buyer_amt"), 0)),
                    "evidence": {"src": "chain.ladder", "key": cd},
                })
            if x.get("top_seller"):
                self.ent("seat:" + str(x["top_seller"]), "seat", x["top_seller"])
                self.rel({
                    "subj": "seat:" + str(x["top_seller"]), "subj_type": "seat",
                    "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                    "via": "top_seller", "t": "15:00", "ts_kind": "close",
                    "priced": False, "weight": x.get("top_seller_amt"),
                    "label": "卖方席位 %s %s 万" % (
                        x["top_seller"], num(x.get("top_seller_amt"), 0)),
                    "evidence": {"src": "chain.ladder", "key": cd},
                })
            self.rel({
                "subj": "flow:relay", "subj_type": "actor", "pred": "ACTED_VIA",
                "obj": cd, "obj_type": "stock", "via": "relay", "t": "15:00",
                "ts_kind": "close", "priced": False, "weight": x.get("nums") or 1,
                "label": "%s %s板 · %s · 榜净 %s万" % (
                    x.get("name"), x.get("nums"), x.get("style_label"),
                    num(x.get("list_net"), 0)),
                "evidence": {"src": "chain.ladder", "key": cd},
            })
        for x in self.dist:
            cd = code_of(x)
            self.ent(cd, "stock", x.get("name"))
            self.rel({
                "subj": "flow:list_net", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                "via": "distribution", "t": "15:00", "ts_kind": "close",
                "priced": False, "weight": x.get("net"),
                "label": "%s 龙虎榜净额 %s万（%s / %s）" % (
                    x.get("name"), num(x.get("net"), 0), x.get("span"),
                    "、".join(x.get("reasons") or [])),
                "evidence": {"src": "chain.distribution", "key": cd},
            })
        for x in self.board_items:
            cd = code_of(x)
            self.rel({
                "subj": "flow:board_ladder", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                "via": "board_ladder", "t": "15:00", "ts_kind": "close",
                "priced": False, "label": "%s 板块内梯队" % (x.get("name") or cd),
                "evidence": {"src": "chain.board.items",
                             "key": cd or x.get("name")},
            })
        for n in self.stocks:
            self.rel({
                "subj": "flow:main_net", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": n.get("id"), "obj_type": "stock",
                "via": "main_net", "t": "15:00", "ts_kind": "close",
                "priced": False, "weight": n.get("main_net"),
                "label": "%s 主力净 %s万 / 全档净 %s万" % (
                    n.get("label"), num(n.get("main_net"), 0),
                    num(n.get("net_mf"), 0)),
                "evidence": {"src": "network.nodes.fund", "key": n.get("id")},
            })
        ranked = list(self.fl.get("top_in") or []) + list(self.fl.get("top_out") or [])
        for i, x in enumerate(ranked):
            cd = next((n.get("id") for n in self.stocks
                       if n.get("label") == x.get("name")), None) or ("name:" + str(x.get("name")))
            self.rel({
                "subj": "flow:board_rank", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                "via": "flow_rank", "t": "15:00", "ts_kind": "close",
                "priced": False, "weight": x.get("main_net"),
                "label": "%s 主力净 %s万" % (x.get("name"), num(x.get("main_net"), 0)),
                "evidence": {"src": "focus.flow.top_in/out",
                             "key": "%s#%d" % (x.get("name"), i)},
            })
        for i, nmx in enumerate(self.fl.get("divergence_names") or []):
            cd = next((n.get("id") for n in self.stocks
                       if n.get("label") == nmx), None) or ("name:" + str(nmx))
            self.rel({
                "subj": "flow:divergence", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                "via": "divergence", "t": "15:00", "ts_kind": "close",
                "priced": False, "label": "%s 主力净买 / 全档净流出（背离）" % nmx,
                "evidence": {"src": "focus.flow.divergence_names",
                             "key": "%s#%d" % (nmx, i)},
            })
        for x in self.hot:
            cd = code_of(x)
            self.ent(cd, "stock", x.get("name"))
            if x.get("guba_rank") is not None:
                self.rel({
                    "subj": "social:guba", "subj_type": "actor",
                    "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                    "via": "guba_rank", "ts_kind": "social", "priced": False,
                    "weight": x.get("posts"),
                    "label": "%s 股吧第 %s 名 / %s 帖" % (
                        x.get("name"), x.get("guba_rank"), x.get("posts") or 0),
                    "evidence": {"src": "social.hot", "key": cd},
                })
            if x.get("xq_hot_rank") is not None:
                self.rel({
                    "subj": "social:xq", "subj_type": "actor",
                    "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                    "via": "xq_rank", "ts_kind": "social", "priced": False,
                    "weight": x.get("xq_hot"),
                    "label": "%s 雪球人气第 %s 名" % (
                        x.get("name"), x.get("xq_hot_rank")),
                    "evidence": {"src": "social.hot", "key": cd},
                })
            if x.get("guba_rank") is None and x.get("xq_hot_rank") is None:
                self.rel({
                    "subj": "social:board", "subj_type": "actor",
                    "pred": "ACTED_VIA", "obj": cd, "obj_type": "stock",
                    "via": "hot_other", "ts_kind": "social", "priced": False,
                    "label": "%s 社交样本" % x.get("name"),
                    "evidence": {"src": "social.hot", "key": cd},
                })
        for i, x in enumerate(self.mtop):
            self.ent(x.get("code"), "stock", x.get("name"))
            self.rel({
                "subj": "social:market_top", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": x.get("code"), "obj_type": "stock",
                "via": "market_hot", "ts_kind": "social", "priced": False,
                "scope": "market",
                "label": "%s 雪球第 %s / 股吧第 %s" % (
                    x.get("name"), x.get("xq_hot_rank") or "-",
                    x.get("guba_rank") or "-"),
                "evidence": {"src": "social.market_top",
                             "key": "%s#%d" % (x.get("code"), i)},
            })
        for i, x in enumerate(self.posts):
            self.ent(x.get("code"), "stock", x.get("name") or c6(x.get("code")))
            self.rel({
                "subj": "social:post_author", "subj_type": "actor",
                "pred": "ACTED_VIA", "obj": x.get("code"), "obj_type": "stock",
                "via": "post", "t": str(x.get("ts") or "")[11:16] or None,
                "ts_kind": "social", "priced": False,
                "label": "%s 吧 %s" % (x.get("name") or c6(x.get("code")),
                                      str(x.get("title") or "")[:20]),
                "evidence": {"src": "social.posts",
                             "key": "post:%s:%d" % (c6(x.get("code")), i)},
            })

    def _p4_price_msg(self):
        for i, e in enumerate(self.edges):
            tk = e.get("type")
            dm = e.get("lag_min")
            if dm is None:
                dm = e.get("gap_min")
            frm, to = e.get("from"), e.get("to")
            self.rel({
                "subj": frm,
                "subj_type": "stock" if c6(frm) != str(frm) else "event",
                "pred": "PRICE_MSG", "obj": to,
                "obj_type": "stock" if _C6_RE.match(c6(to)) else "event",
                "via": "same_wave" if e.get("same_wave") else tk,
                "t": e.get("hm"), "delta_min": dm,
                "ts_kind": e.get("ts_kind")
                           or ("intraday" if tk == "cross" else "price"),
                "priced": tk in ("echo", "hit", "cross"),
                "label": e.get("label") or tk,
                "evidence": {"src": "network.edges",
                             "key": "%s>%s#%d" % (frm, to, i)},
            })
        for x in self.cross:
            eid = "event:" + str(x.get("event_id") or x.get("hm"))
            self.ent(eid, "event", "跨板块事件 %s" % x.get("hm"))
            self.rel({
                "subj": eid, "subj_type": "event", "pred": "PRICE_MSG",
                "obj": "t:" + str(x.get("hm")), "obj_type": "time",
                "via": "cross_cluster", "t": x.get("hm"),
                "ts_kind": "intraday", "priced": True,
                "label": "%s · 跨 %s 板块 / %s 边" % (
                    x.get("label"), x.get("n_boards") or 0, x.get("n_edges") or 0),
                "evidence": {"src": "network.cross_summary",
                             "key": x.get("event_id") or x.get("hm")},
            })

    def _coverage(self):
        """逐源核对，显式列出未被任何谓词捕获的证据（Step 1 的 17 源 + 全量成员）。"""
        rank = list(self.fl.get("top_in") or []) + list(self.fl.get("top_out") or [])
        cross_keys = [x.get("event_id") or x.get("hm") for x in self.cross]
        sources = [
            ("network.nodes.stock", self.stocks,
             [n.get("id") for n in self.stocks]),
            ("network.nodes.news", self.news,
             [n.get("id") for n in self.news]),
            ("network.nodes.seal", [n for n in self.stocks if n.get("seal_time")],
             [n.get("id") for n in self.stocks if n.get("seal_time")]),
            ("network.nodes.fund", self.stocks,
             [n.get("id") for n in self.stocks]),
            ("network.edges", self.edges,
             ["%s>%s#%d" % (e.get("from"), e.get("to"), i)
              for i, e in enumerate(self.edges)]),
            ("network.cross_summary", self.cross, cross_keys),
            ("focus.timeline", self.timeline,
             ["%s@%s#%d" % (x.get("code"), x.get("t"), i)
              for i, x in enumerate(self.timeline)]),
            ("focus.events.items", self.ev_items,
             [x.get("id") for x in self.ev_items]),
            ("focus.disclosures.items", self.disc,
             ["disc:%s:%d" % (c6(x.get("code")), i)
              for i, x in enumerate(self.disc)]),
            ("focus.flow.top_in/out", rank,
             ["%s#%d" % (x.get("name"), i) for i, x in enumerate(rank)]),
            ("focus.flow.divergence_names", self.fl.get("divergence_names") or [],
             ["%s#%d" % (x, i) for i, x in enumerate(
                 self.fl.get("divergence_names") or [])]),
            ("chain.ladder", self.ladder,
             [x.get("code") or x.get("c6") or "" for x in self.ladder]),
            ("chain.distribution", self.dist,
             [x.get("code") or x.get("c6") or "" for x in self.dist]),
            ("chain.board.items", self.board_items,
             [(x.get("code") or x.get("c6") or x.get("name"))
              for x in self.board_items]),
            ("social.hot", self.hot,
             [x.get("code") or x.get("c6") or "" for x in self.hot]),
            ("social.posts", self.posts,
             ["post:%s:%d" % (c6(x.get("code")), i)
              for i, x in enumerate(self.posts)]),
            ("social.market_top", self.mtop,
             ["%s#%d" % (x.get("code"), i) for i, x in enumerate(self.mtop)]),
        ]
        out = []
        for name, rows, keys in sources:
            got = self.hits.get(name, set())
            miss = [k for k in keys if str(k) not in got]
            out.append({"src": name, "total": len(rows),
                        "hit": len(rows) - len(miss), "miss": len(miss),
                        "sample": [str(k) for k in miss[:3]]})
        if self.members:
            got = self.hits.get("snapshot.members", set())
            node_c6 = {c6(n.get("id")) for n in self.stocks}
            # in_nodes 成员由 network.nodes.stock 覆盖，不算漏；
            # member_only 必须逐条有 snapshot.members 命中。
            need = [str(c)[:6] for c in self.members
                    if str(c)[:6] not in node_c6]
            miss = [c for c in need if c not in got]
            out.append({"src": "snapshot.members", "total": len(self.members),
                        "hit": len(self.members) - len(miss), "miss": len(miss),
                        "sample": miss[:3]})
        return out


# ── 查询结果 ───────────────────────────────────────────────────
class KG:
    """可反查的关系集。所有索引按 (day, key) 复合，反查默认锁定当日。"""

    def __init__(self, day, board, board_kind, rels, ents, idx, cover,
                 warnings, members_stat):
        self.day, self.board, self.board_kind = day, board, board_kind
        self.rels, self.ents, self.idx = rels, ents, idx
        self.cover, self.warnings = cover, warnings
        self.members_stat = members_stat
        self.preds = PREDS

    def _ids(self, table, key, day):
        day = day or self.day
        return self.idx.get(table, {}).get((str(day), str(key)), [])

    def of_pred(self, pred, day=None):
        return [r for rid in self._ids("by_pred", pred, day)
                if (r := self._by_id(rid))]

    def of_code(self, code, day=None):
        return [r for rid in self._ids("by_code", c6(code), day)
                if (r := self._by_id(rid))]

    def of_time(self, t, day=None):
        return [r for rid in self._ids("by_time", t, day)
                if (r := self._by_id(rid))]

    def neighbors(self, ref, day=None):
        out = [r for rid in self._ids("by_ref", ref, day)
               if (r := self._by_id(rid))]
        if not out and _C6_RE.match(str(ref)):
            # 6 位码是反查键、不是实体 id（实体可能写成 600900.SH）。
            # 点成员节点时按 code 兜底，避免「走关系」在这里断掉。
            seen = set()
            for r in self.of_code(ref, day):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    out.append(r)
        return out

    def _by_id(self, rid):
        return self.idx.get("by_id", {}).get(rid)

    def count(self, pred, day=None):
        return len(self.of_pred(pred, day))

    def total_miss(self):
        return sum(x["miss"] for x in self.cover)

    def dangling(self):
        return [r["id"] for r in self.rels
                if any(str(k) not in self.ents for k in r["refs"])]

    @property
    def clean(self):
        return (not self.warnings and not self.dangling()
                and self.total_miss() == 0)

    def assert_clean(self, strict=True):
        bad = [r["id"] for r in self.rels
               if r["day"] != self.day or r["board_kind"] != self.board_kind]
        assert not bad, "关系 day/board_kind 串味: %s" % bad[:3]
        assert not self.dangling(), "断链端点: %s" % self.dangling()[:3]
        assert self.total_miss() == 0, "证据漏项: %s" % [
            x for x in self.cover if x["miss"]][:3]
        if strict:
            assert not self.warnings, "跨日/口径告警: %s" % self.warnings[:3]
        return True

    def summary(self):
        return {
            "day": self.day, "board": self.board,
            "board_kind": self.board_kind,
            "n_rel": len(self.rels), "n_ent": len(self.ents),
            "pred_counts": {p: self.count(p) for p in PRED_KEYS},
            "total_miss": self.total_miss(),
            "dangling": len(self.dangling()),
            "clean": self.clean, "warnings": list(self.warnings),
            "members": self.members_stat,
        }

    def to_dict(self, pred=None, code=None):
        rels = self.rels
        if pred:
            rels = [r for r in rels if r["pred"] == pred]
        if code:
            k = c6(code)
            rels = [r for r in rels
                    if any(c6(x) == k for x in r["refs"])]
        used = {str(k) for r in rels for k in r["refs"]}
        return {
            "rels": rels,
            "ents": {k: v for k, v in self.ents.items() if k in used},
            "idx_counts": {
                "by_pred": {p: len(self._ids("by_pred", p, None))
                            for p in PRED_KEYS},
                "by_code": len(self.idx.get("by_code", {})),
                "by_time": len(self.idx.get("by_time", {})),
                "by_src": len(self.idx.get("by_src", {})),
            },
        }


# ── 对外入口 ───────────────────────────────────────────────────
def compile_relations(data, day=None, board_kind="industry", members=None) -> KG:
    """纯编译：graph 响应 dict → KG。零 IO、零网络。

    day 缺省取 data["day"]；显式给出时两者必须一致，否则拒绝跨日编译。
    members 传全量成分（6 位码列表）时，会为被 NODE_MAX 截断的成员补
    IN_BOARD，保证 218/218。
    """
    d = data or {}
    req = norm_day(day if day is not None else d.get("day"))
    return _Compiler(d, req, str(board_kind or "industry"), members).compile()


def from_snapshot(path: str, members=None) -> KG:
    """只读一份落盘 graph 响应 JSON（采集工位的产物）→ KG，完全离线。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return compile_relations(data, members=members)


def read_graph_snapshot(day, board, kind) -> dict:
    """读 `_jev_kg/<day>_<board>_<kind>.json`；不存在抛 FileNotFoundError。"""
    p = snap_path(day, board, kind)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def clear_cache():
    with _LOCK:
        _CACHE.clear()
