# -*- coding: utf-8 -*-
"""K225 · miss 检测哨兵：应跑未跑巡检（与「跑了但失败」告警互补）。

背景：K221 巡检发现 MorningReport 07:00 静默 miss 无人知晓——alert_fail
只覆盖「跑了但失败」（rc≠0），「应跑未跑」（错过/被禁/挂死）是盲区。

口径：
· 白名单底册 REGISTRY：任务名 → {expect "HH:MM", since "YYYY-MM-DD"}——
  expect=每日排程时刻（显式可审计，优于从 NextRun 反推）；since=该任务
  生效日（之前不检，防新任务首日前误报）。
· 判定：now >= expect + GRACE 且 Last Run < 今日 expect → MISS。
  Last Run 从 schtasks /v 实测（不信任日志）。
· 首期只收「交易日无关」子集 8 任务（休市也必须跑的同步/巡检/抽取/
  告警类）；交易日依赖任务（缠论扫描/回填/复盘类）休市日会 miss，属
  K221 判例的正常跳过，二期接交易日判定后纳入。
· 影子纪律：结果落 data/miss_check_last.json（原子写）；有 MISS 时经
  alert_fail 推送（失败可见），全绿静默 exit 0。

用法：python miss_check.py run [--json]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "miss_check_last.json")
GRACE_MIN = 90          # 排程后宽限（错过补跑 AIColAudit 实测隔 4h，先 90min 起步）
# 交易日无关子集：白名单底册（显式 expect，since=生效日）
REGISTRY = {
    "KG-TushareSync":    {"expect": "00:10", "since": "2026-10-06"},
    "AIColAudit":        {"expect": "06:10", "since": "2026-09-01"},
    "TbeStats":          {"expect": "06:45", "since": "2026-10-05"},
    "KgExtractShadow":   {"expect": "06:50", "since": "2026-10-05"},
    "MorningReport":     {"expect": "07:00", "since": "2026-09-28"},
    "KgResonanceNotify": {"expect": "08:05", "since": "2026-10-06"},
    "CryptoSync":        {"expect": "08:10", "since": "2026-09-20"},
    "KG-TdxSync":        {"expect": "17:40", "since": "2026-09-01"},
    "KG-Cl1Sync":        {"expect": "18:10", "since": "2026-10-05"},
    # 10-08 补：JEV 20:30 曾被睡眠窗口静默吃掉，哨兵底册原缺此项——
    # 手动守候才撞见；CainiaoPull 明晚首跑（R4 数据链）
    "CainiaoPull":       {"expect": "19:00", "since": "2026-10-09"},
    "JevReviewBackfill": {"expect": "20:30", "since": "2026-10-07"},
    # 10-09 补：晨间三连（06:20/06:30/06:40）原全不在册——恰处夜间睡眠
    # 窗口尾段，被静默吃掉将无人报（JEV 前车）；三任务已同批加
    # StartWhenAvailable，本册兜底「唤醒后也没补跑」的最后情形
    "KgDailySync":         {"expect": "06:20", "since": "2026-10-09"},
    "StrongEod":           {"expect": "06:30", "since": "2026-10-09"},
    "K260RegressionDaily": {"expect": "06:40", "since": "2026-10-09"},
    # 10-09 凌晨整理补（项目全面整理役）：两大盲区入册——
    # TushareSync 23:30 老田侧全量（1.5~3.5h，10-09 误杀事故主角；
    # alert_fail 兜跑挂，本册兜没跑，21:30 档次日检出）；AI工作站
    # 每日备份 03:00（B4 链核心，深睡高危时段，21:30 档当晚检出，
    # 08:30 晨档 3.8h 检出——晨档与 21:30 双跑位同批部署）
    "TushareSync":        {"expect": "23:30", "since": "2026-10-09"},
    "AI工作站每日备份":     {"expect": "03:00", "since": "2026-10-09"},
    # 10-10 补（八审 TodaysPicks 时序核查）：T1 聚合链四任务原全不在册。
    # 单日漏跑时 alert_fail 不推（没跑≠失败）、聚合闸门 4 天容差拦不住
    # （lag=1<4），T1 错日共振整链静默。ChanlunRefill=聚合消费的早批
    # （ChanlunScan 15:35 晚批在 15:10 聚合后落盘，不参与当日聚合）。
    # RpsDaily 仅交易日跑 → trade_only（非交易日跳过，见 _trade_today）。
    "ChanlunRefill":      {"expect": "06:30", "since": "2026-10-10"},
    "NineDimScan":        {"expect": "06:40", "since": "2026-10-10"},
    "TodaysPicks":        {"expect": "15:10", "since": "2026-10-10"},
    "RpsDaily":           {"expect": "16:05", "since": "2026-10-10",
                           "trade_only": True},
    # 10-10 补（十三审字段契约审计）：WeakScan 周扫（周一 15:35）原不在册
    # ——漏跑时 weak 源静默断供，lag>10 后 stale 拦截属设计内放行（不触发
    # T1 停挂），weak 否决长期失效无人报。周任务用 weekday 语义防误报；
    # 周一 21:30 档检出当日漏跑；若当日档也挂，下周一复查仍报（last 恒旧）。
    "WeakScan":           {"expect": "15:35", "since": "2026-10-10",
                           "weekday": 0},   # 0=周一（Python weekday 约定）
}


def _task_last_run(name):
    """schtasks /v 实测 Last Run Time → datetime 或 None（从未运行）。

    10-08 修（两晚 9/9 never 根因）：任务环境下 schtasks 输出中文本地化
    标签（「上次运行时间:」）且字节为 UTF-8，mbcs 解码即乱码、英文
    regex 永不命中 → bytes 捕获 + utf-8/mbcs 双解 + 中英双 regex。
    """
    try:
        p = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/v", "/fo", "list"],
            capture_output=True, timeout=20)
        for enc in ("utf-8", "mbcs"):
            out = p.stdout.decode(enc, errors="replace")
            m = (re.search(r"Last Run Time:\s*(\S+ \S+)", out)
                 or re.search(r"上次运行时间:\s*(\S+ \S+)", out))
            if m and not m.group(1).startswith("1999/"):
                try:
                    return datetime.strptime(
                        m.group(1), "%Y/%m/%d %H:%M:%S")
                except ValueError:
                    continue
        return None
    except Exception:
        return None


def _trade_today(now=None):
    """今天是否为 A 股交易日：行情湖 ts_daily 最新日 == 今天。

    八审加，供 trade_only 项（仅交易日运行的任务，如 RpsDaily 16:05）
    非交易日免检：21:30 档检查时 TdxSync 17:30 已入库当日数据 →
    最新日==今天 即交易日；周末/假日最新日停在上一交易日 → 跳过。
    查询失败按非交易日跳过（宁漏报不误报）；「TdxSync 挂导致当日
    未入库」由 KG-TdxSync 在册项兜住，不在此扩大检查面。
    """
    try:
        from tdx_read import latest_date
        return latest_date("ts_daily") == \
            (now or datetime.now()).strftime("%Y%m%d")
    except Exception as e:
        print("trade_today 查询失败（trade_only 项按非交易日跳过）: %s"
              % str(e)[:60])
        return False


def run():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    miss, checked = [], 0
    for name, cfg in REGISTRY.items():
        if cfg.get("since") and today < cfg["since"]:
            continue                     # 生效日前不检（新任务防误报）
        if cfg.get("trade_only") and not _trade_today(now):
            continue                     # 仅交易日任务：非交易日不检（八审）
        if cfg.get("weekday") is not None and now.weekday() != cfg["weekday"]:
            continue                     # 周任务：仅指定星期检查（十三审）
        exp_h, exp_m = map(int, cfg["expect"].split(":"))
        expect = now.replace(hour=exp_h, minute=exp_m, second=0)
        deadline = expect + timedelta(minutes=GRACE_MIN)
        if now < deadline:
            continue                     # 未到宽限期不检
        checked += 1
        last = _task_last_run(name)
        if last is None or last < expect:
            miss.append({"task": name, "expect": cfg["expect"],
                         "last_run": last.strftime("%Y-%m-%d %H:%M")
                         if last else "never"})
    for m_ in miss:
        print("MISS  %s  expect %s  last %s"
              % (m_["task"], m_["expect"], m_["last_run"]))
    print("checked %d / miss %d（交易日无关子集 · 宽限 %d 分钟）"
          % (checked, len(miss), GRACE_MIN))
    res = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "grace_min": GRACE_MIN, "checked": checked,
        "n_miss": len(miss), "miss": miss,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    if miss:
        # 失败可见：alert_fail 承担推送（复用既有通道）
        try:
            subprocess.run(
                [sys.executable,
                 os.path.join(HERE, "..", "news_collector", "alert_fail_wrap.py"),
                 "miss_check", "MISS×%d" % len(miss),
                 json.dumps(miss, ensure_ascii=False)],
                timeout=30)
        except Exception as e:
            print("alert_fail 调用失败: %s" % str(e)[:60])
    else:
        print("全绿（静默）")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    a = ap.parse_args()
    run()


if __name__ == "__main__":
    main()
