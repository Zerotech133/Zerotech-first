#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""units_v2 —— 学习源件「按段切分」版知识单元生成器（自包含、默认 dry-run）。

背景
----
旧 pipeline 的 ``learning_ops.draft_units()`` 是**逐行切分**（``for line in
text.splitlines()``）。但视频号转写正文是**一整段没有换行**的长文本，于是
"每条视频 = 1 个 unit = 整篇转写"，实测 unit 字数中位 1,247、最长 4,172，
人工审一张卡要读一篇文章，且断言无法定位。

本模块把整篇转写切成 **≤300 字**的段，一段一断言。

输入结构::

    # 北斗股术1688
    > 共 114 条 ...
    ## 20260823_2151 8月23日晚：解封了...
    - 文件：`....mp4`
    - 时长：9:02
    - 字数：2894
    <正文，一整段无换行>
    ---

硬约束
------
* 只读输入、**默认不落盘**；仅当 ``--write`` 且显式给出 ``--out`` 时才写该文件。
* 不删任何文件、不碰数据库、不 import 本仓库其它模块（纯标准库 + 自带正则）。
* 若拆条产物目录不存在，直接在内存里用正则从原 md 切分，不写中间文件。

切分算法
--------
1. 剥离元数据行（``- 文件：`` / ``- 时长：`` / ``- 字数：``）、``---`` 分隔线、
   ``#`` 标题，得到每个视频的正文（多行则直接拼接，换行计为被丢弃字符）。
2. 按句末标点切句：``。！？；!?;`` 与换行（标点保留在句尾）。
3. 贪心累积成段，**每段 ≤ 300 字**（句粒度累积，因此不会把一句话拦腰截断）。
4. 单句 > 300 字时，先按 ``，、,`` 再切；仍 > 300 字则硬切（按 300 字边界，
   若恰好落在数字中间则向左回退，最多回退 30 字）。
5. 字符完整性：``sum(len(段)) + 丢弃字符数 == len(正文)``，违反即抛错。

字段契约
--------
``unit_id``      **段级 = 内容寻址**（``seq`` 不参与哈希）::

                     text_key = sha1(claim.strip())[:10]
                     unit_id  = "learn_" + pid + "_"
                                + sha1(f"{pid}|{video_key}|{text_key}")[:10]

                 同一段文字无论排第几、``MAX_CHARS`` 怎么调、前面删了几段，ID 恒定；
                 段文本被编辑则 ID 变化（那是新知识）。同视频内文字完全相同的段按出现
                 顺序加 ``_2`` / ``_3`` 后缀消歧（确定性可复现）。
``legacy`` 命名空间 **位置式**，与段级有意不同::

                     legacy_id = "learn_" + pid + "_"
                                 + sha1(f"{pid}|{video_key}|legacy|{seq}")[:10]

                 存量是冻结历史、永不重切，位置锚点对它稳定；段级会重切，故必须按内容
                 寻址。两套语义互不替代，交集为 0。
**ID 解析只允许** ``^learn_(.+)_([0-9a-f]{10})(?:_(\d+))?$``。``pid`` 与 ``video_key``
                 本身含 ``_``，禁止用 ``rsplit('_', 1)`` 取末段当 seq（遇到 ``_2`` 后缀
                 会误判）。``|`` 在拼哈希前禁止出现，否则 ``pid="a|b",vk="c"`` 与
                 ``pid="a",vk="b|c"`` 会同串（``_assert_no_pipe`` 已断言）。
``granularity``  固定 ``"segment"``（与存量整篇 unit 区分）
``text_key``     上述 ``sha1(claim.strip())[:10]``，可直接做内容级反查索引
``unit_class``   **视频级**分型（``class_unit="video"``）：整篇正文跑一次规则 v1，
                 该视频所有段继承同一类别。理由：一条视频是"日复盘"还是"方法论"是
                 视频级属性；逐段分类会把方法论视频里提到"账户"的段误判成 T，
                 也会把日复盘里偶然讲的一句规则误判成 M。
``evidence``     ``{"file": "<video_key>.md", "line": <seq>}``（line 为段在该视频内的
                 1 基序号，仅用于展示/定位，**不参与 ID**）
``pit``          ``video_key`` 的 ``YYYYMMDD_HHMM`` -> ``YYYY-MM-DDTHH:MM:00+08:00``
``expire_at``    继承视频级分型：T 类视频的全部段 = pit + 3 天，其余 ``null``

用法
----
    python tools/kb_corpus/units_v2.py                       # dry-run 报告
    python tools/kb_corpus/units_v2.py --json                # 机器可读报告
    python tools/kb_corpus/units_v2.py --input <md|dir>      # 指定输入
    python tools/kb_corpus/units_v2.py --out x.jsonl --write # 落盘

