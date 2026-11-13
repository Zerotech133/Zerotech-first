# -*- coding: utf-8 -*-
"""jev_prefilter.py — JEV 两段式推理的「第一段：规则粗筛」（K-ORG-20261026-JEV2STAGE）。

背景
----
JEV 判读当前每条都要跑 2 次 LLM（9B 判读 + reason），夜间实测 1,300/h 封顶。
但新闻分布里大量是**例行公告/行情播报/会议通知**——这些按资产价格口径本来就该是 neutral，
用规则就能安全跳过，把 9B 算力让给真有方向的条目。

设计原则（**保守优先，宁漏不误**）
  · 只有在「命中中性模式」且「没有任何方向性关键词」时才 skip；
  · 任何不确定 → keep（交给 9B）；
  · skip 的条目仍写库（bias=neutral, src=prefilter, 带 reason），可被下游统计，
    也可通过 JEV_PREFILTER=0 全量回退。
  · 本模块纯函数、无 IO、无 LLM，便于单测与 A/B 基准。

灰度开关
  news_collector/jev_backfill.py 读环境变量 JEV_PREFILTER（默认 0=关）。
"""

from __future__ import annotations

import os
import re
from typing import Any

# ── 方向性关键词（命中即 keep：宁可多判，不可漏判）────────────────────────
DIRECTIONAL_PATTERNS = [
    # 业绩/财务
    r"预增|预减|预盈|预亏|扭亏|亏损|净利|营收|毛利率|业绩预告|业绩快报|财报",
    # 资本运作
    r"回购|增持|减持|解禁|定增|配股|并购|收购|重组|注入|分拆|股权转让|要约",
    # 风险事件
    r"立案|处罚|罚款|警示|问询|监管|退市|ST|停牌|复牌|爆雷|违约|诉讼|仲裁|冻结|失信",
    # 经营事件
    r"中标|合同|订单|涨价|降价|提价|产能|扩产|投产|量产|裁员|召回|停产|检修",
    # 政策/宏观方向
    r"降息|加息|降准|上调|下调|补贴|关税|制裁|出口管制|获批|批准|试点|放开|收紧",
    # 分红/回报
    r"分红|派息|送转|特别股息",
    # 2026-10-30 BATCH4：补齐审计发现的误杀方向词（原缺 → 破产/涨停/跌停被判中性）
    r"破产|重整|清算|涨停|跌停|连板|封板|开板|暴涨|暴跌|大涨|大跌|闪崩",
    r"净流入|净流出|收益率|申购|赎回|解禁|质押|爆仓|平仓",
]

# ── 例行/中性模式（命中即候选 skip；仍需先确认无方向性词）────────────────
NEUTRAL_PATTERNS = [
    # 行情播报与盘面综述
    r"(收盘|开盘|午盘|早盘|尾盘|盘中).{0,12}(涨|跌|报|收)",
    r"(三大指数|指数|沪指|深成指|创业板指|恒指|道指|纳指|标普).{0,10}(涨|跌|收|报)",
    r"(成交额|成交量|换手率|振幅|涨跌幅)\s*\d",
    r"(油价|金价|银价|铜价|汇率|人民币).{0,8}(涨|跌|报|收)",
    r"(美股|港股|A股|欧股|亚太).{0,10}(收|涨|跌)",
    r"(期货|主力合约).{0,10}(涨|跌|收)",
    # 例行公告/公司治理
    r"股东大会.{0,6}(通知|决议|提示)",
    r"(董事会|监事会).{0,6}(决议|会议)",
    r"独立董事.{0,20}(意见|候选人|声明|专项)",
    r"(章程|议事规则|管理制度).{0,6}(修订|审议)",
    r"(修订|审议).{0,8}(章程|议事规则|管理制度)",
    r"投资者关系.{0,8}(活动|记录|接待)",
    r"(证券变动|股份变动)月报表",
    r"(定期报告|年度报告|季度报告|中期报告|年报|半年报|季报).{0,10}(披露|预约|提示)",
    r"(董监高|高管).{0,6}(变动|辞职|聘任)(?!.*(减持|增持))",
    # 日程/服务类
    r"(日程|会议|论坛|峰会|发布会|路演).{0,8}(预告|提醒|通知|将举行|举行)",
    r"(休市|假期|交易日)安排",
    r"(新股|可转债).{0,6}(申购|上市|缴款)提示",
    # 纯信息复述
    r"^(据统计|数据显示|据.{0,6}报道|消息称)",
]

