# zhice-os-v2 Windows 部署说明

> 部署包版本：main@`50d7d5d`（2026-09-20）｜数据库迁移终态：**0033**｜导出自 macOS 本机生产环境（四市场 CN/US/HK/CRYPTO 全量业务数据）

---

## 0. 包内容清单

```
zhice-os-v2-windows-20260920\
├── code\                        # 完整源码（git main + 本机工作区配置修正）
│   ├── src\                     #   后端 FastAPI（Python 3.12）
│   ├── frontend\                #   前端 React+Vite（Node 20+）
│   ├── migrations\              #   alembic 迁移链（主 dump 已是 0033 终态，一般无需再跑）
│   ├── .env.local               #   ⚠️ 生产凭据原样随包（见第 5 节）
│   ├── .env.example             #   全部配置项说明模板
│   └── requirements-freeze.txt  #   本机验证过的精确依赖版本（163 个包）
├── database\
│   └── prod_data.dump           # 生产数据：全库 schema + 全部业务表数据
│                                 #（用户/策略/持仓/成交/流水/订阅/告警……，行情表为空壳）
├── market_history\              # 历史行情（单独导出，可选择性导入）
│   ├── export_manifest.json     #   各表导出行数基准（导入后核对）
│   ├── v2_bar_minute.dump       #   分钟线 8,887,430 行（约 203MB，导入最久）
│   ├── v2_bar_daily.dump        #   日线 2,936,223 行
│   ├── v2_adj_factor.dump       #   复权因子 2,659,370 行
│   ├── v2_instrument.dump       #   标的字典 15,459 行
│   └── （top_list/dividend/财报/基本面/持仓交易 5 张表）
├── scripts\
│   └── import_market_history.py # 历史行情导入脚本（幂等可重跑）
└── docs\DEPLOY-WINDOWS.md       # 本文
```

**部署顺序总览**：装软件（§1）→ 建库（§2）→ 恢复生产数据（§3）→ Python 环境（§4）→ 核对配置（§5）→ 启动（§6）→ 导入历史行情（§7）→ 验证（§8）。

---

## 1. 前置软件安装

| 软件 | 版本 | 下载 | 安装要点 |
|---|---|---|---|
| Python | **3.12.x** | python.org/downloads | 勾选 **"Add python.exe to PATH"** |
| PostgreSQL | **16.x**（15/17 也可） | postgresql.org/download/windows | ① 记住 `postgres` 超级用户密码；② 端口保持 `5432`；③ locale 默认即可；④ 组件里 **Command Line Tools 必装**（psql/pg_restore） |
| Node.js | **20 LTS+** | nodejs.org | 默认安装即可（含 npm） |

验证（PowerShell）：

```powershell
python --version     # Python 3.12.x
psql --version       # psql (PostgreSQL) 16.x
node --version       # v20.x 或更高
```

> `psql` 不在 PATH 时：把 `C:\Program Files\PostgreSQL\16\bin` 加入系统 PATH，或后续命令用完整路径。

---

## 2. 创建数据库（二选一）

**方案 A（推荐）：随机强口令建库，并写入 `.env.local`**

> 不要从本文或 `.env.example` 复制 `zhice_pass` 等默认口令。生产启动校验会拒绝
> `DB_MAIN_URL` 仍含 `zhice:zhice_pass@`，以及 `V1_DB_PASSWORD`/`V2_DB_PASSWORD`
> 等于 `zhice_pass` 或长度 < 12 的配置。

在**部署包根目录**执行（PowerShell）：

```powershell
# 1) 生成 32 字节随机口令（URL-safe，可直接放进连接串）
$DbPassword = python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2) 建同名用户和库（本机 PG 默认 5432；本部署树若映射到 5446，请同步改端口）
psql -U postgres -c "CREATE USER zhice WITH PASSWORD '$DbPassword';"
psql -U postgres -c "CREATE DATABASE zhice_os_v2 OWNER zhice ENCODING 'UTF8';"

# 3) 打印要写入 code\.env.local 的两行（用编辑器替换旧行，不要重复追加）
Write-Output "DB_MAIN_URL=postgresql://zhice:$DbPassword@localhost:5432/zhice_os_v2"
Write-Output "V2_DB_PASSWORD=$DbPassword"
```

打开 `code\.env.local`：

