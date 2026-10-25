# -*- coding: utf-8 -*-
"""W3 sender 统一切换器（dry-run 默认可跑，2026-10-24）。

背景（关键约束）：
  open_id / chat_id 是 **app 维度**的。把出向 sender 从旧 app 切到新 app
  （零界·通知/执行）时，push_targets 里旧的 open_id/chat_id 对新 app 无效
  → 必须**先在新 app 域下重采收件人 id**，否则 230001 invalid receive_id。

本工具只做只读评估与清单输出，不发消息、不改配置：
  --status                当前 canonical / 各角色凭据 / 目标域阻塞项
  --role zb.ops.notify    该角色切换 sender 的步骤与阻塞项
  --json                  机器可读

切换顺序建议（写进 W3 回执）：
  1) 新 app 域重采收件人 → push_targets 换 id/app
  2) 角色凭据入 feishu_secrets/<role>.json
  3) 生产者带 sender_role 灰度（数据 + 回执核对）
  4) 全量后 canonical 切新 app + 重启 8097
  5) 验收（feishu_acceptance --network）
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
NEWS_CONFIG = os.path.join(os.path.dirname(HERE), "news_collector",
                           "news_config.json")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_targets():
    return _read_json(NEWS_CONFIG, {}).get("push_targets") or []


def role_secret_ready(role, secrets_dir=None):
    d = _read_json(os.path.join(secrets_dir or SECRETS, role + ".json"), {})
    return bool(str(d.get("app_id") or "").strip()
                and str(d.get("app_secret") or "").strip())


def target_domain_blockers(targets, app_id):
    """目标 app 与目标 sender app 不一致 → 阻塞项（必须先重采收件人 id）。"""
    out = []
    for t in targets or []:
        want = str(t.get("app") or "").strip()
        if not want:
            out.append({"name": t.get("name"), "id": t.get("id"),
                        "reason": "目标未登记 app（无法判断域）"})
        elif app_id and want != app_id:
            out.append({"name": t.get("name"), "id": t.get("id"),
                        "reason": "目标 app=%s ≠ sender app=%s（跨域必 230001）"
                                  % (want, app_id)})
    return out


def switch_steps(role, app_id, targets):
    """切换步骤清单（含阻塞项判定）。"""
    blockers = target_domain_blockers(targets, app_id)
    return [
        {"step": 1, "action": "新 app 域重采收件人（个人 open_id / 群 chat_id）",
         "ok": not blockers,
         "detail": ("阻塞：%s" % "; ".join(b["name"] + ":" + b["reason"]
                                          for b in blockers[:3]))
         if blockers else "目标域与 sender app 一致"},
        {"step": 2, "action": "角色凭据入 feishu_secrets/%s.json" % role,
         "ok": role_secret_ready(role), "detail": "已就绪 / 待老田建 app"},
        {"step": 3, "action": "生产者带 sender_role=%s 灰度" % role,
         "ok": True, "detail": "feishu_client 已支持（8097 重启后生效）"},
        {"step": 4, "action": "全量后 canonical 切 %s + 8097 marker 换血" % app_id,
         "ok": True, "detail": "Runbook Step 1"},
        {"step": 5, "action": "验收 feishu_acceptance.py --network",
         "ok": True, "detail": "收件人可达性 + 管理台全绿"},
    ]


def status():
    import feishu_app_pool as pool
    canonical = pool.canonical_app_id()
    roles = []
    for r in ("zb.ops.notify", "zb.owner.exec", "zb.quant.ticker",
              "zb.quant.qa", "zb.customer.svc"):
        ready = role_secret_ready(r)
        aid = ""
        if ready:
            aid = _read_json(os.path.join(SECRETS, r + ".json"),
                             {}).get("app_id") or ""
        roles.append({"role": r, "secret_ready": ready, "app_id": aid})
    targets = load_targets()
    blockers = target_domain_blockers(targets, canonical)
    return {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "canonical_app": canonical,
            "roles": roles,
            "targets_total": len(targets),
            "target_domain_blockers": blockers,
            "all_roles_ready": all(x["secret_ready"] for x in roles
                                   if x["role"] in ("zb.ops.notify",
                                                    "zb.owner.exec")),
            "ready_to_switch_sender": bool(canonical) and not blockers
            and all(x["secret_ready"] for x in roles
                    if x["role"] in ("zb.ops.notify", "zb.owner.exec"))}


def evaluate_role(role):
    tgt = load_targets()
    app_id = ""
    if role_secret_ready(role):
        app_id = _read_json(os.path.join(SECRETS, role + ".json"),
                            {}).get("app_id") or ""
    return {"role": role, "secret_ready": role_secret_ready(role),
            "app_id": app_id,
            "steps": switch_steps(role, app_id or "<新 app_id>", tgt)}


def render_status(st):
    lines = ["# sender 统一切换 · 状态盘点", "",
             "时间：%s ｜ canonical=%s" % (st["ts"], st["canonical_app"] or "-"),
             "", "| 角色 | 凭据 | app_id |", "|---|---|---|"]
    for r in st["roles"]:
        lines.append("| %s | %s | %s |" % (
            r["role"], "✅" if r["secret_ready"] else "—", r["app_id"] or "-"))
    lines += ["", "| 收件人 | 阻塞原因 |", "|---|---|"]
    if st["target_domain_blockers"]:
        for b in st["target_domain_blockers"]:
            lines.append("| %s | %s |" % (b["name"], b["reason"]))
    else:
        lines.append("| 全部 | 无（域一致） |")
    lines += ["", "**能否切 sender：%s**（需先解决目标域重采收件人 + 角色凭据）"
              % ("✅ 可以" if st["ready_to_switch_sender"] else "❌ 否"), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--role", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.role:
        rep = evaluate_role(args.role)
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=1))
        else:
            lines = ["# sender 切换评估 · %s" % rep["role"], "",
                     "凭据：%s ｜ app_id：%s" % (
                         "✅" if rep["secret_ready"] else "—",
                         rep["app_id"] or "-"), ""]
            for s in rep["steps"]:
                lines.append("%s %d. %s —— %s" % (
                    "✅" if s["ok"] else "❌", s["step"], s["action"], s["detail"]))
            print("\n".join(lines))
        ready = rep["secret_ready"] and all(
            s["ok"] for s in rep["steps"] if s["step"] == 1)
        return 0 if ready else 1
    st = status()
    print(json.dumps(st, ensure_ascii=False, indent=1) if args.json
          else render_status(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
