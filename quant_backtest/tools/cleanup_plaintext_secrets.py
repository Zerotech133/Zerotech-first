# -*- coding: utf-8 -*-
"""明文密钥扫描器（工位 #371 · 门 N6 生产密钥 ceremony）。

策略：休眠默认 + dry-run 仅列出、绝不删。

  * scan() 默认对登记在 KNOWN_PLAINTEXT_RISK_PATHS 中的、『存在』的文件做
    存在性检查，返回『存在且疑似明文』的文件路径列表。
  * dry_run=True（默认！）时只报告、绝不删除、绝不修改任何文件。
  * 对不存在的路径不报错（跳过即可）。

删除动作红线：
  删除动作须经老田在 N6 门批准，本工具【不提供删除】。即便将来支持
  dry_run=False，也必须显式传参且二次确认——本工位按规格只保留 dry_run
  参数占位与以下清晰注释，不实现任何删除/修改逻辑。

依赖：仅 stdlib。
"""

from __future__ import annotations

import os
from typing import List, Optional


# 文档化散落明文风险点（来自 N6 清单）。仅作风险登记，不自动操作。
KNOWN_PLAINTEXT_RISK_PATHS = (
    "config/tushare_token.txt",   # 仓内相对路径
    "C:/Temp/tushare_token.txt",  # 临时目录明文
    ".env.local",                 # 仓内本地环境文件
)


def scan(paths: Optional[List[str]] = None, dry_run: bool = True) -> List[str]:
    """扫描疑似明文密钥文件，返回存在的路径列表。

    参数：
      paths   : 要检查的路径列表；默认使用 KNOWN_PLAINTEXT_RISK_PATHS。
      dry_run : 默认 True。本工位无论 True/False 都【不删除、不修改】任何文件；
                删除动作须经老田在 N6 门批准，本工具不提供删除。

    行为：
      * 仅对存在的文件计入结果（存在性检查）。
      * 对不存在的路径静默跳过，绝不报错。
      * 绝不做任何写操作。

    返回：存在的（疑似明文）文件路径列表。
    """
    targets = list(paths) if paths is not None else list(KNOWN_PLAINTEXT_RISK_PATHS)
    found: List[str] = []
    for p in targets:
        # 对不存在或不可访问的路径静默跳过，不抛异常。
        try:
            if os.path.exists(p):
                found.append(p)
        except OSError:
            continue

    # dry_run 占位与红线声明：
    #   dry_run=True  -> 仅列出（默认，安全）。
    #   dry_run=False -> 本工位仍『不删除』；删除须经老田在 N6 门批准，
    #                    本工具不实现删除逻辑。此处不调用任何 shutil/os.remove。
    if not dry_run:
        # 故意留空：删除动作禁止在本工具内实现。
        # 如需清理，请由老田在 N6 门显式批准并由独立流程执行。
        pass

    return found
