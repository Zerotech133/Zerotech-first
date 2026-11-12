# -*- coding: utf-8 -*-
"""盘前一键：校对 → 选票 → 出报告 → 记账。后峰盘前 Agent 的唯一入口。

老田 2026-11-12 的要求是「每天盘前都要做这件事」。那么它就必须是**一条命令**，
不是一串手工步骤 —— 步骤一多，缺一步没人发现，出来的报告就是半成品。

顺序按依赖排，前一步不通过就不往下走（fail-fast）：

  1. 数据校对  tools/preopen/audit_preopen.py
     verdict=fail → 立即停，不发报告。宁可今天没有报告，也不发一份基于坏数据的报告。
  2. 精选 15 只  _preopen_pick15_v2_adj.py --pkg <最新包> --asof <行情最新日>
     参数自动推：包取 strategy_center_inbox 最大日期目录；asof 取个股日线最新日。
  3. 简报 H5  tools/preopen/build_preopen_report.py
     读 2 的产物 + 台账，产出可转发的自包含 HTML。
  4. 记账 + 长建议  tools/preopen/event_log.py / evolve.py
     把今天这件事写进事件账（含意外与候选教训），并检查是否已够格立假设。
  5. 进化台账  evolve.py --digest
     出一份人能读的台账：今天观察到什么、累计到哪、还差多少够格。
     第 4 步不影响 rc —— 记账失败不该让今天的报告作废，但要在日志里留痕。

退出码：0 全绿 / 1 出了东西但有告警 / 2 阻断（未出报告）
            3 选票脚本失败（数据可能没问题，是脚本或依赖的问题）

用法：
  python tools/preopen/run_preopen.py
  python tools/preopen/run_preopen.py --skip-pick      # 只重出报告，不重跑选票
  python tools/preopen/run_preopen.py --json-out x.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\项目\老田的AI助手"
PY = sys.executable
PICK_DIR = r"C:\项目\zerobound-kg\factor_proto"
PICK = os.path.join(PICK_DIR, "_preopen_pick15_v2_adj.py")
PKG_ROOT = os.path.join(PICK_DIR, "strategy_center_inbox")
DELIV = r"C:\项目\deliverables"

sys.path.insert(0, HERE)
import audit_preopen as audit  # noqa: E402


def latest_pkg() -> str | None:
    if not os.path.isdir(PKG_ROOT):
        return None
    ds = sorted(d for d in os.listdir(PKG_ROOT)
                if os.path.isdir(os.path.join(PKG_ROOT, d)) and d.isdigit()
                and os.path.exists(os.path.join(PKG_ROOT, d, "push_package.json")))
    return ds[-1] if ds else None


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前一键：校对→选票→出报告")
    ap.add_argument("--date", default=None, help="数据日，缺省取个股日线最新日")
    ap.add_argument("--skip-pick", action="store_true", help="跳过选票，只重出报告")
    ap.add_argument("--json-out", default=os.path.join(DELIV, "preopen_run_latest.json"),
                    help="运行结论落盘路径（默认与报告同目录）")
    a = ap.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: dict = {"started": stamp, "steps": [], "rc": 0}

    asof, _ = audit.max_date("tdx/ts_daily", "trade_date")
    day = a.date or asof
    out["data_day"] = day
    print("=" * 62)
    print("后峰盘前 · %s" % stamp)
    print("数据日 %s" % day)
    print("=" * 62)

    # ── 1. 校对 ─────────────────────────────────────────────────────────
    au = audit.audit(day)
    out["audit"] = {"verdict": au["verdict"], "problems": au["problems"],
                    "warns": au["warns"]}
    print("\n[1/5] 数据校对 → %s" % au["verdict"])
    for p in au["problems"]:
        print("      × %s" % p)
    for w in au["warns"]:
        print("      ! %s" % w)
    out["steps"].append({"step": "audit", "verdict": au["verdict"]})
    if au["verdict"] == "fail":
        print("\n阻断：数据不通过，不出报告。")
        out["rc"] = 2
        _dump(out, a.json_out)
        return 2

    # ── 2. 选票 ─────────────────────────────────────────────────────────
    pick_json = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json" % day)
    if a.skip_pick and os.path.exists(pick_json):
        print("\n[2/5] 选票 → 跳过（复用 %s）" % os.path.basename(pick_json))
        out["steps"].append({"step": "pick", "skipped": True})
    else:
        pkg = latest_pkg()
        if not pkg:
            print("\n阻断：找不到共识池推送包（strategy_center_inbox）")
            out["steps"].append({"step": "pick", "error": "no package"})
            out["rc"] = 3
            _dump(out, a.json_out)
            return 3
        print("\n[2/5] 选票 → pkg=%s asof=%s" % (pkg, day))
        rc, log = run([PY, "-X", "utf8", PICK, "--pkg", pkg, "--asof", day], cwd=PICK_DIR)
        tail = "\n".join(log.strip().splitlines()[-6:])
        print("      rc=%d\n%s" % (rc, "\n".join("      " + l for l in tail.splitlines())))
        out["steps"].append({"step": "pick", "rc": rc, "pkg": pkg, "tail": tail})
        if rc != 0:
            out["rc"] = 3
            _dump(out, a.json_out)
            return 3

    # ── 3. 报告 ─────────────────────────────────────────────────────────
    print("\n[3/5] 简报 →")
    rc, log = run([PY, "-X", "utf8", os.path.join(HERE, "build_preopen_report.py"),
                   "--date", day])
    print("      " + log.strip().splitlines()[-1] if log.strip() else "      (无输出)")
    out["steps"].append({"step": "report", "rc": rc, "out": log.strip()})

    out["rc"] = 1 if (au["warns"] or rc != 0) else 0
    if rc != 0:
        out["rc"] = 3

    # ── 4. 记账 + 长建议 ────────────────────────────────────────────────
    # 事件化必须读本次运行产物，所以先落一次盘再调 —— 否则 event_log
    # 读到的是上一次的结果，记的就不是今天这件事。
    _dump(out, a.json_out)
    print("\n[4/5] 记账 →")
    rc_e, log_e = run([PY, "-X", "utf8", os.path.join(HERE, "event_log.py"),
                       "--from-run", "--run-json", a.json_out])
    tail_e = "\n".join(log_e.strip().splitlines()[:8])
    print("\n".join("      " + l for l in tail_e.splitlines()) if tail_e
          else "      (无输出)")
    out["steps"].append({"step": "event", "rc": rc_e, "out": tail_e})

    rc_p, log_p = run([PY, "-X", "utf8", os.path.join(HERE, "evolve.py"), "--propose"])
    tail_p = "\n".join(log_p.strip().splitlines()[:12])
    print("\n".join("      " + l for l in tail_p.splitlines()) if tail_p
          else "      (无输出)")
    out["steps"].append({"step": "evolve", "rc": rc_p, "out": tail_p})

    # ── 5. 进化台账（给人看的那一面）──────────────────────────────────
    # 前四步都是给机器和报告用的。这一步是唯一一个"给老田翻一眼"的产物：
    # 今天观察到什么、累计到哪了、还差多少才够格提建议。
    # 失败不影响主流程 —— 台账是输出，不是关卡。
    print("\n[5/5] 进化台账 →")
    # 文件名用执行日，不用数据日 —— 老田早上翻的是「今天这份台账」，
    # 写成 11-11 会让他以为是昨天那份（报告顶栏同一个坑，已经踩过一次）。
    cal = audit.trade_cal()
    later = [x for x in cal if x > day]
    exec_day = later[0] if later else day
    md_out = os.path.join(DELIV, "preopen_evolve_%s.md" % exec_day)
    html_out = os.path.join(DELIV, "preopen_evolve_%s.html" % exec_day)
    rc_d, log_d = run([PY, "-X", "utf8", os.path.join(HERE, "evolve.py"),
                       "--digest", "--md-out", md_out, "--html-out", html_out])
    tail_d = "\n".join(log_d.strip().splitlines()[-2:]) if log_d.strip() else "(无输出)"
    print("\n".join("      " + l for l in tail_d.splitlines()))
    out["steps"].append({"step": "digest", "rc": rc_d,
                         "md": md_out, "html": html_out, "out": tail_d})

    _dump(out, a.json_out)
    print("\n完成：rc=%d（0 全绿 / 1 有告警 / 2 阻断 / 3 脚本失败）" % out["rc"])
    return out["rc"]


def _dump(out: dict, path: str | None) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    io.open(path, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
