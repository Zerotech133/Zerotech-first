# -*- coding: utf-8 -*-
"""§8 复评触发推送生成/发送器（K35-b §8 配套）。

读 reports/weak_track_<code6>.jsonl 最新行：
  命中触发 → 按 §8 SOP 生成结构化推送文案（触发/基线/分流动作/落款/
  结案提醒）；--send 时经飞书直发（outbox 缓存 + send_feishu.py，
  与 alert_fail_wrap.py 同口径）；
  无触发 → 提示后退出 0。

文案格式外置可调：默认读同目录 weak_track_alert_tpl.txt 渲染（# 开头
行为注释不推送），改格式=改模板文件，不动代码；模板缺失/损坏自动回退
内置默认。可用占位符：{code} {trig} {px_d} {px} {ex} {trend}
{actions} {ts}（{actions} 为按触发类型自动生成的分流动作行，内容仍由
代码保证正确性）。

weak_track_check.py 触发命中时自动调 build_alert+send_alert（任务
推送主通路，文案全文不截断）；手动场景（推送丢失重发/复核）CLI 直跑，
默认只打印不发送。

用法:
  python weak_track_alert.py 301216            # 看最新触发的 §8 文案
  python weak_track_alert.py 301216 --send     # 重发推送
  python weak_track_alert.py 301216 --tpl my.txt --send   # 用自定义模板
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(HERE), "reports")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
TPL = os.path.join(HERE, "weak_track_alert_tpl.txt")


def _actions(trig):
    """按触发类型生成分流动作行（模板只管放哪，内容在此保证正确）。"""
    acts = []
    if any("超额" in x for x in trig):
        acts.append("·触发1 turn_lookup 双窗口辨方向：个股涨=修复(情景A升级)"
                    " vs 板块跌=环境恶化(维持回避)")
    if any("信号" in x or "池" in x for x in trig):
        acts.append("·触发2 新信号：3买须过环境核验(本案判例 -34%)；"
                    "1/2买=情景B右侧候选需三线共振")
    if any("归因" in x for x in trig):
        acts.append("·触发3 归因反转：重跑 §2 相关命令块+公告人工核查")
    return "".join(a + "\n" for a in acts)


def _builtin(f):
    """内置默认格式（模板缺失/损坏时的回退，与模板文件同构）。"""
    return ("[WEAK TRACK] {code} 复评触发（按 §8 SOP）\n"
            "触发：{trig}\n"
            "基线 {px_d}：现价 {px}｜超额250 {ex}｜趋势 个/板/沪 {trend}\n"
            "第二步：\n{actions}第三步：§4 复评行+追记区落款；情景改判同步 §5/§0 关键位\n"
            "第四步：结案三选一 → Disable-ScheduledTask WeakTrackCheck\n"
            "(状态 {ts})").format(**f)


def build_alert(code, r, tpl=None):
    """按 §8 四步生成推送文案（r=状态流行 dict；tpl=自定义模板路径）。"""
    trig = r.get("trig") or []
    ex = r.get("ex250")
    f = {
        "code": code,
        "trig": "；".join(trig),
        "px_d": str(r.get("px_d", "?"))[5:],
        "px": r.get("px"),
        "ex": "%+.1fpp" % ex if ex is not None else "—",
        "trend": "/".join(str(x).replace("（均线缠绕）", "")[:4]
                          for x in (r.get("trend") or [])),
        "actions": _actions(trig),
        "ts": r.get("ts", "?"),
    }
    for p in ([tpl, TPL] if tpl else [TPL]):
        if p and os.path.isfile(p):
            try:
                text = "".join(l for l in
                               open(p, encoding="utf-8").read().splitlines(True)
                               if not l.lstrip().startswith("#"))
                return text.format(**f).rstrip("\n")
            except (KeyError, IndexError, ValueError):
                pass  # 模板坏 → 回退内置
    return _builtin(f)


def send_alert(msg):
    """与 alert_fail_wrap.py 同口径：outbox 缓存 + send_feishu.py。"""
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_track.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(msg)
    r = subprocess.run(
        [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    print((r.stdout or "").strip()[:60] or "sent")


def load_last(fp):
    last = None
    with open(fp, encoding="utf-8") as f:
        for line in f:
            try:
                last = json.loads(line.lstrip("\ufeff"))
            except Exception:
                pass
    return last


def main():
    ap = argparse.ArgumentParser(description="§8 复评触发推送")
    ap.add_argument("code", nargs="?", default="301216")
    ap.add_argument("--send", action="store_true", help="实际发送（默认仅打印）")
    ap.add_argument("--tpl", help="自定义模板路径（默认 weak_track_alert_tpl.txt）")
    ap.add_argument("--text", action="store_true",
                    help="文本版（默认飞书卡片 weak_track_card.alert_card）")
    a = ap.parse_args()
    c6 = a.code.strip().upper().split(".")[0]
    fp = os.path.join(REP, "weak_track_%s.jsonl" % c6)
    if not os.path.exists(fp):
        print("状态流不存在：%s" % fp)
        return 1
    r = load_last(fp)
    if not r:
        print("状态流为空")
        return 1
    if not (r.get("trig") or []):
        print("最新一行（%s）无触发，无需推送" % r.get("ts", "?"))
        return 0
    if not a.text:
        import weak_track_card as wcard
        card = wcard.alert_card(r)
        print(json.dumps(card, ensure_ascii=False, indent=1))
        if a.send:
            wcard.send_card(card)
        return 0
    msg = build_alert(r.get("code", c6), r, tpl=a.tpl)
    print(msg)
    if a.send:
        send_alert(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
