# -*- coding: utf-8 -*-
"""B 类批量入图：涨跌停名单 + 十大流通股东 + 股东户数特征。

情报学定位：
· limit_list_d → MarketEvent(kind=limit)：连板数/封单额/炸板次数是
  游资接力强度的直接指纹，与 candle 分时行为互补（candle=怎么封的，
  limit=封成什么样）。新边型 HAS_LIMIT。
· top10_floatholders → Organization-[HOLDS]->Security：流通股东结构
  （机构/牛散/拉萨系），与 TRADED_ON 龙虎榜形成"谁在持有 vs 谁在交易"
  对偶。新标签 Organization + 新边型 HOLDS。
· stk_holdernumber → 特征文件 data/kg_holder_num.parquet（户数环比
  变化率=筹码集中度信号），不入图（B 类定位"Security 属性/特征"）。
· cyq_perf 筹码分布 → 特征文件 data/kg_cyq.parquet（获利盘/成本区间
  宽度/20 日获利盘变化），不入图（同"密度考虑"原议：全历史 ~1170 万
  行，只留 20250901 起日分片窗口）。个股页筹码摘要消费此文件。
· moneyflow_hsgt 不入图：north_money 语义已变成交额（恒正，
  nine_dim 2026-09 教训），大盘级无标的可挂。

数据源：data/tushare/{limit_list_d, top10_floatholders,
stk_holdernumber, cyq_perf}/（python tushare_sync.py --batch bclass 拉取）。

口径：
· limit 入图池 = candle 890 标的池（stk_mins 文件名提取，两条分时
  线同池对齐）；evt_id = code:limit:yyyymmdd 幂等
· HOLDS 时序：validStart=end_date（报告期）开区间快照；边 upsert 的
  MATCH Security 天然过滤池外标的
· holders（总股本前十）拉数备用暂不入图：多为集团/国资背景股东，
  情报密度低于流通股东

用法：
  python kg_bclass.py build [--dryrun]   # 全量回填（limit 全部日分片
                                         #   + floatholders 全部期分片）
  python kg_bclass.py sync [20260930]    # 增量：拉当日 limit + 最新期
                                         #   holders/holdernumber 并入图
"""

import argparse
import glob
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S  # noqa: E402
from kg_client import KgClient  # noqa: E402
import linreg  # noqa: E402

TS_DIR = os.path.join(HERE, "data", "tushare")
MINS_DIR = os.path.join(HERE, "data", "tushare", "stk_mins")
HOLDER_FEAT = os.path.join(HERE, "data", "kg_holder_num.parquet")
CYQ_FEAT = os.path.join(HERE, "data", "kg_cyq.parquet")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pool():
    """limit 入图池：candle 890 标的（stk_mins 文件名提取，同池对齐）。"""
    return sorted(
        {
            f.split("_")[0] + "." + f.split("_")[1]
            for f in os.listdir(MINS_DIR)
            if f.endswith(".parquet")
        }
    )


# ── 源列断源防线（K161/K162）：r.get 静默容忍缺列是断源根因，
# 读入分片即比对消费列，缺列打 WARN（不 raise——历史分片列集参差），
# 批任务日志里当场可见，不再等 N 个月后消费端暴露。
def _warn_missing(df, want, src):
    miss = [c for c in want if c not in df.columns]
    if miss:
        print("WARN 源列缺失 %s: %s（tushare 改名？查消费端）" % (src, miss))
    return miss


