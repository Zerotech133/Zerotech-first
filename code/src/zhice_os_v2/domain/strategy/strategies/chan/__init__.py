"""缠论策略包（CHAN/CHAN_V2）— 自 V1 modules.candidate_engine.strategies.chan 移植。

血统说明（V1 三处现有实现评估后的融合方案）：
  - 结构引擎：czsc 开源库（PyPI 依赖，未装 rs_czsc 时走纯 Python 实现）
  - 中枢构建 / 买卖点几何判定 / 振幅量能背驰：移植自「缠论全闭环系统」czsc_adapter 纯函数
  - MACD 面积背驰：V1 项目补齐
  - 置信度评分思路：参考 guiyuan-qos（信号类型 + 背驰强度 + 中枢质量 + 流动性）

V2 范围：日线级别、只做二买/三买（一买背驰仅作二买加分项）、不做线段；
不移植 intraday（分钟确认）与 control_basket（对照篮子）。
"""
from zhice_os_v2.domain.strategy.strategies.chan import (  # noqa: F401
    engine,
    rules_v2,
    strategy,
    strategy_v2,
)
from zhice_os_v2.domain.strategy.strategies.chan.strategy import (  # noqa: F401
    select_candidates,
)
