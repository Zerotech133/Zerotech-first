# -*- coding: utf-8 -*-
"""JEV 组合 pattern 匹配引擎（P5.1 · 2026-11-10）。

定位（真相源 v2 §阶段 P）：JEV 是 **OSINT 多维信号收集 + pattern 识别 +
演化推演引擎**，不是预测器、也不是事后解释器。本模块只做中间那一层：
把一只票某一天的三维度信号，映射成「命中了哪些候选组合 pattern」。

## 三维度（P3 已拍板：放弃结构维度）

    资金  jev_flow.load_flow / flow_of     main_net, net_mf（万元）
    社交  jev_social.load_social / heat_of  xq_*_rank, guba_rank, posts, reads
    时间  Δmin = T_news − T_limit_seal     P1.4 定义文档 docs/jev_deltamin_spec.md

## 本模块的硬纪律

1. **不联网、不写盘、不采集**。装配层只读既有快照 / 本地 parquet / news.db 只读连接。
   采集是 `tools/*_fetch.py` 的事（JEV 职责边界：我们只回答「拿到了说明什么」）。
   资金面特意走 `jev_flow._snap_read` 而不是 `load_flow`——后者在无快照时会去
   xtcloud 拉数，离线装配绝不能意外触网。
2. **缺失不等于 0，也不等于「不热」**。每个维度判定返回 `True / False / None`：
   `None` = 该维度对本票无数据（未覆盖 / unpriced），既不记命中也不记反向。
   社交快照只覆盖榜单 Top100 + 选票 60 只，全市场 5000+ 只里绝大多数是未覆盖，
   把它们判成「机构沉默」会直接伪造 11-06 判例的结论。
3. **Δmin 任一锚点缺失即 unpriced，绝不硬算**（判例 #25③）。价格锚点取活源
   `quant_backtest/data/tushare/stk_mins/`，禁用只到 2026-10-23 的 curated 快照。
4. **≥2 个维度命中才记入 pattern**（P4 §3 契约），单维度不算组合信号。
5. **阈值全部集中在 `Thresholds`，是 P5 案例库校准的起点，不是结论**。
   首轮跑出来的覆盖率是探索性数字，未经 P5.4 校准不得直接进判定闸 A 条件②。

## 数据契约实测修正（相对 P1.4 §1.2 的勘误）

- `news.stocks` 列存的是**中文名称 JSON 数组**（如 `["中国平安"]`、`["英伟达"]`），
  **不是**逗号分隔 ts_code；拿它 `LIKE '%000001%'` 会零命中。
- 真实可用的新闻→票通道是 `news.jev` JSON 里的 `companies` / `stocks` 数组中的
  6 位数字代码（与既有 `jev_graph.load_events` 同口径）。本模块复用该口径。
- 日级源（announcement / xd_notice / report / survey）ts 批量落在 00:00，
  `ts_kind=batch`，按 P1.4 §3.1 只走日级追认，不进分钟 Δmin。

## 符号口径差异（别混）

- 本模块：`Δmin = T_news − T_limit_seal`，**正 = 价格先行/消息追认**，负 = 消息驱动。
- `jev_graph.first_move_after` 的 `lag_min` 是 `价格 − 消息`，**符号相反**，且锚点是
  「首次异动」而非「首次封板」。两者不通用，本模块只复用 `_ts_kind` 与 `limit_state`。

## 契约（P4 §3）

    match(code, day) -> list[Hit]
    Hit = {pattern, name, dims, values, confidence, hypothesis, fork}

CLI:  python portal/jev_pattern.py --day YYYYMMDD [--limit N] [--code CODE] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def _aware(dt):
    """统一成带时区的时间，避免 naive/aware 相减 TypeError。

    `news_time.parse_full_time` 产出的是 CST aware；但 P5.2 案例库可能从
    别的快照注入 naive 时间。在模块边界一次性规范，比要求所有上游一致可靠。
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=CST)

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
for _p in (HERE, os.path.join(STATION, "news_collector")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jev_flow  # noqa: E402
import jev_graph as G  # noqa: E402
import jev_social  # noqa: E402
from news_time import parse_full_time, parse_news_time  # noqa: E402

NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
MINS_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare", "stk_mins")
DAILY_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare", "daily")
NAMES_JSON = os.path.join(STATION, "quant_backtest", "data", "ashare", "_names.json")

# P1.4 §1.2：真实分钟级白名单（扫描实测 99–100% 带分钟戳）
MINUTE_SOURCES = frozenset({
    "sina", "eastmoney", "wallstreetcn", "chinanews", "world",
    "xd_news", "wechat_kol", "govpolicy", "xq_kol",
})
# 天然只有日期的源：只做日级追认，绝不进分钟 Δmin（P1.4 §3.1）
DAY_LEVEL_SOURCES = frozenset({
    "announcement", "xd_notice", "report", "survey",
})


# ── 阈值（P5 校准起点，不是结论） ──────────────────────────────
@dataclass(frozen=True)
class Thresholds:
    """所有阈值集中在此，P5.3/P5.4 在案例库上校准后整体替换，禁止散落硬编码。"""

    th_lead: float = 5.0           # Δmin > +5min → 价格先行 / 消息追认
    th_drive: float = 5.0          # Δmin < −5min → 消息驱动
    th_lead_strong: float = 30.0   # 「价格提前 ≥30min」的强形态
    th_drive_strong: float = 30.0  # 「消息提前 ≥30min」的强形态
    flow_eps: float = 50.0         # 万元：|净额| 低于此视为资金中性
    flow_mild: float = 300.0       # 万元：机构埋伏型「net_mf 小幅」上界
    xq_top_n: int = 20             # 雪球四榜进前 N = 机构圈关注
    guba_top_n: int = 20           # 股吧人气榜进前 N = 散户追捧
    hot_posts: int = 15            # 帖子数热度线
    hot_reads: int = 5000          # 阅读数热度线


DEFAULT_TH = Thresholds()

# 命中维度权重（默认等权；校准后可调）
DIM_WEIGHTS = {"fund": 1.0, "social": 1.0, "time": 1.0}
MIN_DIMS = 2  # ≥2 维度命中才记（P4 §3）


# ── 信号与命中 ────────────────────────────────────────────────
@dataclass
class Signal:
    """一只票一天的三维度原始信号。所有可选值用 None 表示「本票无此数据」。

    `*_has` 表示**整体数据源**是否在位；具体字段仍可能为 None（该票未被覆盖）。
    这两层区分是刻意的：快照存在 != 该票在榜。
    """

    code: str
    day: str = ""
    # 资金
    main_net: float | None = None
    net_mf: float | None = None
    flow_has: bool = False
    # 社交
    xq_hot_rank: int | None = None
    xq_discuss_rank: int | None = None
    xq_trade_rank: int | None = None
    xq_follow_rank: int | None = None
    guba_rank: int | None = None
    guba_rank_chg: int | None = None
    posts: int | None = None
    reads: int | None = None
    comments: int | None = None
    social_has: bool = False
    # 时间（Δmin）
    delta_min: float | None = None
    delta_kind: str = "unpriced"      # price_lead / news_drive / sync / unpriced
    t_limit_seal: str | None = None
    t_limit_touch: str | None = None
    t_news_first: str | None = None
    t_news_last: str | None = None
    news_n: int = 0
    touch_only: bool = False
    delta_reason: str | None = None

    def social_covered(self) -> bool:
        """该票是否真的被社交快照覆盖（在榜或有帖子）。"""
        return any(v is not None for v in (
            self.xq_hot_rank, self.xq_discuss_rank, self.xq_trade_rank,
            self.xq_follow_rank, self.guba_rank, self.posts, self.reads,
        ))


@dataclass
class Hit:
    pattern: str
    name: str
    dims: dict = field(default_factory=dict)        # fund/social/time → True/False/None
    values: dict = field(default_factory=dict)      # 命中的原始维度值
    confidence: float = 0.0
    hypothesis: str = ""                            # 预期演化（待 P5 案例库验证）
    fork: str = ""                                  # 分化节点假设

    def as_dict(self) -> dict:
        return asdict(self)


def _sig_values(s: Signal) -> dict:
    """Hit.values：P4 契约要求的字段级留痕（含 None，方便回溯为何没命中）。"""
    return {
        "code": s.code, "day": s.day,
        "main_net": s.main_net, "net_mf": s.net_mf,
        "xq_hot_rank": s.xq_hot_rank, "xq_discuss_rank": s.xq_discuss_rank,
        "xq_trade_rank": s.xq_trade_rank, "xq_follow_rank": s.xq_follow_rank,
        "guba_rank": s.guba_rank, "guba_rank_chg": s.guba_rank_chg,
        "posts": s.posts, "reads": s.reads,
        "delta_min": s.delta_min, "delta_kind": s.delta_kind,
        "t_limit_seal": s.t_limit_seal, "t_limit_touch": s.t_limit_touch,
        "t_news_first": s.t_news_first, "t_news_last": s.t_news_last,
        "news_n": s.news_n, "touch_only": s.touch_only,
        "delta_reason": s.delta_reason,
    }


# ── 单维度软判定（返回 True/False/None） ───────────────────────
def fund_divergence(s: Signal, th: Thresholds):
    """主力接而全档逃：main_net>0 & net_mf<0。"""
    if not s.flow_has or s.main_net is None or s.net_mf is None:
        return None
    return s.main_net > 0 and s.net_mf < 0


def fund_consistency(s: Signal, th: Thresholds):
    """主力接 + 全档流入。"""
    if not s.flow_has or s.main_net is None or s.net_mf is None:
        return None
    return s.main_net > 0 and s.net_mf > 0


def fund_neutral(s: Signal, th: Thresholds):
    """资金中性：两个净额都在 ±flow_eps 带内。"""
    if not s.flow_has or s.main_net is None or s.net_mf is None:
        return None
    return abs(s.main_net) <= th.flow_eps and abs(s.net_mf) <= th.flow_eps


def fund_mild_inflow(s: Signal, th: Thresholds):
    """主力温和流入、全档小幅（机构埋伏型）。"""
    if not s.flow_has or s.main_net is None or s.net_mf is None:
        return None
    return 0 < s.main_net <= th.flow_mild and 0 <= s.net_mf <= th.flow_mild


def retail_hot(s: Signal, th: Thresholds):
    """散户热：股吧进前 N / 排名上升 / 帖子或阅读超线。"""
    if not s.social_has or not s.social_covered():
        return None
    return bool(
        (s.guba_rank is not None and s.guba_rank <= th.guba_top_n)
        or (s.guba_rank_chg is not None and s.guba_rank_chg > 0)
        or (s.posts is not None and s.posts >= th.hot_posts)
        or (s.reads is not None and s.reads >= th.hot_reads)
    )


def institution_hot(s: Signal, th: Thresholds):
    """机构圈关注：雪球任一榜进前 N。"""
    if not s.social_has or not s.social_covered():
        return None
    ranks = (s.xq_hot_rank, s.xq_discuss_rank, s.xq_trade_rank, s.xq_follow_rank)
    return any(r is not None and r <= th.xq_top_n for r in ranks)


def xq_silent(s: Signal, th: Thresholds):
    """雪球机构圈无视：快照覆盖了该票，但四榜全无其名。"""
    if not s.social_has or not s.social_covered():
        return None
    ranks = (s.xq_hot_rank, s.xq_discuss_rank, s.xq_trade_rank, s.xq_follow_rank)
    return all(r is None for r in ranks)


def dual_hot(s: Signal, th: Thresholds):
    """股吧 TopN + 雪球 TopN 双榜共振（社交过热型）。"""
    if not s.social_has or not s.social_covered():
        return None
    g = s.guba_rank is not None and s.guba_rank <= th.guba_top_n
    ranks = (s.xq_hot_rank, s.xq_discuss_rank, s.xq_trade_rank, s.xq_follow_rank)
    x = any(r is not None and r <= th.xq_top_n for r in ranks)
    return bool(g and x)


def social_cold(s: Signal, th: Thresholds):
    """社交中性偏冷：覆盖到了，但既不散户狂热、也无机构热榜。"""
    r, i = retail_hot(s, th), institution_hot(s, th)
    if r is None or i is None:
        return None
    return not r and not i


def _time_ready(s: Signal) -> bool:
    """Δmin 可判定 = 非 unpriced 且有数值（判例 #25③ 硬纪律）。"""
    return s.delta_kind != "unpriced" and s.delta_min is not None


def time_price_lead(s: Signal, th: Thresholds):
    """价格先行 / 消息追认。"""
    if not _time_ready(s):
        return None
    return s.delta_min > th.th_lead


def time_price_lead_strong(s: Signal, th: Thresholds):
    """价格提前 ≥30min 的强形态。"""
    if not _time_ready(s):
        return None
    return s.delta_min > th.th_lead_strong


def time_price_lead_mild(s: Signal, th: Thresholds):
    """价格先行但温和（非突兀拉升）：+5 < Δmin ≤ +30。"""
    if not _time_ready(s):
        return None
    return th.th_lead < s.delta_min <= th.th_lead_strong


def time_news_drive(s: Signal, th: Thresholds):
    """消息驱动。"""
    if not _time_ready(s):
        return None
    return s.delta_min < -th.th_drive


def time_news_drive_strong(s: Signal, th: Thresholds):
    """消息提前 ≥30min 发布。"""
    if not _time_ready(s):
        return None
    return s.delta_min < -th.th_drive_strong


def time_sync(s: Signal, th: Thresholds):
    """同步 / 无区分（|Δmin| ≤ 5min）。"""
    if not _time_ready(s):
        return None
    return -th.th_drive <= s.delta_min <= th.th_lead


def time_not_news_drive(s: Signal, th: Thresholds):
    """「资金在前、消息不在前」：价格先行或同步都算，排除消息驱动。"""
    if not _time_ready(s):
        return None
    return s.delta_min >= -th.th_drive


def social_rising(s: Signal, th: Thresholds):
    """消息发布后社交热度上升的代理：股吧排名上升或帖子量高。"""
    if not s.social_has or not s.social_covered():
        return None
    return bool(
        (s.guba_rank_chg is not None and s.guba_rank_chg > 0)
        or (s.posts is not None and s.posts >= th.hot_posts)
    )


def _both(a, b):
    """两个软判定取 AND；任一 None → None（不把缺失当 False）。"""
    if a is None or b is None:
        return None
    return bool(a and b)


# ── 8 个候选 pattern（P4 定义逐条翻译） ────────────────────────
def _p1(s, th):
    """散户接盘型：资金背离 + 散户热且机构沉默 + 消息追认。"""
    return (fund_divergence(s, th),
            _both(retail_hot(s, th), xq_silent(s, th)),
            time_price_lead(s, th))


def _p2(s, th):
    """价格先行型：资金中性 + 社交中性 + 价格提前 ≥30min。"""
    return (fund_neutral(s, th), social_cold(s, th),
            time_price_lead_strong(s, th))


def _p3(s, th):
    """真突破型：全档一致性 + 机构热榜且股吧温和 + 资金在前。"""
    return (fund_consistency(s, th),
            _both(institution_hot(s, th), _not(retail_hot(s, th))),
            time_not_news_drive(s, th))


def _p4(s, th):
    """消息驱动型：资金中性 + 消息后社交升温 + 消息提前 ≥30min。"""
    return (fund_neutral(s, th), social_rising(s, th),
            time_news_drive_strong(s, th))


def _p5(s, th):
    """社交过热型：全档中性 + 双榜共振 + 时间同步。"""
    return (fund_neutral(s, th), dual_hot(s, th), time_sync(s, th))


def _p6(s, th):
    """主力锁仓型：全档一致性 + 社交偏冷 + 价格温和先行。"""
    return (fund_consistency(s, th), social_cold(s, th),
            time_price_lead_mild(s, th))


def _p7(s, th):
    """诱多炸板型：资金背离 + 机构沉默且散户追 + 触板未封且消息追认。"""
    touch = s.touch_only if s.t_limit_touch is not None else None
    t = _both(time_price_lead(s, th), touch)
    return (fund_divergence(s, th),
            _both(_not(institution_hot(s, th)), retail_hot(s, th)),
            t)


def _p8(s, th):
    """机构埋伏型：主力温和流入 + 机构热榜且股吧冷 + 价格先行。"""
    return (fund_mild_inflow(s, th),
            _both(institution_hot(s, th), _not(retail_hot(s, th))),
            time_price_lead(s, th))


def _not(v):
    return None if v is None else (not v)


# (id, 中文名, 三维判定函数, 预期演化假设, 分化节点假设)
PATTERNS = (
    ("P1", "散户接盘型", _p1, "短期回落", "Δmin 越大（追认越滞后）→ 回落概率越高"),
    ("P2", "价格先行型", _p2, "看后续有无持续消息确认；无消息接续警惕兑现",
     "价格先行后 30min 内是否出现对应消息"),
    ("P3", "真突破型", _p3, "继续涨 / 趋势延续", "机构榜 rank 越靠前 + 全档流入越持续"),
    ("P4", "消息驱动型", _p4, "消息落地后延续一段", "事件类型强/弱决定延续方向"),
    ("P5", "社交过热型", _p5, "分歧加大 → 震荡或回落", "双榜共振后 1 日内是否出现资金背离"),
    ("P6", "主力锁仓型", _p6, "趋势平稳延续（慢牛）", "net_mf 转负 → 退化为 P1"),
    ("P7", "诱多炸板型", _p7, "冲高回落 / 炸板", "是否真正封板（seal vs 仅 touch）"),
    ("P8", "机构埋伏型", _p8, "慢牛延续，后期可能加速", "股吧热度由冷转热的时点"),
)


def match_signal(s: Signal, th: Thresholds = DEFAULT_TH) -> list[Hit]:
    """**核心纯函数**：合成信号 → 命中的 pattern 列表（按 confidence 降序）。

    不触网、不读盘，P5.2 案例库与单元测试都从这里进。
    """
    hits: list[Hit] = []
    for pid, name, fn, hypothesis, fork in PATTERNS:
        f, so, t = fn(s, th)
        dims = {"fund": f, "social": so, "time": t}
        n_true = sum(1 for v in dims.values() if v is True)
        if n_true < MIN_DIMS:
            continue
        conf = sum(w for k, w in DIM_WEIGHTS.items() if dims[k] is True) \
            / sum(DIM_WEIGHTS.values())
        hits.append(Hit(pattern=pid, name=name, dims=dims,
                        values=_sig_values(s), confidence=round(conf, 4),
                        hypothesis=hypothesis, fork=fork))
    hits.sort(key=lambda h: (-h.confidence, h.pattern))
    return hits


def match(code: str, day: str, signal: Signal | None = None,
          ctx: "DayContext | None" = None,
          th: Thresholds = DEFAULT_TH) -> list[Hit]:
    """P4 契约入口 `match(code, day)`。

    - 传 `signal`：走纯函数路径（测试 / 已装配好的批量信号）。
    - 传 `ctx`：复用当日共享上下文（批量跑 5000+ 样本，避免重复读盘）。
    - 都不传：现场建一个 DayContext（只读本地，不联网）。
    """
    s = signal or (ctx.signal(code) if ctx is not None
                   else DayContext(day).signal(code))
    return match_signal(s, th=th)


# ── 装配层（只读本地；不联网 / 不写盘 / 不采集） ────────────────
def _load_daily(day: str) -> dict:
    """当日日线 → {c6: {pre_close, pct_chg}}。缺文件返回空 dict（不抛）。"""
    import pyarrow.parquet as pq

    path = os.path.join(DAILY_DIR, "d_%s.parquet" % day)
    if not os.path.exists(path):
        return {}
    try:
        t = pq.read_table(path, columns=["ts_code", "pre_close", "pct_chg"])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for row in t.to_pylist():
        out[str(row["ts_code"])[:6]] = {
            "pre_close": row.get("pre_close"),
            "pct_chg": row.get("pct_chg"),
        }
    return out


def _load_names() -> dict:
    """本地 A 股名称表 → {c6: name}（ST 判定需要，纯本地只读）。"""
    if not os.path.exists(NAMES_JSON):
        return {}
    try:
        with open(NAMES_JSON, encoding="utf-8") as f:
            raw = json.load(f)
        return {str(k)[:6]: v for k, v in raw.items()}
    except Exception:  # noqa: BLE001
        return {}


def _load_flow_offline(day: str) -> dict:
    """只读已落盘的资金快照，**忽略 TTL**，绝不联网补。

    `jev_flow._snap_read` 的 12h TTL 是给当日在线请求用的（跨日自动重拉）。
    P5.2 跑历史案例库时，收盘后的资金终值不会再变，过期快照依然是有效
    ground truth；按 TTL 丢弃会让 11-06 这类历史样本资金维度全灭，凭空造出
    「资金不可用」的假结论。
    """
    path = os.path.join(jev_flow.SNAP_DIR, "flow_%s.json" % day)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def _hit_codes(jev_json: str) -> set[str]:
    """与 `jev_graph.load_events` 同口径：jev.companies / jev.stocks 里的 6 位代码。"""
    try:
        j = json.loads(jev_json)
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for key in ("companies", "stocks"):
        for c in (j.get(key) or []):
            x = str(c).strip()
            if len(x) >= 6 and x[:6].isdigit():
                out.add(x[:6])
    return out


def _load_news_anchors(day: str) -> dict:
    """当日分钟级新闻锚点 → {c6: [(dt, source, ts), ...]}（只读 news.db）。

    四道过滤，缺一不可（P1.4 §1.2 / §3.1 / 判例 #25）：
      1. `jev` 非空且能解析出 6 位代码（真实映射通道）
      2. source ∈ MINUTE_SOURCES（排除日级公告/研报）
      3. `_ts_kind(ts) == intraday`（排除 00:00 批量、午休、盘后）
      4. `parse_news_time(ts, created_at, source)` 解析成功（事件时间，非入库时间）
    """
    since = "%s-%s-%s 00:00" % (day[:4], day[4:6], day[6:])
    until = "%s-%s-%s 23:59" % (day[:4], day[4:6], day[6:])
    out: dict = {}
    if not os.path.exists(NEWS_DB):
        return out
    db = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.replace("?", "%3f"), uri=True)
    try:
        # 整点批量规模（_ts_kind 判 batch 用）：统计当日全部 news，不只 jev 行
        hours = {}
        for hm, n in db.execute(
                "SELECT substr(ts,12,5) hm, COUNT(1) FROM news "
                "WHERE ts>=? AND ts<=? GROUP BY hm", (since, until)):
            hours[hm] = n
        rows = db.execute(
            "SELECT ts, source, created_at, jev FROM news "
            "WHERE ts>=? AND ts<=? AND jev IS NOT NULL AND jev!=''",
            (since, until)).fetchall()
    finally:
        db.close()

    for ts, source, created_at, jev in rows:
        if source not in MINUTE_SOURCES:
            continue
        if G._ts_kind(ts, hours) != "intraday":
            continue
        dt = parse_news_time(ts, reference=created_at, source=source)
        if dt is None:
            continue
        for c6 in _hit_codes(jev):
            out.setdefault(c6, []).append((dt, source, ts))
    for c6 in out:
        out[c6].sort(key=lambda x: x[0])
    return out


