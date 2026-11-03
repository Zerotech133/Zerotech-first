# -*- coding: utf-8 -*-
"""D0 数据管线（按《量化投资知识库垂域模型微调训练方案 v1》）：
7 步清洗 → SFT 构造（A/B/D/E + C 待生成 + F 缺口）→ DPO 规则对 → held-out 105 → dataset_info/配置/报告。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
S = REPO / "portal/static"
KB = Path(r"C:\项目\知识库")
OUT = REPO / "research/finetune"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")
STAMP = datetime.now().strftime("%Y%m%d")

# ---------------- 载入源数据 ----------------
cards_doc = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8"))
cards = cards_doc.get("cards") or []
reviews = json.loads((R / "card_reviews_视频号学习_8博主_20261103.json").read_text(encoding="utf-8"))
conclusions = json.loads((R / "learning_conclusions_lib_20261103.json").read_text(encoding="utf-8")).get("conclusions") or []
multi_csv = REPO / "deliverables/已治理数据_20261103/04_多源概念_人工签批.csv"
multi_rows = list(csv.DictReader(multi_csv.open(encoding="utf-8-sig"))) if multi_csv.exists() else []

units = []
for pid in ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes"):
    p = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    if not p.exists():
        continue
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        ev = d.get("evidence") or {}
        units.append({"project": pid, "unit_id": d.get("unit_id", ""),
                      "text": str(d.get("claim") or ""), "file": str(ev.get("file") or ""),
                      "line": ev.get("line")})

# ---------------- 7 步清洗 ----------------
PLACEHOLDER = re.compile(r"(（图）|\(图\)|点击|扫码|详见|见.{0,3}图|目录|第.{1,3}讲|未完待续|TODO|xxx|\?\?|…{2,}|\.mp4|https?://)")
BOILER = re.compile(r"(点个小关小助|点赞|关注|转发|收藏|评论区|兄弟们|朋友们|大家好|咱们|我们说|记得|小助|公众号|视频号|下期见|拜拜|谢谢观看|不构成投资建议|风险自负|投资有风险|以上内容|仅供参考)")
MD = re.compile(r"[*`_|#~>\[\]]+")
BULLET = re.compile(r"^[\s\-–—•·●○▪◦*\d.、)）]+")


def norm(t: str) -> str:
    t = MD.sub("", str(t or "")).replace("\u3000", " ")
    return re.sub(r"\s{2,}", " ", t).strip()


def norm_key(t: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", norm(t).lower())


def cjk_ratio(t: str) -> float:
    c = re.findall(r"[\u4e00-\u9fff]", t)
    return round(len(c) / max(1, len(t)), 3)


stats = {"input_units": len(units), "raw_chars": sum(len(u["text"]) for u in units), "removed_file_chars": 0, "placeholder_removed": 0, "too_short": 0,
         "whole_file_removed": 0, "long_split": 0, "boiler_removed": 0,
         "exact_dup": 0, "near_dup": 0, "kept": 0}
clean = []
for u in units:
    t = norm(u["text"])
    t = BULLET.sub("", t)
    if "指标说明书" in u["file"]:      # 规则 2：整文件剔除
        stats["whole_file_removed"] += 1
        stats["removed_file_chars"] += len(u["text"])
        continue
    if PLACEHOLDER.search(t):
        stats["placeholder_removed"] += 1
        continue
    if len(t) < 30:
        stats["too_short"] += 1
        continue
    # 超长切分
    pieces = [t]
    if len(t) > 1500:
        stats["long_split"] += 1
        parts, buf = [], ""
        for seg in re.split(r"(?<=[。；;！？\n])", t):
            if len(buf) + len(seg) > 800 and buf:
                parts.append(buf)
                buf = seg
            else:
                buf += seg
        if buf:
            parts.append(buf)
        pieces = [p for p in parts if len(p) >= 30]
    for piece in pieces:
        # 样板话术句级剔除
        sents = [s for s in re.split(r"(?<=[。；;！？\n])", piece) if s.strip()]
        keep = [s for s in sents if not BOILER.search(s)]
        if len(keep) < len(sents):
            stats["boiler_removed"] += 1
        text = "".join(keep).strip()
        if len(text) < 30:
            stats["too_short"] += 1
            continue
        clean.append({"unit_id": u["unit_id"], "project": u["project"], "source_file": u["file"],
                      "source_line": u["line"], "text": text, "chars": len(text),
                      "cjk_ratio": cjk_ratio(text), "category": "clean_unit"})

# 精确 + 近重复去重（Jaccard≥0.7）
def shingles(t: str) -> set:
    k = norm_key(t)
    return {k[i:i + 2] for i in range(len(k) - 1)}


seen, bucket = {}, defaultdict(list)
dedup = []
for c in clean:
    key = norm_key(c["text"])
    if key in seen:
        stats["exact_dup"] += 1
        continue
    seen[key] = c
    prefix = key[:14]
    dup = False
    for other in bucket[prefix]:
        a, b = shingles(c["text"]), shingles(other["text"])
        if a and b and len(a & b) / len(a | b) >= 0.7:
            dup = True
            break
    if dup:
        stats["near_dup"] += 1
        continue
    bucket[prefix].append(c)
    dedup.append(c)
clean = dedup
stats["kept"] = len(clean)
stats["char_retention"] = round(sum(c["chars"] for c in clean) / max(1, stats["raw_chars"] - stats["removed_file_chars"]), 3)

# ---------------- SFT 构造 ----------------
def q_of(concept: str) -> str:
    return f"什么是「{concept}」？"


sft = []
# A 概念 QA：02 卡 ∪ 05 通过，按 concept 去重
def key(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(s or "").lower())


A = {}
for c in cards:
    k = key(c.get("concept"))
    if not k:
        continue
    A[k] = {"concept": c.get("concept"), "q": c.get("q") or q_of(c.get("concept")),
            "a": c.get("a"), "source": c.get("source"), "evidence": c.get("evidence"),
            "category": "A_concept_qa", "origin": "02_card"}
for c in reviews.get("cards") or []:
    cb = c.get("review", {}).get("checkboxes", {})
    if not cb.get("pass"):
        continue
    k = key(c.get("concept"))
    if not k or k in A:
        continue
    A[k] = {"concept": c.get("concept"), "q": q_of(c.get("concept")), "a": c.get("a"),
            "source": c.get("source"), "evidence": c.get("evidence"),
            "category": "A_concept_qa", "origin": "05_pass"}
for v in A.values():
    sft.append(v)
stats["A"] = len(A)

# B 结论改写
B = 0
for c in conclusions[:100]:
    low = c.get("confidence") == "低"
    suffix = "（注意：该结论为单源/低置信，仅供参考）" if low else ""
    q = f"关于「{c.get('concept')}」，知识库的结论是什么？"
    if c.get("claim_type") == "事实型（研报）":
        q = f"截至 {c.get('as_of') or c.get('date') or '数据截止日'}，「{c.get('concept')}」的事实是什么？"
    sft.append({"concept": c.get("concept"), "q": q, "a": str(c.get("text", "")) + suffix,
                "source": "；".join(str(s.get("source_key")) for s in (c.get("sources") or [])),
                "category": "B_conclusion_rewrite", "origin": "03_conclusion"})
    B += 1
stats["B"] = B

# D 多源交叉卡（有卡片答案的才收）
D = 0
by_concept = {key(c.get("concept")): c for c in cards}
for m in multi_rows:
    members = [x.strip() for x in str(m.get("members", "")).split("/") if x.strip()]
    card = by_concept.get(key(m.get("display_concept")))
    if not card:
        for mb in members:
            card = by_concept.get(key(mb))
            if card:
                break
    if not card:
        continue
    src_note = f"（多源一致：{m.get('独立来源数')} 个独立来源；来源：{str(m.get('source_keys',''))[:80]}）"
    sft.append({"concept": card.get("concept"), "q": f"「{m.get('display_concept')}」在多个来源中如何表述？",
                "a": str(card.get("a") or "") + src_note, "source": str(m.get("source_keys", "")),
                "category": "D_multi_source", "origin": "04_multi_source"})
    D += 1
stats["D"] = D

# E 变体增强（规则模板；SFT 内标记 synthetic_variant）
E = 0
templates = ["用一句话解释「{c}」。", "实战中「{c}」怎么用？", "「{c}」的要点是什么？"]
for item in list(sft):
    if item["category"] not in ("A_concept_qa", "B_conclusion_rewrite"):
        continue
    for tpl in templates:
        sft.append({"concept": item["concept"], "q": tpl.format(c=item["concept"]), "a": item["a"],
                    "source": item.get("source"), "category": "E_variant", "origin": "rule_variant",
                    "variant_of": item.get("concept")})
        E += 1
        if E >= 1400:
            break
    if E >= 1400:
        break
stats["E"] = E
stats["F"] = 0  # 通用回混数据（BELLE/Firefly/COIG）本地缺失

# C 阅读理解 QA（若已由强模型生成并落盘）
C = 0
c_file = OUT / "sft" / "c_qa.jsonl"
if c_file.exists():
    for line in c_file.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        sft.append({"concept": d.get("concept") or d.get("q", "")[:20],
                    "q": d.get("q", ""), "a": d.get("a", ""),
                    "source": d.get("source", ""), "category": "C_reading_comprehension",
                    "origin": "llm_generated_c", "needs_review": True})
        C += 1
stats["C"] = C

# ---------------- held-out 105（概念 55 各取 1 条 + 笔记 50） ----------------
heldout, train_pool = [], []
concepts_sorted = sorted({item["concept"] for item in sft}, key=lambda x: hashlib.md5(str(x).encode()).hexdigest())
hold_concepts = set(concepts_sorted[:55])
seen_hold = set()
for item in sft:
    if item["concept"] in hold_concepts:
        if item["concept"] not in seen_hold:      # 每概念只留 1 条代表（变体同侧剔除）
            seen_hold.add(item["concept"])
            heldout.append(item)
        continue
    train_pool.append(item)
# 笔记 50 条（clean units 中按 hash 选，永不入 SFT，留作 C 类评测）
clean_sorted = sorted(clean, key=lambda x: hashlib.md5(x["unit_id"].encode()).hexdigest())
note_hold = clean_sorted[:50]
for u in note_hold:
    heldout.append({"concept": u["text"][:30], "q": "（held-out 笔记，用于 C 类阅读理解评测）",
                    "a": u["text"], "source": f"{u['source_file']} L{u['source_line']}",
                    "category": "C_heldout_note", "origin": "heldout"})
stats["heldout"] = len(heldout)
# 训练/验证切分 95/5
train_pool.sort(key=lambda x: hashlib.md5((x["category"] + x["q"]).encode()).hexdigest())
val_n = max(1, int(len(train_pool) * 0.05))
val, train = train_pool[:val_n], train_pool[val_n:]

# ---------------- DPO 规则对 ----------------
dpo = []
# ① 需修改自我配对 25（chosen=清洗答案，rejected=注入元信息污染）
modifies = [c for c in reviews.get("cards") or [] if c.get("review", {}).get("checkboxes", {}).get("revise")]
for c in modifies[:25]:
    chosen = str(c.get("a") or "")
    rejected = chosen[:120] + " 点个小关小助，记得点赞关注，我们下期见！"
    dpo.append({"prompt": q_of(c.get("concept")), "chosen": chosen, "rejected": rejected,
                "source": "modify_self_pair", "category": "D1_contamination"})
# ④ 句式鲁棒对 40（rejected=复读/截断）
for item in sft[:40]:
    chosen = str(item["a"])
    rejected = (chosen[:20] + "。") * 3
    dpo.append({"prompt": item["q"], "chosen": chosen, "rejected": rejected,
                "source": "sentence_robustness", "category": "D4_robustness"})
stats["DPO"] = {"total": len(dpo), "self_pair": min(25, len(modifies)), "robustness": 40,
                "rejection_sampling_pending": "800-1200（需 D4 模型采样）"}

# ---------------- 输出 ----------------
for d in (OUT / "clean", OUT / "sft", OUT / "dpo", OUT / "heldout", OUT / "pending"):
    d.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: list):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def to_msg(item: dict) -> dict:
    return {"messages": [
        {"role": "system", "content": "你是零界量化知识助手。只依据知识库回答；给出引用；不确定时说明置信度；不构成投资建议。"},
        {"role": "user", "content": item["q"]},
        {"role": "assistant", "content": item["a"]},
    ], "meta": {"category": item["category"], "concept": item.get("concept"),
                "source": item.get("source"), "origin": item.get("origin"),
                "variant_of": item.get("variant_of")}}


write_jsonl(OUT / "clean" / "clean_units.jsonl", clean)
write_jsonl(OUT / "sft" / "quant_kb_sft_v1_train.jsonl", [to_msg(x) for x in train])
write_jsonl(OUT / "sft" / "quant_kb_sft_v1_val.jsonl", [to_msg(x) for x in val])
write_jsonl(OUT / "heldout" / "quant_kb_heldout_105.jsonl", [to_msg(x) for x in heldout])
write_jsonl(OUT / "dpo" / "quant_kb_dpo_v1.jsonl", dpo)
# C 类待生成 prompts（强模型生成阅读理解 QA）
quota = {"视频号学习_8博主": 450, "视频号学习_第二期": 450, "laomu_notes": 80}
pending = []
for proj, n in quota.items():
    cand = [c for c in clean if c["project"] == proj]
    cand.sort(key=lambda x: hashlib.md5(x["unit_id"].encode()).hexdigest())
    for c in cand[:n]:
        pending.append({"unit_id": c["unit_id"], "text": c["text"], "source_file": c["source_file"],
                        "source_line": c["source_line"], "task": "生成 1 个量化知识要点问答（答案必须来自原文）"})
write_jsonl(OUT / "pending" / "c_prompts_pending.jsonl", pending)
stats["C_pending_prompts"] = len(pending)

# dataset_info.json（LLaMA-Factory）
dataset_info = {
    "quant_kb_sft_v1": {"file_name": "sft/quant_kb_sft_v1_train.jsonl",
                        "formatting": "sharegpt", "columns": {"messages": "messages"}},
    "quant_kb_sft_val": {"file_name": "sft/quant_kb_sft_v1_val.jsonl",
                         "formatting": "sharegpt", "columns": {"messages": "messages"}},
    "quant_kb_dpo_v1": {"file_name": "dpo/quant_kb_dpo_v1.jsonl", "formatting": "sharegpt",
                        "ranking": True, "columns": {"messages": "prompt", "chosen": "chosen", "rejected": "rejected"}},
}
(OUT / "dataset_info.json").write_text(json.dumps(dataset_info, ensure_ascii=False, indent=1), encoding="utf-8")

# 配置（按方案 §7 落 yaml）
cfg = OUT / "configs"
cfg.mkdir(parents=True, exist_ok=True)
(cfg / "sft_14b.yaml").write_text("""### Qwen2.5-14B-Instruct QLoRA SFT（方案 §7.1）
model_name_or_path: <Qwen2.5-14B-Instruct 本地路径>
quantization_bit: 4
stage: sft
finetuning_type: lora
lora_target: all
lora_rank: 32
lora_alpha: 64
lora_dropout: 0.05
dataset: quant_kb_sft_v1
template: qwen
cutoff_len: 2048
train_on_prompt: false
per_device_train_batch_size: 2
gradient_accumulation_steps: 8
learning_rate: 7.0e-5
num_train_epochs: 3.0
lr_scheduler_type: cosine
warmup_steps: 10
bf16: true
gradient_checkpointing: true
flash_attn: fa2
weight_decay: 0.0
max_grad_norm: 1.0
packing: false
val_size: 0.05
eval_strategy: epoch
save_strategy: epoch
save_total_limit: 3
load_best_model_at_end: false
logging_steps: 5
output_dir: saves/qwen25-14b-qlora-sft
seed: 42
""", encoding="utf-8")
(cfg / "dpo_14b.yaml").write_text("""### Qwen2.5-14B-Instruct QLoRA DPO（方案 §7.2，含 Phase4 修订）
model_name_or_path: <Qwen2.5-14B-Instruct 本地路径>
adapter_name_or_path: <SFT 终验收通过的 adapter>   # [必须] ref=SFT 权重
stage: dpo
pref_loss: sigmoid
pref_beta: 0.1
rpo_alpha: null
learning_rate: 5.0e-7
num_train_epochs: 1.0
lr_scheduler_type: cosine
warmup_ratio: 0.1
per_device_train_batch_size: 1
per_device_eval_batch_size: 1
gradient_accumulation_steps: 16
max_length: 1024
max_prompt_length: 512
quantization_bit: 4
lora_rank: 32
lora_alpha: 64
lora_dropout: 0.0
lora_target: all
gradient_checkpointing: true
bf16: true
save_steps: 10
eval_steps: 10
logging_steps: 2
output_dir: saves/qwen25-14b-qlora-dpo
seed: 42
""", encoding="utf-8")

# ---------------- 报告 ----------------
report = {"generated_at": NOW, "plan": "2026-11-03_量化投资知识库垂域模型微调训练方案_v1",
          "clean_stats": stats,
          "sft": {"train": len(train), "val": len(val), "heldout": len(heldout),
                  "total_train_val": len(train) + len(val),
                  "by_category": dict(Counter(x["category"] for x in sft)),
                  "plan_target": "2600-3100（C/E/F 完成后）"},
          "dpo": stats["DPO"],
          "gaps": ["C 阅读理解 QA 待强模型生成（pending 980 prompts）",
                   "F 通用回混数据缺失（BELLE/Firefly/COIG，需 800-1200 条）",
                   "DPO 拒绝采样 800-1200 对需 D4 模型采样",
                   "指标说明书整文件剔除（规则 2）"],
          "files": {"clean": "research/finetune/clean/clean_units.jsonl",
                    "sft_train": "research/finetune/sft/quant_kb_sft_v1_train.jsonl",
                    "sft_val": "research/finetune/sft/quant_kb_sft_v1_val.jsonl",
                    "heldout": "research/finetune/heldout/quant_kb_heldout_105.jsonl",
                    "dpo": "research/finetune/dpo/quant_kb_dpo_v1.jsonl",
                    "c_pending": "research/finetune/pending/c_prompts_pending.jsonl",
                    "dataset_info": "research/finetune/dataset_info.json",
                    "configs": "research/finetune/configs/"}}
(OUT / f"d0_report_{STAMP}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
md = f"""# D0 数据管线报告（{STAMP}）

