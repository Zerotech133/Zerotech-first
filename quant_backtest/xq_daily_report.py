#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雪球情报日报生成器（两线预案 M-RT3 消费端，2026-10-18）

双口径设计（数据源只读，零写回）：
  战术情报口径 = xq_status.created_at 落在当日   → 当日新帖（18:00 生产轮产出）
  管道健康口径 = xq_status.fetched_at 落在当日   → 当日采集入库（含播种/回填）

产出（产物统一根 C:/aios/automation_runs/xq_report/）：
  daily/xq_intel_YYYYMMDD.md / .json   当日日报（人读 + 机读）
  latest.md / latest.json              固定名副本（门户/A0A 消费锚点）
  report_status.log                    ASCII 状态日志

退出码：0 正常（含空窗口日）；2 数据库异常。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import jieba

DB_RO = r"file:C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db?mode=ro"
OUT_ROOT = r"C:/aios/automation_runs/xq_report"
RE_CODE = re.compile(r"^\d{6}$")   # A股/ETF 场内代码
RE_HK = re.compile(r"^\d{5}$")     # 港股代码（5 位）
SIG_EVENT = ("个股研判", "交易策略", "行情观点")   # 高置信信号关注的事件类
BEARISH_MIN = 3          # 异动信号：bearish 绝对数阈值
BEARISH_SHARE = 0.6      # 异动信号：bearish 占比阈值（样本 >= BEARISH_MIN）
CONF_FLOOR = 0.8         # 高置信信号阈值
# 多空标题分词停用词表：只过滤完整停用词，不按单字过滤。
STOP_WORDS = {"它们", "更多", "还能", "可以", "没有", "这个",
              "那个", "自己", "不是", "什么", "如何", "怎么",
              "觉得", "认为", "感觉", "表示", "出现", "目前", "当前",
              "现在", "已经", "可能", "相关", "情况", "方面"}


def load_rows(con: sqlite3.Connection, day: str):
    """拉当日窗口联表行：tactical=created_at 口径，ingest=fetched_at 口径"""
    q = ("SELECT s.status_id, s.created_at, s.fetched_at, s.title, s.desc, s.category,"
         "       c.event_type, c.sentiment, c.tickers, c.confidence, c.parse_ok,"
         "       c.model, c.prompt_version, c.content_type"
         "  FROM xq_status s LEFT JOIN xq_status_classified c"
         "    ON s.status_id = c.status_id")
    lo, hi = day + " 00:00:00", day + " 23:59:59"
    tac = con.execute(q + " WHERE s.created_at BETWEEN ? AND ?", (lo, hi)).fetchall()
    ing = con.execute(q + " WHERE s.fetched_at BETWEEN ? AND ?", (lo, hi)).fetchall()
    return tac, ing


def split_tickers(raw: str):
    """tickers JSON → (证券代码 list, 主题词 list)。代码=6位(A股/ETF)或5位(港股)纯数字"""
    codes, themes = [], []
    try:
        arr = json.loads(raw) if raw and raw != "[]" else []
    except Exception:
        return codes, themes
    for t in arr:
        t = str(t).strip()
        if not t:
            continue
        is_code = bool(RE_CODE.match(t) or RE_HK.match(t))
        (codes if is_code else themes).append(t)
    return codes, themes


_NORM_FN = None
_NORM_TRIED = False


def _norm_kind(code: str):
    """单 code → kind（stock_hk/stock_a/index/fund/unknown）。

    懒加载 tools/xq_ticker_normalize（kind 语义唯一权威）；加载失败返回 None
    = 回退现状行为（榜单不分流），不影响报告主产出。
    """
    global _NORM_FN, _NORM_TRIED
    if not _NORM_TRIED:
        _NORM_TRIED = True
        try:
            _tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
            if _tools not in sys.path:
                sys.path.insert(0, _tools)
            from xq_ticker_normalize import normalize_tickers as _f
            _NORM_FN = _f
        except Exception:
            _NORM_FN = None
    if _NORM_FN is None:
        return None
    try:
        items = _NORM_FN(json.dumps([str(code)]))
        return items[0].get("kind") if items else None
    except Exception:
        return None


