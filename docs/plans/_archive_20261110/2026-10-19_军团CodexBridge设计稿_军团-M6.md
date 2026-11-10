# 军团 Codex bridge 设计稿（演进篇）：目标架构与 digital-legion 对接口径（军团-M6 设想）
> STATUS: pending_approval（owner: TBD；2026-11-07 前拍板）



- **日期**：2026-10-19
- **作者**：c-bridgedoc（老田个人 AI 组 C 路文档代理）
- **状态**：**设计稿——零代码改动**。本文档是本任务唯一产出（新建一个 md，不 add 不 commit）；全部【设想】与【建议】均为待拍板项，老田未拍板前不写任何一行代码。
- **编号纪律**：里程碑一律用 **军团-M\*** 前缀，与 AP-M\* 严格区分（M5 门 4 文档纪律）。现状：军团-M1/M2/M3 已落地（静态门/试点注册/沙箱试跑），军团-M4/M5 已拍板待施工（对账自动化/CLI 受理薄壳）。本文承接该序列，将对 digital-legion 侧的对接里程碑暂编为 **军团-M6（设想，未拍板）**；若老田另有编排，以拍板为准。
- **决策依据**：
  - `agent_platform/docs/audit/2026-10-18_军团CodexBridge设计稿.md`（原设计稿，M1-M3 已照其施工，§5 风险表本文引用对齐）；
  - `agent_platform/adapters/codex_bridge.py`（已入库实现，能力面见本文 §一）；
  - `agent_platform/docs/audit/2026-10-18_军团M4M5规划稿.md`（M4/M5 已拍板件，本文不重复主张其范围）;
  - `agent_platform/adapters/heartbeat.py` + `docs/plans/2026-10-19_心跳对接五项拍板执行_K-ORG-190.md`（adapters/ 对接第一刀先例与今日桥接判例）；
  - `agent_platform/docs/audit/2026-10-18_策略系与军团契约映射设计.md`（digital-legion contracts v1 落盘版对表结论，本文 §五 接口设想的契约基线）；
  - `docs/audit/2026-10-18_M5门4签字收口与军团三拍板落地.md`（仓库路线 = agent_platform 扩展，Paperclip/Codex/Hermes 走 adapters/ 对接）。

---

## 一、现状：已实现面（注册 / 收件箱 / processed 流程）

### 1.1 能力面盘点（以 codex_bridge.py 实现为准）

| 件 | 状态 | 说明 |
|---|---|---|
| **M1 静态校验器**（`validate_source`） | ✅ 已落地 | AST 只读解析：import 白名单制（七件套纯计算库 `math/statistics/collections/itertools/functools/typing/dataclasses` + 宿主契约模块 `strategy_registry`，白名单外一律拒）；危险字面调用 DENY_CALLS（eval/exec/compile/open/getattr 系等）；凭据形态 CRED_PATTERN（R2）；行数 ≤300 / 字节 ≤16KB；恰好一个 StrategyAgent 子类、禁 `__init__`（无状态）、propose 必备、`strategy_id` 强制 `codex_` 前缀、stance 三档校验 |
| **M2 试点注册**（`register_from_source`） | ✅ 已落地 | 静态过关后在受限 globals（白名单 builtins + 受限 `__import__`）内 exec 实例化——静态门与 exec 沙箱两道独立防线；注册走 `StrategyRegistry.register()`，同键异值 = 漂移拒绝（不覆盖不静默替换） |
| **M3 子进程沙箱试跑**（`sandbox_trial`） | ✅ 已落地 | 一次性子进程内以合成 context 试跑 propose；独立 runner 复刻受限 globals（双防线跨进程）；Windows 无 rlimit → 墙钟超时击杀（语义覆盖 CPU 上限），真 rlimit/Job Object 留 Linux 部署轮；fail-visible：超时/崩溃/无标记/非法 Verdict 一律可读拒绝；宿主侧出口再验（`_verdict_dict_issues` 第二道网）；试跑耗时 `sandbox_ms` 随注册事件入档 |
| **受理管线**（`process_submission`） | ✅ 已落地 | inbox 落件 → 静态校验 → 沙箱试跑 → 注册 → 移档 processed/；`data_root` 显式才落账（registrations.jsonl，幂等键 submit_id），无显式 root 只 dry-run 不落账（M5 刀 2「无显式 root 拒写」纪律） |
| **fail-visible 落档**（`write_reject`） | ✅ 已落地 | 拒绝原因落 `rejects/<YYYYMMDD>/<submit_id>.md`，按日归档；禁止静默丢弃 |
| **fail-open 装载**（`load_registered`） | ✅ 已落地 | processed/ 件装配进 registry；幂等（重复装载计 skipped）；单插件坏件不拖垮其余；不重复跑沙箱（试跑是受理时一次性动作） |
| **限频（daily_limit）** | 📋 **已规划** | 工单化按日限频（registrations+rejects 同日合计口径），属军团-M5 拍板件范围，他线工位施工中，本文不重复主张 |
| **rejects 按日归档目录纪律** | 📋 **已规划** | 同上，军团-M5 拍板件范围，他线工位施工中 |

