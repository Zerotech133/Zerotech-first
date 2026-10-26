# -*- coding: utf-8 -*-
"""经验入库（WO-LEGION-S26 · 进化层 Z4）。

把一次对照实验的结论写成**四字段经验**（cue/lesson/evidence/confidence）落进
赵云经验库的对应域，并维护 INDEX.md 与本地检索索引（Qdrant 只做索引不存本体
——本单先落 jsonl 索引，Qdrant 接线另单）。

硬门槛（《赵云进化层V3》§六 + §七 + V3.1）：
  · 无签字不写（Paperclip = 老田 approval_gate；沉默与「继续」不构成签字）；
  · riskguard 域**只读**——本模块一律拒写（阈值类观察只登记不生效）；
  · 四字段完整；confidence ∈ [0,1]；
  · 同域同 cue 归一化重复 → 拒写（去重键，防同一问题重复立案）；
  · evidence 中出现的路径先过 Z5 防穿越守卫（不许把 .grader/answers 写进经验）。

存储布局（赵云 profile）：
  experience/<domain>/EXPERIENCE.md   条目正文（追加）
  experience/<domain>/INDEX.md        条目索引表（追加）
  experience/_index.jsonl             检索索引（一行一条，供 search 用）
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import lookahead_guard  # noqa: E402
from engine import signature_guard  # noqa: E402

DOMAINS = ("stock_screen", "execution", "tooling", "riskguard", "supervision")
WRITABLE_DOMAINS = tuple(d for d in DOMAINS if d != "riskguard")
READONLY_DOMAINS = ("riskguard",)

DEFAULT_ROOT = Path.home() / ".hermes" / "profiles" / "zhaoyun" / "experience"


class StoreError(ValueError):
    """入库不合规（域/字段/重复）。"""


# P0-3：签字门升级为“可验证签名”（渐进式），异常类统一由 signature_guard 提供，
# 保留本模块旧名字导出，既有调用方/测试 `S.SignatureRequired` 不受影响。
SignatureRequired = signature_guard.SignatureRequired
SignatureError = signature_guard.SignatureError


@dataclass
class Entry:
    entry_id: str
    domain: str
    cue: str
    lesson: str
    evidence: str
    confidence: float
    signed_by: str
    written_at: str
    # P0-3 渐进签名：未配公钥/未传签名时 verified=False，如实记录不冒充已验签
    verified: bool = False
    signature_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "entry_id": self.entry_id, "domain": self.domain, "cue": self.cue,
            "lesson": self.lesson, "evidence": self.evidence,
            "confidence": self.confidence, "signed_by": self.signed_by,
            "written_at": self.written_at,
            "verified": self.verified, "signature_reason": self.signature_reason,
        }


def _root(root: Path | None) -> Path:
    return Path(root) if root else DEFAULT_ROOT


def _index_path(root: Path) -> Path:
    return root / "_index.jsonl"


def _load_index(root: Path) -> list:
    p = _index_path(root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _next_id(root: Path, domain: str) -> str:
    n = sum(1 for r in _load_index(root) if r.get("domain") == domain) + 1
    return "EXP-%s-%03d" % (domain.upper(), n)


def _check_evidence_paths(evidence: str) -> None:
    paths = re.findall(r"[A-Za-z]:[\\/][^\s，。;；]+|/[^\s，。;；]+", str(evidence or ""))
    if not paths:
        return
    try:
        lookahead_guard.assert_clean(paths=paths, stage="evolve")
    except lookahead_guard.LeakageDetected as exc:
        raise StoreError("evidence rejected by lookahead guard: %s" % exc) from exc


def write_entry(domain: str, cue: str, lesson: str, evidence: str,
                confidence: float, signed_by: str,
                root: Path | None = None, signature: dict | None = None,
                now: float | None = None) -> Entry:
    """写入一条四字段经验。返回 Entry；不合规抛 StoreError/SignatureRequired。

    P0-3（渐进签名）：`signature` 为可选 Ed25519 签名对象（payload 口径见
    signature_guard，item_id=entry_id、action="write"、fingerprint=内容指纹）。
    未提供时保留旧行为，但 Entry/索引里如实记录 `verified=False`；配公钥后自动
    验签，`EVOLVE_SIGNATURE_STRICT=1` 时无签名/无公钥直接拒绝。
    """
    if domain not in DOMAINS:
        raise StoreError("unknown domain: %s" % domain)
    if domain in READONLY_DOMAINS:
        raise StoreError("domain %s is read-only (风控域只读：不得写入)" % domain)
    if not (signed_by or "").strip():
        raise SignatureRequired("无签字不得入库（approval_gate 本人闭环）")
    for name, val in (("cue", cue), ("lesson", lesson), ("evidence", evidence)):
        if not str(val or "").strip():
            raise StoreError("field required: %s" % name)
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        raise StoreError("confidence must be number")
    if not 0.0 <= conf <= 1.0:
        raise StoreError("confidence out of range [0,1]")
    _check_evidence_paths(evidence)

    base = _root(root)
    existing = _load_index(base)
    key = _norm(cue)
    for row in existing:
        if row.get("domain") == domain and _norm(row.get("cue", "")) == key:
            raise StoreError("duplicate cue in domain %s (去重键命中): %s" % (domain, row.get("entry_id")))

    entry_id = _next_id(base, domain)
    auth = signature_guard.check(
        kind="experience_entry", item_id=entry_id,
        fingerprint=signature_guard.fingerprint_of({
            "domain": domain, "cue": str(cue).strip(),
            "lesson": str(lesson).strip(), "evidence": str(evidence).strip(),
            "confidence": conf}),
        action="write", signed_by=signed_by, signature=signature, now=now)

    entry = Entry(
        entry_id=entry_id,
        domain=domain,
        cue=str(cue).strip(),
        lesson=str(lesson).strip(),
        evidence=str(evidence).strip(),
        confidence=conf,
        signed_by=signed_by.strip(),
        written_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        verified=bool(auth.get("verified")),
        signature_reason=str(auth.get("reason", "")),
    )

    d = base / domain
    d.mkdir(parents=True, exist_ok=True)
    exp = d / "EXPERIENCE.md"
    idx = d / "INDEX.md"
    if not exp.exists():
        exp.write_text("# EXPERIENCE — %s\n" % domain, encoding="utf-8")
    if not idx.exists():
        idx.write_text("# INDEX — %s\n\n| 编号 | cue | confidence | 日期 |\n|---|---|---|---|\n" % domain,
                       encoding="utf-8")

    with exp.open("a", encoding="utf-8") as f:
        f.write("\n## %s\n- **cue**: %s\n- **lesson**: %s\n- **evidence**: %s\n"
                "- **confidence**: %s\n- **采纳**: %s 签字于 %s\n"
                "- **验签**: %s（%s）\n" % (
                    entry.entry_id, entry.cue, entry.lesson, entry.evidence,
                    entry.confidence, entry.signed_by, entry.written_at,
                    "verified" if entry.verified else "unverified",
                    entry.signature_reason))
    with idx.open("a", encoding="utf-8") as f:
        f.write("| %s | %s | %s | %s |\n" % (
            entry.entry_id, entry.cue[:40], entry.confidence,
            entry.written_at[:10]))
    with _index_path(base).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
    return entry


def search(query: str, domain: str | None = None, root: Path | None = None,
           limit: int = 10) -> list:
    """检索经验（本地索引版）：CJK 子串 + 词命中数排序，confidence 加权。

    验收判据：一条经验可被下个同类任务检索到。
    """
    if domain is not None and domain not in DOMAINS:
        raise StoreError("unknown domain: %s" % domain)
    # 先按空白/标点切词，再逐个归一（不能先 norm：会把空格吃掉使多词退化为长 token）
    raw_tokens = [t for t in re.split(r"[\s,，。;；、]+", str(query or "")) if t.strip()]
    tokens = [_norm(t) for t in raw_tokens if _norm(t)]
    if not tokens:
        return []
    hits = []
    for row in _load_index(_root(root)):
        if domain and row.get("domain") != domain:
            continue
        blob = _norm(row.get("cue", "")) + _norm(row.get("lesson", "")) + _norm(row.get("evidence", ""))
        score = sum(1 for t in tokens if t and t in blob)
        if score == 0:
            continue
        hits.append({"score": round(score + float(row.get("confidence", 0)) * 0.01, 4),
                     **row})
    hits.sort(key=lambda r: -r["score"])
    return hits[:limit]


def _demo(tmp: Path) -> int:
    """Z4 验收演示：写一条真实验→检索命中→打印落盘片段（临时根，不污染真库）。"""
    e = write_entry(
        domain="stock_screen",
        cue="RiskGuard 误拦率偏高的候选判据是否值得采纳",
        lesson="采纳样本外验证口径：误拦率 -7pp、样本外收益 +0.5pp，回滚预案在位",
        evidence="对照实验 card=EX-demo verifier=3（基线 0.18 / 候选 0.11）",
        confidence=0.6,
        signed_by="laotian",
        root=tmp,
    )
    print("written:", e.entry_id, "|", e.domain, "| conf", e.confidence)
    hits = search("误拦率 判据", root=tmp)
    print("search hits:", len(hits), "->", hits[0]["entry_id"] if hits else "MISS")
    print("---- EXPERIENCE.md ----")
    print((tmp / "stock_screen" / "EXPERIENCE.md").read_text(encoding="utf-8")[:600])
    return 0


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        raise SystemExit(_demo(Path(td)))
