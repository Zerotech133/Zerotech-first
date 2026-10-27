# -*- coding: utf-8 -*-
"""邓芝台 · W4 验收脚本（2026-10-24）。

一眼判定"这一波到底到位没有"：把总纲 §6.2 的验收判据做成可重复执行的检查，
输出 JSON + Markdown 报告并给退出码（CI/验收窗口可直接跑）。

用法：
    python feishu_acceptance.py                 # 全量验收（含跑测试）
    python feishu_acceptance.py --json          # 机器可读
    python feishu_acceptance.py --skip-tests    # 跳过 pytest（快速巡检）
    python feishu_acceptance.py --network       # 追加收件人可达性（真实只读请求）
    python feishu_acceptance.py --allow-debt    # W3 生效窗口内允许"历史丢失"暂红

退出码：0 = 全绿；1 = 有 critical 未通过。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TEST_FILES = [
    "test_feishu_registry.py",
    "test_feishu_bot_base.py",
    "test_feishu_console.py",
    "test_feishu_target_audit.py",
    "test_feishu_sop_rehearsal.py",
    "test_feishu_app_pool.py",
    "test_jev_card_action.py",
]
NEWS_TEST_FILES = ["test_feishu_client_w3.py", "test_send_feishu_fallback.py"]
LEGACY_HEARTBEAT = os.path.join(HERE, "logs", "feishu_bot_heartbeat.txt")
CONSOLE_DEBT_CHECKS = ("24h 消息丢失",)


def _result(name, ok, detail="", critical=True):
    return {
        "name": name,
        "ok": bool(ok),
        "critical": bool(critical),
        "detail": str(detail)[:300],
    }


# ── 单项检查 ────────────────────────────────────────────────
def check_registry():
    import feishu_registry as fr

    try:
        roles, warnings = fr.load_registry()
        routes = fr.load_routes(roles_index=roles)
    except Exception as e:  # noqa: BLE001
        return _result("注册表/路由校验", False, "异常：%s" % e)
    detail = "角色 %d · 路由 %d" % (len(roles), len(routes))
    if warnings:
        detail += " · 告警 %d: %s" % (len(warnings), "; ".join(warnings)[:150])
    return _result("注册表/路由校验", not warnings, detail)


def check_tests(skip=False):
    if skip:
        return _result("自动化测试", True, "已跳过（--skip-tests）", critical=False)
    files = [os.path.join(HERE, f) for f in TEST_FILES]
    files += [os.path.join(ROOT, "news_collector", f) for f in NEWS_TEST_FILES]
    cmd = [sys.executable, "-m", "pytest"] + files + ["-q", "-p", "no:cacheprovider"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        out = (r.stdout or b"").decode("utf-8", "replace")
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        return _result("自动化测试", r.returncode == 0, tail)
    except Exception as e:  # noqa: BLE001
        return _result("自动化测试", False, "执行异常：%s" % e)


def check_secrets():
    import feishu_app_pool as pool

    rep = pool.status_report()
    rows = rep.get("roles") or []
    active = [r for r in rows if r.get("status") in ("active", "active_rename_pending")]
    bad = [
        r["role"] for r in active if not r.get("secret_ready") and not r.get("app_id")
    ]
    return _result(
        "在役机器人凭据", not bad, "在役 %d · 缺凭据 %s" % (len(active), bad or "无")
    )


def check_targets():
    import feishu_app_pool as pool

    cfg = os.path.join(ROOT, "news_collector", "news_config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            targets = json.load(f).get("push_targets") or []
    except (OSError, ValueError) as e:
        return _result("收件人域登记", False, "news_config 读取失败：%s" % e)
    canonical = pool.canonical_app_id()
    missing = [t.get("name") for t in targets if not t.get("app")]
    mismatch = [
        t.get("name")
        for t in targets
        if t.get("app") and canonical and t["app"] != canonical
    ]
    ok = bool(targets) and not missing and not mismatch
    detail = "目标 %d · canonical=%s · 缺app=%s · 域不一致=%s" % (
        len(targets),
        canonical or "-",
        missing or "无",
        mismatch or "无",
    )
    return _result("收件人域登记", ok, detail)


def check_console(allow_debt=False):
    import feishu_console as fc

    try:
        d = fc.collect()
    except Exception as e:  # noqa: BLE001
        return _result("管理台一致性", False, "采集异常：%s" % e)
    failed = [name for name, ok in d["checks"] if not ok]
    debt = [n for n in failed if any(k in n for k in CONSOLE_DEBT_CHECKS)]
    hard = [n for n in failed if n not in debt]
    detail = "检查 %d · 失败 %s" % (len(d["checks"]), hard or "无")
    if debt:
        detail += " · 历史债(允许窗口) %s" % debt
    return _result(
        "管理台一致性", not hard and (allow_debt or not debt), detail, critical=not hard
    )


def check_heartbeat():
    import feishu_console as fc

    d = fc.collect()
    if fc._age_sec(LEGACY_HEARTBEAT) is not None:
        age = fc._age_sec(LEGACY_HEARTBEAT)
        return _result("bot 心跳", age < 900, "legacy 心跳 %ds 前" % age)
    ok = [b for b in d["bots"] if b["heartbeat_ok"]]
    return _result("bot 心跳", bool(ok), "角色心跳正常 %d 个" % len(ok))


def check_ports(expect_9104_closed=False):
    def _listen(port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            return False

    ok8097 = _listen(8097)
    ok9104 = _listen(9104)
    ok = ok8097 and (not ok9104 if expect_9104_closed else True)
    detail = "8097=%s · 9104=%s%s" % (
        "up" if ok8097 else "down",
        "up" if ok9104 else "down",
        "（W3 后应 down）" if expect_9104_closed else "",
    )
    return _result("端口/服务", ok, detail)


def check_audit_network():
    import feishu_target_audit as fta
    import feishu_client

    try:
        s = fta.audit(
            feishu_client.get_tat(), canonical_app=feishu_client._canonical_app_id()
        )
        return _result(
            "收件人可达性（真实只读）",
            s["bad"] == 0,
            "total=%d ok=%d bad=%d" % (s["total"], s["ok"], s["bad"]),
        )
    except Exception as e:  # noqa: BLE001
        return _result("收件人可达性（真实只读）", False, "异常：%s" % e)


def check_sop_dry_run():
    """7 步 SOP dry-run（临时副本，不碰生产）。"""
    import contextlib
    import io

    try:
        import feishu_sop_runner as sop

        with contextlib.redirect_stdout(io.StringIO()):
            steps = sop.dry_run()
        failed = [s for s in steps if not s["ok"]]
        return _result(
            "7 步 SOP 演练（dry-run）",
            not failed,
            "%d/%d 步通过" % (len(steps) - len(failed), len(steps)),
        )
    except Exception as e:  # noqa: BLE001
        return _result("7 步 SOP 演练（dry-run）", False, "异常：%s" % e)


def check_push_unified(days=1):
    """P3 观察：旧模板调用审计（待迁移=0 才通过）。"""
    try:
        import push_legacy_audit as a

        res = a.audit(days=days)
        unlisted = res.get("unlisted") or []
        detail = "旧模板调用 %d 次 · 待迁移 %d · 白名单 %d" % (
            res.get("total", 0),
            len(unlisted),
            res.get("whitelist_size", 0),
        )
        if unlisted:
            detail += " · " + "、".join(sorted({g["caller"] for g in unlisted}))
        return _result("统一推送分装审计", not unlisted, detail)
    except Exception as e:  # noqa: BLE001
        return _result("统一推送分装审计", False, "异常：%s" % e)


def check_rollback_materials():
    """回滚材料演练（只核验材料，不动服务）。"""
    try:
        import feishu_rollback_drill as rb

        rep = rb.summarize(rb.artifact_checks())
        return _result(
            "回滚材料演练",
            rep["verdict"] == "READY",
            "%d/%d 材料就绪" % (rep["passed"], rep["total"]),
        )
    except Exception as e:  # noqa: BLE001
        return _result("回滚材料演练", False, "异常：%s" % e)


# ── 汇总/报告 ───────────────────────────────────────────────
def run_checks(
    skip_tests=False, network=False, allow_debt=False, expect_9104_closed=False
):
    checks = [
        check_registry(),
        check_tests(skip=skip_tests),
        check_secrets(),
        check_targets(),
        check_console(allow_debt=allow_debt),
        check_heartbeat(),
        check_ports(expect_9104_closed=expect_9104_closed),
        check_sop_dry_run(),
        check_push_unified(),
        check_rollback_materials(),
    ]
    if network:
        checks.append(check_audit_network())
    return summarize(checks)


def summarize(checks):
    failed = [c for c in checks if not c["ok"]]
    critical_failed = [c for c in failed if c["critical"]]
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "critical_failed": len(critical_failed),
        "verdict": "PASS" if not critical_failed else "FAIL",
        "checks": checks,
    }


def render_markdown(report):
    lines = [
        "# 邓芝台 · 飞书体系验收报告",
        "",
        "生成：%s" % report["ts"],
        "**结论：%s**（%d/%d 通过，critical 失败 %d）"
        % (
            report["verdict"],
            report["passed"],
            report["total"],
            report["critical_failed"],
        ),
        "",
        "| 结果 | 检查项 | 说明 |",
        "|---|---|---|",
    ]
    for c in report["checks"]:
        mark = "✅" if c["ok"] else ("❌" if c["critical"] else "⚠️")
        lines.append("| %s | %s | %s |" % (mark, c["name"], c["detail"]))
    lines += [
        "",
        "> 口径：W4 验收要求全绿；`--allow-debt` 仅用于 W3 生效窗口内"
        "的历史丢失过渡期。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--network", action="store_true")
    ap.add_argument("--allow-debt", action="store_true")
    ap.add_argument("--expect-9104-closed", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    rep = run_checks(
        skip_tests=args.skip_tests,
        network=args.network,
        allow_debt=args.allow_debt,
        expect_9104_closed=args.expect_9104_closed,
    )
    md = render_markdown(rep)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(md)
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
