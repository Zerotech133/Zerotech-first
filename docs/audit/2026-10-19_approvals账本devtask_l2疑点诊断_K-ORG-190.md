# 审批账本 devtask_l2 疑点诊断（K-ORG-190）

- 日期：2026-10-19 ｜ 诊断人：dev-p2-approvals ｜ 性质：只读诊断，未改任何文件、未动账本
- 疑点：`agent_platform/tests/test_approvals_registry.py::test_real_repo_smoke` 全量测试唯一红——真实账本最新凭据 idempotency_key 前缀 `devtask_l2:` 不在测试域白名单。
- 结论速览：**`devtask_l2` 不是平台接线域，是 K-STOCK-416 L2 豁免通道的审批留痕命名空间（合法的审计凭据、非法的"域"）；该条记录是 L2 门功能落地前的真账本冒烟自检产物（自检应写隔离账本，属测试污染，但已明确自报"自检"字样，非隐瞒式污染）。**

---

## 一、事实链

### 1. 账本现状（只读 sqlite 查询，`file:...?mode=ro`）

- 总条数：**32**。前缀分布：`gzh` 24、`devtask` 4、`invest` 2、`skill` 1、`devtask_l2` **1**。
- `devtask_l2` 全账本**仅 1 条**，即涉事凭据：

| 字段 | 值 |
|---|---|
| approval_id | `1f9b863b6b5f4731b5da6009d7fc49a7` |
| idempotency_key | `devtask_l2:K-STOCK-416-SELFTEST:7198502cb77a8960befdd50c337fac13b0ade637` |
| target_type | `devtask_l2_exemption` |
| target_id | `K-STOCK-416-SELFTEST` |
| decision / 状态迁移 | `approved`，review→merged |
| decided_by | **`selftest-l2`** |
| reason | `自检：命令全文=merge selftest；影响面=仅测试分支；不可逆点=无；替代=已试过直接改；回滚=删分支` |
| decided_at | `2026-10-18T22:39:04+00:00`（北京 2026-10-19 06:39:04） |
| trace_id | `eacb25a75e474607a8980076ecb4710a` |

- `content_hash` = `7198502c` 真实存在于仓对象库：提交 *"test: comment-only v2"*（06:38:35 +0800），当前**不属于任何现存分支**（自检分支已随 drop 清场删除）。

### 2. 写入源定位

- 写入函数：`tools/devtask.py:387` `_l2_exempt()`——K-STOCK-416 `--l2-ack` 人工豁免通道，构造 `Approval(target_type="devtask_l2_exemption", idempotency_key="devtask_l2:<TID>:<head>", content_hash=head)` 后经 `store.record()` 落 `agent_platform/data/approvals/approvals.db`。调用点：`cmd_merge()`（tools/devtask.py:477）在 L2 门（`tools/_l2_guard.py`）returncode==2 且带 `--l2-ack` 时触发。
- 全仓 `git grep K-STOCK-416-SELFTEST` **零命中**——无任何脚本/测试硬编码此单号，排除"测试代码写真库"。结合账本 reason 自报"自检"与时间线：
  - 06:38:35 自检分支 `task/K-STOCK-416-SELFTEST` 末次提交 7198502c（comment-only）
  - 06:39:04 豁免审批单写入真账本（decided_by=`selftest-l2`，AGENT_NAME 环境变量）
  - 06:40:01 提交 `ecc65e8d` "feat(devtask): K-STOCK-416 L2危险操作评审门……人工豁免走 approvals.db 五要素审批留痕"
- **判定：是 L2 门落地会话在提交前对 `merge --l2-ack` 全链路做的真账本冒烟自检**（临时注入 SELFTEST 工单 → 触发豁免通道 → 验证落痕 → drop 工单删分支）。工单/分支已清，但审批单按幂等设计永久留痕，遂成账本孤条。`.worktrees/tasks.json` 中已无 SELFTEST 条目佐证清场。

### 3. 域合法性判定（疑点①）

- 平台已接线七域（M1）：`gzh/academy/invest/dev/pitch/personal/devtask`——见 `agent_platform/adapters/unified_projection.py:283`（`domains_wired`）与 `agent_platform/domains/devtask_wiring.py:74`（`DOMAIN="devtask"`）。域注册/白名单无 `devtask_l2`。
- `devtask_l2` 实为 `_l2_exempt()` 发明的**幂等键命名空间**（tools/devtask.py:411），语义是"L2 豁免审批留痕"，不是业务域。测试白名单（test_approvals_registry.py:142）按 `idempotency_key` 前缀核对七域，`devtask_l2:` 不匹配 `devtask:`（带冒号前缀匹配），故红。
- 判定：**`devtask_l2` 不是合法接线域，但作为审批留痕凭据其存在本身符合 K-STOCK-416 规范设计**（"豁免走 approvals.db 五要素审批留痕"）。问题不在账本记了它，而在①自检不该写真账本、②测试白名单/凭据命名约定未同步豁免通道。

### 4. 测试设计意图与账本现实的差距

- `test_real_repo_smoke`（test_approvals_registry.py:133-146）意图：真实账本冒烟，最新凭据**只需带任意已接线域前缀**，注释明言"域随真实批准动态变化，不硬编码"。
- 差距：测试假设"账本只会被七域业务写入"，未预见 devtask L2 豁免通道会以 `devtask_l2:` 前缀写凭据、也未预见人/Agent 会用真账本做自检。测试对"最新一条"的域断言过于脆弱——任何新留痕通道或一次真账本自检都会打破。

---

## 二、三个候选修法对比

| 方案 | 内容 | 优点 | 缺点 |
|---|---|---|---|
| A. 测试白名单补域 | 白名单加 `devtask_l2:`（或改为"前缀匹配已知留痕通道集"） | 一行改动；承认豁免留痕是合法凭据，账本不动 | 治标：下次新增留痕通道/自检又红；白名单与凭据命名约定散落两处易再漂移 |
| B. selftest 改写隔离测试账本 | `_l2_exempt()`/冒烟流程支持 `APPROVALS_DB` 环境变量或 `--dryrun` 指向临时库；规范明令自检禁写真账本 | 治本：杜绝今后自检污染；留痕通道仍可全链路验证 | 需改 `devtask.py` 与规范文档；**对本次已污染条目无能为力** |
| C. 账本清理污染条目 | 删除 approval_id `1f9b863b…` | 账本纯净、测试即绿 | **违背审批账本 append-only 不可篡改原则**——豁免留痕本身就是为"审计翻旧账"设计的，删痕等于销毁审计证据；且该条已自报"自检"非脏数据，有存档价值 |

## 三、推荐

**A + B 组合，拒绝 C**：白名单补 `devtask_l2:`（附注释说明其为 L2 豁免留痕命名空间、非接线域）让冒烟回归反映真实通道全集；同时给留痕通道加隔离账本开关（方案 B）并在 K-STOCK-416 规范补一条"自检/冒烟一律写隔离账本，禁写真账本"。既有 SELFTEST 条目**保留不删**——它是 L2 门首例真账本落痕的存证，reason 字段已自我标识，建议在下一条相关审计文档中标记为"已知自检痕迹"即可。

> 修法待老田拍板，本诊断不执行任何修改。
