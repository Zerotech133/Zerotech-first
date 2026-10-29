# -*- coding: utf-8 -*-
"""K261 L1：实体链接通用服务 —— 任意文本 → 实体锚点。

从 kg_semantica/门户 _name_dictionary 提炼为通用函数：公告/新闻/研报
统一消费「文本→锚点（概念/公司/产品/行业/股票/席位/题材…）」，不再各链
各自为政。Concept 节点自动生长（后续步）以本服务的锚点为种子。

设计（对齐本体论实体解析、Palantir 实体解析思路）：
· 名称词典从图内实体拉取（label|key|name，Security 含曾用名 aliases），
  长名优先子串匹配 —— 与门户 /kg/ask 同源逻辑，但独立成层供全链复用；
· 锚点去重按 (label,key)，同标签超限截断，防长文本爆炸；
· --eval N 小样评测：公告文本 → link_text 锚点 → LLM 独立提取锚点 →
  精度/召回双口径（门闸 ≥85% 才谈全量接入与 Concept 自动生长）。

用法：
  python kg_link.py 002261.SZ            # 个股文本链接演示（用公告样本）
  python kg_link.py --text "算力概念持续走强，英维克液冷服务器受关注"
  python kg_link.py --eval 100           # 小样评测（LLM 交叉验证）
  python kg_link.py --dict                # 词典规模统计
"""

import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kg_client import KgClient

# 可链接实体标签 → (唯一键字段, 名称字段, 类别)
# 与门户 CANVAS_LABELS 对齐（事件类锚点后续再加：Event text 过长噪声大）
LINK_LABELS = {
    "Security": ("ts_code", ("name", "ts_code"), "thing"),
    "Company": ("com_name", ("com_name",), "org"),
    "Concept": ("name", ("name",), "org"),
    "Product": ("name", ("name",), "thing"),
    "Industry": ("name", ("name",), "org"),
    "SwL1Industry": ("industry_name", ("industry_name",), "org"),
    "Theme": ("name", ("name",), "event"),
    "Segment": ("name", ("name", "chain"), "thing"),
    "Technique": ("name", ("name",), "thing"),
    "HotMoney": ("name", ("name",), "person"),
    "Commentator": ("name", ("name",), "person"),
    "Seat": ("name", ("name",), "person"),
    "Region": ("name", ("name",), "place"),
    "EXP_Community": ("name", ("name",), "org"),
}
MAX_PER_LABEL = 4  # 单标签锚点上限（少而精：L1 门闸 precision>=85% 优先）
MAX_TOTAL = 12  # 单文本锚点总量上限（长名优先下天然挤出短碎片）
MIN_NAME_LEN = 2  # 过短名称不参与匹配（1 字名称噪声大）
# 按 label 分级最小长度：Product 图内 2 字垃圾/通用词多（会议/法律/控股/第二…），
# Region 2 字大城市名在公告里多含于公司全称（上海/北京/深圳…）误报高，均提为 3；
# 行业类（Industry/SwL1Industry）2 字名在公告标题里多为公司名碎片（XX证券→证券），提为 3
LABEL_MIN_LEN = {"Product": 3, "Region": 3, "Industry": 3, "SwL1Industry": 3}
# Product 3 字动词短语黑名单（公告标题高频误报，非真实产品）
STOP_NAMES = {"提供担保", "管理信息"}

# LLM 交叉验证：独立提取锚点（与 kg_notice_llm 同款门闸思路）
LLM_PROMPT = (
    "你是金融文本实体抽取器。提取下面文本中出现的实体，只收这些类别："
    "公司/股票、产品、概念题材、行业。剔除日期/数字/普通动词。\n"
    '只输出JSON数组，不要任何其他文字：["实体名1","实体名2"]\n'
    "文本：%s"
)
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")

# 公司名后缀归一（链接结果 vs LLM 文本比较用；词典名保持原样）
_SUFFIX = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "股份有限公司分公司",
    "集团股份有限公司",
    "集团有限责任公司",
    "科技集团",
    "控股集团",
    "集团有限公司",
    "股份公司",
    "集团公司",
    "股份",
    "集团",
    "公司",
    "科技",
)
_SPACE_RE = re.compile(r"\s+")


