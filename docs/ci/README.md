# CI 草案（docs/ci/）

> 本仓库当前是**本机 git 仓库**（无 GitHub 远端），CI 尚未接入。
> 本目录放"可复制的 CI 定义 + 本地门禁说明"，等有远端镜像时把 `github-actions.ci.yml`
> 复制到 `.github/workflows/ci.yml` 即生效；当前以 merge 门 + 计划任务/夜跑代替。

## 文件

- `github-actions.ci.yml`：GitHub Actions 草案（ruff F 检查、compileall、分套件 pytest）；
- 本地门禁：`python tools/devtask.py merge <TID>`（端口/Mixin/导航/凭据/L2 五门）。

## 与开源实践对齐

| 实践 | 来源 | 本仓库落点 |
|---|---|---|
| 每 PR 必带测试 | QuantConnect Lean CONTRIBUTING | `CONTRIBUTING.md` §二；评审门只看门禁，人工 review 必看测试 |
| `uv`/`ruff`/`pytest` 统一 | OpenAI Agents SDK、AutoGen CONTRIBUTING | 本目录草案；依赖 lock/venv 为 P2 工程债（P2-3） |
| 版本/发布流程 | AutoGen CONTRIBUTING（版本齐步、tag 发布） | `CHANGELOG.md` + 后续 release 流程 |
| 安全策略/漏洞报告 | GitHub SECURITY.md 惯例 | 根 `SECURITY.md` |
| 架构决策记录 | ADR（adr.github.io） | `docs/adr/ADR-*.md` |

## 下一步（不阻塞）

1. 确定是否接 GitHub 远端镜像（老田定）；
2. 若接：起草 `.github/workflows/ci.yml`（本目录草案）→ 小范围试跑 → 稳定后设为必需检查；
3. 若不接：把等价检查做成 `tools/test_gate.ps1` + 计划任务夜跑（B3/B4 工单）。
