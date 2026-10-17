# -*- coding: utf-8 -*-
"""知识提炼器（LCS-2 知识环）：让 AI 越来越聪明的飞轮

扫描源：晨会记录 lessons/decisions 字段 + 复盘卡 q3 + 工单 done 小结
提炼规则（反哺判据 LCS-2 §六）：可证伪可复用才收；防膨胀每周每人 ≤5 条
输出：agent__<ID>__knowledge.jsonl（个人长记性）+ 全军团判例待审清单
"""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat

MEET = ROOT / "agent_platform" / "data" / "meetings"
REVIEW = ROOT / "agent_platform" / "data" / "daily_review"
MEM = ROOT / "agent_platform" / "data" / "memory"
PENDING = ROOT / "agent_platform" / "data" / "knowledge_pending.jsonl"

# 提炼模板：从文本抽取结构化知识条目
LESSON_PAT = re.compile(r"lessons?[:：]\s*(.+)")

def scan_meetings() -> list:
    """扫全部晨会记录的 lessons/decisions 行。"""
    out = []
    if not MEET.exists():
        return out
    for f in sorted(MEET.glob("*_晨会.md")):
        txt = f.read_text(encoding="utf-8")
        dept = f.stem.split("_")[1] if "_" in f.stem else "?"
        for ln in txt.splitlines():
            l = ln.strip()
            if l.startswith("- lessons:") and "待填" not in l:
                content = l.split("lessons:", 1)[1].strip()
                if content and len(content) > 8:
                    out.append({"kind": "lesson", "dept": dept, "content": content[:160],
                                "src": f.name, "ts": time.strftime("%Y-%m-%d")})
            elif l.startswith("- decisions:") and "待填" not in l:
                content = l.split("decisions:", 1)[1].strip()
                if content and len(content) > 6:
                    out.append({"kind": "decision", "dept": dept, "content": content[:160],
                                "src": f.name, "ts": time.strftime("%Y-%m-%d")})
    return out


def scan_academy_outputs() -> list:
    """扫描研究院专属产出，把可复现研究字段送入待审知识池。"""
    out = []
    base = ROOT / "agent_platform" / "data" / "academy"
    if not base.exists():
        return out
    for f in base.rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        if all(x in text for x in ("假设", "证据", "结论", "行动")):
            out.append({"kind": "academy_research", "dept": "academy",
                        "content": " ".join(text.split())[:300],
                        "src": str(f), "ts": time.strftime("%Y-%m-%d")})
    return out


def scan_review_q3() -> list:
    """复盘卡 q3 中的可沉淀句（含'判例/沉淀/固化'关键词的）。"""
    out = []
    if not REVIEW.exists():
        return out
    for d in sorted(REVIEW.glob("2*"), reverse=True)[:3]:  # 近3天
        for f in d.glob("*.md"):
            if f.stem.startswith("_"):
                continue
            for ln in f.read_text(encoding="utf-8").splitlines():
                if "③" in ln:
                    q3 = ln.split("：", 1)[-1].strip()
                    if any(k in q3 for k in ("判例", "沉淀", "固化", "知识库")):
                        out.append({"kind": "insight", "agent": f.stem,
                                    "content": q3[:160], "src": str(f), "ts": d.name})
    return out


def _fingerprint(item):
    raw = "%s|%s|%s|%s" % (item.get("kind", ""), item.get("agent", item.get("dept", "")), item.get("content", ""), item.get("src", ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _pending_fingerprints():
    seen = set()
    if PENDING.exists():
        for line in PENDING.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("fingerprint"):
                    seen.add(row["fingerprint"])
                elif row.get("content"):
                    seen.add(_fingerprint(row))
            except json.JSONDecodeError:
                continue
    return seen


def distill():
    items = scan_meetings() + scan_academy_outputs() + scan_review_q3()
    if not items:
        return {"n": 0, "agents": 0, "pending": 0, "duplicates": 0}
    seen = _pending_fingerprints()
    unique, duplicates = [], 0
    for item in items:
        fp = _fingerprint(item)
        if fp in seen:
            duplicates += 1
            continue
        item["fingerprint"] = fp
        seen.add(fp)
        unique.append(item)
    items = unique
    if not items:
        return {"n": 0, "agents": 0, "pending": 0, "duplicates": duplicates}
    # 写个人 knowledge（lesson/insight 归人）与待审池（decision 归部，部务会拍板）
    MEM.mkdir(parents=True, exist_ok=True)
    per_agent = {}
    pend = []
    for it in items:
        if it["kind"] in ("lesson", "insight") and it.get("agent"): 
            per_agent.setdefault(it["agent"], []).append(it)
        else:
            pend.append(it)
    n_written = 0
    for aid, lst in per_agent.items():
        f = MEM / ("agent__%s__knowledge.jsonl" % aid)
        existing = f.read_text(encoding="utf-8") if f.exists() else ""
        with open(f, "a", encoding="utf-8") as fh:
            for it in lst:
                rec = {"ts": it["ts"], "kind": it["kind"], "content": it["content"],
                       "src": it["src"], "fingerprint": it["fingerprint"], "status": "pending_review"}
                line = json.dumps(rec, ensure_ascii=False)
                if line not in existing:
                    fh.write(line + "\n")
                    n_written += 1
    with open(PENDING, "a", encoding="utf-8") as fh:
        for it in pend:
            fh.write(json.dumps({**it, "status": "pending_review"}, ensure_ascii=False) + "\n")
    heartbeat("A0A", "distill", "知识提炼 %d 条（个人%d/待审%d/重复%d）" % (n_written + len(pend), n_written, len(pend), duplicates))
    return {"n": n_written + len(pend), "agents": len(per_agent), "pending": len(pend), "duplicates": duplicates}


if __name__ == "__main__":
    print(json.dumps(distill(), ensure_ascii=False))
