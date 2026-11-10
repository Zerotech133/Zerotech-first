# 数据缺口通报 · IntelItem.published 覆盖率（发采集端）

> 2026-11-09 · KG 底座（监控职责）· 按通报五问契约 · 关联判例 2026-11-09

## 结论一句话

图内近 6 个月 201,476 条情报（IntelItem）只有 **11,925 条（5.9%）带发布日期 published**，导致情报脉冲/因子历史截面无法回扫——KG 只通报与追踪，拉和存归采集端。

## 通报五问

| 问 | 答 |
|---|---|
| **缺什么** | IntelItem.published（内容发布日期）。缺失集中在雪球/股吧帖子类情报；公告类（anns）已携带 |
| **数据点在哪** | 源侧：雪球帖子/股吧发帖时间戳（源站页面与接口均有）；图侧：IntelItem.published 属性（现 5.9%）；入库侧：kg_intel_xq_classified.py / kg_intel_xq_guba.py 入库字段映射 |
| **为什么现在没了** | ① 2026-11-07 图全量重建，tx_created（入库戳）恒为当日、无历史时间信息；② 雪球/股吧采集入库时源站发布时间未落 published 字段（采集脚本字段映射缺口），不是"以前有现在丢"，是**从未覆盖** |
| **谁认领** | 采集端（雪球/股吧采集脚本负责人）；KG 侧配合做进门校验（kg_intel_link valid_pub_date 已就位，畸形落隔离区） |
| **多久恢复** | 建议 W1（11-13）前：① 增量采集补 published 字段；② 存量回填可从源站帖子时间戳重取（需评估接口成本）。恢复后 KG 因子框架零改动重跑（日线已就位，二次执行分钟级） |

## 影响（为什么这 5.9% 重要）

- 因子 F2 验证（R2）：men_7d 历史截面仅 2/6 有效 → men_7d_hist、events_7d_hist 双双 **untested**，因子进不了策略验证管道
- 传导链发现（R5）：冷启动脉冲从预期数百缩到 104 个
- 北极星指标「F2 占比」持续为 0，卡点在此

## 附：格式质量

published 已有 11,925 条中格式混杂（'20260928' 无分隔 vs '2026-09-02' ISO），建议入库统一 ISO 'YYYY-MM-DD'；KG 侧 norm_pub 已兼容两格式并做范围钳制。


---

## ⚖️ 改判（2026-11-10 05:50，下游扩容时实锤）

本通报当日归因「采集端从未覆盖 published」**有误**，予以更正：

- 真根因：KG 注册表入库管道（kg_intel_link.upsert_intel_batch）只写
  published_at（原始串），未映射 published（归一日期）——而 R2 因子
  截面与通报覆盖率查询用的都是 published。
- 实锤：图内 84,524 条社媒帖（guba/xq_status/xq_timeline/taoguba/
  xueqiu_hot）+ 138,729 条 news 的 published_at 一直在图上，时间轴
  数据采集端早就拿回来了。
- 修复：①写侧 upsert_intel_batch 补 published 归一映射（kg_date_guard
  统一规格）；②存量回填 199,138 条（backfill_published_20261110.py，
  MERGE 幂等）。
- 结果：published 覆盖率 **5.07% → 89.6%**（211,063/235,433）；归一
  失败 24,370 条（多为 xq_status 空 ts，另册待查）。
- 连带：R2 F2 验证截面 2/6 → 5/6 有效，men_7d_hist（IC -0.0135）与
  events_7d_hist（IC -0.0425）出真实判定 failed，双 untested 收口。
  原通报中「谁认领/多久恢复」对采集端的追责条款**撤销**。
