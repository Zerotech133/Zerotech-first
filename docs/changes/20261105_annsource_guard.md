# 2026-11-05 公告源体检与守卫接线（JEV-ANNSRC）

## 一、背景

`signal_review_gate.py`（9102）的公告维度依赖 Tushare `anns_d`。探测失败时它只打一行
`level=info`，之后每轮照常输出「本轮 0 条 … 公告源=none」——**门看起来是健康的，实际
公告维度已空转 6 天（尾部连续 873 轮 none）**，只有翻日志才知道。

老田 2026-11-05 裁定走 A 方案（账号侧提积分/开接口），本次交付的是 A 方案的**配套件**：
可复验的体检工具、令牌回切通道、以及把静默降级变成可见告警的守卫。

## 二、实测事实（不是推断）

| 令牌 | trade_cal | anns_d | 结论 |
|---|---|---|---|
| 生产当前 `sha8=97b7a9a4`（zhice 源，05:24 轮换） | `code=0` | `40203` | 有效但无接口权限 |
| 备份原值 `sha8=05240a8e`（`.bak-20261105-052431`） | `40101` | `40101` | 令牌本身失效 |

即：`40101 → 40203` 是**进展**（认证层已通），卡点在账号积分档，代码侧无解。

## 三、改动清单

| 文件 | 类型 | 改了什么 |
|---|---|---|
| `tools/tushare_probe.py` | 新增 | 令牌体检：4 个接口（`trade_cal` / `anns_d` / `stock_basic` / `daily`）逐项判定，区分 `TOKEN_INVALID` 与 `NO_PERMISSION`；`--from-enc` / `--from-dotenv` / `--token` 三种取值；`--require` 指定必需接口（CI/任务用）；`--json` 单行输出。退出码 0/3/4/5/6 |
| `tools/set_env_secret.py` | 改 | 新增 `--from-enc`：从历史 `.enc` 备份取值 → **回切通道**（改错令牌可一键退回）；`--probe` 追加 anns_d 权限提示（只提示不阻断） |
| `jev-eval/jev_source_guard.py` | 新增 | 公告源守卫：读 review 日志尾部，判 `tushare/none`、错误码（40101/40102 → CRITICAL，40203/40204 → WARN，无码 → NO_TOKEN）、review 停摆（默认 6h）；推送同因 3 天内只推一次 |
| `jev-eval/jev_coverage_task.ps1` | 改 | 同一任务串跑第二段守卫，退出码取 `[Math]::Max(rcCov, rcSrc)`；新增 `logs/source_daily.jsonl` |
| `jev-eval/jev_coverage_guard.py` | 改 | 支持 `JEV_GUARD_NO_PUSH=1` 环境变量抑制推送（演练用，计划任务不设） |
| `jev-eval/tests/test_source_guard.py` | 新增 | 11 项：OK / 40203 / 40101 / NO_SIGNAL / STALLED 优先 / none_streak 只数尾部 / 推送去重与原因变更 / 未知码 / CLI 参数 |
| `jev-eval/docs/公告源恢复操作单_2026-11-05.md` | 新增 | 给老田的一页：现状、A1/A2 两条操作路径、回滚、验证命令、守卫口径表 |
| `jev-eval/docs/月度评测与覆盖率告警_2026-11-05.md` | 改 | §4.2 补充第二段守卫说明 |

## 四、验证证据

```
# 1) 体检（生产令牌）
*trade_cal   0      OK
*anns_d      40203  NO_PERMISSION
 stock_basic 0      OK        2318 行
 daily       0      OK          12 行
结论：PERMISSION_MISSING        rc=4

# 2) 体检（备份里的原令牌）
四项全 40101 TOKEN_INVALID      rc=3

# 3) 回切演练（--dry-run，未写盘）
source  jev-gate.env.enc.bak-20261105-052431
before  len=56 sha8=97b7a9a4 → after len=56 sha8=05240a8e  changed YES

# 4) 日检任务整链空跑（JEV_GUARD_NO_PUSH=1）
coverage exit=1 json={...coverage 0.1916, 508/2651...}
source   exit=1 json={...ann_mode none, cause NO_PERMISSION:40203, none_streak 873, age 0.03h...}
combined exit=1

# 5) 测试
pytest jev-eval -q  → 见提交时输出
```

## 五、五要素

| 项 | 内容 |
|---|---|
| 变更命令 | 无系统级命令。计划任务未新增、未改动（`JevCoverageDaily` 已存在，仅其脚本内部多跑一段） |
| 影响面 | 只影响 9102 review 的**告警可见性**与日检任务退出码；不触碰 9101/9103/9104，不改判定逻辑，不改生产库 |
| 不可逆点 | 无。`.enc` 未被本次改动修改（05:24 的轮换是上一轮动作，且有 `.bak`） |
| 替代方案 | 不做守卫 → 继续静默空转；只做体检工具 → 无人自动跑，仍会退化 |
| 回滚 | `git revert <commit>`；如需彻底停用第二段，把 `jev_coverage_task.ps1` 中 `jev_source_guard.py` 那段删掉即可，其余不受影响 |

## 六、遗留

1. `anns_d` 权限仍在账号侧，**需老田充值/提积分**；到位后跑 `tools/tushare_probe.py` 应为 `结论：OK`。
2. 若最终判定不值当，走换源（巨潮/交易所/东财）需另立项，本守卫的 level/exit 口径可复用。
3. `JevMonthlyEval` 今日 07:20 首跑，`JevCoverageDaily` 07:40 —— 两者都会用到本次改动，07:45 前建议看一眼 `logs/jev_monthly_eval.log` 与 `logs/source_daily.jsonl`。
