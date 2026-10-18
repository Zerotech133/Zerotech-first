"""BEpusdt 网关客户端 + 签名（P3，只收 USDT-TRC20）。

签名算法（对齐 BEpusdt 官方 verifyPacket / getSign）：
1. 参数按 key 升序（ksort）；
2. 跳过空值与 `signature` 键；
3. 拼成 ``k=v&k2=v2...``；
4. 末尾直接追加 api_token（**不是** ``&token=``）；
5. md5 取小写十六进制。

verify() 用同法重算比对 payload 里的 `signature`（回调验签）。

网络：httpx 同步（项目已依赖，telegram_notifier 同款）；异常统一包装成
``BepusdtError`` 供 service 层转 HTTP 5xx/业务错误。
"""
from __future__ import annotations

import hashlib
import hmac

import httpx

_CREATE_PATH = "/api/v1/order/create-transaction"


class BepusdtError(RuntimeError):
    """BEpusdt 交互失败（网络/非 200/业务 status_code!=200）。"""


def _fmt_go(v: object) -> str:
    """复刻 Go fmt.Sprintf("%v", v)：BEpusdt 用它拼接待签串。

    关键差异：float 用最短表示且整数值不带 .0（Go %v: 99.0→"99"、9.9→"9.9"），
    否则 notify 回调验签会因 Python str(99.0)="99.0" 与 Go "99" 不一致而失败。
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = repr(v)
        if s.endswith(".0"):
            s = s[:-2]
        return s
    return str(v)


def sign(params: dict, token: str) -> str:
    """按 BEpusdt 规则对参数计算 md5 签名（小写十六进制）。"""
    parts = []
    for k in sorted(params.keys()):
        if k == "signature":
            continue
        v = params[k]
        if v is None or v == "":
            continue
        parts.append(f"{k}={_fmt_go(v)}")
    raw = "&".join(parts) + token
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify(params: dict, token: str) -> bool:
    """校验回调签名：重算 sign 与 payload['signature'] 常数时间比对。"""
    if not token:
        # 空 token 会让 md5 签名退化为纯参数哈希、任何人可伪造合法签名 → 直接拒绝。
        return False
    got = str(params.get("signature", ""))
    if not got:
        return False
    return hmac.compare_digest(sign(params, token), got)


class BepusdtClient:
    """BEpusdt 收银台下单客户端。

    base_url/token 缺失即视为未接支付 → create_transaction 抛 BepusdtError，
    由 service 层回 4xx（避免静默生成无地址订单）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._token = api_token or ""
        self._timeout = timeout

    def create_transaction(
        self,
        *,
        order_id: str,
        amount: str,
        notify_url: str,
        redirect_url: str,
        trade_type: str,
    ) -> dict:
        """下单，返回 {trade_id, actual_amount, token(收款地址), payment_url, expiration_time}。

        amount 传字符串（避免浮点误差）；BEpusdt 按金额碰撞 classic 匹配实付。
        """
        if not self._base_url or not self._token:
            raise BepusdtError("bepusdt.not_configured")
        params: dict = {
            "order_id": order_id,
            # BEpusdt v1.24+ 的 amount 是 float64；传字符串会被拒
            # （json: cannot unmarshal string into ... amount of type float64）。
            "amount": float(amount),
            # 定价本位是 USDT：BEpusdt 默认 fiat=CNY 会按汇率把金额换成很少的 USDT
            # （少收）。用 fiat=USD + rate=1 强制 1:1，令 amount 即为应付 USDT 数额。
            "fiat": "USD",
            "rate": "1",
            "notify_url": notify_url,
            "trade_type": trade_type,
        }
        if redirect_url:
            params["redirect_url"] = redirect_url
        params["signature"] = sign(params, self._token)
        url = f"{self._base_url}{_CREATE_PATH}"
        try:
            with httpx.Client(timeout=self._timeout) as c:
                resp = c.post(url, json=params)
        except httpx.HTTPError as e:  # 网络/超时
            raise BepusdtError(f"bepusdt.network_error: {e}") from e
        if resp.status_code != 200:
            raise BepusdtError(f"bepusdt.http_{resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            raise BepusdtError("bepusdt.bad_json") from e
        # BEpusdt 成功包：{status_code:200, message, data:{...}}
        if body.get("status_code") != 200:
            raise BepusdtError(
                f"bepusdt.reject: {body.get('message') or body.get('status_code')}"
            )
        data = body.get("data") or {}
        return {
            "trade_id": data.get("trade_id"),
            "order_id": data.get("order_id"),
            "amount": data.get("amount"),
            "actual_amount": data.get("actual_amount"),
            "token": data.get("token"),  # 收款地址
            "payment_url": data.get("payment_url"),
            "expiration_time": data.get("expiration_time"),  # unix 秒
        }
