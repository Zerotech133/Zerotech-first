# page_manifest 使用说明 v1（页面清单机器版）

- 状态：**v1 已落地（v0.2 口径，129 页；含 2026-11-03 状态复核）**，随 UI-UNIFY-MANIFEST-20261103 交付
- 日期：2026-11-03
- 工单：`UI-UNIFY-MANIFEST-20261103`
- 上位依据：`docs/design/2026-11-03_零界UI宪法_v1.md`（§2 分层模型"清单层"、§10 页面清单 schema v0）、
  `docs/design/2026-11-03_UI页面清单与入口职责_v1.md`（入口职责 canonical map / 三态清单）
- 配套复核：`docs/design/2026-11-03_page_manifest状态复核与static路由结论_v1.md`（15 页 status 复核 + static/ 结论 + 路由缺口）
- 产物：
  - 工具：`tools/page_manifest.py`
  - 清单：`portal/config/page_manifest.json`
  - 回归：`portal/test_page_manifest.py`

---

## 1. 一句话

页面清单（page manifest）把"8734 同源入口下有哪些人类可见页面、各自主题/壳/导航/鉴权/生命周期"
固化成**机器可校验**的 JSON；改页面、改路由、改主题、改路由基线都必须让 `--check` 重新变绿，
否则视为清单漂移（W3 门禁候选）。

---

## 2. 范围与统计口径（为什么是 129 页）

> 口径演进：v0 初版按"页目录"得 123 页；2026-11-03 Lead 复核追加后，`static/` 下 4 个
> **有 nginx 短路由、且在 nav.js 导航内**的只读投影页入清单 → 127 页；随后 rebase 到 master
> 同批登记 master 新增的 2 页（`case.html`、`dept_chat.html`）→ **129 页**。
> 差=6，页面本体与归类详见复核文档 §3/§5。

### 2.1 扫描范围

```
portal/*.html
portal/m/**/*.html                       # 含 m/auth/callback.html
portal/protos/*.html
portal/czsc-eval/*.html
portal/chanlun-learn-report/*.html
portal/quant/*.html                      # Quant React 构建产物（适配器例外）
portal/static/notify_command.html        # static/ 显式补录：有 nginx 短路由的只读投影页
portal/static/ops_board.html             #   （EXTRA_PAGES，2026-11-03 复核结论，见
portal/static/sub_admin.html             #    《2026-11-03_page_manifest状态复核与static路由结论_v1.md》）
portal/static/my_subscription.html
```

**动态页对账（v0.2）**：`portal_api.py` 中 `if path == "/x": ... _html_file("<file>.html")`
形式的「动态路由直出静态页」参与规范路由推导——页面 `path` 取 nginx 实际可达的路由
（如 `/btview → bt_view.html`、`/sa → stock_analysis.html`、`/intel → intel_workbench.html`），
避免把在役动态页误判成"静态旧版"；`auth` 随该路由的 `auth_request` 归一（如 `/intel` → oidc）。

**排除（生成物/备份/资产模板，不入清单）：**

| 路径 | 原因 |
|---|---|
| `portal/static/**`（除上述 4 个路由页外） | 静态资产/静态模板/离线评审页（8 个 HTML，如 `dw_v2.html`、`comm_ticket.html`） |
| `portal/_templates/**` | 壳模板（`page_shell_v1.html`） |
| `portal/work/**` | 回滚副本/未发布稿（含 `publish_rollback_*`） |
| `portal/picks_public/**` | 发布链路生成页 |
| `portal/data/**` | 运行数据/任务工作区 |
| `agent_board.html` | 运行时生成页（refresh_agent_board 产物） |
| `portal/quant/app.html` | L1 Quant 构建产物（未跟踪；nginx `/quant/` 302 目标），生成物不入清单 |
| 任意 `rollback/backup/_old/archive/snapshot/unpublished` 目录、`*.bak/*.orig/~` 文件 | 备份/归档 |

