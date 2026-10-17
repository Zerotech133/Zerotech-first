# M1-Approval 注册表 UI 化验收报告

日期：2026-10-17 ｜ 状态：**PASS（代码+测试+端点级冒烟全绿；8091 生产生效待重启窗口）** ｜ 回归：agent_platform 全仓 **104/104 OK**

## 一、交付物

| 检查项 | 状态 | 说明 |
|---|---|---|
| `agent_platform/adapters/approvals_registry.py` | 新增 | 只读注册表数据面：ro 直读账本 + 白名单过滤（target_type/target_id/decided_by）+ by_domain/by_decision/by_target 聚合 + 首末凭据时间；CLI `--root/--output` |
| `portal/api_approvals.py` | 新增 | 只读 mixin：`GET /api/platform/approvals`，参数白名单，非法参数 400 |
| `portal/portal_api.py`（3 处挂载） | 已修改 | `import api_approvals`（模块名绑定判例）→ 继承链追加 → do_GET 加分支 |
| `portal/approvals.html` | 新增 | 注册表页（tokens.css 家族）：统计卡 4 枚（总数/域分布/approved/首末凭据）+ target_type 下拉 + 关键词过滤（target_id/decided_by/reason/幂等键/trace）+ 凭据表格（时间/目标/from→to/决定标签/操作者/理由/幂等键）+ 库缺失/加载失败显式报错条 |
| `agent_platform/tests/test_approvals_registry.py` | 新增 | 9 tests：ro 直读不触发建表/过滤/limit 截断/排序/非法参数/库缺失 fail-visible/只读 mtime 断言/真仓冒烟 |
| `agent_platform/smoke_platform_endpoints.py`（扩展） | 已修改 | 8092 冒烟扩注册表端点：数据 + 400 校验 |

## 二、设计判例

- **只读 ro 直读，不实例化写侧 ApprovalStore**：`engine/approvals.py` 的 `__init__` 含 `CREATE TABLE IF NOT EXISTS` 写路径——注册表数据面用 `sqlite3 mode=ro` 直读，守 M0 只读适配器纪律（判例：**写侧引擎类不可被只读面复用实例化**）。
- **语义不越权**：数据面原样输出 from→to/decision/reason，不做「跳级/回退」语义判定（那是 UI/审计层职责）。
- **挂载路径继续错开**：`/api/platform/*`（platform 数据面家族）与 `/api/unified/*`（聚合投影家族）各归其位。
- **UI 容错对齐 8091 死结**：页面加载失败显式提示「8091 未重启生效前 404 属预期」。

## 三、验证

- 单测 9/9；全仓回归 **104/104**（95 → 104：+注册表 9）。
- 8092 端点级冒烟：`total=2 / by_domain={workitem:2} / errors=[]`；newest=`2026-10-17T04:54:02 gzh:2026-10-17-22 reviewed→draft approved`（M1① 打回凭据精确命中）；非法 limit 400。
- 生产生效：与 ②③ 同受 8091 热更新死结约束（K234 幂等锁 + S4U 杀不动），重启窗口后 `approvals.html` 即可用（nginx `/api/` → 8091 反代已就绪）。

## 四、M1 进度

```
GZH✅ Academy✅ 门户读统一投影✅ 事件回放查询层✅ Approval 注册表 UI✅
剩：其余四域接线（股票投研/软件开发/路演PPT/个人协同）
```

## 五、遗留

- 8091 重启窗口：②③+注册表三端点与 approvals.html 同时点亮（人工提权杀 PID 25408 → `start_portal_api.ps1`，或等进程自然死亡）。
- nav 导航入口：approvals.html 未加入 nav.js 导航清单（避免并行会话动 nav.js 冲突，留给用户或下一轮统一挂）。

## 六、并行协调补齐记录（另一工作线，13:25-13:35）

第五节 nav 缺口已由并行线补齐，另发现并补上**页面路由缺口**：

| 补齐项 | 说明 | 验证 |
|---|---|---|
| `/approvals` 页面路由 | portal_api.py do_GET 补 `elif path == "/approvals"` → `_html_file(approvals.html)`（原报告遗漏：API 分支已挂但页面路由缺，浏览器访问 404） | 临时端口 GET /approvals → 200 |
| nav 导航入口 | api_common.py ops 段 +`("✅ 审批注册表","/approvals","approvals")`，重跑 export_nav.py（5 域/69 入口） | nav.js 含 approvals ✓ |
| 页面挂 nav.js | approvals.html head 补 `<script src="/static/nav.js" defer>`（对齐 flow/audit 页） | 200 且页面含引用 |

临时端口（18091，K234 判例不动生产 8091）四路验收：/approvals 200、/api/platform/approvals 200（total=2）、/api/unified/projection 200（双域 PASS）、/static/nav.js 200（approvals 入口 ✓）。全仓回归 98/98 OK（与本报告 104 差异=并行线随后新增测试，两数均为绿）。

另：并行线曾起草重复端点 /api/unified/approvals，发现撞车后已完整撤销（api_unified.py docstring 留分工声明指向 /api/platform/approvals）。
