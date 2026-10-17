"""Launch an isolated, user-driven browser profile without exposing credentials.

The script deliberately does not read cookies, storage state, headers, or tokens.
Authentication status must be obtained separately from the safe browser adapter.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Iterable

from knowledge.browser_fetcher import BrowserSessionHealth, BrowserSessionStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = PROJECT_ROOT / "data" / "browser_profile"
_ALLOWED_VIDEO_CHANNEL_HOSTS = frozenset({"channels.weixin.qq.com"})
_ALLOWED_VIDEO_CHANNEL_PATH_PREFIXES = ("/", "/platform", "/channels", "/web")
_ALLOWED_VIDEO_CHANNEL_TITLES = ("视频号", "微信视频号", "wechat channels", "video channels")


def _is_allowed_video_channel_page(url: str, title: str) -> bool:
    """Require official host plus a recognizable signed-in page surface."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or host not in _ALLOWED_VIDEO_CHANNEL_HOSTS:
        return False
    path = parsed.path or "/"
    path_match = any(path == prefix or path.startswith(prefix + "/") for prefix in _ALLOWED_VIDEO_CHANNEL_PATH_PREFIXES)
    title_match = any(marker in title for marker in _ALLOWED_VIDEO_CHANNEL_TITLES)
    return path_match and title_match


def visible_page_health(page: Any) -> BrowserSessionHealth:
    """Infer coarse health from visible page URL/title only.

    This helper never reads cookies, headers, local/session storage, or browser
    context state. It is suitable for injection as BrowserFetcher health_probe.
    """
    try:
        url = str(getattr(page, "url", "") or "").lower()
        title_value = page.title() if callable(getattr(page, "title", None)) else ""
        title = str(title_value or "").lower()
    except Exception:
        return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
    if any(marker in url or marker in title for marker in ("login", "signin", "auth")):
        return BrowserSessionHealth(status=BrowserSessionStatus.LOGIN_REQUIRED)
    if any(marker in url or marker in title for marker in ("captcha", "verify", "blocked", "forbidden")):
        return BrowserSessionHealth(status=BrowserSessionStatus.BLOCKED)
    if any(marker in url or marker in title for marker in ("expired", "session-expired")):
        return BrowserSessionHealth(status=BrowserSessionStatus.EXPIRED)
    if _is_allowed_video_channel_page(url, title):
        return BrowserSessionHealth(status=BrowserSessionStatus.AUTHENTICATED)
    return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)


def make_visible_page_health_probe(page: Any) -> Callable[[], BrowserSessionHealth]:
    """Build an injectable probe closure without retaining sensitive browser state."""
    return lambda: visible_page_health(page)


def visible_context_health(context: Any) -> BrowserSessionHealth:
    """Check the first visible page in a live context using only URL/title."""
    try:
        pages = getattr(context, "pages", ())
        page = next(iter(pages), None)
        if page is None:
            return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
        return visible_page_health(page)
    except Exception:
        return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)


def probe_live_context(context: Any, *, pages: Iterable[Any] | None = None) -> BrowserSessionHealth:
    """Return coarse health for a controlled live context; never export state."""
    try:
        candidates = pages if pages is not None else getattr(context, "pages", ())
        page = next(iter(candidates), None)
        return visible_page_health(page) if page is not None else BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
    except Exception:
        return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动隔离浏览器，供用户本人完成授权登录")
    parser.add_argument("url", help="用户明确提供的目标平台登录地址")
    parser.add_argument(
        "--profile",
        type=Path,
        default=DEFAULT_PROFILE,
        help="隔离 Profile 目录（默认 data/browser_profile）",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "缺少 Playwright 依赖，请使用隔离解释器安装："
            " C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pip install playwright"
        ) from exc

    profile = args.profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    print("session_health=login_required（请用户本人在打开的浏览器中完成合法登录）")
    print("安全边界：本入口不读取、不导出 Cookie/Token/Storage State；登录状态由安全适配器单独返回。")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            accept_downloads=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded")
            print("浏览器已启动。仅支持可见页面 coarse session_health 检查，不读取或导出凭证。")
            while True:
                command = input("输入 health 检查会话状态，输入 quit 关闭浏览器：").strip().lower()
                if command == "health":
                    health = visible_page_health(page)
                    print(f"session_health={health.status.value}")
                elif command in {"quit", "exit", ""}:
                    break
                else:
                    print("仅支持 health 或 quit。")
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
