# -*- coding: utf-8 -*-
"""
WORDC1 词云席位工具（陈琳）——双源词频对比与共振词挖掘。

数据源（均只读，URI mode=ro，绝不写库）：
  1) 雪球舆情：quant_backtest/data/xueqiu/xueqiu_info.db 的 xq_status 表（title + desc 拼接）
  2) 新闻通讯：news_collector/news.db 的 news 表（title 列）

口径：双源全量对比。
  - xq_status 33,396 条中仅 2,882 条有 created_at（91% 缺失），且近 30 天仅 322 条，
    时间窗过滤会砍掉 91% 样本，故取全量；
  - news ts 覆盖 2026-07-19 ~ 2026-11-04，标题非空率 100%，取全量。

分词：优先 jieba（精确模式），探测不到则降级为 2-gram 字符切分，报告中如实标注。
产出：
  outputs/<日期>_WORDC1词云首产出.html  —— 自包含浅色报告（双源 Top30 条形对比 + 共振词 Top20 表）
  outputs/<日期>_WORDC1_top词表.json    —— {xueqiu:{top50}, news:{top50}, overlap_top20, meta}

用法：
  python wordc1_cloud.py            # 默认产出日期 = 今天
  python wordc1_cloud.py --jev-weighted [--days 90] [--top 30]
                                    # 追加 jev_weighted 段（工单 WO-INTEL-WORDC1-JEV-20261107-001）
                                    # 按 news.jev 的 bias 分桶（bull/bear/neutral + 未判对照桶），
                                    # conf<0.5 判定降权 50%；JSON schema 只增不改
"""
import argparse
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

# ---------- 路径 ----------
BASE = r"C:/项目/老田的AI助手"
XQ_DB = os.path.join(BASE, "quant_backtest/data/xueqiu/xueqiu_info.db")
NEWS_DB = os.path.join(BASE, "news_collector/news.db")
OUT_DIR = os.path.join(BASE, "outputs")
TODAY = date.today().strftime("%Y-%m-%d")
HTML_PATH = os.path.join(OUT_DIR, f"{TODAY}_WORDC1词云首产出.html")
JSON_PATH = os.path.join(OUT_DIR, f"{TODAY}_WORDC1_top词表.json")

# ---------- 停用词（高频虚词 + 单字符另行统一过滤，本表 >= 50 词）----------
STOPWORDS = set("""
的 了 是 在 我 你 他 她 它 我们 你们 他们 她们 它们 这 那 这个 那个 这些 那些
和 与 及 或 但是 但 而且 并 并且 因为 所以 如果 虽然 然而 不过 于是 因此
就 都 也 很 还 要 会 能 可 可以 有 没 没有 无 不 不是 什么 怎么 为什么
一个 一些 一下 一样 一点 没有 我们自己 大家 自己 现在 已经 曾经 将要
着 过 得 地 之 其 此 该 各 每 某 任何 所有 全部 整个
说 说的是 认为 知道 觉得 看到 说到 开始 出来 起来 下去 上去 过去 现在
月 日 年 时 分 号 周 期 天 亿 万 千 百 十 个 只 条 万亿
公告 发布 报道 记者 消息 报道称 消息称 相关 进行 表示 称 为 及 等
""".split())
# 补充：副词/连词/时间/管道类高频词
STOPWORDS |= set("""对于 关于 以上 以下 其中 之后 之前 以后 以前 目前 近日 近期 今日 明日 昨日
没有 可以 就是 还有 或者 但是 然而 据悉 快讯 详情 点击 查看 更多 相关内容""".split())
# 网页残留噪声词（jieba 对英文按 token 切出）
STOPWORDS |= set("""http https www com cn html htm target blank href br amp quot nbsp
index php user status detail""".split())

_RE_TAGS = re.compile(r"<[^>]+>")

# ---------- JEV 加权扩展常量（工单 WO-INTEL-WORDC1-JEV-20261107-001）----------
# 注意：不进 STOPWORDS（停用词口径冻结）；bias 分桶 3 主桶 + 1 未判对照桶。
JEV_BIAS_BUCKETS = ("bull", "bear", "neutral")
JEV_CONF_DOWNWEIGHT = 0.5     # conf<0.5 的判定权重减半，写入 meta 如实上报
JEV_UNJUDGED_SAMPLE_CAP = 1000  # 未判对照桶抽样上限（按 ts 倒序取，确定性）
JEV_MALFORMED_IS_COUNTED = True  # JSON 解析失败行计入 malformed，不静默丢
_RE_URL = re.compile(r"(?:https?://|www\.)\S+")
_RE_ENTITY = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_RE_XQ_REF = re.compile(r"\[[^\]]{1,12}\]")  # 雪球 [表情] [赞] 等引用标记


