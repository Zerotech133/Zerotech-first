# 多用户改造生产 Runbook 与渐进切换手册（Phase D · Task 10）

> 适用：zhice-os-v2 多用户改造（Phase A 0016–0020 / B 0021–0022 / C 0023 已合 main；Phase D T1–T9 已完成）。
> 来源计划：`docs/superpowers/plans/2026-09-06-multi-user-strategy-telegram-master.md`（生产渐进切换矩阵）、
> `docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md`（Task 10）、
> `docs/superpowers/plans/2026-09-06-telegram-directed-notifications.md`（执行后归档清单）。
> 本文**不含任何密码/token/登录凭据**；两台服务器 IP（192.168.2.113、159.75.220.212）已出现在仓内文档，凭据一律见运维记录。

**当前基线（写文时核对过）**：Alembic head = `0024`；本机 dev 库停在 `0023`（0024 仅 rehearsal 验证过，dev 已降回 0023 保持 legacy 运行）。生产切换前**严禁任何环境 `alembic upgrade head`**——0024 是切换门，只在第 3 节的时序里执行。

---

## 1. 总览：五阶段渐进切换矩阵

| 阶段 | `MULTI_USER_EXECUTION_MODE` | `TELEGRAM_NOTIFICATION_MODE` | 行为 | 进入门禁（进） | 验证信号（看） | 回滚（退） |
|---|---|---|---|---|---|---|
| **A** 现状 | `legacy` | `broadcast` | 新表/API 可用，旧执行与旧通知一字不变 | 部署到 **0023**（见第 2 节，勿 head）；`alembic current`=0023；`GET /api/v1/health` 200；pytest 基线全绿 | 与改造前一致；无新日志模式 | 不需要（默认态） |
| **B** 执行影子 | `shadow` | `broadcast` | legacy 照跑；盘前另记只读 fan-out 指标 | 阶段 A 稳定运行；先 `pg_dump` | scheduler 日志每市场每交易日一行 `multi_user_shadow {market, trade_date, instance_count, account_count, strategy_version_count}`；连续 **≥1 个完整市场周期** legacy 成交/持仓数量与金额逐日对比不变；`multi_user_shadow preview failed` 出现 0 次 | env 改回 `legacy` + 重启 api/scheduler（零数据变更） |
| **C** 通知影子 | `shadow` | `shadow` | 定向路由判定落库（event 有、delivery 无）；广播只发 owner 已解析的成交；市场报告广播照发 | 阶段 B 通过；先 `pg_dump` | 成交日志 `notification_shadow event_id=… user_id=… event_type=… eligible=… reason=…`（白名单五字段，无 chat_id）；`notification_owner_missing fill_id=… mode=shadow` 频率趋零；`market_report_routed … recipients=N`；`v2_notification_event` 行数增长而 `v2_notification_delivery` 不增长 | env 通知改回 `broadcast` + 重启（shadow 写下的 event 行保留无害：无 delivery 永不发送，**不删数据**） |
| **D** 强制隔离 | `enforced` | `shadow` | 用户交易隔离生效（legacy 路径停跑）；通知仍只对比 | 回填 + `verify --strict` 8 项全绿（第 3 节）；维护窗口；先 `pg_dump` | admin legacy 实例先行 canary，再灰度放量；owner 覆盖率 100%；无跨用户命中/预算超限/无 owner 新写入；`notification_owner_missing` 归零后不再复发；随后执行 **0024** 并再次 `verify --strict` 全绿 | 0024 **之前**：开关回 `shadow`/`legacy` + 重启即可（不动数据）；0024 **之后**：先 `alembic downgrade 0023`（仅放开 NOT NULL 约束，不动数据）→ 再回退开关 → 对回退期间新 NULL 重跑回填 |
| **E** 定向通知 | `enforced` | `directed` | 定向推送生效，广播路径关闭 | 阶段 D 稳定；观察期内 `broadcast_telegram`/`notify_fill` 调用计数 **= 0**；绑定前提满足（第 5 节；**159/113 当前不满足则本阶段整体顺延**） | worker 日志 `notification_worker sent/retry/failed delivery_id=…`；A 的成交只产生 A 的 delivery；429→RETRY 退避、401/403→FAILED；偏好关闭项无 delivery | env 改回 `shadow` + 重启；outbox/delivery 数据**只留不删**，失败行靠重放或人工清理 |

