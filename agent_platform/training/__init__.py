# -*- coding: utf-8 -*-
"""训练数据聚合包（IFS v1 F5；WO-IFS-TRAIN-20261103）。

仅做 **outcome / training_episode 只读链**：
- ``outcome_chain``：paper receipt → T+1 outcome（outcome_id 生成/回填）+ 训练 episode 契约；
- 不自动训练、不写生产数据（所有写操作必须显式传路径，测试/CLI 用临时目录）；
- 不改交易/paper 账、不接实盘、不启服务。
"""

__all__ = ["outcome_chain"]
