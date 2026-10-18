# 三版本拆分设计：本机开发版 / 159 A股版 / 207 美股加密版

- 日期：2026-09-18
- 状态：已与负责人逐节确认批准
- 实施基线：main（第 0 步完成 full-market-scan 合入后）；本设计以部署操作为主，预期零/极少代码改动（仅迁移预演发现兼容问题时例外）

## 1. 背景与目标

系统此前以"一套部署、多市场"形态运行。现拆分为三个部署版本：

| 版本 | 定位 | 机器 | 形态 |
|---|---|---|---|
| 4市场完整版 | 开发版 | 本机（macOS） | 原生进程 uvicorn/vite/PG |
| A股单市场版 | 生产版（自用），国内推送渠道（微信） | 159.75.220.212 | 原生 systemd + PG13 + nginx |
| 美股+加密双市场版 | 生产版（有真实用户），telegram 推送 | 207.56.221.140 | Docker Compose |

**核心原则：一套代码（main 单源），三份部署配置。不建分支、不改代码逻辑（除非预演发现兼容问题）。**

## 2. 现状盘点（2026-09-18 实测）

### 159（主战场）
- 代码停在迁移 0015（2026-09-06 部署），落后 main 约 36 commit / 18 个迁移
- 服务自 09-06 持续运行零重启；API 一周 0 错误；磁盘 31%
- **无真实用户业务**：1 个测试用户（0 TG 绑定），无用户实例表/订阅表（0015 时代无这些表）
- 数据缓存健康：CN 日线 212.9 万行到当日、CN 分钟线 663 万行到收盘；CRYPTO 到当日；US 退化（分钟停 09-04）
- 交易日自动化闭环全链路在跑（当日：候选 6→计划 19→意图 37→成交 37→持仓 25→日报 2）
- **已知退化**：CN 决策复盘断档（09-03 起，每天 15:50 被"当日日K未入库"时序护栏拦下且无补跑）；本机新代码+同一 DataCenter 数据源复盘正常 → 升级即修复
- CN 数据源 = 公网 DataCenter（datacenter.xundaotech.cn:24123），159 直连，无内网依赖
- .env.local 已配 `SCHEDULER_MARKETS=CN,US,CRYPTO`；服务描述写"CN单市场"是历史意图

### 207（基本零改动）
- 跑 feat/full-market-scan@919903b（较新），迁移 0032
- `SCHEDULER_MARKETS=US,CRYPTO` + 前端 `VITE_ENABLED_MARKETS=US,CRYPTO` 已配 —— **已是目标形态**
- 9 个真实用户、6 启用实例、14 支付单、5 订阅、1 TG 绑定；bepusdt 支付在跑
- telegram = 管理员手动填 chat_id（legacy broadcast 链路）

### 本机
- feat/full-market-scan 分支（领先 main 36 commit，纯超集），微信渠道（国内双渠道通知）在此分支、**尚未合 main**
- 工作区有上次会话遗留的少量未提交修改

## 3. 关键决策记录（负责人已拍板）

1. **159 = 自用版**，最小化改动获得"A股单市场 + 微信推送"
2. **159 保持原生 systemd 部署**（不迁 Docker Compose）
3. **159 路线 1：全量升级 + enforced 微信**（路线 2 最小收缩会丢微信推送被否；路线 3 弃库重建仅作 PG13 迁移预演失败时的 Plan B）
4. **207 维持现状**：telegram 手动 chat_id，不切 enforced/directed
5. **微信定向投递的硬约束**：`WXPUSHER_DELIVERY_ENABLED` 强制要求 `TELEGRAM_NOTIFICATION_MODE=directed` + `MULTI_USER_EXECUTION_MODE=enforced`；enforced 下盘前执行走用户实例扇出（execute_fanout），平台内置策略的 legacy 统一运行链不再执行。159 自用 + 用户实例模式正好闭环。

## 4. 三版本配置矩阵

| 配置项 | 本机（开发版） | 159（A股版） | 207（美股加密版） |
|---|---|---|---|
| `SCHEDULER_MARKETS` | 空（=4市场全开） | `CN` | `US,CRYPTO`（不动） |
| `VITE_ENABLED_MARKETS` | 空（=4市场全开） | `CN` | `US,CRYPTO`（不动） |
| `MULTI_USER_EXECUTION_MODE` | `legacy` | **`enforced`** | `legacy`（不动） |
| `TELEGRAM_NOTIFICATION_MODE` | `broadcast` | **`directed`** | `broadcast`（不动） |
| `TELEGRAM_BINDING_ENABLED` | false | false | false（不动） |
| `WXPUSHER_BINDING_ENABLED` | false | **`true`** | false |
| `WXPUSHER_DELIVERY_ENABLED` | false | **`true`** | false |
| `APP_ENV` | dev | prod | prod（不动） |
| 支付（bepusdt） | — | 不部署 | 现状保留 |