### 2.2 口径对账（2026-11-03 复核后实测）

| 计数 | 值 | 说明 |
|---|---|---|
| 在库（git tracked）`portal/**/*.html` | 138 | 99 根（含新增 case/dept_chat）+ 17 m（16 主 + auth/callback）+ 12 static + 4 protos + 3 czsc-eval + 1 chanlun + 1 quant + 1 _templates |
| 减 `static/` 非路由页 8 | 130 | 12 个 static HTML 中 4 个有 nginx 短路由（入清单），其余 8 个属资产/模板/离线页 |
| 减 `_templates/` 1 | **129** | **清单最终页数 = 129**（原 123 + 4 static 路由页 + 2 master 新增页） |
| 三态分布 | active 83 / legacy 16 / review 30 | 见 §5 |
| 未入清单的 live 增量 | `picks_public/`、`work/`、`data/`、`agent_board.html`、`quant/app.html` | 生成物/备份，不参与 UI 三件套检查 |

> 与"实测 153 个 HTML"的差异：153/155 含 picks_public、work 回滚、data 工作区等生成物；
> 生产审计页以本清单 129 页为准（与 UI 宪法验收表"页面清单 100%（W3）"同一口径）。
> 原 v0 的 123 页为"页目录口径"；127 为"页目录 + static 路由页"口径；129 = 127 + master
> 新增 2 页（rebase 同批登记；后续 master 再新增页面须同批 `--generate`）。

### 2.3 不在本清单 v0 的对象

- **纯动态页面**（portal_api 直接渲染 HTML、无静态文件可登记）：`/portal`、`/bt`、`/kg/*`、
  `/council`、`/devops` 等，由 `tools/portal_route_baseline.json`（293 条路由）约束；
  v0 仅在 `entry_map` 登记其中入口职责关键的 `/portal`。
  （注：**动态路由直出静态文件**的页面已入清单并按路由归一，见 §2.1。）
- **外部适配器**：`/hermes/`（8787 反代）仅在 `entry_map` 以 `external:hermes` 登记。
- **H5 参数路由**：`/m/strategy/<id>`、`/m/feed/<ticket>` 复用 `m/strategy.html`、`m/feed.html`，
  v0 不单列条目，在对应页 `note` 中说明。

---

## 3. schema v0（manifest 文件结构）

```jsonc
{
  "schema": "lingjie/page-manifest/v0",
  "generated_at": "2026-11-03T...",
  "generator": "tools/page_manifest.py v0.2",
  "scan":   { "root": "portal", "dirs": [...], "extra_pages": [...], "excluded_dirs": [...],
              "dynamic_page_routes": { "portal/bt_view.html": ["/btview"], ... } },
  "route_baseline": { "present": true, "path": "tools/portal_route_baseline.json",
                      "generated_at": "...", "total": 293, "sha256": "..." },
  "entry_map": [ { "entry": "/", "source": "portal/home_v3.html", "role": "...",
                   "theme": "dark-os", "nav": "nav-js", "status": "active", "note": "..." }, ... ],
  "counts": { "total": 129, "by_status": {...}, "by_family": {...} },
  "pages": [
    {
      "id": "page/zero_proto",              // 全局唯一，page/<slug>
      "path": "/zero_proto.html",           // 规范路由：nginx try_files / 动态页可达路由 / m 规则 / 静态直链
      "family": "cockpit",                  // cockpit|os|portal|h5|adapter（legacy/review 由 status 表达）
      "title": "零界 · AI 驾驶舱",
      "theme": "light-cockpit",             // light-cockpit|dark-os|h5|adapter:hermes|adapter:quant|legacy-dark
      "shell": "cockpit-shell",             // cockpit-shell|os-shell|h5-shell|none
      "nav": "own-6",                       // nav-js|own-6|m-shell|none
      "auth": "none",                       // none|oidc|portal-token（oidc 随规范路由的 auth_request）
      "status": "active",                   // active|legacy|review|archived
      "owner": "A0",
      "apis": ["/api/council/state"],       // 页面直接引用的 /api/*（静态扫描，可能不全）
      "note": "人看首页（浅色驾驶舱）；宪法 §3.1 ...",
      "source": "portal/zero_proto.html"
    }
  ]
}
```

