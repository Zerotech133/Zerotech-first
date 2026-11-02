# data-warehouse Q2：登录页验收 + SLO v1

- 日期：2026-11-03｜状态：**自动证据齐；登录后页面验收 PENDING-LOGIN**
- 结论：API/路由/SLO 自动通过；**未完成登录态页面验收，不标 Q2 PASS**

## 一、自动证据

| 项 | 结果 |
|---|---|
| `GET 8091 /api/dw/stats` | **200** |
| `GET 8734 /dw`（未登录） | **302**（鉴权跳转，路由存在） |
| `GET 8734 /dataops`（未登录） | **302** |
| API p95 | **5.5ms**（<800ms） |
| 页面文件 | `portal/dw.html`、`portal/dataops.html` 存在 |
| SLO | API PASS；页面登录验收 PENDING |

## 二、登录后验收（待 A0/人工）

有会话 cookie 时执行：

```powershell
# 1) 浏览器登录 8734 → DevTools → Application → Cookies → 复制 zb_session 的值
# 2) 写入一次性文件（不要发到聊天/不要提交）：
Set-Content -Path C:\Temp\zb_session.txt -Value "zb_session=<value>" -Encoding ascii
# 3) 验收：
python -X utf8 tools/dw_q2_probe.py --cookie-file C:\Temp\zb_session.txt
```

期望：
- `/dw`、`/dataops` → **200**；
- 页面含数据仓统计/列表内容；
- 无 JS 报错、无 401 循环；
- 通过后 `auth_state=PASS`，才可标 Q2 PASS。
- 验收后立即删除 `C:\Temp\zb_session.txt`。

无 cookie 时工具输出 **PENDING-LOGIN**，不伪造通过。

## 三、Q2 缺口

- 登录态页面验收（人工）；
- 回滚预案（数据仓页面/API 回滚）与 owner 签字；
- 契约/数据口径验收（与 W1 条件项联动）。

## 四、证据

- 工具：`tools/dw_q2_probe.py`
- 自动证据包：`docs/q2/data-warehouse_Q2_evidence_v1.md`
- 命令：`python -X utf8 tools/dw_q2_probe.py`