生效语义（全部为已存在并测试过的开关，无需新代码）：
- 159 前端只显示 A股；调度器只跑 CN 盘前/盘中/盘后；平台策略授权/支付候选只剩 CN
- 159 通知走 outbox → WxPusher 定向投递（扫码绑定、偏好矩阵、静音时段全可用）
- 159 盘前走用户实例扇出：负责人在策略广场为自己开实例 → 按自有账户选股/下单 → 成交/信号/晨晚报推微信

## 5. 159 存量数据处置
- CN 行情（212 万行）：原样保留，迁移不动
- US/CRYPTO 行情（约 60 万行）：保留冻结（不再更新、不删）
- legacy 持仓/成交：保留只读（enforced 后不再更新）
- 活跃业务数据 = 负责人自己的账号/实例/通知偏好（全新产生）

## 6. 实施设计

### 第 0 步：本机发布基线
1. 处理工作区遗留未提交修改（检查后提交或丢弃）
2. `feat/full-market-scan` 合入 main（纯超集，无冲突）
3. 本机继续默认配置跑 4 市场开发；`scripts/sync_cn_from_159.py`（本机←159 CN 同步）继续可用

### 第 1 步：159 升级（选周日 A 股休市窗口，"生产不受影响"保障）

**阶段 A 备份（双保险）**
1. `pg_dump` 全量备份 zhice_os_v2
2. 旧代码目录 tar 备份（`/opt/zhice-os-v2 → /opt/zhice-os-v2.bak-0015`）

**阶段 B 迁移预演（PG13 兼容验证）**
1. 备份恢复到临时库 `zhice_os_v2_rehearsal`
2. 临时库 `alembic upgrade head`（0015→0033，18 级）
3. 抽查新表/新列落库正确
4. 通过才进 C；失败切路线 3，原库无损

**阶段 C 代码与依赖同步**
1. 停 api + scheduler
2. rsync main 的 `src/`、`migrations/`、`requirements` 到 `/opt/zhice-os-v2`
3. `.venv` 内 `pip install -r requirements.txt`
4. 本机构建 `VITE_ENABLED_MARKETS=CN npm run build` → rsync `dist/` 到 nginx 目录

**阶段 D 配置切换（.env.local 一次改为目标矩阵）**
- `SCHEDULER_MARKETS=CN`、`MULTI_USER_EXECUTION_MODE=enforced`、`TELEGRAM_NOTIFICATION_MODE=directed`
- `WXPUSHER_BINDING_ENABLED=true`、`WXPUSHER_DELIVERY_ENABLED=true`
- WxPusher 凭据复用现有应用（APP_TOKEN/APP_ID/APP_KEY/WEBHOOK_PATH_KEY）
- 组合校验启动时 fail-fast，配错立即暴露

**阶段 E 真库迁移 + 重启**
1. 真库 `alembic upgrade head`（服务已停）
2. 起服务，确认启动日志：CN 单市场生效、directed 模式、无校验报错

**阶段 F 自用初始化（一次性）**
1. 登录/提权账号
2. 个人中心扫码绑定微信
3. 策略广场开 1 个 CN 策略实例（选版本→专属账户→资金）
4. 发测试通知确认微信收到

**回滚（任何阶段可退）**：恢复 .env.local 旧值 + 代码 tar 还原 + 整库恢复 pg_dump → 回 0015 三市场形态

### 第 2 步：159 验收（见 §8）
### 第 3 步（可选）：CN 复盘 16 天断档补跑
### 第 4 步：沉淀三版本运维 runbook（docs/runbooks/）
### 207：本次零改动；日后可选窗口对齐 main 基线（env 不变）

## 7. 风险清单

| 风险 | 概率 | 缓解 |
|---|---|---|
| PG13 上 18 级迁移失败 | 低 | 备份库预演；失败切路线 3 |
| WxPusher 扫码回调不可达（159 无 https/域名）| 中 | 实施前先单独验证回调链路；绑定开关独立，失败不影响其余功能 |
| enforced 后无平台统一持仓（习惯变化）| 低 | 自用已知；实例模式跑通即验收 |
| 新调度任务时序差异 | 低 | 升级后首个交易日盯盘前任务日志 |
| 回滚需求 | — | 双备份 + 自用库小，整库恢复分钟级 |

## 8. 验收标准（159）

1. 启动无配置校验错误，health ok
2. 前端只显示 A股；调度日志无 US/CRYPTO 任务
3. 首个交易日：盘前 fanout 跑通（用户实例产生计划→成交）
4. 微信扫码绑定成功 + 收到测试消息
5. CN 决策复盘恢复产出（新交易日有 v2_decision_review 行）
6. 晨/晚报推送到微信
7. 数据完整：bar_daily 行数 ≥ 升级前

## 9. 范围外（明确不做）
- 159 商业化/支付渠道（人民币收款）、113 的角色处置、207 切 enforced/directed、159 迁 Docker Compose
