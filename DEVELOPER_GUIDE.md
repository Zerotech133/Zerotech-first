# 零界 · 开发文档 v1（2026-11-07）

> **这份文档解决什么问题**：让一个新加入的开发者（或 AI Agent）在**不做考古**的前提下，知道这个仓库是什么、代码在哪、怎么跑起来、怎么改才不被门禁打回、以及哪些坑已经有人踩过。
>
> **面向读者**：接手开发的工程师、进入本仓的 AI Agent、需要评估改动风险的 owner。
>
> **不替代谁**：`README.md` 是**使用者**上手（每天看什么、怎么用）；`ARCHITECTURE.md` 是**架构简图**（一页看懂组件与端口）；`AGENTS.md` 是**开发宪法**（强制规范，违反即拒合并）；`CONTRIBUTING.md` 是**流程卡**。本文是四者的合订与展开，冲突时以 `AGENTS.md` 与运行态实测为准。

---

## 0. 三分钟摘要

| 项 | 事实 |
|---|---|
| 定位 | **开源情报（OSINT）抓取与数据能力平台**，金融为垂类；六环（采集/整理/分析/分发/反馈/治理）× 金融垂类（A0-DEC-20261107，定义源 `PRODUCT_BASELINE v1.2`） |
| 形态 | **单机（Windows）个人 AI 平台**，不是 SaaS、不是多租户 |
| 人类入口 | **只有一个：`http://127.0.0.1:8734`**（nginx 同源反代；8080 仅容器/局域网链路，文档禁写双口） |
| API 进程 | `portal/portal_api.py` 监听 **8091**，是全部 `/api/*` 的调度器 |
| 代码规模 | 非依赖 `.py` 文件约 **8,000+**；`tools/` 1,540、`quant_backtest/` 1,284、`agent_platform/` 673、`portal/` 571 |
| 测试规模 | 全仓可收集 **11,187 项**（2026-11-07 21:00 实测，0 error）；2026-11-08 01:10 首轮真执行 **11,155 passed / 53 failed**（见 §11.2） |
| 门禁 | `tools/test_gate.ps1 -Suite fast` = **7 个套件**，新增失败即拒合并 |
| 数据根 | 唯一 live 根 `quant_backtest/data`（`quant.db` 183 MB）；新闻 `news_collector/news.db` 155 MB |
| 当前主线 | **JEV 多维判读情报中台**（14 张能力卡，见 §6） |

**五条铁律**（细节见 §8）：

1. 无任务单不写码；一单一事；在独立 worktree 干活，不在 master 直改。
2. 提交必须**显式 pathspec**，禁止 `git add -A` / `commit -a`。**而且 `commit` 本身也要带路径**（`git commit -F msg -- <paths>`）——只给 `add` 加路径是不够的，`commit` 默认提交**整个 index**，会把别人已暂存的改动一起带走（本仓真实发生过，见 §8.2）。
3. **测试解释器必须是 Python 3.11**（见 §2.2，这是本仓最高频的假故障源）。
4. 删除走回收站并留 manifest，不 `rm`、不 `git clean -fdx`。
5. 任何改系统级配置/重启服务/碰生产机的动作，先走 **L2 五要素**并取得批准。

---

## 1. 项目是什么

### 1.1 定位（唯一源）

> 零界 = **开源情报（OSINT）抓取与数据能力平台**，金融为垂类。
>
> 能力口径：**OSINT 六环（采集 / 整理 / 分析 / 分发 / 反馈 / 治理）× 金融垂类**为能力场景。
>
> 服务对象不变：让有判断力、有想法、但不愿亲自承担完整软件工程的人，快速组建一支可长期协作的多 Agent 团队，把想法变成有依据、受约束、可试用、可迭代、可验收的成果。

产品定义唯一源：`docs/plans/2026-10-24_PRODUCT_BASELINE_v1.md` **v1.2**（A0-DEC-20261107 覆盖 2026-11-04 A0 P1/P5）；战略与排期唯一源：`docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md`。**这两个文件改口径要走裁定，不在本文档权限内。**

### 1.2 能力场景（OSINT 六环 × 金融垂类，不定义产品）

下表四行是 OSINT 六环在金融垂类上的**落点**，不是产品定义本身：六环是能力底座，垂类是当前唯一在跑的行业。

| 场景 | 载体 | 当前状态 |
|---|---|---|
| A 股量化情报 | `quant_backtest/` + `k_stock/` + `portal/api_backtest_*` | 主力产线，13 条策略在册（实盘 6 / 影子 3 / 工具 4） |
| 情报判读（JEV） | `news_collector/` + `portal/api_jev_*` | **当前主线**，14 张能力卡对外 |
| 数字军团 | `agent_platform/`（8 投研席 + 研发席） | 17 席日报已日跑，Case 端到端未闭环 |
| 内容（采集 / 分发环） | `gzh/`、`osint_video/`、`video/` | 可用，非主线；`res-downloader-py` 归属采集层（见 `osint_video/README.md`） |

### 1.3 明确不是的东西

- **不是 Tokens 售卖**：对外定位是**能力平台**，量化/情报是能力，不是商品清单。
- **不是无状态服务**：大量逻辑依赖本机 SQLite、本地 Ollama、计划任务、飞书 WS，脱离本机跑不起来属正常。
- **不是纯 Python 仓**：`Hermes/`、`CowAgent/`、`quantdinger/`、`zerobound-kg/` 是**内嵌的独立子工程**，有各自 README 与依赖，服从各自纪律。

---

## 2. 环境与快速上手

### 2.1 硬性环境

| 项 | 要求 | 说明 |
|---|---|---|
| OS | Windows 10/11 + Git Bash | 有大量 `.ps1` 与中文路径 |
| Python | **3.11.x**（实测 3.11.9） | `pyproject.toml` 要求 `>=3.11`；门禁只在 3.11 上验证 |
| 关键包 | `pandas`、`pytest`、`pytest-timeout`、`ruff` | dev 组见 `pyproject.toml [dependency-groups]` |
| Node | 可选 | 仅用于 `node --check` 校验页面内联 JS |
| 编码 | UTF-8 | `conftest.py` 已给子进程设 `PYTHONIOENCODING=utf-8`，不要覆盖 |

### 2.2 解释器铁律（最高频假故障）

本机存在多个 Python。**默认 `python` 是 3.13 且没有 pandas**，直接用它跑 pytest 会得到大量与代码无关的红：

```powershell
# 会误报（3.13 / 无 pandas）
python -m pytest portal/tests -q

# 正确（本仓所有门禁数字均出自这条）
& "C:\Program Files\Python311\python.exe" -m pytest portal/tests -q -p no:cacheprovider
```

**判据**：如果一次失败里出现 `ModuleNotFoundError: pandas` 或大量 import 错，先怀疑解释器，别怀疑代码。§7.1 有完整口径。

### 2.3 十分钟跑通

```powershell
cd C:\项目\老田的AI助手          # ASCII 别名 C:\aios

# 1) 确认服务在（门户 8734 / API 8091）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite portal

# 2) 跑一个最小测试，确认解释器与依赖没问题
& "C:\Program Files\Python311\python.exe" -m pytest portal/test_jev_access.py -q -p no:cacheprovider

# 3) 看一眼当前门禁全貌（7 套件，约 1.5–2 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast

# 4) 领单开工（自动建 worktree + 分支）
$env:DEVTOOL_AGENT="你的席位"; $env:DEVTOOL_IDE="CodeBuddy"; $env:DEVTOOL_MODEL="你的模型"
python tools/devtask.py new <TID> "一句话目标"
```

---

## 3. 仓库地图

### 3.1 目录职责

| 目录 | 职责 | 活跃度 | 入口文件 |
|---|---|---|---|
| `portal/` | 门户：页面、API、卡片、移动端、nginx 配置 | **活跃（主战场）** | `portal_api.py`、`nginx.conf` |
| `portal/api_jev_*.py` | JEV 14 张能力卡的实现（12 个模块） | **活跃** | `api_jev_access.py`（目录+留痕）、`api_jev_interpret.py`（AI 解读） |
| `agent_platform/` | 数字军团：席位、任务账本、审批、记忆、引擎、技能 | **活跃** | `engine/`、`legion_command/`、`skills/` |
| `quant_backtest/` | 行情、缠论、九维、回测、数据仓 | **活跃** | `quant_backtest/data`（数据根） |
| `k_stock/` | 股票域业务（风险、报告、paper 交易） | 活跃 | `k_stock/paper/` |
| `news_collector/` | 新闻/公告采集 + `news.db` | **活跃（JEV 上游）** | `news.db` |
| `tools/` | 工具仓：门禁、devtask、导航/端口守卫、各类 K-* 脚本 | **活跃且庞杂（1,540 文件）** | `test_gate.ps1`、`devtask.py`、`check_ports.py` |
| `common/`、`pylibs/` | 公共库（**`pylibs/` 是 vendored，改前先看 LICENSE**） | 稳定 | — |
| `config/` | 运行配置：云端 LLM、席位路由、模型分配、凭据登记 | 活跃 | `cloud_llm.json` |
| `docs/` | 计划 / 审计 / 规范 / 运行手册 | 活跃 | `docs/plans/`、`docs/audit/`、`docs/dev/` |
| `deliverables/` | 交付物、验收回执、审计报告 | 活跃 | `2026-11-07_代码审查与遗留清单_v1.md`（遗留清单唯一权威） |
| `ops/` | 健康检查、备份、重启、部署脚本 | 活跃 | `health_check.py` |
| `Hermes/`、`CowAgent/`、`quantdinger/`、`zerobound-kg/` | **内嵌独立子工程**（各有 README/依赖/纪律） | 分线维护 | 各目录 README |
| `_vault/` | 凭据（明文 Phase 1 / age 密文） | **只读，禁提交** | 见 `SECURITY.md` |
| `_archive/`、`backups/`、`archive/` | 冷归档与备份 | 只搬不删 | manifest |
| `work/`、`tmp/`、`outputs/`、`logs/`、`locks/`、`state/` | 运行态 / 临时产物 | **可重建，新代码不得依赖** | — |
| `_tmp_pytestroot*`、`portal_backups/`、`prototypes/` | 历史探针与原型，属清理候选 | 半退役 | 见遗留清单 §15.5 |

### 3.2 规模分布（非依赖 `.py` 文件数）

```text
tools/           1540   ← 门禁与一次性脚本混杂，找东西先 grep 别翻目录
quant_backtest/  1284
agent_platform/   673
portal/           571   ← 其中 portal/*.py 438 个，api_* 前缀 99 个
k_stock/          107
news_collector/    72
analysis/          53
```