- 把 `DB_MAIN_URL=` 改为上面输出的第一行（没有就追加）；
- 把已有的 `V2_DB_PASSWORD=zhice_pass` 改为上面输出的第二行；
- 若启用 V1→V2 同步，把 `V1_DB_PASSWORD` 也换成 ≥12 位随机值；不启用则留空。

> ⚠️ 必须 `OWNER zhice`：主 dump 里的对象属主是 `zhice`，属主一致恢复最干净
> （否则要加 `--no-owner` 并接受告警）。
> 口令不要提交到仓库或外发；`.env.local` 已被 `.gitignore` 忽略。

**方案 B**：用 `postgres` 用户建库，之后在 `code\.env.local` 里配置连接串：

```powershell
psql -U postgres -c "CREATE DATABASE zhice_os_v2 OWNER postgres ENCODING 'UTF8';"
```
```ini
# code\.env.local（口令同样必须是随机强口令，不要用 zhice_pass）
DB_MAIN_URL=postgresql://postgres:<你的强口令>@localhost:5432/zhice_os_v2
# 调度器的 V2 同步仍要连接 zhice_os_v2；若未建 zhice 用户，让 V2 也用 postgres，
# 并保证 V2_DB_PASSWORD 与 DB_MAIN_URL 一致且 ≥12 位
V2_DB_USER=postgres
V2_DB_PASSWORD=<你的强口令>
```

---

## 3. 恢复生产数据（业务库，约 1 分钟）

在**部署包根目录**打开 PowerShell：

```powershell
# 方案 A（属主一致）
pg_restore -U zhice -d zhice_os_v2 --no-owner database\prod_data.dump

# 方案 B（postgres 属主）
pg_restore -U postgres -d zhice_os_v2 --no-owner database\prod_data.dump
```

> `--no-owner`：忽略 dump 内的属主声明，按连接用户建对象（两种方案都加最稳）。
> 恢复后行情 9 张表是**空表**（结构已建好），数据在 §7 导入。
> 出现少量 `WARNING` 正常；`ERROR` 为零才继续。

核对（可选）：

```powershell
psql -U zhice -d zhice_os_v2 -c "SELECT count(*) FROM v2_fill_record;"   # 应为 1323
psql -U zhice -d zhice_os_v2 -c "SELECT count(*) FROM v2_position;"     # 应为 669+（含实时新增）
```

---

## 4. Python 环境与依赖

```powershell
cd code
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements-freeze.txt
```

> - 用 `requirements-freeze.txt`（源机验证过的精确版本，含 `czsc==0.10.12`——
>   **此版本必须钉死**，新版本缠论中枢计算会坏）。
> - 若个别包安装失败，可改用 `pip install -e .`（范围版本；本项目全部依赖均有
>   Windows 轮子，正常不会触发编译）。
> - 若提示"禁止运行脚本"：先执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

## 5. 配置核对（code\.env.local）

包内 `.env.local` 已带**源系统的真实凭据**。数据库口令必须按 §2 重新生成并替换，
**不要**沿用随包默认值或从文档复制示例；其余外部行情/通知凭据多数项原样可用：

| 配置项 | 处理 |
|---|---|
| `DATACENTER_BASE_URL` / `DATACENTER_API_KEY` | CN 行情源（datacenter.xundaotech.cn:24123）。**新环境须可访问该地址**，不可达只影响 CN 采集，不影响其他市场 |
| `TUSHARE_TOKEN` / `FINNHUB_TOKEN` | 原样保留（外部行情/结算源） |
| `TELEGRAM_BOT_TOKEN` | 原样保留；`TELEGRAM_PROXY` 若新环境无需代理则删掉该行或留空 |
| `WXPUSHER_APP_TOKEN / APP_ID / APP_KEY / WEBHOOK_PATH_KEY` | 原样保留（微信推送）。webhook 回调地址变化见 §9-Q6 |
| `WXPUSHER_BINDING_ENABLED / DELIVERY_ENABLED` | 当前 true/false，按需调整 |
| `DB_MAIN_URL` | **必须在 §2 显式配置**为随机强口令连接串；不要留空依赖代码默认值（默认含 `zhice_pass`，生产校验会拒绝） |
| `V1_DB_PASSWORD` | 启用 V1→V2 同步时填随机强口令（≥12 位）；不启用可留空。`zhice_pass`/短口令会导致启动失败 |
| `V2_DB_PASSWORD` | **必须**与 §2 的 `zhice` 口令一致且 ≥12 位；随包值 `zhice_pass` 必须替换 |

