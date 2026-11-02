# C1 激活后自检 Runbook v1（8091/API/公共鉴权/L1 口径）

- **日期**：2026-11-03｜**任务单**：task-40（P0 C1 激活后自检器）
- **工具**：`tools/hq_c1_verify.py`（只读 GET；不登录、不重启、不改服务）
- **适用**：每次 8091 重启/换血后、C1 API 与 `/hq` 页面上线变更后、每日/每周例检
- **输入**：本机 `127.0.0.1:8091`（portal API）与 `127.0.0.1:8734`（唯一公共入口）

## 一、用途

把「C1 激活后验收」收敛为**一条可复跑命令**，覆盖 5 组 15 项：

| 组 | 检查 | 期望 |
|---|---|---|
| 8091 | `GET /health` | 200 且 `ok=true` |
| api | `GET /api/hq/{roles,agenda,directives,reports,commercial,snapshot}` | 200 且 `{ok:true, data:…}` |
| snapshot | 必需键 / 计数 | 11 个必需键齐全；`roles=5`；`directives` 非空；`reports.legions` 非空且 status 合法；`commercial.summary` 非空 |
| public | `GET http://127.0.0.1:8734/hq`（**不跟随重定向**） | 302 → `/login?next=/hq` |
| public | `GET http://127.0.0.1:8734/api/hq/snapshot`（未登录） | 401（不暴露数据） |
| activation | 8091 pid / 进程启动时间 vs C1 代码 mtime | 启动时间 > C1 代码 mtime（已换血） |

## 二、命令

```powershell
# 人读摘要（默认 text）
python -X utf8 tools/hq_c1_verify.py

# 机器可读（stdout 纯 JSON；日志/人读信息也都在 JSON 里）
python -X utf8 tools/hq_c1_verify.py --json

# Markdown 报告（便于贴工单/证据包）
python -X utf8 tools/hq_c1_verify.py --markdown

# CI/脚本：PENDING 也视为通过（仅当明确接受未换血/PENDING 场景）
python -X utf8 tools/hq_c1_verify.py --json --allow-pending

# 跳过进程换血检查（只验 API/公共入口；该项记 PENDING）
python -X utf8 tools/hq_c1_verify.py --json --skip-process

# 端口/目标可覆盖（调试用；默认即本机 8091/8734）
python -X utf8 tools/hq_c1_verify.py --base-8091 http://127.0.0.1:8091 \
        --base-8734 http://127.0.0.1:8734 --timeout 8
```

## 三、退出码与状态语义

| 退出码 | 判定 | 含义 | 处置 |
|---|---|---|---|
| `0` | **PASS** | 全部 15 项 PASS（或 `--allow-pending` 下无 FAIL） | 可进入下一步验收 |
| `2` | **PENDING** | 无 FAIL，但有**无法判定/口径未达**项（如 8091 未换血、`/hq` 返回 200 登录页而非 302） | 记录并等待激活/拍板；**不得**当作 PASS |
| `1` | **FAIL** | 至少 1 项 FAIL（API 非 200、snapshot 缺键/计数错、公共入口 302/401 不符、网络异常、鉴权泄漏） | 停止验收、回滚/修复后复跑 |

- **PASS/PENDING/FAIL 均由实测行列汇总**，不做“默认通过”：网络异常=FAIL（服务不可用）；未换血=PENDING（C1 代码未加载，不应误判为功能 FAIL）。
- **不伪造**：不登录、不构造 session、不写 token；401/302 均按**原始状态码**（HTTP 客户端禁用自动跟随重定向）判定。

## 四、输出示例（2026-11-03 07:10 实测，8091 pid45552 已激活）

```text
== C1 激活后自检（只读）==
[PASS   ] 8091       /health                          code=200；body={"ok": true, "ts": "..."}
[PASS   ] api        /api/hq/roles                    code=200 ...
[PASS   ] api        /api/hq/directives               code=200 ...
[PASS   ] api        /api/hq/reports                  code=200 ...
[PASS   ] snapshot   roles=5                          roles=5
[PASS   ] snapshot   directives 非空                    directives>0
[PASS   ] snapshot   reports.legions 非空               legions>0 且 status 合法
[PASS   ] public     /hq 未登录跳转                        code=302；location=/login?next=/hq
[PASS   ] public     /api/hq/snapshot 未登录=401         code=401
[PASS   ] activation 8091 新代码生效（启动时间>C1代码mtime）       pid=45552 ...
汇总：15 项；PASS=15；PENDING=0；FAIL=0；判定=PASS
```

