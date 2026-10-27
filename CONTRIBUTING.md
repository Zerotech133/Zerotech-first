# 贡献指南（CONTRIBUTING.md）

> 本仓库是单机个人 AI 平台，但工程纪律按团队标准执行：**一单一事、worktree 隔离、评审门合并、L2 五要素**。
> 开工前必读：`AGENTS.md`（开发宪法）、`ARCHITECTURE.md`、`docs/README.md`。
> 约定参考开源实践：AutoGen/Lean 的"每个 PR 必须有测试 + 本地 CI 检查"、Diátaxis 文档分类。

## 一、开发流程（三步）

```powershell
# 1) 领单：一单一事（TID 写在提交信息）
$env:DEVTOOL_AGENT="你的席位"; $env:DEVTOOL_IDE="你的 IDE"; $env:DEVTOOL_MODEL="你的模型"
python tools/devtask.py new <TID> "一句话目标" --project portal --acceptance "<验收命令>"

# 2) 在独立 worktree 干活（禁止 master 直改）
cd C:\项目\老田的AI助手\.worktrees\<TID>

# 3) 自测 → 提交（带 TID）→ 过评审门合并
python -m pytest <受影响测试> -q -p no:cacheprovider
git add -A; git commit -m "<TID> 做了什么 + 验收证据"
cd C:\项目\老田的AI助手; python tools/devtask.py merge <TID>
```

- 合并门自动跑：端口注册（`check_ports --strict`）、Mixin 完整性、导航统一、明文凭据扫描、L2 危险操作门。
- 提交信息必须含工单号；`DEVTOOL_AGENT` 缺失会标 `attribution_missing`（可统计，不静默）。

## 二、测试要求

- 改行为 → 必须带测试；修 bug → 测试必须能复现 bug（参考 Lean 贡献指南）。
- 快速回归：`python -m pytest portal/test_m_links.py -q -p no:cacheprovider`（示例）；
- 套件基线/命令见 `docs/testing.md`；worktree 无数据根导致的假红需在**主仓**复跑确认。
- 提交前至少：受影响文件 `py_compile` + 定向 pytest。

## 三、L2 与红线

- L2 = 重启/停服、改计划任务、动凭据区/备份链、宿主机级配置、上线发布 → **先出五要素包（命令全文/影响面/不可逆点/替代方案/回滚预案），老田批准后执行**；沉默≠同意。
- 红线：删除进回收站；长跑输出落文件；补丁先断言锚点唯一；杀进程前先辨认线上服务；修复写盘≠生效（必须换血验证）。
- 禁止：在 master 直接开发、无单写码、把 token/密码写进命令历史或文档、绕过冷启动守卫（8091）。

## 四、文档要求

- 架构级决定 → `docs/adr/ADR-NNN-*.md`（模板见 `docs/adr/ADR-001` 风格）；
- 运维操作 → `docs/ops/`（执行后补回执）；
- 交付物/审计 → `C:\项目\deliverables/`，并在 `STATUS.md` 追加摘要；
- 文档分类见 `docs/README.md`；`DOCS_INDEX` 由生成器维护，`--check` 必须 rc=0。

## 五、评审与合并

- 小步提交，一次只做一件事；跨部门治理任务在 desc 标 `路由例外=跨部门治理`。
- 合并后如需清理：`python tools/devtask.py prune --dry-run` 先看，再人工确认 `--apply`。
