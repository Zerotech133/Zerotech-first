# page_manifest 状态复核与 static 路由结论 v1

- 状态：**已复核并落地**（随 UI-UNIFY-MANIFEST-20261103 第 2 提交）
- 日期：2026-11-03
- 工单：`UI-UNIFY-MANIFEST-20261103`（Lead 追加任务）
- 依据：`docs/design/2026-11-03_零界UI宪法_v1.md`、`docs/design/2026-11-03_UI页面清单与入口职责_v1.md`、
  `docs/design/2026-11-03_page_manifest使用说明_v1.md`
- 产物：`portal/config/page_manifest.json`（129 页）、`tools/page_manifest.py`（v0.2 动态页对账）、
  `portal/test_page_manifest.py`（20 用例）

## 0. 复核方法（证据优先，不猜）

| 证据源 | 用途 |
|---|---|
| `portal/nginx.conf`（只读解析） | 路由是否存在、是否 302/301、是否 `auth_request`（OIDC）、是否前缀反代 8091 |
| `portal/portal_api.py`（只读解析） | **动态页直出静态文件**的真实映射（`if path == "/x": _html_file("<file>.html")`） |
| `portal/api_common.py::DOMAIN_NAV_GROUPS` | 页面是否被 nav.js 导航引用（在役强信号） |
| 8734 只读 HTTP 探测（curl） | 真实入口可否到达（200/302/404） |
| `git log -- <file>` | 页面内容最近一次实质变更（判断是否冻结/旧版） |
| 页面正文/标题/自述 | 是否评审稿、原型、未上线、只读投影、内部域 |

> 方法要点：**"有静态文件"≠"页面在役"**。本仓库大量页面由 portal_api 动态路由直出静态文件
> （如 `/btview → bt_view.html`、`/sa → stock_analysis.html`、`/intel → intel_workbench.html`），
> 若只按 nginx `try_files` 归类，会把在役动态页误判为"静态旧版"。v0.2 工具已加入「动态页对账」，
> 规范路由按**实际可达路由**归一。

## 1. 结论摘要

1. **15 个不确定页面复核完毕**：7 页 `legacy → active`（approvals / capability / flow / perf /
   bt_view / bt2_view / stock_analysis，其中 6 页原判"静态旧版"系误判——实为 portal_api 动态页直出），
   其余 8 页维持原判（dev_legion_v2、card_review、ui_kit 确认 active；org 维持 legacy 待 A0；
   os_approval_voice / os_voice_eval / os_software 维持 review 待上线/待 A0）。
2. **static/ 4 个有 nginx 路由的只读投影页 → 入 v0 manifest**（active，theme 暂 legacy-dark）：
   `/notify-command`、`/ops-board`、`/sub-admin`、`/my-subscription`。
   其余 8 个 `static/` HTML 仍按"资产/静态模板"排除。
3. manifest 页数 **123 → 127**（+4 static 投影页，复核当时）；rebase 到 master 后同批登记
   master 新增的 2 页（`case.html`、`dept_chat.html`）→ **129 页**；三态 **active 83 / legacy 16 / review 30**。
4. 6 处规范路由修正（动态页归一）：`/btview`、`/bt2view`、`/flow`、`/approvals`、`/intel`、`/sa`；
   1 处鉴权修正：`intel_workbench` auth `none → oidc`。
5. 复核中发现 **5 处路由缺口/安全债**（不属于 manifest 工具写域，列 L2/安全单，详见 §4.2）。

## 2. 逐页 status 复核（Lead 指定 15 页）

