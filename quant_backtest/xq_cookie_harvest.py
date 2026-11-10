# -*- coding: utf-8 -*-
"""雪球登录 cookie 收割入库工具（一次性运维件，2026-11-08）。

用法：
  1. 浏览器登录 xueqiu.com
  2. F12 → Network → 刷新 → 任选一条 xueqiu.com 请求 → Request Headers
     → 复制完整 Cookie 值（一长串 name=value; name=value; ...）
  3. 运行：python xq_cookie_harvest.py "<粘贴整串>"
     或：  python xq_cookie_harvest.py --file cookie.txt

动作：解析 → 更新 config/xq_login_cookies_u.json（兼容历史格式）
      → 提取 xq_a_token（40 位 hex 校验）→ 灌 token 池（新条目，
      不动旧条目）→ 单点冒烟 /v5/stock/quote（SH000001）→ 打印掩码。
绝不完整打印任何 cookie/token 值（沿用池模块掩码纪律）。
"""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_PATH = os.path.join(HERE, "config", "xq_login_cookies_u.json")
TOKENS_PATH = os.path.join(HERE, "config", "xq_tokens.json")


def parse_cookie_header(raw: str) -> dict:
    """解析 Cookie 头串为 {name: value}；容忍换行与首尾空白。"""
    out = {}
    for chunk in raw.replace("\n", " ").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


def sync_env_local(tok: str) -> None:
    """把 xq_a_token 幂等写入 .env.local 的 XQ_A_TOKEN（三级链第 1 级）。

    2026-11-08 判例：XueqiuCrawl 不吃 token 池，三级链第 1 级（.env.local）
    才是 18:00 爬虫的活路；rookiepy 第 2 级在 Edge 157 App-Bound 下死路。
    锚点断言 count==1 后替换，无锚点则追加；备份 .env.local.bak-YYYYMMDD。
    """
    env_path = os.path.join(HERE, ".env.local")
    if not os.path.exists(env_path):
        print("[ENV] .env.local 不存在，跳过（XueqiuSync 池路径不受影响）")
        return
    with open(env_path, encoding="utf-8-sig") as f:
        src = f.read()
    lines = src.splitlines()
    idxs = [i for i, ln in enumerate(lines)
            if ln.strip().replace(" ", "").startswith("XQ_A_TOKEN=")]
    if len(idxs) > 1:
        print("[ENV] XQ_A_TOKEN 锚点异常（%d 处），跳过写入防误伤" % len(idxs))
        return
    new_ln = "XQ_A_TOKEN=" + tok
    if idxs:
        lines[idxs[0]] = new_ln
    else:
        lines.append(new_ln)
    bak = env_path + ".bak-" + datetime.now().strftime("%Y%m%d")
    if not os.path.exists(bak):
        with open(bak, "w", encoding="utf-8", newline="") as f:
            f.write(src)
    with open(env_path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    print("[ENV] .env.local XQ_A_TOKEN 已同步（掩码 %s...len=%d，备份 %s）"
          % (tok[:6], len(tok), os.path.basename(bak)))


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        raw = open(sys.argv[2], encoding="utf-8").read()
    elif len(sys.argv) >= 2:
        raw = sys.argv[1]
    else:
        print("用法：python xq_cookie_harvest.py \"<Cookie头整串>\" 或 --file <txt>")
        return 2

    cookies = parse_cookie_header(raw)
    tok = cookies.get("xq_a_token", "")
    if not (len(tok) == 40 and all(c in "0123456789abcdef" for c in tok.lower())):
        print("[FAIL] 未在 Cookie 串中找到合法 xq_a_token（40 位 hex）。")
        print("       拿到的键（仅名字）：%s" % ", ".join(sorted(cookies.keys())))
        print("       请确认你复制的是 xueqiu.com 请求的完整 Cookie 头，且已登录。")
        return 1

    # ① 更新登录 cookie 存档（兼容历史格式：cookies 为 list of {name,value}）
    doc = {
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": "harvest-login-cmdline",
        "cookies": [{"name": k, "value": v} for k, v in cookies.items()],
    }
    with open(LOGIN_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("[OK] 登录 cookie 存档已更新：%d 个 cookie" % len(cookies))

    # ② 灌 token 池：新条目追加，不动旧条目
    pool = json.load(open(TOKENS_PATH, encoding="utf-8")) if os.path.exists(TOKENS_PATH) else []
    name = "harvest-login-" + datetime.now().strftime("%m%d")
    pool = [e for e in pool if e.get("name") != name]
    pool.append({"name": name, "token": tok, "enabled": True})
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)
    print("[OK] token 池已灌入：%s（掩码 %s...len=%d），池共 %d 条"
          % (name, tok[:4], len(tok), len(pool)))

    # ②b 同步 .env.local（XueqiuCrawl 三级链第 1 级，2026-11-08 判例）
    sync_env_local(tok)

    # ③ 单点冒烟：/v5/stock/quote.json SH000001（用新 cookie 直连；
    #    2026-11-08 起 /v4/statuses 系端点已 404 下线，换行情端点）。
    #    退出码语义：入库成功即 0；冒烟失败仅 [WARN] 报告（入库动作
    #    本身已由 40 位 hex 校验背书，冒烟失败不应让调用方误判整体失败）。
    try:
        import requests
        sess = requests.Session()
        for k, v in cookies.items():
            sess.cookies.set(k, v)
        r = sess.get("https://stock.xueqiu.com/v5/stock/quote.json",
                     params={"symbol": "SH000001", "extend": "detail"}, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                              "Referer": "https://xueqiu.com/"})
        ok = r.status_code == 200 and '"data"' in r.text
        if ok:
            print("[SMOKE] /v5/quote HTTP=%d 有效JSON=True" % r.status_code)
        else:
            print("[WARN] /v5/quote HTTP=%d 有效JSON=False"
                  "（入库已完成；请人工复核登录态）" % r.status_code)
        return 0
    except Exception as e:
        print("[WARN] 冒烟异常：%s（入库已完成，仅探测失败）" % str(e)[:120])
        return 0


if __name__ == "__main__":
    sys.exit(main())
