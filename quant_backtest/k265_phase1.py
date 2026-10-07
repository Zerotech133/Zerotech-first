# -*- coding: utf-8 -*-
"""K265 Phase 1：概念级方向性语义边（影子模式）。

430 概念 → 共现对（共同成分股≥3，剔通道/资金类噪声概念）→ qwen 判方向 →
影子清单 k265_phase1_shadow.jsonl，抽样人眼核对后经 k265_phase1_commit.py 入图。
用法：python k265_phase1.py --from 2300 --count 2000
（--from/--count 按共现降序的全局对列表切片；断点续跑自动跳过已判定对）
"""
import json
import re
import sys
import time
from itertools import combinations

import requests

sys.path.insert(0, r"e:\老田的AI助手\quant_backtest")
from kg_client import KgClient  # noqa: E402

OUT = r"e:\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl"
MODEL = "qwen3.5:9b"   # 实测：9b 1.5-3.7s/对；35b 22GB 纯 CPU 不可用；4b 返回空
BATCH = 300         # 默认批量
MIN_OV = 3          # 共同成分股下限
MAX_MEMBERS = 800   # 单概念成分上限（超大全簇无链式语义）

# 通道/资金/指数类噪声概念（K264 遗留剔除项）
NOISE_PAT = re.compile(
    r"股通|融资融券|转融|转债|可转债|注册制|次新|ST|每股|公积金|未分配|"
    r"机构重仓|基金重仓|社保重仓|QFII|预盈|预亏|高送转|GDR|AH股|B股|H股|"
    r"样本|成指|创业板综|MSCI|富时|标普|证金|汇金|昨日涨停|昨日触板|"
    r"百元股|低价股|破净股|高股息|壳资源|独角兽|中字头|地方国企|央企|"
    r"举牌|股权激励|员工持股|回购|增持|减持|重组|借壳|摘帽"
)


def _sanitize(s):
    s = re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


def llm_pair(a, mb, b, ma):
    p = f"""概念A：{a}（成分股示例：{mb}）
概念B：{b}（成分股示例：{ma}）
判断 A/B 两个概念板块是否存在产业链方向关系。只输出JSON：
{{"ok": true, "edge": "SUPPLIES|DRIVES", "src": "A或B", "dst": "A或B", "conf": 0.0, "reason": "一句话"}}
edge 语义：SUPPLIES=src 概念向 dst 概念供应核心产品/材料/服务（src是上游，
仅限直接供料/供件/供核心设备，生态支撑类不算）；DRIVES=src 概念的需求或发展
显著带动 dst 概念景气（如 算力→IDC/电力）。
仅平行板块、纯资金/风格关联或无明确产业传导：{{"ok": false, "reason": "一句话"}}"""
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/chat",
            json={"model": MODEL, "format": "json", "stream": False,
                  "think": False,  # qwen3.5 思考型：不关思考会耗尽 num_predict 致 content 空
                  "options": {"temperature": 0.1, "num_predict": 300},
                  "messages": [{"role": "system",
                                "content": "你是A股产业链分析器。只输出JSON。"},
                               {"role": "user", "content": p}]},
            timeout=120)
        r.raise_for_status()
        return _sanitize(r.json()["message"]["content"])
    except Exception as e:
        return {"ok": False, "reason": f"llm_error:{type(e).__name__}"}


def main():
    # 分批参数：--from N --count M（按共现降序的全局对列表切片）
    args = sys.argv[1:]
    frm = int(args[args.index("--from") + 1]) if "--from" in args else 0
    cnt = int(args[args.index("--count") + 1]) if "--count" in args else BATCH

    cli = KgClient(retry=1, retry_wait=1)
    cy = ("MATCH (c:Concept)<-[:MAPS_TO]-(s:Security) "
          "RETURN c.name AS name, collect(s.name) AS members, "
          "count(s) AS n")
    rows = [dict(r) for r in cli._session().run(cy)]
    print(f"[1] concepts with members: {len(rows)}")

    keep = {r["name"]: set(r["members"]) for r in rows
            if MIN_OV <= r["n"] <= MAX_MEMBERS and not NOISE_PAT.search(r["name"])}
    print(f"[2] after noise/size filter: {len(keep)}")

    names = sorted(keep, key=lambda n: -len(keep[n]))
    pairs = []
    for a, b in combinations(names, 2):
        ov = keep[a] & keep[b]
        if len(ov) >= MIN_OV:
            pairs.append((a, b, len(ov)))
    pairs.sort(key=lambda x: -x[2])
    print(f"[3] candidate pairs (ov>={MIN_OV}): {len(pairs)}, "
          f"top overlap: {pairs[0][2] if pairs else 0}")

    # 断点续跑：跳过已判定对
    done = set()
    try:
        for l in open(OUT, encoding="utf-8"):
            r = json.loads(l)
            done.add((r["a"], r["b"]))
    except FileNotFoundError:
        pass
    batch = [(a, b, ov) for a, b, ov in pairs[frm:frm + cnt]
             if (a, b) not in done]
    print(f"[3b] batch slice [{frm}:{frm+cnt}] todo={len(batch)} "
          f"(done={len(done)})")

    t0 = time.time()
    out = []
    with open(OUT, "a", encoding="utf-8") as f:
        for i, (a, b, ov) in enumerate(batch, 1):
            ext = llm_pair(a, ", ".join(sorted(keep[a])[:5]),
                           b, ", ".join(sorted(keep[b])[:5]))
            rec = {"a": a, "b": b, "overlap": ov, "ext": ext}
            out.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if i % 30 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(batch)} elapsed={el:.0f}s avg={el/i:.1f}s/对",
                      flush=True)

    ok = [r for r in out if r["ext"].get("ok")]
    print(f"[4] done: {len(out)} judged, ok={len(ok)} "
          f"({time.time()-t0:.0f}s)", flush=True)

    from collections import Counter
    cnt2 = Counter(f"{r['ext'].get('edge')}:{r['ext'].get('src')}" for r in ok)
    print("[5] edge:src dist:", dict(cnt2))
    print("--- 抽样 30（人眼核对）---")
    for r in ok[:30]:
        e = r["ext"]
        s = r["a"] if e.get("src") == "A" else r["b"]
        d = r["b"] if e.get("src") == "A" else r["a"]
        print(f"  {s} -[{e.get('edge')}|{e.get('conf')}]-> {d}"
              f"  (ov={r['overlap']}) {e.get('reason','')[:30]}")


if __name__ == "__main__":
    main()
