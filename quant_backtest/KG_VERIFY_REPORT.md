# 知识图谱验证报告

> 验证日期：2026-09-29
> 验证范围：2026-09-28 建成至今的全部交付物（图谱资产 / 查询入口 / 时序语义 / 数据链路）
> 结论：**四层验证全部通过，3 处问题均已闭环，图谱处于自洽、新鲜、可运维状态**

---

## 一、验证项明细（9 项）

### 1. 图谱规模口径 ✅

- 方法：`python kg_client.py --stats` 分项求和，对照收官报告权威口径
- 结果：
  - 节点分项和 **57587** = 权威口径 57587
  - 边分项和 **161099** = 权威口径 161099
  - 9 类节点 / 8 类边逐项吻合，无漂移

### 2. Schema 完整性 ✅

- 方法：`ensure_schema()` 幂等重放（IF NOT EXISTS）
- 结果：9 唯一性约束 + 4 二级索引全部就位，重放无副作用

### 3. 个股全景子图 ✅

- 方法：`--sample 600519.SH` / `--sample 688322.SH`
- 结果：
  - 茅台：4 条基础边（贵州/白酒/公司/申万食品饮料）+ 3 高管 + 9 产品
  - 奥比中光：14 条边 + 黄源浩双职位 + 17 产品 + 当日事件

### 4. 时序区间语义 ✅

- 方法：奥比中光 SW_L1_OF 的 validStart 与上市日对比
- 结果：validStart=20230925 **晚于** 上市日 20220707——入指数晚于 IPO，
  时序区间如实反映（申万成分股进入日做 validStart 的设计正确落地）

### 5. 事件流新鲜度 ✅

- 方法：检查 INVOLVES 边 validStart 最大值
- 结果：含 **20260929 当日事件**（KgDailySync 四步链 evt_build 产出），
  事件流无断点

### 6. 一人多职表达 ✅

- 方法：奥比中光人物查询
- 结果：黄源浩 chairman + manager 双边共存——MERGE 键含 role 的设计生效

### 7. 产业链邻近 ✅

- 方法：`--peers 688322.SH --top 5`
- 结果：兴图新科 / 佳讯飞鸿以共享 7 产品并列居首
  （AI算法/AI硬件/系统集成/智能车载/移动终端），AI 硬件链语义合理

### 8. 数据时点校对 ✅

- 方法：`datamart.freshness_report()` 15 核心表
- 结果：全部 ok——daily 当日、us_daily lag1（美东时差）、
  财报 lag63/130（季频正常）

### 9. freshness 缓存三态 ✅

- 方法：预热 / 命中 / 失效三态实测 + `ttl_sec=0` 强制重算对照
- 结果：冷算 44s（_catalog 全仓扫描）→ 预热后命中 5ms →
  同步完成自动失效后快速重算 0.031s，链路正确

---

## 二、验证中发现并修复的问题（3 处）

| # | 问题 | 根因 | 修复 | 状态 |
|---|------|------|------|------|
| 1 | Event 目标显示 `None` | sample_subgraph 的 coalesce 链缺 Event 节点属性 | 补 `coalesce(m.kind,'')+coalesce(m.date,'')`，显示 `news20260929` | ✅ 已修复验证 |
| 2 | 冷启动首访可能 504 | freshness 冷算 44s 超 nginx `/api/` 40s 代理超时 | portal 启动预热线程，用户永远命中缓存 | ✅ 已修复验证 |
| 3 | "缓存失效不生效"假象 | 0.02s 响应实为快速重算（_catalog 按 mtime 缓存），非缓存命中 | 非缺陷；方法论沉淀：判断命中 vs 重算必须用强制重算对照组 | ✅ 已澄清记录 |

---

## 三、关键数据快照（2026-09-29 实测）

### 节点（57587）

| 标签 | 数量 | 来源 |
|------|------|------|
| Event | 23766 | Semantica 事件流（Phase 3-B） |
| Person | 14268 | stock_company 高管（Phase 2-A） |
| Product | 6700 | business_scope/main_business 抽取（Phase 2-C） |
| Company | 6265 | stock_company（Phase 1） |
| Security | 5908 | stock_basic 含退市（Phase 1） |
| SwL1Industry | 511 | sw_classify_l1（Phase 1） |
| Industry | 110 | tdx 细分行业（Phase 1） |
| Region | 32 | 省份（Phase 1） |
| MarketIndex | 27 | A股宽基 7 + 全球 20（Phase 3-A） |

### 边（161099，全部带 validStart/validEnd）

| 类型 | 数量 | 语义 |
|------|------|------|
| PRODUCES | 95492 | Company→Product |
| SERVES_AS | 18382 | Person→Company（含 role） |
| INVOLVES | 17598 | Security→Event |
| SW_L1_OF | 7737 | Security→申万一级（含行业变迁史） |
| CORRELATES_WITH | 4901 | 跨市场指数联动（季度窗口） |
| OF_COMPANY | 5839 | Security→Company |
| INDUSTRY_OF | 5575 | Security→Industry |
| AREA_OF | 5575 | Security→Region |

### 自动化链路

```
23:30 TushareSync（公告/行情）      高频 金融新闻采集
06:20 KgDailySync 四步链（93s）     evt_build → 全球指数 → 事件同步 → 图特征
06:40 NineDimScan 九维评分          _kg_enrich 注入图特征 5 字段 + 事件统计
自愈  neo4j unless-stopped / portal 保活任务 / KgClient 6×15s 重试
```

---

## 四、查询入口速查

```bash
python kg_client.py --stats                    # 图谱统计（幂等含 DDL）
python kg_client.py --sample 600519.SH         # 个股全景子图（含人物/产品/事件）
python kg_client.py --peers 688322.SH --top 8  # 产业链邻近（共享产品聚合）
```

---

## 五、结论

- 规模口径与权威基准（KG_FINAL_REPORT 核定）完全一致，无漂移
- 时序语义（图谱核心设计）多点抽验正确
- 数据链路新鲜、自动化闭环、自愈机制齐备
- 本轮验证 3 处问题全部闭环，其中 1 处为方法论沉淀（非缺陷）

**知识图谱体系验证通过，可进入常态运维。**