已知限制
--------
1. **legacy 合并单元按主视频立类（不修，仅记录）**：存量 3 条合并单元
   0002/0045/0063 是按主视频取类的。其中 ``..._0063`` 的主视频
   ``20261001_2021`` 判 U，但其合并成员 ``20261002_2058``（国庆交易系统梳理
   第二篇）判 M —— 该合并单元整体落 U，成员里的 M 内容在 legacy 口径下不可见。
   v2 按 114 条**全量**切分后 ``20261002_2058`` 独立成段并被正确判 M，问题只在
   legacy 口径存在，故不修。
2. **段级 ID 为内容寻址，``seq`` 仅用于展示**：同一段文字无论排第几、``MAX_CHARS``
   怎么调，ID 恒定（实测 300/250/350 三档下 28 个跨档同文本段 ID 全一致；受控样例
   三档 ``(seq, unit_id)`` 全同；删掉中间段后其余段 ID 一个不变，只有 ``seq`` 变）。
   由此：
   - **重切不改变已存在段的 ID**，只改变段的划分粒度：被拆开的段产生新 ID、被合并的
     段旧 ID 消失。实测 300/250/350、截尾、逐条砍半共 117 种变更下，
     "同 ID 不同内容" 恒为 **0**；
   - 段文本**被编辑**（重转写/补录）→ 旧 ID 消失、新 ID 出现，**不复用旧 ID**
     （旧实现的位置式 ID 会复用：同 seq 换内容，已废弃）；
   - 删除某段**会使其后句子重新装箱**，相邻段文本可能变化 → 这些段的 ID 随之变化，
     属"内容变则 ID 变"的正确语义，不是漂移。增量对比请以
     ``(video_key, text_key)`` 为键，不要按行序对位。
3. **merge 的误合并**：存量 0002/0045 的合并依据是同模板开场白（"复盘文章这里
   就不发了…"）而非同内容，0824/0825/0826 实为三个不同交易日。v2 不做任何合并，
   重复内容交由 card 层去重。
4. **同视频内文字完全相同的段，``_N`` 后缀仍与出现顺序相关**：两段文本相同时先出现
   者无后缀、后出现者 ``_2``；若其中一段被编辑成与另一段相同，后缀会重新分配
   （先出现者拿 base，后出现者被推到 ``_2``）。这是本方案唯一残留的"位置相关"，
   影响面极小（当前 622 段中重复段为 0，从未触发）。若将来要求纯内容寻址，可把
   前一段的 ``text_key`` 混入消歧输入。
   注意 ``_N`` 在**段级命名空间**里专指"同视频内第 N 个同文本段"；legacy 映射表
   若也用 ``_2`` 表示 10 hex 位哈希碰撞，属于不同命名空间的不同语义。解析前必须
   先确定 ID 属于段级还是 legacy，不可仅凭后缀判断。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 常量 / 正则

DEFAULT_INPUT = Path(
    "C:/项目/知识库/05_学习源件/视频号学习_北斗股术1688/source/视频号学习_北斗股术1688.md"
)

MAX_CHARS = 300  # 单段字数上限（硬约束）

TZ_CN = timezone(timedelta(hours=8))

ANCHOR_RE = re.compile(r"^## (\d{8})_(\d{4})\s+(.+)$")
# B 型导出（2026-11-06 飞书批次）：`## 2026-10-16 17:11 标题 #标签`
# 与 A 型只是锚点写法不同，正文同样是一段连续文本，故归一到同一套 video_key。
ANCHOR_RE_B = re.compile(r"^## (\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})\s+(.+)$")


def match_anchor(line: str):
    """锚点行 → (video_key, title)；不是锚点返回 None。

    两种写法归一成 `YYYYMMDD_HHMM`：
      A 型 `## 20260829_1637 标题`        —— 视频号批次
      B 型 `## 2026-08-29 16:37 标题 #x`  —— 抖音/飞书批次
    """
    m = ANCHOR_RE.match(line)
    if m:
        return f"{m.group(1)}_{m.group(2)}", m.group(3).strip()
    m = ANCHOR_RE_B.match(line)
    if m:
        y, mo, d, hh, mi, title = m.groups()
        return f"{y}{mo}{d}_{hh}{mi}", title.strip()
    return None


# 元数据行（含 B 型的 `- 1048 字 · 2:35 · 赞 2021` 与 OCR 型的 `- 发布日期：...`）
META_RE = re.compile(
    r"^\s*[-*]\s*(文件|时长|字数|发布日期|来源|原图|作者|抖音号|粉丝)\s*[:：]"
    r"|^\s*[-*]\s*\d+\s*字\s*[·・]"
    r"|^\s*[-*]\s*共\s*\d+\s*[条字]"
)
SEP_RE = re.compile(r"^\s*-{3,}\s*$")

