# -*- coding: utf-8 -*-
"""K266 M3 文档结构化影子管线：doc_texts.jsonl → qwen3.5:9b → structure_shadow.jsonl
影子纪律：只落影子不挂图/不进生产，人审抽样后再定入图口径。
新闻批次（src=news，B案）：按 [时间][来源] 标题 头切单条逐判，doc_id=批次id+i序号。
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
 "doc_type": "新闻|投关记录|董事会决议|股权激励|收购摘要|业绩公告|研报|其他",
 "title_guess": "文档标题或主题，≤30字",
 "companies": ["提及的上市公司简称或全称，最多8个"],
 "events": [{"subject":"主体方","rel":"供应|客户|合作|投资|收购|减持|增持|担保|中标|投产|业绩|其他","object":"客体方或产品","date":"YYYY-MM-DD或空","amount":"金额/数量原文或空"}],
 "direction": "利好|利空|中性",
 "direction_why": "判断理由，≤40字",
 "key_numbers": [{"value":"原文数字","context":"数字含义，≤10字"}],
 "tags": ["3-8个主题词，两到四字，用于词云，如:算力,液冷,海外扩产"]
}
规则：只依据文本，不推测；events 最多 6 条；只输出 JSON，不要其他文字。
新闻/快讯（单条 [时间][来源] 标题 或多条合辑）→ doc_type=新闻，tags 概括该条主题（合辑则汇总全批热词）。"""


def load_done():
    done = set()
    if os.path.exists(SHADOW):
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("raw_ok"):  # ERR 记录不算已判，下轮自动重试
                        done.add(rec["doc_id"])
                except Exception:
                    pass
    return done


NEWS_HEAD = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\[[^\]]+\] .+")


def split_news_items(text):
    """新闻批次文本 → 单条列表（按 [时间][来源] 标题 头切分，正文跟随其行）。"""
    items, cur = [], None
    for line in text.splitlines():
        if NEWS_HEAD.match(line):
            if cur:
                items.append("\n".join(cur))
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        items.append("\n".join(cur))
    return [t for t in (s.strip() for s in items) if t]


def ask_ollama(text, timeout=180):
    body = {"model": MODEL, "think": False, "stream": False,
            "options": {"num_ctx": 16384},   # 默认 4096 会被 6000 字 prompt 挤满, 输出撞 length 墙
            "messages": [{"role": "user",
                          "content": PROMPT + "\n\n=== 文档文本（截断）===\n" + text[:6000]}]}
    last = ""
    for attempt in (1, 2, 3):   # 并发抢占下偶发空输出/无 JSON，三试
        r = requests.post(OLLAMA, json=body, timeout=timeout)
        r.raise_for_status()
        last = r.json()["message"]["content"]
        if "{" in last:
            return last
        time.sleep(2)
    return last


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

    def judge(did, name, rec, text):
        """判一篇（整文档或单条新闻）并落影子。"""
        nonlocal n_ok, n_err
        try:
            parsed = parse_json(ask_ollama(text))
            out = {"doc_id": did, "name": name, "src": rec.get("src"),
                   "kind": rec.get("kind"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "pv": 1, "raw_ok": True, "chars": len(text), **parsed}
            flag = ""
        except Exception as e:
            out = {"doc_id": did, "name": name, "src": rec.get("src"),
                   "kind": rec.get("kind"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "pv": 1, "raw_ok": False, "chars": len(text), "err": f"{type(e).__name__}: {str(e)[:120]}"}
            parsed, flag = {}, " ERR"
        with open(SHADOW, "a", encoding="utf-8") as fo:
            fo.write(json.dumps(out, ensure_ascii=False) + "\n")
        if out["raw_ok"]:
            n_ok += 1
        else:
            n_err += 1
        p = out if out["raw_ok"] else {}
        print(f"[{n_ok + n_err}] {did} {str(name)[:28]}{flag} "
              f"{p.get('doc_type', '')} {p.get('direction', '')} "
              f"tags={','.join(p.get('tags', [])[:5])}", flush=True)

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
            # 新闻批次（B案）：切单条逐判，doc_id=批次id+i序号；批次级不落影子（防 tags 双计）
            if rec.get("src") == "news" or str(rec.get("name", "")).startswith("news_"):
                for idx, item in enumerate(split_news_items(rec.get("text", "")), 1):
                    if n_ok + n_err >= limit:
                        break
                    did_i = f"{did}i{idx:02d}"
                    if did_i in done:
                        continue
                    judge(did_i, "%s#%02d" % (rec.get("name", ""), idx), rec, item)
                continue
            judge(did, rec.get("name"), rec, rec.get("text", ""))
    print(f"done ok={n_ok} err={n_err} shadow={SHADOW}")


if __name__ == "__main__":
    main()
