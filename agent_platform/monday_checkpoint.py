#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5 刀 4：周一观察点护航巡检（2026-10-17 规划 81dda30）。

纯只读巡检：不写任何账本（只读影子/perf/trace 三账本 + 查计划任务状态），
出件自写日志 —— 运行即同写 data/shadow/monday_checkpoint.log（append），
不依赖启动方的 shell 重定向（计划任务 Execute=python.exe 时 `1>` 不是重定向）。

验收门（M5 规划刀 4 判据）：
  1. 影子首跑 5 篮子行全带 dk_* 六字段 + schema_version=m5.v1
  2. replay_trace 对最新 run_id 锚点链可回查
  3. perf_ledger 行数>0 且 perf_key 唯一（幂等完好）
  4. 三计划任务当日执行状态（AgentShadowDaily / AgentChiefPush /
     AgentShadowEvalWeekly）

退出码：0=全部 PASS，1=存在 FAIL（供计划任务/人工判别）。
红线：零账本写副作用——唯一落盘是巡检日志本身，演示/巡检永不落真仓（M5 红线 2）。
"""
import atexit
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "engine"))
sys.path.insert(0, str(BASE / "adapters"))

LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
PERF = BASE / "data" / "perf_ledger.jsonl"
LOG = BASE / "data" / "shadow" / "monday_checkpoint.log"
BASKET_N = 5  # 茅台/五粮液/平安/宁德/招行（run_shadow.BASKET）
LAST_V0_DAY = "2026-10-14"  # 存量 v0 行末日：最新交易日未超过它=首跑未发生

DK_FIELDS = ("dk_signal", "dk_regime", "dk_profile",
             "dk_blocked", "dk_reason", "dk_error")

RESULTS = []
_LOG_HANDLE = None


class _Tee:
    """把输出同时写控制台与日志文件（日志写失败不影响巡检结论）。"""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
            except Exception:  # noqa: BLE001  日志不可写时保住控制台
                pass
        return len(s)

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        return False


def _close_log():
    global _LOG_HANDLE
    try:
        if _LOG_HANDLE is not None and not _LOG_HANDLE.closed:
            _LOG_HANDLE.flush()
            _LOG_HANDLE.close()
    except Exception:  # noqa: BLE001
        pass


def _tee_log():
    """计划任务无终端时自写日志（照 run_shadow.py 长跑纪律，不依赖 shell 重定向）。

    反面判例：计划任务 Execute 直接是 python.exe 时，Arguments 里的
    `1> log 2>&1` 不会被任何 shell 解释，只会变成脚本的普通参数——
    任务照跑、退出码照出，但**日志永不落盘**，观察点门失去证据链。
    """
    global _LOG_HANDLE
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG_HANDLE = open(LOG, "a", encoding="utf-8")
    except Exception:  # noqa: BLE001  落盘失败不阻断巡检
        _LOG_HANDLE = None
        return
    sys.stdout = _Tee(sys.stdout, _LOG_HANDLE)
    sys.stderr = _Tee(sys.stderr, _LOG_HANDLE)
    atexit.register(_close_log)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" —— " + detail) if detail else ""))


def load_jsonl(p):
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def main():
    _tee_log()
    print("=" * 56)
    print("M5 刀4 周一观察点护航巡检 %s %s" % (
        date.today(), datetime.now().strftime("%H:%M:%S")))
    print("=" * 56)

    # ---- 门 1：影子首跑行契约 ----
    rows = load_jsonl(LEDGER)
    if not rows:
        check("影子账本存在且有行", False, str(LEDGER))
    else:
        days = sorted({r.get("date") for r in rows if r.get("date")})
        latest = days[-1] if days else None
        # 周一观察点时间窗外首跑未发生=PENDING 中性（不拉低退出码）；
        # 周一当天首跑仍未发生=真 FAIL（观察点门未兑现）
        is_monday = date.today().weekday() == 0
        if latest is None or latest <= LAST_V0_DAY:
            check("影子首跑已发生（最新交易日 > %s）" % LAST_V0_DAY,
                  not is_monday,
                  ("最新=%s，周一首跑未发生/未落账——观察点门未兑现！"
                   if is_monday else
                   "最新=%s，首跑未发生（PENDING 中性，周一 17:10 后验收）")
                  % latest)
            # 首跑未发生时字段三项无从验收，显式跳过
            print("[SKIP] schema_version/dk/degraded 三项（待首跑后验收）")
        else:
            todays = [r for r in rows if r.get("date") == latest
                      and r.get("status") == "done"]
            check("最新交易日 %s done 行数=%d（预期 %d）"
                  % (latest, len(todays), BASKET_N),
                  len(todays) == BASKET_N,
                  "账本总行数 %d（v0 存量 + 新行）" % len(rows))
            m5_rows = [r for r in todays
                       if r.get("schema_version") == "m5.v1"]
            check("首跑行全带 schema_version=m5.v1",
                  len(m5_rows) == len(todays) and bool(todays),
                  "%d/%d" % (len(m5_rows), len(todays)))
            no_dk = [r["symbol"] for r in todays
                     if any(f not in r for f in DK_FIELDS)]
            check("首跑行全带 dk_* 六字段", not no_dk,
                  "缺失行: %s" % no_dk if no_dk else "六字段键齐备")
            bad_deg = [r["symbol"] for r in todays
                       if r.get("parse_ok") is False
                       and r.get("degraded") is not True]
            check("degraded 联动（parse_ok=false 行 degraded 必 True）",
                  not bad_deg,
                  "违例: %s" % bad_deg if bad_deg else "刀1 扩义生效")

    # ---- 门 2：trace 锚点回放 ----
    run_ids = [r.get("run_id") for r in rows if r.get("run_id")]
    if run_ids:
        from adapters.trace_link import LinkStore, replay_trace
        target = run_ids[-1]
        try:
            rep = replay_trace(LinkStore(), target)
            n_hit = len(rep.get("matches", []))
            check("replay_trace 锚点回放（%s）" % target,
                  rep.get("verdict", "").lower() in ("ok", "partial")
                  and n_hit > 0,
                  "verdict=%s 命中=%d 缺环=%s" % (
                      rep.get("verdict"), n_hit,
                      rep.get("missing") or "无"))
        except Exception as exc:  # noqa: BLE001
            check("replay_trace 锚点回放", False, str(exc)[:120])
    else:
        check("replay_trace 锚点回放", False, "账本无 run_id")

    # ---- 门 3：perf 账本幂等完好 + 无演示污染 ----
    perf = load_jsonl(PERF)
    keys = [r.get("perf_key") for r in perf if r.get("perf_key")]
    check("perf_ledger 行数>0 且 perf_key 唯一",
          len(perf) > 0 and len(keys) == len(set(keys)),
          "%d 行 / %d 键" % (len(perf), len(set(keys))))
    import re
    hex12 = re.compile(r"^[0-9a-f]{12}$")
    fake = [r.get("run_id", "?") for r in perf
            if not hex12.match(r.get("run_id", ""))]
    check("perf_ledger 无演示污染（run_id 全 12 位 hex）", not fake,
          "构造行: %s" % fake if fake else "真仓形态纯净")

    # ---- 门 4：三计划任务当日执行状态 ----
    try:
        ps = ("Get-ScheduledTask AgentShadowDaily,AgentChiefPush,"
              "AgentShadowEvalWeekly | ForEach-Object { "
              "$i = $_ | Get-ScheduledTaskInfo; "
              "'{0}|{1}|{2}|{3}' -f $_.TaskName, $_.State, "
              "$i.LastRunTime, $i.LastTaskResult }")
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30).stdout.strip()
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            name, state, last, code = parts
            ran_today = last.startswith(str(date.today()))
            check("任务 %s" % name,
                  state in ("Ready", "Running", "Queued") and
                  (code in ("0", "267009", "267011") or not ran_today),
                  "state=%s last=%s result=%s%s" % (
                      state, last[:16], code,
                      "" if ran_today else "（今日未到/未跑，仅查健康）"))
    except Exception as exc:  # noqa: BLE001
        check("计划任务状态查询", False, str(exc)[:120])

    # ---- 汇总 ----
    fails = [n for n, ok, _ in RESULTS if not ok]
    print("-" * 56)
    print("结论: %d/%d PASS%s" % (
        len(RESULTS) - len(fails), len(RESULTS),
        "（FAIL: %s）" % ", ".join(fails) if fails else "——观察点门全过"))
    print("EXIT=%d 日志=%s" % (1 if fails else 0, LOG))
    sys.stdout.flush()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
