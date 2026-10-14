# 2026-10-14 审计后集中修复（fix-audit）

执行者：fix-audit（CodeBuddy）。范围：jev-eval 侧审计确认 P1/P2 共 8 项 + quant_backtest 测试侧 2 项。
约束遵守：未推飞书、未注册计划任务、未动生产数据、未跑正式评估（不消耗 AI 调用）；解释器 `"C:/Program Files/Python311/python.exe"`。

## 备份与回退

所有被改文件均有同目录副本 `.bak_20261014_fix`（另有既有的 `.bak_20261014` 系列不动）：

| 文件 | 回退方法 |
|---|---|
| jev-eval/run_oos_eval.py | `cp run_oos_eval.py.bak_20261014_fix run_oos_eval.py` |
| jev-eval/jev_oos_question_gen.py | 同上 |
| jev-eval/jev-gate.env.ps1 | 同上（回退后删除 env_fallback.flag 逻辑随之消失，回退行为回到静默） |
| jev-eval/jev_eval.py | 同上 |
| jev-eval/reencrypt_env.py | 同上 |
| jev-eval/migrate_env_dpapi.py | 同上 |
| jev-eval/rotate_tushare_token.ps1 | 同上 |
| quant_backtest/test_backtest_trade_layers.py | 同上 |

产物（新增，非覆盖）：`oos_question_set_v2.json`、`oos_question_set_v1_FLAWED.note.md`（v1 json 保留未动）。

## 逐项修复记录

### jev-eval 侧

**1. run_oos_eval.py — DPAPI 注入 setdefault → 强制覆盖 + WARN**
- 修复：`os.environ.setdefault(_k, _v)` 改为 `os.environ[_k] = _v`；注入前若环境已有同名字段，打印 `WARN: 环境已有 <键名> 残留值，已用 DPAPI 解密值强制覆盖`（只报键名，不含值）。
- 验证：`JEV_API_KEY=sk-fake-stale-value` 环境下跑 `run_oos_eval.py --dry-run`，WARN 正常打出、覆盖生效、dry-run 通过（不调 API）。

**2. jev_oos_question_gen.py — v2 题集脱敏（开卷题修复）**
- v1 缺陷：state 明文含市场域字样与评分数值，答案可直接读出，72 题全部无区分度。
- v2 脱敏规则（考什么藏什么，按题型定向）：
  - 考市场域的题（noul 市场真/假、choice 市场四选一）：state 中市场域字样（`CN（A股）/US（美股）/HK（港股）/CRYPTO（加密货币）`及裸键/A股）→ `市场X`，答案须从 ts_code、信号理由推断；
  - 考评分的题（noul 评分真/假、score 三档）：state 中评分数值 → `[REDACTED]`；score 题仍问档位（推断），noul 评分陈述改为**档位级**真/假（不再给具体数值，防抄答案）；
  - 与 v1 完全一致：抽样口径（排除规则/分层/sqrt 配额/种子 20261014/每事件 2 题/题型 4 循环与 2 交替/choice 轮转）、id 编号、src 留痕字段。
- 脱敏断言：生成器内每题用答案信息反查 state（市场域正则、`%.4f` 精确评分数值），命中即 raise。开发中真实拦下一例误报（score=1.0 的裸 `str()` 子串命中理由文本 `rsi=41.0072` 中的 `1.0`），已收紧为「[REDACTED] 占位必须存在 + `%.4f` 精确值不得残留」。
- 独立复检：题型定向全量扫描 72 题，泄露数 **0**。
- 兼容性：jev_eval dry-run 指向 v2 通过（题库校验 + 请求构造正常）。
- 留痕：`oos_question_set_v1_FLAWED.note.md` 说明 v1 缺陷与 v2 差异；v1 文件未删未改。
- **评估状态：待批**。v2 尚未跑任何评估（消耗 AI 调用，另行批准）；run_oos_eval.py 仍指向 v1，正式评估获批后需改指向 v2。

**3. jev-gate.env.ps1:44-47 — 回退静默 → flag 告警闭环**
- 修复：回退分支原子写 `<目录>/env_fallback.flag`（内容=时间戳 + `reason=<键名>`，如 `dpapi_decrypt_failed`/`dpapi_exception`/`enc_missing`，不含任何值）；同日已有 flag 只更新时间戳（保留历史行）；flag 写失败仅 WARN 不阻塞加载。
- 验证（沙箱临时目录）：无 .enc 场景 flag 正常生成 `2026-10-14 14:52:42 reason=enc_missing`；二次运行同日仅更新时间戳；对 plain 全部凭据值做包含性检查，flag 泄漏数 **0**；产线 stub 正常路径（DPAPI 可解）确认**不落** flag 且 JEV_API_KEY 正常加载。