# 句末标点（保留在句尾）；换行另作切点且丢弃
SENT_END = "。！？；!?"
SENT_RE = re.compile(r"[^。！？；!?;\n]*[。！？；!?;]|[^。！？；!?;\n]+")
COMMA_END = "，、,"
COMMA_RE = re.compile(r"[^，、,]*[，、,]|[^，、,]+")

# v1 分类规则（一字不改地照抄规格）
TIME = re.compile(
    r"(今天|上午|下午|收盘|开盘|放量|缩量|账户|个股红|赚钱效应|承接|低开高走|高开低走|板块上|指数|日复盘)"
)
METH = re.compile(
    r"(第\s*\d+\s*课|核心内容|方法论|规则|条件|如果.{0,20}就|操作要点|口诀|标准|定义|意味着)"
)


# ---------------------------------------------------------------- 解析 md


def parse_md(md_text: str) -> list[dict]:
    """把一个 md 文本切成 [{video_key, title, body, body_line}]，顺序保持原样。"""
    lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hits = [(i, match_anchor(ln)) for i, ln in enumerate(lines)]
    anchors = [(i, kv) for i, kv in hits if kv]
    videos: list[dict] = []

    for pos, (a, kv) in enumerate(anchors):
        vkey, title = kv
        end = anchors[pos + 1][0] if pos + 1 < len(anchors) else len(lines)

        body_parts: list[str] = []
        first_body_line = 0
        for i in range(a + 1, end):
            ln = lines[i]
            s = ln.strip()
            if not s:
                continue
            if SEP_RE.match(s) or s.startswith("#"):
                continue
            if META_RE.match(s):
                continue
            if s.startswith(">"):  # 引用块（说明性文字）
                continue
            body_parts.append(s)
            if not first_body_line:
                first_body_line = i + 1  # 1 基行号

        videos.append(
            {
                "video_key": vkey,
                "title": title,
                "body": "".join(body_parts),
                "body_line": first_body_line,
            }
        )
    return videos


# ---------------------------------------------------------------- 切分


def split_sentences(text: str) -> tuple[list[str], int]:
    """按句末标点切句，标点附着句尾。返回 (句子列表, 被丢弃的换行字符数)。"""
    sents = [s for s in SENT_RE.findall(text) if s]
    dropped = text.count("\n")
    if "".join(sents) != text.replace("\n", ""):
        raise AssertionError("切句丢失字符")
    return sents, dropped


def _split_by_comma(piece: str) -> list[str]:
    parts = [p for p in COMMA_RE.findall(piece) if p]
    if "".join(parts) != piece:  # 兜底：做不到无损就不切
        return [piece]
    return parts


def _hard_cut(piece: str, limit: int) -> list[str]:
    """按 limit 硬切；尽量不切在数字中间（向左回退，最多 30 字）。"""
    out: list[str] = []
    i, n = 0, len(piece)
    while i < n:
        j = i + limit
        if j >= n:
            out.append(piece[i:])
            break
        k = j
        floor = i + max(1, limit - 30)
        while k > floor and piece[k - 1].isdigit() and piece[k].isdigit():
            k -= 1
        if k <= i:
            k = j
        out.append(piece[i:k])
        i = k
    return out


def _pack(pieces: list[str], limit: int) -> list[str]:
    """贪心累积，每段 ≤ limit。"""
    segs: list[str] = []
    cur: list[str] = []
    size = 0
    for p in pieces:
        if size + len(p) <= limit:
            cur.append(p)
            size += len(p)
        else:
            if cur:
                segs.append("".join(cur))
            cur, size = [p], len(p)
    if cur:
        segs.append("".join(cur))
    return segs


def segment_text(text: str, limit: int = MAX_CHARS) -> tuple[list[str], int, int]:
    """正文 -> 段列表。返回 (段, 丢弃字符数, 硬切次数)。"""
    if not text:
        return [], 0, 0
    sents, dropped = split_sentences(text)
    atoms: list[str] = []
    hard = 0
    for s in sents:
        if len(s) <= limit:
            atoms.append(s)
            continue
        for part in _split_by_comma(s):
            if len(part) <= limit:
                atoms.append(part)
            else:
                chunks = _hard_cut(part, limit)
                hard += 1
                atoms.extend(chunks)
    segs = _pack(atoms, limit)

    # 字符完整性断言
    total = sum(len(x) for x in segs) + dropped
    if total != len(text):
        raise AssertionError(f"字符不完整: 段总长 {total} != 正文 {len(text)}")
    return segs, dropped, hard


