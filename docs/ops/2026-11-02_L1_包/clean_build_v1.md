# 智策前端 clean build v1（build / 静态产物 / VERSION.json / 与 /quant/* 反代关系）

- 任务：ADR-016 L1 待批包组件（task-2 / UF-17 静态 build clean + 版本 pin + UF-16 前置）
- 日期：2026-11-02（只读基线：04:12–04:25 +08:00；VERSION.json 快照 2026-10-31）
- 性质：**方案与现状核实**，只读。本包不执行构建、不写仓库、不动外部智策目录（不迁仓，版本 pin）。
- 边界：不改智策源码、不 npm install/ci、不 docker 操作、不 git 操作（外部目录 dirty 提交由 owner 执行）。

## 1. 一页结论

| 项 | 现状（证据） | L1 目标 | 结论 |
|---|---|---|---|
| 5175 前端 | `npm run dev`（vite dev server，`host:true` 监听所有网卡，`strictPort:5175`，`/api` 代理→8105）。进程 PID 38060（node，监听 `::`:5175，2026-11-01 17:02:20 起） | **5175 关停**；React 改为静态页面包，经 8734 `/quant/` 提供 | dev server 不是交付物 → 必须 clean build |
| 现有构建流水线 | `tools/hermes_acad1_build.py`（一条命令：tsc -b → vite build → 补丁 → VERSION.json → 验证 → 可选发布） | 复用该流水线，**新增 same-origin 模式**产出 `/quant/` 页面包 | 缺同源 API/WS 补丁规则（见 §5.2） |
| 上次构建快照 | `Hermes/extensions/legion-ide/zhice/VERSION.json`：version `20261031_141144_f4230a89`，built_at `2026-10-31T14:11:52+08:00`，source head `f4230a89…`，branch master，**dirty=true / dirty_count=56** | `dirty_count=0`（UF-17 验收口径） | 源码未收口 → 当前构建不可作为 L1 交付 |
| 发布链 | live `current` junction → `20261031_143832_bfd3d5ec`（14:38 promote）；该 release **没有 zhice/ 子目录** → 8787 `/extensions/zhice/*` 当前 404（带 trusted-header 只读实测 404；app.js 200） | 页面包走可复现发布链（build→VERSION→deploy→冒烟） | **已实证的发布漂移**，L1 必须一并收口 |
| `/quant/*` | 不存在：`GET 8734/quant/` = **404**；仅有 `location = /quant` → 302 `/quant-drill`（旧静态页） | `^~ /quant/` 静态页面包 + auth_request | 需 nginx L2 变更（见 L1_待批包 §6） |
| `/quant-api/` | 不存在；8105 现役（127.0.0.1，`GET /api/v1/health`=200 `{"status":"ok","app":"zhice-os-v2","version":"0.1.0"}`） | `^~ /quant-api/` 反代 8105 + auth_request | 同上 |
| 安全前置 | autoSession / 默认 admin 口令 / CORS 5175 源仍在源码（`code/frontend/src/services/autoSession.ts`、`main.py:122-133`） | autoSession 下线、默认口令轮换、CORS 收敛（UF-12，先修后用） | **阻塞 L1 切流**（不阻塞本方案评审） |

> 当前 VERSION.json 是**唯一版本证据**（`Hermes/*` 被 gitignore，产物不进库）：`version=20261031_141144_f4230a89`、`head=f4230a892e2da0650eea78bcb819fc680fe3eb8d`、`dirty_count=56`、产物 8 文件 / 2,292,369 bytes、`index-B8miyHf4.js`+`index-Dl42rMEp.css`、`VITE_BASE=/extensions/zhice/`、`VITE_ROUTER=hash`、`VITE_API_BASE=http://127.0.0.1:8105/api/v1`、typecheck=pass/vite_rc=0、node v24.20.0 / vite 8.2.1 / tsc 6.0.3、源码树 digest `dcd8d5f2…`（168 文件 / 1,594,555 bytes）。文件编码 UTF-8 有效。

## 2. 现状细节与发布链漂移（只读实证）

### 2.1 5175 = vite dev，不是构建产物

