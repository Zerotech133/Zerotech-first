# -*- coding: utf-8 -*-
"""订阅日跑编排入口（飞书中台 P3.9-lite · N-B 工位 · WO-FS-122 · 2026-10-20）。

问题：到期提醒（``portal/renewal_reminder.py``）与账本勾稽
（``tools/reconcile_subscription.py``）此前都只是「能力」，**没有任何日跑入口**。
接排程须老田五要素批准，故本文件先只交「入口 + 默认零副作用」：排程不接、
发送侧不接、库不建。

三种模式（默认最安全的那种）::

    --dry-run（默认）  只读账本与状态文件 → 算该提醒谁 → 与已发状态比对出
                       「本次会发」的清单 → 打印摘要。
                       不写账本、不写状态文件、不建库、不发送。
    --commit-state     ① 执行 expire_sweep（写账本，自然到期唯一出口）
                       ② **默认不落已发标记**——因为发送侧尚未接入，此时标记
                       等于把提醒永久吞掉（同日去重键已写，不会重发）。
                       副作用在 ``side_effects`` 里逐条明示。
    --mark-sent        **必须与 --commit-state 同用**：对本次会发的项逐条
                       mark_sent。发送侧接入后的联调开关，单独使用无效。
                       开它就等于承认「这些项已经投递过了」。
    --json             任何模式下都可用：结构化输出（items 内 open_id 已掩码）。

安全纪律（收编时会 grep 校验，删改即违规）：

  1. **不发送**：本文件没有任何消息发送调用——飞书发消息 / 发卡片 / HTTP
     webhook 提交 / 客户端封装一概不出现。发送侧属另一波，须真发授权。
  2. **不建库**：不调用任何建库接口。库不存在时如实报 ``ledger_missing`` 并
     退出 1，**绝不顺手建库**（建库是生产动作，须老田批准）。
     ``--commit-state`` 的 ``expire_sweep`` 内部走 ``_connect(create=True)``，
     所以调用前已先行确认库文件存在（见 ``run()`` 第 1 步），保证「库缺失 →
     一行都不写」。
  3. **状态文件 corrupt 直接停**：去重基准不可信时继续算会重复提醒，故读到
     corrupt 立即返回 1，不取数、不算提醒、不 mark，也不覆盖坏文件
     （``renewal_state.mark_sent`` 自身同样拒写）。
  4. **勾稽不通过必须非 0**：账本有孤儿 / 漏单时若返回 0，日跑会把「账本不
     自洽」当成功——这正是本项目反复强调的 fail-open 隐患。
  5. **不为未发生的投递打标记**（收编追加）：发送侧未接时 ``--commit-state``
     默认不 mark，必须显式 ``--mark-sent`` 才落盘。这条是 N-B 工位自陈风险
     「标记为已发但实际没发、提醒被永久吞掉」的处置，不是可选优化。

判定口径说明：

  - 顺序固定为「先 expire_sweep 再算提醒」（与 renewal_reminder docstring 的
    「每日任务在 expire_sweep 之后调用一次」一致），避免提醒到刚被清扫的行；
  - 提醒档位用 ``plan_reminders`` 默认 ``lead_days=(3, 1)``，stage 为日粒度；
  - 去重键的 ``day`` 取 ``renewal_state.today_str(now)``，与提醒判定同一时区；
  - 取数上限沿用 ``collect_subscribers`` 默认 200：**行数触顶即视为疑似截断**
    （``may_be_truncated``），转成 problem 并退出 1，不假装拿全了。

异常处理统一 ``closed on failure``；但本工具的核心要求是**不可静默**：任何
异常都必须落 ``problems`` 并影响退出码，绝不允许 except 后 pass 或返回成功。

退出码::

    0  执行完成且无阻断性问题（problems 为空、勾稽 ok、状态文件可读）
    1  执行完成但发现问题：勾稽 ok=false / problems 非空 / 状态文件 corrupt /
       订阅取数疑似被截断 / 库不存在
    2  工具自身失败：导入失败、参数错误、未归到具体步骤的异常

输出（``--json``）::

    {"ok": bool, "mode": "dry-run"|"commit-state", "tz": {...},
     "ledger": {"path", "exists"},
     "state": {"path", "corrupt", "sent_count"},
     "collected": {"count", "warnings", "problems"},
     "expired_swept": null | {"ok", "swept"},
     "reminders": {"total", "to_send", "already_sent", "skipped": {...}},
     "items": [{"open_id_masked", "plan_id", "days_left", "stage"}],
     "reconcile": {"ok", "checked", "issues_count"},
     "side_effects": ["已写账本：...", "已写状态文件：..."],
     "problems": [...], "warnings": [...]}

  - ``items`` 只放「本次会发」的项，且 open_id 一律用
    ``renewal_reminder.mask_open_id`` 掩码（该输出会被贴进日志与汇报）；
  - ``reconcile`` 只给计数与 ok，不搬运 issue 明细——明细里含明文 open_id，
    要看请另跑 ``python tools/reconcile_subscription.py --json``；
  - ``reconcile.skipped="ledger_missing"`` 表示库不存在时跳过勾稽（库都没有，
    勾稽只会回一堆 check_failed 噪声，不提供额外信息）；
  - ``side_effects`` 为空即「本次什么都没写」，dry-run 恒为空。

本波边界：**未接排程**（无计划任务 / 定时器 / 服务改动），**未真发**。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORTAL_DIR = _REPO_ROOT / "portal"
_TOOLS_DIR = Path(__file__).resolve().parent
for _dir in (str(_PORTAL_DIR), str(_TOOLS_DIR)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

try:
    import reconcile_subscription as _reconcile_mod
    import renewal_reminder as _reminder
    import renewal_state as _state_mod
    import subscription_store as _store
    _IMPORT_ERROR = None
except Exception as _exc:  # closed on failure：记下导入错误，main 落 problems 并退出 2
    _IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

__all__ = ["main", "run", "render_human", "build_parser", "JobError",
           "MODE_DRY_RUN", "MODE_COMMIT", "EXIT_OK", "EXIT_ISSUES", "EXIT_FATAL"]

MODE_DRY_RUN = "dry-run"
MODE_COMMIT = "commit-state"
MODES = (MODE_DRY_RUN, MODE_COMMIT)

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_FATAL = 2

DEFAULT_LIMIT = 200


class JobError(Exception):
    """工具自身失败（导入失败 / 参数错误 / 步骤无法归类）。对应退出码 2。"""

    def __init__(self, reason, detail=""):
        Exception.__init__(self, "%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# 输入归一
# ---------------------------------------------------------------------------

def _parse_now(text):
    """``--now`` 解析为 naive datetime；None/空串 → None（用当前时刻）。

    只接受 ISO 形式（``2026-11-16`` / ``2026-11-16 09:00:00`` /
    ``2026-11-16T09:00:00``）；解析失败返回 None 并由调用方报参数错误，
    **不静默退化成"现在"**（回放时退化成现在会让判定口径整批漂移）。
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _public_item(item):
    """输出用条目：open_id 掩码（统一走 renewal_reminder 的口径，不另写规则）。"""
    return {
        "open_id_masked": _reminder.mask_open_id(item.get("open_id")),
        "plan_id": item.get("plan_id"),
        "days_left": item.get("days_left"),
        "stage": item.get("stage"),
    }


