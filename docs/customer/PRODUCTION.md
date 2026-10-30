# 客户门户生产化部署清单（P2.4 生产化）

> 现状：代码已支持限流、异步任务、商业化接口网关 token；**HTTPS/域名/nginx 配置属 L2 变更**（需批准后执行）。

## 1. 前置决策（需业务/负责人确认）

| 项 | 建议 | 备注 |
|---|---|---|
| 域名 | `analysis.<domain>`（与主站分离） | 需 DNS 解析 |
| HTTPS | Let's Encrypt（自动续期）或商业证书 | 证书文件权限仅 nginx 可读 |
| 暴露范围 | 公网仅 `/customer.html`、`/landing.html`、`/cases.html`、`/api/customer/*`；`/api/analysis/*` 与 `/api/commercial/*` **不对外** | 白名单式反代 |
| 审批 | L2 五要素（命令全文/影响面/不可逆点/替代/回滚） | 记录工单 note |

## 2. nginx 反代模板

见 `deploy/https/nginx-customer.conf.example`（同源反代到 127.0.0.1:8734，仅放行客户路径）。

## 3. 网关安全

1. **商业化接口** `/api/commercial/*`：必须配置 `COMMERCIAL_ADMIN_TOKEN`（或 `C:\项目\commercial\admin.token`），请求头 `X-Commercial-Token` 校验；
2. **客户接口**：会话 token（HMAC、8h）+ 限流（登录 10/min/IP、试用 5/min/IP、分析 5/min/租户、埋点 120/min/IP）；
3. **分析任务**：异步排队（job 轮询），单租户并发 2，超时 240s；
4. 禁止外网直连 8091；nginx 层再加 IP 白名单/频控（可选）。

## 4. 上线步骤

1. 配置域名解析 → 2. 申请证书 → 3. 部署 nginx 配置（L2 批准） → 4. reload nginx →
5. 配置 `COMMERCIAL_ADMIN_TOKEN` 并重启 8091 → 6. 冒烟：landing/cases/customer 200、trial 签发、登录、异步分析与轮询、商业接口 401/200 →
7. 监控：nginx access log + 8091 日志 + 429/5xx 告警。

## 5. 回滚

- nginx：恢复上一版 conf 并 reload（保留备份）；
- 8091：去掉 admin token 配置即恢复旧行为（仅限内网）；
- 数据：客户/试用租户目录独立，无需回滚数据。

## 6. 待办（上线后）

- 试用表单验证码/频控升级、邮件/飞书跟进自动化；
- 异步任务队列持久化（当前内存 + 租户目录状态），重启会丢未完成任务（可接受：客户可重试）；
- 审计导出页、租户用量看板。
