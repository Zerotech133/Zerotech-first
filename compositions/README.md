# 组合清单（Compositions）

> 平台内核契约与组合模型的落地目录。规则见 [ADR-015](../docs/adr/ADR-015-platform-kernel-composition-model.md)；
> 唯一宿主与 L1/L2 整合见 [ADR-016](../docs/adr/ADR-016-single-host-integration.md)。
> **P0 状态**：影子清单（v0.1 draft），只描述、不装载、不改变运行时；P1 起由 `tools/composition.py` 校验/装载。

## 一、这是什么

一条组合清单声明"一个军团 / 产品 / 策略包 / 页面包 / 数据包 / 工作流 / 通道包"由哪些**已注册单元**组成：

```text
compositions/<id>/manifest.yaml     # 组合清单（只引用，不内嵌）
compositions/registry.json          # 组合账本（id/版本/owner/生命周期/宿主/健康）
compositions/_schema/composition.schema.json
```

组合清单**不复制** org/team/hr/skill/strategy/dataset/page/job 的内容，只写 `ref`。
数据真源仍是各注册表；组合层只做"引用 + 版本锁定 + 依赖 + 生命周期"。

## 二、组合单元类型（C1–C8）

| tier | 含义 | 示例 |
|---|---|---|
| `legion` | 军团/组织组合 | `acad1-legion` |
| `product` | 产品/业务组合 | `quant-domain`、`lusu-d1-flow` |
| `strategy` | 策略包 | 策略 Manifest 收编（S1–S5） |
| `page` | 页面包 | 页面 manifest（P2 建） |
| `data` | 数据包 | `data-warehouse` |
| `workflow` | 工作流包 | `huangzhong-acceptance` |
| `channel` | 通道包 | 飞书/站内/交易通道 |
| `platform` | 平台自身设施（慎用） | 整合迁移计划 |

## 三、新增/删除一个组合

**新增**（目标：内核文件 diff = 0）：

1. 新建 `compositions/<id>/manifest.yaml`，填 metadata + spec；
2. 引用必须在注册表中存在（席位/技能/策略/数据集/页面/任务/扩展/服务）；
3. 在 `registry.json` 登记；
4. P1 起跑 `python -X utf8 tools/composition.py validate compositions/<id>`；
5. 走 devtask 单（组合 gate：validate 全绿 + 引用可达 + 无路由/席位/任务 ID 冲突）。

**停用/退役**（目标：零残留）：`lifecycle` 置 `degraded/retired` → 页面隐藏、任务停触发、席位解绑、数据只读保留；
**不得物理删除 manifest**（决策痕迹保留，同 ADR 纪律）。

## 四、校验规则（v0.1）

1. `apiVersion/kind/metadata/spec` 必填、格式合法；
2. `metadata.id` 全局唯一；`host` 必须是已登记宿主（当前唯一 `lingjie`）；
3. 引用存在（席位/技能/策略/数据集/页面/任务/扩展/服务）；
4. 依赖无环；`extends` 不得形成循环；
5. 默认只读；`write_scopes` 必须落在该组合 owner 的授权写域；
6. 禁明文密钥/token；禁绝对路径写入；端口走 `tools/check_ports.py` REGISTRY；
7. 数据契约存在且版本匹配；`interfaces.auth.public=true` 必须显式理由（P1 起强制）；
8. owner 在 org 域内；必须有 SLO、回滚方式；
9. 页面/接口必须在路由基线内（`tools/portal_route_inventory.py`）；
10. 内核版本兼容（`kernel` 区间命中 `platform/kernel_v1.yaml`）。

## 五、P0 边界（重要）

- 本目录当前只读：**不接运行时、不改注册表、不启停服务**；
- 影子清单用于暴露"现网到底能被组合模型描述到什么程度"，解析不了/引用不到的项 = P2 净化清单；
- P1 才实现 `tools/composition.py`、`tools/kernel_guard.py`，并接入 devtask merge；
- 组合清单**不允许**成为第二真源：每个字段必须能指向已有注册表或声明"由本组合私有实现"。

## 六、当前影子清单

| id | tier | owner | 说明 |
|---|---|---|---|
| `acad1-legion` | legion | ACAD1 | 司马徽军团（11 席 + 选股研究治理） |
| `quant-domain` | product | A0 | 量化域（智策收敛为内部服务 + 页面包 + PG） |
| `lusu-d1-flow` | product | D1 | 鲁肃 D1 投研/裁决/paper 流水线 |
| `quant-strategy-line` | product | STRAT1 | 策略线（12 策略 + 影子账本 + PIT） |
| `data-warehouse` | data | DATA1 | 数据仓（314 数据集 + catalog + 血缘） |
| `huangzhong-acceptance` | workflow | HUANGZHONG | 黄忠 11-04 全链路验收链 |
| `dev-legion` | legion | ARCH1 | 曹操军团（11 席 + 只读作战屏契约 `/api/dev/legion`；WP1+WP3 影子清单，不接运行时） |

## 七、与其它契约的关系

- 席位：`agent_platform/registry/org.json`、`team_*.json`、`hr/<SEAT>/*`（P2 收敛为 Seat 描述符）；
- 技能/模型：`skill_registry.json`、`model_tiers.json`；
- 策略：`agent_platform/engine/strategy_registry.py`（Verdict 契约）；
- 扩展：`extensions/<name>/manifest.json`（O3 扩展契约，type=tool/mcp/skill/filter）；
- 接口：S21 统一信封、`agent_platform/contracts/reuse/`；
- 数据：`tools/datawarehouse/dataset_registry.py`；
- 页面：`portal/static/{tokens.css,nav.js,page_shell.js,shortscreen.js}` + 路由基线；
- 任务：`agent_platform/engine/tasks.py`（TaskStore 状态机）。