# ---------------------------------------------------------------- 分类 / 时间


def classify_v1(c: str) -> str:
    """单位分类规则 v1（规格原文，不得改动判定逻辑）。"""
    t = len(TIME.findall(c))
    k = len(METH.findall(c))
    return "M" if (("第" in c and "课" in c) or (k >= 3 and k > t)) else ("T" if t > k else "U")


# v2 兜底用的**交易专属**词。刻意排除三类泛化词，都是实测踩出来的：
#   · "趋势/突破/涨/跌" —— 科技类博主正文里满地都是，第一版用它们把
#     "英伟达：个人电脑即Agent" 判成方法论；
#   · "K线/成交量/分时" —— 工具评测类博主的口头禅，"Python画K线这么简单"
#     "TradingView数据自动抓" 这类软件推销会整批误升 M（第二期实测 42 条）；
# 留下的是"操作概念"：仓位、止损、背驰、中枢、筹码这类，只有真讲交易才会出现。
ACT_TERM = re.compile(
    r"(买点|卖点|加仓|减仓|建仓|清仓|持仓|仓位|止损|止盈|背驰|中枢|线段|"
    r"盘整|分型|均线|筹码|短拆|换股|抛出|接回|空仓|满仓|涨停|跌停|"
    r"MACD|做多|做空|止损位|止盈位)"
)
# 强动作字：出现即说明这条视频在讲操作，不是纯闲聊
ACT_CORE = re.compile(r"(买|卖|减|加|止|持|抛|建|清|接|换|空仓|满仓)")

# 正文短于此长度无从抽取（v1 的 U 里中位长度仅 93 字，多是片头/空镜转录）
MIN_CLASS_CHARS = 120


def classify_v2(c: str) -> str:
    """单位分类规则 v2：**只改 U 分支**，M/T 判定与 v1 逐字相同。

    为什么要改（2026-11-06 全库实测 1378 条视频）
    -------------------------------------------
    v1 的 U 是"未分类"的兜底桶，占 47.4%（653 条），但里面不是没内容 —— 是内容
    用的说法不在 v1 那两张词表里。北斗 168 段 M 池已实证：同一批视频整篇抽只得
    7 条可用断言（6.5%），切成段后 62.9% 的段可断言。**被判 U 的整条视频里，
    照样有大量"条件→动作"的干货**，只是它既没有"第…课"也没有三次方法词命中。

    改动只有两处
    -----------
    1. **U 分支加交易术语兜底**：既不满足 M 也不满足 T 时，若正文含 3 个以上
       **交易专属**词、且至少有 1 个强动作字，判 M。这类讲的是怎么操作，属方法论
       而非当日复盘。刻意排除"趋势/突破/涨/跌"这类泛化词 —— 科技类博主正文里
       这些词满地都是。
    2. **长度下限**：正文 < 120 字判 U。这类是片头/空镜转录，抽不出东西，
       放进 M 池只会白烧模型钱（v1 判 U 的中位长度只有 93 字）。

    M/T 判定为什么不动（这是本轮最重要的一个否决）
    --------------------------------------------
    第一版 v2 把绝对计数换成密度（每千字命中数），理由是"1 次/300 字 与 10 次/3000 字
    应同权"。实测否掉了：密度法让第二期 83 条科技视频 U→M —— "英伟达：个人电脑即
    Agent"这类正文里 t=1/k=1，密度相等即判 M，而它一个交易术语都没有。密度消除了
    长度偏置，同时消除了"证据是否充分"这道闸。v1 的 k>=3 虽然粗暴，但它同时是
    **充分性门槛**，不能只当偏置修掉。
    结论：保留 v1 的 M/T 判定原样，只补 U 分支。改动面小、可解释、可回滚。
    """
    n = max(len(c), 1)
    if "第" in c and "课" in c:
        return "M"
    if n < MIN_CLASS_CHARS:
        return "U"

    t = len(TIME.findall(c))
    k = len(METH.findall(c))
    if k >= 3 and k > t:
        return "M"
    if t > k:
        return "T"
    # ---- 以下是 v1 判 U 的那条路径（t==k，或 t 与 k 都不足以分型）
    a = len(ACT_TERM.findall(c))
    if a >= 3 and ACT_CORE.search(c):
        return "M"
    return "U"


CLASSIFIERS = {"v1": classify_v1, "v2": classify_v2}

# ---------------------------------------------------------------- 领域闸（仅 v2 生效）

