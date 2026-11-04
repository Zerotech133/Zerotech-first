# -*- coding: utf-8 -*-
"""数据接入出口层：每个源一条明确的出网路径，不靠继承环境变量。

背景（2026-11-05 实测）：本机/沙箱会注入 http(s)_proxy，requests 默认
trust_env=True 会把**国内 A 股源**也塞进代理。实测差异：

    源                直连        走 env 代理
    api.tushare.pro   1.1s        12.3s
    d.10jqka.com.cn   0.96s       超时(6.7s)
    push2.eastmoney   连接重置     ProxyError

结论：出口路径是数据质量的一部分，必须按源声明、实测择优、落盘记忆，
不能靠全局环境变量。

策略值：
    direct  强制直连      trust_env=False，清空 proxies
    env     跟随环境变量  trust_env=True（企业/沙箱统一出口）
    local   强制本地代理  默认 127.0.0.1:7890（快安），可用环境变量覆盖
    auto    实测择优      direct → env → local，胜出路径写入 state/egress.json

本模块只做网络与格式归一，不关心业务语义；任何异常都转成结构化结果，
不抛出到调用方（调用方据 ok/error 决策）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

VALID_POLICIES = ("direct", "env", "local", "auto")
DEFAULT_LOCAL_PROXY = os.environ.get("DATA_HUB_LOCAL_PROXY", "http://127.0.0.1:7890")
EGRESS_TTL_SECONDS = 24 * 3600

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
EGRESS_FILE = STATE_DIR / "egress.json"


class FetchResult(dict):
    """字典形态的结果，附带 ok/error 语义；便于直接 json 序列化落盘。"""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def session(policy: str = "direct", *, retries: int = 2) -> requests.Session:
    """按策略构造会话。policy 非法直接报错，避免静默回退到错误出口。"""
    if policy not in VALID_POLICIES:
        raise ValueError(f"未知出口策略: {policy!r}，合法值 {VALID_POLICIES}")
    sess = requests.Session()
    if policy == "direct":
        sess.trust_env = False
        sess.proxies = {}
    elif policy == "env":
        sess.trust_env = True
    else:  # local / auto 的显式段
        sess.trust_env = False
        sess.proxies = {"http": DEFAULT_LOCAL_PROXY, "https": DEFAULT_LOCAL_PROXY}
    sess.headers.update({"User-Agent": UA, "Accept": "*/*"})
    adapter = HTTPAdapter(max_retries=retries, pool_connections=8, pool_maxsize=8)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


def _load_egress() -> dict:
    try:
        return json.loads(EGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_egress(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, EGRESS_FILE)


def cached_egress(source_id: str) -> str | None:
    """取 24h 内实测过的胜出路径；过期或缺失返回 None。"""
    rec = _load_egress().get(source_id)
    if not rec:
        return None
    if time.time() - float(rec.get("at", 0)) > EGRESS_TTL_SECONDS:
        return None
    return rec.get("policy")


def remember_egress(source_id: str, policy: str, *, latency_ms: int) -> None:
    data = _load_egress()
    data[source_id] = {"policy": policy, "latency_ms": latency_ms, "at": time.time()}
    _save_egress(data)


def fetch(
    url: str,
    *,
    policy: str = "direct",
    timeout: tuple[float, float] = (6.0, 15.0),
    headers: dict | None = None,
    params: dict | None = None,
    method: str = "GET",
    expect_json: bool = False,
    source_id: str | None = None,
) -> FetchResult:
    """单次取数。policy='auto' 时实测择优并记忆。

    返回字段：ok / status / latency_ms / bytes / text|json / policy / error
    """
    tried: list[str] = []
    policies = ("direct", "env", "local") if policy == "auto" else (policy,)
    if policy == "auto" and source_id:
        hit = cached_egress(source_id)
        if hit:
            policies = (hit,)

    last: FetchResult = FetchResult(ok=False, error="not_attempted")
    for pol in policies:
        tried.append(pol)
        # auto 是"换路试探"，不该在单条路上重试：否则 3 条路 × 重试 = 60s+
        sess = session(pol, retries=0 if policy == "auto" else 2)
        started = time.time()
        try:
            resp = sess.request(
                method,
                url,
                timeout=timeout,
                headers=headers or {},
                params=params,
                allow_redirects=True,
            )
            latency = int((time.time() - started) * 1000)
            body = resp.text or ""
            result = FetchResult(
                ok=200 <= resp.status_code < 400,
                status=resp.status_code,
                latency_ms=latency,
                bytes=len(body.encode("utf-8", "ignore")),
                policy=pol,
                url=resp.url,
            )
            if expect_json:
                try:
                    result["json"] = resp.json()
                except Exception:  # noqa: BLE001
                    result["ok"] = False
                    result["error"] = "invalid_json"
            else:
                result["text"] = body[:20000]
            if result["ok"] and policy == "auto" and source_id:
                remember_egress(source_id, pol, latency_ms=latency)
            if result["ok"]:
                return result
            last = result
            if resp.status_code in (401, 403, 404):
                # 出口无关的拒绝，换路径无意义
                break
        except Exception as exc:  # noqa: BLE001
            last = FetchResult(
                ok=False,
                status=None,
                latency_ms=int((time.time() - started) * 1000),
                bytes=0,
                policy=pol,
                error=f"{type(exc).__name__}:{str(exc)[:120]}",
            )
        finally:
            sess.close()
    last["tried"] = tried
    return last


def head(url: str, *, policy: str = "direct") -> FetchResult:
    return fetch(url, policy=policy, method="HEAD", expect_json=False)
