#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门 3 独立复核工具（第二意见）——只读，不写任何文件。

## 为什么要它

周一 17:25 的门 3 判定完全依赖 `agent_platform/monday_checkpoint.py`。
**如果巡检自己坏了**（脚本改坏、Python 环境缺包、计划任务没跑起来、
日志写不进去），门 3 就变成一个没人能验的黑洞——而 `monday_checkpoint.py`
是判定方，不能拿它验它自己。

本工具的作用：从原始账本**重新算一遍**同一套判据，与巡检日志交叉核对。
两份结论一致才叫可信；不一致就说明有一方坏了，必须先查清再判门。

## 独立性边界（刻意为之）

- **不 import** `monday_checkpoint`：判据口径对齐（常量、字段名、判法逐条
  抄自该文件），但代码是重写的一份。抄口径不是抄实现——共用实现的话，
  它算错我也跟着错，就失去第二意见的意义。
- **不复用** `load_jsonl` / `_today_counts` / `_basket_complete`：同理。
- 唯一例外是 `adapters.trace_link.replay_trace`——锚点链的解析逻辑复杂
  且已有测试覆盖，重写一份反而引入口径分歧风险；此处显式调用并记录来源。

## 项数是动态的（判读陷阱，务必知道）

巡检汇总行是 `结论: N/M PASS`，M 随账本状态变化：

| 阶段 | M | 构成 |
|---|---|---|
| 首跑未发生 | **7** | 首跑已发生(中性 PASS) + replay + perf×2 + 任务×3 |
| 首跑已发生 | **11** | 首跑 + done 行数 + schema + dk + degraded + replay + perf×2 + 任务×3 |

所以周一真跑后日志会出现 `11/11 PASS`——**这不是判据被改了**，是
三项字段检查从 SKIP 转为实检。本工具同样按此规则输出，并在汇总里
显式标注当前处于哪一态，避免人拿着「7/7」去找「11/11」的差异。

`--skip-tasks` 会让任务三项不入账（本工具 8 项，巡检仍是 11 项）。
交叉核对因此不能拿裸项数硬比：必须从巡检基数里扣掉被跳过的项数再比，
否则同一份账本会被读成「7 vs 4 不一致」的假 FAIL。

## 用法

    python tools/gate3_verdict.py                  # 默认按今天、真仓判定
    python tools/gate3_verdict.py --date 2026-10-19
    python tools/gate3_verdict.py --no-cross-check # 不读巡检日志
    python tools/gate3_verdict.py --base <dir> --trace-links <file>  # 指向隔离副本

退出码：0=全过（或全部 PENDING 中性）／1=存在 FAIL（数据缺失、账本为空、
查询异常一律判 FAIL——第二意见不允许「无法判定」中性放行；本工具只有
0/1 两个退出码，不存在退出码 2）

红线：**零副作用**。不写账本、不写日志、不改计划任务、不推外部通道。
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
import logging

try:  # Windows 控制台/子进程捕获统一 UTF-8，避免中文输出在 cp936 管道下解码失败
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/gate3_verdict.py", exc)

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = HERE.parent / "agent_platform"

# ---- 判据口径（逐条对齐 monday_checkpoint.py，见其 BASKET_N/LAST_V0_DAY/DK_FIELDS）----
BASKET_N = 5
LAST_V0_DAY = "2026-10-14"      # 存量 v0 行末日：最新交易日未超过它=首跑未发生
DK_FIELDS = ("dk_signal", "dk_regime", "dk_profile",
             "dk_blocked", "dk_reason", "dk_error")
TASK_NAMES = ("AgentShadowDaily", "AgentChiefPush", "AgentShadowEvalWeekly")
TS_RE = re.compile(r"^[0-9a-f]{12}$")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
# 两层判据，刻意分开：
#   RESULTS（核心项）——与巡检 SUMMARY 行 1:1 对应，项数必须能对上，
#                       交叉核对比的就是这两组数字；
#   EXTRA（增补项）——本工具比巡检更严或巡检没做的那几条（坏行检查等），
#                     只单独列出，不参与核心项数比对——否则两边永远对不上，
#                     交叉核对就失去意义。
RESULTS = []
EXTRA = []


def check(name, verdict, detail="", extra=False):
    """verdict 接受 PASS/FAIL/SKIP 或 bool（True→PASS、False→FAIL）——
    调用处多传一次三态枚举字面量没意义，容错比苛求更实在。

    SKIP 只打印不入账：巡检在「首跑未发生」态下同样不把字段三项计入
    RESULTS（只打一行 SKIP 提示），所以核心项数才会是 7 而不是 10。
    """
    if verdict is True:
        verdict = PASS
    elif verdict is False:
        verdict = FAIL
    if verdict not in (PASS, FAIL, SKIP):
        raise ValueError("verdict 非法: %r" % (verdict,))
    if verdict != SKIP:
        (EXTRA if extra else RESULTS).append((name, verdict, detail))
    tag = verdict if not extra else verdict + "+"
    print("  [%s] %s%s" % (tag, name, ("  — " + detail) if detail else ""))


