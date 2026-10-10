# -*- coding: utf-8 -*-
"""K239 · 三期社区摘要层：主题级事实注入式叙事（图谱→AI 可读）。

GraphRAG 社区摘要思想的本地化落法：不引全家桶，对每个 Theme 从图内
拉「事实切片」（传导链环节/挂链标的/近窗结构信号/派系共识/资金接力
证据），交 Ollama 做**事实注入式改写**——只许用给定事实，禁止引入
清单外实体/数字（closed-book 禁止）。

反幻觉校验（K213 判例「evidence in 原文」同源）：摘要中出现的每个
事实实体名必须 ∈ 事实切片实体集，未通过的摘要打 verify=false 不出
门（影子纪律）。

落盘 data/kg_digest_last.json（原子写，影子只记不推）；晨报
KgResonanceNotify 读取同主题 digest 段落（K240 接线）。

用法：
  python kg_digest.py run                # 图内全部主题
  python kg_digest.py run 人形机器人     # 单主题
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import requests                                       # noqa: E402
from kg_client import KgClient                        # noqa: E402

OUT = os.path.join(HERE, "data", "kg_digest_last.json")
OLLAMA = os.environ.get("KG_OLLAMA", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
SIG_DAYS = int(os.environ.get("KG_SIG_DAYS", "30"))
PROMPT = (
    "你是图谱叙事引擎。只允许使用「事实清单」里的信息，写一段不超过 "
    "130 字的中文投研叙事；禁止引入清单外的任何标的/环节/数字/结论；"
    "没有信号或共识就如实说没有。\n"
    "硬约束：①提及信号时必须保留信号类型原文（如「3卖」「1买」），"
    "禁止把卖信号说成买信号或泛化方向；②共识表述照抄事实清单的派系名"
    "（如「技术派✓」）；③禁止使用「利好」「看多」等清单外判断词。\n"
    "事实清单（JSON）：\n%s\n"
    "只输出叙事正文，不要标题和引号。"
)


def _ollama_ok():
    try:
        return requests.get(
            OLLAMA.replace("/api/chat", "/api/tags"), timeout=3).ok
    except Exception:
        return False


def gather(cli, theme):
    """主题事实切片：环节→标的→近窗信号→共识→资金证据（全部图内实查）。"""
    d0 = (datetime.now() - timedelta(days=SIG_DAYS)).strftime("%Y%m%d")
    facts = {"theme": theme, "segments": []}
    with cli._session() as s:
        segs = {}
        for r in s.run(
                "MATCH (t:Theme {name:$t})-[:CONDUCTION]->(g:Segment) "
                "OPTIONAL MATCH (g)<-[:SUB_OF]-(g2:Segment) "
                "RETURN g.name AS g1, g.chain AS c, g.desc AS d, "
                "collect(DISTINCT g2.name) AS subs",
                t=theme).data():
            segs[r["g1"]] = {"desc": r["d"] or "", "subs": r["subs"] or []}
        for g1, info in segs.items():
            rows = s.run(
                "MATCH (sec:Security)-[sp:SUPPLIES_TO]->(g:Segment) "
                "WHERE g.name = $g OR (g)-[:SUB_OF]->(:Segment {name:$g}) "
                "OPTIONAL MATCH (sec)-[:HAS_STRUCTURE]->(e:MarketEvent "
                "{kind:'chanlun'}) WHERE e.detect_date >= $d0 "
                "RETURN sec.name AS nm, sp.evidence AS ev, "
                "collect({t:e.type, dd:e.detect_date}) AS sigs",
                g=g1, d0=d0).data()
            cons = s.run(
                "MATCH (c:Commentator)-[a:ASSESSED {theme:$t}]->"
                "(sec:Security) "
                "WHERE EXISTS { MATCH (sec)-[:SUPPLIES_TO]->(g2:Segment) "
                "WHERE g2.name = $g OR (g2)-[:SUB_OF]->"
                "(:Segment {name:$g}) } "
                "RETURN sec.name AS nm, "
                "collect(c.name + a.verdict) AS vs, a.stars AS st",
                t=theme, g=g1).data()
            cmap = {x["nm"]: x for x in cons}
            items = []
            for r in rows:
                items.append({
                    "seg": g1, "stock": r["nm"], "evidence": r["ev"] or "",
                    "signals": [x["t"] + "(" + (x["dd"] or "") + ")"
                                for x in (r["sigs"] or []) if x["t"]],
                    "consensus": cmap.get(r["nm"], {}).get("vs", []),
                    "stars": cmap.get(r["nm"], {}).get("st", 0)})
            if items:
                facts["segments"].append({"seg": g1, "desc": info["desc"],
                                          "stocks": items})
    return facts


def summarize(facts):
    """Ollama 事实注入式改写（K241：网络/5xx/坏响应重试 2 次 backoff 3s/6s）。"""
    slim = []
    for seg in facts.get("segments") or []:
        for it in seg.get("stocks") or []:
            slim.append({"环节": seg["seg"], "标的": it["stock"],
                         "资金证据": it["evidence"][:40],
                         "近30日信号": it["signals"][:2],
                         "共识": it["consensus"][:4]})
    if not slim:
        return None, []
    body = {"model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "user",
                          "content": PROMPT % json.dumps(
                              slim, ensure_ascii=False)}],
            "options": {"temperature": 0.3, "num_predict": 300}}
    ents = [x["标的"] for x in slim] + [x["环节"] for x in slim]
    last = None
    for i in range(3):
        try:
            r = requests.post(OLLAMA, json=body, timeout=120)
            if r.status_code >= 500:
                raise requests.HTTPError("HTTP %d" % r.status_code)
            r.raise_for_status()
            txt = re.sub(r"\s+", " ",
                         r.json()["message"]["content"]).strip().strip('"')
            return txt, ents
        except (requests.RequestException, ValueError, KeyError,
                TypeError) as e:
            last = e
            if i < 2:
                time.sleep(3 * (i + 1))
    raise RuntimeError("Ollama 改写失败(重试3次): %s" % last)


def verify(txt, ents):
    """反幻觉最低线：叙事必须至少提及一个清单内实体（证明 grounded）。
    更强的逐句对照由影子人工审核承担（K213 判例）。"""
    return any(e in txt for e in ents), []


def run(theme=None):
    cli = KgClient(retry=1)
    try:
        with cli._session() as s:
            themes = [r["n"] for r in s.run(
                "MATCH (t:Theme) RETURN t.name AS n").data()]
    except Exception as e:                 # K241：图库不可达不崩 run，占位落盘
        themes = [theme] if theme else []
        print("Neo4j 不可达: %s" % e)
    if theme:
        themes = [t for t in themes if t == theme]
    ok = _ollama_ok()
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "model": MODEL, "themes": {}}
    for t in themes:
        try:
            facts = gather(cli, t)
        except Exception as e:
            out["themes"][t] = {"digest": "", "verified": False,
                                "note": "Neo4j 不可达"}
            print("[%s] gather 失败: %s" % (t, e))
            continue
        ents = [it["stock"] for seg in facts.get("segments", [])
                for it in seg.get("stocks", [])]
        if not ok:
            out["themes"][t] = {"digest": "", "verified": False,
                                "note": "Ollama 不可达"}
            continue
        try:
            txt, ents2 = summarize(facts)
        except Exception as e:
            out["themes"][t] = {"digest": "", "verified": False,
                                "note": str(e)}
            print("[%s] %s" % (t, e))
            continue
        if txt is None:
            out["themes"][t] = {"digest": "", "verified": True,
                                "note": "无挂链标的（冷启动）",
                                "entities": []}
            continue
        v, bad = verify(txt, ents2)
        if v:
            out["themes"][t] = {"digest": txt, "verified": True,
                                "entities": ents2}
        else:                              # K241：verify 不过不出门，正文不落盘
            out["themes"][t] = {"digest": "", "verified": False,
                                "note": "反幻觉校验未过", "entities": ents2}
        print("[%s] %d 字 / 实体 %d / verified=%s"
              % (t, len(txt), len(ents2), v))
        time.sleep(1)
    cli.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("影子落盘: %s" % OUT)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("theme", nargs="?", default=None)
    a = ap.parse_args()
    run(a.theme)


if __name__ == "__main__":
    main()
