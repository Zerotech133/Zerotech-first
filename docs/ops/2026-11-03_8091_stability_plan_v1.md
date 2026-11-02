# 8091 稳定性方案 v1（S1：会话持久化 + 受监督服务 + 重启健康门）

- **任务**：P0/S1（2026-11-03~11-10；RND_Plan §二/§五）｜**状态**：`方案 + 代码就绪（默认关闭；激活需 L2）`
- **交付**：`portal/auth_gateway.py` 可选 SQLite 会话持久化（默认关）+ `portal/test_auth_session_persist.py`（9 例）+ 本方案
- **边界**：本任务**未重启 8091、未改 nginx/ops 脚本/计划任务/凭据、未 git**；会话持久化默认关闭，线上行为不变。
- **依据**：`docs/plans/2026-11-03_RND_Plan_v1.md`（S1）、`docs/ops/SYSTEM_HEALTH_OPTIMIZATION_v1.md`（P0 8091 单点）、`portal/start_portal_api.ps1`（只读）、`portal/auth_gateway.py`
- **验收目标（RND_Plan）**：连续 **7 天无人工重启**；**重启后 session 不丢**

## 0. 结论摘要

| 项 | 现状/方案 |
|---|---|
| 脆弱点 1：会话内存 | `SessionStore` 纯内存（TTL 1h），8091 重启 → 全员掉线、`/hq` `/dw` 302、Q2 验收需重登。**已实现**可选 SQLite 持久化（`ZB_SESSION_PERSIST=1` 才启用，默认关）；**不落盘 IdP token** |
| 脆弱点 2：监督弱 | 任务 `AI工作站门户API` = S4U / `RunLevel=Limited` / `ExecutionTimeLimit=PT10M` / `RestartCount=0` / 每 5 分钟触发；launcher 前台 `& python portal_api.py`，无「起后健康门」、无自动重启策略 → 见 §2 三套受监督改造方案（待 L2） |
| 脆弱点 3：重启窗口 | nginx 动态反代 `proxy_pass http://$h:8091`（变量+resolver，逐请求解析，**无需 reload**）→ 重启期只出现 10–60s 502；配合会话持久化可做到「不掉登录」 |
| 本次交付 | 代码 + 9 例单测（默认内存/重启可读/过期清理/登出删除/损坏 fail-closed/无明文 token/跨重启 login-guard-logout/503 降级/并发安全）全绿；行数 427→611（auth_gateway），默认路径不变 |
| 激活 | **L2**：改 env + 受控重启 8091（窗口见 §5）；未批不执行 |
| 不做 | 不改 nginx / 不换 supervisor 任务（本任务只出方案）；不做多实例/Redis 集群；不改 OIDC 无状态方案 |

## 1. 现状实测（2026-11-03 06:43–06:47，只读）

| 项 | 实测值 |
|---|---|
| 8091 进程 | PID `17736`，StartTime `2026-11-03 05:47:18`，SessionId=1，`GET /health` = **200**（整个本任务窗口未重启，launcher 日志连续 `skip: 8091 already serving` 06:10/06:15/…/06:45） |
| 监督任务 | `AI工作站门户API`：`MSFT_TaskTimeTrigger`，Repetition `PT5M`（5 分钟一跳）；Principal `UserId=t / LogonType=S4U / RunLevel=Limited`；Settings `ExecutionTimeLimit=PT10M`、`RestartCount=0`、`MultipleInstances=IgnoreNew`、`StartWhenAvailable=True`；LastRunTime 06:45:01，LastTaskResult 0 |
| Launcher | `portal/start_portal_api.ps1`：K234 幂等锁（/health 3×探活，活着即 skip）、K-ORG-155 冷启动 import 冒烟（失败拒绝启动+告警）、`C:\Temp\PORTAL_API_RESTART.flag` 受控重启、前台 `& python portal_api.py`（`C:\aios` junction 路径），日志 `C:\Temp\portal_api_task.log` / `portal_api_out.log` / `portal_api_err.log` |
| 入口链路 | 8734 portproxy → 127.0.0.1:8080（ai-portal nginx 容器）→ `location` 内 `proxy_pass http://$h:8091`（`$h=host.docker.internal`，变量反代逐请求解析）→ 8091 |
| 会话 | `portal/auth_gateway.py:SessionStore` 内存，`SESSION_TTL_SECONDS=3600`；登录会话 cookie `zb_session` HttpOnly+SameSite=Lax |
| 已知事故 | SYSTEM_HEALTH：05:47 重启窗口门户不可用；1 小时内多次重启；`RunLevel=Limited` 无法管理提权进程风险；内存会话重启即失 |