# 强领域词：只有真讲交易/量化/缠论/技术分析才会出现的高区分度词。
# 设计背景（2026-11-06 跨领域失效复盘）：
#   classify 的 M 判定依赖 METH 正则（含「定义/标准/意味着」），会把 SysML 建模、
#   炒股书"投资标准"、科技博主"Agent 定义"等非交易内容误升 M（全库实测占 30% 假 M）。
#   领域闸在视频级分型前先拦一道：非交易领域视频直接归 U，不再进 METH/TIME 判定。
#   词表刻意避开"趋势/突破/涨/跌/大盘/指数"等泛词——科技博主也常说，单点命中
#   不足以判定领域；这些泛词留给弱词两级判定兜底。
DOMAIN_STRONG = re.compile(
    # 操作概念词：来自 ACT_TERM 精华，但剔除 K线/成交量/分时（工具评测口头禅）。
    # 这些词几乎只有真讲交易才出现；上一版漏了"买点/持仓/周线"导致周线口诀被错拦。
    r"(买点|卖点|加仓|减仓|建仓|清仓|持仓|仓位|止损|止盈|背驰|中枢|线段|"
    r"盘整|分型|均线|筹码|短拆|换股|抛出|接回|空仓|满仓|涨停|跌停|"
    r"做多|做空|止损位|止盈位|"
    # 市场/标的/体系词
    r"缠论|缠中说禅|打板|连板|涨停板|游资|主力|庄家|"
    r"题材股|龙头股|龙头|量价|量能|换手率|"
    r"均线系统|波浪理论|江恩|道氏|MACD|KDJ|布林带|RSI|金叉|死叉|"
    r"一买|二买|三买|一类买点|二类买点|三类买点|"
    r"仓位管理|止损纪律|"
    r"量化交易|程序化交易|选股|择时|回测|阿尔法|贝塔|对冲|因子|"
    r"期权|期货|可转债|打新|价值投资|趋势交易|"
    r"盘口|挂单|委比|量比|内外盘|振幅|成交额|换手|"
    # 时间框架（科技/产业内容极少用）
    r"周线|日线|月线|季线)"
)
# 弱领域词：单独出现不足以判定领域（"投资/理财/市场"太泛），需 >=2 个才放行。
DOMAIN_WEAK = re.compile(
    r"(大盘|指数|板块|个股|上证|深证|创业板|科创板|北交所|"
    r"市盈率|估值|基本面|财报|年报|季报|ETF|基金|投资|理财|市场|股)"
)


def is_trading_domain(text: str) -> bool:
    """判断正文是否属于交易/量化/股票领域。

    两级判定：强词命中任一即判领域；弱词需 >=2 个才判领域，避免单点泛词逃逸。
    这是 v2 分型的前置过滤，不改变 classify_v1/v2 的内部判定逻辑。
    """
    if DOMAIN_STRONG.search(text):
        return True
    return len(DOMAIN_WEAK.findall(text)) >= 2




def pit_from_key(video_key: str) -> datetime:
    return datetime.strptime(video_key, "%Y%m%d_%H%M").replace(tzinfo=TZ_CN)


def _assert_no_pipe(*vals: str) -> None:
    """哈希输入禁止包含 ``|``，否则 ``pid="a|b",vk="c"`` 与 ``pid="a",vk="b|c"`` 会同串。"""
    for v in vals:
        if "|" in v:
            raise ValueError(f"哈希输入含 '|'，拼接会产生歧义: {v!r}")


def segment_text_key(claim: str) -> str:
    """段内容锚点：sha1(claim.strip())[:10]。与段的位置/序号无关。"""
    return hashlib.sha1(claim.strip().encode("utf-8")).hexdigest()[:10]


def make_unit_id(project_id: str, video_key: str, claim: str, dup_index: int = 1) -> str:
    """段级命名空间（**内容寻址**）：sha1("pid|video_key|text_key")。

    ``seq`` 不参与哈希 —— 同一段文字无论排第几、``MAX_CHARS`` 怎么调、前面删了几段，
    ID 都恒定；段文本被编辑则 ID 变化，这是正确语义（那是新知识）。
    同一视频内文字完全相同的段按出现顺序加 ``_2`` / ``_3`` 后缀消歧（确定性可复现）。
    """
    _assert_no_pipe(project_id, video_key)
    raw = f"{project_id}|{video_key}|{segment_text_key(claim)}"
    uid = f"learn_{project_id}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"
    return uid if dup_index <= 1 else f"{uid}_{dup_index}"


def make_positional_unit_id(project_id: str, video_key: str, seq: int) -> str:
    """旧的位置式命名（sha1("pid|video_key|seq")）。仅供与历史对照/回归，不再产出。"""
    _assert_no_pipe(project_id, video_key)
    raw = f"{project_id}|{video_key}|{seq}"
    return f"learn_{project_id}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"


