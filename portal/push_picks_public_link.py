# -*- coding: utf-8 -*-
"""今日选股公网链接（带访问码）→ 飞书（个人 + 家人群）。

背景：Tailscale 链接只有装了 Tailscale 的设备能开；已发布鉴权公网版
（访问码 Cookie 门禁，反向代理剥 Authorization 头所以不用 Basic）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feishu_card_v2 import send_card, PORTAL_BASE, PORTAL_KEY, m_url  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
import push_targets  # noqa: E402  K-STOCK-403：收件人单一事实源

GROUP_CHAT_ID = push_targets.target_id("内部测试") or ""  # 家人群
BASE = PORTAL_BASE  # K-STOCK-305：域名/访问码收敛到 feishu_card_v2 单点维护
KEY = PORTAL_KEY


def build_card():
    l0 = ("**今日选股看板已上线公网**，手机/电脑**无需 Tailscale、无需局域网**，"
          "任何设备点开即用（数据带访问码门禁）。\n\n"
          "**访问码：`%s`**（首次打开输一次，30 天免输）" % KEY)
    # 快照口径从 todays_picks.json 实取（2026-10-18 修：原硬编码
    # "2026-10-07/基准 2026-09-30" 已过期，推出去的卡片会误导收件人）
    snap_run, snap_basis = "今日", "最新收盘"
    try:
        import json
        with open(os.path.join(HERE, "data", "todays_picks.json"),
                  encoding="utf-8") as f:
            _d = json.load(f)
        snap_run = str(_d.get("scan_run_date") or snap_run)
        snap_basis = str(_d.get("trade_basis") or snap_basis)
    except Exception:
        pass  # 读不到不外溢，用默认口径
    usage = ("**入口一（推荐）**：点下方「带访问码直达」按钮，自动带码进入\n\n"
             "**入口二**：打开 %s ，输入访问码\n\n"
             "**网页里看什么**：市场徽标 + 分布条 → 点任意一行下钻 → "
             "盈亏比/收盘分位/决策链等大白话解读\n\n"
             "数据快照：%s 强制扫描（%s）"
             % (BASE, snap_run, snap_basis))
    security = ("✅ 无访问码只看到 401 门禁页，数据不公开裸奔\n"
                "✅ 服务端白名单：只暴露选股页 + 选股数据 JSON，其他文件不可达\n"
                "✅ 公网域名走 HTTPS，口令传输加密\n"
                "⚠️ 访问码请勿转发到群外；泄露可在服务端改口令即时作废")
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "🔗 今日选股 · 公网链接上线（带访问码）"},
            "template": "green"},
        "body": {"elements": [
            {"tag": "markdown", "content": l0},
            {"tag": "hr"},
            {"tag": "collapsible_panel", "expanded": False,
             "header": {"title": {"tag": "plain_text", "content": "📱 使用方式"}},
             "border": {"color": "grey"}, "vertical_spacing": "8px",
             "padding": "8px 12px",
             "elements": [{"tag": "markdown", "content": usage}]},
            {"tag": "collapsible_panel", "expanded": False,
             "header": {"title": {"tag": "plain_text", "content": "🔐 安全口径"}},
             "border": {"color": "grey"}, "vertical_spacing": "8px",
             "padding": "8px 12px",
             "elements": [{"tag": "markdown", "content": security}]},
            {"tag": "hr"},
            {"tag": "button", "type": "primary", "size": "medium",
             "text": {"tag": "plain_text", "content": "带访问码直达"},
             "behaviors": [{"type": "open_url",
                            "default_url": "%s/?key=%s" % (BASE, KEY)}]},
            # 2026-10-25：移动端 H5 新版入口（自动签发；未启用则不出）
            *( [{"tag": "button", "type": "default", "size": "medium",
                 "text": {"tag": "plain_text", "content": "移动端新版"},
                 "behaviors": [{"type": "open_url",
                                "default_url": m_url("picks")}]}]
               if m_url("picks") else [] ),
            {"tag": "markdown", "content":
             "<font color='grey'>老田的AI工作站 · 今日选股看板（公网鉴权版 v1.1）</font>"},
        ]},
    }


def main():
    card = build_card()
    ok1, msg1 = send_card(card)
    print("personal:", ok1, "|", msg1)
    ok2, msg2 = send_card(card, receive_id=GROUP_CHAT_ID,
                          receive_id_type="chat_id")
    print("group:", ok2, "|", msg2)
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
