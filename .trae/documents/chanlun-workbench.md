# 缠论工作台 + 信号池手动推送 实施计划

## Context

缠论分析方法体系已研究成熟（三级结构识别 / 三类买卖点 / 背驰四口径 / 中枢演化 / 多级别联立 / 资金大盘 / 置信度融合，22 个引擎脚本全部在线，/bt-chanlun-pool 已有「方法论总纲」tab）。但现有分析台 `/bt-chanlun` 只展示方法进度（P1-P8 表）与图表查看器，方法是"散装"的；且**全系统没有手动入池路径**——股票进入信号池的唯一入口是 ChanlunScan 15:35 全市场扫描。

本计划把方法体系重组为**工作台**（方法→工具→字段→状态映射 + 工具箱分区），并新增**推池动作**：分析台上对任意 A 股跑结构分析，检出的信号可一键推入信号池，走与自动扫描完全相同的增强管线与三层幂等，paper 跟踪自然衔接。

## 改动总览（约 200 行，3 个文件）

| 步骤 | 文件 | 内容 |
|---|---|---|
| 1 | quant_backtest/chanlun_scan.py | 新增 `push_manual(code, sig_type=None, sig_dt=None)`（+~60 行） |
| 2 | portal/portal_api.py | 新增 POST `/api/chanlun/pool/push` 端点（+~15 行） |
| 3 | portal/portal_api.py `_chanlun_page` | 重组为工作台 UI（~120 行改动） |

## Step 1：`chanlun_scan.py` 新增 `push_manual`

流程照抄 `scan_all`（L460-511）单票化：

1. `chanlun_data._norm` 归一代码，校验 `market=='CN'`（缠论池仅 A 股，硬约束），否则返回 error
2. `_bars_from_parquet` + `_tdx_topup` 取 K 线
3. `chanlun_signal.analyze` → `chanlun_gray.enrich(bars, st)`（灰度 8 字段）→ `_trend` → tbe 信号补 `_npv_of`——**与 scan 同一套增强管线，字段口径一致**
4. 逐信号（按 sig_type/sig_dt 可选过滤）：`_make_row(..., source="manual", lag=None)`（lag 在 scan 中本就为 None）→ `_pool_keys` 查重计 skipped → `_write_pool`（原子写）→ `_pool_dual_write_sqlite` → `chanlun_mlf.score_rows` best-effort
5. 加 **msvcrt 文件锁**（+8 行）：portal 手动推与 15:35 任务并发 `_write_pool` 时防原子替换互相覆盖
6. 返回 `{"ok":True,"pushed":[...],"skipped":[...]}`

注意：enrich 只在推池路径调用，**不给 `/api/chanlun/structure` 加**，避免改变现有图表徽章口径。

## Step 2：POST `/api/chanlun/pool/push`

do_POST 区（L16538+）照 `/api/portfolio_open`（L1714）范式：Content-Length → json.loads → `_chanlun_pool_push(rec)` → `self._json(res)`。handler 内 sys.path 插 QBT_DIR、import chanlun_scan、try/except 全包。

paper 衔接零新代码：推池行由下一轮 15:35 `scan_all` 后 `_open_positions` 按账本 `(code,type,sig_dt)` 幂等拾取开仓。

## Step 3：`_chanlun_page` 重组为工作台

1. **信号清单**（新增 `ck-sigs` div，插在 `ck-bt` L6390 后）：`ckLoad` 回调渲染表格——类型/dt/价格 + 影子徽章（bei/bei2/mbei/tbe/vv/mt/reg，配色沿用图例）+ 每行「⇪ 推池」按钮 → POST push → 成功置「已入池」；**dt 非最新 bar 的旧信号按钮标黄提示**（paper 将以最新收盘入账）
2. **方法→工具→字段→状态映射**：P1-P8 表改四列——分型/笔/中枢/线段、三类买卖点、背驰四口径（bei 盘整/mbei MACD/bei2 正宗/tbe 趋势）、gray 中枢演化、mt 多级别、vv/reg 资金大盘、conf/composite 置信度、mlf 质量分，每行挂对应工具/面板链接
3. **工具箱分区**：回测速览（页内）/ 结构图 PNG（`/chanlun-png?c=&p=` 动态链）/ 推演卡 / 标注 / 方法论总纲（/bt-chanlun-pool tab）

## 验证

1. **CLI**：`push_manual("600337.SH")` → pool.jsonl 尾行 `source=manual`、30 字段 schema 与 scan 行一致；二调全 skipped（幂等）
2. **API**：curl POST 两次，第二次返回 skipped 明细
3. **浏览器执行层**（硬约束 K189：字符串存在性≠JS 执行）：TRAE-browseruse 打开 /bt-chanlun → 输入股票跑分析 → SVG 图表正常渲染、信号清单+徽章出现 → 实点一次推池 → 到 /bt-chanlun-pool 确认新行出现且来源标记 manual
4. 重启门户（计划任务 AI工作站门户API）前后各验一次 /bt-chanlun 可达

## 风险与对策

| 风险 | 对策 |
|---|---|
| 并发写池（portal vs 15:35 任务） | msvcrt 文件锁 |
| 历史信号推池后 paper 价脱节 | UI 旧信号标黄提示 |
| 6 位裸码 SH/SZ 误判 | 前端固定传完整 `600337.SH` 形态 |
| 多会话并行改 portal_api.py | 实施前重读 project_memory 确认无他会话在改；改动集中在 _chanlun_page 与 do_POST 新分支 |