def make_legacy_unit_id(project_id: str, video_key: str, seq: int = 1) -> str:
    """存量整篇 unit 的命名空间：sha1("pid|video_key|legacy|seq")。

    存量是**冻结历史、永不重切**，位置锚点对它是稳定的，故保留 ``seq``；
    段级则用内容寻址。两套命名空间有意采用不同的稳定性语义，互不替代。
    """
    _assert_no_pipe(project_id, video_key)
    raw = f"{project_id}|{video_key}|legacy|{seq}"
    return f"learn_{project_id}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"


def check_id_namespace(project_id: str, video_key: str, claim: str, seq: int = 1) -> dict:
    """段级（内容寻址）与 legacy（位置式）两个命名空间是否互不重叠。"""
    seg = make_unit_id(project_id, video_key, claim)
    leg = make_legacy_unit_id(project_id, video_key, seq)
    return {"video_key": video_key, "seq": seq, "text_key": segment_text_key(claim),
            "segment_id": seg, "legacy_id": leg, "distinct": seg != leg}


# ---------------------------------------------------------------- 构建


def build_units(videos: list[dict], project_id: str, limit: int = MAX_CHARS,
                rule_version: str = "v1") -> tuple[list[dict], dict]:
    """切段 + **视频级**分型（分型只做一次，段继承）。

    依据：一条视频是"日复盘"还是"方法论"是视频级属性。逐段分类会把方法论视频里
    提到"账户"的那一段误判成 T，也会把日复盘里偶然讲的一句规则误判成 M。

    ``rule_version`` 默认 ``v1``（历史口径，不得漂移）；``v2`` 用密度判定 + 动作兜底。
    两套并存、结果可逐条对比，切换前先跑 ``--compare-class`` 看差异再决定。
    """
    clf = CLASSIFIERS.get(rule_version)
    if clf is None:
        raise ValueError(f"未知 rule_version={rule_version!r}，可选 {sorted(CLASSIFIERS)}")
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    units: list[dict] = []
    stat = {"source_chars": 0, "unit_chars": 0, "dropped": 0, "hard_cuts": 0,
            "videos": 0, "videos_total": len(videos), "dup_segments": 0,
            "classify_rule_version": rule_version,
            "video_classes": {"M": 0, "T": 0, "U": 0}, "domain_out": 0}

    for v in videos:
        body = v["body"]
        if not body:
            continue
        stat["videos"] += 1
        stat["source_chars"] += len(body)
        segs, dropped, hard = segment_text(body, limit)
        stat["dropped"] += dropped
        stat["hard_cuts"] += hard
        stat["unit_chars"] += sum(len(s) for s in segs)

        # ---- 视频级分型：整篇正文跑一次规则，段全部继承
        # 领域闸（仅 v2）：非交易领域视频直接归 U，避免 METH 的「定义/标准/意味着」
        # 把 SysML/卖书/科技类内容误升 M（全库实测占 30% 假 M）。v1 口径不动。
        domain_out = False
        if rule_version == "v2" and not is_trading_domain(body):
            vcls = "U"
            domain_out = True
        else:
            vcls = clf(body)
        stat["video_classes"][vcls] = stat["video_classes"].get(vcls, 0) + 1
        if domain_out:
            stat["domain_out"] += 1

        pit = pit_from_key(v["video_key"])
        seen: dict[str, int] = {}
        dup_total = 0
        for seq, claim in enumerate(segs, 1):
            tk = segment_text_key(claim)
            seen[tk] = seen.get(tk, 0) + 1
            if seen[tk] > 1:
                dup_total += 1
            units.append(
                {
                    "unit_id": make_unit_id(project_id, v["video_key"], claim, seen[tk]),
                    "legacy_unit_id": None,
                    "project_id": project_id,
                    "video_key": v["video_key"],
                    "seq": seq,
                    "granularity": "segment",
                    "claim": claim,
                    "claim_chars": len(claim),
                    "text_key": tk,
                    "unit_class": vcls,
                    "class_unit": "video",
                    "classify_rule_version": rule_version,
                    "domain_out": domain_out,
                    "pit": pit.isoformat(),
                    "expire_at": (pit + timedelta(days=3)).isoformat() if vcls == "T" else None,
                    "content_hash": hashlib.sha256(claim.encode("utf-8")).hexdigest()[:16],
                    "evidence": {"file": f"{v['video_key']}.md", "line": seq},
                    "status": "draft",
                    "created_at": created,
                }
            )
        stat["dup_segments"] += dup_total
    return units, stat


# ---------------------------------------------------------------- 输入解析


