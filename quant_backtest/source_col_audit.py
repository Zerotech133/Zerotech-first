# -*- coding: utf-8 -*-
"""数据源列对账工具（K165~K173 系列方法沉淀，可定期跑的防线）。

三大模块（对账双链分离范式）：
①源列快照：data/tushare + 自产仓（ashare/features/semantica）各表
  首尾分片 arrow schema（只读 footer，不载全量）；
②消费端底册交叉：CONSUMERS 底册（K165~K173 实读沉淀，82 点清单的
  跨层消费面）× 源列集 → 缺列即 FAIL；
③图内 presence：MarketEvent 按 kind×属性 presence 矩阵 + Seat/边
  属性（--graph 开关，Neo4j 不可达则 SKIP）。

用法：python source_col_audit.py [--json reports/col_audit.json]
退出码：0 全绿 / 1 有 FAIL（可挂计划任务）。

底册维护规则：新增消费点/源列改名后同步 CONSUMERS；audit 报 FAIL
时先判定「tushare 改名」还是「消费端写错」，再决定改哪头。
"""

import argparse
import glob
import json
import os
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 源仓根路径 ──────────────────────────────────────────────
STORE_PATHS = {
    "tushare": os.path.join(HERE, "data", "tushare"),
    "ashare": os.path.join(HERE, "data", "ashare"),
    "features": os.path.join(HERE, "data", "features"),
    "semantica": os.path.join(HERE, "data", "semantica"),
    "tdx": os.path.join(HERE, "data", "tdx"),
}

# ── 消费端底册（K165~K173 实读沉淀；键=(仓, 表或文件)）────────
# 值=[(消费端描述, 必需消费列 set)]——缺列即断源。
A11 = {
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "volume",
    "amount",
}  # ashare schema
CONSUMERS = {
    # —— tushare 直源（kg_bclass 写图链，K161/K164 实伤修复点）——
    ("tushare", "limit_list_d"): [
        (
            "kg_bclass._limit_rows",
            {
                "ts_code",
                "trade_date",
                "name",
                "close",
                "pct_chg",
                "amount",
                "limit_times",
                "limit_amount",
                "fd_amount",
                "first_time",
                "last_time",
                "open_times",
                "up_stat",
                "limit",
            },
        )
    ],
    ("tushare", "top10_floatholders"): [
        (
            "kg_bclass._holds_rows",
            {"ts_code", "end_date", "holder_name", "hold_ratio", "hold_amount"},
        )
    ],
    ("tushare", "stk_holdernumber"): [
        ("kg_bclass._holder_feat", {"ts_code", "end_date", "holder_num"})
    ],
    ("tushare", "cyq_perf"): [
        (
            "kg_bclass._cyq_feat",
            {
                "ts_code",
                "trade_date",
                "cost_15pct",
                "cost_85pct",
                "cost_50pct",
                "weight_avg",
                "winner_rate",
            },
        )
    ],
    # —— tushare 直源（kg 建图/消费链）——
    ("tushare", "stk_mins"): [
        ("kg_candle.sync/detect_day", {"trade_time", "close", "high", "amount"})
    ],
    ("tushare", "stock_basic"): [
        (
            "kg_build(p3 前置补列)",
            {"ts_code", "market", "cnspell", "act_name", "act_ent_type"},
        ),
        ("tushare_sync:927", {"ts_code"}),
    ],
    ("tushare", "stk_managers"): [
        ("kg_build_p2", {"ts_code", "ann_date", "name", "begin_date", "end_date"})
    ],
    ("tushare", "sw_l1_member"): [
        ("kg_build_p2_sw", {"con_code", "in_date", "out_date", "is_new"})
    ],
    ("tushare", "index_global"): [
        ("kg_build_p3:94", {"ts_code", "trade_date", "close"})
    ],
    ("tushare", "trade_cal"): [
        ("runner._today_open/premarket/ts_daily_deep/verify", {"cal_date", "is_open"})
    ],
    ("tushare", "anns_d"): [("verify_tushare_sync", {"ann_date"})],
    ("tushare", "hk_fina_indicator"): [
        ("portal_api._stock_fundamental", {"end_date", "pe_ttm", "total_market_cap"})
    ],
    # —— ashare 自产仓（写端=rps_resonance 11 列 schema）——
    ("ashare", "*.parquet"): [
        (
            "nine_dim._bars/_load",
            {"trade_date", "close", "pre_close", "open", "high", "low", "volume"},
        ),
        (
            "chanlun_scan._bars_from_parquet / chanlun_tbe_stats",
            {"trade_date", "open", "high", "low", "close", "volume"},
        ),
        ("premarket_backtest / rps_resonance:365", {"trade_date", "close"}),
    ],
    # —— 自产特征/标签仓 ——
    ("features", "kg_features.parquet"): [
        (
            "nine_dim:360 KG_FEAT",
            {
                "ts_code",
                "deg_product",
                "peer_industry",
                "peer_sw_l1",
                "person_shared",
                "peer_shared_prod",
            },
        )
    ],
    ("features", "chanlun_sig_dataset.parquet"): [
        ("chanlun_mlf:442", {"detect_date", "win"})
    ],
    ("semantica", "notice_llm.parquet"): [
        (
            "kg_brief / distill_pool / kg_notice_llm",
            {"id", "conf", "judged_at", "text", "tag", "bias"},
        )
    ],
    # —— tdx 仓（tdx_read date_col_of 权威映射器管日期列，此处查跨层列）——
    ("tdx", "ts_daily"): [
        (
            "kg_distill:68 / portal_api:2652 / premarket_backtest:38",
            {"ts_code", "trade_date", "close"},
        )
    ],
    ("tdx", "ts_stock_basic"): [("kg_features:106", {"ts_code", "name"})],
}

