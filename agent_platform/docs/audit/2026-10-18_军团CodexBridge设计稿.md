# 军团 Codex bridge 设计稿（纯文档，待老田拍板后施工）

- **日期**：2026-10-18
- **作者**：dev-bridge-doc（老田个人 AI 组 C 路文档代理）
- **状态**：**拍板三件已落地（2026-10-18 老田令执行）**——①§2.2 白名单制定稿（七件套纯计算库 + 宿主契约模块 strategy_registry）②军团-M1/M2/M3 批准按序施工（M1/M2 本日完成，M3 沙箱化待施工）③首个试点策略 = SMA20+RSI14（`codex_sma20_rsi14`，已受理入 processed/ 并注册落账）。施工产物：`adapters/codex_bridge.py`（M1 校验器+受理管线+fail-open 装载）、`bridge_inbox/`（inbox/processed/rejects 三区纪律）、`run_shadow.py` 挂载点 fail-open 接线、`tests/test_codex_bridge.py` 25 例（恶意样本集固化为回归测试）；全仓 629 绿。§2/§3/§5 中与实现冲突处以实现为准（校验器进程内 AST 门为 M1 形态，子进程沙箱留 M3）。
- **编号纪律**：本文里程碑一律用 **军团-M\*** 前缀（军团-M1/M2/M3），与 AP-M\*（AP 第十一域等）严格区分；对齐 M5 门 4 文档「军团方案 M0-M5 与 AP M0-M6 撞号，此后文档一律加前缀」的纪律。
- **决策依据**：`docs/audit/2026-10-18_M5门4签字收口与军团三拍板落地.md`（总纲 v1.0 有保留批准 + 仓库路线 = agent_platform 扩展）、`agent_platform/engine/strategy_registry.py`（StrategyAgent/Verdict/StrategyRegistry 契约）、`agent_platform/engine/shadow_strategies.py`（首批插件范式）。

---

## 一、定位与边界 【红线】

### 1.1 一句话定位

**建议**：Codex bridge 是军团侧代码生成代理（Codex 类 CLI agent）与 AP strategy 域之间的**单向管道**，只做一件事：

```
Codex 产出策略代码 → 静态/沙箱校验 → 注册为 StrategyAgent 插件
```

### 1.2 边界（红线，逐条继承自存量契约）

| # | 红线 | 来源 |
|---|---|---|
| R1 | **单向通道**：bridge 只进不出。Codex 的产物只能以「策略插件提交物」形态进入 AP，bridge 不反向把 AP 内部状态、账本、凭据回传给 Codex | 总纲 v1.0「研究/执行物理分离」原则 |
| R2 | **Codex 零凭据**：Codex 运行环境不持有任何 API key、token、账户凭据；bridge 提交物内出现任何凭据形态字符串（key/secret/token/password 模式）即拒绝 | strategy 域红线「本域不持有任何凭据」 |
| R3 | **零执行权**：策略只产建议（Verdict），`execution_allowed` 恒 False；下单归 K-STOCK execution 线。bridge 产物不新增任何执行路径 | `strategy_registry.py` 模块 docstring |
| R4 | **只读 propose**：插件 `propose(context)` 只读不写，不得写任何域账本（含 strategy_runs.jsonl——落账只能走 `record_verdicts` 正门） | StrategyAgent 接口注释 |
| R5 | **注册权在 AP 侧**：Codex 只提交代码，注册动作由 AP 侧 bridge 校验器完成；Codex 永远拿不到 Registry 句柄 | 本文新增（建议） |

### 1.3 不做什么

- 不做策略执行、不碰 K-STOCK execution 域；
- 不做策略绩效评估（已有 evidence_collect 第十一证据源 + 影子账本对账）；
- 不做多策略编排（编排归 Hermes Kanban，本桥只管「进」）。

---

## 二、契约设计 【建议，待拍板】

### 2.1 提交物形态

**建议**：单文件策略插件，一个文件 = 一个 StrategyAgent 子类。

```
提交物 = bridge_inbox/<submit_id>/strategy_<strategy_id>.py
```

硬性要求（校验器逐条核对，任一不过即拒）：

1. 单文件、纯标准库（对齐 shadow_strategies 范式：**零 pandas/零第三方依赖**，影子主链 fail-open 纪律——策略层不得引入重型依赖拖垮主链）；
2. 文件内**恰好定义一个** `StrategyAgent` 子类，类属性 `strategy_id` 非空、`stance ∈ ("bull","bear","neutral")`；
3. 实现 `propose(self, context) -> Verdict`，返回前必须能过 `Verdict.validate()`（recommendation 五档枚举 / confidence ∈ [0,1] / stance 合法 / symbol 非空）；
4. `context` 契约与首批插件对齐：`{"symbol": str, "closes": [float,...]}`（升序时间序列）；样本不足必须降级 hold 而非抛异常（对齐 shadow_strategies 降级范式）;
5. 文件头 docstring 声明策略逻辑一句话 + 阈值来源，供审计留痕。

### 2.2 静态校验清单（校验器第一道门，子进程只读执行）

