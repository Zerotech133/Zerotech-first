# A股知识图谱项目收尾总结

> 收尾日期：2026-09-29
> 性质：项目终态确认 + 运行交接卡（建设/验证/复盘见配套三部曲）
> 结论：**全部交付完成，零挂起缺陷，转入常态运维**

---

## 一、终态总览（全绿）

| 板块 | 终态 | 确认方式 |
|------|------|---------|
| 图谱资产 | 9 类节点 57587 / 8 类时序边 161099 | `--stats` 实测，与权威口径逐项吻合 |
| 图特征 | 8 特征 × 5903 标的（kg_features.parquet） | 每日 06:20 自动重算 |
| 九维注入 | 图特征 5 字段 + evt_bull/bear 写快照 | dryrun 回归通过，降级路径防御 |
| 数据底座 | catalog 71 表 / 17 别名 / freshness 15 表 | 全表 done，时点校对 ok |
| 自动化 | 06:20 四步链 → 06:40 九维（93s/链） | 手动全链 + 计划任务双验证 |
| 自愈 | docker unless-stopped / KgClient 重试 / 降级 / 门户保活 | 分层验证 |
| 质量 | 9 项验证全过 / 8 坑全修 / 口径偏差 5 处归零 | 验证报告 + 口径核对记录 |
| 文档 | 三部曲 + 本收尾，本地云端同步 | 飞书知识库目录校验 |

## 二、资产地图

**脚本**（quant_backtest/）：kg_schema / kg_client / kg_build{,_p2,_p2_sw,_p2_prod,_p3,_p3_evt}.py / kg_features.py / pull_global_idx.py / event_graph.py

**数据**：data/features/kg_features.parquet · data/tushare/{cashflow,fina_indicator,sw_l1_member,index_global} · Neo4j 容器 neo4j-kg

**计划任务**：KgDailySync(06:20) / NineDimScan(06:40) / TdxSync(17:30) / TushareSync(23:30) / 金融新闻采集 / JEV / AI工作站门户API(保活)

**文档**：本地 KG_FINAL_REPORT / KG_VERIFY_REPORT / KG_RETRO / KG_CLOSEOUT ↔ 飞书知识库「老田的AI助手」四篇对应

**memory**：project_memory.md 收官+口径+验证+坑记录共 6 条新增条目

## 三、运行手册要点

```bash
# 日常健康（一条命令）
python kg_client.py --stats                    # 节点/边计数 + DDL
python -c "import datamart as dm; print(dm.freshness_report())"

# 手动触发（门户数据仓页 4 按钮，或）
schtasks /run /tn KgDailySync                  # 图谱四步链
python kg_client.py --sample 600519.SH         # 个股子图抽检

# 故障恢复
docker start neo4j-kg                          # 图谱库（正常应自动）
C:\Temp\kg_*.log                               # 四步链分步日志
```

## 四、挂起事项（非缺陷，带触发条件）

| 事项 | 触发条件 | 当前替代 |
|------|---------|---------|
| 产业链 SUPPLIES 显式边 | Wind/iFinD 付费数据 或 公告窗口积累 | `--peers` 产品二跳邻近 |
| 图特征进九维评分 | 因子有效性检验通过 | 仅写快照（不破坏 100 分制） |
| 介数中心性等高阶算法 | Neo4j GDS 插件安装 | 度中心性已覆盖 |

## 五、变更冻结

自本日起图谱 Schema（9 节点/8 边类型）冻结，新增实体/边类型走新 Phase 立项（含设计文档 + 口径基线 + 验证清单三件套），不再向现有 Phase 追加。每日链仅做数据保鲜，结构性变更一律立项。

## 六、文档体系（终版）

| 文档 | 问题 |
|------|------|
| 收官汇报 | 建了什么 |
| 验证报告 | 对不对 |
| 复盘总结 | 学到什么 |
| **本收尾总结** | **交给谁、怎么跑、何时重启** |

*收尾：2026-09-29 · 项目关闭，进入常态运维*
