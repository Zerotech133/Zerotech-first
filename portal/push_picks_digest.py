# -*- coding: utf-8 -*-
"""今日选股 · 手机速览版直投（内容直投方案 A）。

门户 picks.html 关键内容 → 飞书折叠卡，手机免 Tailscale 直达：
首屏汇总 + T1 全量(76) + T2 全量(85) 折叠层 + 多策略命中样本(带理由)。
数字全部取自 todays_picks.json，不手抄。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from feishu_card_v2 import send_card  # noqa: E402

PICKS = os.path.join(HERE, "data", "todays_picks.json")
FACT = os.path.join(HERE, "data", "pick_details", "605100.SH.json")


def stock_line(s):
    """一只股票 → 一行 markdown。"""
    tags = []
    for src in (s.get("sources") or []):
        t = src.get("tag")
        if t:
            tags.append(t)
    tag_txt = (" · " + "/".join(tags[:2])) if tags else ""
    return "**%s** %s · %s · 命中×%s%s" % (
        s.get("code", "").split(".")[0], s.get("name", ""),
        s.get("industry", "-"), s.get("n_hit") or 1, tag_txt)


def layer_md(stocks):
    lines = [stock_line(s) for s in stocks]
    return "\n".join(lines) if lines else "（无）"


def main():
    d = json.load(open(PICKS, encoding="utf-8"))
    stocks = d.get("stocks") or []
    c = d.get("counts") or {}
    t1 = [s for s in stocks if s.get("tier") == "T1"]
    t2 = [s for s in stocks if s.get("tier") == "T2"]
    crowd = [s for s in stocks if s.get("tier") == "CROWD"]

    # 多策略命中样本（n_hit>=3），带命中理由
    multi = sorted([s for s in t1 if (s.get("n_hit") or 0) >= 3],
                   key=lambda x: -(x.get("n_hit") or 0))[:8]
    sample_lines = []
    for s in multi:
        reasons = []
        for src in (s.get("sources") or []):
            for r in (src.get("reasons") or [])[:1]:
                reasons.append(r)
        sample_lines.append("**%s** %s（×%s）：%s" % (
            s.get("code", "").split(".")[0], s.get("name", ""),
            s.get("n_hit"), "；".join(reasons[:2]) or "-"))

    verdict = ""
    if os.path.exists(FACT):
        nb = (json.load(open(FACT, encoding="utf-8"))
              .get("no_buy_reasons") or {})
        verdict = nb.get("verdict") or ""

    sections = [
        ("🔥 T1 策略共振 %d 只（多策略同日命中）" % len(t1), layer_md(t1)),
        ("💎 T2 高价值 %d 只" % len(t2), layer_md(t2)),
        ("⚠️ 拥挤警报 %d 只（K33：高分=拥挤非买入，回避/降仓）" % len(crowd),
         layer_md(crowd) if crowd else "今日无 alert 级拥挤票（mid/low band 不计入警报层）"),
        ("⭐ 多策略命中样本 TOP（≥3 策略，带理由）",
         "\n".join(sample_lines) if sample_lines else "（今日无 ≥3 策略命中）"),
        ("📖 分层口径",
         "**T1 共振** 多策略同日命中（核心观察池）\n"
         "**T2 高价值** 单策略但分值/信号强度高\n"
         "**⚠️ 拥挤警报** 九维 alert 级高分票——K33 实证高分=拥挤非买入，"
         "回避标记（2026-10-08 修正：band 票不再抬进 T2）\n"
         "**T3 单策略** 其余 %d 只（门户可查）\n"
         "基准：%s" % (c.get("T3_single") or 0,
                       (d.get("trade_basis") or "")[:40])),
    ]
    header_extra = "· 选股速览 %s" % (d.get("scan_run_date") or "")
    body = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "今日选股 %s" % header_extra},
            "template": "blue"},
        "body": {"elements": ([
            {"tag": "markdown", "content":
                "**全池 %d 只** ｜ T1 共振 **%d** ｜ T2 高价值 **%d** ｜ "
                "⚠ 拥挤警报 %d ｜ T3 单策略 %d\n基准 %s\n详情例：605100 华丰股份 "
                "（前一条下钻卡，判定：%s）"
                % (c.get("total") or len(stocks),
                   c.get("T1_resonance") or len(t1),
                   c.get("T2_high_value") or len(t2),
                   c.get("crowd_alert") or 0,
                   c.get("T3_single") or 0,
                   (d.get("trade_basis") or "")[:30],
                   verdict or "-")},
            {"tag": "hr"},
        ] + [
            {"tag": "collapsible_panel", "expanded": False,
             "header": {"title": {"tag": "plain_text", "content": t}},
             "border": {"color": "grey"}, "vertical_spacing": "8px",
             "padding": "8px 12px",
             "elements": [{"tag": "markdown", "content": md}]}
            for t, md in sections
        ] + [
            {"tag": "markdown", "content":
             "<font color='grey'>老田的AI工作站 · 六策略强制扫描 · "
             "手机速览版（门户完整版待 Tailscale 入网）</font>"}
        ])},
    }
    card = body
    # 2026-10-25 P2：统一分装（失败回落旧卡）
    try:
        from push_unified_adapter import adapt as _adapt
        from feishu_card_v2 import m_url as _m_url
        _u = _m_url("picks")
        _btn = [("📈 打开移动端选股", _u, "primary")] if _u else None
        _head = ("全池 %d 只 ｜ T1 共振 %d ｜ T2 高价值 %d ｜ 拥挤警报 %d ｜ T3 %d"
                 % (c.get("total") or len(stocks),
                    c.get("T1_resonance") or len(t1),
                    c.get("T2_high_value") or len(t2),
                    c.get("crowd_alert") or 0,
                    c.get("T3_single") or 0))
        card = _adapt(
            "picks", body, "picks", "今日选股速览", sections,
            ts=str(d.get("scan_run_date") or ""), source="选股引擎",
            footer="老田的AI工作站 · 六策略强制扫描 · 手机速览版",
            buttons=_btn, level="info",
            summary=_head,
            kpis=[("T1 共振", str(len(t1))), ("T2 高价值", str(len(t2))),
                  ("拥挤警报", str(len(crowd)))])
    except Exception as _ue:  # noqa: BLE001
        print("unified picks FAIL -> legacy:", str(_ue)[:100])
    ok, msg = send_card(card)
    print("send:", ok, "|", msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
