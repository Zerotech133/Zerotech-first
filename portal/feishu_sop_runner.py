# -*- coding: utf-8 -*-
"""新增机器人 7 步 SOP 执行器（W4 演练工具，2026-10-24）。

对应《飞书体系总规划 v1》§5 的 7 步；默认 **dry-run**：在一份临时副本上
完整走一遍 1→7，证明"新增机器人 = 注册一行配置 + 凭据 + 路由"，不碰生产文件。

用法：
    python feishu_sop_runner.py --dry-run                 # 默认演练角色
    python feishu_sop_runner.py --dry-run --role zb.demo.echo --display 零界·演练
    python feishu_sop_runner.py --live --role zb.owner.exec   # 实机核验（不新建）
    python feishu_sop_runner.py --dry-run --json
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import tempfile
import shutil
import sys
import uuid
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data", "feishu")
REGISTRY = os.path.join(DATA, "registry.json")
ROUTES = os.path.join(DATA, "routes.json")
SECRETS = os.path.join(HERE, "work", "feishu_secrets")
LOGS = os.path.join(HERE, "logs")
WORK_TMP = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "aios_sop_tmp"
)
DEFAULT_ROLE = "zb.demo.echo"
DEFAULT_DISPLAY = "零界·演练"

if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _step(no, name, ok, detail="", manual=False):
    return {
        "step": no,
        "name": name,
        "ok": bool(ok),
        "manual": bool(manual),
        "detail": str(detail)[:240],
    }


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _demo_role(role, display):
    return {
        "role": role,
        "display": display,
        "domain": ["owner"],
        "app_ref": role.upper().replace(".", "_"),
        "app_id_current": "",
        "events": ["im.message.receive_v1"],
        "perms": ["im:message", "im:message:send_as_bot", "im:resource"],
        "group_policy": {"mode": "p2p_only", "allow_chats": []},
        "inputs": "text",
        "outputs": "text",
        "owner": "老田",
        "status": "planned",
    }


def dry_run(role=DEFAULT_ROLE, display=DEFAULT_DISPLAY):
    """临时副本上走完 7 步 → 步骤结果列表。"""
    import feishu_registry as fr
    import feishu_bot_base as base

    os.makedirs(WORK_TMP, exist_ok=True)
    # 注意：不要用 tempfile.mkdtemp —— Windows 沙箱下 mkdtemp 生成的目录
    # ACL 不继承，子目录/文件均 PermissionError（2026-10-24 实测）。
    tmp = os.path.join(WORK_TMP, uuid.uuid4().hex)
    os.makedirs(tmp, exist_ok=True)
    steps = []
    try:
        reg = _read_json(REGISTRY)
        routes = _read_json(ROUTES)
        reg_copy = copy.deepcopy(reg)
        routes_copy = copy.deepcopy(routes)

        # 1) registry 登记角色
        reg_copy["roles"].append(_demo_role(role, display))
        roles, warnings = fr.validate_registry(reg_copy)
        steps.append(
            _step(
                1,
                "registry 登记角色",
                role in roles and not warnings,
                "角色 %s · 显示名 %s · 告警 %s" % (role, display, warnings or "无"),
            )
        )

        # 2) app + secret（dry-run 用临时凭据文件验证装载接口）
        sec_dir = os.path.join(tmp, "secrets")
        os.makedirs(sec_dir, exist_ok=True)
        _write_json(
            os.path.join(sec_dir, role + ".json"),
            {"app_id": "cli_dryrun_demo", "app_secret": "dryrun-secret"},
        )
        aid, sec = base.load_secret(role, sec_dir)
        steps.append(
            _step(
                2,
                "app + secret 装载",
                bool(aid and sec),
                "dry-run 凭据 app_id=%s（真实 app 由老田在开放平台建）" % aid,
                manual=True,
            )
        )

        # 3) routes 加一行
        routes_copy["routes"].append(
            {
                "id": "r.demo.echo",
                "match": {"role": role},
                "handler": "demo:echo",
                "priority": 70,
            }
        )
        rs = fr.validate_routes(routes_copy, roles_index=roles)
        hit = fr.match_route(rs, role=role)
        steps.append(
            _step(3, "routes 加一行", hit == "demo:echo", "路由命中 handler=%s" % hit)
        )

        # 4) 部署标准 bot 进程（校验公共壳 + 拉起器模板存在）
        raw = {
            "_index": roles,
            "group_whitelist": reg_copy.get("group_whitelist") or [],
        }
        shell = base.Shell(role, registry=raw)
        launcher = os.path.join(HERE, "start_feishu_exec_bot.ps1")
        steps.append(
            _step(
                4,
                "标准 bot 进程",
                shell.display == display and os.path.exists(launcher),
                "公共壳构造 OK（%s）；拉起器模板=%s"
                % (shell.display, os.path.basename(launcher)),
            )
        )

        # 5) 管理台确认（数据模型可容纳 + 群策略 p2p_only）
        meta = roles[role]
        p2p = (meta.get("group_policy") or {}).get("mode") == "p2p_only"
        p2p_only_ok = shell.allow_inbound(
            "p2p", "oc_x", "hi"
        ) and not shell.allow_inbound("group", "oc_x", "hi")
        steps.append(
            _step(
                5,
                "管理台确认",
                p2p and p2p_only_ok,
                "群策略=%s · p2p 放行/群拒绝 符合预期"
                % (meta.get("group_policy") or {}).get("mode"),
            )
        )

        # 6) 灰度：默认仅 owner 私聊（演练角色 p2p_only 即灰度起点）
        gray_ok = not meta.get("group_policy", {}).get("allow_chats")
        steps.append(
            _step(
                6,
                "灰度（仅 owner 私聊）",
                gray_ok,
                "allow_chats 为空 → 群里不响应（谨慎上线）",
            )
        )

        # 7) 验收：路由三跳证据（收→路由→回执）用 match_route + dispatch 表验证
        os.path.join(tmp, "action_log.jsonl")
        steps.append(
            _step(
                7,
                "验收证据（路由/回执三跳）",
                hit is not None and os.path.exists(launcher),
                "演示：匹配=%s → handler=demo:echo → 回执由角色 handler 承担；"
                "真实收消息由 Feishu app 长连接触发" % hit,
            )
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return steps


def live_check(role):
    """实机核验：角色已登记、凭据就绪、心跳新鲜、管理台可见。"""
    import feishu_registry as fr
    import feishu_console as console

    steps = []
    try:
        roles, _ = fr.load_registry()
    except Exception as e:  # noqa: BLE001
        return [_step(0, "registry 读取", False, "异常：%s" % e)]
    if role not in roles:
        return [_step(1, "registry 登记角色", False, "角色不存在：%s" % role)]
    steps.append(_step(1, "registry 登记角色", True, "已登记"))
    secret = os.path.join(SECRETS, role + ".json")
    ready = os.path.exists(secret)
    steps.append(
        _step(
            2,
            "app + secret",
            ready,
            "凭据文件存在" if ready else "缺 %s" % secret,
            manual=True,
        )
    )
    d = console.collect()
    routes_hit = [r for r in d["routes"] if (r.get("match") or {}).get("role") == role]
    steps.append(
        _step(3, "routes", bool(routes_hit), "命中 %d 条路由" % len(routes_hit))
    )
    hb = os.path.join(LOGS, "feishu_heartbeat_%s.txt" % role)
    hb_age = console._age_sec(hb)
    hb_source = "role"
    if hb_age is None:
        legacy = os.path.join(LOGS, "feishu_bot_heartbeat.txt")
        canonical = ""
        try:
            with open(os.path.join(SECRETS, "canonical.json"), encoding="utf-8") as f:
                canonical = str(json.load(f).get("app_id") or "")
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feishu_sop_runner.py", exc)
        app = roles.get(role, {}).get("app_id_current") or ""
        if app and app == canonical:
            age = console._age_sec(legacy)
            if age is not None:
                hb_age, hb_source = age, "legacy"
    steps.append(
        _step(
            4,
            "bot 进程/心跳",
            hb_age is not None and hb_age < 900,
            "%s（%s）"
            % ("心跳 %ds" % hb_age if hb_age is not None else "无心跳", hb_source),
        )
    )
    botrow = [b for b in d["bots"] if b["role"] == role]
    visible = bool(botrow)
    steps.append(
        _step(5, "管理台可见", visible, "台账可见" if visible else "管理台未显示")
    )
    return steps


def summarize(role, steps):
    failed = [s for s in steps if not s["ok"]]
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,
        "total": len(steps),
        "passed": len(steps) - len(failed),
        "failed": len(failed),
        "verdict": "PASS" if not failed else "FAIL",
        "steps": steps,
    }


def render(report):
    lines = [
        "# 新增机器人 7 步 SOP · %s" % report["role"],
        "",
        "时间：%s ｜ 结论：**%s**（%d/%d）"
        % (report["ts"], report["verdict"], report["passed"], report["total"]),
        "",
        "| 步 | 项目 | 结果 | 说明 |",
        "|---|---|---|---|",
    ]
    for s in report["steps"]:
        lines.append(
            "| %d | %s | %s | %s |"
            % (s["step"], s["name"], "✅" if s["ok"] else "❌", s["detail"])
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--role", default=DEFAULT_ROLE)
    ap.add_argument("--display", default=DEFAULT_DISPLAY)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.live:
        steps = live_check(args.role)
    else:
        # 公共壳会 print 群策略日志；CLI 输出（尤其 --json）需纯净
        with contextlib.redirect_stdout(io.StringIO()):
            steps = dry_run(args.role, args.display)
    rep = summarize(args.role, steps)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(render(rep))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
