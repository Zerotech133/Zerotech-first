# -*- coding: utf-8 -*-
"""卡片/消息里的移动端链接自动签发（2026-10-25，P1 后续批次）。

统一入口：所有飞书卡片按钮应调用 m_links.url_for(...)，不再手拼 URL。
开关与基地址（`portal/work/m_link.json`，可热改）：
  {"enabled": true, "base": "https://<公网域名>"}
  · enabled=false（默认）：url_for 返回 ""，生产者回退旧链接（零破坏）；
  · base 非空：覆盖 m_api 默认 base（局域网/公网切换只改这里）；
  · 环境变量 M_LINK_ENABLE=1 / M_BASE_URL 可临时覆盖（测试/应急）。

失败策略：任何异常返回 ""（绝不阻塞卡片发送）；需要强制签名时传 force=True。
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("M_LINK_CONFIG") or os.path.join(HERE, "work", "m_link.json")


def _load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def config():
    return _load_config()


def enabled():
    if os.environ.get("M_LINK_ENABLE") == "1":
        return True
    if os.environ.get("M_LINK_ENABLE") == "0":
        return False
    return bool(_load_config().get("enabled"))


def base():
    return (
        os.environ.get("M_BASE_URL") or str(_load_config().get("base") or "")
    ).rstrip("/")


def url_for(scene, code="", id="", tab=None, ttl=None, force=False, st=None):
    """返回带令牌的 /m 链接；未启用/失败返回 ""。

    st：可选分享归因令牌（share_tokens.gen 产物），追加 &st= 供页面埋点回落
    share_tokens.visit 账（与旧 picks.html?st= 同口径）。"""
    if not (force or enabled()):
        return ""
    try:
        import m_api

        extra = {"tab": tab} if tab else None
        url, _p = m_api.page_url(
            scene,
            code=str(code or ""),
            id=str(id or ""),
            ttl=ttl,
            extra=extra,
            base=base() or None,
        )
        if st:
            from urllib.parse import quote

            url += ("&st=" if "?" in url else "?st=") + quote(str(st), safe="")
        return url
    except Exception:  # noqa: BLE001 —— 链接签发失败不得影响卡片主链路
        return ""


def link_footer():
    """卡片脚注用：当前链接基地址（隐藏实现细节）。"""
    return base() or "lan"


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "enabled": enabled(),
                "base": base(),
                "picks": url_for("picks", force=True),
            },
            ensure_ascii=False,
        )
    )
