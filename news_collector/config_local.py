# -*- coding: utf-8 -*-
"""news_config 敏感字段本地覆盖（2026-10-25 SEC 收口 / EXEC-R14）。

背景：`news_config.json` 是 tracked 文件，2026-10-25 B10 基线归因发现以下字段曾
以真值入库（SEC-04 mustfix 6 条）：
  portal_url（内网门户地址）、doc_url / jev_doc_url / jev_base_url / sa_form_url
  （飞书资源标识）、jev_base_token（飞书 Base token，真凭据）。

收口后的取值顺序（前者优先）：
  1. 环境变量：NEWS_PORTAL_URL / NEWS_DOC_URL / JEV_DOC_URL / JEV_BASE_URL /
     JEV_BASE_TOKEN / NEWS_SA_FORM_URL
  2. 本地未跟踪私有文件：环境变量 NEWS_CONFIG_PRIVATE 指定，缺省
     C:\\项目\\_vault\\secrets\\news_config_private.json
  3. 仓库内 news_config.json（只应留空串占位；真值不允许再入库）

设计约束：**私有值绝不能被写回 tracked 的 news_config.json**。文档 URL 首次建成后
由 save_jev_doc_url() 写私有文件；消费方一律用 load_news_config() 拿合并后的视图。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "news_config.json")
_SENSITIVE = {
    "portal_url": "NEWS_PORTAL_URL",
    "doc_url": "NEWS_DOC_URL",
    "jev_doc_url": "JEV_DOC_URL",
    "jev_base_url": "JEV_BASE_URL",
    "jev_base_token": "JEV_BASE_TOKEN",
    "sa_form_url": "NEWS_SA_FORM_URL",
}


def private_path():
    """本地私有覆盖文件路径（未跟踪；默认在 _vault/secrets，可用 env 覆盖）。"""
    return os.environ.get(
        "NEWS_CONFIG_PRIVATE") or r"C:\项目\_vault\secrets\news_config_private.json"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _private_cfg():
    return _read_json(private_path())


def load_news_config():
    """读取 news_config.json，并用私有文件/环境变量覆盖敏感字段。"""
    cfg = _read_json(CFG_PATH)
    priv = _private_cfg()
    for key, env_name in _SENSITIVE.items():
        if priv.get(key):
            cfg[key] = priv[key]
        val = os.environ.get(env_name)
        if val:
            cfg[key] = val
    return cfg


def save_private_value(key, value):
    """把敏感字段写入私有覆盖文件（不写 tracked config）。"""
    if key not in _SENSITIVE:
        raise KeyError("非敏感字段不允许写私有文件: %s" % key)
    p = private_path()
    data = _read_json(p)
    data[key] = value
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return value


def save_jev_doc_url(url):
    """新建 JEV 文档后把 URL 写私有覆盖文件（2026-10-25 SEC 收口）。"""
    return save_private_value("jev_doc_url", url)