- 启动源：`zhice.ps1:95-98` `Start-Frontend` → `Start-Process 'npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory "$Code\frontend"`；`package.json` scripts.dev=`vite`。
- `code/frontend/vite.config.ts`：`port: 5175, strictPort: true, host: true`（注释明确“监听所有网卡：门户/局域网设备经 http://<主机IP>:5175 可达”），`server.proxy['/api'].target = http://127.0.0.1:8105`。
- 运行证据：`Get-NetTCPConnection -State Listen` 5175 由 PID 38060（node）监听，LocalAddress=`::`；CreationDate=2026-11-01 17:02:20；`GET http://127.0.0.1:5175/`=200。
- 安全事实：**5175 监听所有网卡（::）**，与 ADR-016 §5.4「5175/8102 仅 127.0.0.1」不一致 → 属关停前残余暴露面，L2 关停应优先执行（见 L1_待批包 §4）。
- 5175 无计划任务/自启项拉起：416 个计划任务中仅 `ZhiceAcad1Pool`/`ZhiceAPI8105Guard`/`ZhiceObsCalc` 命中 zhice，均不拉起 5175；`schtasks /Query /TN ZhiceWatchdog` = 不存在；HKCU/HKLM Run、启动文件夹均无 5175。→ 复活源只有 `zhice.ps1 start/watchdog`（CSV #27/#28/#30/#33）。

### 2.2 live 发布链漂移（重要）

