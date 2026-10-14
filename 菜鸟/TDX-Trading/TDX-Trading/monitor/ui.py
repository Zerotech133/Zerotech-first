# -*- coding: utf-8 -*-
"""前端页面加载。

templates/index.html   —— 简化版首页（三步添加预警 + 列表管理，2026-10-14 重构）
templates/advanced.html —— 旧版完整功能页（仪表盘/模型/股票/数据/回测），入口 /advanced

模板改动重启生效（模块加载时读入一次）。
"""

import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_FILE = os.path.join(BASE_DIR, 'templates', 'index.html')
ADVANCED_FILE = os.path.join(BASE_DIR, 'templates', 'advanced.html')

# 模块加载时读入一次（与原单文件常量语义一致；模板改动重启生效）
with open(TEMPLATE_FILE, 'r', encoding='utf-8') as _f:
    HTML = _f.read()

if os.path.exists(ADVANCED_FILE):
    with open(ADVANCED_FILE, 'r', encoding='utf-8') as _f:
        ADVANCED_HTML = _f.read()
else:
    ADVANCED_HTML = ''
    logging.warning('templates/advanced.html 不存在，/advanced 将返回空页')


def render_html(refresh_ms):
    """简化版首页（JS 自动刷新，不依赖 meta refresh 占位）。"""
    return HTML.replace('__REFRESH__', str(refresh_ms))


def render_advanced():
    """旧版完整功能页。"""
    return ADVANCED_HTML.replace('__REFRESH__', '5000')
