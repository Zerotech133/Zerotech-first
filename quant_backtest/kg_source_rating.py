# -*- coding: utf-8 -*-
"""G7 来源可靠性评级（Admiralty 式，2026-09-30）。

NATO Admiralty 双维体系的本项目落地：
· 源级 reliability A-F（静态人工定档 + 实证依据）——评级对象=数据源族
  （datamart catalog 的 source 值 + 图内边/节点 source 值归并）
· 实例级 credibility 1-6（映射规则，可由 events.jsonl 的 reason+conf 推导，
  不回写——需要时现场算，judge_credibility()）
· 动态 staleness（配合远端断流教训）：catalog freshness 按 freq 分档预期
  节奏，超期=断流可见。季报表（freshness=20260630）属正常节奏不算断流。

输出 data/kg_source_rating.json；kg_check 第⑦项 source_fresh 消费本表
的 stale_tables()。评级依据全部来自项目实证（G 系交付记录），改档须改
依据注释（人工定档纪律：档随证据走）。

用法：
  python kg_source_rating.py           # 跑评级 + staleness，落盘并打印
  python kg_source_rating.py --json    # 仅打印 JSON
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CATALOG = os.path.join(HERE, "data", "catalog.json")
OUT = os.path.join(HERE, "data", "kg_source_rating.json")

# ── 源级评级表（Admiralty A-F）─────────────────────────────
# A 完全可靠 / B 通常可靠 / C 相当可靠 / D 不常可靠 / E 不可靠 / F 无法判断
# feeds=该族在 catalog 的 source 值；graph_sources=图内 source 属性常见值归并
SOURCES = {
    "tushare_pro": {
        "grade": "B", "feeds": ["tushare"],
        "graph_sources": ["stock_company", "stk_managers", "anns_d",
                          "index_global+ts_index_daily", "tushare.index_member"],
        "basis": ("官方付费接口主通道；G5 实证 47 条 begin/end 时序倒挂、"
                  "部分高管 end_date 缺失；断流有 pull_paged 重试+快照幂等")},
    "xtcloud_local": {
        "grade": "B", "feeds": ["xtcloud"],
        "graph_sources": ["ts_top_inst", "ts_daily", "ts_index_daily",
                          "ts_balancesheet", "ts_fina_indicator"],
        "basis": ("本地通达信落地（行情/龙虎榜/财务镜像），稳定可控；"
                  "席位口径含特殊通道行（G4b：机构专用/中小投资者须子串排除）")},
    "crypto_feed": {
        "grade": "C", "feeds": ["crypto"],
        "graph_sources": [],
        "basis": "单一辅助源（crypto_daily），无交叉验证对象"},
    "human_curation": {
        "grade": "A", "feeds": [],
        "graph_sources": ["youzi_registry", "manual", "m4_alias",
                          "m4_industry", "concept_dict"],
        "basis": "人工审核入库（游资词表/别名/行业映射），口径单一可控"},
    "rule_keyword": {
        "grade": "B", "feeds": [],
        "graph_sources": ["semantica_events"],
        "basis": ("event_graph RULES 14 组词表判偏——确定性 100%（同输入同输出）"
                  "但召回有限；G6 互检实证误命中 ~7%（质押式回购≠回购股份）")},
    "llm_local": {
        "grade": "C", "feeds": [],
        "graph_sources": [],
        "basis": ("G6 qwen3 本地受控分类——一致性互检反转 0.2%、误判可闭环修复"
                  "（质押购回 7 条）；conf 为模型自报非客观校准")},
    "jev_feed": {
        "grade": "D", "feeds": [],
        "graph_sources": [],
        "basis": ("远端 LLM 判偏 feed（news.db）——远端断流教训、无 SLA、"
                  "恢复靠水位续读；判定质量未经系统性互检")},
    "cooccur_stat": {
        "grade": "C", "feeds": [],
        "graph_sources": ["cooccur_lag10"],
        "basis": ("M2 DRIVE 共现统计——设计即声明只算共现不判因果；"
                  "p 值随覆盖期自动修正，短覆盖期系统性偏高")},
    "chanlun_engine": {
        "grade": "B", "feeds": [],
        "graph_sources": ["chanlun_pool"],
        "basis": "本地缠论引擎结构识别，确定性计算；口径阈值可调（人为参数面）"},
    "candle_local": {
        "grade": "B", "feeds": [],
        "graph_sources": ["stk_mins"],
        "basis": "本地分钟线盘口事件（熔断/火箭发射等），确定性规则计算"},
}

# ── 实例级 credibility 映射（1-6，Admiralty 信息可信度）─────
CREDIBILITY_MAP = {
    "1": "双源同判交叉确认（jev 判定 + 规则命中同 bias）",
    "2": "规则词表命中（确定性）或 LLM conf>=0.9 或官方结构化披露",
    "3": "LLM 0.7<=conf<0.9（G6b 回填下限档）",
    "4": "LLM fail 兜底 / conf<0.7（G6b 不回填，保持未判）",
    "5": "与已确认信息矛盾（本项目暂无自动交叉，人工复核时使用）",
    "6": "未判（bias=None，notice 基线组）",
}

# ── staleness 分档预期（自然日容忍；季/月频表不算断流）──────
# date_col=None 的定义表豁免；fresh=None 且有 date_col → 无数据（stale）
STALE_DAYS = {"D": 5, "M": 45, "Q": 130, "Y": 400}

# 表名语义覆盖档（catalog 的 freq 字段全标 'D' 不可靠，首跑实证）：
# 子串匹配（如 ts_balancesheet 含 balancesheet → Q）；EXEMPT=豁免
# （快照/探针/事实表：断流几无影响，或 catalog 无法测 fresh 的大表）
FREQ_OVERRIDE = [
    ("balancesheet", "Q"), ("cashflow", "Q"), ("income", "Q"),
    ("fina_indicator", "Q"),                    # 季度财务
    ("index_weight", "Q"),                      # 指数权重季度调整
    ("irm_qa", "Q"),                            # 互动易低频源
    ("top10", "Q"),                             # K100：十大股东按季披露
                                                # （p_<报告期> 分片，Q3 空
                                                # 壳=未披露非断流，实证
                                                # fresh=20260831 lag34 误报）
    ("sf_month", "M"), ("shibor_lpr", "M"),     # 月频发布
    ("probe", "EXEMPT"),                        # 探针表
    ("basic", "EXEMPT"),                        # 快照/事实表（stock_basic 等）
    ("tdx_sector_min", "EXEMPT"),               # 32M 行大表 catalog 测不了 fresh
    ("member", "EXEMPT"), ("def", "EXEMPT"),    # 定义/成分表
    ("ths_index", "EXEMPT"),                    # 概念指数列表快照
    ("stk_managers", "EXEMPT"),                 # K104：高管快照表（周期全量
                                                # 重拉非日增，0929=最近快照日，
                                                # D 档误报 lag6）
    ("us_", "EXEMPT"),                          # 美股系未在 KG/打分消费链（启用时移除）
    ("moneyflow_hsgt", "D"),                    # K103：精确条目前置——hsgt 是
                                                # 日频表（远端 xtcloud 国庆断供停
                                                # 0929 实证），被下行 ("moneyflow","M")
                                                # 子串误盖 budget=45 → 断供 45 天
                                                # 不可见；前置匹配恢复 D 档
    ("moneyflow", "M"),                         # 深表月度拉取策略（tdx_sync SKIP_DEFAULT）
    ("report", "M"),                            # 研报源低频发布
    ("adj_factor", "EXEMPT"),                   # K95：复权因子深历史预留（ts_daily_deep
                                                # 成对回填），无日更消费方——真源在
                                                # xtcloud ts_daily；启用复权链时移除
]


def _budget_for(table, freq):
    """表 → (budget_days, basis)；EXEMPT → (None, ...)。"""
    for kw, f in FREQ_OVERRIDE:
        if kw in table:
            return (None, "exempt:%s" % kw) if f == "EXEMPT" \
                else (STALE_DAYS[f], "override:%s" % f)
    return STALE_DAYS.get(str(freq or "Q"), STALE_DAYS["Q"]), "freq:%s" % freq


def _days_since(fresh):
    """freshness（YYYYMMDD 或 YYYYMM）→ 距今天数；月格式取月末近似。"""
    f = str(fresh or "")
    try:
        if len(f) == 8:
            d = datetime.strptime(f, "%Y%m%d")
        elif len(f) == 6:
            y, m = int(f[:4]), int(f[4:6]) + 1
            if m == 13:
                y, m = y + 1, 1
            d = datetime(y, m, 1)
        else:
            return None
        return (datetime.now() - d).days
    except ValueError:
        return None


def stale_tables():
    """catalog → 超期表列表 [{table, source, freshness, lag_days, budget, note}]。

    budget=该表语义档的容忍天数；lag>budget → 断流候选。
    deprecated/无日期维度/语义豁免表跳过。
    """
    if not os.path.isfile(CATALOG):
        return []
    with open(CATALOG, encoding="utf-8-sig") as f:
        _cat = json.load(f)
    cat = _cat.get("tables") or {}
    # K95：resolves 从表豁免——别名（如 daily→ts_daily 真源 + tushare 深历史）
    # 的从表设计内不日更（深历史止于真源起点），按日更预算检查恒误报
    secondary = set()
    for al in (_cat.get("aliases") or {}).values():
        rs = al.get("resolves") or []
        if al.get("primary"):
            secondary.update(t for t in rs if t != al["primary"])
    out = []
    for t, m in cat.items():
        if not isinstance(m, dict) or m.get("deprecated"):
            continue
        if t in secondary:
            continue  # resolves 从表：断流由 primary 真源表把关
        if not m.get("date_col"):
            continue  # 定义表无日期维度，豁免
        budget, basis = _budget_for(t, m.get("freq"))
        if budget is None:
            continue  # 语义豁免（快照/探针/大表盲区）
        fresh = m.get("freshness")
        if not fresh:
            # K100：rows 与 files 双兜底——原仅查 rows，而 by_code 系表
            # manifest 多无 rows 记录，fresh=None（空壳失明/真断流）被误判
            # 「未启用空表」静默跳过（实证 7 表失明零告警）。files>0 即
            # 报「有数据但测不到日期」，失明必须可见
            if not (m.get("rows") or 0) and not (m.get("files") or 0):
                continue  # rows=0 且 files=0：未启用的空表，非断流
            out.append({"table": t, "source": m.get("source"),
                        "freshness": None, "lag_days": None,
                        "budget": 0, "note": "有数据但测不到日期"})
            continue
        lag = _days_since(fresh)
        if lag is None:
            continue
        if lag > budget:
            out.append({"table": t, "source": m.get("source"),
                        "freshness": fresh, "lag_days": lag,
                        "budget": budget, "note": basis})
    return out


def judge_credibility(reason, conf):
    """events.jsonl 行 → Admiralty credibility 1-6（映射见 CREDIBILITY_MAP）。

    reason 形态：'规则命中:xxx'（rule）/ 'llm:model'（G6b 回填）/ ''（未判）。
    双源同判（jev+规则）在 event_graph 侧由跨源去重合并体现，此处判 2。
    """
    if reason and reason.startswith("规则命中"):
        return 2
    if reason and reason.startswith("llm:"):
        return 2 if (conf or 0) >= 0.9 else 3
    if reason:                      # jev 等其他判定路径
        return 3
    return 6


def build():
    stale = stale_tables()
    # 元检查：catalog 自身过期（首跑实证——catalog freshness 停在 0924 时
    # 把实到 0928 的 ts_top_inst 误报断流，重建 catalog 后消失）
    cat_age = None
    try:
        with open(CATALOG, encoding="utf-8-sig") as f:
            upd = json.load(f).get("updated") or ""
        if upd:
            cat_age = _days_since(upd.replace("-", "")[:8])
    except Exception:
        cat_age = None
    grade_of_feed = {}
    for fam, m in SOURCES.items():
        for f in m["feeds"]:
            grade_of_feed[f] = (fam, m["grade"])
    # 按源族聚合 staleness（族内任一表超期 → 族告警但不降档：档随证据走，
    # 断流是状态不是属性，恢复即消失）
    fam_stale = {}
    for r in stale:
        fam = grade_of_feed.get(r["source"], (r["source"], "?"))[0]
        fam_stale.setdefault(fam, []).append(r["table"])
    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "admiralty": "reliability A-F(源级,人工定档+实证依据) × "
                     "credibility 1-6(实例级,reason+conf 可推导)",
        "sources": {fam: {"grade": m["grade"], "basis": m["basis"],
                          "feeds": m["feeds"],
                          "graph_sources": m["graph_sources"]}
                    for fam, m in SOURCES.items()},
        "credibility_map": CREDIBILITY_MAP,
        "staleness": {
            "budget_days": STALE_DAYS,
            "stale_tables": stale,
            "stale_by_family": fam_stale,
            "catalog_age_days": cat_age,
            "catalog_stale": bool(cat_age is not None and cat_age > 1),
            "note": "断流是状态非属性：告警不降档，恢复即消；降档须新实证改依据",
        },
        "ok": not stale and not (cat_age is not None and cat_age > 1),
    }
    return out


def save(out):
    d = os.path.dirname(OUT)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="仅打印 JSON")
    a = ap.parse_args()
    out = build()
    save(out)
    if a.json:
        slim = dict(out)
        slim.pop("credibility_map", None)
        print(json.dumps(slim, ensure_ascii=False, indent=1))
        return 0
    print("=== G7 来源可靠性评级（Admiralty 式）ts=%s ===" % out["ts"])
    for fam, m in out["sources"].items():
        st = out["staleness"]["stale_by_family"].get(fam)
        if st:
            more = " +%d" % (len(st) - 4) if len(st) > 4 else ""
            st = " !stale:%s%s" % (",".join(st[:4]), more)
        else:
            st = ""
        print("  [%s] %-16s %s%s" % (m["grade"], fam,
                                     m["basis"][:44], st))
    print("  staleness: %d 表超期 %s" % (
        len(out["staleness"]["stale_tables"]),
        "(ok)" if out["ok"] else
        str([r["table"] for r in out["staleness"]["stale_tables"]])))
    print("落盘 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