### 1.2 收件箱三区纪律（bridge_inbox/）

```
agent_platform/bridge_inbox/
├── inbox/      # 待受理（只增不删，append-only；处理完整目录移走，不覆盖）
├── processed/  # 受理通过（首个试点 20261018_sma20_rsi14 = SMA20+RSI14 已入库并注册落账）
└── rejects/    # 拒绝档（按日归档 <YYYYMMDD>/<submit_id>.md，人工唯一失败反馈源）
```

- inbox 非 AP 运行时路径，挂 `DATA_ROOT` 之外；账本（`data/codex_bridge/registrations.jsonl`）在显式 data_root 之下——「收件箱在仓、账本在根」两分。
- 提交方以 reject 档为唯一失败反馈源，修订后以**新 submit_id** 重提；通过也不发通知（影子 run 数据即通过证明）。

### 1.3 已接入的主链挂点

- `run_shadow.py` 装配挂载点 fail-open 接线：注册成功的 codex_ 插件自动进下一轮影子装配；
- `tests/test_codex_bridge.py` 34 例（恶意样本集固化为回归测试）；全仓 638 绿；
- evidence_collect 第十一证据源自动覆盖新插件绩效（bridge 侧不单独评估）。

---

## 二、目标架构：军团侧 ↔ 主仓侧职责边界

### 2.1 一句话定位（不变式）

**Codex bridge 是军团侧代码生成代理与 AP strategy 域之间的单向管道，只做「进」：代码 → 校验 → 注册。** 军团-M1/M2/M3 已完成该管道的静态门/试点/沙箱，军团-M4/M5 完成「进之后看得见」与「受理口收敛」；本文主张的目标架构不扩边，只把两侧职责边界写清楚，为 digital-legion 对接（§五）立柱。

### 2.2 职责边界表

| 职责 | 军团侧（Codex / digital-legion） | 主仓侧（agent_platform） |
|---|---|---|
| 策略代码产出 | ✅ Codex 类 CLI 代理生成单文件插件（一文件一 StrategyAgent 子类，纯标准库白名单依赖） | ❌ 不代写、不代改插件 |
| 提交 | ✅ 以 submit_id 工单化投递 bridge_inbox/inbox/ | ❌ inbox 只收不改（append-only） |
| 校验（静态/沙箱） | ❌ 军团侧无校验权、无校验器副本（防止两侧规则漂移） | ✅ M1 静态门 + M3 沙箱门，恶意样本集回归锁定，规则放松必须走拍板 |
| 注册 | ❌ **R5：Codex 永远拿不到 Registry 句柄** | ✅ 注册权独占（AP 侧 process_submission → StrategyRegistry.register，漂移拒绝） |
| 账本与对账 | ❌ 不接触 AP 账本 | ✅ registrations.jsonl / strategy_runs.jsonl / 影子 m5.v1 / 证据仓三方对账（军团-M4） |
| 失败反馈 | ✅ 只读 rejects/<日期>/<submit_id>.md 档 + stderr（修订重提的依据） | ✅ fail-visible 落档，禁止静默丢弃；通过不发通知 |
| 运行与绩效 | ❌ 不参与运行时、不看账本 | ✅ 影子 run → strategy_runs.jsonl → evidence_collect 第十一证据源 |
| 凭据 | ✅ **R2：Codex 运行环境零凭据**；提交物内凭据形态即拒 | ✅ bridge 自身零网络、除 rejects/processed/registrations 落档外零写 |

