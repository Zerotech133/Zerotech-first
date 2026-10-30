# ACAD1 × Hermes 零重启接入 · 验收工单 v1（2026-10-30 · 关羽）

- 工单：`WO-GUANYU-P91-20261030`（验收/门禁侧；施工分支：`task/WO-LEGION-ACAD1-HERMES3-20261030`）
- 性质：**L1**（不改 Hermes 核心、不改启动器/环境变量、**不重启 8787**；浏览器硬刷新即生效）
- 背景：先请求"新增 `extensions/acad1` + 改 `start_hermes_bg.py` env + 重启 8787"，经核，**该路径不必走**：
  `HERMES_WEBUI_EXTENSION_DIR` 是**单目录**（现指向 `Hermes/extensions/legion-ide`），加独立目录需改 env+重启（L2）；
  而 acad1 分支已实现的"legion-ide 扩展内入口/摘要/iframe"正是零重启方案。**结论：走方案 A，补齐 3 个硬问题后验收。**

## 一、现状审查（对 `task/WO-LEGION-ACAD1-HERMES3-20261030` 实测）

| # | 发现 | 判定 |
|---|---|---|
| 1 | `PAGE = http://127.0.0.1:5175/cn/replay?tab=selection`，用 iframe 嵌入 | 🔴 **硬阻断**：Hermes 页面 CSP `frame-src 'self'`（8787 实测）→ 跨源 5175 被浏览器拦截，iframe 不会显示 |
| 2 | 摘要 `fetch http://127.0.0.1:8102/api/v1/selection-public/summary` | 🟢 可用（实测 200，`Access-Control-Allow-Origin: *`；CSP `connect-src` 允许 `http://127.0.0.1:*`）。仅注意：页面若经 HTTPS 访问会触发混合内容限制（本机 http 入口无碍） |
| 3 | `portal/academy.html` 无 `?embed=1` 只读模式 | 🟡 若 iframe 直嵌 `/academy`，页内可能含写操作 → 不满足"只读下钻"约束，需 acad 线补 embed 模式 |
| 4 | 代码直接内联在 `app.js` 末尾（+58 行），按钮固定 `bottom:80px` | 🟡 可用；建议拆分到 `acad1/` 子目录 + 少量加载器，降低与 legion-ide 的耦合/冲突面 |
| 5 | 未改 Hermes 核心/启动器/env；未重启 | 🟢 符合零重启前提（分支 diff 仅 2 个扩展文件） |

## 二、必须修正（验收前置，3 项）

### F1 iframe 同源化（CSP 合规）

```js
// 以页面实际 origin 为准，不再写死 5175
const ACAD_ORIGIN = (location.port === "8734") ? "" : "http://127.0.0.1:8734";
const CAN_IFRAME = (location.port === "8734");   // frame-src 'self' 仅允许同源
// 8734 /hermes/ 访问：iframe.src = "/academy"（同源，实测 200 且无 X-Frame-Options/CSP）
// 8787 直连：不嵌 iframe；提供"在 8734 打开"按钮（window.open(ACAD_ORIGIN + "/academy")，属用户手势不触发弹窗拦截）
```
- 实测依据：`/academy` 响应头无 `X-Frame-Options`/`frame-ancestors` 限制；父页面 `frame-src 'self'` 决定只能同源。

### F2 只读下钻（acad 线配合，仍零重启）

- `portal/academy.html` 增加 embed 模式：`?embed=1` → 隐藏/禁用写操作（建主题、推进、反馈提交等），顶部显示"只读 · 来自 Hermes 学院面板"；
- 门户静态页改动**无需重启**（浏览器刷新即生效）；
- 若 acad 线暂不做 embed 模式 → 面板**只展示摘要 + 外链打开**，不做 iframe 下钻（避免嵌入可写页面）。

### F3 目录形态（保留"acad1 独立目录"诉求，零重启）