# ── ① 涨跌停名单 → MarketEvent(kind=limit) ──────────────────
def _limit_rows(days=None):
    """读 limit_list_d 日分片 → (evs, edges)，池内过滤。"""
    pool = set(_pool())
    files = (
        [os.path.join(TS_DIR, "limit_list_d", "d_%s.parquet" % d) for d in (days or [])]
        if days
        else sorted(glob.glob(os.path.join(TS_DIR, "limit_list_d", "d_*.parquet")))
    )
    evs, edges = [], []
    for f in files:
        if not os.path.exists(f):
            continue
        df = pd.read_parquet(f)
        _warn_missing(
            df,
            (
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
            ),
            "limit_list_d",
        )
        for r in df.to_dict("records"):  # itertuples 的 name 列有改名陷阱
            tc = r.get("ts_code")
            if tc not in pool:
                continue
            d = str(r["trade_date"])
            evs.append(
                {
                    "evt_id": "%s:limit:%s" % (tc, d),
                    "kind": "limit",
                    "date": d,
                    "name": r.get("name"),
                    "close": r.get("close"),
                    "pct_chg": r.get("pct_chg"),
                    "amount": r.get("amount"),
                    "limit_times": r.get("limit_times"),
                    "limit_amount": r.get("limit_amount"),
                    "fd_amount": r.get("fd_amount"),
                    "first_seal_time": r.get("first_time"),
                    "last_seal_time": r.get("last_time"),
                    "open_times": r.get("open_times"),
                    "up_stat": r.get("up_stat"),
                    "limit_type": r.get("limit"),  # tushare 新版列名（U/D/Z）
                    "source": "limit_list_d",
                    "updated_at": _now(),
                }
            )
            edges.append(
                {
                    "src": tc,
                    "dst": "%s:limit:%s" % (tc, d),
                    "validStart": d,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "limit_list_d",
                    "updated_at": _now(),
                }
            )
    return evs, edges


# ── ② 十大流通股东 → Organization-[HOLDS]->Security ──────────
# 机构关键词过滤：holder_name 含海量自然人（2-4 字姓名），全入会把
# Organization 污染成 28 万人名节点；牛散重仓无法归一同名，不入
ORG_KEYS = (
    "公司",
    "银行",
    "基金",
    "证券",
    "保险",
    "信托",
    "资管",
    "结算",
    "有限",
    "集团",
    "国际",
    "资本",
    "合伙",
    "中央",
    "国资",
    "社保",
    "组合",
    "汇金",
    "财政",
    "政府",
    "大学",
    "研究院",
    "研究所",
    "投资",
    "控股",
    "资产管理",
    "自营",
    "理财",
    "年金",
    "计划",
)


def _is_org(nm):
    return any(k in nm for k in ORG_KEYS)


def _holds_rows(periods=None):
    """读 floatholders 期分片 → (orgs, edges)，机构型过滤。

    边 upsert 的 Cypher MATCH (s:Security) 找不到即静默跳过——
    天然只入图内标的，无需预过滤。
    """
    files = (
        [
            os.path.join(TS_DIR, "top10_floatholders", "p_%s.parquet" % p)
            for p in periods
        ]
        if periods
        else sorted(
            glob.glob(os.path.join(TS_DIR, "top10_floatholders", "p_*.parquet"))
        )
    )
    orgs, edges, skipped = [], [], 0
    for f in files:
        if not os.path.exists(f):
            continue
        df = pd.read_parquet(f)
        _warn_missing(
            df,
            ("ts_code", "end_date", "holder_name", "hold_ratio", "hold_amount"),
            "top10_floatholders",
        )
        seen = set()
        for r in df.to_dict("records"):
            nm = (r.get("holder_name") or "").strip()
            if not nm:
                continue
            if not _is_org(nm):
                skipped += 1
                continue
            if nm not in seen:
                seen.add(nm)
                orgs.append({"name": nm})
            edges.append(
                {
                    "src": nm,
                    "dst": r.get("ts_code"),
                    "validStart": str(r.get("end_date")),
                    "validEnd": S.VALID_END_OPEN,
                    "hold_ratio": r.get("hold_ratio"),
                    "hold_amount": r.get("hold_amount"),
                    "source": "top10_floatholders",
                    "updated_at": _now(),
                }
            )
    print("  机构过滤：跳过自然人 %d 行" % skipped)
    return orgs, edges