**4. reencrypt_env.py — 模块级执行 → 包 `if __name__ == "__main__"`**
- 修复：重加密+verify 包进 `main()`；被误 import 不再覆盖 .enc。
- 验证：`import reencrypt_env` 无副作用（.enc 未被重写）；本修复后未实际执行重加密（无需轮换）。

**5. migrate_env_dpapi.py — 死代码 + docstring**
- :105 死代码 `rhs = ...`（下一行立即覆盖）删除；:96 docstring 改为「解析明文 ps1 → (out, lines) 元组」。
- 验证：`--verify` → `MIGRATION_OK（10 键解密回读与明文逐键一致；effects 无泄漏）`。

**6. rotate_tushare_token.ps1 — 死变量 + 轮换后清 bak**
- :21 死变量初值 `$EnvFile = stub 路径`（第 70 行以 plain 路径覆盖，从未被读）删除，留注释说明。
- verify 成功分支追加：删除本次轮换生成的带时间戳 bak（含旧 token 明文，不留过期凭据副本）；失败现场保留；清理失败仅 WARN 不阻塞。`$bak` 预置 `$null` 防 token 未变路径引用未定义。
- 验证：Parser 语法解析通过；未实际执行轮换（会动真 token/重启进程，不在本次授权范围）。

**7. run_oos_eval.py 文件头**
- docstring 追加：「⚠ 一次性评估包装器，勿被 import（本模块 import 即做进程级 monkeypatch，且会覆盖环境变量；只允许作为脚本直接执行）」。

**8. jev_eval.py:174 — HTTPError 响应体打印收敛**
- 修复：`e.read()[:200]` → 截前 80 字符 + 只打 status code；正则 `key|token|sk-`（忽略大小写）命中即整段替换为 `[REDACTED：响应体含凭据字样，不打印]`。
- 验证：4 组样例单测（`invalid api key provided`→REDACTED、`Bearer tok`→原样、`error: sk-abc123`→REDACTED、`rate limited`→原样），行为符合预期。

### quant_backtest 侧

**9. test_backtest_trade_layers.py NameError（今日新增测试的 bug）**
- 修复前 traceback：`python test_backtest_trade_layers.py` → `line 104: NameError: name 'pytest' is not defined`（`__main__` 分支用了 `pytest.main` 但文件未 `import pytest`；pytest 收集器跑不触发该分支，故此前 `-m pytest` 掩盖了问题）。
- 修复：补 `import pytest`（仅测试文件，backtest.py 产线逻辑零改动，traceback 亦未指向产线 bug）。
- 测试数字：修复前直接运行即 NameError 崩溃（0/4）；修复后直接运行与 `-m pytest` 均 **4 passed**。

**10. 存量失败对照核对（只跑不改）**
- `test_chanlun_signal_gold.py`：**1 failed / 24 passed 中的那 1 个**——`test_gold_3buy_pivot_breakout_pullback_holds`。根因：信号 dict 现含 `zg`/`zd` 字段（chanlun_signal.py:188），该文件 mtime=10-13 08:42（10-13 改动新增字段），而金样本测试 mtime=10-07 仍按旧字段集断言。**证据链：失败链路全部依赖文件（chanlun_signal/bi/macd/flow/trend/pattern）今日（10-14）零改动；chanlun_signal.py 无 .bak，但其今日未被修改（mtime 为证），故与今日改动无关**。属「10-13 特性新增后金样本未同步」的存量欠账，留待后续修测试（本次只核不改）。
- `test_qpzm_backtest.py`：当前 **全过（无存量失败）**；今日 jevkey 归一化改动（normalize_jev_key）未引入失败。
- 合计两文件：`1 failed, 24 passed`。

## OOS v2 题集样例（oos-n001，同事件脱敏前后对照）

- v1（state 泄答案：市场域+评分直接可读；问题直接问评分值）：
  - state：`候选信号事件（2026-08-07，CN（A股），池类型 PREMARKET）：标的 300836.SZ，策略评分 0.5556。信号理由：均值回归: close=49.80 低于MA20=55.52 10.3%, ...`
  - question：`该候选的策略评分为 0.5556。`（answer=True，答案抄 state 即得）
