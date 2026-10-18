# agent_platform — 盯盘 Agent 中台（v1 + T04 学习闭环）

> 架构稿：`C:\项目\deliverables\agent-watch-platform-architecture-20261016.md`
> 纪律：对 zhice 主干**只读**（import/调用/查询）；表全部 `aw_` 前缀；LLM 只经 LlmArbiter 令牌桶。

## 快速开始

```powershell
# 容量体检（5s 档 3 方法 100 标的水位边界）
& C:\项目\py312\python.exe -m agent_platform.cli capacity --symbols 100 --methods 3 --hz 5s

# 回测（CRYPTO 30 天 15m 档，LLM mock）
& C:\项目\py312\python.exe -m agent_platform.cli backtest --market CRYPTO --days 30 --freq 15m

# 指定标的 + 小样本真 LLM 对照（前 20 次冲突真调 Ollama，共享令牌桶）
& C:\项目\py312\python.exe -m agent_platform.cli backtest --market CRYPTO --days 7 \
    --symbols BTC/USDT,ETH/USDT --real-llm-sample 20

# 学习闭环演示（无飞书凭证全程 dryrun）+ 毕业权重
& C:\项目\py312\python.exe -m agent_platform.cli watch --market CRYPTO --dryrun-card
& C:\项目\py312\python.exe -m agent_platform.cli graduate --market CRYPTO

# 单测（code 目录下）
& C:\项目\py312\python.exe -m pytest tests/test_agent_platform_*.py -v --timeout=120
```

报告输出：`code/agent_platform_reports/`（Markdown + JSON 双格式）。

## 包结构

```
agent_platform/
├── config.py            # 全系数集中（容量/仲裁/LLM/回测默认档）
├── capacity.py          # §3 公式代码化 + CapacityGuard 启动自检（fail-fast）
├── models.py            # aw_* 五表 ORM（独立 metadata，create_all 自建）
├── db.py                # 决策记录器（DB 批量写 + JSONL 降级）
├── metrics.py           # Prometheus: aw_cycle_seconds / aw_arbitration_path_total / ...
├── cli.py               # capacity | backtest | watch 三子命令
├── methods/             # WatchMethod ABC + 三插件（chan/volume/rsi_spec）+ 注册器
├── arbitration/         # RegimeArbiter 三步 + LlmArbiter 令牌桶（Ollama REST）
├── runtime/             # WatchAgent 主循环 + ShardPolicy 分片
├── backtest/            # ★ v1 核心交付：engine / report / data_loader
├── learning/            # ★ T04 学习闭环：card_bridge / case_writer / memory_sink / graduation
└── tests_support/       # 合成 K 线 fixture 工厂（测试与基准共用）
```

顶层 shim：`src/agent_platform/` 转发垫片，支持 `python -m agent_platform.cli` 短命令。

## 三步仲裁（§4.2 硬规范）

1. **加权表决**：`score(X) = Σ wᵢ·confᵢ·1[dir=X] / Σ wᵢ`；法定人数=有效票≥2 且 Σconf≥1.0，否则 WAIT。
2. **regime 否决**：复用 zhice `market_regime_permits_entry`（直接 import）；risk_off 时 SELL 权重×1.5、HOLD×0.5。
3. **LLM 兜底**：|分差|<0.15 平票 或 SELL 且持仓额>阈值 → qwen2.5:7b；令牌桶 660/h，超限/超时 5s → 保守 WAIT（path=DROPPED/LLM）。

路径枚举：`WEIGHTED | REGIME_VETO | LLM | DROPPED`，全部落 `aw_decision`（votes_json 原文永不删改）。

## 方法插件开发指南

```python
from zhice_os_v2.agent_platform.methods.base import WatchMethod, MethodVote, WatchContext

class MyMethod(WatchMethod):
    method_id = "my_method"
    display_name = "我的流派"
    def evaluate(self, ctx: WatchContext) -> MethodVote:
        ...  # 返回 SELL/HOLD/WAIT + confidence[0,1] + reasons(人审依据) + params_snapshot
```
注册：`registry.register(MyMethod())`——仲裁层零改动。

## 数据源（2026-10-16 实查）

- 分钟线：`v2_bar_minute`（CRYPTO 37 标的 × **1m**，binance 源）→ pandas 重采样 5m/15m/1h
- 日线：`v2_bar_daily`（regime 门 + 符号发现）
- 快照：`v2_latest_snapshot`（线上 WatchAgent 用，v1 影子模式暂空转）
- 降级全序「粗先细后」：请求档 → 1h → 5m（1h 也不足时最终落 5m，报告 `freq_fallback` 注明实际档位）

