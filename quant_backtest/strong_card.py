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

stale 闸门（10-19 事故补：strong_card --send 曾把 10-13 旧名单 119 只
「板块领涨」全量静默重推——scan 被守卫拦下时 strong_latest.json 停留
旧数据，card 步骤无 stale 校验照发）：
  --send 前校验待发名单数据日（strong_latest.json 顶层 date，与
  strong_scan.py 熔断判据 strong_scan.py:231 同源同字段）== 今日；
  过期/结构异常（date 空）一律拒发并经 guard_alert 写告警
  （closed on failure：闸门判不了 → 拦；告警链故障 → 仍拦）。
  逃生口 --force：跳过闸门强推，但必须先走 guard_alert 留痕
  （推送尝试失败也不阻断强推，与守卫告警「不反噬主流程」同款）。

CLI:
  python strong_card.py            # dry：打印完整卡（含观察名单组）JSON
  python strong_card.py --send     # 真发：只推板块领涨组（熔断过滤+stale 闸门）
  python strong_card.py --send --force   # 逃生口：绕过 stale 闸门（留告警痕）
  python strong_card.py --latest <path>   # 指定 strong_latest.json（默认 reports/）
"""
import argparse
import datetime as _dt
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

EXIT_STALE_BLOCK = 3   # 守卫拦截退出码（对齐 strong_scan.py freshness_guard）


def stale_check(date, today=None):
    """stale 闸门判据（closed on failure）：待发名单数据日必须==今日。

    判据与 strong_scan.py 熔断同源：strong_scan 落盘 summary 顶层
    "date"=today_str()（strong_scan.py:231 熔断、:325 落盘同一字段），
    故 date==今日 即「今日扫描产出的新鲜名单」。返回 (ok, 原因)：
      · date 空 / 非合法 ISO 日（load_latest 对裸数组名单返回 ""）→ 拦
      · date != 今日 → 拦（旧名单静默重推即 10-19 事故模式）
    """
    today = today or _dt.date.today().isoformat()
    d = str(date or "").strip()
    # 严格 ISO（YYYY-MM-DD）：strong_scan 落盘唯一格式；紧凑式/异形
    # 一律拦（fromisoformat 3.11+ 也吃 20261019，不能只靠它）。
    if len(d) != 10 or d[4] != "-" or d[7] != "-" \
            or not d[:4].isdigit() or not d[5:7].isdigit() \
            or not d[8:10].isdigit():
        return False, ("名单数据日异常（%r，非 YYYY-MM-DD）——结构可疑，"
                       "按 closed on failure 拦发" % d)
    try:
        _dt.date.fromisoformat(d)
    except ValueError:
        return False, ("名单数据日异常（%r，非合法日期）——结构可疑，"
                       "按 closed on failure 拦发" % d)
    if d != today:
        return False, ("名单数据日 %s ≠ 今日 %s（陈旧名单，禁推）"
                       % (d, today))
    return True, ""


def stale_alert(reason, date, today, forced=False):
    """闸门拦截告警：复用 guard_alert 原语（12h 去重/原子写/不反噬）。

    告警链任何故障都不上抛——告警缺席不能反过来打开闸门（closed on
    failure）；forced=True 时 reason 注记强推留痕。返回是否发送。
    """
    try:
        import guard_alert as _ga
        why = reason + ("（--force 强推放行，留痕）" if forced else "")
        ok, _ = _ga.guard_alert(
            "StrongCard", why,
            anchors={"名单数据日": str(date or "无（结构异常）"),
                     "今日": today},
            suggest="先跑 strong_scan.py 产出当日名单（数据未补齐时按其"
                    "告警建议处置）；确认名单无误后重跑 strong_card.py "
                    "--send；确需强推旧名单用 --force（会再留告警痕）",
            send=True)
        return ok
    except Exception as e:
        print("[stale 闸门] 告警推送异常（不影响拦截）: %s" % str(e)[:120],
              flush=True)
        return False


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
    ap.add_argument("--force", action="store_true",
                    help="逃生口：绕过 stale 闸门强推（仍留告警痕）")
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

    # stale 闸门（10-19 事故补）：--send 前校验名单数据日==今日，
    # 过期/结构异常拒发（exit 3 对齐 strong_scan 守卫拦截语义）并告警；
    # --force 逃生口留痕后放行。闸门先于熔断/推送执行。
    today = _dt.date.today().isoformat()
    ok_stale, why_stale = stale_check(date, today=today)
    if not ok_stale:
        stale_alert(why_stale, date, today, forced=a.force)
        if not a.force:
            print("[stale 闸门] %s —— 拒发（--force 可强推但留告警痕）"
                  % why_stale, flush=True)
            return EXIT_STALE_BLOCK
        print("[stale 闸门] %s —— --force 强推放行（已留告警痕）"
              % why_stale, flush=True)

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
