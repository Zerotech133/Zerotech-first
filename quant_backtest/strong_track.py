# -*- coding: utf-8 -*-
"""强于板块领涨识别（strong 产线 · D2 第 11 领域卡 · 阶段1 信号层）。

weak（弱于板块案例跟踪）的镜像产线：weak 跟踪立案个股何时收敛到值得
复评；strong 反转口径——识别显著跑赢板块的领涨个股，与 weak 形成
「强弱两只眼」，板块轮动从两份名单的迁移中自然浮现。

阶段1（本文件）：纯函数信号层 + 单股评估 CLI。
  · strong_ex        250 日超额达标判定（个股-板块，与 /bt-linkage 同源）
  · block_quorum     板块成员数门槛（≥min_n 才参与，防小板块噪声）
  · trend_bull       日线多头三线（必要条件）
  · strong_verdict   综合判定：强信号 / 逆板块强势（更有价值）/ 不构成
阶段2（待展开）：全板块池扫描循环 + 推送链（复用 alert_push/jev-gate/
  熔断模式）+ strategies.json 注册第 11 卡 + 门户专项页。

口径约定（与 weak_track_check 对齐）：
  · 超额 = 个股250日涨幅 - 板块250日涨幅（pp）；边界相等=触发
  · 趋势三线：多=last>ma20>ma60；样本<60 不可判
  · 板块空头背景下的达标个股标记「逆板块强势」——比顺势更强信号

用法:
  python strong_track.py 301216            # 单股评估（阶段1 CLI）
  python -m pytest test_strong_track_gold.py -q   # 金样本
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from codes import normalize_code as _norm  # noqa: E402  P1-2 统一代码归一

STRONG_EX_TH = 10     # 250 日超额达标阈值：≥ +10pp 显著跑赢板块
MIN_BLOCK_MEMBERS = 5  # 板块成员数门槛：≥5 只才参与（防小板块噪声）


def strong_ex(ex250, th=STRONG_EX_TH):
    """250 日超额达标：ex >= th（边界相等=达标）；None=不可算不达标。

    与 weak 的 trig_ex_converge 镜像：weak 找收敛（≥-5pp 回归正常），
    strong 找显著跑赢（≥+10pp 领涨）。
    """
    return ex250 is not None and ex250 >= th


def block_quorum(n_members, min_n=MIN_BLOCK_MEMBERS):
    """板块成员数门槛：n >= min_n（边界相等=达标）。"""
    return n_members is not None and n_members >= min_n


def trend_bull(closes):
    """日线多头三线：last > ma20 > ma60（样本 <60 不可判 → False）。

    与 weak 的 trend_tag 同口径（多/空/缠绕），此处只取布尔判定；
    弱势板块中的达标个股反而是更高价值信号（逆板块强势），
    故板块趋势只作标签不作门槛。
    """
    if not closes or len(closes) < 60:
        return False
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    return closes[-1] > ma20 > ma60


def strong_verdict(ex250, n_members, stock_trend_bull,
                   th_ex=STRONG_EX_TH, min_members=MIN_BLOCK_MEMBERS):
    """综合判定。返回 (is_strong, tag, reasons)。

    判定链：
      1. 超额未达标 或 不可算 → 不构成
      2. 板块成员数不足门槛 → 不构成（噪声板块）
      3. 个股日线多头 → 强信号「板块领涨」
      4. 个股非多头但超额显著 → 「逆板块强势」观察标签（超额先行，
         趋势未确认——提前量更高，但需人工复核，不进推送只进名单）
    """
    reasons = []
    if not strong_ex(ex250, th_ex):
        reasons.append("250日超额未达标（%s）"
                       % ("不可算" if ex250 is None else "%+.1fpp < %+.0fpp" % (ex250, th_ex)))
        return False, "不构成", reasons
    if not block_quorum(n_members, min_members):
        reasons.append("板块成员 %s < %d，噪声板块不参与"
                       % (n_members, min_members))
        return False, "不构成", reasons
    reasons.append("250日超额 %+.1fpp ≥ %+.0fpp" % (ex250, th_ex))
    reasons.append("板块成员 %d ≥ %d" % (n_members, min_members))
    if stock_trend_bull:
        reasons.append("个股日线多头确认")
        return True, "板块领涨", reasons
    reasons.append("个股趋势未确认多头——超额先行，进观察名单人工复核")
    return True, "逆板块强势", reasons


def main():
    ap = argparse.ArgumentParser(description="强于板块领涨识别（阶段1 单股评估）")
    ap.add_argument("code")
    ap.add_argument("--th-ex", type=float, default=STRONG_EX_TH,
                    help="超额达标阈值 pp（默认 %d）" % STRONG_EX_TH)
    ap.add_argument("--min-members", type=int, default=MIN_BLOCK_MEMBERS)
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()
    code = _norm(a.code)

    import linkage
    blk = linkage.resolve_block(code)
    if not blk:
        print("板块解析失败")
        return 2
    bc, bname = blk["code"], blk["name"]

    from chanlun_data import load_kline
    sk = load_kline(code, "1d", 500)
    sb = sk.get("bars") or []
    closes = [float(b.get("close", b.get("c"))) for b in sb]

    r = linkage.series(code, "1d", limit=250, block=bc)
    ex = round(r["stock"][-1] - r["block"][-1], 1) if r.get("ok") and r.get("block") else None
    try:
        n_members = linkage.block_member_count(bc)
    except Exception:
        n_members = None

    bull = trend_bull(closes)
    is_strong, tag, reasons = strong_verdict(ex, n_members, bull,
                                             a.th_ex, a.min_members)
    print("=== strong 单股评估：%s（板块 %s %s）===" % (code, bc, bname))
    for line in reasons:
        print("  · " + line)
    print("  >>> 判定：%s（%s）" % ("★ 强信号" if is_strong else "—", tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