**禁止组合**：`MULTI_USER_EXECUTION_MODE=legacy` + `TELEGRAM_NOTIFICATION_MODE=directed`。应用启动校验（`settings.validate_telegram_notification_config`）直接拒绝，api 与 scheduler 均无法启动——回退时**先退通知模式，再退执行模式**，否则中间态起不来。

**总原则（master 计划铁律 10）**：每个生产阶段先备份数据库 → 再迁移 → 再 shadow → 对比通过后才切换。回滚永远只改开关 + 重启，不改数据；唯一例外是 0024 的对称 downgrade（也只动约束不动数据）。

---

## 2. 两拓扑部署步骤（目标：代码更新 + Alembic 到 **0023**）

> 两台远程当前均为 0015 基线；本节把 A/B/C/D 代码带上去但**停在 0023**。0024 属于第 3/4 节的切换动作，不随常规部署走。

### 2.0 共同门禁（部署后必跑）

```bash
uv run alembic current            # 必须 0023
uv run python scripts/verify_multi_user_ownership.py          # 只读人读摘要（0023 上可跑，回填前 null_owner 会有计数，属预期）
```

### 2.1 113（192.168.2.113 · ARM64 Docker）

拓扑：`docker-compose.prod.yml`（postgres 16 / redis / migrate / api / scheduler / nginx），镜像本机 build → `docker save` → scp → `docker load`（或 `scripts/make-deploy-package.sh` 离线包，两者二选一）。

```bash
# --- 本机（构建机）---
docker build -t zhice-api:prod .          # ARM64 须在 arm64 机器或开启 qemu 构建
docker build -t zhice-nginx:prod ./nginx
docker save zhice-api:prod zhice-nginx:prod | gzip > /tmp/images.tar.gz
scp /tmp/images.tar.gz <user>@192.168.2.113:<deploy-dir>/

# --- 113 上 ---
cd <deploy-dir>                           # docker-compose.prod.yml + .env 所在目录
docker load < images.tar.gz
docker compose --env-file .env up -d postgres && docker compose --env-file .env ps postgres   # 等 healthy
```

> **注意（migrate 服务与 0024 门）**：compose 的 `migrate` 服务硬编码 `alembic upgrade head`，而 head=0024。未经回填的库执行 0024 会被迁移内置前置校验 `RuntimeError` 拒绝（无害但**整个栈起不来**：api/scheduler 依赖 migrate 成功退出）。阶段 A–C 期间在 `<deploy-dir>` 放一个 `docker-compose.override.yml` 把 migrate 钉在 0023：

```yaml
services:
  migrate:
    command: alembic upgrade 0023
```

```bash
# --- 113 上继续 ---
docker compose --env-file .env up -d       # migrate 走 override 到 0023；api/scheduler/nginx 起来
docker compose --env-file .env ps          # migrate Exited(0)，其余 running
curl -fk https://127.0.0.1/api/v1/health   # 经 nginx 自签名 HTTPS；或容器内 8002 端口直查
```

前端在 `zhice-nginx:prod` 镜像内，随镜像更新，无单独发布步骤。第 4 节各阶段只改 `.env` + `up -d api scheduler` 重建。执行 0024 后（第 3.5 节）**删除该 override**，让后续部署回到 `head`。

### 2.2 159（159.75.220.212 · 腾讯云原生 systemd）

拓扑：`/opt/zhice-os-v2`（uv venv）+ systemd（api 8102 / scheduler 两个 unit）+ nginx + 本机 PG13，环境在 `.env.local`（systemd EnvironmentFile，细节见运维记录）。

```bash
# --- 服务器上（或按运维记录走同步脚本）---
cd /opt/zhice-os-v2
git fetch && git checkout <release-tag-or-sha>
uv sync                                   # 依赖；国内网络注意框③

# 迁移：明确目标 0023，绝不在切换前 upgrade head
set -a; source .env.local; set +a         # 或由 systemd EnvironmentFile 提供 DB_MAIN_URL
uv run alembic current                    # 部署前：0015
uv run alembic upgrade 0023               # 部署后：0023

sudo systemctl restart <api-unit> <scheduler-unit>     # unit 名以服务器实际为准（运维记录）
curl -fsS http://127.0.0.1:8102/api/v1/health          # 200
sudo nginx -t && sudo systemctl reload nginx
```

