# -*- coding: utf-8 -*-
"""详情看板镜像（飞书云文档）：卡片「📄 详情文档」按钮直达。

手机飞书内原生打开，不依赖家里网络/Tailscale —— 解决移动端
「按钮点不开、看不到详情」。每次异动提醒与 16:00 收盘记账后
整体 overwrite 刷新；文档 URL 走 config_local（env/私有覆盖），
真值不存 tracked config（2026-10-25 SEC 收口 / EXEC-R16）。
"""

import os
import subprocess

import runner
import logging

HERE = os.path.dirname(os.path.abspath(__file__))


def _fmt(v, nd=4):
    try:
        return ("%." + str(nd) + "g") % float(v)
    except Exception:
        return str(v)


def _pct(p):
    try:
        return "%+.2f%%" % (float(p) * 100)
    except Exception:
        return "—"


def build_md(led, alerts_md=None):
    rep = runner.CFG.get("report", {})
    th = float(rep.get("pos_alert_pct", 0.03)) * 100
    cd = int(rep.get("pos_alert_cooldown_min", 30))
    now = runner._now()  # 已是 "YYYY-MM-DD HH:MM" 字符串
    lines = [
        "# 量化实战·详情看板",
        "",
        "> 更新：%s（北京）· 盘中异动即时刷新 · 16:00 收盘记账整体刷新 · 纸面模拟，不构成投资建议"
        % now,
        "",
        "## 待执行（pending %d）" % len(led.get("pending") or []),
        "",
        "| 标的 | 市场 | 策略 | 信号日 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        "| %s | %s | %s | %s |"
        % (
            e.get("name"),
            e.get("market"),
            _sname(e.get("strategy")),
            e.get("sig_date", ""),
        )
        for e in (led.get("pending") or [])
    ]
    lines += [
        "",
        "## 持仓中（open %d）" % len(led.get("open") or []),
        "",
        "| 标的 | 市场 | 策略 | 买入日 | 买价 | 最新 | 盈亏 | 天数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        "| %s | %s | %s | %s | %s | %s | %s | %d |"
        % (
            e.get("name"),
            e.get("market"),
            _sname(e.get("strategy")),
            e.get("entry_date", ""),
            _fmt(e.get("entry_px")),
            _fmt(e.get("last_px")),
            _pct(e.get("pnl", 0)),
            e.get("days", 0),
        )
        for e in (led.get("open") or [])
    ]
    closed = (led.get("closed") or [])[-10:][::-1]
    lines += [
        "",
        "## 已平仓（近 %d 笔）" % len(closed),
        "",
        "| 标的 | 市场 | 策略 | 买入日 | 卖出日 | 买价 | 卖价 | 盈亏 | 天数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        "| %s | %s | %s | %s | %s | %s | %s | %s | %d |"
        % (
            e.get("name"),
            e.get("market"),
            _sname(e.get("strategy")),
            e.get("entry_date", ""),
            e.get("exit_date", ""),
            _fmt(e.get("entry_px")),
            _fmt(e.get("exit_px")),
            _pct(e.get("pnl", 0)),
            e.get("days", 0),
        )
        for e in closed
    ]
    if alerts_md:
        lines += ["", "## 最新异动提醒", ""] + list(alerts_md)
    lines += [
        "",
        "---",
        "触发阈值：浮盈/浮亏 ±%.0f%%（pos_alert_pct 可调）· 盘中每 30 分钟检查 · 同票同方向 %d 分钟冷却"
        % (th, cd),
    ]
    return "\n".join(lines)


def _sname(s):
    try:
        import report_page

        return report_page.OPT_HEAD.get(s, s)
    except Exception:
        return s


def refresh(alerts_md=None):
    """整体刷新看板文档；成功 True，失败 False（绝不影响卡片推送）。"""
    doc = runner.CFG.get("report", {}).get("doc_url")
    if not doc:
        return False
    tmp = os.path.join(HERE, "_board_tmp.md")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(build_md(runner._load_ledger(), alerts_md))
    try:
        r = subprocess.run(
            [
                "lark-cli",
                "docs",
                "+update",
                "--as",
                "user",
                "--doc",
                doc,
                "--command",
                "overwrite",
                "--doc-format",
                "markdown",
                "--content",
                "@./_board_tmp.md",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=HERE,
        )
        out = r.stdout or ""
        return '"ok": true' in out or '"ok":true' in out
    except Exception:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/doc_board.py", exc)


if __name__ == "__main__":
    import sys

    print("REFRESH", "OK" if refresh() else "FAIL")
    sys.exit(0)
