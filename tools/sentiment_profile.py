# -*- coding: utf-8 -*-
"""N1 舆情资料生成器：从本地数据取可复核的情绪/关注度信号（2026-11-16 WO-SENT-PROFILE）。

## 为什么

N1 席位此前恒定 `unknown`——工单里没有任何舆情/消息面字段。本地可用源经实测确认：

| 源 | 可用性 | 说明 |
|---|---|---|
| `irm_qa_sz.parquet`（深交所互动易） | **可用**，`001228.SZ` 有 210 条 | 有 ts_code，投资者提问 + 公司答复 |
| `stk_holdernumber.parquet` | **可用**，173 条 | 股东户数变化 → 筹码集中度 |
| `moneyflow.parquet` | 可用 | 大单/特大单净额 → 资金情绪 |
| `margin_detail.parquet` | **取决于标的**（非两融标的为 0 行） | 融资余额 |
| `guba/`（股吧） | 不可用 | 文件全是 0 字节 |
| `xueqiu/` | 不可用 | 只有配置，无快照 |
| `news.parquet` / `major_news.parquet` | **无 ts_code** | 只能按名称弱匹配，易误伤 → 本工具不用 |

## 取什么（只取可复核的计数与原文片段）

- 互动易：近 N 天提问数、最近 3 条问题摘要、答复覆盖率、风险词命中
- 股东户数：最新值 + 相比上期变化率（户数降 = 筹码集中）
- 资金流：近 5 日主力净额（大单+特大单）

**不做的**：不做情绪打分模型、不推断利好利空、不用名称匹配新闻（会误伤）。

    python -X utf8 tools/sentiment_profile.py 001228.SZ --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CURATED = REPO / "quant_backtest" / "data" / "tushare_curated"

QA_WINDOW_DAYS = 180          # 互动易只看近期窗口
RISK_WORDS = ("诉讼", "减持", "亏损", "问询", "退市", "质押", "商誉", "计提", "停牌", "违规")


def _read(name: str, cols: list[str] | None, code: str):
    import pyarrow.parquet as pq
    p = CURATED / ("%s.parquet" % name)
    if not p.is_file():
        return None
    try:
        tbl = pq.read_table(p, columns=cols, filters=[("ts_code", "=", code)])
        return tbl.to_pandas() if tbl.num_rows else None
    except Exception:  # noqa: BLE001
        return None


def _f(v):
    try:
        x = float(v)
        return None if x != x else round(x, 2)
    except (TypeError, ValueError):
        return None


def _qa_block(code: str, day: str) -> tuple[dict | None, list[str]]:
    warns: list[str] = []
    df = _read("irm_qa_sz", ["ts_code", "trade_date", "q", "a", "pub_time"], code)
    if df is None or df.empty:
        warns.append("irm_qa_sz 无记录（深市互动易或该标的未被提问）")
        return None, warns
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date")
    try:
        cutoff = (dt.datetime.strptime(day, "%Y-%m-%d") - dt.timedelta(days=QA_WINDOW_DAYS)).strftime("%Y%m%d")
    except ValueError:
        cutoff = "0"
    recent = df[df["trade_date"] >= cutoff]
    answered = recent[recent["a"].astype(str).str.len() > 5]         # 有实质答复
    texts = " ".join(str(x) for x in recent["q"].tolist())
    hits = [w for w in RISK_WORDS if w in texts]
    last = df.iloc[-1]
    block = {
        "total_qa": int(len(df)),
        "window_days": QA_WINDOW_DAYS,
        "window_qa": int(len(recent)),
        "window_answered": int(len(answered)),
        "last_date": str(last.get("trade_date")),
        "last_question": str(last.get("q"))[:120],
        "risk_word_hits": hits,
        "recent_questions": [{"date": str(r["trade_date"]), "q": str(r["q"])[:80]}
                             for _, r in df.tail(3).iterrows()],
    }
    if not hits:
        block["risk_word_note"] = "窗口内提问文本未命中 %d 个风险词" % len(RISK_WORDS)
    return block, warns


def _holder_block(code: str) -> tuple[dict | None, list[str]]:
    warns: list[str] = []
    df = _read("stk_holdernumber", ["ts_code", "ann_date", "end_date", "holder_num"], code)
    if df is None or df.empty:
        warns.append("stk_holdernumber 无记录")
        return None, warns
    df = df.copy()
    df["end_date"] = df["end_date"].astype(str)
    df = df.sort_values("end_date").drop_duplicates(subset=["end_date"])
    last = df.iloc[-1]
    out = {"end_date": str(last.get("end_date")), "holder_num": _f(last.get("holder_num"))}
    if len(df) >= 2:
        prev = df.iloc[-2]
        cur_n, prev_n = _f(last.get("holder_num")), _f(prev.get("holder_num"))
        if cur_n and prev_n:
            out["prev_end_date"] = str(prev.get("end_date"))
            out["prev_holder_num"] = prev_n
            out["change_pct"] = round((cur_n - prev_n) / prev_n * 100, 2)
            out["note"] = "户数下降=筹码趋于集中" if cur_n < prev_n else "户数上升=趋于分散"
    return out, warns


def _moneyflow_block(code: str, days: int = 5) -> tuple[dict | None, list[str]]:
    warns: list[str] = []
    cols = ["ts_code", "trade_date", "buy_lg_amount", "sell_lg_amount",
            "buy_elg_amount", "sell_elg_amount"]
    df = _read("moneyflow", cols, code)
    if df is None or df.empty:
        warns.append("moneyflow 无记录")
        return None, warns
    df = df.copy().sort_values("trade_date").tail(days)
    try:
        net = ((df["buy_lg_amount"] - df["sell_lg_amount"])
               + (df["buy_elg_amount"] - df["sell_elg_amount"]))
        vals = [round(float(v) / 1e4, 1) for v in net]
        block = {"window_days": days,
                 "last_date": str(df.iloc[-1]["trade_date"]),
                 "main_net_wan": round(float(net.sum()) / 1e4, 1),
                 "daily": [{"date": str(d), "net_wan": v}
                           for d, v in zip(df["trade_date"], vals)]}
        if all(abs(v) < 1e-6 for v in vals):
            # 全零不是"资金平衡"，是"没有大单记录/数据未更新"——必须说清，不能当 0 用
            warns.append("moneyflow 近%d日大单净额全为 0，可能无大单成交或数据未更新" % days)
            block["note"] = "全零，不作为资金情绪依据"
        return block, warns
    except Exception:  # noqa: BLE001
        return None, ["moneyflow 字段不完整"]


def profile(code: str, day: str | None = None) -> dict:
    day = day or dt.date.today().isoformat()
    out: dict = {"code": code, "source": "local_curated", "as_of": day,
                 "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    warns: list[str] = []
    for name, fn in (("qa", lambda: _qa_block(code, day)),
                     ("holders", lambda: _holder_block(code)),
                     ("moneyflow", lambda: _moneyflow_block(code))):
        block, w = fn()
        if block:
            out[name] = block
        warns.extend(w)
    out["available"] = any(k in out for k in ("qa", "holders", "moneyflow"))
    out["warnings"] = warns
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="N1 舆情资料（本地互动易/户数/资金流）")
    ap.add_argument("code")
    ap.add_argument("--day", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    p = profile(args.code, args.day)
    print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0 if p.get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
