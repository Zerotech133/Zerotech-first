# -*- coding: utf-8 -*-
"""quant_backtest config 本地私有覆盖（2026-10-25 SEC 收口 / EXEC-R16）。

背景：`quant_backtest/config.json` 是 tracked 文件，2026-10-25 B10 基线归因发现
`report.doc_url` / `report.premarket_doc_url` / `report.afterhours_doc_url`
3 个飞书文档 URL（4 处命中：doc_url 出现 2 次）以真值入库（SEC-04 mustfix）。

收口后取值顺序（前者优先）：
  1. 环境变量：QUANT_DOC_URL / QUANT_PREMARKET_DOC_URL / QUANT_AFTERHOURS_DOC_URL
  2. 本地未跟踪私有文件：环境变量 QUANT_CONFIG_PRIVATE 指定，缺省
     C:\\项目\\_vault\\secrets\\quant_config_private.json
  3. 仓库内 quant_backtest/config.json（doc URL 只应留空串占位）

裁决记录：`report.portal_url`（内网 192.168.0.3:8080）**非凭据**，2026-10-25
已裁决保留在 tracked config，不进本模块。

设计约束：**私有值绝不写回 tracked config.json**。`_doc_sync` 首次建文档后调用
save_report_url() 写私有文件；缺失时由 load_config() 明确打印告警（不静默）。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
_SENSITIVE = {
    "doc_url": "QUANT_DOC_URL",
    "premarket_doc_url": "QUANT_PREMARKET_DOC_URL",
    "afterhours_doc_url": "QUANT_AFTERHOURS_DOC_URL",
}
_warned = False


def private_path():
    """本地私有覆盖文件路径（未跟踪；默认在 _vault/secrets，可用 env 覆盖）。"""
    return os.environ.get(
        "QUANT_CONFIG_PRIVATE") or r"C:\项目\_vault\secrets\quant_config_private.json"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _private_cfg():
    return _read_json(private_path())


def load_config():
    """读取 config.json，并用私有文件/环境变量覆盖 report 下 3 个文档 URL。"""
    global _warned
    cfg = _read_json(CFG_PATH)
    report = cfg.get("report")
    if not isinstance(report, dict):
        report = {}
        cfg["report"] = report
    priv = _private_cfg()
    missing = []
    for key, env_name in _SENSITIVE.items():
        val = os.environ.get(env_name) or priv.get(key) or ""
        if val:
            report[key] = val
        else:
            report[key] = ""
            missing.append(key)
    if missing and not _warned:
        # 明确告警而非静默：缺 doc URL 时卡片不带「详情文档」链接（主卡仍推送）
        _warned = True
        print("QUANT_CFG WARN: 文档 URL 未配置（env 或 %s）：%s；"
              "卡片将不带文档链接" % (private_path(), ",".join(missing)))
    # pos_card.buttons 里「详情文档」按钮是 report.doc_url 的派生副本：tracked 里留空占位，
    # 这里用解析出的 doc_url 回填（watch_positions 按 b.get("url") 过滤，空=不生成按钮）。
    pos_card = cfg.get("pos_card")
    if isinstance(pos_card, dict):
        doc_url = report.get("doc_url") or ""
        for btn in pos_card.get("buttons") or []:
            if (isinstance(btn, dict) and not btn.get("url")
                    and "文档" in str(btn.get("label", ""))):
                btn["url"] = doc_url
    return cfg


def save_report_url(key, url):
    """把新建文档 URL 写入私有覆盖文件（不写 tracked config）。"""
    if key not in _SENSITIVE:
        raise KeyError("非敏感字段不允许写私有文件: %s" % key)
    p = private_path()
    data = _read_json(p)
    data[key] = url
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return url
