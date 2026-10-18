# -*- coding: utf-8 -*-
"""雪球线「指数→标的映射」模块（预案 M-H4 前置件：指数→标的映射，纯新增未接线）。

出处（2026-10-18）：docs/2026-10-18_雪球情报数据两线预案.md 承诺项
「指数→标的映射（沪深300→000300.SH）」——此前无实现无跟进，
复盘报告（docs/2026-10-18_雪球采集线复盘审查报告.md）已列入缺失清单。
本模块为失联前置件的补位实现：独立模块 + 独立测试，不改任何现有文件、
不 import 进任何现有链路，接线时机与落点（日报层 or 因子层）留待拍板。

设计（对齐 xq_classify.py 风格：模块级常量表 + 纯函数）：
  - 映射表 _INDEX_TABLE：规范名 / 交易所代码 / 别名集 / 备注 四元组，
    A股 .SH/.SZ、北交所 .BJ、港股 .HK 后缀齐全，代码口径对齐雪球/tushare；
  - 归一化：NFKC（全角→半角）+ 去全部空白 + 小写，别名表键同样归一，
    「ＨＳ３００」「沪深  300」与「hs300」同键命中；
  - extract_index_mentions：压缩文本上按别名长度降序做区间屏蔽式扫描
    （最长优先，防「上证50」被内嵌别名误切），按首次出现位置去重保序。
  - 已知歧义注记：上证指数 000001.SH 与平安银行 000001.SZ 同码异市，
    本模块只管指数侧；个股侧归属 xq_classify tickers 提取器职责，勿混。

美股取舍（2026-10-18 工位注明）：道指/纳指/标普500 暂缓收录——
  美股指数代码体系不统一（雪球 .DJI/.IXIC/.INX 与 tushare/intl 口径分歧），
  且本模块代码格式契约约束 .SH/.SZ/.BJ/.HK 后缀；待 M-H4 接线口径拍板后
  再扩表，避免半吊子代码污染下游。

用法：
  from xq_index_map import normalize_index_name, lookup_index, extract_index_mentions
  lookup_index("hs300")            -> "000300.SH"
  extract_index_mentions("沪深300与恒科走弱")  -> [("沪深300", "000300.SH"), ("恒生科技", "HSTECH.HK")]
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# 指数映射表（≥15 条）。字段：(规范名, 代码, 别名集, 备注)
#   - 规范名自身隐式进入别名表（无需在 aliases 里重复）
#   - 代码后缀：.SH=上交所 / .SZ=深交所 / .BJ=北交所 / .HK=港交所
#   - 中证A500 代码 000510.SH 系公开行情口径，上线前建议再核一次
# ---------------------------------------------------------------------------
_INDEX_TABLE = [
    ("沪深300", "000300.SH",
     ("沪深300指数", "hs300", "hs 300", "csi300", "csi 300", "中证300", "沪深三百"),
     "A股核心宽基，M-H4 首要目标"),
    ("上证指数", "000001.SH",
     ("上证", "上证综指", "上证综合指数", "沪指", "沪市大盘", "大盘", "sh000001"),
     "与平安银行 000001.SZ 同码异市，本模块只管指数侧"),
    ("上证50", "000016.SH",
     ("上证50指数", "上证五十", "sse50"),
     ""),
    ("上证180", "000010.SH",
     ("上证180指数",),
     ""),
    ("深证成指", "399001.SZ",
     ("深成指", "深圳成指", "深证成分指数", "深市大盘"),
     ""),
    ("创业板指", "399006.SZ",
     ("创业板指数", "创业板", "cyb"),
     "「创业板」泛称归创业板指；创业板50 另行扩表时再拆"),
    ("科创50", "000688.SH",
     ("科创50指数", "科创板50", "科创五十", "star50"),
     ""),
    ("中证500", "000905.SH",
     ("中证500指数", "中证五百", "csi500"),
     ""),
    ("中证1000", "000852.SH",
     ("中证1000指数", "csi1000"),
     ""),
    ("中证800", "000906.SH",
     ("中证800指数", "csi800"),
     ""),
    ("中证A500", "000510.SH",
     ("中证a500指数", "a500", "中证a五百"),
     "代码 000510.SH 待上线前再核（2026-10-18 工位注记）"),
    ("中证红利", "000922.SH",
     ("中证红利指数", "红利指数"),
     ""),
    ("北证50", "899050.BJ",
     ("北证50指数", "北交所50"),
     ""),
    ("恒生指数", "HSI.HK",
     ("恒指", "hsi"),
     "「恒生电子(600570)」是 A 股个股，勿因「恒生」前缀误命中——本表不收裸「恒生」别名（对齐 xq_classify _HK_CONTEXT 注记）"),
    ("恒生科技", "HSTECH.HK",
     ("恒生科技指数", "恒科", "hstech"),
     ""),
    ("国企指数", "HSCEI.HK",
     ("恒生中国企业指数", "h股指数", "h股", "hscei"),
     ""),
]

# ---------------------------------------------------------------------------
# 构建归一化别名索引（模块加载时一次性建好，查询纯查表）
# ---------------------------------------------------------------------------


def _compress(text) -> str:
    """文本归一：NFKC 全角→半角 + 去全部空白 + 小写。"""
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", t).lower()


# compressed 别名 -> (规范名, 代码)；同名键先到先得（首条表项优先）
_ALIAS_MAP: dict = {}
for _name, _code, _aliases, _note in _INDEX_TABLE:
    for _a in (_name,) + tuple(_aliases):
        _k = _compress(_a)
        if _k and _k not in _ALIAS_MAP:
            _ALIAS_MAP[_k] = (_name, _code)

# 别名按长度降序（extract 扫描用：最长优先，减少内嵌误切）
_SORTED_ALIASES = sorted(_ALIAS_MAP.items(), key=lambda kv: -len(kv[0]))


def normalize_index_name(text) -> str | None:
    """任意写法 → 规范名；未知返回 None。

    大小写/全角/空白不敏感：「ＨＳ３００」「沪深  300」→「沪深300」。
    """
    hit = _ALIAS_MAP.get(_compress(text))
    return hit[0] if hit else None


def lookup_index(name_or_alias) -> str | None:
    """规范名或别名 → 交易所代码；未知返回 None。

    lookup_index("恒指") -> "HSI.HK"；lookup_index("沪深三百") -> "000300.SH"。
    """
    hit = _ALIAS_MAP.get(_compress(name_or_alias))
    return hit[1] if hit else None


def _boundary_ok(comp: str, i: int, end: int, alias: str) -> bool:
    """ASCII 字母数字边界检查（防嵌入型噪音误报）。

    「沪深3000」不得命中「沪深300」（尾边界：别名末字符是 ASCII alnum 时，
    紧随字符不得也是 ASCII alnum）；「xhs300」不得命中「hs300」（首边界同理）。
    中文别名末字不受限（「上证指数3000点」正常命中）。
    """
    if end < len(comp) and alias[-1].isascii() and alias[-1].isalnum() \
            and comp[end].isascii() and comp[end].isalnum():
        return False
    if i > 0 and alias[0].isascii() and alias[0].isalnum() \
            and comp[i - 1].isascii() and comp[i - 1].isalnum():
        return False
    return True


def extract_index_mentions(text) -> list:
    """长文本中提取指数提及 → [(规范名, 代码), ...]，去重保序。

    做法：压缩文本（NFKC+去空白+小写）上按别名长度降序扫描，命中区间
    屏蔽防内嵌嵌套（「上证50」整段吃掉后不再被「上证」重切）；同一指数
    多别名命中去重，按压缩文本首次出现位置保序。未知词天然不命中、不误报。
    """
    comp = _compress(text)
    if not comp:
        return []
    taken: list = []          # 已占用区间 [(start, end), ...]
    first_pos: dict = {}      # 规范名 -> 首次出现位置
    for alias, (name, code) in _SORTED_ALIASES:
        start = 0
        while True:
            i = comp.find(alias, start)
            if i < 0:
                break
            end = i + len(alias)
            if not _boundary_ok(comp, i, end, alias):
                start = i + 1
                continue
            if any(i < e and s < end for s, e in taken):
                start = i + 1
                continue
            taken.append((i, end))
            if name not in first_pos:
                first_pos[name] = i
            start = end
    if not first_pos:
        return []
    # 收集去重后的 (规范名, 代码)，按首次出现位置排序
    code_of = {name: code for name, code, _, _ in _INDEX_TABLE}
    return sorted(
        [(name, code_of[name]) for name in first_pos],
        key=lambda nc: first_pos[nc[0]])
