# -*- coding: utf-8 -*-
"""LINEAGE1（谯周·情报部血缘席位）席位工具：11-01~11-04 情报产物链扩谱入库。

把情报产物链（source → bridge → dataset → product）纳入既有血缘库
research/lineage.db（11-02 建的假设-策略血缘，129 节点 78 边，全部保留不动）。

设计约定：
  - 节点 id 一律 `intel:` 前缀，与既有 129 节点（data_layer/evidence/hypothesis/strategy）零冲突；
  - 幂等：INSERT OR REPLACE（nodes 主键 id；edges 主键 src,dst,kind），重复跑零副作用；
  - 事务：全部写入在一个事务内 commit，失败整体回滚；
  - 产物：outputs/2026-11-04_LINEAGE1情报链血缘.html（自包含浅色 0 外链，纯 CSS 四列布局）
          outputs/2026-11-04_LINEAGE1_情报链节点边.json（新增清单供审）；
  - 备份：写入前 file copy lineage.db → lineage.backup_20261104.db（若备份已存在则跳过，不覆盖）。

用法：
  python lineage1_intel.py            # 备份→扩谱→核验→出产物
"""
from __future__ import annotations

import html
import io
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"C:/项目/老田的AI助手"
DB_PATH = os.path.join(REPO, "research", "lineage.db")
BACKUP_PATH = os.path.join(REPO, "research", "lineage.backup_20261104.db")
OUT_DIR = r"C:/项目/outputs"
HTML_OUT = os.path.join(OUT_DIR, "2026-11-04_LINEAGE1情报链血缘.html")
JSON_OUT = os.path.join(OUT_DIR, "2026-11-04_LINEAGE1_情报链节点边.json")

NOW = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

SOURCES_REF = "intel_collector/registry/sources.json"
BRIDGES_REF = "intel_collector/bridges/"

# ---------------- 节点定义 ----------------
# (id, kind, label, ref, meta)
NODES = [
    # source ×7 —— ref 指情报源登记表
    ("intel:src_xueqiu",        "source", "雪球公开舆情",         SOURCES_REF, '{"source_id":"xueqiu","category":"open_source_intel"}'),
    ("intel:src_guba",          "source", "股吧舆情",             SOURCES_REF, '{"source_id":"guba","category":"open_source_intel"}'),
    ("intel:src_news",          "source", "新闻聚合",             SOURCES_REF, '{"source_id":"news","category":"open_source_intel"}'),
    ("intel:src_youtube_corpus","source", "YouTube 学习语料",     SOURCES_REF, '{"source_id":"youtube_corpus","category":"open_source_intel"}'),
    ("intel:src_pdf_methodology","source", "PDF 方法论/研报蒸馏", SOURCES_REF, '{"source_id":"pdf_methodology","category":"open_source_intel"}'),
    ("intel:src_res_downloader","source", "res-downloader-py 通用下载器", SOURCES_REF, '{"source_id":"res_downloader_py","category":"interface"}'),
    ("intel:src_youtube_envelope","source", "YouTube 系统外元信息包(envelope)", SOURCES_REF, '{"source_id":"youtube_envelope","category":"interface"}'),
    # bridge ×4 —— ref 指桥接器目录/适配器
    ("intel:br_xq_pump_bridge", "bridge", "xq_pump_bridge",   BRIDGES_REF + "xq_pump_bridge.py", '{"op":"雪球舆情→库"}'),
    ("intel:br_kb_bridge",      "bridge", "kb_bridge",        BRIDGES_REF + "kb_bridge.py",      '{"op":"知识库语料→库"}'),
    ("intel:br_news_bridge",    "bridge", "news_bridge",      BRIDGES_REF + "news_bridge.py",    '{"op":"新闻→库"}'),
    ("intel:br_livexqfetcher",  "bridge", "LiveXqFetcher",    "intel_collector/adapters/xueqiu_live.py", '{"op":"雪球实时抓取(经xq_pump.core)"}'),
    # dataset ×5 —— ref 指各库实际路径
    ("intel:ds_xueqiu_info_db", "dataset", "xueqiu_info.db", "quant_backtest/data/xueqiu/xueqiu_info.db", '{"engine":"sqlite"}'),
    ("intel:ds_news_db",        "dataset", "news.db",         "news_collector/news.db",                  '{"engine":"sqlite"}'),
    ("intel:ds_yt_learning_db", "dataset", "yt_learning.db", "C:/项目/知识库/00_数据仓/yt_learning.db",  '{"engine":"sqlite"}'),
    ("intel:ds_kg_distill",     "dataset", "kg_distill.parquet", "quant_backtest/data/distill/kg_distill.parquet", '{"engine":"parquet"}'),
    ("intel:ds_quotes",         "dataset", "quotes(xq_quote_snap)", "quant_backtest/data/xueqiu/xq_quote_snap", '{"engine":"parquet(daily)"}'),
    # product ×7 —— 11-04 产出，ref 指 outputs/ 或 tools/
    ("intel:pd_wordc1_cloud",   "product", "wordc1_cloud",       "quant_backtest/tools/wordc1_cloud.py", '{"tool":"wordc1_cloud.py"}'),
    ("intel:pd_kgmap1_graph",   "product", "kgmap1_graph",       "quant_backtest/tools/kgmap1_graph.py", '{"tool":"kgmap1_graph.py"}'),
    ("intel:pd_resonance_v1",   "product", "resonance_v1",       "quant_backtest/tools/resonance_v1.py", '{"tool":"resonance_v1.py"}'),
    ("intel:pd_news_bridge_p",  "product", "news_bridge",        "quant_backtest/tools/news_bridge",   '{"note":"news_bridge 产物位"}'),
    ("intel:pd_kb_bridge_p",    "product", "kb_bridge",          "quant_backtest/tools/kb_bridge",     '{"note":"kb_bridge 产物位"}'),
    ("intel:pd_wordc1_html",    "product", "WORDC1词云HTML",     "C:/项目/outputs/2026-11-04_WORDC1词云首产出.html",  '{"fmt":"html自包含"}'),
    ("intel:pd_kg_res_html",    "product", "P4共振HTML",         "C:/项目/outputs/2026-11-04_P4三源共振v1.html",      '{"fmt":"html自包含"}'),
]

