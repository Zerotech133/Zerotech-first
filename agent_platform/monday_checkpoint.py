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
import logging

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "engine"))
sys.path.insert(0, str(BASE / "adapters"))

LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
PERF = BASE / "data" / "perf_ledger.jsonl"
LOG = BASE / "data" / "shadow" / "monday_checkpoint.log"
BASKET_N = 5  # 茅台/五粮液/平安/宁德/招行（run_shadow.BASKET）
LAST_V0_DAY = "2026-10-14"  # 存量 v0 行末日：最新交易日未超过它=首跑未发生
WAIT_SECONDS = 90  # 首跑未完成时的重试间隔
# 180 分钟：17:25 起可等到 ~20:25。依据是**实测分布**而非乐观基线：
#   · 空载（2026-10-17 venv py3.13）：5 票 27m55s = 5.6 分钟/票；
#   · 竞争态（2026-10-18 03:29 生产 py311，同时有 22GB 大模型占显存 →
#     9b 被挤到 CPU，runner WS 7.64GB）：5 票 4116s = 68.6 分钟，单票
#     latency_s = 1101 / 2283 / 227 / 253 / 237 秒——**双峰**，最慢一票 38 分钟。
# 5 票都按实测最慢跑 = 190 分钟；180 分钟覆盖 4.7 个这样的票，而真实总时长
# 取决于当天有没有别的模型抢显存（不可预知的量）。
#
# 为什么不用「N 分钟无新行」做停滞判据：竞争态下单票要跑 38 分钟，期间账本
# **一行都不落**，任何停滞判据都会把「正在算」误判成「已死」——这是本文件
# 已犯过一次的同类错误（等待条件必须与断言同源，不能拿别的信号近似）。
# 停滞在这个系统里没有可用的观测面（last_run.log 只在每票完成时写一行），
# 所以只能给足上限。
#
# 代价可控：任务 IgnoreNew + PT72H，后面没有依赖它的任务；等待期间每 90 秒
# 打印一次进度，人看日志能区分「在推进」与「没动静」。正常路径首轮即 break。
MAX_WAIT_ROUNDS = 120  # 180 分钟

DK_FIELDS = (
    "dk_signal",
    "dk_regime",
    "dk_profile",
    "dk_blocked",
    "dk_reason",
    "dk_error",
)

RESULTS = []
_LOG_HANDLE = None
_ORIG_STDOUT = None
_ORIG_STDERR = None


def _ran_today(last):
    """2026-10-19 审计修复（K-ORG 审计 P1-1）：LastRunTime 日期容错解析。

    PowerShell Get-ScheduledTaskInfo 的 LastRunTime 本机输出为斜杠格式
    （如 "2026/10/17 23:27"），原实现 last.startswith(str(date.today()))
    按纯 ISO 前缀比对恒 False → ran_today 恒 False → 门 4 对「当日已跑但
    LastTaskResult 异常」的任务 fail-open 放行。现归一化后精确比对年月日。
    """
    day = (
        last.strip().replace("T", " ").split(" ")[0].replace("/", "-")
        if last.strip()
        else ""
    )
    try:
        y, m, d = (int(p) for p in day.split("-"))
    except ValueError:
        return False
    today = date.today()
    return (y, m, d) == (today.year, today.month, today.day)


def _today_counts(rows, latest):
    """latest 日的 (done 行数, failed 行数)。

    failed 也要数：判断「今天这一批是否已经跑完」不能只看 done——全失败的一天
    done 恒为 0，若只看 done 就会把「跑完了但全挂」当成「还没跑完」而白等到
    上限（门 3 的 FAIL 来得越晚，人工介入的窗口越小）。
    """
    if not latest:
        return 0, 0
    todays = [r for r in rows if r.get("date") == latest]
    return (
        sum(1 for r in todays if r.get("status") == "done"),
        sum(1 for r in todays if r.get("status") == "failed"),
    )