# ── SAFE 模式：仅 routine 治理模板（2026-10-30 PREFILTEREVAL 数据验证）──────
# 评估（近 30 天 2 万条已判）：旧全量预筛 skip 1,449 条中 840 条是模型高置信方向
# （误杀率 58%），不可开启。按模式拆分后「董事会决议/独董声明/章程修订」等
# routine 模板精度 100%（各 n=12-20），可安全作为 skip 白名单。
SAFE_NEUTRAL_PATTERNS = [
    r"股东大会.{0,8}(通知|决议|提示)",
    r"(董事会|监事会).{0,8}(决议|会议).{0,6}(公告|决议)?",
    r"独立董事.{0,24}(候选人|提名人|声明|意见|专项)",
    r"(章程|议事规则|管理制度).{0,8}(修订|审议)",
    r"(修订|审议).{0,8}(章程|议事规则|管理制度)",
    r"(证券变动|股份变动)月报表",
    r"(定期报告|年度报告|季度报告|中期报告|年报|半年报|季报).{0,6}(披露|预约)提示",
    r"(新股|可转债).{0,6}(申购|上市|缴款)提示",
    r"(休市|假期|交易日)安排",
]
_SAFE_RE = [re.compile(p) for p in SAFE_NEUTRAL_PATTERNS]

# ── 2026-11-07 RFC-20261107 方案 A：公告类例行模板白名单 ──────────────────
# 逐模板精度抽检（近 30 日已判公告，生产守门条件 = 无方向词 / 无 6 位代码 /
# 未命中既有 safe 模板）：四模板并集 812 命中、0 误杀（0.00%）。
# 【必须按 source 限定】实证：「投资者关系活动记录表」在其他源 23 命中 13 误杀
# （56.5%）、「制度内控报告」4 命中 2 误杀（50%）——新闻源里这类标题多为调研
# 纪要，模型会判出方向性。故仅对 xd_notice / announcement 生效。
NOTICE_SOURCES = ("xd_notice", "announcement")
NOTICE_ENV = "JEV_PREFILTER_NOTICE"
NOTICE_REASON = "例行公告"
ROUTINE_NOTICE_PATTERNS = (
    r"投资者关系活动记录表",
    r"(业绩说明会|投资者说明会).{0,8}(公告|通知|召开)",
    r"(内部控制|内控|审计).{0,10}(制度|报告|评价)",
    r"关于召开.{0,24}(股东会|股东大会).{0,6}(通知|提示)",
)
_NOTICE_RE = [re.compile(p) for p in ROUTINE_NOTICE_PATTERNS]


def _env_on(name, default="1"):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "off", "false", "no", "",
    )


def notice_enabled(source="", mode="safe"):
    """新模板仅在 safe 模式 + 公告类 source + 开关开启时生效。"""
    return (
        mode == "safe"
        and str(source or "").strip().lower() in NOTICE_SOURCES
        and _env_on(NOTICE_ENV)
    )


def notice_hit(title, source="", mode="safe"):
    """返回命中的公告例行模板文本；未命中或不适用返回 None。"""
    if not notice_enabled(source, mode):
        return None
    for rx in _NOTICE_RE:
        m = rx.search(title or "")
        if m:
            return m.group(0)
    return None


def safe_neutral_hit(title: str) -> str | None:
    text = title or ""
    for rx in _SAFE_RE:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


# ── 来源权重（仅用于 reason 标注，不单独决定 skip）──────────────────────
SOURCE_TRUST = {
    "announcement": "high",
    "govpolicy": "high",
    "report": "mid",
    "sina": "mid",
    "eastmoney": "mid",
    "chinanews": "mid",
    "wallstreetcn": "mid",
    "world": "mid",
    "survey": "low",
}

_DIR_RE = [re.compile(p) for p in DIRECTIONAL_PATTERNS]
_NEU_RE = [re.compile(p) for p in NEUTRAL_PATTERNS]