def clean_text(t):
    """去掉 HTML 标签 / URL / 实体 / 雪球引用标记，供分词前的预处理。"""
    t = _RE_URL.sub(" ", t)
    t = _RE_TAGS.sub(" ", t)
    t = _RE_ENTITY.sub(" ", t)
    t = _RE_XQ_REF.sub(" ", t)
    return t


def load_texts():
    """只读两库，返回 (雪球文本列表, 新闻文本列表, 元信息 dict)。"""
    meta = {}

    con = sqlite3.connect(f"file:{XQ_DB}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        rows = cur.execute(
            "SELECT title, desc FROM xq_status "
            "WHERE (title IS NOT NULL AND TRIM(title)!='') "
            "   OR (desc IS NOT NULL AND TRIM(desc)!='')"
        ).fetchall()
    finally:
        con.close()
    xq_texts = [clean_text(((t or "") + " " + (d or "")).strip()) for t, d in rows]
    meta["xueqiu_docs"] = len(xq_texts)

    con = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        total = cur.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        rows = cur.execute(
            "SELECT title FROM news WHERE title IS NOT NULL AND TRIM(title)!=''"
        ).fetchall()
    finally:
        con.close()
    news_texts = [clean_text(r[0]) for r in rows]
    meta["news_docs"] = len(news_texts)
    meta["news_total"] = total
    return xq_texts, news_texts, meta


# ---------- 分词 ----------
def try_jieba():
    try:
        import jieba  # noqa
        return True
    except Exception:
        return False


def iter_words_jieba(text):
    import jieba
    for w in jieba.cut(text):
        w = w.strip()
        if not w:
            continue
        if len(w) == 1:               # 单字符（含纯数字/字母）一律过滤
            continue
        if w in STOPWORDS:
            continue
        if re.fullmatch(r"[\d\W]+", w):  # 纯数字/标点
            continue
        yield w


def iter_words_2gram(text):
    """降级方案：中文 2-gram；英文/数字 token 整词保留（>=2 字符）。"""
    # 先抽英文/数字词
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_+-]{1,}", text):
        w = m.group(0)
        if w.lower() not in STOPWORDS and len(w) >= 2:
            yield w
    # 中文 2-gram（只在汉字之间滑窗）
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in han:
        if len(seg) == 1:
            continue
        for i in range(len(seg) - 1):
            g = seg[i:i + 2]
            if g[0] in STOPWORDS or g[1] in STOPWORDS:
                continue
            if g in STOPWORDS:
                continue
            yield g


def count_words(texts, use_jieba):
    c = Counter()
    for t in texts:
        it = iter_words_jieba(t) if use_jieba else iter_words_2gram(t)
        c.update(it)
    return c


# ---------- JEV 判定加权扩展（工单 WO-INTEL-WORDC1-JEV-20261107-001）----------
def load_jev_rows(days=None):
    """只读取已判新闻 (title, ts, jev)。days=None 取全量；否则按 ts 天数开窗。"""
    since = None
    if days:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        sql = ("SELECT title, COALESCE(ts,''), jev FROM news "
               "WHERE jev IS NOT NULL AND jev!='' "
               "  AND title IS NOT NULL AND TRIM(title)!=''")
        args = []
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        rows = cur.execute(sql, args).fetchall()
        n_unjudged = cur.execute(
            "SELECT COUNT(*) FROM news WHERE (jev IS NULL OR jev='') "
            "  AND title IS NOT NULL AND TRIM(title)!=''"
        ).fetchone()[0]
        unjudged = cur.execute(
            "SELECT title, COALESCE(ts,'') FROM news WHERE (jev IS NULL OR jev='') "
            "  AND title IS NOT NULL AND TRIM(title)!='' "
            "  ORDER BY ts DESC LIMIT ?",
            (JEV_UNJUDGED_SAMPLE_CAP,),
        ).fetchall()
    finally:
        con.close()
    return rows, n_unjudged, [clean_text(t) for t, _ in unjudged]