def load_jsonl(path):
    """读 JSONL；坏行显式跳过并计数（不静默）。"""
    if not Path(path).exists():
        return [], 0
    rows, bad = [], 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return rows, bad


# ---------------------------------------------------------------- 判据实现

def judge_ledger(rows, today, is_monday):
    """门 1：影子首跑行契约。返回首跑是否已发生。"""
    if not rows:
        check("影子账本存在且有行", FAIL, "账本为空或不存在")
        return False
    bad_days = [i for i, r in enumerate(rows) if not r.get("date")]
    if bad_days:
        check("账本行日期字段完整", FAIL, "%d 行缺 date" % len(bad_days), extra=True)
    days = sorted({r.get("date") for r in rows if r.get("date")})
    latest = days[-1] if days else None

    if latest is None or latest <= LAST_V0_DAY:
        # 首跑未发生：周一=真 FAIL（观察点门未兑现）／非周一=PENDING 中性
        check("影子首跑已发生（最新交易日 > %s）" % LAST_V0_DAY,
              FAIL if is_monday else PASS,
              "最新=%s%s" % (
                  latest,
                  "" if is_monday else "（非周一 PENDING 中性，周一 17:10 后验收）"))
        check("schema_version=m5.v1 全覆盖", SKIP, "待首跑后实检")
        check("dk_* 六字段齐备", SKIP, "待首跑后实检")
        check("degraded 联动", SKIP, "待首跑后实检")
        return False

    check("影子首跑已发生（最新交易日 > %s）" % LAST_V0_DAY, PASS, "最新=%s" % latest)

    todays = [r for r in rows if r.get("date") == latest and r.get("status") == "done"]
    failed_today = [r for r in rows
                    if r.get("date") == latest and r.get("status") != "done"]
    syms = {r.get("symbol") for r in todays}
    # 与巡检 _basket_complete 同口径：至少覆盖 BASKET_N 个不同标的；
    # 动态池当日可大于最小篮子。
    ok_basket = len(todays) >= BASKET_N and len(syms) >= BASKET_N
    check("最新交易日 %s done 行数=%d（预期 %d）" % (latest, len(todays), BASKET_N),
          ok_basket,
          "不同标的 %d 个；当日非 done %d 票%s" % (
              len(syms), len(failed_today),
              "；有 failed 票=P0，查 last_run.log 与飞书告警"
              if failed_today else ""))

    m5 = [r for r in todays if r.get("schema_version") == "m5.v1"]
    check("schema_version=m5.v1 全覆盖", bool(todays) and len(m5) == len(todays),
          "%d/%d" % (len(m5), len(todays)))

    missing = {r.get("symbol"): [f for f in DK_FIELDS if f not in r]
               for r in todays if any(f not in r for f in DK_FIELDS)}
    check("dk_* 六字段齐备", not missing,
          "缺失: %s" % missing if missing else "六字段键齐备（%d 行）" % len(todays))

    # degraded 联动：parse_ok 明确为 False 时 degraded 必须 True（刀 1 扩义）
    bad_deg = [r.get("symbol") for r in todays
               if r.get("parse_ok") is False and r.get("degraded") is not True]
    check("degraded 联动（parse_ok=false → degraded=true）", not bad_deg,
          "违例: %s" % bad_deg if bad_deg else "联动成立")
    return True


def judge_trace(rows, trace_links):
    """门 2：trace 锚点回放（唯一复用生产实现的判据，理由见模块 docstring）。"""
    run_ids = [r.get("run_id") for r in rows if r.get("run_id")]
    if not run_ids:
        check("replay_trace 锚点回放", FAIL, "账本无 run_id")
        return
    target = run_ids[-1]
    try:
        sys.path.insert(0, str(DEFAULT_BASE))
        sys.path.insert(0, str(DEFAULT_BASE / "adapters"))
        from adapters.trace_link import LinkStore, replay_trace
        store = LinkStore(Path(trace_links) if trace_links else None)
        rep = replay_trace(store, target)
        verdict = str(rep.get("verdict", "")).lower()
        n_hit = len(rep.get("matches", []))
        check("replay_trace 锚点回放（%s）" % target,
              verdict in ("ok", "partial") and n_hit > 0,
              "verdict=%s 命中=%d 缺环=%s" % (
                  rep.get("verdict"), n_hit, rep.get("missing") or "无"))
    except Exception as exc:  # noqa: BLE001
        check("replay_trace 锚点回放", FAIL, "%s: %s" % (type(exc).__name__, exc))


