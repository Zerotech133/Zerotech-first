# CHANGELOG

本文件记录面向运行的显著变更（Keep a Changelog 风格，日期 = Asia/Shanghai）。
格式参考开源实践：版本（或 Unreleased）/ Added / Changed / Fixed / Removed / Security。

## [2026-11-05] — HermesBriefPush 401 修复与 P0 假红收口

### Fixed
- `zerobound-kg/hermes_push.py`：8787 已切 trusted-header 认证，HermesBriefPush 的
  POST/SSE 未带 `X-Portal-User` → HTTP 401；新增 `_auth_headers()` 并用于两类请求，
  新增 3 个测试。09:15 手动补推 `ok=True`，09:16 计划任务 `Last Result=0`。
  关联 zerobound-kg `3d7a6fb`、告警单 `T-20261105-152`。
- `portal/execution_manifest.json`：`HermesWebUI-Next-Guard` 对应的 8788 已退役，
  Windows 计划任务 Disabled 为预期；监控条目改为 `enabled: false`，避免 stale 假红。
  关联 `df0bfa1d3`、告警单 `T-20261105-151`。

## [2026-11-05] — SFT 206 主审 40 / holdout-027 参考修复

### Added
- `research/sft/v4_gated_20261104/holdout_027_reference_fix_v1.json`：记录 027 的
  old/new reference、修复来源、源文件 SHA256 与 `rejudged=false`；原冻结评测文件未改。
- 修复版独立文件：`holdout_eval_027_reference_fixed_v1.jsonl`（206 行）、
  `main40_independent_review_packet_36_reference_fixed_v1.json`、
  `main40_independent_review_36_excl027_v1.json`、
  `manual_review_40_auto_filled_reference_fixed_v1.csv`（40 行）。
- `research/reports/SFT_206_main40_independent_ai_plus4_v1.{md,json,csv}`：
  36 条独立 AI 复核 + 4 条人工修正的主审 40 汇总，含 027 排除语义。
- 行级工作簿：`C:\Users\t\Desktop\206人工抽检_40条_主审_独立AI复核加人工4条_v1.xlsx`
  （027 标 `excluded`，新增“是否计入准确率”列与说明页）。
- `research/reports/SFT_206_base_vs_RAG_excl027_sensitivity_v1.json`：
  206 排除 027 的 n=205 敏感性结果（不替换原报告）。

### Fixed
- `holdout-027` 的 reference 是跨行串行错配；已按
  `知识库/20_清洗/视频号学习_8博主/clean/20261103_指标公式入门(1).md`
  第 265–271 行“20260912_2003 均线粘合”同源原文替换。
- 027 标记 `EXCLUDED_REFERENCE_MISMATCH`，不重新判定 base/RAG，
  **不计入准确率**；主审 40 分母由 40 改为 39，36 条由 36 改为 35。
- 其余 39 条标签、reference 均未改；原 `holdout_eval.jsonl` 与 206 预测未改。

### Changed
- 主审 40 修正后：36 条 AI 复核 base 20/35、RAG 27/35；4 条人工修正
  base 0/4、RAG 2/4；40 条合并 base 20/39、RAG 29/39（027 排除）。
- `SFT_206_base_vs_RAG_v1.md` 顶部加 027 历史口径提示；AI-draft 旧汇总
  加“含无效 027、不可作为准确率”说明。
- 变更仅限评测/复核数据与报告，不涉及线上服务；未切 canonical、未上线、
  未进影子盘，主审 40 仍非完整人工审阅。

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