> 依据：`docs/plans/2026-11-03_量化投资知识库垂域模型微调训练方案_v1.md`
> 生成：{NOW}

## 一、清洗（7 步）
| 项 | 数值 |
|---|---|
| 输入单元 | {stats['input_units']} |
| 指标说明书整文件剔除 | {stats['whole_file_removed']} |
| 占位符剔除 | {stats['placeholder_removed']} |
| 过短剔除 | {stats['too_short']} |
| 超长切分 | {stats['long_split']} |
| 样板话术句剔除 | {stats['boiler_removed']} |
| 精确重复 | {stats['exact_dup']} |
| 近重复（Jaccard≥0.7） | {stats['near_dup']} |
| **保留干净单元** | **{stats['kept']}** |
| 字符留存 | {stats['char_retention']} |

## 二、SFT 构造
| 类 | 条数 | 计划 |
|---|---|---|
| A 概念 QA（02∪05通过去重） | {stats['A']} | 541 |
| B 结论改写（03） | {stats['B']} | 100 |
| D 多源交叉卡（04） | {stats['D']} | 27 |
| E 变体增强（规则） | {stats['E']} | 1000-1400 |
| C 阅读理解 | {stats['C']} | ~950-1050 |
| F 通用回混 | **0（缺数据）** | 800-1200 |
| 训练/验证 | {len(train)} / {len(val)} | — |
| Held-out | {len(heldout)}（概念 55 + 笔记 50） | 105 |

