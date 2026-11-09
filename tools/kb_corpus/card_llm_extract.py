# -*- coding: utf-8 -*-
"""LLM 提炼试点：对重提炼后仍被驳回的候选，用本地 qwen3.5:9b 重新抽取 concept/answer。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# 回环禁代理：Ollama 在 127.0.0.1:11434，系统代理会接管回环请求（见 tools/safe_urlopen.py）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
R = REPO / "research/reports"
PID = "视频号学习_8博主"
DATE = datetime.now().strftime("%Y%m%d")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"
SYSTEM = ('你是学习卡片抽取器。从用户给的口语文字中抽出1个名词性概念（4-12个汉字，'
          '如“均线粘合”“仓位管理”“通达信公式报错”），并用原文事实写20-120字答案。'
          '只输出 JSON：{"concept":"...","answer":"..."}，不要解释。')
FILLER = re.compile(r"^(看|说|再|那|这|今天|首先|然后|接下来|如果|所以|但是|其实|因为|我|你|他|大家|我们|各位)")


def chat(text: str, timeout: int = 300) -> str:
    payload = {"model": MODEL, "format": "json", "stream": False, "think": False,
               "options": {"temperature": 0.1, "num_predict": 500},
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": text[:800]}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with safe_urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("message") or {}).get("content", "")


def extract_one(item: dict) -> dict | None:
    raw = ""
    for _ in range(int(item.get("retries", 2))):
        try:
            raw = chat(item["claim"])
            obj = json.loads(raw)
            break
        except Exception:  # noqa: BLE001
            obj = None
            continue
    if obj is None:
        return None
    concept = str(obj.get("concept") or "").strip()
    answer = str(obj.get("answer") or "").strip()
    if not (3 <= len(concept) <= 14) or any(ch in concept for ch in "，。；:：,.!?？ \t·`*|"):
        return None
    if FILLER.match(concept) or len(answer) < 20:
        return None
    return {"card_id": item["card_id"], "unit_id": item["unit_id"], "blogger": item["blogger"],
            "concept": concept, "q": f"什么是「{concept}」？", "a": answer[:300],
            "source": item["source"], "evidence": item["evidence"], "theme": "其他"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    prop = json.loads((R / f"card_proposals_{PID}_reextract_20261103.json").read_text(encoding="utf-8"))
    cands = json.loads((R / f"card_candidates_{PID}_v2_20261103.json").read_text(encoding="utf-8"))
    units = {}
    for line in (KB / "30_知识单元" / f"{PID}_units_draft.jsonl").open(encoding="utf-8"):
        if line.strip():
            u = json.loads(line)
            units[u["unit_id"]] = u
    # 以 candidates 的 card_id 建立 单位映射（precheck cards 不带 unit_id）
    cand_by_id = {c["card_id"]: c for c in cands.get("candidates") or []}
    rejected = [c for c in prop["cards"] if c["review"]["checkboxes"]["reject"]]
    items = []
    for c in rejected:
        raw = cand_by_id.get(c["card_id"])
        if not raw:
            continue
        u = units.get(raw.get("unit_id"))
        if not u or len(str(u.get("claim") or "")) < 40:
            continue
        items.append({"card_id": c["card_id"], "unit_id": raw.get("unit_id"),
                      "blogger": c.get("blogger"), "source": c.get("source"),
                      "evidence": c.get("evidence"), "claim": str(u.get("claim") or "")})
    llm_p = R / f"card_candidates_{PID}_llm_{DATE}.json"
    existing = []
    done_units = set()
    if llm_p.exists():
        try:
            ed = json.loads(llm_p.read_text(encoding="utf-8"))
            existing = ed.get("candidates") or []
            done_units = {c.get("unit_id") for c in existing if c.get("unit_id")}
        except ValueError:
            existing = []
    if args.skip_existing:
        items = [i for i in items if i["unit_id"] not in done_units]
    items.sort(key=lambda x: -len(x["claim"]))
    items = items[:args.limit]
    for it in items:
        it["retries"] = args.retries
    print(f"selected {len(items)} rejected for LLM; workers={args.workers}")
    out = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(extract_one, items), 1):
            if res:
                out.append(res)
            if i % 10 == 0:
                print(f"  progress {i}/{len(items)} ok={len(out)}")
    print(f"LLM extracted {len(out)}/{len(items)}")
    result = {"schema_version": "card_candidates.llm.v1", "date": DATE,
              "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "generator": "card_llm_extract.py", "model": MODEL,
              "pid": PID, "sample": len(items), "extracted": len(out), "candidates": out}
    all_cards = existing + out
    result["candidates"] = all_cards
    result["cumulative"] = len(all_cards)
    if args.write:
        p = llm_p
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        (REPO / "portal/static" / f"card_candidates_{PID}_llm.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("written:", p.name)
    for c in out[:10]:
        print("-", c["concept"], "|", c["a"][:50])
    return 0


if __name__ == "__main__":
    sys.exit(main())