> 判断一段逻辑在哪：**先看 URL 前缀**（`/api/jev/*` → `portal/api_jev_*.py`；`/api/backtest/*` → `portal/api_backtest_*.py`），再看 `portal_api.py` 里对应的 `wrap_dispatch` 分派。页面类走 `api_*_pages_*.py`，它们直接吐 HTML 字符串。

---

## 4. 运行时架构

### 4.1 组件与端口

| 组件 | 路径 | 端口 | 说明 |
|---|---|---|---|
| 门户（人类唯一入口） | `portal/` | **8734** | nginx 同源反代，页面/卡片/移动端 |
| 门户 API | `portal/portal_api.py` | **8091** | 全部 `/api/*`，`ThreadingHTTPServer` 系 |
| 军团命令面 | `agent_platform/legion_command/serve.py` | 8796 | org/意图卡 contract、MCP 客户端 |
| OSLINK 网关 | `agent_platform/gateway/serve.py` | 8795 | 隔离通道 |
| Hermes | `Hermes/` | 8787 | 旧代运行中 |
| 飞书 bot | `portal/feishu_bot_ws.py` 等 | 出站 WS | 看护器 `run_bot_watchdog_loop.ps1` |

**端口唯一权威**是 `tools/check_ports.py` 的 `REGISTRY` + `tools/ports_allocated.json`。起新服务前：查册 → `python tools/devtask.py port <服务名>` 申请 → 同一提交内写回 REGISTRY。自查 `python tools/check_ports.py`（`--strict` 供门禁用，只拦「未登记监听 + 端口冲突」，在册服务离线**不算失败**）。

### 4.2 一次请求的完整链路

```text
浏览器 http://127.0.0.1:8734/xxx
        │  （相对路径，不带端口，走同源反代）
        ▼
portal/nginx.conf  ── location 匹配（顺序敏感：精确 > ^~ 前缀 > 正则 > 普通前缀）
        │            ├─ /api/jev/interpret  → proxy_read_timeout 200s（AI 慢）
        │            └─ 其余 /api/           → 默认 40s
        ▼
portal/portal_api.py (8091)  do_GET / do_POST
        │  命中路径 → api_jev_access.wrap_dispatch(...)   ← JEV 系必过这层
        ▼
portal/api_jev_*.py 的业务函数
        │
        ├─ 读 news.db / quant.db（sqlite，只读为主）
        ├─ 调 AI：本地 Ollama → 云端 → 规则（§6.4）
        └─ 写 jev_access.db（异步留痕，失败不影响响应）
        ▼
JSON / HTML 回给 nginx → 浏览器
```

### 4.3 调度器 `portal_api.py`（3,895 行）

- 结构是**巨型 `do_GET`/`do_POST` + 前缀分派**，不是框架路由。找接口靠 grep 路径字面量，不靠 IDE 跳转。
- JEV 系接口统一走 `api_jev_access.wrap_dispatch(handler, path, qs_raw, method, fn, ...)`：
  - 生成 `req_id`（纳秒时间戳 + 4 hex），**回写响应头 `X-JEV-Req-Id`**；
  - 通过实例级包装 `send_response` 捕获状态码（每请求一实例，零全局污染）；
  - 响应完成后**异步**落一条 trace（磁盘满只打 stderr，不改状态码）。
- 调用方报障时，**让对方给 `X-JEV-Req-Id`**，用 `/api/jev/trace?req_id=...` 秒级定位到「谁的请求、打到哪、卡在哪层」。

### 4.4 前端三层与页面准入三件套

新页面缺一不收（`AGENTS.md §2`）：

1. **`portal/static/tokens.css`** —— 唯一视觉来源，禁自绘配色；
2. **`portal/static/nav.js`** —— 统一顶栏。动态页走 `pill_nav` 自动获得；静态页用 `python tools/unify_nav.py` 注入，校验 `--check`；
3. **`shortscreen.js`** —— 短屏化，`h2 ≥ 6` 必须过。

导航只许两层（顶栏 + 面包屑），禁平铺胶囊长条，>7 个并列入口必须收下拉。当前口径：**108 个普通页带统一顶栏 + 6 个定型页有自身导航**，`unify_nav --check` 要求零漂移；路由清单门 `portal_route_inventory --check` 要求**322 条零 diff**。

---

## 5. 数据面

### 5.1 库清单

| 库 | 位置 | 规模 | 用途 | 写者 |
|---|---|---|---|---|
| `news.db` | `news_collector/news.db` | 155 MB | 新闻/公告 + `jev` 判定字段 + `quotes` | 采集器、判定任务 |
| `quant.db` | `quant_backtest/data/quant.db` | 183 MB | 行情、日线、分钟线、因子 | 数据仓任务 |
| `evidence_chain.db` | `quant_backtest/data/evidence_chain.db` | 2.3 MB | 证据链 | 证据任务 |
| `jev_access.db` | `portal/data/jev_access.db` | 小 | **调用留痕**（trace 表，WAL，首次调用自建） | `api_jev_access` |
| `jev_audit.db` | `agent_platform/data/jev_audit/` | 小 | `dec_audit`：判定 T+1 后验（13.5 万判定 / 2.8 万+ 已验证） | 后验回填任务 |
| `dev_events.jsonl` | `portal/data/dev_events.jsonl` | 小 | 开发事件流（15 类），工单页 `/devops` 读它 | post-commit 钩子 |
| `test_baseline/latest.json` | `agent_platform/data/test_baseline/` | 小 | 最近一次全量回归结果 | `tools/test_baseline.py` |

### 5.2 数据根与互斥（历史事故条款）

- **唯一 live 数据根**：`quant_backtest/data`（`KG_DATA_ROOT`）。结构性操作（还原/迁移/清理/换根/重建 catalog）**前必须读 `C:\Temp\kg_sync_task.log` 尾部**确认无其他会话在操作，**完成后必须补记**。
  - 原因：2026-10-17 曾因两会话同时「清理 vs 还原」，17.7 GB 数据反复拉锯一上午。
- 运行态（`C:\项目\dsh_runtime`、`C:\项目\analysis_work`）可重建，**不写回 `work`**。
- 冷归档 `C:\项目\_archive`：**只搬不删**，manifest 可逆。

### 5.3 凭据

- 位置：`C:\项目\_vault\secrets`（Phase 1 明文）/ `secrets.enc`（age 密文）。
- 规则：**不入 git、不进聊天、不写日志**。`pre-commit` 有凭据扫描（warn-only，基线感知），**禁止用 `--no-verify` 绕过**；命中要么移出真值，要么评审后加豁免。

---

## 6. JEV 能力层（当前主线）

### 6.1 14 张能力卡，3 个组

| 组 | 含义 | 卡（`id`） |
|---|---|---|
| `evidence`（6） | 证据能力：判断准不准、来源可信不可信 | `jev_evidence`、`jev_evidence_item`、`jev_posterior`、`jev_wordcloud`、`jev_target_drill`、`jev_trace` |
| `narrative`（3） | 叙事能力：把一段行情/一串消息讲明白 | `jev_story`、`jev_intraday`、`jev_interpret` |
| `base`（5） | 平台底座 | `jev_judge`、`jev_source_trust`、`jev_platform`、`jev_review`、`jev_mcp` |

目录本身即接口：`GET /api/jev/capabilities` 返回每张卡的 `id / title / endpoint / purpose / contract / consumer_hint / group`。**新增能力必须同时登记 CAPABILITIES**，否则外部发现不了。

### 6.2 三条主线（改代码前先认清自己在哪条）

1. **判定证据链**：`news.db.jev`（方向×置信×事件类型×影响标的）→ `dec_audit` T+1 后验（ret/bench/excess/hit）→ 命中率可审计。**「判断→影响谁→后市验证」是这条链的契约，断一环就等于没证据。**
2. **叙事解读链**：`jev_interpret` / `jev_story` / `jev_intraday`，把数据变成人话结论：形态、量价关系、消息归因，并给 `verdict`。日内 5 分钟回溯只对**当天**有效，历史要贯穿（这是老田明确过的口径，别再按「历史也做 5 分钟」理解）。
3. **平台底座**：可信度、人工复核、健康总览、留痕溯源。

### 6.3 失败分级（溯源第一层）

| `error_class` | 含义 | 该找谁 |
|---|---|---|
| `client` | 参数越界 / 未登录 / 无权限 | 调用方 |
| `upstream` | `news.db` 不可读 / Ollama / 总线 | 中台运维 |
| `internal` | 自身未捕获异常 / schema 不符 | JEV 开发（立 BUG 卡） |
| `unknown` | 进程中断等无响应 | 对 `portal_api` 重启日志时间 |

### 6.4 AI 三级降级（算力不够时的既定路径）

```text
本地 Ollama（qwen 系，超时 150s）
   │ 失败 / 超时 / 模型未加载
   ▼
云端（OpenAI 兼容 / Anthropic，配置 config/cloud_llm.json 或环境变量 JEV_CLOUD_LLM_*）
   │ 未启用 / 再次失败
   ▼
规则引擎（确定性兜底，永远有输出）
```

返回体固定三字段：`engine`（实际用了哪一层）、`engine_label`（给 UI 的中文标签）、`degrade_note`（降级原因，未降级为 `null`）。

**两条硬约束**：
- `config/cloud_llm.json` 必须在 **webroot 之外**（历史事故：早期误放页面目录，等于把云端 key 挂在静态站上）。
- 降级必须**说出来**。测试里有断言 `"已切云端" in degrade_note`——降级却静默，等于让用户以为结论来自本地模型。

### 6.5 权限模型

- **读公开、写须会话**：`/api/jev/review/*` 的写操作要求 `zb_session`（门户登录 Cookie）。
- nginx 里 `auth_request /_auth_guard` 是鉴权的**唯一开关**。`satisfy any` 块必须 fail-closed：有 `deny all`、`deny` 在所有 `allow` 之后、且有 `auth_request`。缺任一条 = 把「本机免登录」写成「全网免登录」。
- 这条已有自动化守卫（`portal/tests/test_round7_closeout.py`），改 nginx 前先读 §7.5。

---

## 7. 测试与门禁

### 7.1 pytest 配置（`pytest.ini`）

```ini
testpaths = agent_platform/tests portal/tests tools/tests quant_backtest/tests tests
norecursedirs = .git .worktrees .venvs .pytest_cache __pycache__ node_modules
addopts = --import-mode=importlib     ; 解决多目录同名测试文件冲突
timeout = 120                          ; 兜「挂死」，命令行可覆盖
```

**没有 `testpaths` 的裸跑会捞到更多目录**，口径会变；改 `testpaths` 属于门禁口径变更，要单独说明。

