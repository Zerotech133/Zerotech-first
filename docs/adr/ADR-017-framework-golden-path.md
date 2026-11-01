# ADR-017：唯一开发框架与黄金路径（ZBF v1）

- **状态**：Proposed（待 A0 拍板；与 ADR-015/016 配套）
- **日期**：2026-11-02
- **关联**：ADR-015（内核契约与组合模型）、ADR-016（唯一宿主整合，Accepted）、`docs/architecture/PLATFORM_FRAMEWORK_v1.md`（框架总纲）
- **性质**：制度性决定——**未来所有开发必须基于 ZBF v1**；本 ADR 只定义规则与门禁，不改变任何运行时行为

## 背景

11-02 的整体复盘确认：平台已有大量底座（席位/技能/模型注册表、任务单、信封、扩展 manifest、数据集登记、路由基线、九道质量门），但过去 40 天反复出现"两套栈并行、声明不接执行、事实多源、无组合层、无黄金路径"的问题。社区成熟方案（Backstage 黄金路径、Dify/HA manifest、HA 质量分级、Crossplane/kro 声明式组合、dbt 数据契约、MCP/OTel Agent 协议）表明：平台的核心不是功能数量，而是**铺好的路 + 默认安全 + 可校验的声明 + 分级治理**。

## 决定

1. **采用 ZBF v1 作为平台唯一开发框架**：
   `一套宿主（零界）+ 内核 K0–K8 + 组合模型 C1–C8 + 黄金路径 + 一致性门禁`。
2. **所有未来开发只有三种合法形态**：
   ① 加/减组合（`compositions/**`）；② 替换实现（adapter/manifest 引用）；③ 改内核（必须 ADR）。
   第四种"临时脚本/第二入口/第二认证/第二 DB/第二调度"一律禁止。
3. **黄金路径为唯一路径**：新能力必须走六步法（分类 → 开单 → 清单 → 校验 → 接线 → 合并/观测），模板由 `compositions/_templates/` 提供。
4. **质量分级 Q1–Q4**：Q1 登记、Q2 可运行、Q3 可治理、Q4 可进化；`active` 至少 Q2，对外/关键至少 Q3，平台核心/长期 Q4。
5. **门禁**：
   - `composition validate`（schema + 引用解析 + 冲突 + 质量级别）；
   - `kernel_guard`（内核契约文件改动必须带 ADR 号）；
   - devtask 现有九道门继续执行；组合改动走组合专用 gate，不误触发全量测试；
   - 季度 Kernel Review + 组合账本审计。
6. **禁止**：无 manifest 上线、声明无执行点、复制 `domains/*_wiring.py` 扩工作流、`C:\Temp\*.json` 当契约、共享仓 `clean -fdx/reset --hard`、绕过门禁。
7. **例外流程**：框架不适用 → ADR（问题/替代/回滚）+ A0 批准 + 临时 adapter + 90 天回归；例外进季度评审。
8. **文档同源**：本 ADR 与 `PLATFORM_FRAMEWORK_v1.md`、`platform/kernel_v1.yaml`、`compositions/README.md` 同批维护；框架变更必须同批更新。

## 后果

- **正面**：新能力统一入口；内核改动可审计；组合质量可分级；技术实现可替换；"一套宿主跑 N 个组合"可证明。
- **代价**：短期需要补 P1 工具（composition.py/kernel_guard/模板/resolver）与 P2 解耦；组合清单有少量元数据维护成本。
- **风险**：门禁过严拖慢单人开发（缓解：组合 gate 只跑必要项、模板自动生成）；质量分级被跳过（缓解：`active` 状态必须 Q2，自动检查）。

## 验收

1. P1 后：新组合/改动只改 `compositions/**` + 测试，内核 diff=0（composition gate 证明）；
2. 所有 active 组合质量级别 ≥ Q2，账本可查；
3. 内核改动 100% 带 ADR 号（kernel guard 日志）；
4. 例外 100% 有 ADR + 90 天回归计划；
5. 季度评审输出"新增组合耗时/内核变更数/替换演练/质量分布"。

## 待拍板

1. 是否批准本 ADR（ZBF v1 作为唯一框架）；
2. 质量分级门槛是否按 Q2/Q3/Q4 执行；
3. 例外审批人（建议 A0 + ARCH1 双签）；
4. 模板目录 `compositions/_templates/` 与命名；
5. P1 工具优先顺序（composition.py → kernel_guard → 模板 → resolver）。