> `.env.local` 含密钥，**不要提交到任何代码仓库、不要外发**。

**改完先做 DB 口令自检（不启动服务）**：

```powershell
cd code
$env:APP_ENV = "prod"
.venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv('.env.local'); from zhice_os_v2.config.settings import AppSettings; AppSettings(app_env='prod', secret_key='X'*32, admin_password='StrongPass9', persistence_backend='sql'); print('DB settings OK')"
Remove-Item Env:APP_ENV
```

预期输出 `DB settings OK`。若报 `DB_MAIN_URL` / `V1_DB_PASSWORD` / `V2_DB_PASSWORD`
弱口令，按提示替换后重跑。这条命令用占位值补齐 prod 必填项，重点检查 DB 三条
规则；若还报其他配置错误，按对应项修正。**不要**用“进程能起来”代替这一步——
只有这三条规则通过，才能保证 scheduler 启动时不会因弱口令被拒绝。

> 上述 fail-fast 只在 `APP_ENV=prod` 时执行；`.env.local` 默认是 `local-dev`。
> 若生产进程没有显式设置 `APP_ENV=prod`，新增的 DB 口令校验（以及既有 prod
> 校验）都不会生效。上线前请在启动环境中确认 `APP_ENV=prod`。

> **已部署环境轮换**：由 DBA 执行
> `psql -U postgres -c "ALTER USER zhice WITH PASSWORD '<新强口令>';"`，
> 再同步 `.env.local` 的 `DB_MAIN_URL` / `V2_DB_PASSWORD`，最后重启 API 与
> scheduler。只改 `.env.local` 不改库口令（或反之）会让新连接认证失败。

## 6. 启动（三个独立 PowerShell 窗口）

```powershell
# 窗口1：后端 API（端口 8102）
cd <部署包>\code
.venv\Scripts\Activate.ps1
python -m uvicorn zhice_os_v2.api.main:app --host 0.0.0.0 --port 8102

# 窗口2：调度器（行情采集 + 策略调度 + 对账）
cd <部署包>\code
.venv\Scripts\Activate.ps1
python -m zhice_os_v2.scheduler

# 窗口3：前端 dev server（固定端口 5175）
cd <部署包>\code\frontend
npm install        # 首次
npm run dev
```

浏览器访问：**http://localhost:5175**（用 `localhost`，不要用 `127.0.0.1`——vite 只绑 IPv6 回环）。
登录：**沿用源系统账号密码**（用户表随主 dump 恢复，含 admin）。

> 改了后端代码后 API/scheduler **不会自动重载**（进程无 --reload），需关闭窗口重开。
> 生产常驻建议用任务计划程序（taskschd.msc）把三条命令设为开机自启，
> 触发器"系统启动时"、操作分别指向上述命令（"起始于"填 code\ 目录）。

## 7. 导入历史行情（幂等可重跑，约 10–30 分钟）

前置：§3 主库已恢复（行情表结构已存在）。在部署包根目录：

```powershell
# 方案 A
python scripts\import_market_history.py --dsn "postgresql://zhice:<§2生成的强口令>@localhost:5432/zhice_os_v2"

# 方案 B / pg_restore 不在 PATH 时
python scripts\import_market_history.py --dsn postgresql://postgres:<密码>@localhost:5432/zhice_os_v2 --pg-bin "C:\Program Files\PostgreSQL\16\bin"
```

脚本行为：逐表 `TRUNCATE → pg_restore --data-only → 行数核对`（对照
`export_manifest.json` 基准），最后汇总。**行数不一致直接重跑一遍**（替换语义）。
只想先导日线试试：`--only v2_bar_daily`。

> `--disable-triggers` 需要超级用户或表属主——用 `zhice`（属主）或 `postgres`（超户）连接均可。

## 8. 部署后验证清单

