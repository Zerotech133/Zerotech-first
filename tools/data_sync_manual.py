# -*- coding: utf-8 -*-
"""手动同步更新数据（一键 L1 动作）：Tushare 增量 → 六表守护 → 复权 → 港股 → 公告 → 驾驶舱快照。

红线：只读拉取/写自身数据目录与派生快照；不重启服务、不改生产库、不发布。
产出：work/data_sync/last.json（驾驶舱读取展示）+ work/data_sync/sync_<stamp>.log。

用法：
  python tools/data_sync_manual.py --dry-run          # 只打印计划
  python tools/data_sync_manual.py                     # 跑全部步骤
  python tools/data_sync_manual.py --steps tushare,hk,snapshot
退出码：0=全部成功 / 2=有步骤失败（--continue-on-error 下继续跑完）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
WORK = REPO / "work" / "data_sync"
LAST = WORK / "last.json"
PY312 = sys.executable
PY311 = r"C:\Program Files\Python311\python.exe"

STEPS = {
    "tushare": {
        "desc": "Tushare 增量同步（A股/港股/美股行情基础表）",
        "cmd": [PY311, "-X", "utf8", "tushare_sync.py", "--incremental", "--market", "all"],
        "cwd": REPO / "quant_backtest", "timeout": 1200,
    },
    "guard": {
        "desc": "六表增量守护（increment_guard，补齐最近交易窗口）",
        "cmd": [PY311, "-u", "increment_guard.py"],
        "cwd": REPO / "quant_backtest", "timeout": 900,
    },
    "adj": {
        "desc": "复权因子增量（AdjFactorSync）",
        "cmd": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(REPO / "quant_backtest" / "start_adj_factor_sync.ps1")],
        "cwd": REPO / "quant_backtest", "timeout": 1200,
    },
    "hk": {
        "desc": "港股日线导出（HK R1 Daily --append）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "hk_r1_daily.py"), "--append", "--json"],
        "cwd": REPO, "timeout": 900,
    },
    "ann": {
        "desc": "公告近月增量回填（ann_history_sync --apply）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "datawarehouse" / "ann_history_sync.py"),
                "--apply", "--since", (datetime.now().replace(day=1)).strftime("%Y-%m-%d"), "--limit", "300"],
        "cwd": REPO, "timeout": 900,
    },
    "fin": {
        "desc": "财报补齐（按报告期拉全市场并合并，income/balance/cashflow/fina）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "fin_report_sync.py"), "--period", _last_quarter_end()],
        "cwd": REPO, "timeout": 2400,
    },
    "recent": {
        "desc": "研究相关数据补齐（龙虎榜/港股/资金流/全球指数/预告快报）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "recent_data_fill.py"), "--days", "7"],
        "cwd": REPO, "timeout": 900,
    },
    "audit": {
        "desc": "真对齐审计（源侧 vs 本地逐日核对 + 缺行自动补齐 + 财报/事件型核对）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "data_lag_audit.py"), "--days", "10"],
        "cwd": REPO, "timeout": 1200,
    },
    "snapshot": {
        "desc": "刷新驾驶舱快照（dw_snapshot.json）",
        "cmd": [PY312, "-X", "utf8", str(REPO / "tools" / "dw_dashboard.py")],
        "cwd": REPO, "timeout": 300,
    },
}
ORDER = ["tushare", "guard", "adj", "hk", "ann", "snapshot"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="手动同步更新数据")
    ap.add_argument("--steps", default=",".join(ORDER), help="逗号分隔步骤：%s" % ",".join(ORDER))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--continue-on-error", action="store_true", default=True)
    args = ap.parse_args(argv)
    steps = [s.strip() for s in args.steps.split(",") if s.strip() in STEPS]
    WORK.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = WORK / ("sync_%s.log" % stamp)
    plan = [{"step": s, "desc": STEPS[s]["desc"]} for s in steps]
    if args.dry_run:
        print(json.dumps({"dry_run": True, "steps": plan}, ensure_ascii=False, indent=1))
        return 0

    started = datetime.now().astimezone().isoformat(timespec="seconds")
    results = []
    log_lines = ["==== 手动同步 %s ====" % started]
    failed = []
    env = dict(__import__("os").environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for s in steps:
        spec = STEPS[s]
        t0 = time.time()
        log_lines.append("\n---- [%s] %s ----" % (s, spec["desc"]))
        try:
            r = subprocess.run(spec["cmd"], cwd=str(spec["cwd"]), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=spec["timeout"], env=env)
            rc = r.returncode
            tail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip().splitlines()[-6:]
        except subprocess.TimeoutExpired:
            rc, tail = -1, ["TIMEOUT > %ds" % spec["timeout"]]
        except Exception as e:  # noqa: BLE001
            rc, tail = -1, ["ERROR %s: %s" % (type(e).__name__, str(e)[:120])]
        dur = round(time.time() - t0, 1)
        results.append({"step": s, "desc": spec["desc"], "rc": rc, "duration_s": dur,
                        "tail": tail})
        log_lines.extend(tail)
        if rc != 0:
            failed.append(s)
        print("[%s] rc=%s %.1fs" % (s, rc, dur), flush=True)
    finished = datetime.now().astimezone().isoformat(timespec="seconds")
    out = {"started_at": started, "finished_at": finished, "ok": not failed, "failed": failed,
           "steps": results, "log": str(log_path),
           "how_to": "python tools/data_sync_manual.py（或指定 --steps tushare,hk,snapshot）"}
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    LAST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": out["ok"], "failed": failed, "last": str(LAST)}, ensure_ascii=False), flush=True)
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
