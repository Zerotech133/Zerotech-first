# -*- coding: utf-8 -*-
"""事件接入：读取 tdx 情报表 + 公告关键词，归一为统一事件 schema。

统一 schema（Event 数据类）：

``event_id / ts_code / event_date / publish_phase / category / sentiment /
summary / source / ref_id / confidence``（另有 ``dedupe_key`` 与对齐字段 ``d0``）。

- ``event_id`` 由 ``(source, ref_id)`` 稳定哈希得到，重复运行不变；
- ``ref_id`` 为各源表的业务主键串（经清洗），驱动幂等写入；
- ``dedupe_key``：同标题跨日公告共享同一 ``dedupe_key``（保留多条，不删除），
  便于下游识别“同一事项的进展公告”；
- ``publish_phase``：默认 ``after_close``（日期粒度数据无披露时刻，按盘后保守
  对齐到次一交易日）；支持 ``intraday``（当日生效，预留）。
"""
from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass
from hashlib import sha1

import pandas as pd

from .config import (
    NEG_WORDS,
    POS_WORDS,
    Paths,
    default_paths,
    normalize_date,
    read_tdx,
    read_tdx_ordered,
)

SCHEMA_FIELDS = (
    "event_id",
    "ts_code",
    "event_date",
    "publish_phase",
    "category",
    "sentiment",
    "summary",
    "source",
    "ref_id",
    "confidence",
)
EXTRA_FIELDS = ("dedupe_key", "d0")

PHASE_AFTER_CLOSE = "after_close"
PHASE_INTRADAY = "intraday"

# 结构化情报表的基础置信度；公告关键词按命中词数上浮
SOURCE_CONFIDENCE = {
    "ts_forecast": 0.90,
    "ts_express": 0.92,
    "ts_repurchase": 0.85,
    "ts_stk_holdertrade": 0.85,
    "ts_block_trade": 0.75,
    "ts_share_float": 0.80,
    "ts_pledge_stat": 0.70,
    "ts_top_inst": 0.80,
    "ts_report_rc": 0.65,
    "ts_anns_d": 0.55,
}

# 各源事件日期列
SOURCE_DATE_COL = {
    "ts_forecast": "ann_date",
    "ts_express": "ann_date",
    "ts_repurchase": "ann_date",
    "ts_stk_holdertrade": "ann_date",
    "ts_block_trade": "trade_date",
    "ts_share_float": "ann_date",
    "ts_pledge_stat": "end_date",
    "ts_top_inst": "trade_date",
    "ts_report_rc": "report_date",
    "ts_anns_d": "ann_date",
}

# 各源读取列（控制 IO；与 tr0_card.KEEP_COLS 对齐）
SOURCE_COLUMNS = {
    "ts_forecast": ["ts_code", "ann_date", "end_date", "type", "p_change_min", "p_change_max", "first_ann_date"],
    "ts_express": ["ts_code", "ann_date", "end_date", "yoy_net_profit"],
    "ts_repurchase": ["ts_code", "ann_date", "end_date", "proc", "vol", "amount"],
    "ts_stk_holdertrade": ["ts_code", "ann_date", "holder_name", "in_de", "change_vol", "change_ratio"],
    "ts_block_trade": ["ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"],
    "ts_share_float": ["ts_code", "ann_date", "float_date", "float_share", "float_ratio", "holder_name"],
    "ts_pledge_stat": ["ts_code", "end_date", "pledge_count", "pledge_ratio"],
    "ts_top_inst": ["ts_code", "trade_date", "net_buy"],
    "ts_report_rc": ["ts_code", "report_date", "report_title", "org_name", "classify", "rating"],
    "ts_anns_d": ["ts_code", "ann_date", "title", "url"],
}

EVENT_TABLES = tuple(SOURCE_COLUMNS)


@dataclass
class Event:
    """统一事件 schema。"""

    event_id: str
    ts_code: str
    event_date: str
    publish_phase: str
    category: str
    sentiment: float
    summary: str
    source: str
    ref_id: str
    confidence: float
    dedupe_key: str
    d0: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _hash(*parts: object) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return sha1(raw.encode("utf-8")).hexdigest()


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip().lower() in {"", "nan", "none", "nat", "null"}


