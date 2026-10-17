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
import time
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
WAIT_SECONDS = 90            # 首跑未完成时的重试间隔
# 30 分钟：17:25 起可等到 ~17:55。依据＝预演实测全程 27m55s（17:10 起跑 →
# ~17:38 结束），原 14 轮（21 分钟）只留 8 分钟余量（29%）——冷启模型加载、
# Ollama 排队、行情源抖动任一项超预期即触发**假 FAIL**，而假 FAIL 与真 FAIL
# 在日志形态上一模一样，是门 3 最难判的一类风险。放宽只影响「本来就该等」的
# 坏情况，正常路径（首跑 ~17:38 完成）第 1 轮就 break，零延迟代价。
MAX_WAIT_ROUNDS = 20         # 30 分钟

DK_FIELDS = ("dk_signal", "dk_regime", "dk_profile",
             "dk_blocked", "dk_reason", "dk_error")

RESULTS = []
_LOG_HANDLE = None


def _today_done(rows, latest):
    """latest 交易日的 done 行数（＝首跑进度）。"""
    if not latest:
        return 0
    return sum(1 for r in rows
               if r.get("date") == latest and r.get("status") == "done")


def _basket_complete(todays):
    """当日 done 行是否已覆盖整个篮子：行数 >= BASKET_N **且**标的齐全。

    与 _needs_wait 同源——等待在行数达到 BASKET_N 即停，断言就必须是
    「够不够」而不是「是不是正好 5」。写成 `== BASKET_N` 时，一旦账本多出
    重复行（run_shadow 双跑），等待会正常结束而断言判 FAIL，且 FAIL 文案
    与真正的失败无法区分。
    标的集合校验则比 `==` 更严：4 个标的 + 1 条重复（共 5 行）旧实现会 PASS。
    """
    return (len(todays) >= BASKET_N
            and len({r.get("symbol") for r in todays}) == BASKET_N)


def _needs_wait(rows, latest, is_monday):
    """周一巡检是否应继续等首跑完成。

    **等待条件必须与最终断言同源**：断言是「latest 日 done 行数 >= BASKET_N
    且不同标的数 == BASKET_N」，所以等待条件也必须是「该日 done 行数
    < BASKET_N」（到量即走，不再多等）。只等「完全没有新行」
    会漏掉「部分落账」这个真实危险窗口——真实时序是先落 1 行，`latest` 立刻
    越过 v0 日期，旧条件随即 break，然后带着 2-3/5 行去断言 ==5 判 FAIL
    （10-17 隔离预演实测：5 篮子全程 27m55s > 17:10→17:25 的 15 分钟窗口）。
    非周一不阻塞（时间窗外只查健康，PENDING 中性）。
    """
    if not is_monday:
        return False
    if latest is None or latest <= LAST_V0_DAY:
        return True  # 首跑尚未落任何新行
    return _today_done(rows, latest) < BASKET_N


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


def _flush_log():
    """重试等待前刷盘——日志即时可见（判例：巡检证据链看字节增长）。"""
    if _LOG_HANDLE is not None and not _LOG_HANDLE.closed:
        try:
            _LOG_HANDLE.flush()
        except Exception:  # noqa: BLE001
            pass


def _tasks_schema_observe():
    """附加观察：devtask 真源 tasks.json 语义健康线（M6 观察季）。

    **不计入 RESULTS / 不影响退出码**——门 3 的验收判据严格是上面 7 项，
    本段只往巡检日志里留一条健康线（ERROR/WARN 计数 + 前几条明细）。
    理由：临门改门口径会让此前所有 PASS 失去可比性（判例：验收口径
    中途漂移 = 观察线作废）。观察满一个周期再决定是否升为正式门。
    """
    path = BASE.parent / ".worktrees" / "tasks.json"
    tools = str(BASE.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        from validate_json import check_tasks_doc
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001  观察项永不阻断巡检
        print("[observe] tasks.json schema：不可读/不可校验（%s）——观察项，"
              "不计入退出码" % str(exc)[:100])
        return
    errors, warns = check_tasks_doc(obj)
    print("[observe] tasks.json schema：%d 工单 / ERROR %d / WARN %d"
          "——观察项，不计入退出码" % (len(obj), len(errors), len(warns)))
    for item in (["ERROR: " + e for e in errors] + ["WARN: " + w for w in warns])[:8]:
        print("          · %s" % item)


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
        # 周一当天首跑仍未发生=真 FAIL（观察点门未兑现）。
        # ★时序加固 v2（10-17 隔离预演实测 27m55s/5 篮子，均值 5.6 分钟最坏 5.8）：
        # 旧重试只在「完全没有新行」（latest 仍 <= LAST_V0_DAY）时等待——而真实
        # 时序是**先落 1 行**，latest 立刻越过 v0 日期、循环随即 break，于是带着
        # 2-3/5 行去断言 ==5 直接判 FAIL。危险窗口恰是「部分落账」，旧条件完全
        # 不覆盖（判例：等待条件必须与最终断言同源，否则等的是另一件事）。
        is_monday = date.today().weekday() == 0
        pending_wait = 0
        while True:
            days = sorted({r.get("date") for r in rows if r.get("date")})
            latest = days[-1] if days else None
            if not _needs_wait(rows, latest, is_monday):
                break  # 非周一，或首跑已完成/无待等
            if pending_wait >= MAX_WAIT_ROUNDS:
                print("[wait] 已达等待上限 %d 轮（%.0f 分钟），按现状判定"
                      % (MAX_WAIT_ROUNDS, MAX_WAIT_ROUNDS * WAIT_SECONDS / 60.0))
                break
            pending_wait += 1
            done_n = _today_done(rows, latest)
            print("[wait] 首跑未完成（%s，第 %d/%d 次重试前等 %ds——run_shadow"
                  " 预演实测 5 篮子约 28 分钟，17:25 巡检会撞上未完成）"
                  % ("尚未落账" if latest is None or latest <= LAST_V0_DAY
                     else "当日 %d/%d 行" % (done_n, BASKET_N),
                     pending_wait, MAX_WAIT_ROUNDS, WAIT_SECONDS))
            _flush_log()
            time.sleep(WAIT_SECONDS)
            rows = load_jsonl(LEDGER)
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
            # 断言上界必须与等待条件同源：等待在「done 行数 >= BASKET_N」即
            # 停止（_needs_wait 返回 False），若此处仍写 `== BASKET_N`，一旦
            # 账本多出重复行（run_shadow 双跑）就会「等到 6 行 → 断言 ==5 →
            # 判 FAIL」，而 FAIL 文案与真正的失败无法区分。
            # 改为「>= BASKET_N 且不同标的数 == BASKET_N」：既与等待同源，
            # 又比原来的 == 更严——4 个标的 + 1 条重复（共 5 行）旧码会 PASS。
            syms = {r.get("symbol") for r in todays}
            check("最新交易日 %s done 行数=%d（预期 %d）"
                  % (latest, len(todays), BASKET_N),
                  _basket_complete(todays),
                  "不同标的 %d 个；账本总行数 %d（v0 存量 + 新行）%s"
                  % (len(syms), len(rows),
                     "" if len(todays) == BASKET_N else
                     "；行数>篮子数=疑似重复写入，须查 run_shadow 是否双跑"))
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

    # ---- 附加观察（不计入 7 项门/不影响退出码；M6 观察季）----
    _tasks_schema_observe()

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
