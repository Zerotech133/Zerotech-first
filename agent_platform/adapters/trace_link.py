# -*- coding: utf-8 -*-
"""trace_link：投研链 trace 贯通映射层（M4-P1）

设计（docs/audit/2026-10-17_M4规划-投研与模拟执行线.md P1 刀）：
  影子(run_id) ↔ Paper(trace_id) ↔ 成交(order_id) ↔ 绩效(perf_key)
  只读映射层——各环写侧零侵入；环间关联只能由**显式声明**写入
  （record_link），不做猜测匹配（无源 ID 不伪造判例）。

纪律：
  - append-only：data/trace_links.jsonl，幂等键=链上非空环节键的
    canonical hash，同链重放跳过、同键异链拒绝（对齐 Approval 语义）。
  - 缺环允许：链可为 partial，回放时缺环显式 missing，不断链报错。
  - 环锚点规范化：每环节提供 link_key() 纯函数（规范化校验在此层）。
  - 只读消费：影子账本/ai_operator store/execution 事件均 mode=ro 或
    逐行读，零写副作用（M3 ro 观测面判例）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
LINKS_FILE = REPO_ROOT / "agent_platform" / "data" / "trace" / "trace_links.jsonl"
SHADOW_LEDGER = REPO_ROOT / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"

SCHEMA_VERSION = "m4p1.v1"
# 链环节次序（回放时间线按此排序；perf 槽位 P4 启用）
SEG_ORDER = ("shadow", "paper", "execution", "perf")
# 各环必填锚点字段（link 校验与规范化依据）
SEG_REQUIRED = {
    "shadow": ("run_id", "symbol", "trade_date"),
    "paper": ("trace_id",),
    "execution": ("order_id",),
    "perf": ("perf_key",),
}


# ---------------------------------------------------------------- 环锚点纯函数

def link_key(segment: str, ref: Dict) -> str:
    """环锚点规范化键：`<segment>:<排序后必填键值 joined>`。

    缺必填字段抛 ValueError（伪造/残缺锚点在此层拒绝）。
    """
    if segment not in SEG_REQUIRED:
        raise ValueError("unknown segment: %s" % segment)
    parts = []
    for field in SEG_REQUIRED[segment]:
        value = ref.get(field)
        if not value or not isinstance(value, str):
            raise ValueError("segment %s 缺必填锚点 %s" % (segment, field))
        parts.append(value.strip())
    return "%s:%s" % (segment, "|".join(parts))


def link_id_of(chain: Dict[str, Dict]) -> str:
    """链幂等键：非空环节 link_key 排序后 canonical hash（16 位截断）。"""
    if not chain:
        raise ValueError("空链不允许登记")
    keys = sorted(link_key(seg, ref) for seg, ref in chain.items())
    payload = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_chain(chain: Dict[str, Dict]) -> List[str]:
    """链结构校验：返回 findings（空=合法）。锚点缺失/环节未知即 finding。"""
    findings = []
    if not isinstance(chain, dict) or not chain:
        return ["chain 为空或非对象"]
    for seg, ref in chain.items():
        if seg not in SEG_ORDER:
            findings.append("unknown segment: %s" % seg)
            continue
        try:
            link_key(seg, ref)
        except ValueError as exc:
            findings.append(str(exc))
        if not isinstance(ref, dict):
            findings.append("segment %s ref 非对象" % seg)
    return findings


# ---------------------------------------------------------------- 存储（append-only）

class LinkStore:
    """trace_links.jsonl 追加账本。同链幂等跳过；同键异链拒绝。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else LINKS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self._index[row["link_id"]] = row

    def _append(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False,
                                separators=(",", ":")) + "\n")

    def record(self, chain: Dict[str, Dict], source: str = "",
               declared_at: str = "") -> Dict:
        """显式声明一条环间关联。返回 {status, link_id, findings}。

        status: recorded（新登记）/ replayed（同链重放零副作用）/
        conflict（同键异链拒绝，抛前返回详情由调用方决策时用——本层直接
        抛 ValueError 对齐 ApprovalStore 同键异参语义）。
        """
        findings = validate_chain(chain)
        if findings:
            raise ValueError("链校验失败: %s" % "; ".join(findings))
        lid = link_id_of(chain)
        row = {
            "schema_version": SCHEMA_VERSION,
            "link_id": lid,
            "chain": {seg: dict(ref) for seg, ref in chain.items()},
            "source": source,
            "declared_at": declared_at,
        }
        existing = self._index.get(lid)
        if existing is not None:
            if existing.get("chain") != row["chain"]:
                raise ValueError(
                    "trace link 冲突：link_id=%s 已存在但链内容不一致 "
                    "(M0 契约：同键异值拒绝)" % lid)
            return {"status": "replayed", "link_id": lid, "findings": []}
        self._index[lid] = row
        self._append(row)
        return {"status": "recorded", "link_id": lid, "findings": []}

    def get(self, link_id: str) -> Optional[dict]:
        return self._index.get(link_id)

    def all(self) -> List[dict]:
        return list(self._index.values())

    def stats(self) -> dict:
        seg_counts = {seg: 0 for seg in SEG_ORDER}
        for row in self._index.values():
            for seg in row["chain"]:
                seg_counts[seg] = seg_counts.get(seg, 0) + 1
        return {"total": len(self._index), "by_segment": seg_counts}