### 3.1 `status` 语义（三态 + 归档）

| status | 含义 | 门禁要求 |
|---|---|---|
| `active` | 生产页，且满足三件套依赖（tokens / nav.js / m.js） | theme/shell/nav 非空；`nav=nav-js` 必须实测引用 nav.js；h5 必须 m.js/m.css；非适配器必须 tokens.css |
| `legacy` | 保留入口，待迁移/重定向/退役（含"生产可用但未接 tokens"的宪法缺口页） | 必须有 `note` 说明迁移/退役/保留理由；不得出现在 `review` 才有的隔离中 |
| `review` | 评审稿/原型/演示/报告页，**非生产入口** | 必须有 `note`；不得出现在 `nav.js` 导航路由中 |
| `archived` | 已重定向/移出 webroot | v0 无条目（os_entry 系先标 review + note "302 → /os-entry"） |

**判定纪律**：不确定的页面一律 `legacy`（待迁移）或 `review`（评审稿）+ `note`，
**不允许伪造 active**——active 一旦登记，`--check` 就会按三件套依赖硬校验。

### 3.2 约定

- `family`：结构族（cockpit 浅色驾驶舱 / os 深色工作台 / portal 旧门户 / h5 移动 / adapter 页面包）。
  `review`/`legacy` 不占用 family，用 `status` 表达。
- `owner`：默认 `A0`；`/hq` 归 `ARCH1`（与 `portal/config/hq_manifest.json` 一致）。后续可按域细化为
  ARCH1/DEV1/DEV5 等，需在 `--check` 前同步更新。
- `apis`：HTML 内 `/api/*` 字符串静态扫描结果，**只作线索**，不是运行时契约（运行时以
  `portal_route_inventory` 基线为准）。
- `visual_baseline`：可选字段，v0 未批量登记（视觉基线门由 `ui_visual_gate` 管）。

---

## 4. 入口职责（`entry_map`，先定死）

| 入口 | 实现 | 角色 | theme | nav | 状态 |
|---|---|---|---|---|---|
| `/` | `portal/home_v3.html`（nginx try_files） | **默认首页**（个人 OS 总览） | dark-os | nav-js | active |
| `/portal` | portal_api 动态 | 旧动态门户/运维入口 | legacy-dark | nav-js | legacy |
| `/zero_proto.html` | `portal/zero_proto.html` | **人看首页**（浅色驾驶舱） | light-cockpit | own-6 | active |
| `/os-entry` | `portal/os_entry_v9_prod.html` | **深色工作台/深屏**（auth） | dark-os | nav-js | active |
| `/os-board` | `portal/os.html` | 实况深页 | dark-os | nav-js | active |
| `/hq` | `portal/hq.html` | 指挥部（只读控制面，单入口） | dark-os | nav-js | active |
| `/m/*` | `portal/m/**`（17 页） | **移动 H5** | h5 | m-shell | active |
| `/login` | `portal/login.html` | 登录入口（auth-gateway） | dark-os | nav-js | active |
| `/home.html` | `portal/home.html` | 旧首页 | legacy-dark | nav-js | legacy |
| `/quant/` | Quant React 构建产物 | 量化页面包（适配器例外） | adapter:quant | none | active（入口）/ legacy（追踪文件） |
| `/hermes/` | 8787 同源反代 | 个人助理（适配器例外） | adapter:hermes | none | active |

规则：默认入口唯一 `/`；人看首页 `/zero_proto.html`；专业深屏 `/os-entry`；移动 `/m/*`。
四者不得互相"抢首页"；其余入口在清单中标注 legacy/adapter（同 UI 宪法 §1）。

