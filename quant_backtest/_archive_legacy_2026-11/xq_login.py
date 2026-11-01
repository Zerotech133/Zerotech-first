# -*- coding: utf-8 -*-
"""雪球账号登录 → 登录态 token 灌池（xq_login.py，P1-4 账号池补强）。

背景：匿名预热链（xq_browser.py）产出的匿名 xq_a_token 配额低、
400016 概率高；真实账号登录态 token 配额高、更稳。本脚本用
Playwright headful Edge 完成账号密码登录，导出 xq_a_token 灌入
config/xq_tokens.json（TokenPool 轮换自动生效）。

用法（py311，pysnowball 所在解释器）：
  python xq_login.py --probe
      只打开首页 dump 登录入口候选元素 + 截图，不登录；
  python xq_login.py --user <phone> --password xxx --name tok-u-150
      登录 → 验活 → 灌池（同名替换）；
选项：
  --headless   无头模式（默认 headful：登录风控更宽松、遇滑块可人工辅助）
  --no-pool    只验活不写池
  --wait-human N   滑块人工辅助窗口秒数（默认 120）

流程：开首页过 WAF（commit 判例）→ 点登录入口 → 切「账号密码登录」
→ fill 手机号/密码 → 提交 → 滑块自动拖 2 次 → 失败进人工窗口 →
cookie 出现登录态特征 → 导出 → token_probe 验活 → TokenPool.save。

铁律：token/密码绝不完整打印（掩码）；密码仅经 CLI 传入不落盘；
失败路径截图 C:/Temp/xq_login_fail_*.png 供排查。
"""

import argparse
import logging
import asyncio
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HOME_URL = "https://xueqiu.com/"
CHALLENGE_WAIT_MS = 10_000
LOGIN_FEATURE_COOKIES = {"xq_r_token", "xqat", "xq_id", "xq_or_token"}
# token 值形态：40 位 hex（与匿名同形，靠登录态伴生 cookie 区分）

FAIL_SHOT_DIR = os.path.join(HERE, "tmp")
os.makedirs(FAIL_SHOT_DIR, exist_ok=True)


def _mask(tok: str) -> str:
    return "%s…(len=%d)" % (tok[:4], len(tok)) if tok else "(empty)"


def _looks_waf(text: str) -> bool:
    if not text:
        return False
    head = text[:3000]
    return "aliyun_waf" in head or ("renderData" in head and "_waf_" in head)


