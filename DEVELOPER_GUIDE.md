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
| 形态 | **单机（Windows）个人 AI 平台**，不是 SaaS、不是多租户 |
| 人类入口 | **只有一个：`http://127.0.0.1:8734`**（nginx 同源反代；8080 仅容器/局域网链路，文档禁写双口） |
| API 进程 | `portal/portal_api.py` 监听 **8091**，是全部 `/api/*` 的调度器 |
| 代码规模 | 非依赖 `.py` 文件约 **8,000+**；`tools/` 1,540、`quant_backtest/` 1,284、`agent_platform/` 673、`portal/` 571 |
| 测试规模 | 全仓可收集 **11,187 项**（2026-11-07 21:00 实测，0 error） |
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

> 零界 = **持续创业者的个人事业操作系统**——让有判断力、有想法、但不愿亲自承担完整软件工程的人，快速组建一支可长期协作的多 Agent 团队，把想法变成有依据、受约束、可试用、可迭代、可验收的事业成果。

产品定义唯一源：`docs/plans/2026-10-24_PRODUCT_BASELINE_v1.md` v1.1；战略与排期唯一源：`docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md`。**这两个文件改口径要走裁定，不在本文档权限内。**

### 1.2 四个能力场景（不定义产品）

| 场景 | 载体 | 当前状态 |
|---|---|---|
| A 股量化情报 | `quant_backtest/` + `k_stock/` + `portal/api_backtest_*` | 主力产线，13 条策略在册（实盘 6 / 影子 3 / 工具 4） |
| 情报判读（JEV） | `news_collector/` + `portal/api_jev_*` | **当前主线**，14 张能力卡对外 |
| 数字军团 | `agent_platform/`（8 投研席 + 研发席） | 17 席日报已日跑，Case 端到端未闭环 |
| 内容/OSINT | `gzh/`、`osint_video/`、`video/` | 可用，非主线 |

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
| **1,319 passed / 0 failed** | portal 门禁套件（`portal/tests` + 2 个根级文件） | 门户口径，约 96 s |
| **11,187 collected / 0 error** | 仓库根 `--collect-only` | **只是能收集，不代表执行过** |

**三者不可相加，也不构成「全仓 PASS」。** 全仓真正执行过一次这件事，目前是**未完成项**（遗留清单 P3-5）。任何汇报里写「全仓 0 failed」都必须先跑完 `pytest -q`。

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

### 11.2 未关闭（详见 `deliverables/2026-11-07_代码审查与遗留清单_v1.md`）

| ID | 事项 | 影响 |
|---|---|---|
| P0-2 | 20 项验收 20/20 UNVERIFIED，PASS=0 | 治理门未签，代码通过 ≠ 验收通过 |
| P0-5 | nav/route 工作区绿，但两文件属他线未提交 WIP | **HEAD 上复跑仍红** |
| P1-5 | `gold_signal_shadow` 前视（结算用 `dt` 而非 `detect_date`） | 改口径需重签，否则 d20 虚增 0.80pp |
| P1-6 | portal 根级 16 个测试导入即错、且不在门禁 | 第二个「测试被遮蔽」温床 |
| P2-5 | 5 文件 6 处函数内 `from tools import` | 与「导入遮蔽」同源，未证实失败前不批改 |
| P3-5 | 全仓从未做过一次真执行全量回归 | 所有「全仓绿」结论都缺这一跑 |

### 11.3 建议顺序

1. 窗口内提交 P0-5 两文件，把「工作区绿」变「HEAD 绿」；
2. P1-6 决策：进套件 or 显式 `manual`；
3. P3-5：P1-6 之后跑首轮全仓执行，落 JSON 基线；
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

*版本：v1（2026-11-07）；基线 HEAD `89eb00e6e`。文中所有数字均为当日实测，改动后请同步更新并注明日期。研究分析工具，不构成投资建议。*