| 类别 | 规则 | 判定 |
|---|---|---|
| 禁 import | `os`（允许 `os` 但禁 `os.system/os.popen`？**建议从紧：整禁 os/subprocess/socket/requests/httpx/urllib/http.client/ctypes/multiprocessing/shutil/pathlib 写入口/pty**）、`eval`/`exec`/`compile`/`__import__`/`importlib` 字面调用 | 命中即拒 |
| 禁网络 | 任何 socket/http 关键字 | 命中即拒 |
| 禁文件写 | `open(..., "w"/"a"/"x")`、`Path.write_*`、`shutil` | 命中即拒 |
| 禁凭据形态 | 源码内 `(?i)(api[_-]?key|secret|token|password|credential)\s*=` 模式 | 命中即拒（R2） |
| 接口签名 | AST 解析：唯一 StrategyAgent 子类、propose 存在、strategy_id/stance 合法；**禁止定义 `__init__` 持有可变全局状态**（建议，保持插件无状态） | 不过即拒 |
| 大小/行数上限 | **建议** ≤ 300 行 / ≤ 16KB，超限拒（压缩攻击面） | 超限即拒 |

**判定标签**：以上为「建议」级——禁 import 清单最终宽度（os 整禁 vs 白名单放行 math/statistics 等纯计算库）待老田拍板。**建议采用白名单制**：只允许 `math`/`statistics`/`collections`/`itertools`/`functools`/`typing`/`dataclasses`，白名单外一律拒（黑名单永远追不上绕过手法）。

### 2.3 动态校验（沙箱第二道门，军团-M3 落地）

静态校验挡不住运行时行为（见风险 §5.1）。**建议**第二道门为子进程沙箱试跑：

- 子进程 + 资源上限（CPU 时间 / 内存 / 禁网络），喂固定样例 context（含边界样本：空 closes / 恰好 WINDOW 长度 / 极端值）；
- 校验 propose 产出全部为合法 Verdict 且 strategy_id 一致；
- 试跑进程无文件写权限（只读挂载工作目录）。

### 2.4 漂移拒绝复用 StrategyRegistry 语义 【红线平移】

- 注册走 `StrategyRegistry.register()`，**同 strategy_id 重复注册 = 漂移拒绝**（`strategy_registry.py:87` 同键异值判例平移），不覆盖、不静默替换；
- Codex 想「改版」已有策略 → 新 strategy_id（如 `codex_trend_v2`），旧版本保留，绩效对账可追溯；
- `strategy_id` 命名空间：**建议** Codex 产物强制前缀 `codex_`，与 shadow_ 系首批插件天然隔离，审计一眼可辨来源。

---

## 三、流水线 【建议，待拍板】

```
[Codex CLI] ──产出单文件──▶ [bridge_inbox/]
                                 │
                                 ▼
                    [1] 静态校验器（子进程只读，§2.2）
                         │ 拒 ──▶ fail-visible：拒绝原因写入
                         │         bridge_rejects/<submit_id>.md，人工可见
                         ▼ 过
                    [2] 沙箱试跑（子进程，§2.3，军团-M3 起）
                         │ 拒 ──▶ fail-visible 同上
                         ▼ 过
                    [3] Registry.register()（同键异值=漂移拒绝）
                         │ 拒 ──▶ fail-visible 同上
                         ▼ 成
                    [4] 影子 run 天然纳入：下一轮 run_shadow 的
                        build_default_registry 装配即带新插件，
                        周一 17:10 首跑自动产出 strategy_runs.jsonl
                        （verdict_key 指纹可对账，evidence_collect
                        第十一证据源自动覆盖）
```

### 3.1 fail-visible 显式返回 【建议】

- 每次拒绝必须在 `bridge_rejects/<submit_id>.md` 留档：拒绝阶段（静态/沙箱/注册）、命中规则、源码片段定位；**禁止静默丢弃**（对照判例：测试把 bug 固化成契约最危险——bridge 校验器若静默放过一次，等于把漏洞固化成通道契约）;
- 提交方（Codex 工单）以 reject 文档为唯一失败反馈源，可修订后重新提交（新 submit_id，inbox 不覆盖）;
- **通过也不发通知**：bridge 只在 AP 侧账本登记注册成功事件，无推送——影子 run 数据本身即通过证明。

### 3.2 inbox 纪律

- `bridge_inbox/` 只增不删（append-only，幂等纪律平移）；处理完移入 `bridge_inbox/processed/<submit_id>/`，reject 的留 `bridge_rejects/`;
- inbox 非 AP 运行时路径，挂 `DATA_ROOT` 之外（对齐 M5 刀 2 判例：无显式 root 拒绝落盘——bridge 落账同样显式传 root）。

---

## 四、里程碑建议（军团-M*）【待拍板】

> 以下均为**建议**，老田拍板后施工；编号与 AP-M* 并行不混用。

### 军团-M1：bridge 静态校验器

