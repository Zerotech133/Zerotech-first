# -*- coding: utf-8 -*-
"""强于板块领涨名单 · 飞书卡片推送（strong 产线阶段2b）。

weak_track_card.py 的镜像：复用其 to2() 2.0 转换与 send_feishu.py
interactive 直发通路。数据源 strong_scan.py 产出的
reports/strong_latest.json（汇总，约定结构）：

    {
      "ts": "2026-10-08 15:45",
      "date": "20261008",
      "items": [
        {"ts": "...", "code": "301216", "name": "…", "block": "光模块",
         "ex250": 18.3, "tag": "板块领涨", "reasons": ["…"]},
        ...
      ]
    }

    （items 兼容别名 list/rows；顶层直接为数组时也接受）

分组纪律（strong_track.strong_verdict 三态口径）：
  · tag=板块领涨    → 推送组（--send 真发）
  · tag=逆板块强势  → 观察名单，只落盘不推送（超额先行趋势未确认，
                      需人工复核——strong_track 阶段1 纪律沿用）
  · tag=不构成      → 不进卡不落盘

每日熔断（对齐 subscribe_state daily cap 范式）：
  strong_state.json 记 {"date": "YYYYMMDD", "pushed": {code: ts}}——
  同一股票每日最多推送 1 次：--send 前过滤当日已推 code，发送后登记。
  状态文件为运行时产物，已进 .gitignore。

CLI:
  python strong_card.py            # dry：打印完整卡（含观察名单组）JSON
  python strong_card.py --send     # 真发：只推板块领涨组（熔断过滤）
  python strong_card.py --latest <path>   # 指定 strong_latest.json（默认 reports/）
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_card as wtc  # noqa: E402  (to2/_md/_f2 卡片组件范式)

NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
REP = os.path.join(HERE, "reports")
LATEST_FP = os.path.join(REP, "strong_latest.json")
STATE_FP = os.path.join(HERE, "strong_state.json")
OUTBOX = os.path.join(HERE, "outbox")

TAG_PUSH = "板块领涨"
TAG_WATCH = "逆板块强势"


def load_latest(fp=LATEST_FP):
    """读 strong_latest.json，返回 (date, items[])。结构容错见模块头。"""
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return "", data
    items = data.get("items") or data.get("list") or data.get("rows") or []
    return str(data.get("date") or ""), items


def load_state():
    """读熔断状态；跨日自动失效（date != 今天 → 视为空）。"""
    today = time.strftime("%Y%m%d")
    try:
        with open(STATE_FP, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            return st
    except Exception:
        pass
    return {"date": today, "pushed": {}}


def save_state(st):
    os.makedirs(HERE, exist_ok=True)
    with open(STATE_FP, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def _line(it):
    """名单单条 md 行：code name ｜ 板块 ｜ 超额 ｜ 首条理由。"""
    ex = ("%+.1fpp" % it["ex250"]) if it.get("ex250") is not None else "—"
    rs = (it.get("reasons") or [])
    r = ("；".join(str(x) for x in rs[:2])) if rs else ""
    return "- **%s %s** ｜ %s ｜ 超额 %s%s" % (
        it.get("code", "?"), it.get("name") or "—",
        it.get("block") or "—", ex, ("｜" + r) if r else "")


def strong_card(items, date):
    """完整卡（领涨组 + 观察名单组）。观察名单组仅落盘，发送时剥离。"""
    push = [x for x in items if x.get("tag") == TAG_PUSH]
    watch = [x for x in items if x.get("tag") == TAG_WATCH]
    els = [{"tag": "div", "fields": [
        wtc._f2("数据日", date or "—"),
        wtc._f2("板块领涨", "%d 只" % len(push)),
        wtc._f2("观察名单", "%d 只" % len(watch)),
        wtc._f2("生成", time.strftime("%Y-%m-%d %H:%M"))]}]
    if push:
        els += [{"tag": "hr"},
                {"tag": "div", "text": wtc._md(
                    "**板块领涨（推送组）**\n%s" % "\n".join(
                        _line(x) for x in push[:10]))}]
        if len(push) > 10:
            els.append({"tag": "div", "text": wtc._md(
                "*…另有 %d 只见 strong_latest.json*" % (len(push) - 10))})
    if watch:
        els += [{"tag": "hr"},
                {"tag": "div", "text": wtc._md(
                    "**观察名单（逆板块强势·不推送只落盘）**\n%s"
                    % "\n".join(_line(x) for x in watch[:10]))}]
    els.append({"tag": "note", "elements": [
        {"tag": "plain_text",
         "content": "口径：250日超额 ≥+10pp × 板块成员 ≥5 × 日线多头确认"
         "｜镜像 weak 产线，强弱两只眼｜数据源 reports/strong_latest.json"}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red",   # A股红涨：领涨=红
                   "title": {"tag": "plain_text",
                             "content": "🚀 强于板块领涨名单 · %d 只"
                             % len(push)}},
        "elements": els,
    }


def send_card(card):
    """卡片 dict → pending 文件 → send_feishu.py interactive 直发。"""
    card = wtc.to2(card)
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_strong_card.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(card, ensure_ascii=False))
    r = subprocess.run(
        [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"),
         tmp, "interactive"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    return (r.stdout or "").strip()[:80]


def main():
    ap = argparse.ArgumentParser(description="强于板块领涨名单 · 飞书卡片推送")
    ap.add_argument("--send", action="store_true", help="真发（默认 dry 打印）")
    ap.add_argument("--latest", default=LATEST_FP,
                    help="strong_latest.json 路径（默认 reports/）")
    a = ap.parse_args()

    if not os.path.exists(a.latest):
        print("strong_latest.json 不存在：%s（先跑 strong_scan.py）" % a.latest)
        return 1
    date, items = load_latest(a.latest)
    push = [x for x in items if x.get("tag") == TAG_PUSH]
    card = strong_card(items, date)
    print(json.dumps(card, ensure_ascii=False, indent=1))

    # 落盘：完整卡（含观察名单组）留档 outbox（观察名单只落盘不推送）
    os.makedirs(OUTBOX, exist_ok=True)
    snap = os.path.join(OUTBOX, "strong_card_%s.json"
                        % (date or time.strftime("%Y%m%d")))
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1)

    if not a.send:
        print("DRY：完整卡已落盘 %s（--send 只推板块领涨组）" % snap)
        return 0

    if not push:
        print("无板块领涨个股，不推送（观察名单只落盘）")
        return 0

    st = load_state()
    blocked = [x for x in push if x.get("code") in st["pushed"]]
    todo = [x for x in push if x.get("code") not in st["pushed"]]
    if not todo:
        print("熔断：板块领涨 %d 只今日均已推送过，跳过" % len(push))
        return 0
    send_items = todo[:10]
    r = send_card(strong_card(send_items, date))
    if "SEND_OK" in r:
        for x in todo:
            st["pushed"][x.get("code")] = time.strftime("%Y-%m-%d %H:%M")
        save_state(st)
    print("发送：%s｜本次 %d 只（熔断拦 %d 只）｜state=%s" % (
        r, len(send_items), len(blocked), STATE_FP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