def _mins_index(cache: dict) -> dict:
    """一次 listdir 建 {c6: [filename]} 索引，批量装配复用。

    stk_mins 目录有 3.4 万个文件，逐票 listdir 实测 132ms/次——P5.2 跑
    5000+ 样本会白烧 11 分钟。索引放在共享 cache 里，一次建好全场复用。
    """
    idx = cache.get("__mins_index__")
    if idx is None:
        idx = {}
        if os.path.isdir(MINS_DIR):
            for fn in os.listdir(MINS_DIR):
                if fn.endswith(".parquet"):
                    idx.setdefault(fn.split("_")[0], []).append(fn)
        cache["__mins_index__"] = idx
    return idx


def _mins_rows(code6: str, day: str, cache: dict) -> list:
    """按票+月读本地分钟 parquet，行级轻量过滤（禁止读 1.47 亿行全量）。"""
    import pyarrow.parquet as pq

    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    suffix = "_%s.parquet" % day[:6]
    files = sorted(f for f in _mins_index(cache).get(code6, [])
                   if f.endswith(suffix))
    rows = []
    for fn in files:
        path = os.path.join(MINS_DIR, fn)
        # 缓存键必须带 day：同一文件是「单票整月」，只按 path 缓存会把
        # A 日切片喂给 B 日，算出完全错位的封板时刻。
        ck = (path, day)
        if ck not in cache:
            try:
                # 坑（P5.1 实测）：stk_mins.ts_code 是完整代码 '300750.SZ'，
                # 不是 6 位。用 code6 做 arrow 等值过滤会 0 行，静默产出
                # 「无分钟数据」的假 unpriced。这里只按时间下推过滤，再在
                # Python 侧按前 6 位归票（P1.4 伪代码里的 code 是完整代码）。
                t = pq.read_table(path, filters=[
                    ("trade_time", ">=", d8 + " 09:00:00"),
                    ("trade_time", "<=", d8 + " 15:00:00"),
                ])
                cache[ck] = [r for r in t.to_pylist()
                             if str(r.get("ts_code") or "")[:6] == code6]
            except Exception:  # noqa: BLE001
                cache[ck] = []
        rows.extend(cache[ck])
    rows.sort(key=lambda r: str(r.get("trade_time")))
    return rows