def _basket_complete(todays):
    """当日 done 行是否已覆盖整个篮子：行数 >= BASKET_N **且**标的齐全。

    与 _needs_wait 同源——等待在行数达到 BASKET_N 即停，断言就必须是
    「够不够」而不是「是不是正好 5」。写成 `== BASKET_N` 时，一旦账本多出
    重复行（run_shadow 双跑），等待会正常结束而断言判 FAIL，且 FAIL 文案
    与真正的失败无法区分。
    标的集合校验则比 `==` 更严：4 个标的 + 1 条重复（共 5 行）旧实现会 PASS。
    2026-10-30 动态池：当日票数可大于 BASKET_N，门只要「至少覆盖最小篮子」。
    """
    return (
        len(todays) >= BASKET_N and len({r.get("symbol") for r in todays}) >= BASKET_N
    )


def _needs_wait(rows, latest, is_monday):
    """周一巡检是否应继续等首跑完成。

    **等的必须是「这批跑完了没有」，不是「够不够时间」**（判例：等待条件必须与
    最终断言同源）。run_shadow 对篮子 5 票是串行处理、每票恰好写一行
    （run_shadow.py:287 循环、:338 落账；失败也在两次尝试后落 failed 行），
    所以「当日已落地行数（done+failed）达到 BASKET_N」与「这批跑完了」等价——
    这是唯一可靠的完成信号，且不依赖当天机器负载。

    历史两版都被时间/近似信号带偏：
      · v1 只等「完全没有新行」→ 先落 1 行就 break，带着 2-3/5 去断言判 FAIL；
      · v2 等「done < 5」→ 4 done + 1 failed 时永远等不到，白等满上限才判
        FAIL；且「done 到量」比「整批落地」晚，纯失败的一天要等满。
    现在 done/failed 一起数：已落地即不再等，成不成篮交给断言判。

    非周一不阻塞（时间窗外只查健康，PENDING 中性）。
    """
    if not is_monday:
        return False
    if latest is None or latest <= LAST_V0_DAY:
        return True  # 首跑尚未落任何新行
    done_n, failed_n = _today_counts(rows, latest)
    return (done_n + failed_n) < BASKET_N


class _Tee:
    """把输出同时写控制台与日志文件（日志写失败不影响巡检结论）。"""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/monday_checkpoint.py", exc)
        return len(s)

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/monday_checkpoint.py", exc)

    def isatty(self):
        return False


def _close_log():
    global _LOG_HANDLE, _ORIG_STDOUT, _ORIG_STDERR
    # 先恢复原始流，再关日志；否则解释器 finalize 时会 flush 已关闭的
    # _Tee 日志句柄，Windows 下表现为进程退出码 120（巡检已 PASS 但任务红）。
    if _ORIG_STDOUT is not None:
        sys.stdout = _ORIG_STDOUT
    if _ORIG_STDERR is not None:
        sys.stderr = _ORIG_STDERR
    try:
        if _LOG_HANDLE is not None and not _LOG_HANDLE.closed:
            _LOG_HANDLE.flush()
            _LOG_HANDLE.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/monday_checkpoint.py", exc)


def _tee_log():
    """计划任务无终端时自写日志（照 run_shadow.py 长跑纪律，不依赖 shell 重定向）。

    反面判例：计划任务 Execute 直接是 python.exe 时，Arguments 里的
    `1> log 2>&1` 不会被任何 shell 解释，只会变成脚本的普通参数——
    任务照跑、退出码照出，但**日志永不落盘**，观察点门失去证据链。
    """
    global _LOG_HANDLE, _ORIG_STDOUT, _ORIG_STDERR
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG_HANDLE = open(LOG, "a", encoding="utf-8")
    except Exception:  # noqa: BLE001
        _LOG_HANDLE = None
        return
    _ORIG_STDOUT, _ORIG_STDERR = sys.stdout, sys.stderr
    sys.stdout = _Tee(sys.stdout, _LOG_HANDLE)
    sys.stderr = _Tee(sys.stderr, _LOG_HANDLE)
    atexit.register(_close_log)


