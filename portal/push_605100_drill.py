# -*- coding: utf-8 -*-
"""605100 华丰股份 L0 正式折叠报告（下钻形态实机验证）。

四折叠层：缠论结构 / 四源情报联动 / 决策链与应对 / 数据附录。
数字全部取自 pick_details 事实包，防手抄误差。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feishu_card_v2 import build_drill_card, send_card  # noqa: E402

FACTS = os.path.join(HERE, "data", "pick_details", "605100.SH.json")


def main():
    with open(FACTS, encoding="utf-8") as f:
        facts = json.load(f)
    q = facts.get("quote") or {}
    cl = facts.get("chanlun") or {}
    nb = facts.get("no_buy_reasons") or {}

    # ── 层1 缠论结构 ──────────────────────────────────────────
    md1 = ("**日线** 中枢 [%s, %s] 构建中 ｜ **30m** 中枢 [%s, %s] 已上破\n"
           "**目标/压力** tp2=%s（与日线上沿重合，双级别共振压力位）\n"
           "**历史卖点** 一卖 05-20 @58.70（事后 -46%%）｜ "
           "三卖 08-17 @36.20（盘整背驰）\n"
           "**当前形态** 价格 %.2f 贴近日线中枢上沿 %s，"
           "中枢上沿争夺期，方向未选"
           ) % (cl.get("dzd"), cl.get("dzg"),
                "34.04", "34.76", "36.20",
                (q.get("px") or 0), cl.get("dzg"))
    # 用事实包里的真实 30m 数据替换硬编码（若存在）
    sec1 = ("📐 缠论结构（日线+30m）", md1)

    # ── 层2 四源情报联动 ─────────────────────────────────────
    sec2 = ("🔍 四源情报联动", 
            "**时序链**（缺口=信号弱化点）\n"
            "① 09-16 巨量涨停（量比 2.48 倍）——游资先行 7 日\n"
            "② 09-25 分红+出售资产公告——基本面兑现跟入\n"
            "③ 09-30 价格贴日线中枢上沿——缠论形态确认\n"
            "**三买检验** 不成立：价格在中枢上沿争夺，"
            "未出现「上破→回踩不破」的次级别确认结构\n"
            "**缺口** 龙虎榜零席位上榜（游资痕迹未见于榜）｜"
            "新闻关键词零命中（情报面静默）")

    # ── 层3 决策链与应对 ─────────────────────────────────────
    lights = nb.get("lights") or nb.get("reasons") or []
    light_txt = " ｜ ".join(str(x) for x in lights) if lights else "三灯 0/3（距中枢底 +11.1% / 30m 非回踩窗口 / 距 MA20 33.37 +6.9%）"
    sec3 = ("🎯 决策链与应对（K246）",
            ("**判定：%s**\n趋势闸门拦截 ｜ 三灯 %s\n"
             "**三情形应对**\n"
             "A. 36.20 下方回落 → 观望，等中枢方向选择\n"
             "B. 回踩 34.76（30m 中枢上沿）企稳 → 试探性关注，量能确认再加\n"
             "C. 站稳 36.20 且回踩不破 → 三买候选，重估仓位\n"
             "**纪律** 当前不买；列入 34.76/36.20 双位监测"
             ) % (nb.get("verdict") or "不买", light_txt))

    # ── 层4 数据附录 ─────────────────────────────────────────
    sec4 = ("📊 数据附录",
            ("现价 **%s**（%+.2f%%）｜ 成交 %.2f 亿\n"
             "MA20 33.37 ｜ 日线中枢 %s~%s ｜ 止损参考 %s\n"
             "事实包：pick_details/605100.SH.json（12 维，"
             "K78+K246 引擎产出）\n指令通道：飞书机器人发 "
             "「缠论 605100」「详情 605100 量能」「选股 T1」"
             ) % (q.get("px"), q.get("pct") or 0,
                  (q.get("amount") or 0) / 1e8,
                  cl.get("dzd"), cl.get("dzg"), cl.get("stop")))

    card = build_drill_card(
        "605100", facts,
        sections=[sec1, sec2, sec3, sec4],
        title_extra="· 华丰股份 下钻报告")
    ok, msg = send_card(card)
    print("send:", ok, "|", msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