## 2. 受监督服务改造（待 L2）

### 2.1 目标

1. 8091 进程异常退出 → **自动拉起**（≤90s 内 `/health` 200）；
2. 不误杀健康实例（沿用 K234 幂等锁语义，杜绝 bounce war）；
3. 重启/失败事件可观测（日志 + `dev_events` + 告警），可对账「7 天无人工重启」；
4. **不改变 8734/nginx 入口链路**（nginx 侧零改）。

### 2.2 三套候选（推荐 B 先行，A 作为收敛目标）

| 方案 | 内容 | 优点 | 代价/风险 | 结论 |
|---|---|---|---|---|
| **A. NSSM/WinSW 服务化** | 安装为 Windows 服务（LocalSystem 或指定用户 `t` + Highest），内置 restart-on-failure/health check | 最接近「受监督服务」；崩溃自动重启、开机自启；可配 stdout 循环日志 | 需管理员安装/卸载；服务账户与 `C:\aios` junction、`_vault` 凭据读取权限需验收；服务化后启动脚本语义要重写 | 目标态（L2 专项） |
| **B. Highest 任务 + detached wrapper + 重启策略**（推荐先做） | 现有任务改为 `RunLevel=Highest`；去掉 `PT10M` 执行上限（或设 `PT0S` 仅对 wrapper）；`RestartCount=3/Interval=PT1M`；wrapper 以 detached 方式拉起 8091 并**自轮询 `/health`**；任务仍然每次跑幂等锁 | 不需装服务；改动集中在任务设置 + wrapper；回滚=恢复任务 XML + 旧 launcher | Highest 任务需 A0/管理员一次授权；`RestartCount` 仅在任务进程失败时触发，wrapper 必须保持前台直到健康或退出 | **先做**（P0b） |
| **C. 独立 watchdog 任务** | 新增每 1–2 分钟的 `portal_api_health_watchdog.ps1`：/health 3×失败 → 写 restart flag → 调 launcher；冷却 2min、每小时≤3 次；写事件/告警 | 与现有任务解耦，最小侵入；可先跑一周观察 | 仍是「双触发器」，需与 K234 锁配合防 bounce；只覆盖死亡拉起，不解决 PT10M | 作为 B 的补充/过渡 |

### 2.3 「重启健康门」设计（launcher/wrapper 增补，L2 变更）

```text
1) 探活（3×/3s，Proxy=$null）→ 活着：skip，exit 0（K234 现状，不变）
2) 冷启动 import 冒烟（K-ORG-155 现状，不变）
3) 记录旧 PID/启动时间/命令行 → kill 旧实例（restart flag 路径）
4) 启动新实例（detached），落盘 PID + start_time
5) 健康门：轮询 http://127.0.0.1:8091/health，最长 60s：
   - 200 → 记健康门 PASS + 事件，退出 0
   - 超时/非 200 → 记 FAIL + 告警，保留 err 日志尾部；默认不再盲目重试（交 supervisor/wrapper 决策）
6) 失败回滚：保留旧代码/旧命令原文；健康门 FAIL 时可人工/自动按旧命令重起（同代码，无需 nginx 变更）
```

### 2.4 nginx / 入口不中断

- nginx 使用**变量反代**（`set $h host.docker.internal; proxy_pass http://$h:8091;`）→ 逐请求解析，**8091 重启不需要 nginx reload**，nginx 进程本身无感；
- 重启窗口内请求会短暂 502（实测历史窗口 10–60s）；会话持久化后**登录态不丢**，用户刷新即可；
- 不建议在本期引入 8091 双实例（端口固定 + nginx 单上游）；若后续要蓝绿，需 L2 做 portproxy/上游切换（单独五要素）；
- 前端已有 401→登录 CTA、502 可重试；`/hq` 等页面 30s 自动刷新可自愈。

## 3. 会话持久化（已实现，默认关闭）

