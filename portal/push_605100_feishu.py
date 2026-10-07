# -*- coding: utf-8 -*-
"""推送 605100 华丰股份缠论分析卡片到飞书（复用当日事实包，不重抓）。
用法: python push_605100_feishu.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))

FACTS = os.path.join(HERE, "data", "pick_details", "605100.SH.json")

MD = """**605100 华丰股份 · 缠论分析**（数据基准 9-30 收盘 35.83，+3.7%）

**一、历史卖点（缠论池实录，事后均已验证）**
· 一卖 05-20 @58.70：日线顶背驰，事后 -46%，卖点精准
· 三卖 08-17 @36.20：反弹不回中枢+盘整背驰，确认下跌中继

**二、多级别中枢现状**
· 日线中枢 31.86~36.20（构建中）
· 30m 中枢 34.04~34.76 已向上离开，等幅测量 tp2=36.20 与日线上沿重合
· 现价 35.83 = 30m 目标位兑现区 + 日线压力区，上方空间基本打完

**三、操作方案（门户 K78 口径）**
· 止损 31.86（最近笔低点，跌破坚决走，-11.1%）
· tp1 35.48 / tp2 36.20
· 区间打法：贴 31.86 低吸、36.20 减仓做T

**四、策略判定：不买（2 条压制，1 条硬性）**
· 大级别趋势闸门：日线震荡+30m震荡，铁律只做上升趋势
· 关键位三灯 0/3：距中枢底 +11.1%、非回踩窗口、距 MA20(33.37) +6.9%

**五、三情形应对**
① 36.20 冲高回落 → 三卖结构复制，等回踩
② 回踩 34.76 不破缩量企稳 → 潜在三买雏形，待日线趋势转上升
③ 放量站稳 36.20 回踩不破 → 日线三买候选，三灯齐再放行
风险单点：31.86 一破中枢证伪，下看前测低点延伸。

—— 老田的AI工作站 · 缠论引擎（chanlun_pool + K78 多级别中枢 + K246 决策链）"""


def main():
    facts = json.load(open(FACTS, encoding="utf-8"))
    import portal_report as pr
    import runner as _R
    card = pr.build_card(facts, MD)
    ok = _R.push_feishu(json.dumps(card, ensure_ascii=False), card=True)
    print("push:", ok)


if __name__ == "__main__":
    main()