# ---------------------------------------------------------------- 只读收集器

def collect_shadow_anchors(ledger: Optional[Path] = None) -> List[Dict]:
    """影子账本只读提取环锚点：done 行 → {run_id, symbol, trade_date}。

    逐行读、坏行留过不中断（长跑判例）；status!=done 不产锚点
    （failed/parse_ok=false 行不具备回放价值，交影子环自身告警）。
    """
    path = Path(ledger) if ledger else SHADOW_LEDGER
    if not path.exists():
        return []
    anchors = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") != "done" or not row.get("run_id"):
            continue
        anchors.append({
            "run_id": str(row["run_id"]),
            "symbol": str(row.get("symbol", "")),
            "trade_date": str(row.get("date", "")),
            "score": row.get("score"),
            "parse_ok": row.get("parse_ok"),
        })
    return anchors


# ---------------------------------------------------------------- 回放

def replay_trace(store: LinkStore, target: str) -> Dict:
    """按环锚点值回放：target 命中任一环节的必填锚点值即整链时间线。

    返回 {verdict, matches, findings}；verdict: OK（命中≥1 链）/
    NOT_FOUND。缺环显式 missing_segments（partial 链正常回放）。
    """
    matches = []
    for row in store.all():
        hit = False
        for seg, ref in row["chain"].items():
            for field in SEG_REQUIRED[seg]:
                if str(ref.get(field, "")).strip() == target:
                    hit = True
                    break
            if hit:
                break
        if hit:
            missing = [s for s in SEG_ORDER if s not in row["chain"]]
            matches.append({
                "link_id": row["link_id"],
                "chain": row["chain"],
                "missing_segments": missing,
                "source": row.get("source", ""),
                "declared_at": row.get("declared_at", ""),
            })
    if not matches:
        return {"verdict": "NOT_FOUND", "matches": [],
                "findings": ["无链命中锚点值: %s" % target]}
    return {"verdict": "OK", "matches": matches, "findings": []}


# ---------------------------------------------------------------- CLI

def _cli(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="trace_link 映射层 CLI")
    ap.add_argument("cmd", choices=["stats", "ls", "replay"])
    ap.add_argument("target", nargs="?", help="replay 的锚点值")
    args = ap.parse_args(argv)
    store = LinkStore()
    if args.cmd == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "ls":
        for row in store.all():
            chain_desc = " -> ".join(
                "%s(%s)" % (seg, link_key(seg, ref).split(":", 1)[1])
                for seg in SEG_ORDER if seg in row["chain"])
            print(row["link_id"], "|", chain_desc)
        return 0
    if args.cmd == "replay":
        if not args.target:
            print("replay 需要 target", file=sys.stderr)
            return 2
        result = replay_trace(store, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result["verdict"] == "OK" else 1
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
