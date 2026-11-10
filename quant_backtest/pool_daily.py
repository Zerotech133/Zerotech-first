# -*- coding: utf-8 -*-
"""每日流水线：日线到齐之后，把「结构 → 板块 → 两页 → 快照 → 飞书」一次跑完。

为什么要有这东西：这条链现在有六七个脚本，每个都能单独跑，但没人知道
「今天该按什么顺序跑哪几个」。于是最常见的情况不是哪个环节坏了，而是——
页面还是上一交易日的结构、板块层没重算、飞书停在上午 11:31。每一样都
「能跑」，合起来就是断的。

顺序有讲究：

  1. **日线新鲜度门控**。data/ashare 最新一根不是今天（且今天不是周末）就停。
     用昨天的日线算今天的结构，页面会挂着「基于 11-06 收盘」却标今天的日期，
     这比不跑更糟——错的东西比没有更费人。
  2. `pool_state --scan --save`：全市场结构重算。下游全部读它的落盘。
  3. `sector_state --save`：板块指数跑**同一套** analyse。跑完核对它的 asof
     和日线是否一致——板块指数（ts_ths_daily）和个股日线是两条同步链路，
     一条到了另一条没到，就会出现「个股按 11-09、板块按 11-06」的错配。
  4. `sector_member_snapshot`：把当前成分存一份。上游只给「今天」，翻新后
     旧版就没了，而共振回放要的是历史成分。
  5. 两个页面：个股页、板块页。
  6. `pool_close_review`：推飞书。它自带幂等与时段门控，手动跑过不会重推。
  7. `shadow_ab --daily`：影子台账（固化 v1 六腿，F1 转正验收的 G-A 门靠它
     每天累积动作差）。soft——写失败不挡结构流水线；另有 15:10 WorkBuddy
     自动化兜底，双保险。

用法：
  python pool_daily.py                 # 全跑
  python pool_daily.py --dry           # 只报要做什么，不执行
  python pool_daily.py --allow-stale    # 日线不是今天也跑（补数据用，别发飞书）
  python pool_daily.py --no-push       # 跑到页面为止，不推飞书
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
LOGS = os.path.join(HERE, "logs")
LOG_PATH = os.path.join(LOGS, "pool_daily.log")
PY = sys.executable


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ashare_asof():
    """个股日线的最新交易日。

    取几只大票的 max 而不是扫全库：全库扫描要 6 秒，而这里只需要知道
    「同步有没有跑到今天」。一只票停牌不影响——取 max。
    """
    picks = ["600519_SH", "000001_SZ", "600036_SH", "000858_SZ"]
    best = None
    for n in picks:
        p = os.path.join(HERE, "data", "ashare", n + ".parquet")
        if not os.path.exists(p):
            continue
        try:
            d = pd.read_parquet(p, columns=["trade_date"])
            m = str(d["trade_date"].max())
            if best is None or m > best:
                best = m
        except Exception:
            continue
    if best:
        return best
    fs = sorted(glob.glob(os.path.join(HERE, "data", "ashare", "*.parquet")))
    if not fs:
        return None
    d = pd.read_parquet(fs[0], columns=["trade_date"])
    return str(d["trade_date"].max())


def run(label, args, soft=False, timeout=1800):
    """跑一步。soft=True 表示失败不阻断后续（比如推飞书失败不该影响页面）。"""
    log("── %s" % label)
    t0 = time.time()
    try:
        p = subprocess.run([PY] + args, cwd=HERE, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        log("   [fail] 超时 %ds" % timeout)
        return False
    dt = time.time() - t0
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-5:])
    if p.returncode != 0:
        log("   [fail] rc=%d %.1fs" % (p.returncode, dt))
        if tail:
            for ln in tail.splitlines():
                log("   " + ln)
        return soft
    log("   [ok] %.1fs" % dt)
    if tail:
        for ln in tail.splitlines():
            log("   " + ln)
    return True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只报计划，不执行")
    ap.add_argument("--allow-stale", action="store_true",
                    help="日线不是今天也跑（补历史数据时用，结果会标成旧日期）")
    ap.add_argument("--no-push", action="store_true", help="不推飞书")
    ap.add_argument("--skip-scan", action="store_true",
                    help="跳过全市场结构重算（日线没变时重跑页面用）")
    a = ap.parse_args()

    today = date.today().strftime("%Y%m%d")
    wd = date.today().weekday()
    asof = ashare_asof()
    log("=" * 62)
    log("每日流水线 今日 %s（%s）· 日线最新 %s"
        % (today, "一二三四五六日"[wd], asof or "读不到"))

    if not asof:
        log("[fail] data/ashare 为空，先跑日线同步")
        return 1

    if asof != today:
        if wd >= 5 and not a.allow_stale:
            log("[skip] 今天是周末（日线最新 %s）。计划任务每天触发，周末直接退出。"
                % asof)
            return 0
        if wd >= 5:
            log("[note] 周末 + --allow-stale，按最近交易日 %s 强制跑" % asof)
        elif a.allow_stale:
            log("[warn] 日线停在 %s（今天 %s 还没同步），--allow-stale 强制继续。"
                "下面所有产物都会标成 %s。" % (asof, today, asof))
        else:
            log("[wait] 日线最新是 %s，今天 %s 的还没同步。" % (asof, today))
            log("       现在重算等于用上一交易日的收盘价去算今天的结构，页面会自相矛盾。")
            log("       日线同步一般 16:47 前后完成；节假日请次日再跑。")
            return 0

    steps = []
    if not a.skip_scan:
        steps.append(("全市场结构重算（pool_state --scan）",
                      ["pool_state.py", "--scan", "--save"], False))
    steps.append(("板块结构层（sector_state）",
                  ["sector_state.py", "--save"], False))
    steps.append(("成分快照（member snapshot）",
                  ["sector_member_snapshot.py"], True))
    steps.append(("个股页", ["pool_report_h5.py"], False))
    steps.append(("板块页", ["sector_platform_h5.py"], False))
    # PC 工作台要在打包之前生成：h5_pack 的清单里已经有 pc.html，缺了会直接非 0 退出
    steps.append(("PC 工作台", ["pool_pc.py"], False))
    # 缠论分析工具与 PC 工作台同源（都读 pool_state），也是 h5_pack 的核心页，
    # 必须在打包前生成，否则平台少掉「分析工具」这个入口。
    steps.append(("缠论分析工具", ["chanlun_tool.py"], False))
    # 模拟盘对账读 portfolio.json：账本由 portfolio.py 单独维护，这里只渲染不交易。
    # 标 soft —— 账本不存在（例如被清空重建）时不该挡住整条结构流水线。
    steps.append(("模拟盘对账页", ["sim_accounts.py"], True))
    # 两个页面是双向相对跳转的，发布目录必须「成套」——少一个就是 404。
    # 这一步只做本地拷贝和引用闭环自检，不碰线上；线上仍要手动发布一次。
    steps.append(("发布目录打包（h5_pack）", ["h5_pack.py"], False))
    if not a.no_push:
        steps.append(("收盘复盘推送", ["pool_close_review.py"], True))
    # 选股工作台 v2（老田 2026-11-10：档卡片→档列表→个股全部关系详情）
    steps.append(("选股工作台 v2", ["pool_workbench2.py"], True))
    # 影子台账（固化 v1 六腿）：F1 转正验收的 G-A 门靠每个交易日的动作差累积。
    # 标 soft —— 台账失败不该挡住整条结构流水线；15:10 自动化已写过的当日
    # 文件会被这里正常覆盖重算（幂等，同输入同输出）。
    steps.append(("影子台账（shadow --daily）",
                  ["shadow_ab.py", "--daily"], True))

    if a.dry:
        log("计划执行：")
        for lab, args, soft in steps:
            log("   %s%s → %s" % (lab, "（可失败）" if soft else "", " ".join(args)))
        return 0

    t_all = time.time()
    for lab, args, soft in steps:
        if not run(lab, args, soft=soft):
            log("[abort] %s 失败，后面的步骤依赖它，停在这里。" % lab)
            return 2

    # 板块层与个股层的日期一致性（T+1 感知，2026-11-10 裁定）：
    # tdx 板块指数链每天 04:33 更新 D-1，个股日线当天 16:47 更新 D，
    # 所以 18:30 运行时板块层落后个股「一个交易日」是设计内常态——不告警，
    # 只提示；落后两个交易日及以上才是同步链断裂，仍然告警。
    def prev_trading_day(dstr):
        d = datetime.strptime(dstr, "%Y%m%d").date() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y%m%d")

    sp = os.path.join(REPORTS, "sector_state_%s.json" % asof)
    if not os.path.exists(sp):
        got = sorted(glob.glob(os.path.join(REPORTS, "sector_state_*.json")))
        latest = os.path.basename(got[-1]) if got else ""
        got_day = latest.replace("sector_state_", "").replace(".json", "")
        if got_day and got_day >= prev_trading_day(asof):
            log("[note] 板块层 %s（T+1 晨更，落后个股一天为设计内；"
                "个股页「所属板块」跨天不挂载是已知现状，非故障）" % latest)
        else:
            log("[warn] 板块层停在 %s，落后个股层（%s）两个交易日以上"
                "——tdx 板块指数同步链断了，需人工排查。"
                % (latest or "无", asof))
    log("完成，合计 %.1fs" % (time.time() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
