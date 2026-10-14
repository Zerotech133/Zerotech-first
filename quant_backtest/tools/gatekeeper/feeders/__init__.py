#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""特征供给器包：feeder 可插拔，闸门逻辑不认数据源（方案 §二）。

qpzm_parquet —— 读 data/ashare/*.parquet 自算宽度/20 日收益中位；
lake         —— 读 reports/qd_breadth_series.json 湖序列（只有 breadth，
                med20 缺维 → safe=None 降权）；
未来：盯盘实时 feeder 接同一契约（方案 §五阶段 2/3）。
"""
