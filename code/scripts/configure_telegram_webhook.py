"""Telegram webhook 配置/检查运维脚本（Phase C Task 3 Step 5）。

用法：
    uv run python scripts/configure_telegram_webhook.py           # 只读：打印 redacted 状态
    uv run python scripts/configure_telegram_webhook.py --apply   # setWebhook 并验证

行为：
- 读 settings（telegram_bot_token / telegram_webhook_secret / telegram_webhook_public_url）；
- 拒绝非 https:// 的 webhook URL（本机 HTTP / 自签名证书不满足正式 webhook）；
- 默认只调用 getWebhookInfo 打印当前/期望状态；--apply 才调 setWebhook
  （url + secret_token）；
- 验证 getWebhookInfo.url 与配置一致且 last_error_message 为空，否则 exit 1。

安全：**绝不打印 bot token 原文**（redact 中间为 ***）；secret 同样只显示长度。
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

from zhice_os_v2.config.settings import get_settings


def _redact(secret: str) -> str:
    """中间打码：仅保留首尾各 4 位（过短则全 ***）。绝不输出原文。"""
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _proxy() -> str | None:
    """与 telegram_notifier 同口径的代理（国内直连被墙）。"""
    settings_proxy = getattr(get_settings(), "telegram_proxy", "")
    return (
        settings_proxy
        or os.getenv("TELEGRAM_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or None
    )


def _call(token: str, method: str, *, json_body: dict | None = None) -> dict:
    """调 Telegram Bot API；非 2xx/ok=False 抛 RuntimeError（消息不含 token）。"""
    url = f"https://api.telegram.org/bot{token}/{method}"
    proxy = _proxy()
    if proxy:
        with httpx.Client(proxy=proxy, timeout=15.0) as client:
            resp = client.post(url, json=json_body) if json_body else client.post(url)
    else:
        resp = httpx.post(url, json=json_body, timeout=15.0) if json_body else httpx.post(url, timeout=15.0)
    if resp.status_code != 200:
        raise RuntimeError(f"{method} HTTP {resp.status_code}")
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method} 返回 ok=false: {data.get('description', 'unknown error')}")
    return data.get("result", {})


def main() -> int:
    parser = argparse.ArgumentParser(description="配置/检查 Telegram bot webhook")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际调用 setWebhook（默认只读检查 getWebhookInfo）",
    )
    args = parser.parse_args()

    settings = get_settings()
    token = settings.telegram_bot_token
    secret = settings.telegram_webhook_secret
    url = settings.telegram_webhook_public_url

    if not token or not secret or not url:
        print("配置不完整：需要 TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET / TELEGRAM_WEBHOOK_PUBLIC_URL")
        return 1
    if not url.startswith("https://"):
        print(f"拒绝：webhook URL 必须为 https://（当前 {url}）")
        return 1

    print(f"bot token: {_redact(token)}")
    print(f"webhook secret: {'*' * len(secret)}（{len(secret)} 字符，未打印）")
    print(f"desired webhook url: {url}")
    if args.apply:
        try:
            _call(token, "setWebhook", json_body={"url": url, "secret_token": secret})
            print("setWebhook: ok")
        except RuntimeError as exc:
            print(f"setWebhook 失败: {exc}")
            return 1

    try:
        info = _call(token, "getWebhookInfo")
    except RuntimeError as exc:
        print(f"getWebhookInfo 失败: {exc}")
        return 1

    current = info.get("url", "")
    last_error = info.get("last_error_message") or ""
    pending = info.get("pending_update_count", 0)
    print(f"current webhook url: {current or '(未设置)'}")
    print(f"pending updates: {pending}")

    ok = True
    if current != url:
        print("不一致：getWebhookInfo.url 与配置的 telegram_webhook_public_url 不同")
        ok = False
    if last_error:
        print(f"不一致：last_error_message 非空: {last_error}")
        ok = False
    if ok:
        print("OK: webhook 配置与期望一致且无投递错误")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
