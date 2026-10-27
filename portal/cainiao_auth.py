# -*- coding: utf-8 -*-
"""菜鸟(yudao) OAuth2 Bearer token 校验模块 —— X1 登录整合预置（2026-10-08）。

自包含、零新依赖（仅标准库）。portal_api.py 未接线前本模块不被任何现有
代码引用，等菜鸟侧 client 凭据（clientId/clientSecret）到位后按下面
「portal_api.py 接线点」三行插入即可启用。

架构定调（详见 portal/docs/2026-10-08_零界接入菜鸟登录_勘查.md）：
  零界 portal 作 OAuth2 资源服务器 —— 浏览器用户带
  Authorization: Bearer <accessToken> 访问，本模块调菜鸟
  POST /admin-api/system/oauth2/check-token 远程校验，结果进程内
  缓存 60s（不落盘）；X-Portal-Token 机器通道原样保留；菜鸟不可达时
  按 CAINIAO_AUTH_MODE 降级（默认 degraded = 放行本机可信域，保产线）。

配置（全部走环境变量，默认值见 _cfg）：
  CAINIAO_BASE_URL          默认 http://rookie.xtcloud-tech.com:43083
  CAINIAO_CHECK_TOKEN_PATH  默认 /admin-api/system/oauth2/check-token
  CAINIAO_TIMEOUT           默认 3（秒）
  CAINIAO_AUTH_MODE         enabled / degraded（默认）；degraded=菜鸟不可
                            达时按「本机可信域」放行，enabled=一律拒绝
  CAINIAO_CLIENT_ID / CAINIAO_CLIENT_SECRET  等凭据到位后设置，
                            以 HTTP Basic 头随 check-token 上送
  CAINIAO_TENANT_ID         芋道多租户：check-token 必须带 tenant-id 头
                            （缺头被租户过滤器拦截 code=400「请求的租户
                            标识未传递」，2026-10-09 真端点实证）；默认 1
                            （管理租户；菜鸟库另有 162=股票量化，须与
                            发 token 的租户一致）
  CAINIAO_LOCAL_TRUSTED     本机可信域来源清单（P1 判定源修复 2026-10-19）：
                            逗号分隔单 IP 或 CIDR 段，默认仅回环；反代链
                            以 X-Real-IP 对该清单判定（详见下方注释块）

安全注意：token 只在内存比对/缓存，绝不写日志、绝不落盘；
缓存 TTL 60s，进程重启即清空。

====================================================================
portal_api.py 接线点（等凭据到位后，仅此一处，注释形式示例）：

  在 _check_portal_token()（约 L4736，P0-1b 变更类 POST 鉴权分支）
  的「本机放行」之后、「X-Portal-Token 常量比对」之前插入：

      # --- X1 菜鸟登录校验（预置于 cainiao_auth.py）---
      import cainiao_auth
      if cainiao_auth.verify_request(self.headers, self.client_address[0],
                                     portal_token=_PORTAL_TOKEN).ok:
          return True

  三行语义：机器通道（X-Portal-Token）/ 本机回环 原语义放行；
  浏览器用户走 Bearer 远程校验；两者皆无则落到原常量比对逻辑，
  行为与现状完全兼容。校验失败时调用方按需返回 401，前端引导
  重登（P2 做 refresh-token 自动续期）。
====================================================================
"""

import base64
import hmac
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

DEFAULT_BASE_URL = "http://rookie.xtcloud-tech.com:43083"
DEFAULT_CHECK_TOKEN_PATH = "/admin-api/system/oauth2/check-token"
DEFAULT_TIMEOUT = 3.0
CACHE_TTL = 60.0  # 校验结果进程内缓存秒数（不落盘）

# 本机可信域（与 portal_api.py P0-1b 的 _is_local_req 判定口径一致）
_LOOPBACK = ("127.0.0.1", "::1")


# ===== P1 判定源修复（2026-10-19 老田批准，K-ORG-190 §7.2）=====
# 缺口：8091 绑回环、nginx 同机反代入站连接的 client_address 恒为回环，
# 原判定「remote_addr in 回环 → local_trusted 放行」对反代链恒真——
# X-Real-IP 局域网判定（portal_api.py _is_local_req）被架构性架空，
# 经反代的无 token 写请求实际能过闸门。
# 修复：受信来源判定不再依赖 client_address 回环兜底——
#   直连（无 X-Portal-Proxy 反代标记）→ 维持原 client_address 判定
#   （本机直连 8091 场景行为不变，老田日常零改造）；
#   反代链（X-Portal-Proxy 存在，nginx server 级覆写注入、客户端不可
#   伪造，见 portal/nginx.conf:26-27）→ 只认 X-Real-IP（缺头即不信任，
#   closed on failure），且须落受信来源清单。
# 受信来源清单配置化（环境变量，逐次读取便于测试/热改）：
#   CAINIAO_LOCAL_TRUSTED  逗号分隔的单 IP 或 CIDR 段（ipaddress 标准库
#                          解析，非法项忽略）；默认仅回环。若日后确需
#                          直信某内网网段（CIDR 写法入清单），设此
#                          环境变量即可，无需改代码。