`--json` 顶层结构：`{schema:"hq/c1_verify/v1", generated_at, read_only:true, targets, rows[], summary{total,pass,pending,fail}, verdict}`；
`--markdown` 为同数据表格（含判定行）。

## 五、8091 重启后验收步骤（SOP）

1. **确认已重启**：`Get-NetTCPConnection -LocalPort 8091 -State Listen` 取 pid；`/health` = 200；
   记录 pid + 启动时间（`Get-CimInstance Win32_Process`）。
2. **跑自检**：`python -X utf8 tools/hq_c1_verify.py --json`；
   - **FAIL**：先看 `rows` 中 FAIL 组 → 对照 §六 排查；**不要**继续后续验收。
   - **PENDING**：区分「未换血（启动时间早于 C1 代码）」还是「`/hq` 302 口径变化」；
     未换血需重新受控重启（L2），`/hq` 口径变化需 A0/后端拍板，**不得静默改自检器忽略**。
   - **PASS**：进入 3。
3. **人工下一步**（自检器不代做）：登录 `/hq` 检查 8 tabs 数据/交互（U1）、Q2 登录验收（S2）、
   `python -X utf8 tools/l1_acceptance.py --json` 全量 L1 口径复跑（C1 是 L1 的子集）。
4. **留证**：`--markdown` 输出贴工单/证据包；记录命令、时点、pid、判定、`rows` 汇总。

## 六、常见 FAIL/PENDING 与处置

| 现象 | 判定 | 原因/处置 |
|---|---|---|
| `/health` 网络异常/非 200 | FAIL | 8091 未起/崩溃/端口变更 → 按 8091 稳定性计划拉起（L2，勿裸启） |
| `/api/hq/directives|reports` 404 | FAIL | 8091 未加载 C1 代码（`portal/api_hq.py` 旧版）→ 受控重启换血 |
| 8091 `/api/hq/*` 401 | FAIL | 内部端点被错误挂 auth_request → 检查 nginx/portal 路由，勿当成"安全"放过 |
| snapshot 缺 keys / roles≠5 / directives 空 / legions 空 / commercial.summary 空 | FAIL | 上游 `tools/hq_*.py` 数据源异常或代码未更新 → 对应模块自检/修复后重启 |
| `/hq` 返回 200 登录页 | PENDING | 与 C1 口径（302）不符：入口可达但验收口径未达 → 记录并拍板（不可静默 PASS） |
| `/hq` 404/5xx | FAIL | nginx 静态挂载/页面缺失 → 检查 `portal/nginx.conf` 与 `portal/hq.html` |
| 公共 `/api/hq/snapshot` 未登录 200 | FAIL | **敏感子集鉴权失效**（数据暴露）→ 立即回滚 nginx H4-B 相关变更并上报 |
| 公共 `/api/hq/snapshot` 403/302 | PENDING | 非 JSON 401 口径 → 记录（302=被登录跳转；403=权限拒绝） |
| activation PENDING（启动时间早于 C1 代码 mtime） | PENDING | 未换血：按 L2 受控重启；重启后复跑应转 PASS |
| activation PENDING（pid/PS 查询异常） | PENDING | 权限/进程枚举受限 → 用管理员 PowerShell 复跑；不伪造 PASS |

## 七、边界（红线）

- **只读**：仅 `GET`；不登录、不构造/写入 session、不写任何文件、不改 8091/nginx/portal/服务；
- **不重启、不启停服务**；重启属 L2，须 A0 批准/管理员执行；
- **不改口径**：若实测与 C1 验收口径不符（如 `/hq` 302→200），如实记 PENDING/FAIL，**不得**改自检器放宽或伪造登录态；
- 不整仓扫描、不越写域；本工具只覆盖 C1 激活面，不替代 L1 全量验收（`tools/l1_acceptance.py`）。

## 八、未做项 / 后续

1. **登录后数据完整性**（8 tabs 真实渲染/交互）需人工或受控会话，本工具不做自动登录（不碰凭据）。
2. **持续监控**：不做常驻/定时；如需接入 watchdog 另立单（只读探针可复用本工具 `verify()`）。
3. **历史留档**：本工具不落盘报告；证据由调用方重定向/贴工单（避免新增写面）。
4. **C2/C3 数据链闭环**验证仍依赖各自 runbook；本工具只验证 C1 API/snapshot/公共鉴权可达性。
