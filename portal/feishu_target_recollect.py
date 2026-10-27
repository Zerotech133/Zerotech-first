# -*- coding: utf-8 -*-
"""新 app 域收件人重采工具（W3 sender 统一前置，2026-10-24）。

为什么需要：open_id / chat_id 是 **app 维度**的；sender 换到新 app 后旧 id
全部失效（230001 / 99992356）。本工具用**新 app 凭证**只读采集：
  · 群：GET /im/v1/chats（机器人须已在群内）→ chat_id + name
  · 人：显式 --owner-open-id（老田用新 app 私聊 bot 后从事件取），可选按
        手机号/邮箱走通讯录 batch_get_id 查
然后按旧 push_targets 的名字生成迁移补丁（dry-run 只打印；--apply 才写，
写前备份 news_config.json，供 W3 窗口内人工确认后使用）。

用法：
    python feishu_target_recollect.py --role zb.ops.notify --dry-run
    python feishu_target_recollect.py --role zb.ops.notify --owner-open-id ou_xxx
    python feishu_target_recollect.py --role zb.ops.notify --chats-file chats.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NEWS = os.path.join(ROOT, "news_collector")
SECRETS = os.path.join(HERE, "work", "feishu_secrets")
NEWS_CONFIG = os.path.join(NEWS, "news_config.json")
OPEN = "https://open.feishu.cn/open-apis"
for p in (HERE, NEWS):
    if p not in sys.path:
        sys.path.insert(0, p)

# 旧 push_targets 名 → 新 app 群名候选（群名可能带品牌/别名）
CHAT_NAME_ALIASES = {
    "内部测试": ["内部测试"],
    "量化群": ["老田的AI量化", "量化群"],
    "家人群": ["家人群", "家庭群"],
}


def role_creds(role, secrets_dir=None):
    import feishu_client

    path = os.path.join(secrets_dir or SECRETS, role + ".json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    aid, sec = str(d.get("app_id") or ""), str(d.get("app_secret") or "")
    if not aid or not sec:
        raise ValueError("凭据缺 app_id/app_secret：%s" % path)
    tok = feishu_client.get_tat(aid, sec)
    if not tok:
        raise ValueError("新 app 取 tenant token 失败（app=%s）" % aid[:10])
    return aid, tok


def _get(url, token, opener=None):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    o = opener or urllib.request.urlopen
    with o(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def list_chats(token, opener=None):
    """GET /im/v1/chats 全量（分页）→ [{chat_id,name}]。"""
    chats, page = [], ""
    while True:
        url = OPEN + "/im/v1/chats?page_size=50"
        if page:
            url += "&page_token=" + urllib.parse.quote(page, safe="")
        d = _get(url, token, opener=opener)
        if d.get("code") != 0:
            raise ValueError("列群失败：code=%s %s" % (d.get("code"), d.get("msg")))
        data = d.get("data") or {}
        for it in data.get("items") or []:
            chats.append({"chat_id": it.get("chat_id"), "name": it.get("name") or ""})
        page = data.get("page_token") or ""
        if not data.get("has_more") or not page:
            return chats


def discover_owner_open_id(token, mobile="", email="", opener=None):
    """按手机号/邮箱查 open_id（新 app 域）；未提供或查不到 → ""。"""
    body = {}
    if mobile:
        body["mobiles"] = [mobile]
    if email:
        body["emails"] = [email]
    if not body:
        return ""
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OPEN + "/contact/v3/users/batch_get_id?user_id_type=open_id",
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        o = opener or urllib.request.urlopen
        with o(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        for key in ("mobiles", "emails"):
            for u in (d.get("data") or {}).get(key) or []:
                if u.get("user_id"):
                    return u["user_id"]
    except Exception:  # noqa: BLE001
        return ""
    return ""


def build_patch(old_targets, chats, owner_open_id="", app_id=""):
    """→ ({patch:[...], unmatched:[...], warnings:[...]})。纯函数，不写盘。"""
    by_name = {}
    for c in chats or []:
        if c.get("name"):
            by_name.setdefault(c["name"].strip(), c["chat_id"])
    patch, unmatched, warnings = [], [], []
    for t in old_targets or []:
        name = t.get("name") or "?"
        ttype = t.get("type")
        if ttype == "open_id":
            if owner_open_id:
                patch.append(
                    {
                        "name": name,
                        "type": "open_id",
                        "id": owner_open_id,
                        "app": app_id,
                    }
                )
            else:
                unmatched.append(
                    {"name": name, "reason": "缺新域 open_id（--owner-open-id）"}
                )
            continue
        hit = None
        for cand in CHAT_NAME_ALIASES.get(name, [name]):
            if cand in by_name:
                hit = by_name[cand]
                break
        if hit:
            patch.append({"name": name, "type": "chat_id", "id": hit, "app": app_id})
        else:
            unmatched.append(
                {"name": name, "reason": "新 app 群列表未找到（先把机器人拉进群）"}
            )
    if not chats:
        warnings.append("群列表为空：新 app 机器人可能未加入任何群")
    return {"patch": patch, "unmatched": unmatched, "warnings": warnings}


def apply_patch(new_targets, config_path=None):
    """窗口内人工确认后使用：备份 news_config.json → 替换 push_targets。"""
    path = config_path or NEWS_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    backup = path + ".bak." + datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    cfg["push_targets"] = new_targets
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    return backup


def run(
    role,
    dry_run=True,
    owner_open_id="",
    chats_file="",
    mobile="",
    email="",
    apply=False,
):
    rep = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,
        "dry_run": dry_run,
    }
    try:
        app_id, token = role_creds(role)
    except Exception as e:  # noqa: BLE001
        rep.update({"ok": False, "error": "凭据不可用：%s" % e})
        return rep
    try:
        if chats_file:
            with open(chats_file, encoding="utf-8") as f:
                chats = json.load(f)
        else:
            chats = list_chats(token)
        rep["chats"] = chats
        if not owner_open_id and (mobile or email):
            owner_open_id = discover_owner_open_id(token, mobile, email)
        with open(NEWS_CONFIG, encoding="utf-8") as f:
            old_targets = (json.load(f) or {}).get("push_targets") or []
        built = build_patch(old_targets, chats, owner_open_id, app_id)
        rep.update(built)
        rep["ok"] = not built["unmatched"]
        if apply and not dry_run and not built["unmatched"]:
            rep["backup"] = apply_patch(built["patch"])
        return rep
    except Exception as e:  # noqa: BLE001
        rep.update({"ok": False, "error": "采集失败：%s" % e})
        return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="zb.ops.notify")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--owner-open-id", default="")
    ap.add_argument("--chats-file", default="")
    ap.add_argument("--mobile", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = run(
        args.role,
        dry_run=not args.apply,
        owner_open_id=args.owner_open_id,
        chats_file=args.chats_file,
        mobile=args.mobile,
        email=args.email,
        apply=args.apply,
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        lines = [
            "# 收件人重采（新 app 域）· %s" % rep.get("role"),
            "时间：%s ｜ dry_run=%s ｜ ok=%s"
            % (rep.get("ts"), rep.get("dry_run"), rep.get("ok")),
        ]
        if rep.get("error"):
            lines.append("错误：%s" % rep["error"])
        for w in rep.get("warnings") or []:
            lines.append("⚠️ " + w)
        lines.append("## 迁移补丁")
        for p in rep.get("patch") or []:
            lines.append(
                "+ %s %s %s (app=%s)" % (p["name"], p["type"], p["id"], p["app"])
            )
        lines.append("## 未匹配")
        for u in rep.get("unmatched") or []:
            lines.append("- %s：%s" % (u["name"], u["reason"]))
        if rep.get("backup"):
            lines.append("已备份：%s" % rep["backup"])
        print("\n".join(lines))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