def title_words(title: str):
    """中文标题分词；过滤停用词/单字/纯数字标点，并返回帖内去重词集合。"""
    out = set()
    for word in jieba.lcut(str(title or "")):
        word = word.strip()
        if (len(word) >= 2 and word not in STOP_WORDS
                and not re.fullmatch(r"[\dA-Za-z%.:：,，！!？?]+", word)):
            out.add(word)
    return out


def summarize(rows):
    """对窗口行做全维度汇总（未分类行 event_type=None 自动进 pending 桶）"""
    ev, senti, codes, themes = Counter(), Counter(), Counter(), Counter()
    concepts = Counter()   # (code, kind) → n；kind∈(index,fund,unknown) 的概念榜（fund=kind 标记上榜，2026-10-19 拍板）
    ctype = Counter()      # v1.2 内容形态（tech/community/news/fundamental）
    pending, parse_bad = 0, 0
    sig_rows = []          # (conf, code, senti, event, title)
    ticker_senti = defaultdict(Counter)   # code -> Counter(senti)（仅个股，概念词不进信号面）
    lineages = Counter()
    words = {"bearish": Counter(), "bullish": Counter()}   # 多空标题词帖子覆盖数（排除 ad/edu）
    for sid, _cat, _ft, title, _desc, _tg, et, sn, tk, conf, pok, model, pv, ct in rows:
        if et is None:
            pending += 1
            continue
        if not pok:
            parse_bad += 1
            continue
        ev[et] += 1
        lineages["%s|%s" % (model, pv)] += 1
        ctype[ct or "(待补)"] += 1
        if sn in words and ct not in ("ad", "edu"):
            for w in title_words(title):
                words[sn][w] += 1
        if sn:
            senti[sn] += 1
        c, th = split_tickers(tk or "")
        for x in c:
            k = _norm_kind(x)
            if k is None:
                # normalize 不可用 → 回退现状（全部进个股榜，行为与 v1.6 一致）
                codes[x] += 1
                if sn:
                    ticker_senti[x][sn] += 1
            elif k in ("stock_hk", "stock_a"):
                codes[x] += 1
                if sn:
                    ticker_senti[x][sn] += 1
            else:
                # index/fund/unknown → 概念榜（kind 标记，隔离于个股榜与信号面）
                concepts[(x, k or "unknown")] += 1
        for x in th:
            themes[x] += 1
        if sn and c and et in SIG_EVENT and (conf or 0) >= CONF_FLOOR:
            sig_rows.append((conf, c[0], sn, et, (title or "")[:42]))
    sig_rows.sort(reverse=True)
    return dict(ev=ev, senti=senti, codes=codes, themes=themes, pending=pending,
                parse_bad=parse_bad, sig=sig_rows[:12],
                ticker_senti=ticker_senti, lineages=lineages, ctype=ctype,
                concepts=concepts,
                bear_words=words["bearish"], bull_words=words["bullish"])


def anomaly_signals(summ, con, day):
    """异动侦测：当日 bearish 突增/高占比，对比前 6 日同 ticker 基线"""
    base_lo = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    q = ("SELECT c.tickers FROM xq_status_classified c JOIN xq_status s ON s.status_id = c.status_id"
         " WHERE c.parse_ok=1 AND c.sentiment='bearish' AND c.tickers IS NOT NULL AND c.tickers != '[]'"
         "   AND s.created_at >= ? AND s.created_at < ?")
    base = Counter()
    for (tk,) in con.execute(q, (base_lo + " 00:00:00", day + " 00:00:00")):
        for x in split_tickers(tk)[0]:
            base[x] += 1
    out = []
    for code, cnt in summ["ticker_senti"].items():
        bear = cnt.get("bearish", 0)
        n = sum(cnt.values())
        if bear >= BEARISH_MIN and bear / n >= BEARISH_SHARE:
            out.append((code, n, bear, base.get(code, 0)))
    out.sort(key=lambda r: (-r[2], r[0]))
    return out


