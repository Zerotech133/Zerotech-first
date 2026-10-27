# -*- coding: utf-8 -*-
"""E2E-08/09：飞书 bot 存活/心跳 + 私聊命令 mock 回环（不发真实消息）。"""
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
HB = pathlib.Path(r"C:\aios\portal\logs\feishu_bot_heartbeat.txt")
LOG = pathlib.Path(r"C:\aios\portal\logs\feishu_bot.log")


def _age_min(p):
    return (time.time() - p.stat().st_mtime) / 60.0


def test_e2e08_bot_heartbeat_fresh():
    assert HB.exists(), "heartbeat 文件不存在：%s" % HB
    age = _age_min(HB)
    assert age < 20, "heartbeat 过期 %.1f 分钟（bot 可能未运行）" % age
    assert LOG.exists(), "bot 日志不存在：%s" % LOG
    assert _age_min(LOG) < 20, "bot 日志 %.1f 分钟无更新" % _age_min(LOG)


def test_e2e09_feishu_private_chat_mock_loop():
    harness = REPO / "tests" / "e2e" / "feishu_mock_harness.py"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8", str(harness)],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    assert p.returncode == 0, p.stdout[-300:] + p.stderr[-300:]
    out = p.stdout.strip().splitlines()
    data = json.loads(out[-1]) if out else {}
    assert data.get("ok") is True, data
    assert data.get("reply"), "回执为空"
