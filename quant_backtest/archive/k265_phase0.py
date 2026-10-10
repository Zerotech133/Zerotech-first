# -*- coding: utf-8 -*-
"""K265 Phase 0：notice_llm 全量标题扫描 → qwen 抽取供应链五元组 → kg_link 归一 → 影子清单。

证据级别：title-only（notice_llm.text 为公告标题）。
输出：data/semantica/k265_phase0_shadow.jsonl（影子模式，不入图）。
"""
import json
import re
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, r"e:\老田的AI助手\quant_backtest")
from kg_link import OLLAMA, name_dictionary  # noqa: E402

OUT = r"e:\老田的AI助手\quant_backtest\data\semantica\k265_phase0_shadow.jsonl"
CAP = 200          # 今晚 LLM 抽取上限
CONC = 2           # 预留
MODEL = "qwen3.5:9b"   # 实测：9b 6.5s 含加载/JSON 干净；35b 22GB 纯 CPU 64s 不可用；4b 返回空

# 标题级供应关系候选模式
PATS = re.compile(
    r"中标|签订.{0,12}(合同|协议|订单)|框架采购|采购合同|供货|供应|"
    r"销售合同|重大合同|经营合同|项目定点|框架协议|买入.{0,6}合同"
)

PROMPT_SYS = "你是A股公告供应链关系抽取器。只输出JSON，不要解释。"
PROMPT_TMPL = """公告标题：{title}
上市公司主体（冒号"："前的公司）通常是信息披露方。
判断标题是否表明供应/采购/中标关系。输出JSON：
{{"ok": true, "supplier": "供应方公司名或null", "customer": "采购方/客户公司名或null",
  "product": "标的物简述", "rel": "SUPPLIES|WIN_BID|FRAMEWORK",
  "conf": 0.0, "evidence": "标题中的依据短语"}}
规则：中标类 supplier=中标方/披露方；无明确对手方可填 null；
rel 取值：SUPPLIES=直接供货/销售合同, WIN_BID=中标/项目定点, FRAMEWORK=框架采购/合作协议。
以下不属于供应关系，必须输出 ok:false：金融服务/资金管理协议、投资建设/投资协议/对外投资、
租赁合同、募集资金监管协议、担保、单纯的战略合作框架（无具体货物或服务供应标的）。
若标题无明确供应关系：{{"ok": false, "reason": "简述原因"}}"""


def _sanitize(s):
    """qwen3.5 思考型模型：<think> 块 + markdown 围栏剥离后提取首个 JSON 对象。"""
    s = re.sub(r"<think>.*?</think>", "", s or "", flags=re.S)
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


def llm_extract(title):
    try:
        r = requests.post(
            OLLAMA,
            json={"model": MODEL, "format": "json", "stream": False,
                  "think": False,  # qwen3.5 思考型：不关思考会耗尽 num_predict 致 content 空
                  "options": {"temperature": 0.1, "num_predict": 400},
                  "messages": [
                      {"role": "system", "content": PROMPT_SYS},
                      {"role": "user", "content": PROMPT_TMPL.format(title=title)},
                  ]},
            timeout=180,
        )
        r.raise_for_status()
        return _sanitize(r.json()["message"]["content"])
    except Exception as e:
        return {"ok": False, "reason": f"llm_error:{type(e).__name__}"}


def _norm_company(s):
    """公司名归一：去空格 + 剥常见后缀（归一口径）。"""
    s = re.sub(r"\s+", "", str(s or ""))
    for suf in ("股份有限公司", "集团有限公司", "有限责任公司", "集团有限公司",
                "股份公司", "有限公司", "集团公司", "集团", "股份", "公司"):
        if s.endswith(suf) and len(s) - len(suf) >= 2:
            s = s[: -len(suf)]
            break
    return s


def resolve(name, dictionary):
    """公司名 → 图谱实体。精确名优先 + 后缀归一匹配（防子串错配：
    江西铜业集团有限公司 不得匹配 上市公司「江西铜业」以外的任何子串命中）。"""
    if not name:
        return None
    n = str(name).strip()
    nrm = _norm_company(n)
    # pass1 精确全名；pass2 后缀归一相等
    for d in dictionary:
        if d["label"] not in ("Company", "Security"):
            continue
        if d["name"] == n:
            return {"id": d["id"], "label": d["label"], "name": d["name"]}
    for d in dictionary:
        if d["label"] not in ("Company", "Security"):
            continue
        if _norm_company(d["name"]) == nrm and nrm:
            return {"id": d["id"], "label": d["label"], "name": d["name"]}
    return None


def main():
    df = pd.read_parquet(r"e:\老田的AI助手\quant_backtest\data\semantica\notice_llm.parquet")
    df = df.dropna(subset=["text"])
    cand = df[df["text"].astype(str).str.contains(PATS, na=False)].copy()
    cand = cand.drop_duplicates(subset=["text"])
    # tag 优先级：合同/中标/协议在前
    prio = cand["tag"].astype(str).str.contains("合同|中标|协议")
    cand = pd.concat([cand[prio], cand[~prio]])
    cand = cand.head(CAP)
    print(f"[1] candidates: {len(cand)} (of {len(df)} rows)")

    print("[2] building kg dictionary ...")
    dictionary = name_dictionary()

    out, ok_cnt = [], 0
    t0 = time.time()
    fsh = open(OUT, "w", encoding="utf-8")
    for i, r in enumerate(cand.itertuples(), 1):
        title = str(r.text)
        ext = llm_extract(title)
        rec = {"ts": str(r.ts), "tag": str(r.tag), "title": title,
               "ext": ext}
        if ext.get("ok"):
            ok_cnt += 1
            rec["supplier_node"] = resolve(ext.get("supplier"), dictionary)
            rec["customer_node"] = resolve(ext.get("customer"), dictionary)
            # 归一后双端同节点 = 对手方与披露方同名歧义（如集团 vs 上市公司），不算有效边
            if (rec["supplier_node"] and rec["customer_node"]
                    and rec["supplier_node"]["id"] == rec["customer_node"]["id"]):
                rec["ambiguous"] = True
        out.append(rec)
        fsh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fsh.flush()
        if i % 10 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(cand)} ok={ok_cnt} elapsed={el:.0f}s avg={el/i:.1f}s/条", flush=True)
    fsh.close()
    print(f"[3] LLM done: {len(out)} processed, ok={ok_cnt} "
          f"({time.time()-t0:.0f}s total)", flush=True)

    print(f"[4] shadow written: {OUT}", flush=True)

    # 影子清单摘要：ok 且至少一端归一成功
    usable = [r for r in out if r["ext"].get("ok")
              and (r.get("supplier_node") or r.get("customer_node"))]
    both = [r for r in usable if r.get("supplier_node") and r.get("customer_node")]
    print(f"[5] ok={ok_cnt} usable(≥1端归一)={len(usable)} 双端归一={len(both)}")
    print("--- 双端归一样本 ---")
    for r in both[:15]:
        s = r["supplier_node"]["name"] if r.get("supplier_node") else "?"
        c = r["customer_node"]["name"] if r.get("customer_node") else "?"
        print(f"  {s} -[{r['ext'].get('rel')},{r['ext'].get('conf')}]-> {c}"
              f" | {r['ext'].get('product','')} | {r['title'][:40]}")


if __name__ == "__main__":
    main()
