# -*- coding: utf-8 -*-
"""
KGMAP1 首个真数据知识图谱构建工具（席位职责工具，只读）
====================================================
席位: 情报部图谱席位 KGMAP1（郑玄）｜车道: D 车道开源情报板块

四层图: 频道(经 topic_tags 提炼为频道标签) → 主题 → 视频 → 信号词
  - 节点: {id, type(channel_tag/topic/video/term), label, weight}
  - 边:   {src, dst, type(has_tag/belongs_to/hits), weight}
  - 频道标签来自 dim_channel.topic_tags 拆分（如 core_quant/Python/backtesting）
  - 主题层用 dim_topic 全量 32 个
  - 视频层取度数 Top100（度数 = 跨主题数 + 信号词命中数）
  - 信号词层取 Top200（按命中边数）

数据源（只读 URI 打开，绝不写入）:
  C:/项目/知识库/00_数据仓/yt_learning.db
  - dim_channel(100) / dim_topic(32) / dim_video(45,827)
  - bridge_video_topic(49,965: video_id↔topic 多对多)
  - fact_transcript_signal(3,672: video_id→signal_type/term/hit)

产物:
  a. C:/项目/outputs/2026-11-04_KGMAP1图谱首产出.json
  b. C:/项目/outputs/2026-11-04_KGMAP1_主题×频道矩阵.json
  c. C:/项目/outputs/2026-11-04_KGMAP1图谱首产出.html（自包含浅色 0 外链）

用法:
  python kgmap1_graph.py            # 全量构建 + 输出三产物 + 打印摘要
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ---------- 常量 ----------
DB_URI = "file:C:/项目/知识库/00_数据仓/yt_learning.db?mode=ro"
OUT_DIR = Path("C:/项目/outputs")
DATE_TAG = "2026-11-04"
OUT_GRAPH = OUT_DIR / f"{DATE_TAG}_KGMAP1图谱首产出.json"
OUT_MATRIX = OUT_DIR / f"{DATE_TAG}_KGMAP1_主题×频道矩阵.json"
OUT_HTML = OUT_DIR / f"{DATE_TAG}_KGMAP1图谱首产出.html"

MAX_NODES = 500          # 图谱产物节点总数上限
TOP_TERMS = 200          # 信号词层保留数
TOP_VIDEOS = 100         # 视频层保留数（按度数）
TOP_CHANNELS_MATRIX = 30 # 矩阵产物频道列数
TOP_HUB_VIDEOS_HTML = 20 # HTML 枢纽视频表行数

SEAT = "KGMAP1（郑玄）· 情报部图谱席位 · D车道开源情报"

# ---------- 只读连接 ----------
def open_ro():
    con = sqlite3.connect(f"file:{DB_URI.split('file:', 1)[1]}", uri=True)
    con.execute("PRAGMA query_only = ON")  # 双保险: 连接级只读
    return con


def fetch_all(con, sql):
    return con.execute(sql).fetchall()


# ---------- 数据抽取 ----------
def load_data(con):
    channels = fetch_all(con, """
        SELECT channel_id, name, topic_tags, video_count, total_views
        FROM dim_channel ORDER BY video_count DESC
    """)
    topics = fetch_all(con, """
        SELECT topic, video_count FROM dim_topic ORDER BY video_count DESC
    """)
    video2channel = dict(fetch_all(con, """
        SELECT video_id, channel_id FROM dim_video WHERE channel_id IS NOT NULL
    """))
    vt_pairs = fetch_all(con, """
        SELECT b.video_id, b.topic FROM bridge_video_topic b
        JOIN dim_topic t ON t.topic = b.topic
    """)  # 只保留能落到 32 主题内的边
    signals = fetch_all(con, """
        SELECT video_id, signal_type, term FROM fact_transcript_signal
        WHERE term IS NOT NULL AND TRIM(term) != ''
    """)
    video_meta = fetch_all(con, """
        SELECT video_id, title, channel_id FROM dim_video
    """)
    return channels, topics, video2channel, vt_pairs, signals, video_meta


# ---------- 图构建 ----------
def build_graph(channels, topics, video2channel, vt_pairs, signals, video_meta):
    """返回 nodes, edges, 以及中间统计（频道标签→频道、视频度数等）。"""
    # --- 频道标签层（topic_tags 拆分提炼）---
    tag2channels = defaultdict(list)   # tag -> [channel_id]
    channel_name = {}
    for cid, name, tags, vcnt, views in channels:
        channel_name[cid] = name
        for tag in [t.strip() for t in (tags or "").split(",") if t.strip()]:
            tag2channels[tag].append(cid)

    # --- 主题层 ---
    topic_set = {t for t, _ in topics}
    topic_video_count = dict(topics)

    # --- 视频→主题 多对多 ---
    video_topics = defaultdict(set)    # vid -> {topic}
    for vid, topic in vt_pairs:
        video_topics[vid].add(topic)

    # --- 视频→信号词 命中 ---
    video_terms = defaultdict(set)     # vid -> {term}
    term_videos = defaultdict(set)     # term -> {vid}
    term_type = {}
    for vid, stype, term in signals:
        term = term.strip()
        video_terms[vid].add(term)
        term_videos[term].add(vid)
        term_type[term] = stype        # 后写覆盖，同词信号类型以最后一次为准

    # --- 视频度数 = 跨主题数 + 命中信号词数 ---
    video_degree = {}
    for vid, topics_of_v in video_topics.items():
        video_degree[vid] = len(topics_of_v) + len(video_terms.get(vid, ()))

    top_videos = {vid for vid, _ in sorted(
        video_degree.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VIDEOS]}
    top_terms = {term for term, _ in sorted(
        term_videos.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_TERMS]}

    # --- 组装节点 ---
    nodes = []
    vid_title = {vid: title for vid, title, _ in video_meta}
    vid_channel_of = {vid: cid for vid, _, cid in video_meta}

    for tag, cids in sorted(tag2channels.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        nodes.append({"id": f"tag::{tag}", "type": "channel_tag",
                      "label": tag, "weight": len(cids)})
    for topic, vcnt in topics:
        nodes.append({"id": f"topic::{topic}", "type": "topic",
                      "label": topic, "weight": vcnt})
    for vid in sorted(top_videos):
        deg = video_degree[vid]
        nodes.append({"id": f"video::{vid}", "type": "video",
                      "label": (vid_title.get(vid) or vid)[:40], "weight": deg})
    for term in top_terms:
        nodes.append({"id": f"term::{term}", "type": "term",
                      "label": term, "weight": len(term_videos[term])})

    # --- 组装边 ---
    edges = []
    seen_edges = set()
    def add_edge(src, dst, etype, weight=1):
        key = (src, dst, etype)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"src": src, "dst": dst, "type": etype, "weight": weight})

    # 频道 → 标签倒排
    channel2tags = defaultdict(list)
    for tag, cids in tag2channels.items():
        for cid in cids:
            channel2tags[cid].append(tag)

    # 边1: 频道标签 —主题 (经频道内视频数加权)
    tag_topic_weight = defaultdict(int)
    for vid, tps in video_topics.items():
        cid = video2channel.get(vid) or vid_channel_of.get(vid)
        if not cid:
            continue
        for tag in channel2tags.get(cid, ()):
            for tp in tps:
                tag_topic_weight[(tag, tp)] += 1
    for (tag, tp), w in tag_topic_weight.items():
        add_edge(f"tag::{tag}", f"topic::{tp}", "has_tag", w)
    # 边2: 主题 —视频 (多对多, weight=1)
    for vid, tps in video_topics.items():
        if vid not in top_videos:
            continue
        for tp in tps:
            add_edge(f"topic::{tp}", f"video::{vid}", "belongs_to", 1)
    # 边3: 视频 —信号词 (weight=命中边数: 该视频对该词的信号条数)
    video_term_hits = Counter()
    for vid, stype, term in signals:
        video_term_hits[(vid, term.strip())] += 1
    for (vid, term), w in video_term_hits.items():
        if vid in top_videos and term in top_terms:
            add_edge(f"video::{vid}", f"term::{term}", "hits", w)

    stats = {
        "tag2channels": tag2channels,
        "channel_name": channel_name,
        "video_topics": video_topics,
        "video_terms": video_terms,
        "term_videos": term_videos,
        "term_type": term_type,
        "video_degree": video_degree,
        "top_videos": top_videos,
        "top_terms": top_terms,
        "vid_title": vid_title,
        "vid_channel_of": vid_channel_of,
        "tag_topic_weight": tag_topic_weight,
    }
    return nodes, edges, stats


# ---------- 截断控制 ----------
def enforce_node_cap(nodes, edges, meta):
    """超 500 节点时按层内权重截断（term 最先砍，再砍 video），并记录截断规则。"""
    if len(nodes) <= MAX_NODES:
        meta["truncation"] = "未触发：节点总数 %d ≤ %d" % (len(nodes), MAX_NODES)
        return nodes, edges
    by_type = defaultdict(list)
    for n in nodes:
        by_type[n["type"]].append(n)
    order = ["term", "video"]  # 截断优先级: 先砍信号词，再砍视频
    rules = []
    for t in order:
        overflow = len(nodes) - MAX_NODES
        if overflow <= 0:
            break
        lst = sorted(by_type[t], key=lambda n: (n["weight"], n["id"]))
        cut, lst_sorted = min(overflow, len(lst)), sorted(
            by_type[t], key=lambda n: (-n["weight"], n["id"]))
        dropped = {n["id"] for n in lst_sorted[len(lst_sorted) - cut:]}
        # lst_sorted 高→低，砍掉尾部 cut 个
        dropped = set(n["id"] for n in lst_sorted[len(lst_sorted) - cut:])
        by_type[t] = [n for n in lst_sorted if n["id"] not in dropped]
        rules.append(f"{t} 层按权重升序截去 {cut} 个")
        nodes = []
        for t2 in ("channel_tag", "topic", "video", "term"):
            nodes.extend(by_type.get(t2, []))
    kept_ids = {n["id"] for n in nodes}
    before = len(edges)
    edges = [e for e in edges if e["src"] in kept_ids and e["dst"] in kept_ids]
    meta["truncation"] = "; ".join(rules) + (
        f"；截断后孤立边剔除 {before - len(edges)} 条")
    return nodes, edges


# ---------- 产物 a: 图谱 JSON ----------
def dump_graph(nodes, edges, meta):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    obj = {"nodes": nodes, "edges": edges, "meta": meta}
    OUT_GRAPH.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                         encoding="utf-8")

# ---------- 产物 b: 主题×频道矩阵 ----------
def dump_matrix(channels, video2channel, vt_pairs, video_meta):
    # 视频频道归属: dim_video.channel_id
    vid_channel_of = {vid: cid for vid, _, cid in video_meta}
    def vid_channel(vid):
        return video2channel.get(vid) or vid_channel_of(vid)
    # 矩阵只统计 32 主题内的话题
    topics = [t[0] for t in fetch_all(con_g, "SELECT topic FROM dim_topic ORDER BY video_count DESC")]
    top_ch = [cid for cid, name, tags, vc, tv in channels[:TOP_CHANNELS_MATRIX]]
    cell = defaultdict(int)  # (topic, channel_id) -> 视频数
    for vid, tp in vt_pairs:
        cid = vid_channel(vid)
        if cid in top_ch and tp in set(topics):
            cell[(tp, cid)] += 1
    ch_name = {cid: name for cid, name, tags, vc, tv in channels}
    rows = []
    for tp in topics:
        items = {ch_name[c]: cell.get((tp, c), 0) for c in top_ch}
        rows.append({"topic": tp, "channel_counts": items})
    obj = {
        "meta": {
            "seat": SEAT,
            "date": DATE_TAG,
            "caliber": ("32 主题 × Top30 频道（按 dim_channel.video_count 降序）"
                        "的视频数矩阵；视频归属频道取 dim_video.channel_id；"
                        "视频↔主题取 bridge_video_topic 多对多（同一视频可计入多个主题列）；"
                        "稀疏表示：仅保留非 0 单元格"),
            "n_topics": len(topics), "n_channels": len(top_ch),
            "n_cells_nonzero": len(cell),
        },
        "channels": [{"channel_id": c, "name": ch_name[c]} for c in top_ch],
        "rows": rows,
        "sparse": [{"topic": tp, "channel": ch_name[c], "video_count": n}
                   for (tp, c), n in sorted(cell.items(),
                                            key=lambda kv: -kv[1])],
    }
    OUT_MATRIX.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return obj


# ---------- 产物 c: 自包含 HTML ----------
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def dump_html(graph_meta, nodes, edges, matrix_obj, stats, channels):
    topic_nodes = [n for n in nodes if n["type"] == "topic"]
    # 主题度数 = 与主题相连的边数（图内实际边）
    topic_deg = Counter()
    for e in edges:
        if e["src"].startswith("topic::"):
            topic_deg[e["src"][7:]] += 1
        if e["dst"].startswith("topic::"):
            topic_deg[e["dst"][7:]] += 1
    topics_rank = sorted(topic_nodes, key=lambda n: -n.get("weight", 0))

    # 枢纽视频: 跨主题数 + 信号数（取图内 top_videos 全量统计）
    hub = []
    for vid in stats["top_videos"]:
        n_tp = len(stats["video_topics"].get(vid, ()))
        n_term = len(stats["video_terms"].get(vid, ()))
        hub.append((vid, n_tp, n_term, n_tp + n_term))
    hub.sort(key=lambda x: (-x[3], x[0]))
    hub = hub[:TOP_HUB_VIDEOS_HTML]

    # 矩阵热力表数据
    m_ch = matrix_obj["channels"]
    m_rows = matrix_obj["rows"]
    mmax = max([r["channel_counts"].get(c["name"], 0) for r in m_rows for c in m_ch] or [1])

    css = """
    body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:#fafafa;
         color:#1c2733;margin:0;padding:24px;max-width:1180px}
    h1{font-size:22px;border-bottom:3px solid #275d8c;padding-bottom:8px}
    h2{font-size:17px;color:#275d8c;margin-top:34px}
    .sub{color:#5b6b7a;font-size:13px;margin:4px 0 18px}
    .bar-row{display:flex;align-items:center;margin:3px 0}
    .bar-label{width:210px;font-size:12px;text-align:right;padding-right:8px;
               white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .bar{height:16px;background:linear-gradient(90deg,#4d94c7,#275d8c);
         border-radius:2px;min-width:2px}
    .bar-val{font-size:12px;padding-left:6px;color:#42525f}
    table{border-collapse:collapse;font-size:11px;background:#fff}
    th,td{border:1px solid #d8e0e8;padding:3px 7px}
    th{background:#eef3f8;font-weight:600;position:sticky;top:0}
    .mtx td{width:36px;text-align:center}
    .hub td,.hub th{text-align:left;font-size:12px}
    .meta-box{background:#fff;border:1px solid #d8e0e8;border-left:4px solid #275d8c;
              padding:10px 16px;font-size:12.5px;line-height:1.7;border-radius:3px}
    .num{font-variant-numeric:tabular-nums}
    .tag{display:inline-block;background:#e8eef4;border-radius:3px;padding:1px 6px;
         font-size:11px;color:#39546b;margin:1px}
    """

    # 主题度数排行条形图（纯 CSS div 宽度）
    max_w = max((n["weight"] for n in topics_rank), default=1) or 1
    bars = "".join(
        f'<div class="bar-row"><div class="bar-label" title="{esc(n["label"])}">'
        f'{esc(n["label"])}</div><div class="bar" style="width:{n["weight"]*560//max_w}px"></div>'
        f'<div class="bar-val num">{n["weight"]}</div></div>'
        for n in topics_rank)

    # 热力表
    head = "<tr><th>主题＼频道</th>" + "".join(
        f'<th title="{esc(c["name"])}">{esc(c["name"][:10])}</th>' for c in m_ch) + "</tr>"
    body_rows = []
    for r in m_rows:
        tds = []
        for c in m_ch:
            v = r["channel_counts"].get(c["name"], 0)
            alpha = (v / mmax) * 0.85 if v else 0
            bg = f"background:rgba(39,93,140,{alpha:.2f})" if v else ""
            tds.append(f'<td class="num" style="{bg}" title="{esc(c["name"])}: {v}">{v or ""}</td>')
        body_rows.append(f'<tr><th>{esc(r["topic"])}</th>{"".join(tds)}</tr>')

    # 枢纽视频表
    hub_rows = "".join(
        f'<tr><td>{i+1}</td><td title="{esc(vid)}">{esc(vid)}</td>'
        f'<td>{esc((stats["vid_title"].get(vid) or "")[:60])}</td>'
        f'<td class="num">{n_tp}</td><td class="num">{n_term}</td>'
        f'<td class="num"><b>{tot}</b></td></tr>'
        for i, (vid, n_tp, n_term, tot) in enumerate(hub))

    meta_lines = esc(graph_meta.get("truncation", ""))
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>KGMAP1 图谱首产出 · {DATE_TAG}</title><style>{css}</style></head><body>
<h1>KGMAP1 知识图谱首产出（真数据）</h1>
<div class="sub">{esc(SEAT)}｜数据源: yt_learning.db（只读）｜四层: 频道标签 → 主题 → 视频 → 信号词｜
生成时间: {esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</div>

<h2>① 主题节点度数排行（视频覆盖数，全量 32 主题）</h2>
{bars}

<h2>② 主题 × Top30 频道 热力矩阵（单元格=视频数，颜色越深越多）</h2>
<table class="mtx"><thead>{head}</thead><tbody>{"".join(body_rows)}</tbody></table>

<h2>③ Top20 枢纽视频（跨主题数 + 信号词数）</h2>
<table class="hub"><thead><tr><th>#</th><th>video_id</th><th>标题</th>
<th>跨主题数</th><th>信号词数</th><th>枢纽度</th></tr></thead>
<tbody>{hub_rows}</tbody></table>

<h2>④ 口径说明</h2>
<div class="meta-box">
<b>建法</b>: 频道标签层 = dim_channel.topic_tags 逗号拆分（core_quant/Python/backtesting…，共 {len(stats['tag2channels'])} 个标签）；
主题层 = dim_topic 全量 32；视频层 = 度数(跨主题数+信号词数) Top{TOP_VIDEOS}；信号词层 = 命中视频数 Top{TOP_TERMS}（fact_transcript_signal.term）。<br>
<b>边</b>: 频道标签—主题（has_tag，权=该标签频道下归属于该主题的视频数）｜主题—视频（belongs_to，bridge_video_topic 多对多）｜视频—信号词（hits，权=命中条数）。<br>
<b>规模</b>: 节点 {len(nodes)}（频道标签/主题/视频/信号词 四层计数见产物 a meta），边 {len(edges)}。<br>
<b>截断</b>: {meta_lines}<br>
<b>只读声明</b>: 全程以 URI mode=ro + PRAGMA query_only 打开 yt_learning.db，未写入任何字节。
</div>
</body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")


# ---------- 主流程 ----------
con_g = None

def main():
    global con_g
    con = open_ro()
    con_g = con
    channels, topics, video2channel, vt_pairs, signals, video_meta = load_data(con)

    nodes, edges, stats = build_graph(channels, topics, video2channel,
                                      vt_pairs, signals, video_meta)
    type_count = Counter(n["type"] for n in nodes)
    edge_type_count = Counter(e["type"] for e in edges)
    meta = {
        "seat": SEAT, "date": DATE_TAG,
        "caliber": (
            "四层图 频道标签→主题→视频→信号词。频道标签=dim_channel.topic_tags 逗号拆分；"
            "主题=dim_topic 全量 32；视频=度数(跨主题数+信号词数) Top100；"
            "信号词=fact_transcript_signal.term 按命中视频数 Top200。"
            "边: has_tag(标签-主题,权=视频数)/belongs_to(主题-视频,多对多)/hits(视频-信号词,权=命中条数)。"
            "视频→频道归属=dim_video.channel_id（库内直连，无需外部映射）。"),
        "build": "kgmap1_graph.py v1.0",
        "n_nodes": len(nodes), "n_edges": len(edges),
        "node_count_by_type": dict(type_count),
        "edge_count_by_type": dict(edge_type_count),
        "db_readonly": "URI mode=ro + PRAGMA query_only",
    }
    nodes, edges = enforce_node_cap(nodes, edges, meta)
    meta["n_nodes"], meta["n_edges"] = len(nodes), len(edges)
    meta["node_count_by_type"] = dict(Counter(n["type"] for n in nodes))
    meta["edge_count_by_type"] = dict(Counter(e["type"] for e in edges))
    dump_graph(nodes, edges, meta)

    matrix_obj = dump_matrix(channels, video2channel, vt_pairs, video_meta)
    dump_html(meta, nodes, edges, matrix_obj, stats, channels)
    con.close()

    # ---- 终端摘要 ----
    print(f"[OK] 图谱: {len(nodes)} 节点 / {len(edges)} 边  -> {OUT_GRAPH}")
    print(f"     分层: {meta['node_count_by_type']}")
    print(f"     边型: {meta['edge_count_by_type']}")
    print(f"[OK] 矩阵: {matrix_obj['meta']['n_cells_nonzero']} 非零单元格 -> {OUT_MATRIX}")
    print(f"[OK] HTML -> {OUT_HTML}")

    # 主题枢纽 Top5（图内度数: 与主题相连的边数）
    topic_deg = Counter()
    for e in edges:
        if e["src"].startswith("topic::"):
            topic_deg[e["src"][7:]] += 1
        if e["dst"].startswith("topic::"):
            topic_deg[e["dst"][7:]] += 1
    print("[主题枢纽Top5]", topic_deg.most_common(5))

    # 跨主题最多视频 Top3（全量口径, 不止 top_videos）
    cross = Counter()
    for vid, tps in stats["video_topics"].items():
        cross[vid] = len(tps)
    print("[跨主题视频Top3]", cross.most_common(3))


if __name__ == "__main__":
    sys.exit(main())
