# -*- coding: utf-8 -*-
"""采集器 trust_env 护栏单测（2026-10-19，判例 2026-10-18 假403/假502）。

背景：WorkBuddy automation 宿主曾注入 HTTP(S)_PROXY 等 env，requests
默认 trust_env=True 会读入 → 采集「假快+假403/假502」。护栏要求所有
采集用 session 一律 trust_env=False；显式出口代理池（proxies.update）
不受影响。mock 层面验证，不发真实网络请求。
跑法：pytest tests/test_xq_crawl_trust_env.py -q
"""
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

import xueqiu_crawl


def test_crawler_session_trust_env_disabled():
    # XqCrawler 主采集 session 必须不信宿主 env（禁代理注入护栏）
    real_session = requests.Session
    with mock.patch.object(xueqiu_crawl.requests, "Session",
                           side_effect=real_session), \
         mock.patch.object(xueqiu_crawl.XqCrawler, "_warm",
                           return_value="fake-token"), \
         mock.patch.dict("os.environ", {"https_proxy": "http://127.0.0.1:9",
                                        "http_proxy": "http://127.0.0.1:9"}):
        crawler = xueqiu_crawl.XqCrawler(mf=mock.MagicMock())
    assert crawler.sess.trust_env is False
    # 显式出口代理池不因护栏失效（仍可显式 update proxies）
    crawler.sess.proxies.update({"https": "http://explicit-pool:8080"})
    assert crawler.sess.proxies["https"] == "http://explicit-pool:8080"


def test_requests_default_trust_env_is_true():
    # 对照锚：requests 原生 Session 默认信 env——护栏必须逐处显式关
    assert requests.Session().trust_env is True
