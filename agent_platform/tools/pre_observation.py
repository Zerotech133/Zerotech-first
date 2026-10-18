# -*- coding: utf-8 -*-
"""pre_observation：周一 17:25 巡检前的「收编 → 判读 → 汇总」一键入口（T 工位）。

## 为什么需要它

观察点判据 e（`check_observation_point.py` 的 `e_verdict_key_reconcile`）
直接复用 `verify_strategy_ledger.verify()`；而 verify 的对账项③「证据覆盖」
已真实化——证据侧键从证据仓 `evidence.db` 实体提取。真仓 strategy.runs
证据行为 0 时，周一账本首跑后若**没有先跑证据收编**，判据 e 会真实 FAIL。

`evidence_collect collect --domains strategy` 本身幂等、可重复跑、无副作用
（空根实测 collected:0 / verdict:PASS / EXIT=0），但它没有被任何链路自动调用
（`run_shadow.py`、`monday_checkpoint.py` 均未调用）。缺口不是「能不能跑」，
而是「有没有一个不会忘、不会跑错顺序的入口」——本工具就是那个入口。

口径来源：`docs/audit/2026-10-18_周一观察点预演基线.md` 第三节
「⚠️ 17:25 前置动作」——17:10 首跑落账后、17:25 检查点前，先执行
evidence_collect 的 strategy 域收编，再跑 check_observation_point。

## 三段（顺序固定，不可调换）

  ① 证据收编  evidence_collect.collect(root=<repo>, domains=["strategy"])
               幂等：同 fact_hash 重放零副作用，重复跑同结果。
  ② 观察点判读 check_observation_point --data-root <repo>/agent_platform/data
               五项判据 a-e，其中 e 依赖①已收编。
  ③ 账本独立复核 verify_strategy_ledger --data-root <...>
               可选段，仅 `--with-verify` 时执行（第二意见，不参与主判读门）。

## 为什么第①段走 in-process 而不是 subprocess（重要，勿"优化"成 CLI）

`adapters/evidence_collect.py` 的 `EVIDENCE_DB` 是**模块常量**，恒指
源码仓 `agent_platform/data/evidence/evidence.db`；CLI 的 `--root` 只决定
**扫描面**，不决定**写面**。若用 subprocess 并传 `--root <tmp>`，扫描的是
tmp，但写的仍是真仓证据仓——测试隔离与「误写」两道红线同时破。
`collect(root, domains, store=...)` 本来就有 store 接缝（own = store is None），
注入 `<data_root>/evidence/evidence.db` 后语义与生产**完全一致**（真仓
data_root 就是 agent_platform/data），且根可完全隔离。第②③段是只读工具，
走 subprocess 调真实 CLI，退出码原样取回。

## 退出码归一（本工具最容易写错的地方）

子工具退出码语义各不相同，**绝不能原样透传**：

    子工具                          rc=0        rc=1              rc=2
    check_observation_point     无FAIL有PASS   全PENDING（中性）  存在FAIL
    verify_strategy_ledger      全PASS/空中性  存在FAIL           参数缺失
    evidence_collect collect    PASS           —                 —

注意：同一个 `1` 在两处含义相反——观察点巡检的 1 是「全 PENDING 中性」，
对账工具的 1 是「存在 FAIL」。wrapper 必须显式区分「中性」与「真红」，
归一规则（`normalize_rc`，可单测）：

    step="observation"  {0: PASS, 1: PENDING, 2: FAIL}
    step="verify"       {0: PASS, 1: FAIL,    2: ERROR}
    step="collect"      {0: PASS}            其余/异常 → ERROR

本工具自身退出码（**不照抄子工具**）：

    0 = 无 FAIL（全 PASS，或含 PENDING 中性——PENDING 不是通过，输出会显式标注）
    1 = 存在 FAIL（真红，先查清再判门）
    2 = 参数或环境错误（root 不存在、子工具无法执行/异常退出）

## CLI

    --repo-root PATH   仓库根（含 agent_platform/）；默认=脚本所在仓库，自动推断，
                       不硬编码绝对路径
    --dry-run          只打印将要执行的命令与顺序，不执行、不写任何文件
    --json             机读输出（steps 恒为三段，未启用的段 status=SKIPPED）
    --with-verify      追加第③段账本独立复核

数据根固定派生为 `<repo-root>/agent_platform/data`（与 `--data-root` 语义一致，
对齐 run_shadow DATA_ROOT），不额外暴露参数以免现场传错层。

## 红线

- 不调用任何计划任务、不改任何既有脚本；
- 写面仅限 `<data-root>/evidence/`（证据仓本身，属本工具正常用途）；
- 第②③段为只读工具，原样调用，不加任何写动作；
- `--dry-run` 连 import 都不做（避免 __pycache__ 之类副作用），只打印计划。

用法（周一 17:25 巡检后，一条命令）：

    python tools/pre_observation.py --repo-root C:/项目/老田的AI助手

首次或不放心时先看顺序：

    python tools/pre_observation.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

TOOL_NAME = "pre_observation"
TOOL_VERSION = "1.0.0"

_TOOLS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _TOOLS_DIR.parent          # .../agent_platform
_REPO_ROOT_DEFAULT = _AGENT_PLATFORM.parent  # 含 agent_platform/ 的仓库根

if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

# 归一状态
PASS = "PASS"
PASS_WITH_PENDING = "PASS_WITH_PENDING"
PENDING = "PENDING"
FAIL = "FAIL"
ERROR = "ERROR"
SKIPPED = "SKIPPED"
PLANNED = "PLANNED"

# 段标识
STEP_COLLECT = "collect"
STEP_OBSERVE = "observation"
STEP_VERIFY = "verify"

#: ①只收 strategy 域（判据 e 的唯一依赖）
COLLECT_DOMAINS: Tuple[str, ...] = ("strategy",)

#: 子工具退出码 → 归一状态。缺失组合一律 ERROR。
#: 关键：observation 的 1 是「全 PENDING 中性」，verify 的 1 是「存在 FAIL」，
#: 两者不可共用一张表——这是 wrapper 层唯一的正确性要点。
RC_TABLE: Dict[str, Dict[int, str]] = {
    STEP_COLLECT: {0: PASS},
    STEP_OBSERVE: {0: PASS, 1: PENDING, 2: FAIL},
    STEP_VERIFY: {0: PASS, 1: FAIL, 2: ERROR},
}

STATUS_LABEL = {
    PASS: "PASS（通过）",
    PASS_WITH_PENDING: "PASS_WITH_PENDING（无 FAIL，含中性）",
    PENDING: "PENDING（中性，非失败）",
    FAIL: "FAIL（存在硬失败）",
    ERROR: "ERROR（参数/环境）",
    SKIPPED: "SKIPPED（未启用）",
    PLANNED: "PLANNED（dry-run 未执行）",
}


# ----------------------------------------------------------------------
# 归一逻辑（纯函数，可单测——不要塞进 main 里）
# ----------------------------------------------------------------------
def normalize_rc(step_key: str, rc: Optional[int]) -> str:
    """子工具退出码 → 归一状态；**中性(PENDING) 与 真红(FAIL) 显式区分**。

    observation: 1 → PENDING（全 PENDING 中性，绝不判 FAIL）
    verify:      1 → FAIL（对账存在差异，真红）
    None/负数/未登记组合 → ERROR（进程被杀、参数错、环境坏）。
    """
    if rc is None or not isinstance(rc, int) or rc < 0:
        return ERROR
    return RC_TABLE.get(step_key, {}).get(rc, ERROR)


def aggregate(statuses: Sequence[str]) -> Tuple[str, int]:
    """三段状态 → (总状态, wrapper 退出码)。

    ERROR(2) 优先于 FAIL(1) 优先于 PENDING(0)。SKIPPED/PLANNED 不参与判读。
    PENDING 归 0：中性不是失败，但输出必须标注「未全过」，不得冒充通过。
    """
    active = [s for s in statuses if s not in (SKIPPED, PLANNED)]
    if ERROR in active:
        return ERROR, 2
    if FAIL in active:
        return FAIL, 1
    if PENDING in active:
        return PASS_WITH_PENDING, 0
    return PASS, 0


# ----------------------------------------------------------------------
# 计划
# ----------------------------------------------------------------------
def _quote(path: Path) -> str:
    return '"%s"' % str(path).replace("\\", "/")


def build_plan(repo_root: Path, data_root: Path,
               with_verify: bool) -> List[Dict[str, Any]]:
    """三段固定顺序的计划（dry-run 与实跑共用同一份，防止两条路径漂移）。"""
    py = sys.executable or "python"
    obs_argv = [py, str(_TOOLS_DIR / "check_observation_point.py"),
                "--data-root", str(data_root)]
    ver_argv = [py, str(_TOOLS_DIR / "verify_strategy_ledger.py"),
                "--data-root", str(data_root)]
    db = data_root / "evidence" / "evidence.db"

    return [
        {
            "key": STEP_COLLECT,
            "order": "1/3",
            "title": "① 证据收编（strategy 域，幂等）",
            "cmd": ('evidence_collect collect --root %s --domains strategy'
                    % _quote(repo_root)),
            "note": ("in-process 调 collect()，store 注入 %s"
                     "（--root 只定扫描面，写面由 store 决定）" % _quote(db)),
            "argv": None,
            "target_db": str(db),
            "enabled": True,
        },
        {
            "key": STEP_OBSERVE,
            "order": "2/3",
            "title": "② 观察点判读（判据 a-e）",
            "cmd": ('%s %s --data-root %s'
                    % (_quote(Path(py)),
                       _quote(_TOOLS_DIR / "check_observation_point.py"),
                       _quote(data_root))),
            "note": "只读；rc 0=有PASS无FAIL / 1=全PENDING中性 / 2=有FAIL",
            "argv": obs_argv,
            "target_db": None,
            "enabled": True,
        },
        {
            "key": STEP_VERIFY,
            "order": "3/3",
            "title": "③ 账本独立复核（三方对账）",
            "cmd": ('%s %s --data-root %s'
                    % (_quote(Path(py)),
                       _quote(_TOOLS_DIR / "verify_strategy_ledger.py"),
                       _quote(data_root))),
            "note": "可选段（--with-verify）；rc 0=无差异 / 1=有FAIL / 2=参数缺失",
            "argv": ver_argv,
            "target_db": None,
            "enabled": with_verify,
        },
    ]


# ----------------------------------------------------------------------
# 执行
# ----------------------------------------------------------------------
def _child_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_collect(repo_root: Path, data_root: Path) -> Dict[str, Any]:
    """① in-process 执行 strategy 域收编（store 显式注入数据根下的证据仓）。"""
    from adapters.evidence_collect import collect  # 延迟 import：dry-run 不加载
    from engine.evidence import EvidenceStore

    db = data_root / "evidence" / "evidence.db"
    store = EvidenceStore(db)
    try:
        rep = collect(repo_root, domains=list(COLLECT_DOMAINS), store=store)
    finally:
        store.close()

    dom = rep.get("domains", {}).get(STEP_COLLECT, {})
    return {
        "rc": 0,
        "detail": ("collected=%d new=%d replayed=%d db=%s"
                   % (dom.get("collected", 0), dom.get("new", 0),
                      dom.get("replayed", 0), db)),
        "warnings": list(rep.get("findings", [])),
        "output": json.dumps(rep, ensure_ascii=False, indent=1),
    }


def _extract_summary(text: str) -> str:
    """从子工具输出里取「汇总/结论」两行作为结论依据（无则空串）。"""
    picked = [ln.strip() for ln in text.splitlines()
              if ln.strip().startswith(("汇总:", "结论:", "复核结论:"))]
    return "；".join(picked)


def _generic_conclusion(step_key: str, status: str) -> str:
    if step_key == STEP_OBSERVE:
        return {
            PASS: "无 FAIL 且有 PASS——可放行",
            PENDING: "全 PENDING 中性：首跑/巡检未到，非失败，不判红",
            FAIL: "存在 FAIL——先查清再判门",
        }.get(status, "子工具异常/不可执行")
    if step_key == STEP_VERIFY:
        return {
            PASS: "三方对账无差异（空账本 rc=0 亦归此，属中性）",
            FAIL: "三方对账存在差异（diffs 见子工具明细）",
        }.get(status, "对账工具异常/参数缺失")
    return {
        PASS: "strategy 域收编完成，幂等可重复跑",
    }.get(status, "收编异常")


def execute_step(step: Dict[str, Any], repo_root: Path,
                 data_root: Path) -> Dict[str, Any]:
    """执行单段；异常不外抛（转 ERROR），保证三段都能给出结论。"""
    out: Dict[str, Any] = {
        "key": step["key"],
        "order": step["order"],
        "title": step["title"],
        "cmd": step["cmd"],
        "note": step["note"],
        "target_db": step.get("target_db"),
        "enabled": step["enabled"],
        "rc": None,
        "status": SKIPPED,
        "conclusion": "未启用（加 --with-verify 才跑）" if not step["enabled"] else "",
        "detail": "",
        "warnings": [],
        "output": "",
    }
    if not step["enabled"]:
        return out

    try:
        if step["key"] == STEP_COLLECT:
            res = _run_collect(repo_root, data_root)
            rc = res["rc"]
            out["detail"] = res["detail"]
            out["warnings"] = res["warnings"]
            out["output"] = res["output"]
        else:
            cp = subprocess.run(
                step["argv"], cwd=str(_AGENT_PLATFORM), env=_child_env(),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600,
            )
            rc = cp.returncode
            out["output"] = (cp.stdout or "") + (cp.stderr or "")
            out["detail"] = _extract_summary(out["output"])
    except Exception as exc:  # noqa: BLE001  fail-visible：环境异常必须可见
        out["status"] = ERROR
        out["rc"] = None
        out["conclusion"] = "ERROR —— 执行异常：%s" % str(exc)[:160]
        return out

    status = normalize_rc(step["key"], rc)
    out["rc"] = rc
    out["status"] = status
    base = "%s —— %s" % (STATUS_LABEL[status],
                         _generic_conclusion(step["key"], status))
    if status == ERROR:
        base = "ERROR —— 子工具退出码 %r 未在归一表内（参数/环境错误）" % (rc,)
    out["conclusion"] = base
    return out


def run(plan: List[Dict[str, Any]], repo_root: Path, data_root: Path,
        dry_run: bool) -> Dict[str, Any]:
    """按计划跑（或只打印）。返回可 JSON 序列化的报告。"""
    if dry_run:
        steps = [{
            "key": s["key"], "order": s["order"], "title": s["title"],
            "cmd": s["cmd"], "note": s["note"], "target_db": s.get("target_db"),
            "enabled": s["enabled"],
            "rc": None, "status": PLANNED,
            "conclusion": ("dry-run：仅打印，未执行" if s["enabled"]
                           else "未启用（--with-verify 才跑）；dry-run 未执行"),
            "detail": "", "warnings": [], "output": "",
        } for s in plan]
        return {
            "tool": TOOL_NAME, "version": TOOL_VERSION,
            "dry_run": True, "repo_root": str(repo_root),
            "data_root": str(data_root), "steps": steps,
            "status": PLANNED, "exit_code": 0,
            "summary": {"pass": 0, "pending": 0, "fail": 0, "error": 0,
                        "skipped": 0},
        }

    steps = [execute_step(s, repo_root, data_root) for s in plan]
    status, code = aggregate([s["status"] for s in steps])
    return {
        "tool": TOOL_NAME, "version": TOOL_VERSION,
        "dry_run": False, "repo_root": str(repo_root),
        "data_root": str(data_root), "steps": steps,
        "status": status, "exit_code": code,
        "summary": {
            "pass": sum(1 for s in steps if s["status"] == PASS),
            "pending": sum(1 for s in steps if s["status"] == PENDING),
            "fail": sum(1 for s in steps if s["status"] == FAIL),
            "error": sum(1 for s in steps if s["status"] == ERROR),
            "skipped": sum(1 for s in steps if s["status"] == SKIPPED),
        },
    }


# ----------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------
def _print_output(text: str) -> None:
    if not text.strip():
        return
    print("      子工具输出：")
    for line in text.rstrip().splitlines():
        print("        | %s" % line)


def render_text(report: Dict[str, Any]) -> None:
    dry = report["dry_run"]
    print("=" * 70)
    print("T 工位 · 周一 17:25 前置一键（收编 → 判读 → 汇总）v%s"
          % report["version"])
    print("仓库根: %s" % report["repo_root"])
    print("数据根: %s" % report["data_root"])
    print("模式  : %s" % ("dry-run（只打印，不执行、不写盘）" if dry
                          else "实跑（写面仅 data/evidence/）"))
    print("=" * 70)

    for s in report["steps"]:
        print("[%s] %s" % (s["order"], s["title"]))
        print("      命令: %s" % s["cmd"])
        print("      说明: %s" % s["note"])
        if dry:
            print("      退出码: -")
            print("      结论  : %s" % s["conclusion"])
            print("")
            continue
        print("      退出码: %s" % ("-" if s["rc"] is None else s["rc"]))
        print("      结论  : %s" % s["conclusion"])
        if s["detail"]:
            print("      依据  : %s" % s["detail"])
        for w in s["warnings"]:
            print("      [warning] %s" % w)
        _print_output(s["output"])
        print("")

    print("-" * 70)
    if dry:
        print("汇总: dry-run 结束——未执行任何命令、未写任何文件（退出码 0）")
        print("下一步: 去掉 --dry-run 即按上述顺序实跑。")
        return

    sm = report["summary"]
    print("汇总: PASS %d / PENDING %d / FAIL %d / ERROR %d / SKIPPED %d"
          % (sm["pass"], sm["pending"], sm["fail"], sm["error"], sm["skipped"]))
    st = report["status"]
    if st == PASS:
        print("结论: 全部 PASS——可放行（wrapper 退出码 0）")
    elif st == PASS_WITH_PENDING:
        print("结论: 无 FAIL，但含 PENDING 中性——未判红、也未全过；"
              "按 SOP 等首跑/巡检推进，勿把中性当通过（wrapper 退出码 0）")
    elif st == FAIL:
        print("结论: 存在 FAIL——先查清再判门（wrapper 退出码 1）")
    else:
        print("结论: 参数或环境错误——修好再跑（wrapper 退出码 2）")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pre_observation",
        description="周一 17:25 前置一键：证据收编 → 观察点判读 →（可选）账本复核。"
                    "退出码 0=无FAIL / 1=有FAIL / 2=参数或环境错误。",
    )
    ap.add_argument("--repo-root", default=str(_REPO_ROOT_DEFAULT),
                    help="仓库根（含 agent_platform/）；默认按脚本位置自动推断")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="只打印将要执行的命令与顺序，不执行、不写任何文件")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="机读输出（steps 恒为三段）")
    ap.add_argument("--with-verify", action="store_true", dest="with_verify",
                    help="追加第③段 verify_strategy_ledger 三方对账")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.is_dir():
        print("参数/环境错误：--repo-root 不存在或不是目录：%s" % repo_root,
              file=sys.stderr)
        return 2
    data_root = repo_root / "agent_platform" / "data"
    if not data_root.is_dir():
        print("参数/环境错误：数据根不存在（--repo-root 是否指错层？）：%s"
              % data_root, file=sys.stderr)
        return 2

    plan = build_plan(repo_root, data_root, args.with_verify)
    report = run(plan, repo_root, data_root, dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        render_text(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