### 2.3 数据流目标态（M4/M5 落地后）

```
[军团侧 Codex 工单] ──单文件插件──▶ bridge_inbox/inbox/
                                        │ （主仓侧，唯一受理路径）
                                        ▼
                          [军团-M5 CLI 受理薄壳]（daily_limit 配置化）
                                        │
                          [M1 静态门] ─拒─▶ rejects/<YYYYMMDD>/ (fail-visible)
                                        ▼
                          [M3 沙箱试跑门] ─拒─▶ rejects/ 同上
                                        ▼
                          [StrategyRegistry.register]（漂移拒绝）
                                        │
                     ┌──────────────────┴──────────────────┐
                     ▼                                     ▼
              processed/ 移档 +                    run_shadow 装配（fail-open）
              registrations.jsonl 落账                   │
                     │                                  ▼
                     ▼                          strategy_runs.jsonl
              [军团-M4 三方对账 + 观察档]                  │
              （周一巡检附加观察行，                       ▼
               垫底标记只留痕不生效）            evidence_collect 第十一证据源
```

边界自证口径：军团侧到主仓侧只有一条进入线（inbox）；主仓侧到军团侧只有一条反馈线（rejects 档）。两线都不携带凭据、账本内容或 Registry 句柄（R1 单向通道）。

---

## 三、与心跳 adapters 的关系（heartbeat.py 第一刀先例）

### 3.1 先例判值

`adapters/heartbeat.py`（Paperclip 心跳对接第一刀，K-ORG-190 判例链）确立了 adapters/ 层对接**军侧 ↔ 主仓**的四个可平移判值，Codex bridge 与之同构：

| 判值 | heartbeat.py 口径 | codex_bridge 对应 |
|---|---|---|
| **探针抽象**：部署未发生不造不可验的真实探针（NullProbe 诚实语义） | 未接线的 Docker/网络探针不做，先以协议 + NullProbe/FileProbe 立契约 | Codex 工单受理未网关化前，先以进程内 `process_submission` 立契约；CLI 薄壳（军团-M5）只是形态收敛不是能力扩张 |
| **文件为界**：两侧以状态文件交互，不直连进程/端口 | hb.v1 → `data/heartbeat/heartbeat.jsonl`，军侧 8790 部署后由计划任务承载周期化 | 提交物 → `bridge_inbox/inbox/`，反馈 → `rejects/<日期>/`；不开端口、不做 HTTP 受理服务（军团-M5 §3.2 负空间显式声明） |
| **幂等 + fail-visible 安全阀** | 无显式 root 拒写；先全量校验后开写；账本缺失 = NOTICE exit 0 不假绿 | data_root 显式才落账；先校验后 exec；账本缺失 dry-run stderr 提示不假绿；拒绝全程落档 |
| **零凭据零网络** | 模块自身零凭据、零网络、零真实探针 | 模块自身零网络、除三区落档外零写；CRED_PATTERN 源码门 |

### 3.2 K-ORG-190 今日判例的两条直接适用

1. **二实体铁律（拍板③）**：心跳侧已拍板 `paper_engine`（军侧 8790 容器）与 `paperclip_org_shell`（主仓探针）不得混用。Codex bridge 侧同构口径：**提交来源实体 = Codex 工单（军团侧），受理与注册实体 = agent_platform bridge（主仓侧）**，registrations.jsonl 行与 reject 档均以 submit_id 关联工单，两实体在账本上可一眼分辨（strategy_id `codex_` 前缀即来源隔离标记）。
2. **桥接器形态（拍板⑤）**：`tools/legion_heartbeat_bridge.py` 的「run-once 非守护、不自建循环、跨仓依赖只锁主仓」三纪律，是**主仓侧桥接件的形态判例**。Codex bridge 受理面（军团-M5 CLI 薄壳）沿用同形：run-once 受理、非守护进程、不常驻不轮询；digital-legion 侧若需周期化投递，由军团侧自己的调度承载，主仓侧只提供文件入口。

### 3.3 adapters/ 对接层的三件套格局

M5 门 4 拍板「Paperclip 心跳 / Codex bridge / Hermes gateway 走 adapters/ 对接」，现状两件已在位：