def compute_jev_weighted(use_jieba, days=None, top_n=30):
    """bias 分桶词频（conf 降权），3 主桶 + 1 未判对照桶。纯只读。"""
    rows, n_unjudged_total, unjudged_texts = load_jev_rows(days)
    buckets = {b: {"count": 0, "words": Counter()} for b in JEV_BIAS_BUCKETS}
    malformed = 0
    low_conf = 0
    for title, _ts, jev_raw in rows:
        try:
            d = json.loads(jev_raw)
            bias = d.get("bias")
            if bias not in buckets:
                malformed += 1
                continue
        except Exception:
            malformed += 1
            continue
        conf = d.get("conf")
        weight = 1.0
        if conf is not None and conf < 0.5:
            weight = JEV_CONF_DOWNWEIGHT
            low_conf += 1
        buckets[bias]["count"] += 1
        bc = buckets[bias]["words"]
        for w in (iter_words_jieba(title) if use_jieba else iter_words_2gram(title)):
            bc[w] += weight
    uc = Counter()
    for t in unjudged_texts:
        uc.update(iter_words_jieba(t) if use_jieba else iter_words_2gram(t))
    return {
        "buckets": {
            b: {
                "count": v["count"],
                "top": [{"word": w, "count": round(n, 1)} for w, n in v["words"].most_common(top_n)],
            }
            for b, v in buckets.items()
        },
        "unjudged": {
            "total": n_unjudged_total,
            "sampled": len(unjudged_texts),
            "top": [{"word": w, "count": n} for w, n in uc.most_common(top_n)],
        },
        "meta": {
            "workorder": "WO-INTEL-WORDC1-JEV-20261107-001",
            "window_days": days,
            "judged_rows": len(rows),
            "malformed": malformed,
            "low_conf_downweighted": low_conf,
            "conf_downweight": JEV_CONF_DOWNWEIGHT,
            "stopwords": len(STOPWORDS),
            "note": "只读 news.db（mode=ro）；词频为加权值（conf<0.5 计 0.5）；"
                    "malformed 为 bias 缺失或 JSON 解析失败行，未静默丢弃",
        },
    }


def jev_weighted_html(jw):
    """JEV 加权段的自包含 HTML（三主桶 Top15 + 未判对照桶 Top10）。"""
    colors = {"bull": "#c0392b", "bear": "#1e8449", "neutral": "#7d8790"}
    panels = []
    for b in JEV_BIAS_BUCKETS:
        v = jw["buckets"][b]
        rows = "\n".join(
            f'<tr><td class="rk">{i}</td><td class="wd">{w}</td>'
            f'<td class="num">{n:,}</td></tr>'
            for i, x in enumerate(v["top"][:15], 1)
            for w, n in [(x["word"], x["count"])]
        )
        if not rows:
            rows = '<tr><td colspan="3" style="color:#8a919c">（样本不足）</td></tr>'
        panels.append(
            f'<div class="panel"><h3 style="color:{colors[b]}">{b}（{v["count"]:,} 条）</h3>'
            f'<table class="freq">{rows}</table></div>'
        )
    u = jw["unjudged"]
    u_rows = "\n".join(
        f'<tr><td class="rk">{i}</td><td class="wd">{w}</td><td class="num">{n:,}</td></tr>'
        for i, x in enumerate(u["top"][:10], 1)
        for w, n in [(x["word"], x["count"])]
    )
    m = jw["meta"]
    return (
        '<h2>JEV 判定加权词频（bias 分桶 × conf 降权）</h2>'
        + '<div class="meta" style="margin-bottom:10px">'
        + f"已判 {m['judged_rows']:,} 条（malformed {m['malformed']:,}）；"
        + f"低置信降权 {m['low_conf_downweighted']:,} 条（conf&lt;0.5 计 {m['conf_downweight']}）；"
        + f"未判对照桶全量 {u['total']:,} 条、抽样 {u['sampled']:,} 条</div>"
        + '<div class="cols">' + "".join(panels) + "</div>"
        + '<h3 style="margin-top:18px">未判对照桶 Top10（抽样）</h3>'
        + f'<table class="ov" style="max-width:480px"><tr><th>#</th><th>词</th><th>频次</th></tr>{u_rows}</table>'
    )


# ---------- 报告 ----------
def bar_rows_html(counter, topn=30, color="#2f6fdb"):
    items = counter.most_common(topn)
    maxv = items[0][1] if items else 1
    rows = []
    for i, (w, n) in enumerate(items, 1):
        pct = n / maxv * 100
        rows.append(
            f'<tr><td class="rk">{i}</td><td class="wd">{w}</td>'
            f'<td class="barcell"><div class="bar" style="width:{pct:.1f}%;background:{color}"></div></td>'
            f'<td class="num">{n:,}</td></tr>'
        )
    return "\n".join(rows)


