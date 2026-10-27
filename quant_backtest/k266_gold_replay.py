# -*- coding: utf-8 -*-
"""K266 金标集首用：v2 提示词重放 30 篇 → 对照金标基线（96.7%）。
v2 口径全部锚定 2026-10-08 用户终审定稿：
  - 监测/月报类纯数据披露判中性（#29 判例）
  - 厂商推介/白皮书偏正面（#25 口径）
  - 国资收购强化控制权偏正面（#11 口径）
  - direction_why 只引本文内容（#01 影子混入周报标题瑕疵）
独立输出 gold_replay_v2.jsonl，不触碰生产 structure_shadow.jsonl。
用法: python k266_gold_replay.py"""

import json
import os
import re
import time
import requests
import logging

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "data", "k266_docs")
GOLD = os.path.join(DOCS, "gold_labels.jsonl")
TEXTS = os.path.join(DOCS, "doc_texts.jsonl")
OUT = os.path.join(DOCS, "gold_replay_v2.jsonl")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"

PROMPT_V2 = """你是金融文档结构化引擎。输入一篇文档文本（公告/投关记录/研报/决议等），输出严格 JSON：
{
 "doc_type": "投关记录|董事会决议|股权激励|收购摘要|业绩公告|研报|监测报告|其他",
 "title_guess": "文档标题或主题，≤30字",
 "companies": ["提及的上市公司简称或全称，最多8个"],
 "events": [{"subject":"主体方","rel":"供应|客户|合作|投资|收购|减持|增持|担保|中标|投产|业绩|其他","object":"客体方或产品","date":"YYYY-MM-DD或空","amount":"金额/数量原文或空"}],
 "direction": "利好|利空|中性",
 "direction_why": "判断理由，≤40字",
 "key_numbers": [{"value":"原文数字","context":"数字含义，≤10字"}],
 "tags": ["3-8个主题词，两到四字，用于词云，如:算力,液冷,海外扩产"]
}
方向判定口径（金标校准 v2）：
1. 政府或高校的统计监测/月报/季报，纯数据披露、无评级动作、无公司标的动作的，一律判「中性」——数据增长本身是事实描述，不是事件驱动。
2. 厂商/公司发布的白皮书、产品推介，宣传自身技术布局的，偏正面判「利好」。
3. 国资或产业资本收购、增持强化控制权且价格公允的，判「利好」。
4. direction_why 只依据本文内容概括，不要引用来源刊物、栏目或周报标题。
规则：只依据文本，不推测；events 最多 6 条；只输出 JSON，不要其他文字。"""


def ask_ollama(text, timeout=180):
    body = {
        "model": MODEL,
        "think": False,
        "stream": False,
        "options": {"num_ctx": 16384},
        "messages": [
            {
                "role": "user",
                "content": PROMPT_V2 + "\n\n=== 文档文本（截断）===\n" + text[:6000],
            }
        ],
    }
    last = ""
    for attempt in (1, 2, 3):
        r = requests.post(OLLAMA, json=body, timeout=timeout)
        r.raise_for_status()
        last = r.json()["message"]["content"]
        if "{" in last:
            return last
        time.sleep(2)
    return last


def parse_json(raw):
    t = re.sub(r"```(?:json)?|```", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError("no json object")
    obj = json.loads(m.group(0))
    for k in ("companies", "events", "key_numbers", "tags"):
        if not isinstance(obj.get(k), list):
            obj[k] = []
    obj.setdefault("doc_type", "其他")
    obj.setdefault("direction", "中性")
    return obj


def main():
    gold = [json.loads(l) for l in open(GOLD, encoding="utf-8")]
    gmap = {g["doc_id"]: g for g in gold}
    texts = {}
    with open(TEXTS, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("doc_id") in gmap and rec.get("status") == "ok":
                texts[rec["doc_id"]] = rec.get("text", "")
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(line)["doc_id"])
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/k266_gold_replay.py", exc)
    n_ok = n_err = 0
    results = {}
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("raw_ok"):
                    results[r["doc_id"]] = r
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/k266_gold_replay.py", exc)
    for did, g in gmap.items():
        if did in done:
            continue
        if did not in texts:
            print(f"SKIP {did} (no text)", flush=True)
            continue
        try:
            parsed = parse_json(ask_ollama(texts[did]))
            out = {
                "doc_id": did,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pv": 2,
                "raw_ok": True,
                **parsed,
            }
        except Exception as e:
            out = {
                "doc_id": did,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pv": 2,
                "raw_ok": False,
                "err": f"{type(e).__name__}: {str(e)[:120]}",
            }
        with open(OUT, "a", encoding="utf-8") as fo:
            fo.write(json.dumps(out, ensure_ascii=False) + "\n")
        if out["raw_ok"]:
            results[did] = out
            n_ok += 1
            print(
                f"[{len(results)}/{len(gmap)}] {did[:12]} {out['doc_type']} "
                f"{out['direction']} gold={g['gold_direction']}",
                flush=True,
            )
        else:
            n_err += 1
            print(f"[ERR] {did[:12]} {out['err']}", flush=True)
    # ---- 对照报告 ----
    print("\n===== 对照报告 v2 vs 金标 =====")
    agree = diverge = 0
    flips = []
    for g in gold:
        did = g["doc_id"]
        r = results.get(did)
        if not r or not r.get("raw_ok"):
            continue
        v2, gold_d, v1 = (
            r["direction"],
            g["gold_direction"],
            g.get("shadow_direction", ""),
        )
        if v2 == gold_d:
            agree += 1
        else:
            diverge += 1
        if v2 != v1:
            flips.append((did, v1, v2, gold_d))
    tot = agree + diverge
    if tot:
        print(
            f"v2 vs 金标: {agree}/{tot} = {agree / tot:.1%}（基线 v1 = 96.7%，门闸 85%）"
        )
    print(f"v2 vs v1 翻转 {len(flips)} 篇：")
    for did, v1, v2, gd in flips:
        mark = "→对齐金标" if v2 == gd else ("→偏离金标!" if v1 == gd else "")
        g = gmap[did]
        print(
            f"  {did[:12]} v1={v1} v2={v2} gold={gd} {mark} | {g.get('title_guess', '')[:24]}"
        )
        print(f"    v2 why: {results[did].get('direction_why', '')}")


if __name__ == "__main__":
    main()