def _flush_log():
    """重试等待前刷盘——日志即时可见（判例：巡检证据链看字节增长）。"""
    if _LOG_HANDLE is not None and not _LOG_HANDLE.closed:
        try:
            _LOG_HANDLE.flush()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/monday_checkpoint.py", exc)


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
    except Exception as exc:  # noqa: BLE001
        print(
            "[observe] tasks.json schema：不可读/不可校验（%s）——观察项，"
            "不计入退出码" % str(exc)[:100]
        )
        return
    errors, warns = check_tasks_doc(obj)
    print(
        "[observe] tasks.json schema：%d 工单 / ERROR %d / WARN %d"
        "——观察项，不计入退出码" % (len(obj), len(errors), len(warns))
    )
    for item in (["ERROR: " + e for e in errors] + ["WARN: " + w for w in warns])[:8]:
        print("          · %s" % item)


# ------------------------------------------------------------------
# B 路追加观察项（2026-10-18）：strategy 域首跑可见性（M6 观察季）。
# 铁律：只增不改——门 3 的判据与输出格式冻结，本段永不 append 进
# RESULTS / 永不影响退出码（判例：验收口径中途漂移=观察线作废）。
# 两个观察项均只读（exists/read_text），坏行经 load_jsonl 跳过并显式
# 计数告警（fail-visible），异常打印不吞不静默、不阻断巡检。
STRATEGY_RUNS = BASE / "data" / "strategy" / "strategy_runs.jsonl"
CODEX_REGS = BASE / "data" / "codex_bridge" / "registrations.jsonl"


def _strategy_runs_observe(path=None):
    """观察：strategy 账本是否已首落账（周一 17:10 影子首跑，先于 17:25 巡检）。

    文件缺失=PENDING 中性（首跑未到，不拉低退出码）；存在=报告总行数与
    带 run_id 行数（判决行实际带的影子链接字段，对账锚点）。
    注：判决行本身无 schema_version 键（record_verdicts 不写该键，
    schema 契约在影子账本侧），故不做 schema 计数——2026-10-18 预演
    实证恒 0 会误导现场判读。
    """
    p = Path(path) if path is not None else STRATEGY_RUNS
    try:
        if not p.exists():
            print(
                "[observe] strategy_runs：文件不存在（PENDING 中性——"
                "周一 17:10 首跑后应存在）——观察项，不计入退出码"
            )
            return
        rows = load_jsonl(p)  # 坏行在 load_jsonl 内已打印计数并跳过
        anchored = sum(1 for r in rows if r.get("run_id"))
        print(
            "[observe] strategy_runs：%d 行 / 带 run_id %d 行"
            "——观察项，不计入退出码" % (len(rows), anchored)
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "[observe] strategy_runs：不可读（%s）——观察项，不计入退出码"
            % str(exc)[:100]
        )


def _codex_regs_observe(path=None):
    """观察：codex_bridge 受理账本可见性（M2 每次受理落 1 行）。"""
    p = Path(path) if path is not None else CODEX_REGS
    try:
        if not p.exists():
            print(
                "[observe] codex_registrations：文件不存在（PENDING 中性）"
                "——观察项，不计入退出码"
            )
            return
        rows = load_jsonl(p)
        sids = [str(r.get("strategy_id")) for r in rows if r.get("strategy_id")]
        print(
            "[observe] codex_registrations：%d 行 / strategy_id: %s"
            "——观察项，不计入退出码"
            % (len(rows), ", ".join(sids) if sids else "（无 strategy_id 字段）")
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "[observe] codex_registrations：不可读（%s）——观察项，不计入退出码"
            % str(exc)[:100]
        )


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(
        "[%s] %s%s"
        % ("PASS" if ok else "FAIL", name, (" —— " + detail) if detail else "")
    )


def load_jsonl(p):
    if not p.exists():
        return []
    rows = []
    bad = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    if bad:
        # 观察面是最后一道网（判例：手编辑绕过一切写侧校验）——账本半行
        # （append 中断/手编辑损坏）静默跳过会让「账本损坏」对巡检失明，
        # 门 1 行数判读被静默稀释。显式告警，证据留在巡检日志。
        print(
            "[observe] %s 有 %d 行损坏（半行/手编辑），已跳过不计入判读" % (p.name, bad)
        )
    return rows


