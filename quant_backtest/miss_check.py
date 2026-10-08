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


def run():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    miss, checked = [], 0
    for name, cfg in REGISTRY.items():
        if cfg.get("since") and today < cfg["since"]:
            continue                     # 生效日前不检（新任务防误报）
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
                 os.path.join(HERE, "alert_fail.py"),
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
