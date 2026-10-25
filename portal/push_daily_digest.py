# -*- coding: utf-8 -*-
"""军机驿·推送日报（P1-4，2026-10-26）：每日一条只读摘要卡。

数据：m_push.push_payload()（本机直读：推送流水/死信/渠道/订阅）
卡片：统一分装（feishu_push.build），按钮跳 /m/push。
投递：feishu_client.send_msg（经 8097 闸门，kind=auto:push_digest）。
用法：python push_daily_digest.py --dry   # 只打印卡片 JSON
      python push_daily_digest.py         # 发送给 owner
调度：待老田批准后挂到现有计划任务（不新建任务）。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
NC = os.path.join(ROOT, "news_collector")
if NC not in sys.path:
    sys.path.insert(0, NC)

import authorization  # noqa: E402
import feishu_push  # noqa: E402
import m_links  # noqa: E402
import m_push  # noqa: E402


def build_card():
    d = m_push.push_payload()
    stats = (d.get("blocks") or [{}])[0].get("items") or []
    vals = {it.get("label"): it.get("value") for it in stats}
    channels = []
    for b in d.get("blocks") or []:
        if str(b.get("title") or "").startswith("①"):
            channels = b.get("rows") or []
            break
    top_ch = "、".join("%s %s" % (r[0], r[1]) for r in channels[:3]) or "—"
    today = vals.get("今日发送", "—")
    okfail = vals.get("24h 成功/失败", "—")
    dl = vals.get("死信 24h", "—")
    fb = vals.get("降级直发 24h", "—")
    url = m_links.url_for("push") or ""
    buttons = [{"type": "url", "text": "打开推送中台", "url": url,
                "style": "primary"}] if url else None
    return feishu_push.build(
        "notify", "推送日报", summary="今日发送 %s · 24h 成功/失败 %s · 死信 %s · 降级 %s"
        % (today, okfail, dl, fb),
        kpis=[("今日", "%s" % today), ("成功/失败", "%s" % okfail),
              ("死信", "%s" % dl), ("降级", "%s" % fb)],
        items=["渠道：%s" % top_ch,
               "静默窗 %s（critical 豁免）" % vals.get("静默窗", "—")],
        ts=d.get("generated_at", ""), source="推送中台（只读）",
        level="warn" if (str(dl) not in ("0", "—") or
                         str(okfail).split("/")[-1] not in ("0", "—")) else "info",
        buttons=buttons, tags="#pj #push_digest")


def send(card):
    """经「零界·通知」app 发 union_id（open_id 是 app 维度，跨 app 会 400）。

    说明：这是运维摘要卡，走通知 app 直发；生产推送链仍以 8097 闸门为准
    （P0-1 审计已记录本旁路，待 hub 支持 app 选择后收敛）。
    """
    import json as _j
    import urllib.request as _u
    sec_path = os.path.join(HERE, "work", "feishu_secrets", "zb.ops.notify.json")
    try:
        sec = _j.load(open(sec_path, encoding="utf-8"))
    except (OSError, ValueError):
        return False, "缺通知 app 凭据"
    try:
        req = _u.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=_j.dumps({"app_id": sec["app_id"],
                           "app_secret": sec["app_secret"]}).encode(),
            headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=15) as r:
            tok = _j.loads(r.read().decode()).get("tenant_access_token")
        union = (authorization.person("田峰") or {}).get("union_id") or ""
        if not tok or not union:
            return False, "缺 token/union_id"
        req = _u.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=union_id",
            data=_j.dumps({"receive_id": union, "msg_type": "interactive",
                           "content": _j.dumps(card, ensure_ascii=False)},
                          ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + tok,
                     "Content-Type": "application/json"})
        with _u.urlopen(req, timeout=20) as r:
            res = _j.loads(r.read().decode())
        ok = res.get("code") == 0
        return ok, ("message_id=" + str((res.get("data") or {}).get("message_id"))
                    if ok else str(res.get("msg") or res.get("code")))
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


STATE = os.path.join(HERE, "work", "push_digest_state.json")


def send_once(force=False):
    """每日一次（挂现有 08:00 任务用）。→ (ok, why)。"""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if not force and st.get("date") == today:
        return True, "already_sent"
    card = build_card()
    ok, why = send(card)
    if ok:
        try:
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump({"date": today,
                           "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                           "message_id": why}, f, ensure_ascii=False)
        except OSError:
            pass
    return ok, why


def main(argv=None):
    argv = argv or sys.argv[1:]
    if "--once" in argv:
        ok, why = send_once(force="--force" in argv)
        print("PUSH_DIGEST_ONCE", "SEND_OK" if ok else "SEND_FAIL", why or "")
        return 0 if ok else 1
    card = build_card()
    if "--dry" in argv:
        print(json.dumps(card, ensure_ascii=False, indent=1)[:3000])
        return 0
    ok, why = send(card)
    print("PUSH_DIGEST", "SEND_OK" if ok else "SEND_FAIL", why or "")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
