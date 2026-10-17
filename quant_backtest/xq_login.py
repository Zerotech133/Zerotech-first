# -*- coding: utf-8 -*-
"""雪球账号登录 → 登录态 token 灌池（xq_login.py，P1-4 账号池补强）。

背景：匿名预热链（xq_browser.py）产出的匿名 xq_a_token 配额低、
400016 概率高；真实账号登录态 token 配额高、更稳。本脚本用
Playwright headful Edge 完成账号密码登录，导出 xq_a_token 灌入
config/xq_tokens.json（TokenPool 轮换自动生效）。

用法（py311，pysnowball 所在解释器）：
  python xq_login.py --probe
      只打开首页 dump 登录入口候选元素 + 截图，不登录；
  python xq_login.py --user 15071133950 --password xxx --name tok-u-150
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
import asyncio
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HOME_URL = "https://xueqiu.com/"
CHALLENGE_WAIT_MS = 10_000
LOGIN_FEATURE_COOKIES = {"xq_r_token", "xqat", "xq_id", "xq_or_token"}
# token 值形态：40 位 hex（与匿名同形，靠登录态伴生 cookie 区分）

FAIL_SHOT_DIR = "C:/Temp"


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
        browser = await p.chromium.launch(
            channel="msedge", headless=headless)
        ctx = await browser.new_context(
            user_agent=UA, locale="zh-CN",
            viewport={"width": 1366, "height": 768})
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        await page.wait_for_timeout(CHALLENGE_WAIT_MS)
        print("== WAF 判定：body 前 3KB %s"
              % ("像挑战页" if _looks_waf(await page.content()) else "正常"))
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
            }""")
        print("== 登录入口候选（前 15）：")
        for c in cands["cands"]:
            print("   <%s cls=%r text=%r href=%r>"
                  % (c["tag"], str(c["cls"])[:60], c["text"], c["href"][:60]))
        print("== 页面内 password 输入框：%s" % (cands["pws"] or "无"))
        await page.screenshot(path="C:/Temp/xq_probe_home.png")
        print("== 截图 C:/Temp/xq_probe_home.png")
        await browser.close()


def _mask_cookies(cookies: list[dict]) -> list[str]:
    names = sorted(c["name"] for c in cookies if isinstance(c, dict))
    return names