| adapter | 方向 | 交互介质 | 状态 |
|---|---|---|---|
| `adapters/heartbeat.py` | 军侧引擎状态 → 主仓账本（进） | heartbeat.jsonl | 第一刀已落地，27/27 测试绿 |
| `adapters/codex_bridge.py` | 军侧代码产出 → 主仓注册（进） | bridge_inbox/ 三区 | M1-M3 已落地，M4/M5 已拍板 |
| Hermes gateway（编排面） | 编排指令进主仓（进） | 待设计 | 未开工（不在本文范围） |

三者共享同一安全骨架：**文件为界 + 幂等 + fail-visible + 零凭据 + 不开端口**。本文建议将此骨架固化为 adapters/ 对接层公约（表述性约定，非新代码），后续 Hermes gateway 与 digital-legion 对接直接继承。

---

## 四、安全边界（只读 propose、授权显式化）

### 4.1 五红线核对（R1-R5 平移自原设计稿 §1.2，零新增授权）

| # | 红线 | 现状落实 | 目标态强化点 |
|---|---|---|---|
| R1 单向通道 | 只进不出 | 进= inbox；出= rejects 档 + stderr，无内部状态回传 | M5 拍板件：受理接口 grep 自证无回传通道（判据 3）；digital-legion 对接后同口径，出方向账本投影归 legion_adapter（§五），与 bridge 不相交 |
| R2 零凭据 | 凭据形态即拒 | CRED_PATTERN 全文扫 + 模块自身零凭据 | CLI 薄壳零凭据前置（M5 拍板件）；军侧提交环境人工复核（M2 判据 3 口径延续） |
| R3 零执行权 | 策略只产建议 | `execution_allowed` 恒 False，下单归 K-STOCK execution 线 | 任何演进不新增执行路径（M5 §3.2 负空间）；risk_passed 置 True 的写入权独属验收链（契约映射稿 §6.1，bridge 层永不触碰） |
| R4 只读 propose | 插件不写账本 | 白名单 import 禁写面 + 沙箱试跑拦截 + 落账只能走 record_verdicts 正门 | 插件契约零改动；观察档（军团-M4）走 codex_bridge 专属目录，不经 propose 写任何域账本 |
| R5 注册权在 AP 侧 | Codex 无 Registry 句柄 | 注册只在 process_submission 内部完成 | 受理入口收敛唯一化（M5 判据 5，直调点列名 grep 可审计）；digital-legion 侧对接后入口仍唯一 |

### 4.2 授权显式化（三道显式闸门）

1. **落账显式**：`data_root` 不传不落账（dry-run）——对齐 heartbeat「CLI 无 --persist-root 只打印不落账」与 M5 刀 2「无显式 root 拒写」同款安全阀。
2. **运行授权显式**：注册 ≠ 运行授权。插件注册进 Registry 后能否跑、以什么版本快照跑，由 RunAuthorization 授权快照绑定（strategy_registry 既有语义），bridge 只负责「进」，不触碰授权面。
3. **边线升级显式**：限频数值、垫底标记升级为限频依据、rlimit 平移、任何远程受理形态——全部单独拍板，无一项自动生效（军团-M4「只留痕不生效」与 M5「不开端口」口径延续）。

### 4.3 纵深防线小结（现状实测在位）

静态门（AST 白名单）→ exec 沙箱（受限 globals）→ 子进程沙箱试跑（跨进程双防线 + 墙钟击杀）→ 漂移拒绝（同键异值）→ 宿主侧 Verdict 出口再验 → 装载 fail-open。六道独立防线，任一单点失守不致穿透；每道防线的拒绝行为都有回归测试锁定（恶意样本集 34 例）。

---

## 五、演进路线：对接 digital-legion 侧的接口设想（军团-M6 设想）

### 5.1 契约基线（借用既有对表结论，不重开谈判）

