# -*- coding: utf-8 -*-
"""P-B3 盘前晨报：昨日信号 + npv 分层 + 近24h事件 + 数据新鲜度 → 飞书。

纯模板聚合（无 LLM 依赖，定时可靠性优先）；幂等：每日一发，
data/morning_report_state.json 记 last_date（--force 忽略水位）。
发送（K-STOCK-356 起）：v1 互动卡优先（五节分区+台账/看板双按钮），
卡失败降级原纯文本；卡走 card_build.push_card，文本复用
news_collector/send_feishu.py（SEND_OK 口径）。

五节内容：
  ① 大盘研判（上证多级别缠论 · 纯引擎结构化零幻觉，
     口径权威单点 chanlun_market.py，同门户 /bt-chanlun 综合研判卡）
  ② 昨日入池信号（最近扫描日 pool 行；1卖·趋✓ 置顶强预警）
  ③ npv 分层一行（tgraph_left_tail le2/3p 先例左尾口径）
  ④ 近24h强事件（recent_events bull/bear 前8）
  ⑤ 数据新鲜度（data_health 滞后表）

用法：
  python morning_report.py --dry     # 只打印不发送不写水位
  python morning_report.py [--force] # 构建+发送（每日幂等）
定时：计划任务 23:00Z（=北京 07:00，UTC 锚定防冬夏令时漂移）
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATE = os.path.join(HERE, "data", "morning_report_state.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
SEND_PY = os.path.join(NEWS_DIR, "send_feishu.py")
DISCLAIMER = "个人研究晨报·非投资建议"


def _today8():
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(obj):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


# ── 五节内容（每节独立 try，失败降级占位） ───────────────────
def _sec_market():
    """① 大盘研判（上证 1d/30m/5m 缠论 · 纯引擎结构化零幻觉；
    口径权威单点 chanlun_market.py，与门户综合研判卡同源）。"""
    try:
        import chanlun_market as CM
    except Exception:
        return "① 大盘研判：模块不可用"
    brief = CM.levels()
    if not brief:
        return "① 大盘研判：引擎读数不可用"
    v, _ck, sem = CM.verdict(brief)
    bmap = {b["per"]: b for b in brief}
    seg = []
    for per in ("1d", "30m", "5m"):
        b = bmap.get(per)
        if b:
            seg.append("%s%s%s" % (b["label"], b["pos"], b["piv"]))
    px = bmap.get("1d", {}).get("px")
    head = ("① 大盘研判：%s（日线%s × 30m%s × 5m%s）"
            % (v,
               bmap.get("1d", {}).get("trend", "—"),
               bmap.get("30m", {}).get("trend", "—"),
               bmap.get("5m", {}).get("trend", "—")))
    lines = [head]
    if px:
        lines.append("  现价 %.0f ｜ %s" % (px, " · ".join(seg)))
    lines.append("  %s" % sem)
    keys = CM.key_levels(brief)
    if keys:
        lines.append("  🔑 %s" % " ｜ ".join(keys))
    # 🎯 综合研判块：判定+读数+语义+关键位之外的增量=操作倾向+环境闸门
    lines.append("  ── 🎯 综合研判 ──")
    _gate = CM.gate_line()
    lines.append("  倾向：%s%s"
                 % (CM.action_hint(_ck),
                    " ｜ %s" % _gate if _gate else ""))
    _plan = CM.trade_plan(brief)
    if _plan:
        _m = _plan["mgmt"]
        lines.append("  💼 仓位档：%s（%s）"
                     % (_plan["size"], _plan["ladder"]))
        lines.append("     分批：%s ｜ 上限：%s"
                     % (_m["steps"], _m["cap"]))
        lines.append("     调仓：%s" % _m["rebalance"])
        _dyn = CM.dynamic_pos(brief)
        lines.append("     🔄 动态调仓：目标 %s（≤%d%%）｜ 动作：%s"
                     % (_dyn["name"], _dyn["pct"], _dyn["action"]))
        if _plan["buy"]:
            lines.append("     买：%s" % " ｜ ".join(_plan["buy"]))
        if _plan["sell"]:
            lines.append("     卖：%s" % " ｜ ".join(_plan["sell"]))
    return "\n".join(lines)


def _sec_pool():
    """② 最近扫描日入池信号。"""
    from agent_tools import call as T
    d = (T("pool_rows", limit=5000).get("data") or {})
    rows = d.get("rows") or []
    last = d.get("last_date") or max(
        (r.get("detect_date") or "") for r in rows) if rows else ""
    day = [r for r in rows if (r.get("detect_date") or "") == last]
    if not day:
        return "② 最近扫描日(%s)：无新入池" % (last or "无记录")
    hot = [r for r in day if r.get("type") == "1卖" and r.get("tbe")]
    lines = ["② %s 入池 %d 个（1卖·趋✓ %d）" % (last, len(day), len(hot))]
    for r in hot[:5]:
        lines.append("  🚨 %s %s 1卖+趋✓ npv=%s" % (
            r.get("code"), r.get("name", ""), r.get("npv", "-")))
    for r in [x for x in day if x not in hot][:8]:
        lines.append("  · %s %s %s%s" % (r.get("code"), r.get("name", ""),
                                         r.get("type"),
                                         " npv=%s" % r.get("npv")
                                         if r.get("npv") is not None else ""))
    if len(day) - len(hot) > 8:
        lines.append("  …等 %d 个" % (len(day) - len(hot) - 8))
    return "\n".join(lines)


def _sec_npv():
    """③ npv 分层先例左尾一行。"""
    from agent_tools import call as T
    d = (T("tgraph_left_tail").get("data") or {})
    le, p3 = d.get("le2") or {}, d.get("3p") or {}
    if not (le.get("n") or p3.get("n")):
        return "③ npv分层：样本不足"
    return ("③ npv分层(先例左尾): ≤2枢 n=%s win%s%% | 3+枢 n=%s "
            "win%s%% <-30%%:%s例"
            % (le.get("n"), le.get("win"), p3.get("n"),
               p3.get("win"), p3.get("lt30")))


def _sec_events():
    """④ 近24h强事件。"""
    from agent_tools import call as T
    d = T("recent_events", n=8, days=1).get("data")
    evs = d if isinstance(d, list) else ((d or {}).get("rows") or [])
    if not evs:
        return "④ 近24h强事件：无"
    lines = ["④ 近24h强事件 %d 条：" % len(evs)]
    for e in evs[:8]:
        icon = "🟢" if e.get("bias") == "bull" else \
            "🔴" if e.get("bias") == "bear" else "⚪"
        lines.append("  %s %s [%s] %s" % (icon, e.get("ts", ""),
                                          e.get("tag", ""),
                                          (e.get("text") or "")[:40]))
    return "\n".join(lines)


def _sec_fresh():
    """⑤ 数据新鲜度（agent_tools data_health：kpi.fresh_bad + gaps）。"""
    d = {}
    try:
        from agent_tools import call as T
        d = (T("data_health").get("data") or {})
    except Exception:
        return "⑤ 数据新鲜度：健康检查不可用"
    kpi = d.get("kpi") or {}
    bad = kpi.get("fresh_bad")
    if bad is None:
        return "⑤ 数据新鲜度：健康检查不可用"
    if not bad:
        return "⑤ 数据新鲜度：核心表全部就绪 ✓"
    lines = ["⑤ 数据新鲜度：%d 表滞后 ⚠" % bad]
    gaps = d.get("gaps") or []
    for g in gaps[:3]:
        lines.append("  · %s" % (g if isinstance(g, str)
                                 else json.dumps(g, ensure_ascii=False)[:70]))
    return "\n".join(lines)


def build_sections():
    """五节内容（每节独立 try，失败降级占位）→ 列表。"""
    secs = []
    for fn in (_sec_market, _sec_pool, _sec_npv, _sec_events, _sec_fresh):
        try:
            secs.append(fn())
        except Exception as e:
            secs.append("%s 失败: %s" % (fn.__name__, str(e)[:80]))
    return secs


def build_text(secs):
    """聚合五节 → 晨报文本（卡片失败的降级保底形态）。"""
    head = "📊 盘前晨报 %s" % datetime.now(timezone.utc).astimezone(
        ).strftime("%Y-%m-%d %H:%M")
    return head + "\n" + "\n".join(secs) + "\n" + DISCLAIMER


def build_card_payload(secs):
    """K-STOCK-356 五节 → v1 互动卡（首行加粗分区 + 台账/看板双按钮）。

    v1 模式（不传 use_schema2）：K-STOCK-354 判例——按钮卡勿用 v2；
    按钮域名/访问码单点收敛 portal/feishu_card_v2（K-STOCK-305 判例）。"""
    blocks = []
    for s in secs:
        lines = s.split("\n")
        lines[0] = "**%s**" % lines[0]
        blocks.append("\n".join(lines))
    buttons = None
    try:
        sys.path.insert(0, os.path.abspath(
            os.path.join(HERE, "..", "portal")))
        from feishu_card_v2 import PORTAL_BASE, PORTAL_KEY
        buttons = [
            ("💼 交易台账",
             "%s/ledger.html?key=%s" % (PORTAL_BASE, PORTAL_KEY), "primary"),
            ("📈 选股看板",
             "%s/picks.html?key=%s" % (PORTAL_BASE, PORTAL_KEY), "default"),
        ]
    except Exception:
        pass  # 按钮可选组件：域名模块不可用→纯分区卡照发
    sys.path.insert(0, NEWS_DIR)
    from card_build import build_card
    return build_card(
        "📊 盘前晨报", "%s · 缠论研判 × 入池 × 事件" % datetime.now(
            timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "blue", blocks,
        footer="%s · 纯引擎结构化读数" % DISCLAIMER, buttons=buttons)


def _send_card(card):
    """复用 card_build.push_card 发互动卡（内部走 send_feishu interactive）。"""
    try:
        sys.path.insert(0, NEWS_DIR)
        from card_build import push_card
        return push_card(card)
    except Exception as e:
        print("card send error: %s" % str(e)[:120])
        return False


def _send(text):
    """复用 send_feishu.py 发文本；返回是否成功（SEND_OK 口径）。"""
    if not os.path.isfile(SEND_PY):
        print("send skip: %s not found" % SEND_PY)
        return False
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run([sys.executable, SEND_PY, tmp],
                           capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        return (r.stdout or "").strip().startswith("SEND_OK")
    except Exception as e:
        print("send error: %s" % str(e)[:120])
        return False
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="P-B3 盘前晨报")
    ap.add_argument("--dry", action="store_true", help="只打印不发送")
    ap.add_argument("--force", action="store_true", help="忽略水位重发")
    a = ap.parse_args()
    today = _today8()
    if not a.dry and not a.force and \
            _load_state().get("last_date") == today:
        print("already sent today (%s), skip (use --force to resend)"
              % today)
        return
    secs = build_sections()
    text = build_text(secs)
    print(text)
    if a.dry:
        return
    # K-STOCK-356 卡优先 → 文本降级保底（watermark 只看最终是否送达）
    ok = _send_card(build_card_payload(secs))
    if not ok:
        print("card FAIL -> text fallback")
        ok = _send(text)
    print("send: %s" % ("SEND_OK" if ok else "FAIL"))
    if ok:
        _save_state({"last_date": today,
                     "ts": datetime.now(timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ")})


if __name__ == "__main__":
    main()