```powershell
# ① API 健康（先确认 §5 的 DB 口令自检输出 DB settings OK）
curl http://localhost:8102/api/v1/health        # 200

# ② 前端
#    浏览器 http://localhost:5175 → 登录 → 持仓/交易记录/策略广场 页面有数据

# ③ 业务数据量级（psql）
SELECT count(*) FROM v2_fill_record;            -- ≥1323
SELECT count(*) FROM v2_position;               -- ≥669

# ④ 行情导入完整性（导入脚本末尾汇总应全 OK）
SELECT count(*) FROM v2_bar_daily;              -- 2,936,223
SELECT count(*) FROM v2_bar_minute;             -- 8,887,430

# ⑤ 成交流水对账（无缺口应输出 missing_sell=0, missing_buy=0）
cd code ; .venv\Scripts\Activate.ps1
python -c "from zhice_os_v2.application.services.fill_reconciliation import reconcile_fills_job; print(reconcile_fills_job())"

# ⑥ 调度器窗口应持续滚动行情采集日志（CRYPTO 7×24，CN/US/HK 交易时段）
```

## 9. 常见问题

**Q1 `psycopg2-binary` 安装失败？**
不会——它是二进制轮子。若你改用了 `pip install -e .` 且装到 `psycopg2`（非 binary）需要编译，回到 freeze 清单最稳。

**Q2 前端打不开？**
① 地址用 `http://localhost:5175`（不是 127.0.0.1）；② `netstat -ano | findstr 5175` 确认监听；③ 5175 被占时改 `code\frontend\vite.config.ts` 的 `port`，并同步在 `src\zhice_os_v2\api\main.py` 的 CORS 允许源列表加同端口。

**Q3 端口 8102 被占用？**
`netstat -ano | findstr 8102` 找 PID → 任务管理器结束；或 API 换端口后设环境变量 `VITE_PROXY_TARGET=http://localhost:<新端口>` 再起前端。

**Q4 CN 行情采集失败？**
日志大量 `datacenter.xundaotech.cn` 连接错误 = 新环境到该源不可达（跨网/防火墙）。只影响 CN；US/HK/CRYPTO 走公网源不受影响。CRYPTO（Binance/Gate）在部分网络需代理，可接受 CRYPTO 断更或自行配系统代理。

**Q5 Telegram 不推？**
`.env.local` 的 `TELEGRAM_PROXY` 与新环境不匹配（源机走本地代理）。删掉或改成新代理地址。

**Q6 WxPusher 微信推送 webhook 收不到？**
webhook 回调需要公网可达 URL。个人环境保持 `WXPUSHER_BINDING_ENABLED=true`（扫码绑定走轮询，不需公网）即可；`DELIVERY_ENABLED` 场景才需要公网回调（内网穿透后更新 WxPusher 后台回调地址与 `WXPUSHER_WEBHOOK_PATH_KEY`）。

**Q7 导入脚本行数 WARN？**
直接整轮重跑（替换语义幂等）。仍不一致 → 检查磁盘空间与 PG 日志。

**Q8 升级代码后数据库怎么办？**
主 dump 已是迁移终态 0033。后续代码升级带新迁移时：
`cd code ; $env:DB_MAIN_URL = "<§2 的强口令连接串>" ; .venv\Scripts\Activate.ps1 ; alembic upgrade head`
（alembic 只认进程环境变量 `DB_MAIN_URL`；不设置会回退 `alembic.ini` 的旧默认值）。**先备份再升级**：
`pg_dump -U zhice -Fc -d zhice_os_v2 -f backup_%DATE%.dump`

**Q9 想开机自启？**
任务计划程序 → 创建基本任务 → 触发器"计算机启动时" → 操作"启动程序"
`python.exe`、参数 `-m uvicorn zhice_os_v2.api.main:app --host 0.0.0.0 --port 8102`、
"起始于" `C:\<部署路径>\code`。scheduler 同理；前端程序填 `npm.cmd`、参数 `run dev`、起始于 `...\code\frontend`。

---

## 10. 版本与来源信息

- 代码：`main@50d7d5d`（含成交流水丢失窝案修复、国内双渠道通知、全市场扫描）
- 数据库：本机 `zhice_os_v2`（PostgreSQL），导出时刻 2026-09-20 22:43 (+08:00)
- 迁移终态：0033（`alembic heads` 单头）
- 行情基准行数：见 `market_history\export_manifest.json`
- `v2_latest_snapshot`（实时快照）**不在导出内**：部署后调度器 5 秒级任务自动重建
