# -*- coding: utf-8 -*-
"""xq_pump：雪球数据增量抽取包（免授权、只抓数据、逐步增量）。

整理自 quant_backtest 原有 xq_* 体系，剥离治理外壳（代理池/登录/请求闸/
RightsGate），保留 token 注入 + 限速 + 403 熔断 + 健壮 GET，收口为统一 CLI。
"""
__version__ = "0.1.0"
