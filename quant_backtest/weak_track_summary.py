# -*- coding: utf-8 -*-
"""§10 案例库结案汇总推送（weak 产线第 10 节 · K35-u 新增）。

§9 结案是单案收尾；§10 是结案之上的汇总层：扫描 reports/weak_cases/
全部 weak_close_*.md（每案一份终版报告），解析头部元信息，聚合统计后
推送（结案分布/区间涨跌/超额修复/触发频率 + 明细列表）。

数据源为 md 头部元信息（K35-t 起每份结案报告固定含：标题类型行/
结案时间/区间涨跌+超额变化/触发计数），不重读 jsonl——单一事实源。

默认只打印预览（不动任何文件）；--send 才推送（飞书卡片默认，
--text 回退文本，通路复用 send_feishu / weak_track_card.send_card）。
文案外置 weak_track_summary_tpl.txt（判例：推送格式一律外置模板，
坏模板回退内置）。无已结案案例时空态提示退出（rc=0，不算异常）。

用法:
  python weak_track_summary.py                # 预览（文本+卡片 JSON）
  python weak_track_summary.py --send         # 推送卡片
  python weak_track_summary.py --send --text  # 推送文本
"""

import argparse
import glob
import json
import os
import re
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(HERE), "reports")
CASES = os.path.join(REP, "weak_cases")
TPL = os.path.join(HERE, "weak_track_summary_tpl.txt")

# 结案类型表述 → A/B/C（与 weak_track_close.CTYPE 反查一致）
_T2C = {"情景A兑现": "A", "情景B立案": "B", "利空实证": "C"}
_RE_TITLE = re.compile(r"^# 结案报告 · (\S+)(?: (\S+))?（(.+?)）")
_RE_CLOSE_TS = re.compile(r"^- 结案时间：(\d{4}-\d{2}-\d{2})")
_RE_CHG = re.compile(r"^- 区间涨跌：.*?（([+\-\d.]+)%）")
_RE_EXD = re.compile(r"·(转正|持平|收敛|走扩)）")
_RE_TRIG = re.compile(r"^- 触发：共 (\d+) 次")
_RE_OPEN = re.compile(r"^- 案例档案：weak_review_\d+_(\d{8})\.md")  # 立案日期
_RE_ARCH = re.compile(r"^- 状态流：(\S+?\.jsonl)（已归档）")  # 归档声明（§13）


_MIN_YEAR = 2015  # 本产线数据起点（更早的年份必为坏串）


def valid_day(s):
    """日期串是否可信：YYYY-MM-DD 且真实存在、年份落在 [_MIN_YEAR, 今天]。

    用于拦截行情源坏日期入库（历史坏例：px_d/ts = 2026-10-16，月份与
    日号越界；另有 02-30 这类"格式对但日期不存在"的串）。带时间的 ts
    串只取前 10 位判定。缺失（None/空）一律判 False —— 调用方需自行
    决定"缺失"是否算坏。
    """
    t = str(s or "").strip()[:10]
    if len(t) != 10 or t[4] != "-" or t[7] != "-":
        return False
    try:
        y, m, d = time.strptime(t, "%Y-%m-%d")[:3]
    except ValueError:
        return False
    return _MIN_YEAR <= y and (y, m, d) <= time.localtime()[:3]