def word_tempo(con, day, today_words=None, topn=8):
    """词覆盖温度计：当日多空标题词 vs 前 6 日基线的突增检测。

    today_words 可传 summarize() 已算好的 {"bearish": Counter, "bullish": Counter}
    以复用避免二次扫描；不传则由窗口查询现算（仅单测/独立调用场景）。
    返回 [(sentiment, word, today_n, base_avg, ratio)]：
      基线 = 前 6 日（day-6 ~ day-1，不含当日）合并词频的日均；
      ratio = today_n / max(base_avg, 0.5)（平滑，新词 base_avg=0 时 ratio 极大属预期）；
    只保留 today_n>=2 且 ratio>=2 的「新词/升温词」，按 ratio 降序，每边最多 topn 条。
    """
    base_lo = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    q_sel = ("SELECT c.sentiment, s.title FROM xq_status s JOIN xq_status_classified c"
             "   ON s.status_id = c.status_id"
             " WHERE c.parse_ok=1 AND c.sentiment IN ('bearish','bullish')"
             "   AND (c.content_type IS NULL OR c.content_type NOT IN ('ad','edu'))")
    # 基线窗口：day-6 00:00 ~ day 00:00（左闭右开，不含当日）
    q_base = q_sel + "   AND s.created_at >= ? AND s.created_at < ?"
    # 当日窗口：day 00:00 ~ day 23:59（独立条件，勿叠加基线右边界）
    q_today = q_sel + "   AND s.created_at BETWEEN ? AND ?"
    base = {"bearish": Counter(), "bullish": Counter()}
    for sn, title in con.execute(q_base, (base_lo + " 00:00:00", day + " 00:00:00")):
        for w in title_words(title):
            base[sn][w] += 1
    if today_words is None:
        today_words = {"bearish": Counter(), "bullish": Counter()}
        for sn, title in con.execute(q_today, (day + " 00:00:00", day + " 23:59:59")):
            for w in title_words(title):
                today_words[sn][w] += 1
    out = []
    for sn in ("bearish", "bullish"):
        rows = []
        for w, n in today_words[sn].items():
            if n < 2:
                continue
            b = base[sn][w] / 6.0
            ratio = n / max(b, 0.5)
            if ratio < 2:
                continue
            rows.append((sn, w, n, b, ratio))
        rows.sort(key=lambda r: (-r[4], r[1]))
        out.extend(rows[:topn])
    return out


