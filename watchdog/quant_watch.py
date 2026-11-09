# -*- coding: utf-8 -*-
"""K-QUANT-003: 量化交易盯盘机器人 —— 独立进程.

职责: 读 trade_pool 里 status=active_watch 的持仓 → 拉实时价 →
  对比埋点 (stop_loss / take_profit) → 触发异动 → POST 8097 /api/quant/alert/{id}
  (8097 有飞书出口, push_feishu=true)

数据源: akshare stock_bid_ask_em (实时价)
频率: 每 30s 扫一轮所有 active_watch 持仓
去重: alert_triggered=FALSE 才检查 → 触发后 DB 状态变 watch_alert, 自动跳过
停机: Ctrl+C / kill

运行: python watchdog/quant_watch.py [--interval 30] [--once] [--dry-run]
  --interval   轮询间隔秒 (默认 30)
  --once       跑一轮就退 (smoke / cron 用)
  --dry-run    只打印检查结果, 不写 DB / 不推飞书

历史: 2026-10-19 K-QUANT-003 初版, 方案 A (8097 升级) 配套
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import logging
from typing import Optional

# ── 基础 ──────────────────────────────────────────────
WATCHDOG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WATCHDOG_DIR)  # watchdog/ → 项目根
sys.path.insert(0, os.path.join(ROOT, "portal"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("quant_watch")

# ── 回环禁代理 ────────────────────────────────────────
# 8097 hub 是 127.0.0.1：urllib 不豁免回环，只要 HTTP_PROXY 存在，打本机的请求也会被
# 送去代理。代理一严格，hub_call 全部失败 → 盯盘触发静默失效，日志里只看到
# "hub_call GET ... fail"，看不出是代理的锅。safe_urlopen 对回环走空 ProxyHandler。
# 上行已把 ROOT/portal 插入 sys.path，portal 树有一份同步副本。
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # pragma: no cover - portal 目录缺失时兜底，但会丢失回环豁免
    import urllib.request as _ur

    log.warning("safe_urlopen 不可用，回退原生 urlopen：回环请求可能被代理接管")

    def safe_urlopen(url, *args, **kwargs):
        return _ur.urlopen(url, *args, **kwargs)


# ── 配置 ──────────────────────────────────────────────
HUB_URL = os.environ.get("QUANT_HUB_URL", "http://127.0.0.1:8097")

def _hub_key() -> str:
    for env_k in ("X_PUSH_HUB_KEY", "PUSH_HUB_KEY"):
        v = os.environ.get(env_k)
        if v:
            return v
    try:
        sp = os.environ.get("PUSH_SECRETS_PATH",
                            os.path.join(ROOT, "portal", "work", "push_secrets.json"))
        with open(sp, encoding="utf-8") as f:
            return json.load(f).get("push_hub_key", "")
    except Exception:
        return ""


# ── HTTP 调 8097 ──────────────────────────────────────
def _hub_call(path: str, method: str = "GET",
              body: Optional[dict] = None) -> Optional[dict]:
    """调 8097 /api/quant/* 端点. 返回 response dict 或 None (异常)."""
    import urllib.request
    import urllib.error
    url = f"{HUB_URL}{path}"
    headers = {"X-Push-Hub-Key": _hub_key(),
               "Content-Type": "application/json"}
    try:
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with safe_urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("hub_call %s %s fail: %s", method, path, e)
        return None


# ── 实时价拉取 (akshare) ───────────────────────────────
_recent_price_cache: dict = {}   # code → (ts, price), 10s 缓存避免高频请求

def _get_price(code: str) -> Optional[float]:
    """拉单股实时价. code='600000.SH'. 返回 None 表示拉不到.

    数据源: 新浪实时行情原始接口 (东财 akshare stock_bid_ask_em 被公司防火墙挡).
    新浪代码格式: 600000.SH → sh600000, 000001.SZ → sz000001
    """
    import urllib.request as _req
    global _recent_price_cache
    now = time.time()
    if code in _recent_price_cache and (now - _recent_price_cache[code][0]) < 10:
        return _recent_price_cache[code][1]

    try:
        pure, market = code.split(".")
        prefix = "sh" if market.upper() == "SH" else "sz"
        url = f"https://hq.sinajs.cn/list={prefix}{pure}"
        req = _req.Request(url, headers={"Referer": "https://finance.sina.com.cn",
                                           "User-Agent": "Mozilla/5.0"})
        with _req.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk", errors="replace")
            # format: var hq_str_sz000001="平安银行,12.45,...,11.67,..."
            inside = raw.split('"')[1] if '"' in raw else ""
            parts = inside.split(",")
            if len(parts) > 3:
                price = float(parts[3])  # index 3 = 当前价
                if price > 0:
                    _recent_price_cache[code] = (now, price)
                    return price
    except Exception as e:
        log.debug("sina price fail %s: %s", code, e)
    return None


# ── 异动判定 ───────────────────────────────────────────
def check_breakout(row: dict, price: float) -> tuple[bool, str]:
    """检查一条 active_watch 持仓是否触发止损/止盈.

    返回 (triggered, detail_text). triggered=False → 持仓正常.
    """
    sl = float(row.get("stop_loss") or 0)
    tp = float(row.get("take_profit") or 0)
    entry = float(row.get("entry_price") or 0)
    code = row.get("stock_code", "?")

    if price <= 0 or (sl <= 0 and tp <= 0):
        return False, ""

    if sl > 0 and price <= sl:
        pct = (entry - price) / entry * 100 if entry > 0 else 0
        detail = (f"【止损触发】{code} 现价 {price:.2f} ≤ 止损 {sl:.2f}，"
                  f"距建仓 {entry:.2f} 亏损 {pct:.1f}%")
        return True, detail

    if tp > 0 and price >= tp:
        pct = (price - entry) / entry * 100 if entry > 0 else 0
        detail = (f"【止盈触发】{code} 现价 {price:.2f} ≥ 止盈 {tp:.2f}，"
                  f"距建仓 {entry:.2f} 盈利 {pct:.1f}%")
        return True, detail

    return False, ""


# ── 主循环 ──────────────────────────────────────────────
def scan_once(dry_run: bool = False) -> int:
    """跑一轮扫描. 返回本轮触发异动条数."""
    resp = _hub_call("/api/quant/active")
    if not resp or not resp.get("ok"):
        log.warning("active_watch list 拉取失败")
        return 0

    items = resp.get("items", [])
    if not items:
        log.info("active_watch: 空 (无持仓)")
        return 0

    log.info(f"本轮检查 {len(items)} 条 active_watch 持仓")
    triggered = 0

    for row in items:
        rid = row["id"]
        code = row.get("stock_code", "?")
        name = row.get("stock_name", "")
        sl = row.get("stop_loss", "?")
        tp = row.get("take_profit", "?")
        entry = row.get("entry_price", "?")

        price = _get_price(code)
        if price is None:
            log.info(f"  #{rid} {name}({code}) — 行情拉不到, 跳过")
            continue

        log.info(f"  #{rid} {name}({code}) 现价={price:.2f}  "
                 f"埋点=SL {sl}/TP {tp}  建仓={entry}")

        fired, detail = check_breakout(row, price)
        if not fired:
            continue

        triggered += 1
        if dry_run:
            log.warning(f"  [DRY-RUN] 触发: {detail}")
            continue

        # 触发 → POST /api/quant/alert/{id}, push_feishu=true
        ar = _hub_call(f"/api/quant/alert/{rid}", method="POST",
                       body={"detail": detail, "push_feishu": True})
        if ar and ar.get("ok"):
            fs = ar.get("feishu", {})
            log.warning(f"  ✅ 异动已推送 DB + 飞书: {detail}")
            log.warning(f"     feishu.ok={fs.get('ok')} why={fs.get('why', '')}")
        else:
            log.error(f"  ❌ alert POST 失败: {ar}")

    log.info(f"本轮完成: 触发 {triggered}/{len(items)}")
    return triggered


def main():
    ap = argparse.ArgumentParser(description="K-QUANT-003 量化交易盯盘机器人")
    ap.add_argument("--interval", type=int, default=30, help="轮询间隔秒 (默认 30)")
    ap.add_argument("--once", action="store_true", help="跑一轮就退")
    ap.add_argument("--dry-run", action="store_true", help="只打印, 不写 DB / 不推飞书")
    args = ap.parse_args()

    log.info(f"盯盘机器人启动 interval={args.interval}s dry_run={args.dry_run} hub={HUB_URL}")

    if args.once:
        scan_once(dry_run=args.dry_run)
        return

    try:
        while True:
            scan_once(dry_run=args.dry_run)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Ctrl+C, 退出")


if __name__ == "__main__":
    main()