# ── 浏览器流程（async）──────────────────────────────────────
async def _probe_async(headless: bool) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=headless)
        ctx = await browser.new_context(
            user_agent=UA, locale="zh-CN", viewport={"width": 1366, "height": 768}
        )
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="commit", timeout=30_000)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "xq_login.py", exc
            )
        await page.wait_for_timeout(CHALLENGE_WAIT_MS)
        print(
            "== WAF 判定：body 前 3KB %s"
            % ("像挑战页" if _looks_waf(await page.content()) else "正常")
        )
        cands = await page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('a,button,span,div').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (t.length <= 10 && /登录|登陆/.test(t)) {
                        out.push({tag: el.tagName, cls: el.className,
                                  text: t, href: el.href || ''});
                    }
                });
                const pws = [...document.querySelectorAll('input[type=password]')]
                    .map(e => ({name: e.name, ph: e.placeholder,
                                visible: !!(e.offsetWidth || e.offsetHeight)}));
                return {cands: out.slice(0, 15), pws};
            }"""
        )
        print("== 登录入口候选（前 15）：")
        for c in cands["cands"]:
            print(
                "   <%s cls=%r text=%r href=%r>"
                % (c["tag"], str(c["cls"])[:60], c["text"], c["href"][:60])
            )
        print("== 页面内 password 输入框：%s" % (cands["pws"] or "无"))
        _shot = os.path.join(FAIL_SHOT_DIR, "xq_probe_home.png")
        await page.screenshot(path=_shot)
        print("== 截图 %s" % _shot)
        await browser.close()


def _mask_cookies(cookies: list[dict]) -> list[str]:
    names = sorted(c["name"] for c in cookies if isinstance(c, dict))
    return names


async def _login_async(
    user: str, password: str, headless: bool, wait_human: int, mode: str = "pwd"
) -> tuple[str, list[dict]]:
    """登录并返回 (xq_a_token, cookies)。mode=pwd 账号密码+极验；
    mode=qr 二维码扫码（用户雪球 App 扫窗内码，免滑块免验证码）。"""
    from playwright.async_api import async_playwright

    _safe_name = "u" + re.sub(r"\D", "", user)[-4:]  # 账号脱敏（后 4 位数字）
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=headless)
        ctx = await browser.new_context(
            user_agent=UA, locale="zh-CN", viewport={"width": 1366, "height": 768}
        )
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="commit", timeout=30_000)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "xq_login.py", exc
            )
        await page.wait_for_timeout(CHALLENGE_WAIT_MS)
        pre = {
            c["name"]: c.get("value", "")
            for c in await ctx.cookies()
            if isinstance(c, dict)
        }

        # 1) 登录面板可能已嵌在页面（落地页右侧：tab=账号密码登录），
        #    否则（普通首页形态）先点登录入口
        panel = False
        try:
            await page.get_by_text("账号密码登录", exact=False).first.wait_for(
                state="visible", timeout=8000
            )
            panel = True
            print("[login] 落地页形态：登录面板已在页面")
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "xq_login.py", exc
            )
        if not panel:
            opened = False
            for sel in (
                "a[class*='btn--login']",
                "a:has-text('立即登录/注册')",
                "a:has-text('登录')",
                "button:has-text('登录')",
            ):
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=20_000)
                    await loc.click(timeout=4000)
                    opened = True
                    print("[login] 入口点击成功：%s" % sel)
                    break
                except Exception:
                    continue
            if not opened:
                try:
                    await page.screenshot(
                        path=os.path.join(
                            FAIL_SHOT_DIR, "xq_login_noentry_%d.png" % int(time.time())
                        )
                    )
                    waf = _looks_waf(await page.content())
                    print("[login] 入口未现（WAF 挑战页=%s），截图已存" % waf)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "silent-except %s: %s", "xq_login.py", exc
                    )
                raise RuntimeError("登录入口未找到（首页无「登录」可点）")
            await page.wait_for_timeout(2500)

        # 2) mode=qr：二维码扫码早退路径（密码流程零改动）
        if mode == "qr":
            for txt in ("二维码登录", "扫码登录"):
                try:
                    loc = page.get_by_text(txt, exact=False).first
                    await loc.click(timeout=2500)
                    print("[login] 已切到二维码 tab：%s" % txt)
                    break
                except Exception:
                    continue
            await page.wait_for_timeout(2500)
            await page.screenshot(
                path=os.path.join(
                    FAIL_SHOT_DIR, "xq_login_qr_%d.png" % int(time.time())
                )
            )
            print("[login]" + "!" * 60)
            print("[login] ⚠ 请用雪球 App 扫描屏幕上浏览器窗口中的二维码")
            print("[login]   确认后脚本自动检测登录态并继续（窗口 %ds）" % wait_human)
            print("[login]" + "!" * 60)
            got = await _wait_login_feature(ctx, pre, time.time() + wait_human)
            if not got:
                await page.screenshot(
                    path=os.path.join(
                        FAIL_SHOT_DIR, "xq_login_fail_%d.png" % int(time.time())
                    )
                )
                raise RuntimeError("扫码登录未成功（窗口超时）")
            return await _export_login(ctx, pre, _safe_name, browser)

        # 2p) 切「账号密码登录」tab（若有验证码 tab）
        for txt in ("账号密码登录", "密码登录", "账号密码"):
            try:
                loc = page.get_by_text(txt, exact=False).first
                await loc.click(timeout=2500)
                print("[login] 切到密码登录 tab：%s" % txt)
                break
            except Exception:
                continue
        await page.wait_for_timeout(1200)

        # 3) fill 手机号 + 密码（:visible 过滤——切 tab 后两 tab 各有一个
        #    phone 框，不过滤会命中隐藏的那个）
        phone = page.locator(
            "input[type=tel]:visible, input[placeholder*='手机']:visible, "
            "input[name*='phone']:visible, "
            "input[name*='account']:visible"
        ).first
        await phone.fill(user, timeout=8000)
        pwd = page.locator("input[type=password]:visible").first
        await pwd.fill(password, timeout=8000)
        print("[login] 账号密码已填入")

        # 4) 勾协议：圆圈是纯样式元素（无 input），物理点击其几何中心，
        #    前后对比 className 验证勾选状态
        try:
            info = await page.evaluate(
                """() => {
                    const lb = [...document.querySelectorAll('label,div,span')]
                        .find(l => /阅读并同意/.test(l.textContent || '')
                              && (l.textContent || '').length < 40);
                    if (!lb) return null;
                    const circle = lb.querySelector('span,i,div,em,b') || lb;
                    const r = circle.getBoundingClientRect();
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                            cls: String(circle.className || '').slice(0, 80)};
                }"""
            )
            if not info:
                print("[login] 协议元素未找到")
            else:
                print("[login] 协议圆圈 class 前：%s" % info["cls"])
                await page.mouse.click(info["x"], info["y"])
                await page.wait_for_timeout(500)
                cls2 = await page.evaluate(
                    """() => {
                        const lb = [...document.querySelectorAll('label,div,span')]
                            .find(l => /阅读并同意/.test(l.textContent || '')
                                  && (l.textContent || '').length < 40);
                        const c = lb && lb.querySelector('span,i,div,em,b');
                        return c ? String(c.className || '').slice(0, 80) : '?';
                    }"""
                )
                print(
                    "[login] 协议圆圈 class 后：%s %s"
                    % (cls2, "✓ 已勾选" if cls2 != info["cls"] else "⚠ 未变化")
                )
        except Exception as e:
            print("[login] 协议点击失败：%s" % str(e)[:60])

        # 5) 提交：大蓝按钮「登 录」（tab 是 a 元素不会误中）
        submitted = False
        for sel in (
            "button:has-text('登')",
            "[class*='login'] button, [class*='Login'] button",
            "text=登 录",
            "[class*=submit]",
        ):
            try:
                loc = page.locator(sel).last
                await loc.click(timeout=4000)
                submitted = True
                print("[login] 提交点击成功：%s" % sel)
                break
            except Exception:
                continue
        if not submitted:
            raise RuntimeError("登录提交按钮未找到")

        # 6) 等登录结果（自动窗口 30s；滑块自动拖 + 人工窗口）
        deadline = time.time() + 30
        got = await _wait_login_feature(ctx, pre, deadline)
        if not got:
            # dump 页面错误提示（toast/tip），区分「拦截提示」vs「滑块」
            try:
                tips = await page.evaluate(
                    """() => [...document.querySelectorAll(
                            '[class*=error],[class*=toast],[class*=tip],[class*=alert]')]
                        .filter(e => e.offsetWidth)
                        .map(e => (e.textContent || '').trim())
                        .filter(t => t && t.length < 60)"""
                )
                if tips:
                    print("[login] 页面提示：%s" % tips[:5])
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "xq_login.py", exc
                )
        if not got and not headless:
            # 滑块自动拖（仅当页面确有滑块/验证元素——实测拦截多为
            # 「手机号码验证」短信弹窗，无滑块元素时跳过白等）
            has_slider = False
            try:
                has_slider = await page.locator(
                    "[class*=slider],[class*=aptcha],[class*=puzzle]"
                ).first.is_visible(timeout=1500)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "xq_login.py", exc
                )
            if has_slider:
                for i in range(3):
                    print("[login] 极验自动尝试 %d/3" % (i + 1))
                    ok = await _try_slider(page)
                    got = await _wait_login_feature(ctx, pre, time.time() + 6)
                    if got:
                        break
                    if ok and i < 2:  # 拖了但没过 → 刷新换图
                        try:
                            await page.locator("[class*=geetest_refresh]").first.click(
                                timeout=2000
                            )
                            await page.wait_for_timeout(1200)
                        except Exception as exc:
                            logging.getLogger(__name__).warning(
                                "silent-except %s: %s", "xq_login.py", exc
                            )
            if not got:
                print("[login]" + "!" * 60)
                print("[login] ⚠ 请人工辅助（窗口 %ds）：" % wait_human)
                print(
                    "[login]   若弹出「手机号码验证」→ 查收该账号手机"
                    "的短信验证码，在浏览器窗口输入并提交；"
                )
                print("[login]   若是滑块 → 手动拖动。")
                print("[login]   完成后脚本自动检测登录态并继续，无需其他操作。")
                print("[login]" + "!" * 60)
                got = await _wait_login_feature(ctx, pre, time.time() + wait_human)
        if not got:
            await page.screenshot(
                path=os.path.join(
                    FAIL_SHOT_DIR, "xq_login_fail_%d.png" % int(time.time())
                )
            )
            raise RuntimeError("登录未成功（未见登录态 cookie）")

        return await _export_login(ctx, pre, _safe_name, browser)


async def _export_login(
    ctx, pre: dict, _safe_name: str, browser
) -> tuple[str, list[dict]]:
    """登录成功后共用导出：提取 xq_a_token + 全套 cookie 备份落盘。"""
    cookies = await ctx.cookies()
    tok = next(
        (c["value"] for c in cookies if c["name"] == "xq_a_token" and c.get("value")),
        "",
    )
    if not tok:
        raise RuntimeError("登录态 cookie 存在但无 xq_a_token")
    print("[login] 登录成功：cookie %d 项，xq_a_token=%s" % (len(cookies), _mask(tok)))
    # 全套登录态 cookie 备份落盘（token 失效时可人工回灌，免重登）
    try:
        bak = os.path.join(HERE, "config", "xq_login_cookies_%s.json" % _safe_name)
        with open(bak + ".tmp", "w", encoding="utf-8") as f:
            json.dump(
                {"saved_at": time.time(), "user": _safe_name, "cookies": cookies},
                f,
                ensure_ascii=False,
            )
        os.replace(bak + ".tmp", bak)
        print(
            "[login] 登录态 cookie 已备份 config/xq_login_cookies_%s.json" % _safe_name
        )
    except Exception as e:
        print("[login] cookie 备份失败（忽略）：%s" % str(e)[:60])
    await browser.close()
    return tok, cookies


async def _wait_login_feature(ctx, pre_map: dict, deadline: float) -> bool:
    """轮询 cookie：登录态特征 cookie 的**值发生变化**（或新出现）→ True。

    判例：匿名访问时雪球即下发 xq_a_token/xqat/xq_id 伴生 cookie，
    登录后是值更新而非名字新增——用名字差集检测恒 False（实测判例
    2026-10-17：截图已登录态但脚本误报失败）。xq_a_token 值变化即
    登录成功（匿名 token → 登录 token 值必然不同）。
    """
    while time.time() < deadline:
        try:
            cur = {
                c["name"]: c.get("value", "")
                for c in await ctx.cookies()
                if isinstance(c, dict)
            }
        except Exception:
            await asyncio.sleep(2)
            continue
        if cur.get("xq_a_token") and cur.get("xq_a_token") != pre_map.get("xq_a_token"):
            return True
        for k in LOGIN_FEATURE_COOKIES:
            if k in cur and cur[k] != pre_map.get(k):
                return True
        await asyncio.sleep(2)
    return False


async def _geetest_gap(page) -> dict:
    """极验缺口检测：读 geetest_canvas_bg 像素，列亮度 8px 窗差分
    取最强边缘 = 缺口左缘（canvas 同源可 getImageData，无需 PIL）。"""
    return await page.evaluate(
        """() => {
            const c = document.querySelector('.geetest_canvas_bg');
            if (!c) return {err: 'no-canvas'};
            const ctx = c.getContext('2d');
            if (!ctx) return {err: 'no-ctx'};
            const w = c.width, h = c.height;
            const img = ctx.getImageData(0, 0, w, h).data;
            const colLum = new Array(w).fill(0);
            for (let x = 0; x < w; x++) {
                let s = 0;
                for (let y = 0; y < h; y++) {
                    const i = (y * w + x) * 4;
                    s += 0.299 * img[i] + 0.587 * img[i + 1]
                       + 0.114 * img[i + 2];
                }
                colLum[x] = s / h;
            }
            const sm = colLum.map((v, i) =>
                (colLum[Math.max(0, i - 1)] + v +
                 colLum[Math.min(w - 1, i + 1)]) / 3);
            let best = -1, bestDiff = 0;
            for (let x = 12; x < w * 0.9; x++) {
                const d = Math.abs(sm[x] - sm[x - 8]);
                if (d > bestDiff) { bestDiff = d; best = x; }
            }
            return {w: w, h: h, gapX: best, diff: bestDiff};
        }"""
    )


def _human_track(dist: float) -> list[float]:
    """人类拖动轨迹（相对位移序列）：连续速度模型——分相速度平滑趋近
    （加速 25% → 巡航 → 末端爬行 1.6px/帧），噪声速度扰动，过冲回拉。"""
    import random

    over = dist + random.uniform(3, 6)
    pts: list[float] = []
    pos, v = 0.0, 0.0
    while pos < over and len(pts) < 220:
        phase = pos / over
        vmax = (
            random.uniform(7, 10)
            if phase < 0.25
            else random.uniform(5, 7)
            if phase < 0.75
            else 1.6
        )
        v += (vmax - v) * 0.3 + random.uniform(-0.4, 0.4)
        v = max(0.8, v)
        pos = min(over, pos + v)
        pts.append(pos)
    pts.append(dist + 2)
    pts.append(dist - random.uniform(2, 3.5))
    return pts


async def _try_slider(page) -> bool:
    """极验拼图自动拖：canvas 缺口检测 → 比例换算拖距 → 仿真轨迹。
    弹窗若是「网络不给力」错误面板，先点「重试」重开拼图。"""
    try:
        btn = page.locator(".geetest_slider_button").first
        try:
            await btn.wait_for(state="visible", timeout=2500)
        except Exception:
            # 错误面板 → 点重试重开
            try:
                rst = page.get_by_text("重试", exact=False).first
                if await rst.is_visible(timeout=1200):
                    await rst.click(timeout=2000)
                    print("[login] 错误面板已点重试，重开拼图")
                    await page.wait_for_timeout(1500)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "xq_login.py", exc
                )
        box = await btn.bounding_box(timeout=4000)
        if not box:
            print("[login] 无 geetest_slider_button")
            return False
        gap = await _geetest_gap(page)
        if not gap or gap.get("err") or gap.get("gapX", -1) < 0:
            print(
                "[login] 缺口检测失败：%s" % json.dumps(gap, ensure_ascii=False)[:120]
            )
            return False
        bg = page.locator(".geetest_canvas_bg").first
        bbox = await bg.bounding_box(timeout=3000)
        if not bbox:
            return False
        scale = bbox["width"] / gap["w"]  # canvas 像素 → 显示 px
        dist = gap["gapX"] * scale
        print(
            "[login] 缺口 x=%d/%dpx diff=%.1f 比例=%.2f → 拖距 %.0fpx"
            % (gap["gapX"], gap["w"], gap["diff"], scale, dist)
        )
        if gap["diff"] < 5:
            print("[login] 边缘差过弱（%.1f<5），疑似无缺口特征" % gap["diff"])
            return False
        import random

        sx = box["x"] + box["width"] / 2
        sy = box["y"] + box["height"] / 2
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        yoff = 0.0
        for px in _human_track(dist):
            yoff = yoff * 0.9 + random.uniform(-1, 1)
            await page.mouse.move(sx + px, sy + yoff)
            await page.wait_for_timeout(random.randint(12, 20))
        await page.wait_for_timeout(random.randint(120, 260))
        await page.mouse.up()
        print("[login] 滑块轨迹拖放完成（缺口 %.0fpx）" % dist)
        return True
    except Exception as e:
        print("[login] 滑块自动拖失败：%s" % str(e)[:60])
        return False


# ── 验活 + 灌池（同步）──────────────────────────────────────
def verify_token(tok: str) -> dict:
    """token_probe 验活（pysnowball 走 XUEQIUTOKEN 环境变量）。"""
    os.environ["XUEQIUTOKEN"] = tok
    from xueqiu_common import token_probe

    return token_probe("SH600519")


def _is_market_ip_blacklist_error(probe: dict) -> bool:
    """仅识别 token_probe 明确标注的行情域 IP 黑名单错误。"""
    if not isinstance(probe, dict):
        return False
    detail = probe.get("error")
    if not isinstance(detail, dict):
        return False
    domain = str(detail.get("domain") or detail.get("host") or "").lower()
    code = str(detail.get("code") or detail.get("error_code") or "").upper()
    reason = str(detail.get("reason") or detail.get("type") or "").upper()
    return (
        domain in {"stock.xueqiu.com", "行情域"}
        and code in {"403", "HTTP_403", "IP_BLACKLIST"}
        and reason in {"IP_BLACKLIST", "IP_BLOCKED", "BLACKLIST"}
    )


def save_to_pool(name: str, tok: str) -> bool:
    """同名替换 / 追加 → 原子落盘。"""
    from xq_tokens import load_tokens
    from xq_tokens import TokenEntry

    pool = load_tokens()
    for e in pool.entries:
        if e.name == name:
            e.token = tok
            e.fail_count = 0
            e.cooldown_until = 0.0
            pool.save()
            print("[pool] 同名替换：%s → %s" % (name, _mask(tok)))
            return True
    pool.entries.append(TokenEntry(name=name, token=tok))
    pool.save()
    print("[pool] 追加：%s → %s（池内 %d 条）" % (name, _mask(tok), len(pool.entries)))
    return True


def main() -> int:
    # 输出实时化（管道/重定向全缓冲会让中断丢全部日志，判例 2026-10-17）；
    # 强制 utf-8（GBK console 遇特殊字符死进程判例）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "xq_login.py", exc)
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--name", required=False, help="token 池条目名（如 tok-u-150）")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-pool", action="store_true")
    ap.add_argument(
        "--qr", action="store_true", help="扫码登录（雪球 App 扫窗内二维码，免滑块）"
    )
    ap.add_argument("--wait-human", type=int, default=180)
    a = ap.parse_args()

    if a.probe:
        asyncio.run(_probe_async(headless=a.headless))
        return 0
    if a.qr:
        if not a.name:
            print("扫码模式需要 --name")
            return 1
        try:
            tok, _cookies = asyncio.run(
                _login_async(
                    "", "", headless=a.headless, wait_human=a.wait_human, mode="qr"
                )
            )
        except Exception as e:
            print("[FATAL] 扫码登录失败：%s" % str(e)[:200])
            return 2
    else:
        if not (a.user and a.password and a.name):
            print("需要 --user --password --name（或 --probe/--qr）")
            return 1
        try:
            tok, _cookies = asyncio.run(
                _login_async(
                    a.user,
                    a.password,
                    headless=a.headless,
                    wait_human=a.wait_human,
                    mode="pwd",
                )
            )
        except Exception as e:
            print("[FATAL] 登录失败：%s" % str(e)[:200])
            return 2

    tp = verify_token(tok)
    print(
        "[verify] token_probe ok=%s valid=%s latency=%dms err=%s"
        % (tp["ok"], tp.get("valid"), tp["latency_ms"], tp["err"] or "-")
    )
    if not (tp["ok"] and tp.get("valid")):
        if _is_market_ip_blacklist_error(tp):
            print("[verify] 行情域 IP 黑名单错误，继续入池")
        else:
            print("[FATAL] token_probe 未明确通过，不灌池")
            return 3
    if not a.no_pool:
        save_to_pool(a.name, tok)
    print("[done] %s 验活通过%s" % (a.name, "" if a.no_pool else "，已入池"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
