# -*- coding: utf-8 -*-
"""W3 切换窗口前置检查（GO / PARTIAL-GO / NO-GO，2026-10-24）。

窗口到来前 10 秒跑一次，决定"能不能动手"：
  · infra_go：验收 critical 全绿（历史债允许窗口）+ 回滚材料 READY
  · w2_go：零界·执行/通知 两个角色凭据就绪（决定能否同窗口完成分域）
  · verdict：GO（两者都齐）/ PARTIAL-GO（只能做基础设施窗口）/ NO-GO

用法：
    python feishu_w3_preflight.py
    python feishu_w3_preflight.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SECRETS = os.path.join(HERE, "work", "feishu_secrets")
W2_ROLES = ("zb.owner.exec", "zb.ops.notify")


def _secret_ready(role):
    p = os.path.join(SECRETS, role + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return bool(
            str(d.get("app_id") or "").strip()
            and str(d.get("app_secret") or "").strip()
        )
    except (OSError, ValueError):
        return False


def preflight():
    import feishu_acceptance as acc
    import feishu_rollback_drill as rb

    acc_rep = acc.run_checks(skip_tests=True, allow_debt=True)
    rb_rep = rb.summarize(rb.artifact_checks())
    missing = [r for r in W2_ROLES if not _secret_ready(r)]
    infra_go = acc_rep["verdict"] == "PASS" and rb_rep["verdict"] == "READY"
    w2_go = not missing
    if infra_go and w2_go:
        verdict = "GO"
    elif infra_go:
        verdict = "PARTIAL-GO"
    else:
        verdict = "NO-GO"
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "infra_go": infra_go,
        "w2_go": w2_go,
        "w2_missing_roles": missing,
        "acceptance": {
            "verdict": acc_rep["verdict"],
            "passed": acc_rep["passed"],
            "total": acc_rep["total"],
            "failed_checks": [c["name"] for c in acc_rep["checks"] if not c["ok"]],
        },
        "rollback": {
            "verdict": rb_rep["verdict"],
            "passed": rb_rep["passed"],
            "total": rb_rep["total"],
        },
    }


def render(rep):
    lines = [
        "# W3 切换窗口 · 前置检查",
        "",
        "时间：%s ｜ 结论：**%s**" % (rep["ts"], rep["verdict"]),
        "",
        "| 维度 | 状态 | 说明 |",
        "|---|---|---|",
        "| 基础设施窗口 | %s | 验收 %s（%d/%d）· 回滚材料 %s（%d/%d） |"
        % (
            "✅ GO" if rep["infra_go"] else "❌ NO-GO",
            rep["acceptance"]["verdict"],
            rep["acceptance"]["passed"],
            rep["acceptance"]["total"],
            rep["rollback"]["verdict"],
            rep["rollback"]["passed"],
            rep["rollback"]["total"],
        ),
        "| W2 分域 | %s | 缺凭据角色：%s |"
        % (
            "✅ GO" if rep["w2_go"] else "⏸ 未就绪",
            ", ".join(rep["w2_missing_roles"]) or "无",
        ),
    ]
    if rep["acceptance"]["failed_checks"]:
        lines.append("")
        lines.append("验收未过项：%s" % "；".join(rep["acceptance"]["failed_checks"]))
    lines += [
        "",
        "> GO = 全量窗口；PARTIAL-GO = 只做基础设施"
        "（重启/9104 退役/回调关闭），W2 分域等凭据；NO-GO = 先修红项。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = preflight()
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json else render(rep))
    return 0 if rep["verdict"] in ("GO", "PARTIAL-GO") else 1


if __name__ == "__main__":
    sys.exit(main())
