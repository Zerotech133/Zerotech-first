# -*- coding: utf-8 -*-
"""analysis 包：个股全维分析平台（P0 MVP）

流水线：S0 触发 -> S1 快照 -> S2 事件 -> S3 特征 -> S4 研判 -> S5 结论
        -> S6 交付（报告/H5/图/manifest） -> G1-G4 质量门

用法：
    python -m analysis.run --code 600337.SH
    python -m analysis.run --code 688023.SH --no-llm --no-push
    python -m analysis.sync --days 180 --only anns,top_list,report_rc
"""
__version__ = "0.1.0"
