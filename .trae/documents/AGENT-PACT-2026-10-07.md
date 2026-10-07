# AGENT-PACT 摘要（给 Trae-CN 会话）— 双 Agent 边界协定 v1.0

> 正式全文：`E:\KGStockAnalysis\docs\planning\dual-agent-pact-2026-10-07.md`（修改请去全文，此处 WorkBuddy 会话会同步）
> 发起背景：老田指示「两个 ID 的工作不要重叠抢端口、知识图谱改动别打架」。2026-10-07 生效。

## 一句话

两边共用这台机器的 Neo4j / PostgreSQL / Ollama / Tushare 表，**写入排队、读随意**；文件目录各管各的；新增服务先登记端口。

## 五条硬规则

1. **Neo4j 大批量写入互斥**：K260 回填（你们，进行中）期间，WorkBuddy 侧六座桥图谱写入不动笔；反之亦然。动笔前在协定 §四 登记窗口。WorkBuddy 桥写入用独立标签前缀（`CNPool_*`/`LJ_*`），不碰 MarketEvent/HotMoney/Seat 等 12 类既有边型。
2. **Ollama 算力：交互优先，批量让路**：/kg/ask、预警推送优先；大批量校准/回测推理排收盘后/深夜单飞行。
3. **ts_* 表**：你们只读，KG-TushareSync（00:10）/TdxSync（17:40）写入（WorkBuddy）。T11 摘 dryrun 转正式前会等 K260 回填完成。
4. **停写窗口联席**：任何一方开维护窗口（如 R2/R3 口令轮换），提前在协定 §四 登记 + **双方各自停自己的看门狗**（C7 期间 ZhiceWatchdog 打破过窗口）。
5. **目录所有权**：`portal/`、`quant_backtest/`、`jev-eval/`、`news_collector/`、`tools/`、`.trae/` 归 Trae-CN；`E:\KGStockAnalysis\`、`C:\aios\` 归 WorkBuddy；`ollama_service/`+`llamacpp_35b/` 文件归 WorkBuddy、服务双方共用。越界只读。

## 端口登记（10-07 实测，新增先登记）

8091 portal 唯一实例(Trae)（**8080 是 docker 容器端口，非 portal**）｜ 8060 local_service(WB，当前未在听) ｜ 9101-9103 jev 指标(Trae) ｜ 11434 Ollama(共享) ｜ 8030 35b(WB 按需) ｜ 8001/8098(WB) ｜ 5432/6379/6333/7474/7687(共享)

## 目录所有权补充（v1.0 修订）

`C:\aios\portal` = E 盘 portal 的**部署副本**（10-07 实测字节级同步）——归 Trae-CN 管理，WorkBuddy 不碰；`C:\aios\quant_backtest` = WorkBuddy（生产 PG-Robot）。

## N-Agent 扩展（v1.1，老田指示：未来多 Agent/多 IDE 工具）

协定升级为**注册制**：新 Agent/IDE 开工前在全文 §四 登记（名字/管辖区/标签与表前缀/端口/资源权限）。已预留：**AI 选股 Agent**（图谱前缀 `AS_`、表 `as_train_`/`as_bt_`、端口 8061-8069）。批量算力（训练/回测/蒸馏）一律窗口制；产品化走「服务化→影子期→灰度」三步。详见全文 §六 与整合方案 §九。

**Agent 接入套件已发布**（10-07 10:08）：`E:\KGStockAnalysis\agents\AGENT-KIT.md`——新 Agent 即插即用五面标准（接口/记忆库/图库/凭据/算力）。首个注册 Agent：**临界探子**（lingjie_scout，别名探针；开源社区巡视→拉代码→判优劣→整合建言；前缀 `SC_`/`sc_`/`scout_*`，文件契约 inbox/outbox 不占端口，图库默认只读，独立凭据 SC_GITHUB_TOKEN）。章程：`KGStockAnalysis\docs\planning\agent-scout-charter-2026-10-07.md`。

## 命名口径（老田 10-07 钦定，两侧统一）

项目总名**临界**；图谱=**元子**；数据仓=**数据城池**；数据联动=**数据血缘**。命名宪章：`E:\KGStockAnalysis\docs\planning\lingjie-naming-charter-2026-10-07.md`。新子项目取名照宪章两族办理（微观物理×文明设施），登记追加。

## 事件流水（append-only，见全文 §四）

- 10-07 09:50 WorkBuddy：协定建立。K260 回填期间本侧不动图谱写入。
- 10-07 09:55 WorkBuddy：首轮冲突实扫完成——portal 双份部署定性为对端内部部署机制（无抢端口）；8080 端口表纠错；本侧 8060 未在听已记录。
- **冲突处理约定（老田指示）**：任一会话发现两侧行为冲突 → 立即向老田报告冲突点 + 最优解决方案，不擅自裁决。
- 凭据备忘：R1 飞书 APP_SECRET 已于 10-07 09:32 轮换（config.json+HKLM 双写，jev 三组件已验证存活）；R2/R3 轮换将开联席窗口。