# ── ③ 股东户数 → 特征文件（环比变化率） ──────────────────────
def _holder_feat():
    """stk_holdernumber per_code 全历史 → 户数环比变化率特征 parquet。"""
    files = sorted(glob.glob(os.path.join(TS_DIR, "stk_holdernumber", "*.parquet")))
    files = [f for f in files if not os.path.basename(f).startswith("p_")]
    if not files:
        print("stk_holdernumber 无数据，跳过特征")
        return
    df = pd.concat(
        [
            pd.read_parquet(f, columns=["ts_code", "end_date", "holder_num"])
            for f in files
        ]
    )
    df = df.drop_duplicates(["ts_code", "end_date"]).sort_values(
        ["ts_code", "end_date"]
    )
    df["holder_num_chg"] = df.groupby("ts_code")["holder_num"].pct_change()
    df.to_parquet(HOLDER_FEAT, index=False)
    df.dropna(subset=["holder_num_chg"])
    print(
        "特征 %s：%d 标的 × %d 期（%s~%s）"
        % (
            os.path.basename(HOLDER_FEAT),
            df["ts_code"].nunique(),
            df["end_date"].nunique(),
            df["end_date"].min(),
            df["end_date"].max(),
        )
    )


def _cyq_feat():
    """cyq_perf 日分片 → 池内筹码特征 data/kg_cyq.parquet。

    每标的最新一行：winner_rate（获利盘%）+ wr_chg20（20 交易日获利盘
    变化，pp）+ cost_span（85-15 分位成本区间宽度/cost_50，筹码集中度
    越小越集中）+ weight_avg（平均成本）。全市场入文件，消费端自筛。
    """
    files = sorted(glob.glob(os.path.join(TS_DIR, "cyq_perf", "d_*.parquet")))
    if not files:
        print("cyq_perf 无数据，跳过特征")
        return
    df = pd.concat(
        [
            pd.read_parquet(
                f,
                columns=[
                    "ts_code",
                    "trade_date",
                    "cost_15pct",
                    "cost_85pct",
                    "cost_50pct",
                    "weight_avg",
                    "winner_rate",
                ],
            )
            for f in files
        ]
    )
    df = (
        df.dropna(subset=["winner_rate"])
        .drop_duplicates(["ts_code", "trade_date"])
        .sort_values(["ts_code", "trade_date"])
    )
    # 20 日获利盘变化（pp）：groupby shift 对齐交易日序列
    g = df.groupby("ts_code")
    df["wr_chg20"] = df["winner_rate"] - g["winner_rate"].shift(20)
    # cost_50pct=0 (休市日/新标的脏值) -> NaN, avoid ZeroDivisionError
    den = df["cost_50pct"].mask(df["cost_50pct"] <= 0)
    df["cost_span"] = ((df["cost_85pct"] - df["cost_15pct"]) / den).round(4)
    last = df.groupby("ts_code").tail(1)[
        ["ts_code", "trade_date", "winner_rate", "wr_chg20", "cost_span", "weight_avg"]
    ]
    last.to_parquet(CYQ_FEAT, index=False)
    print(
        "特征 %s：%d 标的（快照日 %s）"
        % (os.path.basename(CYQ_FEAT), len(last), last["trade_date"].max())
    )


