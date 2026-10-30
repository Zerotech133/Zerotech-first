# Phase B3：owner 映射 + test actor 清理 + client 收紧 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-POCKETID-B3-OWNER-20261030`
- 状态：**shadow 保持；owner 复验通过；真实设备 passkey 仍待老田补**
- 前置：B2 虚拟 passkey shadow 联调通过

## 1. 变更

### 身份映射

`agent_platform/identity/identity_registry.json`：

- `zero_bound:owner` 增加：
  - `idp_issuer=http://localhost:1411`
  - `idp_sub=f5d9ec3c-cfd2-482c-9a00-b7b2250e9a43`（Pocket ID 用户 `tianfeng`）
  - `source_ids.pocket_id_sub` 同步；
- 删除测试 actor `zero_bound:oidc_test`。

### Pocket ID client

- 新建用户组 `zero-bound-owners`；
- `tianfeng` 加入该组；
- OIDC client `zero-bound-bff` 的 allowed user groups 收紧为该组（API 200）；
- client 不再“所有用户可登录”。

### 保持

- `AUTH_OIDC_MODE=shadow`；
- `/login` 仍密码桥；
- 5 页 auth_request、只读 BFF 不变。

## 2. 真实 passkey 边界

本次复验使用**软件测试 passkey**（Playwright virtual authenticator），不是老田的 Windows Hello/安全密钥。

真实 passkey 必须由老田本人：

```powershell
docker exec zerobound-pocket-id /app/pocket-id one-time-access-token tianfeng
```

打开一次性链接 → 账户页 → 添加通行密钥；完成后需再复验一次，再决定 `/login` 切换窗口。

## 3. 验证结果（2026-10-30 14:30）

实际执行：

```text
OTP 添加软件 passkey
→ 清 Pocket ID cookie
→ /api/auth/oidc/start?next=/os-entry
→ Pocket ID passkey
→ callback
→ /os-entry
```

`/api/bff/identity/me`：

```json
{
  "ok": true,
  "mode": "session",
  "session": {
    "sub": "zero_bound:owner",
    "idp": "pocket_id",
    "amr": ["phr"],
    "read_only": true,
    "identity_context": {"roles": ["ADMIN"]}
  }
}
```

确认：

- `sub=zero_bound:owner`，不是 test actor；
- `roles=["ADMIN"]`；
- `amr=["phr"]`（软件 passkey，不是密码/OTP）；
- `/login` 仍密码桥（`#password` 存在、无 Pocket ID 按钮）；
- `zero_bound:oidc_test` 已从 registry 删除；
- Pocket ID client 已收紧到用户组 `zero-bound-owners`。

截图：`docs/visual_baseline/2026-10-30_oidc_b3/b3_owner_identity.png`

**仍未完成**：真实设备 passkey。当前 `amr=phr` 来自 Playwright 虚拟 authenticator；真实 passkey 需老田本人在账户页添加。

## 4. 回滚

- 把 owner 的 `idp_sub/idp_issuer` 删除，恢复 test actor（如仍需联调）；
- client allowed groups 改回所有用户；
- 删除用户组；
- 删除 runtime shadow 文件；
- 登录入口始终未切换，无需回滚登录。