def _text(value: object, limit: int = 200) -> str:
    if _blank(value):
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()[:limit]


def _clip_sentiment(value: float) -> float:
    return max(-1.0, min(1.0, round(float(value), 2)))


def _stable_ref(*parts: object) -> str:
    return "|".join(_text(p, 120) for p in parts)


def _make_event(
    *,
    source: str,
    ts_code: object,
    event_date: object,
    category: str,
    sentiment: float,
    summary: object,
    ref_id: str,
    publish_phase: str = PHASE_AFTER_CLOSE,
    confidence: float | None = None,
    dedupe_key: str | None = None,
) -> Event | None:
    code = _text(ts_code, 16)
    date = normalize_date(event_date)
    if not code or not date:
        return None
    ref = ref_id or _hash(source, code, date, category, _text(summary))[:16]
    conf = confidence if confidence is not None else SOURCE_CONFIDENCE.get(source, 0.5)
    return Event(
        event_id="ev_" + _hash(source, ref)[:16],
        ts_code=code,
        event_date=date,
        publish_phase=publish_phase,
        category=category,
        sentiment=_clip_sentiment(sentiment),
        summary=_text(summary),
        source=source,
        ref_id=ref,
        confidence=round(max(0.0, min(1.0, float(conf))), 2),
        dedupe_key=dedupe_key or f"{source}:{ref}",
    )


def classify_ann(title: str) -> tuple[float, str, list[str]]:
    """公告标题关键词打分（与 tr0_card.classify_ann 口径一致）。

    返回 ``(sentiment, category_text, hits)``；无方向词时 ``category_text`` 为空。
    """
    text = str(title or "")
    score = 0.0
    cats: list[str] = []
    hits: list[str] = []
    for word in POS_WORDS:
        if word in text:
            score += 0.3
            cats.append(word)
            hits.append(word)
    for word in NEG_WORDS:
        if word in text:
            score -= 0.3
            cats.append(word)
            hits.append(word)
    # 关键词反向语境修正（与 TR0 一致）
    if "回购" in text and any(k in text for k in ("司法处置", "被强制", "终止", "失败")):
        score -= 0.6
        cats.append("回购受阻")
    if "增持" in text and any(k in text for k in ("终止", "未实施", "取消")):
        score -= 0.6
    if "重整" in text:
        cats.append("重整")
        score += 0.1 if "进展" not in text else 0.15
    if "破产" in text or "退市" in text:
        score -= 0.4
        cats.append("风险")
    return _clip_sentiment(score), "、".join(sorted(set(cats))), hits


def _norm_title(title: str) -> str:
    return "".join(str(title or "").split())