---

## 5. 当前基线（2026-11-03 复核 + rebase 后生成，129 页）

- 三态：**active 83 / legacy 16 / review 30 / archived 0**
- family：os 107、h5 17、portal 3、adapter 1、cockpit 1
- theme：dark-os 77、legacy-dark 33、h5 17、adapter:quant 1、light-cockpit 1
- auth：none 109、oidc 17、portal-token 3
- 路由基线：`tools/portal_route_baseline.json` 293 条（sha256 记录在 manifest 头部）

**legacy 16 页构成**：
1. 旧入口/旧版：`/home.html`、`/hall`（旧 index）、`/picks_index.html`（301 → `/picks.html`）、
   `/dev_legion.html`（v1，nav 已指 v2）、`/notify_command.html`（旧版通知指挥，现行页为
   `/notify-command` 静态投影）；`/org.html`（新版指挥台待接线，**待 A0**）；
2. **缺 tokens 的宪法 §11 缺口页**（生产可用但未接设计令牌，W1 补齐后转 active）：
   `/dw`、`/h14_shadow.html`、`/kb_cards.html`、`/kb_corpus.html`、`/kb_ingest.html`、
   `/kb_reports.html`、`/minervini.html`、`/selection.html`、`/shadow.html`（9 页，均在 nav.js 导航内）；
3. Quant 旧构建产物：`/quant/`（nginx 已 302 → `/quant/app.html`，app.html 为未跟踪构建产物，不入清单）。

> 复核后 `capability/flow/perf/bt_view/bt2_view/stock_analysis/approvals` 7 页由 legacy 修正为 active
> （动态页直出/在役看板，详见《2026-11-03_page_manifest状态复核与static路由结论_v1.md》§2）。

**review 30 页构成**：os_entry 旧入口系 6 页（v系全部 nginx 302 → `/os-entry`）、
`os_demo`/`os_integrated_demo`/`os_map`/`os_goal`/`os_personal`/`os_room_preview`/`os_software`/
`os_approval_voice`/`os_voice_eval`/`zero_home`/`quant_v2_demo`（评审稿/骨架/演示；其中
os_software 未上线、os_approval_voice/os_voice_eval 待 A0 定是否转 active）、
`d1_war_room`/`sandbox`/`watch_admin_page`/`preview_hq`（原型/预览）、
`protos/` 4 页（待拍板原型）、`czsc-eval/` 3 页与 `chanlun-learn-report/`（静态报告证据页）、
`intel_dossier_skeleton`（/intel-dossier 路由未接通）。

**static/ 4 个路由投影页（active，theme 暂 legacy-dark）**：
`/notify-command`、`/ops-board`、`/sub-admin`、`/my-subscription`（未接 os_theme/ui.css，W2 迁主题；
`sub_admin` 页面层鉴权待评估）。

---

## 6. 工具用法

```powershell
# 生成/更新清单（保留既有条目的人工字段；新增页面按内置判定 + 入口表登记）
python tools/page_manifest.py --generate

# 重算人工字段（仅当确认要按内置判定重判全量时用；会覆盖手工 status/note）
python tools/page_manifest.py --generate --refresh

# 校验（差异 exit 1；manifest 缺失 exit 2）
python tools/page_manifest.py --check
python tools/page_manifest.py --check --json            # 机器可读结果
python tools/page_manifest.py --check --skip-route-baseline   # 仅本地排查：跳过路由基线对账

# 查看
python tools/page_manifest.py --list                    # 全量表格
python tools/page_manifest.py --list --status active
python tools/page_manifest.py --list --family h5
python tools/page_manifest.py --json                    # 打印 manifest JSON

# 指定仓库根（测试/临时树）
python tools/page_manifest.py --root C:\项目\老田的AI助手 --check
```

