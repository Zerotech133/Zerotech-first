# -*- coding: utf-8 -*-
"""流水线健康检查：18:30 PoolDailyPipeline 跑完后核产物齐全性，缺失即告警。

检查项（2026-11-10 审计定）：
  1. 个股日线新鲜度：ashare max(trade_date) == 今天（交易日 17:30 后）
  2. pool_state：最新 data_asof == 今天（数据日字段是 data_asof，不是 mtime）
  3. sector_state：asof >= pool_state 的前一交易日（tdx 板块链 T+1 晨更，
     恒落后个股一天是设计内；落后两个交易日以上才是异常）
  4. 页面三件套 mtime：pool_report_h5 / sector_platform / pool_pc == 今天
  5. 影子台账：data/shadow/<today>.json 存在、六腿无 error、F1 stop_pct=-0.12
  6. 流水线日志：今天有 [fail]（非 soft）或 [abort] 即告警

时间感知：工作日 18:40 前不算「缺」，报 pending 不告警；之后缺失才告警。
周末直接跳过（流水线自身也跳周末）。
退出码：0 全绿 / 1 有 WARN / 2 有 ALERT（监控方按非 0 处理）。
字段口径注意：pool_state 用 data_asof，sector_state 用 asof（勿读 mtime）。
"""
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
LOG = os.path.join(LOGS := os.path.join(HERE, "logs"), "pool_daily.log")

ALERTS, WARNS = [], []


def log(level, msg):
    print("[%s] %s" % (level, msg))
    if level == "ALERT":
        ALERTS.append(msg)
    elif level == "WARN":
        WARNS.append(msg)