**`portal/conftest.py` 另外把 `portal/` 追加进 `sys.path`**（用 `append` 不用 `insert(0)`：只在「原本找不到」时命中，不会顶掉仓库根的同名包）。原因是上面那行 `--import-mode=importlib`：pytest 只把 rootdir 放进 `sys.path`，`portal/` 自身不在其中，于是根级测试里 `import api_common` / `from watch_tower_linkage import ...` 这类裸 import 必然 `ModuleNotFoundError`。这就是「收集即错」的机械成因，不是测试写坏了（P1-6，2026-11-08）。

### 7.2 `test_gate.ps1` 套件

| 套件 | 内容 | 何时用 |
|---|---|---|
| `fast` | **portal + kstock + ide + agent_key + quant_key + tools_key + datawarehouse**（7 套） | 合并门前置、日常回归 |
| `portal` / `kstock` / `ide` / `agent_key` / `quant_key` / `tools_key` / `datawarehouse` | 单套件 | 改动命中哪个跑哪个 |
| `agent` / `quant` / `tools` / `all` | 大会话/夜间全量 | 夜间窗口，不用于单次改动 |

判据：**本次 FAILED/ERROR 减去 `tools/test_baseline.json` 的 known_failures**，只有「新增失败」才拒合并——所以基线是**必须维护**的，不是摆设。

### 7.3 三把尺子不相等（最容易误读的地方）

| 数字 | 范围 | 含义 |
|---|---|---|
| **3,881 passed / 0 failed** | `agent_platform/tests`（`tools/test_baseline.py`） | 军团全量，约 393 s |
| **1,330 passed / 2 failed** | portal 门禁套件（`portal/tests` + 4 个根级文件） | 门户口径，约 93 s（并发负载下实测到 161 s）。那 2 红与本批改动无关：`portal/index.html` 工作区字节是纯 LF（865 行），换行守卫读原始字节就红；转 CRLF 后两条立刻绿（已实测并还原），而 `core.autocrlf=true` 下 `git status` 一直认为它没改 —— 见 §9 判例 #19 |
| **11,187 collected / 0 error** | 仓库根 `--collect-only` | **只是能收集，不代表执行过** |

**三者不可相加，也不构成「全仓 PASS」。** 全仓真执行已做过首轮（2026-11-08 01:10，**11,155 passed / 53 failed**，见 §11.2），结论是**不 PASS**；JSON 基线尚未落。任何汇报里写「全仓 0 failed」都必须先跑完 `pytest -q` 并给出该跑的日志路径。

### 7.4 断言的强度：字节锁 vs 语义断言（判例）

`portal/tests/test_round7_closeout.py` 曾用「sha256 常量 + 精确行数 + diff 恰为 +96/-3」锁 `nginx.conf`。结果：

- 每次有人合法改 nginx，门禁就红一次，**连续多个窗口复发**；
- 更糟：5 条「被删行」里有 **3 条是误判**（2 条 `auth_request` 只是被 allow/deny 插入后**下移**，1 条正则**扩面**）；
- 而它对**真删除**的拦截力并不更强——删一条 auth 只是把 `-3` 变成 `-4`，顺手改常量就放行了。

现改为 6 类语义断言：结构合法（无裸 LF、括号平衡、行数有下限）、`location` 不消失且指令集合不缩水、`auth_request` 覆盖不减、`satisfy any` fail-closed、副本不超前、原 4 条语义测试保留。

**通用结论**：锁「字节」会把合法变更和回滚混为一谈；应该锁「契约」。写断言前先问：**这条断言在什么情况下会红？如果答案是「有人改了正常代码」，它锁错了对象。**

### 7.5 自测必须能红

只写断言、不验证断言会不会红 = 没写。该文件 12 条 → 25 条的过程中新增 **13 条 mutation 自测**：9 条喂坏变体要求必红、3 条喂合法变体要求不误红、1 条拿**真实 `nginx.conf`** 做变异（摘掉 `/api/jev/` 的 `auth_request`）。

这次自测**当场咬出一个真漏洞**：初版按「词元子集」判扩面，`/api/x/` 被删后 `/api/y/` 因共享 `api` 词元被当成「扩面后代」放过了。收紧为「仅正则 + 同骨架 + 词元超集」才拦住。

**规范**：新增守卫类测试，必须自带「坏变体必红」用例，否则不予合并。

### 7.6 不在门禁里的测试：manual 清单（P1-6 分层落地，2026-11-08）

`portal/` 根级有 163 个 `test_*.py`，全都不在 `testpaths` 里。其中 147 个可收集、共 1,663 条用例**从未进过任何套件**——这是「测试被遮蔽」的第二现场。2026-11-08 按层处理，**不是一键全标 manual**（那会把真红永久沉底）：

| 层 | 文件 | 处理 | 理由 |
|---|---|---|---|
| 入口可达且全绿 | `test_watch_tower_linkage.py`、`test_watch_tower_snapshot.py` | **已纳入 fast**（2 + 6 条） | 补路径后实跑全绿，是现成的回归价值 |
| 入口可达但红 | `test_alerts_level.py`（4/4 红）、`test_alert_source_health.py`（14 个方法中 13 个含失败） | 模块级 `pytestmark = pytest.mark.skip(reason="stale: … 见 P1-6")` | 红因是「产品变了、测试没跟上」（分级少一档 5≠6、分区整体换名），定性归 owner。**不删、不改口径、不静默** |
| 顶层 `sys.exit` | `test_portal_key_rotation.py`、`test_publish_allowlist.py` | 已在 `collect_ignore` | 收集即 INTERNALERROR，设计如此，别统计成「导入错误」 |
| 脚本式自检 | 见下表 10 个 | 登记为 **manual**，不进 pytest | module 体直跑 + 自写 `check()`，0 个 pytest 用例 |

**manual 清单（改到相关模块时必须手动跑一次）**：

| 文件 | 跑法 | 为什么不能用 pytest 收 |
|---|---|---|
| `test_feishu_event_guard.py` | `python portal/test_feishu_event_guard.py` | module 级把 `api_misc.subprocess.run` 打桩且不恢复 → 收集即污染全进程（82 文件连带 AttributeError） |
| `test_inbound_gateway.py` | 同上 | module 体直跑，断言走自写 `check()` |
| `test_intel_bus.py` | 同上；真库对账段由 `__main__` 触发 | 单测与 `news.db` 对账混在一个脚本，结果用 `sys.exit` 表达 |
| `test_legion_auth_p0.py` | 同上 | 需本机拉起议事厅 HTTP 实例 |
| `test_legion_room_api_live.py` | 同上（临时端口 18096） | 起真实 HTTP 全链路，`sys.exit` |
| `test_legion_room_unit.py` | 同上 | module 体直跑 |
| `test_org_e2e.py` | 同上（OS 分配随机高位端口） | 拉隔离 `portal_api` 实例做端到端，绝不碰 8080/8091 |
| `test_org_guard_p1.py` | 同上 | 起服务 + `sys.exit` |
| `test_qd_multisource.py` | 同上（mock HTTP，不联网） | module 体直跑 |
| `test_voice_ingest.py` | 同上（无网络、无凭证、无 lark SDK） | module 体直跑 |

**口径提醒（最容易误判的一处）**：对这 10 个脚本式文件跑 `pytest --collect-only` 会返回 **rc=5（no tests collected）**。这是「收集成功但没有用例」，**不是错误**。把它们计入「16 个收集失败」是误读——真实收集错误只有 4 个 sys.path 型，且已修完（现在 163 个文件里 0 个因 import 失败）。

**红了怎么办**：`skip` 只用于「等 owner 定性」。定性结论出来必须二选一——修测试对齐新契约，或修实现对齐旧契约；**把 skip 改成删文件、把红用例清掉，是同一条病的复发**。

---

## 8. 开发流程与红线

### 8.1 工单与隔离

```powershell
$env:DEVTOOL_AGENT="席位名"; $env:DEVTOOL_IDE="IDE名"; $env:DEVTOOL_MODEL="模型名"   # 缺了会标 attribution_missing
python tools/devtask.py new <TID> "一句话目标"        # 建 .worktrees/<TID> + 分支 task/<TID>
cd .worktrees\<TID>                                   # 在这里干活
python tools/devtask.py merge <TID>                   # 内联质量闸：跑 fast，新增失败即拒
```

- **禁止在 master 直接开发**。紧急情况在主区改，`wb-guard` 会留痕 `.git/wb-main-wt-commits.log`（audit 模式放行，但不等于合规）。
- 合并另有一道 `pre-merge-commit` 钩子跑 `tools/merge_preflight.py`（纯文档单放行）。
- 纯文档/配置单（`docs/`、`deliverables/`、`portal/static/` 下的 md/json/css/js）自动跳过测试。

### 8.2 提交纪律（2026-11-02 并发事故后）

```powershell
git add -- <显式路径1> <显式路径2>          # 禁 -a / -A
git diff --cached --name-only              # ①核对暂存区：确认没夹带别人的 WIP
git commit -F <msgfile> -- <同样两个路径>   # ②commit 也带路径（见下方陷阱）
```

**陷阱（2026-11-07 真实事故）**：只写 `git add -- A B` 然后 `git commit -F msg`，会把**别人已经 `add` 过的文件一并提交**——因为显式 pathspec 只约束 `add` 的边界，`commit` 默认提交整个 index。当次就把他线 10 个交付物卷进了我的文档提交，逼得 `git reset --soft HEAD~1` 重做。

**正确姿势**：共享仓一律用**路径级提交** `git commit -F msg -- <paths>`，不看 index 脸色；提交前必须看 `git diff --cached --name-only` 而不是只看 `git status <我改的文件>`。

- 提交信息必须带工单号；内核 artifact 变更还要带 ADR 号（`tools/kernel_guard.py --commit-msg`）。
- **共享仓里别人未提交的改动不许覆盖**，也不许代为提交。发现同文件区有他线 WIP，先问再动。

### 8.3 删除纪律

- Windows 上删除一律**进回收站**（`SHFileOperationW` + `FOF_ALLOWUNDO`，本机脚本 `C:/项目/.workbuddy/tools/recycle_del.py`）。
- 批量删除前**先归档 + 落 manifest**（`.workbuddy/cleanup/last_targets.txt`），保证可逐条还原。
- 绝不 `git clean -fdx`、`rm -rf`；判「废弃」前先查引用方与守卫测试——本仓有一批文件是**测试断言其存在**的（如 `tools/kb_corpus/*DEPRECATED*`），单删必红。

### 8.4 门禁与绕过

四道门：端口注册（`check_ports --strict`）、Mixin 完整性、导航统一、明文凭据扫描；外加路由清单门（阻断）与 budget 预算门（strict）。

