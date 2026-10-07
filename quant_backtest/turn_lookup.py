# -*- coding: utf-8 -*-
"""前强补跌拐点查询（K35-b §2③ 命令块）：个股-板块超额曲线峰值分段。

口径：超额(t) = 个股累计涨幅%(t) - 板块累计涨幅%（归一化基准=窗口首日，
     与联动页 /bt-linkage 同源，复用 linkage.series）；
     峰值日 = 超额曲线最大值日（个股相对强度拐点=由强转弱分界）。
判读：峰前超额 ≥+10pp 且峰后大幅回吐 → ③ 前强补跌成立；
     峰前 ≤0 → 窗口内从未跑赢，③ 不成立（趋势性走弱）。

用法:
  python turn_lookup.py 301216              # 默认 500/250 双窗口对照
  python turn_lookup.py 301216.SZ --last 250
  python turn_lookup.py 301216 --block 881019
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkage


def _norm(code):
    c = code.strip().upper()
    if "." in c:
        return c
    return c + (".SH" if c.startswith(("6", "9")) else ".SZ")


def run_window(code, last, block=None, block_name=None):
    r = linkage.series(code, "1d", limit=last, block=block,
                       block_name=block_name)
    if not r.get("ok") or not r.get("block"):
        print("  取数失败：%s" % r.get("error", "block 线缺失"))
        return
    dts, s, b = r["dts"], r["stock"], r["block"]
    ex = [None if (x is None or y is None) else round(x - y, 2)
          for x, y in zip(s, b)]
    vi = [i for i, v in enumerate(ex) if v is not None]
    if len(vi) < 20:
        print("  有效样本不足（%d）" % len(vi))
        return
    i0 = vi[0]
    pk = max(vi, key=lambda i: ex[i])
    blk = r["meta"].get("block", {})
    print("  窗口 %d 根：%s → %s（板块 %s %s）" % (
        len(vi), dts[i0], dts[-1], blk.get("name"), blk.get("code")))
    print("  超额峰值：%s %+0.1fpp（个股 %+0.1f%% vs 板块 %+0.1f%%）" % (
        dts[pk], ex[pk], s[pk], b[pk]))
    print("  超额现值：%+.1fpp（个股 %+.1f%% vs 板块 %+.1f%%）" % (
        ex[-1], s[-1], b[-1]))
    # 最后一次由正转负穿越日
    cross = None
    for i in range(1, len(ex)):
        if ex[i - 1] is not None and ex[i] is not None \
                and ex[i - 1] > 0 >= ex[i]:
            cross = dts[i]
    if cross:
        print("  超额由正转负：%s" % cross)
    # 分段
    pre_s, pre_b = s[pk] - s[i0], b[pk] - b[i0]
    post_s, post_b = s[-1] - s[pk], b[-1] - b[pk]
    print("  峰前段（%s→%s）：个股 %+.1f%% 板块 %+.1f%% 超额 %+.1fpp" % (
        dts[i0], dts[pk], pre_s, pre_b, pre_s - pre_b))
    print("  峰后段（%s→%s）：个股 %+.1f%% 板块 %+.1f%% 超额 %+.1fpp" % (
        dts[pk], dts[-1], post_s, post_b, post_s - post_b))
    verdict = ("前强成立（峰前超额 %+0.1fpp ≥+10pp）且峰后回吐 "
               "%+0.1fpp → ③ 前强补跌成立" % (pre_s - pre_b,
                                              post_s - post_b)
               ) if pre_s - pre_b >= 10 else (
        "峰前超额仅 %+0.1fpp，窗口内无显著前强 → ③ 不成立"
        "（趋势性走弱，非均值回归）" % (pre_s - pre_b))
    print("  判读：" + verdict)
    print()
    return {"pre_ex": pre_s - pre_b, "post_ex": post_s - post_b,
            "peak_dt": dts[pk]}


def main():
    ap = argparse.ArgumentParser(description="前强补跌拐点（超额峰值分段）")
    ap.add_argument("code")
    ap.add_argument("--last", type=int, default=0,
                    help="单窗口根数；缺省跑 500/250 双窗口")
    ap.add_argument("--block", default=None)
    ap.add_argument("--block-name", default=None)
    a = ap.parse_args()
    code = _norm(a.code)
    wins = [a.last] if a.last else [500, 250]
    for w in wins:
        print("=== 窗口 %d 日线 ===" % w)
        run_window(code, w, block=a.block, block_name=a.block_name)
    print("提示：拐点应与 §3 池内信号史互证（峰值日附近若现 3买=抢跑，"
          "峰后若现 3卖=结构确认）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
