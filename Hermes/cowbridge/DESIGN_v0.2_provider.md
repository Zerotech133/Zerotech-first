# cowbridge v0.2 设计稿 — Hermes MemoryProvider 第三插件形态

> 日期：2026-10-08 · V6-5 · 状态：**设计稿（未接生产）**
> 前置：K272 v0.1 影子桥已落地；**生产写须待人审三项拍板后开工**
> 源码依据：`C:\Users\Administrator\.hermes\hermes-agent\agent\memory_provider.py`（281 行 ABC 全文）+ `plugins/memory/__init__.py`（发现/装载机制）+ mem0 插件先例，2026-10-08 逐行核实

## 一、两种形态对比（拍板依据）

| 维度 | 形态 a：直写 MEMORY.md（外部定时脚本） | 形态 b：MemoryProvider 第三插件（本文档） |
|---|---|---|
| 进程边界 | 进程外写 Hermes 生产文件 | 进程内生命周期钩子 |
| 并发安全 | ❌ Hermes 运行中写 MEMORY.md 可能与 agent 自身写入相撞 | ✅ 同进程，无写冲突 |
| Hermes→Cow 方向 | 无挂点，只能定时快照 diff | ✅ `on_memory_write` 钩子原生镜像每次内置记忆写入 |
| Cow→Hermes 注入 | 无（只能落盘等 agent 下次读文件） | ✅ `prefetch` 每轮回合注入 + `cow_memory_search` 工具暴露给模型 |
| 外部 provider 名额 | 不占（mem0/supermemory 可并存） | ❌ 独占（MemoryManager 限外部 provider 最多 1 个） |
| 字符上限管理 | 自己做 | 可复用 MemoryManager 既有逻辑 |
| 回滚 | 删脚本+手工清增量段 | 删插件目录+config 移除一行；内置 BuiltinMemoryProvider 永远在、不受影响 |

**推荐 b**：读写两个方向都有原生挂点，这是直写形态给不了的。代价（名额独占）当前无实际损失——mem0/supermemory 均未激活。

## 二、装载与发现（源码级核实）

```
C:\Users\Administrator\.hermes\plugins\cowbridge\   ← 用户级插件目录（$HERMES_HOME/plugins/）
├── __init__.py      # 薄壳：register(ctx) 注册 CowbridgeProvider；逻辑复用 E 盘桥本体
├── plugin.yaml      # description 元数据（discover_memory_providers 读它）
└── (可选 cli.py)    # hermes cowbridge 子命令，v0.3 再说
```

- **发现**：`plugins/memory/__init__.py::_is_memory_provider_dir` 启发式——`__init__.py` 前 8192 字符含 `MemoryProvider` 或 `register_memory_provider` 字样即认
- **装载**：`register(ctx)` 模式优先（`ctx.register_memory_provider(CowbridgeProvider())`），fallback 找 MemoryProvider 子类自动实例化——推荐 register 模式（对齐官方插件写法）
- **激活**：`config.yaml` → `memory.provider: cowbridge`；`hermes memory setup` 走 `get_config_schema()`（本地无凭据，返回 `[]` 即可）
- **单一事实源**：薄壳经 `importlib.util.spec_from_file_location` 加载 `E:\老田的AI助手\Hermes\cowbridge\cowbridge.py`，复用 `split_sections / read_hermes_memory / read_cow_memory / sha1_12`——不复制逻辑

## 三、生命周期映射（核心设计）

| ABC 方法 | cowbridge 实现 | 影子纪律落点 |
|---|---|---|
| `name` | `"cowbridge"` | — |
| `is_available()` | `~/cow/memory/` 存在 且 E 盘桥本体可加载；**不做网络调用**（ABC 明文要求） | — |
| `initialize(session_id, hermes_home, agent_context, ...)` | 记录 `hermes_home`（**用参数，弃硬编码**）；`agent_context != "primary"` 时置只读标志（cron/subagent 写入会污染用户画像，ABC 文档明示）；预热对侧 sha1 去重集 | 只读标志对所有写路径生效 |
| `system_prompt_block()` | 静态说明「CowAgent 记忆桥已激活（影子/生产模式）」 | — |
| `prefetch(query)` | 返回缓存结果；要求**快**，真检索放后台 | — |
| `queue_prefetch(query)` | 后台线程检索 CowAgent 三层（优先 `long-term/index.db` FTS5，不可用则词面粗筛），结果供下轮 `prefetch` | — |
| `sync_turn(user, asst)` | 入队；后台按小节粒度提候选 | 影子模式→只追加 `shadow/to-cowagent/`，生产模式→经拍板口径过滤后写 CowAgent |
| `get_tool_schemas()` | `cow_memory_search`（参数 query/tier 可选）：检索 CowAgent 记忆返回带 provenance 的小节 | — |
| `handle_tool_call()` | 分发上述唯一工具 | — |
| `shutdown()` | flush 写队列 | — |
| **`on_memory_write(action, target, content, metadata)`** | **Hermes→Cow 生产写挂点**：镜像每次内置记忆写入（含 write_origin/session_id 溯源），小节化+去重后作为 Cow 候选 | 影子→shadow 留痕；生产→写 CowAgent daily 层（天级先沉淀，Deep Dream 蒸馏自然升 long） |
| `on_session_end(messages)` | 会话末批量提取候选（幂等靠 sha1 去重集） | 同上 |
| `on_pre_compress(messages)` | 压缩丢弃前抢救性提取 | 同上 |
| `get_config_schema()` / `save_config()` | 返回 `[]`（local-only，无凭据）；`save_config` 保持默认 no-op | — |

