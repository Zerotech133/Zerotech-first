# -*- coding: utf-8 -*-
"""§11 案例库复盘学习层推送（weak 产线第 11 节 · K35-v 新增）。

§9 结案是单案收尾；§10 是事实汇总（发生了什么）；§11 是经验提炼
（学到了什么）：从全部 weak_close_*.md 提炼规律统计——剧本兑现率
（A=预判命中）/跟踪时长分布/触发密度与结局关联/超额修复率——并
生成带样本量标注的经验条目。

样本纪律：n < 5 不下规律结论（只出「样本积累」简版，明细照列），
n >= 5 才出全量规律——防止小样本过度归纳（§6 触发阈值同款纪律）。

数据源与 §10 同源：weak_track_summary.parse_cases（唯一解析器，
本节消费其 open 立案日期字段算跟踪时长），不重读 jsonl。

默认只打印预览（不动任何文件）；--send 才推送（飞书卡片默认，
--text 回退文本）。文案外置 weak_track_learn_tpl.txt（判例沿用，
坏模板回退内置）。空态提示退出 rc=0。

用法:
  python weak_track_learn.py                # 预览（文本+卡片 JSON）
  python weak_track_learn.py --send         # 推送卡片
  python weak_track_learn.py --send --text  # 推送文本
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_summary as wts  # noqa: E402
import logging

REP = os.path.join(os.path.dirname(HERE), "reports")
CASES = os.path.join(REP, "weak_cases")
TPL = os.path.join(HERE, "weak_track_learn_tpl.txt")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
MIN_N = 5  # 规律结论样本门槛


def _days(c):
    """单案跟踪天数：结案日期 - 立案日期（档案文件名）；坏数据 None。

    口径：**日历天**，非交易日。同日结案为 0。与结案报告里的
    「跟踪 N 日/23 次」不是同一口径，后者按交易日/运行次数计。
    """
    try:
        d1 = datetime.datetime.strptime(c["ts"], "%Y-%m-%d")
        d0 = datetime.datetime.strptime(c["open"], "%Y%m%d")
        return (d1 - d0).days
    except (KeyError, ValueError, TypeError):
        return None


def learn_fields(cases):
    """案例列表 → 学习层统计字段 dict（文案与卡片共用单一数据源）。

    全部格式化为字符串——模板/内置/卡片三端消费同一 dict 不再算数。
    """
    n = len(cases)
    na = sum(1 for c in cases if c.get("type") == "A")
    nb = sum(1 for c in cases if c.get("type") == "B")
    nc = sum(1 for c in cases if c.get("type") == "C")
    bc = [c for c in cases if c.get("type") in ("B", "C")]
    days = [d for d in (_days(c) for c in cases) if d is not None]
    tags = [c["exd"] for c in cases if c["exd"]]
    fix = sum(1 for t in tags if t in ("收敛", "转正"))
    a_tags = [c["exd"] for c in cases if c.get("type") == "A" and c["exd"]]
    fix_a = sum(1 for t in a_tags if t in ("收敛", "转正"))
    hi = [c for c in cases if c["ntrig"] >= 2]
    hi_bc = sum(1 for c in hi if c.get("type") in ("B", "C"))
    pct = lambda a, b: "%d%%" % round(a * 100.0 / b) if b else "—"  # noqa: E731
    _avg = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    trig_a = _avg([c["ntrig"] for c in cases if c.get("type") == "A"])
    trig_bc = _avg([c["ntrig"] for c in bc])
    f = {
        "n": n,
        "na": na,
        "nb": nb,
        "nc": nc,
        "a_rate": pct(na, n),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "first_ts": min((c["ts"] for c in cases if c["ts"]), default="—"),
        "last_ts": max((c["ts"] for c in cases if c["ts"]), default="—"),
        "ndays": len(days),
        "dur_avg": "%.0f" % _avg(days) if days else "—",
        "dur_min": "%d" % min(days) if days else "—",
        "dur_max": "%d" % max(days) if days else "—",
        "d1": str(sum(1 for d in days if d <= 14)) if days else "—",
        "d2": str(sum(1 for d in days if 14 < d <= 30)) if days else "—",
        "d3": str(sum(1 for d in days if d > 30)) if days else "—",
        "trig_a": "%.1f" % trig_a if trig_a is not None else "—",
        "trig_bc": "%.1f" % trig_bc if trig_bc is not None else "—",
        "ex_fix": fix,
        "ex_tagged": len(tags),
        "fix_a": fix_a,
        "tag_a": len(a_tags),
        "ntrig_avg": "%.1f" % _avg([c["ntrig"] for c in cases]) if cases else "0",
        "gate": "已达标" if n >= MIN_N else "积累中",
    }
    # 经验条目：n>=5 出规律（每条自带样本量），否则样本积累提示
    if n >= MIN_N:
        L = [
            "剧本兑现：A %s/%s（%s）——A=预判命中，B/C=改判（n=%s）"
            % (na, n, f["a_rate"], n)
        ]
        if days:
            L.append(
                "跟踪时长：均 %s 日历天（%s~%s，非交易日）｜≤2 周 %s｜半月-月 %s｜"
                ">1 月 %s（可用样本 %s）"
                % (
                    f["dur_avg"],
                    f["dur_min"],
                    f["dur_max"],
                    f["d1"],
                    f["d2"],
                    f["d3"],
                    f["ndays"],
                )
            )
        L.append(
            "触发密度：A 类均 %s 次 vs B/C 类均 %s 次（n=%s）"
            % (f["trig_a"], f["trig_bc"], n)
        )
        if len(hi) >= 3:
            L.append(
                "高频触发（≥2 次）%d 例中 %d 例 B/C（%s）——"
                "触发频繁多改判，首次触发即复核情景前提（n=%d）"
                % (len(hi), hi_bc, pct(hi_bc, len(hi)), len(hi))
            )
        L.append(
            "超额修复：%s/%s 收敛或转正（A 类 %s/%s）（n=%s）"
            % (fix, len(tags), fix_a, len(a_tags), n)
        )
    else:
        L = [
            "样本积累中：n=%s（门槛 %d），暂不下规律结论——"
            "§9 每结一案自动沉淀" % (n, MIN_N)
        ]
    f["lessons"] = "\n".join("- " + x for x in L)
    f["detail"] = "\n".join(
        "- %s %s %s %s（%s，触发 %d 次）"
        % (
            c["code"],
            c.get("name") or "",
            c.get("type", "?"),
            ("%+.1f%%" % c["chg"]) if c["chg"] is not None else "—",
            c.get("exd") or "—",
            c["ntrig"],
        )
        for c in cases[-5:]
    )  # 明细最多 5 行（最新在后）——赋值语句
    # 尾逗号会变 tuple（dict 字面量里才是分隔符）
    return f


def build_learn(f):
    """学习层字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(
                l
                for l in open(TPL, encoding="utf-8").read().splitlines(True)
                if not l.lstrip().startswith("#")
            )
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_learn.py", exc)
    return (
        "🧠 [WEAK LEARN] 案例库复盘学习（§11）\n"
        "样本：{n} 例（{first_ts} ~ {last_ts}）｜规律门槛 {gate}\n"
        "经验条目：\n{lessons}\n"
        "明细（最新 5 条）：\n{detail}"
    ).format(**f)


def main():
    ap = argparse.ArgumentParser(description="§11 案例库复盘学习层推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    cases = wts.parse_cases()
    if not cases:
        print(
            "暂无已结案案例（%s 下无 weak_close_*.md）——§9 结案后本节才有数据" % CASES
        )
        return 0
    f = learn_fields(cases)
    msg = build_learn(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.learn_card(f), ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.learn_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_learn.txt")
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
