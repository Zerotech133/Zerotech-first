# L2 五要素待批：5 页 nginx auth_request 真保护

- 日期：2026-10-30
- 工单：`WO-LEGION-AUTHREQUEST-L2-20261030`
- 状态：**已批准并执行（2026-10-30 12:24）**；上线记录见 §7。
- 前置：Phase A auth-gateway 已上线；`/api/auth/guard` 已验证 401/200；nginx 已带 `--with-http_auth_request_module`（实测 nginx 1.31.7）

## 1. 目标

对 5 个 OS/管理页面启用**服务端**登录保护；未登录 → `/login?next=<page>`，登录后回跳。其余页面保持现状，避免全站硬锁。

保护范围：

| 页面 | 现有 nginx 位置 | 类型 |
|---|---|---|
| `/os-entry` | L405 精确 location | 静态 |
| `/os-board` | L356 精确 location | 静态 |
| `/devops` | L273 精确 location | 反代 8091 |
| `/model_admin` | L411 精确 location | 静态 |
| `/approvals` | L599 精确 location | 反代 8091 |

不保护：`/login`、`/api/**`、`/static/**`、`/`、`/quant`、`/intel` 及其余页面。

## 2. 精确配置（待插入 `portal/nginx.conf`）

在 server 内、任一 protected location 之前加入公共块：

```nginx
    # L2 页面真保护（Phase A auth-gateway）
    location = /_auth_guard {
        internal;
        set $h host.docker.internal;
        proxy_pass http://$h:8091/api/auth/guard;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 3s;
        proxy_read_timeout 10s;
    }
    location @login_required {
        return 302 /login?next=$uri;
    }
```

然后 5 个 location 各加 2 行（示例）：

```nginx
    location = /os-entry {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        try_files /os_entry_v9_prod.html =404;
        add_header Cache-Control "no-cache";
    }
```

```nginx
    location = /os-board {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        try_files /os.html =404;
        add_header Cache-Control "no-cache";
    }
```

```nginx
    location = /devops {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        set $h host.docker.internal;
        proxy_pass http://$h:8091;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
    }
```

```nginx
    location = /model_admin {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        try_files /model_admin.html =404;
        add_header Cache-Control "no-cache";
    }
```

```nginx
    location = /approvals {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        set $h host.docker.internal;
        proxy_pass http://$h:8091;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
    }
```

> `next=$uri` 不带查询串；5 个目标页当前无必要 query。若以后需要保留 query，再单独做 URL 编码 map。

## 3. 五要素

### 3.1 命令全文

```powershell
# 1) 备份
Copy-Item C:\项目\老田的AI助手\portal\nginx.conf C:\Temp\nginx.conf.pre-authrequest.bak

# 2) 编辑：插入公共块 + 5 个 location 各加 2 行（见 §2）

# 3) 校验（不通过不 reload）
docker exec ai-portal nginx -t

# 4) 生效
docker exec ai-portal nginx -s reload

# 5) 验证（见 §5）
```

### 3.2 影响面

- 未登录访问 5 页 → 302 `/login?next=...`；
- 已登录（Phase A session cookie）→ 正常页面；
- `/login`、`/api/**`、`/static/**`、其余页面**不变**；
- 5 页在 8091 auth-gateway 不可达时可能 500（auth_request 失败），不会绕过；
- 不写业务数据、不重启 8091、不改端口。

### 3.3 不可逆点

- 无数据变更；nginx 配置可完整回滚；
- reload 不重启容器，现有连接平滑；
- 若配置错误，`nginx -t` 会阻止 reload。

### 3.4 替代方案与已尝试

- 已上线客户端软保护（`auth_guard.js` 横幅/提示），可绕过，不是安全边界；
- 可只保护 `/os-entry` 单页（更低风险）；
- 可继续保持全站游客只读（零风险）。

### 3.5 回滚预案

```powershell
Copy-Item C:\Temp\nginx.conf.pre-authrequest.bak C:\项目\老田的AI助手\portal\nginx.conf -Force
docker exec ai-portal nginx -t
docker exec ai-portal nginx -s reload
```

回滚后验证：5 页未登录可访问、`/login` 200。

## 4. 审批

- 级别：**L2**
- 批准：老田已明确选择「5 页全保护（推荐）」；
- 执行：2026-10-30 12:24 完成 §3.1；
- 结果：5 页未登录 302，登录后 200；详见 §7。

## 5. 验收

```powershell
# 未登录：应 302 到 /login?next=...
curl.exe -I http://127.0.0.1:8734/os-entry
curl.exe -I http://127.0.0.1:8734/os-board
curl.exe -I http://127.0.0.1:8734/devops
curl.exe -I http://127.0.0.1:8734/model_admin
curl.exe -I http://127.0.0.1:8734/approvals

# /login 必须 200，不能循环
curl.exe -I http://127.0.0.1:8734/login

# 未保护页仍 200
curl.exe -I http://127.0.0.1:8734/quant
curl.exe -I http://127.0.0.1:8734/api/bff/overview

# 登录拿 cookie 后访问 5 页应 200（用服务账号 smoke）
```

浏览器验收：

1. 未登录打开 `/os-board` → `/login?next=/os-board`；
2. 登录成功 → 回 `/os-board`；
3. 登出 → 再开 `/os-board` → 再跳登录；
4. `/quant`、`/intel` 未登录仍可访问。

## 6. 风险

| 风险 | 处理 |
|---|---|
| auth_request 子请求 Cookie 未透传 | 显式 `proxy_set_header Cookie` |
| 登录 302 循环 | `/login` 不在保护列表 |
| 8091 不可达导致 500 | 不绕过；监控 auth-gateway 健康 |
| `next` 丢 query | 5 页当前无 query；后续再补 |
| 配置错误 | `nginx -t` 失败不 reload；备份可回滚 |

## 7. 上线记录（2026-10-30 12:24-12:32）

### 执行

1. 备份配置：`C:\Temp\nginx.conf.pre-authrequest.bak`（来自 `94c4081`，不含 auth_request，含 `/login` 路由）；
2. 合入 5 页 auth_request（commit `a0b08b3e`）；
3. 首次 `nginx -t` **失败**：`unknown directive "﻿#"`——配置文件带 UTF-8 BOM；
4. 热修去 BOM（commit `60800c44`）后 `nginx -t` 通过；
5. reload 后发现未登录 302 为 `http://127.0.0.1/login`（丢 8734）：容器内端口 80 导致绝对跳转；
6. 修复：`location @login_required { absolute_redirect off; ... }`（commit `c6efe3c5`）；
7. 再次 `nginx -t` 通过并 reload。

### 验收结果

```text
未登录：
/os-entry  302 /login?next=/os-entry
/os-board  302 /login?next=/os-board
/devops    302 /login?next=/devops
/model_admin 302 /login?next=/model_admin
/approvals 302 /login?next=/approvals
/login     200
/intel     200
/api/bff/overview 200
```

登录（zero_bff 服务账号）后：

```text
/os-entry /os-board /devops /model_admin /approvals 全部 200
```

浏览器实测：

- 未登录打开 `/os-board` → `/login?next=/os-board`；
- 登录成功回 `/os-board`；
- 顶部横幅显示“已登录 · 零界只读 BFF · VIEWER · 只读”；
- 无 pageerror。

### 回归测试

`portal/tests/test_nginx_auth_request.py`：

- 配置无 BOM；
- `_auth_guard` internal + Cookie 透传；
- `absolute_redirect off` + 相对 `/login?next=$uri`；
- 5 页均有 `auth_request` / `error_page`；
- `/login` 未被保护；
- `/quant`、`/intel`、`/api/` 未被锁。