def directional_hit(title: str) -> str | None:
    """返回命中的第一个方向性正则（用于 reason/证据）。"""
    text = title or ""
    for rx in _DIR_RE:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def neutral_hit(title: str) -> str | None:
    """返回命中的第一个中性模式正则。"""
    text = title or ""
    for rx in _NEU_RE:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def decide(title: str, source: str = "", stocks: list | None = None,
           mode: str = "aggressive") -> dict[str, Any]:
    """粗筛决策 → {decision: keep|skip, reason, bias_hint, matched}。

    公式：方向性词命中 → keep；否则中性模式命中 → skip；否则 keep（宁漏不误）。
    """
    text = (title or "").strip()
    if not text:
        return {
            "decision": "skip",
            "reason": "空标题",
            "bias_hint": None,
            "matched": "",
            "source": source,
        }
    d = directional_hit(text)
    if d:
        return {
            "decision": "keep",
            "reason": "方向性词",
            "bias_hint": None,
            "matched": d,
            "source": source,
        }
    # 2026-10-30 BATCH4：标题出现 6 位个股代码时，不接受「例行/行情」skip
    # （实证误杀："开盘涨停！000678，6连板！"）。
    has_stock_code = bool(re.search(r"(?<!\d)\d{6}(?!\d)", text))
    hit_fn = safe_neutral_hit if mode == "safe" else neutral_hit
    n = hit_fn(text)
    # 2026-11-07 RFC-20261107 方案 A：旧模板未命中时再试公告例行模板，
    # 并用独立 reason 标记，便于 §7 的 SQL 精确回退。
    n_notice = notice_hit(text, source, mode) if n is None else None
    matched = n or n_notice
    reason = NOTICE_REASON if (n is None and n_notice) else "例行/行情"
    if matched and has_stock_code:
        return {
            "decision": "keep",
            "reason": "含个股代码（保守保留，防误杀）",
            "bias_hint": None,
            "matched": matched,
            "source": source,
        }
    if matched:
        return {
            "decision": "skip",
            "reason": reason,
            "bias_hint": "neutral",
            "matched": matched,
            "source": source,
        }
    return {
        "decision": "keep",
        "reason": "未命中中性模式（保守保留）",
        "bias_hint": None,
        "matched": "",
        "source": source,
    }


def prefilter_jev(title: str, source: str = "", mode: str = "aggressive") -> dict[str, Any] | None:
    """给 jev_backfill 用：skip 时返回可落库的 jev dict，keep 时返回 None。

    落库 dict 与 news_ai 的本地判读结构保持同构（少 reason/event_type 细节），
    并带 src=prefilter 与 pref 证据字段，便于审计与回退。
    """
    r = decide(title, source, mode=mode)
    if r["decision"] != "skip":
        return None
    return {
        "bias": "neutral",
        "bias_cn": "中性",
        "conf": 0.5,
        "scope": "",
        "scope_cn": "",
        "event_type": "other",
        "event_cn": "其他",
        "impact": "low",
        "impact_cn": "低",
        "companies": [],
        "src": "prefilter",
        "pref": {"reason": r["reason"], "matched": r["matched"], "source": source},
        "reason": "规则粗筛：%s（%s）" % (r["reason"], r["matched"] or "-"),
        # 2026-11-01 PREFILTERV2：契约 v2 字段（skip 行也是最终判定，必须与主路径同 schema）
        "horizon": "unknown",
        "prob": {"bull": 0.25, "bear": 0.25, "neutral": 0.5},
        "bias_probs": {"bull": 0.25, "bear": 0.25, "neutral": 0.5},
        "meter": {"ms": 0, "model": "prefilter", "src": "prefilter"},
    }