## 四、影子纪律衔接（v0.2 首版即装也零风险）

- 配置文件 `cowbridge.json`（放 `$HERMES_HOME/`）：`{"shadow_mode": true, "allow_write_cow": false, "allow_write_hermes": false}`
- **v0.2 装上后默认 shadow_mode=true**：`sync_turn / on_memory_write / on_session_end` 全部降级为「shadow/ 追加 + log」——插件生命周期、读写正确率判据**在影子模式下即可开测**，生产写只是配置翻转，不改代码
- 判据挂账（转正条件）：读写正确率 100% 连续 3 日——prefetch 注入内容与 CowAgent 源一致、on_memory_write 镜像与内置写入一一对应、sync_turn 零丢 turn

## 五、互斥与迁移注意

- `memory.provider` 全局唯一 ⇒ 激活 cowbridge 即 mem0/supermemory/hindsight 等全部下线（当前均未激活，无实际冲突；未来若要上 mem0 语义检索，二者须取舍或等 Hermes 放宽限制）
- 内置 BuiltinMemoryProvider（MEMORY.md/USER.md）**永远活跃且不可移除**——cowbridge 是附加层，不是替代

## 六、回滚

删 `C:\Users\Administrator\.hermes\plugins\cowbridge\` + config.yaml 移除 `memory.provider` 行 = 完全回滚。生产记忆文件全程只受影子纪律保护（v0.2 影子模式下同样零接触）。

## 七、实现差异（2026-10-08 全量审计 P1-4 补记）

本稿为设计目标；v0.2.1 实测实现与下列条目存在差异——**以本节为准**：

| ABC 方法 | 本稿设计 | v0.2.1 实际实现 | 定性 |
|---|---|---|---|
| `is_available` | ~/cow/memory/ 存在 且 桥本体可加载 | 仅查桥本体文件存在（快路径，不加载、不查 Cow 目录） | 有意简化 |
| `queue_prefetch` | 优先 index.db FTS5，退词面粗筛 | 纯词面粗筛（read_cow_memory 全量+关键词打分），未用 FTS5 | 简化，挂账 v0.3 |
| `sync_turn` | 入队→后台小节化提候选→生产写 Cow | 影子=留痕；生产=入队但无消费者（shutdown 丢弃前如实日志） | ⚠️ **生产写未实现**（v0.3 挂账；initialize 有防误翻转断言兜底） |
| `on_memory_write` | 小节化+去重→Cow 候选；生产写 daily 层 | 影子=mirror 留痕；生产分支仅换名留痕（applied.jsonl），不触 Cow workspace | ⚠️ **生产写未实现**（同上） |
| `on_session_end` | 会话末批量提取候选 | 仅留痕计数，不提取 | 简化，挂账 v0.3 |
| `on_pre_compress` | 压缩丢弃前抢救性提取 | 恒返回 ""（防注入面扩大，有意关闭） | 有意简化 |
| 工具参数 | query + tier 可选 | query + limit（无 tier 过滤；limit 夹逼 [1,50]，非法回退 5） | 简化 |
| 读侧数据源 | （成稿时认知）MEMORY.md + daily | 实际主数据源 = long-term/index.db（chunks tier=long + messages 会话转录 tier=dialog），根部 MEMORY.md 常为空模板 | v0.1.1 实测修订（K273） |

结论：影子模式（默认）下读写正确率判据不受上述差异影响——判据所测的 prefetch 注入一致性、on_memory_write 镜像一一对应、sync_turn 零丢 turn，在「留痕即真相」口径下成立。**两处「生产写未实现」条目是转正前置硬门槛**：v0.3 补齐真实现并另行过测后，方可翻转 cowbridge.json（误翻转会被 initialize 断言拒绝，实例硬只读，不会假成功）。

**⚠️ v0.3 转正必改点（2026-10-08 复审补充）**：补齐生产写真实现时，须**同步删除插件壳 `initialize()` 里的防误翻转断言**（否则合法生产配置会被误拒、provider 硬只读），并同步更新本节两处「生产写未实现」行。断言位置：`~/.hermes/plugins/cowbridge/__init__.py` initialize 内「v0.3 转正必改点」注释处；回归护栏：test_provider_plugin.py 第 10 节三断言（raise/_disabled/零落盘）届时一并删除。