def _t_limit(code6: str, day: str, daily: dict, names: dict, cache: dict):
    """→ (seal_dt, touch_dt, touch_only, reason)。主锚点 = 首次封板。

    复用 `jev_graph.limit_state`（涨停档位先定死，判例 #25），禁止另写一套。
    """
    rec = daily.get(code6)
    pre_close = (rec or {}).get("pre_close")
    if pre_close is None or pre_close <= 0:
        return None, None, False, "no_pre_close"
    rows = _mins_rows(code6, day, cache)
    if not rows:
        return None, None, False, "no_minute"
    name = names.get(code6, "")
    pct = (rec or {}).get("pct_chg")
    seal = touch = None
    for r in rows:
        try:
            lu, to, _ratio, _lp = G.limit_state(
                r.get("close"), r.get("high"), pre_close, code6, name, pct)
        except (TypeError, ValueError):
            continue
        t = parse_full_time(r.get("trade_time"))
        if t is None:
            continue
        if lu and seal is None:
            seal = t
        if (lu or to) and touch is None:
            touch = t
    if seal is None and touch is None:
        return None, None, False, "no_limit_touch"
    return seal, touch, seal is None, (None if seal else "no_seal_touch_only")


def compute_delta_min(code: str, day: str, daily: dict | None = None,
                      names: dict | None = None, anchors: dict | None = None,
                      cache: dict | None = None,
                      th: Thresholds = DEFAULT_TH) -> dict:
    """按 P1.4 定义计算 Δmin。任一锚点缺失 → unpriced，**绝不硬算**。

    返回 dict：delta_min / delta_kind / t_limit_* / t_news_* / news_n /
    touch_only / delta_reason。
    """
    c6 = str(code)[:6]
    daily = _load_daily(day) if daily is None else daily
    names = _load_names() if names is None else names
    anchors = _load_news_anchors(day) if anchors is None else anchors
    cache = {} if cache is None else cache

    out = {"delta_min": None, "delta_kind": "unpriced",
           "t_limit_seal": None, "t_limit_touch": None,
           "t_news_first": None, "t_news_last": None,
           "news_n": 0, "touch_only": False, "delta_reason": None}

    seal, touch, touch_only, reason = _t_limit(c6, day, daily, names, cache)
    seal, touch = _aware(seal), _aware(touch)
    out["t_limit_seal"] = seal.isoformat() if seal else None
    out["t_limit_touch"] = touch.isoformat() if touch else None
    out["touch_only"] = touch_only
    evs = anchors.get(c6) or []
    out["news_n"] = len(evs)
    if evs:
        out["t_news_first"] = _aware(evs[0][0]).isoformat()
        out["t_news_last"] = _aware(evs[-1][0]).isoformat()

    # 判例 #25③：两个锚点都在才往下走
    if seal is None:
        out["delta_reason"] = reason or "no_price_anchor"
        return out
    if not evs:
        out["delta_reason"] = "no_news_anchor"
        return out

    d_first = (_aware(evs[0][0]) - seal).total_seconds() / 60.0
    d_last = (_aware(evs[-1][0]) - seal).total_seconds() / 60.0
    out["delta_min"] = round(d_first, 2)
    if d_first > th.th_lead:
        out["delta_kind"] = "price_lead"
    elif d_last < -th.th_drive:
        out["delta_kind"] = "news_drive"
        out["delta_min"] = round(d_last, 2)
    else:
        out["delta_kind"] = "sync"
    return out