def judge_perf(perf, bad_lines):
    """门 3：perf 账本幂等完好 + 无演示污染。"""
    keys = [r.get("perf_key") for r in perf if r.get("perf_key")]
    dup = len(keys) - len(set(keys))
    check("perf_ledger 行数>0 且 perf_key 唯一",
          len(perf) > 0 and dup == 0,
          "%d 行 / %d 键%s" % (len(perf), len(set(keys)),
                               "；重复 %d" % dup if dup else ""))
    fake = [r.get("run_id", "?") for r in perf
            if not TS_RE.match(str(r.get("run_id", "")))]
    check("perf_ledger 无演示污染（run_id 全 12 位 hex）", not fake,
          "构造行: %s" % fake[:4] if fake else "真仓形态纯净")
    # 巡检不查坏行数（load_jsonl 静默跳过），这里补一条观察项
    if bad_lines:
        check("perf_ledger 无坏行", FAIL, "%d 行 JSON 解析失败" % bad_lines, extra=True)


def judge_tasks():
    """门 4：三计划任务当日执行状态（只读查询，不改任务）。"""
    ps = ("Get-ScheduledTask %s | ForEach-Object { $i = $_ | "
          "Get-ScheduledTaskInfo; '{0}|{1}|{2}|{3}' -f $_.TaskName, "
          "$_.State, $i.LastRunTime, $i.LastTaskResult }"
          % ",".join(TASK_NAMES))
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        check("计划任务状态查询", FAIL, "%s: %s" % (type(exc).__name__, exc))
        return
    seen = set()
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name, state, last, code = parts
        seen.add(name)
        ran_today = last.startswith(str(date.today()))
        healthy = (state in ("Ready", "Running", "Queued")
                   and (code in ("0", "267009", "267011") or not ran_today))
        check("任务 %s" % name, PASS if healthy else FAIL,
              "state=%s last=%s result=%s%s" % (
                  state, last[:16], code,
                  "" if ran_today else "（今日未到/未跑，仅查健康）"))
    for name in TASK_NAMES:
        if name not in seen:
            check("任务 %s" % name, FAIL, "未注册或查询无返回")


def cross_check(log_path, skipped=0):
    """与巡检日志交叉核对：两份判据必须同结论，否则一方坏了。

    本条是**元检查**（检查判定方自身），不是门 3 的七项判据之一，
    所以记入 EXTRA——计入核心项会让项数永远比巡检多 1，交叉核对就没法比。

    `skipped`：本次有意跳过、因而没进 RESULTS 的核心项数（目前只有
    `--skip-tasks` 的任务三项）。巡检那边照样会判这三项并计入 N/M，
    所以比对基数必须从巡检数字里扣掉同样的项数——否则 7/7 对上 4/4，
    明明是同一份账本却报假 FAIL。扣减只作用于「基数」：本工具自己
    真实失败时 my_pass 会变小，仍然对不上、仍然判 FAIL。
    """
    p = Path(log_path)
    if not p.exists():
        check("与巡检日志交叉核对", SKIP, "%s 不存在（巡检尚未跑过）" % p.name, extra=True)
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    # 取最后一段（最近一次运行）：从最后一个分隔线起
    seg = text.rsplit("=" * 56, 1)[-1]
    m = re.search(r"结论:\s*(\d+)/(\d+)\s*PASS", seg)
    e = re.search(r"EXIT=(\d+)", seg)
    if not m:
        check("与巡检日志交叉核对", FAIL,
              "日志里找不到「结论: N/M PASS」行（巡检可能改过输出格式）", extra=True)
        return
    n_pass, n_total = int(m.group(1)), int(m.group(2))
    my_fail = [n for n, v, _ in RESULTS if v == FAIL]
    my_total, my_pass = len(RESULTS), len(RESULTS) - len(my_fail)
    # 同口径基数：巡检总项数 / 通过项数减去本次跳过的项数。
    # 跳过项在巡检侧按「通过」扣除（本机任务全健康时即如此）；巡检侧真有
    # 失败时扣完会偏小，my_pass 对不上 → 仍报不一致，不会被悄悄抹平。
    exp_total, exp_pass = n_total - skipped, n_pass - skipped
    same = (exp_total == my_total) and (exp_pass == my_pass)
    check("与巡检日志交叉核对", PASS if same else FAIL,
          "巡检=%d/%d（EXIT=%s） 本工具=%d/%d%s%s" % (
              n_pass, n_total, e.group(1) if e else "?", my_pass, my_total,
              "（扣除跳过 %d 项后比 %d/%d）" % (skipped, exp_pass, exp_total)
              if skipped else "",
              "" if same else " ← 两份判据不一致，先查哪方坏了再判门"),
          extra=True)


