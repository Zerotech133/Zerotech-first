# -*- coding: utf-8 -*-
"""D1 辩论质量评估（P1，2026-11-01 WO-D1-DEBATEQ-20261101）。

定位：纯函数、只读、无 IO —— 对 D1 投研会的四线辩论材料做**辅助质量评估**，
产出证据覆盖（evidence_coverage）/ 分歧标记（disagreement_flags）/
0~100 质量分（score_debate）/ 会议汇总（evaluate_meeting）。

四线口径（与 d1_meeting 的 4 个投票席一一对应）：
  · bull        T1 多方（激进多头技术面）
  · bear        T2 空方（保守空头技术面）
  · sentiment   N1 情绪面
  · fundamental F1 基本面

record 是普通 dict，字段均可缺省（缺 = 扣分，不抛异常）：
  bull / bear / sentiment / fundamental（str | list | dict 均可）
  evidence（引用列表）/ dissent（反方意见列表）/ votes（席位→票）
  final_decision（D1 最终裁决，只原样展示，不参与打分改写）

边界（硬约束，评审看这里）：
  · 所有输出 not_a_verdict=true、replaces_d1=false —— 辅助质量评估，不替代 D1；
  · final tier 只原样展示，不推荐、不改写、不覆盖 D1（鲁肃）裁决；
  · 不读盘、不落盘、不联网；同输入同输出（确定性），不写时间戳/随机数；
  · 不生成交易单、不接实盘。
"""
from __future__ import annotations

import re

SCHEMA = "d1_debate_quality/v1"
COVERAGE_SCHEMA = SCHEMA + "/coverage"
FLAGS_SCHEMA = SCHEMA + "/disagreement"
MEETING_SCHEMA = SCHEMA + "/meeting"

# 四线字段（顺序即输出顺序，保证确定性）
LINES = ("bull", "bear", "sentiment", "fundamental")
LINE_SEATS = {"bull": "T1", "bear": "T2", "sentiment": "N1", "fundamental": "F1"}
LINE_LABELS = {
    "bull": "T1 多方",
    "bear": "T2 空方",
    "sentiment": "N1 情绪",
    "fundamental": "F1 基本面",
}
# 与 d1_meeting.VOTERS / d1_decision.TIERS 对齐（本模块不 import，保持纯函数零依赖；
# test_d1_debate_quality 里做常量一致性守卫）
VOTERS = ("T1", "T2", "N1", "F1")
TIERS = ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")
BULLISH_TIERS = frozenset(("STRONG_BUY", "BUY"))
BEARISH_TIERS = frozenset(("SELL", "STRONG_SELL"))
NEUTRAL_TIERS = frozenset(("HOLD",))

# 维度权重（合计 1.0）与缺料扣分
DIMENSIONS = ("evidence", "balance", "specificity", "dissent")
WEIGHTS = {"evidence": 0.40, "balance": 0.25, "specificity": 0.20, "dissent": 0.15}
PENALTY_MISSING_LINE = 8   # 缺一整条线（无实质内容）
PENALTY_UNCITED_LINE = 3   # 有内容但无引用/出处
MIN_LINE_CHARS = 8         # “实质内容”下限（字符数）

BOUNDARY = (
    "辅助质量评估：not_a_verdict=true；不裁决、不改写 D1 最终 tier、不替代 D1（鲁肃）；"
    "不落盘、不读盘、不联网、不下单、不接实盘。"
)

# 字段别名：四线既可按 canonical 名给，也可按席位号给
_LINE_ALIASES = {
    "bull": ("bull", "t1", "T1", "bull_view", "t1_bull"),
    "bear": ("bear", "t2", "T2", "bear_view", "t2_bear"),
    "sentiment": ("sentiment", "n1", "N1", "n1_sentiment", "sentiment_view"),
    "fundamental": ("fundamental", "f1", "F1", "f1_fundamental", "fundamental_view"),
}
_TEXT_KEYS = ("summary", "text", "content", "thesis", "statement", "view",
              "argument", "rationale", "note", "conclusion")
_META_KEYS = frozenset((
    "tier", "tier_label", "confidence", "seat", "voter", "agent", "evidence",
    "refs", "sources", "citations", "links", "voted_at", "decided_by",
    "decided_by_name", "paper_only", "finalized_at", "vote_count", "quorum",
    "schema", "meeting_id", "code", "status", "created_at", "created_by",
))
_REF_KEYS = ("evidence", "refs", "sources", "citations", "links", "url", "urls",
             "source", "ref", "artifact_ref")