def render(day, tac, ing, summ_t, anomalies, tempo=None) -> str:
    n_tac, n_ing = len(tac), len(ing)
    cls_t = n_tac - summ_t["pending"]
    L = []
    L.append("# 雪球情报日报 · %s" % day)
    L.append("")
    L.append("> 生成 %s ｜ 数据源 xueqiu_info.db（只读）｜ 生成器 xq_daily_report.py" %
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("")
    L.append("## 一、总览")
    L.append("")
    L.append("| 口径 | 数量 | 说明 |")
    L.append("|---|---|---|")
    L.append("| 当日采集入库 | %d | fetched_at 口径（含播种/回填，管道健康）|" % n_ing)
    L.append("| 当日新帖 | %d | created_at 口径（战术情报）|" % n_tac)
    L.append("| 其中已打标 | %d | 覆盖率 %s |" %
             (cls_t, "%.0f%%" % (100.0 * cls_t / n_tac) if n_tac else "-"))
    if n_tac == 0:
        L.append("")
        L.append("⚠️ 当日新帖窗口为空——18:00 生产轮（XueqiuCrawl）首跑后才有战术数据，属预期；"
                 "本报告其余段落按当前入库数据渲染。")
    L.append("")
    L.append("## 二、事件分布（当日新帖 · parse_ok）")
    L.append("")
    if summ_t["ev"]:
        for k, v in summ_t["ev"].most_common():
            L.append("- %s：%d" % (k, v))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 三、情绪分布（当日新帖 · parse_ok）")
    L.append("")
    tot = sum(summ_t["senti"].values())
    if tot:
        for k in ("bullish", "neutral", "bearish"):
            v = summ_t["senti"].get(k, 0)
            L.append("- %s：%d（%.0f%%）" % (k, v, 100.0 * v / tot))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 三-B、内容形态分布（当日新帖 · v1.2 分类维度）")
    L.append("")
    CTN = {"tech": "技术面分析", "community": "社区探讨", "news": "个股资讯",
           "fundamental": "基本面逻辑", "edu": "投教科普", "ad": "广告活动", "(待补)": "（待补标）"}
    if summ_t["ctype"]:
        ctot = sum(summ_t["ctype"].values())
        for k, v in summ_t["ctype"].most_common():
            L.append("- %s：%d（%.0f%%）" % (CTN.get(k, k), v, 100.0 * v / ctot))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 三-C、多空标题词帖子覆盖数（排除广告/科普）")
    L.append("")
    L.append("> 统计口径：×N 表示提及该词的帖子数；每篇帖子内同一词只计 1 次。")
    L.append("")
    for label, key in (("bearish", "bear_words"), ("bullish", "bull_words")):
        top = summ_t[key].most_common(8)
        if top:
            L.append("- %s top8：%s" % (label, "、".join("%s×%d" % (w, c) for w, c in top)))
        else:
            L.append("- %s top8：（空）" % label)
    tempo_rows = tempo or []
    for label in ("bearish", "bullish"):
        rows = [r for r in tempo_rows if r[0] == label]
        if rows:
            L.append("- %s 升温词（帖子数）：%s" % (label, "、".join(
                "%s×%d(基线%g/ratio%g)" % (w, n, b, rt) for _sn, w, n, b, rt in rows)))
        else:
            L.append("- %s 升温词（帖子数）：（无突增）" % label)
    L.append("")
    L.append("## 四、热门标的（当日新帖 · 个股代码）")
    L.append("")
    if summ_t["codes"]:
        for k, v in summ_t["codes"].most_common(10):
            L.append("- %s：%d 帖" % (k, v))
    else:
        L.append("（空）")
    if summ_t["themes"]:
        L.append("")
        L.append("主题词 top（基金/指数词，非个股口径）：")
        L.append("")
        for k, v in summ_t["themes"].most_common(6):
            L.append("- %s：%d" % (k, v))
    L.append("")
    L.append("## 五、异动信号（bearish 突增 · 阈值 n>=%d 且占比>=%.0f%%）" % (BEARISH_MIN, BEARISH_SHARE * 100))
    L.append("")
    if anomalies:
        L.append("| 代码 | 当日样本 | bearish | 前6日基线 |")
        L.append("|---|---|---|---|")
        for code, n, bear, b in anomalies[:10]:
            L.append("| %s | %d | %d | %d |" % (code, n, bear, b))
    else:
        L.append("（无——当日无达标标的）")
    L.append("")
    L.append("## 六、高置信信号（confidence>=%.1f · 个股/策略/行情 · 带代码）" % CONF_FLOOR)
    L.append("")
    if summ_t["sig"]:
        L.append("| conf | 代码 | 情绪 | 事件 | 标题 |")
        L.append("|---|---|---|---|---|")
        for conf, code, sn, et, title in summ_t["sig"]:
            L.append("| %.2f | %s | %s | %s | %s |" % (conf, code, sn, et, title.replace("|", "/")))
    else:
        L.append("（无）")
    L.append("")
    L.append("## 七、管道体检")
    L.append("")
    total_all = con_count("SELECT COUNT(*) FROM xq_status")
    cls_all = con_count("SELECT COUNT(*) FROM xq_status_classified")
    L.append("- 库内总量：%d ｜ 已打标：%d ｜ pending：%d" % (total_all, cls_all, total_all - cls_all))
    L.append("- 当日窗口 parse 失败：%d ｜ 未打标：%d" % (summ_t["parse_bad"], summ_t["pending"]))
    if summ_t["lineages"]:
        for k, v in summ_t["lineages"].most_common(2):
            L.append("- 标注 lineage：`%s` × %d" % (k, v))
    if cls_all < total_all:
        L.append("- ⏳ 存量追平轮进行中（probe-then-run 自动错峰），pending=%d 属预期" % (total_all - cls_all))
    L.append("")
    return "\n".join(L)


def con_count(sql: str) -> int:
    con = sqlite3.connect(DB_RO, uri=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球情报日报生成器（M-RT3）")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="报表日（默认今天）")
    a = ap.parse_args()
    day = a.date
    os.makedirs(os.path.join(OUT_ROOT, "daily"), exist_ok=True)

    con = sqlite3.connect(DB_RO, uri=True)
    try:
        tac, ing = load_rows(con, day)
        summ = summarize(tac)
        anomalies = anomaly_signals(summ, con, day)
        tempo = word_tempo(con, day, today_words={"bearish": summ["bear_words"],
                                                  "bullish": summ["bull_words"]})
        md = render(day, tac, ing, summ, anomalies, tempo)

        # 血缘透明化：动态读 xq_classify 既有常量（提取器版本由 hk-ticker 工位维护，
        # 勿硬编码；import 失败兜底 unknown，不影响报告主产出）
        try:
            from xq_classify import _TICKER_VERSION, MODEL as _CLS_MODEL, PROMPT_VERSION as _CLS_PROMPT
        except Exception:
            _TICKER_VERSION = _CLS_MODEL = _CLS_PROMPT = "unknown"

        payload = {
            "date": day,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "extractor_version": _TICKER_VERSION,
            "pipeline": {"llm_model": _CLS_MODEL, "prompt_version": _CLS_PROMPT},
            "ingested_today": len(ing),
            "posts_today": len(tac),
            "classified_today": len(tac) - summ["pending"],
            "event_type": dict(summ["ev"]),
            "sentiment": dict(summ["senti"]),
            "content_type": dict(summ["ctype"]),  # v1.3 六类内容形态分布（门户条形消费）
            "top_codes": dict(summ["codes"].most_common(10)),
            # v1.7 概念榜（fund=kind 标记上榜拍板落地）：指数/基金/unknown 隔离于个股榜
            "top_concepts": [{"code": c, "kind": k, "count": n}
                             for (c, k), n in summ["concepts"].most_common(8)],
            "top_themes": dict(summ["themes"].most_common(6)),
            "anomaly_signals": [{"code": c, "n": n, "bearish": b, "baseline6d": x}
                                for c, n, b, x in anomalies],
            "word_tempo": {"bearish": [], "bullish": []},  # 先占位，下方按情绪边填充
            "high_confidence": [{"confidence": cf, "code": c, "sentiment": s,
                                 "event_type": e, "title": t}
                                for cf, c, s, e, t in summ["sig"]],
        }
        for sn, w, n, b, rt in tempo:
            payload["word_tempo"][sn].append(
                {"word": w, "today": n, "base_avg": round(b, 2), "ratio": round(rt, 2)})
        stem = "xq_intel_%s" % day.replace("-", "")
        dp = os.path.join(OUT_ROOT, "daily", stem + ".md")
        jp = os.path.join(OUT_ROOT, "daily", stem + ".json")
        open(dp, "w", encoding="utf-8").write(md)
        json.dump(payload, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        # latest 仅由"今天"的报告更新：历史日期回填/验证运行不得覆盖消费锚点
        # （2026-10-18 判例：seed 播种后 --date 2020-05-13 验证把 latest 打回历史）
        if day == time.strftime("%Y-%m-%d"):
            for ext, src in ((".md", md), (".json", json.dumps(payload, ensure_ascii=False, indent=1))):
                open(os.path.join(OUT_ROOT, "latest" + ext), "w", encoding="utf-8").write(src)

        with open(os.path.join(OUT_ROOT, "report_status.log"), "a", encoding="ascii",
                  errors="backslashreplace") as f:
            f.write("%s | report done date=%s posts=%d ingested=%d anomalies=%d rc=0\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), day, len(tac), len(ing), len(anomalies)))
        print("[report] date=%s posts_today=%d ingested_today=%d -> %s"
              % (day, len(tac), len(ing), dp))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