def _trusted_sources() -> list:
    """本机可信域显式来源清单；默认仅回环。"""
    raw = (os.environ.get("CAINIAO_LOCAL_TRUSTED") or "").strip()
    if not raw:
        return list(_LOOPBACK)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _ip_trusted(ip: str, sources: list) -> bool:
    """ip 是否落受信来源清单（单 IP 精确匹配或 CIDR 段包含）。

    解析失败（畸形 IP / 非法 CIDR 项）一律不信任——closed on failure。"""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    for s in sources:
        try:
            if addr in ipaddress.ip_network(s, strict=False):
                return True
        except ValueError:
            continue  # 清单里的非法项忽略，不影响其余判定
    return False


@dataclass(frozen=True)
class VerifyResult:
    """鉴权结果。ok=True 放行；reason 记录判定来源/失败原因。"""

    ok: bool
    user_id: Optional[str] = None
    expiry: Optional[float] = None  # epoch 秒；来自菜鸟 expiresTime(ms)/1000
    reason: str = ""  # machine_channel / local_trusted /
    # cainiao_ok / degraded_* / invalid_token / ...

    def __bool__(self):  # 便于 if result: 写法
        return self.ok


def _cfg() -> dict:
    """每次调用读 env，便于测试与热改配置。"""
    return {
        "base_url": (os.environ.get("CAINIAO_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        ),
        "path": os.environ.get("CAINIAO_CHECK_TOKEN_PATH") or DEFAULT_CHECK_TOKEN_PATH,
        "timeout": float(os.environ.get("CAINIAO_TIMEOUT") or DEFAULT_TIMEOUT),
        "mode": (os.environ.get("CAINIAO_AUTH_MODE") or "degraded").strip().lower(),
        "client_id": (os.environ.get("CAINIAO_CLIENT_ID") or "").strip(),
        "client_secret": (os.environ.get("CAINIAO_CLIENT_SECRET") or "").strip(),
        "tenant_id": (os.environ.get("CAINIAO_TENANT_ID") or "1").strip(),
    }


# ===== 进程内校验缓存：{token: (VerifyResult, monotonic_ts)}，线程安全 =====
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(token: str) -> Optional[VerifyResult]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(token)
        if hit and now - hit[1] < CACHE_TTL:
            return hit[0]
        if hit:
            del _cache[token]
    return None


_CACHE_MAX = 2000  # 兜底上限：防御随机 token 灌缓存（正常租户远达不到）


def _cache_put(token: str, result: VerifyResult) -> None:
    # 只缓存权威结论（菜鸟明确判定的有效/无效）；网络降级结论不缓存，
    # 下次请求重试，菜鸟恢复后立即回到正常路径。
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            now = time.monotonic()
            # 先清过期项；仍超限则淘汰最旧（dict 保插入序）
            for k in [k for k, (_, ts) in _cache.items() if now - ts >= CACHE_TTL]:
                del _cache[k]
            while len(_cache) >= _CACHE_MAX:
                del _cache[next(iter(_cache))]
        _cache[token] = (result, time.monotonic())


def _http_check_token(token: str, cfg: dict) -> dict:
    """调菜鸟 check-token，返回解析后的 JSON dict。

    走 ProxyHandler({}) 直连：本机系统代理会劫持 127.0.0.1 与外网请求
    （历史坑），必须显式空代理。失败抛 urllib.error.HTTPError /
    URLError / socket.timeout / ValueError（响应非 JSON）。
    """
    url = cfg["base_url"] + cfg["path"]
    body = urllib.parse.urlencode({"token": token}).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cfg.get("tenant_id"):
        # 芋道多租户过滤器：缺 tenant-id 头直接 400，到不了 OAuth2 校验
        headers["tenant-id"] = cfg["tenant_id"]
    if cfg["client_id"] and cfg["client_secret"]:
        basic = base64.b64encode(
            ("%s:%s" % (cfg["client_id"], cfg["client_secret"])).encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = "Basic " + basic
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with opener.open(req, timeout=cfg["timeout"]) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _map_response(payload: dict) -> VerifyResult:
    """映射 yudao CommonResult：code==0 且 data 含 userId 判有效。"""
    data = payload.get("data") or {}
    if payload.get("code") == 0 and data:
        expiry_ms = data.get("expiresTime")
        expiry = (float(expiry_ms) / 1000.0) if expiry_ms else None
        uid = data.get("userId")
        return VerifyResult(
            True,
            user_id=str(uid) if uid is not None else None,
            expiry=expiry,
            reason="cainiao_ok",
        )
    return VerifyResult(False, reason="invalid_token")


def verify_bearer(token: str) -> VerifyResult:
    """校验菜鸟 accessToken。缓存命中直接返回（60s TTL）。"""
    token = (token or "").strip()
    if not token:
        return VerifyResult(False, reason="missing_token")

    cached = _cache_get(token)
    if cached is not None:
        return cached

    cfg = _cfg()
    try:
        result = _map_response(_http_check_token(token, cfg))
        _cache_put(token, result)
        return result
    except urllib.error.HTTPError as e:
        # 4xx = 菜鸟明确拒绝（token 无效/过期）→ 权威结论，缓存
        if 400 <= e.code < 500:
            result = VerifyResult(False, reason="invalid_token")
            _cache_put(token, result)
            return result
        return _on_unreachable("http_%d" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        # URLError/socket.timeout/JSON 解析失败 → 菜鸟不可达或响应异常
        return _on_unreachable(type(e).__name__)


_last_unreachable_warn = 0.0  # 不可达告警节流（60s）


def _on_unreachable(detail: str) -> VerifyResult:
    """菜鸟不可达：一律拒绝（K-STOCK-187 收窄，安全审计 F-005）。

    原 degraded 模式在此放行，但能走到本分支的调用方必然是非回环
    来源（本机可信域在 verify_request step2 已先行放行）——放行等于
    「远程依赖抖动=鉴权洞」。现改为 fail-closed：enabled/degraded 均
    拒绝，reason 标注模式；stderr 告警 60s 节流，提醒运维 auth 服务
    异常。本机产线流量不经此路径，不受影响。降级结论不缓存。"""
    mode = _cfg()["mode"]
    now = time.time()
    global _last_unreachable_warn
    if now - _last_unreachable_warn > 60:
        _last_unreachable_warn = now
        print(
            "WARN: cainiao auth 服务不可达，写鉴权 fail-closed "
            "(mode=%s): %s" % (mode, detail),
            file=sys.stderr,
        )
    return VerifyResult(
        False, reason="auth_unreachable_rejected(mode=%s):%s" % (mode, detail)
    )


def verify_request(headers, remote_addr, portal_token: str = "") -> VerifyResult:
    """组合鉴权入口（供 do_GET/do_POST 鉴权分支调用）。

    优先级：
      1. X-Portal-Token 与 portal_token 常量比对命中 → 机器通道放行
         （Agent/自动化脚本非人，不登录，原语义不动）
      2. 本机可信域放行（产线保底；P1 判定源修复后的显式来源判定，
         见下方 _trusted_sources/_ip_trusted 注释块）
      3. Authorization: Bearer <token> → verify_bearer 远程校验
      4. 其余 → 拒绝（调用方落到原有 X-Portal-Token 常量比对逻辑）
    headers 为 dict 或 http.client.HTTPMessage 等 .get() 对象均可。
    """

    def _h(name: str) -> str:
        try:
            return (headers.get(name) or "").strip()
        except Exception:
            return ""

    got = _h("X-Portal-Token")
    if portal_token and got and hmac.compare_digest(got, portal_token):
        return VerifyResult(True, reason="machine_channel")
    # 2. 本机可信域（P1 判定源修复 2026-10-19）：反代链下 client_address
    #    恒回环（8091 绑回环），不得作信任源；真实来源只认 X-Real-IP。
    #    直连 8091（无反代标记）维持 client_address 判定，行为不变。
    if _h("X-Portal-Proxy"):
        real_ip = _h("X-Real-IP")  # 反代链：缺 X-Real-IP 即不信任（closed on failure）
    else:
        real_ip = (remote_addr or "").strip()  # 直连：维持原判定源
    if _ip_trusted(real_ip, _trusted_sources()):
        return VerifyResult(True, reason="local_trusted")
    auth = _h("Authorization")
    if auth.lower().startswith("bearer "):
        return verify_bearer(auth[7:].strip())
    return VerifyResult(False, reason="no_credentials")
