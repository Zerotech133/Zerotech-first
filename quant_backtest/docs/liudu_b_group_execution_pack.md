# 六度选股 · B 组执行包（N3 / N5 / N6 / N12）

> 状态：门前准备（休眠默认）与 5 个非生产门（N1/N4/N8/N10/N11）决策记录已全部落码并**真实生效**（8 模块编译通过、8 套件 64 passed、管线冒烟通过、端到端 trial 落账 30 条且幂等不累积）。
> 本文件把 B 组 4 门组织成「材料一到即一键执行」的落地包。**任何涉及真实凭证注入或 R2 生产切流的动作，均须老田明示批准（五要素），沉默/"继续"不算审批。**

---

## 0. 当前护栏（任何路径下均不可被误触发）

| 开关 | 当前值 | 含义 |
|---|---|---|
| `LIUDU_UNIVERSE_ENABLED` | `True` | N1 universe=market **路径A已生效**，真实链强制剔除非A股/北交所（实测76→60，残留0） |
| `RECONCILE_SEMANTICS_ENABLED` | `True` | N8 模式 B **路径A已生效**；`reconcile()` 仍恒 `NOT_VERIFIED` |
| `SIGNAL_POOL_AUTO_WRITE` | `True` | N11 方案 B 选定，经「按计划组织实施」指令翻开关真实生效；`signal_pool.jsonl` 已落账 **30 条**（xunyou_pool 上游，trial 数据，幂等 upsert 重跑不累积） |
| `STOP_LOSS_EXPERIMENT_ENABLED` | `True` | N4 参数落定，**路径A已生效**（仅激活对比能力，不接入生产交易） |
| `DATA_SOURCE_REAL_AUTH_ENABLED` | `False` | N5 real_auth 默认不武装，trial 态合成数据 |
| `R2CutoverStaging.DEFAULT_PRECONDITIONS` | 全 `False` | N12 的 10 项前置恒未满足，`trigger()` 强制 `triggered=False` |

---

## 1. N3 采纳门（BACK1 / STRAT1 签字）

**性质**：纯签字结论，非代码改动。六度选股策略/回测须经「BACK1 回测锚定口径 + STRAT1 策略采纳」签字后，才解锁 R1 试运行。

**定位**：`market_scan.py:77-121` 的 ST-PIT 增量函数已建成、生产仍走 legacy；采纳后接引擎。

**需老田给**：一句话结论（采纳 / 驳回）+ 回测证据路径。

**我收到后将执行**：在 `docs/` 落 `liudu_adoption_record.md`（含 BACK1/STRAT1 签字栏 + 证据引用），并把 `docs/plans/` 既有决策门清单对应项标记 CLOSED。

---

## 2. N5 数据源（8 类凭证回签）

**8 类源 ↔ 密钥键名**（对齐 `.env.example`）：

| 数据源 | 键名 |
|---|---|
| Tushare | `TUSHARE_TOKEN` |
| xtcloud | `XTCLOUD_KEY` |
| Snowball | `SNOWBALL_COOKIE` |
| 东财 | `EASTMONEY_TOKEN` |
| 股吧 | `GUBA_COOKIE` |
| akshare | `AKSHARE_TOKEN` |
| crypto | `CRYPTO_API_KEY` |
| 飞书 | `FEISHU_APP_SECRET` |

**需老田给**：上述 8 项真实值（填入 `.env.local`），或告知读取位置。

**我收到后将执行**（不打印任何值）：
1. `cp .env.example .env.local`（若不存在）
2. `python tools/verify_credential_readiness.py` → 确认 8 类齐备（仅报在/缺）
3. 经 N6 ceremony 批准后在 `data_source_adapter.py` 将 `DATA_SOURCE_REAL_AUTH_ENABLED` 置 `True` 并逐源 `build_adapter(name, state="real_auth")`，运行 `tools/liudu_trial_smoke.py` 复验真实态 fetch 不抛异常。

---

## 3. N6 密钥治理（明文清理授权 + 注入位置）

**dry-run 现状**（只读扫描，未删任何文件）：当前唯一明文风险 = `.env.local` 已存在；`config/tushare_token.txt`、`C:/Temp/tushare_token.txt` 均不存在。

**需老田给**：明确授权 ——
- (a) 清理 `.env.local`（删除或仅保留占位）；或
- (b) 保留但将真实值迁移进 `SecretStore` 托管（不落明文）。

**我收到后将执行**：按 `tools/cleanup_plaintext_secrets.py` 的 `scan(dry_run=False)` 在授权范围内执行；密钥统一经 `SecretStore` 读取，绝不硬编码。

---

## 4. N12 R2 生产切流（五要素审批包）

**护栏**：`tools/r2_cutover_staging.py` 的 `R2CutoverStaging.arm()` 仅在 10 前置全满足 + `armed=True` 后才允许 `trigger()`；当前 `DEFAULT_PRECONDITIONS` 全 `False`，`trigger()` 强制 `triggered=False`，**任何路径不可达切流**。

**10 前置项**（编码于 `DEFAULT_PRECONDITIONS`，现全 `False`）：N3 采纳 ✗ / N5 凭证齐备 ✗ / N6 ceremony 完成 ✗ / R0 离线闭环 ✗ / R1 试运行达标 ✗ / reconcile READY ✗ / universe 锁定 ✗ / 止损实验验收 ✗ / 导航挂载 ✗ / 回滚演练 ✗。

**需老田给（五要素全文）**：
1. **命令全文** —— 触发切流的具体命令/脚本
2. **影响面** —— 哪些服务/数据/用户受影响
3. **不可逆点** —— 哪些动作不可回退
4. **替代方案** —— 不切流的备选
5. **回滚预案** —— 失败如何回退

**我收到后将执行**：把五要素写入 `docs/liudu_r2_cutover_approval.md`，逐项置 `DEFAULT_PRECONDITIONS` 对应项为 `True` 并经 `validate_checklist()` 校验，再 `arm()` + `trigger()`，落 `data/meetings/` 风格的执行回执。

---

## 5. 解阻塞清单（老田只需 paste 这些）

| 门 | 给我什么 | 形式 |
|---|---|---|
| N3 | BACK1/STRAT1 签字结论（采纳/驳回）+ 证据路径 | 一句话 |
| N5 | 8 类真实凭证值（或读取位置） | 填 `.env.local` / 告知 |
| N6 | 清理授权 (a) 或 (b) | 一句话 |
| N12 | 五要素审批全文 | 五条文本 |

> 任一缺失我均不执行该门。材料齐了，B 组可一次性凭单落地。
