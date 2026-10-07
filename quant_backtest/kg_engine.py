# -*- coding: utf-8 -*-
"""K215 · E4 主题起草引擎：任意主题 → 环节拆解草稿 → 审核 → 收编。

通用传导引擎生长层第一块：把「人工拆解半小时」变成「机器起草、人只审
几分钟」。语法固定（kg_chain_seeds 同构 schema + 传导类型×4），词元开放
（任意主题只引擎调用参数）。

命令：
  draft <主题名>      LLM 按固定 schema 起草 → data/kg_theme_draft/<主题>.json
                      （影子，重跑覆盖；不碰图不碰种子）
  adopt <主题名>      审核后的草稿合并进 kg_chain_seeds.json——默认只打印
                      将写入内容，--yes 才落盘（种子是人工真相源，关键
                      操作显式确认）

draft 硬约束（闭集纪律，与 kg_extract 同款降幻觉三板斧）：
· conduction ∈ {载体, 供应链, 物理约束, 配套预期}（传导语法永不开放）；
· 环节名 ≤12 字、强制主题前缀「主题名-名称」（与现有链命名同构防混淆）；
· desc 必须含边界条件（对齐 K213 判例：环节定义是防误挂的核心）；
· 一级 3~6 个、每级二级 0~4 个（防无限膨胀）；
· 已有图内环节在 prompt 中列出供复用交叉引用，避免同义重复建环。
审核闸门：draft 产物必须人工编辑确认后才可 adopt；adopt 默认 dryrun。
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

SEEDS = os.path.join(HERE, "data", "kg_chain_seeds.json")
DRAFT_DIR = os.path.join(HERE, "data", "kg_theme_draft")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
COND_ENUM = ("载体", "供应链", "物理约束", "配套预期")

PROMPT = (
    "你是产业链拆解引擎。对主题「%s」按固定语法起草知识传导链。\n"
    "语法（固定，不得改）：一级环节带 conduction 字段标传导类型，枚举："
    "载体＝该主题的物理载体核心；供应链＝直接供给关系；物理约束＝缺之不"
    "成立的因果前置（如发热→散热、功耗→电力）；配套预期＝间接配套/参股/"
    "预期关联。细节环节作二级，parent 引用一级环节名。\n"
    "硬约束：①一级环节 3~6 个，每个一级下二级 0~4 个；②环节名 ≤12 字且"
    "必须带前缀「%s-」；③desc 一句话定义且必须含边界条件（说明什么算、"
    "什么不算，参照：「算力-电力＝数据中心功耗的电力供应环节，须直接供给"
    "数据中心——泛新能源/一般用电不算」）；④与已有环节重复或同义的不要"
    "新建（已有环节清单：%s）；⑤整车/终端等输出形态不建环节。\n"
    "关键区分：环节是产业实体（产品/服务/部件/基础设施的名词），不是关系"
    "类型！错误示范：「X-载体」「X-供应链」（那是 conduction 类型，不是"
    "环节）。正确示范（算力主题）：一级「算力-GPU芯片」conduction=载体、"
    "「算力-AI服务器」conduction=供应链、「算力-散热」conduction=物理"
    "约束；二级「算力-液冷」parent=「算力-散热」。desc 直接写定义，不要"
    "复读环节名。\n"
    "只输出JSON不要任何其他文字：{\"theme\":\"%s\",\"segments\":["
    "{\"name\":\"%s-…\",\"level\":1,\"conduction\":\"枚举\",\"desc\":"
    "\"…含边界条件\"},{\"name\":\"%s-…\",\"level\":2,\"parent\":\"%s-…\","
    "\"desc\":\"…\"}]}"
)


def _ollama_ok():
    try:
        return requests.get(
            OLLAMA.replace("/api/chat", "/api/tags"), timeout=3).ok
    except Exception:
        return False


def _seg_names():
    """图内已有环节全集（供 prompt 防重复 + 交叉引用）。"""
    cli = KgClient(retry=1)
    with cli._session() as s:
        return [r["n"] for r in s.run(
            "MATCH (g:Segment) RETURN g.name AS n ORDER BY n").data()]


def _validate(theme, segs, existing):
    """草稿校验：返回 (合法 segs, 问题列表)。"""
    ok, errs = [], []
    n1 = sum(1 for s in segs if s.get("level") == 1)
    if not 3 <= n1 <= 6:
        errs.append("一级环节数 %d 越界（3~6）" % n1)
    names = set()
    for s in segs:
        nm = str(s.get("name") or "").strip()
        lv = s.get("level")
        if not nm.startswith(theme + "-") or len(nm) > 20:
            errs.append("%s：命名须「%s-…」且短" % (nm, theme))
            continue
        if nm in existing or nm in names:
            errs.append("%s：与已有环节重复" % nm)
            continue
        if lv == 1 and s.get("conduction") not in COND_ENUM:
            errs.append("%s：conduction 非法" % nm)
            continue
        d = str(s.get("desc") or "")
        if len(d) < 10:
            errs.append("%s：desc 过短（须含边界条件）" % nm)
            continue
        if lv == 2 and s.get("parent") not in \
                {x.get("name") for x in segs if x.get("level") == 1}:
            errs.append("%s：parent 不在一级清单" % nm)
            continue
        names.add(nm)
        ok.append(s)
    return ok, errs


def draft(theme):
    """LLM 起草 → 校验 → 影子落盘（原子写，重跑覆盖）。"""
    if not _ollama_ok():
        print("Ollama 不可达，跳过（影子不阻断）")
        return
    existing = _seg_names()
    body = {
        "model": MODEL, "stream": False, "think": False,
        "messages": [{"role": "user",
                      "content": PROMPT % (theme, theme,
                                           "、".join(existing) or "（无）",
                                           theme, theme, theme, theme)}],
        "options": {"temperature": 0.3, "num_predict": 800},
    }
    print("起草 %s …（LLM 调用中）" % theme)
    d = None
    for _ in range(2):
        try:
            r = requests.post(OLLAMA, json=body, timeout=120)
            r.raise_for_status()
            m = re.search(r"\{.*\}", r.json()["message"]["content"], re.S)
            if m:
                d = json.loads(m.group(0))
                break
        except Exception:
            time.sleep(1.0)
    if not d:
        print("FAIL：LLM 输出解析失败（重试 1 次未果）")
        return
    segs, errs = _validate(theme, d.get("segments") or [], set(existing))
    out = {"theme": theme, "drafted_at": time.strftime("%Y-%m-%d %H:%M"),
           "model": MODEL, "segments": segs, "errors": errs,
           "status": "drafted（人工编辑确认后 adopt --yes）"}
    os.makedirs(DRAFT_DIR, exist_ok=True)
    fp = os.path.join(DRAFT_DIR, "%s.json" % theme)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)
    n1 = sum(1 for s in segs if s.get("level") == 1)
    print("草稿: %s（一级 %d / 环节 %d / 校验问题 %d）"
          % (fp, n1, len(segs), len(errs)))
    for e in errs:
        print("  WARN %s" % e)
    for s in segs:
        p = ("  └ %s" % s["name"]) if s.get("level") == 2 \
            else ("%s [%s]" % (s["name"], s.get("conduction")))
        print(p)
        print("      ＝%s" % s.get("desc"))
    print("审核闸门：人工编辑 %s 后执行 adopt %s --yes 收编种子"
          % (fp, theme))


def adopt(theme, yes=False):
    """审核后草稿 → 合并进 kg_chain_seeds.json（默认 dryrun）。"""
    fp = os.path.join(DRAFT_DIR, "%s.json" % theme)
    if not os.path.exists(fp):
        print("无草稿 %s（先 draft）" % fp)
        return
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("errors"):
        print("草稿存在未清校验问题 %d 条，先人工编辑清除再 adopt"
              % len(d["errors"]))
        return
    segs = [{"name": s["name"], "level": s.get("level") or 1,
             "parent": s.get("parent"),
             "conduction": s.get("conduction"),
             "desc": s.get("desc") or ""}
            for s in d.get("segments") or []]
    chain = {"theme": theme, "segments": segs, "conductions": [],
             "supplies": []}
    if not yes:
        print("=== DRYRUN：将向 %s 追加 ===" % os.path.basename(SEEDS))
        print(json.dumps(chain, ensure_ascii=False, indent=1))
        print("确认无误执行：python kg_engine.py adopt %s --yes" % theme)
        return
    with open(SEEDS, encoding="utf-8") as f:
        seeds = json.load(f)
    if any(c.get("theme") == theme for c in seeds.get("chains") or []):
        print("种子已存在主题 %s（防覆盖中止）" % theme)
        return
    seeds["chains"].append(chain)
    tmp = SEEDS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seeds, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SEEDS)
    print("已收编 %s（环节 %d）。执行 kg_chain.py build 入图。"
          % (theme, len(segs)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "adopt"])
    ap.add_argument("theme")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.cmd == "draft":
        draft(a.theme)
    else:
        adopt(a.theme, yes=a.yes)


if __name__ == "__main__":
    main()