| # | 页面 | 原 status | 关键证据 | 终 status | 理由（一句话） |
|---|---|---|---|---|---|
| 1 | `dev_legion_v2.html` | active | nav.js 直接挂 `/dev_legion_v2.html`；页面走在役 `/api/dev/legion`；11-01 WO-DEV-LEGION-V2 交付；v1 页内互相引用且 nav 已指 v2 | **active** | 研发指挥台 v2 是在役正式入口，取代 v1 |
| 2 | `card_review.html` | active | nav.js 直接挂 `/card_review.html`；11-01 学习卡 wave 交付的 100 候选审核工具（挂 `/static/card_review_data.json` + 导出 CLI） | **active** | 在役人工审核工具页，非评审稿 |
| 3 | `ui_kit.html` | active | WO-LEGION-UI-KIT 交付的"UI Kit v0.1 组件规范/示例页"；`docs/design/2026-10-30_UI能力基线与开源对标_v1.md` 指定"页面见 portal/ui_kit.html" | **active** | 设计系统官方组件示例页（参考页定位：无短路由/无导航，直链访问） |
| 4 | `capability.html` | legacy | portal_api `/capability` 段 `_html_file(capability.html)`（M3-P5 能力看板）；nginx 无 `/capability` 路由（8734 实测 404），`/capability.html` 实测 200；nav 指向 `/capability` | **active**（路由缺口单列） | 页面本体在役（数据 `/api/capability/board`），原判"静态旧版"误判；入口断链属 nginx 缺口 |
| 5 | `flow.html` | legacy | portal_api `/flow` 直出本文件；nginx `location = /flow` 反代 8091；8734 `/flow` 实测 200；nav 指向 `/flow` | **active** | 动作流中心（F1）在役动态页，原判误判 |
| 6 | `org.html` | legacy | portal_api `/org` 走 `_org_page()` **历史 SSR**（api_misc 注释"K-STOCK-037 历史 SSR，已由新版 org.html 替代"）；nginx 无 org.html 短路由；`/org.html` 实测 200、`/org` 实测 200（旧 SSR） | **legacy（待 A0）** | 新版 Agent 指挥台已建但未接线，接线决策（换路由/转 active）待 A0/ARCH1 |
| 7 | `perf.html` | legacy | portal_api `/perf` 直出本文件（M4-P5 绩效看板）；nginx 无 `/perf` 路由（8734=404），`/perf.html` 实测 200 | **active**（路由缺口单列） | 页面本体在役，入口断链属 nginx 缺口 |
| 8 | `bt_view.html` | legacy | portal_api `/btview` 直出本文件；nginx `^~ /bt` 反代 8091（前缀命中 /btview）；8734 `/btview` 实测 200 | **active**（路由归一 `/btview`） | 回测中心在役动态页，原判误判 |
| 9 | `bt2_view.html` | legacy | portal_api `/bt2view` 直出本文件；nginx `^~ /bt` 前缀反代命中；8734 `/bt2view` 实测 200 | **active**（路由归一 `/bt2view`） | 回测框架 v2 在役动态页，原判误判 |
| 10 | `stock_analysis.html` | legacy | portal_api `/sa` → `_sa_page()` 直出 stock_analysis.html（K-STOCK-ANALYSIS-P0F）；nginx `^~ /sa` 反代；8734 `/sa` 实测 200；nav 指向 `/sa` | **active**（路由归一 `/sa`） | 个股全维分析页在役，原判误判 |
| 11 | `os_approval_voice.html` | review | C1 审批语音通道页；`/api/os/*` 后端在役（api_os_goal：asr/consumer 端点）；但无 nginx 短路由、不在 nav、全仓零引用；内容无"评审稿"字样 | **review（待 A0）** | 功能已实现但未接入入口/未被使用，转 active 与否属产品决策 |
| 12 | `os_software.html` | review | G1b 软件面工作台；页面自述"页面可直接打开，但接口需 8091 重启后才在现网生效" | **review** | 明确未上线（骨架/预览），上线后转 active |
| 13 | `os_voice_eval.html` | review | 语音质量门槛评测页（真人 10 条，`/api/os/asr` 在役） | **review（待 A0）** | QA 评测工具定位、非生产入口，是否登记 active 待 A0 |
| — | `intel_workbench.html`（复核附带发现） | active | portal_api `/intel` 直出本文件；nginx `/intel` OIDC 保护；但静态直链 `/intel_workbench.html` 实测 **200 未鉴权** | **active**（auth→oidc） | 在役情报工作台；静态直链绕过 auth_request 属安全债，列 L2 |
| — | `notify_command.html`（复核附带发现） | active | 根目录旧版"飞书通知指挥（HERMES）"；现行页面是 `/notify-command → static/notify_command.html`（K-ORG-201） | **legacy** | 同主题旧版页，保留入口/待归档 |

## 3. static/ 4 个有 nginx 路由页结论：**入 v0 manifest**

### 3.1 结论与证据

| 页面 | nginx 路由（`try_files` 直出） | 8734 实测 | nav.js | 依赖 | 终 status |
|---|---|---|---|---|---|
| `static/notify_command.html` | `/notify-command`（L889） | 200 | ✅ `/notify-command` | tokens+nav.js；无 os_theme/ui.css | **active**（theme=legacy-dark） |
| `static/ops_board.html` | `/ops-board`（L894） | 200 | ✅ `/ops-board` | tokens+nav.js；无 os_theme/ui.css | **active**（theme=legacy-dark） |
| `static/sub_admin.html` | `/sub-admin`（L899） | 200 | ✅ `/sub-admin` | tokens+nav.js；无 os_theme/ui.css | **active**（theme=legacy-dark） |
| `static/my_subscription.html` | `/my-subscription`（L905；移动 UA 302 → `/m/subscription`） | 200 | ✅ `/my-subscription` | tokens+nav.js；无 os_theme/ui.css | **active**（theme=legacy-dark） |

**入清单理由：**
1. 四者是 **8734 同源入口下人类可见页面**（UI 宪法 §1 适用范围），且有**显式 nginx 短路由**，
   不是"资产/模板"；