前端（159 为本地构建上传 dist，不在镜像内）：

```bash
# --- 构建机（macOS）---
npm --prefix frontend ci && npm --prefix frontend run build
rsync -av --delete --exclude='._*' frontend/dist/ <user>@159.75.220.212:<nginx-dist-目录>/
# --- 服务器上 ---
sudo nginx -t && sudo systemctl reload nginx
```

### 2.3 已验证的坑（来自历史部署运维记录）

> **① 159 venv shebang**：uv venv 的 console scripts（alembic/uvicorn 等）内嵌创建时的绝对路径；**移动/拷贝/改名 venv 后全部失效**。重建或迁移目录后必须重新 `uv sync` 生成脚本，不要拷旧 venv。
> **② AppleDouble `._` 文件**：macOS 打 tar/rsync 传 Linux 会产生 `._*` 元数据垃圾，污染代码目录与备份恢复。tar 加 `--exclude='._*'`（或导出前 `COPYFILE_DISABLE=1`），rsync 一律 `--exclude='._*'`。
> **③ uv 国内源**：159 直连 PyPI 慢/超时，`uv sync` 配国内镜像（如 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`）。
> **④ 前端 node 版本**：构建机与历史构建环境的 node 大版本保持一致（仓内 `package.json` 未钉 engines，具体版本以运维记录为准），避免 vite/tsc 产物差异。
> 以上细节仓内文档无完整记录时，以运维记录为准；操作前先在 113/159 各自验证一遍再进切换窗口。

---

## 3. 回填与 0024 门（生产顺序，缺一不可）

**适用时机**：阶段 C 观察通过后、切阶段 D（enforced）的同一个维护窗口内完成 3.1–3.5。159 库约 178 万行，0024 的 `SET NOT NULL`（五表 × 两列，PG 全表扫描 + ACCESS EXCLUSIVE 锁）**必须安排在非交易时段维护窗口**，先停 scheduler 写入。

### 3.1 备份（每阶段切换前都要）

```bash
# 159（宿主机自带 pg_dump）
cd /opt/zhice-os-v2 && set -a; source .env.local; set +a
uv run python scripts/backup_db.py        # 走 DB_MAIN_URL，写 backup/zhice_os_v2_<时间戳>.dump
# 等价模板：pg_dump "$DB_MAIN_URL" -Fc -f backup/zhice_os_v2_$(date +%Y%m%d_%H%M%S).dump

# 113（pg_dump 在 postgres 容器里；api 镜像是 slim 无 pg_dump）
cd <deploy-dir>
docker compose --env-file .env exec -T postgres pg_dump -U zhice -Fc zhice_os_v2 \
  > backup_$(date +%Y%m%d_%H%M%S).dump
```

### 3.2 backfill dry-run 与 conflicts 判读

脚本不在 113 镜像内（Dockerfile 只拷 src/migrations），先 `docker compose cp` 进 api 容器（容器 env 已带 `DB_MAIN_URL`）：

```bash
# 113
docker compose --env-file .env cp scripts/backfill_multi_user_ownership.py api:/tmp/
docker compose --env-file .env cp scripts/verify_multi_user_ownership.py api:/tmp/
docker compose --env-file .env exec api python /tmp/backfill_multi_user_ownership.py \
  --dry-run --json-out /tmp/ownership-dry.json
docker compose --env-file .env cp api:/tmp/ownership-dry.json ./

# 159
uv run python scripts/backfill_multi_user_ownership.py --dry-run --json-out /tmp/ownership-dry.json
python3 -m json.tool /tmp/ownership-dry.json
```

JSON 判读：每表 `source / already_owned / assignable / updated / unassigned / conflicts`，顶层 `updated_total / conflicts_total / unassigned_total`。dry-run 是 rollback-only，零写入，可反复跑。

**conflicts 处置决策树**（conflict 类型固定三种：`market_mismatch` / `unknown_market` / `no_anchor_version`）：

```text
conflicts_total == 0
  └→ 直接进 3.3。