退出码：`0` 通过/成功；`1` 校验失败（有 violation）；`2` 用法/环境错误（manifest 缺失、解析失败等）。

### 6.1 校验项（violation code）

| code | 触发 | 修法 |
|---|---|---|
| `missing_page` | 磁盘上有生产页，manifest 未登记 | `--generate`（并人工复核 status） |
| `extra_page` | manifest 有，磁盘上无文件 | 删除/重命名后 `--generate`（审查是否应保留 review 归档条目） |
| `route_drift` | manifest `path` ≠ nginx/m 规则/**动态页可达路由**推导的规范路由 | 改 nginx（L2 单）后 `--generate`；或修正 manifest |
| `auth_drift` | manifest `auth` 与规范路由的 OIDC 保护状态不一致（如页面已挂 `/intel` 仍标 none） | 核实路由/访问控制后 `--generate` 刷新 |
| `active_deps` | active 页缺 tokens.css / nav.js / m.js 依赖 | 补齐资产（page_shell_gen）或把 status 降为 legacy/review |
| `entry_map_*` | 入口职责缺失/与页面登记不一致（默认首页非 home_v3 等） | 修 `ENTRY_MAP` 后 `--generate` |
| `route_baseline_drift` | 路由基线 sha256/条数变化，manifest 未对账 | 先 `portal_route_inventory --write-baseline`（同批路由变更），再 `--generate` |
| `schema` / `active_meta` / `status_note` / `nav_isolation` / `counts_stale` / `duplicate_*` | 枚举、字段、三态语义、review 不得入导航、计数过期、唯一性 | 按提示修 manifest |

---

## 7. 更新流程（改页面的人必读）

1. **新增页面**：`tools/page_shell_gen.py --new` 生成 → 编辑业务 → `page_manifest.py --generate`
   （新页默认按依赖判定为 active，若为评审稿/旧版需手工把 status 改 `review/legacy` + note）；
   若新增/改动了 nginx 路由或 portal_api 路由，同批重冻结 `portal_route_baseline.json`。
2. **删除/重命名页面**：先确认引用链（nav.js/页面内链接）→ 物理移动进归档（不删）→
   `--generate`（多余条目会被裁剪；仍要留档的改 review 并保留文件）。
3. **改主题/壳/导航**：改 manifest 对应条目 theme/shell/nav，跑 `--check`；
   active 页依赖校验会兜底（例如声明 dark-os+nav-js 却没引 nav.js 会 FAIL）。
4. **改路由基线**：`portal_route_inventory.py --write-baseline` 与页面/清单**同一提交**，
   然后 `page_manifest.py --generate` 刷新头部 sha256。
5. **改动态页路由**（portal_api 把路由切到/新增直出静态文件，或 nginx 新增/删除页路由）：
   同批 `page_manifest.py --generate`——工具会重新推导规范 `path`/`auth`；
   nginx 未接通的动态路由不会虚构路径（保持静态直链并在 note 标注缺口）。
6. **static/ 新增有 nginx 短路由的页面**：在 `tools/page_manifest.py::EXTRA_PAGES` 显式补录后
   `--generate`（目录口径排除 + 路由页显式补录，见复核文档 §3）。
7. **提交前自检（必须）**：

   ```powershell
   python tools/page_manifest.py --check
   python -m pytest portal/test_page_manifest.py -q
   ```

---

## 8. 门禁接入（W3 候选，本单不改 test_gate）

本单**只交付工具+清单+回归**，未改 `tools/test_gate.ps1`（写域外）。接入建议（由 Lead/门禁单执行）：

1. `tools/test_gate.ps1` 的 `'portal' = @(...)` 文件列表追加 `portal/test_page_manifest.py`
   （该套件已进 fast 门；`portal/tests` 之外的根级回归需显式列入，先例：`portal/test_bot_ws_backoff.py`）；
2. fast 门内在路由清单门（P130）之后追加一条命令门：
   `python tools/page_manifest.py --check`（失败即阻断合并）；
3. 文档单/纯 CSS/资产单可在 merge_preflight 分类中放行（清单不变时 --check 仍应 rc0）。

> 未接入前，页面治理依赖人工按 §7 跑两条命令；`portal/test_page_manifest.py` 已在仓库内，
> 可随时并入 fast 门。

---

## 9. 已知缺口 / 待复核

> 15 页不确定性复核 + static/ 结论 + 路由缺口已单独成文：
> **`docs/design/2026-11-03_page_manifest状态复核与static路由结论_v1.md`**（含逐页证据与 8734 实测）。
> 本节只列仍未关闭项（不确定项保持现状，已写 note）。

1. **待 A0/ARCH1 决策项**：
   - `org.html`：新版 Agent 指挥台未接 nginx 短路由（`/org` 仍历史 SSR）→ 是否接线/转 active；
   - `os_approval_voice.html`、`os_voice_eval.html`：功能/后端在役但无入口无引用 → 是否转 active；
   - `os_software.html`：页面自述接口需 8091 重启后生效（未上线）→ 上线后转 active；
   - 3 个页面当前保持 review/legacy，**不伪造 active**。
2. **13 页缺 tokens 的宪法 §11 缺口**：本清单口径下实测 10 页（9 页在 nav 内 + quant 构建产物），
   已标 legacy；W1/W2 补齐 tokens 后应把 status 改回 active（`--check` 会强制依赖齐全）。
3. **路由缺口/安全债（L2 单，非 manifest 工具写域，详见复核文档 §4.2）**：
   `/capability`、`/perf`、`/intel-dossier` 静态页在役但 nginx 未接通（8734 404）；
   `/bt_view.html`、`/bt2_view.html` 直链被 `^~ /bt` 前缀反代劫持后 404（短路由 `/btview`/`/bt2view` 正常）；
   **`/intel_workbench.html` 静态直链实测未过 auth_request（情报内容可绕过 OIDC 直读，建议优先处置）**。
4. `m/strategy.html`、`m/feed.html` 的参数路由（`/m/strategy/<id>`、`/m/feed/<ticket>`）未单列条目。
5. 纯动态渲染页（/bt、/kg/*、/council 等无静态文件）不在 v0 清单，仅由路由基线约束；
   如需 100% 页面覆盖，W3 起可加 `dynamic_pages` 段（需与 portal_api 路由表连通）。
6. `owner` 目前 128 页 `A0`、1 页 `ARCH1`，未按域细化；细化时需同步 manifest。
7. `static/` 4 个路由投影页未接 `os_theme.css`/`ui.css`（theme 暂 `legacy-dark`，W2 迁主题）；
   `sub_admin` 页面层未接 `auth_request`（依赖 8091 后端密钥闸，L2 待评估）。

---

## 10. 回滚

- 清单/工具/测试/文档为新增或本工单内更新文件，回滚 = `git revert` 本工单提交（无运行时/服务/nginx 依赖，不影响 8734）。
- 工具只读扫描 portal 页面、nginx.conf、portal_api.py（不改任何 HTML/nginx/路由基线/tokens）。
- `--generate` 写盘前保留人工字段，误生成可用 `git checkout -- portal/config/page_manifest.json` 恢复。

---

## 11. 参考

- `docs/design/2026-11-03_page_manifest状态复核与static路由结论_v1.md`（15 页 status 复核 + static/4 页入清单结论 + 路由缺口）
- `docs/design/2026-11-03_零界UI宪法_v1.md`（清单层/schema v0/入口职责）
- `docs/design/2026-11-03_UI页面清单与入口职责_v1.md`（人工版页面清单 v0）
- `tools/portal_route_inventory.py` + `tools/portal_route_baseline.json`（路由基线，293 条）
- `portal/config/hq_manifest.json`（/hq 单页原型，schema 先例）
- `tools/page_shell_gen.py`（壳资产三件套生成器）