# ---------------- 边定义 ----------------
# (src, dst, kind, op, meta)
EDGES = [
    # source → bridge（ingest）：源经桥接器摄入
    ("intel:src_xueqiu",         "intel:br_xq_pump_bridge", "ingest", "雪球舆情经 xq_pump 桥摄入",      '{"since":"11-01"}'),
    ("intel:src_xueqiu",         "intel:br_livexqfetcher",  "ingest", "雪球实时线经 LiveXqFetcher",     '{"since":"11-01"}'),
    ("intel:src_guba",           "intel:br_xq_pump_bridge", "ingest", "股吧舆情并入雪球桥",             '{"since":"11-01"}'),
    ("intel:src_news",           "intel:br_news_bridge",    "ingest", "新闻聚合经 news_bridge",         '{"since":"11-01"}'),
    ("intel:src_youtube_corpus", "intel:br_kb_bridge",      "ingest", "YT 语料经 kb_bridge 只读桥",     '{"since":"11-02"}'),
    ("intel:src_pdf_methodology","intel:br_kb_bridge",      "ingest", "PDF 方法论蒸馏入 kb 桥",         '{"since":"11-02"}'),
    ("intel:src_youtube_envelope","intel:br_kb_bridge",     "ingest", "YT envelope 元信息入 kb 桥",     '{"since":"11-02"}'),
    ("intel:src_res_downloader", "intel:br_kb_bridge",      "ingest", "res-downloader 下载供 kb 桥",    '{"since":"11-01"}'),
    ("intel:src_res_downloader", "intel:br_livexqfetcher",  "ingest", "通用下载器辅助实时线",           '{"since":"11-01"}'),
    # bridge → dataset（writes）：桥接器写库
    ("intel:br_xq_pump_bridge",  "intel:ds_xueqiu_info_db", "writes", "xq_pump.store 落雪球库",         '{"store":"xq_pump/store.py"}'),
    ("intel:br_livexqfetcher",   "intel:ds_xueqiu_info_db", "writes", "LiveXqFetcher→xq_pump.core 落库",'{"via":"xq_pump.core"}'),
    ("intel:br_livexqfetcher",   "intel:ds_quotes",         "writes", "quotec 快照落 quotes",           '{"via":"XqSession.quotec"}'),
    ("intel:br_news_bridge",     "intel:ds_news_db",        "writes", "news_bridge 落新闻库",           '{}'),
    ("intel:br_kb_bridge",       "intel:ds_yt_learning_db", "writes", "kb 桥维护 yt_learning 库",       '{"mode":"read_mostly"}'),
    ("intel:br_kb_bridge",       "intel:ds_kg_distill",     "writes", "kb 语料→kg 蒸馏样本",            '{"via":"kg_distill.py"}'),
    # dataset → product（feeds）：库喂产物
    ("intel:ds_xueqiu_info_db",  "intel:pd_wordc1_cloud",   "feeds",  "xq_status 表喂词云",             '{"table":"xq_status"}'),
    ("intel:ds_news_db",         "intel:pd_wordc1_cloud",   "feeds",  "news 表喂词云",                  '{"table":"news"}'),
    ("intel:ds_yt_learning_db",  "intel:pd_kgmap1_graph",   "feeds",  "dim_channel/topic 喂图谱",       '{"mode":"ro"}'),
    ("intel:ds_kg_distill",      "intel:pd_resonance_v1",   "feeds",  "蒸馏样本喂共振分析",             '{}'),
    ("intel:ds_quotes",          "intel:pd_resonance_v1",   "feeds",  "quote 快照喂共振量能",           '{}'),
    ("intel:ds_xueqiu_info_db",  "intel:pd_resonance_v1",   "feeds",  "雪球情绪喂共振源1",              '{}'),
    ("intel:ds_news_db",         "intel:pd_news_bridge_p",  "feeds",  "news 库喂 news_bridge 产物位",   '{}'),
    ("intel:ds_yt_learning_db",  "intel:pd_kb_bridge_p",    "feeds",  "yt 库喂 kb_bridge 产物位",       '{}'),
    # 产物→产物派生（derives）
    ("intel:pd_wordc1_cloud",    "intel:pd_resonance_v1",   "derives","resonance_v1 读 WORDC1 词表",    '{"input":"2026-11-04_WORDC1_top词表.json"}'),
    ("intel:pd_wordc1_cloud",    "intel:pd_wordc1_html",    "derives","词云工具产出 HTML 报告",         '{}'),
    ("intel:pd_resonance_v1",    "intel:pd_kg_res_html",    "derives","共振分析产出 HTML 报告",         '{}'),
    # source → dataset（raw）：不经桥的直接原始落点
    ("intel:src_news",           "intel:ds_news_db",        "raw",    "新闻原始快照",                   '{}'),
    ("intel:src_youtube_corpus", "intel:ds_yt_learning_db", "raw",    "YT 规范化语料原始层",            '{"mode":"batch_ro"}'),
    ("intel:src_pdf_methodology","intel:ds_kg_distill",     "raw",    "方法论蒸馏样本原始层",           '{}'),
    ("intel:src_xueqiu",         "intel:ds_quotes",         "raw",    "雪球 quote 原始快照",            '{}'),
]