market_mismatch（行内 market ≠ version.market）
  └→ 脚本按 version.market（权威）归属，不会错挂市场：
     抽样 conflicts 明细（JSON 内最多 50 条）人工核对是数据源写错行内 market
     └→ 确认无误 → 视作 0 处置，进 3.3（apply 时带 --strict 之外无需额外参数）
     └→ 看不懂/批量出现 → 停，先修数据根因，不 apply。
unknown_market（currency_for_market 不认识的 market）
  └→ 行会进单一 CN 兜底 unassigned 占位。逐条确认该 market 是否合法新增市场：
     合法 → 先补 currency_for_market 再重跑 dry-run；
     脏数据 → 接受兜底或先清数据。
no_anchor_version（整库无任何 strategy_version，极端病态）
  └→ 该行保持 NULL，0024 门必然不过。先查 0016-0020 迁移是否完整、strategy 表是否空。
```

**红线**：`unassigned > 0` 是正常路径（不可归属进 admin 占位，绝不映射普通用户），**不算失败**；`conflicts > 0` 且未经上述判读确认前，**绝不 `--apply`**。

### 3.3 apply（幂等，可重跑）

```bash
# 建议生产同时带 --strict：万一窗口期间又出现 conflict，整体回滚 exit 1，绝不带冲突落库
# 159
uv run python scripts/backfill_multi_user_ownership.py --apply --strict --json-out /tmp/ownership-apply.json
# 113
docker compose --env-file .env exec api python /tmp/backfill_multi_user_ownership.py \
  --apply --strict --json-out /tmp/ownership-apply.json
```

幂等判据：重复 apply 第二次 `updated_total == 0`；脚本只 UPDATE 归属列（`WHERE user_strategy_id IS NULL`，绝不覆盖已有归属）+ INSERT 占位 account/instance（`ON CONFLICT DO NOTHING`），**不改历史行数**。

### 3.4 verify --strict（0024 的前置门）

```bash
# 159
uv run python scripts/verify_multi_user_ownership.py --strict --json-out /tmp/verify.json
# 113
docker compose --env-file .env exec api python /tmp/verify_multi_user_ownership.py --strict
```

只读（结束统一 rollback），可反复跑。8 项判读：

| 检查项 | 含义 | 不过时怎么办 |
|---|---|---|
| `null_owner` | 五表（plan/approval/intent/position/replay）`user_strategy_id` 或 `account_id` 为 NULL | 回填没跑干净 → 重跑 3.3；切换窗口期间 legacy 又写了新行 → 见 3.5 时序，属预期 |
| `cross_user` | 行实例用户 ≠ 行账户用户 | 严重：不得继续；查是否有人工改库/脚本 bug |
| `market_mismatch` | 行内 market ≠ 实例→版本→策略 market | 对照 3.2 决策树核对样本 |
| `orphan_chain` | 归属引用的 instance/account 不存在（FK 应挡，防御） | 查 FK 是否被禁/手工删行 |
| `negative_available` | `cash_balance<0` / `reserved_cash<0` / `reserved_cash>cash_balance` | 主要盯 reserved>cash（无 DB 约束）；查并发预留 |
| `quota_overflow` | 共享账户启用实例 `capital_quota` 之和 > `initial_capital`；启用共享实例 quota=None 单独计 `shared_missing_quota` | 关停超配实例或调额；历史无 quota 实例先补齐或禁用 |
| `duplicate_delivery` | 同 `(event_id,user_id,channel)` 多行（unique 应挡，防御） | 查唯一约束是否存在（0023） |
| `legacy_placeholder_sanity` | `ustrat-legacy-*` 占位必须 `enabled=False` 且挂 ADMIN 账户 | 有人误启用占位 → 立即 disable |

`--strict` 任何一项 FAIL 即 exit 1，**0024 不许执行**。

### 3.5 0024 时序：**先 enforce，后 0024**（推荐）

0024 把五表归属列 `SET NOT NULL`。关键事实（已核对代码）：**legacy 执行路径的计划构建（`TradePlanService.create_from_candidate`）不携带归属**，即 legacy/shadow 阶段每天仍在产生新的 NULL 归属行；只有 enforced 路径（`UserInstanceExecutionService`）保证新写入必带归属。由此：

- **shadow 阶段不能执行 0024**：回填刚清完 NULL，legacy 一跑又写新的，0024 的内置前置校验会拒绝（无害但白忙）；即便强过了门，legacy 后续写入会直接撞 NOT NULL 报错，**交易落库失败**。
- **推荐时序（同一维护窗口内，顺序执行）**：

```text
1. 停 scheduler 交易写入（维护窗口；159 库大，0024 的 DDL 也在此窗口）
2. pg_dump（3.1）
3. backfill --apply --strict（清掉 shadow 期间累积的尾批 NULL，3.2–3.3）
4. verify --strict 全绿（3.4）
5. env: MULTI_USER_EXECUTION_MODE=enforced → 重启 api/scheduler（第 4 节阶段 D）
6. 观察 enforced 路径正常出计划/成交（canary：admin legacy 实例先行）
7. 执行 0024：
   159: uv run alembic upgrade 0024
   113: docker compose --env-file .env run --rm migrate alembic upgrade 0024
        （然后删除 2.1 的 override，恢复 upgrade head）
