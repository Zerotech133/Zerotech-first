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
    with op.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return {
        "content": data["choices"][0]["message"]["content"],
        "model": data.get("model", model),
        "usage": data.get("usage", {}),
    }


if __name__ == "__main__":
    print("目录内模型:", sorted(allowed_models()))
    try:
        out = chat("GLM-5.2", [{"role": "user", "content": "用一句话自我介绍"}])
        print("实测 OK:", out["content"][:120])
        print("usage:", out["usage"])
    except Exception as e:
        print("实测未通过:", e)
        raise SystemExit(1)