# ---------------------------------------------------------------------------
# 各数据源 → Event 列表
# ---------------------------------------------------------------------------
def _events_forecast(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        typ = _text(row.get("type"))
        if any(k in typ for k in ("预增", "略增", "扭亏", "续盈")):
            sentiment = 0.5
        elif any(k in typ for k in ("预减", "略减", "首亏", "续亏")):
            sentiment = -0.5
        else:
            sentiment = 0.0
        event = _make_event(
            source="ts_forecast",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="业绩预告",
            sentiment=sentiment,
            summary=f"{typ} 变动 {_text(row.get('p_change_min'))}~{_text(row.get('p_change_max'))}%",
            ref_id=_stable_ref(
                row.get("ts_code"), row.get("ann_date"), row.get("end_date"), row.get("type"), row.get("first_ann_date")
            ),
        )
        if event:
            out.append(event)
    return out


def _events_express(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        yoy = row.get("yoy_net_profit")
        try:
            sentiment = 0.5 if float(yoy) > 0 else -0.5
        except (TypeError, ValueError):
            sentiment = 0.0
        event = _make_event(
            source="ts_express",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="业绩快报",
            sentiment=sentiment,
            summary=f"净利润同比 {_text(yoy)}",
            ref_id=_stable_ref(row.get("ts_code"), row.get("ann_date"), row.get("end_date")),
        )
        if event:
            out.append(event)
    return out


def _events_repurchase(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        event = _make_event(
            source="ts_repurchase",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="回购",
            sentiment=0.4,
            summary=f"进度 {_text(row.get('proc'))} 金额 {_text(row.get('amount'))}",
            ref_id=_stable_ref(
                row.get("ts_code"), row.get("ann_date"), row.get("end_date"), row.get("proc"), row.get("amount"), row.get("vol")
            ),
        )
        if event:
            out.append(event)
    return out


def _events_holdertrade(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        in_de = _text(row.get("in_de")).upper()
        if in_de.startswith("IN"):
            sentiment = 0.4
        elif in_de.startswith("DE"):
            sentiment = -0.4
        else:
            sentiment = 0.0
        event = _make_event(
            source="ts_stk_holdertrade",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="增减持",
            sentiment=sentiment,
            summary=(
                f"{_text(row.get('holder_name'), 60)} {in_de} "
                f"{_text(row.get('change_vol'))} 股 ({_text(row.get('change_ratio'))}%)"
            ),
            ref_id=_stable_ref(
                row.get("ts_code"),
                row.get("ann_date"),
                row.get("holder_name"),
                row.get("in_de"),
                row.get("change_vol"),
                row.get("change_ratio"),
            ),
        )
        if event:
            out.append(event)
    return out


def _events_block_trade(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        event = _make_event(
            source="ts_block_trade",
            ts_code=row.get("ts_code"),
            event_date=row.get("trade_date"),
            category="大宗交易",
            sentiment=0.0,
            summary=f"成交 {_text(row.get('price'))} 元 {_text(row.get('amount'))} 万元",
            ref_id=_stable_ref(
                row.get("ts_code"),
                row.get("trade_date"),
                row.get("price"),
                row.get("vol"),
                row.get("amount"),
                row.get("buyer"),
                row.get("seller"),
            ),
        )
        if event:
            out.append(event)
    return out


def _events_share_float(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        event = _make_event(
            source="ts_share_float",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="解禁",
            sentiment=-0.3,
            summary=(
                f"{_text(row.get('float_date'))} 解禁 {_text(row.get('float_share'))} 股 "
                f"({_text(row.get('float_ratio'))}%)"
            ),
            ref_id=_stable_ref(
                row.get("ts_code"),
                row.get("ann_date"),
                row.get("float_date"),
                row.get("holder_name"),
                row.get("float_share"),
            ),
        )
        if event:
            out.append(event)
    return out


def _events_pledge(df: pd.DataFrame) -> list[Event]:
    """质押：同一股票按 end_date 去重（保留文件序最后一条），比例变化 <0.01 跳过。

    注意：不做日期排序——tr0_card 依赖 tdx 分片的物理行序（近期追加片在历史片之后），
    顺序去重结果与文件序绑定；TR1 复刻同一口径以保证与 TR0 事件数可比。
    """
    out: list[Event] = []
    if df is None or len(df) == 0:
        return out
    work = df.copy()
    work["_d"] = work["end_date"].map(normalize_date)
    work = work.dropna(subset=["_d"])
    for code, sub in work.groupby("ts_code", sort=False):
        sub = sub.drop_duplicates(subset=["_d"], keep="last")
        dates = sub["_d"].to_list()
        raw_ratios = sub["pledge_ratio"].to_list()
        ratios = pd.to_numeric(sub["pledge_ratio"], errors="coerce").to_list()
        last_ratio: float | None = None
        for date, raw_ratio, ratio in zip(dates, raw_ratios, ratios):
            ratio_f = None if pd.isna(ratio) else float(ratio)
            if ratio_f is not None and last_ratio is not None and abs(ratio_f - last_ratio) < 0.01:
                continue
            last_ratio = ratio_f if ratio_f is not None else last_ratio
            event = _make_event(
                source="ts_pledge_stat",
                ts_code=code,
                event_date=date,
                category="质押",
                sentiment=-0.2,
                summary=f"质押比例 {_text(raw_ratio)}%",
                ref_id=_stable_ref(code, date, raw_ratio),
                dedupe_key=f"pledge:{code}:{date}",
            )
            if event:
                out.append(event)
    return out


def _events_top_inst(df: pd.DataFrame) -> list[Event]:
    """龙虎榜：按 (ts_code, trade_date) 汇总席位净买入（单位万元，±0.3）。"""
    out: list[Event] = []
    if df is None or len(df) == 0:
        return out
    work = df.copy()
    work["trade_date"] = work["trade_date"].map(normalize_date)
    work["net_buy"] = pd.to_numeric(work["net_buy"], errors="coerce")
    work = work.dropna(subset=["trade_date"])
    grouped = work.groupby(["ts_code", "trade_date"], sort=False)["net_buy"].sum(min_count=0)
    for (code, date), value in grouped.items():
        try:
            value_wan = float(value) / 1e4
            sentiment = 0.3 if value_wan > 0 else -0.3
        except (TypeError, ValueError):
            value_wan = 0.0
            sentiment = -0.3
        event = _make_event(
            source="ts_top_inst",
            ts_code=code,
            event_date=date,
            category="龙虎榜",
            sentiment=sentiment,
            summary=f"席位净买入 {value_wan:.2f} 万元",
            ref_id=f"{code}|{date}",
            dedupe_key=f"top_inst:{code}:{date}",
        )
        if event:
            out.append(event)
    return out


def _events_report_rc(df: pd.DataFrame) -> list[Event]:
    out: list[Event] = []
    for row in df.to_dict("records"):
        rating = _text(row.get("rating"))
        classify = _text(row.get("classify"))
        label = f"{rating} {classify}"
        if any(k in label for k in ("买入", "推荐", "增持", "上调")):
            sentiment = 0.3
        elif any(k in label for k in ("减持", "卖出", "下调")):
            sentiment = -0.3
        else:
            sentiment = 0.0
        event = _make_event(
            source="ts_report_rc",
            ts_code=row.get("ts_code"),
            event_date=row.get("report_date"),
            category="研报评级",
            sentiment=sentiment,
            summary=f"{_text(row.get('org_name'), 60)} {rating} {classify}".strip(),
            ref_id=_stable_ref(
                row.get("ts_code"), row.get("report_date"), row.get("org_name"), _text(row.get("report_title"), 80)
            ),
        )
        if event:
            out.append(event)
    return out


def _events_anns_d(df: pd.DataFrame) -> list[Event]:
    """公告关键词：仅保留带方向词的公告；同 (代码, 日期, 标题) 只保留一条。"""
    out: list[Event] = []
    if df is None or len(df) == 0:
        return out
    work = df.copy()
    work["ann_date"] = work["ann_date"].map(normalize_date)
    work = work.dropna(subset=["ann_date"])
    work["ts_code"] = work["ts_code"].astype(str)
    work["_title"] = work["title"].astype(str)
    # 与 TR0 一致：同标题同日只保留一条；跨日同标题保留（共享 dedupe_key）
    work = work.drop_duplicates(subset=["ts_code", "ann_date", "_title"], keep="first")
    for row in work.to_dict("records"):
        title = _text(row.get("_title"), 200)
        if not title:
            continue
        sentiment, category, hits = classify_ann(title)
        if not category:
            continue
        confidence = min(0.8, 0.5 + 0.05 * len(hits))
        event = _make_event(
            source="ts_anns_d",
            ts_code=row.get("ts_code"),
            event_date=row.get("ann_date"),
            category="公告·" + category,
            sentiment=sentiment,
            summary=title,
            ref_id=_stable_ref(row.get("ts_code"), row.get("ann_date"), title, row.get("url")),
            confidence=confidence,
            dedupe_key=f"ann:{_text(row.get('ts_code'), 16)}:{_hash(_norm_title(title))[:12]}",
        )
        if event:
            out.append(event)
    return out


_BUILDERS = {
    "ts_forecast": _events_forecast,
    "ts_express": _events_express,
    "ts_repurchase": _events_repurchase,
    "ts_stk_holdertrade": _events_holdertrade,
    "ts_block_trade": _events_block_trade,
    "ts_share_float": _events_share_float,
    "ts_pledge_stat": _events_pledge,
    "ts_top_inst": _events_top_inst,
    "ts_report_rc": _events_report_rc,
    "ts_anns_d": _events_anns_d,
}


def _dedupe(events: list[Event]) -> list[Event]:
    """按 event_id 去重（同源同业务主键只保留一条）。"""
    seen: set[str] = set()
    result: list[Event] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        result.append(event)
    return result


def _load_events(
    codes: list[str] | None,
    start: str | None = None,
    end: str | None = None,
    paths: Paths | None = None,
) -> list[Event]:
    paths = paths or default_paths()
    start = normalize_date(start)
    end = normalize_date(end)
    events: list[Event] = []
    for source, builder in _BUILDERS.items():
        if source == "ts_pledge_stat":
            # 质押按 end_date 顺序去重（比例变化 <0.01 跳过），次序敏感；
            # 为与 tr0_card 全历史、文件序去重口径一致：整表按分片文件序读取，
            # 单票取全历史，全市场只保留窗口内有披露的股票，构建后再裁回窗口。
            frame = read_tdx_ordered(source, columns=SOURCE_COLUMNS[source], paths=paths)
            if frame is not None and len(frame):
                if codes is not None:
                    frame = frame[frame["ts_code"].isin([str(c) for c in codes])]
                elif start or end:
                    dates = frame["end_date"].map(normalize_date)
                    mask = pd.Series(True, index=frame.index)
                    if start:
                        mask &= dates >= start
                    if end:
                        mask &= dates <= end
                    window_codes = frame.loc[mask, "ts_code"].unique()
                    frame = frame[frame["ts_code"].isin(window_codes)]
            built = builder(frame) if frame is not None and len(frame) else []
            if start:
                built = [event for event in built if event.event_date >= start]
            if end:
                built = [event for event in built if event.event_date <= end]
            events.extend(built)
            continue
        frame = read_tdx(
            source,
            columns=SOURCE_COLUMNS[source],
            date_col=SOURCE_DATE_COL[source],
            date_start=start,
            date_end=end,
            codes=codes,
            paths=paths,
        )
        if frame is None or len(frame) == 0:
            continue
        events.extend(builder(frame))
    events = _dedupe(events)
    events.sort(key=lambda e: (e.event_date, e.ts_code, e.source, e.category, e.summary))
    return events


def load_events_for_code(
    code: str,
    start: str | None = None,
    end: str | None = None,
    *,
    paths: Paths | None = None,
) -> list[Event]:
    """读取单只股票的窗口内事件。"""
    code = _text(code, 16)
    if not code:
        raise ValueError("code is required")
    return _load_events([code], start=start, end=end, paths=paths)


def load_events_for_range(
    start: str | None = None,
    end: str | None = None,
    *,
    paths: Paths | None = None,
) -> list[Event]:
    """读取全市场窗口内事件（Top 列表 / build-db 用）。"""
    return _load_events(None, start=start, end=end, paths=paths)


def assign_d0(events: list[Event], trade_days: list[str] | tuple[str, ...]) -> list[Event]:
    """事件 → 生效交易日 d0。

    默认（``after_close``）取事件日**之后**第一个交易日；``intraday`` 取事件日当天
    或之后第一个交易日。窗口最后一个交易日之后无数据时 ``d0`` 为 None。
    """
    days = sorted({d for d in (normalize_date(x) for x in trade_days) if d})
    if not days:
        for event in events:
            event.d0 = None
        return events
    for event in events:
        if event.publish_phase == PHASE_INTRADAY:
            idx = bisect.bisect_left(days, event.event_date)
        else:
            idx = bisect.bisect_right(days, event.event_date)
        event.d0 = days[idx] if idx < len(days) else None
    return events


def count_by_source(events: list[Event]) -> dict[str, int]:
    """按数据源统计事件数（用于回执与 meta）。"""
    counts: dict[str, int] = {}
    for event in events:
        counts[event.source] = counts.get(event.source, 0) + 1
    return counts


def events_to_records(events: list[Event]) -> list[dict]:
    """Event 列表 → dict 列表（JSON/DB 用）。"""
    return [event.to_dict() for event in events]
