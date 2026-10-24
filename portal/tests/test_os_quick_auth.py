# -*- coding: utf-8 -*-
"""QUICKFIX1：8734 反代下本机页面 POST 判定单测。"""
import os
import sys

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402


def h(**kw):
    return {k: v for k, v in kw.items()}


def test_direct_8091_is_local():
    assert A.is_local_page_request(h()) is True


def test_8734_proxy_with_local_origin_and_docker_gateway():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "172.17.0.1",
           "Origin": "http://127.0.0.1:8734"})) is True


def test_8734_proxy_with_local_origin_and_lan_ip():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "192.168.204.12",
           "Origin": "http://localhost:8734"})) is True


def test_8734_proxy_with_empty_real_ip():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "Origin": "http://127.0.0.1:8734"})) is True


def test_foreign_origin_rejected():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "172.17.0.1",
           "Origin": "http://evil.example"})) is False


def test_proxy_without_origin_rejected():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "172.17.0.1"})) is False


def test_public_ip_rejected():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "8.8.8.8",
           "Origin": "http://127.0.0.1:8734"})) is False

def test_private_lan_entry_origin_allowed():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "192.168.204.12",
           "Origin": "http://192.168.204.12:8734"})) is True


def test_public_entry_origin_rejected():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "192.168.204.12",
           "Origin": "http://evil.example:8734"})) is False

def test_page_marker_header_allowed_even_without_origin():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "172.17.0.1",
           "X-OS-Page": "quick"})) is True


def test_page_marker_header_from_public_ip_rejected():
    assert A.is_local_page_request(h(
        **{"X-Portal-Proxy": "1", "X-Real-IP": "8.8.8.8",
           "X-OS-Page": "quick"})) is False
