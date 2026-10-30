# Phase B4：登录页 OIDC 路径打通（保留密码桥）

- 日期：2026-10-30
- 工单：`WO-LEGION-OIDC-LOGINPATH-EVID-20261030`
- 状态：**路径已打通；`AUTH_OIDC_MODE=shadow`；密码桥保留**
- 仍未做：真实设备 passkey、`/login` 完全切 OIDC

## 1. 变更

`portal/login.html` 新增：

- 仅当 `/api/auth/oidc/status` 返回 `ready=true` 且 `mode != off` 时显示；
- “使用 Pocket ID 登录”按钮，跳转：
  `/api/auth/oidc/start?next=<当前 next>`
- 密码表单保留，未启用 OIDC 时完全隐藏 OIDC 入口。

## 2. 实测路径

```text
http://127.0.0.1:8734/login?next=/os-entry
→ 点击“使用 Pocket ID 登录”
→ http://localhost:1411/interaction/...
→ 虚拟 passkey 登录
→ http://127.0.0.1:8734/api/auth/oidc/callback
→ http://127.0.0.1:8734/os-entry
```

`/api/bff/identity/me`：

```json
{
  "status": 200,
  "sub": "zero_bound:owner",
  "roles": ["ADMIN"],
  "amr": ["phr"]
}
```

## 3. 回退

- `AUTH_OIDC_MODE=off` 或删除 runtime 文件 → OIDC 按钮消失，密码桥不变；
- 登录页按钮是渐进入口，不影响 `/api/auth/login`。

## 4. 待办

- 真实设备 passkey 由老田本人添加；
- 真实 passkey 复验通过后，才讨论 `/login` 默认切 OIDC。
