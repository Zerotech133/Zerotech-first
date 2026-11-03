# W0 merge train 执行回执 v1（16:30 窗口）

- 时间：2026-11-03 16:30–16:40｜窗口：16:30–17:00
- 结果：**0 单合入**；doing **30** / worktree **38** 未变
- 纪律：未用 `--train-force`，未绕过门禁

## 一、尝试记录

| 顺序 | 任务 | 结果 | 阻塞 |
|---:|---|---|---|
| 1 | `WO-IFS-STD-20261103` | 拒绝 | **H5 钉扎**：分支改了 `tools/test_gate.ps1`，需独立审批后 Lead 手动合并 |
| 2 | `UI-UNIFY-TOKENS-20261103` | 拒绝 | **凭据扫描误报**：`docs/design/...tokens...md` 中 tokens.css hash 被判 HIGH_ENTROPY |
| 3 | `WO-CAOCAO-FRAME-20261103` | 拒绝 | **fast gate 9 条新失败**：`tools/datawarehouse/tests`（catalog/query/registry 相关） |

其他候选未继续：按“失败即停”规则，先修门禁环境，避免带病合并。

## 二、根因判断

1. **H5**：IFS 分支故意修改质量闸脚本，按规则必须独立审批，不能随任务单合入；
2. **凭据扫描**：文档中的哈希被判高熵，属误报；需将该文件加入白名单/改写法，不应绕过扫描；
3. **datawarehouse 9F**：在分支 worktree 跑 fast 时，catalog/registry/snapshot 数据与主工作区不一致（worktree 缺生成物/数据），测试失败不等于分支代码坏；
   - 但这意味着当前 merge 队列在 16:30 窗口**不可执行**，需要先修 worktree 数据快照/测试环境。

## 三、对计划的影响

- W0 今天无法达到 doing≤8/worktree≤20；
- 11-04 黄忠验收前不应强行 merge；
- HZ 基线保持 15:58 的 **0F**，不被本轮 merge 污染（0 单合入）；
- HZ daily 16:06 **PASS**，但 review 仍 4/6 / GoNoGo NO_GO。

## 四、下一步

1. **修复 datawarehouse worktree 测试环境**：确认 catalog/metadata 生成物是否应复制/生成到 worktree；
2. **处理凭据误报**：对 tokens 文档哈希做白名单或改写；
3. **H5 独立审批**：IFS test_gate.ps1 变更单独立项，不进任务合并；
4. 下一 merge 窗口（11-04 15:00 后）重新跑；
5. HZ 11-04 09:30/10:00 照常；不得因 W0 延期而跳过。

## 五、证据

- `C:\Temp\w0_merge_train.log`
- `C:\Users\t\AppData\Local\Temp\merge_gated_20261103_163349.log`
- 当前 `summary.json`：doing 30 / worktree 38

## 六、16:40–17:00 追加尝试与修复

- `WO-CAOCAO-FRAME`：数据引导后 fast 门通过，但被 kernel_guard **组合注册 ADR 门**拦下，未提交；
- `UI-UNIFY-MANIFEST`：数据引导前 fast 门 1F（`xq_xq_status` 缺 sqlite）；
- 新增 `tools/w0_worktree_bootstrap.py`：复制 catalog/registry/xueqiu_info.db 到 worktree；
- 修复后 UI worktree 的 datawarehouse 测试 **340 passed / 0F**；
- 17:00 窗口关闭，**不再 merge；代码冻结到 11-04 10:00**；
- 下一窗口 11-04 15:00 重试。
