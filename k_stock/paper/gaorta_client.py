# -*- coding: utf-8 -*-
"""K-STOCK-137 S-a · gaorta 模型通道（经理/操盘手角色共用）

硬约束：仅允许调用 k_stock/paper/models.json 目录内的模型（老田拍板）。
key 从环境变量 GAORTA_API_KEY 读取；未配置时 fail-closed 报清晰错误。

用法：
    from gaorta_client import chat
    reply = chat("GLM-5.2", [{"role": "user", "content": "你好"}])
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MODELS_FP = Path(__file__).resolve().parent / "models.json"
BASE_URL = "https://client.gaorta.com/v1/chat/completions"


def _catalog():
    return json.loads(MODELS_FP.read_text(encoding="utf-8"))


def allowed_models():
    """目录内模型名集合（唯一信任源）"""
    return {m["model"] for m in _catalog()["models"]}


def chat(model, messages, timeout=60, temperature=None):
    """调用 gaorta 通道。model 必须在目录内，否则拒绝（fail-closed）。"""
    if model not in allowed_models():
        raise ValueError(f"模型 {model!r} 不在 gaorta 目录内（见 models.json），已拒绝")
    key = os.environ.get("GAORTA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未配置 GAORTA_API_KEY 环境变量，无法调用 gaorta 通道")

    payload = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST",
    )
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # K-STOCK-147 P2-9：保留状态码/原因但包装为 RuntimeError，不裸抛 HTTPError
        raise RuntimeError(
            f"gaorta HTTP {e.code}: {e.reason}（模型 {model}）") from e
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        # K-STOCK-147 P2-9：缺 choices/choices[0].message 时给可排障上下文，替代裸 KeyError
        top_keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise RuntimeError(
            f"gaorta 响应结构异常：缺 choices[0].message.content"
            f"（模型 {model}），顶层键={top_keys}") from e
    return {
        "content": content,
        "model": data.get("model", model),
        "usage": data.get("usage", {}),
    }


def chat_for_hire(mid, source, messages, timeout=60):
    """运行层统一入口：按 manager 账本 active 雇佣记录取模后调用 gaorta 通道（K-STOCK-137j P1-1）。

    约定一（运行层纪律）：业务代码必须经 chat_for_hire 调用模型，禁止裸调 chat()。
    hire() 校验的是档案快照、chat() 校验的是调用实参，两者之间的窗口
    （账本 model 快照 vs 实际调用模型）只能靠本函数闭环。

    约定二（模型比对纪律，审查 P2-3）：账本记录与模型目录的比对必须精确匹配（==），
    禁止 normalize（casefold/strip 等）后比对——防止大小写/空白别名绕过白名单。

    无该 mid+source 的 active 雇佣记录时 raise RuntimeError（fail-closed，不降级、不猜测）。
    """
    import manager  # 延迟 import：manager 顶部亦引用 gaorta_client，避免循环依赖
    model = None
    for h in manager._load_hires().get("hires", []):
        if (h.get("manager") == mid and h.get("source") == source
                and h.get("status") == "active"):
            model = h.get("model")
            break
    if not model:
        raise RuntimeError(
            f"chat_for_hire 拒绝：无 {mid}×{source} 的 active 雇佣记录"
            f"（账本 {manager.HIRES_PATH}），运行层调用模型前必须先由经理雇佣建立（fail-closed）")
    # chat() 内部再过 allowed_models() 精确匹配白名单——双重校验第二道
    return chat(model, messages, timeout=timeout)


if __name__ == "__main__":
    print("目录内模型:", sorted(allowed_models()))
    try:
        out = chat("GLM-5.2", [{"role": "user", "content": "用一句话自我介绍"}])
        print("实测 OK:", out["content"][:120])
        print("usage:", out["usage"])
    except Exception as e:
        print("实测未通过:", e)
        raise SystemExit(1)
