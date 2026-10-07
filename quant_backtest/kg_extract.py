# -*- coding: utf-8 -*-
"""K211 · C 阶段延伸后半：研报闭集抽取扩链管线（影子）。

回答「链骨架有了，怎么自动长大」——从研报事件流（report_events.jsonl，
report_ai.py 维护）抽取「标的→环节」供应关系候选，扩链 SUPPLIES_TO。

闭集纪律（降幻觉三板斧）：
· 标的侧不抽——研报事件自带 stocks（上游 report_ai 已实体链接），零幻觉；
· 环节侧受控——prompt 内嵌图内 Segment 全集（按链分组），输出必须是
  枚举原文或 NEW（新环节候选：短语 ≤12 字无标点，只进影子待人工审核）；
· 关系侧枚举——type ∈ {供应链, 配套预期, 物理约束}（与 CONDUCTION
  词表同源）+ evidence 原文短引用（可回查）。
输出 JSON-only（temperature 0.1 / num_predict 限长），解析失败重试一次，
连续 FAIL_STREAK_MAX 熔断（kg_notice_llm 同款范式）。

影子纪律：结果只落 data/kg_extract_shadow.jsonl（append，按 id 幂等），
不入图不推送——新环节候选与挂链候选经人工审核后由 kg_chain 种子收编
（编辑 kg_chain_seeds.json 重跑 build 即 upsert 入图）。

用法：
  python kg_extract.py run            # 增量抽取（幂等，跳过已判 id）
  python kg_extract.py stats          # 影子候选统计
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import requests                                       # noqa: E402
from kg_client import KgClient                        # noqa: E402

REPORTS = os.path.join(HERE, "data", "semantica", "report_events.jsonl")
OUT = os.path.join(HERE, "data", "kg_extract_shadow.jsonl")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
FAIL_STREAK_MAX = 10
TYPE_ENUM = ("供应链", "配套预期", "物理约束")

PROMPT = (
    "你是产业链关系抽取器。研报讲述下列标的的业务，请判断该标的与哪些"
    "产业环节有关系。\n"
    "环节枚举（links[].seg 的值必须是枚举中的中文环节名原文，可多选或 "
    "NEW；「＝」后为环节定义与边界条件）：\n%s\n"
    "type 枚举（links[].type 的值必须是其中之一）：供应链＝公司产品直接"
    "供给该环节下游；配套预期＝业务与该环节有间接配套/参股/预期关联；"
    "物理约束＝该环节的物理前置条件（如散热/电力）。\n"
    "硬约束：①只抽研报明确提及的业务/产品关系，环节定义中的边界条件必须"
    "满足（如「算力-电力」要求电力业务直接供给数据中心/算力设施，泛新能源"
    "/一般用电不算）；②单纯业绩增长、行业景气、泛化转型愿景不构成挂链；"
    "③枚举中没有合适环节才用 NEW，new_seg 给 ≤12 字的产业环节名（不含"
    "标点；公司自身行为如「运力扩张」不是环节）；④无关时输出 {\"links\":[]}"
    "；⑤evidence 必须是研报原文连续片段。\n"
    "只输出JSON不要任何其他文字：{\"links\":[{\"seg\":\"环节名或NEW\","
    "\"new_seg\":\"新环节名或空\",\"type\":\"枚举\",\"evidence\":\"原文\","
    "\"conf\":0到1}]}\n"
    "研报：%s\n标的：%s（%s）"
)


def _segments():
    """图内 Segment 闭集清单（带定义，按链分组文本 + 全集 set）。"""
    cli = KgClient(retry=1)
    with cli._session() as s:
        rows = s.run(
            "MATCH (g:Segment) RETURN g.name AS n, g.chain AS c, "
            "g.desc AS d ORDER BY c, n").data()
    cli.close()
    by_chain = {}
    for r in rows:
        by_chain.setdefault(r["c"] or "其他", []).append(
            (r["n"], r["d"] or ""))
    txt = "\n".join(
        "【%s】\n%s" % (c, "\n".join(
            "%s＝%s" % (n, d) if d else n for n, d in ns))
        for c, ns in by_chain.items())
    return txt, {r["n"] for r in rows}


def _load_reports():
    out = []
    if not os.path.exists(REPORTS):
        return out
    with open(REPORTS, encoding="utf-8") as f:
        for ln in f:
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("id") and e.get("stocks"):
                out.append(e)
    return out


def _load_done():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for ln in f:
                try:
                    done.add(json.loads(ln).get("rid"))
                except Exception:
                    continue
    return done


def _ollama_ok():
    try:
        return requests.get(
            OLLAMA.replace("/api/chat", "/api/tags"), timeout=3).ok
    except Exception:
        return False


def judge(text, stock, name, seg_txt, timeout=60):
    """单条研报 → [link 候选] 或 None（解析失败重试一次）。"""
    body = {
        "model": MODEL, "stream": False, "think": False,
        "messages": [{"role": "user",
                      "content": PROMPT % (seg_txt, text[:300],
                                           stock, name)}],
        "options": {"temperature": 0.1, "num_predict": 200},
    }
    for _ in range(2):
        try:
            r = requests.post(OLLAMA, json=body, timeout=timeout)
            r.raise_for_status()
            m = re.search(r"\{.*\}", r.json()["message"]["content"],
                          re.S)
            if not m:
                continue
            d = json.loads(m.group(0))
            return d.get("links") or []
        except Exception:
            time.sleep(1.0)
    return None


def _clean_new(name):
    """新环节名白名单清洗：≤12 字、无标点空白。"""
    n = re.sub(r"[\s，。；：、/／·\-—()（）]+", "", str(name or ""))
    return n[:12] if n and len(n) <= 12 and not re.search(
        r"[^\u4e00-\u9fa5A-Za-z0-9]", n) else ""


def run():
    seg_txt, seg_set = _segments()
    if not seg_set:
        print("图内无 Segment，先跑 kg_chain.py build")
        return
    if not _ollama_ok():
        print("Ollama 不可达（127.0.0.1:11434），跳过（影子不阻断）")
        return
    done = _load_done()
    reports = _load_reports()
    todo = [r for r in reports if r["id"] not in done]
    print("研报 %d 条 / 待抽 %d（已抽 %d 跳过）/ 环节闭集 %d"
          % (len(reports), len(todo), len(done), len(seg_set)))
    n_link = n_new = n_ev_bad = 0
    fail_streak = 0
    with open(OUT, "a", encoding="utf-8") as fo:
        for i, r in enumerate(todo, 1):
            code = r["stocks"][0] if r.get("stocks") else ""
            links = judge(r.get("text") or "", code,
                          r.get("reason") or "", seg_txt)
            if links is None:
                fail_streak += 1
                print("  [%d/%d] FAIL %s" % (i, len(todo), r["id"]))
                if fail_streak >= FAIL_STREAK_MAX:
                    print("  熔断：连续 %d 条失败，中止（断点续跑）"
                          % fail_streak)
                    break
                continue
            fail_streak = 0
            row = {"rid": r["id"], "ts": r.get("ts"), "stock": code,
                   "text": (r.get("text") or "")[:120], "links": [],
                   "new_segs": []}
            for lk in links:
                if not isinstance(lk, dict):
                    continue
                tp = lk.get("type")
                if tp not in TYPE_ENUM:
                    continue
                raw_ev = str(lk.get("evidence") or "")
                if raw_ev and raw_ev not in (r.get("text") or ""):
                    n_ev_bad += 1      # evidence 非研报原文（如抄 prompt
                    continue           # 定义文本）——确定性校验直接拦截
                seg = str(lk.get("seg") or "").strip()
                if seg == "NEW":
                    nm = _clean_new(lk.get("new_seg"))
                    if not nm:
                        continue
                    row["new_segs"].append(
                        {"seg": nm, "type": tp,
                         "evidence": (lk.get("evidence") or "")[:80],
                         "conf": lk.get("conf")})
                    n_new += 1
                elif seg in seg_set:
                    row["links"].append(
                        {"seg": seg, "type": tp,
                         "evidence": (lk.get("evidence") or "")[:80],
                         "conf": lk.get("conf")})
                    n_link += 1
            fo.write(json.dumps(row, ensure_ascii=False) + "\n")
            fo.flush()
            print("  [%d/%d] %s %s → 挂链 %d 新环节 %d"
                  % (i, len(todo), code, r["id"][:14],
                     len(row["links"]), len(row["new_segs"])))
    print("影子落盘: %s（本批挂链 %d / 新环节候选 %d / evidence 非原文拦截 %d）"
          % (OUT, n_link, n_new, n_ev_bad))


def stats():
    """影子候选统计：挂链环节分布 + 新环节候选清单。"""
    if not os.path.exists(OUT):
        print("无影子文件（先 run）")
        return
    n_row = n_link = n_hi = 0
    seg_cnt, new_cnt = {}, {}
    with open(OUT, encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            n_row += 1
            for lk in r.get("links") or []:
                n_link += 1
                hi = 1 if (lk.get("conf") or 0) >= 0.85 else 0
                n_hi += hi
                cur = seg_cnt.get(lk["seg"], (0, 0))
                seg_cnt[lk["seg"]] = (cur[0] + 1, cur[1] + hi)
            for ns in r.get("new_segs") or []:
                k = "%s(%s)" % (ns["seg"], ns["type"])
                new_cnt[k] = new_cnt.get(k, 0) + 1
    print("== kg_extract 影子统计（conf≥0.85 记高置信） ==")
    print("研报行 %d / 挂链候选 %d（高置信 %d）/ 新环节候选 %d 种"
          % (n_row, n_link, n_hi, len(new_cnt)))
    print("-- 挂链环节分布 --")
    for k, v in sorted(seg_cnt.items(), key=lambda x: -x[1][0]):
        print("  %-18s %d（高置信 %d）" % (k, v[0], v[1]))
    print("-- 新环节候选（待人工审核后收编 kg_chain_seeds.json） --")
    for k, v in sorted(new_cnt.items(), key=lambda x: -x[1]):
        print("  %-24s %d" % (k, v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "stats"])
    a = ap.parse_args()
    run() if a.cmd == "run" else stats()


if __name__ == "__main__":
    main()