`策略系与军团契约映射设计.md` 已完成 digital-legion contracts v1（落盘版四文件，`C:\项目\digital-legion\contracts\`）与引擎侧的对表，关键结论直接约束 bridge 演进：

- **引擎建议件 ≠ legion Verdict**：legion Verdict = 风控裁决件（decided_by=风控部，risk_passed 显式 bool 缺省即非法）；引擎 strategy_registry.Verdict = 研究建议件。建议载荷走 IntelItem.payload + ResearchReport，**bridge 产出的插件建议件永不映射为 legion Verdict**（R3 的契约层落实）。
- **三套 Verdict 同名异义**（AP contracts M0 / engine / legion）：对接实现必须显式别名 import，防混淆（对表稿 §6.5-3 建议 legion Verdict 更名 RiskVerdict，待拍板）。
- **risk_passed 产出环节缺码**是全链最大缺口，属验收链（RiskGuard）工单，**不在 bridge 范围**；bridge 只保证建议件不带任何 risk_passed 语义出厂。

### 5.2 与 legion_adapter 的分工：「进」「出」两桥不相交

digital-legion 对接后，adapters/ 层将同时存在两座桥，方向相反、载荷不同、永不交叠：

| | codex_bridge（进桥） | legion_adapter（出桥，对表稿 §2 已签名级设计） |
|---|---|---|
| 方向 | 军团代码 → AP 注册 | AP 账本 → 军团契约形态 |
| 载荷 | 单文件插件源码 | 影子行/判决行 → IntelItem / ResearchReport 投影 |
| 写面 | inbox/processed/rejects/registrations | **零写面**（纯函数投影，只返回不落盘） |
| 凭据面 | 零 | 零 |

R1 单向通道在新格局下的表述：**进桥只进代码，出桥只出公开账本投影**——两条线介质不同（inbox vs 投影返回值）、内容不重叠、无共享句柄。军团侧拿到的任何东西都是账本读侧投影，不含凭据、不含 Registry 句柄、不含未公开内部状态。

### 5.3 军团-M6 设想范围（全部【设想，待拍板】）

1. **提交物工单化贯通**：Codex 工单（`Codex工单作者指南` 已有模板）→ digital-legion 侧任务编排产出插件 → 投递 bridge_inbox/inbox/。主仓侧零改动（入口即文件），军团侧自行承载工单调度；submit_id 规则建议带上军团工单号前缀（如 `<工单号>_codex_<slug>`）以贯通两侧审计。
2. **受理状态回读接口（只读）**：军团侧查询某 submit_id 是否已受理/注册——**建议不做主动推送**，只提供「读 processed/ 与 rejects/ 目录」的既成事实口径（文件在即是状态），必要时加一条只读投影函数；不开端口、不做 RPC。
3. **契约形态回传（远期，另立拍板件）**：若军团侧需要 codex_ 系插件的绩效观察档（军团-M4 产物），建议走 legion_adapter 同款「纯函数投影、零写面」模式输出 IntelItem 形态，**而不是**让军团侧直读 AP data/ 目录；该投影是否立项由老田单独拍板。
4. **Hermes gateway 对位**：多策略编排归 Hermes Kanban（原设计稿 §1.3 负空间），bridge 只管「进」的口径在 M6 不变；M6 与 Hermes gateway 的边界 = 代码受理面 vs 编排执行面，两者通过 Registry 状态间接衔接（gateway 读 manifest，不碰 bridge）。

### 5.4 里程碑序列总览（含编号纪律）

```
军团-M1 静态校验器        ✅ 已落地（2026-10-18）
军团-M2 首个 Codex 试点    ✅ 已落地（codex_sma20_rsi14 入 processed/ 并注册落账）
军团-M3 沙箱试跑          ✅ 已落地（墙钟口径，rlimit 留 Linux 轮）
军团-M4 对账自动化+观察档  📋 已拍板待施工（依赖周一 17:10 首跑真数据）
军团-M5 CLI 受理薄壳       📋 已拍板待施工（限频配置化；限频+按日归档他线工位施工中）
军团-M6 digital-legion 对接 🔶 本文设想（§5.3，未拍板；依赖 M5 收口与 QT-M0 开放点追认）
Linux 轮 rlimit 平移       📋 已拍板条目化（M5 规划稿 §3.3 七条清单，部署窗口另定）
```

---

## 六、风险与缓解（对齐原设计稿 §5 风险表口径）

原设计稿 §5 六条风险全部继续有效，本文按「已实现 / 已规划 / 待拍板」三态重述，**不新增风险、不放松缓解**：

| # | 风险 | 三态 | 缓解（现状） |
|---|---|---|---|
| 1 | 动态 import 绕过（`__import__("os")`、字符串拼接、importlib） | 已实现 | 白名单 import 制 + AST 扫 Name/Attribute + M3 沙箱兜底；对抗样本在回归集内 |
| 2 | 资源消耗（propose 死循环/大内存拖垮影子主链） | 已实现（Windows 口径） | 插件无状态 + 行数上限 + M3 墙钟击杀；真 rlimit 留 Linux 轮（M5 §3.3 清单条目化） |
| 3 | 同名插件覆盖（重提交静默换掉旧策略） | 已实现 | 漂移拒绝复用 StrategyRegistry 语义；改版必须新 ID |
| 4 | 校验器自身被绕过/放松 | 已实现 | 校验器只读 + 恶意样本集固化回归；注册入口 grep 可审计；reject 档 fail-visible；M5 判据 5 收敛受理入口唯一化 |
| 5 | 提交物夹带非策略代码 | 已实现 | 单文件单类（AST 强制）+ 白名单 import + 禁写面 + 大小上限 |
| 6 | Codex 产出质量噪声淹没 reject 档 | **已规划** | **submit_id 工单化按日限频（registrations+rejects 同日合计口径）+ rejects/<YYYYMMDD>/ 按日归档——军团-M5 拍板件范围，他线工位施工中，本文按「已规划」口径引用，不重复主张、不在本稿施工** |

M6 设想新增前的两条前置核验（非新风险，是开工厂否的门）：

- **依赖门**：M6 设想依赖军团-M5 收口（受理入口唯一化）与 QT-M0 开放点追认（三套 Verdict 收敛、legion_adapter 落位豁免）——两者未收口前 M6 不开工；
- **实体门**：M6 提交贯通后，「谁提交」必须保持二实体可辨（对齐 K-ORG-190 拍板③判例）：账本与 reject 档上的 submit_id/strategy_id 可一眼分辨军侧工单来源，禁止共用身份。

---

## 七、与现状的差距清单（本稿主张 vs 今日实况）

| # | 差距 | 现状 | 目标态 | 归属 |
|---|---|---|---|---|
| 1 | 受理入口未收敛 | process_submission 为进程内函数，调用方手工传参 | CLI 工单薄壳为唯一受理路径 | 军团-M5（已拍板） |
| 2 | 限频未配置化 | daily_limit 依赖调用方显式传 | 网关配置默认值（建议 3/日） | 军团-M5（已拍板，他线施工中） |
| 3 | 三方对账未自动化 | verify_strategy_ledger.py 为手工只读工具 | 接入周一巡检附加观察行 + codex_ 观察档 | 军团-M4（已拍板，待首跑数据） |
| 4 | Windows 沙箱无真资源上限 | 墙钟超时击杀（语义覆盖 CPU） | rlimit（CPU/内存）+ 墙钟兜底 | Linux 轮（已拍板条目化） |
| 5 | 军团侧提交未贯通 | Codex 产出靠人工投递 inbox | 工单化自动投递 + submit_id 贯通两侧审计 | 军团-M6（本文设想，待拍板） |
| 6 | 受理状态无回读口径 | 文件在即是状态（事实成立但未成文） | 只读投影口径成文，禁推送禁 RPC | 军团-M6（本文设想，待拍板） |
| 7 | adapters/ 对接公约未固化 | 三判值散在两 adapter docstring | 表述性公约成文（文件为界/幂等/fail-visible/零凭据/不开端口） | 本文建议（零代码） |
| 8 | digital-legion 契约侧开放点 | 三套 Verdict 同名异义、risk_passed 缺产出方 | QT-M0 拍板收敛 | 契约映射稿开放点（非 bridge 范围） |

---

## 八、结论

- 本文为**设计稿（演进篇）**：§一 现状盘点以实现为准；§二/§三/§四 为红线平移与先例判值的成文化（无新授权）；§五 军团-M6 全部【设想，待拍板】；§六 风险表按三态重述，限频+按日归档按「已规划」口径引用他线工位施工；§七 差距清单为拍板议事底稿。
- 核心主张一句话：**Codex bridge 的「进」管道已由军团-M1/M2/M3 打通并被 M4/M5 拍板延伸，下一步唯一值得新增的边是 digital-legion 侧的工单贯通（M6）——主仓侧零新门、零新授权，只把「文件为界、只读回读、二实体可辨」三件事成文。**
- 零代码零测试改动——本文档本身是本任务唯一产出，不 add 不 commit。