def parse_cases(cases_dir=None):
    """扫描全部 weak_close_*.md → 案例列表（解析失败的文件跳过不炸）。"""
    d = cases_dir or CASES
    out = []
    for fp in sorted(glob.glob(os.path.join(d, "weak_close_*.md"))):
        try:
            c = {
                "md": os.path.basename(fp),
                "name": "",
                "chg": None,
                "exd": "",
                "ntrig": 0,
                "ts": "",
                "open": "",
                "arch": "",
            }
            for line in open(fp, encoding="utf-8"):
                if line.startswith("# 结案报告") and not c.get("code"):
                    m = _RE_TITLE.match(line.strip())
                    if m:
                        c["code"] = m.group(1)
                        c["name"] = m.group(2) or ""  # 无名称标题组为 None
                        ctype = m.group(3)
                        c["type"] = next(
                            (v for k, v in _T2C.items() if ctype.startswith(k)), "?"
                        )
                elif line.startswith("- 结案时间"):
                    m = _RE_CLOSE_TS.match(line.strip())
                    c["ts"] = m.group(1) if m else ""
                elif line.startswith("- 区间涨跌"):
                    m = _RE_CHG.search(line)
                    if m:
                        c["chg"] = float(m.group(1))
                    m = _RE_EXD.search(line)
                    c["exd"] = m.group(1) if m else ""
                elif line.startswith("- 触发"):
                    m = _RE_TRIG.match(line.strip())
                    if m:
                        c["ntrig"] = int(m.group(1))
                elif line.startswith("- 案例档案"):
                    m = _RE_OPEN.match(line.strip())
                    if m:
                        c["open"] = m.group(1)  # 立案日期（§11 时长用）
                elif line.startswith("- 状态流"):
                    m = _RE_ARCH.match(line.strip())
                    if m:
                        c["arch"] = m.group(1)  # 归档文件名（§13 审计用）
            if c.get("code"):  # 标题行解析成功才算一条（半坏文件跳过）
                out.append(c)
        except Exception:
            continue
    return out


def summary_fields(cases):
    """案例列表 → 汇总统计字段 dict（文案与卡片共用单一数据源）。"""
    n = len(cases)
    chgs = [c["chg"] for c in cases if c["chg"] is not None]
    tags = [c["exd"] for c in cases if c["exd"]]
    fix = sum(1 for t in tags if t in ("收敛", "转正"))
    ntrigs = [c["ntrig"] for c in cases]
    f = {
        "n": n,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "na": sum(1 for c in cases if c.get("type") == "A"),
        "nb": sum(1 for c in cases if c.get("type") == "B"),
        "nc": sum(1 for c in cases if c.get("type") == "C"),
        "first_ts": min((c["ts"] for c in cases if c["ts"]), default="—"),
        "last_ts": max((c["ts"] for c in cases if c["ts"]), default="—"),
        "chg_avg": "%+.1f%%" % (sum(chgs) / len(chgs)) if chgs else "—",
        "chg_best": "%+.1f%%" % max(chgs) if chgs else "—",
        "chg_worst": "%+.1f%%" % min(chgs) if chgs else "—",
        "ex_fix": fix,
        "ex_tagged": len(tags),
        "ntrig": sum(ntrigs),
        "ntrig_avg": "%.1f" % (sum(ntrigs) / len(ntrigs)) if ntrigs else "0",
        "detail": "\n".join(
            "- %s %s %s %s（%s，触发 %d 次）"
            % (
                c["code"],
                c.get("name") or "",
                c.get("type", "?"),
                ("%+.1f%%" % c["chg"]) if c["chg"] is not None else "—",
                c.get("exd") or "—",
                c["ntrig"],
            )
            for c in cases[-8:]
        ),  # 明细最多 8 行（最新在后）
    }
    return f


def build_summary(f):
    """汇总字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(
                l
                for l in open(TPL, encoding="utf-8").read().splitlines(True)
                if not l.lstrip().startswith("#")
            )
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_summary.py", exc)
    return (
        "📊 [WEAK SUMMARY] 案例库结案汇总（§10）\n"
        "范围：{first_ts} ~ {last_ts}｜已结案 {n} 例\n"
        "类型分布：A 修复 {na}｜B 滚动 {nb}｜C 专项 {nc}\n"
        "区间涨跌：均 {chg_avg}｜最好 {chg_best}｜最差 {chg_worst}\n"
        "超额修复：{ex_fix}/{ex_tagged}（收敛或转正）｜"
        "触发合计 {ntrig} 次（均 {ntrig_avg}）\n"
        "明细：\n{detail}"
    ).format(**f)


def main():
    ap = argparse.ArgumentParser(description="§10 案例库结案汇总推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    cases = parse_cases()
    if not cases:
        print(
            "暂无已结案案例（%s 下无 weak_close_*.md）——§9 结案后本节才有数据" % CASES
        )
        return 0
    f = summary_fields(cases)
    msg = build_summary(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.summary_card(f), ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.summary_card(f)))
    else:
        import subprocess
        import sys

        tmp = os.path.join(
            os.path.dirname(HERE),
            "news_collector",
            "outbox",
            "pending_alert_weak_summary.txt",
        )
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        r = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(HERE), "news_collector", "send_feishu.py"),
                tmp,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