# ── 图内属性 presence（kg_portal 消费集，K168 矩阵沉淀）────────
ME_KINDS = ("limit", "candle", "topgap", "volume", "chanlun")
ME_ATTRS = (
    "date",
    "type",
    "mtime",
    "pct",
    "n_touch",
    "tail_vol",
    "limit_times",
    "pct_chg",
    "limit_amount",
    "open_times",
    "up_stat",
    "limit_type",
    "close",
    "amount",
    "fd_amount",
    "first_seal_time",
    "last_seal_time",
    "gap",
    "gap_ratio",
    "dom_nature",
    "quant_ratio",
    "ret1",
    "oret1",
    "pen",
    "reason",
)
# 属性×kind 适用矩阵（天然不含=适用外，不报 FAIL）：从 K168 实测沉淀
ME_EXPECT = {
    "limit": {
        "date",
        "limit_times",
        "pct_chg",
        "limit_amount",
        "open_times",
        "up_stat",
        "limit_type",
        "close",
        "amount",
        "fd_amount",
    },
    "candle": {"date", "type", "mtime", "pct", "n_touch", "tail_vol"},
    "topgap": {
        "date",
        "pct_chg",
        "close",
        "amount",
        "gap",
        "gap_ratio",
        "dom_nature",
        "quant_ratio",
        "ret1",
        "oret1",
        "pen",
        "reason",
    },
    "volume": {"date", "type"},
    "chanlun": {"date", "type"},
}
SEAT_ATTRS = ("wr_win_rate", "wr_n")
EDGE_CHECKS = (
    ("SIMILAR_K", ("dist", "corr", "win_start", "win_end")),
    ("TRADED_ON", ("net_buy", "validStart")),
    ("HOLDS", ("hold_ratio", "hold_amount")),
    ("DRIVE", ("validStart", "group")),
)


def snapshot_cols(path):
    """arrow footer 只读列名（不载全量）。"""
    import pyarrow.parquet as pq

    try:
        return pq.ParquetFile(path).schema_arrow.names
    except Exception:
        return None


