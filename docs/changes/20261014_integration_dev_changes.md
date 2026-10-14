# 20261014 integration-dev 变更留痕（W1-2 双任务）

执行人：integration-dev ｜ 日期：2026-10-14 ｜ 解释器：`C:/Program Files/Python311/python.exe`

全局纪律执行情况：改前备份 ✔（`*.bak_20261014`）；未推送飞书、未注册/修改计划任务、未启停常驻进程、未动生产库（仅只读 SELECT）；新文件与本文档均不含任何密钥值；新逻辑带开关可回退（`--full` / 删 `.enc` 即回退）。

---

## 任务A：jev-gate 密钥 DPAPI 迁移（P0 安全）

### 改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `jev-eval/jev-gate.env.ps1` | 重写为 stub | 优先加载 `.enc`；失败/缺失回退明文并 stderr WARN |
| `jev-eval/migrate_env_dpapi.py` | 新增 | 迁移工具：DPAPI(CurrentUser) 加密 + `--verify` + `--rotate-checklist` |
| `jev-eval/load_env_dpapi.py` | 新增 | 加载器：py 侧 `get_secret(key)`；CLI `--ps1`（供 stub IEX 注入）/`--list`（只列键名） |
| `jev-eval/jev-gate.env.plain.ps1` | 新增（明文副本） | 回退目标，**轮换完成前保留**，是否删除由老田拍板 |
| `jev-eval/jev-gate.effects.ps1` | 新增（脚本自动提取） | 原文件非密钥副作用段（建 JEV_LOG_DIR 目录、lark-cli PATH 注入）；写入前自检不含任何明文值 |
| `news_collector/news_ai.py` | 小改 | `_jev_key()` 在明文正则兜底之前新增 DPAPI 加载分支（stub 化后原正则读不到明文 key） |
| `jev-gate.env.ps1.bak_20261014`、`news_ai.py.bak_20261014` | 备份 | 回滚直接还原 |

### 关键设计

- **10 个活跃键全部迁入** `jev-gate.env.enc`（JSON，每键 base64 DPAPI 密文，原子写）。DPAPI CurrentUser 作用域：仅当前 Windows 账户可解密。
- **stub 兼容全部消费方**：`zhice.ps1` / `run_eval.ps1` / `remind_report.ps1` / `run_backfill.ps1` 照旧 dot-source，零改动；`jev_common.py` 等 py 侧走 os.environ 注入，零改动。仅 `news_ai.py` 因直接正则解析明文文件需一处适配（已做）。
- **UTF-8 管道对策**：stub 临时切 `[Console]::OutputEncoding=UTF8`（PS5.1 默认 ANSI 会把 JEV_LOG_DIR 中文路径读成乱码）。
- **回滚**：删除 `jev-gate.env.enc` → stub 自动回退明文 + WARN；或整体还原 `.bak_20261014`。

### 验证结果（不打印任何真实值）

1. `migrate_env_dpapi.py` → 迁移 10 键；`--verify` → **MIGRATION_OK**（解密回读与明文逐键一致，effects 无泄漏）。
2. stub dot-source → 10 键注入：9 键 SET（长度核对通过，含中文路径 JEV_LOG_DIR 正确解码、目录已建）；`ALERT_WEBHOOK_URL` 为空串（弃用键，与明文一致）。
3. 假键 `get_secret('FAKE_KEY_NOT_EXIST')` → 空串 OK；真实键存在性 OK。
4. 回退演练：临时移走 `.enc` → WARN 打出、明文副本加载成功、TUSHARE_TOKEN SET；`.enc` 已恢复。
5. `news_ai._jev_key()` → SET(len=107)（走 DPAPI 分支）。

### 轮换清单（`--rotate-checklist` 输出，不含值）

1. **typesafe（Jev）**：`JEV_API_KEY` —— 平台生成新 key → 更新 plain 对应行 → 重跑 migrate → `--verify`
2. **Tushare**：`TUSHARE_TOKEN` —— platform.tushare.pro 重置 → 同上；⚠ `rotate_tushare_token.ps1` 仍指向 stub 路径（其"原子更新明文行"已失效），**轮换期内勿用其自动更新**
3. **QuantDinger**：`QD_USER` / `QD_PASSWORD` —— QD 后台改密 → 同上
4. **zhice OS v2**：`ZHICE_ADMIN_USER` / `ZHICE_ADMIN_PASSWORD` —— 后台改密 → 同上
5. **飞书**（open_id 类，非密钥）：`LARK_ALERT_OPEN_ID` / `LARK_ALLOWED_OPERATORS` —— 无需轮换，确认收件人白名单
6. **清理项**：`ALERT_WEBHOOK_URL`（已弃用、值为空）—— 确认无消费方后从 plain 删除并重跑 migrate
7. **收尾**：全部轮换完成且稳定 ≥1 周 → 删除 `plain.ps1` 与 `.bak_20261014`、stub 去掉回退分支——**由老田拍板**