def prev_trading_day(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def is_trading_day(d):
    return d.weekday() < 5


def ashare_asof():
    import pandas as pd
    picks = ["600519_SH", "000001_SZ", "600036_SH", "000858_SZ"]
    best = None
    for n in picks:
        p = os.path.join(HERE, "data", "ashare", n + ".parquet")
        if not os.path.exists(p):
            continue
        try:
            d = pd.read_parquet(p, columns=["trade_date"])
            m = str(d["trade_date"].max())
            best = m if best is None or m > best else best
        except Exception:
            continue
    return best


def main():
    now = datetime.now()
    today = date.today()
    day_s = today.strftime("%Y%m%d")

    print("== 流水线健康检查 %s %02d:%02d ==" % (day_s, now.hour, now.minute))

    if not is_trading_day(today):
        print("[SKIP] 周末，流水线与检查都跳过")
        return 0

    due = now.time() >= datetime.strptime("18:40", "%H:%M").time()
    if not due:
        print("[INFO] 18:40 前，产物缺失按 pending 处理不告警")

    # 1. 个股日线新鲜度
    asof = ashare_asof()
    if asof == day_s:
        log("OK", "个股日线已到今天 %s" % asof)
    elif now.time() >= datetime.strptime("17:30", "%H:%M").time():
        log("ALERT", "交易日 17:30 后个股日线还停在 %s（同步链 16:47 应到位）" % asof)
    else:
        print("[INFO] 日线最新 %s（未到 17:30，等同步）" % asof)

    # 2. pool_state 数据日
    ps_files = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
    ps_asof = None
    if ps_files:
        try:
            ps_asof = str(json.load(open(ps_files[-1], encoding="utf-8"))
                          .get("data_asof") or "")
        except Exception:
            ps_asof = None
    if ps_asof == day_s:
        log("OK", "pool_state 数据日 = 今天")
    elif not due:
        print("[INFO] pool_state 最新 %s（流水线未到执行时间，pending）" % ps_asof)
    else:
        log("ALERT", "18:40 后 pool_state 数据日仍是 %s ≠ 今天（流水线没跑成？"
                     "查 logs/pool_daily.log）" % ps_asof)

    # 3. sector_state 时滞（T+1 内正常）
    ss_files = sorted(glob.glob(os.path.join(REPORTS, "sector_state_*.json")))
    ss_asof = None
    if ss_files:
        try:
            ss_asof = str(json.load(open(ss_files[-1], encoding="utf-8"))
                          .get("asof") or "")
        except Exception:
            ss_asof = None
    if ps_asof and ss_asof:
        ps_d = datetime.strptime(ps_asof, "%Y%m%d").date()
        ss_d = datetime.strptime(ss_asof, "%Y%m%d").date()
        floor_d = prev_trading_day(ps_d)
        if ss_d >= floor_d:
            log("OK", "板块层 %s（T+1 晨更，落后一天为设计内）" % ss_asof)
        else:
            log("ALERT", "板块层停在 %s，落后个股层（%s）两个交易日以上"
                         "——tdx 板块指数同步链断了" % (ss_asof, ps_asof))

    # 4. 页面三件套
    for pg in ("pool_report_h5.html", "sector_platform.html", "pool_pc.html"):
        p = os.path.join(REPORTS, pg)
        if not os.path.exists(p):
            log("ALERT", "页面缺失：%s" % pg)
            continue
        mt = datetime.fromtimestamp(os.path.getmtime(p)).date()
        if mt == today:
            log("OK", "%s 今天已重建" % pg)
        elif due:
            log("ALERT", "%s 最后构建 %s（今天流水线没重建页面）" % (pg, mt))
        else:
            print("[INFO] %s 最后构建 %s（pending）" % (pg, mt))

    # 5. 影子台账
    sp = os.path.join(HERE, "data", "shadow", day_s + ".json")
    if not os.path.exists(sp):
        (log if due else lambda *_: None)("ALERT" if due else "INFO",
                                          "影子台账 %s 不存在" % day_s)
        if not due:
            print("[INFO] 影子台账今天还没写（15:10 自动化 / 18:30 流水线会写）")
    else:
        try:
            d = json.load(open(sp, encoding="utf-8"))
            legs = d.get("legs", d)
            names = list(legs.keys()) if isinstance(legs, dict) else []
            errs = [k for k, v in (legs.items() if isinstance(legs, dict) else [])
                    if isinstance(v, dict) and v.get("error")]
            if len(names) < 6:
                log("WARN", "影子台账只有 %d 腿（期望 6）：%s" % (len(names), names))
            elif errs:
                log("ALERT", "影子台账有 error 腿：%s" % errs)
            else:
                f1 = legs.get("F1_v3_stop12") or legs.get("F1") or {}
                stopv = (f1.get("assumptions") or {}).get("stop_pct")
                if stopv is not None and abs(float(stopv) + 0.12) > 1e-9:
                    log("ALERT", "F1 stop_pct=%s ≠ -0.12（参数回落，禁止转正）" % stopv)
                else:
                    log("OK", "影子台账六腿齐全无 error（F1 stop_pct 在位）")
        except Exception as e:
            log("ALERT", "影子台账解析失败：%s" % e)

    # 6. 流水线日志
    if os.path.exists(LOG):
        txt = open(LOG, encoding="utf-8", errors="replace").read()
        today_lines = [l for l in txt.splitlines()
                       if l.startswith("[%s" % now.strftime("%H")) or True]
        # 简化：看今天日期相关的 fail/abort（日志只有时分秒，按当次进程）
        fails = [l for l in txt.splitlines()[-200:]
                 if "[fail]" in l and "可失败" not in l]
        aborts = [l for l in txt.splitlines()[-200:] if "[abort]" in l]
        if aborts:
            log("ALERT", "流水线日志有 [abort]（结构步骤失败，看最后 200 行）")
        elif fails:
            log("WARN", "流水线日志有 [fail]（soft 步骤失败，不阻断）：%d 条" % len(fails))
        else:
            log("OK", "流水线日志无 fail/abort")

    # 7. 缓存自愈过程可见性（11-10 影子台账日期滚动自愈的首考验收）
    #    有今天的 rescan 日志 → 核对重扫结果；没有 → 今天缓存本来就在（15:10
    #    先跑已建好），INFO 即可。自愈失败最终会体现在第 5 项六腿 error 上。
    res_logs = sorted(glob.glob(os.path.join(REPORTS, "market_scan_rescan_*.log")),
                      key=os.path.getmtime)
    today_logs = []
    for p in res_logs:
        mt = datetime.fromtimestamp(os.path.getmtime(p)).date()
        if mt == today:
            today_logs.append(p)
    if today_logs:
        for p in today_logs[-2:]:
            txt = open(p, encoding="utf-8", errors="replace").read().strip()
            last = txt.splitlines()[-1] if txt else ""
            if "缓存已生成" in last and "rc=0" in last:
                log("OK", "缓存自愈重扫成功（%s）" % os.path.basename(p))
            elif "缓存仍缺失" in last or "自愈失败" in last:
                log("WARN", "缓存自愈重扫未产出缓存（%s 末行：%s）"
                            "——六腿将报缓存错误，人工跑 market_scan 重扫"
                            % (os.path.basename(p), last[:80]))
            else:
                log("WARN", "重扫日志 %s 无结论行（进程被截断？）" % os.path.basename(p))
    else:
        print("[INFO] 今天未触发缓存重扫（滚动日缓存已在位或检查先于流水线）")

    print("== 汇总：ALERT %d / WARN %d ==" % (len(ALERTS), len(WARNS)))
    return 2 if ALERTS else (1 if WARNS else 0)


if __name__ == "__main__":
    sys.exit(main())
