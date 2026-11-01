# -*- coding: utf-8 -*-
"""采集即服务平台 · 归一化 / 跨源身份归并 / 消费契约

解决原型审计反复点名的三个缺口：
  1) schema 归一化：各适配器 payload 形态不一，下游无法直接消费；
  2) 跨源身份归并：同一只票（SH600519 / 600519）散落在 xueqiu/guba/news，需统一键；
  3) 消费契约：下游要的是"按实体聚合的稳定视图"，而非原始快照。

设计：
  - CanonicalEntity = (domain, key)。A 股归并到 domain="astock"，用 normalize_symbol
    自动补板前缀（60/68/90→SH，00/30→SZ，8/4→BJ），使 SH600519 与 600519 归一；
    YouTube 等系统外来源走 domain="yt"/"generic"，杜绝跨命名空间误并。
  - Observation 不可变、不覆盖；本层只做"只读投影"，原快照仍由 store 保管。
  - 不触网、不改既有适配器。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contracts import Observation


# ---------- A 股代码归一化 ----------

_BOARD_BY_PREFIX2 = {"60": "SH", "68": "SH", "90": "SH", "00": "SZ", "30": "SZ"}


def normalize_symbol(raw: Optional[str]) -> Optional[str]:
    """把任意形态 A 股代码归一为 {BOARD}{CODE6}；非 A 股代码返回 None。

    例：SH600519->SH600519；600519->SH600519；000001->SZ000001；830799->BJ830799；
        纯文本/不足6位->None。
    """
    if not raw:
        return None
    s = str(raw).strip().upper()
    s = re.sub(r"^(SH|SZ|BJ)\s*", "", s)          # 去已有板前缀
    m = re.search(r"\d{6}", s)
    if not m:
        return None
    code = m.group(0)
    if not re.fullmatch(r"\d{6}", code):
        return None
    lead2 = code[:2]
    if lead2 in _BOARD_BY_PREFIX2:
        board = _BOARD_BY_PREFIX2[lead2]
    elif code[0] in ("8", "4"):                  # 北交所
        board = "BJ"
    else:
        return None
    return f"{board}{code}"


_SYMBOL_RE = re.compile(r"\d{6}")


def extract_symbols(text: str) -> list[str]:
    """从任意文本抽取并归一 A 股代码（去重、保序）。"""
    out: list[str] = []
    for tok in _SYMBOL_RE.findall(text or ""):
        sym = normalize_symbol(tok)
        if sym and sym not in out:
            out.append(sym)
    return out


# ---------- 跨源实体 ----------

@dataclass(frozen=True)
class CanonicalEntity:
    """跨源归并后的规范实体键。domain 隔离命名空间，避免不同来源误并。"""
    domain: str
    key: str

    def __str__(self) -> str:
        return f"{self.domain}:{self.key}"


def _collect_text(obs: Observation) -> str:
    """尽力拼接一条 Observation 的可索引文本（title/desc/text/content）。"""
    p = obs.payload or {}
    parts: list[str] = []

    # 股吧 posts
    for it in (p.get("posts") or []):
        if isinstance(it, dict):
            v = it.get("title")
            if isinstance(v, str) and v.strip():
                parts.append(v)
    # 雪球 / 新闻 items
    for it in (p.get("items") or []):
        if isinstance(it, dict):
            for k in ("title", "desc", "text", "content"):
                v = it.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
    # YouTube envelope info
    info = p.get("info")
    if isinstance(info, dict):
        for k in ("title", "description", "text"):
            v = info.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return " ".join(parts)


def resolve_entities(obs: Observation) -> list[CanonicalEntity]:
    """把一条 Observation 解析为其涉及的规范实体（0..N 个）。只读投影，不改原快照。"""
    src = obs.source_id
    p = obs.payload or {}
    ents: list[CanonicalEntity] = []

    if src == "xueqiu":
        tgt = p.get("target")
        sym = normalize_symbol(tgt) if tgt else None
        if sym:
            ents.append(CanonicalEntity("astock", sym))
        for it in (p.get("items") or []):
            if isinstance(it, dict):
                tt = (it.get("target") or {})
                tt = tt.get("id") or tt.get("symbol") if isinstance(tt, dict) else None
                s2 = normalize_symbol(tt)
                if s2:
                    ents.append(CanonicalEntity("astock", s2))
        ents += [CanonicalEntity("astock", s) for s in extract_symbols(_collect_text(obs))]

    elif src == "guba":
        sym = normalize_symbol(p.get("code6"))
        if sym:
            ents.append(CanonicalEntity("astock", sym))
        ents += [CanonicalEntity("astock", s) for s in extract_symbols(_collect_text(obs))]

    elif src == "news":
        # 新闻本身作为 news 域实体，同时从标题抽取提及的 A 股代码
        ep = p.get("endpoint")
        if ep:
            ents.append(CanonicalEntity("news", str(ep)))
        ents += [CanonicalEntity("astock", s) for s in extract_symbols(_collect_text(obs))]

    elif src == "youtube_envelope":
        key = obs.entity_id or (p.get("info") or {}).get("id")
        if key:
            ents.append(CanonicalEntity("yt", str(key)))

    else:
        # 通用兜底：文本抽码 + entity_id
        ents += [CanonicalEntity("astock", s) for s in extract_symbols(_collect_text(obs))]
        if obs.entity_id:
            ents.append(CanonicalEntity("generic", obs.entity_id))

    # 去重保序
    seen: set[CanonicalEntity] = set()
    out: list[CanonicalEntity] = []
    for e in ents:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


# ---------- 消费契约（下游稳定视图） ----------

@dataclass
class CanonicalRecord:
    """一条 Observation 的规范投影：下游消费的原子记录。"""
    observation_id: str
    source_id: str
    entity_type: str
    status: str
    collected_at: Optional[str]
    published_at: Optional[str]
    content_kind: Optional[str]
    text: str
    entities: list[CanonicalEntity] = field(default_factory=list)
    allowed_use_cases: tuple[str, ...] = ()


def canonicalize(obs: Observation) -> CanonicalRecord:
    """把 Observation 投影为 CanonicalRecord（含跨源实体）。"""
    return CanonicalRecord(
        observation_id=obs.snapshot_id,
        source_id=obs.source_id,
        entity_type=obs.entity_type,
        status=obs.status,
        collected_at=obs.clock.collected_at,
        published_at=obs.clock.platform_published_at,
        content_kind=obs.source_field,
        text=_collect_text(obs),
        entities=resolve_entities(obs),
        allowed_use_cases=obs.allowed_use_cases,
    )


@dataclass
class EntityRollup:
    """某规范实体（domain,key）的跨源聚合：下游消费契约的核心对象。"""
    domain: str
    key: str
    observation_count: int = 0
    sources: set[str] = field(default_factory=set)
    entity_types: set[str] = field(default_factory=set)
    statuses: Counter = field(default_factory=Counter)
    first_collected: Optional[str] = None
    last_collected: Optional[str] = None
    sample_texts: list[str] = field(default_factory=list)

    MAX_SAMPLE = 5

    def add(self, rec: CanonicalRecord) -> None:
        self.observation_count += 1
        self.sources.add(rec.source_id)
        self.entity_types.add(rec.entity_type)
        self.statuses[rec.status] += 1
        if rec.collected_at:
            if self.first_collected is None or rec.collected_at < self.first_collected:
                self.first_collected = rec.collected_at
            if self.last_collected is None or rec.collected_at > self.last_collected:
                self.last_collected = rec.collected_at
        if rec.text and len(self.sample_texts) < self.MAX_SAMPLE:
            self.sample_texts.append(rec.text[:120])

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "key": self.key,
            "observation_count": self.observation_count,
            "sources": sorted(self.sources),
            "source_count": len(self.sources),
            "entity_types": sorted(self.entity_types),
            "statuses": dict(self.statuses),
            "first_collected": self.first_collected,
            "last_collected": self.last_collected,
            "sample_texts": self.sample_texts,
        }


def rollup_by_entity(observations: Iterable[Observation]) -> dict[tuple[str, str], EntityRollup]:
    """跨源实体聚合：把多条 Observation 映射到 (domain,key) 的稳定消费视图。

    这是平台对外（下游分析/检索/告警）的消费契约——下游只认 (domain,key) 聚合，
    不关心原始快照形态。返回 {(domain,key): EntityRollup}。
    """
    groups: dict[tuple[str, str], EntityRollup] = {}
    for obs in observations:
        rec = canonicalize(obs)
        for ent in rec.entities:
            g = groups.get((ent.domain, ent.key))
            if g is None:
                g = EntityRollup(domain=ent.domain, key=ent.key)
                groups[(ent.domain, ent.key)] = g
            g.add(rec)
    return groups
