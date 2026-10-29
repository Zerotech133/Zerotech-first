# -*- coding: utf-8 -*-
"""HK 影子信号源 v0.1（KSTOCK-HK-SIGNAL，2026-10-27 老田拍板 P1A/P2A/P3R1/P4默认）。

定位：给 hk_qingping 影子账本产生 run 产物（picks→pending），跑批仍由
``hk_market.py run`` 消费；**影子练习，不构成投资建议、不接实盘**。

策略（P2-A）：20 日动量轮动
  · 标的池（P1-A）：ETF 3 只 + 蓝筹 12 只（UNIVERSE）
  · 每日收盘算 mom = close/close[-20] - 1（Top3 买入；跌出 Top5 卖出）
  · 风险（P4）：单票 ≤20%、总仓 ≤95%（由 engine 风控执行）、固定止损 8%（本脚本先出 SELL 信号）
  · 持仓期退出 v0.1 暂不做（持仓天数需逐笔审计，等影子样本再定）

用法：
  python k_stock/paper/hk_signal.py                # --dry-run 默认：只打印计划，不落单
  python k_stock/paper/hk_signal.py --exec         # 生成 run JSON + exec_run 落 pending
  python k_stock/paper/hk_signal.py --exec --account hk_qingping --out <dir>

纪律：
  · 默认 dry-run 不写任何东西（只读湖 + 只读账本快照）；
  · --exec 才写 run JSON 并落 pending（写影子账本 hk_% 账户）；
  · 不改 hk_market.py 跑批；费用/可卖时序由 HK config（D1-A/D2-A）保证。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HK_DIR = REPO / "quant_backtest" / "data" / "hk"
DEFAULT_DB = HERE.parent / "data" / "paper.db"
DEFAULT_OUT = HERE.parent / "data" / "runs" / "hk"
ACCOUNT = "hk_qingping"

UNIVERSE = [
    "02800", "02828", "03033",             # ETF：盈富/国企/恒生科技
    "00700", "09988", "01810", "01299", "00005", "03690",
    "00981", "00992", "02269", "01347", "00100", "06869",
]
MOM_WINDOW = 20
TOP_N = 3
HOLD_TOP = 5
STOP_LOSS = 0.08


def _paper_db() -> Path:
    """与 hk_market._paper_db 同口径：HK_PAPER_DB 可指向副本库（B1）。"""
    override = os.environ.get("HK_PAPER_DB", "").strip()
    return Path(override) if override else DEFAULT_DB


def load_bars(code: str):
    """读单只 HK 日线 → list[dict]（ts_code/trade_date/close）；无文件返回 []。"""
    p = HK_DIR / ("%s.parquet" % code)
    if not p.is_file():
        return []
    import pandas as pd  # 惰性：仅 I/O 边界

    df = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
    df = df.sort_values("trade_date")
    return df.to_dict("records")


def compute_momentum(bars, window: int = MOM_WINDOW):
    """返回 (mom, last_close, asof_date)；bar 不足返回 (None, last_close, asof)。"""
    if not bars:
        return None, None, None
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    asof = str(bars[-1]["trade_date"])
    if len(closes) < window + 1:
        return None, (closes[-1] if closes else None), asof
    last, prev = closes[-1], closes[-1 - window]
    if prev <= 0:
        return None, last, asof
    return last / prev - 1.0, last, asof


def read_positions(db: Path, account: str = ACCOUNT) -> dict:
    """只读账本：{code: {qty, avg_cost}}（持仓 qty>0）。"""
    if not Path(db).is_file():
        return {}
    uri = "file:%s?mode=ro" % str(db).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT stock, qty, avg_cost FROM positions WHERE account_id=? AND qty>0",
            (account,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return {str(r[0]): {"qty": int(r[1]), "avg_cost": float(r[2] or 0)} for r in rows}


def build_plan(universe=None):
    """计算当日计划：返回 (payload, detail)。

    payload 为 exec_run 可消费的 run 列表：
      [{"stock": "00700.HK", "asof": ISO, "outputs": {"D1": {"action": ..., "confidence": ...}}}]
    """
    universe = universe or UNIVERSE
    ranked, skipped = [], []
    for code in universe:
        bars = load_bars(code)
        mom, last, asof = compute_momentum(bars)
        if mom is None:
            skipped.append({"code": code, "why": "bars不足或数据缺失"})
            continue
        ranked.append({"code": code, "mom": mom, "last": last, "asof": asof})
    ranked.sort(key=lambda x: x["mom"], reverse=True)
    if not ranked:
        return [], {"ranked": [], "skipped": skipped, "asof": None}
    asof = max(r["asof"] for r in ranked)
    asof_iso = "%s-%s-%sT15:30:00" % (asof[0:4], asof[4:6], asof[6:8])
    top = ranked[:TOP_N]
    hold = {r["code"] for r in ranked[:HOLD_TOP]}
    pos = read_positions(_paper_db())
    payload, detail = [], []
    for r in ranked:
        stock = r["code"] + ".HK"
        held = pos.get(r["code"])
        action = "HOLD"
        conf = round(max(0.0, min(1.0, 0.5 + r["mom"] * 5)), 3)
        if held and r["last"] is not None and held["avg_cost"] > 0:
            pnl = r["last"] / held["avg_cost"] - 1.0
            if pnl <= -STOP_LOSS:
                action, conf = "SELL", 1.0
                detail.append({"code": r["code"], "action": action, "why": "止损 %.1f%%" % (pnl * 100)})
            elif r["code"] not in hold:
                action, conf = "SELL", 0.9
                detail.append({"code": r["code"], "action": action, "why": "跌出Top%d" % HOLD_TOP})
            else:
                detail.append({"code": r["code"], "action": "HOLD", "why": "持有中且仍在Top%d" % HOLD_TOP})
        elif r["code"] in {t["code"] for t in top} and not held:
            action = "BUY"
            detail.append({"code": r["code"], "action": action, "why": "动量Top%d" % TOP_N})
        elif not held:
            detail.append({"code": r["code"], "action": "HOLD", "why": "未入Top%d" % TOP_N})
        payload.append(
            {"stock": stock, "asof": asof_iso,
             "outputs": {"D1": {"action": action, "confidence": conf}}}
        )
    return payload, {"ranked": ranked, "skipped": skipped, "asof": asof, "detail": detail}


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(HERE))
    dry_run = "--exec" not in argv
    account = ACCOUNT
    out_dir = DEFAULT_OUT
    for i, a in enumerate(argv):
        if a == "--account" and i + 1 < len(argv):
            account = argv[i + 1]
        if a == "--out" and i + 1 < len(argv):
            out_dir = Path(argv[i + 1])
    payload, meta = build_plan()
    asof = meta.get("asof") or datetime.now().strftime("%Y%m%d")
    print(json.dumps({
        "dry_run": dry_run,
        "asof": asof,
        "db": str(_paper_db()),
        "daily_rank": [
            {"code": r["code"], "mom": round(r["mom"] * 100, 2), "last": r["last"]}
            for r in meta.get("ranked", [])
        ],
        "skipped": meta.get("skipped", []),
        "plan": meta.get("detail", []),
    }, ensure_ascii=False, indent=1))
    if dry_run:
        print("[dry-run] 未写 run JSON、未落单（加 --exec 才执行）")
        return 0
    if not payload:
        print("[exec] 无可用信号（数据不足），退出")
        return 1
    from engine import PaperEngine  # noqa: PLC0415
    import hk_market as hm  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    run_path = out_dir / ("poc_hk_%s.json" % asof)
    run_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    hm.apply_hk_engine_waivers()
    eng = PaperEngine(_paper_db(), hm.HkMarket(), config=hm.hk_default_config(), account=account)
    r = eng.exec_run(run_path, allow_stale=False)
    print(json.dumps({"run_json": str(run_path), "orders": r.get("orders"),
                      "skipped": r.get("skipped")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
