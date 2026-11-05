# -*- coding: utf-8 -*-
"""DPO v3 拒绝采样：W13 30 题 + citation 90 题（带上下文），k=4，产出百炼 DPO 格式。

输出：
- research/finetune/dpo/quant_kb_dpo_v3_train.jsonl   （Bailian DPO：messages/chosen/rejected）
- research/reports/dpo_sampling_v3_20261104.md
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
FT = REPO / "research" / "finetune"
sys.path.insert(0, str(FT))
sys.path.insert(0, str(REPO / "tools" / "finetune"))
sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))

import gen_dpo_pairs as g  # noqa: E402
import bailian_eval as be  # noqa: E402

MODEL = "qwen2.5-14b-instruct-a57a3521e4e0"
BASE = Path(r"C:/项目/_vault/secrets/dashscope_base_url.txt").read_text(encoding="utf-8").strip()
KEY = Path(r"C:/项目/_vault/secrets/dashscope_api_key.txt").read_text(encoding="utf-8").strip()
K = 4
CIT_LIMIT = 90
OUT = FT / "dpo" / "quant_kb_dpo_v3_train.jsonl"
REPORT = REPO / "research" / "reports" / "dpo_sampling_v3_20261104.md"

# 放宽 rejected 阈值：v4d 模型整体质量高，按排名取 bottom，仍保留分差过滤
g.REJECT_MAX = 0.90
g.GAP_MIN = 0.03


def load_items() -> list[dict]:
    w13 = be.load_w13(REPO / "research/reports/answer_eval_20261104.json")
    items = [d for d in be.build_dataset(w13) if d.get("answerable")]
    seen = {str(d.get("question")).strip() for d in items}
    cit = []
    for line in (FT / "sft" / "citation_clean.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        q = str(r.get("question") or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        cit.append({"qid": f"C{len(cit)+1:03d}", "type": "citation_qa", "answerable": True,
                    "question": q, "gold_doc_ids": r.get("gold_doc_ids") or [],
                    "gold_text": "", "ctx": r.get("contexts") or [], "w13_ctx_ids": []})
        if len(cit) >= CIT_LIMIT:
            break
    print(f"[load] w13={len(items)} citation={len(cit)} total={len(items)+len(cit)}", flush=True)
    return items + cit


def main() -> int:
    items = load_items()
    backend = g.OpenAICompatBackend(KEY, BASE, MODEL)
    pairs = []
    skipped = {}
    t0 = time.time()
    for i, item in enumerate(items, 1):
        try:
            answers = g.generate_candidates(backend, item, K, temperature=0.8)
        except Exception as e:
            skipped["gen_error"] = skipped.get("gen_error", 0) + 1
            print(f"[{i}/{len(items)}] {item['qid']} gen_err {type(e).__name__}: {e}", flush=True)
            continue
        scored = g.score_candidates(item, answers)
        chosen, rejected, reason = g.select_pair(scored)
        if chosen is None:
            skipped[reason] = skipped.get(reason, 0) + 1
        else:
            pairs.append({"item": item, "chosen": chosen, "rejected": rejected,
                          "gap": round(chosen["composite"] - rejected["composite"], 4)})
        if i % 10 == 0:
            print(f"[{i}/{len(items)}] pairs={len(pairs)} skipped={skipped} elapsed={time.time()-t0:.0f}s", flush=True)
    # 百炼 DPO 格式（纯净 schema：messages + chosen/rejected）
    # 2026-11-05 审计修复：messages 必须 >=2 条且以 user 结尾（W26 校验器只认
    # system+user），此前只写 1 条 user 导致 74/74 全判 messages_too_short。
    sys_msg = ("你是零界量化知识助手。只依据给定资料回答，并在每个事实句末用 [n] 标注来源；"
               "资料不足时明确说明无法回答；不构成投资建议。")
    rows = []
    for p in pairs:
        prompt = be.build_prompt(p["item"]["question"], p["item"]["ctx"])
        rows.append({
            "messages": [{"role": "system", "content": sys_msg},
                         {"role": "user", "content": prompt}],
            "chosen": {"role": "assistant", "content": p["chosen"]["answer"]},
            "rejected": {"role": "assistant", "content": p["rejected"]["answer"]},
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    stats = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "model": MODEL, "k": K, "questions": len(items), "pairs": len(rows),
             "skipped": skipped, "elapsed_s": round(time.time() - t0, 1)}
    (REPO / "research/reports/dpo_sampling_v3_20261104.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