def _warn_early_run(rows, day, is_monday):
    """在「还没跑完」的时候提醒别把 FAIL 当真失败。

    本工具**不等待**（这正是它与巡检的分工：巡检等到整批落地再判，
    复核立即给出现状）。所以周一 17:25 刚起跑几十秒就跑一遍，必然看到
    当日 done 行不足——那是「正在跑」，不是「跑坏了」。
    两条判据形态一样但结论相反，是最容易误判的一类，必须显式提示。
    """
    if not is_monday:
        return
    days = sorted({r.get("date") for r in rows if r.get("date")})
    latest = days[-1] if days else None
    if not latest or latest <= LAST_V0_DAY:
        return
    todays = [r for r in rows if r.get("date") == latest and r.get("status") == "done"]
    if len(todays) >= BASKET_N:
        return
    print()
    print("  ⚠ 注意：当日 done %d/%d 票。巡检在本时段会**继续等待**整批落地"
          "（窗口 17:25 起，硬上限 180 分钟）。" % (len(todays), BASKET_N))
    print("     若 run_shadow 进程仍在跑（last_run.log 或进程列表可见），"
          "这个 FAIL 只是「还没跑完」——请等巡检出结论后再复核一次。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="门 3 独立复核（只读，第二意见）")
    ap.add_argument("--base", default=str(DEFAULT_BASE),
                    help="agent_platform 目录（默认真仓）")
    ap.add_argument("--date", default=str(date.today()),
                    help="判定基准日（默认今天，格式 YYYY-MM-DD）")
    ap.add_argument("--trace-links", default="",
                    help="trace_links.jsonl 路径（默认 LinkStore 内置真仓路径）")
    ap.add_argument("--log", default="",
                    help="巡检日志路径（默认 <base>/data/shadow/monday_checkpoint.log）")
    ap.add_argument("--no-cross-check", action="store_true",
                    help="跳过与巡检日志的交叉核对（隔离环境用）")
    ap.add_argument("--skip-tasks", action="store_true",
                    help="跳过计划任务状态查询（隔离/异地环境用——任务状态只在本机有意义）")
    args = ap.parse_args(argv)

    base = Path(args.base)
    ledger = base / "data" / "shadow" / "shadow_ledger.jsonl"
    perf_p = base / "data" / "perf_ledger.jsonl"
    log_p = Path(args.log) if args.log else (base / "data" / "shadow" / "monday_checkpoint.log")

    is_monday = date.fromisoformat(args.date).weekday() == 0
    print("=" * 62)
    print("门 3 独立复核 %s（基准日 %s，%s）" % (
        date.today(), args.date, "周一" if is_monday else "非周一"))
    print("  账本: %s" % ledger)
    print("=" * 62)

    rows, bad_ledger = load_jsonl(ledger)
    if bad_ledger:
        check("影子账本无坏行", FAIL, "%d 行 JSON 解析失败" % bad_ledger, extra=True)

    first_done = judge_ledger(rows, args.date, is_monday)
    judge_trace(rows, args.trace_links)
    perf, bad_perf = load_jsonl(perf_p)
    judge_perf(perf, bad_perf)
    if args.skip_tasks:
        check("计划任务状态", SKIP, "--skip-tasks（隔离/异地环境）")
        skipped_core = len(TASK_NAMES)
    else:
        judge_tasks()
        skipped_core = 0
    if not args.no_cross_check:
        cross_check(log_p, skipped=skipped_core)

    fails = [n for n, v, _ in RESULTS if v == FAIL]
    extra_fails = [n for n, v, _ in EXTRA if v == FAIL]
    total = len(RESULTS)
    print("-" * 62)
    print("阶段: %s → 核心判据 %d 项（首跑前 7 项 / 首跑后 11 项，与巡检同口径）"
          % ("首跑已发生" if first_done else "首跑未发生", total))
    if EXTRA:
        print("增补判据 %d 项（巡检未做，不参与交叉核对）: %s"
              % (len(EXTRA), ", ".join("%s=%s" % (n, v) for n, v, _ in EXTRA)))
    if fails or extra_fails:
        if fails:
            print("复核结论: FAIL %d/%d（%s）"
                  % (total - len(fails), total, ", ".join(fails)))
            _warn_early_run(rows, args.date, is_monday)
        else:
            print("复核结论: 核心 %d/%d 全过，但增补项 FAIL（%s）"
                  % (total, total, ", ".join(extra_fails)))
        print("退出码 1")
        return 1
    print("复核结论: %d/%d 全过%s" % (
        total, total,
        "" if first_done else "（首跑未发生，字段三项 SKIP 待周一实检）"))
    print("退出码 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
