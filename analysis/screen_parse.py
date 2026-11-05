# -*- coding: utf-8 -*-
"""自然语言 → ScreeningDSL 规则解析（H2-2，确定性优先）。

设计（对齐 quant_backtest/nl_backtest.py 先例）：
- 规则解析优先（零延迟、可解释、可单测）；未识别部分记入 unmatched，交 Hermes 侧澄清/LLM 兜底；
- **未消费条件不静默丢弃**：片段含数字/百分比/比较词却未被任何规则消费 → 记入 unmatched 且 ok=False，
  防止「用户以为筛了、实际没筛」的错结果；
- 产出 DSL 必须再过 `analysis.screener.validate` 才可执行；
- 只解析"研究筛选条件"，不生成买卖建议。

用法：
  from analysis.screen_parse import parse
  r = parse("轻工制造里，PS 历史分位低于 50%、营收同比转正、剔除 ST，按质量分排序，最多 20 只")
  # r = {"ok":True,"spec":{...},"notes":[...],"unmatched":[...]}
"""
import re
from typing import Any, Dict, List, Optional

# 申万 L1 常见行业（缺省与 analysis.screener.INDUSTRIES_L1 同源；可被调用方注入 industries 覆盖）
DEFAULT_INDUSTRIES = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售",
    "社会服务", "综合", "建筑材料", "建筑装饰", "电力设备", "国防军工", "计算机", "传媒",
    "通信", "银行", "非银金融", "煤炭", "石油石化", "环保", "美容护理", "机械设备",
]
NUM = r"(\d+(?:\.\d+)?)"
COMPARE_WORDS = ("大于", "小于", "低于", "高于", "超过", "不足", "以上", "以下")
KEYWORDS = ("PS", "营收", "净利", "净利润", "ROE", "资产负债", "ST", "最多", "前", "排序",
            "行业", "观察池", "沪深", "A股", "全A")


def _pct(m):
    return float(m) / 100.0


def _industries(industries: Optional[List[str]]) -> List[str]:
    if industries:
        return industries
    try:
        from . import screener
        return list(getattr(screener, "INDUSTRIES_L1", DEFAULT_INDUSTRIES))
    except Exception:
        return DEFAULT_INDUSTRIES


