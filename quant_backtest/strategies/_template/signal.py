# -*- coding: utf-8 -*-
"""模板信号实现（策略包规格 v1，出处：方案 §2.1）。

复制本模板后只改本文件与 policy.json/feeder.yaml/tests，
其余流程（影子期/registry/台账）由底座 SOP 驱动（规格 §6）。

接口约定（纯函数，两入口）：
    load_features(feeder) -> dict[str, DataFrame]
        从 feeder 注入的特征源读取数据，返回 {字段名: DataFrame}。
        本函数内不做任何计算逻辑——只做装载与基本对齐。
        feeder.yaml 声明依赖，数据服务按声明注入，不在本文件自取数
        （规格 §1：feeder.yaml 职责）。

    signals(features, policy) -> DataFrame
        纯函数：特征 + 冻结判据参数 → 信号集。
        - 无 IO、无全局态、无时钟、无随机数（回归锁 diff=0 的前提，
          出处：方案 §六风险第五条"字节级等价迁移+回归锁"）；
        - policy 只读传入（policy.json 解析后的 dict），本文件不得改写；
        - 输出列约定：至少含 [date, code, signal]，signal ∈ {0,1}
          （12 策略现状最 common 的最小公共口径，不引入假想需求）。

以下实现全部为占位，返回空结果——填实现后删除 NOT_IMPLEMENTED 标记。
"""


def load_features(feeder):
    """feeder 注入的特征源 → {字段名: DataFrame}。占位：空 dict。"""
    # 示例（qpzm 口径）：
    #   feats = feeder.load(source="qpzm_parquet",
    #                       fields=["breadth", "med20"])
    #   return feats
    raise NotImplementedError("load_features: 复制模板后填实现")


def signals(features, policy):
    """特征 + 冻结 policy → 信号集 DataFrame[date, code, signal]。

    占位：空 DataFrame。policy 参数见包内 policy.json（sha 冻结，
    本函数只读）。
    """
    raise NotImplementedError("signals: 复制模板后填实现")
