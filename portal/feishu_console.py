# -*- coding: utf-8 -*-
"""邓芝台 · 飞书中台管理台（W1 离线版，2026-10-24）。

只读汇总"机器人 + 通道 + 收件人 + 证据"，生成自包含 HTML（后续 W3 挂 /feishu-console）。
不直连任何服务、不改运行态；数据全部来自本机文件/账本。

用法：
    python feishu_console.py                 # 生成 outputs/feishu_console_<ts>.html
    python feishu_console.py --json          # 机器可读摘要（stdout）
    python feishu_console.py --out <path>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
LOGS = os.path.join(HERE, "logs")
# 2026-10-26 OS-FIX-W4CLOSE：测试归因清单（JSONL）。只登记明确为"测试/未挂调度"
# 的发送（精确 ts+kind+target），从 lost24 剔除但单独显示；不删流水、不改 ok。
ANNOTATIONS = os.path.join(WORK, "push_flow_annotations.jsonl")
SECRETS = os.path.join(WORK, "feishu_secrets")
DATA = os.path.join(HERE, "data", "feishu")
NEWS_CONFIG = os.path.join(ROOT, "news_collector", "news_config.json")
CANONICAL = os.path.join(SECRETS, "canonical.json")
OUT_DIR = os.environ.get("FEISHU_CONSOLE_OUT") or os.path.join(
    os.path.dirname(ROOT), "work", "feishu_console")

import feishu_registry as fr  # noqa: E402


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _tail_jsonl(path, limit=2000):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return rows
    return rows[-limit:]


def _age_sec(path):
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except OSError:
        return None


def _load_annotations(path=None):
    """读取测试归因清单 JSONL → list[dict]；坏行/缺失静默跳过（fail-safe）。"""
    rows = []
    try:
        with open(path or ANNOTATIONS, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    rows.append(d)
    except OSError:
        pass
    return rows


def _is_annotated(row, anns):
    """只有 ts+kind+target 三者精确匹配才算已归因（防宽泛豁免）。"""
    for a in anns:
        if (a.get("ts") == row.get("ts")
                and a.get("kind") == row.get("kind")
                and a.get("target") == row.get("target")):
            return True
    return False


# 2026-10-26 OS-FIX-W4CLOSE2：W41 死信自测使用一次性假收件人（ou_deadpayload_*），
# 这类发送不可能是生产投递，不能计入 W4「24h 消息丢失」。用显式前缀白名单
# 而不是按 kind 猜，避免把真实收件人失败也放过；计数单独显示「自测目标 N」。
TEST_TARGET_PREFIXES = ("ou_deadpayload",)


def _is_self_test_target(row):
    t = str(row.get("target") or "")
    return any(t.startswith(p) for p in TEST_TARGET_PREFIXES)


def collect():
    roles_index, warnings = fr.load_registry()
    routes = fr.load_routes(roles_index=roles_index)
    raw = _read_json(os.path.join(DATA, "registry.json"), {}) or {}
    canonical_app = str((_read_json(CANONICAL, {}) or {}).get("app_id")
                        or os.environ.get("FEISHU_APP_ID") or "")
    legacy_hb = os.path.join(LOGS, "feishu_bot_heartbeat.txt")

    # 1) 机器人台账 + 健康（旧 bot 无角色心跳文件时回退 legacy 心跳）
    bots = []
    for rid, meta in roles_index.items():
        hb = os.path.join(LOGS, "feishu_heartbeat_%s.txt" % rid)
        hb_age = _age_sec(hb)
        hb_source = "role"
        secret_file = os.path.join(SECRETS, rid + ".json")
        sec = _read_json(secret_file)
        role_app = (sec or {}).get("app_id") or meta.get("app_id_current") or ""
        if hb_age is None and role_app and role_app == canonical_app:
            legacy_age = _age_sec(legacy_hb)
            if legacy_age is not None:
                hb_age, hb_source = legacy_age, "legacy"
        bots.append({
            "role": rid,
            "display": meta["display"],
            "domain": "/".join(meta["domain"]),
            "status": meta["status"],
            "app_ref": meta.get("app_ref"),
            "app_id": (sec or {}).get("app_id") or meta.get("app_id_current") or "-",
            "secret_ready": bool(sec and sec.get("app_id") and sec.get("app_secret")),
            "events": meta.get("events") or [],
            "group_mode": (meta.get("group_policy") or {}).get("mode"),
            "heartbeat_age_sec": hb_age,
            "heartbeat_ok": hb_age is not None and hb_age < 900,
            "heartbeat_source": hb_source,
        })

    # 1b) 收件人域登记（W3-D）：每条 push_targets 应有 app 字段，且与当前 canonical app 一致
    targets = [{"name": t.get("name"), "type": t.get("type"),
                "id": t.get("id"), "app": str(t.get("app") or "")}
               for t in ((_read_json(NEWS_CONFIG, {}) or {}).get("push_targets") or [])]


    # 2) 通道账本（区分：静默拦截 / hub 降级失败 / lark-cli 兜底 / 真实丢失）
    push = _tail_jsonl(os.path.join(WORK, "push_flow.jsonl"))
    cutoff = time.time() - 24 * 3600
    push24 = [r for r in push if _ts(r.get("ts", "")) >= cutoff]
    dead = _tail_jsonl(os.path.join(WORK, "dead_letters.jsonl"))
    dead24 = [r for r in dead if _ts(r.get("ts", "")) >= cutoff]
    inbound = _tail_jsonl(os.path.join(WORK, "inbound_tickets.jsonl"))

    kinds = {}
    ok_count = 0
    denied24 = []
    for r in push24:
        k = r.get("kind") or "?"
        kinds[k] = kinds.get(k, 0) + 1
        if "deny:silent window" in k:
            denied24.append(r)
        elif r.get("ok"):
            ok_count += 1

    hub_fail = [r for r in push24
                if not r.get("ok") and r not in denied24
                and r.get("channel") == "openapi"]
    larkcli_fail = [r for r in push24
                    if not r.get("ok") and r.get("channel") == "lark-cli"]

    def _recovered(row):
        # 同目标 15 分钟内出现任一成功发送行 → 视为已由兜底通道送达
        t = _ts(row.get("ts", ""))
        for o in push24:
            if not o.get("ok") or o.get("target") != row.get("target"):
                continue
            if abs(_ts(o.get("ts", "")) - t) <= 900:
                return True
        return False

    hub_fail_recovered = [r for r in hub_fail if _recovered(r)]
    # 2026-10-26 OS-FIX-W4LOSS：lark-cli 失败也必须先判「同目标 15 分钟内已有成功」
    # 再计丢失——旧实现把「失败后 38s 重试成功」也当真实丢失（W4 07:41 实例：
    # auto:news_collector 00:10:46 fail → 00:11:24 ok 仍被计入 lost=3）。
    larkcli_fail_recovered = [r for r in larkcli_fail if _recovered(r)]
    lost24 = ([r for r in hub_fail if not _recovered(r)]
              + [r for r in larkcli_fail if not _recovered(r)])
    # 2026-10-26 OS-FIX-W4CLOSE：测试归因清单——只剔除清单里精确登记的
    # (ts, kind, target) 行（不删流水、不改 ok 字段）；单独计数并在检查项
    # 文本里显示「已归因测试 N」，W4 报告可见可追溯。
    # OS-FIX-W4CLOSE2：W41 死信自测的一次性假收件人同样不是生产投递，单列
    # 「自测目标 M」，不计入真实丢失（仍可见、可追溯）。
    lost_test_target = [r for r in lost24 if _is_self_test_target(r)]
    lost24 = [r for r in lost24 if not _is_self_test_target(r)]
    anns = _load_annotations()
    lost_annotated = [r for r in lost24 if _is_annotated(r, anns)]
    lost_unannotated = [r for r in lost24 if not _is_annotated(r, anns)]

    decisions = {"total": None, "pending": None, "error": ""}
    db = os.path.join(WORK, "notification_decisions.sqlite3")
    if os.path.exists(db):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            cur = con.cursor()
            decisions["total"] = cur.execute("select count(*) from decisions").fetchone()[0]
            try:
                decisions["pending"] = cur.execute(
                    "select count(*) from decisions where status in ('pending','preview')"
                ).fetchone()[0]
            except sqlite3.Error:
                decisions["pending"] = None
            con.close()
        except sqlite3.Error as e:
            decisions["error"] = str(e)[:120]
    else:
        decisions["error"] = "决策账未实例化"

    loop_pid = None
    pid_file = os.path.join(LOGS, "bot_watchdog.pid")
    if os.path.exists(pid_file):
        try:
            loop_pid = open(pid_file, encoding="ascii", errors="ignore").read().strip()
        except OSError:
            loop_pid = None

    task_log = os.path.join(LOGS, "feishu_bot_task.log")
    task_tail = ""
    try:
        with open(task_log, encoding="utf-8", errors="replace") as f:
            task_tail = "".join(f.readlines()[-6:]).strip()
    except OSError:
        pass

    # 3) 一致性检查
    checks = []
    policy = raw.get("channel_policy") or {}
    checks.append(("通道铁律（入 W43 / 出 8097 / 禁直连）",
                   policy.get("inbound") == "W43 only"
                   and policy.get("outbound") == "8097 only"
                   and policy.get("direct_openapi") == "forbidden"))
    groups = raw.get("group_whitelist") or []
    checks.append(("群白名单 ≥2 且有 chat_id",
                   len(groups) >= 2 and all(g.get("chat_id") for g in groups)))
    active = [b for b in bots if b["status"] in ("active", "active_rename_pending")]
    # 在役机器人：新 bot 需 secrets 就绪；旧 bot 走既有凭据链（app_id_current 非空即视为在役）
    checks.append(("在役机器人凭据就绪",
                   all(b["secret_ready"] or b["app_id"] not in ("", "-")
                       for b in active) if active else True))
    checks.append(("路由表覆盖注册角色", len(routes) >= len(roles_index)))
    checks.append(("收件人域登记（app 字段齐全）",
                   bool(targets) and all(t["app"] for t in targets)))
    if canonical_app:
        checks.append(("收件人 app 与当前凭据一致（%s）" % canonical_app[:12],
                       all(t["app"] == canonical_app for t in targets if t["app"])))
    else:
        warnings.append("未配置 canonical app（portal/work/feishu_secrets/"
                        "canonical.json），无法校验收件人域一致性")
    checks.append(("24h 消息丢失 = 0（当前 %d%s%s）"
                   % (len(lost_unannotated),
                      "；已归因测试 %d" % len(lost_annotated)
                      if lost_annotated else "",
                      "；自测目标 %d" % len(lost_test_target)
                      if lost_test_target else ""),
                   len(lost_unannotated) == 0))
    checks.append(("静默窗口策略生效（拦截 %d 次属正常）" % len(denied24), True))

    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "registry_warnings": warnings,
        "bots": bots,
        "routes": routes,
        "group_whitelist": groups,
        "targets": targets,
        "canonical_app": canonical_app,
        "gateway": {
            "push_24h": len(push24),
            "push_ok_24h": ok_count,
            "push_kinds": kinds,
            "denied_24h": len(denied24),
            "hub_fail_24h": len(hub_fail),
            "hub_fail_recovered_24h": len(hub_fail_recovered),
            "larkcli_fail_24h": len(larkcli_fail),
            "larkcli_fail_recovered_24h": len(larkcli_fail_recovered),
            "lost_24h": len(lost_unannotated),
            "lost_annotated_24h": len(lost_annotated),
            "lost_test_target_24h": len(lost_test_target),
            "dead_letters_24h": len(dead24),
            "inbound_tickets": len(inbound),
            "decisions": decisions,
            "task_log_tail": task_tail,
            "loop_pid": loop_pid,
        },
        "checks": checks,
    }


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def render_html(d):
    bad = [c for c, ok in d["checks"] if not ok]
    status_color = "#2f9e44" if not bad else "#e03131"
    rows = []
    for b in d["bots"]:
        hb = ("%ds" % b["heartbeat_age_sec"]) if b["heartbeat_age_sec"] is not None else "无"
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                b["role"], b["display"], b["domain"], b["status"],
                "✅" if b["secret_ready"] else "—",
                b["group_mode"], ", ".join(b["events"]),
                ("✅ " if b["heartbeat_ok"] else "⚠️ ") + hb))
    check_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>"
        % ("✅" if ok else "❌", name) for name, ok in d["checks"])
    gw = d["gateway"]
    kinds = "<br>".join("%s: %d" % (k, v) for k, v in sorted(gw["push_kinds"].items()))
    target_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            t.get("name"), t.get("type"), t.get("app") or "—",
            str(t.get("id"))[:26]) for t in d.get("targets") or [])
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>邓芝台 · 飞书中台管理台</title>
<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px;background:#f8f9fa;color:#212529}
h1{font-size:20px}h2{font-size:16px;margin-top:24px;border-left:4px solid #4c6ef5;padding-left:8px}
table{border-collapse:collapse;width:100%%;background:#fff;font-size:13px}
th,td{border:1px solid #dee2e6;padding:6px 8px;text-align:left}th{background:#eef2ff}
.badge{padding:2px 8px;border-radius:10px;color:#fff;background:%s;font-size:12px}
pre{background:#fff;border:1px solid #dee2e6;padding:8px;font-size:12px;overflow:auto}</style></head>
<body><h1>邓芝台 · 飞书中台管理台（只读）</h1>
<p>生成时间：%s ｜ 总体：<span class="badge">%s</span></p>
<h2>机器人台账 / 健康</h2><table><tr><th>角色</th><th>显示名</th><th>域</th><th>状态</th>
<th>凭据</th><th>群策略</th><th>事件</th><th>心跳</th></tr>%s</table>
<h2>收件人台账（push_targets）</h2><table><tr><th>名称</th><th>类型</th><th>app</th><th>id</th></tr>%s</table>
<h2>一致性检查</h2><table><tr><th>结果</th><th>检查项</th></tr>%s</table>
<h2>通道闸门（24h）</h2><table><tr><th>出向记录</th><th>成功</th><th>静默拦截</th>
<th>hub 降级失败</th><th>已兜底</th><th>真实丢失</th><th>死信</th><th>入向工单</th>
<th>决策账</th><th>看护 PID</th></tr>
<tr><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>
<td>%s</td><td>%s</td></tr></table>
<p><b>kind 分布：</b><br>%s</p>
<h2>路由表</h2><pre>%s</pre>
<h2>看护日志尾部</h2><pre>%s</pre>
<p style="color:#868e96;font-size:12px">W1 离线版：只读汇总本机文件/账本；W3 挂到 /feishu-console 后由 nginx 鉴权。</p>
</body></html>""" % (
        status_color, d["ts"], "全绿" if not bad else "有 %d 项不绿" % len(bad),
        "".join(rows), target_rows, check_rows,
        gw["push_24h"], gw["push_ok_24h"], gw["denied_24h"],
        gw["hub_fail_24h"], gw["hub_fail_recovered_24h"], gw["lost_24h"],
        gw["dead_letters_24h"], gw["inbound_tickets"],
        ("total=%s pending=%s %s" % (gw["decisions"]["total"],
                                     gw["decisions"]["pending"],
                                     gw["decisions"]["error"])).strip(),
        gw["loop_pid"] or "无", kinds or "—",
        json.dumps(d["routes"], ensure_ascii=False, indent=1),
        gw["task_log_tail"] or "—")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    data = collect()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    out = args.out or os.path.join(
        OUT_DIR, "feishu_console_%s.html" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_html(data))
    bad = [c for c, ok in data["checks"] if not ok]
    print("WROTE %s" % out)
    print("bots=%d routes=%d checks=%s" % (
        len(data["bots"]), len(data["routes"]),
        "ALL GREEN" if not bad else "NOT GREEN: " + "; ".join(bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
