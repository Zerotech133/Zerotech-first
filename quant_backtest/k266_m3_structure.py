# -*- coding: utf-8 -*-
"""K266 M3 文档结构化影子管线：doc_texts.jsonl → qwen3.5:9b → structure_shadow.jsonl
影子纪律：只落影子不挂图/不进生产，人审抽样后再定入图口径。
用法: python k266_m3_structure.py [--limit N]（默认全量，幂等跳过已判 doc_id）"""
import json, os, re, sys, time
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "data", "k266_docs")
TEXTS = os.path.join(DOCS, "doc_texts.jsonl")
SHADOW = os.path.join(DOCS, "structure_shadow.jsonl")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"

PROMPT = """你是金融文档结构化引擎。输入一篇文档文本（公告/投关记录/研报/决议等），输出严格 JSON：
{
 "doc_type": "投关记录|董事会决议|股权激励|收购摘要|业绩公告|研报|其他",
 "title_guess": "文档标题或主题，≤30字",
 "companies": ["提及的上市公司简称或全称，最多8个"],
 "events": [{"subject":"主体方","rel":"供应|客户|合作|投资|收购|减持|增持|担保|中标|投产|业绩|其他","object":"客体方或产品","date":"YYYY-MM-DD或空","amount":"金额/数量原文或空"}],
 "direction": "利好|利空|中性",
 "direction_why": "判断理由，≤40字",
 "key_numbers": [{"value":"原文数字","context":"数字含义，≤10字"}],
 "tags": ["3-8个主题词，两到四字，用于词云，如:算力,液冷,海外扩产"]
}
规则：只依据文本，不推测；events 最多 6 条；只输出 JSON，不要其他文字。"""


def load_done():
    done = set()
    if os.path.exists(SHADOW):
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["doc_id"])
                except Exception:
                    pass
    return done


def ask_ollama(text, timeout=180):
    body = {"model": MODEL, "think": False, "stream": False,
            "messages": [{"role": "user",
                          "content": PROMPT + "\n\n=== 文档文本（截断）===\n" + text[:6000]}]}
    r = requests.post(OLLAMA, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


def parse_json(raw):
    """容错：剥 ```json 围栏，取最外层 {}"""
    t = re.sub(r"```(?:json)?|```", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError("no json object")
    obj = json.loads(m.group(0))
    # 规范化关键字段
    for k, dflt in (("companies", []), ("events", []), ("key_numbers", []),
                    ("tags", [])):
        if not isinstance(obj.get(k), list):
            obj[k] = dflt
    obj.setdefault("doc_type", "其他")
    obj.setdefault("direction", "中性")
    return obj


def main():
    limit = 10 ** 9
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    done = load_done()
    n_ok = n_err = 0
    with open(TEXTS, encoding="utf-8") as f:
        for line in f:
            if n_ok + n_err >= limit:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            did = rec.get("doc_id")
            if not did or did in done or rec.get("status") != "ok":
                continue
            try:
                raw = ask_ollama(rec.get("text", ""))
                parsed = parse_json(raw)
                out = {"doc_id": did, "name": rec.get("name"), "src": rec.get("src"),
                       "kind": rec.get("kind"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "pv": 1, "raw_ok": True, **parsed}
                flag = ""
            except Exception as e:
                out = {"doc_id": did, "name": rec.get("name"), "src": rec.get("src"),
                       "kind": rec.get("kind"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "pv": 1, "raw_ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}
                parsed, flag = {}, " ERR"
            with open(SHADOW, "a", encoding="utf-8") as fo:
                fo.write(json.dumps(out, ensure_ascii=False) + "\n")
            if out["raw_ok"]:
                n_ok += 1
            else:
                n_err += 1
            p = out if out["raw_ok"] else {}
            print(f"[{n_ok + n_err}] {did} {rec.get('name', '')[:28]}{flag} "
                  f"{p.get('doc_type', '')} {p.get('direction', '')} "
                  f"tags={','.join(p.get('tags', [])[:5])}", flush=True)
    print(f"done ok={n_ok} err={n_err} shadow={SHADOW}")


if __name__ == "__main__":
    main()