8. verify --strict 再次全绿 → 窗口结束，恢复放量
```

- **为什么"先 enforce 后 0024"而不是反过来**：enforced 先在 0023 上运行，行为收敛靠 verifier 观察（此期间若 enforced 路径意外写 NULL，verifier 能抓到，还有只改开关的完整回退余地）；0024 作为**棘轮**最后落下，把已验证的状态锁死。反过来（先 0024 后 enforce）则从落约束那一刻起 legacy 永久不可回退、且中间任何 legacy 写入都变成硬错误——等于放弃观察窗口与回退能力。
- 0024 自带前置校验（任一表存在 NULL 即 `RuntimeError` 拒发 DDL），但**不要依赖它兜底**：门禁是 3.4 的 strict 绿，内置校验只是最后保险丝。
- **0024 后回退 enforced**：必须 `uv run alembic downgrade 0023`（113：`docker compose --env-file .env run --rm migrate alembic downgrade 0023`）先放开约束——downgrade 只改 nullable，**不动任何数据**——再改回开关重启，随后对回退期间的新 NULL 重跑 3.3 回填。直接在 0024 上回退 legacy 会因 NOT NULL 使交易写入失败。

---

## 4. 每阶段操作（改哪里 / 重启什么 / 看什么 / 怎么退）

开关位置：113 = `<deploy-dir>/.env`（compose env-file，模板 `.env.prod.example`）；159 = `/opt/zhice-os-v2/.env.local`；本机 dev = shell 环境。改完必须重启**两个**进程（api + scheduler）——scheduler 是模式的主要消费方（盘前执行门、通知 worker 都注册在 scheduler）。

```bash
# 113（改 .env 后）
docker compose --env-file .env up -d api scheduler
# 159（改 .env.local 后）
sudo systemctl restart <api-unit> <scheduler-unit>
```

观察通道：

```bash
# 113
docker compose --env-file .env logs -f scheduler api | grep -E \
  'multi_user_shadow|notification_shadow|notification_owner_missing|market_report_routed|notification_worker'
