# Phase B3：owner 映射 + test actor 清理 + client 收紧 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-POCKETID-B3-OWNER-20261030`
- 状态：**shadow 保持；真实登录入口未切换**
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

## 3. 验证计划

```text
GET /api/auth/oidc/status → mode=shadow, ready=true
OTP 添加软件 passkey → 清 cookie → OIDC start
→ Pocket ID passkey → callback
→ /api/bff/identity/me
   sub=zero_bound:owner
   roles=["ADMIN"]
   idp=pocket_id
   amr=["phr"]
```

## 4. 回滚

- 把 owner 的 `idp_sub/idp_issuer` 删除，恢复 test actor（如仍需联调）；
- client allowed groups 改回所有用户；
- 删除用户组；
- 删除 runtime shadow 文件；
- 登录入口始终未切换，无需回滚登录。