- **范围**：`adapters/` 下新增 codex bridge 校验器（AST 白名单 import + 接口签名核对 + 禁凭据模式 + 行数上限）+ `bridge_rejects` fail-visible 落档；**不含沙箱试跑**（先行用静态门跑通全流程）。
- **验收判据**：
  1. 喂一个合规插件（可改造 shadow_strategies 任一）→ 校验通过并成功 `register()`，`list_ids()` 可见；
  2. 喂 ≥5 个恶意样本（subprocess import / eval / open 写 / 凭据赋值 / 双子类）→ 全部拒绝且 reject 文档逐条可读；
  3. 同 strategy_id 二次提交 → 漂移拒绝（复用 StrategyRegistry 语义，测试断言 StrategyError）;
  4. 校验器自身零网络、零写（除 rejects/processed 目录），单测覆盖。

### 军团-M2：首个 Codex 策略试点

- **范围**：用 Codex 实际生成 1 个真策略（**建议**：SMA20 + RSI14 双条件，逻辑写在 docstring 供比对），走完 inbox → 校验 → 注册 → 周一 17:10 影子首跑全链路。
- **验收判据**：
  1. 新插件出现在周一影子 run 的 `strategy_runs.jsonl`，verdict_key 指纹与 `evidence_collect` 第十一证据源对得上账；
  2. 样本不足场景降级 hold（不抛异常、不断影子主链——fail-open 纪律实测）;
  3. 全程 Codex 侧零凭据接触（人工复核提交环境）。

### 军团-M3：沙箱化（动态校验第二道门）

- **范围**：§2.3 子进程资源受限试跑并入流水线（CPU/内存上限、禁网、只读 FS）。
- **验收判据**：
  1. 恶意样本（静态过关但运行时起线程/尝试 importlib 的对抗样本）在沙箱门被拦;
  2. 死循环策略被 CPU 上限击杀 → fail-visible 落档，主进程不受影响;
  3. 沙箱开销有基线数据（单次试跑耗时记录入 reject/accept 档）。

---

## 五、风险清单 【建议，逐条给缓解】

| # | 风险 | 判例对照 | 缓解 |
|---|---|---|---|
| 1 | **动态 import 绕过**：`__import__("os")`、字符串拼接 import、`importlib`、装饰器内联执行 | 静态黑名单永远追不上绕过手法 | 白名单 import 制（§2.2）+ AST 扫 Name/Attribute 节点 + 军团-M3 沙箱兜底 |
| 2 | **资源消耗**：策略 propose 内死循环/大内存计算拖垮影子主链 | 影子主链 fail-open 纪律：策略层不得拖垮主链 | 插件无状态 + 行数上限 + M3 资源上限子进程；M1 期过渡缓解=run_shadow 侧单策略产出加超时包裹（fail-open 跳过） |
| 3 | **同名插件覆盖**：Codex 重提交同 strategy_id 静默换掉旧策略 | StrategyRegistry 同键异值=漂移拒绝（strategy_registry.py:87） | 复用漂移拒绝，不新增任何覆盖路径；改版必须新 ID |
| 4 | **校验器自身被绕过**：绕开 bridge 直接改引擎装配、或校验器被「优化」放松规则 | **判例：测试把 bug 固化成契约最危险**——校验器一旦静默放过一次，漏洞即成通道契约 | ①校验器只读+自身单测锁定拒绝行为（恶意样本集固化为回归测试）；②注册入口只在 AP 侧 build_default_registry 装配路径，grep 可审计；③reject 档 fail-visible，拒绝行为人工可见 |
| 5 | **提交物夹带非策略代码**：一个文件里藏工具函数/数据外传逻辑 | R1 单向通道 | 单文件单类（AST 强制）+ 白名单 import + 禁文件写 + 行数上限 |
| 6 | **Codex 产出质量噪声**：大量垃圾提交淹没 reject 档 | — | submit_id 工单化限频（建议：每 Codex 会话 ≤3 次）， rejects 按日归档 |

---

## 六、与存量基建对齐清单

| 存量资产 | bridge 复用方式 |
|---|---|
| `StrategyAgent`/`Verdict`/`StrategyRegistry`（engine/strategy_registry.py） | 提交物实现目标接口；注册复用 register 漂移拒绝；产出复用 Verdict.validate 出口校验 |
| 三策略插件范式（engine/shadow_strategies.py） | 提交物模板蓝本：纯 Python、零 pandas、样本不足降级 hold |
| 影子 run + build_default_registry | 注册成功的插件自动进下一轮装配，无额外接线 |
| strategy_runs.jsonl + verdict_key 幂等键 | 试点对账凭据（M2 验收判据 1） |
| evidence_collect 第十一证据源 | 新插件绩效自动纳入证据链，无需 bridge 侧单独评估 |
| 幂等/显式 root/fail-visible 纪律 | inbox append-only、落账显式传 root、拒绝路径全程可见 |

---

## 七、结论

- 本文档为**设计稿**：§1 边界为红线平移（无新授权），§2/§3/§4/§5 均为建议，**待老田拍板后施工**；
- 施工顺序建议：军团-M1（静态校验器）→ M2（试点）→ M3（沙箱化），每步验收判据见 §4；
- 零代码改动发生在拍板之前——本文档本身是本任务唯一产出。