2. 四者均被 **nav.js 导航引用**（DOMAIN_NAV_GROUPS：通知指挥/运维对账/订阅运营/我的订阅），
   属生产入口页面；宪法 §6.3「页面必须登记到页面清单，未登记页面不得上线」；
3. 页面本体为只读投影页（零写操作），有完整业务语义与在役数据 API；
4. 原 v0 把 `static/` 整目录按资产排除，是"目录口径"而非"页面口径"，本次修正为
   **目录排除 + 4 个路由页显式补录**（`EXTRA_PAGES`，可审计）。

**保留问题（已写 note / 列后续）：** 四页均未接 `os_theme.css`/`ui/ui.css`（theme 暂 `legacy-dark`），
W2 迁主题；`sub_admin` 页面层未接 `auth_request`（依赖 8091 后端密钥闸，含 open_id 明文），列 L2 评估。

### 3.2 其余 `static/` 8 个 HTML：**不入清单**

`static/` 共 12 个 HTML，除上述 4 个外：
`comm_ticket.html`（动态路由 `/comm/ticket` 存在但 **nginx 未接通**，8734 不可达）、
`dw.html` / `dw_v2.html` / `dw_v2.template.html`（模板/副本）、
`acad1_25card_review.html`、`acad1_lcs1_redefine.html`、`huangzhong_events.html`、`jev_review.html`
（专项评审/活动静态页，无 nginx 路由、无 nav 入口）。
→ 结论：维持"资产/模板/离线评审页"口径排除；如未来接通路由或入 nav，按 §3.1 流程补录。

## 4. 路由归一与缺口清单（复核附带发现）

### 4.1 规范路由修正（manifest path/auth 变更）

| 页面 | 原 path | 新 path | 依据 |
|---|---|---|---|
| `bt_view.html` | `/bt_view.html` | `/btview` | portal_api `/btview` 直出 + nginx `^~ /bt` 反代（实测 200） |
| `bt2_view.html` | `/bt2_view.html` | `/bt2view` | portal_api `/bt2view` 直出 + nginx `^~ /bt` 反代（实测 200） |
| `flow.html` | `/flow.html` | `/flow` | portal_api `/flow` 直出 + nginx `location = /flow`（实测 200） |
| `approvals.html` | `/approvals.html` | `/approvals` | portal_api `/approvals` 直出 + nginx `location = /approvals`（OIDC） |
| `intel_workbench.html` | `/intel_workbench.html` | `/intel` | portal_api `/intel` 直出 + nginx `location = /intel`（OIDC） |
| `stock_analysis.html` | `/stock_analysis.html` | `/sa` | portal_api `/sa → _sa_page` 直出 + nginx `^~ /sa`（实测 200） |

> `screen.html → /screen`、`paper.html → /paper`、`acad1_pool.html → /acad1/pool` 原本已一致，不变。
> `capability.html`、`perf.html`、`intel_dossier_skeleton.html`、`quant_paper.html` 的动态路由
> **在 nginx 不可达**（见 §4.2），故规范路由仍按静态直链登记，未虚构 `/capability` 等路径。

### 4.2 路由缺口 / 安全债（不在 manifest 工具写域，建议 L2/安全单处置）

| # | 现象 | 证据（2026-11-03 只读探测） | 影响 | 建议 |
|---|---|---|---|---|
| G1 | `nav.js` 指向 `/capability`，nginx 无该路由 | `GET /capability`=404；`GET /capability.html`=200；portal_api `/capability` 已实现 | 导航断链（能力看板只能记直链） | nginx 补 `location = /capability` 反代 8091（L2） |
| G2 | `nav.js` 指向 `/perf`，nginx 无该路由 | `GET /perf`=404；`GET /perf.html`=200 | 导航断链 | 同上（L2） |
| G3 | portal_api `/intel-dossier` 已实现，nginx 未接通 | `GET /intel-dossier`=404；`/intel_dossier_skeleton.html`=200 | 评审骨架页只能直链 | 若需保留入口则补 nginx，否则维持 review+直链 |
| G4 | `/bt_view.html`、`/bt2_view.html` 直链 404（被 `^~ /bt` 前缀反代"劫持"进 8091 后无匹配路由） | `GET /bt_view.html`=404、`/bt2_view.html`=404；但 `/btview`、`/bt2view`=200 | 旧直链书签失效（不影响规范入口） | 可接受；如要兼容可在 nginx 加 `.html`→短路由 301 |
| G5 | 情报私有收口遗漏静态直链 | `GET /intel_workbench.html`=200 **未鉴权**（对照：`/xq_intel.html`、`/news_intel.html` 均 302 → /login） | 情报工作台内容可绕过 OIDC 直读（安全债） | nginx 对 `/intel_workbench.html` 加 auth_request 或 302 → /intel（L2/安全，**建议优先**） |