# 159
journalctl -u <scheduler-unit> -f | grep -E 'multi_user_shadow|notification_shadow|notification_owner_missing|market_report_routed|notification_worker'
```

verifier 任何时候可重跑（只读），是各阶段通用的回归探针。

### 阶段 B（shadow 执行）

- 改 env：`MULTI_USER_EXECUTION_MODE=shadow`，`TELEGRAM_NOTIFICATION_MODE` 保持 `broadcast`；先 pg_dump。
- 看什么：每个交易日每市场应出现**一行** `multi_user_shadow {…instance_count…}`；`instance_count/account_count` 随用户启用实例增长属正常；legacy 侧成交、持仓、风控、订单与上一周期逐日对比不变；不允许出现 `multi_user_shadow preview failed`（出现说明 preview 崩了，legacy 虽照跑但要修）。
- 健康判据：连续 ≥1 个完整市场周期，对比零差异、零异常日志。
- 回滚：`MULTI_USER_EXECUTION_MODE=legacy` + 重启。零数据变更。

### 阶段 C（shadow 通知）

- 改 env：`TELEGRAM_NOTIFICATION_MODE=shadow`；先 pg_dump。
- 看什么：
  - 成交后 `notification_shadow event_id=… eligible=… reason=…`（只有五白名单字段）；无 chat_id/token 泄漏；
  - `notification_owner_missing … mode=shadow`：**预期语义**——shadow 期间 legacy 新开仓无归属 → 不广播只记日志；已回填的历史持仓平仓会解析到 admin legacy 占位 → 广播照发并落 admin 的 event。owner_missing 频率应随历史持仓了结趋零；
  - 市场报告：广播照发（shadow 不停广播）+ `market_report_routed … mode=shadow recipients=N`；
  - scheduler 启动日志出现 `notification_worker` job（15s 一轮，shadow 下无 delivery 可发，空转正常）。
- 健康判据：owner 解析率收敛；`v2_notification_delivery` 在 shadow 下零增长；广播内容与阶段 A 一致（除上述无 owner 成交不再广播——已知且可接受的语义变化，提前周知用户）。
- 回滚：`TELEGRAM_NOTIFICATION_MODE=broadcast` + 重启。shadow 写下的 event 行保留（无 delivery 不会发送），**不删**。

### 阶段 D（enforced 执行）——按第 3.5 节时序，包含回填/verify/0024

- canary：先只给 admin legacy 实例开 enforced 观察，再同公共策略做 A/B 合成对比（独立账户现金/持仓隔离），最后逐步放普通用户。
- 看什么：`multi_user_shadow` 日志**消失**（不再走 legacy 体）；计划/订单/成交/持仓按实例账户分离；无 `notification_owner_missing mode=directed`（此时仍是 shadow 通知）；0024 落完后 `verify --strict` 持续全绿。
- 健康判据：owner 覆盖率 100%；无跨用户命中、无预算超限、无无 owner 新写入；任何一条出现立即按第 1 节矩阵回退。
- **门禁补充（Phase D 整体终审 2026-09-07）**：
  1. `GET /strategies/{sv}/detail` 的持仓/成交按 strategy_version_id 聚合，会**跨用户实例混显**（同一公共版本被多用户启用时各实例的持仓/成交合并展示）——终审 Important #1 只修了"私有策略可见性"（他人私有 404）。**切 enforced 前必须完成 detail 按 user_strategy 实例隔离**，否则多用户下的策略详情数据互串。
  2. `user_strategy_instance.upgrade_available_version_id` **计算归属待立项**：全仓无写入方（无 scheduler/发布流程落该字段），运行实例页升级横幅目前靠前端按"同策略最新可运行版本 > 当前"兜底计算。后端字段的计算时机（版本发布/调度扫描）与写入方确定前，该字段不应作为任何流程的唯一依据。
- 回滚：见 3.5 末条（0024 前后语义不同）。

### 阶段 E（directed 通知）

- 前置：阶段 D 稳定 + **观察期内广播调用计数为 0**（日志 grep `notify_fill`/`broadcast_telegram` 无成交/报告广播行）+ 第 5 节绑定前置清单全绿。
- **159/113 现状不满足绑定前提**（见第 5 节），在拿到可信公网 HTTPS 前这两台**停在阶段 D**；强行切 directed 的后果：所有 delivery 因无 chat_id 终态 `FAILED no_chat_binding`（不重试），用户收不到任何通知。
- 改 env：`TELEGRAM_NOTIFICATION_MODE=directed`；先 pg_dump。
- 看什么：`notification_worker sent/retry/failed`；429→RETRY 退避、401/403→FAILED 终态；A 的成交只产生 A 的 delivery，关闭 fill 偏好的实例无 delivery；中英文模板按 user.locale。
- 回滚：`TELEGRAM_NOTIFICATION_MODE=shadow` + 重启。outbox/delivery **只留不删**（回退靠改模式，不靠清数据）；FAILED 行等修复后重放或按保留策略清理。

---

## 5. Telegram 绑定启用前置清单（Phase C 归档浓缩）

**绑定开关**：`TELEGRAM_BINDING_ENABLED=false`（默认）。开启时启动校验要求以下四项齐备，缺一即拒：

1. `TELEGRAM_BOT_TOKEN`（@BotFather 创建；原文绝不进前端/日志/文档）；
2. `TELEGRAM_BOT_USERNAME`；
3. `TELEGRAM_WEBHOOK_SECRET`（webhook `X-Telegram-Bot-Api-Secret-Token`，`hmac.compare_digest` 比对）；
4. `TELEGRAM_WEBHOOK_PUBLIC_URL` 且必须 `https://` 开头的**公网可信**证书。