class DayContext:
    """当日共享上下文：一次读盘，供 5000+ 样本批量装配（P5.2 用）。

    只持有本地只读数据：资金快照 / 社交快照 / 日线 / 新闻锚点 / 名称表 /
    分钟文件缓存。**不写盘、不联网**。
    """

    def __init__(self, day: str, offline: bool = True):
        if not offline:
            raise ValueError("DayContext 只支持离线装配；采集请用 tools/*_fetch.py")
        self.day = day
        self.daily = _load_daily(day)
        self.names = _load_names()
        self.flow = _load_flow_offline(day)
        self.social = jev_social.load_social(day)
        self.anchors = _load_news_anchors(day)
        self._mins_cache: dict = {}
        self.th = DEFAULT_TH

    def signal(self, code: str) -> Signal:
        c6 = str(code)[:6]
        s = Signal(code=c6, day=self.day)
        # 资金：只读快照，缺则 flow_has=False（不触发 load_flow 的远端兜底）
        f = jev_flow.flow_of(self.flow, c6)
        s.flow_has = bool(f.get("has")) and bool(self.flow)
        s.main_net = f.get("main_net")
        s.net_mf = f.get("net_mf")
        # 社交：纯本地快照
        h = jev_social.heat_of(self.social, c6)
        s.social_has = bool(self.social.get("has"))
        for k in ("xq_hot_rank", "xq_discuss_rank", "xq_trade_rank",
                  "xq_follow_rank", "guba_rank", "guba_rank_chg",
                  "posts", "reads", "comments"):
            s.__setattr__(k, h.get(k))
        # 时间：按 P1.4 计算
        d = compute_delta_min(c6, self.day, daily=self.daily, names=self.names,
                              anchors=self.anchors, cache=self._mins_cache,
                              th=self.th)
        for k, v in d.items():
            s.__setattr__(k, v)
        return s


