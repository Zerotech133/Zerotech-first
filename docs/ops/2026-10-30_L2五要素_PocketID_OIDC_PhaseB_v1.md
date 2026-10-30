# Phase B L2 待批：Pocket ID 部署 + OIDC 替换密码桥 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-PHASEB-L2-20261030`
- 状态：**待批，未创建容器、未改代码、未占端口**
- 前置：Phase A auth-gateway 已上线；Phase A 密码桥仍可用，作为回退
- 目标：用 Pocket ID（passkey/OIDC）替换人工登录的密码桥，**不替换**只读 BFF 的智策服务账号

## 1. 结论

分三小步，避免一次性替换：

```text
B0 部署 Pocket ID（独立容器，不影响现有登录）
B1 auth-gateway 增加 OIDC 客户端（feature flag 默认 off）
B2 老田注册 passkey → 映射 idp_sub → 切换登录入口 → 观察
B3 稳定 2 周后关闭人工密码桥
```

只有 B0+B2 属 L2；B1 是代码+测试（L1）。

## 2. B0：Pocket ID 容器

### 端口

- 默认 `1411`（当前 **未监听**，registry 未登记）
- 绑定 `127.0.0.1:1411`，不暴露局域网
- 若 1411 被占用，改用 `tools/devtask.py port pocket-id` 分配并同提交登记

### 数据/密钥

```powershell
# 目录（ASCII 路径，避免 Docker 中文路径问题）
New-Item -ItemType Directory -Force C:\aios\_vault\pocket-id\data
New-Item -ItemType Directory -Force C:\aios\_vault\pocket-id\secrets

# 加密密钥：只落 vault，不回显
python -c "import secrets; print(secrets.token_urlsafe(32))" |
  Set-Content C:\aios\_vault\pocket-id\secrets\encryption_key -NoNewline -Encoding ascii
```

### 启动命令（待批后执行）

```powershell
docker run -d --name zerobound-pocket-id --restart unless-stopped `
  -p 127.0.0.1:1411:1411 `
  -v C:\aios\_vault\pocket-id\data:/app/data `
  -v C:\aios\_vault\pocket-id\secrets\encryption_key:/run/secrets/pocket_id_key:ro `
  -e APP_URL=http://127.0.0.1:1411 `
  -e ENCRYPTION_KEY_FILE=/run/secrets/pocket_id_key `
  -e PORT=1411 `
  -e HOST=0.0.0.0 `
  -e TRUST_PROXY=false `
  -e ALLOW_INSECURE_CALLBACK_URLS=false `
  -e ANALYTICS_DISABLED=true `
  -e VERSION_CHECK_DISABLED=true `
  ghcr.io/pocket-id/pocket-id:v2.19.1
```

- 镜像版本固定 `v2.19.1`（官方当前 release）；
- `APP_URL` 必须与浏览器访问地址一致；passkey 绑定该域；
- SQLite 数据在 named/bind volume；`ENCRYPTION_KEY` 丢失=token 签名/敏感数据不可恢复，必须备份 vault。

### 初始化

1. 打开 `http://127.0.0.1:1411`，创建管理员 + passkey；
2. 关闭开放注册（`ALLOW_USER_SIGNUPS=disabled`，UI 配置）；
3. 创建 OIDC client：
   - Name：`zero-bound-bff`
   - Callback：`http://127.0.0.1:8734/api/auth/oidc/callback`
   - Grant：Authorization Code + PKCE
   - 保存 client secret 到 `C:\aios\_vault\pocket-id\secrets\zero_bound_client.env`（不打印）
4. 验证 discovery：

```powershell
Invoke-WebRequest http://127.0.0.1:1411/.well-known/openid-configuration
```

## 3. B1：auth-gateway OIDC 客户端

新增 `portal/auth_oidc.py`（或扩展 `auth_gateway.py`），保持密码桥并存：

```text
GET  /api/auth/oidc/start     → 生成 state/nonce/PKCE，302 Pocket ID authorize
GET  /api/auth/oidc/callback  → 校验 state/nonce/code，换 token，验证 id_token
POST /api/auth/oidc/logout    → 清本地 session（可选 RP-initiated logout）
```

Feature flag：

```text
AUTH_OIDC_MODE=off | shadow | on
```