## 容量速查（§3.3）

| 配置 | 容量 |
|---|---|
| CRYPTO 5s × 3 方法 | 100 标的（现役 37） |
| CN 10s × 3 方法 | 200 标的 |
| US/HK 3min × 5 方法 | ~1000（内存约束） |
| LLM 仲裁 | 660 判定/h 全平台共享 |

## T04 学习闭环（2026-10-16 交付）

架构稿 §5 八步全链已实装：卡片送审 → ack → LearningCase → T+5 回填 → 记忆沉淀
→ 毕业判据 v2 → 权重热更新 → 回到仲裁（`registry.load_weights` 合拢）。

### 模块清单

| 模块 | 职责 | 失败降级 |
|---|---|---|
| `learning/card_bridge.py` | 飞书卡片送审（webhook / OpenAPI / **dryrun** 三模式）+ 文件 ack 回收 | 无凭证/发送失败 → 卡片 JSON 落盘 `cards_outbox/`，不抛异常 |
| `learning/case_writer.py` | ack → `aw_decision.status` 流转 + zhice `LearningCase`（`save_learning` 只调不改）+ `aw_learning_link`；`backfill_fwd_ret()` T+5 前向收益（口径同 backtest 引擎，价取 `v2_bar_minute`） | zhice_db 失败 → aw 表照常更新，案例仅构造返回 |
| `learning/memory_sink.py` | cowbridge daily 层异步沉淀（后台线程 flush，`write_memory_to_cow` 模式）+ `search_hints` 关键词检索注入 `WatchContext.memory_hints` | cow 写失败 → JSONL 留痕 `memory_store.jsonl`（route=fallback），主链零阻塞 |
| `learning/graduation.py` | 判据 v2：n≥30（CRYPTO 降档 6）且该方法投 SELL 子集 T+5 均值 >+1pp → ×1.2 封顶 3.0；<-1pp → ×0.8 地板 0.3；`apply_weights` 写 `aw_method` + `aw_graduation` 留痕 + registry 热更新 | aw 库不可用 → 仅 registry 热更新 |

### 闭环演示（dryrun，无需飞书凭证/无 cow/无 zhice 写权限）

```powershell
cd C:\项目\zhice-os-v2-windows-20260920\code

# ① 卡片送审：合成 K 线（vol_div 量价背离）走真实投票+仲裁 → SELL 决策 → 卡片 JSON 落盘
& C:\项目\py312\python.exe -m agent_platform.cli watch --market CRYPTO --dryrun-card
# 产出: src/zhice_os_v2/agent_platform/learning/cards_outbox/<ts>_<decision_id>.json
#       （卡片含标的/score/各方法票人话版 reasons + 「确认卖点」「忽略」两按钮）

# ② 人工 ack：向 ack_inbox 写回执（模拟点按钮）
#    文件格式 {"decision_id": "...", "action": "CONFIRM_SELL", "operator": "老田", "reason": "..."}

# ③ T+5 回填 + 毕业权重（一键评估+应用+报告）
& C:\项目\py312\python.exe -m agent_platform.cli graduate --market CRYPTO
```

全链 mock 测试（24 用例）：`tests/test_agent_platform_learning.py`
（ack→case→memory(dryrun)→graduate 全链 + dryrun 落盘断言 + 判据边界 6/30/封顶/地板）。

<!-- dryrun 演示截图位：cards_outbox 卡片 JSON 样例 + graduate 报告输出 -->

### ack 回收取舍说明

v1 选 **文件 ack**（`ack_inbox/*.json` 轮询，消费后改 `*.consumed.json` 去重）而非
lark_card_handler 卡片回查/webhook：实现成本最低、dryrun 可完整演示、接口
（`poll_acks() -> list[CardAck]`）不变，生产可无缝替换为 HTTP 端点。

### 环境变量（飞书凭证，均可选）

| 变量 | 说明 |
|---|---|
| `AW_CARD_WEBHOOK` | 自建机器人 webhook（路①，优先） |
| `AW_CARD_APP_ID` / `AW_CARD_APP_SECRET` / `AW_CARD_OPEN_ID` | OpenAPI 凭证（路②） |
| 均未配置 | dryrun 模式（一等公民） |

## 遗留 TODO（T05）

- [ ] Prometheus 常驻暴露 + ZhiceWatchdog 补注册（部署清单 T05）
- [ ] ack 文件 → HTTP `/ack` 端点升级（接口已隔离在 `poll_acks`）
- [ ] MemorySink 检索升 bge-m3 语义匹配（v1 关键词够用）