**网络前提（当前结论）**：159 是 HTTP、113 是自签名 HTTPS，**均不满足** Telegram webhook 的公网可信要求——在拿到可信证书/可信反代之前，两台保持 `TELEGRAM_BINDING_ENABLED=false`，关闭绑定不影响其余功能（广播照常、/my API 照常）。

**独立 Bot 原则**：每个独立数据库部署（本机/113/159）第一阶段各用自己的 Bot 与 webhook secret，**不共享中心 webhook**——webhook 按单 bot 注册，共享会串数据库。

**webhook 配置脚本用法**（先检查后生效，两步走）：

```bash
# 只读检查：打印 redacted 的当前/期望 webhook 状态（绝不打印 token 原文）
uv run python scripts/configure_telegram_webhook.py
# 确认无误后实际 setWebhook（url + secret_token），随后 getWebhookInfo 验证
# URL 不一致或 last_error_message 非空则 exit 1
uv run python scripts/configure_telegram_webhook.py --apply
```

脚本拒绝非 `https://` URL；走 `TELEGRAM_PROXY`（国内直连被墙）。

**enforced/directed 启用前已确认的遗留缺口（Phase C 终审归档，切阶段 D/E 前必读）**：

1. **开仓（BUY）无定向通知**：enforced 的 owned 订单链（`_persist_owned_order_chain`）不经 `_record_fill`，开仓成交不触发 `route_fill`——目前仅平仓（SELL）有定向通知。切 enforced 前要么补接线，要么明确周知用户"开仓静默"。
2. **人工审批 `execute_approved` 不走 `_record_fill`**：三模式下该路径均无成交通知（承自广播时代的既有缺口，非本次引入）。
3. **无保留清理策略**：已消费 challenge 行与 SENT delivery/event 只增不删，需按保留窗口定期归档/清理（容量规划考虑）。
4. ~~实例级通知偏好无 API~~：已由 Phase D T6 补齐（`/my/notification-preferences` + 实例 create/PATCH 的 `notification_preferences`）。
5. ~~409 `telegram.chat_conflict` 前端文案缺失~~：已入 `frontend/src/i18n/locales/{zh,en}/errors.json`。

绑定开启后：manual PATCH `/auth/me/telegram` 自动变为 409 `telegram.manual_binding_disabled`（用户一律走 deep-link）；deep-link challenge 10 分钟过期、一次一码、只存 hash。

---

## 6. 红线

1. **绝不 `git add .` / `git add -A`**：工作区存在含明文凭据的运维脚本（`scripts/sync_to_*.py` 等）与 `.env.prod`/`.env.local`/`*.dump`。提交一律显式路径（本手册即 `git add docs/runbooks/multi-user-rollout.md`）。
2. **绝不信文档抄命令进 shell 的密码**：本文档模板全部引用环境变量（`$DB_MAIN_URL`、`.env`），真实凭据只存服务器 env 与运维记录。
3. **绝不带 conflicts 强 apply**：dry-run → 决策树（3.2）→ `--apply --strict`。
4. **0024 前必 `verify --strict` 全绿**，且仅在 enforced 已生效（或交易写入已停）的窗口执行；内置前置校验是保险丝不是门禁。
5. **回滚只改开关 + 重启，不改数据**；0024 后回退先 `alembic downgrade 0023`（仅放开约束）。
6. **每阶段切换前 pg_dump**（master 铁律 10）。
7. **切换前绝不 `alembic upgrade head`**：head=0024，常规部署钉 0023（113 用 override，159 显式 `upgrade 0023`）。
8. bot token / webhook secret / 绑定 challenge 原文**绝不**进前端、数据库日志、错误响应与本仓库。