# 引用标记：URL 或“来源/依据/引用/出处/参考：”（带否定前缀的“无依据”不算）
_CITATION_RE = re.compile(
    r"https?://|(?<![无没缺])(?:来源|依据|引用|出处|参考|source|ref|src)\s*[:：]",
    re.IGNORECASE,
)
_BULL_RE = re.compile(r"(?<![不没无别未])(看多|多方|多头|增持|买入|bullish)")
_BEAR_RE = re.compile(r"(?<![不没无别未])(看空|空方|空头|减持|卖出|bearish)")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class FlagList(list):
    """分歧标记列表：保持 list 语义（JSON 序列化为数组），并声明非裁决。"""

    not_a_verdict = True
    advisory = True
    replaces_d1 = False


def _clip(value, limit=300):
    return str(value or "").strip()[:limit]


def _round_half_up(value):
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return round(out, 4)


def _text(value):
    """把 str/list/dict 统一成可读文本（确定性；不猜字段语义）。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        parts = [_text(value[key]) for key in _TEXT_KEYS if key in value]
        parts = [part for part in parts if part]
        if not parts:
            for key in sorted(value, key=str):
                if str(key).lower() in _META_KEYS:
                    continue
                part = _text(value[key])
                if part:
                    parts.append(part)
        return " ".join(parts).strip()
    return str(value).strip()


def _refs(value, in_ref_field=False):
    """抽取显式引用（只认引用字段，不把整段正文当引用）。"""
    if value is None:
        return []
    if isinstance(value, dict):
        out = []
        for key in _REF_KEYS:
            if key in value:
                out.extend(_refs(value[key], True))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if in_ref_field:
                out.extend(_refs(item, True))
            elif isinstance(item, (dict, list, tuple)):
                out.extend(_refs(item, False))
        return out
    if isinstance(value, str):
        text = value.strip()
        return [text] if (in_ref_field and text) else []
    if isinstance(value, (int, float)) and in_ref_field:
        return [str(value)]
    return []


def _dedupe(items):
    return list(dict.fromkeys(item for item in items if item))


def _line_value(record, name):
    for key in _LINE_ALIASES[name]:
        if key in record and _text(record[key]):
            return record[key]
    return record.get(_LINE_ALIASES[name][0])


def _as_record(record):
    if not isinstance(record, dict):
        return {}
    for key in ("record", "material"):
        inner = record.get(key)
        if isinstance(inner, dict) and inner:
            merged = dict(inner)
            for name, value in record.items():
                if name not in ("record", "material"):
                    merged.setdefault(name, value)
            return merged
    return record


def _direction(value):
    """粗判文本方向；双向词并存或无法判定时返回空串（不过度断言）。"""
    text = _text(value).lower()
    bull = bool(_BULL_RE.search(text))
    bear = bool(_BEAR_RE.search(text))
    if bull and not bear:
        return "bullish"
    if bear and not bull:
        return "bearish"
    return ""


def _numbers(text):
    """行内不同数字（最多 4 个），用于“具体度”。"""
    seen = []
    for match in _NUM_RE.findall(text):
        if match not in seen:
            seen.append(match)
        if len(seen) >= 4:
            break
    return seen


def _text_has_citation(text):
    return bool(_CITATION_RE.search(text))


def _dissent_items(record):
    raw = record.get("dissent")
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = [_text(raw[key]) for key in sorted(raw, key=str)]
    elif isinstance(raw, (list, tuple)):
        items = [_text(item) for item in raw]
    else:
        items = [_text(raw)]
    return _dedupe(items)


def _tier_direction(tier):
    value = _clip(tier, 24).upper()
    if value in BULLISH_TIERS:
        return "bullish"
    if value in BEARISH_TIERS:
        return "bearish"
    if value in NEUTRAL_TIERS:
        return "neutral"
    return ""


def _vote_item(seat, value):
    item = {"seat": seat, "tier": "", "confidence": None,
            "rationale": "", "evidence": []}
    if isinstance(value, dict):
        item["tier"] = _clip(value.get("tier") or value.get("recommendation"), 24).upper()
        item["confidence"] = _num(value.get("confidence"))
        item["rationale"] = _text(value.get("rationale"))
        item["evidence"] = _dedupe(_refs(value, True))
    else:
        item["tier"] = _clip(value, 24).upper()
    return item


def _vote_map(record):
    """归一座席票型；dict 与 list 两种输入都吃。"""
    raw = record.get("votes")
    out = {}
    if isinstance(raw, dict):
        for key in sorted(raw, key=str):
            seat = _clip(key, 8).upper()
            if seat:
                out[seat] = _vote_item(seat, raw[key])
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            seat = _clip(entry.get("seat") or entry.get("voter") or entry.get("agent"), 8).upper()
            if seat and seat not in out:
                out[seat] = _vote_item(seat, entry)
    return out


def _tier_distribution(vote_map):
    counts = {}
    for item in vote_map.values():
        tier = item.get("tier") or ""
        if tier:
            counts[tier] = counts.get(tier, 0) + 1
    ordered = {}
    for tier in TIERS:
        if tier in counts:
            ordered[tier] = counts[tier]
    for tier in sorted(set(counts) - set(TIERS)):
        ordered[tier] = counts[tier]
    return ordered


def _votes_opposed(vote_map):
    directions = {_tier_direction(item["tier"]) for item in vote_map.values()}
    return "bullish" in directions and "bearish" in directions


def _final_tier(record):
    raw = record.get("final_decision")
    if isinstance(raw, dict):
        tier = _clip(raw.get("tier") or raw.get("recommendation"), 24).upper()
    elif isinstance(raw, str):
        tier = _clip(raw, 24).upper()
    else:
        tier = ""
    if not tier:
        tier = _clip(record.get("final_tier"), 24).upper()
    return tier


def _flag(code, severity, message, observed=None):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "observed": dict(observed or {}),
        "not_a_verdict": True,
        "advisory": True,
        "replaces_d1": False,
    }


def evidence_coverage(record):
    """四线实质内容 + 引用覆盖（0~1）与缺失清单。

    coverage = 每线得分均值；每线 = 0.7（实质内容 >= MIN_LINE_CHARS）+ 0.3（有引用）。
    因此：四线齐全且有引用 = 1.0；有内容无引用 = 0.7；空 = 0。
    """
    rec = _as_record(record)
    lines = {}
    missing = []
    uncited = []
    credit_total = 0
    inline_refs = 0
    for name in LINES:
        value = _line_value(rec, name)
        text = _text(value)
        refs = _refs(value)
        present = len(text) >= MIN_LINE_CHARS
        cited = present and (bool(refs) or _text_has_citation(text))
        credit = (7 if present else 0) + (3 if cited else 0)
        credit_total += credit
        inline_refs += len(refs)
        if not present:
            missing.append(name)
        elif not cited:
            uncited.append(name)
        lines[name] = {
            "seat": LINE_SEATS[name],
            "label": LINE_LABELS[name],
            "present": present,
            "chars": len(text),
            "cited": cited,
            "refs": len(refs),
            "numbers": len(_numbers(text)),
            "direction": _direction(text) if present else "",
        }
    evidence_refs = _dedupe(_refs(rec.get("evidence"), True))
    coverage = round(credit_total / (len(LINES) * 10.0), 4)
    return {
        "schema": COVERAGE_SCHEMA,
        "coverage": coverage,
        "covered_lines": len(LINES) - len(missing),
        "total_lines": len(LINES),
        "missing": missing,
        "uncited": uncited,
        "lines": lines,
        "evidence_refs": len(evidence_refs),
        "inline_refs": inline_refs,
        "not_a_verdict": True,
        "advisory": True,
        "replaces_d1": False,
        "readonly": True,
        "side_effects": [],
        "boundary": BOUNDARY,
        "notes": [
            "coverage 只衡量四线材料的完稿度/引用显式度，不代表结论正确。",
            "missing=无实质内容（< %d 字符）；uncited=有内容但无引用标记/出处。" % MIN_LINE_CHARS,
        ],
    }


def disagreement_flags(record):
    """分歧标记（返回 list；每条都带 not_a_verdict=true）。

    只给 info/warn 两级，不出通过/否决/错误结论：
      · t1_t2_direction_conflict   T1/T2 方向相反（辩论常态，非缺陷）
      · t1_t2_direction_unstated   T1/T2 均有材料但方向难判
      · stance_collapse            席位立场与角色职责相反（Day0 倒戈模式，仅提示）
      · votes_missing_seats        投票席缺失
      · votes_tier_divergence      票型 tier 分布分歧
      · votes_direction_opposed    票型多空对立
      · dissent_not_recorded       有可见分歧但 dissent 为空
      · final_vs_votes_divergence  D1 最终方向与票型多数相反（仅记录对照）
    """
    rec = _as_record(record)
    cov = evidence_coverage(rec)
    lines = cov["lines"]
    flags = FlagList()

    t1_dir = lines["bull"]["direction"]
    t2_dir = lines["bear"]["direction"]
    both_present = lines["bull"]["present"] and lines["bear"]["present"]
    if both_present and t1_dir and t2_dir and t1_dir != t2_dir:
        flags.append(_flag(
            "t1_t2_direction_conflict", "info",
            "T1/T2 方向相反（%s vs %s）：投研会本就应有多空对立，非缺陷；"
            "仅提示核对分歧是否被纪要记录。" % (LINE_LABELS["bull"], LINE_LABELS["bear"]),
            {"t1": t1_dir, "t2": t2_dir},
        ))
    elif both_present and not (t1_dir and t2_dir):
        flags.append(_flag(
            "t1_t2_direction_unstated", "info",
            "T1/T2 均有材料但方向词不足以判定多空对立（缺方向词或双向词并存）："
            "无法确认辩论是否真的对立。",
            {"t1": t1_dir, "t2": t2_dir},
        ))

    collapsed = []
    if lines["bull"]["present"] and t1_dir == "bearish":
        collapsed.append("T1")
    if lines["bear"]["present"] and t2_dir == "bullish":
        collapsed.append("T2")
    if collapsed:
        flags.append(_flag(
            "stance_collapse", "warn",
            "%s 文本方向与角色职责相反（Day0 倒戈模式）：仅提示立场漂移，"
            "不代表其论据不成立。" % "/".join(collapsed),
            {"seats": collapsed},
        ))

    vote_map = _vote_map(rec)
    tiers = [item["tier"] for item in vote_map.values() if item["tier"]]
    missing_seats = [seat for seat in VOTERS
                     if seat not in vote_map or not vote_map[seat]["tier"]]
    if missing_seats:
        flags.append(_flag(
            "votes_missing_seats", "warn",
            "投票席未齐：缺 %s；票型分歧结论可能不完整。" % "/".join(missing_seats),
            {"missing": missing_seats, "vote_count": len(tiers)},
        ))
    distributions = _tier_distribution(vote_map)
    if len(set(tiers)) >= 2:
        flags.append(_flag(
            "votes_tier_divergence", "info",
            "票型分布分歧（%s）：多档并存属正常辩论，不预判哪档正确。"
            % "、".join("%s=%d" % (k, v) for k, v in distributions.items()),
            {"tiers": distributions},
        ))
    if _votes_opposed(vote_map):
        directions = [_tier_direction(tier) for tier in tiers]
        flags.append(_flag(
            "votes_direction_opposed", "info",
            "票型含多空对立（%d 多 / %d 空）：说明未塌缩为单边共识。"
            % (directions.count("bullish"), directions.count("bearish")),
            {"bullish": directions.count("bullish"),
             "bearish": directions.count("bearish"),
             "neutral": directions.count("neutral")},
        ))

    if not _dissent_items(rec):
        debated = (both_present and bool(t1_dir) and bool(t2_dir) and t1_dir != t2_dir)
        if debated or _votes_opposed(vote_map):
            flags.append(_flag(
                "dissent_not_recorded", "warn",
                "存在可见多空分歧但 dissent 为空：建议补录反方意见，"
                "避免纪要丢掉少数派与失效条件。",
                {"dissent_count": 0},
            ))

    final_tier = _final_tier(rec)
    final_dir = _tier_direction(final_tier)
    if final_dir in ("bullish", "bearish") and tiers:
        directions = [_tier_direction(tier) for tier in tiers]
        bullish_n = directions.count("bullish")
        bearish_n = directions.count("bearish")
        against = ((final_dir == "bullish" and bearish_n > bullish_n)
                   or (final_dir == "bearish" and bullish_n > bearish_n))
        if against:
            flags.append(_flag(
                "final_vs_votes_divergence", "info",
                "D1 最终方向（%s）与票型多数相反：主席有最终表决权，"
                "此项仅作记录对照，不质疑裁决。" % final_tier,
                {"final_tier": final_tier, "bullish": bullish_n, "bearish": bearish_n},
            ))
    return flags


def disagreement_report(record):
    """分歧标记的 dict 包装（供 API/页面直接使用；flags 本身是 list）。"""
    flags = disagreement_flags(record)
    counts = {"info": 0, "warn": 0}
    for item in flags:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return {
        "schema": FLAGS_SCHEMA,
        "count": len(flags),
        "by_severity": counts,
        "flags": flags,
        "not_a_verdict": True,
        "advisory": True,
        "replaces_d1": False,
        "readonly": True,
        "side_effects": [],
        "boundary": BOUNDARY,
        "notes": ["分歧标记只提示“材料上看到了什么”，不判断谁对谁错。"],
    }


def _evidence_dimension(coverage):
    score = _round_half_up(coverage["coverage"] * 100)
    return min(100, score + min(10, coverage["evidence_refs"] * 3))


def _balance_dimension(coverage, vote_map):
    lines = coverage["lines"]
    bull_ok = lines["bull"]["present"]
    bear_ok = lines["bear"]["present"]
    if bull_ok and bear_ok:
        score = 70
    elif bull_ok or bear_ok:
        score = 30
    else:
        score = 0
    t1_dir = lines["bull"]["direction"]
    t2_dir = lines["bear"]["direction"]
    if bull_ok and bear_ok and t1_dir and t2_dir and t1_dir != t2_dir:
        score += 15
    tiers = [item["tier"] for item in vote_map.values() if item["tier"]]
    if len(set(tiers)) >= 2:
        score += 15
    return min(100, score)


def _specificity_dimension(coverage):
    numbers = sum(min(4, coverage["lines"][name]["numbers"]) for name in LINES)
    refs = coverage["evidence_refs"] + coverage["inline_refs"]
    cited = sum(1 for name in LINES if coverage["lines"][name]["cited"])
    return min(100, numbers * 6 + refs * 6 + cited * 4)


def _dissent_dimension(record, vote_map):
    items = _dissent_items(record)
    if items:
        return min(100, 60 + 10 * len(items))
    if _votes_opposed(vote_map):
        return 30  # 票型已含反方意见，但没有显式 dissent 记录
    return 0


def _grade(score):
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def score_debate(record):
    """四线辩论质量分（0~100）+ 维度分（evidence/balance/specificity/dissent）。"""
    rec = _as_record(record)
    coverage = evidence_coverage(rec)
    vote_map = _vote_map(rec)
    dimensions = {
        "evidence": _evidence_dimension(coverage),
        "balance": _balance_dimension(coverage, vote_map),
        "specificity": _specificity_dimension(coverage),
        "dissent": _dissent_dimension(rec, vote_map),
    }
    missing = list(coverage["missing"])
    uncited = list(coverage["uncited"])
    penalty_points = (PENALTY_MISSING_LINE * len(missing)
                      + PENALTY_UNCITED_LINE * len(uncited))
    weighted = sum(dimensions[name] * WEIGHTS[name] for name in DIMENSIONS)
    score = max(0, min(100, _round_half_up(weighted - penalty_points)))
    return {
        "schema": SCHEMA,
        "score": score,
        "grade": _grade(score),
        "dimensions": dimensions,
        "weights": dict(WEIGHTS),
        "weighted_score": round(weighted, 2),
        "penalties": {
            "missing_lines": len(missing),
            "uncited_lines": len(uncited),
            "missing": missing,
            "uncited": uncited,
            "points": penalty_points,
        },
        "coverage": coverage,
        "flags": disagreement_flags(rec),
        "vote_count": len([item for item in vote_map.values() if item["tier"]]),
        "line_count": len(LINES) - len(missing),
        "not_a_verdict": True,
        "advisory": True,
        "replaces_d1": False,
        "readonly": True,
        "side_effects": [],
        "boundary": BOUNDARY,
        "notes": [
            "质量分用于比较材料完备度；低分≠结论错误，高分≠结论正确。",
            "缺整条线扣 %d 分、有内容无引用再扣 %d 分（已含在 score 内）。"
            % (PENALTY_MISSING_LINE, PENALTY_UNCITED_LINE),
            "D1 最终裁决权不变：本分数不推荐 tier、不改写 final_decision。",
        ],
    }


def _meeting_evidence(record, doc, vote_map):
    refs = _dedupe(_refs(record.get("evidence"), True))
    if refs:
        return refs
    merged = []
    for seat in VOTERS:
        merged.extend((vote_map.get(seat) or {}).get("evidence") or [])
    final = doc.get("final_decision")
    if isinstance(final, dict):
        merged.extend(_refs(final.get("evidence"), True))
    return _dedupe(merged)


def _meeting_dissent(record, doc):
    items = _dissent_items(record)
    if items:
        return items
    final = doc.get("final_decision")
    if isinstance(final, dict):
        return _dissent_items({"dissent": final.get("dissent")})
    return []


def _meeting_record(doc):
    """把 d1_meeting 纪要 doc 折叠成四线 record（票席 rationale 当四线材料）。"""
    record = {}
    for key in ("material", "record"):
        inner = doc.get(key)
        if isinstance(inner, dict) and inner:
            record.update(inner)
            break
    for name in LINES:
        value = _line_value(doc, name)
        if _text(value) and not _text(_line_value(record, name)):
            record[name] = value
    # record 形输入（未包 meeting 壳）时，保留顶层引用/反方意见
    for key in ("evidence", "dissent"):
        if not record.get(key) and key in doc:
            record[key] = doc[key]
    vote_map = _vote_map({"votes": doc.get("votes")})
    for name, seat in LINE_SEATS.items():
        if _text(_line_value(record, name)):
            continue
        vote = vote_map.get(seat) or {}
        if vote.get("rationale") or vote.get("evidence"):
            record[name] = {"rationale": vote.get("rationale", ""),
                            "evidence": vote.get("evidence", [])}
    record["votes"] = doc.get("votes")
    if doc.get("final_decision") is not None:
        record["final_decision"] = doc.get("final_decision")
    record["evidence"] = _meeting_evidence(record, doc, vote_map)
    record["dissent"] = _meeting_dissent(record, doc)
    return record


def evaluate_meeting(meeting_doc):
    """汇总一场 D1 投研会：quality（四线质量分）+ final tier（原样展示）。"""
    doc = meeting_doc if isinstance(meeting_doc, dict) else {}
    record = _meeting_record(doc)
    quality = score_debate(record)
    vote_map = _vote_map({"votes": doc.get("votes")})
    tiers = [item["tier"] for item in vote_map.values() if item["tier"]]
    missing_seats = [seat for seat in VOTERS
                     if seat not in vote_map or not vote_map[seat]["tier"]]
    final_tier = _final_tier({"final_decision": doc.get("final_decision"),
                              "final_tier": doc.get("final_tier")})
    final = doc.get("final_decision")
    final_confidence = _num(final.get("confidence")) if isinstance(final, dict) else None
    return {
        "schema": MEETING_SCHEMA,
        "meeting_id": _clip(doc.get("meeting_id"), 64),
        "code": _clip(doc.get("code"), 32),
        "topic": _clip(doc.get("topic"), 120),
        "status": _clip(doc.get("status"), 16),
        "vote_count": len(tiers),
        "votes_missing": missing_seats,
        "tier_distribution": _tier_distribution(vote_map),
        "final_tier": final_tier or None,
        "final_confidence": final_confidence,
        "quality": quality,
        "score": quality["score"],
        "grade": quality["grade"],
        "flags": quality["flags"],
        "not_a_verdict": True,
        "advisory": True,
        "replaces_d1": False,
        "readonly": True,
        "side_effects": [],
        "boundary": BOUNDARY,
        "notes": [
            "final_tier 原样展示 D1 最终表决；本模块不推荐、不改写、不覆盖该 tier。",
            "会议未 finalize（或未填 final_decision）时 final_tier 为 null。",
            "四线材料取自票席 rationale/evidence；未投票的席位按 missing 计。",
        ],
    }


__all__ = [
    "BOUNDARY", "FlagList", "LINES", "LINE_LABELS", "LINE_SEATS", "MEETING_SCHEMA",
    "SCHEMA", "TIERS", "VOTERS", "WEIGHTS", "disagreement_flags",
    "disagreement_report", "evaluate_meeting", "evidence_coverage", "score_debate",
]