async def _login_async(user: str, password: str, headless: bool,
                       wait_human: int) -> tuple[str, list[dict]]:
    """登录并返回 (xq_a_token, cookies)。失败 raise RuntimeError。"""
    from playwright.async_api import async_playwright
    _safe_name = "u" + re.sub(r"\D", "", user)[-4:]   # 账号脱敏（后 4 位数字）
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge", headless=headless)
        ctx = await browser.new_context(
            user_agent=UA, locale="zh-CN",
            viewport={"width": 1366, "height": 768})
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        await page.wait_for_timeout(CHALLENGE_WAIT_MS)
        pre = {c["name"]: c.get("value", "")
               for c in await ctx.cookies() if isinstance(c, dict)}

        # 1) 登录面板可能已嵌在页面（落地页右侧：tab=账号密码登录），
        #    否则（普通首页形态）先点登录入口
        panel = False
        try:
            await page.get_by_text("账号密码登录", exact=False).first.wait_for(
                state="visible", timeout=8000)
            panel = True
            print("[login] 落地页形态：登录面板已在页面")
        except Exception:
            pass
        if not panel:
            opened = False
            for sel in ("a[class*='btn--login']",
                        "a:has-text('立即登录/注册')",
                        "a:has-text('登录')", "button:has-text('登录')"):
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
                        path="C:/Temp/xq_login_noentry_%d.png"
                             % int(time.time()))
                    waf = _looks_waf(await page.content())
                    print("[login] 入口未现（WAF 挑战页=%s），截图已存" % waf)
                except Exception:
                    pass
                raise RuntimeError("登录入口未找到（首页无「登录」可点）")
            await page.wait_for_timeout(2500)

        # 2) 切「账号密码登录」tab（若有验证码 tab）
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
            "input[name*='account']:visible").first
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
                }""")
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
                    }""")
                print("[login] 协议圆圈 class 后：%s %s"
                      % (cls2, "✓ 已勾选" if cls2 != info["cls"] else "⚠ 未变化"))
        except Exception as e:
            print("[login] 协议点击失败：%s" % str(e)[:60])

        # 5) 提交：大蓝按钮「登 录」（tab 是 a 元素不会误中）
        submitted = False
        for sel in ("button:has-text('登')",
                    "[class*='login'] button, [class*='Login'] button",
                    "text=登 录", "[class*=submit]"):
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
                        .filter(t => t && t.length < 60)""")
                if tips:
                    print("[login] 页面提示：%s" % tips[:5])
            except Exception:
                pass
        if not got and not headless:
            # 滑块自动拖（仅当页面确有滑块/验证元素——实测拦截多为
            # 「手机号码验证」短信弹窗，无滑块元素时跳过白等）
            has_slider = False
            try:
                has_slider = await page.locator(
                    "[class*=slider],[class*=aptcha],"
                    "[class*=puzzle]").first.is_visible(timeout=1500)
            except Exception:
                pass
            if has_slider:
                for i in range(2):
                    print("[login] 检测到滑块元素，尝试自动拖动 %d/2"
                          % (i + 1))
                    ok = await _try_slider(page)
                    got = await _wait_login_feature(ctx, pre,
                                                    time.time() + 8)
                    if got:
                        break
            if not got:
                print("[login]" + "!" * 60)
                print("[login] ⚠ 请人工辅助（窗口 %ds）：" % wait_human)
                print("[login]   若弹出「手机号码验证」→ 查收该账号手机"
                      "的短信验证码，在浏览器窗口输入并提交；")
                print("[login]   若是滑块 → 手动拖动。")
                print("[login]   完成后脚本自动检测登录态并继续，无需其他操作。")
                print("[login]" + "!" * 60)
                got = await _wait_login_feature(ctx, pre,
                                                time.time() + wait_human)
        if not got:
            await page.screenshot(
                path="C:/Temp/xq_login_fail_%d.png" % int(time.time()))
            raise RuntimeError("登录未成功（未见登录态 cookie）")

        cookies = await ctx.cookies()
        tok = next((c["value"] for c in cookies
                    if c["name"] == "xq_a_token" and c.get("value")), "")
        if not tok:
            raise RuntimeError("登录态 cookie 存在但无 xq_a_token")
        print("[login] 登录成功：cookie %d 项，xq_a_token=%s"
              % (len(cookies), _mask(tok)))
        # 全套登录态 cookie 备份落盘（token 失效时可人工回灌，免重登）
        try:
            bak = os.path.join(HERE, "config",
                               "xq_login_cookies_%s.json" % _safe_name)
            with open(bak + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(), "user": _safe_name,
                           "cookies": cookies}, f, ensure_ascii=False)
            os.replace(bak + ".tmp", bak)
            print("[login] 登录态 cookie 已备份 config/xq_login_cookies_%s.json"
                  % _safe_name)
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
            cur = {c["name"]: c.get("value", "")
                   for c in await ctx.cookies() if isinstance(c, dict)}
        except Exception:
            await asyncio.sleep(2)
            continue
        if cur.get("xq_a_token") and cur.get("xq_a_token") != \
                pre_map.get("xq_a_token"):
            return True
        for k in LOGIN_FEATURE_COOKIES:
            if k in cur and cur[k] != pre_map.get(k):
                return True
        await asyncio.sleep(2)
    return False


async def _try_slider(page) -> bool:
    """滑块自动拖：以「拖动滑块完成拼图」文本为锚定轨道，几何抓柄
    （圆柄必在轨道左端 ~22px 中心），物理轨迹拖动 + 诊断输出。"""
    try:
        geo = await page.evaluate(
            """() => {
                const out = {iframes: [...document.querySelectorAll('iframe')]
                             .map(f => (f.src || '').slice(0, 80))};
                const all = [...document.querySelectorAll('*')]
                    .filter(e => /拖动滑块完成拼图/.test(e.textContent || ''));
                out.bar_n = all.length;
                if (!all.length) { out.stage = 'no-bar'; return out; }
                const leaf = all.find(e =>
                    (e.textContent || '').length < 30) || all[all.length - 1];
                out.leaf = {tag: leaf.tagName,
                            cls: String(leaf.className || '').slice(0, 60)};
                const track = leaf.closest('div[class]') ||
                              leaf.parentElement;
                if (!track) { out.stage = 'no-track'; return out; }
                const tr = track.getBoundingClientRect();
                out.stage = 'ok';
                out.track = {cls: String(track.className || '').slice(0, 60),
                             x: tr.x, y: tr.y, w: tr.width, h: tr.height};
                return out;
            }""")
        print("[login] 滑块诊断：%s" % json.dumps(geo, ensure_ascii=False)[:300])
        if not isinstance(geo, dict) or geo.get("stage") != "ok":
            return False
        tr = geo["track"]
        sx = tr["x"] + 22                      # 圆柄中心：轨道左端 ~22px
        sy = tr["y"] + tr["h"] / 2
        dist = max(120, tr["w"] - 55)
        print("[login] 几何抓柄 (%.0f,%.0f) 拖距 %.0fpx" % (sx, sy, dist))
        import random
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        steps = 30
        for i in range(steps):
            r = (i + 1) / steps
            ease = 1 - (1 - r) ** 2
            await page.mouse.move(sx + dist * ease,
                                  sy + random.uniform(-2, 2))
            await page.wait_for_timeout(random.randint(8, 25))
        await page.mouse.move(sx + dist + 3, sy)
        await page.wait_for_timeout(120)
        await page.mouse.move(sx + dist - 2, sy)   # 过冲回弹
        await page.mouse.up()
        print("[login] 滑块轨迹拖放完成")
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
    print("[pool] 追加：%s → %s（池内 %d 条）"
          % (name, _mask(tok), len(pool.entries)))
    return True


def main() -> int:
    # 输出实时化（管道/重定向全缓冲会让中断丢全部日志，判例 2026-10-17）；
    # 强制 utf-8（GBK console 遇特殊字符死进程判例）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                                line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                                line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--name", required=False,
                    help="token 池条目名（如 tok-u-150）")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-pool", action="store_true")
    ap.add_argument("--wait-human", type=int, default=180)
    a = ap.parse_args()

    if a.probe:
        asyncio.run(_probe_async(headless=a.headless))
        return 0
    if not (a.user and a.password and a.name):
        print("需要 --user --password --name（或 --probe）")
        return 1

    try:
        tok, _cookies = asyncio.run(_login_async(
            a.user, a.password, headless=a.headless,
            wait_human=a.wait_human))
    except Exception as e:
        print("[FATAL] 登录失败：%s" % str(e)[:200])
        return 2

    tp = verify_token(tok)
    print("[verify] token_probe ok=%s valid=%s latency=%dms err=%s"
          % (tp["ok"], tp.get("valid"), tp["latency_ms"], tp["err"] or "-"))
    if not (tp["ok"] and tp.get("valid")):
        print("[FATAL] token 验活未通过，不灌池")
        return 3
    if not a.no_pool:
        save_to_pool(a.name, tok)
    print("[done] %s 验活通过%s" % (a.name, "" if a.no_pool else "，已入池"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
