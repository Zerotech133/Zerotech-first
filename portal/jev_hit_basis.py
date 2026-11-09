# -*- coding: utf-8 -*-
"""判定命中溯源与准入校验 —— 「这条新闻凭什么算这只股票的事件」的答案层。

为什么要有这个文件
------------------
2026-11-08 老田实测发现两条荒唐记录：

    标题「2026中原农谷种业大会在河南启幕 聚焦国际育种合作」（正文 64 字，
          讲河南新乡种业大会）
    jev = {"companies": ["300750"], "anchor": {"type": "stock",
           "code": "300750"}, "bias": "bull", "conf": 0.8,
           "reason": "种业大会促国际合作，利好生物育种龙头股价短期上行"}

    —— 正文里没有「宁德时代」「宁德」「300750」「电池」任何一个词。
    同一条新闻的另一个副本（cns:finance:...）companies 却是 []。

根因是三段全断：
  ① 判定层 news_ai.py 的 prompt 只写「companies 只填明确关联的A股代码」，
     没给名录、没给校验，9b 模型凭训练集高频代码自由发挥 → 幻觉；
  ② 聚合层 api_jev_story.py / api_jev_target.py 用
     `jev LIKE '%"companies": ["300750%'` 直接采信，零校验；
  ③ 展示层没有任何「命中依据」字段可以摆给用户看，于是只剩一句
     「local / conf 0.8」——用户自然要问「命中点在哪」。

本模块只做两件事，不改历史数据、不重跑 13.7 万条判定（那要几天）：
  · verify()  给定标题/正文/代码/判定，反查「文本里到底有没有出现这只票」，
              给出可展示的命中词、缺失项与人话裁决；
  · rank()    把裁决折成可排序的采信等级，读取侧据此过滤/降权。

裁决四档（刻意做成可解释，不用模型，纯字符串面）：
  text_confirmed  标题或正文出现公司全名/简称/代码 → 强支撑，采信
  industry_only   只出现所属行业词（含同义词）→ 板块级关联，保留但标注
  code_only       只有 jev.companies 里有代码，文本零支撑 → 「无文本支撑的
                  代码绑定」，默认剔除（种业大会那条就是这一档）
  none            连代码都没有 → 不相关

用词一律取自 stock_basic（5922 只，0.34s 全量读，进程内缓存 600s），
不引第三方词典，避免「凭感觉认行业」。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CACHE_TTL_S = 600
_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "by_code": {}, "names": []}

# 行业同义词：stock_basic 的 industry 与新闻里常用的行业表述并不一致
# （宁德时代 industry=电气设备，新闻常写「电力设备」「电池」「新能源」）。
# 只做「同一产业环节」的收敛，不做跨行业联想——宁可少认，不可错认。
_INDUSTRY_SYNONYMS = {
    "电气设备": ("电力设备", "电池", "动力电池", "储能", "新能源", "锂电"),
    "电力设备": ("电气设备", "电池", "储能", "新能源", "锂电"),
    "半导体": ("芯片", "集成电路", "晶圆", "光刻"),
    "元器件": ("电子元件", "被动元件", "PCB"),
    "化学制药": ("创新药", "医药", "制药", "仿制药"),
    "医疗保健": ("医疗器械", "医疗服务", "医疗信息化", "医药"),
    "软件服务": ("软件", "SaaS", "信创", "云计算"),
    "通信设备": ("运营商", "光模块", "5G", "6G"),
    "汽车整车": ("整车", "新能源车", "乘用车", "商用车"),
    "汽车配件": ("零部件", "汽配", "汽车零部件"),
    "银行": ("商业银行", "城商行", "农商行", "息差"),
    "证券": ("券商", "经纪业务", "投行"),
    "保险": ("寿险", "财险", "保费"),
    "白酒": ("白酒", "酱酒", "酒企"),
    "食品": ("食品饮料", "调味品", "乳制品"),
    "种植业": ("种业", "育种", "粮食", "农业"),
    "农业综合": ("种业", "育种", "粮食", "农业"),
    "小金属": ("稀土", "锂矿", "钴", "镍"),
    "煤炭开采": ("煤炭", "动力煤", "焦煤"),
    "石油开采": ("原油", "石油", "油气"),
    "火力发电": ("火电", "电力", "发电"),
    "水力发电": ("水电", "电力", "发电"),
    "新型电力": ("光伏", "风电", "绿电", "新能源发电"),
    "建筑施工": ("基建", "建筑工程", "施工"),
    "房地产": ("地产", "楼市", "商品房"),
}

# 公司名里可以剥掉的后缀/前缀，剥完若仍有 ≥2 字，作为「简称」参与匹配。
# 例：宁德时代新能源科技股份有限公司 → 宁德时代
_SUFFIX_RE = re.compile(
    r"(股份有限公司|有限责任公司|有限公司|集团|控股|科技|股份|公司)$")
_PREFIX_RE = re.compile(r"^(中国|中|大|新)")


def _dm():
    """延迟导入 datamart（portal 进程里 quant_backtest 已在 path 末尾，前置更稳）。"""
    p = os.path.join(STATION, "quant_backtest")
    if p not in sys.path:
        sys.path.insert(0, p)
    import datamart  # noqa: F401
    return datamart


def _load_registry() -> dict:
    """全 A 名录（代码→{name, industry, area}），600s 进程内缓存。

    失败一律返回空名录：校验层挂了要退化成「不拦」，绝不能把页面搞崩
    —— 宁可漏拦，不可错杀。
    """
    now = time.time()
    with _LOCK:
        if _CACHE["by_code"] and now - _CACHE["at"] < _CACHE_TTL_S:
            return _CACHE["by_code"]
        by_code, names = {}, []
        try:
            sb = _dm().read("stock_basic", limit=8000)
            for r in sb.itertuples():
                code = str(getattr(r, "symbol", "") or "").strip()
                if not re.fullmatch(r"\d{6}", code):
                    continue
                nm = str(getattr(r, "name", "") or "").strip()
                ind = getattr(r, "industry", None)
                ind = "" if ind != ind else str(ind or "")   # NaN 判定
                area = getattr(r, "area", None)
                area = "" if area != area else str(area or "")
                by_code[code] = {"name": nm, "industry": ind, "area": area}
                if nm:
                    names.append((code, nm))
        except Exception:  # noqa: BLE001 —— 名录拿不到就退化为不校验
            by_code, names = {}, []
        _CACHE.update(at=now, by_code=by_code, names=names)
        return by_code


def name_of(code6: str) -> str:
    """代码 → 公司名（取不到返回空串）。"""
    return (_load_registry().get(code6) or {}).get("name") or ""


def industry_of(code6: str) -> str:
    return (_load_registry().get(code6) or {}).get("industry") or ""


def _short_names(name: str) -> list:
    """由全名派生可匹配的简称候选（保守：最多 2 个，长度 ≥2）。

    宁德时代 → 只保留「宁德时代」本身；「宁德」太泛（宁德是地名/还有宁德
    其他公司），不自动放出去，避免「宁德」撞「宁德时代」以外的标的。
    """
    if not name:
        return []
    out = []
    core = _SUFFIX_RE.sub("", name).strip()
    if 2 <= len(core) <= 8 and core != name:
        out.append(core)
    # 去掉「ST/*ST」前缀后再试一次
    plain = re.sub(r"^\*?ST", "", name).strip()
    if plain != name and 2 <= len(plain) <= 8:
        out.append(plain)
    seen, uniq = set(), []
    for x in out:
        if x and x not in seen and x != name:
            seen.add(x)
            uniq.append(x)
    return uniq[:2]


def _industry_terms(industry: str) -> list:
    """该股票所属行业的「新闻用词」候选集（含自身）。"""
    if not industry:
        return []
    keys = [industry]
    for k, vs in _INDUSTRY_SYNONYMS.items():
        if industry == k or industry in vs:
            keys.extend(vs)
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def verify(title: str, content: str, code6: str, jev: dict = None,
           name: str = None) -> dict:
    """反查文本支撑，返回可展示的命中依据。

    参数：
      title/content  该条新闻原文（content 可只传前 800 字，够用）
      code6          6 位代码
      jev            该条判定 JSON（从中读 companies/stocks）
      name           显式公司名（不走名录时的兜底；None 则查 stock_basic）

    返回：
      {
        "verdict": "text_confirmed|industry_only|code_only|none",
        "keep": bool,              # 读取侧是否采信
        "level": 3|2|1|0,          # 排序用（3 最强）
        "in_code": bool,           # jev 里是否绑定了该代码
        "terms": {"name": [...], "short": [...], "code": [...],
                  "industry": [...]},   # 各自命中的具体词
        "hit_text": "...",         # 命中片段（便于界面上直接展示）
        "why": "...",              # 一句人话裁决理由
      }
    """
    code6 = (code6 or "").strip()
    text = (title or "") + "\n" + (content or "")
    text = text[:4000]
    j = jev or {}

    comps = [str(x).strip() for x in (j.get("companies") or []) if str(x).strip()]
    stks = [str(x).strip() for x in (j.get("stocks") or []) if str(x).strip()]
    in_code = code6 in comps or code6 in stks or any(
        code6 in s for s in stks if s)

    nm = name or name_of(code6)
    t_name = [nm] if (nm and nm in text) else []
    t_short = [s for s in _short_names(nm) if s in text] if nm else []
    t_code = [code6] if code6 and code6 in text else []
    t_ind = [w for w in _industry_terms(industry_of(code6)) if w and w in text]

    # 命中片段：优先名称，其次代码，其次行业词
    hit_word = (t_name or t_short or t_code or t_ind or [""])[0]
    hit_text = ""
    if hit_word:
        i = text.find(hit_word)
        if i >= 0:
            hit_text = text[max(0, i - 18):i + len(hit_word) + 18].replace("\n", " ")

    if t_name or t_short:
        verdict, keep, level = "text_confirmed", True, 3
        why = "标题/正文出现「%s」，文本确证" % (t_name or t_short)[0]
    elif t_code:
        verdict, keep, level = "text_confirmed", True, 3
        why = "标题/正文直接出现代码 %s" % code6
    elif t_ind:
        verdict, keep, level = "industry_only", True, 2
        why = ("文本未提「%s」，但出现其所属行业「%s」的用词「%s」→ 属板块级"
               "关联，不是个股事件" % (nm or code6, industry_of(code6), t_ind[0]))
    elif in_code:
        verdict, keep, level = "code_only", False, 1
        why = ("jev.companies 绑定了 %s，但标题与正文未出现「%s」、代码或所属"
               "行业词 → 无文本支撑的代码绑定（疑似模型幻觉），不予采信"
               % (code6, nm or code6))
    else:
        verdict, keep, level = "none", False, 0
        why = "文本与判定均无该标的痕迹"

    return {
        "verdict": verdict, "keep": keep, "level": level,
        "in_code": in_code,
        "name": nm, "industry": industry_of(code6),
        "terms": {"name": t_name, "short": t_short, "code": t_code,
                  "industry": t_ind},
        "hit_text": hit_text,
        "why": why,
    }


def rank(verdict: str) -> int:
    """裁决 → 采信排序权重（历史数据无法重判，只能按等级排/降权）。"""
    return {"text_confirmed": 3, "industry_only": 2,
            "code_only": 1, "none": 0}.get(verdict, 0)


def audit(code6: str, since: str, limit: int = 4000) -> dict:
    """体检：该股窗口内全部命中判定，按裁决分档统计 + 列出幻觉样本。

    给「为什么这只票的事件这么脏」一个可复算的账，也用于回归测试。
    """
    import json
    import sqlite3

    db_path = os.path.join(STATION, "news_collector", "news.db")
    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                          uri=True, timeout=10)
    try:
        rows = con.execute(
            "SELECT id, ts, source, title, content, jev FROM news "
            "WHERE jev IS NOT NULL AND jev!='' AND ts>=? "
            "AND (jev LIKE ? OR stocks LIKE ?) ORDER BY ts DESC LIMIT ?",
            (since, '%"companies": ["%' + code6 + '%', '%' + code6 + '%',
             limit)).fetchall()
    finally:
        con.close()

    stat = {"text_confirmed": 0, "industry_only": 0, "code_only": 0, "none": 0}
    suspects = []
    for nid, ts, source, title, content, jev_text in rows:
        try:
            j = json.loads(jev_text or "{}")
        except Exception:  # noqa: BLE001
            j = {}
        r = verify(title or "", content or "", code6, j)
        stat[r["verdict"]] = stat.get(r["verdict"], 0) + 1
        if r["verdict"] == "code_only" and len(suspects) < 40:
            suspects.append({"id": nid, "ts": ts, "source": source,
                             "title": (title or "")[:70],
                             "reason": str(j.get("reason") or "")[:60],
                             "why": r["why"]})
    total = sum(stat.values())
    return {"code": code6, "since": since, "total": total, "stat": stat,
            "keep_rate": round(
                (stat["text_confirmed"] + stat["industry_only"]) / total, 3
            ) if total else None,
            "suspects": suspects}
