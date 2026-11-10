# -*- coding: utf-8 -*-
"""金融文本情绪打分引擎 —— P3（sentiment_v2 合主线后的可导入形态）。

承袭 worktree 版 sentiment_v2.py 的核心修正（对照 v1 子串匹配缺陷）：
  1. jieba 分词 + 词典词全量 add_word（避免多字词被切碎）；
  2. 精确词匹配，不做子串扫描（v1「好/大/高」命中一切的 96% 假阳性病根）；
  3. 否定词只回看紧邻 1 词（NEG_WINDOW=1，宽窗口实测只添噪声）；
  4. 程度副词调权（STRONG ×2 / WEAK ×0.5）。

词典：data/cnopendata/chinese-dictionary-of-financial-sentiment（已入湖）。
输出：score_text(text) -> {"polarity": float, "intensity": float,
                            "pos": int, "neg": int, "label": "bullish|bearish|neutral"}

导入即用；批处理见 sentiment_card.py。
"""
import os
import re

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
DICT_DIR = r"C:/aios/quant_backtest/data/cnopendata/chinese-dictionary-of-financial-sentiment"

NEGATIONS = {"不", "没", "没有", "无", "未", "非", "别", "莫", "勿", "难以", "毫无",
             "并未", "不是", "不会", "不能", "不能", "不足", "缺乏", "避免", "否认"}
STRONG = {"很", "非常", "极", "极其", "十分", "特别", "格外", "尤其", "太", "最", "强烈"}
WEAK = {"略", "稍", "稍微", "有点", "有些", "略微", "较"}
NEG_WINDOW = 1

# 金融域词表（2026-11-09 补丁）：入湖词典为通用情感词典，实测核心金融词
# 全缺（看好/看多/利好/涨停/回购/减持/抄底均不在），且「注销」等被标负
# 会在回购语境误判。域词表优先于词典判定。
DOMAIN_POS = {
    "看好", "看多", "看涨", "利好", "利多", "涨停", "连板", "超预期", "超预估",
    "回购", "增持", "加仓", "抄底", "建仓", "满仓", "做多", "新高", "突破", "主升",
    "放量上攻", "爆单", "满产", "订单饱满", "供不应求", "涨价", "提价", "业绩预增",
    "预盈", "扭亏", "高景气", "景气度向上", "龙头", "卡位", "稀缺", "爆发", "起飞",
    "吃肉", "涨停板", "打板", "强势", "硬气", "抗跌", "护城河", "业绩兑现", "中标",
    "放量突破", "北向流入", "主力流入", "增持评级", "买入评级", "目标价上调",
}
DOMAIN_NEG = {
    "看空", "看跌", "利空", "跌停", "砸盘", "出货", "跑路", "割肉", "套牢", "踏空",
    "减持", "清仓", "做空", "爆雷", "暴雷", "退市", "立案", "问询", "警示", "处罚",
    "商誉减值", "业绩变脸", "预亏", "首亏", "续亏", "下滑", "滞涨", "杀跌", "崩了",
    "腰斩", "破位", "破发", "解禁", "质押爆仓", "高位站岗", "接盘", "造假", "暴雷",
    "财务造假", "违规", "冻结", "诉讼", "股东减持", "高位套现", "放量下跌", "闪崩",
    "杀逻辑", "杀业绩", "杀估值", "逻辑破了", "景气度向下",
}

_pos_words = None
_neg_words = None
_jieba_ready = False

# ---- FinBERT 模型通道（P2 融合）----
# bardsai/finance-sentiment-zh-base：新闻语料训练。2026-11-09 实测边界：
#   规范书面语（公告/互动易提问）4/5 判对；散户口语坍缩 neutral p=1.00（7 样本 5 塌）。
# 结论：模型通道只用于规范文本源（互动易），股吧/淘股吧口语一律词典法。
# 权重加载已验证无 missing/mismatched（UNEXPECTED 仅 position_ids，可忽略）。
MODEL_ID = "bardsai/finance-sentiment-zh-base"
W_DICT, W_MODEL = 0.4, 0.6  # QuantMind 验证配比
_model = None
_model_failed = False


def _load_model():
    """懒加载模型；失败置 _model_failed 后永远走纯词典（降级不抛错）。"""
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    try:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        import warnings
        warnings.filterwarnings("ignore")
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(MODEL_ID)
        mdl = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
        mdl.eval()
        _model = (tok, mdl, {int(k): v for k, v in mdl.config.id2label.items()})
    except Exception:
        _model_failed = True
        _model = None
    return _model


def score_model_batch(texts, batch_size=32, max_length=128):
    """模型批量打分。返回 [{polarity, conf}]，polarity = p_pos - p_neg ∈ [-1,1]。
    模型不可用时逐条返回 None（调用方降级）。"""
    m = _load_model()
    if m is None:
        return [None] * len(texts)
    tok, mdl, id2name = m
    import torch
    out = []
    clean = [re.sub(r"\s+", "", str(t or "")) or "无内容" for t in texts]
    with torch.no_grad():
        for i in range(0, len(clean), batch_size):
            enc = tok(clean[i:i + batch_size], return_tensors="pt", padding=True,
                      truncation=True, max_length=max_length)
            probs = torch.softmax(mdl(**enc).logits, dim=-1)
            for p in probs:
                pp = {id2name[j]: float(p[j]) for j in range(len(id2name))}
                out.append({"polarity": round(pp.get("positive", 0.0) - pp.get("negative", 0.0), 3),
                            "conf": round(max(pp.values()), 3)})
    return out