- v2（脱敏后，须推断）：
  - state：`候选信号事件（2026-08-07，市场X，池类型 PREMARKET）：标的 300836.SZ，策略评分 [REDACTED]。信号理由：均值回归: close=49.80 低于MA20=55.52 10.3%, ...`
  - question：`该候选策略评分所处档位为 中（0.5-0.7）。`（answer=True，须从理由推档位）

## 遗留事项

1. **OOS v2 评估待批**：72 题未跑（消耗 AI 调用）。获批后需把 run_oos_eval.py 的 QUESTIONS_FILE 从 v1 改指 v2（并同步 meta.how_to_run）。
2. `test_chanlun_signal_gold` 的金样本需按 10-13 的 zg/zd 字段同步更新（存量欠账，未在本次授权范围）。
3. rotate_tushare_token.ps1 的 bak 清理逻辑待下次真实轮换时在产线验证（本次仅语法级验证，未实际轮换）。
4. flag 文件 env_fallback.flag 为累积追加式（每日一行），无自动清理；如嫌增长可在后续加轮转。
5. v1 题集文件保留在原位（已标注 FLAWED note），建议评估切换 v2 并留档一段时间后再决定是否归档 v1。

## OOS v2 评估（2026-10-14，老田已批准 72 题×1 遍）

> 关闭「遗留事项 1」。上一节所述 v2 评估已获批准执行；以下为运行记录与结论。

### 运行记录

- 批准：老田批准 72 题×1 遍（不跑 3 遍，正式认定另行批准）。
- 准备：run_oos_eval.py:54 的 QUESTIONS_FILE 由 oos_question_set_v1.json 改指 **oos_question_set_v2.json**（docstring 同步注明 v1 作废禁用）；dry-run 校验通过（state 已见「市场X」「[REDACTED]」占位，题库校验/请求构造正常）。
- 执行：`python.exe -u run_oos_eval.py`，stdout 重定向 `/c/Temp/jev_oos_v2_eval.log`（不走管道）。
- 起止：15:15:41 → 15:18:36，**耗时 2 分 55 秒**；退出码 0，未触发连续 5 题熔断。
- 请求/重试：共 **78 次请求**（72 题 + 6 次首试失败重试）；5 次重试成功（SSL UNEXPECTED_EOF / RemoteDisconnected，走 7890 代理），1 题 **oos-s012** 重试后仍失败，按协议记 None 计错。
- 成本粗估：成功响应 72 次，token 总量 **31,484**（input+output），延迟中位 561ms / 均值 850ms；未观察到 451/内容级拒绝。
- 解释器：C:/Program Files/Python311/python.exe；未触碰 DPAPI 密钥/明文、未重启常驻进程。

### 结果（result-20261014-151836.json + -bydomain.json）

总体准确率：**77.8%（56/72）**；排除网络故障题后 78.9%（56/71）。三遍一致率 98.6%（1 遍下即单次自洽标记）。

| 维度 | 准确率 | 对照平凡基线 |
|---|---|---|
| noul（36） | 72.2%（26/36） | 全猜 False = 50.0% |
| choice（18） | **94.4%（17/18）** | 随机四选一 = 25% |
| score（18） | 72.2%（13/18） | 全猜档位2 = **88.9%** |
| CN（36） | 86.1%（31/36） | |
| US（16） | 75.0%（12/16） | |
| HK（10） | 60.0%（6/10） | |
| CRYPTO（10） | 70.0%（7/10） | |

错题模式（16 题）：noul 9 错**全部**是真值 True 判成 False（模型预测 False 28/36，偏空）；score 5 错中 3 错为真值 2 答成 1，另 2 错恰是全卷仅有的稀有档位（真值 0/1 各 1 题）；choice 仅 HK c015 一错（HK 猜成 CN）。

### 与 v1 / in-sample 对照——脱敏后真实区分度

| 口径 | 准确率 | 说明 |
|---|---|---|
| v1 开卷 | 100% | state 明文含答案，作弊灌水，已作数 |
| in-sample | 93.3% | 题来自已参与评估的池 |
| **v2 脱敏 OOS** | **77.8%** | 本节结果 |

**核心结论：脱敏后真实区分度约 78%，较 v1 开卷虚高的 100% 回落 22.2 个百分点——v1 的 100% 几乎全是「抄题」增益；较 in-sample 93.3% 回落 15.5 个百分点，呈现正常 OOS 衰减。** 分题型看能力构成：

