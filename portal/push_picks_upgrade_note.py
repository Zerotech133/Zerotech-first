# -*- coding: utf-8 -*-
"""picks.html 两轮改版升级通告 → 飞书（个人 + 机器人在的群）。

内容：市场徽标/分布条/筛选 + 个股下钻 + 解读层（大白话翻译）。
目标：push_targets 双通道——个人 open_id + 家人群 chat_id（同 send_feishu 口径）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feishu_card_v2 import send_card, PORTAL_BASE  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
from send_feishu import USER_ID  # noqa: E402

GROUP_CHAT_ID = "oc_6b97bee8b5340e071d0fc10cc6c8569f"  # 家人群（news_config.push_targets[1]）


def build_card():
    l0 = ("**今日选股总览页（picks.html）两轮升级上线**：\n"
          "① 580 只标的**按市场可视化标识**（A股/北交所/港股/美股/加密）；\n"
          "② 个股**点击下钻**看详情，原始字段全部**翻译成大白话**。\n"
          "手机可直接点文末「门户详情页」按钮。")

    mkt = ("**徽标配色**：A股·沪/深 红 ｜ 北交所 黄 ｜ 港股 紫 ｜ 美股 蓝（含 GB_ 前缀备份源）｜ 加密 橙\n\n"
           "**顶部市场分布条**：A股 557（96.0%）｜ 美股 20（3.4%）｜ 加密 3（0.5%）\n\n"
           "**市场筛选按钮带计数**（全部/A股 557/美股 20/加密 3），与层级筛选、搜索可叠加。\n"
           "徽标按代码形态自动判别：.SH/.SZ=A股沪深，.BJ=北交所，.HK=港股，"
           "裸字母≤5位=美股，USDT=加密。")

    drill = ("点列表**任意一行**下钻：\n\n"
             "**头部**：层级 + 市场徽标 + 名称/代码/行业 + 现价涨跌\n\n"
             "**双刻度条**：日内区间（开盘/现价游标）＋ 缠论位阶（止损→TP1→TP2 绿黄红渐变标尺，标注中枢状态）\n\n"
             "**六宫格事实包**：共振四维 ｜ 缠论结论 ｜ 不买决策链 ｜ 筹码/板块 ｜ 公告/龙虎榜（**公告可点原文**）｜ 买点/量能事件\n\n"
             "美股同样有事实包（NVDA 等已验证，共振评分/缠论/决策链齐全）。")

    interp = ("**综合解读卡**（层级/结构/决策 三行说清）\n\n"
              "**盈亏比自动算**：至 TP1 / TP2 分别多少比 1，赔率不足会直说；TP 已兑现自动标注\n\n"
              "**收盘分位**：收在日内区间 ≥70% 分位=强势收盘，≤30%=弱势收盘\n\n"
              "**决策链口径**：共几条压制、是否含硬性否决（铁律一票否决）\n\n"
              "**悬停任意策略标签**出单条释义；页脚「📖 信号口径与解读词典」19 条全收录。")

    sample = ("**002011 盾安环境（T1 · 缠论1买）**：\n"
              "结构=日线下跌衰竭段第一类买点（左侧抄底，必须带止损纪律），参考介入价 12.6；\n"
              "位置=收在日内 11% 分位，弱势收盘；\n"
              "空间=盈亏比至 TP1 ≈ 0.59:1（赔率不足），TP1/TP2 已兑现；\n"
              "**决策=不买（4 条压制，含硬性否决）——等趋势与关键位补齐再动手。**")

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "🎯 今日选股页改版上线 · 市场徽标 + 下钻解读"},
            "template": "blue"},
        "body": {"elements": [
            {"tag": "markdown", "content": l0},
            {"tag": "hr"},
        ] + [
            {"tag": "collapsible_panel", "expanded": False,
             "header": {"title": {"tag": "plain_text", "content": t}},
             "border": {"color": "grey"}, "vertical_spacing": "8px",
             "padding": "8px 12px",
             "elements": [{"tag": "markdown", "content": md}]}
            for t, md in [
                ("🏷️ 市场徽标 + 分布条 + 市场筛选", mkt),
                ("🔍 个股下钻详情（点任意一行）", drill),
                ("💡 解读层：字段 → 大白话", interp),
                ("📌 实测样例：盾安环境 002011", sample),
            ]
        ] + [
            {"tag": "hr"},
            {"tag": "button", "type": "primary", "size": "medium",
             "text": {"tag": "plain_text", "content": "门户详情页"},
             "behaviors": [{"type": "open_url",
                            "default_url": "%s/picks.html" % PORTAL_BASE}]},
            {"tag": "markdown", "content":
             "<font color='grey'>老田的AI工作站 · 六策略强制扫描 · "
             "picks.html v3（市场徽标+下钻+解读层）</font>"},
        ]},
    }


def main():
    card = build_card()
    results = []
    ok1, msg1 = send_card(card)  # 个人（默认 open_id）
    print("personal:", ok1, "|", msg1)
    results.append(("个人", ok1, msg1))
    ok2, msg2 = send_card(card, receive_id=GROUP_CHAT_ID,
                          receive_id_type="chat_id")  # 家人群
    print("group:", ok2, "|", msg2)
    results.append(("家人群", ok2, msg2))
    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
