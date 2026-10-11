# -*- coding: utf-8 -*-
"""K-STOCK-122 M5 收口：复盘纪要 → 飞书手机推送。

读 review_minutes/ 指定日（缺省今日）纪要 JSON → feishu_card_v2.send_card
折叠卡（首屏总评 + 分析师 + 操盘手 + 分歧决议 + action_items）。
推送失败只记日志不阻塞（调方约定）。

用法：python push_review_minutes.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_DIR = HERE.parent / "data" / "review_minutes"
PORTAL = HERE.parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

from feishu_card_v2 import send_card  # noqa: E402


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace("**", "＊"))


def build_card(m: dict) -> dict:
    date = m.get("date") or datetime.now().strftime("%Y-%m-%d")
    mgr = m.get("manager") or {}
    ana = m.get("analyst") or {}
    trd = m.get("trader") or {}
    summary = m.get("minutes_narrative") or (
        "评分 %s（%s）· 发现 %s 项"
        % (mgr.get("score"), mgr.get("grade"),
           len(mgr.get("findings") or [])))
    n_acts = len(trd.get("actions") or [])
    dis = m.get("disagreements") or []
    items = m.get("action_items") or []

    def layer(title, md):
        return {"tag": "markdown", "content": "**%s**\n%s" % (title, md)}

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content":
            "**📋 投研团队复盘会 · %s**\n%s" % (date, _esc(summary))}},
        {"tag": "hr"},
        {"tag": "collapsible_panel",
         "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "① 分析师（策略建议）"}},
         "border": {"color_style": "BLUE"},
         "vertical_align": "top",
         "background_style": "default",
         "elements": [{"tag": "markdown", "content":
                       _esc(json.dumps(ana, ensure_ascii=False)[:600])
                       or "（无）"}]},
        {"tag": "collapsible_panel",
         "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "② 操盘手（盯盘动作 %s 笔）" % n_acts}},
         "border": {"color_style": "GREEN"},
         "elements": [{"tag": "markdown", "content": "\n".join(
             "- %s %s %s %s" % (a.get("account"), a.get("stock"),
                                a.get("side"), (a.get("reason") or "")[:40])
             for a in (trd.get("actions") or [])[:10]) or "（今日无动作）"}]},
        {"tag": "collapsible_panel",
         "expanded": bool(dis),
         "header": {"title": {"tag": "plain_text",
                              "content": "③ 博弈分歧与决议（%d）" % len(dis)}},
         "border": {"color_style": "ORANGE"},
         "elements": [{"tag": "markdown", "content": "\n".join(
             "- **%s**：经理「%s」\n  裁决：%s"
             % (_esc(d.get("on")), _esc(d.get("manager")),
                _esc(d.get("suggestion"))) for d in dis) or "（无分歧）"}]},
        {"tag": "collapsible_panel",
         "expanded": False,
         "header": {"title": {"tag": "plain_text",
                              "content": "④ 待办 action_items（%d）" % len(items)}},
         "elements": [{"tag": "markdown", "content": "\n".join(
             "- " + _esc(x) for x in items[:8]) or "（无）"}]},
        {"tag": "note", "elements": [{"tag": "plain_text", "content":
            "模拟盘 · 详见门户 /market-strategy 组合页"}]},
        # K-STOCK-122 联动：卡片按钮 → 机器人指令回路（点按即执行/查询）
        {"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text",
                                       "content": "📊 持仓线位/下指令"},
             "type": "primary", "value": {"tag": "er:list"}},
            {"tag": "button", "text": {"tag": "plain_text",
                                       "content": "📜 指令表"},
             "type": "default", "value": {"tag": "er:help"}},
        ]},
    ]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text",
                       "content": "复盘会纪要 · %s" % date}},
            "elements": elements}


def main(date: str | None = None) -> int:
    fp = MIN_DIR / ("%s.json" % (date or datetime.now().strftime("%Y-%m-%d")))
    if not fp.exists():
        print(json.dumps({"ok": False, "error": "no minutes: %s" % fp},
                         ensure_ascii=False))
        return 1
    m = json.loads(fp.read_text(encoding="utf-8"))
    card = build_card(m)
    ok, msg = send_card(card)
    print(json.dumps({"ok": bool(ok), "push": str(msg), "minutes": str(fp)},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 2 and
                          sys.argv[1] == "--date" else
                          (sys.argv[2] if "--date" in sys.argv else None)))