# 供报告展示的一条完整示例路径（起点 source → 终点 product HTML）
EXAMPLE_CHAIN = [
    ("intel:src_xueqiu", "雪球公开舆情"),
    ("intel:br_xq_pump_bridge", "xq_pump_bridge [ingest]"),
    ("intel:ds_xueqiu_info_db", "xueqiu_info.db [writes]"),
    ("intel:pd_resonance_v1", "resonance_v1 [feeds]"),
    ("intel:pd_kg_res_html", "P4共振HTML [derives]"),
]

CST = timezone(timedelta(hours=8))


def tnow() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def backup_db() -> str:
    if os.path.exists(BACKUP_PATH):
        return "skip-exists"
    os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)
    shutil.copyfile(DB_PATH, BACKUP_PATH)
    return "copied"


def legacy_counts(con: sqlite3.Connection):
    cur = con.cursor()
    n = cur.execute(
        "SELECT COUNT(*) FROM nodes WHERE id NOT LIKE 'intel:%'").fetchone()[0]
    e = cur.execute(
        "SELECT COUNT(*) FROM edges e WHERE e.src NOT LIKE 'intel:%' AND e.dst NOT LIKE 'intel:%'"
    ).fetchone()[0]
    return n, e


def write_intel(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    for nid, kind, label, ref, meta in NODES:
        cur.execute(
            "INSERT OR REPLACE INTO nodes(id, kind, label, ref, meta) VALUES (?,?,?,?,?)",
            (nid, kind, label, ref, meta))
    for src, dst, kind, op, meta in EDGES:
        cur.execute(
            "INSERT OR REPLACE INTO edges(src, dst, kind, op, meta) VALUES (?,?,?,?,?)",
            (src, dst, kind, op, meta))
    # meta 表：built_at 刷新 + intel 扩谱计数（含 kind 细分）
    cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('intel_built_at', ?)", (NOW,))
    cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('intel_nodes', ?)",
                (str(len(NODES)),))
    cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('intel_edges', ?)",
                (str(len(EDGES)),))
    detail = json.dumps({
        "nodes_by_kind": {
            "source": sum(1 for x in NODES if x[1] == "source"),
            "bridge": sum(1 for x in NODES if x[1] == "bridge"),
            "dataset": sum(1 for x in NODES if x[1] == "dataset"),
            "product": sum(1 for x in NODES if x[1] == "product"),
        },
        "edges_by_kind": {k: sum(1 for e in EDGES if e[2] == k)
                          for k in {e[2] for e in EDGES}},
    }, ensure_ascii=False)
    cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('intel_detail', ?)", (detail,))


