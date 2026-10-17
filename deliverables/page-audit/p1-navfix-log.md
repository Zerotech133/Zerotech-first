# P1 动线修复执行日志（2026-10-17 · 会话 WorkBuddy/software-p1-navfix）

依据：`deliverables/page-audit/portal-audit.md` D 节方案。工作目录：`C:/项目/老田的AI助手`。
撞车防护遵守：动手前核 `C:/Temp/kg_sync_task.log` 无进行中 portal 操作；所有编辑基于文件最新内容增量修改；未动并行会话已有的 /approvals 导航项与 /flow。

---

## 任务 1：org API 反代收编（修 404）✅

**改动**：`portal/nginx.conf`（/legion-api 块之后新增）
- `location ^~ /api/org` → 反代 127.0.0.1:8093（org_registry_server）
- `location ^~ /api/agent/knowledge` → 同上
- 两处均带 `proxy_set_header X-Portal-Proxy "1"` / `X-Real-IP $remote_addr`（location 级显式重声明，保持 P0-1b 语义），读超时 15s
- `^~` 长前缀优先于普通前缀 `location /api/`，其余 /api/* 不受影响

**验证**：
- `nginx -t` 通过，`nginx -s reload` 成功
- `curl http://127.0.0.1:8080/api/org?caller=nav` → **200**，body 为 agent_ide bundle JSON（36 agents / 13 departments）
- `curl http://127.0.0.1:8080/api/agent/knowledge?id=1` → **200**，`{"ok": true, "agent_id": "1", "items": []}`
- 回归：`/api/health` 200、`/api/overview` 200（未误伤既有 /api/*）

**未动**：`location = /org` SSR 旧版路由（新旧版并存属产品拍板项，本次只修数据断链）。

## 任务 2：legion.html 挂导航 ✅

**改动**：
1. `portal/nginx.conf`：`location = /legion { try_files /legion.html =404; add_header Cache-Control "no-cache"; }`（放在 /academy 之后；用 `=` 精确匹配防 `^~` 劫持 /legion-api）
2. `portal/api_common.py` DOMAIN_NAV_GROUPS "dev" 组 None 子组：/dev 之后新增 `("🪖 军团作战", "/legion", "legion")`
3. `tools/export_nav.py` 重跑 → nav.js 5 域 / 75 入口，grep 确认含 legion
4. `portal/index.html` FAV_GROUPS「💻 开发与学院 → 开发」组新增小卡片 `['🪖', '军团作战', '/legion', ...]`（index 域卡是整卡单锚点无法内嵌链接，选了数据驱动的 FAV_GROUPS 加入口，/hall 渲染验证含"军团作战"）

**验证**：`/legion` → **200**，`/legion-api/` → **200**（数据面不受影响）

## 任务 3：半孤儿页挂导航 ✅

**改动**：`portal/api_common.py` DOMAIN_NAV_GROUPS
- "dev" 组：`("🏗 开发指挥台", "/devops", "devops")`
- "ops" 组：`("🤖 审计 AI", "/audit-ai", "audit-ai")`（/audit 之后）
- "quant" 组"🧰 研究与数据"：`("🧾 逐日回测账本", "/btview", "btview")`、`("🧪 回测实验台 v2", "/bt2view", "bt2view")`、`("📊 列对账", "/col-audit", "col-audit")`
- PAGE_SECTION 同步补 7 键（legion/devops/audit-ai/btview/bt2view/col-audit/cowagent/zhice 归域）
- **追加**：nginx 补 `location = /col-audit` → 8091（实测该页不匹配 /bt 前缀，此前无 nginx 路由落静态 404；/btview /bt2view 命中既有 `^~ /bt` 无需加）
- 重跑 export_nav.py → grep 全部命中

**验证**：/devops **200** / /audit-ai **200** / /btview **200** / /bt2view **200** / /col-audit **200**

## 任务 4：硬链治理（CowAgent/智策）⚠️ 一收编一回滚

**CowAgent(9899) 收编 ✅**：
- nginx：`location = /cowagent` 302 + `location ^~ /cowagent/` 剥前缀反代 9899（判例同 /push），读超时 35s；显式 `proxy_set_header Host 127.0.0.1:9899`（防上游 Host 白名单 403，实测判例）
- api_common.py：`http://127.0.0.1:9899` → `("/cowagent/", "cowagent")`
- 验证：`/cowagent` → **302**（重定向正常）、`/cowagent/` → **502**。502 系上游自身：直连 `curl http://127.0.0.1:9899/` 同为 502（CowAgent 服务当前不在线），路由链路已通，符合"上游不在线则 502 也算路由通"判据。

**zhice(5175) 试反代后回滚 ❌→按预案处理**：
- 过程：反代后先遇 Vite `allowedHosts` 403（Host=host.docker.internal 被拒），补 `proxy_set_header Host 127.0.0.1:5175` 后 200；但页面 HTML 引用**绝对路径** `/@vite/client`、`/@react-refresh`、`/src/main.tsx`——浏览器经 8080/zhice/ 加载时这些打到 8080 根（实测 404）→ SPA 白屏。正是任务预警的绝对路径问题。
- 回滚：nginx 两块 /zhice location 已删；api_common.py 恢复 `("🧠 智策 OS", "http://127.0.0.1:5175", "zhice")` 直链（附注释记录判例）。本机用户为主，硬链不算 bug。
- 留档：若日后 zhice 配 `base: '/zhice/'` 可重新收编。

## 收尾

- nav.js 终态重导出（含全部新增 7 项 + cowagent 相对路径 + zhice 直链），8080 实测服务版含 legion
- 15:42 后最终扫描统一导航：77 个路径全部可达；唯一 502 为 `/cowagent/`，与上游 CowAgent 当前离线一致；此前瞬时失败的 `/bt-chanlun-pool`、`/kg/*` 重试均为 200。
- 全量回归：`/` 200 `/hall` 200 `/legion` 200 `/legion-api/` 200 `/api/org` 200 `/api/agent/knowledge` 200 `/btview` 200 `/bt2view` 200 `/col-audit` 200 `/devops` 200 `/audit-ai` 200 `/cowagent/` 502(上游离线) `/api/health` 200 `/api/overview` 200
- `C:/Temp/kg_sync_task.log` 已补记
- nginx reload 共 4 次，每次均先 `nginx -t` 通过

## 遗留（不属本次范围，如实记录）

- `/approvals` 初始经 8080 → 404：portal_api.py 源码已有路由，但 nginx 缺少精确反代入口；已补 `location = /approvals` → 8091，nginx -t/reload 通过，现已 200。
- `/org` 新旧版切换已另见 `org-cutover-validation.md`，当前 8080 新版稳定 200。
- /org 新旧版并存（静态 org.html vs SSR /org）属产品拍板项，未处理。
