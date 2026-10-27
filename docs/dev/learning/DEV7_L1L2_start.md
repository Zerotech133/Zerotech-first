# DEV7 能力学习 · L1/L2 启动包

- **对象**：DEV7 姜维（后端标准化交付，E1）｜**导师**：DEV1 马钧
- **启动**：2026-10-28｜**L1 截止**：2026-10-29｜**L2 截止**：2026-10-30
- **计划真源**：`agent_platform/config/seat_learning/DEV7.json`（L1/L2 状态=learning）
- **原则**：学完必须留证据（笔记/命令输出/导师确认），不虚报 done。

## L1 岗位与契约（0.5 天）

### 必读
1. `agent_platform/prompts/code_engineer.md` —— 岗位职责/边界；
2. `docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md` —— 谁能派给谁；
3. `2026-10-27_研发部AI协作开发与分派总纲_v1.md` §5 任务卡（验收命令/边界/依赖/署名）。

### 自检题（答对 5/6 视为 L1 过；答题记录交导师）
1. 一张 dev 单的四个必填要素是什么？（owner/验收命令/边界/依赖；署名另计）
2. `--approver` 为什么必须有 ARCH1？什么情况可 DEV1 代行？
3. 合并门有哪些？（Mixin/导航/端口/凭据/L2/署名）
4. 什么情况必须升级 ARCH1？时限多少？（24h 未决→组长；48h→ARCH1）
5. 组员可以直接找 ARCH1 派单吗？（不可以，走组长；紧急后补报）
6. 自审自批的判定是什么？（approver==owner）

### L1 完成证据
- 答题记录（建议 6 题全答，附 1 句"我理解"）→ 交 DEV1 抽查；
- 在 `DEV7.json` 的 L1 `evidence` 填：`docs/dev/learning/DEV7_L1_answers.md`（或工单 note 链接）。

## L2 代码定位与影响面（1 天）

目标：能用 `tools/codebase_intel.py` 独立回答"改这里会影响谁"。

### 练习 1：定位入口
```powershell
python -X utf8 tools/codebase_intel.py --help
python -X utf8 tools/codebase_intel.py search "run_evals" --limit 5
```
**产出**：列出 3 个命中文件 + 1 句用途；写进 L2 笔记。

### 练习 2：引用/影响面
```powershell
python -X utf8 tools/codebase_intel.py refs "WO-RUFF-F401F821" --limit 10
```
（若 refs 参数不同，先 --help）**产出**：说明该符号/字符串被哪些文件引用；判断"只改一处是否安全"。

### 练习 3：真实改动影响面
从 `WO-RUFF-F401F821-20261027` 的 ruff 修复项里挑 **1 个文件**：
1. 用 codebase_intel 找该文件的引用方；
2. 列出"直接调用方/间接依赖/测试覆盖"三栏；
3. 判断是否需要同步改测试/文档。
**产出**：影响面清单（≤1 页），贴到工单 note。

### L2 完成证据
- 3 个练习输出/笔记 → `docs/dev/learning/DEV7_L2_notes.md`；
- 导师 DEV1 确认"能独立判断影响面"（工单 note 签字）。

## 导师（DEV1）确认清单

- [ ] 已看计划与启动包，认可目标/模块/截止；
- [ ] L1 答题抽查通过；
- [ ] L2 影响面清单评审通过；
- [ ] 记录：`DEV7.json` kickoff.mentor_confirmed=true，或工单 note 签字。

## 后续（通过后）

- L3 测试/回归、L4 质量门；
- 11-03 检查点：4 模块 evidence + 实践单验收 + 导师意见；
- 若试点通过 → 复用本包模板到 DEV8/DEV3/DEV4，形成能力矩阵/学习地图。
