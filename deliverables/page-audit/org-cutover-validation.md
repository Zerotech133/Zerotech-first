# org.html 新版收编验收记录

日期：2026-10-17

## 变更

- `portal/nginx.conf`：`/org` 改为静态直出 `org.html`，不再转发旧 8091 SSR；`/api/org` 与 `/api/agent/knowledge` 反代至 8093。
- `portal/portal_api.py`：移除 `/org` 旧 SSR 路由注册。
- `portal/api_misc.py`：保留历史 `_org_page` 实现但明确标注已被新版替代，未扩大删除范围。
- `portal/api_common.py`：组织架构导航去除“🚧”标记。
- `portal/static/nav.js`：由统一导航源重新导出（5 域 / 75 入口）。

## 验证

- Python `py_compile`：`portal_api.py`、`api_common.py` 通过。
- `http://127.0.0.1:8080/org`：HTTP 200，返回静态 `org.html`。
- `http://127.0.0.1:8080/api/org?caller=laotian@org-chart`：HTTP 200，JSON 数据正常。
- `http://127.0.0.1:8080/api/agent/knowledge?id=chief`：HTTP 200，JSON 数据正常。
- 8080 入口已完成新版收编；nginx 配置已执行 `nginx -t` 并 reload 成功。
- 复核确认：8080 `/org` 返回新版（约 23KB，包含新版 fetch）。
- 已通过 `PORTAL_API_RESTART.flag` + 计划任务完成 8091 受控重启；哨兵文件已消费。
- 重启后 8091 对 `/org`、`/api/org`、`/api/agent/knowledge` 均返回 404，说明旧 SSR 路由已从运行态移除，统一入口由 8080 nginx 提供。
- 8080 数据接口再次验证：`/api/org` 返回约 232KB JSON，`/api/agent/knowledge` 返回 200。
- 最终语法检查：`portal_api.py`、`api_misc.py`、`api_common.py` 全部通过。

## 未扩大范围项

未删除 `api_misc.py` 中历史 `_org_page` 函数，以避免与受保护旧进程或其他内部调用产生非必要风险；当前公开统一入口已不再注册该路由。