def probe_store(store, rel):
    """表目录（或单文件）探针：首尾分片 schema 并集。"""
    root = STORE_PATHS[store]
    target = os.path.join(root, rel)
    if os.path.isfile(target):
        files = [target]
    elif os.path.isdir(target):
        files = sorted(
            glob.glob(os.path.join(target, "**", "*.parquet"), recursive=True)
        )
    else:  # glob 模式（如 ashare/*.parquet）
        files = sorted(glob.glob(os.path.join(root, rel)))
        if not files:
            files = sorted(glob.glob(target))
    if not files:
        return None, "无分片"
    have = set()
    for f in {files[0], files[-1]}:
        cols = snapshot_cols(f)
        if cols:
            have |= set(cols)
    return (have or None), ("" if have else "schema 不可读")


def audit_parquet():
    """模块①②：底册 × 源列集交叉。返回 issues=[(级别,仓,表,消息)]。"""
    issues = []
    for (store, rel), consumers in sorted(CONSUMERS.items()):
        have, note = probe_store(store, rel)
        if have is None:
            issues.append(("SKIP", store, rel, note))
            continue
        for desc, want in consumers:
            miss = sorted(want - have)
            if miss:
                issues.append(
                    (
                        "FAIL",
                        store,
                        rel,
                        "%s 缺列 %s（源改名？查消费端）" % (desc, miss),
                    )
                )
    return issues


def audit_graph():
    """模块③：图内 presence。返回 (issues, reachable)。"""
    issues = []
    try:
        from kg_client import KgClient

        cli = KgClient(retry=1)
        with cli._session() as s:
            kinds = {
                r["k"]: r["c"]
                for r in s.run(
                    "MATCH (m:MarketEvent) RETURN m.kind AS k, count(*) AS c"
                ).data()
                if r["k"]
            }
            for kind in ME_KINDS:
                total = kinds.get(kind)
                if not total:
                    issues.append(("SKIP", "graph", kind, "kind 无节点"))
                    continue
                miss = []
                for a in ME_ATTRS:
                    if a not in ME_EXPECT.get(kind, ()):
                        continue
                    n = s.run(
                        "MATCH (m:MarketEvent {kind:$k}) WHERE m.%s "
                        "IS NOT NULL RETURN count(m) AS c" % a,
                        k=kind,
                    ).single()["c"]
                    if not n:
                        miss.append(a)
                if miss:
                    issues.append(("FAIL", "graph", kind, "消费属性 0 覆盖 %s" % miss))
            st = s.run("MATCH (s:Seat) RETURN count(s) AS c").single()["c"]
            for a in SEAT_ATTRS:
                n = s.run(
                    "MATCH (s:Seat) WHERE s.%s IS NOT NULL RETURN count(s) AS c" % a
                ).single()["c"]
                if st and not n:
                    issues.append(("FAIL", "graph", "Seat", "%s 0 覆盖" % a))
            for lbl, attrs in EDGE_CHECKS:
                t = s.run("MATCH ()-[r:%s]->() RETURN count(r) AS c" % lbl).single()[
                    "c"
                ]
                for a in attrs:
                    n = s.run(
                        "MATCH ()-[r:%s]->() WHERE r.%s IS NOT NULL "
                        "RETURN count(r) AS c" % (lbl, a)
                    ).single()["c"]
                    if t and not n:
                        issues.append(("FAIL", "graph", lbl, "%s 0 覆盖" % a))
        return issues, True
    except Exception as ex:
        return [("SKIP", "graph", "-", "Neo4j 不可达 %s" % str(ex)[:60])], False