绕过只有一个开关 `DEVTOOL_MERGE_GATE=0`，**仅限回滚/紧急**，且回单必须注明「绕过质量闸」。

### 8.5 时间窗

- 主窗 **16:30–17:00**，次窗 **21:40–22:10**。
- **窗外只提交、不合并**。合并在窗口内做，便于出问题立刻回滚。

### 8.6 危险操作：L2 五要素

改系统级配置（代理/防火墙/服务/计划任务）、重启或关机、动生产服务器之前，必须提交五要素并取得**明确批准**（沉默不等于同意）：

1. 命令全文；2. 影响面；3. 不可逆点；4. 替代方案；5. 回滚预案。

---

## 9. 已知坑（判例库）

> 这一节是本文档最有价值的部分：每条都是真实踩过、有代价的。

| # | 坑 | 现象 | 规避 |
|---|---|---|---|
| 1 | **默认 `python` 是 3.13 且无 pandas** | pytest 大面积 import 错、假红 | 一律 `& "C:\Program Files\Python311\python.exe"` |
| 2 | **模块级副作用**（`test_legion_live_boot_p0.py` 曾在模块顶层起 HTTP 服务 + 改 `ops._load_token`） | pytest **收集阶段** import 即执行 → 全仓 `1 error`，11,154 项一条不跑 | 测试主体收进 `main()`，`__main__` 调用；pytest 侧只留一条 `skip` 占位 |
| 3 | **`from tools import` 命中仓库根同名 `tools` 包** | `ImportError: cannot import name 'quote_snapshot'` | 改 `from engine.tools import ...`，不要靠 `sys.path.insert` 抢 |
| 4 | **给 SELECT 加列会挤指标** | 证据页指标错位（`flow.first/last` 吃到 `id`） | 加列必须同步改索引取值；内部标识不进 UI |
| 5 | **`Promise.resolve(null)` 走 `.then`** | 全景页白屏（拿到 null 还继续渲染） | 先判空再进 then 链 |
| 6 | **`verdict` 里的 `**` 直出** | 页面出现裸星号 | 用统一富文本渲染，别直接插 |
| 7 | **`.vbar{flex:1}`** | 柱子被挤压变形 | 固定尺寸或 `flex:0 0 auto` |
| 8 | **兜底数据与官方后验混淆** | 用户以为看到的是真实后验 | 返回体加 `proxied` 标记，UI 显式区分 |
| 9 | **云端 LLM 配置放进 webroot** | key 等于挂在静态站上 | 配置放 `config/`（webroot 之外）或环境变量 |
| 10 | **字节锁断言** | 合法改配置→门禁红；真删除→顺手改常量放行 | 锁契约（§7.4） |
| 11 | **中文路径上 PowerShell 5.1 不可信** | `Test-Path` 返回 False、`DeleteFile` 报「找不到」但实际已删 | 中文路径的文件操作走 Bash/Python；删除后必须 `exists` 复核 |
| 12 | **`.venv_czsc`（852 MB）在工作树内** | 裸 `grep "TODO("` 命中 165 条，真实只有 2 条 | 扫描一律 `--exclude-dir={.venv_czsc,site-packages,pylibs}` |
| 13 | **数字并列被当成「全仓 PASS」** | 3881 + 1319 被相加 | 见 §7.3，口径必须带范围 |
| 14 | **服务器 git 1.8.3.1 不支持 `git -C`** | 不报错，只打 usage 并返回非 0，`$(...)` 静默变空串 | 用 `( cd dir && git ... )` |
| 15 | **用 `python` 跑长任务不落文件** | 输出丢失、无法判断进度 | 长跑输出重定向到文件，判进度看进程 + 产物 mtime |
| 16 | **`position:fixed` 的全局浮层压住内容**（`.wb-palette-trigger` 钉死 `right:18px;bottom:18px`，窗口一窄就压到内容栏里，`jev_target` 情绪面末行被盖） | 换个窗口宽度就出现，截图看是「差一点」，实测重叠 300~2400 px² | **按实测边线分级收窄**，不要用视口宽度做算术（有无经典滚动条会差 15px）：先改样式，再 `getBoundingClientRect().left >= 内容右边线 + 2` 判定，逐级 full→mini→corner→dot→hidden；内容右边线取 `['.ui-page','main','#app','.wrap','.container','#root']` 里最宽者再扣它自身 `padding-right` |
| 17 | **同角两个浮层可以同为 `z-index:900`**（`#ai-orb` 与 ⌘K 按钮） | 后注入者压在球上，谁也没报错，只有肉眼可见 | 浮层要么约定各自层级，要么显式互斥：`ui_palette.js` 检测 `#ai-orb` 贴近右下角时把按钮抬到球上方（`bottom = 视口高 − 球 top + 10`），球被拖走后自动回落 |
| 18 | **`os_theme.css` 给 `button` 设了 `min-height:40px`** | 局部写 `height:30px` 静默失效，实测仍是 40（截图像「多出 10px」） | 收紧控件高度时同时写 `min-height:0; min-width:0`，不要只写 `height` |
| 19 | **`core.autocrlf=true` 下 `git status` 干净 ≠ 工作区字节合规**（`portal/index.html` 工作区是纯 LF，换行守卫读原始字节必红，`git diff` 却一行都不显示） | 追换行问题的人会反复怀疑自己刚改的文件，因为凶手在 `git status` 里根本不存在 | 判「换行被破坏」只读磁盘字节（`b.count(b"\n") == b.count(b"\r\n")`），不要拿 `git status`/`git diff` 当证据；修法是把该文件按 CRLF 重写，提交后 blob 仍是 LF、diff 规模不变 |
| 20 | **「重采一遍基线」不等于修好**：`db_fingerprint` 收 `backups/db` 全库，而日排班每天 03:30 新建日期目录 + 追加日志 | 重采当天全绿，次日 03:30 后又红，且红在 `added_outside`，看着像「有人越权新增」 | 重采前先问「基线对象会不会自己长大」：会 → 把易变集合从判据里摘出来单列观测（本项目 `FROZEN_DB_DIRS` 冻结 9 个治理日期目录 + `db_runtime_dirs` 只留痕，与 ADR-29 外部作业同构），再重采。另：基线之后**任何并行会话写盘**都会判「未归因新增」，故签发必须 `capture` → `verify` **背靠背**（实测隔 8 分钟就红） |
| 21 | **让 LLM 自由填 `companies`，等于让它背股票代码表**：`news_ai.py` 的 prompt 只写「只填明确关联的A股代码，无则空数组」，没给名录、没要求原文出现、没有后置校验 | 30 天内 685 条「宁德时代」相关判定，**391 条（57.1%）正文通篇不提这只票**：种业大会（正文 64 字讲河南新乡育种）、医保影像 AI 大赛、大族激光回购、亿纬锂能评级全被绑成 300750；`reason` 写「利好生物育种/医疗信息化龙头」，`companies` 填锂电池代码，字段自相矛盾。同一条新闻的两个副本一次填 `[]`、一次填 `["300750"]` —— 是模型抖动不是规则命中。下游 `jev LIKE '%"companies": ["300750%'` 裸采信，于是「利多 570 条、证实率 46%」全建在幻觉上 | 三条一起做：①**写入侧**给 `companies` 加候选白名单（只允许从传入 stocks ∪ 文本实体里选）+ 正向要求「填了就必须在标题或正文原样出现」；②**读取侧**一律过 `portal/jev_hit_basis.verify` 反查文本，四档裁决（text_confirmed / industry_only / code_only / none），`code_only` 剔除并计入 `hit_audit.dropped`；③**展示侧**每条事件必须能渲染「命中词 + 原文片段」，否则用户无法判断真伪。注：行业词只做收敛映射（电气设备↔电力设备/电池/储能），**禁止跨行业联想**（育种、医疗不能给电池板块背书） |
| 22 | **按「事件」聚合收益统计 = 重复计数**：同一交易日 25 条事件，同一个 D1/D3/D5 收益被累加 25 次 | 页面印出「事件后 1 日 P25~P75 = +1.43%~+3.12%，中位 +1.43%」「5 日样本 15 却 +9.14%~+9.14%、同向 87%」——分布被单日行情完全支配，看着像规律 | 统计单位必须是**独立交易日**不是事件：先按事件所属交易日聚成一个观测，同日多空相抵视为方向不明不计；D0 落非交易日要**顺延到下一交易日**（旧代码 `dl.index(d0)` 失败即丢弃，而公告/研报恰恰多发在非交易时段）；百分位用线性插值（`chgs[int(n*0.25)]` 在 n 小时会与中位撞车）；独立样本 <5 一律输出「样本不足」而不是印区间 |
| 23 | **研判卡的过期判据用自然日**：`_sediment` 判 `(今天 - 卡交易日).days > 1` 即作废 | 每个周一、每个节后第一天，上一交易日盘后刚生成的卡全部被判过期 → 回落在线现算（实测 100s+），「打开即有结论」在最需要的时候（周初）恰好失效。周日晚实测：卡 `trade_date=11-06`（周五），`age=2` → 直接 MISS。同源缺陷在写入侧：预生成默认 `date = today`，周末跑出来的卡被标成周日（数据实为周五），页面读起来像「周日也有行情」，按日期做的时效判断也跟着错 | 时效判据跟**交易日**走、不跟自然日走：卡的 `trade_date ≥ 最近一个已收盘交易日` 即新鲜（口径复用 `datamart._expected_trade_day`，与 facts 的行情源同源），日历读不到才退回工作日近似（1 个工作日容差）；写入侧默认日同样取最近已收盘交易日，禁止写 `today`。历史错标卡可无损迁移——只改 `trade_date`、内容一字不动，且迁移后 pregen 的 datasig 命中即免重算 |
| 24 | **把「重复」当一件事处理**：同一个标题下有四种完全不同的东西——采集重复、媒体转发、模板快讯复读、固定栏目复用 | 一刀切去重会同时犯两个错：把「韩国交易所启动SIDECAR机制」跨 5 天的 4 波独立事件压成 1 条（丢掉 3 次真首发）；又把「同一篇稿被 4 家转」算成 4 个独立信号（热度虚增，方向被同一句话重复投票）。实测 13.7 万条里：真转发 6502、采集重复 2075、模板复读 6392、栏目复用 11144——四类各占不同量级、含义互不相同 | 三个维度一起用，缺一不可：①**正文相似度**（3-gram Jaccard，实测跨源同题中位 1.00，阈值 0.5 覆盖 87%）；②**时间间隔**（≤24h 才算同一波传播，超过即新一轮，轮内各自有首发）；③**是否跨源**（同源重复是采集缺陷或复读，不是转发）。另两条必须同时立：`title` 与 `content` 的「【标题】」语义相反（新浪把标题+正文前段塞进 title、东财把引题放正文），归一化要分 `norm_title`/`norm_text` 两个函数；**判「同一篇文章」优先用 id 里的内容 hash**（`cns:scroll:x` 与 `cns:finance:x` 是同一篇），别用 SQL 尾段截取——库里 6.9 万条 id 长度不足 20，截尾 16 位会错位比较，曾据此算出 61623 条重复（44.7%）的假数字，真值 1563（1.1%） |
| 25 | **板块联动的四类口径陷阱**（2026-11-08 Step 5 实测，11-06 普跌日）：①涨停判定用「任一档位 `close ≥ 涨停价`」——对 10% 档的票 close 必然高于 5% 档价，涨 6.47% 的海天股份被判涨停；②主板 ST 一律按 5% 档——撤销 ST 未改名的 *ST亿晶当日 +10.22% 永远匹配不上，漏判；③`news.ts` 是**入库时刻**不是发布时刻——公告批量落 00:00（单日 1313 条）、「临近午盘强势涨停」的稿子落 12:40（午休），拿它对分钟线得出「价格领先消息 189 分钟」，按 11:30 收市计应为 119；④「领涨」若按涨幅排——秋乐种业 +11.54% 涨幅第一，但万向德农 09:42 先封板，「谁领涨」变成「谁涨得多」的同义词 | 页面会批量印出「涨停 43 只」（真 33）「价格领先消息 189min」（真 119）这类半真半假的数，且整套领涨-跟随关系建立在错误锚点上 | 四条一起立：①**档位先定死再判定**（主板10%/创业·科创20%/北交所30%/ST5%，ST 当日涨幅>6% 反推为 10% 档），判定用 `|close − round(pre_close×(1+r),2)| ≤ 0.011` 双向接近，不用单向 ≥；②**ts_kind 三档**（batch=00:00 或整点同批≥50 条；intraday=09:15–11:30 ∪ 13:00–15:05；offhour=其余含午休），只有 intraday 参与 Δmin；③**Δmin 带符号**：正=消息先到（hit），负=价格先行（echo）——11-06 新型电力全板块 echo（价格 09:31 启动、消息 10:27 后才到），这正是「隐形关系」最有价值的发现，旧实现把它当 None 丢了；④**领涨按时间算**：最早封板 > 最早异动 > （无分钟时）涨幅第一并标 `basis=pct_only`，无分钟数据的票单列 `unpriced` 不冒充跟随。数据前提：stk_mins 只拉中证800（11 月目录 800 个 code 文件），热点票零覆盖——`tools/jev_mins_fetch.py` 按日补拉当日涨停+涨幅≥5%+焦点板块+盘中事件票（60 只 33.5s），写回用「剔除目标日→追加→原子替换」，不动其它日期、不改 manifest |
| 26 | **把 225GB 数据源接进来之前先踩一遍它的坑**（2026-11-08 Step 5 接入 TDX-Trading 实测，181 张表 5 个坑 + 1 个前端坑）：①requests 默认走系统代理（7890），内网白名单服务走代理从 0.4s 拖到 18s 甚至读超时——`Session.trust_env=False` 后 42 倍提速；②`ts_stk_mins` 的 start_date/end_date **完全失效**（三种格式全 total=0），只能 `code+limit` 倒序取再本地按时间前缀筛；③返回的 `total` 是「该 code 的全部历史行数」不是过滤后行数（002310 total=401 但当日 0 行），拿它算分页必错；④**存量不同步**：有的票分钟停在上一交易日，同一交易日 33 只涨停票只有 18 只在 `ts_stk_mins`——兜底必须允许单票失败，且 `ts_rt_min`（全市场采集 5570 只）只对**最近交易日**有效，11-04 只命中 6/45；⑤TDX 板块接口是坏的：rank 三个 seg 全 n=0、trade_date 停在 20261022，文档吹的「权威 56 行业+涨跌家数」取不到，板块口径当时继续用 stock_basic.industry（**这条已被判例 #28 推翻**：行业名≠题材名，「新型电力」是 32 只的发电运营商桶，只覆盖概念「电力」219 只的 12%）；前端坑：时间轴把 14 条配额全给早盘价格动作，「消息几点」反而看不见——消息保底占一半（≥4 条） | 接口 18s 超时假死 / 假 total 引发假覆盖 / 兜底静默全灭 / 时间轴只剩价格没有消息 | `tools/xtcloud_client.py` 集中兜（五个坑全写进类注释），`jev_graph._warm_many` 带预算（JEV_MINS_BUDGET 默认 12s）+ 并发取数串行写盘，`minute_read` 台账 local/remote/fail 三档分开报——只报总数会掩盖「远端兜底失效」 |
| 27 | **兜底取来的数据写回本地，会把残缺日「洗白」**（2026-11-08 代码审查，JEV 接入次日复盘）：`ts_rt_min` 一个交易日只有 237 根（缺 15:00 收盘集合竞价那根），而 `merge_write` 的幂等挡板是「目标日已有 ≥200 根就算补齐」——写回一次，237 根永久变成「已补齐」，之后再也不会补第 241 根，**尾盘才封板的票直接被判成没封板**。同一批审查还捞出三处同型缺陷：①`--source auto` 只在 `items is None` 时回落，远端返回 `[]`（存量停在上一交易日/老日期没采集）时 tushare 一次都没试过；②`_nodes` 读 `focus["board_main_net"]`，而 `rank_board` 根本不产出这个键——前端永远显示「—」，最容易被当成「没数据」；③`XtCloud.mins` 直接切 `day[:4]/[4:6]/[6:]`，传 `2026-11-06` 会拼出 `2026--1-06` 然后**静默返回空列表**，「没数据」和「参数写错」长得一模一样。另有两处并发/缓存：负缓存无过期（远端抖一次就整天 `no_minute` 直到进程重启）、同月 parquet 的 read-modify-write 无锁（并发 build 各读各写，先写的丢）；再加一处容量：进程内分钟缓存**无上限**（常驻 8091 一天 800 只 × 20 天 = 1.6 万个 DataFrame，涨到几百 MB 只能靠重启回收） | 残缺数据被固化 / 兜底链路假死 / 口径字段恒空 / 参数错当成没数据 / 一屏全是旧报告 | 写回按**来源表**分档（rt_min 只进内存不落盘）、回落一律判 `not items`、口径字段必须挂在**真正被 return 的那个 dict** 上、日期统一 `_d8()` 归一化且非法即抛、负缓存 TTL（`JEV_MINS_NEG_TTL` 默认 600s）、`_WRITE_LOCK` 串行写盘、缓存写入统一走 `_mem_put()` 并淘汰到上限（`JEV_MINS_MEM_MAX` 默认 4000）；`minute_read` 增 `partial` 单列。**已污染的数据要主动修**：5 只 237 根的票用 tushare 完整源 `--force` 重补到 241 根，另按「237 根且末根 14:59」全库 30,508 个 parquet 扫描确认无残留 |
| 28 | **把「行业」当「题材」用，等于换了个分析对象还不自知**（2026-11-08 板块口径核对，11-06 实测）：`stock_basic.industry` 有个行业就叫**「新型电力」**（32 只），Name 看着像市场说的新型电力系统，实际是 tushare 的「新能源发电运营商」桶。开盘啦 250 个题材里**没有这个名字**，对应的是「电力」219 只。两者只重合 26 只：行业独有 6 只（东方新能/云南能投/富春环保/珠海港/蓝丰生化/龙源电力，其中珠海港是港口、蓝丰生化是农药，开盘啦给它们挂的概念是港口航运/无），概念独有 193 只——**长江电力、华能国际、国电电力、国电南瑞、中国电建、特变电工、许继电气一家都不在行业里**。后果：所有「新型电力板块」结论只覆盖了电力概念的 12%，且恰好漏掉全部龙头；11-06 该桶内连板梯队 0 只、龙虎榜 0 只，而概念口径下润建股份 2 板、上榜 2 只。另一坑：`ts_kpl_concept`（题材统计 z_t_num/up_num）**停在 2024-11-17**（5000 行），`ts_kpl_list`（个股题材标签+涨停时刻）滞后一日；只有 `ts_kpl_concept_cons` 是现时的（13711 行/日）。 | 页面口径自洽、数字全对，但「板块」这个主语换成了另一群股票；旧判例 #26 里「板块口径继续用 stock_basic.industry」这句话会被照着执行，越用越歪 | ①**名称不可直接映射**：同名不同物（新型电力）、异物不同名（电力 ⊃ 新型电力）两种都有，禁止字符串匹配把行业名当题材名；②**双轨并存不二选一**：行业管「产业链位置」（谁是设备谁是运营商），概念管「市场在炒什么」，250 个题材互相重叠（一票平均挂 4~5 个）不能当互斥分区，110 个行业又不能当题材；③**题材强度自算**，别读停更的 `ts_kpl_concept`；④采集走 `tools/jev_concept_fetch.py`（按日翻页、**落盘前断言返回 trade_date 全等于请求日**——这张表的日期过滤偶尔仍会失效，不校验会把别的日子盖到今天的文件名上，与 #27 同型）、幂等 + 原子写，渲染层只读快照不联网。⑤单日 lift 行业 2.64 / 概念 2.96 差 12%，**不足以立论「概念更好」**；概念口径真正的收益是「板块内只有自己一个涨停」的孤立票 15/30 → 9/30，要立通用结论得跑 20 个交易日的 lift 序列 |
| 29 | **多实例并发是「谁也没发现自己不是唯一」的事故**（2026-11-08 深夜，瞬态 500 排查）：portal_api 用 ThreadingHTTPServer，继承 `allow_reuse_address=1`——Windows 上**第二个实例 bind 同端口会成功**，代码里 `except OSError: 静默退场` 在本机永不触发。换血 ~20s 空窗（flag 杀旧到新起）里看门狗探活失败拉起的孤儿进程不但活着还抢着服务，两个实例并发原子替换分钟 parquet，读方拿到 OSError [Errno 22] Invalid argument（幂等写回、本地读、merge_write 全有兜底，500 从这些路径都进不来——这也是当时难定位的原因）。次要坑：①计划任务 `MultipleInstances=IgnoreNew` + `ExecutionTimeLimit=10min`，python 在 ps1 前台跑，实例占坑 10 分钟 → **flag 换血最长要等 15 分钟**（00:05/00:10 两窗被忽略，00:15 才消费），别当场判失败；②沙箱内测不了跨进程锁——两次 Bash 调用是两个独立沙箱，各看各的文件覆盖层，进程 A 的锁在 B 里不存在（B 会照常抢到锁）；③`msvcrt.locking` 字节锁**不挡**别人的 open（实测），挡的是同区间读写；④换血脚本被安全中心拦 schtasks 时，flag 会由 5 分钟轮询自然消费，等就行 | 瞬态 500、信号随机、六组参数只有 kind=theme 挂——看起来像新代码 bug，其实是部署层多实例；差点顺着「题材口径并发不安全」去改错地方 | ①**单实例守卫锁**：`C:/Temp/portal_api_8091.guard` + `msvcrt.locking(LK_NBLCK)`，抢不到=已有实例 → exit 0；open 失败只警告**继续跑**（可用性优先），**只有抢锁失败才退场**——两个失败语义不同，errno 都是 EACCES，必须按阶段分；②`PORT`/守卫路径可环境变量覆盖（`PORTAL_API_PORT`/`PORTAL_API_GUARD`），测试用；③接口层异常堆栈落盘 `C:/Temp/jev_graph_err.log`（原来只回 type+msg，远程没法排查）；④`load_board` 的 dm.read 包 `_dm_read_retry`（OSError 等 0.25s 重试一次） |
| 30 | **把「视图截断」当「数据边界」，且关系层一联网就破了职责边界**（2026-11-09 Step 2，四谓词关系层下沉实测）：题材「电力」快照全量 **219** 只、当日有行情 **218** 只、`network.nodes` 只有 **60** 个 stock 节点（`NODE_MAX=60`）。从 nodes 编译 IN_BOARD 只能得 60/219；`focus.n=218` 与快照 219 差 1 只（无日线），两个数字都真、口径不同——直接相减会造出「补齐 158」这种假账。另一坑：`jev_graph.build()` 的 `chain` 层**会联网**（xtcloud 3 请求）、分钟层还有远端兜底，关系层只要图省事调一次 `build()`，就同时破了「只读快照 / 不联网 / 采集与渲染物理隔离」三条。第三坑：Step 1 前端的簇是硬编排散列表，边是 `build(day,board,code,top)` 按焦点现算且带采样截断（`hit_n<12`、`max_edges=24`），拿它当「可查询关系集」等于把视图固化成数据契约 | ①IN_BOARD 永远缺 159 只成员却显示「全量」；②跨板块 / 跨日 / 按席位反查统统做不到；③KG 接口冷启动顺手发起 xtcloud 请求，职责边界名存实亡；④索引不带 day 时换一天反查会串进别的交易日 | ①**三层数字分开报**：`snapshot_total=219` / `graph_focus_n=218` / `view_nodes=60` / `member_only=159`，成员补齐只对不在 nodes 的成员生成 `scope=member_only` 关系，证据源单列 `snapshot.members`，不许拿差值当补齐数；②`jev_kg.py` 是**纯编译器**：AST 级零网络 import（门禁掐断 `socket.connect` 仍须编译通过），只借 `jev_graph.load_board()`（本地 parquet）与 `jev_theme.load()`（题材快照），**禁止调 `build()`**；graph 响应由 `/api/jev/graph` 既有 60s 缓存提供，`src=snapshot` 可走完全离线路径；③索引一律 `(day, pred)` 复合键，顶层 day 不符**直接拒编译**，子源自带 day 不符**整源隔离**并留 warning，`of_pred(p, day=别的日子)` 必为空；④四谓词先在静态原型验证覆盖率再下沉，避免憋出没人用的 schema——行业 252 / 题材 326 条关系、17 源零漏项、端点零断链，Python 版与 JS 版逐项对齐才算下沉完成；⑤**新增 `/api/jev/*` 路由在 8091 换血前会被 `/api/jev/` 兜底吞成 200**（实测未重启时 `/api/jev/kg` 返回 `schema_version=jev.v2` 的判定查询数据，与 KG 毫无关系）——静默成功比 404 更危险：新分支必须排在兜底（`portal_api.py:1884`）之前，前端切 KG 前要校验 `ok && source` 字段，不能只看 HTTP 200 |
| 31 | **动生产页前先查页面真身；「L1 不动」要靠物理隔离，不靠口头约定**（2026-11-09 Step 4，下钻层替换实测）：此前多轮把「L1 总览」写成 `/board`，实际 nginx `location = /board` 的 `try_files` 指向 **`chanlun_board.html`（结构池与模拟仓·缠论，240KB）**，与 JEV 毫无关系；JEV 真正的生产页是 `jev_graph.html`（板块联动网）。差点照错误路径去重排 240KB 的缠论页。另两坑：①个股深链若把**旧焦点板块**当 board 传给下钻页，KG 编译的是上一个板块（输入 000595 时焦点还停在种植业），个股不在 `network.nodes` 里 → L3 抽屉静默不打开；②`url.replace("&embed=1","")` 剥不掉 `?embed=1&`（embed 是首个参数时前面是 `?` 不是 `&`），「新窗口打开」链接仍带嵌态。 | ①改错页面、把别人的业务看板当 JEV 总览重排；②点个股没反应却没有任何报错，看起来像「叙事层没数据」；③新窗口打开的不是完整页，少一层顶栏还以为是样式 bug | ①**先查 `nginx.conf` 的 try_files + 页面 `<title>`** 再认定生产页，禁止凭记忆写路径；②「L1 不动」的物理保证：覆盖层 DOM 全部放在 `</main>` 之外（`git diff` 只有追加 + 三处 onclick 替换），CSS / L1 容器 / 九个 `draw*()` 渲染函数零改动；`portal/tests/test_jev_drill.py` 15 条门禁锁死 L1 容器、渲染链、覆盖层位置、下钻 URL 契约、两页内联脚本 `node --check`；③**个股下钻不传 board**，让 `/api/jev/graph?code=` 走 `jev_graph.build()` 第 1053 行 `focus_name = z["board"]` 自动定位该股所属板块（实测 000595 → 新型电力，抽屉显示新能股份 +10.02% / 252 关系），再加 KG 实体表按 6 位码反查兜底（`showEntity(ref || _deepCode)`），覆盖 `NODE_MAX=60` 截断与跨板块两种情况；④下钻页 `?embed=1` 嵌态隐藏与 L1 重复的 crumb/kpis，返回条用 `postMessage` 关覆盖层，右上「收起 ×」与 ESC 是另两条可收路径；⑤剥参数同时处理 `?embed=1&` 与 `&embed=1` 两种形态。验收：真实浏览器 11 项 `TOTAL_FAIL=0`（L1 完整 / 点种植业进 L2 205 关系 / 嵌态返回 / 个股进 L3 / 收起 / 滚动恢复），`source=graph_cache` 未变、KG 门禁 26 passed + 新增 15 passed；**nginx、后端 Python、8091 全程零改动零重启** |
| 32 | **「全部展开」按钮按「有没有开着的」判状态，语义就反了**（2026-11-09 Step 4b，L1 下半区折叠实测）：首版逻辑「有开着的就全收」，单组展开后点「全部展开」反而把刚展开的也收掉；另一隐性风险是 `hidden` 容器让 SVG 按 0 宽度绘制（联动网用固定 `viewBox 680×430`，唯一 `offsetWidth` 只用于 tip 浮层定位，实测不触发） | ①用户点「展开」看到内容消失，会误判成「数据没加载」；②折叠后若 SVG 重绘失败，整个联动网空白且无报错；③只拍 900px 视口首屏，工具条与展开卡片都在折叠线以下，视觉验收会假绿 | ①按钮改成「未全开→全开，已全开→全收」，`allOpen()` 按 4 组实测状态判定且文案随状态切换，不按单个布尔；②折叠只给下半区 4 个 `div.grid` 加 `class="grid evidence"` + `hidden`，**中间联动网保留**（它是 L2 下钻入口），13 容器 / 9 个 `draw*()` / 渲染链字符串一个不动；③新增 `portal/tests/test_jev_evidence.py` 14 条门禁，锁「4 组默认收起 + 逐组 chip + 全部展开语义 + 联动网不被折叠」，原 `test_jev_drill.py` 15 条零改动仍全过；④首屏高度 3306px → **1514px（-54%）**，真实浏览器七项 `TOTAL_FAIL=0`（折叠 / 单组展开 / 全部展开 / 全收 / 长图变短 / 联动网仍可见 / L2 下钻回归），55 passed 门禁；⑤滚动补拍工具条与展开卡片确认无压字、两列对齐。48h 回看检查器 `C:\Temp\jev_kg_health.py` 已纳入三条 pytest 门禁，重采 T0 **31/31 PASS**，自动任务 2026-11-11 11:00 触发。nginx / 后端 / 8091 零改动零重启 |
| 33 | **「完全离线」要先有落盘工位；「内容哈希幂等」不等于「上游幂等」**（2026-11-09 Step 5 / 计划 A 实测）：`/api/jev/kg?src=snapshot` 从 Step 2 起一直是 400 `snapshot_missing`——只写了读盘代码、没人落盘。补采集工位时又发现 graph 响应**不是确定性的**：分钟层「本地优先→xtcloud 远端兜底→写回本地 parquet」让 60s 缓存过期后重建拿到更多分钟（`minute_read 58→59`、`focus.timeline` 整条换股、`edge_types.lead 8→9`），第一次「幂等复跑」全部 WRITE 而非 SKIP | ①为凑 SKIP 去冻结/忽略真实台账，会把「上游真变了」洗成「没变」，快照永久停在过期分钟口径；②拿冻结快照当实时 graph 用，结论载体悄悄换对象（判例 #28 同型）；③能力探测降级只有 Node vm 脚本验证，前端一改就没人拦；④既有门禁把「快照目录不存在」当成固定前提，目录一落盘就假红 | ①`tools/jev_kg_fetch.py` 输入 day、输出 `_jev_kg/<day>_<board>_<kind>.json`（**原始 graph 响应**，不是编译后 KG，读盘后现编译才与线上同源），无状态可整体替换；四道闸：日期 `[ord]` 码点复核 / `board_kind` 与 `focus.name` 口径断言 / 拒 `jev.v2` 兜底 / 非空；`ProxyHandler({})` 直连不走 7890（判例 #26）；原子写 + `.bak` 备份 + 内容哈希幂等。②`VOLATILE_KEYS` **只摘两类非结论字段**：`_kg_meta`（采集时间）与 `minute_coverage`（本地 parquet 目录规模遥测 1026→1029）；`minute_read` 等真实台账一律留在哈希里，收敛后实测 32 份里 27 SKIP / 5 WRITE，逐份核对 5 次 WRITE 全是 `minute_read`/`timeline`/`edge_types` 真实变化，不许冻结。③`portal/tests/test_jev_kg_fallback.py` 9 条门禁 = 静态锁降级接线 + Node vm 实跑 **31 个行为用例**（`kgUsable` 13 类伪造响应、`kgFromServer` 与 `compileRelations` 键覆盖 / 附加键白名单、6 位码兜底、徽标 hot/ok、深链）；④修掉 `test_jev_kg.py` 里依赖生产目录状态的假红用例，改成 monkeypatch 空目录 验 400 `snapshot_missing` + 把 graph 替身设成一调用就炸、验 `src=snapshot` 零 graph 调用。实测：32 份快照（双口径各 15 板块 + 2 auto，1.7MB），逐份回调 `source=snapshot` / `clean=True` / 零 FAIL；行业 252/176、题材 485/377 与 `graph_cache` **逐项零 DIFF**，耗时 **0.03s vs 14s**；真实浏览器徽标 `KG 服务端·落盘快照 · 252 关系`，五场景 `TOTAL_FAIL=0`。8091 全程未重启（pid 44936），后端仅改 docstring、行为零变化 |
| 34 | **T0 基线不能在抖动窗口采集**（2026-11-09 计划 A 收尾实测）：`jev_kg_health.py --init` 第一次（12:11）把 industry 空响应 + theme 439 关系（缓存重建 / 分钟写回中途）固化成 T0 基线，导致 11 项硬指标假红 + 软指标 439 vs 标准 485 永久偏差，48h 后对比必报假 DIFF。**根因**：graph 缓存 60s 重建期间 `/api/jev/kg` 可能瞬时超时或返回契约不完整的半成品。**修法**：① 单条用例加契约级重试（只针对 HTTP / 契约不完整，不针对 n_rel 数值——真实数据变化必须进 soft DIFF）；② `--init` 加双守门——连续两轮轻量软指标收敛且与全量一致、且零 FAIL 才写基线，否则拒绝覆盖已有 T0（返回 1）。实测：修复后重采 47/47 PASS，软指标全回标准值（252/176、485/377、252/176），行业 32/32、题材 219/219、snapshot 32/32 | ① 把抖动窗口的 FAIL 写成 T0，48h 回看基准失效、误判「无退化」；② 把 439 当标准值，指南口径与实时对不上；③ 重试去「洗白」真实 n_rel 变化 | ① 稳定性采样只比 `soft()`（source/n_rel/n_ent/pred_counts/members），不比数值是否等于某常量——换日仍可收敛；② 真实数据变化仍如实进 soft DIFF，硬判据只锁不漂移项（沿用判例 #33）；③ T0 旧坏基线另存 `.bad_1211.json` 留证，新基线全绿才覆盖 |