### 3.1 开关与路径

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `ZB_SESSION_PERSIST` | `0`（关） | `1/true/yes/on` 启用 SQLite 持久化；**默认 0 = 与线上内存行为完全一致** |
| `ZB_SESSION_DB` | `portal/private/auth_sessions.db` | DB 路径；`portal/private/` 已被 `.gitignore` 覆盖（不随仓库分发），激活时需 ACL 收紧（L2） |

### 3.2 数据与安全

- 持久化字段：`session_id/sub/capacity/identity_context/csrf_hash/issued_at/auth_time/expires_at/idp/amr`；
- **不落盘 IdP token**（`zhice_token` 列不存在；测试用 marker 扫描 DB+WAL 断言无明文）；
- **不落盘明文 CSRF**：只存 `csrf_hash`；8091 重启后首次读取会重新签发 CSRF 并回写哈希（保证 logout 可用）；
- TTL 仍 1h（`SESSION_TTL_SECONDS=3600`）；过期在 `create/get` 时清理（memory+DB）；
- 说明：`session_id` 本身是 bearer 凭据 → **DB 文件必须 ACL 限制**（用户 `t` + SYSTEM/管理员只读），不可放共享目录；
- 线程安全：单 store `threading.Lock` 包裹内存+SQLite 操作（每操作短连接、`PRAGMA journal_mode=WAL`、timeout=5s）；9 例测试含 8 线程并发；
- 失败语义（fail-closed）：DB 写入失败 → `SessionPersistenceError` → 登录返回 **503 `session_store_unavailable`**、不下发 cookie；DB 读取失败 → 视为无会话（guard/me 401），**绝不降级匿名**；
- 多进程/重启重叠：WAL 支持新旧进程短时共存；会话加载后回填进程内缓存，避免 CSRF 轮换漂移（测试 §7 覆盖）。

### 3.3 代码落点（当前行号，611 行）

| 位置 | 内容 |
|---|---|
| `portal/auth_gateway.py:57` | `SESSION_PERSIST_ENABLED`（env 解析，默认 False） |
| `portal/auth_gateway.py:59` | `SESSION_PERSIST_DB`（默认 private/sessions DB） |
| `portal/auth_gateway.py:65` | `SessionPersistenceError`（fail-closed 信号） |
| `portal/auth_gateway.py:98` | `SessionStore`（内存 + 可选 SQLite；`persist=`/`db_path=` 可注入） |
| `portal/auth_gateway.py:183` | `_db_get_locked`（读取/过期清理/CSRF 重签发/回填缓存） |
| `portal/auth_gateway.py:249` | `create`（持久化写入失败则不建内存会话） |
| `portal/auth_gateway.py:310` | `STORE = SessionStore()`（默认内存） |
| `portal/auth_gateway.py:509/536` | 登录（local-admin / 智策密码桥）捕获持久化异常 → 503 |

### 3.4 测试

`portal/test_auth_session_persist.py`（9 例，全绿）：
1. 默认内存 & 关闭时不创建 DB；2. 持久化后新 store 可读（模拟重启）；3. 过期清理；4. 登出删除 DB 行；5. 损坏 DB fail-closed（读 None/写 raise/不降级内存）；6. 无明文 IdP token（DB+WAL marker 扫描）；7. login→换 store（重启）→me/guard→logout 全兼容；8. 持久化不可用登录 503 无 cookie；9. 8 线程并发安全。

## 4. L2 激活五要素（**仅批准后执行**）

### ① 命令全文