## 5. manifest 变更明细

| 变更 | 数量 | 明细 |
|---|---|---|
| 页数 | 123 → 127（复核）→ **129**（rebase master 后） | +4 static 路由页（notify_command/ops_board/sub_admin/my_subscription）；+2 master 新增页（case/dept_chat，均 active/OIDC） |
| status：legacy→active | 7 | approvals、capability、flow、perf、bt_view、bt2_view、stock_analysis |
| status：active→legacy | 1 | `/notify_command.html`（根目录旧版通知指挥，现行页为 `/notify-command` 静态投影） |
| 三态 | active 83 / legacy 16 / review 30 / archived 0 | review 30 页不变（os_entry 系/os_* 评审稿/protos/czsc 报告等）；active +2（case/dept_chat） |
| path 归一 | 6 | 见 §4.1 |
| auth 变更 | 1 | `intel_workbench`：none → `oidc`（随 `/intel` 路由） |
| 新增 note 标注 | — | 4 static 页 W2 迁主题；capability/perf 路由缺口；org/os_approval_voice/os_voice_eval/os_software 待 A0；intel 安全债 |

> 工具同步升级 `page_manifest.py v0.2`：新增 `parse_dynamic_pages()`（portal_api 直出静态页对账）、
> nginx 前缀反代/精确 location 可达性解析、`auth_drift` 校验、`EXTRA_PAGES` 补录；回归 20 用例。

## 6. 仍待 A0 / ARCH1 决策（不确定项保持现状，已写 note）

| # | 事项 | 现状 | 待决 |
|---|---|---|---|
| D1 | `org.html`（新版 Agent 指挥台，K-ORG-190 写回链） | legacy；`/org` 仍为历史 SSR，org.html 仅直链 | 是否把 `/org` 切到 org.html（L2 nginx/portal_api）→ 切后转 active |
| D2 | `os_approval_voice.html`（C1 语音审批通道） | review；后端 `/api/os/*` 在役、无入口/无引用 | 是否作为正式入口（接 nav/路由）→ 转 active |
| D3 | `os_voice_eval.html`（ASR 质量门槛评测） | review；QA 工具定位 | 是否登记 active（评测工具页是否属"生产页"口径） |
| D4 | `os_software.html`（G1b 软件面工作台） | review；页面自述接口需 8091 重启后生效 | 上线后转 active |
| D5 | `/capability`、`/perf`、`/intel-dossier` 路由缺口（G1/G2/G3） | 页面在役/登记，入口 404 | nginx L2 单补齐（补齐后 `--generate` 自动把 path 归一为短路由） |
| D6 | `/intel_workbench.html` 静态直链未鉴权（G5） | 已写 note | 安全单优先处置 |

## 7. 验证与复现（本次提交证据）

```powershell
# 1) 清单校验：129 页三态，0 violation
python tools/page_manifest.py --check
# => OK [page_manifest] pages=129 active=83 legacy=16 review=30

# 2) 回归（20 用例，含动态页归一/static 补录/路由漂移/依赖/基线/导航隔离）
python -m pytest portal/test_page_manifest.py -q
# => 20 passed

# 3) 清单列表 / JSON
python tools/page_manifest.py --list
python tools/page_manifest.py --check --json

# 4) 关键入口只读探测（复核证据，可复跑）
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/btview        # 200
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/sa            # 200
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/flow          # 200
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/approvals     # 302（OIDC）
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/intel         # 302（OIDC）
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/capability    # 404（G1）
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8734/intel_workbench.html  # 200（G5 安全债）
```

旁证（同分支已跑）：`portal_route_inventory.py --check` 293 条零 diff、`check_portal_split.py --strict` PASS、
`check_syntax.py --fail-on-warning` PASS、`unify_nav.py --check` PASS。

## 8. 回滚

- 复核结论全部体现在 `portal/config/page_manifest.json` + `tools/page_manifest.py` 的 `CURATED`/`EXTRA_PAGES`；
  回滚 = `git revert` 第 2 提交，清单回到 123 页原状态（不影响任何 portal HTML/nginx/运行时）。
- 4 个 static 投影页若决定回退：从 `EXTRA_PAGES` 移除并 `--generate --refresh` 即可（页面本体不动）。
- 本单**未改 nginx/HTML/路由基线/tokens**，§4.2 缺口仅记录，处置走 L2 单。

## 9. 参考

- `docs/design/2026-11-03_page_manifest使用说明_v1.md`（工具用法/门禁接入/更新流程）
- `docs/design/2026-11-03_零界UI宪法_v1.md`（§1 范围、§6 准入、§10 清单 schema）
- `docs/design/2026-11-03_UI页面清单与入口职责_v1.md`（人工版清单 v0）
- `tools/portal_route_inventory.py` + `tools/portal_route_baseline.json`（路由基线 293 条）