- 新建 `Hermes/extensions/legion-ide/acad1/acad1.js`、`acad1.css`（同源静态：`/extensions/acad1/acad1.js`，由现有 `/extensions/*` 路由提供服务，**无需 env/重启**）；
- `legion-ide/app.js` 仅追加 ~20 行加载器（幂等注入 script/link，mount 时调 `window.ACAD1.mount()`）；
- 全部 CSS 类名以 `acad1-` 前缀命名（现有实现已满足）；禁止外部 CDN 脚本。

## 三、验收矩阵（浏览器实测）

| 场景 | 断言 |
|---|---|
| 入口：`http://127.0.0.1:8734/hermes/` | 出现"司马徽"按钮；打开面板摘要显示 `数据日 2026-10-30 · 候选池 244 · 日报 12 · 共振 1`（以实际为准）；iframe 加载 `/academy` 正常，Console **无 CSP 报错** |
| 入口：`http://127.0.0.1:8787/`（直连） | 出现按钮、摘要可用；**不嵌跨源 iframe**，显示"经 8734 打开"按钮 |
| 只读性 | 打开面板/摘要/下钻全过程 **Network 仅 GET**；无 POST/PUT/DELETE |
| 回归 | legion-ide 原面板（打开/工单/预览/确认）功能不回归；Hermes 页面其他功能无影响 |
| 边界 | `git diff --name-only master...分支` 仅含：`Hermes/extensions/legion-ide/{app.js,app.css,acad1/*}`（+ acad 线门户/数据文件），**无** Hermes 核心、`start_hermes_*.py`、env 变更 |
| 零重启验证 | 部署方式：直接更新扩展目录文件 → 浏览器 **Ctrl+F5** → 新面板出现；8787 进程未重启（PID/启动时间不变） |

## 四、回滚

1. `git revert` 分支提交（或删除 `acad1/` 子目录 + 还原 `app.js` 追加段）；
2. 浏览器强刷；**无需重启 8787**；
3. 若同时改了 `portal/academy.html` embed 模式 → revert 该文件即可。

## 五、安全约束（扩展 JS 具备完整会话权限）

- 只读：扩展仅发起 GET（摘要/详情），不做任何写操作；
- 无外部脚本/样式/CDN；iframe 仅同源 `/academy`；不读取/不传递凭据；
- 不修改 Hermes 核心、不改 `HERMES_WEBUI_EXTENSION_*` env、不 touch `C:\Temp\hermes_restart.marker`、不杀/不重启 8787；
- 摘要接口若不可用：面板显示"摘要不可用"，不得静默空白（现有 catch 已满足）。

## 六、责任与排期

| 角色 | 事项 | 截止 |
|---|---|---|
| acad 线（WO-LEGION-ACAD1-HERMES3） | 修 F1/F3 + 与 acad 线协作 F2；自测验收矩阵 | 10-31 |
| 关羽 | 验收（CSP/只读/边界/零重启四类证据）+ 合并门禁 | 收到自测后当日 |
| 军团线（Hermes owner） | 知会（仅扩展面新增，无核心/env 变更） | 同步 |

## 七、附：验证命令（可复跑）

```powershell
# 1) 父页面 CSP 仅同源 frame
(Invoke-WebRequest http://127.0.0.1:8787/ -UseBasicParsing).Headers['Content-Security-Policy'] -split '; ' | Select-String 'frame-src'
# 2) /academy 可被同源嵌入（无 XFO/CSP 禁止头）
$r=Invoke-WebRequest http://127.0.0.1:8734/academy -UseBasicParsing; $r.StatusCode; $r.Headers['X-Frame-Options']
# 3) 摘要接口 + CORS
Invoke-WebRequest http://127.0.0.1:8102/api/v1/selection-public/summary -UseBasicParsing -Headers @{Origin='http://127.0.0.1:8734'} | % { $_.StatusCode; $_.Headers['Access-Control-Allow-Origin'] }
# 4) 边界（不应出现核心/启动器/env 改动）
git diff --name-only master...task/WO-LEGION-ACAD1-HERMES3-20261030
```