---

## 10. 运维

### 10.1 服务启停

| 动作 | 入口 |
|---|---|
| 受控重启 8091 | `tools/restart_8091_controlled.ps1` |
| 服务重启封装 | `tools/service_restart.py` |
| 健康检查 | `ops/health_check.py` → `ops/health_latest.json` |
| 军团命令面重启 | `ops/dsh_restart_8796.ps1` |
| 浏览器端重启入口 | 门户内 `/devops` 工单页 |

**重启前先辨认它是不是线上服务**（按启动时间 + 端口归属），别把正在服务人的进程当僵尸杀掉。

### 10.2 nginx 结构要点

- `location` **顺序敏感**：精确 `=` > `^~` 前缀 > 正则 `~*` > 普通前缀。改顺序等于改路由语义。
- `/api/jev/interpret` 单独放宽 `proxy_read_timeout 200s`（AI 慢），其余 `/api/` 默认 40s。**调超时只调这一个 location**，别全局放宽。
- 鉴权集中在 `auth_request /_auth_guard`，`satisfy any` 的 fail-closed 由测试守（§6.5）。
- 备份链：`reports/nginx.conf.bak-r7`（基线）、`reports/nginx.conf.pending-r8`（待应用副本）。**改配置先改副本，应用后同步**。

### 10.3 生产服务器

