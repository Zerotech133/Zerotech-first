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
新闻/快讯（单条 [时间][来源] 标题 或多条合辑）→ doc_type=新闻，tags 概括该条主题（合辑则汇总全批热词）。
用词规范：同一主题只用规范词——算力(不用AI算力/智算)、人工智能(不用AI)、机器人(不用人形/工业机器人)、新能源(不用清洁能源)、涨停(不用触及涨停/涨停潮)、燃料油(不用低硫燃料油)。"""

# K266 热词规范词表（门户 /kg/doc 展示端 import 同一份，改这里两端生效）：
# 只并真同义/明确变体，不并"股价下跌"类主题+方向复合词（防方向词虚胖）。
# 2026-10-08 按全量影子数据（2942 词/4767 计数）核定；新变体按数据补录。
TAG_ALIAS = {
    "AI算力": "算力", "智算": "算力", "AI": "人工智能",
    "人形机器人": "机器人", "工业机器人": "机器人",
    "清洁能源": "新能源", "触及涨停": "涨停", "涨停潮": "涨停",
    "低硫燃料油": "燃料油",
}


def norm_tags(tags):
    """tags 同义归一：变体并到规范词，去空串，归一后去重防同文档重复计数。"""
    out, seen = [], set()
    for t in tags:
        t = TAG_ALIAS.get(t, t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def load_done():
    done = set()
    err_cnt = {}
    if os.path.exists(SHADOW):
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("raw_ok"):  # ERR 记录不算已判，下轮自动重试
                        done.add(rec["doc_id"])
                    else:
                        err_cnt[rec["doc_id"]] = err_cnt.get(rec["doc_id"], 0) + 1
                except Exception:
                    pass
    # 钉子户防御：ERR≥3（如超长条目独占 GPU 也判不完）视为放弃，防每轮
    # 空转 600s 看门狗超时（2026-10-08 i11 三连败实测）
    for did, n in err_cnt.items():
        if n >= 3:
            done.add(did)
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


def ask_ollama(text, timeout=280, hard_deadline=600):
    """三试重试 + 硬超时看门狗。

    requests 的 timeout 语义：stream=False 时是"整个生成完成"的时限（首字节=
    完整响应）。GPU 被抢占/长文档下生成可超时（2026-10-08 实测 17:03 起挂死
    18h）。故整条调用放工作线程跑，join 超过 hard_deadline 即放弃 → judge 落
    ERR 下一条；abort 事件通知 worker 在重试检查点自行退出，防僵尸请求继续
    占 Ollama 推理槽挤压后续条目（并发 16k ctx 会把层挤到 CPU，全体变慢）。
    """
    import threading
    box = {}
    abort = threading.Event()

    def _worker():
        body = {"model": MODEL, "think": False, "stream": False,
                # num_ctx 8192：16384 的 KV cache 挤显存会把权重层压到 CPU
                # （ollama ps 显示 12%/88%），5000 字长文单条 >600s 判不完；
                # 配 2500 字截断（tags/doc_type 靠标题导语，正文细节非关键）
                "options": {"num_ctx": 8192},
                "messages": [{"role": "user",
                              "content": PROMPT + "\n\n=== 文档文本（截断）===\n" + text[:2500]}]}
        for attempt in (1, 2, 3):   # 并发抢占下偶发空输出/无 JSON，三试
            if abort.is_set():
                return                  # 看门狗已放弃，勿再发起新请求
            try:
                r = requests.post(OLLAMA, json=body, timeout=timeout)
                r.raise_for_status()
                box["last"] = r.json()["message"]["content"]
                if "{" in box["last"]:
                    return
            except Exception as e:
                box["last"] = str(e)[:120]
            if abort.is_set():
                return
            time.sleep(2)
        box.setdefault("last", "")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(hard_deadline)
    if t.is_alive():
        abort.set()     # 当前阻塞中的请求 ≤timeout 内自行退出，不再 3 连击
        raise TimeoutError("ollama hard timeout %ss（慢流/队列饿死）" % hard_deadline)
    return box.get("last", "")


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
    # Ollama 错峰锁：与 news 域 JEV 回填互斥（wait_s 覆盖其 1800s deadline 上限；
    # 正常退出/异常走 atexit 释放，强杀靠锁模块 20 分钟陈旧回收）
    _root = os.path.dirname(BASE)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import atexit
    import ollama_lock
    atexit.register(ollama_lock.release)
    if not ollama_lock.acquire("k266_m3", wait_s=1900):
        print("ollama busy: JEV 回填持锁超 1900s，本轮让位（调度 5 分钟自愈）")
        return
    done = load_done()
    n_ok = n_err = 0

    def judge(did, name, rec, text):
        """判一篇（整文档或单条新闻）并落影子。"""
        nonlocal n_ok, n_err
        try:
            parsed = parse_json(ask_ollama(text))
            parsed["tags"] = norm_tags(parsed.get("tags", []))  # 源头归一，展示端兜底
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
        ollama_lock.heartbeat()

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