def build_html(xq_c, news_c, overlap, meta, method_label, extra_html=""):
    xq50 = xq_c.most_common(50)
    news50 = news_c.most_common(50)
    ov20 = overlap[:20]

    ov_rows = "\n".join(
        f'<tr><td class="rk">{i}</td><td class="wd">{w}</td>'
        f'<td class="num">{xn:,}</td><td class="num">{nn:,}</td></tr>'
        for i, (w, xn, nn, _) in enumerate(ov20, 1)
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>WORDC1 词云首产出 · 双源词频对比（{TODAY}）</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background:#f7f8fa; color:#2b2f36; margin:0; padding:24px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 22px; border-bottom: 3px solid #2f6fdb; padding-bottom: 8px; }}
  h2 {{ font-size: 17px; margin-top: 32px; }}
  .meta, .meta td {{ font-size: 13px; }}
  table.meta {{ border-collapse: collapse; background:#fff; padding:10px 14px; border-radius:8px; }}
  table.meta td {{ border-bottom: 1px solid #e8eaee; padding: 6px 12px; }}
  .cols {{ display:flex; gap:20px; flex-wrap:wrap; }}
  .panel {{ flex:1 1 460px; background:#fff; border-radius:10px; padding:14px 18px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .panel h3 {{ margin:4px 0 10px; font-size:15px; }}
  table.freq {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.freq td {{ padding:2px 4px; vertical-align:middle; }}
  td.rk {{ color:#8a919c; width:26px; text-align:right; }}
  td.wd {{ font-weight:600; width:88px; }}
  td.barcell {{ padding:0 8px; }}
  .bar {{ height:14px; border-radius:3px; min-width:2px; }}
  td.num {{ width:64px; text-align:right; color:#4a5058; font-variant-numeric:tabular-nums; }}
  table.ov {{ width:100%; border-collapse:collapse; font-size:14px; background:#fff; border-radius:10px; overflow:hidden; }}
  table.ov th {{ background:#eef3fb; padding:8px 10px; text-align:left; }}
  table.ov td {{ padding:6px 10px; border-bottom:1px solid #eef0f4; }}
  table.ov td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr:hover {{ background:#f4f7fd; }}
  .foot {{ margin-top:28px; font-size:12px; color:#8a919c; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>WORDC1 词云首产出 —— 雪球舆情 × 新闻通讯 双源词频对比</h1>
  <table class="meta">
    <tr><td><b>产出日期</b></td><td>{TODAY}</td></tr>
    <tr><td><b>席位</b></td><td>情报部词云席位 WORDC1（陈琳）· 开源情报板块 P3 车道首个真数据产出</td></tr>
    <tr><td><b>雪球口径</b></td><td>xq_status 全量 {meta['xueqiu_docs']:,} 帖（title+desc 拼接；created_at 91% 缺失，近 30 天仅 322 条，时间窗不可用，故取全量）</td></tr>
    <tr><td><b>新闻口径</b></td><td>news 表全量 {meta['news_docs']:,} 条标题（ts 覆盖 2026-07-19 ~ 2026-11-04，标题非空率 100%）</td></tr>
    <tr><td><b>分词方法</b></td><td>{method_label}</td></tr>
    <tr><td><b>停用词</b></td><td>内置 {len(STOPWORDS)} 词 + 全部单字符词 + 纯数字/标点 token</td></tr>
    <tr><td><b>共振词定义</b></td><td>双源 Top500 内共同出现的词，按 min(雪球排名, 新闻排名) 升序取 Top20（两源都热的词情报价值最高）</td></tr>
  </table>

  <h2>双源 Top30 横向条形对比</h2>
  <div class="cols">
    <div class="panel">
      <h3>雪球舆情 Top30（{meta['xueqiu_docs']:,} 帖）</h3>
      <table class="freq">{bar_rows_html(xq_c, 30, "#e8743b")}</table>
    </div>
    <div class="panel">
      <h3>新闻通讯 Top30（{meta['news_docs']:,} 条）</h3>
      <table class="freq">{bar_rows_html(news_c, 30, "#2f6fdb")}</table>
    </div>
  </div>

  <h2>共振词 Top20（双源同热）</h2>
  <table class="ov">
    <tr><th>#</th><th>词</th><th>雪球频次</th><th>新闻频次</th></tr>
    {ov_rows}
  </table>

  {extra_html}
  <div class="foot">WORDC1 · 只读数据库，未写入任何库文件 · 工具：quant_backtest/tools/wordc1_cloud.py</div>
</div>
</body>
</html>
"""
    return html


def main(argv=None):
    ap = argparse.ArgumentParser(description="WORDC1 双源词频对比（只读）")
    ap.add_argument("--jev-weighted", action="store_true",
                    help="追加 jev_weighted 段：bias 分桶 × conf 降权（工单 WO-INTEL-WORDC1-JEV-20261107-001）")
    ap.add_argument("--days", type=int, default=90,
                    help="JEV 加权统计窗口天数（默认 90；0/负值=全量）")
    ap.add_argument("--top", type=int, default=30, help="每桶返回词数（默认 30）")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)

    print("[1/4] 读取两源文本（只读模式）...")
    xq_texts, news_texts, meta = load_texts()
    print(f"      雪球 {len(xq_texts):,} 帖 / 新闻 {len(news_texts):,} 条")

    use_jieba = try_jieba()
    method_label = ("jieba 0.42.1 精确模式中文分词（主用环境已安装）"
                    if use_jieba else
                    "2-gram 字符切分（降级方案：未找到 jieba）")
    print(f"[2/4] 分词方法：{'jieba' if use_jieba else '2-gram 降级'}，停用词 {len(STOPWORDS)} 词 + 单字符过滤")

    print("[3/4] 统计词频与共振词...")
    xq_c = count_words(xq_texts, use_jieba)
    news_c = count_words(news_texts, use_jieba)

    # 共振词：双源 Top500 交集，按 min 排名排序
    xq_top500 = {w: i for i, (w, _) in enumerate(xq_c.most_common(500), 1)}
    news_top500 = {w: i for i, (w, _) in enumerate(news_c.most_common(500), 1)}
    common = set(xq_top500) & set(news_top500)
    overlap = sorted(
        ((w, xq_c[w], news_c[w], min(xq_top500[w], news_top500[w])) for w in common),
        key=lambda x: (x[3], -(xq_top500[x[0]] + news_top500[x[0]]))
    )

    print(f"      雪球词条（去停用词后）{len(xq_c):,} 个 / 新闻 {len(news_c):,} 个 / 双源 Top500 交集 {len(common)} 词")

    print("[4/4] 写出产物...")
    jw = None
    if args.jev_weighted:
        print(f"      JEV 加权段：窗口 {args.days if args.days and args.days > 0 else '全量'} 天，每桶 Top{args.top}")
        jw = compute_jev_weighted(use_jieba,
                                  days=args.days if args.days and args.days > 0 else None,
                                  top_n=args.top)
        print(f"      已判 {jw['meta']['judged_rows']:,}（malformed {jw['meta']['malformed']:,}）"
              f" / 低置信降权 {jw['meta']['low_conf_downweighted']:,}"
              f" / 未判抽样 {jw['unjudged']['sampled']:,}")
        for b in JEV_BIAS_BUCKETS:
            print(f"      {b:8s} {jw['buckets'][b]['count']:>7,} 条  top1: "
                  f"{jw['buckets'][b]['top'][0]['word'] if jw['buckets'][b]['top'] else '-'}")
    html = build_html(xq_c, news_c, overlap, meta, method_label,
                      extra_html=jev_weighted_html(jw) if jw else "")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    payload = {
        "xueqiu": {"top50": [{"word": w, "count": n} for w, n in xq_c.most_common(50)]},
        "news": {"top50": [{"word": w, "count": n} for w, n in news_c.most_common(50)]},
        "overlap_top20": [
            {"word": w, "xueqiu_count": xn, "news_count": nn,
             "xueqiu_rank": xq_top500[w], "news_rank": news_top500[w]}
            for w, xn, nn, _ in overlap[:20]
        ],
        "meta": {
            "date": TODAY,
            "seat": "WORDC1 词云席位（陈琳）",
            "xueqiu_docs": meta["xueqiu_docs"],
            "news_docs": meta["news_docs"],
            "window": "双源全量（xq_status created_at 91% 缺失无法开时间窗；news ts 覆盖 2026-07-19~2026-11-04）",
            "tokenizer": method_label,
            "stopwords": len(STOPWORDS),
            "note": "只读两库，未写入任何库文件",
        },
    }
    if jw is not None:
        payload["jev_weighted"] = jw  # schema 只增不改：既有键原样保留
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"完成：\n  HTML -> {HTML_PATH}\n  JSON -> {JSON_PATH}")

    # 控制台摘要：共振词 Top10
    print("\n共振词 Top10：")
    for i, (w, xn, nn, _) in enumerate(overlap[:10], 1):
        print(f"  {i:2d}. {w}  雪球 {xn:,} / 新闻 {nn:,}")


if __name__ == "__main__":
    main()
