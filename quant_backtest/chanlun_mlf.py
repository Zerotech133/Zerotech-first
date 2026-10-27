# -*- coding: utf-8 -*-
"""缠论信号特征化（E3 对接）：影子/灰度字段 → 训练就绪数据集 → LightGBM 基线。

定位（INTEL_RD_PLAN L787/L834 路线）：corroboration 特征化 → LightGBM
买卖点质量分 → 替代静态 confidence → E3 对接。本模块是特征化落地件。

纪律红线：
· 影子观察期产物——质量分只做排序实验，不进推送/拦截/选池；
· 特征只用信号时点已知信息（lag/影子标注/灰度重放），防前视；
· 不动 pool 历史行、不动图——灰度回填落独立 jsonl，联接时用。

三个命令（幂等，可独立跑）：
  python chanlun_mlf.py --backfill-gray   # closed 信号离线截断重放 enrich
  python chanlun_mlf.py --dataset         # pool×paper×灰度 → parquet
  python chanlun_mlf.py --train           # LightGBM 基线（时间切分）

── 灰度回填口径（因果安全） ──
对每个已平仓仓位，用「检出日截断」的 bars 重放 analyze+enrich：
信号在检出日结构里必然可见（scan/backfill 的检出定义），enrich 的
MACD 面积/DIF 极值/前序中枢全部取自截断窗内（信号时点之前），
与当日在线口径一致；已确认笔端点不随后续行情修订（分型确认即定格）。
落 data/chanlun_gray_backfill.jsonl，键 (code,type,dt) 幂等覆盖。

── 数据集口径 ──
pool 行 × paper 仓位按 (code, type, sig_dt) 联接：
· 标签 exe20 = closed.ret（已实现方向收益；跨除权 adj_flag 剔除）；
  open 仓位的盯市 pnl 不进训练集（未定型弱标签）；
· 特征（缺失三态 -1 编码，bool→{-1,0,1}）：
  type/reg/pv_state 类别；lag/npv/conf/n_prior 数值；
  mbei/bei/bei2/vv/tbe/div_area/div_dif/reg顺风 三态；
· kg_* 图谱截面特征（kg_features.parquet left join，无图节点票 NaN
  由 LightGBM 原生处理）：deg_total/deg_product/peer_industry/peer_sw_l1/
  peer_region/person_shared/peer_shared_prod/n_persons。
  ⚠ 前视污染声明：kg_features 为当前截面（度中心性/同业拥挤度等），
  对历史信号存在"未来图谱状态"泄漏；此类特征变化缓慢，影子实验可
  接受，但 AUC 提升不能直接归因，启用前须做时点切片复评；
· 输出 data/features/chanlun_sig_dataset.parquet。

── 训练口径 ──
二分类 win=ret>0；按 detect_date 时间切分前 80%/后 20%（防时序泄漏），
整体 AUC + 按 type 分层 AUC + 特征重要度；模型落 data/models/。
样本量 4-5k 属影子观察级：结论只用于「质量分方向是否可学」，不谈启用。
"""

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GRAY_BF = os.path.join(HERE, "data", "chanlun_gray_backfill.jsonl")
TREND_BF = os.path.join(HERE, "data", "chanlun_trend_backfill.jsonl")
DS_OUT = os.path.join(HERE, "data", "features", "chanlun_sig_dataset.parquet")
MODEL_DIR = os.path.join(HERE, "data", "models")

import chanlun_scan as cs  # 复用 _bars_from_parquet/_code_path/_load_pool
from chanlun_signal import analyze
from chanlun_gray import enrich, _prior_pivots
from chanlun_flow import aligned

GRAY_KEYS = (
    "div_area",
    "fv_area",
    "div_dif",
    "fv_dif",
    "pv_state",
    "pv_dir",
    "pv_fv",
    "conf",
)

KG_FEATS = (
    "deg_total",
    "deg_product",
    "peer_industry",
    "peer_sw_l1",
    "peer_region",
    "person_shared",
    "peer_shared_prod",
    "n_persons",
)


