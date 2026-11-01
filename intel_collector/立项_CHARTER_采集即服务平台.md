# 采集即服务平台（Collection-as-a-Platform）· 立项章程

> 立项批准：老田 2026-11-01（override 田丰归口已完成源登记）
> 状态：**内核原型已落地并验证；真实数据根接入待老田开通访问路径（与 lg-97d585b261bb 解耦）**

---

## 1. 定位与价值

情报板块"开源情报"供给底座，数据平台 L0 入口。把分散的爬虫脚本升级为**能力平台**：
加一个新源 = 写一个适配器 + 登记权利，不重写管线。解决历史 owner 真空、用途无闸、失败不可见、跨源无归并等问题。

## 2. 内核四层（已落地）

| 层 | 模块 | 职责 |
|----|------|------|
| 源注册 | `registry/source_registry.py` + `sources.json` | 一源一档；硬拒 UNOWNED owner；健康/SLA |
| 适配器 SDK | `core/base_adapter.py` + `core/contracts.py` | 统一契约 `collect()->Observation[]`；不可变 Observation |
| 权利闸门 | `core/rights_gate.py` | 默认拒绝；training/redistribution/commercial 需显式批准 |
| 编排 + 存储 | `core/orchestrator.py` + `store/obs_store.py` | 采集绑定用途；幂等 latest/as_of 双语义 |

## 3. 已接入适配器

`guba`（股吧）、`news`（新闻）、`youtube_envelope`（系统外元信息包，仅 envelope）、`xueqiu`（雪球，合成→真实待冒烟）。5 源已全部登记，owner=`intel(军情司·INTEL0)`，零 UNOWNED。

## 4. 权利口径（铁律）

- 入湖默认仅 `internal_retention/cleaning/controlled_query`。
- `training/redistribution/commercial` **不随入湖开放**，必须显式、可追溯批准。
- 真实雪球 fetcher 已设三用途授权；训练/外发/商用仍为 false。

## 5. 待办与风险（不冒充完成）

| 项 | 状态 | 阻塞方 |
|----|------|--------|
| ① 真实雪球冒烟 | 代码就绪+闸门通过，**待老田提供 XQ_COOKIE + XQ_LIVE_ARMED=1** | 老田提供凭据 |
| ③ 真实数据根接入 | **未接入**；沙箱无访问路径，lg-97d585b261bb 不授予 | 老田开通路径+写权 |
| 跨源身份归并 / schema 归一化 / 消费契约 | ✅ **已建**（`core/canonical.py`：A股代码归一+跨源实体+rollup 消费契约，22 测试覆盖） | — |
| 多源调度编排（退避/失败封禁/熔断） | ✅ **已建**（`core/scheduler.py`：虚拟时钟驱动+按 freshness 到期+指数退避+熔断态绑定 registry.health；5 测试覆盖，27 全绿） | — |
| 质量 SLA（新鲜度四态+空比+熔断联动） | ✅ **已建**（`core/quality_sla.py`：按 sources.json 真实阈值判 no_data/达标/滞后/违约；6 测试覆盖） | — |
| PII 合规脱敏（email/手机/身份证） | ✅ **已建**（`core/pii.py`：递归脱敏 payload、入湖即脱敏、仅计数不落明文；6 测试覆盖；全量 40 测试通过） | — |
| PIT 时间旅行查询（时点回溯 / 全局快照） | ✅ **已建**（`core/pit.py`：filter_as_of+pit_rollup+pit_entity_state，对 obs_store.as_of 升级为全局时点快照；ISO8601 解析；8 测试覆盖；全量 48 测试通过） | — |
| 分析→采集反馈环 | ✅ **已建**（`core/feedback.py`：纯计算 compute_sla_feedback/compute_pii_feedback 与副作用 apply_actions 分离 + 会话态 FeedbackEngine；SLA 违约/滞后/无数据→加速补采，连续违约达阈值→熔断开闸+复核，PII 命中→高风险用途自动降级+复核；9 测试覆盖） | — |
| 统一 cron 实体化 | ✅ **已建**（`core/cron_runner.py`：CronRunner 收敛分散调度逻辑，tick 取到期源→派发→推进 next_due→末跑分析反馈环；run_forever 生产部署，now_provider/sleep_fn 可注入单测；11 测试覆盖；全量 68 测试通过） | — |

## 6. 红线

- 不替田丰补 owner（本次为老田显式 override，已留痕）。
- 不绕 RightsGate 开高风险用途。
- 不碰生产数据根直至老田单独授予写权。
- 真实触网需老田明确授权的凭据与 arming。