def _empty_report(mode):
    """完整骨架：任何分支（含致命失败）都吐同一套键，日志侧好解析。"""
    return {
        "ok": False,
        "mode": mode,
        "tz": {},
        "ledger": {"path": None, "exists": False},
        "state": {"path": None, "corrupt": False, "sent_count": 0},
        "collected": {"count": 0, "warnings": [], "problems": []},
        "expired_swept": None,
        "reminders": {"total": 0, "to_send": 0, "already_sent": 0, "skipped": {}},
        "items": [],
        "reconcile": {"ok": False, "checked": 0, "issues_count": 0},
        "side_effects": [],
        "problems": [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(mode=MODE_DRY_RUN, now=None, mark_sent=False):
    """执行一次日跑编排，返回 ``(report, exit_code)``。

    ``mode``：``dry-run``（默认，零写入）/ ``commit-state``（写账本清扫，
    默认不落已发标记）。``mark_sent``：仅在 ``commit-state`` 下生效，为 True
    时才 ``mark_sent`` 落状态文件——发送侧未接入时打标记会把提醒永久吞掉，
    故必须显式开启（对应 CLI ``--mark-sent``）。``now``：naive datetime，
    None → 当前时刻（按 ``resolve_tz()`` 的时区口径）；用于回放与测试钉时间。

    任何步骤失败都落 ``problems`` 并体现在退出码上，closed on failure。
    """
    if _IMPORT_ERROR:
        raise JobError("import_failed", _IMPORT_ERROR)
    if mode not in MODES:
        raise JobError("bad_mode", repr(mode))

    report = _empty_report(mode)
    tz, tz_meta = _state_mod.resolve_tz()
    report["tz"] = tz_meta
    now_dt = _state_mod.local_now(now, tz)  # naive 墙上时间，与提醒判定同口径
    if tz_meta.get("degraded"):
        report["warnings"].append("tz_degraded:%s" % tz_meta.get("name"))

    # 1. 账本存在性：读路径不建库；缺失即停（不取数、不勾稽、绝不建库）
    try:
        ledger = Path(_store._db_path())
        exists = ledger.exists()
    except Exception as exc:  # closed on failure：归到具体步骤并退出 2
        raise JobError("ledger_path_failed", "%s: %s" % (type(exc).__name__, exc))
    report["ledger"] = {"path": str(ledger), "exists": bool(exists)}
    if not exists:
        report["problems"].append({
            "reason": "ledger_missing",
            "detail": "账本不存在，本工具不建库（建库须老田批准）：%s" % ledger,
        })
        report["reconcile"]["skipped"] = "ledger_missing"
        return report, EXIT_ISSUES

    # 2. 已发状态：corrupt 直接停（去重基准不可信，继续算会重复提醒）
    state = _state_mod.load()
    report["state"] = {
        "path": state.get("path"),
        "corrupt": bool(state.get("corrupt")),
        "sent_count": len(state.get("sent") or {}),
    }
    for item in state.get("problems") or []:
        report["warnings"].append("state:%s" % (item.get("reason") or "problem"))
    if report["state"]["corrupt"]:
        report["problems"].append({
            "reason": state.get("error") or "state_corrupt",
            "detail": "状态文件不可信，已停止（不取数 / 不 mark / 不覆盖）：%s"
                      % state.get("path"),
        })
        return report, EXIT_ISSUES

    # 3. commit-state：先清扫再算提醒（顺序见模块 docstring）
    if mode == MODE_COMMIT:
        try:
            swept = _store.expire_sweep(now=now_dt)
            swept = int(swept)
            report["expired_swept"] = {"ok": True, "swept": swept}
            report["side_effects"].append(
                "已写账本：expire_sweep 清扫 %d 行" % swept)
        except Exception as exc:  # closed on failure：落 problems；清扫失败即停
            report["expired_swept"] = {"ok": False, "swept": 0,
                                       "error": "%s: %s"
                                                % (type(exc).__name__, exc)}
            report["problems"].append({
                "reason": "expire_sweep_failed",
                "detail": "账本清扫失败，已停止（不拿未清扫的账本算提醒）：%s: %s"
                          % (type(exc).__name__, exc),
            })
            return report, EXIT_ISSUES

    # 4. 取数（可能截断必须可见）
    collected = _reminder.collect_subscribers(
        store=_store, limit=DEFAULT_LIMIT, now=now_dt)
    report["collected"] = {
        "count": int(collected.get("count") or 0),
        "warnings": list(collected.get("warnings") or []),
        "problems": list(collected.get("problems") or []),
    }
    report["warnings"].extend(report["collected"]["warnings"])
    report["problems"].extend(report["collected"]["problems"])
    for warn in report["collected"]["warnings"]:
        if str(warn).startswith("may_be_truncated"):
            # 触顶即疑似漏人：作为阻断项，不降级成普通提示
            report["problems"].append({
                "reason": "subscribers_may_be_truncated",
                "detail": "取数触顶（%s），订阅者可能超过上限而静默漏人" % warn,
            })

    # 5. 算该提醒谁
    plan = _reminder.plan_reminders(
        collected.get("subscribers") or [], now=now_dt)
    report["warnings"].extend(plan.get("warnings") or [])
    report["problems"].extend(plan.get("problems") or [])
    report["reminders"]["total"] = len(plan.get("items") or [])
    report["reminders"]["skipped"] = dict(plan.get("skipped") or {})

    # 6. 与已发状态比对出「本次会发」
    day = _state_mod.today_str(now=now_dt, tz=tz)
    todo, done = _state_mod.filter_new(plan.get("items") or [], state, day)
    report["reminders"]["to_send"] = len(todo)
    report["reminders"]["already_sent"] = len(done)
    report["items"] = [_public_item(it) for it in todo]

    # 7. commit-state：落已发标记——发送侧未接时必须显式 mark_sent 才写
    if mode == MODE_COMMIT and todo:
        if not mark_sent:
            report["side_effects"].append(
                "未写状态文件：本次 %d 条只算不标记（发送侧未接入，"
                "标记会让这些提醒永不再发；联调请显式加 --mark-sent）"
                % len(todo))
            report["warnings"].append("mark_sent_skipped_no_sender")
        else:
            marked = 0
            for item in todo:
                out = _state_mod.mark_sent(state, item, day,
                                           path=report["state"]["path"])
                if out.get("ok") and out.get("written"):
                    marked += 1
                    continue
                # closed on failure：写不进去就停（原子写单写者，再试也是同一结果）
                report["problems"].append({
                    "reason": "state_write_failed",
                    "detail": "%s（%s）" % (out.get("error") or "write_failed",
                                           _reminder.mask_open_id(item.get("open_id"))),
                })
                break
            if marked:
                report["side_effects"].append(
                    "已写状态文件：mark_sent 落 %d 条（发送侧未接，"
                    "这些项实际未投递且不会重发）" % marked)
                report["warnings"].append("marked_without_delivery:%d" % marked)

    # 8. 勾稽（只读；不通过必须非 0）
    try:
        rec = _reconcile_mod.reconcile(db_path=str(ledger), now=now_dt)
        issues = list(rec.get("issues") or [])
        report["reconcile"] = {
            "ok": bool(rec.get("ok")),
            "checked": int(rec.get("checked") or 0),
            "issues_count": len(issues),
        }
        if not rec.get("ok"):
            codes = sorted({str(i.get("code")) for i in issues})
            report["problems"].append({
                "reason": "reconcile_not_ok",
                "detail": "勾稽未通过：%d 条问题（%s）；明细另跑 "
                          "tools/reconcile_subscription.py --json"
                          % (len(issues), ", ".join(codes) or "unknown"),
            })
    except Exception as exc:  # closed on failure：落 problems，不当成"账本干净"
        report["reconcile"] = {"ok": False, "checked": 0, "issues_count": 0,
                               "error": "%s: %s" % (type(exc).__name__, exc)}
        report["problems"].append({
            "reason": "reconcile_exception",
            "detail": "勾稽执行异常，不能视为通过：%s: %s"
                      % (type(exc).__name__, exc),
        })

    code = EXIT_OK if not report["problems"] else EXIT_ISSUES
    report["ok"] = code == EXIT_OK
    return report, code


# ---------------------------------------------------------------------------
# 人读输出
# ---------------------------------------------------------------------------

def render_human(report):
    """人读摘要：模式 / 副作用 / 会发清单（掩码）/ 勾稽 / 问题与提示。"""
    report = report or {}
    lines = ["订阅日跑编排（本工具不发送任何消息）"]

    if report.get("mode") == MODE_COMMIT:
        lines.append("模式: commit-state（会写账本清扫；已发标记需 --mark-sent，"
                     "本工具始终不发送）")
    else:
        lines.append("模式: dry-run（不写任何东西，仍不发送）")

    tz = report.get("tz") or {}
    if tz:
        lines.append("时区: %s（source=%s, offset=%s, degraded=%s）"
                     % (tz.get("name"), tz.get("source"),
                        tz.get("offset"), tz.get("degraded")))

    ledger = report.get("ledger") or {}
    lines.append("账本: %s（%s）"
                 % (ledger.get("path") or "-",
                    "存在" if ledger.get("exists") else "缺失"))

    state = report.get("state") or {}
    lines.append("状态文件: %s（corrupt=%s, 已发记录 %d 条）"
                 % (state.get("path") or "-", state.get("corrupt"),
                    state.get("sent_count") or 0))

    collected = report.get("collected") or {}
    lines.append("取数: %d 行（警告 %d 条）"
                 % (collected.get("count") or 0,
                    len(collected.get("warnings") or [])))

    swept = report.get("expired_swept")
    if swept is None:
        lines.append("清扫: 未执行（dry-run 不写账本）")
    elif swept.get("ok"):
        lines.append("清扫: 已写账本，expire_sweep 清扫 %d 行"
                     % (swept.get("swept") or 0))
    else:
        lines.append("清扫: 失败（%s）" % swept.get("error"))

    reminders = report.get("reminders") or {}
    lines.append("提醒: 命中档位 %d 条 / 本次会发 %d 条 / 已发过 %d 条"
                 % (reminders.get("total") or 0,
                    reminders.get("to_send") or 0,
                    reminders.get("already_sent") or 0))
    for item in report.get("items") or []:
        lines.append("  - %s  档位=%s  stage=%s  剩余 %s 天"
                     % (item.get("open_id_masked") or "-",
                        item.get("plan_id"),
                        item.get("stage"),
                        item.get("days_left")))
    skipped = reminders.get("skipped") or {}
    hit = ["%s=%s" % (k, v) for k, v in sorted(skipped.items()) if v]
    if hit:
        lines.append("跳过: %s" % ", ".join(hit))

    rec = report.get("reconcile") or {}
    if rec.get("skipped"):
        lines.append("勾稽: 跳过（%s）" % rec.get("skipped"))
    else:
        lines.append("勾稽: ok=%s，检查 %d 行，问题 %d 条"
                     % (rec.get("ok"), rec.get("checked") or 0,
                        rec.get("issues_count") or 0))
        if rec.get("ok") is False:
            lines.append("      明细（含明文 open_id）请另跑："
                         "python tools/reconcile_subscription.py --json")

    effects = report.get("side_effects") or []
    if effects:
        lines.append("副作用:")
        for line in effects:
            lines.append("  - %s" % line)
    else:
        lines.append("副作用: 无")

    problems = report.get("problems") or []
    warnings = report.get("warnings") or []
    if problems:
        lines.append("问题 %d 条:" % len(problems))
        for item in problems:
            lines.append("  - %s: %s" % (item.get("reason"),
                                         item.get("detail") or ""))
    if warnings:
        lines.append("提示 %d 条:" % len(warnings))
        for line in warnings:
            lines.append("  - %s" % line)

    lines.append("")
    if report.get("ok"):
        lines.append("结论: 执行完成，无阻断性问题（退出码 0）")
    else:
        lines.append("结论: 发现问题或已停止，退出码非 0（见上）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    """命令行定义。usage 里写死「本工具不发送任何消息」，--help 必见。"""
    parser = argparse.ArgumentParser(
        prog="daily_subscription_job",
        usage=("%(prog)s [--dry-run | --commit-state [--mark-sent]]"
               " [--json] [--now TS]\n"
               "       本工具不发送任何消息：dry-run 不写盘；"
               "commit-state 只写账本清扫，加 --mark-sent 才落已发标记"),
        description="订阅日跑编排入口：算到期提醒 + 账本勾稽，默认 dry-run。",
        epilog="本工具不发送任何消息，也不建库、不接排程；"
               "库不存在时报 ledger_missing 并退出 1。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", dest="mode", action="store_const", const=MODE_DRY_RUN,
        help="默认。只读账本与状态文件，算出「本次会发」清单，不写任何东西")
    group.add_argument(
        "--commit-state", dest="mode", action="store_const", const=MODE_COMMIT,
        help="额外写：expire_sweep 清扫账本（仍不发送；已发标记见 --mark-sent）")
    parser.add_argument(
        "--mark-sent", dest="mark_sent", action="store_true",
        help="必须与 --commit-state 同用：对本次会发的项落已发标记。"
             "发送侧未接入时开它等于把这些提醒标记为已投递且不再重发，"
             "只在发送侧联调时使用")
    parser.add_argument(
        "--json", action="store_true",
        help="结构化输出（items 内 open_id 已掩码）")
    parser.add_argument(
        "--now", default=None, metavar="TS",
        help="固定判定时间，ISO 形式（2026-11-16 或 2026-11-16 09:00:00），"
             "默认当前时刻；主要用于回放")
    parser.set_defaults(mode=MODE_DRY_RUN)
    return parser


def main(argv=None):
    """CLI 入口：返回退出码（0/1/2），不抛业务异常。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    now = _parse_now(args.now)
    if args.now is not None and now is None:
        # 参数错误 → argparse 的 2 号退出（与未知参数一致）
        parser.error("--now 无法解析：%r（要 ISO 形式，如 2026-11-16）"
                     % (args.now,))

    try:
        report, code = run(mode=args.mode, now=now,
                           mark_sent=bool(getattr(args, "mark_sent", False)))
    except JobError as exc:
        report = _empty_report(args.mode)
        report["problems"].append({"reason": exc.reason, "detail": exc.detail})
        code = EXIT_FATAL
    except Exception as exc:  # closed on failure：落 problems，退出 2
        report = _empty_report(args.mode)
        report["problems"].append({
            "reason": "unexpected_error",
            "detail": "%s: %s" % (type(exc).__name__, exc),
        })
        code = EXIT_FATAL

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
