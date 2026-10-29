# 研发部组织架构（将作监）

> 实线=行政/指挥；虚线=独立质量/审计/治理与项目矩阵；矩阵不占研发指挥链。

```text
老田（人类所有权 / L2 最终批准）
  └─ A0 田丰（研发总负责人：目标/优先级/跨部门/L2 转呈）
       └─ ARCH1 曹操（研发部主官 · 架构终审 · L1_dev_max）
            ├─ A0A 董允（交付协调 0.5，虚线汇报）
            ├─ G1 后端/AI/平台组（组长 马钧）
            │   ├─ DEV6 胡昭（backend）
            │   ├─ DEV7 姜维（backend）
            │   ├─ DEV2 刘晔（ai）
            │   └─ OPS1 王基（devops）
            └─ G2 前端/量化/文档组（组长 邓艾）
                ├─ DEV8 郝昭（frontend）
                ├─ DEV3 蒋济（quant）
                ├─ DEV4 杜预（quant）
                └─ DOC1 陈寿（docs）
```

## 独立线（不占研发指挥链）

- **QA1 于禁 质量门**（质量工程部）：独立验收；ARCH1/组长不得代替质量门。
- **关羽（GUANYU）代码审计**：洁净上下文评审/审计，ARCH1 不自审。

## 项目矩阵（不改变行政归属）

- **DEV1 马钧**：proj_data(data_dev)
- **DEV2 刘晔**：proj_next(next_version_dev)
- **DEV3 蒋济**：proj_backtest(backtest_dev)
- **DEV4 杜预**：proj_backtest(backtest_dev)
- **ARCH1 曹操**：proj_next(next_version_arch)

> 矩阵任务按项目部门派单，但人的行政归属/考核仍在研发部→组；不得以矩阵角色绕过组长/ARCH1。