- `C:\项目\_vault\legion_ide\release\legion-ide\current`（Junction）→ `20261031_143832_bfd3d5ec`；`current.json`：`version=20261031_143832_bfd3d5ec, promoted_at=2026-10-31T14:38:39+08:00`。
- `20261031_143832_bfd3d5ec` 目录仅 5 文件（a0-mainchat.js/app.css/app.js/manifest.json/README.md），**无 zhice/**。
- 8787 只读探针（`-H "X-Portal-User: laotian"`）：`/extensions/app.js`=200，`/extensions/zhice/index.html`=**404**，`/extensions/zhice/VERSION.json`=**404** → 当前 8734 `/extensions/`（auth 后）→ 8787 的 zhice 扩展不可用。
- 历史 release `20261031_141059_02fc06f8\zhice\VERSION.json`（23,432 bytes，14:11:55）与 `20261031_092952_7e6dbe8a\zhice\VERSION.json`（23,625 bytes，13:49:17）仍在；`_build_backup/live_zhice_*` 有两份发布前备份。
- 结论：14:11 的 `--publish-live`（add-only zhice/）之后，14:38 另一条 Hermes 发布 promote 了新 release 并整体替换 current，**把 zhice/ 覆盖丢失**。→ `/quant/` 页面包必须进“可复现构建 + 版本化发布 + 冒烟”的单一链路，禁止手工拷贝。修复 L1 内另开工单（owner：Hermes/legion-ide 发布线，A0 确认），不属本包执行。

### 2.3 构建流水线（现有能力，可直接复用）

`tools/hermes_acad1_build.py`（1,375 行，纯标准库）：

| 能力 | 入口/参数 | 事实 |
|---|---|---|
| 一条命令 | `python -X utf8 tools/hermes_acad1_build.py` | tsc -b → vite build → patch → VERSION.json → 8787 静态校验/探针 → 交付清单 |
| 参数化 | `--source/--dest/--deliverables/--base-path/--api-base/--markets/--router/--skip-typecheck/--no-verify/--verify-only/--publish-live/--json/--force/--retention` | `--api-base` 必须以 `/api/v1` 结尾（`tools/hermes_acad1_build.py:333-340`） |
| 源码 | 默认 `C:\项目\zhice-os-v2-windows-20260920\code\frontend` | 外部目录，**不迁仓**（技术框架统一方案 §十三-8） |
| 工具链 | `node_tool_paths`：node 在 PATH + `source/node_modules/vite/bin/vite.js`（缺失即 die，需先 `npm ci`） | 本机 node v24.20.0（≥20 满足） |
| 构建环境 | `build_env`：VITE_BASE/VITE_ROUTER/VITE_ENABLED_MARKETS/VITE_API_BASE；清除代理变量 | `NO_COLOR=1/FORCE_COLOR=0` |
| 构建 | `node node_modules/typescript/bin/tsc -b` + `node node_modules/vite/bin/vite.js build --outDir <out> --emptyOutDir --logLevel info` | 日志落 `<deliverables>/build.log`（长跑落盘红线） |
| 补丁 | `patch_build` 字节级替换：WS `${location.host}`、相对 `/api/v1`、根绝对 `/logo*.png`；`assert_patched` 断言 | 仅适配“绝对 API base + 子路径 base” |
| 版本 | `VERSION.json`（schema/version/built_at/source git/build/output/patch/verify/live_publish/live_probe） | `source_git_info` 用 `git status --porcelain` 计 dirty_count，取 HEAD/branch |
| 发布 | `--publish-live` → `HERMES_WEBUI_EXTENSION_DIR`（start_hermes_bg.py:168 = `_vault\legion_ide\release\legion-ide\current`）add-only + 备份 + 校验 | 本次漂移即发布链覆盖问题 |

## 3. VERSION.json dirty_count 现状（56）与 clean 口径

`dirty_count=56` = 12 个已跟踪修改（M）+ 44 个未跟踪（??），来自 2026-10-31 14:11 构建时 `git status --porcelain`（外部仓 `C:\项目\zhice-os-v2-windows-20260920`，head `f4230a89`，branch `master`）。

**M（12）**：
- `code/.env.example`
- `code/frontend/src/App.tsx`、`components/ProtectedRoute.tsx`、`main.tsx`、`pages/StrategyCatalogPage.tsx`、`pages/groups/ReplayGroup.tsx`、`pages/groups/StrategiesGroup.tsx`、`services/api.ts`、`services/myStrategiesApi.ts`、`vite.config.ts`
- `code/src/zhice_os_v2/api/main.py`、`code/src/zhice_os_v2/api/routers/strategy.py`

**??（44）分类**：
- 前端新增 8：`components/ProtectedRoute.tsx.bak_20261030`（备份文件，**clean 前应删除/入回收站**）、`pages/Acad1ResearchPage.tsx`、`pages/LusuDeskPage.tsx`、`pages/SelectionDeskPage.tsx`、`pages/SimFactorPage.tsx`、`pages/StrategyWorkspacePage.tsx`、`services/autoSession.ts`（**ADR §5 要求下线**）、`services/strategyWorkspaceApi.ts`
- 迁移 12：`code/migrations/versions/0034…0045`（strategy_workspace / strategy_daily_signal_key / acad1_pool_review / lusu_decision / stock_research / selection_desk / selection_tables / supervision / sim_factor / drop_unused / market_columns / report_market）
- 脚本 13：`adapt_platform_cn.py`、`adapt_platform_global.py`、`backfill_selection_market.py`、`calc_observations.py`、`ingest_acad1_pool.py`、`ingest_observations_backtest.py`、`ingest_strategy_workspace.py`、`run_calc_observations.ps1`、`run_calc_observations.py`、`run_daily_research_ops.py`、`seed_selection_desk.py`、`select_acad1_pool.py`、`sync_candidate_to_acad1.py`
- 路由 7：`api/routers/acad1_research.py`、`lusu_research.py`、`selection_desk.py`、`selection_public.py`、`sim_factor.py`、`stock_research.py`、`strategy_workspace.py`
- 测试 4：`code/tests/test_acad1_research_api.py`、`test_migration_0045.py`、`test_selection_desk_integration.py`、`test_selection_market_split.py`

clean 口径（UF-17 验收）：`dirty_count=0`。路径由外部目录 owner（智策OS线/A0）执行：先在外部仓提交/归档上述改动（`.bak` 入回收站，`autoSession.ts` 属删除项），再重跑构建。**本包不做 git 操作**；执行人只用只读核对：

```powershell
$src = "C:\项目\zhice-os-v2-windows-20260920"
git -C $src status --porcelain | Measure-Object -Line        # 预期 0（执行人窗口内只读检查）
git -C $src rev-parse HEAD ; git -C $src rev-parse --abbrev-ref HEAD
```

> 注意：构建脚本每次都会重新计算 dirty_count 并写入 VERSION.json；`dirty_count=0` 才满足“可复现 + 版本 pin”。若短期不能清 0，必须把 56 个变更打包为 `source-dirty.diff` + `source-untracked-files.json`（现有 `deliverables/2026-10-31_Hermes_ACAD1_扩展包_v1/` 已有此证据形态）并显式标注“非 clean”，不得宣称 UF-17 完成。

## 4. 目标：`/quant/` 页面包 clean build 可复现步骤

> 前置依赖（缺一不可，按序）：
> **P1** 流水线新增 same-origin 模式（标准道代码单，owner A0/ARCH1，`tools/hermes_acad1_build.py` + 其测试）——现有 `patch_build` 只支持“绝对 API base”；`--api-base /quant-api/api/v1` 时 `_url_parts` 会算出 `abs_prefix=/quant-api`（非绝对 origin），WS 规则不适用。需新增规则（见 §5.2）。
> **P2** 智策源码 `autoSession` 下线 + `ProtectedRoute` 改为门户登录跳转（UF-12 先修后用，智策OS线）。
> **P3** 源码 dirty_count=0（§3）。
> **P4** L2 批准的 nginx `/quant/` 路由（L1_待批包 §6）——构建可在其前完成并落盘验证。

### 4.1 复现步骤（目标命令；执行前需 P1–P3 完成）

```powershell
# 0) 变量
$repo   = "C:\项目\老田的AI助手"
$src    = "C:\项目\zhice-os-v2-windows-20260920\code\frontend"
$stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$stage  = "C:\项目\deliverables\2026-12-02_L1_quant页面包\$stamp\quant"
$deliv  = "C:\项目\deliverables\2026-12-02_L1_quant页面包\$stamp"
New-Item -ItemType Directory -Force $deliv | Out-Null        # 只建父目录；$stage 交给流水线创建/清空

# 1) 前置：工具链与源码干净态（只读核对）
node --version                                        # 预期 v24.20.0（>=20）
Test-Path "$src\node_modules\vite\bin\vite.js"        # 预期 True；False 则先 cd $src; npm ci
git -C "C:\项目\zhice-os-v2-windows-20260920" status --porcelain | Measure-Object -Line   # 预期 0

# 2) 构建（same-origin 模式；P1 完成后该命令才存在）
python -X utf8 "$repo\tools\hermes_acad1_build.py" `
  --source $src `
  --base-path /quant/ `
  --api-base /quant-api/api/v1 `
  --router hash `
  --markets CN,US,HK,CRYPTO `
  --dest $stage `
  --deliverables $deliv `
  --no-verify --json
# 预期：typecheck=pass、vite_rc=0；产出 $stage\index.html + assets\*.js|*.css + $stage\VERSION.json；
#       日志 $deliv\build.log；JSON 回执落 $deliv\result.json（若 --json 输出重定向）

# 3) 断言（clean build 证据包）
python -X utf8 -c @"
import json,pathlib,hashlib,sys
stage=pathlib.Path(r'$stage')
m=json.loads((stage/'VERSION.json').read_text(encoding='utf-8'))
assert m['source']['dirty_count']==0, m['source']['dirty_count']
assert m['build']['typecheck']=='pass' and m['build']['vite_rc']==0
assert m['build']['env']['VITE_BASE']=='/quant/'
assert m['build']['env']['VITE_API_BASE']=='/quant-api/api/v1'
js=list((stage/'assets').glob('*.js')); css=list((stage/'assets').glob('*.css'))
assert js and css
raw=js[0].read_text(encoding='utf-8',errors='replace')
for bad in ('http://127.0.0.1:8105','autoSession','zhice_auto_password','5175'):
    assert bad not in raw, bad
# 同源化正确性：/quant-api/api/v1/ 必须存在；剥掉该前缀后不得残留裸相对 /api/v1/
assert '/quant-api/api/v1/' in raw
assert '/api/v1/' not in raw.replace('/quant-api/api/v1/','')
assert 'localStorage.getItem(\'zhice_auto_password\')' not in raw
print('CLEAN_BUILD_ASSERTS_OK', m['version'], [p.name for p in js+css])
"@
# 预期：CLEAN_BUILD_ASSERTS_OK

# 4) 发布到门户 webroot（标准道部署单；不要手工 cp 到容器外路径）
$portal = "$repo\portal\quant"
$prev   = "$repo\portal\_quant_prev_$stamp"
if (Test-Path $portal) { Copy-Item $portal $prev -Recurse }     # 保留上一版（回滚用）
Copy-Item "$stage\*" $portal -Recurse -Force
# 容器挂载 portal/ 为 webroot（start_portal_nginx.ps1:23-26），无需重启容器；新增 location 才需 reload（L2）
```

### 4.2 原始 vite 命令（仅调试用，不作交付）

```powershell
cd "C:\项目\zhice-os-v2-windows-20260920\code\frontend"
$env:VITE_BASE='/quant/'; $env:VITE_ROUTER='hash'; $env:VITE_ENABLED_MARKETS='CN,US,HK,CRYPTO'
$env:VITE_API_BASE='/quant-api/api/v1'; $env:NO_COLOR='1'; $env:FORCE_COLOR='0'
node node_modules/typescript/bin/tsc -b --pretty false
node node_modules/vite/bin/vite.js build --outDir C:\项目\deliverables\_tmp_quant_build --emptyOutDir --logLevel info
# 注意：此路径不会生成 VERSION.json / 无补丁 / 无审计，禁止作为 L1 产物
```

## 5. 产物落点与 `/quant/*` 反代关系

### 5.1 落点

| 层 | 路径 | 说明 |
|---|---|---|
| 构建暂存（不可变证据） | `C:\项目\deliverables\2026-12-02_L1_quant页面包\<version>\` | index.html/assets/VERSION.json/build.log/result.json/sha256 清单；**唯一真源** |
| 部署运行位置 | `C:\项目\老田的AI助手\portal\quant\`（新增，容器 webroot 挂载点内） | `:/usr/share/nginx/html:ro` 直接可见；`portal/quant/VERSION.json` 可经 `/quant/VERSION.json` 访问（`^~` 前缀会绕过 nginx 的 `.json` 404 正则，见 §6） |
| 上一版回滚位置 | `portal\_quant_prev_<stamp>\`（部署后保留 ≥1 个版本，验收后按保留策略回收） | 回滚=目录换回 + `nginx -s reload`（静态文件本身热更新） |
| 发布历史 | `deliverables/2026-12-02_L1_quant页面包/` 各 `<version>` 目录 | 版本 pin：`index.html` 引用的 assets 带 hash，可逐字节对账 |

### 5.2 目标 `/quant/` 与 `/quant-api/`（nginx 侧，L2 五要素见 L1_待批包 §6）

```nginx
# 新增（插在 location = /quant-drill 之后，量化域路由组）
location ^~ /quant/ {
    auth_request /_auth_guard;
    error_page 401 = @login_required;
    try_files $uri $uri/ /quant/index.html;
    add_header Cache-Control "no-cache";                       # index.html 立即生效
}
location ^~ /quant/assets/ {
    try_files $uri =404;                                       # 带 hash 资产，长缓存可后续加
    add_header Cache-Control "public, max-age=31536000, immutable";
}
location ^~ /quant-api/ {
    auth_request /_auth_guard;
    error_page 401 = @api_unauthorized;
    set $h host.docker.internal;
    rewrite ^/quant-api/(.*)$ /$1 break;
    proxy_pass http://$h:8105;                                 # 8105 仅 127.0.0.1；nginx 容器经 host.docker.internal
    proxy_connect_timeout 3s;
    proxy_read_timeout 120s;                                   # 冷加载 pandas/回测接口放宽（对齐 /bt 判例）
}
# 反代映射：/quant-api/api/v1/health → 8105 /api/v1/health；WS → /quant-api/api/v1/market/ws/market
```

关键设计点（都必须写进 L2 变更单）：

1. **必须用 `^~` 前缀**：现有 `portal/nginx.conf:69` 的正则 `\.(py|js|json|...)$ → 404` 会拦截 `/quant/assets/*.js`、`/quant/VERSION.json`；nginx 正则 location 优先于普通前缀，`^~` 可抑制正则检查（与 `/extensions/`、`/static/` 同判例）。
2. **构建参数协同**：`VITE_BASE=/quant/`（`vite.config.ts` `base` 直接消费）、`VITE_ROUTER=hash`（静态托管无 history fallback；`main.tsx` hash 分支不设 basename）、`VITE_API_BASE=/quant-api/api/v1`（同源，浏览器不跨域、**不再需要 8105 CORS**）。
3. **构建后补丁规则必须改为同源**（P1 新增模式）：
   - 相对 `/api/v1` → `/quant-api/api/v1`（4 处相对 fetch 等）；
   - WS `` ://${location.host}/api/v1 `` → `` ://${location.host}/quant-api/api/v1 ``（`useMarketQuoteSocket.ts:26`）；
   - 根绝对 `/logo*.png` → `/quant/logo*.png`；
   - 删除/禁用 `autoSession`（不得再 `fetch('/quant-api/api/v1/auth/register')` 自动注册；改为未登录跳 `/login`）；
   - 断言：产物内无 `http://127.0.0.1:8105`、无裸 `/api/v1/`、无 `5175`、无 `localStorage` 存 token/密码。
4. **认证**：页面与 API 都过 `auth_request`（页面 302 `/login`、API 401 JSON）；8105 自身认证见 L1_待批包 §7 单认证方案（门户会话 → `/api/auth/quant-token` 派生短 TTL token 或 BFF 注入；autoSession 必须已下线）。
5. **旧入口不动**：`location = /quant` 仍 302 `/quant-drill`（`portal/nginx.conf:501`），本批不改，避免 11-04 彩排/11-08 冻结期入口行为变化；是否切换交 A0（未决项 U-1）。

### 5.3 与 `/extensions/zhice/` 的关系（两个构建目标，勿混）

| 目标 | base | api base | 用途 | 归属 |
|---|---|---|---|---|
| `/extensions/zhice/`（现有） | `/extensions/zhice/` | `http://127.0.0.1:8105/api/v1`（绝对，Hermes CSP 放行段） | Hermes WebUI 内嵌智策研究台 | Hermes/legion-ide 发布线 |
| `/quant/`（L1 新增） | `/quant/` | `/quant-api/api/v1`（同源相对） | 零界宿主唯一量化页面包 | A0/ARCH1 |

两者共用同一源码树与同一条构建流水线（不同参数），但**发布链、版本目录、VERSION.json 各自独立**；`/extensions/zhice/` 的当前 404 漂移（§2.2）不能靠 `/quant/` 顺手修复——页面包必须独立可验收。

## 6. 与 `/quant/*` 反代的验收（L2 后执行）

```powershell
$B="http://127.0.0.1:8734"
docker exec ai-portal nginx -t                          # 预期 syntax is ok / test is successful
curl.exe -s -o NUL -w "GET /quant/ -> %{http_code} %{redirect_url}`n" $B/quant/
# 未登录预期 302 /login?next=/quant/；已登录（§认证三态）预期 200
$jar=Join-Path $env:TEMP "q.cookies"; # …登录后…
curl.exe -s -o NUL -w "GET /quant/assets/index-<hash>.js -> %{http_code}`n" -b $jar "$B/quant/assets/index-<hash>.js"   # 200
curl.exe -s -o NUL -w "GET /quant/VERSION.json -> %{http_code}`n" -b $jar "$B/quant/VERSION.json"                          # 200
curl.exe -s -o NUL -w "GET /quant-api/api/v1/health -> %{http_code}`n" -b $jar "$B/quant-api/api/v1/health"                # 200
curl.exe -s -o NUL -w "GET /quant-api/api/v1/health (anon) -> %{http_code}`n" $B/quant-api/api/v1/health                    # 401
# 反代直连对照（8105 仅回环，不经 nginx 不出容器网络）
curl.exe -s http://127.0.0.1:8105/api/v1/health
```

## 7. 风险与未决（交 L2/A0）

| # | 风险/未决 | 处置 |
|---|---|---|
| B-1 | 源码 `dirty_count=56` 不清零 | UF-17 无法验收；先由智策OS线提交，构建只认 dirty=0 |
| B-2 | `autoSession` 未下线即切 `/quant/` → 前端会自动注册账号并登录，违背“单认证” | UF-12 先修后用，作为 L1 切流硬门 |
| B-3 | 现有发布链会整体替换 `current`（已实证丢 zhice/） | `/quant/` 走独立 deploy（portal/quant/），不进 Hermes current；Hermes 侧另修 |
| B-4 | 8105 CORS 仍含 5175/5174/5173 源 | 同源方案不依赖 CORS；收敛属 UF-12（安全前置） |
| B-5 | 5175 当前监听 `::`（全网卡） | 关停优先执行；关停前不放宽任何网络暴露（L1_待批包 §4） |
| B-6 | `/quant/` 与 `/quant`（精确 302）语义分裂 | A0 拍板是否切 `/quant` → `/quant/`；本批默认不切（U-1） |
| B-7 | node_modules 状态未核（`npm ci` 是否需重跑） | 构建前置 `Test-Path vite.js`；缺失由执行人 `npm ci`（不改 lockfile） |