# ── 灰度离线回填 ──────────────────────────────────────────
def _load_gray_bf():
    out = {}
    if os.path.exists(GRAY_BF):
        with io.open(GRAY_BF, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    out[(r.get("code"), r.get("type"), r.get("dt"))] = r
                except Exception:
                    continue
    return out


def backfill_gray(limit=0):
    """closed 仓位信号 → 检出日截断重放 enrich → 灰度 jsonl。

    同票同截断点只 analyze 一次（信号按 detect_date 分组去重）；
    已回填键跳过（幂等续跑）；limit>0 只跑前 N 票（调试）。
    """
    led = cs._load_paper()
    closed = [
        p
        for p in led.get("closed", [])
        if not p.get("adj_flag") and p.get("ret") is not None
    ]
    done = _load_gray_bf()
    by_code = {}
    for p in closed:
        k = (p["code"], p["type"], p.get("sig_dt"))
        if k in done:
            continue
        by_code.setdefault(p["code"], []).append(p)
    codes = sorted(by_code)
    if limit > 0:
        codes = codes[:limit]
    print(
        "backfill_gray: %d closed usable, %d codes todo (done=%d)"
        % (len(closed), len(codes), len(done))
    )
    out_rows, n_drop = [], 0
    for ci, code in enumerate(codes):
        bars = cs._bars_from_parquet(cs._code_path(code))
        if not bars:
            n_drop += len(by_code[code])
            continue
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        # 截断点去重：同 detect_date 的仓位共享一次重放
        by_cut = {}
        for p in by_code[code]:
            i = idx.get(p.get("detect_date"))
            if i is None or i >= len(bars):
                n_drop += 1
                continue
            by_cut.setdefault(i, []).append(p)
        for cut, poss in sorted(by_cut.items()):
            try:
                sub = bars[: cut + 1]
                st = analyze(sub)
                enrich(sub, st)
                sigmap = {(s["type"], s["dt"]): s for s in st["signals"]}
            except Exception:
                n_drop += len(poss)
                continue
            for p in poss:
                sig = sigmap.get((p["type"], p.get("sig_dt")))
                if sig is None:  # 截断结构里不可见（口径漂移）
                    n_drop += 1
                    continue
                row = {
                    "code": code,
                    "type": p["type"],
                    "dt": p["sig_dt"],
                    "n_prior": len(_prior_pivots(st["pivots"], sig)),
                }
                for k in GRAY_KEYS:
                    row[k] = sig.get(k)
                out_rows.append(row)
        if (ci + 1) % 200 == 0:
            print("  ..%d/%d codes, +rows=%d" % (ci + 1, len(codes), len(out_rows)))
    if out_rows:
        merged = list(done.values()) + out_rows
        cs._write_jsonl(GRAY_BF, merged)
    print(
        "backfill_gray done: +%d rows (drop=%d), total=%d"
        % (len(out_rows), n_drop, len(done) + len(out_rows))
    )
    return out_rows


# ── 数据集构建 ────────────────────────────────────────────
def _tri(v):
    """三态 bool → {-1, 0, 1}（None 不可判=-1）。"""
    return -1 if v is None else int(bool(v))


def build_dataset():
    """pool × paper(closed) × 灰度回填 → parquet。"""
    import pandas as pd

    pool = cs._load_pool()
    led = cs._load_paper()
    gray = _load_gray_bf()
    closed = {(p["code"], p["type"], p.get("sig_dt")): p for p in led.get("closed", [])}
    rows = []
    for r in pool:
        pos = closed.get((r.get("code"), r.get("type"), r.get("dt")))
        if pos is None or pos.get("adj_flag") or pos.get("ret") is None:
            continue
        g = gray.get((r.get("code"), r.get("type"), r.get("dt"))) or {}
        rows.append(
            {
                "code": r["code"],
                "type": r["type"],
                "sig_dt": r["dt"],
                "detect_date": r.get("detect_date"),
                "exit_reason": pos.get("exit_reason"),
                "lag": r.get("lag"),
                "mbei": _tri(r.get("mbei")),
                "bei": _tri(r.get("bei")),
                "bei2": _tri(r.get("bei2")),
                "vv": _tri(r.get("vv")),
                "tbe": _tri(r.get("tbe")),
                "reg_fav": _tri(aligned(r.get("reg"), r.get("type"))),
                "npv": r.get("npv"),
                "div_area": _tri(g.get("div_area")),
                "div_dif": _tri(g.get("div_dif")),
                "pv_state": g.get("pv_state") or "na",
                "conf": g.get("conf"),
                "n_prior": g.get("n_prior"),
                "exe20": pos["ret"],
                "win": int(pos["ret"] > 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("dataset empty: closed×pool 联接 0 行")
    # None 混型列统一转 float（LightGBM 只认 int/float/bool）
    for c in ("lag", "npv", "conf", "n_prior", "exe20"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # kg_* 图谱截面特征（前视污染声明见模块 docstring；缺图票 NaN）
    kg_fp = os.path.join(HERE, "data", "features", "kg_features.parquet")
    if os.path.exists(kg_fp):
        kg = pd.read_parquet(kg_fp)
        kg = kg.rename(columns={c: "kg_" + c for c in KG_FEATS})
        df = df.merge(
            kg[["ts_code"] + ["kg_" + c for c in KG_FEATS]],
            left_on="code",
            right_on="ts_code",
            how="left",
        ).drop(columns=["ts_code"])
        print(
            "  kg join: %.0f%% codes matched"
            % (100.0 * df["kg_deg_total"].notna().mean())
        )
    # TR 批：trend 储备列（信号时点日线趋势，K30 回填）——只落列
    # 不进 feature_cols（旧模型列数锁定，下次重训再入特征表）
    tb = _load_jsonl_map(TREND_BF)
    df["trend_bf"] = [
        ((tb.get((r.code, r.type, r.sig_dt)) or {}).get("trend"))
        for r in df.itertuples()
    ]
    print("  trend join: %.0f%% matched" % (100.0 * df["trend_bf"].notna().mean()))
    os.makedirs(os.path.dirname(DS_OUT), exist_ok=True)
    df.to_parquet(DS_OUT, index=False)
    print("dataset: %d rows × %d cols -> %s" % (len(df), df.shape[1], DS_OUT))
    print(
        "  win rate %.1f%% | by type: %s"
        % (
            df["win"].mean() * 100,
            {
                t: "%d/%.0f%%" % (len(g), g["win"].mean() * 100)
                for t, g in df.groupby("type")
            },
        )
    )
    print(
        "  gray coverage: conf %.0f%% | pv_state %s"
        % (100.0 * df["conf"].notna().mean(), df["pv_state"].value_counts().to_dict())
    )
    return df


# ── trend 历史回填 + 三维对照（TR 批，K30） ────────────────
def _load_jsonl_map(fp):
    out = {}
    if os.path.exists(fp):
        with io.open(fp, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    out[(r.get("code"), r.get("type"), r.get("dt"))] = r
                except Exception:
                    continue
    return out


def backfill_trend(limit=0):
    """closed 信号检出日截断重放 → 信号时点日线趋势（up/down/range）。

    口径与 backfill_gray 同构（因果安全：已确认笔端点定格）；K24 _trend
    （HH/HL + 末笔 last_px 延伸修正，last_px=截断窗末价=检出日收盘）。
    独立 jsonl（不动灰度库）；幂等键 (code,type,dt) 续跑。
    """
    from chanlun_card import _trend

    led = cs._load_paper()
    closed = [
        p
        for p in led.get("closed", [])
        if not p.get("adj_flag") and p.get("ret") is not None
    ]
    done = _load_jsonl_map(TREND_BF)
    by_code = {}
    for p in closed:
        if (p["code"], p["type"], p.get("sig_dt")) in done:
            continue
        by_code.setdefault(p["code"], []).append(p)
    codes = sorted(by_code)
    if limit > 0:
        codes = codes[:limit]
    print(
        "backfill_trend: %d usable, %d codes todo (done=%d)"
        % (len(closed), len(codes), len(done))
    )
    out_rows, n_drop = [], 0
    for ci, code in enumerate(codes):
        bars = cs._bars_from_parquet(cs._code_path(code))
        if not bars:
            n_drop += len(by_code[code])
            continue
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        for cut, poss in sorted(_cuts_for(by_code[code], idx).items()):
            try:
                sub = bars[: cut + 1]
                st = analyze(sub)
                t = _trend(st.get("bis") or [], sub[-1]["c"])["state"]
            except Exception:
                n_drop += len(poss)
                continue
            for p in poss:
                out_rows.append(
                    {"code": code, "type": p["type"], "dt": p["sig_dt"], "trend": t}
                )
        if (ci + 1) % 300 == 0:
            print("  ..%d/%d codes, +rows=%d" % (ci + 1, len(codes), len(out_rows)))
    if out_rows:
        merged = list(done.values()) + out_rows
        cs._write_jsonl(TREND_BF, merged)
    print(
        "backfill_trend done: +%d rows (drop=%d), total=%d"
        % (len(out_rows), n_drop, len(done) + len(out_rows))
    )
    return out_rows


def _cuts_for(poss, idx):
    """仓位按检出日截断点分组去重（同 cut 共享一次重放）。"""
    by_cut = {}
    for p in poss:
        i = idx.get(p.get("detect_date"))
        if i is None or i >= len(idx):
            continue
        by_cut.setdefault(i, []).append(p)
    return by_cut


def trend_report():
    """(trend×type×exe20) 三维对照——trend=up 前置过滤的启用依据。

    影子结论口径：只报数据不启用；差异看 win 率差与 exe20 均值差，
    子集 n<50 只列不评。
    """
    tb = _load_jsonl_map(TREND_BF)
    if not tb:
        raise SystemExit("trend backfill empty: 先跑 --backfill-trend")
    led = cs._load_paper()
    closed = {
        (p["code"], p["type"], p.get("sig_dt")): p
        for p in led.get("closed", [])
        if not p.get("adj_flag") and p.get("ret") is not None
    }
    rows = []
    for k, p in closed.items():
        t = tb.get(k)
        if not t:
            continue
        rows.append((t["trend"], p["type"], p["ret"]))
    print("trend_report: %d/%d matched" % (len(rows), len(closed)))
    from collections import defaultdict

    g = defaultdict(list)
    for tr, ty, ret in rows:
        g[(tr, ty)].append(ret)
    types = sorted({ty for _, ty in g})
    print("\n%-6s %-4s %5s %7s %8s" % ("trend", "type", "n", "win%", "exe20%"))
    for tr in ("up", "range", "down"):
        agg = [r for (a, _), rs in g.items() if a == tr for r in rs]
        if agg:
            print(
                "%-6s %-4s %5d %6.1f%% %+7.2f%%   ← 全类型合计"
                % (
                    tr,
                    "ALL",
                    len(agg),
                    100.0 * sum(1 for r in agg if r > 0) / len(agg),
                    sum(agg) / len(agg),
                )
            )
        for ty in types:
            rs = g.get((tr, ty)) or []
            if not rs:
                continue
            win = 100.0 * sum(1 for r in rs if r > 0) / len(rs)
            avg = sum(rs) / len(rs)
            note = "" if len(rs) >= 50 else "  (n<50 仅列不评)"
            print(
                "%-6s %-4s %5d %6.1f%% %+7.2f%%%s" % (tr, ty, len(rs), win, avg, note)
            )
    # 买点类聚合对照（过滤启用的核心问题：up 买 vs 非up 买）
    for grp, name in (
        (("1买", "2买", "3买"), "买点类"),
        (("1卖", "2卖", "3卖"), "卖点类"),
    ):
        up = [r for (tr, ty), rs in g.items() if tr == "up" and ty in grp for r in rs]
        rest = [r for (tr, ty), rs in g.items() if tr != "up" and ty in grp for r in rs]
        if up and rest:
            wu = 100.0 * sum(1 for r in up if r > 0) / len(up)
            wr = 100.0 * sum(1 for r in rest if r > 0) / len(rest)
            print(
                "\n[%s] trend=up n=%d win=%.1f%% avg=%+.2f%% | "
                "非up n=%d win=%.1f%% avg=%+.2f%% | Δwin=%+.1fpp Δavg=%+.2fpp"
                % (
                    name,
                    len(up),
                    wu,
                    sum(up) / len(up),
                    len(rest),
                    wr,
                    sum(rest) / len(rest),
                    wu - wr,
                    sum(up) / len(up) - sum(rest) / len(rest),
                )
            )
    return rows


# ── 影子打分（scan 产线接入，S 批） ───────────────────────
def feature_cols():
    """训练/打分共用特征列（cat, num）——两端口径永不分叉。"""
    return (
        ["type", "reg_fav", "pv_state"],
        [
            "lag",
            "npv",
            "conf",
            "n_prior",
            "mbei",
            "bei",
            "bei2",
            "vv",
            "tbe",
            "div_area",
            "div_dif",
        ]
        + ["kg_" + c for c in KG_FEATS],
    )


_MODEL = None


def _booster():
    """模型懒加载（Booster C 端不吃中文路径 → temp ASCII 拷贝）。"""
    global _MODEL
    if _MODEL is None:
        import shutil
        import tempfile
        import lightgbm as lgb

        fp = os.path.join(MODEL_DIR, "chanlun_sig_lgb.txt")
        if not os.path.exists(fp):
            return None
        tmp = os.path.join(tempfile.gettempdir(), "chanlun_sig_lgb_rt.txt")
        shutil.copyfile(fp, tmp)
        _MODEL = lgb.Booster(model_file=tmp)
        os.remove(tmp)
    return _MODEL


def score_rows(rows):
    """给 pool 新信号行打分 → 附加 mls 字段（影子，不拦截不推送）。

    rows: _make_row 产出的 dict 列表（v4 起含灰度字段+n_prior）；
    特征口径与 --dataset 完全一致（三态 -1、reg_fav=aligned、kg_* join），
    缺列记 NaN 由 LightGBM 原生处理；失败静默返回 0 不阻断扫描。
    返回打分行数。
    """
    try:
        import pandas as pd

        m = _booster()
        if m is None or not rows:
            return 0
        cat_cols, num_cols = feature_cols()
        kg = None
        kg_fp = os.path.join(HERE, "data", "features", "kg_features.parquet")
        if os.path.exists(kg_fp):
            kg = pd.read_parquet(kg_fp).set_index("ts_code")
        recs = []
        for r in rows:
            rec = {
                "type": r.get("type"),
                "reg_fav": _tri(aligned(r.get("reg"), r.get("type"))),
                "pv_state": r.get("pv_state") or "na",
                "lag": r.get("lag"),
                "npv": r.get("npv"),
                "conf": r.get("conf"),
                "n_prior": r.get("n_prior"),
                "mbei": _tri(r.get("mbei")),
                "bei": _tri(r.get("bei")),
                "bei2": _tri(r.get("bei2")),
                "vv": _tri(r.get("vv")),
                "tbe": _tri(r.get("tbe")),
                "div_area": _tri(r.get("div_area")),
                "div_dif": _tri(r.get("div_dif")),
            }
            if kg is not None and r.get("code") in kg.index:
                k = kg.loc[r["code"]]
                for c in KG_FEATS:
                    rec["kg_" + c] = float(k[c])
            recs.append(rec)
        X = pd.DataFrame(recs)[cat_cols + num_cols]
        for c in cat_cols:  # 列序+dtype 与训练严格一致
            X[c] = X[c].astype("category")
        for c in num_cols:
            X[c] = pd.to_numeric(X[c], errors="coerce")
        for r, s in zip(rows, m.predict(X)):
            r["mls"] = round(float(s), 4)
        return len(rows)
    except Exception as e:
        print("score_rows skip: %s" % str(e)[:120])
        return 0


# ── LightGBM 基线 ─────────────────────────────────────────
def train():
    """时间切分 80/20 二分类基线；影子观察级结论（不启用）。"""
    import pandas as pd
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    df = pd.read_parquet(DS_OUT)
    df = df.sort_values("detect_date").reset_index(drop=True)
    cat_cols, num_cols = feature_cols()
    feats = cat_cols + num_cols
    X = df[feats].copy()
    for c in cat_cols:
        X[c] = X[c].astype("category")
    y = df["win"]
    n_tr = int(len(df) * 0.8)
    split_dt = df["detect_date"].iloc[n_tr]
    Xtr, Xte = X.iloc[:n_tr], X.iloc[n_tr:]
    ytr, yte = y.iloc[:n_tr], y.iloc[n_tr:]
    dtr = lgb.Dataset(Xtr, ytr, categorical_feature=cat_cols)
    dte = lgb.Dataset(Xte, yte, reference=dtr)
    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 40,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbose": -1,
        "seed": 42,
    }
    m = lgb.train(
        params,
        dtr,
        num_boost_round=300,
        valid_sets=[dte],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    pte = m.predict(Xte, num_iteration=m.best_iteration)
    auc = roc_auc_score(yte, pte)
    print(
        "train: n=%d (tr %d / te %d, split@%s) best_iter=%d"
        % (len(df), n_tr, len(df) - n_tr, split_dt, m.best_iteration)
    )
    print(
        "  test AUC = %.4f  (win rate %.1f%%, baseline AUC=0.5)"
        % (auc, yte.mean() * 100)
    )
    # 分层：测试集按 type
    dte_df = df.iloc[n_tr:].copy()
    dte_df["p"] = pte
    for t, g in dte_df.groupby("type"):
        if len(g) >= 30 and g["win"].nunique() > 1:
            print(
                "    %-3s n=%-4d AUC=%.4f win=%.0f%%"
                % (t, len(g), roc_auc_score(g["win"], g["p"]), g["win"].mean() * 100)
            )
    imp = sorted(zip(feats, m.feature_importance("gain")), key=lambda x: -x[1])
    print("  importance(gain): " + ", ".join("%s=%.0f" % (k, v) for k, v in imp[:8]))
    # 质量分分布（影子观察：高分段胜率是否抬升）
    qs = dte_df["p"].quantile([0.2, 0.4, 0.6, 0.8]).values
    lo = dte_df[dte_df["p"] < qs[0]]
    hi = dte_df[dte_df["p"] >= qs[-1]]
    print(
        "  score tail: bottom20%% win=%.1f%% (n=%d) | top20%% win=%.1f%% (n=%d)"
        % (
            lo["win"].mean() * 100 if len(lo) else -1,
            len(lo),
            hi["win"].mean() * 100 if len(hi) else -1,
            len(hi),
        )
    )
    os.makedirs(MODEL_DIR, exist_ok=True)
    fp = os.path.join(MODEL_DIR, "chanlun_sig_lgb.txt")
    # LightGBM C 端 fopen 不支持中文路径 → model_to_string + Python 落盘
    # （LF 换行必须保真，CRLF 会破坏树文本格式）
    with io.open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write(m.model_to_string(num_iteration=m.best_iteration))
    print("  model saved: %s" % fp)
    return auc


# ── 入口 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="chanlun signal ML features")
    ap.add_argument("--backfill-gray", action="store_true")
    ap.add_argument(
        "--backfill-trend",
        action="store_true",
        help="TR 批：closed 信号回填信号时点日线趋势",
    )
    ap.add_argument(
        "--trend-report", action="store_true", help="TR 批：(trend×type×exe20) 三维对照"
    )
    ap.add_argument("--dataset", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument(
        "--limit", type=int, default=0, help="backfill 系列调试：只跑前 N 票"
    )
    args = ap.parse_args()
    if args.backfill_gray:
        backfill_gray(limit=args.limit)
    if args.backfill_trend:
        backfill_trend(limit=args.limit)
    if args.trend_report:
        trend_report()
    if args.dataset:
        build_dataset()
    if args.train:
        train()


if __name__ == "__main__":
    main()