```powershell
# 0) 前置：代码已合并 + 评审门绿；备份任务 XML 与 launcher 现状
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
schtasks /Query /TN "AI工作站门户API" /XML > "C:\Temp\AI工作站门户API_$ts.xml"
Copy-Item C:\aios\portal\start_portal_api.ps1 "C:\Temp\start_portal_api.ps1.bak-$ts" -Force
Get-Item C:\Temp\AI工作站门户API_$ts.xml, "C:\Temp\start_portal_api.ps1.bak-$ts" | Select-Object FullName,Length

# 1) 初始化 DB 目录并 ACL 收紧（仅用户 t + SYSTEM/管理员）
New-Item -ItemType Directory -Force C:\aios\portal\private | Out-Null
$db = "C:\aios\portal\private\auth_sessions.db"
icacls C:\aios\portal\private /inheritance:r /grant:r "t:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"

# 2) 仅对 8091 任务上下文注入 env（不改机器全局；在 launcher 顶部加 2 行，随备份可回滚）
#    $env:ZB_SESSION_PERSIST = '1'
#    $env:ZB_SESSION_DB = 'C:\aios\portal\private\auth_sessions.db'
#    （或 L2 批准写用户级 env；推荐 launcher 内显式设置，便于随脚本回滚）

# 3) L2 窗口内受控重启（低峰，例 06:00–07:00；脚本已含 3×探活/冷启动冒烟/健康门）
powershell -ExecutionPolicy Bypass -File C:\aios\portal\start_portal_api.ps1

# 4) 验证（代码级 + 会话跨重启）
python -X utf8 -m pytest portal/test_auth_session_persist.py portal/tests/test_auth_gateway.py -q
$jar = "C:\Temp\zb_persist_verify.cookies"
curl.exe -s -c $jar -X POST http://127.0.0.1:8734/api/auth/login -H "Content-Type: application/json" `
  --data-binary '{"username":"<local-admin>","password":"<人工输入>"}' -o NUL -w "login=%{http_code}`n"
