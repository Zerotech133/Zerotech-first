# X5 Tushare token 写入路径 v1（待投递执行）

> 工单：`WO-X5-TOKEN-WIRE-20261027` ｜ 日期：2026-10-27 ｜ 状态：**写入路径已就绪；token 未投递，轮换未执行**
> 背景：R1 Tushare token 轮换需老田先在 tushare 控制台重置 token；token 不得进聊天/日志。

## 1. 当前事实（22:5x 实测）

- `--dry-run`：`target=C:\项目\_vault\secrets\mcp\tushare_mcp_token (exists=False)`；
- 无 `TUSHARE_TOKEN` 环境变量、无新 token 投递文件；旧值仍在离线段（`_vault\secrets.enc\mcp\tushare_mcp_token.age`）；
- 结论：**不能执行真实轮换**（没有新 token），仅完成写入路径与校验。

## 2. 新增写入方式（非交互，供 AI 执行）

`tools/secrets/rotate_tushare_token.py` 新增：

```powershell
python -X utf8 tools/secrets/rotate_tushare_token.py --token-file C:\项目\_vault\secrets\_inbox\tushare_token.txt
```

- 只取文件第一个非空行；**不回显 token**、不进 shell 历史；
- 保留原 getpass 交互方式（不带 `--token-file` 时）；
- 文件**不自动删除**（轮换成功后人工/回收站处理）；
- `--dry-run --token-file <path>` 只校验存在性，不读内容、不写盘；
- 前置：token 长度 ≥20、无空白；缺失/空文件明确报错。

## 3. 投递与执行步骤

1. 在 tushare 控制台重置 token（老田）；
2. 将新 token 写入：`C:\项目\_vault\secrets\_inbox\tushare_token.txt`（单行，勿发聊天）；
3. 回一句「token 已放到 inbox」；
4. AI 执行轮换（自动写 target → `vault encrypt --recursive` → `vault verify --json` → `render_mcp` → `render_mcp --check`）；
5. 验收：`vault verify ok=true`、`render_mcp --check` 通过、旧值快照存在、MCP 只读查询可用；
6. **8796 重启五要素包**：token 就绪且上述验收通过后，另出 L2 五要素（命令全文/影响面/不可逆点/替代方案/回滚），等老田批准再动 8796。

## 4. 测试

`tests/test_rotate_tushare_token.py` **4 passed**：
- 文件首行读取/空行过滤；缺失/空文件 dry-run 行为；
- dry-run 不回显 token；`steps: token-file` 生效；缺文件 exit1。

## 5. 边界

- 未执行真实轮换、未写 target、未改 MCP 配置、未重启任何服务；
- 8796 重启属 L2，需 token 就绪 + 人类五要素批准；
- 无 L2、无生产变更。

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-10-27 | 写入路径（--token-file）+ 投递 inbox + 4 测试；等 token |
