# -*- coding: utf-8 -*-
"""EvidenceBundle（2026-10-30 WO-D1-EVIDENCE，执行"无证据不调 D1"）。

从 make_context 的上下文段构造可核验证据引用：source/uri/as_of/hash/freshness。
不伪造来源；段为空就不进 bundle。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field

from l4_artifact import validate_artifact_ref

SEGMENTS = ("market_technical", "fundamental", "news_event", "final_decision")
REQUIRED = ("market_technical", "fundamental")


@dataclass
class EvidenceRef:
    source: str
    uri: str
    as_of: str
    hash: str
    freshness_s: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class EvidenceBundle:
    symbol: str
    name: str
    refs: list = field(default_factory=list)
    l4_status: str = "missing"  # established|unresolved|missing|unknown
    l4_source: str = ""
    l4_reason: str = ""         # 机读原因：no_research_artifact 等（B1，WO-D1-B123）
    input_degraded: str = ""

    @classmethod
    def from_context(cls, symbol, name, ctx, now=None, l4=None, artifact_root=None):
        now = now or time.time()
        as_of = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
        refs = []
        texts = {}
        for seg in SEGMENTS:
            text = str((ctx or {}).get(seg) or "").strip()
            if not text:
                continue
            texts[seg] = text
            refs.append(EvidenceRef(
                source=seg,
                uri="make_context://%s/%s" % (symbol, seg),
                as_of=as_of,
                hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                freshness_s=0,
            ))
        l4_status, l4_source = _detect_l4(texts)
        l4_reason = ""
        if isinstance(l4, dict):
            status = str(l4.get("status") or "").strip().lower()
            l4_status = status if status in ("established", "unresolved", "missing", "unknown") else "unknown"
            l4_source = str(l4.get("source") or "") + (
                ":" + str(l4.get("as_of")) if l4.get("as_of") else "")
            l4_reason = str(l4.get("reason") or "").strip()
            # 2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0）：消费侧二次防线——
            # 调用方直接塞 dict 绕过 l4_provider 时，established 仍须带可核验
            # artifact_ref，否则降级 unresolved（不静默信任上游 dict）。
            # 2026-10-31 WO-D1-B123-20261031（B1）：降级落机读原因
            # no_research_artifact，并把标记写进 l4_source 供账本/日报追溯。
            if l4_status == "established":
                ok, reason, _ = validate_artifact_ref(l4.get("artifact_ref"),
                                                      root=artifact_root)
                if not ok:
                    l4_status = "unresolved"
                    l4_reason = "no_research_artifact"
                    l4_source = "%s;no_research_artifact:%s" % (l4_source, reason)
        bundle = cls(symbol=str(symbol), name=str(name or ""), refs=refs,
                     l4_status=l4_status, l4_source=l4_source,
                     l4_reason=l4_reason)
        if not bundle.is_sufficient():
            bundle.input_degraded = "missing:" + ",".join(
                s for s in REQUIRED if s not in texts)
        return bundle

    def is_sufficient(self):
        have = {r.source for r in self.refs}
        return len(self.refs) >= 3 and all(s in have for s in REQUIRED)

    def to_dict(self):
        d = asdict(self)
        d["refs"] = [asdict(r) for r in self.refs]
        return d


def _detect_l4(texts):
    """从上下文里识别 L4 状态；不猜：找不到就 missing。"""
    blob = "\n".join(texts.values())
    if "L4" not in blob:
        return "missing", ""
    for seg, text in texts.items():
        for line in text.splitlines():
            if "L4" not in line:
                continue
            if any(k in line for k in ("存疑", "观察", "散手")):
                return "unresolved", "%s:%s" % (seg, line.strip()[:80])
            if any(k in line for k in ("成立", "定性成立", "确定")):
                return "established", "%s:%s" % (seg, line.strip()[:80])
    return "unknown", ""
