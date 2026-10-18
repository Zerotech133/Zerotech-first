# -*- coding: utf-8 -*-
"""策略收编流水线四件套（RC-1 S1~S4 模板工位，pipeline-fw）。

本包只新增模板/工具，不 import 任何执行链（execution_engine / backtest /
runner / agent_platform LLM 模块），不修改 strategy_layer 既有文件。

- manifest_template.json + MANIFEST_GUIDE.md : S1 Manifest 注册模板
- classic_adapter.py                        : S2 经典策略 Agent 化适配模板
- replay_reconcile.py                       : S3 历史回放对账脚本
- shadow_report_template.md + shadow_collect.py : S4 影子评估报告模板与采集

红线：收编零公共主链 diff；数据源一律只读接口，不重拉数据、不写库。
"""