def build_json() -> dict:
    return {
        "built_at": NOW,
        "seat": "LINEAGE1 谯周（情报部血缘席位）",
        "db": DB_PATH,
        "backup": BACKUP_PATH,
        "id_namespace": "intel:",
        "counts": {
            "new_nodes": len(NODES),
            "new_edges": len(EDGES),
            "nodes_by_kind": {
                "source": sum(1 for x in NODES if x[1] == "source"),
                "bridge": sum(1 for x in NODES if x[1] == "bridge"),
                "dataset": sum(1 for x in NODES if x[1] == "dataset"),
                "product": sum(1 for x in NODES if x[1] == "product"),
            },
            "edges_by_kind": {k: sum(1 for e in EDGES if e[2] == k)
                              for k in {e[2] for e in EDGES}},
        },
        "example_chain": [
            {"id": nid, "label": lab} for nid, lab in EXAMPLE_CHAIN
        ],
        "nodes": [
            {"id": n[0], "kind": n[1], "label": n[2], "ref": n[3],
             "meta": json.loads(n[4])}
            for n in NODES
        ],
        "edges": [
            {"src": e[0], "dst": e[1], "kind": e[2], "op": e[3],
             "meta": json.loads(e[4])}
            for e in EDGES
        ],
    }


LAYER_ORDER = ["source", "bridge", "dataset", "product"]
LAYER_CN = {"source": "① 情报源 SOURCE", "bridge": "② 桥接器 BRIDGE",
            "dataset": "③ 数据集 DATASET", "product": "④ 产物 PRODUCT"}
KIND_COLOR = {"source": "#1f6f5c", "bridge": "#8a5a00", "dataset": "#1a4f8b", "product": "#8b1a4f"}
EDGE_KIND_CN = {"ingest": "摄入", "writes": "写库", "feeds": "喂给", "derives": "派生", "raw": "原始"}


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def node_box(nid, label, ref, kind) -> str:
    return (f'<div class="node" style="border-color:{KIND_COLOR[kind]}">'
            f'<div class="ntitle" style="color:{KIND_COLOR[kind]}">{esc(label)}</div>'
            f'<div class="nid">{esc(nid)}</div>'
            f'<div class="nref">{esc(ref)}</div></div>')