## 三、DPO 构造
| 类 | 对数 | 计划 |
|---|---|---|
| 需修改自我配对 | {stats['DPO']['self_pair']} | 25 |
| 句式鲁棒对 | {stats['DPO']['robustness']} | 30-50 |
| 拒绝采样 | **0（待 D4 模型）** | 800-1200 |

## 四、缺口与下一步
1. **C 类**：{stats['C_pending_prompts']} 条 prompts 已备好（`pending/c_prompts_pending.jsonl`），待强模型（云端/本地）生成 + 人工抽检 10%；
2. **F 类**：需引入 BELLE/Firefly/COIG 800-1200 条通用数据；
3. **DPO 主体**：待 D4 拒绝采样（需 SFT 模型）；
4. 数据配比/同源上限/退火权重在 C/F 完成后统一调整。

## 五、产物
- `research/finetune/clean/clean_units.jsonl`
- `research/finetune/sft/quant_kb_sft_v1_{{train,val}}.jsonl`
- `research/finetune/heldout/quant_kb_heldout_105.jsonl`
- `research/finetune/dpo/quant_kb_dpo_v1.jsonl`
- `research/finetune/dataset_info.json`、`research/finetune/configs/{{sft_14b,dpo_14b}}.yaml`
"""
(OUT / f"d0_report_{STAMP}.md").write_text(md, encoding="utf-8")
print(json.dumps({"clean": stats["kept"], "A": stats["A"], "B": stats["B"], "D": stats["D"],
                  "E": stats["E"], "train": len(train), "val": len(val),
                  "heldout": len(heldout), "dpo": len(dpo), "C_pending": len(pending)},
                 ensure_ascii=False))
