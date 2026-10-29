# -*- coding: utf-8 -*-
"""§13 结案质量审计推送（weak 产线第 13 节 · K35-x 新增）。

§12 盯在案失速（超龄/停滞/失联），§13 盯已结案侧完整性——对每个
weak_close_*.md 五项审计，发现 §9 走到一半或数据残缺：

  1. 字段缺失：头部 ts/open/chg 任一空（§10/§11 依赖的解析字段）；
  2. 归档缺失：声明的 weak_track_<c6>.closed_<ymd>.jsonl 不在 reports/；
  3. 档案缺失：案例档案 weak_review_<c6>_<open>.md 不在 weak_cases/；
  4. 任务未停：结案后每日任务仍 Ready（B 类滚动新案重启同任务除外）；
  5. B 类新案缺：B 类结案但无「🔄 新案已建」行（生成失败/未生成）。

全部干净 → 打印提示 rc=0 不推送（不打扰）；有异常 → --send 推送
turquoise 审计卡（🧾）。默认预览。文案外置 weak_track_audit_tpl.txt
（判例沿用，坏模板回退内置）。

用法:
  python weak_track_audit.py                # 预览
  python weak_track_audit.py --send         # 推送审计卡
  python weak_track_audit.py --send --text  # 推送文本
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_summary as wts  # noqa: E402
import weak_track_watch as ww  # noqa: E402
import logging

REP = os.path.join(os.path.dirname(HERE), "reports")
CASES = os.path.join(REP, "weak_cases")
TPL = os.path.join(HERE, "weak_track_audit_tpl.txt")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

_FIX = {
    "字段缺失": "重跑 §9 结案（close md 头部解析字段不全）",
    "归档缺失": "检查 reports/ 归档改名是否执行（§9 第 3 步）",
    "档案缺失": "补案例档案或更正头部档案行",
    "任务未停": "Disable 每日任务（§9 第 2 步），或确认 B 类新案已建",
    "B 类新案缺": "手动跑命令块⑨ 生成新案（或补 --newcase 重推）",
}


def _task_ready(c6, tm):
    """该标的关联每日任务是否 Ready（含 301216 首例任务名特例）。"""
    keys = [k for k in tm if c6 in k]
    if c6 == "301216":
        keys.append("WeakTrackCheck")
    return any(tm.get(k) == "Ready" for k in keys)


def _ncase_line(cases_dir, md):
    """close md 尾部的 B 类新案行（🔄 新案已建 / 生成失败），无则空。"""
    try:
        txt = open(os.path.join(cases_dir, md), encoding="utf-8").read()
        for ln in reversed(txt.splitlines()):
            if ln.startswith("🔄"):
                return ln
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_audit.py", exc)
    return ""


def audit_cases(cases_dir=None, rep=None, task_map=None):
    """全部结案 md → 审计结果列表（issues 非空即异常）。"""
    cases_dir = cases_dir or CASES
    rep = rep or REP
    tm = task_map if task_map is not None else ww._task_map()
    out = []
    for c in wts.parse_cases(cases_dir):
        c6 = c["code"].split(".")[0]
        issues = []
        missing = [k for k in ("ts", "open") if not c[k]] + (
            ["chg"] if c["chg"] is None else []
        )
        if missing:
            issues.append("字段缺失:" + "+".join(missing))
        if c["arch"] and not os.path.isfile(os.path.join(rep, c["arch"])):
            issues.append("归档缺失")
        if c["open"] and not glob.glob(
            os.path.join(cases_dir, "weak_review_%s_%s.md" % (c6, c["open"]))
        ):
            issues.append("档案缺失")
        nc = _ncase_line(cases_dir, c["md"])
        nc_ok = nc.startswith("🔄 新案已建")
        if c["type"] == "B" and not nc_ok:
            issues.append("B 类新案缺")
        if _task_ready(c6, tm) and not (c["type"] == "B" and nc_ok):
            issues.append("任务未停")
        out.append(
            {
                "code": c["code"],
                "c6": c6,
                "name": c.get("name") or "",
                "type": c["type"],
                "issues": issues,
            }
        )
    return out


def audit_fields(cases):
    """审计结果 → 推送字段 dict（文案与卡片共用单一数据源）。"""
    bad = [c for c in cases if c["issues"]]
    return {
        "n": len(cases),
        "n_bad": len(bad),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "detail": "\n".join(
            "- %s %s（%s）：%s\n  → %s"
            % (
                c["code"],
                c["name"],
                c["type"],
                "、".join(c["issues"]),
                "；".join(_FIX[i.split(":")[0]] for i in c["issues"]),
            )
            for c in bad
        ),
    }


def build_audit(f):
    """审计字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(
                l
                for l in open(TPL, encoding="utf-8").read().splitlines(True)
                if not l.lstrip().startswith("#")
            )
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_audit.py", exc)
    return (
        "🧾 [WEAK AUDIT] 结案质量审计（§13）\n"
        "已结案 {n} 例｜异常 {n_bad} 例｜{ts}\n"
        "{detail}"
    ).format(**f)


def main():
    ap = argparse.ArgumentParser(description="§13 结案质量审计推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    cases = audit_cases()
    if not cases:
        print(
            "暂无已结案案例（%s 下无 weak_close_*.md）——§9 结案后本节才有数据" % CASES
        )
        return 0
    f = audit_fields(cases)
    if not f["n_bad"]:
        print(
            "结案质量审计全绿（%d 例，五项全过：字段/归档/档案/"
            "任务停/B类新案）" % f["n"]
        )
        return 0
    msg = build_audit(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.audit_card(f), ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.audit_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_audit.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
