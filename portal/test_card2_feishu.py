# -*- coding: utf-8 -*-
"""飞书卡片 2.0 多层折叠卡测试：验证 collapsible_panel 下钻渲染。
用法: python test_card2_feishu.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
from send_feishu import send_content  # noqa: E402

CARD = {
    "schema": "2.0",
    "config": {"update_multi": True},
    "header": {
        "title": {"tag": "plain_text", "content": "605100 华丰股份 · 下钻测试卡 v0"},
        "subtitle": {"tag": "plain_text",
                     "content": "缠论×情报联动 · 9-30 收盘 35.83 (+3.7%)"},
        "template": "orange"},
    "body": {"elements": [
        {"tag": "markdown", "content":
            "**系统判定：不买**（趋势闸门+三灯 0/3）\n"
            "**关键位**：中枢 31.86~36.20 ｜ 止损 31.86 ｜ 现价距上沿 -1.0%"},
        {"tag": "hr"},
        {"tag": "collapsible_panel", "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "📈 第一层：缠论中枢与操作方案"}},
         "border": {"color": "grey"},
         "vertical_spacing": "8px", "padding": "8px 12px",
         "elements": [
             {"tag": "markdown", "content":
              "**日线中枢** 31.86~36.20（构建中）\n"
              "**30m 中枢** 34.04~34.76 已向上离开\n"
              "等幅测量 tp2=36.20 与日线上沿重合\n"
              "**操作**：贴 31.86 低吸 · 36.20 减仓做T · 破 31.86 走\n"
              "tp1 35.48 / tp2 36.20 / 止损距 -11.1%"}]},
        {"tag": "collapsible_panel", "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "🔍 第二层：四源情报联动"}},
         "border": {"color": "grey"},
         "vertical_spacing": "8px", "padding": "8px 12px",
         "elements": [
             {"tag": "markdown", "content":
              "**时序**：09-16 巨量2.48倍涨停（先手）→ 09-25 中期分红+卖资产公告 → "
              "09-30 价格贴中枢上沿\n"
              "**判定**：四源方向一致（偏多），节奏分歧在三买\n"
              "**缺口**：龙虎榜零席位 · 新闻14天零命中"}]},
        {"tag": "collapsible_panel", "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "📋 第三层：三情形应对与观察动作"}},
         "border": {"color": "grey"},
         "vertical_spacing": "8px", "padding": "8px 12px",
         "elements": [
             {"tag": "markdown", "content":
              "① 36.20 冲高回落 → 三卖复制，等回踩\n"
              "② 回踩 34.76 缩量企稳 → 三买雏形（资金滞留证据）\n"
              "③ 放量站稳 36.20 回踩不破 → 三买候选，三源共振出手\n"
              "**节后盯**：34.76 回踩质量 · 10-01 股东会利好出尽风险"}]},
        {"tag": "collapsible_panel", "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "🗂 第四层：数据附录（12维事实包）"}},
         "border": {"color": "grey"},
         "vertical_spacing": "8px", "padding": "8px 12px",
         "elements": [
             {"tag": "markdown", "content":
              "quote 35.83/+3.7% ｜ vol_events 1次(巨量) ｜ notices 10条\n"
              "lhb 空 ｜ entry_signal 空(三买未触发) ｜ in_pool 否\n"
              "事实包：portal/data/pick_details/605100.SH.json\n"
              "缠论池：2 条历史信号（数据基准 2026-09-30 收盘）"}]},
        {"tag": "hr"},
        {"tag": "markdown", "content":
            "<font color='grey'>老田的AI工作站 · 缠论引擎K78+K246 · 下钻能力测试卡</font>"},
    ]}}


def main():
    ok = send_content(json.dumps(CARD, ensure_ascii=False), "interactive")
    print("card2 test:", ok)


if __name__ == "__main__":
    main()