1. **市场域推断是真能力**：choice 94.4%（随机基线 25%），模型能从 ts_code/理由可靠推断 CN/US/HK/CRYPTO，该能力接近 in-sample 水平。
2. **noul 存在系统性偏空**：72.2% 高于 50% 基线但全部错在「真判假」，预测 False 占 28/36——对利好型真值陈述偏保守，判读需注意。
3. **score 子卷弱于平凡策略**：72.2% 低于全猜档位 2 的 88.9% 基线（答案分布 16/18 偏向档位 2，题集 meta.known_limits 已披露）。该子卷当前**不具备正区分度**，结论应以 choice+noul 为主。
4. 分域梯度 CN 86% > US 75% > CRYPTO 70% > HK 60%，HK/CRYPTO 样本各仅 10 题，代表性有限（题集 known_limits 已披露）。

### 建议（待批事项，未执行）

- score 题生成口径需调整（档位分布均衡化或提高稀有档位题量），否则该子卷无法用于能力认定；
- 如需正式 OOS 认定，建议 3 遍重复 + score 口径修复后再批。


## OOS v3 score 修复（2026-10-14，v2 首评驱动）

### 根因（已用数据证实）

v2 score 子卷 18 题 answer_level 分布 {档2:16, 档0:1, 档1:1}，全猜档 2 = 88.9%，而 v2 首评 score 72.2%——**低于平凡基线，无正区分度**。根因在 fresh 池本身：1599 个候选事件评分段分布 **high 1481 / low 63 / mid 55（high 占 92.6%）**，CN/US 域候选分普遍 ≥0.9；抽题"余量回补大段"逻辑使 score 题事件 16/18 落 high。这是数据天然偏置，**抽题口径内无法修**（分档均衡抽题每档 6 题需 mid/low 各 ≥6 事件，全池 mid 仅 55 且分散在 4 域，强行均衡 score 子卷将缩到不足 10 题且代表性更差）。

### 修法选择（任务口径优先 b，已采纳）

**改题型**：score 题从"档位三选一"改为 **"该候选应 FOCUS 还是 PASS"两档二分类**（判据确定性：score ≥ 同批 18 个 score 事件评分中位数 0.985450 → FOCUS）。理由：
- 二分类平凡基线 50%，比修法 a 的均衡三档天花板高且稳定；
- 判据贴近毕业判据真实用途（候选跟/弃决策），比"背档位边界"更有业务含义；
- noul/choice 沿 v2 逐字不动（已 diff 校验 0 差异，同种子 20261014 可复现）。
备选 a（分档均衡抽题）弃用，理由如上（样本量不允许）。

产出：`oos_question_set_v3.json`（72 题，score 18 题 PASS/FOCUS = 9/9）+ `oos_question_set_v3_score_only.json`（18 题小评估子集）。v2 文件保留不删，缺陷说明 `oos_question_set_v2_score_biased.note.md`。生成器内置断言：PASS/FOCUS 单类占比超 60% 即 raise（本次 9/9 通过）；脱敏沿 v2 规则（评分 [REDACTED] + 数值反查），独立复查 0 泄露；中位数值不入题面 state。

### 小评估（18 题 ×1 遍，run_oos_eval.py + OOS_QUESTIONS_FILE 指向子集）

- 结果：results/result-20261014-154128.json；日志 /c/Temp/jev_oos_v3_score.log
- **score 子卷准确率 33.3%（6/18），token 8508（预算 1 万内）**
- 判定线 = 基线 50% + 10pp = 60% → **未达标**（差 26.7pp；即使 s010 请求失败题判对，上界 38.9% 仍不达标）
- 错题结构：混淆矩阵 TP=5 FP=8 TN=1 FN=3 ERR=1——模型偏猜 FOCUS（13/18）；**US 0/4、HK 0/2、CRYPTO 0/3 全灭**，CN 6/9 尚可；CRYPTO 三个低分题（0.49/0.65/0.70，真值 PASS）全部高置信（0.74-0.80）猜 FOCUS，属系统性误判非随机

### 判定

- **题集口径**：v3 成立——答案 9/9 均衡、平凡基线 50%、脱敏 0 泄露，题集本身不再是"全猜即赢"；v2 的 88.9% 基线漏洞已消除。
- **模型能力**：33.3% 低于基线 16.7pp，**负区分度**。**如实报告：score 维度评估仍不可用**（不能用于能力正认定），**不建议批"正式认定 3 遍"**，留待 jev 侧其他证据（如非 CN 域候选、或模型侧跟进/放弃判据对齐后再评）。
- 与 noul 偏空对照：noul 9 错全为"真判假"（对利好偏保守），score 则偏激进（低分也高置信猜 FOCUS），两子卷错误方向相反、但都是**系统性偏置**而非随机错——score 维度的问题在模型判据侧，非题集侧。