def _norm(name):
    """名称归一：去空格 + 剥公司后缀（比较口径，非匹配口径）。"""
    s = _SPACE_RE.sub("", str(name or ""))
    for suf in _SUFFIX:
        if s.endswith(suf) and len(s) - len(suf) >= 2:
            s = s[: -len(suf)]
            break
    return s


def name_dictionary(cli=None):
    """图内实体名词典 → [{id,label,name}]（长名优先排序调用方做）。

    只取名称非空且长度>=MIN_NAME_LEN 的行；Security 额外带 aliases。
    """
    own = cli is None
    if own:
        cli = KgClient(retry=1, retry_wait=1)
    out = []
    try:
        for lbl, (key, fields, _cat) in LINK_LABELS.items():
            cond = " OR ".join("toString(n.%s) IS NOT NULL" % f for f in fields)
            if lbl == "Security":
                # Security 带曾用名/别名（K235 namechange 入图 LIST 属性）
                cy = (
                    "MATCH (n:Security) WHERE %s RETURN n.%s AS k, "
                    "coalesce(n.%s, n.%s) AS nm, n.aliases AS aliases"
                    % (cond, key, fields[0], fields[-1])
                )
            else:
                cy = (
                    "MATCH (n:%s) WHERE %s RETURN n.%s AS k, "
                    "coalesce(n.%s, n.%s) AS nm, null AS aliases"
                    % (lbl, cond, key, fields[0], fields[-1])
                )
            for r in cli._session().run(cy):
                nm = r["nm"]
                if not nm or len(str(nm)) < MIN_NAME_LEN:
                    continue
                names = [nm]
                if r["aliases"]:
                    names += [
                        a for a in (r["aliases"] or []) if len(str(a)) >= MIN_NAME_LEN
                    ]
                for name in dict.fromkeys(str(x).strip() for x in names):
                    if not name:
                        continue
                    out.append(
                        {"id": "%s|%s" % (lbl, r["k"]), "label": lbl, "name": name}
                    )
    finally:
        if own:
            cli.close()
    return out


def link_text(text, dictionary=None):
    """任意文本 → 实体锚点列表（长名优先子串匹配，去重 + 截断）。

    Returns: [{"id","label","name","span"}]
    """
    text = _SPACE_RE.sub("", text or "")
    if not text:
        return []
    if dictionary is None:
        dictionary = name_dictionary()
    entries = sorted(dictionary, key=lambda d: -len(d["name"]))
    seen, out = set(), []
    for d in entries:
        if d["name"] in STOP_NAMES:
            continue
        if len(d["name"]) < LABEL_MIN_LEN.get(d["label"], MIN_NAME_LEN):
            continue
        pos = text.find(d["name"])
        if pos < 0:
            continue
        k = d["id"]
        if k in seen:
            continue
        # 已命中更长的同类名时，跳过短名（防子串重复锚）
        if any(
            o["id"].split("|")[0] == d["label"]
            and d["name"] in o["name"]
            and d["name"] != o["name"]
            for o in out
        ):
            continue
        seen.add(k)
        out.append({"id": k, "label": d["label"], "name": d["name"], "span": pos})
        if len(out) >= MAX_TOTAL:
            break
    # 按标签截断（防单标签刷屏），然后按出现位置重排
    per_label = {}
    final = []
    for o in out:
        per_label.setdefault(o["label"], []).append(o)
    for arr in per_label.values():
        final += arr[:MAX_PER_LABEL]
    final.sort(key=lambda o: o["span"])
    return final