- `off`：现密码桥；
- `shadow`：OIDC 可登录但不替换入口（验证用）；
- `on`：登录页只显示 OIDC；密码桥保留在后台回退一个周期。

实现要求：

- Authorization Code + PKCE(S256)、state、nonce；
- 校验 `iss/aud/exp/iat/nonce/签名`（JWKS，缓存 + kid 轮换）；
- `id_token` 只用于验证身份；access token 不下发浏览器；
- SessionContext 的 `idp` 字段改为 `pocket_id`；`amr` 至少含 `passkey`；
- Identity 注册表加 `idp_sub/idp_issuer`；老田在 Pocket ID 注册后填真实 sub；
- 未映射 sub → 403，不自动建身份；
- 不改变只读 BFF 的 `zero_bff` 智策服务账号；
- 失败回退密码桥（flag off），不能自动降级绕过。

## 4. B2：切换与验收

1. 老田在 Pocket ID 注册 passkey；
2. 取其 `sub`，更新 `identity_registry.json` 的 `idp_sub`，映射到 `zero_bound:owner`；
3. 设 `AUTH_OIDC_MODE=shadow`，用 OIDC 登录但入口仍密码；
4. 验证 `/api/bff/identity/me` 返回 `idp=pocket_id`、`amr=passkey`；
5. 设 `AUTH_OIDC_MODE=on`，登录页只显示“使用 Pocket ID 登录”；
6. 观察 2 周，保留 `AUTH_OIDC_MODE=off` 回退路径。

## 5. 影响面

- B0：新增容器 + 端口 1411 + 数据目录；不影响 8091/8734/现有登录；
- B1：代码 + 测试；flag off 时行为不变；
- B2：登录入口切换；session 仍 HttpOnly cookie；
- 保护页 nginx `auth_request` 不变；
- 不写交易/业务数据。

## 6. 不可逆点

- 创建容器/数据卷本身可删除；
- **加密密钥丢失不可恢复**（备份 vault）；
- 删除 Pocket ID 数据会丢 passkey/客户端配置，需要重建；
- 切换后回退密码桥需保留 Phase A 代码至少 2 周。

## 7. 回滚

```powershell
# 登录入口回退
# 设 AUTH_OIDC_MODE=off 并重启 8091（或保留密码桥 UI）

# 容器回退（保留数据）
docker stop zerobound-pocket-id
docker rm zerobound-pocket-id

# 端口登记回退
# revert check_ports.py 1411 行
```

数据目录/vault 保留，不删除；需要时同镜像/同密钥重启。

## 8. 验收

B0：

```powershell
docker ps --filter name=zerobound-pocket-id
Invoke-WebRequest http://127.0.0.1:1411/.well-known/openid-configuration
Invoke-WebRequest http://127.0.0.1:1411
```

B1/B2：

- OIDC 登录成功；state/nonce 错误被拒；PKCE 校验；
- `/api/bff/identity/me` 返回 `idp=pocket_id`；
- 未映射账号 403；
- 密码桥 flag off 后仍可回退；
- 5 个保护页登录态正常；
- 无 token 进入 localStorage/URL/日志。

## 9. 五要素摘要

- **命令全文**：§2 启动命令 + §4 切换步骤；
- **影响面**：新增 1411 容器；登录入口切换；不碰交易；
- **不可逆点**：仅加密密钥丢失/数据删除；容器可回滚；
- **替代**：继续 Phase A 密码桥 / Zitadel / 无 SSO；
- **回滚**：§7，保留数据与密码桥代码。

## 10. 审批请求

需老田明确批准：

1. 新增 `zerobound-pocket-id` 容器，绑定 `127.0.0.1:1411`；
2. 使用 vault 内 `C:\aios\_vault\pocket-id` 数据/密钥；
3. 先只做 B0（部署+discovery 验证），不切换登录；
4. B1/B2 另行确认。

批准后我只执行 B0，不直接替换密码桥。

## 11. 参考

- [Pocket ID Installation](https://pocket-id.org/docs/setup/installation)
- [Environment variables](https://pocket-id.org/docs/configuration/environment-variables)
- [Connect an app / OIDC client](https://pocket-id.org/docs/setup/connect-an-app)
- [OIDC client authentication](https://pocket-id.org/docs/guides/oidc-client-authentication)
- [RFC 8693 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
