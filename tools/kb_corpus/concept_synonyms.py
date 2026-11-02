# -*- coding: utf-8 -*-
"""概念命名统一 / 同义归并 v1.0（W1-2，2026-11-03）。

背景：W1-1 发现 1092 个概念中仅 12 个多源（≈1%），瓶颈是「同一概念不同叫法」
（均线/移动平均线/MA、中枢/缠论中枢、涨停/涨停板、仓位/仓位管理…）。
本模块**只读** `research/reports/knowledge_multisource_20261103.json` 的 concepts，
做三级归一后输出合并簇、合并后多源概念数、未合并/不确定清单：

  1. **规则清洗**：去 Markdown/序号/疑问前缀/括注/版本号/分隔副标题（——、：后的解释）/标点，
     去尾部「的/呢/啊」等语气词；清洗后过短或纯序号的记为噪声（junk，不参与合并）；
  2. **领域同义表**（内置 ≥40 组，含任务点名：均线/MA/移动平均线、中枢/缠论中枢、
     买卖点/缠论买卖点、涨停/涨停板/封板、仓位/仓位管理/资金管理、止损/止损位、
     背离/背驰、平台/箱体、放量/放量突破、筹码/筹码集中度 …）→ 别名映射到规范词；
  3. **模糊合并**：包含关系（长词包含短词，短词必须为规范词/词表词，且差异部分不含数字/
     窗口周期/方向词）或**字符集合 Jaccard ≥ 0.6**（同样排除数字/方向差异），再并查集传递合并。

只读声明：仅读入 knowledge_multisource JSON（前后 sha256 校验），只写 --out/--md 两个输出，
不改学习卡/units/治理 JSON。

用法：
  python tools/kb_corpus/concept_synonyms.py
  python tools/kb_corpus/concept_synonyms.py --input X.json --out Y.json --md Y.md
  python tools/kb_corpus/concept_synonyms.py --json-stdout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / "research" / "reports" / "knowledge_multisource_20261103.json"
OUT_JSON = REPO / "research" / "reports" / "concept_synonyms_20261103.json"
OUT_MD = REPO / "research" / "reports" / "concept_synonyms_20261103.md"

VERSION = "1.0.0"
FUZZY_JACCARD = 0.60            # 字符集合 Jaccard 阈值（任务口径）
MIN_KEY_LEN = 2                 # 清洗后概念最短长度（更短→噪声）
MAX_CONTAIN_EXTRA = 4           # 包含合并允许的额外修饰字符数上限
MULTI_LOWER_MIN = 2

SOURCE_TYPE_LABEL = {"blogger": "博主", "book": "书", "research_report": "研报", "other": "其他"}

# ---------------------------------------------------------------- 领域同义表（≥40 组）
# 每组第一个为规范词（canonical），其余为别名；覆盖量学/缠论/技术指标/量化/交易执行/研报高频词。
DOMAIN_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("均线", "移动平均线", "MA", "均线系统", "均价线"),
    ("中枢", "缠论中枢", "走势中枢", "中枢区间"),
    ("买卖点", "缠论买卖点", "三类买卖点", "买点卖点"),
    ("涨停", "涨停板", "封板", "涨停封板"),
    ("仓位", "仓位管理", "资金管理", "头寸管理", "持仓管理"),
    ("止损", "止损位", "止损线", "止损点"),
    ("止盈", "止盈位", "止盈线"),
    ("背离", "背驰"),
    ("平台", "箱体", "整理平台", "横盘平台"),
    ("放量", "放量突破", "放量上涨", "量能放大"),
    ("筹码", "筹码集中度", "筹码分布"),
    ("缩量", "缩量柱", "缩量洗盘", "量能萎缩"),
    ("倍量", "倍量柱", "倍量阳柱"),
    ("高量", "高量柱", "高量柱体"),
    ("低量", "低量柱"),
    ("平量", "平量柱"),
    ("梯量", "梯量柱"),
    ("百日低量", "百日低量群", "百低量", "百低"),
    ("黄金柱", "黄金柱形态", "黄金柱战法"),
    ("黄金线", "金线", "黄金线战法"),
    ("黄金十字", "黄金十字线"),
    ("涨停基因", "涨停基因组合"),
    ("倍量伸缩", "倍量伸缩战法"),
    ("价升量缩", "量缩价升"),
    ("量价背离", "价量背离"),
    ("长阴短柱", "长阴短柱形态"),
    ("假阴真阳", "假阴真阳柱"),
    ("接力双阳", "双阳接力"),
    ("凹口", "凹口形态", "凹口淘金"),
    ("凹峰", "凹间峰", "凹间峰形态"),
    ("极阴次阳", "极阴次阳战法"),
    ("现场直憋", "直憋形态"),
    ("平斜交叉", "平斜交叉战法"),
    ("一剑封喉", "一剑封喉战法"),
    ("倍量不穿", "倍量不穿形态"),
    ("高量不破", "高量不破形态"),
    ("跳空不补", "缺口不补"),
    ("底分形", "底分型", "底分形结构", "底分型结构"),
    ("顶分形", "顶分型", "顶分形结构", "顶分型结构"),
    ("笔", "缠论笔", "走势笔"),
    ("线段", "缠论线段", "走势线段"),
    ("KDJ", "随机指标", "KDJ指标"),
    ("RSI", "相对强弱指标", "RSI指标"),
    ("MACD", "指数平滑异同移动平均线", "MACD指标"),
    ("布林带", "布林线", "BOLL", "布林通道"),
    ("VWAP", "成交量加权平均价"),
    ("市盈率", "PE", "PE_TTM", "PE估值"),
    ("市净率", "PB", "PB估值"),
    ("净资产收益率", "ROE"),
    ("换手率", "周转率"),
    ("北向资金", "陆股通", "北上资金"),
    ("南向资金", "港股通"),
    ("融资融券", "两融"),
    ("龙虎榜", "龙虎榜数据"),
    ("集合竞价", "竞价"),
    ("回测", "回溯测试"),
    ("过拟合", "过度拟合"),
    ("夏普比率", "夏普率"),
    ("最大回撤", "回撤"),
    ("波动率", "波动性"),
    ("市场情绪", "情绪指标"),
    ("龙头股", "龙头"),
    ("打板", "涨停打板"),
    ("低吸", "低吸买点"),
    ("量柱", "量柱形态", "量柱分类", "成交量柱"),
    ("量线", "量线系统", "量线定义"),
    ("量波", "量波形态"),
    ("量学", "量学理论"),
    ("分时图", "分时走势"),
    ("周期", "时间周期", "周期共振"),
]

# 通用泛词/噪声词：不允许作为包含合并的「短词」，避免 价格/波动 吞掉所有子概念
STOP_HEADS: frozenset[str] = frozenset({
    "价格", "波动", "关系", "行为", "证据", "评估", "仓位", "名次", "层", "线", "点", "值",
    "注意", "说明", "备注", "结论", "观点", "分析", "数据", "指标", "公式", "系统", "结构",
    "买点", "卖点", "类买点", "类卖点", "第", "个", "呢", "的", "了", "吗", "啊",
})
# 方向/相对词：包含合并或模糊合并时若差异部分含这些词 → 不合并（底/顶分形、多/空…）
DIRECTION_CHARS: frozenset[str] = frozenset("底顶买卖多空涨跌增减高低进出上下左右前后第一二三四五六七八九十")
# 允许在模糊合并中忽略的“修饰词”（去掉后两词相同 → 同一概念的不同叫法）
MODIFIER_WORDS: frozenset[str] = frozenset({
    "行业", "板块", "细分", "周度", "月度", "年度", "季度", "表现", "变动", "变化", "数据",
    "特征", "结构", "逻辑", "定义", "方法", "策略", "技术", "指标", "公式", "系统", "模型",
    "分析", "判断", "应用", "运用", "原则", "规则", "信号", "级别", "方向", "位置", "形态",
    "状态", "回顾", "概率", "占比", "口径", "定位", "格局", "滚动", "动态", "静态", "主要",
    "基本", "核心", "基础", "实战", "标准", "经典", "常用", "常规", "完整", "解读", "说明",
    "介绍", "详解", "案例", "示例", "密码", "金叉", "死叉", "缠论", "量学", "通达信",
    "与", "和", "及", "的", "了", "呢", "吗", "啊", "吧", "呀",
})
# 常见同义/错别字对（前者视同后者）
EQUIV_PAIRS: tuple[tuple[str, str], ...] = (
    ("区域", "地区"), ("粘", "黏"), ("禅", "缠"), ("准", "确"), ("割", "隔"),
    ("账", "帐"), ("版", "板"), ("交易", "操作"),
)
WINDOW_RE = re.compile(r"[0-9一二三四五六七八九十百]+\s*(日|天|分钟|周|月|年|倍|周期|级别|根|条|档)")
NUMERAL_RE = re.compile(r"[0-9一二三四五六七八九十百千万零两]")
VERSION_RE = re.compile(r"[（(]\s*(?:v|V|版本)?\s*\d+(?:\.\d+)*\s*[）)]|(?<=\w)v\d+(?:\.\d+)*", re.I)
PAREN_RE = re.compile(r"[（(][^（）()]{1,24}[）)]")
MD_MARKS_RE = re.compile(r"[`*_>#|\[\]【】]+")
LEAD_BULLET_RE = re.compile(r"^[\-\+•·\s]*\d{1,3}[\s\.、·\-]+")
LEAD_ORDINAL_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*(个|条|节|章|部分|步|课|集|讲)?[、\.．:：\s]*")
LEAD_Q_RE = re.compile(r"^(什么是|什么叫|如何|怎样|为什么|啥是)")
SEP_PATTERNS = ("——", "--", "：", ":", "|")
NON_WORD_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
TAIL_PARTICLE_RE = re.compile(r"[的了吗呢啊吧呀哈]+$")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 噪声概念：纯序号/无意义标题/过长分镜碎片
JUNK_EXACT = frozenset({"注意", "说明", "备注", "结论", "观点", "分析", "数据", "合计", "总计", "目录"})
JUNK_PATTERNS = (
    re.compile(r"^第?\d+个?$"),
    re.compile(r"^(可以直接当口播稿用的|适合改成分图文案的|可以直接当|适合改成)"),
    re.compile(r"^[a-z]?$"),
)


# ---------------------------------------------------------------- 清洗
def clean_concept(concept: str, strip_paren: bool = True) -> str:
    """规则清洗：Markdown/序号/疑问前缀/括注/版本/分隔副标题/标点/语气词 → 归一化概念 key。"""
    s = str(concept or "")
    s = MD_MARKS_RE.sub(" ", s)
    s = LEAD_BULLET_RE.sub("", s.strip())
    s = LEAD_ORDINAL_RE.sub("", s)
    s = LEAD_Q_RE.sub("", s)
    for sep in SEP_PATTERNS:                     # 「主标题——解释」「标题：解释」只留主标题
        if sep in s:
            s = s.split(sep)[0]
    if strip_paren:
        s = PAREN_RE.sub("", s)                  # 去括注（MSME）/版本（v2）
    s = VERSION_RE.sub("", s)
    s = NON_WORD_RE.sub("", s).lower()
    s = TAIL_PARTICLE_RE.sub("", s)              # ai量化呢 → ai量化
    return s


def clean_display(concept: str) -> str:
    """展示名清洗：保留括注（信息量更大），只去 Markdown/序号/分隔副标题。"""
    s = str(concept or "")
    s = MD_MARKS_RE.sub(" ", s)
    s = LEAD_BULLET_RE.sub("", s.strip())
    s = LEAD_ORDINAL_RE.sub("", s)
    s = LEAD_Q_RE.sub("", s)
    for sep in SEP_PATTERNS:
        if sep in s:
            s = s.split(sep)[0]
    s = re.sub(r"\s+", " ", s).strip()
    return s


def junk_reason(key: str) -> str | None:
    """噪声判定：返回原因或 None。"""
    if not key:
        return "清洗后为空"
    if len(key) < MIN_KEY_LEN:
        return f"过短（<{MIN_KEY_LEN} 字）"
    if key in JUNK_EXACT:
        return "无意义通用词"
    for pat in JUNK_PATTERNS:
        if pat.search(key):
            return "纯序号/模板标题"
    return None


# ---------------------------------------------------------------- 同义表
def _canonical_of(group: tuple[str, ...]) -> str:
    return clean_concept(group[0])


def build_synonym_map(groups: list[tuple[str, ...]] | None = None) -> dict[str, str]:
    """{清洗后别名: 清洗后规范词}；规范词自身不入表（key 已是规范词）。"""
    groups = groups if groups is not None else DOMAIN_SYNONYM_GROUPS
    mapping: dict[str, str] = {}
    for group in groups:
        canonical = _canonical_of(group)
        if not canonical:
            continue
        for alias in group:
            key = clean_concept(alias)
            if key and key != canonical:
                mapping.setdefault(key, canonical)
    return mapping


def canonical_concept(concept: str, synonym_map: dict[str, str] | None = None) -> str:
    """清洗 + 同义表映射 → 规范概念 key。"""
    key = clean_concept(concept)
    return (synonym_map or {}).get(key, key)


def canonical_set(groups: list[tuple[str, ...]] | None = None) -> set[str]:
    return {_canonical_of(g) for g in (groups if groups is not None else DOMAIN_SYNONYM_GROUPS)
            if _canonical_of(g)}


# ---------------------------------------------------------------- 合并守卫
def _char_jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _numeral_diff(a: str, b: str) -> bool:
    """两词差异部分是否含数字/序数（第一/第二定律、v2、20日 → 不同概念，不合并）。"""
    diff = (set(a) ^ set(b))
    return any(ch in DIRECTION_CHARS for ch in diff)


def _windowed(key: str) -> bool:
    return bool(WINDOW_RE.search(key))


def _dict_direction_diff(a: str, b: str) -> bool:
    """差异部分含方向/序数词（底/顶、第一/第二、多/空…）→ 不合并。"""
    return _numeral_diff(a, b)


def _merge_core(key: str) -> str:
    """去掉同义/错别字等价对与修饰词后的「概念核心」（相同 → 视为同一概念的不同叫法）。"""
    s = key
    for a, b in EQUIV_PAIRS:
        s = s.replace(a, b)
    for word in sorted(MODIFIER_WORDS, key=len, reverse=True):
        s = s.replace(word, "")
    return s


def can_contain_merge(short: str, long: str, canon_set: set[str]) -> tuple[bool, str]:
    """包含合并守卫：短词落在长词内，短词须为同义表规范词，且差异部分只能是修饰词/等价词。"""
    if not short or not long or short == long or short not in long:
        return False, "无包含关系"
    if len(short) < MIN_KEY_LEN:
        return False, "短词过短"
    if short in STOP_HEADS:
        return False, "短词为通用泛词（避免误吞）"
    if short not in canon_set:
        return False, "短词非领域同义表规范词（保守：不做包含合并）"
    extra = long.replace(short, "", 1)
    if len(extra) > MAX_CONTAIN_EXTRA:
        return False, "修饰差异过长"
    if NUMERAL_RE.search(extra):
        return False, "差异含数字/序数（参数/序数不同）"
    if _numeral_diff(short, long):
        return False, "差异含方向词（底/顶、多/空等）"
    if _windowed(long) or _windowed(short):
        return False, "窗口/周期词（不同参数）"
    if _merge_core(extra):
        return False, "额外修饰含实质内容（不做包含合并）"
    return True, f"包含：{long} ⊃ {short}（修饰={extra}）"


def should_fuzzy_merge(a: str, b: str) -> tuple[bool, str, float]:
    """模糊合并：字符集合 Jaccard ≥ 0.6，且两词去掉修饰词/等价词后核心一致（或仅词序不同）。"""
    if not a or not b or a == b:
        return False, "相同/空", 0.0
    if len(a) < MIN_KEY_LEN or len(b) < MIN_KEY_LEN:
        return False, "过短", 0.0
    if abs(len(a) - len(b)) > 3:
        return False, "长度差>3", 0.0
    jac = _char_jaccard(a, b)
    if jac < FUZZY_JACCARD:
        return False, f"jaccard={jac:.2f}<{FUZZY_JACCARD}", jac
    if _windowed(a) != _windowed(b):
        return False, "窗口/周期词不一致", jac
    if _dict_direction_diff(a, b):
        return False, "差异含数字/方向/序数词", jac
    if a in STOP_HEADS or b in STOP_HEADS:
        return False, "含通用泛词", jac
    core_a, core_b = _merge_core(a), _merge_core(b)
    if core_a and core_a == core_b:
        return True, f"字符 jaccard={jac:.2f}≥{FUZZY_JACCARD}，去修饰后同核={core_a}", jac
    if Counter(a) == Counter(b) and len(a) >= 3 and not (set(a) & DIRECTION_CHARS):
        return True, f"字符 jaccard={jac:.2f}≥{FUZZY_JACCARD}，词序重排", jac
    return False, "差异含实质内容（不做模糊合并）", jac


# ---------------------------------------------------------------- 并查集
class UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self, keys) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for key in keys:
            out[self.find(key)].append(key)
        return out


# ---------------------------------------------------------------- 合并
def merge_concepts(entries: list[dict], groups: list[tuple[str, ...]] | None = None) -> dict:
    """概念条目 → 合并簇（纯函数，便于单测）。

    entries: [{"concept": str, "confidence": "高/中/低", "n_sources": int,
               "source_types": [...], "sources": [...]?}]（W1-1 JSON concepts 元素）
    返回：{"clusters": [...], "junk": [...], "stats": {...}}
    """
    groups = groups if groups is not None else DOMAIN_SYNONYM_GROUPS
    syn = build_synonym_map(groups)
    canon = canonical_set(groups)
    uf = UnionFind()
    members: dict[str, list[dict]] = defaultdict(list)   # canonical key → entries
    display_votes: dict[str, Counter] = defaultdict(Counter)
    merge_reasons: dict[str, list[str]] = defaultdict(list)
    junk: list[dict] = []

    for entry in entries:
        raw = str(entry.get("concept") or "")
        key = clean_concept(raw)
        reason = junk_reason(key)
        if reason:
            junk.append({"concept": raw, "clean": key, "reason": reason})
            continue
        canonical = syn.get(key, key)
        uf.find(canonical)                                # 注册节点
        members[canonical].append(entry)
        display_votes[canonical][clean_display(raw) or raw] += 1
        if canonical != key:
            merge_reasons[canonical].append(f"同义：{key}→{canonical}")

    keys = sorted(members, key=len)
    # 1) 包含合并（短词必须为同义表规范词；防泛词/数字/方向误吞）
    for i, short in enumerate(keys):
        if len(short) < MIN_KEY_LEN or len(short) > 8:
            continue
        for long in keys[i + 1:]:
            if len(long) <= len(short) or len(long) > len(short) + MAX_CONTAIN_EXTRA:
                continue
            ok, why = can_contain_merge(short, long, canon)
            if ok:
                uf.union(short, long)
                merge_reasons[short].append(why)
    # 2) 字符 jaccard 模糊合并
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ok, why, jac = should_fuzzy_merge(keys[i], keys[j])
            if ok:
                uf.union(keys[i], keys[j])
                merge_reasons[keys[i]].append(why)

    clusters: list[dict] = []
    for root, merged_keys in uf.groups([k for k in keys]).items():
        merged_keys = sorted(merged_keys, key=lambda k: (-len(k), k))
        entries_in = [e for k in merged_keys for e in members[k]]
        display = display_votes[merged_keys[0]].most_common(1)[0][0]
        if len(merged_keys) > 1:
            # 簇展示名取“信息量最大 + 出现次数最多”的成员清洗名
            disp_counter: Counter = Counter()
            for k in merged_keys:
                for name, cnt in display_votes[k].items():
                    disp_counter[name] += cnt
            display = max(disp_counter.items(), key=lambda kv: (len(kv[0]), kv[1]))[0]
        known_sources: list[dict] = []
        known_keys: set[tuple] = set()
        unknown: list[dict] = []
        for entry in entries_in:
            for src in entry.get("sources") or []:
                key2 = (src.get("source_type"), src.get("source_key"))
                if key2 not in known_keys:
                    known_keys.add(key2)
                    known_sources.append({"source_type": src.get("source_type"),
                                          "source_key": src.get("source_key"),
                                          "source_label": src.get("source_label")})
            if not entry.get("sources"):
                for stype in entry.get("source_types") or []:
                    unknown.append({"concept": entry.get("concept"), "source_type": stype})
        known_types = {s["source_type"] for s in known_sources}
        new_types = {u["source_type"] for u in unknown if u["source_type"] not in known_types}
        lower = len(known_sources) + len(new_types)
        upper = len(known_sources) + len(unknown)
        if lower >= MULTI_LOWER_MIN:
            status = "多源-确认"
        elif upper >= MULTI_LOWER_MIN:
            status = "多源-待确认"
        else:
            status = "单源"
        confidences = [e.get("confidence") for e in entries_in if e.get("confidence")]
        reasons = [r for k in merged_keys for r in merge_reasons.get(k, [])]
        if status == "多源-确认":
            # 只有“全部成员原本高置信 + 合并方式无模糊”才维持高，否则保守降为中
            after_conf = ("高" if confidences and all(c == "高" for c in confidences)
                          and not any(r.startswith("字符 jaccard") for r in reasons) else "中")
        elif status == "多源-待确认":
            after_conf = "待确认"
        else:
            after_conf = "低"
        clusters.append({
            "cluster_id": f"CM-{len(clusters) + 1:04d}",
            "display": display,
            "members": [{"concept": e.get("concept"), "cleaned": clean_concept(e.get("concept")),
                         "confidence_before": e.get("confidence"),
                         "n_sources_before": e.get("n_sources"),
                         "source_types_before": e.get("source_types")}
                        for e in entries_in],
            "n_members": len(merged_keys),
            "merged_keys": merged_keys,
            "merge_reasons": sorted(set(reasons)),
            "known_sources": known_sources,
            "unknown_sources": unknown,
            "merged_n_sources_lower": lower,
            "merged_n_sources_upper": upper,
            "source_types_after": sorted(known_types | {u["source_type"] for u in unknown}),
            "status": status,
            "confidence_after": after_conf,
        })
    clusters.sort(key=lambda c: (-c["n_members"], c["display"]))
    for idx, cluster in enumerate(clusters, 1):
        cluster["cluster_id"] = f"CM-{idx:04d}"

    conf_counts = Counter(c["confidence_after"] for c in clusters)
    stats = {
        "concepts_before": len(entries),
        "concepts_after": len(clusters),
        "clusters": len(clusters),
        "merged_clusters": sum(1 for c in clusters if c["n_members"] > 1),
        "merged_members": sum(c["n_members"] for c in clusters if c["n_members"] > 1),
        "junk": len(junk),
        "multi_before": sum(1 for e in entries if int(e.get("n_sources") or 0) >= MULTI_LOWER_MIN),
        "multi_after_confirmed": sum(1 for c in clusters if c["status"] == "多源-确认"),
        "multi_after_uncertain": sum(1 for c in clusters if c["status"] == "多源-待确认"),
        "confidence_after": dict(conf_counts),
        "merge_by_synonym": sum(1 for c in clusters for r in c["merge_reasons"] if r.startswith("同义")),
        "merge_by_contain": sum(1 for c in clusters for r in c["merge_reasons"] if r.startswith("包含")),
        "merge_by_fuzzy": sum(1 for c in clusters for r in c["merge_reasons"]
                              if r.startswith("字符 jaccard")),
    }
    return {"clusters": clusters, "junk": junk, "stats": stats}


# ---------------------------------------------------------------- 报告
def plan_from_input(doc: dict) -> tuple[list[dict], dict]:
    """从 W1-1 JSON 提取参与归并的概念条目 + 输入元信息。"""
    concepts = doc.get("concepts") or []
    multi = {str(m.get("concept")): m for m in doc.get("multi_source_concepts") or []}
    entries = []
    for c in concepts:
        entry = dict(c)
        if str(c.get("concept")) in multi:
            entry["sources"] = multi[str(c.get("concept"))].get("sources") or []
        entries.append(entry)
    meta = {
        "concepts_total": doc.get("meta", {}).get("concepts_total"),
        "multi_source_concepts": doc.get("meta", {}).get("multi_source_concepts"),
        "confidence_counts": doc.get("meta", {}).get("confidence_counts"),
    }
    return entries, meta


def build_payload(input_path: str | Path = DEFAULT_INPUT,
                  groups: list[tuple[str, ...]] | None = None) -> dict:
    """读入 W1-1 JSON（只读）→ 合并簇报告 dict。不写文件。"""
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"输入不存在: {path}")
    raw = path.read_bytes()
    sha_before = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw.decode("utf-8"))
    entries, in_meta = plan_from_input(doc)
    result = merge_concepts(entries, groups)
    sha_after = hashlib.sha256(path.read_bytes()).hexdigest()
    stats = result["stats"]
    clusters = result["clusters"]
    multi_clusters = [c for c in clusters if c["status"] == "多源-确认"]
    uncertain_clusters = [c for c in clusters if c["status"] == "多源-待确认"]
    merged_clusters = [c for c in clusters if c["n_members"] > 1]
    unchanged = sha_before == sha_after
    payload = {
        "meta": {
            "report": "concept_synonyms",
            "version": VERSION,
            "task": "W1-2 概念命名统一/同义归并",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "read_only": True,
            "read_only_verified": unchanged,
            "input": {"path": str(path), "sha256_before": sha_before,
                      "sha256_after": sha_after, "unchanged": unchanged,
                      "concepts_total": in_meta.get("concepts_total"),
                      "multi_source_concepts": in_meta.get("multi_source_concepts")},
            "params": {
                "synonym_groups": len(groups if groups is not None else DOMAIN_SYNONYM_GROUPS),
                "fuzzy_jaccard": FUZZY_JACCARD,
                "min_key_len": MIN_KEY_LEN,
                "max_contain_extra": MAX_CONTAIN_EXTRA,
                "normalization": "规则清洗（去括注/版本/序号/副标题/语气词）+ 领域同义表 + 包含/jaccard≥0.6 模糊合并",
                "merge_guard": "数字/序数/方向/窗口周期差异不合并；泛词不作包含短词；短词须为同义表规范词",
            },
            "stats": stats,
            "limitations": [
                "输入 W1-1 JSON 中「低置信」概念只保留 source_types、无 source_key：同类型单源概念合并后"
                "只能给出多源数下界/上界，独立来源确认需回源（标「多源-待确认」）。",
                "同义表/模糊合并会引入一定误并风险；已用数字/方向/窗口/泛词四类守卫，但仍建议人工抽检"
                "「多源-确认」簇后再抬置信。",
                "只做概念命名归并，不重新计算答案一致性/冲突；合并簇的置信等级仅为命名层面的保守估计。",
                "只读输入，不改学习卡/units/治理 JSON；本报告由 run() 写到指定输出路径。",
            ],
        },
        "stats": stats,
        "merged_multi_source": multi_clusters,
        "uncertain": {
            "multi_source_pending": uncertain_clusters,
            "unmerged_uncertain": [c for c in clusters if c["n_members"] == 1
                                   and c["status"] == "单源"][:50],
            "junk": result["junk"],
        },
        "merged_clusters": merged_clusters,
        "clusters": clusters,
    }
    return payload


def render_markdown(payload: dict) -> str:
    m = payload["meta"]
    s = payload["stats"]
    params = m["params"]
    lines: list[str] = []
    add = lines.append
    add("# 概念命名统一 / 同义归并（W1-2，2026-11-03）")
    add("")
    add(f"- 生成：{m['generated_at']} ｜ v{m['version']} ｜ 只读输入：**{m['read_only_verified']}**"
        f"（sha256 {'一致' if m['read_only_verified'] else '不一致'}）")
    add(f"- 输入：`{m['input']['path']}`（概念 {m['input'].get('concepts_total')} 个，"
        f"多源 {m['input'].get('multi_source_concepts')} 个）")
    add(f"- 规则：{params['normalization']}")
    add(f"- 同义表：**{params['synonym_groups']} 组** ｜ 模糊阈值：字符 jaccard ≥ {params['fuzzy_jaccard']}")
    add("")
    add("## 一、合并前后对比")
    add("")
    add("| 指标 | 合并前（W1-1） | 合并后（W1-2） |")
    add("|---|---:|---:|")
    add(f"| 概念数 | **{s['concepts_before']}** | **{s['concepts_after']}**（含 {s['junk']} 个噪声剔除） |")
    add(f"| 多源概念数 | **{s['multi_before']}** | **{s['multi_after_confirmed']}**（确认）+ "
        f"{s['multi_after_uncertain']}（待确认） |")
    add(f"| 合并簇（≥2 成员） | 0 | **{s['merged_clusters']}**（覆盖 {s['merged_members']} 个原始概念） |")
    add(f"| 合并触发 | — | 同义 {s['merge_by_synonym']} / 包含 {s['merge_by_contain']} / 模糊 {s['merge_by_fuzzy']} |")
    add(f"| 噪声概念剔除 | 0 | {s['junk']} |")
    add("")
    add("> 结论：合并后多源概念数 **≈" + f"{s['multi_after_confirmed']}"
        + ("（+待确认 " + str(s['multi_after_uncertain']) + "）" if s['multi_after_uncertain'] else "")
        + "**；W1-1 原为 " + str(s['multi_before']) + "。")
    add("")
    add("## 二、合并簇样例（多源确认 / 成员最多，Top 20）")
    add("")
    multi = payload["merged_multi_source"]
    if multi:
        add("| # | 合并后概念 | 成员数 | 合并方式 | 来源类型 | 多源下界 | 置信（合并后） |")
        add("|---:|---|---:|---|---|---:|---|")
        for i, c in enumerate(multi[:20], 1):
            add(f"| {i} | **{c['display']}** | {c['n_members']} | "
                f"{'；'.join(c['merge_reasons'])[:80] or '同义'} | "
                f"{'/'.join(SOURCE_TYPE_LABEL.get(t, t) for t in c['source_types_after'])} | "
                f"{c['merged_n_sources_lower']} | {c['confidence_after']} |")
    else:
        add("（无多源确认簇）")
    add("")
    add("### 多源确认簇详情（成员原名对照）")
    add("")
    for c in multi[:12]:
        add(f"- **{c['display']}**（{c['n_members']} 个原始概念 → 下界 {c['merged_n_sources_lower']} 源，"
            f"{'/'.join(SOURCE_TYPE_LABEL.get(t, t) for t in c['source_types_after'])}）："
            + "；".join(m["concept"] for m in c["members"]))
    add("")
    add("## 三、未合并 / 不确定清单")
    add("")
    add(f"- **多源待确认**（合并后可能有 ≥2 源但缺 source_key，需回源确认）：{s['multi_after_uncertain']} 个簇")
    uncertain = payload["uncertain"]
    for c in uncertain["multi_source_pending"][:10]:
        add(f"  - {c['display']}（成员 {c['n_members']}，类型 "
            f"{'/'.join(SOURCE_TYPE_LABEL.get(t, t) for t in c['source_types_after'])}，"
            f"下界 {c['merged_n_sources_lower']}/上界 {c['merged_n_sources_upper']}）")
    add(f"- **噪声剔除**：{len(uncertain['junk'])} 个"
        + ("：" + "、".join(f"`{j['concept']}`({j['reason']})" for j in uncertain["junk"][:12])
           if uncertain["junk"] else ""))
    add(f"- **未合并单源概念**：{s['concepts_after'] - s['multi_after_confirmed'] - s['multi_after_uncertain']} 个"
        "（命名差异大或本就单源，清单见 JSON `clusters`；下列为示例）")
    for c in uncertain["unmerged_uncertain"][:10]:
        add(f"  - {c['display']}")
    add("")
    add("## 四、同义表（内置 %d 组，节选 30 组）" % params["synonym_groups"])
    add("")
    add("| 规范词 | 别名 |")
    add("|---|---|")
    for group in DOMAIN_SYNONYM_GROUPS[:30]:
        add(f"| {group[0]} | {'、'.join(group[1:])} |")
    add("")
    add("## 五、下一步建议")
    add("")
    add("1. 对「多源-确认」簇回源抽检 10~20 个，验证同义合并是否误并（尤其量学词与研报主题词）；")
    add("2. 对「多源-待确认」簇回源补齐 source_key（同一博主/书/研报识别），把上界收敛为确认值；")
    add("3. 在 W1-1 pipeline 里前置本模块的 `clean_concept + synonym_map`（同一套表），减少 W1-1 阶段的概念分裂；")
    add("4. 长尾概念建议用 LLM 语义归并（人工抽检阈值 0.8+），但需保留可回滚的映射表（本模块表即底稿）。")
    add("")
    add("## 六、口径与局限")
    add("")
    for i, item in enumerate(m["limitations"], 1):
        add(f"{i}. {item}")
    add("")
    add("## 七、复现")
    add("")
    add("```powershell")
    add("C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/concept_synonyms.py")
    add("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_concept_synonyms.py")
    add("```")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 落盘 / CLI
def run(input_path: str | Path = DEFAULT_INPUT, out_json: str | Path | None = None,
        out_md: str | Path | None = None) -> dict:
    payload = build_payload(input_path)
    jp = Path(out_json) if out_json else OUT_JSON
    mp = Path(out_md) if out_md else OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="concept_synonyms",
                                 description="概念命名统一/同义归并（W1-2）：只读 W1-1 JSON → 合并簇/多源数/不确定清单")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="W1-1 多源 JSON（只读）")
    ap.add_argument("--out", default=str(OUT_JSON), help="输出 JSON 路径")
    ap.add_argument("--md", default=str(OUT_MD), help="输出 Markdown 路径")
    ap.add_argument("--json-stdout", action="store_true", help="stdout 打印完整 JSON")
    args = ap.parse_args(argv)
    try:
        payload = run(args.input, args.out, args.md)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[concept_synonyms] 运行失败: {exc}", file=sys.stderr)
        return 2
    s = payload["stats"]
    print(f"# 概念归并 v{payload['meta']['version']}：概念 {s['concepts_before']} → {s['concepts_after']}"
          f"（噪声 {s['junk']}，合并簇 {s['merged_clusters']}）；多源 {s['multi_before']} → "
          f"{s['multi_after_confirmed']} 确认 + {s['multi_after_uncertain']} 待确认")
    print(f"# 输出: {Path(args.out).resolve()}")
    print(f"# 输出: {Path(args.md).resolve()}")
    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