### 常驻进程说明（未重启任何进程）

当前在跑的常驻进程仍持有旧明文 env（内存内）：**下次进程重启自动走新加载器（stub→.enc），重启前明文文件仍生效**。明文 `plain.ps1` 未删除，可用性不受影响。

### 遗留风险

- 计划任务若以**其他 Windows 账户**运行 → DPAPI 解密失败 → stub 回退明文 + WARN（可用性保住、安全降级）。建议下次重启前确认任务运行账户与本账户一致。
- `rotate_tushare_token.ps1` 未适配 stub（本次最小改动不动它），轮换 TUSHARE_TOKEN 时按 checklist 手工流程走。
- stub 回退路径依赖 `plain.ps1` 存在；误删 plain 且 .enc 不可用时 stub 会 WARN"凭据未加载，人工介入"。
- `--ps1` 输出为明文赋值行（等同 `cat .env`），勿落盘/进日志。

---

## 任务B：strong 全量扫描 `--full` 开关（覆盖率修复）

### 改动文件

- `quant_backtest/strong_scan.py`（备份 `strong_scan.py.bak_20261014`）
- `quant_backtest/_full_scan_logic_check.py`（新增，一次性合成验证探针，仓库 `_3xx` 探针风格，可留档复查）

### 关键 diff（语义级）

1. **新增 `--full` 开关**：`scan_block(..., full=True)` 去 `PER_BLOCK_CAP=20` 截断，评估板块全部成员；不带 `--full` 时行为与旧版逐字节等价（默认防 15:45 计划任务窗口超时）。
2. **兜底截断**：full 模式下单板块成员 > `FULL_BLOCK_CAP=200` 时，按**近 20 日成交额**（`v2_bar_daily.turnover` 合计，tushare 同源千元口径，仅用于排序）降序截前 200；查询失败/空结果回退清单序截 200（可用性优先）。
3. **summary 新增字段**：`full_scan: bool`、`elapsed_sec`（两种模式都记录）、`per_block_cap`（full 时置 0）；`coverage_note` 分两种口径（full 时注明是否触发 200 兜底及板块数）；`member_coverage` 随全量自然上升。下游消费方核查过：`per_block_cap/coverage_note/member_coverage` 无代码消费（仅文档引用），`portal/strong.html` 消费 `items/list` 不受影响。
4. **熔断逻辑未动**：当日已扫（不看 full_scan 标志）仍跳过——若手动跑过 `--full` 并落盘，当日 15:45 计划任务自动熔断不重复跑。

### 验证结果

- `py_compile` + `--help` ✔。
- 真实链路小样本（`--block 881019 --dry --force`）：**freshness_guard 正常拦截**（个股锚 10-13 ≠ 板块锚 10-12，凌晨补数未齐的 T-1 静默名单防护生效）——守卫工作正常，但也说明当前时点无法端到端实跑，改用合成验证：
- `_full_scan_logic_check.py` → **CHECK_OK**，四断言全过：
  1. 默认路径 50 成员 → 只评前 20（清单序，与旧版一致）
  2. full 路径 50 成员 → 评全部 50
  3. full 兜底 250 成员 → 截 200，高额票入选、清单序低额头部被挤出
  4. 成交额查询故障 → 回退清单序截 200
- 未跑全市场、未落盘（合成验证 monkeypatch，不碰真 DB 取数）。

### 给老田的建议（拍板项）

1. **先手动测时**：`python strong_scan.py --full --force`（待守卫通过后，建议 17:30 板块仓更新后跑）。预暖实测 599 板块约 13 分钟（前 20 截断口径）；全量为逐股取数，粗估线性放大 ~3-10 倍（40-130 分钟区间），**实测为准**。
2. 若实测 **< 50 分钟** → 可考虑给 15:45 计划任务加 `--full`（**改任务由你拍板，本次未动计划任务**）；若接近或超窗，保持默认截断，`--full` 仅手动周期跑。
3. 落盘的 `elapsed_sec` 可直接作为后续评估依据。

### 遗留风险

- 全量模式逐股 `series` 取数：极端情况下板块锚/个股锚交叉取数可能放大 DB 压力（200 兜底已封顶单板块代价）。
- `turnover` 排序查询为单次 `ANY(%s)` 批量 SQL（250 码级），失败即降级清单序，名单口径会在 coverage_note 说明——下游读名单时注意 full/非 full 口径差异。
- `strong_latest.json` 一旦被 `--full` 落盘，当日默认任务熔断跳过（见上）；若需当日再跑默认口径需 `--force`。

---

## 本机环境备注

- `schtasks` 查询被本机安全策略（程序黑名单）拦截——本次未查/未改任何计划任务，与纪律一致。
- 读明文凭据文件的安全审批未放行，迁移脚本按"值不经过开发者视野"设计（机械解析+自检），属预期工作方式。