def main():
    _tee_log()
    print("=" * 56)
    print(
        "M5 刀4 周一观察点护航巡检 %s %s"
        % (date.today(), datetime.now().strftime("%H:%M:%S"))
    )
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
                break  # 非周一，或首跑已完成/整批已落地
            done_n, failed_n = _today_counts(rows, latest)
            if pending_wait >= MAX_WAIT_ROUNDS:
                print(
                    "[wait] 已达等待上限 %d 轮（%.0f 分钟），按现状判定"
                    "（当日 done %d / failed %d）"
                    % (
                        MAX_WAIT_ROUNDS,
                        MAX_WAIT_ROUNDS * WAIT_SECONDS / 60.0,
                        done_n,
                        failed_n,
                    )
                )
                break
            pending_wait += 1
            print(
                "[wait] 首跑未完成（%s，第 %d/%d 次重试前等 %ds——首跑实测"
                "空载 28 分钟 / 竞争态 69 分钟，17:25 巡检必撞上未完成）"
                % (
                    "尚未落账"
                    if latest is None or latest <= LAST_V0_DAY
                    else "当日 done %d / failed %d（共 %d 票）"
                    % (done_n, failed_n, BASKET_N),
                    pending_wait,
                    MAX_WAIT_ROUNDS,
                    WAIT_SECONDS,
                )
            )
            _flush_log()
            time.sleep(WAIT_SECONDS)
            rows = load_jsonl(LEDGER)
        if latest is None or latest <= LAST_V0_DAY:
            check(
                "影子首跑已发生（最新交易日 > %s）" % LAST_V0_DAY,
                not is_monday,
                (
                    "最新=%s，周一首跑未发生/未落账——观察点门未兑现！"
                    if is_monday
                    else "最新=%s，首跑未发生（PENDING 中性，周一 17:10 后验收）"
                )
                % latest,
            )
            # 首跑未发生时字段三项无从验收，显式跳过
            print("[SKIP] schema_version/dk/degraded 三项（待首跑后验收）")
        else:
            # 2026-10-30：首跑已发生后仍把这一项计入核心判据，和
            # gate3_verdict 的 11 项口径保持一致；否则交叉核对会永远少 1 项。
            check(
                "影子首跑已发生（最新交易日 > %s）" % LAST_V0_DAY,
                True,
                "最新=%s" % latest,
            )
            todays = [
                r for r in rows if r.get("date") == latest and r.get("status") == "done"
            ]
            # 断言上界必须与等待条件同源：等待在「整批已落地（done+failed
            # >= BASKET_N）」即停（_needs_wait 返回 False），所以断言不能写
            # `== BASKET_N`——账本多出重复行（run_shadow 双跑）时会「等到 6 行
            # → 断言 ==5 → 判 FAIL」，而 FAIL 文案与真正的失败无法区分。
            # 现为「>= BASKET_N 且不同标的数 == BASKET_N」：既与等待同源，
            # 又比原来的 == 更严——4 个标的 + 1 条重复（共 5 行）旧码会 PASS。
            syms = {r.get("symbol") for r in todays}
            _dn, _fn = _today_counts(rows, latest)
            check(
                "最新交易日 %s done 行数=%d（预期 %d）"
                % (latest, len(todays), BASKET_N),
                _basket_complete(todays),
                "不同标的 %d 个；当日 failed %d 票；账本总行数 %d"
                "（v0 存量 + 新行）%s"
                % (
                    len(syms),
                    _fn,
                    len(rows),
                    "；行数>篮子数=疑似重复写入，须查 run_shadow 是否双跑"
                    if len(todays) > BASKET_N
                    else (
                        "；有 failed 票=P0，见 last_run.log 与飞书告警" if _fn else ""
                    ),
                ),
            )
            m5_rows = [r for r in todays if r.get("schema_version") == "m5.v1"]
            check(
                "首跑行全带 schema_version=m5.v1",
                len(m5_rows) == len(todays) and bool(todays),
                "%d/%d" % (len(m5_rows), len(todays)),
            )
            no_dk = [r["symbol"] for r in todays if any(f not in r for f in DK_FIELDS)]
            check(
                "首跑行全带 dk_* 六字段",
                not no_dk,
                "缺失行: %s" % no_dk if no_dk else "六字段键齐备",
            )
            bad_deg = [
                r["symbol"]
                for r in todays
                if r.get("parse_ok") is False and r.get("degraded") is not True
            ]
            check(
                "degraded 联动（parse_ok=false 行 degraded 必 True）",
                not bad_deg,
                "违例: %s" % bad_deg if bad_deg else "刀1 扩义生效",
            )

    # ---- 门 2：trace 锚点回放 ----
    run_ids = [r.get("run_id") for r in rows if r.get("run_id")]
    if run_ids:
        from adapters.trace_link import LinkStore, replay_trace

        target = run_ids[-1]
        try:
            rep = replay_trace(LinkStore(), target)
            n_hit = len(rep.get("matches", []))
            check(
                "replay_trace 锚点回放（%s）" % target,
                rep.get("verdict", "").lower() in ("ok", "partial") and n_hit > 0,
                "verdict=%s 命中=%d 缺环=%s"
                % (rep.get("verdict"), n_hit, rep.get("missing") or "无"),
            )
        except Exception as exc:  # noqa: BLE001
            check("replay_trace 锚点回放", False, str(exc)[:120])
    else:
        check("replay_trace 锚点回放", False, "账本无 run_id")

    # ---- 门 3：perf 账本幂等完好 + 无演示污染 ----
    perf = load_jsonl(PERF)
    keys = [r.get("perf_key") for r in perf if r.get("perf_key")]
    check(
        "perf_ledger 行数>0 且 perf_key 唯一",
        len(perf) > 0 and len(keys) == len(set(keys)),
        "%d 行 / %d 键" % (len(perf), len(set(keys))),
    )
    import re

    hex12 = re.compile(r"^[0-9a-f]{12}$")
    fake = [r.get("run_id", "?") for r in perf if not hex12.match(r.get("run_id", ""))]
    check(
        "perf_ledger 无演示污染（run_id 全 12 位 hex）",
        not fake,
        "构造行: %s" % fake if fake else "真仓形态纯净",
    )

    # ---- 门 4：三计划任务当日执行状态 ----
    try:
        ps = (
            "Get-ScheduledTask AgentShadowDaily,AgentChiefPush,"
            "AgentShadowEvalWeekly | ForEach-Object { "
            "$i = $_ | Get-ScheduledTaskInfo; "
            "'{0}|{1}|{2}|{3}' -f $_.TaskName, $_.State, "
            "$i.LastRunTime, $i.LastTaskResult }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            name, state, last, code = parts
            ran_today = _ran_today(last)
            check(
                "任务 %s" % name,
                state in ("Ready", "Running", "Queued")
                and (code in ("0", "267009", "267011") or not ran_today),
                "state=%s last=%s result=%s%s"
                % (
                    state,
                    last[:16],
                    code,
                    "" if ran_today else "（今日未到/未跑，仅查健康）",
                ),
            )
    except Exception as exc:  # noqa: BLE001
        check("计划任务状态查询", False, str(exc)[:120])

    # ---- 附加观察（不计入 7 项门/不影响退出码；M6 观察季）----
    _tasks_schema_observe()

    # ---- 追加观察（B 路 2026-10-18：strategy 域首跑可见性；不计入退出码）----
    _strategy_runs_observe()
    _codex_regs_observe()

    # ---- 汇总 ----
    fails = [n for n, ok, _ in RESULTS if not ok]
    print("-" * 56)
    print(
        "结论: %d/%d PASS%s"
        % (
            len(RESULTS) - len(fails),
            len(RESULTS),
            "（FAIL: %s）" % ", ".join(fails) if fails else "——观察点门全过",
        )
    )
    print("EXIT=%d 日志=%s" % (1 if fails else 0, LOG))
    sys.stdout.flush()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