def is_prefilter_judgment(jev_text: str) -> bool:
    """该 JEV JSON 是否由预筛规则写入（src=prefilter），用于回退工具。"""
    import json as _json

    try:
        d = _json.loads(jev_text or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(d, dict) and str(d.get("src") or "") == "prefilter"


# ══════════════════════════════════════════════════════════════════════════
# 公告类口径豁免（2026-11-13 老田裁定 B 案：先止消耗）
#
# 为什么豁免
#   datamart 的 `notice` 别名只有 8 列，xtcloud 的 `stock_notice` 字段并集也
#   只有 notice_date —— 两者都没有发布时间。news.ts 因此恒为 00:00，被
#   jev_graph._ts_kind 判为 batch，本就不参与 Δmin 与 hit 边。日增 1,127 条
#   （约占新增三分之一）却要逐条跑 9B，价值密度最低。
#
# 三条硬约束（别改，改了就是造假）
#   1. bias 必须是 "na"，**绝不能写 neutral**。写 neutral 等于让下游把「没判读」
#      当成「判过了且中性」，正是判例 #43 禁止的自安慰。
#   2. 不写 judged_at。judged_at 是「LLM 真判过」的证据；豁免行若写它，
#      每日判读量统计会虚高 1,127，把真实产能缺口盖住。
#      ↑ 2026-11-13 实测：按 judged_at 算的逐日平衡表是发现入不敷出的唯一凭据。
#   3. 不带 prob / bias_probs。没有概率就是没有，不编 0.25/0.25/0.5。
#
# 回退（单条 SQL，幂等可逆）
#   UPDATE news SET jev=NULL, model=NULL WHERE model='notice_exempt';
#
# 开关
#   JEV_NOTICE_EXEMPT=0            全关，退回逐条判读
#   JEV_NOTICE_EXEMPT_SOURCES=a,b  改豁免源，默认 xd_notice
# ══════════════════════════════════════════════════════════════════════════
EXEMPT_SRC = "notice_exempt"
EXEMPT_ENV = "JEV_NOTICE_EXEMPT"
EXEMPT_SOURCES_ENV = "JEV_NOTICE_EXEMPT_SOURCES"
EXEMPT_SOURCES_DEFAULT = "xd_notice"


def exempt_on():
    """豁免总开关，默认开（老田 2026-11-13 已裁定）。"""
    return _env_on(EXEMPT_ENV, "1")


def exempt_sources():
    """豁免源元组。未设该 env → 默认 xd_notice；**显式设为空串 → 空元组（禁用）**。

    别写成 `os.environ.get(K) or DEFAULT`：那样空串会落回默认值，
    「设空来禁用」这个动作会静默失效（2026-11-13 测试抓到过一次）。
    """
    raw = os.environ.get(EXEMPT_SOURCES_ENV)
    if raw is None:
        raw = EXEMPT_SOURCES_DEFAULT
    return tuple(s.strip() for s in str(raw).split(",") if s.strip())


def exempt_jev(source=""):
    """豁免载荷：是「不判读」的显式标记，不是判读结果。"""
    return {
        "bias": "na",
        "bias_cn": "不适用",
        "conf": 0.0,
        "scope": "",
        "scope_cn": "",
        "event_type": "other",
        "event_cn": "其他",
        "impact": "none",
        "impact_cn": "无",
        "companies": [],
        "src": EXEMPT_SRC,
        "exempt": {
            "policy": "JEV_NOTICE_EXEMPT",
            "reason": "公告上游只有日期粒度，无发布时间；ts 恒 00:00 → ts_kind=batch",
            "source": source,
        },
        "reason": "口径豁免：公告仅日粒度，不参与 LLM 判读（判例#42 降为附表）",
        "horizon": "unknown",
        "meter": {"ms": 0, "model": EXEMPT_SRC, "src": EXEMPT_SRC},
    }


def apply_notice_exempt(db, sources=None):
    """把指定源的未判行批量标为豁免（幂等）。返回受影响行数。

    db 为已连接的 sqlite3.Connection；本函数自行 commit。
    只写 jev 与 model，**不写 judged_at**（见上方约束 2）。
    """
    import json as _json

    if not exempt_on():
        return 0
    srcs = tuple(sources) if sources else exempt_sources()
    if not srcs:
        return 0
    payload = _json.dumps(exempt_jev(srcs[0]), ensure_ascii=False)
    placeholders = ",".join("?" * len(srcs))
    cur = db.execute(
        "UPDATE news SET jev=?, model=? "
        "WHERE source IN (%s) AND (jev IS NULL OR jev='')" % placeholders,
        (payload, EXEMPT_SRC) + srcs,
    )
    db.commit()
    return cur.rowcount

