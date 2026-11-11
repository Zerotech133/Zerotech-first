# -*- coding: utf-8 -*-
"""正文股票名抽取（P5.4，2026-11-10 老田批准）。

用途：news 入库时 stocks 为空的行，用 stock_basic 证券简称做最长匹配打码，
      并给出置信度分级（标题命中=high / 仅正文命中=low）。

口径与 dry-run（_qpzm_rd_20261106/maintenance/_p54_tag_dryrun.py 2026-11-10 影响面报告）
保持一致：MIN_NAME_LEN=3、正文扫前 300 字、同条去重、max_hits=3、歧义名黑名单。

置信度消费约定（下游）：
  - high：标题点名，强相关，可作一切 pattern 依据；
  - low ：仅正文提及，可能只是罗列板块成分股——只作「有信号」判定，
          **不得作 Δmin 锚点**（防污染 P-01/P-05/P-06/P-09 的分钟级锚点）。
  - None：未打码（无命中）。
"""
import json
import os
import re
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
BASIC = os.path.join(_ROOT, "quant_backtest", "data", "tushare_curated", "stock_basic.parquet")

MIN_NAME_LEN = 3          # 简称 >=3 字才参与（2 字名歧义过大）
CONTENT_SCAN = 300        # 正文只扫前 300 字
MAX_HITS = 3              # 单条新闻最多打码数

# 歧义名黑名单：既是证券简称、又是通用词/地名，正文出现时大概率不是指这只票。
# 依据：2026-10-20 / 全库 dry-run 人工抽样（"OpenAI面临诉讼"→机器人、
#       "美国房地产ETF"→农产品、黑龙江自然保护地→长白山、IMF 年报→驱动力）。
AMBIGUOUS = {
    "机器人", "农产品", "金融街", "陆家嘴", "太平洋", "太阳能", "驱动力", "长白山",
}

# 研报署名：标题「[个股研报·华鑫证券]」中的券商名是署名不是标的
BROKER_SIGN_RE = re.compile(r"[·•]\s*([\u4e00-\u9fa5]{2,12}证券)")

_INDEX = None             # 首字 -> [(name, ts_code)]，长名优先
_NAME2TS = None


def _load_index():
    global _INDEX, _NAME2TS
    if _INDEX is not None:
        return _INDEX
    import pandas as pd
    b = pd.read_parquet(BASIC).drop_duplicates("ts_code")   # 表内每票重复约 30 次，先去重
    _INDEX = defaultdict(list)
    _NAME2TS = {}
    for ts, nm in zip(b["ts_code"].astype(str), b["name"].astype(str)):
        nm = nm.replace(" ", "")
        if len(nm) < MIN_NAME_LEN:
            continue
        _INDEX[nm[0]].append((nm, ts))
        _NAME2TS.setdefault(nm, ts)
    for k in _INDEX:
        _INDEX[k].sort(key=lambda x: -len(x[0]))            # 长名优先，减少短名截胡
    return _INDEX


def _scan(text, idx, taken):
    """在 text 里找股票名（长名优先、黑名单跳过、命中即占位避免重叠）。"""
    hits = []
    i, L = 0, len(text)
    while i < L and len(hits) < MAX_HITS:
        cand = idx.get(text[i])
        if cand:
            for nm, ts in cand:
                if nm in AMBIGUOUS:
                    continue
                if text.startswith(nm, i) and not any(s < i + len(nm) and i < e for s, e in taken):
                    hits.append((nm, ts))
                    taken.append((i, i + len(nm)))
                    i += len(nm)
                    break
            else:
                i += 1
        else:
            i += 1
    return hits


def tag_stocks(title, content, source=""):
    """对标题+正文做名称抽取 → (codes, conf)。

    conf: 'high' 标题命中 / 'low' 仅正文命中 / None 无命中。
    研报署名剔除：source 以 report/survey 开头时，标题中的「·XX证券」段从候选剔除。
    """
    idx = _load_index()
    title = str(title or "")
    content = str(content or "")[:CONTENT_SCAN]

    sign_set = set()
    if str(source or "").lower().startswith(("report", "survey")):
        sign_set = set(BROKER_SIGN_RE.findall(title))

    taken = []
    t_hits = _scan(title, idx, taken)
    t_hits = [(nm, ts) for nm, ts in t_hits if nm not in sign_set]
    b_hits = _scan(content, idx, taken)                      # taken 共享 → 标题已占位的不再重复命中
    seen, codes = set(), []
    for _nm, ts in t_hits:
        if ts not in seen:
            seen.add(ts)
            codes.append(ts)
    for _nm, ts in b_hits:
        if ts not in seen:
            seen.add(ts)
            codes.append(ts)
    if not codes:
        return [], None
    return codes, ("high" if t_hits else "low")


if __name__ == "__main__":
    # 自检样例（不写库）
    cases = [
        ("【人形机器人板块持续拉升，兆威机电触及涨停】人形机器人板块持续拉升", "", "sina", (["003021.SZ"], "high")),
        ("OpenAI面临社交媒体式安全诉讼浪潮，机器人产业遭殃", "", "eastmoney", ([], None)),
        ("美国房地产ETF跌1.14%，农产品期货走弱", "", "wallstreetcn", ([], None)),
        ("[个股研报·华鑫证券] 公司事件点评：投资太初集成电路", "华鑫证券分析师范益进", "report", ([], None)),
        ("锂电产业链赴港上市热度不减 璞泰来、新宙邦同日获证监会H股备案", "璞泰来与新宙邦同日获备案", "eastmoney", (["603659.SH", "300037.SZ"], "high")),
        ("今天市场普涨", "盘面上宁德时代大涨，拖动创业板指", "sina", (["300750.SZ"], "low")),
    ]
    ok = 0
    for title, content, source, want in cases:
        got = tag_stocks(title, content, source)
        flag = "OK " if got == want else "FAIL"
        ok += (got == want)
        print("%s %-30s -> %s" % (flag, title[:30], got))
    print("selftest %d/%d" % (ok, len(cases)))