零界有对外发布目标机（Linux/CentOS，独立于本机）。发布走既定 skill（打包 → 上传 → 暂存树预检 → 切换 → 验证），**不手工 scp 覆盖**。任何涉及该机的系统级动作，按 §8.6 走批准。

### 10.4 备份

- 数据备份由计划任务承担（PG + SQLite 多库），另有一套 git 仓备份。
- 恢复演练做过且留回执；**没演练过的备份等于没备份**。

---

## 11. 当前状态与路线图

### 11.1 已关闭

| 项 | 结论 |
|---|---|
| 全仓测试收集中断 | 已修（模块级副作用），收集 11,187 / 0 error |
| nginx 字节锁常红 | 已改语义断言，portal 套件 1,319 / 0 failed，改配置不再需要改测试 |
| `agent_platform` 全量回归 | 三轮 3,881 passed / 0 failed |
| **P0-5** nav/route「工作区绿、HEAD 红」 | 已闭合：30 个 JEV 文件原子入库 `a65063496`、`config/cloud_llm.json` 进忽略 `f5c5e375b`；2026-11-08 00:1x 于 `80fc170e3` 复跑 `unify_nav --check` = 108 页零漂移、`portal_route_inventory --check` = 322 条零 diff |
| **budget 门（F1）** `api_chanlun_buy` 1514 / `api_chanlun_pages` 2411 超限 | 已闭合：23:50 `a8b4108ef` 把 ①e 影子清单块并回 `chanlun_buy_coarse.py`（341 行），buy 1514→**1176**（≤1200）、pages 2411→**2097**（≤冻结 2328）；00:1x 于 `80fc170e3` 跑 `check_portal_split.py --strict` = **rc=0，零告警**。基线未动过（`FROZEN` 自 10-28 起未改） |

