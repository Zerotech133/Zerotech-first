# Phase A 页面登录态软保护 / 权限提示 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-AUTHGUARD-20261030`
- 前置：Phase A auth-gateway 已上线；本单只做**前端提示/软保护**，不强制锁死现有页面。

## 1. 结论

- 新增 `portal/static/auth_guard.js`，默认**软保护**：
  - 顶部显示登录态横幅；
  - 已登录显示身份/角色/只读；
  - 未登录显示“游客只读 + 去登录”；
  - 不发写请求、不读 token、不写浏览器存储。
- 已接入：`/os-board`、`/quant`、`/intel`；`/os-entry` 使用已有登录态面板。
- 新增 `/api/auth/guard`：有效 session → 200，否则 401，供后续 nginx `auth_request` 使用。
- 硬保护模式预留：
  - 页面根元素加 `data-auth-required="1"` 时，未登录会跳 `/login?next=...`；
  - 当前页面**未**开启硬保护，避免误锁。

## 2. 边界（重要）

这是**客户端提示**，不是安全边界：

- 可用 DevTools/脚本绕过；
- 真正的页面保护必须由 nginx `auth_request` 或服务端中间件执行；
- 当前只读页面仍可游客浏览，符合“仍只读”；
- 不改变任何写权限/交易权限。

## 3. 文件

| 文件 | 作用 |
|---|---|
| `portal/static/auth_guard.js` | 横幅、状态、`AuthGuard.require()`、硬模式 |
| `portal/auth_gateway.py` | 新增 `/api/auth/guard` |
| `portal/os.html` | `/os-board` 接入 |
| `portal/quant.html` | `/quant` 接入 |
| `portal/intel_workbench.html` | `/intel` 接入 |

## 4. 行为

### 未登录

```text
未登录 · 游客只读        去登录 →
```

链接带 `next=` 当前页面；登录后回原页。

### 已登录

```text
已登录 · 老田 · ADMIN · 只读        只读会话
```

身份来自 `/api/bff/identity/me` 的 IdentityContext；不显示 token。

### 服务异常

```text
登录服务不可用 · 游客只读 · HTTP 404
```

不猜测为已登录。

## 5. 硬保护（预留，未启用）

页面可加：

```html
<html data-auth-required="1">
```

`auth_guard.js` 检测未登录/错误 → `location.href=/login?next=...`。

生产启用前需：
1. 确认所有目标页面可登录；
2. 验证服务账号/owner 登录可用；
3. 评估 401/回跳循环；
4. 或改用 nginx `auth_request /api/auth/guard`（服务端真保护）。

## 6. 验证

- 12 tests passed（guard 401/200、页面接入、脚本无 localStorage/token）；
- `/os-board`、`/quant`、`/intel` 均含 `auth_guard.js`；
- `/api/auth/guard` 需 8091 重启后生效；
- 页面横幅在 8091 重启前即可用（依赖 `/identity/me`）。

## 7. 下一步

1. 验证三页横幅（游客/登录）；
2. 若要真保护：nginx `auth_request` + 精确页面列表 + 回跳；
3. 不建议全站硬锁，先保护 OS/管理面。
