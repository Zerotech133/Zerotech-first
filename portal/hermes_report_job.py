# -*- coding: utf-8 -*-
"""Hermes 军机驿日报/周报/月报 · 平台侧定时抓取与投递（H3 收尾，2026-10-26）。

按 K-ORG-112「Hermes 不当定时器」：定时由平台任务触发本脚本，
脚本调用 Hermes 只读 API 生成草稿（Hermes 不自己排程）；
草稿落盘 portal/work/hermes_reports/，并投递一份「摘要卡」给 owner。

用法：
  python hermes_report_job.py --dry            # 只抓取/打印，不发送
  python hermes_report_job.py --period day     # 抓取并投递（每日一次）
  python hermes_report_job.py --period week|month
环境：HERMES_REPORT_ENABLED=0 可关闭定时投递；HERMES_BASE 可覆盖地址。
"""

from __future__ import annotations



import json
import os
import sys
import urllib.request
from datetime import datetime

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen



try:  # Phase B1: 8787 密码认证后自动带 hermes_session
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from agent_platform.legion_command.hermes_auth import auth_urlopen as safe_urlopen  # noqa: E402
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NC = os.path.join(ROOT, "news_collector")
for p in (HERE, NC):
    if p not in sys.path:
        sys.path.insert(0, p)

BASE = os.environ.get("HERMES_BASE", "http://127.0.0.1:8787")
OUT_DIR = os.path.join(HERE, "work", "hermes_reports")
STATE = os.path.join(HERE, "work", "hermes_report_state.json")
SECRETS = os.path.join(HERE, "work", "feishu_secrets", "zb.ops.notify.json")
UNION = "on_73852db6352a7f834ba0802060ae5aee"
TIMEOUT = 60


def fetch(period="day"):
    url = "%s/api/hermes/junjiyi/report?period=%s" % (BASE, period)
    try:
        with safe_urlopen(url, timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "Hermes 报告不可用：%s" % str(e)[:160]}


def save(period, md):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        fp = os.path.join(
            OUT_DIR,
            "hermes_%s_%s.md" % (period, datetime.now().strftime("%Y%m%d-%H%M")),
        )
        with open(fp, "w", encoding="utf-8") as f:
            f.write(md)
        return fp
    except OSError:
        return ""


def _once_today(key):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    today = datetime.now().strftime("%Y-%m-%d")
    return st.get(key) == today


def _mark(key):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st[key] = datetime.now().strftime("%Y-%m-%d")
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def send_card(period, md):
    """摘要卡（前 1600 字 + 关键计数），经通知 app 直发 owner。"""
    try:
        sec = json.load(open(SECRETS, encoding="utf-8"))
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=json.dumps(
                {"app_id": sec["app_id"], "app_secret": sec["app_secret"]}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with safe_urlopen(req, timeout=20) as r:
            tok = json.loads(r.read().decode()).get("tenant_access_token")
        if not tok:
            return False, "token 获取失败"
        label = {"day": "日报", "week": "周报", "month": "月报"}.get(period, "日报")
        body = md[:1600] + (
            "\n\n…（完整版见 portal/work/hermes_reports/）" if len(md) > 1600 else ""
        )
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "turquoise",
                "title": {
                    "tag": "plain_text",
                    "content": "📋 军机驿 · Hermes %s草稿" % label,
                },
            },
            "elements": [
                {"tag": "markdown", "content": body},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "Hermes 只读草稿；发送/执行仍需人工；"
                            "8097 console 可查决策与审批",
                        }
                    ],
                },
            ],
        }
        data = json.dumps(
            {
                "receive_id": UNION,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            ensure_ascii=False,
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=union_id",
            data=data,
            headers={
                "Authorization": "Bearer " + tok,
                "Content-Type": "application/json",
            },
        )
        with safe_urlopen(req, timeout=25) as r:
            res = json.loads(r.read().decode())
        return res.get("code") == 0, str(
            (res.get("data") or {}).get("message_id") or res.get("msg")
        )
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def run_once(period="day", force=False, dry=False):
    key = "sent_" + period
    if not force and _once_today(key):
        return True, "already_sent_today"
    d = fetch(period)
    if not d.get("ok"):
        return False, d.get("error") or "报告生成失败"
    md = d.get("markdown") or ""
    fp = save(period, md)
    if dry:
        print(md[:1200])
        return True, "dry ok len=%d file=%s" % (len(md), fp)
    ok, why = send_card(period, md)
    if ok:
        _mark(key)
    return ok, why


def main(argv=None):
    argv = argv or sys.argv[1:]
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="day")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    ok, why = run_once(a.period, force=a.force, dry=a.dry)
    print("HERMES_REPORT", "OK" if ok else "FAIL", why)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