### 11.2 未关闭（详见 `deliverables/2026-11-07_代码审查与遗留清单_v1.md`）

| ID | 事项 | 影响 |
|---|---|---|
| P0-2 | 20 项验收 20/20 UNVERIFIED，PASS=0 | 治理门未签，代码通过 ≠ 验收通过 |
| P1-5 | `gold_signal_shadow` 前视（结算用 `dt` 而非 `detect_date`） | 改口径需重签，否则 d20 虚增 0.80pp |
| P1-6 | portal 根级 **163** 个 `test_*.py` 全不在 `testpaths`（147 个可收集、**1,663 条**用例从未进过门禁） | **已按分层落地（2026-11-08）**：`conftest.py` 补 `sys.path.append` 修掉 4 个 sys.path 型收集错误；2 个全绿文件（watch_tower linkage/snapshot，2+6 条）纳入 fast；2 个实测红的（alerts_level 4/4、alert_source_health 13/14 方法）整模块 skip 挂起；10 个脚本式登记 manual（清单见 §7.6）。**剩余**：2 个红文件的「测试过时 or 实现退化」定性归 owner |
| P2-5 | 5 文件 6 处函数内 `from tools import` | 与「导入遮蔽」同源，未证实失败前不批改 |
| P3-5 | 首轮全仓真执行已跑（2026-11-08 01:10–01:32）：**11,155 passed / 53 failed** / 11 skipped / 13 xfailed，21′34″，RC=1 | 「全仓绿」仍不成立；53 条已归四类（见下）。**JSON 基线仍未落，且按现工具落不了全仓**：`tools/test_baseline.py` 的 `SUITES` 只有 `agent_platform/tests`（现存 `latest.json` = 2026-11-07 21:34，3,881 passed / 0 failed），照计划 `--run` 得到的是单套件绿基线，覆盖不到这 53 条 |

**P3-5 首轮归因（活树快照，非冻结 HEAD；期间 HEAD 从 `89e35983d` 漂到 `557df84ac`）**

| 类别 | 数 | 现状与归属 |
|---|---|---|
| 治理验收基线 `tests/test_governance_acceptance.py` | 4 | **已转绿（2026-11-08 按方案 A 重采 W-08 基线）**：① `dump_latest.log` 迁出 `backups/db` → `logs/db_dump_daily.log`（历史整份迁移，sha256 前后一致）；② 基线脚本 `db_fingerprint` 冻结为 9 个治理日期目录，日排班新目录进 `db_runtime_dirs` 只留痕；③ `AC_N03` 硬编码 `dates[0]=="2026-11-05"` 改为「最大且含 `quant_pg_alldb.sql.gz`」。实测 **21 passed**、`verify` **9/9 ALL_PASS**（基线 `db_fingerprint` 45→44）。首轮归因里那句「不是挪个日志落点能转绿」已被实跑验证：单独挪日志只会把红换成 `absent`。**全仓未复跑，53 → 理论上限 49（未实测）**。执行记录 `deliverables/2026-11-08_W08基线重采_治理4项转绿_v1.md`，判例见 §9 #20 |
| xq 分类契约 | 41 | 待 quant/xq 属主定性（断言「合法枚举直通」；实现侧 10-30 起改走 `safe_urlopen`，测试仍 patch `urllib.request.urlopen`，替身是否失效未证）。**不得加白** |
| portal 换行守卫 | 2 | **已转绿**：`portal/index.html` 工作区已在 01:45 之后的提交里恢复 CRLF（实测 CRLF=865 / bareLF=0），两条守卫复跑 **2 passed** |
| 导入顺序依赖 | 6 | 隔离 6/6 绿、仅混跑红；`tools/` 无 `__init__.py`（命名空间包），属 §9 判例 #3 家族。给 `tools` 加包标记会改全仓导入语义，**未做** |

### 11.3 建议顺序

1. ~~窗口内提交 P0-5 两文件~~ → **已闭合**（`a65063496` / `f5c5e375b`，HEAD 复跑绿，见 §11.1）；
2. ~~P1-6 决策：进套件 or 显式 `manual`~~ → **已按分层落地（2026-11-08）**，剩 2 个红文件等 owner 定性（见 §7.6）；
3. P3-5：~~首轮全仓执行~~ **已跑（53 failed 见 §11.2）**；落基线仍未做，且要先定口径——`test_baseline.py` 只覆盖 `agent_platform/tests`，要么扩 `SUITES`（属门禁口径变更，需裁定），要么另立「全仓基线」件；无论哪条都要先冻结 HEAD 复跑，写 `agent_platform/data/test_baseline/latest.json` 属数据写入需授权；
4. P1-5 口径重签后再改结算定位。

---

## 12. 附录

### A. 命令速查

```powershell
# 门禁
powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast
python tools/check_ports.py --strict
python tools/unify_nav.py --check
python tools/portal_route_inventory.py --check

# 测试（必须 3.11）
& "C:\Program Files\Python311\python.exe" -m pytest portal/tests -q -p no:cacheprovider
& "C:\Program Files\Python311\python.exe" -m pytest --collect-only -q -p no:cacheprovider

# 静态检查
& "C:\Program Files\Python311\python.exe" -m ruff check <files>
& "C:\Program Files\Python311\python.exe" -m py_compile <files>

# 工单
python tools/devtask.py new <TID> "目标"
python tools/devtask.py port <服务名>
python tools/devtask.py merge <TID>
```