$sid = (Select-String -Path $jar -Pattern 'zb_session').Line   # 仅用于本窗口验证，禁止外传
curl.exe -s -b $jar -o NUL -w "me_before_restart=%{http_code}`n" http://127.0.0.1:8734/api/auth/me
# 再次受控重启 8091（同第 3 步）后，用同一 cookie：
curl.exe -s -b $jar -o NUL -w "me_after_restart=%{http_code}`n" http://127.0.0.1:8734/api/auth/me   # 期望 200
curl.exe -s -b $jar -o NUL -w "hq_after_restart=%{http_code}`n" http://127.0.0.1:8734/hq             # 期望 200
# DB 不含明文 token（无凭据输出）：
python -X utf8 -c "import pathlib; b=pathlib.Path(r'C:\aios\portal\private\auth_sessions.db').read_bytes(); print('TOKEN_MARKER_ABSENT', b'token' not in b.lower())"
Remove-Item $jar -Force -ErrorAction SilentlyContinue
```

### ② 影响面

- 仅 8091 进程重启（10–60s 502 窗口）；nginx/8734/8080 容器、8105、8091 依赖的队列/计划任务**不改**；
- 激活后**首次重启前已登录用户会掉线一次**（旧内存 session 未落库）；其后重启保持登录；
- 登录失败时（DB 异常）返回 503，用户仍可用密码/Passkey 重试；不影响其他 API 前缀；
- DB 文件新增于 `portal/private/`（gitignore），需 ACL；不新增端口/服务/计划任务；
- `/hq` `/dw` `/devops` 等 `auth_request` 保护面沿用同一 guard，行为不变。

### ③ 不可逆点

- 无数据不可逆：DB 可删除（Windows 回收站，AGENTS 红线）→ 回到纯内存；
- 激活后 `ZB_SESSION_PERSIST=0` + 受控重启即可完全回退；不涉及 schema/数据迁移；
- 唯一成本：回退时持久会话全部失效（用户需重登一次）。

### ④ 替代方案

1. 维持内存现状（零改动；每次重启全员掉线，S1 不达验收）；
2. OIDC 无状态签名 cookie（长期方向，但需改 cookie 结构/签名密钥管理 + 现有 guard 语义改造，工作量大）；
3. Redis session（引入第二外部依赖；当前无 Redis 常驻，违反「不新增第二栈」倾向）；
4. 只做受监督服务不持久化（重启自动恢复但会话仍失，Q2/HQ 验收仍被打断）→ 不足。

### ⑤ 回滚预案

```powershell
# 1) launcher 还原（去掉 2 行 env）
Copy-Item "C:\Temp\start_portal_api.ps1.bak-<ts>" C:\aios\portal\start_portal_api.ps1 -Force
# 2) 受控重启（K234 幂等；restart flag 路径）
New-Item -ItemType File -Force C:\Temp\PORTAL_API_RESTART.flag | Out-Null
powershell -ExecutionPolicy Bypass -File C:\aios\portal\start_portal_api.ps1
# 3) 验证：/health 200；/api/auth/status authenticated=false（重登后 true）
curl.exe -s -o NUL -w "health=%{http_code}`n" http://127.0.0.1:8091/health
# 4) （可选）DB 文件进回收站；从备份恢复任务 XML（若任务设置被改）
schtasks /Create /TN "AI工作站门户API" /XML "C:\Temp\AI工作站门户API_<ts>.xml" /F
```

## 5. 分阶段计划与窗口

| 阶段 | 内容 | owner | 窗口/截止 |
|---|---|---|---|
| P0a（本次） | 持久化代码 + 9 测试（默认关）+ 本方案 | DSH/ARCH1 | 2026-11-03（已完成） |
| P0b（L2） | 任务设置收敛（Highest/去 PT10M/RestartCount）+ 健康门 wrapper + watchdog（方案 B/C 落地） | ARCH1/OPS（A0 批） | 11-04 后低峰窗口；11-08 前 |
| P0c（L2） | 激活持久化 env + 受控重启 + 跨重启会话验收 + 7 天观察 | ARCH1/DSH | 11-05 06:00–07:00 或 11-06 低峰；观察至 11-10 出口门 |
| 回滚演练 | launcher 还原 + env 关闭 + 重启（一次写入回执） | DSH | 激活后 24h 内 |

- 合并窗口：主 16:30–17:00 / 次 21:40–22:10（代码先合、激活后置）；避开 11-04 黄忠验收与 11-08 ACAD1 冻结的变更窗口。

## 6. 验证与度量

```powershell
# 代码级（不需激活、不重启）
python -X utf8 -m pytest portal/test_auth_session_persist.py portal/tests/test_auth_gateway.py portal/tests/test_auth_oidc.py -q
python -X utf8 tools/check_syntax.py --fail-on-warning --files portal/auth_gateway.py,portal/test_auth_session_persist.py
python -X utf8 -m ruff check portal/auth_gateway.py portal/test_auth_session_persist.py
# 激活后运行态
curl.exe -s -o NUL -w "8091 health=%{http_code}`n" http://127.0.0.1:8091/health
# 7 天指标：重启次数（launcher 日志 "restart-flag"/"starting" 计数）、健康门 PASS/FAIL、会话跨重启存活率、502 窗口时长
Select-String -Path C:\Temp\portal_api_task.log -Pattern '^starting|restart-flag|skip:' | Select-Object -Last 20
```

## 7. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | DB 文件泄露 session_id（bearer） | `portal/private/` + ACL（仅 `t`/SYSTEM/管理员）；DB 不含 IdP token；不做共享目录；`ZB_SESSION_DB` 不指向仓库/备份明文目录 |
| R2 | 双实例/重启重叠时 CSRF 轮换竞争 | 单实例部署；读取后回填缓存；WAL + 5s timeout；极端情况用户重登一次（可接受） |
| R3 | supervisor 误杀健康 8091（历史 bounce war） | 保持 K234 幂等锁语义；watchdog 冷却/限次；不允许「无条件 kill 全部 portal_api」 |
| R4 | `RunLevel=Limited` 无法管理提权进程 | P0b 改 Highest（A0 授权）或改用 NSSM/WinSW；先解决 supervisor 再启用 watchdog |
| R5 | `PT10M` 执行上限误杀 wrapper | P0b 去掉/放宽 `ExecutionTimeLimit`；wrapper 前台守到健康门结束 |
| R6 | 激活窗口 502 被误判为故障 | 选低峰窗口；提前通知；会话持久化保证不重登；HTTP 502 页面/重试自愈 |
| R7 | 回退后用户全部掉线 | 回退动作选低峰；回执记录；用户重登即可 |
| R8 | 测试/实现漂移（默认行为被误改） | 默认值测试（`SESSION_PERSIST_ENABLED is False`）+ 9 例单测进 fast 门；激活前 `pytest` 全绿 |

## 8. 边界与未做（本次）

- **未激活**：`ZB_SESSION_PERSIST` 未设置，线上仍内存会话（默认行为不变）；
- **未重启 8091**（实测 PID 17736 自 05:47:18 未变，launcher 连续 skip）；
- **未改** nginx / `ops/*.ps1` / 计划任务 / 凭据；**未 git**；
- 未做 supervisor 任务设置变更（方案 B/C 需 L2）；未做 NSSM/WinSW 安装；未做多实例蓝绿。