def schema_snapshot(s):
    """K190-P3：全图 label/边类型计数快照（本体变更审计）。"""
    nodes = {
        r["lbl"]: r["c"]
        for r in s.run("MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c").data()
        if r.get("lbl")
    }
    edges = {
        r["t"]: r["c"]
        for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c").data()
    }
    return {"nodes": nodes, "edges": edges, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def schema_diff(cur, prev):
    """新增 label/边类型检出（本体变更审计：防旁路管线私加节点类）。"""
    issues = []
    for kind in ("nodes", "edges"):
        prev_keys = set((prev or {}).get(kind) or {})
        for k in sorted((cur.get(kind) or {})):
            if prev_keys and k not in prev_keys:
                issues.append(
                    (
                        "WARN",
                        "graph-schema",
                        kind,
                        "新增 %s（本体变更，确认是否报备）" % k,
                    )
                )
    return issues


def run_graph_schema(cli=None):
    """P3：schema 快照 + 与最近旧快照 diff + 滚动落盘（保留 8 份）。"""
    import kg_client as _kc

    cli = cli or _kc.KgClient(retry=1)
    reports = os.path.join(HERE, "reports")
    os.makedirs(reports, exist_ok=True)
    today = time.strftime("%Y%m%d")
    out = os.path.join(reports, "graph_schema_%s.json" % today)
    olds = sorted(glob.glob(os.path.join(reports, "graph_schema_*.json")))
    prev = None
    for old in reversed(olds):
        if os.path.basename(old) != os.path.basename(out):
            try:
                with open(old, encoding="utf-8") as f:
                    prev = json.load(f)
                break
            except Exception:
                continue
    issues = []
    try:
        with cli._session() as s:
            cur = schema_snapshot(s)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        for old in olds[:-7]:
            try:
                os.remove(old)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/source_col_audit.py", exc)
        issues = schema_diff(cur, prev)
        for lv, kind, sub, msg in issues:
            print("[%s] graph-schema %s/%s %s" % (lv, kind, sub, msg))
        print(
            "graph-schema: nodes=%d edge-types=%d 快照=%s"
            % (len(cur["nodes"]), len(cur["edges"]), os.path.basename(out))
        )
    except Exception as ex:
        issues = [("SKIP", "graph-schema", "-", str(ex)[:60])]
        print("[SKIP] graph-schema %s" % issues[0][3])
    return issues


def main():
    ap = argparse.ArgumentParser(description="数据源列对账")
    ap.add_argument("--json", metavar="PATH", default=None, help="报告落盘（json）")
    ap.add_argument("--no-graph", action="store_true", help="跳过图内 presence 模块")
    a = ap.parse_args()

    print("=== 数据源列对账（底册 %d 表）===" % len(CONSUMERS))
    pq_issues = audit_parquet()
    for lv, store, rel, msg in pq_issues:
        print("[%s] %s/%s %s" % (lv, store, rel, msg))
    n_ok = len(CONSUMERS) - sum(
        1 for i in pq_issues if i[0] != "OK" and False
    )  # OK 不进 issues
    print(
        "parquet 链：%d 表，FAIL %d / SKIP %d"
        % (
            len(CONSUMERS),
            sum(1 for i in pq_issues if i[0] == "FAIL"),
            sum(1 for i in pq_issues if i[0] == "SKIP"),
        )
    )

    if a.no_graph:
        g_issues, reachable = [("SKIP", "graph", "-", "--no-graph")], False
    else:
        g_issues, reachable = audit_graph()
        if reachable:
            # K190-P3：本体变更审计（schema 快照 diff，WARN 不计退出码）
            g_issues += run_graph_schema()
    for lv, store, rel, msg in g_issues:
        print("[%s] %s/%s %s" % (lv, store, rel, msg))
    if reachable:
        print("图内链：FAIL %d" % sum(1 for i in g_issues if i[0] == "FAIL"))

    all_issues = pq_issues + g_issues
    n_fail = sum(1 for i in all_issues if i[0] == "FAIL")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fails": [i for i in all_issues if i[0] == "FAIL"],
                    "skips": [i for i in all_issues if i[0] == "SKIP"],
                    "n_consumers": len(CONSUMERS),
                    "graph_reachable": reachable,
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
        print("报告: %s" % a.json)
    print(
        "=== 结论：%s（FAIL %d）===" % ("全绿" if not n_fail else "有断源嫌疑", n_fail)
    )
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