def score_fused_batch(texts, w_dict=W_DICT, w_model=W_MODEL):
    """融合打分（规范文本通道）：w_dict*词典 + w_model*模型。模型不可用=纯词典。
    返回与 score_text 同构 dict，另带 model 字段标记是否参与。"""
    texts = list(texts)
    dict_scores = [score_text(t) for t in texts]
    model_scores = score_model_batch(texts)
    out = []
    for d, m in zip(dict_scores, model_scores):
        if m is None:
            r = dict(d); r["model"] = False
            out.append(r)
            continue
        if d["pos"] + d["neg"] == 0:
            # 词典零命中：完全采信模型
            pol = m["polarity"]
        else:
            # 词典证据按命中数收缩（2026-11-09 补丁）：单命中满值噪声实测
            # 把中性提问拉成 bullish——total<3 时效力按比例衰减
            d_eff = d["polarity"] * min(1.0, (d["pos"] + d["neg"]) / 3.0)
            pol = w_dict * d_eff + w_model * m["polarity"]
        label = "bullish" if pol > 0.15 else ("bearish" if pol < -0.15 else "neutral")
        out.append({"polarity": round(pol, 3), "intensity": d["intensity"],
                    "pos": d["pos"], "neg": d["neg"], "label": label, "model": True})
    return out


def _load_dict():
    global _pos_words, _neg_words
    if _pos_words is not None:
        return _pos_words, _neg_words
    pos, neg = set(), set()
    for p in sorted(_f for _f in os.listdir(DICT_DIR) if _f.startswith("part_") and _f.endswith(".parquet")):
        df = pd.read_parquet(os.path.join(DICT_DIR, p))
        col = df.columns[0]
        words = {str(w).strip() for w in df[col].dropna() if str(w).strip()}
        if "Negative" in col or "neg" in col.lower():
            neg |= words
        else:
            pos |= words
    _pos_words, _neg_words = pos - neg, neg - pos
    return _pos_words, _neg_words


def _ensure_jieba(pos, neg):
    global _jieba_ready
    if _jieba_ready:
        return
    import jieba
    for w in pos | neg | NEGATIONS | STRONG | WEAK | DOMAIN_POS | DOMAIN_NEG:
        jieba.add_word(w)
    jieba.setLogLevel(60)
    _jieba_ready = True


def score_text(text):
    """打分单条文本。polarity ∈ [-1,1]，intensity = 命中密度 ∈ [0,1)。"""
    pos, neg = _load_dict()
    _ensure_jieba(pos, neg)
    import jieba
    text = re.sub(r"\s+", "", str(text or ""))
    if not text:
        return {"polarity": 0.0, "intensity": 0.0, "pos": 0, "neg": 0,
                "label": "neutral"}
    tokens = list(jieba.cut(text))
    pos_n = neg_n = 0
    weighted = 0.0
    n = len(tokens)
    for i, tok in enumerate(tokens):
        weight = 1.0
        if i > 0 and tokens[i - 1] in STRONG:
            weight = 2.0
        elif i > 0 and tokens[i - 1] in WEAK:
            weight = 0.5
        # 域词表优先，其次入湖词典
        base = None
        if tok in DOMAIN_POS:
            base = 1.0
        elif tok in DOMAIN_NEG:
            base = -1.0
        elif tok in pos:
            base = 1.0
        elif tok in neg:
            base = -1.0
        if base is None:
            continue
        j = i - 1
        negated = False
        while j >= 0 and (i - j) <= NEG_WINDOW:
            if tokens[j] in NEGATIONS and tokens[j] not in pos and tokens[j] not in neg \
                    and tokens[j] not in DOMAIN_POS and tokens[j] not in DOMAIN_NEG:
                negated = True
                break
            j -= 1
        val = -base if negated else base
        pos_n += 1 if val > 0 else 0
        neg_n += 1 if val < 0 else 0
        weighted += val * weight
    total = pos_n + neg_n
    if total == 0:
        return {"polarity": 0.0, "intensity": 0.0, "pos": 0, "neg": 0,
                "label": "neutral"}
    polarity = max(-1.0, min(1.0, weighted / total))
    label = "bullish" if polarity > 0.15 else ("bearish" if polarity < -0.15 else "neutral")
    return {"polarity": round(polarity, 3), "intensity": round(total / max(n, 1), 4),
            "pos": pos_n, "neg": neg_n, "label": label}


def dist_label(scores):
    """一批分数 → (bullish, neutral, bearish, 均极性)。"""
    b = sum(1 for s in scores if s["label"] == "bullish")
    r = sum(1 for s in scores if s["label"] == "bearish")
    n = len(scores) - b - r
    pol = (sum(s["polarity"] for s in scores) / len(scores)) if scores else 0.0
    return b, n, r, round(pol, 3)


if __name__ == "__main__":
    tests = ["业绩超预期，订单爆满，强烈看好", "不看好后市，减持跑路", "今天横盘，没啥动静",
             "回购注销，管理层有担当", "没有利空，就是涨不动"]
    for t in tests:
        print(t, "->", score_text(t))
    print("\n=== 模型融合通道自测（规范文本） ===")
    formal = ["公司订单饱满，产能利用率维持高位，业绩有望持续增长",
              "公司主营业务竞争加剧，市场份额下滑，盈利能力承压",
              "公司拟以自有资金回购部分股份用于股权激励",
              "投资者请问公司海外业务拓展情况如何",
              "请说明公司本期计提资产减值准备的具体依据"]
    for t, r in zip(formal, score_fused_batch(formal)):
        print("%-6s pol=%+.2f %s | %s" % (r["label"], r["polarity"],
                                          "模型" if r["model"] else "词典降级", t[:24]))