def build_html(json_doc: dict, legacy_n: int, legacy_e: int) -> str:
    nodes_by_id = {n["id"]: n for n in json_doc["nodes"]}
    col_html, layer_stat = [], {}
    for layer in LAYER_ORDER:
        ns = [n for n in json_doc["nodes"] if n["kind"] == layer]
        layer_stat[layer] = len(ns)
        boxes = "\n".join(node_box(n["id"], n["label"], n["ref"], layer) for n in ns)
        col_html.append(
            f'<div class="col"><div class="colhead" style="background:{KIND_COLOR[layer]}">'
            f'{LAYER_CN[layer]}<span class="badge">{len(ns)}</span></div>\n{boxes}</div>')
    cols = "\n".join(col_html)

    edge_rows = "\n".join(
        f'<tr><td>{esc(e["src"])}</td><td class="ek">{EDGE_KIND_CN.get(e["kind"], e["kind"])}</td>'
        f'<td>{esc(e["dst"])}</td><td class="op">{esc(e["op"])}</td></tr>'
        for e in json_doc["edges"])

    chain_html = " →\n".join(
        f'<span class="chip" style="border-color:{KIND_COLOR[nodes_by_id[nid]["kind"]]}">{esc(lab)}</span>'
        for nid, lab in EXAMPLE_CHAIN)

    ek_counts = json_doc["counts"]["edges_by_kind"]
    ek_stat = " · ".join(
        f'{EDGE_KIND_CN.get(k, k)} {v}' for k, v in
        sorted(ek_counts.items(), key=lambda kv: -kv[1]))

    nbk = json_doc["counts"]["nodes_by_kind"]
    cols = cols.replace('</div>\n<div class="col">', '</div>\n<div class="colgap">→</div>\n<div class="col">')
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>LINEAGE1 情报链血缘 · 2026-11-04</title>
<style>
  :root {{ --ink:#1c2733; --sub:#5b6b7a; --line:#d7dee6; --bg:#f7fafc; --card:#ffffff; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:"Microsoft YaHei","PingFang SC",sans-serif; background:var(--bg); color:var(--ink); padding:28px 34px; }}
  h1 {{ font-size:21px; margin-bottom:4px; }}
  .sub {{ color:var(--sub); font-size:12px; margin-bottom:20px; }}
  .cards {{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:22px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 20px; min-width:150px; }}
  .card .num {{ font-size:26px; font-weight:700; }}
  .card .lbl {{ font-size:12px; color:var(--sub); margin-top:2px; }}
  .board {{ display:flex; gap:0; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px; overflow-x:auto; }}
  .col {{ flex:1 1 0; min-width:215px; display:flex; flex-direction:column; }}
  .colhead {{ color:#fff; font-size:12px; font-weight:700; padding:7px 10px; border-radius:6px; margin-bottom:12px; position:sticky; top:0; }}
  .badge {{ float:right; background:rgba(255,255,255,.25); border-radius:9px; padding:0 7px; }}
  .node {{ background:#fff; border:1.5px solid; border-left-width:5px; border-radius:7px; padding:8px 10px; margin-bottom:10px; box-shadow:0 1px 2px rgba(28,39,51,.05); }}
  .ntitle {{ font-size:13px; font-weight:700; }}
  .nid {{ font-size:10.5px; color:var(--sub); font-family:Consolas,monospace; margin-top:1px; }}
  .nref {{ font-size:10px; color:var(--sub); font-family:Consolas,monospace; margin-top:3px; word-break:break-all; }}
  .colgap {{ width:26px; align-self:center; color:#9fb0bf; font-size:17px; flex:0 0 26px; text-align:center; }}
  h2 {{ font-size:15px; margin:26px 0 10px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:12px; }}
  th, td {{ padding:6px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ background:#eef3f7; font-size:12px; }}
  td {{ font-family:Consolas,monospace; color:#2a3947; }}
  td.ek {{ font-weight:700; color:#1a4f8b; text-align:center; white-space:nowrap; }}
  td.op {{ font-family:inherit; color:var(--sub); }}
  tr:last-child td {{ border-bottom:none; }}
  .chain {{ background:var(--card); border:1px dashed #b8c6d3; border-radius:10px; padding:13px 16px; font-size:13px; line-height:2.1; }}
  .chip {{ display:inline-block; border:1.5px solid; border-radius:14px; padding:2px 11px; margin:2px 0; font-weight:600; white-space:nowrap; }}
  .note {{ background:#fdf9ee; border:1px solid #e8dcbb; border-radius:10px; padding:12px 16px; font-size:12px; color:#5f5322; line-height:1.9; }}
  .foot {{ margin-top:18px; font-size:11px; color:var(--sub); }}
</style>
</head>
<body>
<h1>LINEAGE1 情报产物链血缘图 · 2026-11-04</h1>
<div class="sub">开源情报板块 F 车道 · 情报部血缘席位 LINEAGE1（谯周）· 覆盖 11-01 ~ 11-04 情报产物链 · id 命名空间 intel:</div>

<div class="cards">
  <div class="card"><div class="num">23</div><div class="lbl">新增节点（source 7 / bridge 4 / dataset 5 / product 7）</div></div>
  <div class="card"><div class="num">{len(json_doc["edges"])}</div><div class="lbl">新增边（{esc(ek_stat)}）</div></div>
  <div class="card"><div class="num">{legacy_n}</div><div class="lbl">既有节点（假设-策略血缘，11-02，全部保留）</div></div>
  <div class="card"><div class="num">{legacy_e}</div><div class="lbl">既有边（全部保留）</div></div>
  <div class="card"><div class="num">{legacy_n + 23}</div><div class="lbl">库内节点总数</div></div>
</div>

<h2>四列血缘（source → bridge → dataset → product）</h2>
<div class="board">
{cols}
</div>
<div class="sub" style="margin-top:8px">列内盒 = 节点（标题 / intel:id / ref 落点）；箭头方向恒为左→右：ingest 摄入 → writes 写库 → feeds 喂给 → derives 派生（raw 为源直落库）。</div>

<h2>完整血缘链示例</h2>
<div class="chain">{chain_html}</div>

<h2>边清单（{len(json_doc["edges"])} 条）</h2>
<table>
<tr><th>src</th><th>关系</th><th>dst</th><th>说明</th></tr>
{edge_rows}
</table>

<h2>口径说明</h2>
<div class="note">
· 既有基建：research/lineage.db（11-02 建假设-策略血缘）—— {legacy_n} 节点（data_layer 14 / evidence 44 / hypothesis 60 / strategy 11）、{legacy_e} 边（data_flow / derived_strategy / evidence_path），本席全部保留、零改动；<br>
· 本次扩谱：新增 23 节点（情报域 kinds：source/bridge/dataset/product，与既有 kinds 并存）+ {len(json_doc["edges"])} 边（ingest/writes/feeds/derives/raw），id 一律 intel: 前缀防撞；<br>
· 幂等：INSERT OR REPLACE（nodes 主键 id，edges 主键 src,dst,kind），重复执行零副作用；单事务写入，失败整体回滚；<br>
· 备份：写入前已 file copy 至 research/lineage.backup_20261104.db；<br>
· 元数据：meta 表写入 intel_built_at / intel_nodes / intel_edges / intel_detail；<br>
· 产物：本 HTML 与 2026-11-04_LINEAGE1_情报链节点边.json 均输出至 C:/项目/outputs/。
</div>

<div class="foot">built_at {esc(json_doc["built_at"])} · LINEAGE1 谯周 · 自包含浅色 · 0 外链 · 不提交 git</div>
</body>
</html>
"""


def main() -> int:
    if not os.path.exists(DB_PATH):
        print("FATAL: lineage.db 不存在", DB_PATH)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) 备份（不覆盖已有备份）
    bstate = backup_db()
    bsize = os.path.getsize(BACKUP_PATH) if os.path.exists(BACKUP_PATH) else -1
    print(f"[backup] {bstate} → {BACKUP_PATH} ({bsize} bytes)")

    # 2) 事务写入
    con = sqlite3.connect(DB_PATH)
    try:
        legacy_n0, legacy_e0 = legacy_counts(con)
        with con:  # 单事务：异常整体回滚
            write_intel(con)
        legacy_n1, legacy_e1 = legacy_counts(con)
        cur = con.cursor()
        intel_n = cur.execute("SELECT COUNT(*) FROM nodes WHERE id LIKE 'intel:%'").fetchone()[0]
        intel_e = cur.execute(
            "SELECT COUNT(*) FROM edges WHERE src LIKE 'intel:%' OR dst LIKE 'intel:%'").fetchone()[0]
        total_n = cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_e = cur.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        con.close()

    print(f"[legacy] {legacy_n0}/{legacy_e0} → {legacy_n1}/{legacy_e1} (须相等)")
    print(f"[intel ] nodes={intel_n} edges={intel_e} | [total] nodes={total_n} edges={total_e}")
    assert (legacy_n0, legacy_e0) == (legacy_n1, legacy_e1), "既有节点/边被改动！"
    assert intel_n == len(NODES) and intel_e == len(EDGES), "intel 扩谱计数不符！"
    assert total_n == legacy_n1 + len(NODES), "总数不守恒！"

    # 3) 产物
    doc = build_json()
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(build_html(doc, legacy_n1, legacy_e1))
    print(f"[out  ] {HTML_OUT}")
    print(f"[out  ] {JSON_OUT}")
    print("OK: 扩谱入库完成，幂等可重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
