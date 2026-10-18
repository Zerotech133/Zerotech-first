# -*- coding: utf-8 -*-
"""monday_drill：周一观察点现场 SOP 的只读 dry-run 演练器（不执行任何命令）。

## 用途

把《2026-10-18_周一观察点现场SOP手册.md》的时序做成可演练的时间轴：
按时刻打印「几点 / 该看什么 / 该跑哪条命令 / 期望退出码 / 期望看到什么字样 /
出问题查哪里」，让值班人在 2026-10-19（周一）17:10 前把每个时间点预演一遍。

**它只打印命令文本，供人复制到终端自行执行，绝不代跑。** 现场真跑仍以
SOP 手册与各工具源码实测输出为准（手册末尾口径：与实测冲突时以工具实测为准）。

## 时间轴（出处：SOP §一~§四 + 工具箱索引卡第 21 行时序速查）

  16:50  人工   check_trigger_chain 触发链体检（必跑）
  17:10  自动   AgentShadowDaily —— 不要手动跑，只看 last_run.log
  17:25  自动   AgentMondayCheckpoint + 人工 check_observation_point 主判读
                （FAIL 时 verify_strategy_ledger / gate3_verdict 复核）
  17:35  自动   AgentChiefPush —— 看 chief_last_run.log 是否含 SEND_OK
  周五 17:45 自动 AgentShadowEvalWeekly + gate3_verdict 复核
                （手册仅写「周五」未给时刻；此处 17:45 取自本机任务 XML 实读）

## CLI

  --at HH:MM   模拟某时刻：打印「现在该做什么」+「接下来三步」+「下一个是」
               缺省 = 当前时间（只取本地时钟的 HH:MM，不看星期）
  --list       打印完整时间轴（含 5 个锚点全部字段）
  --json       机读输出（含 timeline 列表；可与 --at / --list 组合）
  --list 与 --at 同给时以 --list 为准（时间轴模式）。

## 退出码

  0 = 正常输出（无论时刻是否落在轴内——轴外只提示，不当错误）
  2 = 参数错误（--at 格式非法，或 argparse 层参数错误）

## 硬约束（READ_ONLY）

本模块是 dry-run 演练器，**纯只读**：
  - 绝不调用外部进程/计划任务（不 import subprocess，也不经 os.system 兜底）；
    唯一的「潜在执行入口」run_command() 无条件抛 ReadOnlyViolation；
  - 绝不写 data/、绝不创建任何目录、绝不落任何日志；
  - 全部输出走 stdout/stderr，只读磁盘面 = 本文件与传入的时间参数。
READ_ONLY 为模块级常量；任何潜在执行入口先 _assert_read_only() 显式拒绝。
若有人把它改成 False，run_command() 仍然拒绝——常量只是第一道闸。

## 取证说明

本文件为纯打印器，未运行任何被列出的工具（编制时只读源码 argparse 段与
docstring，并只读两个计划任务 XML 核对形状）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

TOOL_NAME = "monday_drill"
TOOL_VERSION = "1.0.0"

#: 模块级只读闸。任何潜在执行入口都必须先过它。
READ_ONLY = True

#: 统一解释器（SOP 手册头部口径）。仅用于打印，不用于执行。
PY = "C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
#: 人工观察统一走真仓路径（与计划任务侧 C:\\aios junction 同一份 data/）。
REPO = "C:/项目/老田的AI助手"
AP = "C:/项目/老田的AI助手/agent_platform"


class ReadOnlyViolation(RuntimeError):
    """dry-run 演练器拒绝任何执行/写盘动作。"""


def _assert_read_only(entry: str = "unknown") -> None:
    """潜在执行入口的第一道闸：READ_ONLY 不为 True 就整体拒绝。"""
    if not READ_ONLY:
        raise ReadOnlyViolation(
            "%s: 演练器被配置为非只读模式，拒绝继续（本工具设计上无写/执行能力）"
            % entry
        )


def run_command(command: str) -> int:
    """唯一的「潜在执行入口」——永远拒绝。

    时间轴里的命令只是给人复制的文本；若将来有人想「顺手执行一下」，
    必须在 _assert_read_only() 之后立刻撞上这里。计划任务同样不许经此触发。
    """
    _assert_read_only("run_command")
    raise ReadOnlyViolation(
        "dry-run 演练器不执行任何命令（含计划任务）：%r —— 请自行复制到终端执行"
        % command
    )


def _cmd(cmd: str, rc: str, expect: str) -> Dict[str, str]:
    return {"cmd": cmd, "rc": rc, "expect": expect}


# ---------------------------------------------------------------------------
# 时间轴（字段：at/minute/order/label/mode/forbidden/look/commands/expect_text/
#                 troubleshoot/notes）
# ---------------------------------------------------------------------------
TIMELINE: List[Dict[str, Any]] = [
    {
        "at": "16:50",
        "minute": 16 * 60 + 50,
        "order": 1,
        "label": "T-30min 前置体检（必跑）",
        "mode": "人工",
        "forbidden": None,
        "look": [
            "Ollama daemon 存活：11434/api/ps 能返回 JSON（models 可空）",
            "四个计划任务已注册：AgentShadowDaily / AgentMondayCheckpoint / "
            "AgentChiefPush / AgentShadowEvalWeekly",
            "巡检工具链路可用 + 基线形态：check_observation_point 退出码 1（全 PENDING 中性）",
            "账本基线完好：backups/2026-10-18_observation_baseline/ 下 5 个 .baseline + MANIFEST.md",
            "触发链前置自检：check_trigger_chain（本段主项）",
        ],
        "commands": [
            _cmd("$PY tools/check_trigger_chain.py --repo-root .",
                 "0（或仅 ChiefPush 首跑前无日志一项 WARN）",
                 "汇总: PASS N / WARN N / FAIL 0 → 结论: 无 FAIL 且有 PASS——链路就绪"),
            _cmd("$PY tools/check_observation_point.py --data-root data",
                 "1",
                 "汇总: PASS 0 / PENDING 5 / FAIL 0 → 结论: 全 PENDING——中性"),
            _cmd("curl -s --max-time 3 http://127.0.0.1:11434/api/ps",
                 "0",
                 '含 "models" 字段的 JSON；连接拒绝 = daemon 未起'),
            _cmd("ls /c/Windows/System32/Tasks/ | grep Agent",
                 "0",
                 "四个任务名齐在（本 shell 可能权限拒绝，改用直读 XML 或 -f 单文件探测）"),
            _cmd('ls "C:/项目/backups/2026-10-18_observation_baseline/"',
                 "0",
                 "5 个 .baseline + MANIFEST.md 共 6 件"),
        ],
        "expect_text": [
            "结论: 无 FAIL 且有 PASS——链路就绪，可放行（退出码 0）",
            "结论: 全 PENDING——中性：首跑/巡检未到，非失败（退出码 1）",
        ],
        "troubleshoot": "SOP §五 异常矩阵 #3（Ollama 未起→报 lead）/ #4（任务未注册→报 lead）",
        "notes": [
            "预检任何一项不就绪 → 先按矩阵处置，不要等到 17:10 才发现。",
            "check_trigger_chain 只覆盖三个任务（a 项自检 XML）；第四任务 "
            "AgentShadowEvalWeekly 靠本段 ls/直读自行核对。",
        ],
    },
    {
        "at": "17:10",
        "minute": 17 * 60 + 10,
        "order": 2,
        "label": "AgentShadowDaily 自动触发（观察段）",
        "mode": "自动（计划任务）",
        "forbidden": "不要手动跑 AgentShadowDaily——无交易日判断，手动重跑会污染账本；"
                     "90s 重试等待属设计内。",
        "look": [
            "data/shadow/last_run.log 出现 [strategy] 装配行与每票产出行",
            "data/strategy/strategy_runs.jsonl 首跑后 20 行（5 票 × 4 策略），"
            "每行带 run_id（12 位 hex）与 verdict_key",
        ],
        "commands": [
            _cmd("tail -n 40 data/shadow/last_run.log",
                 "0（只读查看，非判据）",
                 "[strategy] codex 桥接插件 +1 (skip 0)"),
            _cmd("tail -n 20 data/shadow/last_run.log",
                 "0（只读查看，非判据）",
                 "[strategy] <sym> 产出 4 条 recorded=4 replayed=0（5 票共 20 条）"),
            _cmd("wc -l data/strategy/strategy_runs.jsonl",
                 "0（只读查看，非判据）",
                 "20 data/strategy/strategy_runs.jsonl"),
        ],
        "expect_text": [
            "[strategy] codex 桥接插件 +1 (skip 0)",
            "[strategy] <sym> 产出 4 条 recorded=4 replayed=0",
        ],
        "troubleshoot": "SOP §五 #6（strategy 零行→看 fail-open 哪一级）/ #4（任务未触发）/ #2（17:35 后仍全 PENDING）",
        "notes": [
            "首跑实测空载 28 分钟、竞争态最长 69 分钟（monday_checkpoint.py:38-44）。",
            "17:25 触发后 monday_checkpoint 会自动每 90s 重试、最长等 180 分钟；"
            "monday_checkpoint.log 内 [wait] 行 = 在推进，属正常。",
        ],
    },
    {
        "at": "17:25",
        "minute": 17 * 60 + 25,
        "order": 3,
        "label": "AgentMondayCheckpoint 自动 + check_observation_point 主判读（门 3）",
        "mode": "自动（计划任务）+ 人工巡检",
        "forbidden": None,
        "look": [
            "任务自动跑 monday_checkpoint.py，写 data/shadow/monday_checkpoint.log"
            "（含 [observe] 两段标记，属设计内 outbox 行为）",
            "随后人工跑一键巡检 check_observation_point（五项判据 a-e）",
        ],
        "commands": [
            _cmd("$PY tools/check_observation_point.py --data-root data",
                 "0=无 FAIL 且有 PASS（可放行/门 3 PASS）｜1=全 PENDING（中性）｜2=存在 FAIL（先查清）",
                 "汇总: PASS N / PENDING N / FAIL N → 结论: 无 FAIL 且有 PASS——可放行"),
            _cmd("grep -c \"\\[observe\\]\" data/shadow/monday_checkpoint.log",
                 "0（只读查看，非判据）",
                 "[observe] strategy_runs：20 行 / 带 run_id 20 行 与 "
                 "[observe] codex_registrations：1 行 / strategy_id: codex_sma20_rsi14"),
            _cmd("$PY tools/verify_strategy_ledger.py --data-root data"
                 "   # 仅当巡检退出码 2（存在 FAIL）才跑",
                 "0=全 PASS（含空账本中性）｜1=存在 FAIL｜2=参数缺失",
                 "[PASS] ①verdict_key 重算一致 → 结论: 全部 PASS"),
            _cmd("$PY tools/gate3_verdict.py   # cwd=C:/项目/老田的AI助手（在上级仓 tools/）",
                 "0=全过｜1=存在 FAIL（有效语义仅 0/1，无退出码 2）",
                 "复核结论: 11/11 全过（首跑前为 7 项口径）"),
            _cmd("$PY adapters/strategy_replay.py --format table   # 可选，只读",
                 "0=视图产出｜2=两侧账本均缺",
                 "表格视图；只落了 strategy_runs 单侧时 hit_rate 显示 n/a 属正常"),
        ],
        "expect_text": [
            "[observe] strategy_runs：20 行 / 带 run_id 20 行",
            "[observe] codex_registrations：1 行 / strategy_id: codex_sma20_rsi14",
            "结论: 无 FAIL 且有 PASS——可放行（退出码 0）",
        ],
        "troubleshoot": "SOP §五 #1（判决行无 run_id / observe 段缺一 / verdict_key 三方 diffs）→ "
                        "verify_strategy_ledger 定位，再与基线逐行 diff（回滚须报 lead）",
        "notes": [
            "门 3 PASS = 两个 [observe] 段都亮 + 巡检退出码 0。",
            "退出码 1 全 PENDING 是中性，不是通过：看 [wait] 行确认在推进，等待，勿重跑。",
            "PENDING ≠ 通过（周末 7/7 即 PENDING 中性口径）。",
        ],
    },
    {
        "at": "17:35",
        "minute": 17 * 60 + 35,
        "order": 4,
        "label": "AgentChiefPush 自动触发（飞书日报推送）",
        "mode": "自动（计划任务）",
        "forbidden": None,
        "look": [
            "data/shadow/chief_last_run.log 须含 SEND_OK"
            "（该日志是唯一记录 SEND_OK/SEND_FAIL 的面，shadow_automation.ps1:39-43）",
        ],
        "commands": [
            _cmd("tail -n 40 data/shadow/chief_last_run.log",
                 "0（只读查看，非判据）",
                 "含 SEND_OK（含 SEND_FAIL 或无标记 = 推送失败）"),
            _cmd("grep -c SEND_OK data/shadow/chief_last_run.log",
                 "0=命中 1 次（grep 无命中返回 1，属异常信号）",
                 "1"),
        ],
        "expect_text": ["SEND_OK"],
        "troubleshoot": "SOP §五 #5（凭证/网络/a0_chief 推送链）→ 读日志全文报 lead；"
                        "不影响门 3 判读（推送与观察点是两条独立成败线）",
        "notes": ["本段无需任何工具，只看日志标记。"],
    },
    {
        "at": "周五 17:45",
        "minute": None,
        "order": 5,
        "label": "AgentShadowEvalWeekly 自动 + gate3_verdict 复核（周评估日）",
        "mode": "自动（计划任务）+ 人工复核",
        "forbidden": None,
        "look": [
            "任务按周触发（DaysOfWeek=Friday，StartBoundary 17:45；时刻取自本机任务 XML 实读，"
            "手册/索引卡只写「周五」未给点）",
            "gate3_verdict 第二意见复核，与巡检交叉核对",
        ],
        "commands": [
            _cmd("$PY tools/gate3_verdict.py   # cwd=C:/项目/老田的AI助手",
                 "0=全过（或全 PENDING 中性）｜1=存在 FAIL",
                 "复核结论: 11/11 全过"),
            _cmd("$PY tools/gate3_verdict.py --date 2026-10-19   # 复核任意日",
                 "0=全过｜1=存在 FAIL",
                 "[PASS] 影子首跑已发生（最新交易日 > 2026-10-14）  — 最新=2026-10-19"),
        ],
        "expect_text": ["复核结论: 11/11 全过", "影子首跑已发生"],
        "troubleshoot": "SOP §五 #1（账本类 FAIL 先跑 verify_strategy_ledger 定位再 diff 基线）",
        "notes": ["周评估任务不在 16:50 check_trigger_chain 的三任务注册面内，需单独核对。"],
    },
]

#: 轴外提示：当「接下来三步」不足 3 条时按序补位（不冒充时间点）。
EXTRA_NEXT: List[str] = [
    "轴外：当日观察窗已结束——只读复盘（diff_baseline 对比基线，不做任何写动作）",
    "轴外：把当日巡检输出贴进观察记录，交 lead 归档；勿重跑任何计划任务",
]


# ---------------------------------------------------------------------------
# 时刻解析与定位
# ---------------------------------------------------------------------------
def parse_hhmm(text: str) -> int:
    """'HH:MM' → 当日分钟数；非法格式抛 ValueError（main 转退出码 2）。"""
    s = (text or "").strip()
    if len(s) != 5 or s[2] != ":" or not (s[:2].isdigit() and s[3:].isdigit()):
        raise ValueError("--at 需 HH:MM（两位小时，00:00-23:59），收到 %r" % text)
    h, m = int(s[:2]), int(s[3:])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("--at 时刻越界（00:00-23:59），收到 %r" % text)
    return h * 60 + m


def fmt_minute(minute: int) -> str:
    return "%02d:%02d" % (minute // 60, minute % 60)


def _monday_nodes() -> List[Dict[str, Any]]:
    return [e for e in TIMELINE if e["minute"] is not None]


def current_node(minute: int) -> Optional[Dict[str, Any]]:
    passed = [e for e in _monday_nodes() if e["minute"] <= minute]
    return passed[-1] if passed else None


def next_nodes(minute: int, limit: int = 3) -> List[Dict[str, Any]]:
    """当前时刻之后的节点（含周五周评估，追加在末尾）。"""
    upcoming = [e for e in _monday_nodes() if e["minute"] > minute]
    weekly = [e for e in TIMELINE if e["minute"] is None]
    return (upcoming + weekly)[:limit]


def build_payload(mode: str, at: Optional[str], now: datetime) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "read_only": READ_ONLY,
        "executes_commands": False,
        "repo": REPO,
        "cwd": AP,
        "python": PY,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "at": at,
        "timeline": TIMELINE,
        "current": None,
        "next": [],
        "next_steps_text": [],
    }
    if at is None:
        return payload
    minute = parse_hhmm(at)
    cur = current_node(minute)
    nxt = next_nodes(minute)
    payload["current"] = cur
    payload["next"] = nxt
    payload["current_range"] = _range_text(cur, minute)
    payload["anchor"] = {"now": fmt_minute(minute), "previous": cur["at"] if cur else None,
                         "next": nxt[0]["at"] if nxt else None}
    return payload


def _range_text(cur: Optional[Dict[str, Any]], minute: int) -> str:
    if cur is None:
        return "尚未进入观察窗（首个节点 16:50）"
    upcoming = [e for e in _monday_nodes() if e["minute"] > minute]
    end = fmt_minute(upcoming[0]["minute"]) if upcoming else "轴尾"
    return "处于 %s–%s 区间（当前节点 %s）" % (cur["at"], end, cur["at"])


# ---------------------------------------------------------------------------
# 人类可读输出
# ---------------------------------------------------------------------------
def _print_header(mode: str, at: Optional[str], now: datetime) -> None:
    print("=" * 72)
    print("周一观察点 dry-run 演练器 v%s（只读：只打印命令，绝不代跑）" % TOOL_VERSION)
    print("$PY  = %s" % PY)
    print("工作目录 = %s" % AP)
    print("本机时间 = %s   模式 = %s" % (now.strftime("%Y-%m-%d %H:%M:%S"),
                                      "完整时间轴(--list)" if mode == "list"
                                      else "模拟时刻(--at %s)" % at))
    print("=" * 72)


def _print_node(entry: Dict[str, Any], idx: Optional[int] = None,
                heading: str = "现在该做什么") -> None:
    num = ("%d. " % idx) if idx else ""
    print("")
    print("【%s】%s%s  %s" % (heading, num, entry["at"], entry["label"]))
    print("  模式：%s" % entry["mode"])
    if entry.get("forbidden"):
        print("  ⚠ 禁止：%s" % entry["forbidden"])
    print("  该看什么：")
    for item in entry["look"]:
        print("    - %s" % item)
    print("  该跑哪条命令（本工具只打印、不代跑；$PY 已展开为真实路径，可直接复制）：")
    for c in entry["commands"]:
        print("    $ %s" % c["cmd"].replace("$PY", PY))
        print("      期望退出码：%s" % c["rc"])
        print("      期望看到：  %s" % c["expect"])
    if entry["expect_text"]:
        print("  期望字样（逐字比对）：")
        for t in entry["expect_text"]:
            print("    · %s" % t)
    print("  出问题查哪里：%s" % entry["troubleshoot"])
    for n in entry.get("notes", []):
        print("  注：%s" % n)


def render_list(now: datetime) -> None:
    _print_header("list", None, now)
    print("五个时间锚点：16:50 → 17:10 → 17:25 → 17:35 → 周五（周评估）")
    print("退出码口径：0=正常输出（轴内外都是 0）；2=参数错误（--at 格式非法）")
    for entry in TIMELINE:
        _print_node(entry, entry["order"], heading="节点")
    print("")
    print("-" * 72)
    print("红线：①不手动跑 AgentShadowDaily ②PENDING ≠ 通过 "
          "③不修他人在途件 ④演示永不落真仓")
    print("-" * 72)


def render_at(at: str, now: datetime) -> None:
    minute = parse_hhmm(at)
    cur = current_node(minute)
    nxt = next_nodes(minute)
    _print_header("at", at, now)
    print("定位：%s" % _range_text(cur, minute))
    if cur is None:
        print("【现在该做什么】还没到 16:50——先确认本手册/工具链就位，"
              "16:50 起按节点逐项预演（本次 dry-run 不触发任何任务）。")
    else:
        _print_node(cur, None, heading="现在该做什么")
    if nxt:
        n0 = nxt[0]
        print("")
        print("下一个是 %s %s" % (n0["at"], _short_name(n0)))
    else:
        print("")
        print("下一个是 ——（当日观察窗已结束，下一节点为下周五 17:45）")
    print("")
    print("【接下来三步】")
    steps: List[str] = []
    for e in nxt:
        steps.append("%s %s —— %s" % (e["at"], _short_name(e), e["mode"]))
    for extra in EXTRA_NEXT:
        if len(steps) >= 3:
            break
        steps.append(extra)
    for i, s in enumerate(steps, 1):
        print("  %d) %s" % (i, s))
    print("")
    print("-" * 72)
    print("提示：本演练器不执行任何命令；上面命令请自行复制到终端。"
          "退出码 0（轴外时刻同样返回 0）。")


def _short_name(entry: Dict[str, Any]) -> str:
    return {
        "16:50": "触发链体检",
        "17:10": "AgentShadowDaily（只看日志，勿手动跑）",
        "17:25": "巡检（AgentMondayCheckpoint + check_observation_point 主判读）",
        "17:35": "AgentChiefPush（看 SEND_OK）",
        "周五 17:45": "AgentShadowEvalWeekly + gate3_verdict 复核",
    }.get(entry["at"], entry["label"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _at_type(text: str) -> str:
    try:
        parse_hhmm(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    return text


def main(argv: Optional[List[str]] = None) -> int:
    _assert_read_only("main")
    ap = argparse.ArgumentParser(
        prog="monday_drill",
        description="周一观察点 SOP 只读 dry-run 演练器：只打印时间轴与命令文本，"
                    "绝不执行任何命令/计划任务，绝不写盘。",
    )
    ap.add_argument("--at", metavar="HH:MM", type=_at_type,
                    help="模拟某时刻（00:00-23:59），打印现在该做什么与接下来三步；"
                         "缺省=当前时间")
    ap.add_argument("--list", action="store_true", dest="list_all",
                    help="打印完整时间轴（五个锚点全字段）")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="机读输出（含 timeline 列表）")
    args = ap.parse_args(argv)

    now = datetime.now()
    mode = "list" if args.list_all else "at"
    at = None if args.list_all else (args.at or now.strftime("%H:%M"))

    if args.as_json:
        payload = build_payload(mode, at, now)
        if mode == "at":
            minute = parse_hhmm(at)
            cur = current_node(minute)
            payload["next_steps_text"] = [
                "%s %s —— %s" % (e["at"], _short_name(e), e["mode"])
                for e in next_nodes(minute)
            ]
            payload["next_steps_text"] += EXTRA_NEXT[
                : max(0, 3 - len(payload["next_steps_text"]))]
            payload["now_doing"] = (
                "%s %s" % (cur["at"], _short_name(cur)) if cur
                else "尚未进入观察窗（首个节点 16:50）")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if mode == "list":
        render_list(now)
    else:
        render_at(at, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