def ollama_ok():
    try:
        import requests

        r = requests.get(OLLAMA.replace("/api/chat", "/api/tags"), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def llm_extract(text, timeout=45):
    """LLM 独立提取锚点 → [归一化名称]。失败返回 None。"""
    import requests

    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": LLM_PROMPT % str(text)[:300]}],
        "options": {"temperature": 0.1, "num_predict": 120},
    }
    for _ in range(2):
        try:
            r = requests.post(OLLAMA, json=body, timeout=timeout)
            r.raise_for_status()
            m = re.search(r"\[[^\]]*\]", r.json()["message"]["content"])
            if not m:
                continue
            arr = json.loads(m.group(0))
            if not isinstance(arr, list):
                continue
            return [_norm(x) for x in arr if isinstance(x, str) and len(_norm(x)) >= 2]
        except Exception:
            time.sleep(1.0)
    return None


def eval_sample(n=100, seed=7):
    """小样评测：公告文本抽样 → link_text vs LLM 提取 → 精度/召回。

    输出「锚点一致率：precision X% / recall Y%（n=锚点数, 文本 m）」，
    precision（我方锚点被 LLM 确认的比例）≥85% 视为通过门闸。
    """
    evt_f = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "semantica", "events.jsonl"
    )
    rows = []
    with open(evt_f, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("kind") == "notice" and len(e.get("text") or "") >= 20:
                rows.append(e["text"])
    rnd = random.Random(seed)
    sample = rnd.sample(rows, min(n, len(rows)))
    print("评估文本 %d 条（notice 抽样）" % len(sample))
    print("构建实体名词典…", flush=True)
    dictionary = name_dictionary()
    print("词典 %d 条" % len(dictionary))
    tp = fp = llm_anchors = texts_with_anchor = 0
    details = []
    for i, text in enumerate(sample, 1):
        anchors = link_text(text, dictionary)
        llm = llm_extract(text)
        if llm is None:
            print("  [%d/%d] LLM 提取失败（跳过）" % (i, len(sample)))
            continue
        if anchors:
            texts_with_anchor += 1
        {_norm(a["name"]): a for a in anchors}
        a_names = {_norm(a["name"]) for a in anchors}
        llm_set = set(llm)
        tp += len(a_names & llm_set)
        fp += len(a_names - llm_set)
        llm_anchors += len(llm_set)
        details.append(
            {
                "text": text,
                "anchors": sorted(a_names),
                "llm": sorted(llm_set),
                "fp": sorted(a_names - llm_set),
                "miss": sorted(llm_set - a_names),
            }
        )
        if i % 10 == 0:
            print(
                "  %d/%d (tp=%d fp=%d llm=%d)" % (i, len(sample), tp, fp, llm_anchors),
                flush=True,
            )
    prec = 100.0 * tp / (tp + fp) if (tp + fp) else 0.0
    rec = 100.0 * tp / llm_anchors if llm_anchors else 0.0
    print(
        "\n锚点一致率: precision %.1f%% / recall %.1f%% "
        "（我方锚点 %d，LLM 锚点 %d，文本 %d 条，含锚文本 %d 条）"
        % (prec, rec, tp + fp, llm_anchors, len(sample), texts_with_anchor)
    )
    print("门闸：precision>=85%% %s" % ("通过" if prec >= 85 else "未过（先修再全量）"))
    # 落评估明细（每文本 anchors/llm/fp/miss，供噪音归因）
    detail_f = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data",
        "semantica",
        "kg_link_eval_detail.jsonl",
    )
    with open(detail_f, "w", encoding="utf-8") as f:
        for d in details:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print("评估明细 %d 条 → %s" % (len(details), detail_f))
    return prec, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dict", action="store_true", help="词典规模统计")
    ap.add_argument("--eval", type=int, default=0, help="小样评测 N 条")
    ap.add_argument("--text", default="", help="单文本链接演示")
    args = ap.parse_args()

    if args.dict:
        d = name_dictionary()
        from collections import Counter

        print("词典 %d 条：%s" % (len(d), dict(Counter(x["label"] for x in d))))
        return
    if args.eval:
        eval_sample(args.eval)
        return
    if args.text:
        anchors = link_text(args.text)
        print("文本：%s" % args.text[:80])
        for a in anchors:
            print("  [%s] %s  (@%d)" % (a["label"], a["name"], a["span"]))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