def parse(text: str, industries: Optional[List[str]] = None) -> Dict[str, Any]:
    t = (text or "").strip()
    specs: List[Dict[str, Any]] = []
    notes: List[str] = []
    unmatched: List[str] = []
    universe = "WATCHLIST"
    universe_explicit = False
    limit = 20
    rank_by = []
    consumed: List[tuple] = []
    ind_list = _industries(industries)

    def take(m):
        """记录规则消费的文本区间（用于未识别片段判定，避免静默丢条件）。"""
        if m:
            consumed.append(m.span())
        return m

    if not t:
        return {"ok": False, "error": "输入为空", "spec": None, "notes": [], "unmatched": []}

    # 股票池（显式指定优先；行业筛选不得覆盖显式股票池）
    if re.search(r"沪深\s*300|hs300", t, re.I):
        universe = "HS300"
        universe_explicit = True
        notes.append("股票池：沪深300")
    elif re.search(r"观察池|watchlist", t, re.I):
        universe = "WATCHLIST"
        universe_explicit = True
        notes.append("股票池：观察池")
    elif re.search(r"全\s*A|A\s*股|全市场|全a", t):
        universe = "A_SHARE"
        universe_explicit = True
        notes.append("股票池：全 A")
    # 行业（行业名 + 里/内/中）：只追加行业过滤，不覆盖显式股票池
    ind_hit = None
    for ind in ind_list:
        if ind and ind in t:
            ind_hit = ind
            break
    if ind_hit:
        specs.append({"field": "industry_l1", "op": "contains", "value": ind_hit})
        notes.append("筛选：行业含 %s" % ind_hit)
        if not universe_explicit:
            universe = "A_SHARE"
            notes.append("股票池：全 A（行业筛选需全市场，未指定股票池）")

    # PS 历史分位
    m = take(re.search(r"PS[^，。;；\n]{0,12}?分位[^0-9]{0,8}?(?:低于|小于|<=|≤)\s*" + NUM + r"\s*%", t, re.I))
    if m:
        specs.append({"field": "ps_pct_hist", "op": "<=", "value": float(m.group(1))})
        notes.append("筛选：PS 历史分位 ≤ %s%%" % m.group(1))

    # 营收同比
    if re.search(r"营收[^，。;；\n]{0,6}?同比[^0-9]{0,4}?转正", t):
        specs.append({"field": "revenue_yoy", "op": ">=", "value": 0.0})
        notes.append("筛选：营收同比 ≥ 0")
    m = take(re.search(r"营收[^，。;；\n]{0,6}?同比[^0-9]{0,6}?(?:大于|>|超过|>=|≥)\s*" + NUM + r"\s*%", t))
    if m:
        specs.append({"field": "revenue_yoy", "op": ">=", "value": _pct(m.group(1))})
        notes.append("筛选：营收同比 ≥ %s%%" % m.group(1))

    # 净利润同比
    if re.search(r"(?:净利润|净利)[^，。;；\n]{0,6}?同比[^0-9]{0,4}?转正", t):
        specs.append({"field": "net_profit_yoy", "op": ">=", "value": 0.0})
        notes.append("筛选：净利同比 ≥ 0")
    m = take(re.search(r"(?:净利润|净利)[^，。;；\n]{0,6}?同比[^0-9]{0,6}?(?:大于|>|超过|>=|≥)\s*" + NUM + r"\s*%", t))
    if m:
        specs.append({"field": "net_profit_yoy", "op": ">=", "value": _pct(m.group(1))})
        notes.append("筛选：净利同比 ≥ %s%%" % m.group(1))

    # ROE
    m = take(re.search(r"ROE[^0-9]{0,8}?(?:大于|>|超过|>=|≥)\s*" + NUM + r"\s*%", t, re.I))
    if m:
        specs.append({"field": "roe", "op": ">=", "value": _pct(m.group(1))})
        notes.append("筛选：ROE ≥ %s%%" % m.group(1))

    # 资产负债率
    m = take(re.search(r"资产负债率[^0-9]{0,8}?(?:低于|小于|<|<=|≤)\s*" + NUM + r"\s*%", t))
    if m:
        specs.append({"field": "debt_ratio", "op": "<=", "value": _pct(m.group(1))})
        notes.append("筛选：资产负债率 ≤ %s%%" % m.group(1))

    # ST 排除
    take(re.search(r"剔除\s*ST|排除\s*ST|去掉\s*ST|非\s*ST", t, re.I))
    if re.search(r"剔除\s*ST|排除\s*ST|去掉\s*ST|非\s*ST", t, re.I):
        specs.append({"field": "flag_st", "op": "=", "value": False})
        notes.append("筛选：剔除 ST")
    # 回撤/涨跌（近 N 日）
    m = take(re.search(r"近\s*(\d{1,3})\s*日(?:跌幅|下跌)[^0-9]{0,6}?(?:超过|大于|>)\s*" + NUM + r"\s*%", t))
    if m:
        n = int(m.group(1))
        field = "ret_20" if n <= 20 else "ret_60"
        specs.append({"field": field, "op": "<=", "value": -_pct(m.group(2))})
        notes.append("筛选：近 %s 日跌幅 > %s%%（按 %s）" % (n, m.group(2), field))

    # 排序
    take(re.search(r"按\s*质量分|质量分\s*排序", t))
    if re.search(r"按\s*质量分|质量分\s*排序", t):
        rank_by = [{"field": "quality_score", "order": "desc"}]
        notes.append("排序：质量分降序")
    else:
        m = take(re.search(r"PS[^，。;；\n]{0,8}?分位[^，。;；\n]{0,8}?(?:升序|从低到高)", t, re.I))
        if m:
            rank_by = [{"field": "ps_pct_hist", "order": "asc"}]
            notes.append("排序：PS 分位升序")

    # 数量
    m = take(re.search(r"(?:最多|前|取前|返回)\s*" + NUM + r"\s*只", t))
    if m:
        limit = max(1, min(int(float(m.group(1))), 200))
        notes.append("数量上限：%d" % limit)

    if not specs:
        unmatched.append("未识别到筛选条件")
    # 未识别片段：按"规则是否消费过该区间"判定；含数字/%/比较词却未被消费 → 阻止执行（ok=False）
    for m in re.finditer(r"[^，。;；,;、\n]+", t):
        frag = m.group(0).strip()
        if not frag:
            continue
        s, e = m.span()
        if any(a < e and s < b for a, b in consumed):
            continue
        has_num = bool(re.search(r"\d|%|" + "|".join(COMPARE_WORDS), frag))
        known = any(k in frag for k in KEYWORDS)
        has_ind = any(ind in frag for ind in ind_list)
        if has_num:
            unmatched.append(frag[:30])
        elif known or has_ind:
            continue
        elif len(frag) >= 2:
            unmatched.append(frag[:30])

    blocking = [u for u in unmatched if re.search(r"\d|%|" + "|".join(COMPARE_WORDS), u)]
    if blocking:
        return {"ok": False,
                "error": "存在未识别的筛选条件：%s（请改写或补充说明，避免漏筛）" % "、".join(blocking),
                "spec": None, "notes": notes, "unmatched": unmatched, "source": "rule", "text": t}

    spec = {
        "universe": universe,
        "filters": specs,
        "rank_by": rank_by,
        "limit": limit,
        "explain": True,
    }
    # 用 screener.validate 二次校验（字段/算子/行业白名单/日期）
    try:
        from . import screener
        v = screener.validate(spec)
        if not v["ok"]:
            return {"ok": False, "error": "DSL 校验失败", "errors": v["errors"],
                    "spec": spec, "notes": notes, "unmatched": unmatched}
        spec = v["spec"]
    except Exception:
        pass
    return {"ok": True, "spec": spec, "notes": notes, "unmatched": unmatched,
            "source": "rule", "text": t}
