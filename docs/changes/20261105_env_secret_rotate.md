# 凭据轮换工具 + 登录依赖结论 — 2026-11-05

## 一、为什么做

两条遗留项：

1. `JevRestart` 是 `InteractiveToken`，**用户注销后不会运行**——要彻底脱离登录会话就得改 SYSTEM/S4U；
2. `signal_review_gate` 公告源长期报 `anns_d token不对 (40101)`，公告维度一直降级 `none`。

## 二、登录依赖：结论是「不能改 SYSTEM」，有硬证据

| 证据 | 说明 |
|---|---|
| `jev_gate.py:313` | `os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")` —— 飞书推送的主通道（OpenAPI 直连）读取用户 profile 下的凭据 |
| `backup_alert.py:146` | 同样 `~/.lark-cli/config.json`，告警兜底通道也依赖它 |
| `jev-gate.env.plain.ps1` | **不存在**（W1-2 迁移后删除）；`.enc` 目前用户/机器两作用域都解不开时无明文回退 |
| SYSTEM 的 `~` | 落在 `C:\Windows\system32\config\systemprofile`，没有 `.lark-cli` 配置 → 主通道失效，只剩 webhook |

即：这套栈**按设计就是用登录用户的身份跑**（lark-cli 凭据 + 用户级 DPAPI）。改 SYSTEM 会把飞书推送打残，
且会把三件套重新扔回 session 0。**结论：保留 `Interactive` 身份，不追 SYSTEM。**

对应整改（已落地）：给 `JevStack` 增加 **AtLogOn 触发器**，登录后立即拉起，不用等下一个 10 分钟边界。

| 项 | 改前 | 改后 |
|---|---|---|
| 触发器 | 每日 04:10 + 每 10 分钟重复 | 同前 **+ 登录时触发** |
| 身份 | 用户 t / Interactive / Limited | **不变** |

验证：`Get-ScheduledTask JevStack` → `triggers=2`（`MSFT_TaskDailyTrigger rep=PT10M` + `MSFT_TaskLogonTrigger`）。

## 三、Tushare 令牌：从「无效」推进到「有效但无权限」

### 3.1 旧轮换脚本已失效（照跑会造成安全回归）

`jev-eval/rotate_tushare_token.ps1` 的更新目标是 `jev-gate.env.plain.ps1`（明文副本），
该文件在 W1-2 迁移（2026-10-14）后已删除。**现在跑它会在仓库里重新落一份明文凭据**。

新增 `tools/set_env_secret.py` 取代它：

```powershell
# 演练（不写盘）
python tools/set_env_secret.py --key TUSHARE_TOKEN --from-dotenv "<dotenv>" --probe --dry-run
# 实际轮换（自动备份 .enc.bak-<时间戳>，原子写，回读校验）
python tools/set_env_secret.py --key TUSHARE_TOKEN --from-dotenv "<dotenv>" --probe
```

特性：只打印长度与 sha256 前 8 位；`--probe` 写盘前用 `trade_cal` 做只读校验；失败即中止不落盘。

### 3.2 实测结果

机载候选只有两处令牌（`zhice-os-v2 .env.local` 与 JEV `.enc`），JEV 那份已失效：

| 令牌 | `trade_cal` | `anns_d` |
|---|---|---|
| JEV 原值（sha8 `05240a8e`） | `40101` 无效 | — |
| zhice（sha8 `97b7a9a4`） | **`code=0` 有效** | **`40203` 无接口权限** |

已把 JEV 的 `TUSHARE_TOKEN` 轮换为 zhice 的可用令牌（`.enc` 回读校验通过，11 键完整），
重启后 `review` 日志由 `40101` 变为：

```
公告源探测失败（降级 none 模式）: anns_d 失败 code=40203: 抱歉，您没有接口(anns_d)访问权限
```

**含义**：认证这一层通了，卡在**账号积分/接口权限**。这不是代码能修的，三选一待定：

| 选项 | 动作 | 代价 |
|---|---|---|
| A | Tushare 账号充值/提积分开 `anns_d`（或恢复原 JEV 账号的令牌，若该账号本就有权限） | 花钱，但零开发 |
| B | 换公告源（巨潮/交易所/东财） | 开发量中等，长期免积分依赖 |
| C | 维持 `公告源=none` 降级 | 复核门缺公告维度，等于该判断维度长期空转 |

> 现状对比：轮换前是「无效令牌」，轮换后是「有效但无权限」，功能上同为 `none`，但排障面收敛了一层。
> 备份：`jev-eval/jev-gate.env.enc.bak-20261105-052431`（回滚：覆盖回 `.enc` 后重启）。

## 四、变更清单

| 文件 | 变更 |
|---|---|
| `tools/set_env_secret.py` | 新增：`.enc` 单键轮换（DPAPI / 原子写 / 备份 / probe / dry-run） |
| `tools/tests/test_set_env_secret.py` | 新增：dotenv 解析、空值拒绝、dry-run 不写盘、回读一致性 |
| `JevStack` 计划任务 | 增加 AtLogOn 触发器（身份不变） |
| `jev-eval/jev-gate.env.enc` | `TUSHARE_TOKEN` 轮换（文件本身不入 git） |

## 五、风险

1. `--probe` 依赖 `api.tushare.pro` 可达；离线时轮换会中止（不落盘），不影响服务。
2. `.enc` 现在全量重加密时用 CurrentUser 作用域，与 `migrate_env_dpapi.py` 一致；`load_env_dpapi.py`
   仍按 `0x1 → 0x5` 两作用域依次尝试，向后兼容。
3. 令牌是 JEV 与 zhice 共用同一账号，额度（每日调用次数）是共享的，公告类大批量拉取前需注意。