def resolve_inputs(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*.md") if p.is_file())
    return [path]


# ---------------------------------------------------------------- 报告


def build_report(units: list[dict], stat: dict, inputs: list[Path], write: bool, out: Path | None) -> dict:
    lens = [u["claim_chars"] for u in units]
    classes = {"M": 0, "T": 0, "U": 0}
    for u in units:
        classes[u["unit_class"]] = classes.get(u["unit_class"], 0) + 1
    over = sum(1 for x in lens if x > MAX_CHARS)
    samples = [
        {"unit_id": u["unit_id"], "seq": u["seq"], "video_key": u["video_key"],
         "claim_chars": u["claim_chars"], "unit_class": u["unit_class"],
         "granularity": u["granularity"], "class_unit": u["class_unit"],
         "head": u["claim"][:50]}
        for u in units[:3]
    ]
    vc = stat.get("video_classes", {"M": 0, "T": 0, "U": 0})
    id_check = None
    if units:
        u0 = units[0]
        id_check = check_id_namespace(u0["project_id"], u0["video_key"], u0["claim"], 1)
    return {
        "mode": "write" if write else "dry-run",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": [str(p) for p in inputs],
        "project_ids": sorted({u["project_id"] for u in units}),
        "videos": stat["videos"],
        "videos_parsed": stat.get("videos_total", stat["videos"]),
        "units": len(units),
        "dup_segments": stat.get("dup_segments", 0),
        "id_scheme": "content-addressed: sha1(pid|video_key|sha1(claim.strip())[:10])[:10]",
        "length": {
            "avg": round(statistics.mean(lens), 1) if lens else 0,
            "median": statistics.median(lens) if lens else 0,
            "max": max(lens) if lens else 0,
            "min": min(lens) if lens else 0,
            "over_limit": over,
            "limit": MAX_CHARS,
        },
        "integrity": {
            "source_chars": stat["source_chars"],
            "unit_chars": stat["unit_chars"],
            "dropped_chars": stat["dropped"],
            "delta": stat["source_chars"] - stat["unit_chars"] - stat["dropped"],
            "hard_cuts": stat["hard_cuts"],
            "ok": stat["source_chars"] - stat["unit_chars"] - stat["dropped"] == 0,
        },
        "video_classes": vc,
        "classes": classes,
        "class_scope": "video",
        "id_check": id_check,
        "samples": samples,
        "out": str(out) if (write and out) else None,
    }


def print_report(rep: dict) -> None:
    L = rep["length"]
    I = rep["integrity"]
    C = rep["classes"]
    V = rep["video_classes"]
    print(f"== units_v2 {rep['mode']} ==")
    print(f"输入      : {', '.join(rep['inputs'])}")
    print(f"项目      : {', '.join(rep['project_ids']) or '-'}")
    print(f"视频数    : {rep['videos']}  (解析到 {rep['videos_parsed']} 条)")
    print(f"总段数    : {rep['units']}")
    print(f"平均段长  : {L['avg']}")
    print(f"中位段长  : {L['median']}   (验收断言 ≤{L['limit']}: {'PASS' if L['median'] <= L['limit'] else 'FAIL'})")
    print(f"最长段长  : {L['max']}   最短: {L['min']}")
    print(f"超300字段 : {L['over_limit']}   ({'PASS' if L['over_limit'] == 0 else 'FAIL'})")
    print(f"视频级分型: M {V.get('M', 0)} / T {V.get('T', 0)} / U {V.get('U', 0)}   (class_scope=video)")
    print(f"段级继承  : M {C.get('M', 0)} / T {C.get('T', 0)} / U {C.get('U', 0)}")
    print(f"字符完整  : 原文 {I['source_chars']} = 段总 {I['unit_chars']} + 丢弃 {I['dropped_chars']}"
          f"  差值 {I['delta']}  ({'OK' if I['ok'] else 'BROKEN'})")
    print(f"硬切次数  : {I['hard_cuts']}")
    print(f"重复段消歧: {rep.get('dup_segments', 0)} 段带 _N 后缀")
    if rep.get("id_check"):
        d = rep["id_check"]
        print(f"ID 命名空间: segment={d['segment_id']} (text_key={d['text_key']}) vs "
              f"legacy={d['legacy_id']}  ({'DISTINCT' if d['distinct'] else 'COLLISION'})")
        print(f"ID 方案    : {rep.get('id_scheme', '-')}")
    print("--- 前 3 条样例 ---")
    for i, s in enumerate(rep["samples"], 1):
        print(f"{i}. [{s['unit_class']}] {s['claim_chars']}字 seq={s['seq']} "
              f"{s['granularity']}/{s['class_unit']} {s['unit_id']}")
        print(f"   {s['head']}")
    if rep["out"]:
        print(f"已写出    : {rep['out']}")


# ---------------------------------------------------------------- CLI


def _compare_classes(files: list[Path]) -> int:
    """v1 vs v2 分型对比。只读不写，用来决定要不要切换口径。"""
    from collections import Counter, defaultdict

    rows = []
    for f in files:
        md = f.read_text(encoding="utf-8", errors="replace")
        for v in parse_md(md):
            body = v.get("body") or ""
            if not body:
                continue
            c1, c2 = classify_v1(body), classify_v2(body)
            rows.append({"file": f.stem, "video_key": v["video_key"],
                         "chars": len(body), "v1": c1, "v2": c2,
                         "title": (v.get("title") or "")[:30]})

    n = len(rows)
    if not n:
        print("[compare] 没有可用视频")
        return 0
    print(f"[compare] 视频 {n} 条（{len(files)} 个源件）")
    for tag in ("v1", "v2"):
        c = Counter(r[tag] for r in rows)
        print(f"  {tag}: " + "  ".join(f"{k}={c.get(k,0)}({c.get(k,0)/n*100:.1f}%)"
                                      for k in ("M", "T", "U")))
    moved = [r for r in rows if r["v1"] != r["v2"]]
    print(f"\n  变动 {len(moved)} 条（{len(moved)/n*100:.1f}%）")
    cm = Counter((r["v1"], r["v2"]) for r in moved)
    for (a, b), cnt in cm.most_common():
        print(f"    {a} → {b}  {cnt} 条")
    # 字数分布：验证"v1 的 U 集中在短文本"这个假设
    by_v1 = defaultdict(list)
    for r in rows:
        by_v1[r["v1"]].append(r["chars"])
    print("\n  各型正文长度（v1 口径）")
    for k in ("M", "T", "U"):
        xs = by_v1.get(k) or []
        if xs:
            xs = sorted(xs)
            print(f"    {k}: 中位 {xs[len(xs)//2]} 字，均值 {sum(xs)//len(xs)} 字，n={len(xs)}")
    print("\n  v1=U 但 v2 改判的样例（前 8）")
    for r in [x for x in moved if x["v1"] == "U"][:8]:
        print(f"    [{r['v2']}] {r['chars']:>5}字 {r['video_key']} {r['title']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="学习源件按段切分（≤300 字/段）生成 v2 知识单元，默认 dry-run。"
    )
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="md 文件或目录（默认北斗股术1688 源件）")
    ap.add_argument("--out", default=None, help="输出 jsonl 路径（仅 --write 时使用）")
    ap.add_argument("--write", action="store_true", help="真正落盘到 --out（默认 dry-run）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    ap.add_argument("--limit", type=int, default=MAX_CHARS, help=f"单段字数上限（默认 {MAX_CHARS}）")
    ap.add_argument("--rule-version", default="v1", choices=sorted(CLASSIFIERS),
                    help="分型规则版本，默认 v1（历史口径）")
    ap.add_argument("--compare-class", action="store_true",
                    help="只对比 v1 与 v2 的分型差异，不切段不写盘")
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERR] 输入不存在: {in_path}", file=sys.stderr)
        return 2
    if args.write and not args.out:
        print("[ERR] --write 必须同时指定 --out，拒绝写入默认路径", file=sys.stderr)
        return 2

    files = resolve_inputs(in_path)
    if not files:
        print(f"[ERR] 目录下没有 md: {in_path}", file=sys.stderr)
        return 2

    if args.compare_class:
        return _compare_classes(files)

    videos: list[dict] = []
    per_project: dict[str, list[dict]] = {}
    for f in files:
        md = f.read_text(encoding="utf-8", errors="replace")
        vs = parse_md(md)
        videos.extend(vs)
        per_project.setdefault(f.stem, []).extend(vs)

    units: list[dict] = []
    stat = {"source_chars": 0, "unit_chars": 0, "dropped": 0, "hard_cuts": 0,
            "videos": 0, "videos_total": 0, "video_classes": {"M": 0, "T": 0, "U": 0}}
    for pid, vs in per_project.items():
        u, s = build_units(vs, pid, args.limit, rule_version=args.rule_version)
        units.extend(u)
        for k in ("source_chars", "unit_chars", "dropped", "hard_cuts", "videos", "videos_total"):
            stat[k] += s[k]
        for c in ("M", "T", "U"):
            stat["video_classes"][c] += s["video_classes"].get(c, 0)

    if args.write:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            for u in units:
                fh.write(json.dumps(u, ensure_ascii=False) + "\n")

    rep = build_report(units, stat, files, args.write, Path(args.out) if args.out else None)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