# ── build / sync ────────────────────────────────────────────
def build(cli, dryrun=False):
    evs, edges = _limit_rows()
    by = {}
    for d in edges:
        k = d["validStart"][:6]
        by[k] = by.get(k, 0) + 1
    print(
        "limit：%d 事件/边（%d 个月，%s）"
        % (len(evs), len(by), dict(sorted(by.items())[-3:]))
    )
    if not dryrun and evs:
        for _e in evs:
            _e["data_source"] = "ts_top_list"  # F2-P2-b
        cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
        cli.upsert_edge(
            S.LBL_SECURITY,
            "ts_code",
            S.LBL_MARKET_EVENT,
            "evt_id",
            S.REL_HAS_LIMIT,
            edges,
        )

    orgs, hedges = _holds_rows()
    print(
        "HOLDS：%d 机构节点 / %d 边（近 %d 期）"
        % (len(orgs), len(hedges), len({e["validStart"] for e in hedges}))
    )
    if not dryrun and orgs:
        cli.upsert_nodes(S.LBL_ORGANIZATION, ["name"], orgs)
        cli.upsert_edge(
            S.LBL_ORGANIZATION, "name", S.LBL_SECURITY, "ts_code", S.REL_HOLDS, hedges
        )

    _holder_feat()
    _cyq_feat()

    if not dryrun:
        with cli._session() as s:
            nl = s.run(
                "MATCH ()-[r:%s]->() RETURN count(r) AS c" % S.REL_HAS_LIMIT
            ).single()["c"]
            nh = s.run(
                "MATCH ()-[r:%s]->() RETURN count(r) AS c" % S.REL_HOLDS
            ).single()["c"]
            no = s.run(
                "MATCH (n:%s) RETURN count(n) AS c" % S.LBL_ORGANIZATION
            ).single()["c"]
        print(
            "图谱：HAS_LIMIT %d 边 / HOLDS %d 边 / Organization %d 节点" % (nl, nh, no)
        )


def _recent_open_days(n=2):
    """本地 trade_cal 最近 n 个已过开市日（升序，去重）——cyq_perf 拉取窗。

    筹码是 T+1 延迟源（T 日数据当晚才出），晨链 06:20 拉"今日"必空，
    拉取窗必须锚定开市日历而非自然日。"""
    parts = sorted(glob.glob(os.path.join(TS_DIR, "trade_cal", "part_*.parquet")))
    if not parts:
        return []
    cal = pd.concat(
        [pd.read_parquet(p, columns=["cal_date", "is_open"]) for p in parts]
    )
    today = datetime.now().strftime("%Y%m%d")
    days = sorted({str(d) for d in cal[cal["is_open"] == 1]["cal_date"]})
    return [d for d in days if d <= today][-n:]


def _pull_one(api, params, path, cap=1000, keep_empty=True):
    """sync 用：单分片拉取（文件存在且非空跳过）。

    keep_empty=False（T+1 延迟源）：0 行空片视为缺失重拉覆盖，拉到
    0 行不落片——空片永久占位是断流根因（OPS 教训 2026-10-09）；
    用覆盖写而非删文件（Windows DeletePending 句柄滞留会让删除
    假成功，见同日排查）。"""
    import tushare_sync as TS

    if os.path.exists(path):
        if keep_empty:
            return
        try:
            if len(pd.read_parquet(path)) > 0:
                return
        except Exception:
            return  # 读不了的片不冒险覆盖
    df = TS.pull_paged(api, params, cap)
    if df.empty and not keep_empty:
        return
    df.to_parquet(path, index=False)


