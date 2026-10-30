# -*- coding: utf-8 -*-
"""黄忠验收证据每日刷新（L2 计划任务 HuangzhongEvidenceRefresh 的执行体）。

用途：每小时验收依赖「证据年龄 ≤48h」（R3）。本脚本每日刷新：
  1) 备份 `agent_platform/data/test_baseline/latest.json` → `latest_prev.json`（保留刷新前快照）
  2) 跑 `tools/test_baseline.py --run --json`（约 5~7min）→ `C:\\Temp\\huangzhong_baseline_refresh.log`
  3) 跑 `tools/zhuge_redteam.py --json`（约 5s）→ `C:\\Temp\\huangzhong_redteam_refresh.log`
  4) 状态行追加 `C:\\Temp\\huangzhong_evidence_refresh_status.log`（时间/rc/耗时/证据 generated_at）

退出码：基线 rc=0 且红队 rc=0 → 0；否则 1（任务 LastTaskResult 可见，便于巡检）。
边界：只写 gitignored 证据（`agent_platform/data/test_baseline/`、`work/p1/redteam/`）与 C:\\Temp 日志；
不重启服务、不改配置/计划任务、无端口、不阻断 merge。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable or "python"
# 日志固定落 C:\Temp（与仓库长跑落盘约定一致；%TEMP% 因用户环境而异，不便于巡检/归档）。
# 需要时用 HUANGZHONG_REFRESH_LOG_DIR 覆盖（测试/迁移用）。
DEFAULT_LOG_DIR = Path(os.environ.get("HUANGZHONG_REFRESH_LOG_DIR") or r"C:\Temp")


def _run(cmd: list[str], cwd: Path, runner=None) -> dict:
    """执行一条命令并返回 {rc, out, err, seconds}；runner 可注入（测试用）。"""
    fn = runner or subprocess.run
    t0 = time.perf_counter()
    try:
        r = fn(cmd, cwd=str(cwd), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=1200)
        rc = int(getattr(r, "returncode", 1))
        out = getattr(r, "stdout", "") or ""
        err = getattr(r, "stderr", "") or ""
    except Exception as e:  # noqa: BLE001 —— 任务体 fail-visible，异常转 rc=1
        rc, out, err = 1, "", "%s: %s" % (type(e).__name__, e)
    return {"rc": rc, "out": out, "err": err,
            "seconds": round(time.perf_counter() - t0, 2)}


def _current_baseline_summary(root: Path) -> dict:
    p = Path(root) / "agent_platform" / "data" / "test_baseline" / "latest.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"generated_at": d.get("generated_at"),
                "summary": d.get("summary")}
    except (OSError, ValueError):
        return {}


def refresh(root: Path | None = None, log_dir: Path | None = None,
            runner=None) -> dict:
    """执行一次证据刷新；返回结构化结果（供测试与人工巡检）。"""
    root = Path(root) if root else ROOT
    log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # 1) 备份刷新前基线（保留上一次快照；失败不阻断刷新）
    backup_ok = False
    latest = root / "agent_platform" / "data" / "test_baseline" / "latest.json"
    prev = latest.parent / "latest_prev.json"
    try:
        if latest.is_file():
            prev.write_bytes(latest.read_bytes())
            backup_ok = True
    except OSError:
        backup_ok = False

    py = PY
    # 2) 基线复跑（约 5~7min）
    base = _run([py, "-X", "utf8", "tools/test_baseline.py", "--run", "--json"],
                root, runner)
    (log_dir / "huangzhong_baseline_refresh.log").write_text(
        (base["out"] or "") + (("\n[stderr]\n" + base["err"]) if base["err"] else ""),
        encoding="utf-8", errors="replace")
    # 3) 红队刷新（秒级）
    rt = _run([py, "-X", "utf8", "tools/zhuge_redteam.py", "--json"],
              root, runner)
    (log_dir / "huangzhong_redteam_refresh.log").write_text(
        (rt["out"] or "") + (("\n[stderr]\n" + rt["err"]) if rt["err"] else ""),
        encoding="utf-8", errors="replace")

    status = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "baseline_rc": base["rc"], "redteam_rc": rt["rc"],
        "baseline_seconds": base["seconds"], "redteam_seconds": rt["seconds"],
        "backup_ok": backup_ok,
        "evidence": _current_baseline_summary(root),
    }
    status["ok"] = (base["rc"] == 0 and rt["rc"] == 0)
    with (log_dir / "huangzhong_evidence_refresh_status.log").open(
            "a", encoding="utf-8") as f:
        f.write(json.dumps(status, ensure_ascii=False) + "\n")
    return status


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="黄忠验收证据每日刷新（基线+红队）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = refresh()
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else "evidence refresh ok=%s baseline_rc=%s redteam_rc=%s"
          % (rep["ok"], rep["baseline_rc"], rep["redteam_rc"]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
