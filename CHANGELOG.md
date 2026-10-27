# CHANGELOG

本文件记录面向运行的显著变更（Keep a Changelog 风格，日期 = Asia/Shanghai）。
格式参考开源实践：版本（或 Unreleased）/ Added / Changed / Fixed / Removed / Security。

## [Unreleased] — 2026-10-27

### Added
- 文档体系落地：`docs/README.md`（Diátaxis 分类）、`ARCHITECTURE.md`（架构权威简图）、
  `CONTRIBUTING.md`、`SECURITY.md`、`docs/testing.md`、`docs/ci/` 草案；
  架构决策 `docs/adr/ADR-004`（文档体系）、`ADR-005`（Agent 可观测 OTel GenAI）。
- C6 指标日报工具 `tools/c6_daily_kpi.py`（每日 09:30 提醒）；5 张 dev 派工单（X2/X3/B8/B3/B4）。
- L2 包：X5 Tushare token 轮换执行包；C 阶段三包合并待批单。
- **D2 测试门禁**：`tools/ruff_ratchet.py`（F/E9 棘轮 + 基线）、`tools/night_test_baseline.py`
  （分套件夜跑基线/新增失败 diff）；复用既有 `tools/test_gate.ps1`（并行单交付）。
- **D3 Agent 可观测**：`agent_platform/observability/genai.py`（`gen_ai.*` 映射 + 默认关 OTel span），
  `engine/llm.py::make_llm` 单点包装；`docs/specs/2026-10-27_GenAI可观测字段映射_v1.md`。
- **D4 MCP 安全自查**：`tools/mcp_security_check.py` + `docs/specs/2026-10-27_MCP安全自查_v1.md`
  （read-only / token 仅 env / URL 无 token / scope；0 error 1 warn）。
- **D5/D6 构建与发布**：`pyproject.toml`（ruff 配置 + dev 工具组）、`uv.lock`、`requirements-dev.txt`、
  `docs/standards/2026-10-27_版本与发布流程_v1.md`。

### Changed
- WORKPATH Phase 1/2/3：work 根去硬编码；analysis 数据迁 `C:\项目\analysis_work`；
  飞书 bot 运行态迁 `C:\项目\dsh_runtime`（env `ANALYSIS_WORK` / `DSH_REMOTE_DIR` 可覆盖）。
- M1 `models` junction 重指 `C:\项目\dsh_runtime\models`（faster-whisper-small 463.7MB，语音链路恢复）。
- 8091 重启（并行会话已完成，诸葛 P1-1/P1-2 + PROJAUDIT 修复生效）。

### Fixed
- bot 看护父子进程误判导致的重启风暴（`run_bot_watchdog_loop.ps1`，含 `-SelfTest`）。
- `test_m_links` 下钻按钮断言漂移（跟随 H5 拍板）。
- audit3：`vector.py` F821 / `serve.py` os 未导入 / codex noqa / 无用导入。
- ENVTEST 6 个测试环境依赖；quant 收集错误 12→0；8796 LiteLLM 上游 500。

### Security
- L1 凭据保险库（SOPS/age）完成：`_vault/secrets.enc` 32/32 verify、MCP token 移出明文配置；
  token 轮换待老田外部重置（当前仍为旧值，见 X5 包）。

## 约定

- 每次合并后由执行人追加一条（日期 + 变更摘要 + 关联工单/回执路径）；
- 版本号方案：单机个人平台，暂用日期段；有对外发布时再切 SemVer（参考 AutoGen 版本齐步实践）。