def sync(cli, date=None):
    """每日保鲜：拉当日 limit + 最新报告期 holders/holdernumber 入图。"""
    _jr = linreg.JobRun("kg_daily_sync", "bclass_sync").__enter__()  # X3 血缘
    date = date or datetime.now().strftime("%Y%m%d")
    _pull_one(
        "limit_list_d",
        {"trade_date": date},
        os.path.join(TS_DIR, "limit_list_d", "d_%s.parquet" % date),
    )
    evs, edges = _limit_rows([date])
    print("sync %s：limit 事件 %d（池内）" % (date, len(evs)))
    if evs:
        cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
        cli.upsert_edge(
            S.LBL_SECURITY,
            "ts_code",
            S.LBL_MARKET_EVENT,
            "evt_id",
            S.REL_HAS_LIMIT,
            edges,
        )
    _jr.edge("neo4j:HAS_LIMIT", "parquet:limit_list_d", transform="agg")  # X3 血缘

    # 最新报告期（当季/上季可能未出数：两期都试，文件存在跳过）
    y, mo = int(date[:4]), int(date[4:6])
    q = {
        1: "1231",
        2: "1231",
        3: "1231",
        4: "0331",
        5: "0331",
        6: "0331",
        7: "0630",
        8: "0630",
        9: "0630",
        10: "0930",
        11: "0930",
        12: "0930",
    }
    per = [
        "%04d%s" % (y, q[mo]),
        "%04d%s"
        % (
            y - 1 if mo <= 3 else y,
            {
                1: "0930",
                2: "0930",
                3: "0930",
                4: "1231",
                5: "1231",
                6: "1231",
                7: "0331",
                8: "0331",
                9: "0331",
                10: "0630",
                11: "0630",
                12: "0630",
            }[mo],
        ),
    ]
    for p in per:
        _pull_one(
            "top10_floatholders",
            {"period": p},
            os.path.join(TS_DIR, "top10_floatholders", "p_%s.parquet" % p),
            cap=5000,
        )
    orgs, hedges = _holds_rows(per)
    print("sync %s：HOLDS %d 机构 / %d 边" % (date, len(orgs), len(hedges)))
    if orgs:
        cli.upsert_nodes(S.LBL_ORGANIZATION, ["name"], orgs)
        cli.upsert_edge(
            S.LBL_ORGANIZATION, "name", S.LBL_SECURITY, "ts_code", S.REL_HOLDS, hedges
        )
    _jr.edge("neo4j:Organization", "parquet:top10_floatholders")  # X3 血缘
    _jr.edge("neo4j:HOLDS", "parquet:top10_floatholders", transform="agg")  # X3 血缘

    # holdernumber：ann_date 拉当日新披露，merge 回 per_code 文件
    import tushare_sync as TS

    try:
        nd = TS.pull_paged("stk_holdernumber", {"ann_date": date}, 5000)
    except Exception as e:
        nd = pd.DataFrame()
        print("  holdernumber ann_date 拉取跳过(%s)" % str(e)[:50])
    for code, g in nd.groupby("ts_code") if len(nd) else []:
        fp = os.path.join(
            TS_DIR, "stk_holdernumber", "%s.parquet" % code.replace(".", "_")
        )
        old = pd.read_parquet(fp) if os.path.exists(fp) else pd.DataFrame()
        pd.concat([old, g]).drop_duplicates(["ts_code", "end_date"]).to_parquet(
            fp, index=False
        )
    if len(nd):
        print(
            "  holdernumber 当日披露 %d 行 / %d 标的"
            % (len(nd), nd["ts_code"].nunique())
        )
    _holder_feat()
    _jr.edge(
        "parquet:holder_feat", "parquet:stk_holdernumber", transform="feat"
    )  # X3 血缘

    # cyq_perf：近 2 个已过开市日逐日补片（0 行不落片）+ 特征重刷。
    # T+1 延迟源：晨链拉"今日"必空且空片会永久占位（OPS 2026-10-09），
    # 锚定开市日 T-1 稳定入库；今日留给明晨链以 T-1 身份自愈。
    for d in _recent_open_days(2):
        _pull_one(
            "cyq_perf",
            {"trade_date": d},
            os.path.join(TS_DIR, "cyq_perf", "d_%s.parquet" % d),
            cap=6000,
            keep_empty=False,
        )
    _cyq_feat()
    # X3 血缘：特征落盘边 + 运行收尾
    _jr.edge("parquet:cyq_feat", "parquet:cyq_perf", transform="feat")  # X3 血缘
    _jr.rows_written = len(evs) + len(edges) + len(orgs) + len(hedges)  # X3 血缘
    _jr.finish("ok")  # X3 血缘（显式收尾，异常路径靠 __del__ 记 lost）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "sync"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    cli = KgClient(retry=1)
    cli.ensure_schema()  # 新增 org_name 约束幂等生效
    if a.cmd == "build":
        build(cli, dryrun=a.dryrun)
    else:
        sync(cli, a.args[0] if a.args else None)


if __name__ == "__main__":
    main()