# ── 覆盖率统计（P5.2/P5.4 的基础 API） ─────────────────────────
def summarize(signals: list[Signal], hits: dict[str, list[Hit]],
              th: Thresholds = DEFAULT_TH) -> dict:
    """汇总维度可用率 / ≥1 pattern 覆盖率 / 每 pattern 命中数 / Δmin 分布。

    注意：这是**探索性**统计。阈值未校准前不得直接用作判定闸 A 条件②结论。
    """
    n = len(signals)
    if not n:
        return {"n": 0, "coverage": 0.0}
    by_pattern: dict = {}
    for hs in hits.values():
        for h in hs:
            by_pattern[h.pattern] = by_pattern.get(h.pattern, 0) + 1
    n_hit = sum(1 for s in signals if hits.get(s.code))
    dim_avail = {
        "fund": sum(1 for s in signals if s.flow_has and s.main_net is not None),
        "social": sum(1 for s in signals if s.social_has and s.social_covered()),
        "time": sum(1 for s in signals if _time_ready(s)),
    }
    kinds: dict = {}
    for s in signals:
        kinds[s.delta_kind] = kinds.get(s.delta_kind, 0) + 1
    return {
        "n": n,
        "n_hit_any": n_hit,
        "coverage": round(n_hit / n, 4),
        "dim_avail": dim_avail,
        "dim_avail_rate": {k: round(v / n, 4) for k, v in dim_avail.items()},
        "by_pattern": by_pattern,
        "delta_kind": kinds,
        "thresholds": asdict(th),
        "calibrated": False,
    }


