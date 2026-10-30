# Phase B2 虚拟联调记录（不切真实登录）

- 日期：2026-10-30
- 工单：`WO-LEGION-POCKETID-B2B-20261030`
- 状态：**shadow 联调；登录入口仍未切换**
- 授权：老田明确授权使用虚拟测试 passkey

## 1. 已完成

- Pocket ID B0 容器 healthy（`http://localhost:1411`）；
- 用官方 `one-time-access-token` 恢复管理员 `tianfeng`，Playwright 虚拟 authenticator 注册测试 passkey；
- 创建机密 OIDC client：
  - 名称：`zero-bound-bff`
  - 回调：`http://localhost:8734/api/auth/oidc/callback`
  - client id/secret：只写 `C:\aios\_vault\pocket-id\secrets`，不入库、不回显；
- 新增 `portal/auth_oidc_runtime.py`：
  - 从 `C:\aios\_vault\pocket-id\secrets\oidc_runtime.env` 读取 `AUTH_OIDC_MODE` 与 client 配置；
  - `shadow` 模式只开放 OIDC 端点，不替换 `/login`；
- auth_gateway 在 OIDC 路由前调用 runtime.apply()；
- 注册表新增 **明确标注 test** 的 actor：
  - `zero_bound:oidc_test`
  - `mapping_status=test`
  - `idp_issuer=http://localhost:1411`
  - `idp_sub=f5d9ec3c-...`（管理员 tianfeng）
  - 只读 VIEWER，不映射真实 owner。

## 2. 未做

- 未把真实 `zero_bound:owner` 映射到该 sub；
- 未修改 `/login` UI；
- 未切换 `AUTH_OIDC_MODE=on`；
- 未删除/覆盖用户其他 passkey；
- 未动只读 BFF、5 页 auth_request。

## 3. 验证

代码测试：

```text
portal/tests/test_auth_oidc.py
portal/tests/test_auth_oidc_runtime.py
8 passed
```

live 验证：

- `/api/auth/oidc/status` → `mode=shadow`、`ready=true`；
- OIDC start 302 到 Pocket ID；
- 虚拟 passkey 登录 → callback → session；
- `/api/bff/identity/me` → actor `zero_bound:oidc_test`；
- 密码桥仍 200。

## 4. 回滚

```powershell
# 关闭 shadow：删除/改名 runtime 文件（或 AUTH_OIDC_MODE=off）
# 重启 8091 后 OIDC 端点回到 404；登录入口不变
```

`identity_registry` 的 test actor 可单独删除。

## 5. 下一步（真实 B2）

1. 老田本人在 Pocket ID 账户页添加**真实 passkey**；
2. 把 `zero_bound:owner` 的 `idp_sub` 指向其真实 sub；
3. `AUTH_OIDC_MODE=shadow` 验证真实身份；
4. 改 `/login` 为 OIDC 入口，`AUTH_OIDC_MODE=on`；
5. 删除 test actor/test client 或保留隔离。
