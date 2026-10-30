# -*- coding: utf-8 -*-
"""一键发送「逐页测试卡」脚本（2026-10-25）。

用途：给老田逐页验收 H5——按场景列表一张一张发单按钮卡片（零界·通知通道）。
依赖：portal/m_links（开关与 base）、portal/work/feishu_secrets/zb.ops.notify.json。

用法：
  python m_test_cards.py --dry-run            # 只打印每张卡的目标 URL，不发
  python m_test_cards.py                      # 发全部 8 张
  python m_test_cards.py --only picks,chan    # 只发指定场景
  python m_test_cards.py --scene ticket --id lg-5917d4c4357b
  python m_test_cards.py --st                # 每张卡附分享归因 st（share_tokens）
  python m_test_cards.py --to <open_id|union_id> --to-type union_id

安全：只读 secrets 文件；不打印 token/URL 令牌全文；失败重试 3 次。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SECRETS = os.path.join(HERE, "work", "feishu_secrets", "zb.ops.notify.json")
DEFAULT_UNION = "on_73852db6352a7f834ba0802060ae5aee"
OPENAPI = "https://open.feishu.cn/open-apis"

SCENES = [
    ("entry", "统一入口", "所有飞书页面汇总在这里（10 个入口，令牌 7 天）", "blue", {}),
    ("picks", "今日选股", "候选 / T1·T2 / 点个股进缠论", "green", {}),
    (
        "chan",
        "缠论详情 605100",
        "中枢 / 止损止盈 / 决策链（数据为缓存，页面会标注）",
        "orange",
        {"code": "605100"},
    ),
    ("position", "持仓 · 止盈止损", "模拟仓线位 / 盈亏", "red", {}),
    ("daily", "数据日报", "情绪 × 次日实证 / 漂移哨兵 / 管道体检", "turquoise", {}),
    ("ledger", "账本", "开仓 / 待定 / 已平 / 胜率", "purple", {}),
    ("lineage", "数据血缘", "追踪表 / 健康 / 断链与最新产出", "indigo", {}),
    (
        "ticket",
        "工单详情",
        "派单受理进度（示例工单）",
        "grey",
        {"id": "lg-5917d4c4357b"},
    ),
]


def _token(app):
    req = urllib.request.Request(
        OPENAPI + "/auth/v3/tenant_access_token/internal",
        data=json.dumps(
            {"app_id": app["app_id"], "app_secret": app["app_secret"]}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with safe_urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["tenant_access_token"]


def _send(token, receive_id, receive_type, card, retries=3):
    body = json.dumps(
        {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        ensure_ascii=False,
    ).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(
                OPENAPI + "/im/v1/messages?receive_id_type=" + receive_type,
                data=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            )
            with safe_urlopen(req, timeout=25) as r:
                res = json.loads(r.read().decode())
            return (res.get("data") or {}).get("message_id") or "?"
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                return "ERR:" + str(e)[:80]
            time.sleep(2)
    return "ERR"


def _st_token(kind="h5_test"):
    try:
        import share_tokens

        return share_tokens.gen(kind=kind, ttl_hours=72)
    except Exception:  # noqa: BLE001
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--scene", default="")
    ap.add_argument("--id", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--st", action="store_true")
    ap.add_argument("--to", default=DEFAULT_UNION)
    ap.add_argument("--to-type", default="union_id")
    args = ap.parse_args(argv)

    import m_links

    if not m_links.enabled():
        print("m_link.json 未启用（enabled=false），先启用再发卡")
        return 2

    scene_list = SCENES
    if args.scene:
        scene_list = [(s, t, d, c, k) for (s, t, d, c, k) in SCENES if s == args.scene]
    elif args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        scene_list = [x for x in SCENES if x[0] in want]
    if not scene_list:
        print("没有匹配的场景")
        return 2

    planned = []
    for i, (scene, title, desc, color, kw) in enumerate(scene_list, 1):
        kw = dict(kw)
        if args.id:
            kw["id"] = args.id
        if args.code:
            kw["code"] = args.code
        st = _st_token() if args.st else None
        url = m_links.url_for(scene, st=st, **kw)
        planned.append((i, scene, title, desc, color, url))

    if args.dry_run:
        for i, scene, title, desc, color, url in planned:
            print("%d. %-9s %s  %s" % (i, scene, title, url[:100]))
        return 0

    app = json.load(open(SECRETS, encoding="utf-8"))
    tok = _token(app)
    print("发送 %d 张卡片 → %s（%s）" % (len(planned), args.to, args.to_type))
    ok = 0
    for i, scene, title, desc, color, url in planned:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {
                    "tag": "plain_text",
                    "content": "零界 H5 · 测试 %d/%d · %s" % (i, len(planned), title),
                },
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": desc
                        + "\n\n<font color='grey'>点下面按钮，用手机打开这个页面。</font>",
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {"tag": "plain_text", "content": "打开 · " + title},
                            "url": url,
                        }
                    ],
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "第 %d/%d 张 · 逐页验收" % (i, len(planned)),
                        }
                    ],
                },
            ],
        }
        mid = _send(tok, args.to, args.to_type, card)
        print("%d/%d %-9s %s" % (i, len(planned), scene, mid), flush=True)
        if mid and not mid.startswith("ERR"):
            ok += 1
        time.sleep(1.5)
    print("完成：%d/%d 发送成功" % (ok, len(planned)))
    return 0 if ok == len(planned) else 1


if __name__ == "__main__":
    sys.exit(main())
