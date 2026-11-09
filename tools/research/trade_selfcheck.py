# -*- coding: utf-8 -*-
"""模拟交易账本自检：溯源链、势档、可执行拆分、行业覆盖、持有期。

断言要能在口径被改坏时报错，否则等于没断言。`--degrade` 会故意抹掉溯源字段，
预期 T1/T5 精确报错——报不出来说明断言是空的。

用法::

    python -X utf8 tools/research/trade_selfcheck.py
    python -X utf8 tools/research/trade_selfcheck.py --degrade
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOOK = os.path.join(ROOT, "portal", "trade_book.json")
TRADES = os.path.join(ROOT, "portal", "trade_trades.json")
PY = sys.executable

RESULT = []


def chk(name, ok, msg):
    RESULT.append((name, bool(ok), msg))
    print("  [%s] %-4s %s" % ("OK" if ok else "!!", name, msg))


def load():
    book = json.load(open(BOOK, encoding="utf-8"))
    trades = json.load(open(TRADES, encoding="utf-8"))
    return book, trades


def t1_chain(book, trades):
    """每笔已平仓都要有四段日期，且顺序不能乱。

    这是「可溯源」的定义：拿不出一笔的完整链条，这条链就是断的。
    **空集不算通过**——如果一笔都没有，说明过滤或字段被改坏了，
    这时候报「断链 0 笔、通过」是最危险的假阳性（本断言第一次写就踩了这个坑）。
    """
    if not trades["rows"]:
        chk("T1", False, "已平 0 笔——没有可验证的链条，视为失败")
        return
    bad = []
    for r in trades["rows"]:
        if not r.get("sig_dt") or not r.get("detect") or not r.get("entry") or not r.get("exit"):
            bad.append((r["code"], "缺日期"))
            continue
        if not (r["sig_dt"] <= r["detect"] <= r["entry"] <= r["exit"]):
            bad.append((r["code"], "%s/%s/%s/%s" % (r["sig_dt"], r["detect"], r["entry"], r["exit"])))
    chk("T1", not bad, "已平 %d 笔的溯源链完整且顺序正确（断链 %d 笔%s）"
        % (len(trades["rows"]), len(bad),
           "，如 " + str(bad[:2]) if bad else ""))


def t2_regime(book):
    """势档必须与 20 日动量的符号一致：弱<−2、强>+2、其余中。"""
    bad = []
    for r in book["positions"] + book["sell_hints"]:
        m, g = r.get("mom20"), r.get("reg")
        if m is None or g is None:
            continue
        want = "弱" if m < -2 else ("强" if m > 2 else "中")
        if g != want:
            bad.append((r["code"], g, m))
    chk("T2", not bad, "势档与 20 日动量一一对应（不符 %d 笔%s）"
        % (len(bad), "，如 " + str(bad[:2]) if bad else ""))


def t3_split(book):
    """可执行 / 不可执行必须守恒，且不能重叠。"""
    st = book["stats"]
    ok = (st["n_buy"] + st["n_sell_hint"] == st["n_rows"]
          and st["ret_buy"]["n"] + st["ret_sell"]["n"] == st["n_closed"])
    chk("T3", ok, "多头 %d + 减仓提示 %d = 全部 %d；已平 %d + %d = %d"
        % (st["n_buy"], st["n_sell_hint"], st["n_rows"],
           st["ret_buy"]["n"], st["ret_sell"]["n"], st["n_closed"]))
    # 合并口径的 alpha 必须被「多头为负、卖点为正」解释，否则说明拆分没生效
    ok2 = st["alpha_buy"]["mean"] < st["alpha"]["mean"] < st["alpha_sell"]["mean"]
    chk("T3b", ok2, "alpha 排序：多头 %+.3f%% < 合并 %+.3f%% < 卖点 %+.3f%%（拆分生效）"
        % (st["alpha_buy"]["mean"], st["alpha"]["mean"], st["alpha_sell"]["mean"]))


def t4_industry(book):
    """行业覆盖率：分板块看股票的前提，缺太多就没法分组。"""
    rows = book["positions"] + book["sell_hints"]
    unk = sum(1 for r in rows if r.get("ind") in (None, "", "未分类"))
    cov = (len(rows) - unk) / len(rows) * 100 if rows else 0
    chk("T4", cov >= 90, "行业覆盖 %.1f%%（未分类 %d / %d，阈值 90%%）" % (cov, unk, len(rows)))


def t5_hold(book, trades):
    """已平仓的持有期不得超出台账规则太多（hold20，容差 25）。"""
    if not trades["rows"]:
        chk("T5", False, "已平 0 笔——持有期无从验证，视为失败")
        return
    over = [(r["code"], r["held"]) for r in trades["rows"]
            if r.get("held") is not None and r["held"] > 25]
    chk("T5", not over, "已平仓持有期 ≤25 日（越界 %d 笔%s）"
        % (len(over), "，如 " + str(over[:3]) if over else ""))


def t6_paper(book):
    """在途仓位的 in_paper 标记必须能对上产线台账，否则「真在途」是编的。"""
    paper = json.load(open(os.path.join(ROOT, "quant_backtest", "data",
                                        "chanlun_paper.json"), encoding="utf-8"))
    n_paper_open = len([r for r in (paper.get("open") or [])
                        if r["type"] in ("1买", "2买", "3买")])
    n_flag = sum(1 for r in book["positions"] if r.get("in_paper"))
    ok = abs(n_flag - n_paper_open) <= 5      # 允许少量口径差（full 重建 vs 产线）
    chk("T6", ok, "在途标记 %d 笔 vs 产线台账多头在途 %d 笔（差 %d，容差 5）"
        % (n_flag, n_paper_open, abs(n_flag - n_paper_open)))


def run_degrade(tmp):
    print("\n退化副本：抹掉溯源日期与出场日，预期 T1/T5 报错")
    out = os.path.join(tmp, "b.json")
    out2 = os.path.join(tmp, "t.json")
    rc = subprocess.run([PY, "-X", "utf8",
                         os.path.join(ROOT, "tools", "research", "trade_book.py"),
                         "--degrade", "--out", out, "--out-trades", out2],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if rc.returncode != 0:
        print(rc.stdout[-1500:])
        print(rc.stderr[-1500:])
        return 1
    global BOOK, TRADES
    BOOK, TRADES = out, out2
    book, trades = load()
    t1_chain(book, trades)
    t5_hold(book, trades)
    bad = [n for n, ok, _ in RESULT if not ok]
    if not bad:
        print("\n退化副本全过 —— 断言是空的，不可信")
        return 1
    print("\n退化副本按预期报错：%s" % ", ".join(bad))
    return 0


def main():
    ap_deg = "--degrade" in sys.argv
    print("=" * 88)
    print("模拟交易账本自检%s" % ("（退化副本）" if ap_deg else ""))
    print("=" * 88)
    if ap_deg:
        tmp = tempfile.mkdtemp(prefix="tb_chk_")
        return run_degrade(tmp)

    book, trades = load()
    t1_chain(book, trades)
    t2_regime(book)
    t3_split(book)
    t4_industry(book)
    t5_hold(book, trades)
    t6_paper(book)

    n_bad = sum(1 for _, ok, _ in RESULT if not ok)
    print("-" * 88)
    print("%d/%d 通过" % (len(RESULT) - n_bad, len(RESULT)))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
