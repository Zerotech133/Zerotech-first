# -*- coding: utf-8 -*-
"""改写批1（hype 去夸大）+ 批2（time_sensitive 时效标注），2026-11-08

产物分桶：
- rewritten      ：有夸大词且非否定语境 → claim_rewrite 已生成
- teaching_ctx   ：夸大词全在否定/对冲语境（未必/以为/概率…）→ 无需改写
- manual_review  ：含翻倍等事实性用法难自动判定 → 留人工

批2：claim_rewrite = 【时效档案…】前缀 + 原文（原文不动）。
所有产物只写 claim_rewrite 字段，claim 原文永不修改。
"""
import json
import re
from collections import Counter
from pathlib import Path

DRAFT = r"C:/项目/知识库/30_知识单元/视频号学习_全部文案_20261108_units_draft.jsonl"
QUEUE = r"C:/项目/_tmp_ci9/revise_rewrite_queue.jsonl"
OUT = r"C:/项目/_tmp_ci9/rewrite_batch12_proposals.jsonl"

# 自动替换表（带否定守卫）
AUTO = [
    (re.compile(r"稳赚不赔"), "盈利可能性较高但无保证"),
    (re.compile(r"稳赚"), "有较大概率盈利"),
    (re.compile(r"包赚"), "有盈利机会"),
    (re.compile(r"必涨"), "看涨"),
    (re.compile(r"百分百"), "大概率"),
    (re.compile(r"绝对盈利"), "有盈利可能"),
    (re.compile(r"涨停敢死队"), "涨停板短线激进打法"),
    (re.compile(r"暴涨"), "大幅上涨"),
]
# 否定/教学语境守卫：命中词前 8 字窗内出现这些 → 不改写
NEG_GUARD = re.compile(r"(未必|不一定|并不|并非|不是|别信|不要|勿|以为|如果|假如|要是|谁说|幻想|梦想|教你|告诉你|没有|谈不上|毫无|绝非|动不动说|张口|谎言|谣言|误区|真相是|是不是|哪有|哪能|怎么可能|难道|岂能)[^，。]{0,8}$")
# 对冲语境：命中词后紧跟概率类词 → 已有对冲，不改写
HEDGE_GUARD = re.compile(r"^(的概率|可能性|几率|的概率大)")
# 引述守卫：命中词处于引述用法（说的是/用的是X这个词）→ 不改写
QUOTE_GUARD = re.compile(r"(用|说|叫|喊|写|打)[^，。]{0,3}$")

# 事实性用法难判定词 → manual
MANUAL_WORDS = re.compile(r"翻倍")

claims = {}
for l in open(DRAFT, encoding="utf-8"):
    r = json.loads(l)
    claims[r["unit_id"]] = r.get("claim") or ""

queue = [json.loads(l) for l in open(QUEUE, encoding="utf-8") if l.strip()]
b12 = [r for r in queue if r["batch"] in (1, 2)]

buckets = Counter()
out = []
for r in b12:
    uid, batch = r["unit_id"], r["batch"]
    claim = claims.get(uid, "").strip()
    if not claim:
        buckets["no_claim"] += 1
        continue
    if batch == 2:
        rw = f"【时效档案·抓取于2026-11-08】{claim}"
        out.append({"unit_id": uid, "batch": 2, "bucket": "annotated",
                    "claim_rewrite": rw, "hits": []})
        buckets["annotated_ts"] += 1
        continue

    # batch 1: hype
    hits = []
    rewritten = claim
    all_guarded = True
    manual_flag = False
    for pat, rep in AUTO:
        for m in pat.finditer(claim):
            pre = claim[max(0, m.start() - 8):m.start()]
            post = claim[m.end():m.end() + 4]
            if NEG_GUARD.search(pre) or HEDGE_GUARD.match(post) or QUOTE_GUARD.search(pre):
                continue  # 语境守卫：教学/对冲用法，保留
            hits.append(m.group(0))
            all_guarded = False
    if MANUAL_WORDS.search(claim) and len(re.findall(r"[^涨月年]翻倍|能翻倍|要翻倍", claim)):
        manual_flag = True
    if all_guarded and not hits:
        buckets["teaching_ctx"] += 1
        out.append({"unit_id": uid, "batch": 1, "bucket": "teaching_ctx",
                    "claim_rewrite": None, "hits": []})
    elif manual_flag:
        buckets["manual_review"] += 1
        out.append({"unit_id": uid, "batch": 1, "bucket": "manual_review",
                    "claim_rewrite": None, "hits": sorted(set(hits))})
    else:
        for pat, rep in AUTO:
            def _sub(m, rep=rep):
                pre = claim  # rewritten 上逐个替换，守卫用原文位置近似（替换不改长度差大，简单重跑守卫）
                pre2 = rewritten[max(0, m.start() - 8):m.start()]
                if NEG_GUARD.search(pre2) or HEDGE_GUARD.match(rewritten[m.end():m.end() + 4]) or QUOTE_GUARD.search(pre2):
                    return m.group(0)
                return rep
            rewritten = pat.sub(_sub, rewritten)
        buckets["rewritten"] += 1
        out.append({"unit_id": uid, "batch": 1, "bucket": "rewritten",
                    "claim_rewrite": rewritten, "hits": sorted(set(hits))})

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    for o in out:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
print("批1+2 总数:", len(b12), dict(buckets))
