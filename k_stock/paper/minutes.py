# -*- coding: utf-8 -*-
"""K-STOCK-137 S-c · 会议纪要投递通道插件化

蓝图原则：纪要面向 AI 或直接发操盘手，通道不绑死、可插拔。
独立账本 minutes.json（tmp+随机后缀+os.replace 原子写，同 manager.py 137c 修后形状）。
通道注册表 DELIVERY_CHANNELS + @register_channel，每通道 deliver(record) -> bool：
  - site   站内收件箱 minutes_inbox.json（默认通道，无外部依赖，必须完全可用）
  - trader 操盘手订阅流 minutes_trader_feed.json（追加写，可过滤）
  - feishu 读 FEISHU_WEBHOOK 环境变量；未配置 fail-closed 返回 False+原因，不抛，不硬编码 URL
经理档案可选字段 delivery_channels（空/缺省=默认站内），send 时校验声明通道子集合法。
"""

import argparse
import json
import os
import re
import secrets
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
import logging
from tools.safe_urlopen import safe_urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MANAGERS_DIR = HERE / "managers"

MINUTES_PATH = HERE / "minutes.json"
INBOX_PATH = HERE / "minutes_inbox.json"
TRADER_FEED_PATH = HERE / "minutes_trader_feed.json"

_MID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_KINDS = ("pre_open", "post_close")


class Reject(Exception):
    """业务拒绝（投递语义错误），与 manager.Reject 同款语义。"""


# ── 通道插件注册表 ────────────────────────────────────────────────
DELIVERY_CHANNELS: dict = {}


def register_channel(name: str):
    """通道插件装饰器：注册 name -> deliver(record)->bool。"""

    def deco(fn):
        DELIVERY_CHANNELS[name] = fn
        return fn

    return deco


# ── 账本原子写（同 manager.py 137c 修后形状）─────────────────────
def _atomic_save(path: Path, d: dict) -> None:
    tmp = path.with_suffix(f".json.tmp.{os.getpid()}.{secrets.token_hex(2)}")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _load(path: Path, default: dict) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/minutes.py", exc)
    return default


# ── 经理档案校验 ─────────────────────────────────────────────────
def _profile(mid: str) -> dict:
    if not isinstance(mid, str) or not _MID_RE.match(mid):
        raise Reject(f"非法经理ID: {mid!r}（限 1-32 位小写字母/数字/下划线）")
    fp = MANAGERS_DIR / f"{mid}.json"
    if fp.parent != MANAGERS_DIR or not fp.exists():
        raise Reject(f"未知经理: {mid}")
    return json.loads(fp.read_text(encoding="utf-8"))


def _check_channels(mid: str, channel: str) -> None:
    """channel 必须在注册表内；经理档案 delivery_channels 若声明则须覆盖之（子集合法校验）。"""
    if channel not in DELIVERY_CHANNELS:
        raise Reject(
            f"未知投递通道: {channel!r}（可选: {', '.join(sorted(DELIVERY_CHANNELS))}）"
        )
    declared = _profile(mid).get("delivery_channels")
    if declared and channel not in declared:
        raise Reject(f"经理 {mid} 未声明通道 {channel!r}（声明: {declared}）")


# ── 通道实现 ─────────────────────────────────────────────────────
@register_channel("site")
def _deliver_site(record: dict) -> bool:
    """站内投递：写收件箱 minutes_inbox.json（portal 只读投影用）。默认通道，必须完全可用。"""
    d = _load(INBOX_PATH, {"items": []})
    d["items"].append(record)
    _atomic_save(INBOX_PATH, d)
    return True


@register_channel("trader")
def _deliver_trader(record: dict) -> bool:
    """直发操盘手：追加写订阅流 minutes_trader_feed.json（按 trainee id / style 可过滤）。"""
    d = _load(TRADER_FEED_PATH, {"items": []})
    d["items"].append(record)
    _atomic_save(TRADER_FEED_PATH, d)
    return True


@register_channel("feishu")
def _deliver_feishu(record: dict) -> bool:
    """飞书：读 FEISHU_WEBHOOK 环境变量；未配置 fail-closed 返回 False+原因，不抛，不硬编码 URL。"""
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not url:
        record["meta"]["error"] = "FEISHU_WEBHOOK 未配置，fail-closed 不投递"
        return False
    try:
        payload = json.dumps(
            {
                "msg_type": "text",
                "content": {
                    "text": f"[{record['kind']}] {record['title']}\n{record['body']}"
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with safe_safe_urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:  # fail-closed：网络失败不抛，只记原因
        record["meta"]["error"] = f"feishu 投递失败: {e}"
        return False


# ── 对外 API ─────────────────────────────────────────────────────
def send(
    mid: str,
    kind: str,
    title: str,
    body: str = "",
    channel: str = "site",
    meta: dict | None = None,
) -> dict:
    """纪要投递：校验通道 → 写账本 → 经通道投递 → 回写状态。"""
    _profile(mid)
    if kind not in _KINDS:
        raise Reject(f"非法纪要类型: {kind!r}（可选: {'/'.join(_KINDS)}）")
    if not isinstance(title, str) or not title.strip():
        raise Reject("标题不可为空")
    _check_channels(mid, channel)
    rec = {
        "id": f"min_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}",
        "mid": mid,
        "kind": kind,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": title.strip()[:100],
        "body": str(body or "")[:5000],
        "channel": channel,
        "status": "pending",
        "meta": dict(meta or {}),
    }
    d = _load(MINUTES_PATH, {"items": []})
    d["items"].append(rec)
    _atomic_save(MINUTES_PATH, d)
    ok = DELIVERY_CHANNELS[channel](rec)
    rec["status"] = "delivered" if ok else "failed"
    _atomic_save(MINUTES_PATH, d)
    return rec


def list_minutes(mid: str | None = None) -> list[dict]:
    items = _load(MINUTES_PATH, {"items": []})["items"]
    if mid:
        _profile(mid)
        items = [r for r in items if r.get("mid") == mid]
    return items


def inbox(mid: str | None = None) -> list[dict]:
    """站内收件箱投影（portal 只读用）。"""
    items = _load(INBOX_PATH, {"items": []})["items"]
    return [r for r in items if not mid or r.get("mid") == mid]


# ── CLI ──────────────────────────────────────────────────────────
def _main(argv=None):
    ap = argparse.ArgumentParser(
        prog="minutes", description="会议纪要投递管线（通道插件化）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("send", help="投递纪要")
    p.add_argument("mid")
    p.add_argument("kind", choices=_KINDS)
    p.add_argument("title")
    p.add_argument("--channel", default="site", choices=sorted(DELIVERY_CHANNELS))
    p.add_argument("--body-file", default=None, help="正文从文件读（缺省空正文）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(
        fn=lambda a: [
            send(
                a.mid,
                a.kind,
                a.title,
                body=(
                    Path(a.body_file).read_text(encoding="utf-8") if a.body_file else ""
                ),
                channel=a.channel,
            )
        ],
        cmd_name="send",
    )
    p = sub.add_parser("list")
    p.add_argument("mid", nargs="?", default=None)
    p.set_defaults(fn=lambda a: list_minutes(a.mid))
    sub.add_parser("inbox").set_defaults(fn=lambda a: inbox())
    args = ap.parse_args(argv)
    try:
        print(json.dumps(args.fn(args), ensure_ascii=False, indent=1))
        return 0
    except Reject as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