# ── CLI ───────────────────────────────────────────────────────
def _candidate_codes(ctx: DayContext, limit: int) -> list[str]:
    """首轮样本池：优先社交快照覆盖的票（三维度最可能齐），再补日线票。"""
    seen, codes = set(), []
    for c6 in (ctx.social.get("by_code") or {}):
        if c6 not in seen:
            seen.add(c6)
            codes.append(c6)
    for c6 in ctx.daily:
        if c6 not in seen and len(codes) < limit:
            seen.add(c6)
            codes.append(c6)
    return codes[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV P5.1 组合 pattern 匹配引擎（离线）")
    ap.add_argument("--day", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--code", default=None, help="只跑单只票（6 位或带后缀）")
    ap.add_argument("--limit", type=int, default=30, help="样本池上限")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = ap.parse_args(argv)

    ctx = DayContext(args.day)
    codes = [str(args.code)[:6]] if args.code else _candidate_codes(ctx, args.limit)
    signals = [ctx.signal(c) for c in codes]
    hits = {s.code: match_signal(s, th=ctx.th) for s in signals}
    report = summarize(signals, hits, th=ctx.th)
    payload = {
        "day": args.day,
        "summary": report,
        "rows": [{"code": s.code,
                  "delta_kind": s.delta_kind,
                  "delta_reason": s.delta_reason,
                  "hits": [h.as_dict() for h in hits[s.code]]}
                 for s in signals],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("day=%s  n=%d  命中≥1 pattern=%d  coverage=%.1f%%  (阈值未校准，仅探索)"
              % (args.day, report["n"], report["n_hit_any"],
                 100 * report["coverage"]))
        print("维度可用率: " + "  ".join(
            "%s=%d(%.0f%%)" % (k, report["dim_avail"][k],
                                100 * report["dim_avail_rate"][k])
            for k in ("fund", "social", "time")))
        print("Δmin 分布: " + str(report["delta_kind"]))
        print("各 pattern 命中: " + str(report["by_pattern"]))
        for row in payload["rows"]:
            if row["hits"]:
                print("  %s  %s" % (row["code"],
                                    ", ".join("%s(%s,%.2f)" % (
                                        h["pattern"], h["name"], h["confidence"])
                                        for h in row["hits"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
