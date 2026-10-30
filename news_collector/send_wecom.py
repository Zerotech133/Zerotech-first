# -*- coding: utf-8 -*-
"""企业微信群机器人推送通道（微信外网可达，与飞书并行）。

key 来源（优先级）：
  1. news_config.json 的 "wecom_webhook"（完整 webhook URL 或裸 key）
  2. news_collector/wecom_webhook.txt（内容为完整 URL 或裸 key）
未配置时所有函数直接返回 False 并打印提示（绝不阻塞主链路）。

用法:
  python send_wecom.py "测试文本"          # 文本
  python send_wecom.py --image <png> [说明]  # 图片
"""

import base64
import hashlib
import json
import os
import sys

import requests
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "news_config.json")
KEY_PATH = os.path.join(HERE, "wecom_webhook.txt")
API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="


def get_webhook():
    """完整 webhook URL；未配置返回 None。"""
    url = ""
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            url = (json.load(f) or {}).get("wecom_webhook") or ""
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/send_wecom.py", exc)
    if not url and os.path.exists(KEY_PATH):
        with open(KEY_PATH, encoding="utf-8") as f:
            url = f.read().strip()
    if not url:
        return None
    if url.startswith("qyapi.weixin.qq.com") or not url.startswith("http"):
        url = API + url.strip("?").split("key=")[-1]
    return url


def _post(payload, timeout=10):
    url = get_webhook()
    if not url:
        print(
            "WECOM_SKIP: 未配置 wecom_webhook（news_config.json 或 wecom_webhook.txt）"
        )
        return False
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        return (r.json() or {}).get("errcode") == 0
    except Exception as e:
        print("WECOM_FAIL %s" % str(e)[:120])
        return False


def send_text(content):
    return _post({"msgtype": "text", "text": {"content": content[:2000]}})


def send_markdown(content):
    """markdown（企微子集语法，4096 字节内自动截断）。"""
    raw = content.encode("utf-8")
    if len(raw) > 4000:
        content = raw[:4000].decode("utf-8", "ignore") + "\n…（截断）"
    return _post({"msgtype": "markdown", "markdown": {"content": content}})


def send_image(png_path):
    """本地 PNG → base64+md5 直发（企微机器人原生支持）。"""
    try:
        with open(png_path, "rb") as f:
            data = f.read()
        if len(data) > 8 * 1024 * 1024:
            print("WECOM_SKIP: image >8MB")
            return False
        return _post(
            {
                "msgtype": "image",
                "image": {
                    "base64": base64.b64encode(data).decode(),
                    "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
                },
            }
        )
    except Exception as e:
        print("WECOM_FAIL %s" % str(e)[:120])
        return False


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--image":
        ok = send_image(sys.argv[2])
        if ok and len(sys.argv) > 3:
            send_text(sys.argv[3])
    else:
        ok = send_text(sys.argv[1] if len(sys.argv) > 1 else "wechat ok")
    print("WECOM_%s" % ("OK" if ok else "SKIP" if not get_webhook() else "FAIL"))
