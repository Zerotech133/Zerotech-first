# Phase B2 虚拟联调记录（不切真实登录）

- 日期：2026-10-30
- 工单：`WO-LEGION-POCKETID-B2B-20261030`
- 状态：**shadow 联调完成；登录入口仍未切换**
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


## 6. live 联调结果（2026-10-30 14:06）

环境：

```text
AUTH_OIDC_MODE=shadow
OIDC_ISSUER=http://localhost:1411
CLIENT=zero-bound-bff（test 期间取消用户组限制）
```

流程：

1. OTP 恢复管理员并注册虚拟 passkey；
2. 清理 Pocket ID cookie；
3. 访问 `/api/auth/oidc/start?next=/os-entry`；
4. Pocket ID passkey 登录 → OIDC callback；
5. 最终 URL：`http://localhost:8734/os-entry`。

`/api/bff/identity/me` 返回（节选）：

```json
{
  "ok": true,
  "mode": "session",
  "session": {
    "sub": "zero_bound:oidc_test",
    "idp": "pocket_id",
    "amr": ["phr"],
    "read_only": true,
    "identity_context": {
      "roles": ["VIEWER"],
      "mapping_status": "test"
    }
  }
}
```

说明：

- `amr=["phr"]` 是 Pocket ID passkey（WebAuthn）断言，**不是密码/OTP**；
- `mode=shadow`，`/login` 仍显示密码桥（实测 `#password` 存在、无 Pocket ID 按钮）；
- 5 页 auth_request 未改动；
- 截图：`docs/visual_baseline/2026-10-30_oidc_b2/b2_passkey_identity.png`。

## 7. test-only 清理项

- `identity_registry` 的 `zero_bound:oidc_test` 只用于联调，真实 B2 前删除或改成真实 owner 映射；
- OIDC client 当前为“所有用户可登录”，真实切换前应收紧到允许用户组（老田）；
- 虚拟 passkey 留在管理员 `tianfeng` 账户，仅测试；真实使用前由老田添加本机 passkey；
- 虚拟 passkey 对应私钥只在测试 Chromium profile，不属于任何真实设备。

## 8. 回滚

- 删除 `C:\aios\_vault\pocket-id\secrets\oidc_runtime.env` 或设 `AUTH_OIDC_MODE=off`；
- 重启 8091 → OIDC 端点 404；`/login` 不变；
- 删除 test actor 可与代码回滚分开执行。