### B. 路径索引

| 要找什么 | 去哪 |
|---|---|
| 接口实现 | `portal/api_*.py`（按 URL 前缀对应） |
| 接口分派 | `portal/portal_api.py` grep 路径字面量 |
| 页面 HTML 生成 | `portal/api_*_pages_*.py` |
| 前端样式/顶栏 | `portal/static/tokens.css`、`nav.js` |
| nginx 规则 | `portal/nginx.conf` |
| JEV 能力目录 | `portal/api_jev_access.py` 的 `CAPABILITIES` |
| 判定后验 | `agent_platform/data/jev_audit/`（`dec_audit`） |
| 遗留清单 | `deliverables/2026-11-07_代码审查与遗留清单_v1.md` |
| 计划/排期 | `docs/plans/`（现行：ROADMAP v4、未完成项总表） |
| 复盘/审计 | `docs/audit/` |
| 运行手册 | `docs/runbooks/` |

### C. 术语

| 词 | 含义 |
|---|---|
| **JEV** | 新闻/公告多维判读引擎：方向 × 置信 × 事件类型 × 影响标的 × 后验 |
| **dec_audit** | 判定审计表，T+1 回填真实收益/基准/超额/命中 |
| **后验（posterior）** | 判定发出后，用真实行情验证准不准 |
| **证据四级溯源** | 原文 → 判定 → 后验 → 归组 |
| **叙事（narrative）** | 把判定串成故事线：信源战绩 + 变化验证 |
| **强弱 / 超额** | 个股相对板块的表现（pp 为单位） |
| **RPS** | 相对价格强度排名 |
| **能力卡** | `CAPABILITIES` 里的一条对外接口说明 |
| **req_id** | 每次 JEV 调用的唯一标识，报障必带 |
| **L2 五要素** | 命令全文/影响面/不可逆点/替代方案/回滚预案 |

### D. 文档关系

```text
README.md            使用者：每天看什么、怎么用
ARCHITECTURE.md      架构简图：一页看懂组件与端口
AGENTS.md            开发宪法：强制规范（冲突时以此为准）
CONTRIBUTING.md      流程卡：三步走 + 验收独立性
docs/testing.md      测试指南：套件与基线（部分数字已过时，以 §7 为准）
DEVELOPER_GUIDE.md   开发全貌：环境/地图/架构/数据/能力/门禁/坑/运维/状态（本文件）
docs/plans/          计划与排期（唯一源）
docs/audit/          复盘与审计
deliverables/        交付物与遗留清单（唯一权威）
```

---

*版本：v1.3（2026-11-08 修订：§9 新增判例 #20「重采基线不连带收口口径 = 只绿一天」；§11.2 治理验收 4 条按方案 A 重采 W-08 基线转绿，21 passed / verify 9/9。v1.1 曾做 §0/§1.1/§1.2 定位同步 `A0-DEC-20261107` + `PRODUCT_BASELINE v1.2`、§11.2 入账 P3-5 首轮真执行 53 failed）。基线 HEAD 见提交时点；首轮真执行快照为 `89e35983d` → `557df84ac`。文中所有数字均为实测，改动后请同步更新并注明日期。v1.3 补 §9 判例 #27（兜底数据写回会洗白残缺日 / 回落判 not items / 日期格式静默失败 / 负缓存与写盘竞态），对应 7 处修复 + 12 条回归用例（第 7 条为常驻进程内分钟缓存无上限，二十个交易日可涨到几百 MB，统一走 `_mem_put()` 按插入序淘汰至 `JEV_MINS_MEM_MAX` 默认 4000），tests/ 201 passed、门户门禁 1332 passed。v1.4 补 §9 判例 #28（**把行业当题材用 = 换了分析对象还不自知**：行业「新型电力」32 只 vs 概念「电力」219 只，仅重合 26 只、电力龙头一家不含；`ts_kpl_concept` 主表停更在 2024-11-17，只有成分子表 `ts_kpl_concept_cons` 现时），配套 `tools/jev_concept_fetch.py`（日期纯净性校验 + 幂等 + 原子写）与 `deliverables/2026-11-08_板块口径核对_行业vs概念_v1.md`。v1.5 补 §9 判例 #29（多实例并发：allow_reuse_address 让第二实例 bind 成功、孤儿抢服务、分钟 parquet 原子替换撞读方 → 瞬态 OSError 22；单实例守卫锁 + 堆栈落盘 + dm.read 重试；附换血 flag 最长等 15 分钟、沙箱测不了跨进程锁两条纪律），tests/ 239 passed、门户门禁 1332 passed。v1.6 补 §9 判例 #30（**视图截断 ≠ 数据边界 / 关系层零联网 / 跨日索引 / 新 `/api/jev/*` 被兜底吞成 200**）：题材「电力」快照全量 219、当日行情 218、视图节点 60、补齐 159，三层数字分开报；`portal/jev_kg.py` 为纯编译器（AST 零网络 import、禁调 `build()`），`/api/jev/kg` 只读接口复用 graph 缓存，Python 版与 Step 1 JS 版逐项对齐（行业 252 / 题材 326 条关系、17 源零漏项、端点零断链）。本轮实测：新增 KG 门禁 **26 passed**；门户门禁 1373 passed / **7 failed**（全部为 `portal/nginx.conf` 既有未提交改动「白名单 141→142」与收口测试注释计数不一致）、根目录 tests 243 passed / **1 failed**（`check_ports.py --strict` 报 8772 未登记）、portal 下 JEV 套件 88 passed / **15 failed**（既有数据依赖类）；三份失败输出均零 `jev_kg` 命中，三把尺子不可相加、既有红不算绿。v1.7 补 Step 3 前端契约（**前端不能只看 HTTP 200**）：`/api/jev/kg` 重启前返回 200 但内容是 `/api/jev/` 兜底（`schema_version=jev.v2` 且 `ok:true`），故 `jev_intel.html` 的 `kgUsable()` 必须同时校验 `ok===true && !schema_version && source∈{graph_cache,snapshot} && rels.length>0 && cover`，不满足则降级 Step 1 本地 `compileRelations()` 并在页脚徽标标 hot；`kgFromServer()` 把服务端响应适配成与本地编译**完全同形状**的对象，L2/L3 渲染层零改动，L1 总览仍只读 `/api/jev/graph`，生产页 `/board` 零改动。深链 `kg=auto|server|local`、`src=snapshot`。验收：Node vm 全路径 0 FAIL（server 485 关系 / 377 实体 / 18 源零漏项 / 端点零断链，兜底降级 326 条与 Step 1 一致），headless Edge 双模 `.err=0`。**口径裁定（老田 2026-11-09 定案）**：题材成员**保持快照全量 219** 为成员集（结论载体不随当日行情变动）；218 只作为 `members.graph_focus_n` 单列，**不替换成员集**；`NODE_MAX=60` 视图节点与补齐 159 分列，不许拿差值当补齐数。理由：改成「只算当日有行情 218」等于把「无日线」判成「非该题材成员」（重演判例 #28 换分析对象），且 `cover` 的 `snapshot.members` 源会把那 1 只变成永久漏项。门禁已锁死：`test_full_members_219_of_219`（219/219，含 ord 复核）、`test_every_member_has_in_board`、`test_snapshot_members_source_covered`。**代码零改动**。v1.8 补 §9 判例 #31（**生产页真身 = `jev_graph.html` 而非 `/board`；「L1 不动」靠覆盖层放在 `</main>` 之外的物理隔离；个股下钻不能钉旧焦点板块**）：Step 4 只替换生产页 L2/L3 下钻层——点板块榜 / 点联动网节点 / 输入个股三处交互改为打开可收覆盖层，内嵌 `jev_intel.html?embed=1`（服务端真关系 KG 图谱 + 个股叙事抽屉），L1 的 13 个容器与九个 `draw*()` 渲染函数一个字未动；新增 `portal/tests/test_jev_drill.py` 15 条静态门禁（含两页内联脚本 `node --check`）。实测：真实浏览器 L1→L2→L3→收起 11 项 0 FAIL，L2 种植业 205 关系、L3 000595 自动定位新型电力并开出「新能股份 +10.02%」抽屉（252 关系），后端 `source=graph_cache` 未变。nginx / 后端 / 8091 零改动零重启。v1.9 补 §9 判例 #32（**折叠层按钮语义 / 只收下半区证据卡 / 中间联动网作为 L2 入口必须保留**）：按老田「现在就重排 L1」确认执行 Step 4b，`jev_graph.html` 下半区 4 组 8 张证据卡默认收起、chip 逐组可展开，「全部展开 / 全部收起」按实测状态切换；首屏 3306px → 1514px（-54%）。L1 13 容器与九个 `draw*()` 渲染函数仍零改动，新增 `portal/tests/test_jev_evidence.py` 14 条门禁，连同 drill 15 + KG 26 共 **55 passed**；真实浏览器七项 0 FAIL。48h 回看检查器重采 T0 为 **31/31 PASS**（新增「证据层 4 组默认收起」检查），一次性自动任务（2026-11-11 11:00）已激活，检查器同步纳入三条 pytest 门禁。8091 仍 pid 44936，零重启。v2.1 补 §9 判例 #33（**快照离线链路落地 / 上游非幂等 / 遥测白名单 / 降级契约 pytest 化**）：计划 A 两项加固完成——采集工位 `tools/jev_kg_fetch.py` 落盘 32 份 `_jev_kg/` 快照，`src=snapshot` 由 400 转 200 且与 `graph_cache` 逐项一致（0.03s vs 14s）；`portal/tests/test_jev_kg_fallback.py` 9 条门禁把 Step 3 能力探测降级从 Node vm 验证升为 pytest 常驻（含 31 个行为用例）。48h 检查器升级为**四条 pytest 门禁 + 第三条 `src=snapshot` 实调 + 快照目录检查 + 徽标第四场景**，T0 重采。教训：内容哈希幂等 ≠ 上游幂等；只摘运维遥测，不摘真实台账；冻结快照允许与实时在分钟态分叉，硬判据只锁不漂移项（否则 48h 后假红）。nginx / 后端接口行为 / 8091 零改动零重启。研究分析工具，不构成投资建议；v2.1 补 §9 判例 #34（T0 基线不能在抖动窗口采集：单条用例契约级重试 + --init 双守门，48h 回看基准失效风险根除；实测重采 47/47 PASS，软指标全回标准值）。*
