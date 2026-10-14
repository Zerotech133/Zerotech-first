# 20261014 情绪影子日采器落地（sentiment-shadow：regime 第二传感器）

## 一、出处与定位
开源借鉴 #4 执行面（akshare 探测三接口全可用→tools/probe_akshare_sentiment.py）。
连板高度/炸板率是比市场宽度更灵敏的 A 股情绪指标，做**影子日采**（只记录
不改策略行为，与 gate_shadow_collect 同定位），攒满后与宽度并列喂 W8 regime
定档讨论。

## 二、新增/改动
- `quant_backtest/sentiment_collect_daily.py`（新增）：
  - 每日采涨停家数/炸板家数/炸板率（=zb/(zt+zb)，与探测报告 20261013=22.09%
    对账一致）/连板高度/连板分布 → `data/sentiment_shadow.jsonl`
    （幂等键 date；O_APPEND+fsync+锁，判例 gate_shadow_collect）；
  - **接口窗口实测（重要发现）**：炸板池仅保留最近 30 个交易日
    （akshare 1.19.1 超窗抛 ValueError）、涨停池历史仅回补约 15 个交易日
    （20260916 起）——超窗降级为仅涨停口径（zb/rate 记 None 不炸行），
    **情绪历史只能从 2026-10-14 起攒**；
  - 非交易日/空日不落行；接口异常落 status=error 行便于发现断流；
  - 直连保险：清代理环境变量（判例 09-26 502）。
- `quant_backtest/test_sentiment_collect.py`：4/4 过（合成两池口径、
  30 日窗降级、空日 None、幂等+坏行容忍；fetch_day 打桩不触网）。
- **开发过程修正**：zb=None 被后续 isinstance 统一转回空表（降级失效，
  测试抓到，真 bug 已修）；测试舍入容差与坏行计数两处断言修正。

## 三、调度
- WorkBuddy 常驻自动化 id=846b3def：每周一~五 16:35（收盘后）跑
  `envs/default/Scripts/python.exe sentiment_collect_daily.py`；
  **必须用受管 venv**（系统 python311 无 akshare）。
- 不注册 Windows 计划任务（schtasks 被沙箱黑名单拦截，且避免混入
  LinkageCollect 等既有任务链）。

## 四、首采与现状
- 回补 20260601~20261014：实际可得 15 个交易日（20260916~20261014，
  含 10-12 连板高度 10 板/炸板率 35.2%、10-14 炸板率 40%）；
- 与湖口径宽度相关分析：重叠样本 14 <20，攒满后自动出读数
  （corr_with_breadth 已内置，每次运行打印）。

## 五、回退
- 删 sentiment_collect_daily.py / test_sentiment_collect.py 即回退；
- data/sentiment_shadow.jsonl(+.lock) 可直接删；
- 自动化 id 846b3def 停用即可。
